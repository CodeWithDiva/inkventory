"""Encryption for secrets stored in the database (Fernet: AES-128-CBC + HMAC).

Encrypted values are stored as "enc:<token>". Plain values written before encryption
existed are still readable and get encrypted the next time they are saved.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

PREFIX = "enc:"


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.encryption_key or base64.urlsafe_b64encode(
        hashlib.sha256(("catalogai-secrets:" + settings.secret_key).encode()).digest()
    ).decode()
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value  # legacy plain value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise ValueError("Stored secret cannot be decrypted (ENCRYPTION_KEY or SECRET_KEY changed)")


SECRET_HINTS = ("token", "secret", "key", "password")


def is_secret_field(name: str) -> bool:
    return any(h in name.lower() for h in SECRET_HINTS)


def encrypt_config(config: dict) -> dict:
    return {k: encrypt(v) if is_secret_field(k) and isinstance(v, str) else v for k, v in config.items()}


def decrypt_config(config: dict) -> dict:
    return {k: decrypt(v) if is_secret_field(k) and isinstance(v, str) else v for k, v in config.items()}


def mask(value: str) -> str:
    plain = decrypt(value) if value else ""
    return "••••" + plain[-4:] if plain else ""
