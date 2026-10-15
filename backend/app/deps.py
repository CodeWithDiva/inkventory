from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import ApiKey, Role, User, UserSession
from app.security import decode_access_token, hash_api_key

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

API_KEY_PREFIX = "cai_"


def _unauthorized(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value  # SQLite returns naive datetimes


def _user_from_api_key(raw: str, db: Session) -> User:
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw)))
    if key is None or key.revoked_at is not None:
        raise _unauthorized("Invalid or revoked API key")
    creator = db.get(User, key.created_by)
    if creator is None or not creator.is_active:
        raise _unauthorized("The user who created this API key is no longer active")
    now = datetime.now(timezone.utc)
    if key.last_used_at is None or now - _aware(key.last_used_at) > timedelta(minutes=1):
        key.last_used_at = now
        db.commit()
    # A detached, read-only identity: the creator's account with the key's role.
    api_user = User(id=creator.id, org_id=key.org_id, email=creator.email, full_name=f"API key: {key.name}",
                    role=key.role, is_active=True, password_hash="", created_at=key.created_at)
    api_user.api_key_id = key.id
    return api_user


def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    header_key: str | None = Depends(api_key_header),
    db: Session = Depends(get_db),
) -> User:
    if header_key or (token and token.startswith(API_KEY_PREFIX)):
        return _user_from_api_key(header_key or token, db)
    if not token:
        raise _unauthorized("Not authenticated")
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized()
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    if user.password_changed_at is not None:
        changed = _aware(user.password_changed_at)
        if payload.get("ims", payload.get("iat", 0) * 1000) < int(changed.timestamp() * 1000):
            raise _unauthorized()  # signed in before the password was changed
    session_id = payload.get("sid")
    if session_id:
        session = db.get(UserSession, session_id)
        if session is None or session.revoked_at is not None or session.user_id != user.id:
            raise _unauthorized("This session was signed out")
        now = datetime.now(timezone.utc)
        if now - _aware(session.last_seen_at) > timedelta(minutes=5):
            session.last_seen_at = now
            db.commit()
    request.state.token_payload = payload
    request.state.session_id = session_id
    return user


def is_api_key(user: User) -> bool:
    return getattr(user, "api_key_id", None) is not None


def require_session(user: User = Depends(get_current_user)) -> User:
    """Account-level actions (passwords, keys, billing) need a signed-in person, not an API key."""
    if is_api_key(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This action is not available with an API key")
    return user


def is_superadmin(user: User) -> bool:
    return not is_api_key(user) and user.email.lower() in get_settings().superadmins


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if not is_superadmin(user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


def require_roles(*roles: Role) -> Callable[..., User]:
    """Dependency factory: owners always pass, other users need one of ``roles``."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role != Role.OWNER and user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return checker


def require_session_roles(*roles: Role) -> Callable[..., User]:
    """Like require_roles, but API keys are refused."""

    def checker(user: User = Depends(require_session)) -> User:
        if user.role != Role.OWNER and user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return checker


# Common role groups
can_edit = require_roles(Role.ADMIN, Role.EDITOR)
can_review = require_roles(Role.ADMIN, Role.REVIEWER)
can_admin = require_session_roles(Role.ADMIN)


def get_owned(db: Session, model, obj_id: int, user: User):
    """Fetch a tenant-owned row or 404 (also 404 for other orgs' rows, to avoid leaking IDs)."""
    obj = db.get(model, obj_id)
    if obj is None or obj.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj
