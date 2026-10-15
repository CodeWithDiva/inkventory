"""Runs the real Anthropic SDK against a mocked HTTP transport (no network, no API key)."""

import json

import anthropic
import httpx2
import pytest

from app.services.llm import LLMError, LLMRefusal
from app.services.llm.anthropic_provider import AnthropicProvider

CONTENT = {
    "title": "Classic Leather Wallet",
    "short_description": "A slim leather wallet.",
    "long_description": "This leather wallet is made from genuine leather.",
    "bullet_points": ["Genuine leather", "Slim", "Bifold"],
    "meta_title": "Classic Leather Wallet | Urban Craft",
    "meta_description": "Shop the classic leather wallet.",
}


def make_provider(handler) -> tuple[AnthropicProvider, list]:
    captured: list = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return handler(request)

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model, provider.effort, provider.use_fallbacks = "claude-opus-5-5", "medium", True
    provider.client = anthropic.Anthropic(
        api_key="test-key", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(wrapped))
    )
    return provider, captured


def message(stop_reason: str = "end_turn", text: str = json.dumps(CONTENT)) -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 400, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 600},
    }


def test_request_shape_and_parsing():
    provider, captured = make_provider(lambda r: httpx2.Response(200, json=message()))
    result = provider.generate("SYSTEM RULES", "PRODUCT DATA")

    assert result.content.title == "Classic Leather Wallet"
    assert result.input_tokens == 1500 and result.output_tokens == 400

    request = captured[0]
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]


def test_images_are_sent_as_content_blocks():
    from app.services.llm import ImageInput

    provider, captured = make_provider(lambda r: httpx2.Response(200, json=message()))
    result = provider.generate("SYS", "PRODUCT", images=[
        ImageInput(url="https://cdn.x.com/a.jpg"), ImageInput(data=b"\x89PNG", media_type="image/png")])
    content = json.loads(captured[0].content)["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {"type": "url", "url": "https://cdn.x.com/a.jpg"}}
    assert content[1]["source"]["type"] == "base64" and content[1]["source"]["media_type"] == "image/png"
    assert content[2] == {"type": "text", "text": "PRODUCT"}
    assert result.used_images == 2


def test_refusal_and_errors():
    provider, _ = make_provider(lambda r: httpx2.Response(200, json=message("refusal", "")))
    with pytest.raises(LLMRefusal):
        provider.generate("s", "u")

    provider, _ = make_provider(lambda r: httpx2.Response(
        429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}))
    with pytest.raises(LLMError, match="rate limit"):
        provider.generate("s", "u")
