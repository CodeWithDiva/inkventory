from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import decrypt, encrypt
from app.db import get_db
from app.deps import can_admin, get_current_user, get_owned, is_api_key, is_superadmin, require_session
from app.models import Organization, Role, User, UserSession
from app.plans import DEFAULT_PLAN, get_plan
from app.schemas import (
    ChangePasswordIn,
    DeleteAccountIn,
    ForgotPasswordIn,
    LoginOut,
    MeOut,
    MfaVerifyIn,
    OrganizationOut,
    RegisterIn,
    ResetPasswordIn,
    SessionOut,
    TokenOut,
    TwoFactorCodeIn,
    TwoFactorDisableIn,
    TwoFactorEnabledOut,
    TwoFactorSetupOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.security import (
    RateLimiter,
    create_mfa_token,
    create_reset_token,
    decode_reset_token,
    decode_typed_token,
    hash_password,
    reset_token_matches,
    verify_password,
)
from app.services import audit, email, sessions, totp

router = APIRouter(tags=["auth"])

login_limiter = RateLimiter(get_settings().login_attempts_per_15_min, 15 * 60)
reset_limiter = RateLimiter(5, 60 * 60)
mfa_limiter = RateLimiter(5, 5 * 60)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "?")


def _current_session_id(request: Request) -> str | None:
    return getattr(request.state, "session_id", None)


