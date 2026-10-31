"""Password hashing and JWT issuance.

Uses the `bcrypt` package directly rather than passlib. passlib 1.7.4 is
unmaintained and its backend probe raises on bcrypt >= 4.1 ("password cannot be
longer than 72 bytes"), which breaks at import time rather than at use.

bcrypt's 72-byte input limit is handled by pre-hashing with SHA-256 and
base64-encoding. Two consequences worth stating:

  * Passwords of any length are supported, including long passphrases.
  * The digest is computed over the *hash*, so a 72-byte truncation collision
    is impossible.

Cost factor 12 is roughly 250ms per verify on this class of hardware -- painful
for an attacker with a dump, invisible at login.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from .config import settings


def _prepare(plain: str) -> bytes:
    """SHA-256 then base64: 44 bytes, well under bcrypt's 72-byte ceiling.

    base64 rather than the raw digest because bcrypt treats a NUL byte as a
    terminator, and a raw digest can contain one.
    """
    digest = hashlib.sha256(plain.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(_prepare(plain), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed stored hash must read as "wrong password", never as an
        # exception, or the difference becomes a probe.
        return False


def create_access_token(subject: str | int) -> tuple[str, int]:
    expires_in = settings.jwt_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {"sub": str(subject), "exp": now + timedelta(seconds=expires_in), "iat": now}
    token = jwt.encode(payload, settings.secret_key or "", algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_token(token: str) -> str | None:
    """Return the subject, or None for any failure. No partial trust."""
    try:
        payload = jwt.decode(token, settings.secret_key or "", algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
