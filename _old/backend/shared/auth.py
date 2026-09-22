"""
API-key authentication and admin key verification.

- Chat endpoints require a valid ``X-Api-Key`` header with sufficient
  credits.  Each request deducts credits based on message length.
- Admin endpoints require a separate ``X-Admin-Key`` header.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import Header, HTTPException, Request, Response, status

from backend.shared.api_keys import (
    ROLE_OWNER,
    consume_credits,
    get_credits_remaining,
    get_next_reset_time,
    record_request,
    validate_api_key,
)
from backend.shared import config
from backend.shared.config import DAILY_CREDIT_LIMIT, get_credit_cost


async def verify_api_key(
    request: Request,
    response: Response,
    x_api_key: str = Header(..., alias="X-Api-Key"),
) -> dict[str, Any]:
    """
    FastAPI dependency that validates an API key and checks credits.

    - Validates the key against the database
    - Owner-role keys (see ``backend.shared.api_keys.create_owner_key``) skip credit
      checks and deductions entirely — unlimited, for the product owners only
    - Otherwise: calculates credit cost from the request body's message
      length, checks the account's shared credit pool, and deducts on success
    - Injects ``X-Credits-Remaining``, ``X-Credits-Daily-Limit``,
      ``X-Credits-Reset-At``, and ``X-Credit-Cost`` into response headers

    Returns the key record dict on success.
    """
    # Validate key
    key_record = validate_api_key(x_api_key)
    if key_record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or revoked API key.",
        )

    # Owner keys bypass credit checks entirely — unlimited access. They are
    # still logged: not charging is deliberate, leaving no audit trail is not.
    if key_record["role"] == ROLE_OWNER:
        try:
            body = await request.json()
            message_len = len(body.get("message", ""))
        except Exception:
            message_len = 0

        record_request(
            key_record["user_id"], key_record["id"], request.url.path, message_len
        )

        response.headers["X-Credits-Remaining"] = "unlimited"
        response.headers["X-Credits-Daily-Limit"] = "unlimited"
        response.headers["X-Credits-Reset-At"] = get_next_reset_time()
        response.headers["X-Credit-Cost"] = "0"
        return key_record

    # Calculate credit cost from message length
    try:
        body = await request.json()
        message = body.get("message", "")
        message_len = len(message)
    except Exception:
        message_len = 0

    credit_cost = get_credit_cost(message_len)

    # Check credits (pooled across all of this account's keys)
    user_id = key_record["user_id"]
    daily_limit = key_record["daily_credit_limit"]
    remaining = get_credits_remaining(user_id, daily_limit)
    if remaining < credit_cost:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "Insufficient credits",
                "credits_remaining": remaining,
                "credit_cost": credit_cost,
                "resets_at": get_next_reset_time(),
                "message": (
                    f"This query costs {credit_cost} credits but you only "
                    f"have {remaining} remaining. Credits reset at midnight IST."
                ),
            },
        )

    # Deduct credits
    endpoint = request.url.path
    # The account's own limit is passed in: without it the returned figure —
    # and therefore the X-Credits-Remaining header — would be computed against
    # the global default, understating what a paid account has left.
    new_remaining = consume_credits(
        user_id, key_record["id"], credit_cost, endpoint, message_len,
        daily_credit_limit=daily_limit,
    )

    # Set response headers
    response.headers["X-Credits-Remaining"] = str(new_remaining)
    response.headers["X-Credits-Daily-Limit"] = str(
        DAILY_CREDIT_LIMIT if daily_limit is None else daily_limit
    )
    response.headers["X-Credits-Reset-At"] = get_next_reset_time()
    response.headers["X-Credit-Cost"] = str(credit_cost)

    return key_record


async def verify_owner_key(
    request: Request,
    x_api_key: str = Header(..., alias="X-Api-Key"),
) -> dict[str, Any]:
    """
    FastAPI dependency for the internal owner endpoints.

    Stricter than ``verify_api_key``: a valid customer key is **not** enough,
    the key must carry the owner role. These routes read across every tenant,
    so the check is role-based rather than "is this key valid".

    No credits are consumed — owner keys are unmetered by design — but the
    request is still written to the audit log. An unlimited key that can read
    every tenant's data is the one most worth a trail.
    """
    key_record = validate_api_key(x_api_key)
    if key_record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or revoked API key.",
        )

    if key_record["role"] != ROLE_OWNER:
        # Deliberately the same message a bad key gets. A customer key holder
        # probing this route learns only that it didn't work, not that they
        # found a real endpoint gated on a role they don't have.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or revoked API key.",
        )

    try:
        body = await request.json()
        message_len = len(body.get("message", ""))
    except Exception:
        message_len = 0

    record_request(
        key_record["user_id"], key_record["id"], request.url.path, message_len
    )
    return key_record


def verify_admin_key(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
) -> bool:
    """
    FastAPI dependency for admin endpoints.

    Accepts either the configured ``ADMIN_API_KEY`` (for scripts) or an
    unexpired session token from ``POST /api/admin/login`` (for the staff
    pages), both in the ``X-Admin-Key`` header.

    The key is read off ``config`` per call rather than bound at import. A
    module-level ``from ... import ADMIN_API_KEY`` takes a snapshot, and a
    snapshot silently drifts: whatever the value happened to be the first
    time this module was imported is what the check uses forever, even after
    the setting changes. That cost a confusing afternoon in the test suite,
    where the snapshot captured a patched value and outlived the patch.
    """
    from backend.admin import session  # local: admin imports shared, not the reverse at load

    key_ok = hmac.compare_digest(x_admin_key.encode(), config.ADMIN_API_KEY.encode())
    if not key_ok and not session.verify_token(x_admin_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin API key.",
        )
    return True