@router.post("/auth/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    """Sign up a new customer: creates their organization (Free plan) and makes them its owner."""
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = create_owner_account(db, body.organization_name, body.full_name, body.email, hash_password(body.password))
    email.welcome(user.email, user.full_name, body.organization_name)
    token = sessions.start(db, user, "password")
    audit.record(db, user, "auth.login", details={"method": "signup"})
    return TokenOut(access_token=token)


def create_owner_account(db: Session, org_name: str, full_name: str, email_addr: str, password_hash: str) -> User:
    plan = get_plan(DEFAULT_PLAN)
    org = Organization(name=org_name, plan=plan.id, monthly_generation_limit=plan.monthly_generations)
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=email_addr.lower(), full_name=full_name, password_hash=password_hash, role=Role.OWNER)
    db.add(user)
    db.commit()
    return user


@router.post("/auth/login", response_model=LoginOut)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    key = f"{form.username.lower()}|{_client_ip(request)}"
    if not login_limiter.hit(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-in attempts. Try again in 15 minutes.")
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if user is None or not verify_password(form.password, user.password_hash):
        if user is not None:
            audit.record(db, user, "auth.login_failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")
    login_limiter.reset(key)
    if user.totp_enabled:
        return LoginOut(mfa_required=True, mfa_token=create_mfa_token(user.id))
    token = sessions.start(db, user, "password")
    audit.record(db, user, "auth.login", details={"method": "password"})
    return LoginOut(access_token=token)


@router.post("/auth/2fa/verify", response_model=TokenOut)
def verify_second_factor(body: MfaVerifyIn, db: Session = Depends(get_db)):
    """Second login step: the code from the authenticator app, or a one-time recovery code."""
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired code. Please sign in again.")
    try:
        user = db.get(User, int(decode_typed_token(body.mfa_token, "mfa")["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise invalid
    if user is None or not user.is_active or not user.totp_enabled:
        raise invalid
    if not mfa_limiter.hit(str(user.id)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Wait a few minutes.")
    code = body.code.strip()
    method = "2fa"
    if not totp.verify(decrypt(user.totp_secret), code):
        hashed = totp.hash_recovery_code(code)
        if hashed not in (user.recovery_codes or []):
            audit.record(db, user, "auth.login_failed", details={"reason": "wrong 2FA code"})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code is not correct")
        user.recovery_codes = [c for c in user.recovery_codes if c != hashed]  # single use
        method = "recovery_code"
    mfa_limiter.reset(str(user.id))
    token = sessions.start(db, user, method)
    audit.record(db, user, "auth.login", details={"method": method})
    return TokenOut(access_token=token)


@router.post("/auth/forgot-password")
def forgot_password(body: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)):
    """Always returns the same answer so it cannot be used to discover which emails have accounts."""
    if reset_limiter.hit(f"{body.email.lower()}|{_client_ip(request)}"):
        user = db.scalar(select(User).where(User.email == body.email.lower()))
        if user and user.is_active:
            token = create_reset_token(user.id, user.password_hash)
            email.password_reset(user.email, user.full_name, f"{get_settings().app_url}/reset-password?token={token}")
    return {"message": "If an account exists for this email, a reset link has been sent."}


def _set_password(user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    user.password_changed_at = datetime.now(timezone.utc)


@router.post("/auth/reset-password", response_model=TokenOut)
def reset_password(body: ResetPasswordIn, db: Session = Depends(get_db)):
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    try:
        payload = decode_reset_token(body.token)
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise invalid
    if user is None or not user.is_active or not reset_token_matches(payload, user.password_hash):
        raise invalid
    _set_password(user, body.new_password)
    db.commit()
    sessions.revoke_all(db, user.id)
    audit.record(db, user, "auth.password_reset")
    # The emailed link proves access to the mailbox; 2FA users still need their second factor.
    if user.totp_enabled:
        return TokenOut(access_token="", mfa_required=True, mfa_token=create_mfa_token(user.id))
    return TokenOut(access_token=sessions.start(db, user, "reset"))


@router.post("/auth/change-password", response_model=TokenOut)
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(require_session),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    _set_password(user, body.new_password)
    db.commit()
    sessions.revoke_all(db, user.id)
    audit.record(db, user, "auth.password_changed")
    # Every other device is signed out; this one continues with a fresh session.
    return TokenOut(access_token=sessions.start(db, user, "password"))


@router.get("/auth/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # API-key identities are detached objects without the organization relationship loaded.
    org_row = db.get(Organization, user.org_id)
    org = OrganizationOut.model_validate(org_row)
    return MeOut(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role, is_active=user.is_active,
        created_at=user.created_at, organization=org, is_superadmin=is_superadmin(user),
        totp_enabled=bool(user.totp_enabled),
        must_setup_2fa=bool(org_row.require_2fa and not user.totp_enabled and not is_api_key(user)),
    )


@router.delete("/auth/me", status_code=204)
def delete_my_account(body: DeleteAccountIn, user: User = Depends(require_session), db: Session = Depends(get_db)):
    """Team members can delete their own account (GDPR). Owners delete the whole workspace instead."""
    if user.role == Role.OWNER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Owners delete the workspace from Settings → Danger zone")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password is incorrect")
    audit.record(db, user, "user.deleted", target_type="user", target_id=user.id)
    sessions.revoke_all(db, user.id)
    # Content they created stays with the company; personal data is removed.
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.full_name = "Deleted user"
    user.password_hash = "!"
    user.is_active = False
    user.totp_secret, user.totp_enabled, user.recovery_codes = None, False, []
    db.commit()


# ---------- Two-step login ----------
@router.post("/auth/2fa/setup", response_model=TwoFactorSetupOut)
def setup_2fa(user: User = Depends(require_session), db: Session = Depends(get_db)):
    """Start enrolment: returns a secret and QR code. Not active until confirmed with a code."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two-step login is already on")
    secret = totp.new_secret()
    user.totp_secret = encrypt(secret)
    db.commit()
    uri = totp.provisioning_uri(secret, user.email, get_settings().app_name)
    return TwoFactorSetupOut(secret=secret, otpauth_uri=uri, qr_svg=totp.qr_svg_data_uri(uri))


