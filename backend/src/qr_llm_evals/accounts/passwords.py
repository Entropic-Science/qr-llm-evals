"""Password hashing with stdlib scrypt (no third-party dependency).

Stored form: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``. The cost parameters
travel with the hash, so they can be raised later without invalidating old
rows; :func:`needs_rehash` says when a row is below the current cost.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import secrets

SCHEME = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
HASH_BYTES = 32
#: scrypt needs ~128 * n * r bytes; leave headroom over the 16 MiB that n=2**14 takes.
MAXMEM = 64 * 1024 * 1024

PASSWORD_MIN = 10
PASSWORD_MAX = 256


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=HASH_BYTES, maxmem=MAXMEM
    )


def hash_password(password: str, *, n: int = SCRYPT_N) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, n, SCRYPT_R, SCRYPT_P)
    return f"{SCHEME}${n}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check; a malformed stored value is simply a non-match."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != SCHEME:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        actual = _derive(password, salt, int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str, *, n: int = SCRYPT_N) -> bool:
    parts = stored.split("$")
    return len(parts) != 6 or parts[0] != SCHEME or parts[1] != str(n)


@functools.cache
def dummy_hash() -> str:
    """Verified against when the username is unknown, so both paths cost one scrypt.

    Computed on first use, not at import: importing the package stays cheap.
    """
    return hash_password("not-a-real-password")


__all__ = [
    "PASSWORD_MAX",
    "PASSWORD_MIN",
    "dummy_hash",
    "hash_password",
    "needs_rehash",
    "verify_password",
]
