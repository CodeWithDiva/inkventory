"""AI engine registry.

Engine ids: "gemini", "groq" (free cloud tiers), "ollama:<model>" (free, local), "anthropic",
"openai", "mock". The default comes from LLM_PROVIDER ("auto" = first configured free engine);
users can choose any engine available to their organization, per job.

Claude: by default (CLAUDE_OWN_KEY_ONLY) only organizations that saved their own Claude key in
Settings can use it, and their jobs run on - and are billed to - that key.
"""

import hashlib
import os
import threading
import time
from typing import TYPE_CHECKING

import httpx

from app.config import get_settings
from app.services.llm.base import (
    GeneratedContent,
    GenerationResult,
    ImageInput,
    LLMError,
    LLMProvider,
    LLMQuotaExceeded,
    LLMRefusal,
    estimate_cost,
)

if TYPE_CHECKING:
    from app.models import Organization

_providers: dict[str, LLMProvider] = {}
_override: LLMProvider | None = None
_lock = threading.Lock()


FREE_CLOUD = ("gemini", "groq")


def default_engine() -> str:
    settings = get_settings()
    choice = settings.llm_provider
    if choice == "auto":
        if settings.gemini_api_key:
            return "gemini"
        if settings.groq_api_key:
            return "groq"
        try:
            if settings.ollama_model in _ollama_models(settings.ollama_base_url):
                return f"ollama:{settings.ollama_model}"
        except (httpx.HTTPError, ValueError):
            pass
        return "mock"
    if choice == "ollama":
        return f"ollama:{settings.ollama_model}"
    return choice


def engine_kind(engine: str) -> str:
    if engine.startswith("ollama"):
        return "local"
    if engine == "mock":
        return "demo"
    if engine in FREE_CLOUD:
        return "free"
    return "cloud"


def _build(engine: str, api_key: str | None) -> LLMProvider:
    if engine == "anthropic":
        from app.services.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key)
    if engine == "openai":
        from app.services.llm.openai_provider import OpenAIProvider

        return OpenAIProvider()
    if engine in FREE_CLOUD:
        from app.services.llm.openai_provider import OpenAIProvider

        s = get_settings()
        if engine == "gemini":
            return OpenAIProvider(name="gemini", api_key=s.gemini_api_key, model=s.gemini_model,
                                  base_url=s.gemini_base_url, supports_images=True,
                                  requests_per_minute=s.gemini_rpm, patient_retries=s.free_patient_retries)
        return OpenAIProvider(name="groq", api_key=s.groq_api_key, model=s.groq_model,
                              base_url=s.groq_base_url, supports_images=False,
                              requests_per_minute=s.groq_rpm, patient_retries=s.free_patient_retries)
    if engine == "ollama" or engine.startswith("ollama:"):
        from app.services.llm.ollama_provider import OllamaProvider

        return OllamaProvider(engine.partition(":")[2] or None)
    if engine == "mock":
        from app.services.llm.mock_provider import MockProvider

        return MockProvider()
    raise LLMError(f"Unknown AI engine: {engine}")


def get_provider(engine: str | None = None, api_key: str | None = None) -> LLMProvider:
    if _override is not None:
        return _override
    engine = engine or default_engine()
    cache_key = engine + ("|" + hashlib.sha256(api_key.encode()).hexdigest() if api_key else "")
    with _lock:
        if cache_key not in _providers:
            _providers[cache_key] = _build(engine, api_key)
        return _providers[cache_key]


def org_api_key(org: "Organization | None", engine: str | None) -> str | None:
    """The organization's own key for this engine, if it saved one."""
    if org is None or (engine or default_engine()) != "anthropic" or not org.anthropic_api_key:
        return None
    from app.crypto import decrypt

    return decrypt(org.anthropic_api_key) or None


def provider_for_org(engine: str | None, org: "Organization | None") -> LLMProvider:
    engine = engine or default_engine()
    key = org_api_key(org, engine)
    if engine == "anthropic" and not key and get_settings().claude_own_key_only and _override is None:
        raise LLMError("Claude needs your organization's own Claude API key (Settings)")
    return get_provider(engine, key)


def vision_provider_for(engine: str | None, org: "Organization | None") -> LLMProvider | None:
    """The engine that analyses product photos: the chosen engine if it can see images, otherwise the first
    available vision-capable one (Gemini, the org's Claude, a local vision model). None if there is none."""
    try:
        chosen = provider_for_org(engine, org)
        if chosen.supports_images:
            return chosen
    except LLMError:
        pass
    usable = [e["id"] for e in available_engines(org) if e["available"] and e["kind"] != "demo"]
    candidates = [e for e in ("gemini", "anthropic", "openai") if e in usable]
    candidates += [e for e in usable if e.startswith("ollama:")]
    for candidate in candidates:
        try:
            provider = provider_for_org(candidate, org)
        except Exception:
            continue
        if provider.supports_images:
            return provider
    return None


