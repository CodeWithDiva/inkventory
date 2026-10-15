from app.services.llm import LLMError, set_provider
from app.services.llm.mock_provider import MockProvider
from tests.conftest import SAMPLE_CSV, register, upload_sample


def test_auth_flow(client):
    headers = register(client)
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["role"] == "owner" and me["organization"]["name"] == "Shop One"

    r = client.post("/api/auth/login", data={"username": "owner@shop.com", "password": "password123"})
    assert r.status_code == 200
    assert client.post("/api/auth/login", data={"username": "owner@shop.com", "password": "wrong"}).status_code == 401
    assert client.get("/api/catalogs").status_code == 401


def test_preview_and_upload(client, auth):
    with SAMPLE_CSV.open("rb") as f:
        preview = client.post("/api/catalogs/preview", headers=auth, files={"file": ("p.csv", f, "text/csv")}).json()
    assert preview["row_count"] == 10
    assert preview["suggested_mapping"]["sku"] == "Product ID"

    result = upload_sample(client, auth)
    assert result["imported"] == 8 and len(result["errors"]) == 2
    catalog_id = result["catalog"]["id"]

    page = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()
    assert page["total"] == 8
    search = client.get(f"/api/catalogs/{catalog_id}/products?search=mug", headers=auth).json()
    assert [p["sku"] for p in search["items"]] == ["SKU-1005"]


def test_api_import_upserts_by_sku(client, auth):
    r = client.post("/api/catalogs/api", headers=auth, json={
        "name": "API catalog", "products": [{"sku": "A1", "name": "Desk"}, {"sku": "A2", "name": "Chair"}],
    })
    assert r.status_code == 201 and r.json()["imported"] == 2
    catalog_id = r.json()["catalog"]["id"]
    r = client.post(f"/api/catalogs/{catalog_id}/products", headers=auth, json={
        "products": [{"sku": "A1", "name": "Standing Desk"}, {"sku": "A3", "name": "Lamp"}],
    })
    assert r.json()["imported"] == 1 and r.json()["updated"] == 1
    assert r.json()["catalog"]["product_count"] == 3


