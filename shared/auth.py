"""
API-key authentication and admin key verification.

- Chat endpoints require a valid ``X-Api-Key`` header with sufficient
  credits.  Each request deducts credits based on message length.
- Admin endpoints require a separate ``X-Admin-Key`` header.
"""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request, Response, status

from shared.api_keys import (
    ROLE_OWNER,
    consume_credits,
    get_credits_remaining,
    get_next_reset_time,
    record_request,
    validate_api_key,
)
from shared.config import ADMIN_API_KEY, DAILY_CREDIT_LIMIT, get_credit_cost


async def verify_api_key(
    request: Request,
    response: Response,
    x_api_key: str = Header(..., alias="X-Api-Key"),
) -> dict[str, Any]:
    """
    FastAPI dependency that validates an API key and checks credits.

    - Validates the key against the database
    - Owner-role keys (see ``shared.api_keys.create_owner_key``) skip credit
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
    new_remaining = consume_credits(
        user_id, key_record["id"], credit_cost, endpoint, message_len
    )

    # Set response headers
    response.headers["X-Credits-Remaining"] = str(new_remaining)
    response.headers["X-Credits-Daily-Limit"] = str(
        DAILY_CREDIT_LIMIT if daily_limit is None else daily_limit
    )
    response.headers["X-Credits-Reset-At"] = get_next_reset_time()
    response.headers["X-Credit-Cost"] = str(credit_cost)

    return key_record


def verify_admin_key(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
) -> bool:
    """
    FastAPI dependency for admin endpoints.

    Expects an ``X-Admin-Key`` header matching the configured
    ``ADMIN_API_KEY``.
    """
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin API key.",
        )
    return True

