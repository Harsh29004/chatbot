"""
Username/password sign-in for the staff tools.

The admin panel and the support inbox used to ask staff to paste the raw
admin key. Now staff sign in with ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD`` and
get a signed, expiring session token instead, which the browser sends in the
same ``X-Admin-Key`` header. The raw key keeps working for scripts.

Why a signed token rather than handing back the admin key:

- It expires (``ADMIN_SESSION_HOURS``), so a token left in a browser tab stops
  working on its own. The admin key never expires.
- It is signed with both the admin key and the password, so changing either
  one immediately invalidates every token already issued.

Failed sign-ins are counted per client address in MongoDB, so the password
can't be guessed at speed, and a restart doesn't reset the count.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from backend.shared import config, rate_limits

TOKEN_PREFIX = "nxa."

# At most this many failed attempts per address inside the window.
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60

_ADMIN_BUCKET = "admin_login_failure"


def password_login_enabled() -> bool:
    return bool(config.ADMIN_USERNAME and config.ADMIN_PASSWORD)


def _secret() -> bytes:
    return f"{config.ADMIN_API_KEY}\x00{config.ADMIN_PASSWORD}".encode()


def _sign(payload: str) -> str:
    digest = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_token(now: float | None = None) -> tuple[str, float]:
    """A signed token and the Unix time it expires."""
    expires_at = (now or time.time()) + config.ADMIN_SESSION_HOURS * 3600
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(expires_at)}).encode()
    ).decode().rstrip("=")
    return f"{TOKEN_PREFIX}{payload}.{_sign(payload)}", expires_at


def verify_token(token: str, now: float | None = None) -> bool:
    if not password_login_enabled() or not token.startswith(TOKEN_PREFIX):
        return False
    try:
        payload, signature = token[len(TOKEN_PREFIX):].split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(signature, _sign(payload)):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        expires_at = json.loads(base64.urlsafe_b64decode(padded))["exp"]
    except (ValueError, KeyError, TypeError):
        return False
    return (now or time.time()) < expires_at


def credentials_match(username: str, password: str) -> bool:
    """Constant-time on both fields, so timing reveals neither."""
    if not password_login_enabled():
        return False
    user_ok = hmac.compare_digest(username.strip().encode(), config.ADMIN_USERNAME.encode())
    pass_ok = hmac.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode())
    return user_ok and pass_ok


def is_locked_out(client: str) -> bool:
    return rate_limits.count(_ADMIN_BUCKET, client, FAILURE_WINDOW_SECONDS) >= MAX_FAILURES


def record_failure(client: str) -> None:
    rate_limits.record(_ADMIN_BUCKET, client, FAILURE_WINDOW_SECONDS)


def clear_failures(client: str) -> None:
    rate_limits.clear(_ADMIN_BUCKET, client)
