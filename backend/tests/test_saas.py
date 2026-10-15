"""Accounts, security, organization settings, plans, billing and the platform admin."""

import hashlib
import hmac
import json
import re

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Integration, Organization
from app.services import email
from tests.conftest import register, upload_sample


def login(client, email_addr, password="password123"):
    return client.post("/api/auth/login", data={"username": email_addr, "password": password})


def test_password_reset_flow(client, auth):
    r = client.post("/api/auth/forgot-password", json={"email": "owner@shop.com"})
    assert r.status_code == 200
    # Unknown emails get the same answer (no account discovery).
    assert client.post("/api/auth/forgot-password", json={"email": "nobody@x.com"}).json() == r.json()

    sent = [m for m in email.outbox if m["subject"] == "Reset your password"]
    assert len(sent) == 1 and sent[0]["to"] == "owner@shop.com"
    token = re.search(r"token=([\w\-.]+)", sent[0]["body"]).group(1)

    r = client.post("/api/auth/reset-password", json={"token": token, "new_password": "brand-new-pass"})
    assert r.status_code == 200
    new_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=new_auth).status_code == 200
    # Old sessions are signed out, the link works only once, and the new password works.
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "another-pass"}).status_code == 400
    assert login(client, "owner@shop.com", "brand-new-pass").status_code == 200


def test_change_password(client, auth):
    bad = client.post("/api/auth/change-password", headers=auth, json={"current_password": "nope", "new_password": "newpassword1"})
    assert bad.status_code == 400
    r = client.post("/api/auth/change-password", headers=auth,
                    json={"current_password": "password123", "new_password": "newpassword1"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    assert login(client, "owner@shop.com", "newpassword1").status_code == 200


def test_login_rate_limit(client, auth):
    for _ in range(get_settings().login_attempts_per_15_min):
        assert login(client, "owner@shop.com", "wrong").status_code == 401
    assert login(client, "owner@shop.com", "password123").status_code == 429


def test_welcome_and_job_emails(client, auth):
    assert any(m["subject"].startswith("Welcome") for m in email.outbox)
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id})
    done = [m for m in email.outbox if m["subject"] == "Descriptions ready: Sample catalog"]
    assert done and "8 descriptions are ready" in done[0]["body"]

    client.patch("/api/settings", headers=auth, json={"notify_on_job_complete": False})
    email.outbox.clear()
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id})
    assert not [m for m in email.outbox if m["subject"].startswith("Descriptions ready")]


def test_own_claude_key_is_encrypted_and_unlocks_claude(client, auth):
    engines = {e["id"]: e for e in client.get("/api/options", headers=auth).json()["engines"]}
    assert not engines["anthropic"]["available"]

    assert client.patch("/api/settings", headers=auth, json={"anthropic_api_key": "not-a-key"}).status_code == 400
    r = client.patch("/api/settings", headers=auth, json={"anthropic_api_key": "sk-ant-test-abcd1234"})
    assert r.json()["has_own_anthropic_key"] and r.json()["anthropic_key_hint"] == "••••1234"
    with SessionLocal() as db:
        stored = db.scalar(select(Organization.anthropic_api_key))
    assert stored.startswith("enc:") and "abcd1234" not in stored

    engines = {e["id"]: e for e in client.get("/api/options", headers=auth).json()["engines"]}
    assert engines["anthropic"]["available"] and "own" in engines["anthropic"]["note"]

    assert client.patch("/api/settings", headers=auth, json={"anthropic_api_key": ""}).json()["has_own_anthropic_key"] is False


def test_free_plan_cannot_use_platform_claude(client, auth, monkeypatch):
    # Only relevant when the platform shares its own Claude key with paid plans.
    monkeypatch.setattr(get_settings(), "claude_own_key_only", False)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-ant-platform-key")
    engines = {e["id"]: e for e in client.get("/api/options", headers=auth).json()["engines"]}
    assert engines["anthropic"]["needs_upgrade"] and not engines["anthropic"]["available"]
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    r = client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id, "engine": "anthropic"})
    assert r.status_code == 402


def test_store_secrets_encrypted_at_rest(client, auth):
    client.post("/api/integrations", headers=auth, json={
        "platform": "woocommerce", "name": "Woo",
        "config": {"store_url": "https://x.com", "consumer_key": "ck_123456", "consumer_secret": "cs_987654"},
    })
    with SessionLocal() as db:
        config = db.scalar(select(Integration.config))
    assert config["store_url"] == "https://x.com"
    assert config["consumer_key"].startswith("enc:") and config["consumer_secret"].startswith("enc:")
    shown = client.get("/api/integrations", headers=auth).json()[0]["config"]
    assert shown["consumer_secret"] == "••••7654"


