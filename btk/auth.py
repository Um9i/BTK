"""Password hashing and session-token helpers for the website login.

Stdlib-only (hashlib.scrypt) rather than pulling in bcrypt/passlib -- scrypt
is memory-hard and built into Python 3.6+, which is enough for a small
Discord-alliance tool with a handful of accounts.
"""

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

SESSION_COOKIE_NAME = "btk_session"
SESSION_TTL = timedelta(days=30)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 64


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
    )
    return hmac.compare_digest(derived, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry() -> datetime:
    return datetime.now(UTC) + SESSION_TTL
