"""Direct push of approved descriptions to e-commerce platforms, matched by SKU."""

import html
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx

from app.models import DescriptionVersion, Product
from app.services.exporters.common import to_html

TIMEOUT = httpx.Timeout(30.0)
# Overridable in tests (httpx.MockTransport).
transport: httpx.BaseTransport | None = None


class PlatformError(Exception):
    pass


# Shopify supports each Admin API version for about a year; keep this on a recent release.
DEFAULT_SHOPIFY_VERSION = "2026-07"


def ensure_public_host(url: str) -> None:
    """Store URLs come from users: refuse ones that point into private networks (SSRF protection).

    Unresolvable names are left to fail on the request itself.
    """
    host = urlparse(url).hostname or ""
    if not host:
        raise PlatformError("The store address is not a valid URL")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise PlatformError("The store address points to a private or local network")


def shopify_domain(value: str) -> str:
    """Accept what merchants paste: "mystore", "mystore.myshopify.com", "https://mystore.myshopify.com/admin"
    or the new admin link "https://admin.shopify.com/store/mystore". Returns "mystore.myshopify.com"."""
    text = (value or "").strip()
    admin = re.search(r"admin\.shopify\.com/store/([a-z0-9][a-z0-9-]*)", text, re.I)
    if admin:
        return f"{admin.group(1).lower()}.myshopify.com"
    host = re.sub(r"^[a-z]+://", "", text, flags=re.I).split("/")[0].split("?")[0].strip().lower()
    if host and "." not in host:
        host += ".myshopify.com"
    return host


