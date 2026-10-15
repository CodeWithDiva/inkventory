"""End to end against fake stores: connect, import, generate, approve, push back, and check the store changed."""

import json
import re

import httpx
import pytest

from app.services.exporters import platforms


class FakeShopify:
    """Stateful stand-in for the Shopify Admin GraphQL API (only the operations the app uses)."""

    def __init__(self, token: str = "shpat_ok"):
        self.token = token
        self.products = {
            "gid://shopify/Product/1": {"title": "Blue Mug", "handle": "blue-mug", "sku": "MUG 1", "html": "<p>Old</p>"},
            "gid://shopify/Product/2": {"title": "Tea \"Pot\"", "handle": "tea-pot", "sku": 'TP"2', "html": ""},
        }
        self.updates: list[dict] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        if request.headers.get("X-Shopify-Access-Token") != self.token:
            return httpx.Response(401, json={"errors": "Invalid API key or access token"})
        body = json.loads(request.content)
        query, variables = body["query"], body.get("variables") or {}
        if "shop {" in query:
            return httpx.Response(200, json={"data": {"shop": {"name": "Demo Store"}}})
        if "products(first" in query:
            nodes = [{"id": pid, "title": p["title"], "handle": p["handle"], "productType": "Kitchen", "vendor": "Acme",
                      "tags": ["kitchen"], "descriptionHtml": p["html"], "images": {"nodes": []},
                      "variants": {"nodes": [{"sku": p["sku"], "price": "10.00"}]}, "options": []}
                     for pid, p in self.products.items()]
            return httpx.Response(200, json={"data": {"products": {"nodes": nodes,
                                                                   "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
        if "productVariants" in query:
            # Shopify search syntax: sku:"value" with \" escapes
            match = re.fullmatch(r'sku:"((?:[^"\\]|\\.)*)"', variables["q"])
            sku = re.sub(r"\\(.)", r"\1", match.group(1)) if match else None
            found = [{"product": {"id": pid}} for pid, p in self.products.items() if p["sku"] == sku]
            return httpx.Response(200, json={"data": {"productVariants": {"nodes": found}}})
        if "productUpdate" in query:
            data = variables["product"]
            self.products[data["id"]]["html"] = data["descriptionHtml"]
            self.updates.append(data)
            return httpx.Response(200, json={"data": {"productUpdate": {"userErrors": []}}})
        return httpx.Response(400, json={"errors": "unexpected query"})


class FakeWoo:
    def __init__(self):
        self.products = [{"id": 7, "name": "Oak Chair", "sku": "CH-7", "description": "<p>Chair</p>",
                          "regular_price": "40", "categories": [], "images": [], "tags": [], "attributes": []}]
        self.puts: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") is None:
            return httpx.Response(401, json={"code": "woocommerce_rest_cannot_view"})
        if request.method == "GET" and request.url.path.endswith("/products"):
            if "sku" in request.url.params:
                return httpx.Response(200, json=[p for p in self.products if p["sku"] == request.url.params["sku"]])
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, json=self.products if page == 1 else [])
        if request.method == "PUT":
            pid = int(request.url.path.rsplit("/", 1)[1])
            body = json.loads(request.content)
            next(p for p in self.products if p["id"] == pid)["description"] = body["description"]
            self.puts.append(body)
            return httpx.Response(200, json={"id": pid})
        return httpx.Response(404)


def generate_and_approve(client, auth, catalog_id):
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en"], "engine": "mock"})
    approved = client.post(f"/api/catalogs/{catalog_id}/approve-drafts", headers=auth,
                           json={"language": "en", "min_seo_score": 0, "skip_flagged": False}).json()
    assert approved["updated"] >= 1, approved


def push(client, auth, catalog_id, integration_id, platform):
    export = client.post("/api/exports", headers=auth, json={
        "catalog_id": catalog_id, "language": "en", "format": platform, "integration_id": integration_id}).json()
    return client.get(f"/api/exports/{export['id']}", headers=auth).json()


def test_shopify_connect_import_generate_push(client, auth, monkeypatch):
    store = FakeShopify()
    monkeypatch.setattr(platforms, "transport", httpx.MockTransport(store))
    integration = client.post("/api/integrations", headers=auth, json={
        "platform": "shopify", "name": "Demo", "config": {"shop_domain": "https://admin.shopify.com/store/demo",
                                                          "access_token": "shpat_ok"}}).json()
    assert client.post(f"/api/integrations/{integration['id']}/test", headers=auth).json() == {"ok": True, "detail": "Demo Store"}
    assert store.urls[0] == f"https://demo.myshopify.com/admin/api/{platforms.DEFAULT_SHOPIFY_VERSION}/graphql.json"

    run = client.post(f"/api/integrations/{integration['id']}/sync", headers=auth, json={}).json()
    assert run["status"] == "completed" and run["imported"] == 2
    generate_and_approve(client, auth, run["catalog_id"])

    export = push(client, auth, run["catalog_id"], integration["id"], "shopify")
    assert export["status"] == "completed" and export["succeeded"] == 2 and export["failed"] == 0, export
    # SKUs with spaces and quotes were found, and the store now holds the generated copy and SEO fields.
    assert len(store.updates) == 2 and all(u["seo"]["title"] and u["seo"]["description"] for u in store.updates)
    assert "<p>Old</p>" not in store.products["gid://shopify/Product/1"]["html"]


def test_shopify_bad_token_gives_a_clear_message(client, auth, monkeypatch):
    monkeypatch.setattr(platforms, "transport", httpx.MockTransport(FakeShopify(token="other")))
    integration = client.post("/api/integrations", headers=auth, json={
        "platform": "shopify", "name": "Demo", "config": {"shop_domain": "demo", "access_token": "shpat_wrong"}}).json()
    result = client.post(f"/api/integrations/{integration['id']}/test", headers=auth).json()
    assert result["ok"] is False and "access token" in result["detail"]


def test_woocommerce_connect_import_generate_push(client, auth, monkeypatch):
    store = FakeWoo()
    monkeypatch.setattr(platforms, "transport", httpx.MockTransport(store))
    integration = client.post("/api/integrations", headers=auth, json={"platform": "woocommerce", "name": "Woo", "config": {
        "store_url": "woo.example.com/wp-admin/", "consumer_key": "ck_1", "consumer_secret": "cs_1"}}).json()
    assert client.post(f"/api/integrations/{integration['id']}/test", headers=auth).json()["ok"] is True
    run = client.post(f"/api/integrations/{integration['id']}/sync", headers=auth, json={}).json()
    assert run["imported"] == 1
    generate_and_approve(client, auth, run["catalog_id"])
    export = push(client, auth, run["catalog_id"], integration["id"], "woocommerce")
    assert export["succeeded"] == 1 and store.puts and store.puts[0]["short_description"]
    assert store.products[0]["description"] != "<p>Chair</p>"


@pytest.mark.parametrize("config", [
    {"platform": "woocommerce", "config": {"store_url": "http://127.0.0.1:8000", "consumer_key": "a", "consumer_secret": "b"}},
    {"platform": "woocommerce", "config": {"store_url": "http://169.254.169.254", "consumer_key": "a", "consumer_secret": "b"}},
    {"platform": "shopify", "config": {"shop_domain": "127.0.0.1", "access_token": "x"}},
])
def test_store_addresses_inside_private_networks_are_refused(client, auth, config):
    r = client.post("/api/integrations", headers=auth, json={"name": "x", **config})
    assert r.status_code == 400 and "private or local network" in r.json()["detail"]


def test_editing_a_store_validates_the_new_settings(client, auth, monkeypatch):
    monkeypatch.setattr(platforms, "transport", httpx.MockTransport(FakeWoo()))
    created = client.post("/api/integrations", headers=auth, json={"platform": "woocommerce", "name": "Woo", "config": {
        "store_url": "https://woo.example.com", "consumer_key": "ck_1", "consumer_secret": "cs_1"}}).json()
    masked = created["config"]["consumer_secret"]
    bad = client.put(f"/api/integrations/{created['id']}", headers=auth, json={"platform": "woocommerce", "name": "Woo", "config": {
        "store_url": "http://10.0.0.5", "consumer_key": "ck_1", "consumer_secret": masked}})
    assert bad.status_code == 400
    ok = client.put(f"/api/integrations/{created['id']}", headers=auth, json={"platform": "woocommerce", "name": "Woo 2", "config": {
        "store_url": "https://woo.example.com", "consumer_key": "ck_1", "consumer_secret": masked}})
    assert ok.status_code == 200
    assert client.post(f"/api/integrations/{created['id']}/test", headers=auth).json()["ok"] is True  # secret kept
