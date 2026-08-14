"""
Per-user sliding-window rate limiter.

In-memory implementation — resets on server restart.  Good enough for
a single-instance deployment.  For multi-instance, swap to Redis.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Depends, HTTPException, status

from shared.auth import get_current_user
from shared.config import RATE_LIMIT_PER_MINUTE

# user_id -> list of request timestamps (epoch seconds)
_REQUESTS: dict[str, list[float]] = defaultdict(list)

WINDOW_SECONDS: int = 60


def _prune(user_id: str, now: float) -> None:
    """Remove timestamps older than the current window."""
    cutoff = now - WINDOW_SECONDS
    _REQUESTS[user_id] = [
        ts for ts in _REQUESTS[user_id] if ts > cutoff
    ]


def check_rate_limit(user_id: str) -> bool:
    """
    Return ``True`` if the request is within the rate limit.
    ``False`` if the user has exceeded ``RATE_LIMIT_PER_MINUTE``.
    """
    now = time.time()
    _prune(user_id, now)
    if len(_REQUESTS[user_id]) >= RATE_LIMIT_PER_MINUTE:
        return False
    _REQUESTS[user_id].append(now)
    return True


def rate_limit_dependency(
    user: dict = Depends(get_current_user),
) -> dict:
    """
    FastAPI dependency that enforces the per-user rate limit.
    Raises 429 if exceeded.  Returns the decoded user payload on success.
    """
    user_id = user.get("user_id", user.get("sub", "unknown"))
    if not check_rate_limit(user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded. Maximum {RATE_LIMIT_PER_MINUTE} "
                f"requests per minute."
            ),
        )
    return user


def reset_rate_limits() -> None:
    """Clear all stored timestamps — useful for tests."""
    _REQUESTS.clear()
