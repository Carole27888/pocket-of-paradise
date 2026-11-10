"""Time-based one-time passwords (RFC 6238) with recovery codes. No third-party dependency."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from django.conf import settings


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_at(secret: str, when: float | None = None, step: int = 30) -> str:
    return _hotp(secret, int((when if when is not None else time.time()) // step))


def verify(secret: str, code: str, last_counter: int = 0, window: int = 1, step: int = 30, now: float | None = None):
    """Returns the matching time counter (so it can be stored and never reused) or None."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    counter = int((now if now is not None else time.time()) // step)
    for c in range(counter - window, counter + window + 1):
        if c > last_counter and hmac.compare_digest(_hotp(secret, c), code):
            return c
    return None


def otpauth_uri(email: str, secret: str, issuer: str | None = None) -> str:
    issuer = issuer or settings.COMPANY_NAME
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def new_recovery_codes(n: int = 8):
    """(plain codes shown once, hashes to store)."""
    plain = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return plain, [hash_code(c) for c in plain]


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()


def feature_on() -> bool:
    """Two-factor is on when the environment forces it, or an administrator switched it on under Security Settings."""
    if settings.TWO_FACTOR_ENABLED:
        return True
    from django.core.cache import cache

    val = cache.get("2fa_feature_on")
    if val is None:
        try:
            from apps.core.models import CompanySetting

            val = bool(CompanySetting.load().two_factor_enabled)
        except Exception:   # before the column exists (first migrate)
            val = False
        cache.set("2fa_feature_on", val, 60)
    return val


def set_feature(on: bool) -> None:
    from django.core.cache import cache

    from apps.core.models import CompanySetting

    obj = CompanySetting.load()
    obj.two_factor_enabled = on
    obj.save(update_fields=["two_factor_enabled"])
    cache.delete("2fa_feature_on")


def requires_2fa(user) -> bool:
    return bool(user.is_authenticated and user.has_role(*settings.TWO_FACTOR_REQUIRED_ROLES) and feature_on())
