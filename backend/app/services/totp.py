"""Time-based one-time passwords (RFC 6238), compatible with Google Authenticator, Microsoft
Authenticator, Authy, 1Password and similar apps."""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def now_code(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // PERIOD))


def verify(secret: str, code: str, at: float | None = None, window: int = 1) -> bool:
    """Accepts the current code and one step either side (clock drift)."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return False
    counter = int((at or time.time()) // PERIOD)
    return any(hmac.compare_digest(_code(secret, counter + d), code) for d in range(-window, window + 1))


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={PERIOD}"


def qr_svg_data_uri(text: str) -> str:
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def new_recovery_codes(count: int = 10) -> list[str]:
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