def test_team_size_limited_by_plan(client, auth):
    new = lambda e: {"email": e, "full_name": "X", "password": "password123", "role": "editor"}  # noqa: E731
    assert client.post("/api/users", headers=auth, json=new("a@shop.com")).status_code == 201
    assert client.post("/api/users", headers=auth, json=new("b@shop.com")).status_code == 402  # Free: 2 members
    assert any(m["subject"] == "You were added to Shop One" for m in email.outbox)


def _signed(payload: dict) -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode()
    sig = hmac.new(b"whsec-test", raw, hashlib.sha256).hexdigest()
    return raw, {"X-Signature": sig, "Content-Type": "application/json"}


def test_billing_checkout_and_webhook(client, auth):
    billing = client.get("/api/billing", headers=auth).json()
    assert billing["plan"]["id"] == "free" and billing["monthly_limit"] == 100
    assert {p["id"]: p["can_checkout"] for p in billing["plans"]} == {"free": False, "pro": True, "business": False}

    url = client.post("/api/billing/checkout", headers=auth, json={"plan": "pro"}).json()["url"]
    assert url.startswith("https://shop.lemonsqueezy.com/buy/pro-123?") and "org_id%5D=1" in url
    assert client.post("/api/billing/checkout", headers=auth, json={"plan": "business"}).status_code == 400

    event = {
        "meta": {"event_name": "subscription_created", "custom_data": {"org_id": "1"}},
        "data": {"id": "sub_9", "attributes": {"status": "active", "variant_id": 111, "customer_id": 55,
                                                 "renews_at": "2026-11-05T00:00:00.000000Z"}},
    }
    raw, headers = _signed(event)
    assert client.post("/api/billing/webhook/lemonsqueezy", content=raw,
                       headers={**headers, "X-Signature": "bad"}).status_code == 401
    assert client.post("/api/billing/webhook/lemonsqueezy", content=raw, headers=headers).status_code == 200
    billing = client.get("/api/billing", headers=auth).json()
    assert billing["plan"]["id"] == "pro" and billing["monthly_limit"] == 2000 and billing["plan_renews_at"]

    # Later events find the org by subscription id; expiry drops back to Free.
    expired = {"meta": {"event_name": "subscription_expired"},
               "data": {"id": "sub_9", "attributes": {"status": "expired", "variant_id": 111}}}
    raw, headers = _signed(expired)
    client.post("/api/billing/webhook/lemonsqueezy", content=raw, headers=headers)
    assert client.get("/api/billing", headers=auth).json()["plan"]["id"] == "free"


def test_upgrade_request_for_manual_payment(client, auth):
    r = client.post("/api/billing/request-upgrade", headers=auth, json={"plan": "business", "message": "Invoice please"})
    assert r.status_code == 200 and "Business" in r.json()["message"]
    assert client.get("/api/billing", headers=auth).json()["requested_plan"] == "business"
    sent = [m for m in email.outbox if m["subject"] == "Upgrade request: Shop One → Business"]
    assert sent and sent[0]["to"] == "boss@platform.com" and "Invoice please" in sent[0]["body"]

    boss = register(client, email="boss@platform.com", org="Platform HQ")
    shop = next(o for o in client.get("/api/admin/organizations", headers=boss).json() if o["name"] == "Shop One")
    assert shop["requested_plan"] == "business"
    client.patch(f"/api/admin/organizations/{shop['id']}", headers=boss, json={"plan": "business"})
    billing = client.get("/api/billing", headers=auth).json()
    assert billing["plan"]["id"] == "business" and billing["requested_plan"] is None


def test_platform_admin(client, auth):
    assert client.get("/api/admin/organizations", headers=auth).status_code == 404
    boss = register(client, email="boss@platform.com", org="Platform HQ")
    assert client.get("/api/auth/me", headers=boss).json()["is_superadmin"] is True

    orgs = client.get("/api/admin/organizations", headers=boss).json()
    shop = next(o for o in orgs if o["name"] == "Shop One")
    assert shop["owner_email"] == "owner@shop.com" and shop["plan"] == "free"

    r = client.patch(f"/api/admin/organizations/{shop['id']}", headers=boss, json={"plan": "business"})
    assert r.json()["plan"] == "business" and r.json()["monthly_generation_limit"] == 10_000
    r = client.patch(f"/api/admin/organizations/{shop['id']}", headers=boss, json={"monthly_generation_limit": 50_000})
    assert r.json()["monthly_generation_limit"] == 50_000
    assert client.get("/api/billing", headers=auth).json()["plan"]["id"] == "business"
