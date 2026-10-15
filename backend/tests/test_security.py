"""Sessions, two-step login, SSO, audit log and GDPR export/deletion."""

import io
import json
import zipfile
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

from app.config import get_settings
from app.routers import sso
from app.services import totp
from tests.conftest import register, upload_sample


def login(client, email="owner@shop.com", password="password123"):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- Sessions ----------
def test_sessions_list_and_revoke(client, auth):
    sid = jwt.decode(auth["Authorization"][7:], options={"verify_signature": False})["sid"]
    other = bearer(login(client).json()["access_token"])
    sessions = client.get("/api/auth/sessions", headers=auth).json()
    assert len(sessions) == 2 and [s["current"] for s in sessions if s["id"] == sid] == [True]
    assert sessions[0]["ip"] == "testclient"

    r = client.post("/api/auth/sessions/revoke-others", headers=auth)
    assert r.json()["revoked"] == 1
    assert client.get("/api/auth/me", headers=other).status_code == 401
    assert client.get("/api/auth/me", headers=auth).status_code == 200

    third = bearer(login(client).json()["access_token"])
    third_sid = next(s["id"] for s in client.get("/api/auth/sessions", headers=auth).json() if not s["current"])
    client.delete(f"/api/auth/sessions/{third_sid}", headers=auth)
    assert client.get("/api/auth/me", headers=third).status_code == 401

    client.post("/api/auth/logout", headers=auth)
    assert client.get("/api/auth/me", headers=auth).status_code == 401


def test_deactivating_a_user_signs_them_out(client, auth):
    client.post("/api/users", headers=auth, json={"email": "ed@shop.com", "full_name": "Ed", "password": "password123", "role": "editor"})
    ed = bearer(login(client, "ed@shop.com").json()["access_token"])
    user_id = next(u["id"] for u in client.get("/api/users", headers=auth).json() if u["email"] == "ed@shop.com")
    client.patch(f"/api/users/{user_id}", headers=auth, json={"is_active": False})
    assert client.get("/api/auth/me", headers=ed).status_code == 401


# ---------- Two-step login ----------
def enable_2fa(client, headers) -> tuple[str, list[str]]:
    setup = client.post("/api/auth/2fa/setup", headers=headers).json()
    assert setup["qr_svg"].startswith("data:image/svg+xml;base64,") and setup["otpauth_uri"].startswith("otpauth://totp/")
    assert client.post("/api/auth/2fa/enable", headers=headers, json={"code": "000000"}).status_code == 400
    r = client.post("/api/auth/2fa/enable", headers=headers, json={"code": totp.now_code(setup["secret"])})
    assert r.status_code == 200
    return setup["secret"], r.json()["recovery_codes"]


def test_totp_algorithm_matches_rfc6238():
    # RFC 6238 test vector (SHA1, secret "12345678901234567890", T=59 -> 94287082, 8 digits; we use 6 -> 287082)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.now_code(secret, at=59) == "287082"
    assert totp.verify(secret, "287 082", at=59) and not totp.verify(secret, "287083", at=59)


def test_two_step_login(client, auth):
    secret, recovery = enable_2fa(client, auth)
    assert len(recovery) == 10
    assert client.get("/api/auth/me", headers=auth).json()["totp_enabled"] is True

    first = login(client).json()
    assert first["mfa_required"] and first["access_token"] == "" and first["mfa_token"]
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": first["mfa_token"], "code": "123456"}).status_code == 401
    ok = client.post("/api/auth/2fa/verify", json={"mfa_token": first["mfa_token"], "code": totp.now_code(secret)})
    assert ok.status_code == 200 and client.get("/api/auth/me", headers=bearer(ok.json()["access_token"])).status_code == 200

    # A recovery code works exactly once.
    second = login(client).json()
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": second["mfa_token"], "code": recovery[0]}).status_code == 200
    third = login(client).json()
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": third["mfa_token"], "code": recovery[0]}).status_code == 401

    # Access tokens can't be used as MFA tokens.
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": auth["Authorization"][7:], "code": totp.now_code(secret)}).status_code == 401

    bad = client.post("/api/auth/2fa/disable", headers=auth, json={"password": "password123", "code": "000000"})
    assert bad.status_code == 400
    client.post("/api/auth/2fa/disable", headers=auth, json={"password": "password123", "code": totp.now_code(secret)})
    assert login(client).json()["access_token"]


def test_workspace_can_require_2fa(client, auth):
    assert client.patch("/api/settings", headers=auth, json={"require_2fa": True}).status_code == 400  # owner first
    enable_2fa(client, auth)
    assert client.patch("/api/settings", headers=auth, json={"require_2fa": True}).json()["require_2fa"] is True
    client.post("/api/users", headers=auth, json={"email": "rev@shop.com", "full_name": "R", "password": "password123", "role": "reviewer"})
    reviewer = bearer(login(client, "rev@shop.com").json()["access_token"])
    assert client.get("/api/auth/me", headers=reviewer).json()["must_setup_2fa"] is True
    assert client.get("/api/auth/me", headers=auth).json()["must_setup_2fa"] is False


