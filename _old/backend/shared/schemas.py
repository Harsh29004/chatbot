"""
Pydantic request / response models for API-key management and health.

The bot API's own request/response models live with it, in
``bot/router.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------

class GenerateKeyRequest(BaseModel):
    """Request to generate a new API key."""

    owner_email: str = Field(
        ..., min_length=3, max_length=255, description="Customer's email address."
    )
    owner_name: str = Field(
        default="", max_length=255, description="Customer's name or company."
    )
    label: str = Field(
        default="",
        max_length=255,
        description="Optional label for this key (e.g. 'production', 'staging').",
    )


class GenerateKeyResponse(BaseModel):
    """Response after generating a new API key."""

    api_key: str = Field(
        ..., description="The raw API key — save it, it won't be shown again!"
    )
    key_id: str = Field(..., description="Internal key ID.")
    key_prefix: str = Field(..., description="First 12 chars of the key for display.")
    owner_email: str
    owner_name: str
    message: str = (
        "Save this key — it won't be shown again! Credits are shared across "
        "every key on this account (identified by owner_email), not per-key."
    )


class KeyInfo(BaseModel):
    """Summary of a single API key (for admin listing)."""

    id: str
    key_prefix: str
    label: str
    owner_email: str
    owner_name: str
    role: str
    created_at: str
    is_active: int
    credits_remaining: int


class KeyListResponse(BaseModel):
    """Admin response listing all API keys."""

    total: int
    keys: list[KeyInfo]


class DailyUsageEntry(BaseModel):
    """One day's credit usage."""

    usage_date: str
    credits_used: int


class UsageResponse(BaseModel):
    """Account's credit usage and remaining balance (pooled across all of the account's keys)."""

    is_unlimited: bool = Field(
        default=False, description="True for owner accounts, which have no credit limit."
    )
    credits_remaining: int = Field(
        ..., description="-1 when is_unlimited is True."
    )
    credits_used_today: int
    credits_daily_limit: int = Field(
        ..., description="-1 when is_unlimited is True."
    )
    resets_at: str = Field(..., description="Next credit reset time (midnight IST).")
    total_queries_all_time: int
    total_credits_consumed_all_time: int
    last_7_days: list[DailyUsageEntry]


class CreditCostInfo(BaseModel):
    """Information about how credits are charged."""

    tiers: list[dict] = Field(
        ..., description="Credit cost tiers based on message length."
    )
    daily_limit: int


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Health-check response."""

    status: str = "ok"

