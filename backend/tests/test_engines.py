import json

import httpx
import pytest

from app.services.llm import LLMError
from app.services.llm.ollama_provider import OllamaProvider
from tests.conftest import upload_sample
from tests.test_anthropic_provider import CONTENT


def ollama_with(handler) -> tuple[OllamaProvider, list]:
    captured: list = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    provider = OllamaProvider("qwen2.5:7b")
    provider.client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return provider, captured


def test_ollama_provider_parses_structured_output():
    reply = {"message": {"content": json.dumps(CONTENT)}, "done_reason": "stop",
             "prompt_eval_count": 500, "eval_count": 350}
    provider, captured = ollama_with(lambda r: httpx.Response(200, json=reply))
    result = provider.generate("SYSTEM", "USER")
    assert result.content.title == CONTENT["title"]
    assert (result.input_tokens, result.output_tokens, result.model) == (500, 350, "qwen2.5:7b")
    body = json.loads(captured[0].content)
    assert body["model"] == "qwen2.5:7b" and body["stream"] is False
    assert body["format"]["properties"]["meta_description"]  # JSON schema constrains output


def test_ollama_retries_bad_json_then_reports_errors():
    calls = iter([
        httpx.Response(200, json={"message": {"content": "not json"}, "done_reason": "stop"}),
        httpx.Response(200, json={"message": {"content": json.dumps(CONTENT)}, "done_reason": "stop"}),
    ])
    provider, _ = ollama_with(lambda r: next(calls))
    assert provider.generate("s", "u").content.title == CONTENT["title"]

    provider, _ = ollama_with(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(LLMError, match="ollama pull"):
        provider.generate("s", "u")

    def refuse(request):
        raise httpx.ConnectError("refused")

    provider, _ = ollama_with(refuse)
    with pytest.raises(LLMError, match="not running"):
        provider.generate("s", "u")


def test_engine_choice_per_job(client, auth):
    options = client.get("/api/options", headers=auth).json()
    engines = {e["id"]: e for e in options["engines"]}
    assert engines["mock"]["available"] and not engines["anthropic"]["available"]
    assert engines["ollama"]["available"] is False  # Ollama unreachable in tests

    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    bad = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "engine": "anthropic"})
    assert bad.status_code == 400

    estimate = client.post("/api/jobs/estimate", headers=auth, json={"catalog_id": catalog_id, "engine": "mock"}).json()
    assert estimate["estimated_cost_usd"] == 0 and "estimated_minutes" in estimate
    job = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "engine": "mock"}).json()
    job = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
    assert job["engine"] == "mock" and job["status"] == "completed" and job["completed"] == 8