@router.post("/auth/2fa/enable", response_model=TwoFactorEnabledOut)
def enable_2fa(body: TwoFactorCodeIn, user: User = Depends(require_session), db: Session = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two-step login is already on")
    if not user.totp_secret or not totp.verify(decrypt(user.totp_secret), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code is not correct. Check the time on your phone.")
    codes = totp.new_recovery_codes()
    user.totp_enabled = True
    user.recovery_codes = [totp.hash_recovery_code(c) for c in codes]
    db.commit()
    audit.record(db, user, "auth.2fa_enabled")
    return TwoFactorEnabledOut(recovery_codes=codes)


@router.post("/auth/2fa/disable", status_code=204)
def disable_2fa(body: TwoFactorDisableIn, user: User = Depends(require_session), db: Session = Depends(get_db)):
    if not user.totp_enabled:
        return
    org = db.get(Organization, user.org_id)
    if org.require_2fa:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your workspace requires two-step login")
    if not verify_password(body.password, user.password_hash) or not totp.verify(decrypt(user.totp_secret), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password or code is not correct")
    user.totp_enabled, user.totp_secret, user.recovery_codes = False, None, []
    db.commit()
    audit.record(db, user, "auth.2fa_disabled")


@router.post("/auth/2fa/recovery-codes", response_model=TwoFactorEnabledOut)
def regenerate_recovery_codes(body: TwoFactorCodeIn, user: User = Depends(require_session), db: Session = Depends(get_db)):
    if not user.totp_enabled or not totp.verify(decrypt(user.totp_secret), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code is not correct")
    codes = totp.new_recovery_codes()
    user.recovery_codes = [totp.hash_recovery_code(c) for c in codes]
    db.commit()
    return TwoFactorEnabledOut(recovery_codes=codes)


# ---------- Sessions (signed-in devices) ----------
@router.get("/auth/sessions", response_model=list[SessionOut])
def list_sessions(request: Request, user: User = Depends(require_session), db: Session = Depends(get_db)):
    rows = db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                      .order_by(UserSession.last_seen_at.desc()).limit(50)).all()
    current = _current_session_id(request)
    return [SessionOut.model_validate(s).model_copy(update={"current": s.id == current}) for s in rows]


@router.delete("/auth/sessions/{session_id}", status_code=204)
def revoke_session(session_id: str, user: User = Depends(require_session), db: Session = Depends(get_db)):
    session = db.get(UserSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    session.revoked_at = session.revoked_at or datetime.now(timezone.utc)
    db.commit()
    audit.record(db, user, "auth.session_revoked", target_type="session", target_id=session_id)


@router.post("/auth/sessions/revoke-others")
def revoke_other_sessions(request: Request, user: User = Depends(require_session), db: Session = Depends(get_db)):
    count = sessions.revoke_all(db, user.id, except_id=_current_session_id(request))
    audit.record(db, user, "auth.session_revoked", details={"count": count, "scope": "all other devices"})
    return {"revoked": count}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, user: User = Depends(require_session), db: Session = Depends(get_db)):
    session_id = _current_session_id(request)
    if session_id:
        session = db.get(UserSession, session_id)
        if session:
            session.revoked_at = datetime.now(timezone.utc)
            db.commit()
    audit.record(db, user, "auth.logout")


# ---------- Team management ----------
@router.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.org_id == user.org_id).order_by(User.id)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(can_admin), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    org = db.get(Organization, admin.org_id)
    plan = get_plan(org.plan)
    members = db.scalar(select(func.count(User.id)).where(User.org_id == org.id, User.is_active.is_(True))) or 0
    if members >= plan.max_users:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"The {plan.name} plan allows {plan.max_users} team members. Upgrade to add more.",
        )
    user = User(
        org_id=admin.org_id,
        email=body.email.lower(),
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    email.team_invite(user.email, user.full_name, org.name, admin.full_name)
    audit.record(db, admin, "user.created", target_type="user", target_id=user.id,
                 details={"email": user.email, "role": user.role})
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(can_admin), db: Session = Depends(get_db)):
    target = get_owned(db, User, user_id, admin)
    if target.role == Role.OWNER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The organization owner cannot be modified")
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(target, field, value)
    db.commit()
    if changes.get("is_active") is False:
        sessions.revoke_all(db, target.id)  # deactivated users are signed out everywhere
    audit.record(db, admin, "user.updated", target_type="user", target_id=target.id,
                 details={"email": target.email, **changes})
    return target
