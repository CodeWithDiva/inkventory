"""Sign in with Google or Microsoft (OpenID Connect authorization-code flow).

The ID provider vouches for the email address; it is matched to an existing account, or a new
workspace is created when SSO_ALLOW_SIGNUP is on. SSO sign-ins don't ask for the app's own 2FA
code: the identity provider's login (and its MFA) is the strong factor.
"""

import secrets
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.security import create_state_token, decode_typed_token
from app.services import audit, email, sessions

router = APIRouter(prefix="/auth/sso", tags=["auth"])

# Overridable in tests.
transport: httpx.BaseTransport | None = None


def _providers() -> dict[str, dict]:
    s = get_settings()
    tenant = s.microsoft_tenant or "common"
    providers = {}
    if s.google_client_id and s.google_client_secret:
        providers["google"] = {
            "name": "Google", "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
            "token": "https://oauth2.googleapis.com/token",
            "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        }
    if s.microsoft_client_id and s.microsoft_client_secret:
        providers["microsoft"] = {
            "name": "Microsoft", "client_id": s.microsoft_client_id, "client_secret": s.microsoft_client_secret,
            "authorize": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            "token": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            "userinfo": "https://graph.microsoft.com/oidc/userinfo",
        }
    return providers


def _redirect_uri(provider: str) -> str:
    return f"{get_settings().app_url.rstrip('/')}/api/auth/sso/{provider}/callback"


def _fail(message: str) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().app_url.rstrip('/')}/login?{urlencode({'sso_error': message})}", 302)


@router.get("/providers")
def providers():
    return [{"id": pid, "name": p["name"]} for pid, p in _providers().items()]


@router.get("/{provider}/start")
def start(provider: str):
    config = _providers().get(provider)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This sign-in method is not set up")
    params = {
        "client_id": config["client_id"],
        "redirect_uri": _redirect_uri(provider),
        "response_type": "code",
        "scope": "openid email profile",
        "state": create_state_token({"provider": provider}),
        "prompt": "select_account",
    }
    return RedirectResponse(f"{config['authorize']}?{urlencode(params)}", 302)


@router.get("/{provider}/callback")
def callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    config = _providers().get(provider)
    if config is None:
        return _fail("This sign-in method is not set up")
    if error or not code or not state:
        return _fail("Sign-in was cancelled")
    try:
        if decode_typed_token(state, "state").get("provider") != provider:
            raise jwt.InvalidTokenError("provider mismatch")
    except jwt.PyJWTError:
        return _fail("The sign-in link expired. Please try again.")

    try:
        with httpx.Client(timeout=15, transport=transport) as client:
            token = client.post(config["token"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(provider),
                "client_id": config["client_id"], "client_secret": config["client_secret"],
            })
            token.raise_for_status()
            info = client.get(config["userinfo"],
                              headers={"Authorization": f"Bearer {token.json()['access_token']}"})
            info.raise_for_status()
            profile = info.json()
    except (httpx.HTTPError, KeyError, ValueError):
        return _fail(f"Could not verify your {config['name']} account")

    email_addr = (profile.get("email") or profile.get("preferred_username") or "").strip().lower()
    if not email_addr or "@" not in email_addr:
        return _fail(f"Your {config['name']} account did not share an email address")
    if profile.get("email_verified") is False:
        return _fail("Your email address is not verified with the provider")

    user = db.scalar(select(User).where(User.email == email_addr))
    if user is not None and not user.is_active:
        return _fail("This account has been deactivated")
    if user is None:
        if not get_settings().sso_allow_signup:
            return _fail("No account exists for this email. Ask your admin to invite you.")
        from app.routers.auth import create_owner_account

        name = profile.get("name") or email_addr.split("@")[0]
        user = create_owner_account(db, f"{name}'s workspace", name, email_addr, "!" + secrets.token_hex(8))
        email.welcome(user.email, user.full_name, f"{name}'s workspace")
    user.sso_provider = provider
    db.commit()
    access_token = sessions.start(db, user, f"sso:{provider}")
    audit.record(db, user, "auth.login", details={"method": f"sso:{provider}"})
    # Fragment: the token never reaches server logs or Referer headers.
    return RedirectResponse(f"{get_settings().app_url.rstrip('/')}/sso-callback#token={access_token}", 302)
