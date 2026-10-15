from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


class GeneratedContent(BaseModel):
    """Structured output every provider must return for a product description."""

    title: str = Field(description="Product page title")
    short_description: str = Field(description="1-2 sentence summary, max 300 characters")
    long_description: str = Field(description="Main description, plain-text paragraphs")
    bullet_points: list[str] = Field(description="Benefit-led bullet points")
    meta_title: str = Field(description="SEO title, 30-60 characters")
    meta_description: str = Field(description="SEO meta description, 120-160 characters")


T = TypeVar("T", bound=BaseModel)


@dataclass
class ImageInput:
    """A product image for vision-capable models: either a public URL or raw bytes."""

    url: str | None = None
    data: bytes | None = None
    media_type: str = "image/jpeg"


@dataclass
class GenerationResult(Generic[T]):
    content: T
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    used_images: int = 0
    notes: list[str] = field(default_factory=list)
    photo_analysis: dict | None = None  # set when photos were analysed for this result


class LLMError(Exception):
    """Generation failed for this product (after retries)."""


class LLMRefusal(LLMError):
    """The model declined to generate content for this input."""


class LLMQuotaExceeded(LLMError):
    """The account's daily/monthly allowance is used up: retrying soon is pointless, so jobs stop."""


# USD per 1M tokens (input, output); used for job cost estimates and tracking.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "mock": (0.0, 0.0),
    # Ollama models run locally and are free; unknown model names also cost 0.
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


class LLMProvider(ABC):
    name: str
    model: str
    supports_images: bool = False

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        images: list[ImageInput] | None = None,
        output_model: type[T] = GeneratedContent,
    ) -> GenerationResult[T]: ...
