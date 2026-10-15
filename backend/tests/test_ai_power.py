"""Images, variants, marketplace channels, glossary and brand-voice learning."""

import io

from app.services import csv_import
from app.services.llm.ollama_provider import OllamaProvider
from tests.conftest import upload_sample

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def first_product(client, auth, catalog_id):
    return client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"][0]


def test_csv_image_column():
    parsed = csv_import.parse_csv(b"sku,name,Image URL\nA1,Chair,https://cdn.x.com/a.jpg | https://cdn.x.com/b.png\n")
    mapping = csv_import.suggest_mapping(parsed.headers)
    assert mapping["images"] == "Image URL"
    products, _ = csv_import.build_products(parsed, mapping)
    assert products[0].images == ["https://cdn.x.com/a.jpg", "https://cdn.x.com/b.png"]


def test_product_photos_feed_the_ai(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    product = first_product(client, auth, catalog_id)

    bad = client.post(f"/api/products/{product['id']}/images", headers=auth,
                      files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")})
    assert bad.status_code == 400
    r = client.post(f"/api/products/{product['id']}/images", headers=auth,
                    files={"file": ("photo.png", io.BytesIO(PNG), "image/png")})
    assert r.status_code == 200 and r.json()["images"][0].startswith("storage:")
    assert client.get(f"/api/products/{product['id']}/images/0", headers=auth).content == PNG
    client.post(f"/api/products/{product['id']}/image-urls", headers=auth, json={"url": "https://cdn.x.com/p.jpg"})

    v = client.post(f"/api/products/{product['id']}/regenerate", headers=auth, json={"language": "en"}).json()
    # The photos were analysed first and the writer used that report.
    assert "As the product photos show" in v["long_description"] and "classic look" in v["long_description"]
    analysis = client.get(f"/api/products/{product['id']}", headers=auth).json()["image_analysis"]
    assert analysis["style"] == "classic" and analysis["photos"] == 2
    v = client.post(f"/api/products/{product['id']}/regenerate", headers=auth,
                    json={"language": "en", "use_images": False}).json()
    assert "product photos show" not in v["long_description"]

    estimate = client.post("/api/jobs/estimate", headers=auth, json={"catalog_id": catalog_id}).json()
    assert estimate["products_with_images"] == 1

    after = client.delete(f"/api/products/{product['id']}/images/0", headers=auth).json()
    assert after["images"] == ["https://cdn.x.com/p.jpg"]


def test_variants_pick_best_and_switch(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    estimate = client.post("/api/jobs/estimate", headers=auth, json={"catalog_id": catalog_id, "variants": 3}).json()
    assert estimate["items"] == 24
    job = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "variants": 3}).json()
    job = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
    assert job["completed"] == 24 and job["variants"] == 3

    product = first_product(client, auth, catalog_id)
    versions = client.get(f"/api/products/{product['id']}/versions?language=en", headers=auth).json()
    assert len(versions) == 3 and {v["job_id"] for v in versions} == {job["id"]}
    assert len({v["long_description"] for v in versions}) == 3  # genuinely different
    current = next(v for v in versions if v["is_current"])
    assert current["seo_score"] == max(v["seo_score"] for v in versions)

    other = next(v for v in versions if not v["is_current"])
    client.post(f"/api/descriptions/{other['id']}/make-current", headers=auth)
    versions = client.get(f"/api/products/{product['id']}/versions?language=en", headers=auth).json()
    assert [v["id"] for v in versions if v["is_current"]] == [other["id"]] and len(versions) == 3


def test_amazon_channel_rules_and_export(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    preset = client.post("/api/presets", headers=auth, json={
        "name": "Amazon listings", "tone": "professional", "length": "medium", "channel": "amazon"}).json()
    assert preset["channel"] == "amazon"
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "preset_id": preset["id"]})
    product = first_product(client, auth, catalog_id)
    v = client.get(f"/api/products/{product['id']}/versions?language=en", headers=auth).json()[0]
    ids = {c["id"]: c for c in v["seo_report"]["checks"]}
    assert ids["bullets"]["label"] == "5-5 bullet points" and not ids["bullets"]["passed"]  # mock writes 3
    assert ids["ch_banned"]["passed"] and ids["ch_desc_len"]["passed"]
    assert ids["title_len"]["label"] == "Title length 20-200 chars"

    client.post(f"/api/catalogs/{catalog_id}/approve-drafts", headers=auth,
                json={"language": "en", "min_seo_score": 0, "skip_flagged": False})
    for fmt, header in [("csv_amazon", "item_sku,item_name,bullet_point1"), ("csv_ebay", "Custom label (SKU),Title"),
                        ("csv_daraz", "SellerSku,name,short_description")]:
        export = client.post("/api/exports", headers=auth, json={"catalog_id": catalog_id, "format": fmt}).json()
        text = client.get(f"/api/exports/{export['id']}/download", headers=auth).content.decode("utf-8-sig")
        assert text.startswith(header), fmt


def test_channel_banned_phrases_flagged():
    from app.services.channels import channel_checks, get_channel

    checks = {c["id"]: c for c in channel_checks(get_channel("amazon"), title="Best Seller Mug",
                                                 bullet_points=["a"] * 5, long_description="Free shipping today")}
    assert not checks["ch_banned"]["passed"] and "free shipping" in checks["ch_banned"]["detail"]


def test_glossary_replaces_and_flags(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    assert client.post("/api/glossary", headers=auth, json={"term": "everyday", "kind": "avoid",
                                                             "replacement": "daily"}).status_code == 201
    client.post("/api/glossary", headers=auth, json={"term": "dependable", "kind": "avoid"})
    client.post("/api/glossary", headers=auth, json={"term": "HydroPeak", "kind": "keep"})
    assert client.post("/api/glossary", headers=auth, json={"term": "everyday", "kind": "keep"}).status_code == 409
    assert len(client.get("/api/glossary", headers=auth).json()) == 3

    product = first_product(client, auth, catalog_id)
    v = client.post(f"/api/products/{product['id']}/regenerate", headers=auth, json={"language": "en"}).json()
    assert "everyday" not in v["long_description"].lower() and "daily use" in v["long_description"]
    types = {w["type"] for w in v["warnings"]}
    assert {"glossary_fixed", "glossary"} <= types


def test_learn_brand_voice(client, auth):
    samples = [
        "You will love how this lamp brightens your desk. Its soft light keeps your eyes relaxed while you work late.",
        "Your mornings deserve better coffee. This mug keeps your drink warm so you can enjoy every single sip slowly.",
        "Take your yoga practice anywhere you go. The mat grips the floor so you can focus on your breathing and flow.",
    ]
    assert client.post("/api/presets/learn-voice", headers=auth, json={"name": "Our voice", "samples": samples[:2]}).status_code == 400
    r = client.post("/api/presets/learn-voice", headers=auth, json={"name": "Our voice", "samples": samples})
    assert r.status_code == 201
    body = r.json()
    assert body["samples_used"] == 3 and body["preset"]["tone"] == "friendly"
    assert len(body["preset"]["examples"]) == 3 and "you" in body["preset"]["instructions"].lower()


def test_ollama_vision_detection():
    assert not OllamaProvider("qwen2.5:7b").supports_images
    assert OllamaProvider("llava:7b").supports_images
    assert OllamaProvider("qwen2.5vl:7b").supports_images
