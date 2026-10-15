"""Self-review: check a draft the way a reviewer would, and ask the model to fix what is wrong.

The first draft is scored with the same SEO and quality checks reviewers see. When it falls
short, the model gets one revision request listing the concrete problems; the better of the
two drafts is kept. This lifts weak drafts without paying for a second call on good ones.
"""

import json
from dataclasses import dataclass, field

from app.services import quality, seo
from app.services.llm.base import GeneratedContent
from app.services.prompts import LANGUAGES, MARKETPLACES, ProductInput


@dataclass
class Review:
    score: int
    issues: list[str] = field(default_factory=list)
    serious: int = 0  # invented numbers, wrong language: these matter more than a few SEO points

    def better_than(self, other: "Review") -> bool:
        return (self.serious, -self.score, len(self.issues)) < (other.serious, -other.score, len(other.issues))


def review(content: GeneratedContent, product: ProductInput, language: str, length: str, channel: str,
           allow_visual: bool = False) -> Review:
    score, report = seo.score_content(
        title=content.title, short_description=content.short_description, long_description=content.long_description,
        bullet_points=content.bullet_points, meta_title=content.meta_title, meta_description=content.meta_description,
        keywords=product.keywords or [], language=language, length=length, channel=channel,
    )
    issues = [f"{c['label']}: {c['detail']}" for c in report["checks"] if not c["passed"]]
    serious = 0
    text = " ".join([content.title, content.short_description, content.long_description,
                     *content.bullet_points, content.meta_description])
    numbers = quality.find_unsupported_numbers(text, product.source_text())
    if numbers:
        serious += 1
        issues.append(f"These numbers are not in the product data, remove them or use the data's values: "
                      f"{', '.join(numbers)}")
    if quality.wrong_language(content.long_description, language):
        serious += 1
        issues.append(f"The text is not written in {LANGUAGES.get(language, language)}; rewrite every field in it")
    claims = quality.find_unsupported_claims(
        text, product.source_text(), allow_visual,
        appearance_only=(product.photo_analysis or {}).get("looks_like_materials"))
    if claims:
        serious += 1
        issues.append(f"These materials/colors/claims are not in the product data, remove them: {', '.join(claims)}")
    if product.brand and channel not in MARKETPLACES and product.brand.lower() in content.title.lower():
        issues.append(TITLE_RULE + f'remove the brand "{product.brand}" from the title (mention it in the description)')
    style = quality.ai_style_issues(" ".join([content.short_description, content.long_description]), language)
    if style:
        issues.append(HUMAN + "; ".join(style))
    return Review(score=score, issues=issues, serious=serious)


HUMAN = "Make it read like a person wrote it: "
TITLE_RULE = "Title rule: "


def needs_revision(result: Review, min_score: int) -> bool:
    return (result.serious > 0 or result.score < min_score
            or any(i.startswith((HUMAN, TITLE_RULE)) for i in result.issues))


def revision_prompt(original_user_prompt: str, content: GeneratedContent, issues: list[str]) -> str:
    draft = json.dumps(content.model_dump(), ensure_ascii=False, indent=2)
    problems = "\n".join(f"- {i}" for i in issues)
    return (
        f"{original_user_prompt}\n\n"
        "A first draft was written and reviewed. Rewrite it so that it fixes every problem below while "
        "keeping all the rules above. Keep what already works; only use facts from the product data.\n\n"
        f"<first_draft>\n{draft}\n</first_draft>\n\n"
        f"<problems>\n{problems}\n</problems>\n\n"
        "Return the complete improved content in the same format."
    )