def woocommerce_base(value: str) -> str:
    """Store URL without a trailing slash, admin path or missing scheme ("mystore.com" -> "https://mystore.com")."""
    url = (value or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return re.sub(r"/(wp-admin|wp-json).*$", "", url, flags=re.I).rstrip("/")


def strip_html(value: str) -> str:
    text = re.sub(r"<(br|/p|/li)\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


class ShopifyClient:
    """Shopify Admin GraphQL API. Config: shop_domain, access_token, api_version."""

    def __init__(self, config: dict) -> None:
        domain = shopify_domain(config.get("shop_domain", ""))
        if not domain or not config.get("access_token"):
            raise PlatformError("Shopify integration needs shop_domain and access_token")
        version = (config.get("api_version") or "").strip() or DEFAULT_SHOPIFY_VERSION
        self.url = f"https://{domain}/admin/api/{version}/graphql.json"
        ensure_public_host(self.url)
        self.client = httpx.Client(
            timeout=TIMEOUT, headers={"X-Shopify-Access-Token": config["access_token"]}, transport=transport
        )

    def _query(self, query: str, variables: dict | None = None) -> dict:
        response = self.client.post(self.url, json={"query": query, "variables": variables or {}})
        if response.status_code in (401, 403):
            raise PlatformError("Shopify rejected the access token (check it and the app's read_products / "
                                "write_products scopes)")
        if response.status_code == 404:
            raise PlatformError("Shopify store not found: check the store address")
        if response.status_code >= 400:
            raise PlatformError(f"Shopify HTTP {response.status_code}")
        payload = response.json()
        if payload.get("errors"):
            raise PlatformError(f"Shopify error: {payload['errors']}")
        return payload["data"]

    def test(self) -> str:
        return self._query("{ shop { name } }")["shop"]["name"]

    def list_products(self, limit: int = 5000):
        """Yield products as plain dicts (sku, name, category, brand, price, images, description, tags)."""
        query = """query($after: String) {
          products(first: 100, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id title handle productType vendor tags descriptionHtml
              images(first: 3) { nodes { url } }
              variants(first: 1) { nodes { sku price } }
              options { name values }
            }
          }
        }"""
        after, count = None, 0
        while count < limit:
            page = self._query(query, {"after": after})["products"]
            for node in page["nodes"]:
                variant = (node.get("variants") or {}).get("nodes") or [{}]
                yield {
                    "sku": (variant[0].get("sku") or node["handle"]).strip(),
                    "name": node["title"],
                    "category": node.get("productType") or None,
                    "brand": node.get("vendor") or None,
                    "price": str(variant[0].get("price") or "") or None,
                    "images": [i["url"] for i in (node.get("images") or {}).get("nodes", [])],
                    "description": strip_html(node.get("descriptionHtml") or ""),
                    "keywords": list(node.get("tags") or [])[:10],
                    "attributes": {o["name"]: ", ".join(o["values"]) for o in node.get("options") or []
                                   if o["name"] != "Title"},
                }
                count += 1
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]

    def push(self, product: Product, version: DescriptionVersion) -> None:
        data = self._query(
            "query($q: String!) { productVariants(first: 1, query: $q) { nodes { product { id } } } }",
            {"q": "sku:" + '"' + product.sku.replace("\\", "\\\\").replace('"', '\\"') + '"'},
        )
        nodes = data["productVariants"]["nodes"]
        if not nodes:
            raise PlatformError("No Shopify product with this SKU")
        result = self._query(
            """mutation($product: ProductUpdateInput!) {
                 productUpdate(product: $product) { userErrors { field message } }
               }""",
            {"product": {
                "id": nodes[0]["product"]["id"],
                "descriptionHtml": to_html(version),
                "seo": {"title": version.meta_title, "description": version.meta_description},
            }},
        )
        errors = result["productUpdate"]["userErrors"]
        if errors:
            raise PlatformError("; ".join(e["message"] for e in errors))


class WooCommerceClient:
    """WooCommerce REST API v3. Config: store_url, consumer_key, consumer_secret."""

    def __init__(self, config: dict) -> None:
        url = woocommerce_base(config.get("store_url", ""))
        if not url or not config.get("consumer_key") or not config.get("consumer_secret"):
            raise PlatformError("WooCommerce integration needs store_url, consumer_key and consumer_secret")
        self.base = f"{url}/wp-json/wc/v3"
        ensure_public_host(self.base)
        self.client = httpx.Client(timeout=TIMEOUT, auth=(config["consumer_key"], config["consumer_secret"]),
                                   transport=transport)

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code in (401, 403):
            raise PlatformError("WooCommerce rejected the API keys (they need Read/Write permission; the store "
                                "must use https)")
        if response.status_code == 404:
            raise PlatformError("WooCommerce REST API not found: check the store address and that permalinks are on")
        if response.status_code >= 400:
            raise PlatformError(f"WooCommerce HTTP {response.status_code}: {response.text[:200]}")
        return response

    def test(self) -> str:
        self._check(self.client.get(f"{self.base}/products", params={"per_page": 1}))
        return "connected"

    def list_products(self, limit: int = 5000):
        page = 1
        count = 0
        while count < limit:
            items = self._check(self.client.get(f"{self.base}/products",
                                                params={"per_page": 100, "page": page, "status": "any"})).json()
            if not items:
                break
            for item in items:
                yield {
                    "sku": (item.get("sku") or f"woo-{item['id']}").strip(),
                    "name": item.get("name") or "",
                    "category": ", ".join(c["name"] for c in item.get("categories") or []) or None,
                    "brand": None,
                    "price": item.get("regular_price") or item.get("price") or None,
                    "images": [i["src"] for i in (item.get("images") or [])[:3]],
                    "description": strip_html(item.get("description") or item.get("short_description") or ""),
                    "keywords": [t["name"] for t in item.get("tags") or []][:10],
                    "attributes": {a["name"]: ", ".join(a.get("options") or []) for a in item.get("attributes") or []},
                }
                count += 1
            page += 1

    def push(self, product: Product, version: DescriptionVersion) -> None:
        found = self._check(self.client.get(f"{self.base}/products", params={"sku": product.sku})).json()
        if not found:
            raise PlatformError("No WooCommerce product with this SKU")
        self._check(self.client.put(
            f"{self.base}/products/{found[0]['id']}",
            json={"description": to_html(version), "short_description": version.short_description},
        ))


def get_client(platform: str, config: dict):
    if platform == "shopify":
        return ShopifyClient(config)
    if platform == "woocommerce":
        return WooCommerceClient(config)
    raise PlatformError(f"Unsupported platform: {platform}")
