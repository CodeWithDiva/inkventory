"""Free cloud engines (Gemini, Groq) and Claude restricted to organizations' own keys."""

import json

import httpx2
import pytest

from app.config import get_settings
from app.services.llm import LLMError, available_engines, default_engine, provider_for_org
from app.services.llm.openai_provider import OpenAIProvider
from tests.conftest import upload_sample
from tests.test_anthropic_provider import CONTENT


def chat_reply(content: str) -> dict:
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "gemini-2.5-flash",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 300, "total_tokens": 700}}


def provider_with(handler, **kwargs):
    captured = []

    def wrapped(request):
        captured.append(request)
        return handler(request)

    provider = OpenAIProvider(api_key="k", http_client=httpx2.Client(transport=httpx2.MockTransport(wrapped)), **kwargs)
    return provider, captured


def test_gemini_request_and_parsing():
    provider, captured = provider_with(lambda r: httpx2.Response(200, json=chat_reply(json.dumps(CONTENT))),
                                       name="gemini", model="gemini-2.5-flash",
                                       base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    from app.services.llm import ImageInput

    result = provider.generate("SYS", "PRODUCT", images=[ImageInput(url="https://cdn.x/a.jpg")])
    assert result.content.title == CONTENT["title"] and result.input_tokens == 400 and result.used_images == 1
    req = captured[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "gemini-2.5-flash" and body["response_format"] == {"type": "json_object"}
    assert body["messages"][1]["content"][0]["image_url"]["url"] == "https://cdn.x/a.jpg"


def test_groq_text_only_and_code_fences():
    fenced = "```json\n" + json.dumps(CONTENT) + "\n```"
    provider, captured = provider_with(lambda r: httpx2.Response(200, json=chat_reply(fenced)),
                                       name="groq", model="llama", base_url="https://api.groq.com/openai/v1",
                                       supports_images=False)
    from app.services.llm import ImageInput

    result = provider.generate("SYS", "PRODUCT", images=[ImageInput(url="https://cdn.x/a.jpg")])
    assert result.content.title == CONTENT["title"] and result.used_images == 0
    assert json.loads(captured[0].content)["messages"][1]["content"] == "PRODUCT"  # no image parts sent


def test_free_tier_rate_limit_message():
    provider, _ = provider_with(lambda r: httpx2.Response(429, json={"error": {"message": "quota"}}),
                                name="gemini", model="m", base_url="https://g.example/v1/")
    provider.client = provider.client.with_options(max_retries=0)
    with pytest.raises(LLMError, match="rate limit"):
        provider.generate("s", "u")


def test_free_engines_listed_and_auto_default(client, auth, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    monkeypatch.setattr(settings, "groq_api_key", "q-key")
    engines = {e["id"]: e for e in client.get("/api/options", headers=auth).json()["engines"]}
    assert engines["gemini"]["available"] and engines["gemini"]["kind"] == "free"
    assert engines["groq"]["available"] and engines["groq"]["free"]

    monkeypatch.setattr(settings, "llm_provider", "auto")
    assert default_engine() == "gemini"
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert default_engine() == "groq"
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert default_engine() == "mock"  # no free key and Ollama unreachable in tests


def test_claude_requires_own_key(client, auth, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-platform-key")  # platform key is NOT shared
    engines = {e["id"]: e for e in client.get("/api/options", headers=auth).json()["engines"]}
    assert not engines["anthropic"]["available"] and "own Claude API key" in engines["anthropic"]["note"]
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    assert client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "engine": "anthropic"}).status_code == 400
    with pytest.raises(LLMError, match="own Claude API key"):
        provider_for_org("anthropic", None)

    client.patch("/api/settings", headers=auth, json={"anthropic_api_key": "sk-ant-their-own-key"})
    engines = {e["id"]: e for e in client.get("/api/options", headers=auth).json()["engines"]}
    assert engines["anthropic"]["available"] and "billed by Anthropic" in engines["anthropic"]["note"]

def test_json_instructions_use_an_example_not_a_schema():
    from app.services.llm import GeneratedContent
    from app.services.llm.openai_provider import extract_json, json_instructions

    text = json_instructions(GeneratedContent)
    assert '"title": "<Product page title>"' in text and '"bullet_points": [' in text
    assert '"type"' not in text  # no schema vocabulary for the model to copy
    raw = '<think>planning...</think>\nSure! ```json\n{"a": {"b": 1}}\n``` done'
    assert extract_json(raw) == '{"a": {"b": 1}}'


def test_json_mode_rejection_falls_back_to_plain_answer():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append("response_format" in body)
        if "response_format" in body:
            return httpx2.Response(400, json={"error": {"message": "Failed to validate JSON", "code": "json_validate_failed"}})
        return httpx2.Response(200, json=chat_reply("Here it is: " + json.dumps(CONTENT)))

    provider, _ = provider_with(handler, name="groq", model="m", base_url="https://api.groq.com/openai/v1", supports_images=False)
    assert provider.generate("s", "u").content.title == CONTENT["title"]
    assert calls == [True, False]

def test_patient_retry_on_rate_limit_and_busy(monkeypatch):
    from app.services.llm import openai_provider

    waits = []
    monkeypatch.setattr(openai_provider, "sleep", waits.append)
    replies = iter([
        httpx2.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow down"}}),
        httpx2.Response(503, json={"error": {"message": "busy"}}),
        httpx2.Response(200, json=chat_reply(json.dumps(CONTENT))),
    ])
    provider, _ = provider_with(lambda r: next(replies), name="gemini", model="m", base_url="https://g.example/v1/",
                                patient_retries=5)
    assert provider.generate("s", "u").content.title == CONTENT["title"]
    assert waits == [7.0, 20]  # provider's retry-after first, then our backoff

    always_busy, _ = provider_with(lambda r: httpx2.Response(503, json={"error": {"message": "busy"}}),
                                   name="gemini", model="m", base_url="https://g.example/v1/", patient_retries=2)
    with pytest.raises(LLMError, match="busy"):
        always_busy.generate("s", "u")


def test_pacing_spreads_requests(monkeypatch):
    from app.services.llm import openai_provider

    waits = []
    monkeypatch.setattr(openai_provider, "sleep", waits.append)
    provider, _ = provider_with(lambda r: httpx2.Response(200, json=chat_reply(json.dumps(CONTENT))),
                                name="gemini", model="m", base_url="https://g.example/v1/", requests_per_minute=6)
    for _ in range(3):
        provider.generate("s", "u")
    # 6/min = one slot every 10 s (sleep is faked, so the clock does not move between calls)
    assert len(waits) == 2 and 9 < waits[0] <= 10 and 9 < waits[1] - waits[0] <= 10.5

def test_daily_quota_fails_fast_and_stops_the_job(client, auth, monkeypatch):
    from app.services.llm import LLMQuotaExceeded, set_provider
    from app.services.llm.mock_provider import MockProvider

    quota_body = {"error": {"message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: PerDay"}}
    provider, captured = provider_with(lambda r: httpx2.Response(429, json=quota_body), name="gemini", model="m",
                                       base_url="https://g.example/v1/", patient_retries=5)
    with pytest.raises(LLMQuotaExceeded, match="today's free limit"):
        provider.generate("s", "u")
    assert len(captured) == 1  # no pointless retries

    calls = []

    class QuotaGone(MockProvider):
        def generate(self, system_prompt, user_prompt, **kwargs):
            calls.append(1)
            if len(calls) > 2:
                raise LLMQuotaExceeded("gemini: today's free limit is used up")
            return super().generate(system_prompt, user_prompt, **kwargs)

    set_provider(QuotaGone())
    monkeypatch.setattr(get_settings(), "generation_concurrency", 1)  # 2 requests in flight at most
    monkeypatch.setattr(get_settings(), "auto_improve", False)  # count one call per product
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    job = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id}).json()
    job = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
    assert job["completed"] == 2 and job["failed"] <= 2 and job["status"] == "completed"
    assert any("Job stopped" in e["error"] for e in job["errors"])
    assert len(calls) <= 4  # stopped instead of trying all 8 products

def test_daily_vs_per_minute_quota_detection():
    from app.services.llm.openai_provider import is_daily_quota

    assert is_daily_quota(Exception("You exceeded your current quota. limit: 20, model: x. Please retry in 17h56m58.47s."))
    assert not is_daily_quota(Exception("You exceeded your current quota. Please retry in 32.5s."))
    assert not is_daily_quota(Exception("You exceeded your current quota. Please retry in 1m5s."))
    assert is_daily_quota(Exception("Rate limit reached on requests per day (RPD): Limit 1000"))