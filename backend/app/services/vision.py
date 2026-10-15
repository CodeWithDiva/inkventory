"""Photo analysis: a vision model studies the product photos before any copy is written.

It reports what a shopper can actually see (type, colors, visible details, style) separately from
what the product only *looks like* it is made of, and flags photos that don't match the product.
The writer then uses this report, so even text-only engines write from the photos, and the
accuracy checks know which visual details are backed by the photos.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.services import images as image_store
from app.services.llm import GenerationResult, LLMProvider
from app.services.prompts import ProductInput


class PhotoAnalysis(BaseModel):
    product_type: str = Field(description="What the product is, judged from the photos, in a few words")
    colors: list[str] = Field(description="Colors clearly visible on the product itself (not the background or props)")
    visible_features: list[str] = Field(description="3-8 short items: clearly visible parts, shape, pattern, "
                                                    "closures, stitching, finish or design details")
    style: str = Field(description="The overall look in a few words, e.g. minimalist, boho, sporty, classic")
    looks_like_materials: list[str] = Field(description="Materials the product only APPEARS to be made of, phrased "
                                                        "as appearance, e.g. 'leather-look strap', 'gold-tone metal'")
    summary: str = Field(description="1-2 plain sentences describing what a shopper sees in the photos")
    mismatch: str = Field(description="Only if the photos show a different kind of product than the one named, "
                                      "explain briefly; color or size variants are not a mismatch. Otherwise an "
                                      "empty string")

    @classmethod
    def mock_example(cls, user_prompt: str) -> "PhotoAnalysis":
        name = re.search(r"Product name: (.+)", user_prompt)
        product = name.group(1).strip() if name else "product"
        return cls(product_type=product.lower(), colors=["brown"], visible_features=["clean stitched edges"],
                   style="classic", looks_like_materials=[], summary=f"A {product.lower()} shown on a plain background.",
                   mismatch="")


SYSTEM = (
    "You are a meticulous e-commerce cataloguer. You study product photos and report exactly what a shopper "
    "can see, so a copywriter can describe the product accurately.\n"
    "- Report only what is visible on the product itself. Ignore the background, props, packaging and models.\n"
    "- Never guess brand, size, weight, capacity, price, certifications, care rules or performance.\n"
    "- Materials cannot be confirmed from a photo: list them only under looks_like_materials and phrase them as "
    "appearance (\"leather-look\", \"gold-tone\", \"wood-grain finish\").\n"
    "- When several photos are given, combine what they show.\n"
    "- If the photos clearly show a different kind of product than the one named, explain it in mismatch. "
    "Different colors, sizes or variants of the same product are NOT a mismatch; leave mismatch empty then."
)


def fingerprint(refs: list[str]) -> str:
    """Identifies the photo set an analysis belongs to; a new photo makes the cached analysis stale."""
    return hashlib.sha256(json.dumps(refs[: image_store.MAX_IMAGES_PER_REQUEST]).encode()).hexdigest()[:16]


def cached(images: list[str] | None, analysis: dict | None) -> dict | None:
    if images and analysis and analysis.get("refs_hash") == fingerprint(images):
        return analysis
    return None


def analyze(provider: LLMProvider, product: ProductInput, refs: list[str]) -> tuple[dict | None, GenerationResult | None]:
    """Run the vision model over the product's photos. Returns (analysis, raw result) or (None, None)."""
    loaded = image_store.load(refs)
    if not loaded or not provider.supports_images:
        return None, None
    data = {k: v for k, v in {"category": product.category, "brand": product.brand,
                              "attributes": product.attributes}.items() if v}
    user = (f"Product name: {product.name}\n"
            f"Product data from the merchant: {json.dumps(data, ensure_ascii=False)}\n"
            f"{len(loaded)} photo(s) of this product are attached. Describe what they show.")
    result = provider.generate(SYSTEM, user, images=loaded, output_model=PhotoAnalysis)
    analysis = {
        **result.content.model_dump(),
        "refs_hash": fingerprint(refs),
        "photos": len(loaded),
        "model": result.model,
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return analysis, result


def visible_facts(analysis: dict | None) -> str:
    """The parts of an analysis that count as evidence for accuracy checks.

    Materials and the free-text summary are left out: they may contain guesses.
    """
    if not analysis:
        return ""
    return " ".join([analysis.get("product_type", ""), " ".join(analysis.get("colors", [])),
                     " ".join(analysis.get("visible_features", [])), analysis.get("style", "")])
