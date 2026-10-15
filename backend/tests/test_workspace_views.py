from tests.conftest import register, upload_sample


def test_catalog_cards_and_workspace_products(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    pid = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"][0]["id"]
    client.post(f"/api/products/{pid}/image-urls", headers=auth, json={"url": "https://example.com/a.jpg"})

    card = client.get("/api/catalogs", headers=auth).json()[0]
    assert card["thumbnails"] == [{"product_id": pid, "index": 0, "url": "https://example.com/a.jpg"}]
    assert card["generated"] == 0 and card["last_job_status"] is None

    job = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en"], "engine": "mock"})
    assert job.status_code in (200, 201), job.text
    card = client.get("/api/catalogs", headers=auth).json()[0]
    assert card["generated"] == 8 and card["pending_review"] == 8 and card["last_job_status"] == "completed"

    page = client.get("/api/products?status=pending", headers=auth).json()
    assert page["total"] == 8 and page["items"][0]["catalog_name"] == card["name"]
    assert client.get("/api/products?status=approved", headers=auth).json()["total"] == 0

    other = register(client, email="other@shop.com", org="Shop Two")
    assert client.get("/api/products", headers=other).json()["total"] == 0
