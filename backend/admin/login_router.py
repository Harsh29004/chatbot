"""
Staff sign-in: ``POST /api/admin/login``.

Kept off the admin router on purpose. That router declares the admin-key
check once for every route, and the one endpoint that hands out credentials
obviously can't require them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.admin import session

router = APIRouter(tags=["Admin"])


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


class AdminLoginResponse(BaseModel):
    token: str
    expires_at: float


@router.post("/api/admin/login", response_model=AdminLoginResponse)
async def admin_login(body: AdminLoginRequest, request: Request) -> AdminLoginResponse:
    if not session.password_login_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin sign-in isn't set up. Set ADMIN_PASSWORD on the server.",
        )

    client = request.client.host if request.client else "unknown"
    if session.is_locked_out(client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-ins. Try again in 15 minutes.",
        )

    if not session.credentials_match(body.username, body.password):
        session.record_failure(client)
        # One message for a wrong username and a wrong password alike.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong username or password.",
        )

    session.clear_failures(client)
    token, expires_at = session.issue_token()
    return AdminLoginResponse(token=token, expires_at=expires_at)
