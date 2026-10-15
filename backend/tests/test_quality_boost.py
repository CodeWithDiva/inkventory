from app.config import get_settings
from app.db import SessionLocal
from app.services import quality
from app.services.generation import _call, approved_examples
from app.services.llm.base import GeneratedContent, GenerationResult, LLMError, LLMProvider
from app.services.prompts import ProductInput, StyleSpec, build_system_prompt, build_user_prompt
from tests.conftest import upload_sample

PRODUCT = ProductInput(sku="W-1", name="Classic Leather Wallet", category="Accessories", brand="Urban Craft",
                       attributes={"material": "Genuine leather", "color": "Brown"}, keywords=["leather wallet"])


def content(long: str, title: str = "Leather Wallet in Brown Genuine Leather") -> GeneratedContent:
    return GeneratedContent(
        title=title, short_description="A slim leather wallet in brown genuine leather.",
        long_description=long, bullet_points=["Soft feel - genuine leather", "Classic look - rich brown color",
                                              "Everyday carry - slim profile"],
        meta_title="Leather Wallet in Brown Genuine Leather | Urban Craft",
        meta_description="Shop the Urban Craft leather wallet in brown genuine leather: a slim, classic everyday "
                         "wallet that feels soft in your hand. Order yours today.",
    )


WEAK = content("This leather wallet comes with a 5 year warranty and will elevate your style. It is good.")
GOOD = content("This leather wallet from Urban Craft is made of brown genuine leather that feels soft in your hand. "
               "Its slim shape slides easily into a pocket.\n\nThe classic brown color suits work and weekend outfits. "
               "Genuine leather wears in beautifully with use, so the wallet looks better over time.")


class Scripted(LLMProvider):
    name = model = "scripted"

    def __init__(self, *drafts, fail_after: int | None = None):
        self.drafts, self.prompts, self.fail_after = list(drafts), [], fail_after

    def generate(self, system_prompt, user_prompt, *, images=None, output_model=GeneratedContent):
        self.prompts.append(user_prompt)
        if self.fail_after is not None and len(self.prompts) > self.fail_after:
            raise LLMError("rate limited")
        return GenerationResult(content=self.drafts[min(len(self.prompts), len(self.drafts)) - 1], model=self.model,
                                input_tokens=100, output_tokens=50)


def test_weak_draft_is_revised_with_concrete_problems(client):
    provider = Scripted(WEAK, GOOD)
    result = _call(provider, "system", PRODUCT, "en", [], 1, 1)
    assert len(provider.prompts) == 2
    revision = provider.prompts[1]
    assert "<first_draft>" in revision and "5" in revision and "elevate" in revision
    assert result.content == GOOD
    assert (result.input_tokens, result.output_tokens) == (200, 100)  # both calls are counted


def test_good_draft_is_not_sent_again(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "auto_improve_min_score", 0)
    provider = Scripted(GOOD)
    result = _call(provider, "system", PRODUCT, "en", [], 1, 1)
    assert len(provider.prompts) == 1 and result.content == GOOD


def test_worse_revision_or_failed_revision_keeps_first_draft(client):
    assert _call(Scripted(GOOD, WEAK), "system", PRODUCT, "en", [], 1, 1).content == GOOD
    assert _call(Scripted(WEAK, fail_after=1), "system", PRODUCT, "en", [], 1, 1).content == WEAK


def test_auto_improve_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "auto_improve", False)
    provider = Scripted(WEAK, GOOD)
    assert _call(provider, "system", PRODUCT, "en", [], 1, 1).content == WEAK and len(provider.prompts) == 1


def test_prompt_has_craft_rules_category_focus_and_cliche_check():
    system = build_system_prompt(StyleSpec(), "en")
    assert "Copywriting craft" in system and "Turn features into benefits" in system
    assert "shoppers care most about: style" in build_user_prompt(PRODUCT)
    assert "comfort and support" in build_user_prompt(ProductInput(sku="S", name="Trail Running Shoes",
                                                                   category="Footwear"))
    assert quality.find_cliches("A must-have that will elevate your look", "en") == ["elevate", "must-have"]
    assert quality.find_cliches("A must-have", "ur") == []


def test_team_approved_edits_become_style_examples(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "languages": ["en"], "engine": "mock"})
    first = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"][0]
    version_id = first["current"]["en"]["id"]
    version = client.get(f"/api/descriptions/{version_id}", headers=auth).json()
    edited_text = "Hand-edited by the team. " + version["long_description"]
    edited = client.put(f"/api/descriptions/{version_id}", headers=auth, json={
        **{k: version[k] for k in ("title", "short_description", "bullet_points", "meta_title", "meta_description")},
        "long_description": edited_text,
    }).json()

    with SessionLocal() as db:
        org_id = db.execute(__import__("sqlalchemy").text("select org_id from catalogs where id=:i"),
                            {"i": catalog_id}).scalar()
        assert approved_examples(db, org_id, catalog_id, "en") == []  # nothing approved yet
        client.post(f"/api/descriptions/{edited['id']}/approve", headers=auth, json={})
        examples = approved_examples(db, org_id, catalog_id, "en")
        assert examples and examples[0] == edited_text
        assert approved_examples(db, org_id, catalog_id, "en", exclude_product=first["id"]) == []

    system = build_system_prompt(StyleSpec(), "en", approved_examples=examples)
    assert "Descriptions your client's team approved" in system and "Hand-edited by the team." in system
