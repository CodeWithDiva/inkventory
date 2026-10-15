"""Free, local generation through Ollama (https://ollama.com). No API key or credits needed.

Ollama's native chat API supports JSON-schema constrained output via ``format`` and images
(base64) for vision models.
"""

import base64

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.services.llm.base import GeneratedContent, GenerationResult, ImageInput, LLMError, LLMProvider, T

# Model families that accept images. Text-only models are sent the text alone.
VISION_MODEL_HINTS = ("llava", "vision", "-vl", "vl:", "gemma3", "minicpm-v", "moondream", "bakllava", "llama4")

MAX_IMAGE_BYTES = 5 * 1024 * 1024


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.ollama_model
        self.url = settings.ollama_base_url.rstrip("/") + "/api/chat"
        self.retries = settings.llm_max_retries
        self.supports_images = any(h in self.model.lower() for h in VISION_MODEL_HINTS)
        # Local models on a CPU can take minutes per product.
        self.client = httpx.Client(timeout=httpx.Timeout(settings.ollama_timeout, connect=10))

    def _image_b64(self, image: ImageInput) -> str | None:
        data = image.data
        if data is None and image.url:
            try:
                response = httpx.get(image.url, timeout=20, follow_redirects=True)
                response.raise_for_status()
                data = response.content
            except httpx.HTTPError:
                return None
        if not data or len(data) > MAX_IMAGE_BYTES:
            return None
        return base64.b64encode(data).decode()

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        images: list[ImageInput] | None = None,
        output_model: type[T] = GeneratedContent,
    ) -> GenerationResult[T]:
        user_message: dict = {"role": "user", "content": user_prompt}
        notes: list[str] = []
        encoded: list[str] = []
        if images:
            if self.supports_images:
                encoded = [b for b in (self._image_b64(i) for i in images) if b]
                if encoded:
                    user_message["images"] = encoded
            else:
                notes.append(f"{self.model} cannot read images; generated from text data only")
        payload = {
            "model": self.model,
            "stream": False,
            "format": output_model.model_json_schema(),
            "options": {"temperature": 0.7, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system_prompt}, user_message],
        }
        last_error = "unknown error"
        for _ in range(max(1, self.retries)):
            try:
                response = self.client.post(self.url, json=payload)
            except httpx.ConnectError as exc:
                raise LLMError("Ollama is not running. Start the Ollama app and try again.") from exc
            except httpx.TimeoutException:
                last_error = "Ollama took too long to respond"
                continue
            if response.status_code == 404:
                raise LLMError(f"Model '{self.model}' is not installed. Run: ollama pull {self.model}")
            if response.status_code >= 400:
                last_error = f"Ollama error ({response.status_code}): {response.text[:200]}"
                continue

            data = response.json()
            if data.get("done_reason") == "length":
                last_error = "The model returned incomplete output"
                continue
            try:
                parsed = output_model.model_validate_json(data.get("message", {}).get("content", ""))
            except ValidationError:
                last_error = "The model returned output in an unexpected format"
                continue  # small local models occasionally slip; retry
            return GenerationResult(
                content=parsed,
                model=self.model,
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
                used_images=len(encoded),
                notes=notes,
            )
        raise LLMError(last_error)
