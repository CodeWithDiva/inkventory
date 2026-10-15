"""API keys, webhooks, store product sync, URL / Google Sheets import."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.db import SessionLocal
from app.models import Integration
from app.services import net, webhooks
from app.services.exporters import platforms
from tests.conftest import SAMPLE_CSV, upload_sample


@pytest.fixture()
def captured(monkeypatch):
    """Collect outgoing webhook requests instead of sending them."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(webhooks, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr("app.routers.developer.check_public_url", lambda url: None)
    return calls


# ---------- API keys ----------
def test_api_keys(client, auth):
    r = client.post("/api/api-keys", headers=auth, json={"name": "ERP", "role": "editor"})
    assert r.status_code == 201
    raw = r.json()["key"]
    assert raw.startswith("cai_") and r.json()["prefix"] == raw[:12]
    assert "key" not in client.get("/api/api-keys", headers=auth).json()[0]  # shown only once

    key = {"X-API-Key": raw}
    created = client.post("/api/catalogs/api", headers=key, json={"name": "From ERP", "products": [{"sku": "E1", "name": "Desk"}]})
    assert created.status_code == 201
    assert client.get("/api/catalogs", headers={"Authorization": f"Bearer {raw}"}).json()[0]["name"] == "From ERP"
    assert client.get("/api/auth/me", headers=key).json()["full_name"] == "API key: ERP"
    # Account-level actions need a signed-in person.
    assert client.get("/api/api-keys", headers=key).status_code == 403
    assert client.post("/api/auth/change-password", headers=key, json={"current_password": "x", "new_password": "newpass123"}).status_code == 403

    reviewer = client.post("/api/api-keys", headers=auth, json={"name": "QA", "role": "reviewer"}).json()["key"]
    assert client.post("/api/catalogs/api", headers={"X-API-Key": reviewer}, json={"name": "x"}).status_code == 403

    key_id = client.get("/api/api-keys", headers=auth).json()[-1]["id"]
    client.delete(f"/api/api-keys/{key_id}", headers=auth)
    assert client.get("/api/catalogs", headers=key).status_code == 401
    assert client.get("/api/catalogs", headers={"X-API-Key": "cai_wrong"}).status_code == 401


# ---------- Webhooks ----------
def test_webhooks_are_signed_and_fired(client, auth, captured):
    assert "job.completed" in client.get("/api/webhooks/events", headers=auth).json()
    r = client.post("/api/webhooks", headers=auth, json={"url": "https://hooks.example.com/catalog", "events": ["*"]})
    assert r.status_code == 201
    secret = r.json()["secret"]
    assert "secret" not in client.get("/api/webhooks", headers=auth).json()[0]

    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id})
    client.post(f"/api/catalogs/{catalog_id}/approve-drafts", headers=auth,
                json={"language": "en", "min_seo_score": 0, "skip_flagged": False})
    client.post("/api/exports", headers=auth, json={"catalog_id": catalog_id, "format": "csv_generic"})

    events = [req.headers["X-Inkventory-Event"] for req in captured]
    assert events == ["catalog.imported", "job.completed", "description.approved", "export.completed"]
    for req in captured:
        expected = "sha256=" + hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
        assert req.headers["X-Inkventory-Signature"] == expected
    job_event = json.loads(captured[1].content)
    assert job_event["data"]["completed"] == 8 and job_event["data"]["catalog_id"] == catalog_id
    assert json.loads(captured[2].content)["data"]["count"] == 8

    hook_id = r.json()["id"]
    ping = client.post(f"/api/webhooks/{hook_id}/test", headers=auth).json()
    assert ping["success"] and ping["status_code"] == 200
    deliveries = client.get(f"/api/webhooks/{hook_id}/deliveries", headers=auth).json()
    assert len(deliveries) == 5 and all(d["success"] for d in deliveries)


def test_webhook_filtering_and_failures(client, auth, monkeypatch):
    monkeypatch.setattr("app.routers.developer.check_public_url", lambda url: None)
    monkeypatch.setattr(webhooks, "transport", httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    hook = client.post("/api/webhooks", headers=auth, json={"url": "https://h.example.com", "events": ["job.completed"]}).json()
    upload_sample(client, auth)  # catalog.imported: not subscribed
    assert client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=auth).json() == []
    ping = client.post(f"/api/webhooks/{hook['id']}/test", headers=auth).json()
    assert not ping["success"] and ping["status_code"] == 500 and ping["error"] == "boom"
    assert client.post("/api/webhooks", headers=auth, json={"url": "https://h.example.com", "events": ["nope"]}).status_code == 400


def test_webhook_rejects_internal_urls(client, auth):
    for url in ["http://127.0.0.1:8000/hook", "http://localhost/x", "ftp://example.com"]:
        assert client.post("/api/webhooks", headers=auth, json={"url": url, "events": ["*"]}).status_code == 400


