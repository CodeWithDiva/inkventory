"""Deterministic offline provider for tests, demos and local development without an API key.

It builds content from the product data embedded in the user prompt, so the rest of the
pipeline (SEO scoring, review, export) can be exercised end to end.
"""

import json
import re

from app.services.llm.base import GeneratedContent, GenerationResult, ImageInput, LLMProvider, T


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock"
    supports_images = True

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        images: list[ImageInput] | None = None,
        output_model: type[T] = GeneratedContent,
    ) -> GenerationResult[T]:
        words_in = len(system_prompt.split()) + len(user_prompt.split())
        if output_model is not GeneratedContent:
            # Other structured outputs (e.g. brand voice profiles) provide their own sample.
            return GenerationResult(content=output_model.mock_example(user_prompt), model=self.model,
                                    input_tokens=int(words_in * 1.3), output_tokens=150)

        match = re.search(r"<product_data>\s*(\{.*?\})\s*</product_data>", user_prompt, re.S)
        data = json.loads(match.group(1)) if match else {}
        kw_match = re.search(r"Target SEO keywords \(first one is primary\): (.*)$", user_prompt, re.M)
        keywords = [] if not kw_match or "none provided" in kw_match.group(1) else [
            k.strip() for k in kw_match.group(1).split(",") if k.strip()
        ]
        variant = re.search(r"Variant (\d+) of \d+", user_prompt)
        angle = ["designed for everyday use", "made to stand out", "built for people who value quality"][
            (int(variant.group(1)) - 1) % 3 if variant else 0
        ]

        name = data.get("name", "Product")
        brand = data.get("brand")
        category = data.get("category") or "product"
        attributes: dict = data.get("attributes", {})
        primary = keywords[0] if keywords else name.lower()
        brand_part = f" from {brand}" if brand else ""
        features = [f"{k.replace('_', ' ').title()}: {v}" for k, v in list(attributes.items())[:5]]

        detail = "; ".join(f"{f.split(': ', 1)[0].lower()} {f.split(': ', 1)[1]}" for f in features)
        paragraphs = [
            f"The {name}{brand_part} is a {primary} {angle}. "
            f"Pick it up and you'll notice the care that went into this {category.lower()}.",
            (f"Here are the details that matter: {detail}. " if detail else "")
            + "Each one was chosen with daily use in mind, so it stays dependable long after the first week.",
            f"Upgrading or buying your first {category.lower()}? Either way, the {name} is a practical choice. "
            f"Related searches include {', '.join(keywords[1:3]) or primary}.",
        ]
        seen = re.search(r"<seen_in_photos>\s*(\{.*?\})\s*</seen_in_photos>", user_prompt, re.S)
        if seen:
            look = json.loads(seen.group(1))
            paragraphs.append(f"As the product photos show, the {name} has a {look.get('style', 'clean')} look"
                              + (f" in {', '.join(look['colors'])}." if look.get("colors") else "."))
        elif images:
            paragraphs.append(f"As the product photos show, the {name} has a clean, finished look.")
        long_description = "\n\n".join(paragraphs)
        content = GeneratedContent(
            title=f"{name}{brand_part}"[:70],
            short_description=f"The {name} is a {primary} that balances quality, comfort and value."[:300],
            long_description=long_description,
            bullet_points=features or [f"Quality {category.lower()}", f"Designed by {brand or 'experts'}", "Easy to use"],
            meta_title=f"{name} | {primary.title()}"[:60],
            meta_description=(
                f"Shop the {name}{brand_part}, a {primary} {angle}. "
                f"Quality design and great value. Order online today."
            )[:160],
        )
        return GenerationResult(
            content=content,
            model=self.model,
            input_tokens=int(words_in * 1.3),
            output_tokens=int(len(long_description.split()) * 1.3) + 80,
            used_images=len(images or []),
        )