def set_provider(provider: LLMProvider | None) -> None:
    """Force one provider for every engine (used by tests)."""
    global _override
    _override = provider
    _providers.clear()


def concurrency_for(engine: str | None) -> int:
    settings = get_settings()
    engine = engine or default_engine()
    if engine.startswith("ollama"):
        return max(1, settings.ollama_concurrency)
    if engine in FREE_CLOUD:
        return max(1, settings.free_concurrency)
    return max(1, settings.generation_concurrency)


_ollama_cache: tuple[float, str, list[str]] | None = None


def _ollama_models(base_url: str) -> list[str]:
    """Installed Ollama models, cached for 30s (the lookup can take seconds on a busy machine)."""
    global _ollama_cache
    now = time.monotonic()
    if _ollama_cache and _ollama_cache[1] == base_url and now - _ollama_cache[0] < 30:
        return _ollama_cache[2]
    tags = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=5).json()
    models = [m["name"] for m in tags.get("models", []) if "embed" not in m["name"]]
    _ollama_cache = (now, base_url, models)
    return models


def available_engines(org: "Organization | None" = None) -> list[dict]:
    """Engines for the UI's dropdown, with whether each one can be used right now by this organization."""
    from app.plans import get_plan

    settings = get_settings()
    plan = get_plan(org.plan) if org is not None else None
    cloud_allowed = plan is None or "cloud" in plan.engine_kinds
    engines = []

    if settings.gemini_api_key:
        engines.append({"id": "gemini", "label": f"Google Gemini ({settings.gemini_model})", "kind": "free",
                        "free": True, "available": True,
                        "note": "Free tier: fast, good in Urdu/Arabic, reads photos; daily limits apply"})
    if settings.groq_api_key:
        engines.append({"id": "groq", "label": f"Groq ({settings.groq_model})", "kind": "free", "free": True,
                        "available": True, "note": "Free tier: very fast, best for English; daily limits apply"})

    platform_claude = bool(settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")) \
        and not settings.claude_own_key_only
    own_claude = bool(org is not None and org.anthropic_api_key)
    if own_claude:
        claude = {"available": True, "note": "Uses your organization's own Claude API key (billed by Anthropic)",
                  "needs_upgrade": False}
    elif platform_claude and cloud_allowed:
        claude = {"available": True, "note": "Best quality in every language, fast", "needs_upgrade": False}
    elif platform_claude:
        claude = {"available": False, "needs_upgrade": True,
                  "note": f"Not included in the {plan.name} plan. Upgrade, or add your own Claude key in Settings"}
    else:
        claude = {"available": False, "needs_upgrade": False,
                  "note": "Add your own Claude API key in Settings to use Claude"}
    engines.append({"id": "anthropic", "label": f"Claude ({settings.anthropic_model})", "kind": "cloud",
                    "free": False, **claude})

    if settings.openai_api_key or os.environ.get("OPENAI_API_KEY"):
        engines.append({"id": "openai", "label": f"OpenAI ({settings.openai_model})", "kind": "cloud",
                        "free": False, "available": cloud_allowed, "needs_upgrade": not cloud_allowed,
                        "note": "Paid per use" if cloud_allowed else "Upgrade your plan to use cloud AI"})

    try:
        models = _ollama_models(settings.ollama_base_url)
        for name in models:
            engines.append({"id": f"ollama:{name}", "label": f"Local: {name}", "kind": "local", "free": True,
                            "available": True, "note": "Free, runs on the server (slower)"})
        if not models:
            engines.append({"id": "ollama", "label": "Local (Ollama)", "kind": "local", "free": True,
                            "available": False, "note": "No models installed. Run: ollama pull qwen2.5:7b"})
    except (httpx.HTTPError, ValueError):
        engines.append({"id": "ollama", "label": "Local (Ollama)", "kind": "local", "free": True,
                        "available": False, "note": "Ollama is not running on the server"})

    engines.append({"id": "mock", "label": "Demo (sample text)", "kind": "demo", "free": True,
                    "available": True, "note": "Placeholder text for trying the app"})
    for engine in engines:
        engine.setdefault("needs_upgrade", False)
    return engines


__all__ = [
    "GeneratedContent",
    "GenerationResult",
    "ImageInput",
    "LLMError",
    "LLMProvider",
    "LLMQuotaExceeded",
    "LLMRefusal",
    "available_engines",
    "concurrency_for",
    "default_engine",
    "engine_kind",
    "estimate_cost",
    "get_provider",
    "org_api_key",
    "provider_for_org",
    "set_provider",
]