# ---------- Store sync ----------
def shopify_handler(pages: list[list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "shop" in body["query"]:
            return httpx.Response(200, json={"data": {"shop": {"name": "Demo"}}})
        index = 0 if body["variables"].get("after") is None else int(body["variables"]["after"])
        return httpx.Response(200, json={"data": {"products": {
            "pageInfo": {"hasNextPage": index + 1 < len(pages), "endCursor": str(index + 1)},
            "nodes": pages[index],
        }}})
    return handler


def shopify_product(sku: str, title: str, **extra) -> dict:
    return {"id": f"gid://shopify/Product/{sku}", "title": title, "handle": title.lower().replace(" ", "-"),
            "productType": "Mugs", "vendor": "MorningCo", "tags": ["ceramic mug", "coffee"],
            "descriptionHtml": "<p>Old <b>description</b></p>", "images": {"nodes": [{"url": f"https://cdn.shop/{sku}.jpg"}]},
            "variants": {"nodes": [{"sku": sku, "price": "12.99"}]},
            "options": [{"name": "Color", "values": ["Blue", "White"]}], **extra}


def test_shopify_sync_imports_and_updates(client, auth, monkeypatch):
    pages = [[shopify_product("M1", "Blue Mug"), shopify_product("M2", "White Mug")],
             [shopify_product("M3", "Travel Mug"), shopify_product("", "No SKU", handle="")]]
    monkeypatch.setattr(platforms, "transport", httpx.MockTransport(shopify_handler(pages)))
    integration = client.post("/api/integrations", headers=auth, json={
        "platform": "shopify", "name": "My shop", "config": {"shop_domain": "demo.myshopify.com", "access_token": "shpat_x"}}).json()

    run = client.post(f"/api/integrations/{integration['id']}/sync", headers=auth, json={}).json()
    assert run["status"] == "completed" and run["imported"] == 3 and len(run["errors"]) == 1, run
    catalog_id = run["catalog_id"]
    products = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"]
    m1 = next(p for p in products if p["sku"] == "M1")
    assert m1["images"] == ["https://cdn.shop/M1.jpg"] and m1["brand"] == "MorningCo"
    assert m1["original_description"] == "Old description" and m1["attributes"] == {"Color": "Blue, White"}
    assert m1["keywords"] == ["ceramic mug", "coffee"]

    pages[0][0]["title"] = "Blue Mug XL"
    again = client.post(f"/api/integrations/{integration['id']}/sync", headers=auth, json={}).json()
    assert again["catalog_id"] == catalog_id and again["imported"] == 0 and again["updated"] == 3
    assert client.get(f"/api/integrations/{integration['id']}/syncs", headers=auth).json()[0]["trigger"] == "manual"
    shown = client.get("/api/integrations", headers=auth).json()[0]
    assert shown["sync_catalog_id"] == catalog_id and shown["last_synced_at"]


def test_woocommerce_sync_and_schedule(client, auth, monkeypatch):
    items = [{"id": 1, "name": "Red Chair", "sku": "C1", "description": "<p>Nice chair</p>", "regular_price": "40",
              "categories": [{"name": "Furniture"}], "images": [{"src": "https://w.shop/c1.jpg"}],
              "tags": [{"name": "chair"}], "attributes": [{"name": "Material", "options": ["Oak"]}]}]

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=items if page == 1 else [])

    monkeypatch.setattr(platforms, "transport", httpx.MockTransport(handler))
    integration = client.post("/api/integrations", headers=auth, json={"platform": "woocommerce", "name": "Woo", "config": {
        "store_url": "https://w.shop", "consumer_key": "ck_1", "consumer_secret": "cs_1"}}).json()
    client.patch(f"/api/integrations/{integration['id']}/sync-settings", headers=auth, json={"auto_sync_hours": 6})

    from app.services.store_sync import run_due_syncs

    assert run_due_syncs() == 1  # never synced -> due
    assert run_due_syncs() == 0  # just synced -> not due
    with SessionLocal() as db:
        db.get(Integration, integration["id"]).last_synced_at = datetime.now(timezone.utc) - timedelta(hours=7)
        db.commit()
    assert run_due_syncs() == 1
    runs = client.get(f"/api/integrations/{integration['id']}/syncs", headers=auth).json()
    assert [r["trigger"] for r in runs] == ["schedule", "schedule"] and runs[-1]["imported"] == 1
    product = client.get(f"/api/catalogs/{runs[0]['catalog_id']}/products", headers=auth).json()["items"][0]
    assert product["category"] == "Furniture" and product["attributes"] == {"Material": "Oak"}


# ---------- URL / Google Sheets import ----------
def test_google_sheets_url_conversion():
    url = "https://docs.google.com/spreadsheets/d/1AbC_x-9/edit#gid=12345"
    assert net.google_sheets_csv_url(url) == "https://docs.google.com/spreadsheets/d/1AbC_x-9/export?format=csv&gid=12345"
    assert net.google_sheets_csv_url("https://x.com/a.csv") == "https://x.com/a.csv"


def test_ssrf_guard():
    for url in ["http://127.0.0.1/x", "http://10.0.0.5/x", "file:///etc/passwd", "http://169.254.169.254/latest"]:
        with pytest.raises(net.UnsafeURLError):
            net.check_public_url(url)


def test_import_from_url(client, auth, monkeypatch):
    fetched = []
    monkeypatch.setattr(net, "fetch", lambda url: fetched.append(url) or SAMPLE_CSV.read_bytes())
    sheet = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"
    preview = client.post("/api/catalogs/preview-url", headers=auth, json={"url": sheet}).json()
    assert preview["row_count"] == 10 and preview["suggested_mapping"]["sku"] == "Product ID"
    result = client.post("/api/catalogs/import-url", headers=auth, json={"url": sheet, "name": "From Sheets"}).json()
    assert result["imported"] == 8 and result["catalog"]["source"] == "url"
    assert fetched[0] == "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=0"
