from app.services import vision
from app.services.llm import set_provider
from app.services.llm.mock_provider import MockProvider
from app.services.prompts import ProductInput, build_user_prompt
from tests.conftest import upload_sample


class CountingVision(MockProvider):
    """Mock that counts photo-analysis calls and can report a mismatching photo."""

    def __init__(self, mismatch: str = ""):
        self.analyses, self.mismatch = 0, mismatch

    def generate(self, system_prompt, user_prompt, *, images=None, output_model=None, **kw):
        if output_model is vision.PhotoAnalysis:
            self.analyses += 1
            result = super().generate(system_prompt, user_prompt, images=images, output_model=output_model)
            result.content = result.content.model_copy(update={
                "colors": ["black", "gold"], "looks_like_materials": ["leather-look band"], "mismatch": self.mismatch})
            return result
        return super().generate(system_prompt, user_prompt, images=images, **({"output_model": output_model} if output_model else {}))


def add_photo(client, auth, product_id, url="https://cdn.example.com/a.jpg"):
    return client.post(f"/api/products/{product_id}/image-urls", headers=auth, json={"url": url}).json()


def test_cache_follows_the_photo_set():
    analysis = {"refs_hash": vision.fingerprint(["https://x/a.jpg"]), "colors": ["red"]}
    assert vision.cached(["https://x/a.jpg"], analysis) == analysis
    assert vision.cached(["https://x/a.jpg", "https://x/b.jpg"], analysis) is None  # new photo -> stale
    assert vision.cached([], analysis) is None


def test_writer_gets_the_report_and_unconfirmed_materials_rule():
    product = ProductInput(sku="A", name="Anchor Bracelet", photo_analysis={
        "product_type": "bracelet", "colors": ["black"], "visible_features": ["anchor charm"], "style": "nautical",
        "summary": "A black cord bracelet with an anchor.", "looks_like_materials": ["gold-tone metal"]})
    prompt = build_user_prompt(product)
    assert "<seen_in_photos>" in prompt and "anchor charm" in prompt
    assert "NOT confirmed: gold-tone metal" in prompt
    assert "anchor charm" in product.source_text() and "gold-tone" not in product.source_text()


def test_job_analyses_each_photo_once_and_reuses_it(client, auth):
    provider = CountingVision()
    set_provider(provider)
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    items = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"]
    add_photo(client, auth, items[0]["id"])
    add_photo(client, auth, items[1]["id"])

    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en", "ur"]})
    assert provider.analyses == 2  # two products with photos, two languages: still one analysis each
    product = client.get(f"/api/products/{items[0]['id']}", headers=auth).json()
    assert product["image_analysis"]["colors"] == ["black", "gold"]
    current = client.get(f"/api/products/{items[0]['id']}/versions?language=en", headers=auth).json()[0]
    assert "As the product photos show" in current["long_description"]

    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en"], "only_missing": False})
    assert provider.analyses == 2  # cached
    add_photo(client, auth, items[0]["id"], "https://cdn.example.com/b.jpg")
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en"], "only_missing": False})
    assert provider.analyses == 3  # only the product whose photos changed

    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en"], "only_missing": False,
                                                  "use_images": False})
    assert provider.analyses == 3


def test_analyze_button_and_photo_mismatch_warning(client, auth):
    provider = CountingVision(mismatch="The photo shows a pair of shoes, not a wallet.")
    set_provider(provider)
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    product = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"][0]
    assert client.post(f"/api/products/{product['id']}/analyze-photos", headers=auth, json={}).status_code == 400

    add_photo(client, auth, product["id"])
    r = client.post(f"/api/products/{product['id']}/analyze-photos", headers=auth, json={})
    assert r.status_code == 200 and r.json()["image_analysis"]["mismatch"].startswith("The photo shows")
    client.post(f"/api/products/{product['id']}/analyze-photos", headers=auth, json={})
    assert provider.analyses == 2  # the button always re-analyses

    v = client.post(f"/api/products/{product['id']}/regenerate", headers=auth, json={"language": "en"}).json()
    assert provider.analyses == 2  # regenerate reuses the saved analysis
    assert any(w["type"] == "photo_mismatch" for w in v["warnings"])
