"""
Password hashing and session tokens.

Uses PBKDF2-HMAC-SHA256 from the standard library rather than pulling in
bcrypt/argon2 — one less dependency to keep patched, and PBKDF2 at a high
iteration count is still an accepted choice. If you later move to argon2id,
``verify_password`` can grow a branch on the stored prefix without a
migration: existing hashes keep their ``pbkdf2_sha256$`` marker.

Session tokens are opaque random strings. Only their SHA-256 hash is stored,
so a leaked database cannot be replayed as live sessions. They are revocable
(a JWT is not, without extra machinery).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

# OWASP's floor for PBKDF2-HMAC-SHA256 (2023 guidance).
# Override with PBKDF2_ITERATIONS in .env for faster dev signup (e.g. 10000).
_ITERATIONS = int(os.environ.get("PBKDF2_ITERATIONS", "600000"))
_ALGO = "pbkdf2_sha256"
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return a self-describing hash: ``pbkdf2_sha256$iterations$salt$hash``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return "{}${}${}${}".format(
        _ALGO,
        _ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(derived).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of *password* against a stored hash."""
    try:
        algo, iterations_s, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, int(iterations_s)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


def generate_session_token() -> str:
    """A fresh opaque session token (raw value — only ever sent to its owner)."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """SHA-256 of a session token. Only this goes in the database."""
    return hashlib.sha256(token.encode()).hexdigest()
