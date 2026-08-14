"""
JWT authentication and admin API-key verification.

- Chat endpoints require a valid JWT whose ``user_type`` claim matches
  the bot (``"customer"`` for the customer bot, ``"partner"`` for the
  partner bot).
- The admin/reindex endpoint requires a separate admin API key, never
  the same credential as user auth.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt

from shared.config import ADMIN_API_KEY, JWT_ALGORITHM, JWT_SECRET


def _decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT, raising 401 on failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        ) from exc
    return payload


def get_current_user(
    authorization: str = Header(..., description="Bearer <JWT>"),
) -> dict[str, Any]:
    """
    FastAPI dependency that extracts and validates the JWT from the
    ``Authorization`` header.

    Returns the decoded payload dict containing at minimum
    ``user_id`` and ``user_type``.
    """
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be: Bearer <token>",
        )
    return _decode_token(token)


def require_user_type(expected_type: str):
    """
    Return a FastAPI dependency that ensures the JWT's ``user_type``
    matches *expected_type*.

    Usage::

        @router.post("/ask")
        async def ask(
            body: ChatRequest,
            user: dict = Depends(require_user_type("customer")),
        ):
            ...
    """

    def _dependency(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if user.get("user_type") != expected_type:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This endpoint requires user_type='{expected_type}'.",
            )
        return user

    return _dependency


def verify_admin_key(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
) -> bool:
    """
    FastAPI dependency for the admin reindex endpoint.

    Expects an ``X-Admin-Key`` header matching the configured
    ``ADMIN_API_KEY``.
    """
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin API key.",
        )
    return True
