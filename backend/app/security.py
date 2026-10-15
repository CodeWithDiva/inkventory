import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings

ALGORITHM = "HS256"
RESET_TOKEN_MINUTES = 60


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_access_token(user_id: int, org_id: int, session_id: str | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id), "org": org_id, "exp": expire, "typ": "access",
        "iat": int(now.timestamp()),
        "ims": int(now.timestamp() * 1000),  # issued-at in ms, compared with password changes
    }
    if session_id:
        payload["sid"] = session_id
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


MFA_TOKEN_MINUTES = 5


def create_mfa_token(user_id: int) -> str:
    """Short-lived proof that the password was correct; exchanged for an access token with a 2FA code."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=MFA_TOKEN_MINUTES)
    return jwt.encode({"sub": str(user_id), "typ": "mfa", "exp": expire}, get_settings().secret_key, algorithm=ALGORITHM)


def decode_typed_token(token: str, typ: str) -> dict:
    payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    if payload.get("typ") != typ:
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def create_state_token(data: dict, minutes: int = 10) -> str:
    """Signed, expiring value for OAuth 'state' (prevents CSRF on the SSO callback)."""
    payload = {**data, "typ": "state", "nonce": secrets.token_urlsafe(8),
               "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes)}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    if payload.get("typ", "access") != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def new_api_key() -> tuple[str, str, str]:
    """(raw key shown once, display prefix, sha256 hash stored)."""
    raw = "cai_" + secrets.token_urlsafe(32)
    return raw, raw[:12], hash_api_key(raw)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _password_fingerprint(password_hash: str) -> str:
    # Changes whenever the password changes, so a reset link works only once.
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_reset_token(user_id: int, password_hash: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_MINUTES)
    payload = {"sub": str(user_id), "typ": "reset", "pwf": _password_fingerprint(password_hash), "exp": expire}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_reset_token(token: str) -> dict:
    payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    if payload.get("typ") != "reset":
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def reset_token_matches(payload: dict, password_hash: str) -> bool:
    return payload.get("pwf") == _password_fingerprint(password_hash)


class RateLimiter:
    """In-memory sliding-window limiter (per API process). Use Redis-backed limits when scaling out."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit, self.window = limit, window_seconds
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record an attempt; False if the key is over the limit."""
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self, key: str) -> None:
        with self.lock:
            self.hits.pop(key, None)
