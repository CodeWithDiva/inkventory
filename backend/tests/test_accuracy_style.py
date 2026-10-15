from app.services import quality, seo
from app.services.generation import _call
from app.services.prompts import StyleSpec, build_system_prompt
from tests.conftest import upload_sample
from tests.test_quality_boost import GOOD, PRODUCT, Scripted, content


def test_unsupported_materials_and_claims_are_found():
    source = "Classic Leather Wallet Accessories color Brown material Genuine leather"
    text = "A brown leather wallet that is waterproof, handmade and comes with a lifetime warranty in black."
    assert quality.find_unsupported_claims(text, source) == ["waterproof", "handmade", "warranty", "lifetime", "black"]
    # With photos the model may see colors, so only colors are allowed.
    assert "black" not in quality.find_unsupported_claims(text, source, allow_visual=True)
    assert quality.find_unsupported_claims("Soft genuine leather in brown.", source) == []


def test_machine_written_style_is_detected():
    robotic = ("Discover the wallet. This wallet is soft. This wallet is slim. This wallet is brown. "
               "This wallet fits well. Elevate your style — effortlessly — today.")
    issues = quality.ai_style_issues(robotic, "en")
    joined = " | ".join(issues)
    assert "Discover" in joined and "em dashes" in joined and "this" in joined and "elevate" in joined
    natural = ("It's a slim wallet in brown genuine leather. Six cards fit in the front slots, and the cash "
               "pocket runs the full width. Leather like this darkens a little with use. Most people carry it "
               "in a front pocket. Short trips or long days, it stays flat.")
    assert quality.ai_style_issues(natural, "en") == []
    assert quality.ai_style_issues(robotic, "ur") == []


def test_invented_claim_triggers_revision():
    invented = content(GOOD.long_description + " It is also waterproof.")
    provider = Scripted(invented, GOOD)
    result = _call(provider, "system", PRODUCT, "en", [], 1, 1)
    assert len(provider.prompts) == 2 and "waterproof" in provider.prompts[1] and result.content == GOOD


def test_new_seo_checks():
    base = dict(title="Leather Wallet in Brown", short_description="A wallet.",
                long_description="It's a slim leather wallet. It holds cards and cash.", bullet_points=["a", "b", "c"],
                meta_title="Leather Wallet in Brown | Urban Craft",
                meta_description="A slim brown leather wallet for cards and cash. Shop the Urban Craft leather wallet online.",
                keywords=["leather wallet"])
    _, report = seo.score_content(**base)
    checks = {c["id"]: c["passed"] for c in report["checks"]}
    assert checks["kw_meta_title"] and checks["kw_title_front"] and checks["reading_ease"] and checks["meta_cta"]
    _, report = seo.score_content(**{**base, "meta_title": "Urban Craft accessories", "meta_description": "Nice."})
    checks = {c["id"]: c["passed"] for c in report["checks"]}
    assert not checks["kw_meta_title"] and not checks["meta_cta"]


def test_prompt_asks_for_human_voice_and_no_invented_claims():
    system = build_system_prompt(StyleSpec(), "en")
    assert "Sound like a person, not an AI" in system and "hypoallergenic" in system and '"delve"' in system


def test_dashboard_quality_attention_and_trend(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en"], "engine": "mock"})
    d = client.get("/api/dashboard", headers=auth).json()
    q = d["quality"]
    assert q["total"] == 8 and sum(q["seo_buckets"].values()) == 8 and 0 <= q["clean_share"] <= 100
    assert len(d["trend"]) == 14 and d["trend"][-1]["generated"] == 8
    for item in d["attention"]:
        assert item["catalog_id"] == catalog_id and (item["flags"] or item["seo_score"] < 70)


def test_brand_in_title_is_sent_back_for_own_store_but_kept_for_marketplaces():
    from app.services import refine

    branded = content(GOOD.long_description, title="Urban Craft Leather Wallet in Brown")
    provider = Scripted(branded, GOOD)
    result = _call(provider, "system", PRODUCT, "en", [], 1, 1)
    assert len(provider.prompts) == 2 and 'remove the brand "Urban Craft"' in provider.prompts[1]
    assert result.content == GOOD
    review = refine.review(branded, PRODUCT, "en", "medium", "amazon")
    assert not any(i.startswith(refine.TITLE_RULE) for i in review.issues)


def test_missing_title_keyword_is_always_sent_back_first():
    from app.services import refine

    no_keyword = content(GOOD.long_description, title="Slim Brown Bifold for Everyday Carry")
    review = refine.review(no_keyword, PRODUCT, "en", "medium", "generic")
    assert review.issues[0].startswith(refine.TITLE_RULE) and '"leather wallet"' in review.issues[0]
    assert refine.needs_revision(review, min_score=0)  # even when the score alone would pass
    provider = Scripted(no_keyword, GOOD)
    assert _call(provider, "system", PRODUCT, "en", [], 1, 1).content == GOOD and len(provider.prompts) == 2
