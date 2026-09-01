"""
API key management router.

Admin endpoints for generating, listing, and revoking API keys.
Customer endpoint for checking usage/credits.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from shared.api_keys import (
    generate_api_key,
    get_next_reset_time,
    get_usage_stats,
    list_all_keys,
    revoke_key,
    validate_api_key,
)
from shared.auth import verify_admin_key
from shared.config import CREDIT_COST_TIERS, DAILY_CREDIT_LIMIT
from shared.schemas import (
    CreditCostInfo,
    GenerateKeyRequest,
    GenerateKeyResponse,
    KeyListResponse,
    UsageResponse,
)

router = APIRouter(prefix="/api/keys", tags=["API Keys"])


# ---------------------------------------------------------------------------
# Admin endpoints (require X-Admin-Key)
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=GenerateKeyResponse)
async def create_key(
    body: GenerateKeyRequest,
    _admin: bool = Depends(verify_admin_key),
) -> GenerateKeyResponse:
    """
    Generate a new API key for a customer.

    The raw key is returned **once** in the response — it cannot be
    retrieved again (only the hash is stored).
    """
    result = generate_api_key(
        owner_email=body.owner_email,
        owner_name=body.owner_name,
        label=body.label,
    )
    return GenerateKeyResponse(**result)


@router.get("", response_model=KeyListResponse)
async def list_keys(
    _admin: bool = Depends(verify_admin_key),
) -> KeyListResponse:
    """List all API keys with their current credit balance (admin only)."""
    keys = list_all_keys()
    return KeyListResponse(total=len(keys), keys=keys)


@router.delete("/{key_id}")
async def delete_key(
    key_id: int,
    _admin: bool = Depends(verify_admin_key),
) -> dict:
    """Revoke an API key (admin only). The key becomes immediately unusable."""
    success = revoke_key(key_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with id={key_id} not found.",
        )
    return {"status": "revoked", "key_id": key_id}


# ---------------------------------------------------------------------------
# Customer endpoints (require X-Api-Key)
# ---------------------------------------------------------------------------

@router.get("/usage", response_model=UsageResponse)
async def check_usage(
    x_api_key: str = Header(..., alias="X-Api-Key"),
) -> UsageResponse:
    """
    Check your credit usage and remaining balance.

    This endpoint does NOT consume credits.
    """
    key_record = validate_api_key(x_api_key)
    if key_record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or revoked API key.",
        )

    if key_record["role"] == "owner":
        return UsageResponse(
            is_unlimited=True,
            credits_remaining=-1,
            credits_used_today=0,
            credits_daily_limit=-1,
            resets_at=get_next_reset_time(),
            total_queries_all_time=0,
            total_credits_consumed_all_time=0,
            last_7_days=[],
        )

    # Usage is account-level: pooled across every key this owner_email holds.
    stats = get_usage_stats(key_record["user_id"], key_record["daily_credit_limit"])
    return UsageResponse(**stats)


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@router.get("/pricing", response_model=CreditCostInfo)
async def get_pricing() -> CreditCostInfo:
    """
    Public endpoint showing credit cost tiers and daily limit.

    No authentication required.
    """
    tiers = [
        {"max_characters": max_chars, "credit_cost": cost}
        for max_chars, cost in CREDIT_COST_TIERS
    ]
    return CreditCostInfo(tiers=tiers, daily_limit=DAILY_CREDIT_LIMIT)
