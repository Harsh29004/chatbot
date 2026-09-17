"""Pydantic models for the account, billing, and dashboard endpoints."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    name: str = Field(default="", max_length=120)
    # Length beats character-class rules; 8 is the floor.
    password: str = Field(..., min_length=8, max_length=200)
    # Optional and never validated here: a mistyped code costs the signup
    # nothing, it just goes unattributed.
    referral_code: str = Field(default="", max_length=32)


class ReferralInviteRequest(BaseModel):
    email: EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class CustomerResponse(BaseModel):
    id: str
    email: str
    name: str
    created_at: str


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

class PlanResponse(BaseModel):
    id: str
    name: str
    price_cents: int
    price_display: str
    currency: str
    interval: str
    daily_credits: int
    tagline: str


class PricingResponse(BaseModel):
    currency: str
    plans: list[PlanResponse]
    yearly_savings_cents: int
    yearly_savings_display: str
    yearly_discount_percent: int
    yearly_effective_monthly_display: str
    trial_days: int


class SubscriptionResponse(BaseModel):
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    status: str
    is_entitled: bool
    current_period_end: Optional[str] = None
    daily_credits: Optional[int] = None
    can_start_trial: bool = False


class CheckoutRequest(BaseModel):
    plan_id: Literal["monthly", "yearly"]


class CheckoutResponse(BaseModel):
    checkout_url: str
    reference: str
    provider: str
    requires_manual_confirmation: bool
    message: str


class InvoiceResponse(BaseModel):
    id: str
    plan_id: str
    amount_cents: int
    currency: str
    status: str
    issued_at: str
    paid_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Dashboard — API keys
# ---------------------------------------------------------------------------

class CreateKeyRequest(BaseModel):
    label: str = Field(default="", max_length=60)


class CreatedKeyResponse(BaseModel):
    api_key: str
    key_id: str
    key_prefix: str
    message: str = "Copy this key now — it is not stored and cannot be shown again."


class DashboardKey(BaseModel):
    id: str
    key_prefix: str
    label: str
    created_at: str
    is_active: int


class DashboardResponse(BaseModel):
    customer: CustomerResponse
    subscription: SubscriptionResponse
    keys: list[DashboardKey]
    usage: dict[str, Any]