# ---------- SSO ----------
@pytest.fixture()
def google(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "gid")
    monkeypatch.setattr(settings, "google_client_secret", "gsecret")
    profile = {"email": "maria@brand.com", "email_verified": True, "name": "Maria"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["code"] == ["the-code"] and form["client_secret"] == ["gsecret"]
            return httpx.Response(200, json={"access_token": "at", "id_token": "x"})
        assert request.headers["Authorization"] == "Bearer at"
        return httpx.Response(200, json=profile)

    monkeypatch.setattr(sso, "transport", httpx.MockTransport(handler))
    return profile


def _sso(client, code="the-code"):
    start = client.get("/api/auth/sso/google/start", follow_redirects=False)
    assert start.status_code == 302 and start.headers["location"].startswith("https://accounts.google.com/")
    params = parse_qs(urlparse(start.headers["location"]).query)
    assert params["redirect_uri"] == ["http://localhost:5173/api/auth/sso/google/callback"]
    done = client.get("/api/auth/sso/google/callback", params={"code": code, "state": params["state"][0]},
                      follow_redirects=False)
    return done.headers["location"]


def test_sso_signup_and_login(client, google):
    assert client.get("/api/auth/sso/providers").json() == [{"id": "google", "name": "Google"}]
    location = _sso(client)
    assert location.startswith("http://localhost:5173/sso-callback#token=")
    token = location.split("#token=")[1]
    me = client.get("/api/auth/me", headers=bearer(token)).json()
    assert me["email"] == "maria@brand.com" and me["role"] == "owner" and me["organization"]["name"] == "Maria's workspace"
    # Second time: same account, no new workspace.
    again = client.get("/api/auth/me", headers=bearer(_sso(client).split("#token=")[1])).json()
    assert again["organization"]["id"] == me["organization"]["id"]
    assert login(client, "maria@brand.com", "anything").status_code == 401  # SSO accounts have no password


def test_sso_existing_user_and_rejections(client, auth, google):
    google["email"] = "owner@shop.com"
    token = _sso(client).split("#token=")[1]
    assert client.get("/api/auth/me", headers=bearer(token)).json()["organization"]["name"] == "Shop One"

    google["email_verified"] = False
    assert "sso_error=" in _sso(client)
    bad_state = client.get("/api/auth/sso/google/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert "sso_error=" in bad_state.headers["location"]
    assert client.get("/api/auth/sso/microsoft/start", follow_redirects=False).status_code == 404


# ---------- Audit log ----------
def test_audit_log(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id})
    client.patch("/api/settings", headers=auth, json={"anthropic_api_key": "sk-ant-x-12345678"})
    login(client, password="wrong")

    entries = client.get("/api/settings/audit-log", headers=auth).json()
    actions = [e["action"] for e in entries]
    for expected in ["auth.login", "catalog.imported", "job.created", "settings.updated", "auth.login_failed"]:
        assert expected in actions
    settings_entry = next(e for e in entries if e["action"] == "settings.updated")
    assert settings_entry["details"]["anthropic_api_key"] == "set"  # never the key itself
    assert settings_entry["actor"] == "owner@shop.com" and settings_entry["ip"] == "testclient"

    jobs_only = client.get("/api/settings/audit-log?action=job", headers=auth).json()
    assert {e["action"] for e in jobs_only} == {"job.created"}
    csv_text = client.get("/api/settings/audit-log/export", headers=auth).content.decode("utf-8-sig")
    assert csv_text.startswith("time,actor,action") and "job.created" in csv_text

    client.post("/api/users", headers=auth, json={"email": "ed@shop.com", "full_name": "Ed", "password": "password123", "role": "editor"})
    ed = bearer(login(client, "ed@shop.com").json()["access_token"])
    assert client.get("/api/settings/audit-log", headers=ed).status_code == 403
    # Other workspaces see nothing.
    other = register(client, email="x@other.com", org="Other")
    assert all(e["actor"] == "x@other.com" for e in client.get("/api/settings/audit-log", headers=other).json())


# ---------- GDPR ----------
def test_export_all_data(client, auth):
    upload_sample(client, auth)
    client.patch("/api/settings", headers=auth, json={"anthropic_api_key": "sk-ant-secret-key-1"})
    r = client.get("/api/settings/export-data", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"products.json", "users.json", "organization.json", "audit_log.json"} <= set(zf.namelist())
    assert len(json.loads(zf.read("products.json"))) == 8
    everything = b"".join(zf.read(n) for n in zf.namelist())
    assert b"password_hash" not in everything and b"sk-ant-secret" not in everything


def test_delete_workspace_and_account(client, auth):
    upload_sample(client, auth)
    other = register(client, email="x@other.com", org="Other")
    upload_sample(client, other)
    client.post("/api/users", headers=auth, json={"email": "ed@shop.com", "full_name": "Ed", "password": "password123", "role": "editor"})

    # A member can delete their own account.
    ed = bearer(login(client, "ed@shop.com").json()["access_token"])
    assert client.request("DELETE", "/api/auth/me", headers=ed, json={"password": "wrong"}).status_code == 400
    assert client.request("DELETE", "/api/auth/me", headers=ed, json={"password": "password123"}).status_code == 204
    assert login(client, "ed@shop.com").status_code == 401
    assert client.request("DELETE", "/api/auth/me", headers=auth, json={"password": "password123"}).status_code == 400

    assert client.request("DELETE", "/api/settings/organization", headers=auth,
                          json={"organization_name": "Wrong", "password": "password123"}).status_code == 400
    assert client.request("DELETE", "/api/settings/organization", headers=auth,
                          json={"organization_name": "Shop One", "password": "password123"}).status_code == 204
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    assert login(client).status_code == 401
    assert len(client.get("/api/catalogs", headers=other).json()) == 1  # other workspace untouched
