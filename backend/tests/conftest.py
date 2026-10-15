import os
import tempfile
from pathlib import Path

# Configure an isolated environment before the app is imported.
_tmp = Path(tempfile.mkdtemp(prefix="catalogai-test-"))
os.environ.update(
    DATABASE_URL=f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    STORAGE_DIR=str(_tmp / "files"),
    TASK_MODE="sync",
    LLM_PROVIDER="mock",
    SECRET_KEY="test-secret",
    # Keep tests independent of the developer's backend/.env and local Ollama.
    ANTHROPIC_API_KEY="",
    OPENAI_API_KEY="",
    OLLAMA_BASE_URL="http://127.0.0.1:9",
    GENERATION_CONCURRENCY="4",
    EMAIL_BACKEND="console",
    SUPERADMIN_EMAILS="boss@platform.com",
    LEMONSQUEEZY_WEBHOOK_SECRET="whsec-test",
    LEMONSQUEEZY_CHECKOUT_PRO="https://shop.lemonsqueezy.com/buy/pro-123",
    LEMONSQUEEZY_CHECKOUT_BUSINESS="",
    LEMONSQUEEZY_VARIANT_PRO="111",
    LEMONSQUEEZY_VARIANT_BUSINESS="222",
    BILLING_CONTACT="billing@platform.com",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.auth import login_limiter, reset_limiter  # noqa: E402
from app.services import email  # noqa: E402
from app.services.llm import set_provider  # noqa: E402

SAMPLE_CSV = Path(__file__).resolve().parents[2] / "sample_data" / "products.csv"


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    set_provider(None)
    email.outbox.clear()
    login_limiter.hits.clear()
    reset_limiter.hits.clear()
    with TestClient(app) as c:  # runs startup: migrations + seed presets
        yield c
    set_provider(None)


def register(client: TestClient, email: str = "owner@shop.com", org: str = "Shop One") -> dict:
    r = client.post("/api/auth/register", json={
        "organization_name": org, "full_name": "Owner", "email": email, "password": "password123",
    })
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def auth(client):
    return register(client)


def upload_sample(client: TestClient, headers: dict) -> dict:
    with SAMPLE_CSV.open("rb") as f:
        r = client.post(
            "/api/catalogs",
            headers=headers,
            files={"file": ("products.csv", f, "text/csv")},
            data={"name": "Sample catalog"},
        )
    assert r.status_code == 201, r.text
    return r.json()