def test_tenant_isolation(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    other = register(client, email="other@shop.com", org="Shop Two")
    assert client.get(f"/api/catalogs/{catalog_id}", headers=other).status_code == 404
    assert client.get("/api/catalogs", headers=other).json() == []
    assert client.delete(f"/api/catalogs/{catalog_id}", headers=other).status_code == 404


def test_full_generation_review_export_flow(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    presets = client.get("/api/presets", headers=auth).json()
    assert len(presets) >= 5 and all(p["is_builtin"] for p in presets)

    body = {"catalog_id": catalog_id, "preset_id": presets[0]["id"], "languages": ["en", "ur"]}
    estimate = client.post("/api/jobs/estimate", headers=auth, json=body).json()
    assert estimate["items"] == 16

    job = client.post("/api/jobs", headers=auth, json=body).json()
    job = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
    assert job["status"] == "completed" and job["completed"] == 16 and job["failed"] == 0

    catalog = client.get(f"/api/catalogs/{catalog_id}", headers=auth).json()
    assert catalog["stats"]["by_language"]["en"]["draft"] == 8
    assert catalog["stats"]["avg_seo_score"] > 0

    product = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"][0]
    current = product["current"]["en"]
    version = client.get(f"/api/descriptions/{current['id']}", headers=auth).json()
    assert version["seo_report"]["checks"] and version["version_no"] == 1

    # Manual edit creates version 2 and keeps version 1 in history.
    edit = {k: version[k] for k in ("title", "short_description", "long_description", "bullet_points", "meta_title", "meta_description")}
    edit["title"] = "Edited title for leather wallet"
    v2 = client.put(f"/api/descriptions/{version['id']}", headers=auth, json=edit).json()
    assert v2["version_no"] == 2 and v2["source"] == "manual" and v2["is_current"]
    history = client.get(f"/api/products/{product['id']}/versions?language=en", headers=auth).json()
    assert [v["version_no"] for v in history] == [2, 1]

    # Restore v1 -> becomes v3.
    v3 = client.post(f"/api/descriptions/{version['id']}/restore", headers=auth).json()
    assert v3["version_no"] == 3 and v3["title"] == version["title"]

    # Regenerate -> v4.
    v4 = client.post(f"/api/products/{product['id']}/regenerate", headers=auth, json={"language": "en"}).json()
    assert v4["version_no"] == 4

    # Approve one, reject one, bulk approve the rest.
    assert client.post(f"/api/descriptions/{v4['id']}/approve", headers=auth, json={}).json()["status"] == "approved"
    r = client.post(f"/api/catalogs/{catalog_id}/approve-drafts", headers=auth,
                    json={"language": "en", "min_seo_score": 0, "skip_flagged": False})
    assert r.json()["updated"] == 7

    # CSV export of approved English descriptions.
    for fmt, header in [("csv_generic", "sku,language,title"), ("csv_shopify", "Handle,Title,Body (HTML)"),
                        ("csv_woocommerce", "SKU,Name,Short description")]:
        export = client.post("/api/exports", headers=auth, json={"catalog_id": catalog_id, "language": "en", "format": fmt}).json()
        export = client.get(f"/api/exports/{export['id']}", headers=auth).json()
        assert export["status"] == "completed" and export["succeeded"] == 8, export
        csv_text = client.get(f"/api/exports/{export['id']}/download", headers=auth).content.decode("utf-8-sig")
        assert csv_text.startswith(header)

    # Urdu has no approved descriptions yet -> empty export.
    export = client.post("/api/exports", headers=auth, json={"catalog_id": catalog_id, "language": "ur", "format": "csv_generic"}).json()
    assert client.get(f"/api/exports/{export['id']}", headers=auth).json()["total"] == 0

    dashboard = client.get("/api/dashboard", headers=auth).json()
    assert dashboard["products"] == 8 and dashboard["descriptions"]["approved"] == 8
    assert dashboard["usage"]["this_month"] == 17  # 16 from the job + 1 regenerate


def test_only_missing_skips_existing(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    first = client.get(f"/api/catalogs/{catalog_id}/products?page_size=2", headers=auth).json()["items"]
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "product_ids": [p["id"] for p in first]})
    estimate = client.post("/api/jobs/estimate", headers=auth, json={"catalog_id": catalog_id, "only_missing": True}).json()
    assert estimate["items"] == 6
    missing = client.get(f"/api/catalogs/{catalog_id}/products?status=missing", headers=auth).json()
    assert missing["total"] == 6


def test_failures_are_recorded_per_product(client, auth):
    class FlakyProvider(MockProvider):
        def generate(self, system_prompt, user_prompt, **kwargs):
            if "SKU-1003" in user_prompt:
                raise LLMError("Simulated API failure")
            return super().generate(system_prompt, user_prompt, **kwargs)

    set_provider(FlakyProvider())
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    job = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id}).json()
    job = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
    assert job["status"] == "completed" and job["completed"] == 7 and job["failed"] == 1
    assert job["errors"] == [{"sku": "SKU-1003", "language": "en", "error": "Simulated API failure"}]


def test_roles_and_quota(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    r = client.post("/api/users", headers=auth, json={
        "email": "rev@shop.com", "full_name": "Reviewer", "password": "password123", "role": "reviewer",
    })
    assert r.status_code == 201
    token = client.post("/api/auth/login", data={"username": "rev@shop.com", "password": "password123"}).json()
    reviewer = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.post("/api/jobs", headers=reviewer, json={"catalog_id": catalog_id}).status_code == 403
    assert client.post("/api/users", headers=reviewer, json={
        "email": "x@shop.com", "full_name": "X", "password": "password123"}).status_code == 403

    from app.db import SessionLocal
    from app.models import Organization

    with SessionLocal() as db:
        org = db.query(Organization).first()
        org.monthly_generation_limit = 3
        db.commit()
    r = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id})
    assert r.status_code == 402


def test_integration_secrets_are_masked(client, auth):
    r = client.post("/api/integrations", headers=auth, json={
        "platform": "shopify", "name": "My store",
        "config": {"shop_domain": "demo.myshopify.com", "access_token": "shpat_supersecret1234"},
    })
    assert r.status_code == 201
    assert r.json()["config"]["access_token"] == "••••1234"
    bad = client.post("/api/integrations", headers=auth, json={"platform": "woocommerce", "name": "x", "config": {}})
    assert bad.status_code == 400
