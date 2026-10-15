"""OpenAI and OpenAI-compatible APIs: Google Gemini, Groq (and others with the same API shape)."""

import base64
import json
import re
import threading
import time

import openai
from pydantic import ValidationError

from app.config import get_settings
from app.services.llm.base import (
    GeneratedContent,
    GenerationResult,
    ImageInput,
    LLMError,
    LLMProvider,
    LLMQuotaExceeded,
    T,
)


def _image_part(image: ImageInput) -> dict:
    url = image.url or f"data:{image.media_type};base64,{base64.b64encode(image.data or b'').decode()}"
    return {"type": "image_url", "image_url": {"url": url}}


RETRY_DELAYS = (10, 20, 40, 60, 60)  # seconds; a provider's retry-after header wins
sleep = time.sleep  # replaceable in tests


def is_daily_quota(exc: Exception) -> bool:
    """True for per-day limits (retrying soon is pointless), False for per-minute ones.

    Gemini says e.g. "...limit: 20 ... Please retry in 17h56m58s"; others name the metric ("per day").
    """
    text = str(exc).lower()
    if any(k in text.replace("_", "").replace(" ", "") for k in ("perday", "requestsperday", "tokensperday",
                                                                  "dailylimit", "dailyquota", "insufficientquota")):
        return True
    wait = re.search(r"retry in (?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", text)
    if wait and any(wait.groups()):
        hours, minutes, seconds = (float(g) if g else 0.0 for g in wait.groups())
        return hours * 3600 + minutes * 60 + seconds > 300
    return False

def json_instructions(output_model: type) -> str:
    """A filled-in example works far better than a JSON schema: models tend to copy a schema's shape."""
    example = {}
    for name, prop in output_model.model_json_schema()["properties"].items():
        hint = prop.get("description", name)
        example[name] = [f"<{hint}>"] if prop.get("type") == "array" else f"<{hint}>"
    return ("Respond with ONLY a JSON object with exactly these keys, replacing each <...> with your text "
            "(lists stay lists of strings):\n" + json.dumps(example, indent=2, ensure_ascii=False))


def extract_json(text: str) -> str:
    """The JSON object in a reply, ignoring reasoning tags, code fences or chatter around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


class OpenAIProvider(LLMProvider):
    name = "openai"
    supports_images = True

    def __init__(
        self,
        *,
        name: str = "openai",
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        supports_images: bool = True,
        http_client=None,
        requests_per_minute: int = 0,
        patient_retries: int = 0,
    ) -> None:
        settings = get_settings()
        self.name = name
        self.model = model or settings.openai_model
        self.supports_images = supports_images
        # Free tiers: pace requests under the per-minute limit, and on "rate limited" / "busy"
        # wait and try again instead of failing the product straight away.
        self.min_interval = 60.0 / requests_per_minute if requests_per_minute else 0.0
        self.patient_retries = patient_retries
        self._pace_lock = threading.Lock()
        self._next_slot = 0.0
        self.client = openai.OpenAI(
            api_key=api_key or settings.openai_api_key or None,
            base_url=base_url,
            max_retries=0 if patient_retries else settings.llm_max_retries,
            http_client=http_client,
        )

    def _wait_for_slot(self) -> None:
        if not self.min_interval:
            return
        with self._pace_lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            self._next_slot = start + self.min_interval
        if start > now:
            sleep(start - now)

    def _create(self, **kwargs):
        """One API call with pacing and patient retries for 429 / 5xx."""
        attempt = 0
        while True:
            self._wait_for_slot()
            try:
                return self.client.chat.completions.create(**kwargs)
            except (openai.RateLimitError, openai.InternalServerError) as exc:
                if isinstance(exc, openai.RateLimitError) and is_daily_quota(exc):
                    raise LLMQuotaExceeded(
                        f"{self.name}: today's free limit is used up. Try again tomorrow, or use another AI engine."
                    ) from exc
                if attempt >= self.patient_retries:
                    raise
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                retry_after = exc.response.headers.get("retry-after") if exc.response is not None else None
                if retry_after:
                    try:
                        delay = min(120.0, max(float(retry_after), 1.0))
                    except ValueError:
                        pass
                attempt += 1
                sleep(delay)

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        images: list[ImageInput] | None = None,
        output_model: type[T] = GeneratedContent,
    ) -> GenerationResult[T]:
        user_content: str | list[dict] = user_prompt
        if images and self.supports_images:
            user_content = [*(_image_part(i) for i in images), {"type": "text", "text": user_prompt}]
        messages = [
            {"role": "system", "content": f"{system_prompt}\n\n{json_instructions(output_model)}"},
            {"role": "user", "content": user_content},
        ]
        try:
            try:
                response = self._create(model=self.model, response_format={"type": "json_object"}, messages=messages)
            except openai.BadRequestError as exc:
                # Some providers (e.g. Groq) reject an answer their JSON validator dislikes; ask once
                # more without JSON mode and parse the JSON out of the text ourselves.
                if "json" not in str(exc).lower():
                    raise
                response = self._create(model=self.model, messages=messages)
        except openai.RateLimitError as exc:
            raise LLMError(f"{self.name} rate limit reached (free tiers have per-minute and daily limits)") from exc
        except openai.AuthenticationError as exc:
            raise LLMError(f"{self.name} rejected the API key") from exc
        except openai.InternalServerError as exc:
            raise LLMError(f"{self.name} is busy right now ({exc.status_code}); try again in a few minutes") from exc
        except openai.APIError as exc:
            raise LLMError(f"{self.name} API error: {exc}") from exc

        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("The model returned incomplete output")
        try:
            parsed = output_model.model_validate_json(extract_json(choice.message.content or ""))
        except ValidationError as exc:
            raise LLMError("The model returned output in an unexpected format") from exc
        usage = response.usage
        return GenerationResult(
            content=parsed,
            model=response.model or self.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            used_images=len(images or []) if self.supports_images else 0,
        )
