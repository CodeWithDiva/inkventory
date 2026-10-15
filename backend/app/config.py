from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    app_name: str = "Inkventory"
    environment: str = "development"

    # Database: SQLite for zero-setup local dev, PostgreSQL in Docker/production.
    database_url: str = f"sqlite:///{(BASE_DIR / 'storage' / 'app.db').as_posix()}"

    # Auth
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # File storage: "local" or "s3"
    storage_backend: str = "local"
    storage_dir: str = str(BASE_DIR / "storage" / "files")
    s3_bucket: str = ""
    s3_region: str = "us-east-1"

    # Background jobs: "sync" (tests), "thread" (local dev), "celery" (production)
    task_mode: str = "thread"
    redis_url: str = "redis://localhost:6379/0"
    generation_concurrency: int = 4

    # Default AI engine: "auto" (first configured free engine: Gemini, Groq, Ollama, then demo),
    # or "gemini", "groq", "ollama", "anthropic", "openai", "mock". Users pick per job in the UI.
    llm_provider: str = "auto"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    anthropic_fallbacks: bool = True
    # Claude is only offered to organizations that saved their own Claude key (billed to them).
    # Set to false to also let paid plans use the platform's ANTHROPIC_API_KEY.
    claude_own_key_only: bool = True
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    # Free-tier cloud AI (OpenAI-compatible APIs). Keys: aistudio.google.com / console.groq.com
    gemini_api_key: str = ""
    # Flash-Lite: higher free daily allowance than Flash (Flash free tier allows only ~20 requests/day)
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    free_concurrency: int = 2  # free tiers have low per-minute limits
    # Requests per minute we send to each free tier (keep below the provider's limit for your key)
    gemini_rpm: int = 8
    groq_rpm: int = 20
    # On "rate limited" / "busy" answers: wait and retry this many times before failing a product
    free_patient_retries: int = 5
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout: float = 900.0
    # Local models share one CPU/GPU, so parallel requests only slow each other down.
    ollama_concurrency: int = 1
    llm_max_retries: int = 3

    # Limits
    max_upload_rows: int = 50_000
    duplicate_similarity_threshold: float = 0.6
    # Quality: one automatic revision for drafts below this SEO score or with invented facts / wrong language.
    auto_improve: bool = True
    auto_improve_min_score: int = 85
    # Show the model up to 2 descriptions the team approved (edited ones first) as a style reference.
    learn_from_approved: bool = True
    login_attempts_per_15_min: int = 10

    # Public URL of the web app (used in email links)
    app_url: str = "http://localhost:5173"

    # Encryption of stored secrets (store credentials, customers' own API keys).
    # A Fernet key; if empty one is derived from SECRET_KEY. Changing it makes stored secrets unreadable.
    encryption_key: str = ""

    # Email: "console" (prints emails to the server log; dev) or "smtp"
    email_backend: str = "console"
    email_from: str = "Inkventory <no-reply@example.com>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    # Sign in with Google / Microsoft (OpenID Connect). Redirect URI to register with the provider:
    #   {APP_URL}/api/auth/sso/google/callback   and   {APP_URL}/api/auth/sso/microsoft/callback
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"  # or your Azure AD tenant id to allow only your company
    sso_allow_signup: bool = True  # new emails get their own workspace

    # Platform owners (comma-separated emails) get the Platform Admin panel:
    # all organizations, usage, and manual plan changes (bank transfer / invoice customers).
    superadmin_emails: str = ""

    # Billing via Lemon Squeezy (optional). Checkout URLs and variant ids per paid plan.
    lemonsqueezy_webhook_secret: str = ""
    lemonsqueezy_checkout_pro: str = ""
    lemonsqueezy_checkout_business: str = ""
    lemonsqueezy_variant_pro: str = ""
    lemonsqueezy_variant_business: str = ""
    # Shown to customers who want to pay by bank transfer / invoice
    billing_contact: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def superadmins(self) -> set[str]:
        return {e.strip().lower() for e in self.superadmin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
