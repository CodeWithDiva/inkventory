import base64
from functools import lru_cache

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.services.llm.base import (
    GeneratedContent,
    GenerationResult,
    ImageInput,
    LLMError,
    LLMProvider,
    LLMRefusal,
    T,
)


@lru_cache
def output_schema(model: type[BaseModel]) -> dict:
    # Structured outputs: the API guarantees the reply text is JSON matching this schema.
    return anthropic.transform_schema(model.model_json_schema())


def _image_block(image: ImageInput) -> dict:
    if image.url:
        return {"type": "image", "source": {"type": "url", "url": image.url}}
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image.media_type,
            "data": base64.standard_b64encode(image.data or b"").decode(),
        },
    }


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    supports_images = True

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = settings.anthropic_model
        self.effort = settings.anthropic_effort
        self.use_fallbacks = settings.anthropic_fallbacks
        # The SDK retries 408/409/429/5xx and connection errors with exponential backoff.
        # An organization's own key, else the platform key from backend/.env; if neither is set
        # the SDK resolves credentials from the environment itself.
        self.client = anthropic.Anthropic(
            api_key=api_key or settings.anthropic_api_key or None, max_retries=settings.llm_max_retries
        )

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        images: list[ImageInput] | None = None,
        output_model: type[T] = GeneratedContent,
    ) -> GenerationResult[T]:
        extra: dict = {}
        if self.use_fallbacks:
            # If a safety classifier declines a request, the API re-runs it on a fallback
            # model inside the same call instead of failing the product.
            extra = {
                "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
                "extra_body": {"fallbacks": "default"},
            }
        content: str | list[dict] = user_prompt
        if images:
            content = [*(_image_block(i) for i in images), {"type": "text", "text": user_prompt}]
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                # The system prompt is identical for every product in a job: cache it.
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": output_schema(output_model)},
                },
                **extra,
            )
        except (anthropic.BadRequestError, anthropic.AuthenticationError,
                anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise LLMError(f"Claude API rejected the request: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude API rate limit reached; try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error ({exc.status_code})") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Claude API") from exc

        # Check why the model stopped before reading content.
        if response.stop_reason == "refusal":
            raise LLMRefusal("The model declined to write content for this product")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model returned incomplete output")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            parsed = output_model.model_validate_json(text)
        except ValidationError as exc:
            raise LLMError("The model returned output in an unexpected format") from exc

        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0)
        )
        return GenerationResult(
            content=parsed,
            model=response.model,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            used_images=len(images or []),
        )
