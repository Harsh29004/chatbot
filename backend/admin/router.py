"""
The admin panel's HTTP surface.

Gated on ``X-Admin-Key`` — the same header and the same shared secret the
staff support inbox already uses, so there is one staff credential rather than
two. Every route in this module reads or writes across *all* tenants, which is
why the dependency is declared once on the router rather than per route: a new
endpoint added here is protected by default, and forgetting the decorator
cannot quietly open a cross-tenant hole.

Reads live in ``backend.admin.queries``. Writes deliberately do not: granting
credits goes through ``api_keys``, changing a template goes through
``bot.catalogue``, and deactivating an account goes through both stores that
have an opinion about it. This module's job is to validate input, call the
module that owns the change, and say what happened.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.admin import queries
from backend.billing import db as billing_db
from backend.billing import referrals
from backend.billing.plans import list_plans
from backend.shared import llm
from backend.shared.api_keys import (
    get_user_by_email,
    grant_bonus_credits,
    list_credit_grants,
    revoke_key,
    set_daily_credit_limit,
)
from backend.shared.auth import verify_admin_key
from bot import catalogue

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_admin_key)],
)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class GrantCreditsRequest(BaseModel):
    # Bounded so a slipped keypress cannot mint a million credits.
    credits: int = Field(..., gt=0, le=1_000_000)
    note: str = Field(default="", max_length=200)


class SetLimitRequest(BaseModel):
    # None restores the platform default rather than setting zero — "no
    # override" and "no credits" are very different instructions.
    daily_credit_limit: Optional[int] = Field(default=None, ge=0, le=10_000_000)


class SetActiveRequest(BaseModel):
    is_active: bool


class TemplateUpdateRequest(BaseModel):
    """
    Every field optional, and ``None`` means "leave it alone".

    Resetting one field back to the code default is a separate, explicit list
    (``reset``) precisely because ``None`` already means something here.
    """

    enabled: Optional[bool] = None
    name: Optional[str] = Field(default=None, max_length=80)
    tagline: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    scope_label: Optional[str] = Field(default=None, max_length=200)
    decline_message: Optional[str] = Field(default=None, max_length=600)
    near_match_suffix: Optional[str] = Field(default=None, max_length=600)
    strong_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    near_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reset: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def _clamp_days(days: int) -> int:
    return max(1, min(days, 365))


@router.get("/overview")
async def overview(days: int = 30) -> dict[str, Any]:
    """Headline platform numbers: accounts, plans, revenue, usage, referrals."""
    return await asyncio.to_thread(queries.overview, _clamp_days(days))


@router.get("/health")
async def platform_health() -> dict[str, Any]:
    """
    What the panel needs to explain a number, rather than just show it.

    "Grounded rewording is on for 12 bots" means something different when no
    model is reachable, so the model's actual state belongs on the same screen.
    """
    return {
        "llm_available": llm.available(),
        "llm_model": llm.model_name() if llm.available() else None,
        "plans": list_plans(),
        "referral_rewards": {
            "referrer_signup_credits": referrals.REFERRER_SIGNUP_CREDITS,
            "referred_signup_credits": referrals.REFERRED_SIGNUP_CREDITS,
            "topup_credits": referrals.TOPUP_CREDITS,
        },
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/users")
async def users(
    q: str = "",
    status_filter: str = "",
    days: int = 30,
    sort: str = "created",
    limit: int = queries.DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Every customer, with plan, usage, bot and referral standing.

    ``status_filter`` takes a subscription status, or ``entitled`` (can use the
    product now), ``paying`` (on a paid plan), or ``disabled``.
    """
    return await asyncio.to_thread(
        queries.list_users,
        query=q,
        status=status_filter,
        days=_clamp_days(days),
        sort=sort,
        limit=limit,
        offset=max(0, offset),
    )


@router.get("/users/{customer_id}")
async def user_detail(customer_id: str, days: int = 30) -> dict[str, Any]:
    detail = await asyncio.to_thread(queries.user_detail, customer_id, _clamp_days(days))
    if detail is None:
        raise HTTPException(status_code=404, detail="No such customer.")
    return detail


def _account_for(customer_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The billing customer and its API-key account, or 404 / 409."""
    customer = billing_db.get_customer_by_id(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="No such customer.")

    user = get_user_by_email(customer["email"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "That customer has no API account yet — it is created when "
                "their plan first grants an allowance."
            ),
        )
    return customer, user


@router.post("/users/{customer_id}/credits")
async def grant_credits(customer_id: str, body: GrantCreditsRequest) -> dict[str, Any]:
    """
    Add bonus credits to an account — goodwill, an outage, a support call.

    They land in the same balance referral rewards use: permanent, spent only
    after the day's allowance runs out.
    """
    customer, user = _account_for(customer_id)
    grant = grant_bonus_credits(
        user["id"], body.credits, reason="admin_grant", note=body.note
    )
    logger.info(
        "Admin granted %s credits to customer_id=%s (%s).",
        body.credits, customer_id, body.note or "no note",
    )
    return {
        "granted": grant,
        "grants": list_credit_grants(user["id"]),
        "customer_email": customer["email"],
    }


@router.post("/users/{customer_id}/limit")
async def set_limit(customer_id: str, body: SetLimitRequest) -> dict[str, Any]:
    """
    Override an account's daily allowance, or clear the override.

    Note this is not sticky against billing: the next plan change writes the
    plan's allowance over it. It is for a temporary raise, not a permanent
    plan of its own.
    """
    customer, _ = _account_for(customer_id)
    updated = set_daily_credit_limit(
        customer["email"], body.daily_credit_limit, name=customer["name"]
    )
    logger.info(
        "Admin set daily_credit_limit=%s for customer_id=%s.",
        body.daily_credit_limit, customer_id,
    )
    return {"user": updated}


@router.post("/users/{customer_id}/active")
async def set_active(customer_id: str, body: SetActiveRequest) -> dict[str, Any]:
    """
    Enable or disable an account.

    Both halves are flipped together — the billing customer (which gates
    signing in) and the API account (which gates the keys). Disabling only one
    leaves someone who cannot log in but whose bot is still answering, which is
    the worst of both.
    """
    customer, user = _account_for(customer_id)
    queries.set_account_active(customer_id, user["id"], body.is_active)

    logger.warning(
        "Admin set is_active=%s for customer_id=%s (%s).",
        body.is_active, customer_id, customer["email"],
    )
    return {"customer_id": customer_id, "is_active": body.is_active}


@router.delete("/keys/{key_id}")
async def revoke_any_key(key_id: str) -> dict[str, Any]:
    """Revoke any API key on the platform. Takes effect on the next request."""
    if not revoke_key(key_id):
        raise HTTPException(status_code=404, detail="No such key.")
    logger.warning("Admin revoked api_key id=%s.", key_id)
    return {"status": "revoked", "key_id": key_id}


# ---------------------------------------------------------------------------
# Subscriptions, usage, audit, AI
# ---------------------------------------------------------------------------

@router.get("/subscriptions")
async def subscriptions(days: int = 90) -> dict[str, Any]:
    """Plan counts, MRR, revenue by month, churn and trial conversion."""
    return await asyncio.to_thread(queries.subscriptions, _clamp_days(days))


@router.get("/usage")
async def usage(days: int = 30) -> dict[str, Any]:
    """Credits and requests over time, by account and by endpoint."""
    return await asyncio.to_thread(queries.usage, _clamp_days(days))


@router.get("/audit")
async def audit(
    days: int = 7,
    email: str = "",
    endpoint: str = "",
    role: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """
    The API request trail, plus the inputs the injection detector flagged.

    Filter by ``role=owner`` to see only what the unmetered cross-tenant keys
    have been doing.
    """
    return await asyncio.to_thread(
        queries.audit,
        days=_clamp_days(days),
        email=email,
        endpoint=endpoint,
        role=role,
        limit=limit,
        offset=max(0, offset),
    )


@router.get("/ai-usage")
async def ai_usage(days: int = 30) -> dict[str, Any]:
    """AI usage split by the plan the account is on, plus retrieval quality."""
    return await asyncio.to_thread(queries.ai_usage, _clamp_days(days))


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@router.get("/templates")
async def templates() -> dict[str, Any]:
    """
    The catalogue with its live values, its code defaults, and adoption counts.

    Adoption is on the same payload because the number that matters when
    editing a template is how many live bots the edit will reach.
    """
    live = await asyncio.to_thread(catalogue.list_for_admin)
    adoption = await asyncio.to_thread(queries.template_adoption)
    for template in live:
        stats = adoption.get(template["id"], {})
        template["bots"] = stats.get("bots", 0)
        template["bots_ready"] = stats.get("ready", 0)
        template["bots_rewording_on"] = stats.get("rewording_on", 0)

    return {"templates": live, "editable_fields": list(catalogue.OVERRIDABLE_FIELDS)}


@router.patch("/templates/{template_id}")
async def update_template(template_id: str, body: TemplateUpdateRequest) -> dict[str, Any]:
    """
    Edit one template. Named fields are set; fields in ``reset`` go back to code.

    Edits reach every bot on the template immediately, including bots already
    running — which is the point, since this exists so a wrong decline message
    can be fixed without a deploy.
    """
    changes: dict[str, Any] = {
        field: value
        for field, value in body.model_dump(exclude={"enabled", "reset"}).items()
        if value is not None
    }
    for field in body.reset:
        if field not in catalogue.OVERRIDABLE_FIELDS:
            raise HTTPException(status_code=400, detail=f"{field} is not an editable field.")
        changes[field] = None

    try:
        updated = await asyncio.to_thread(
            catalogue.set_override, template_id, changes, enabled=body.enabled
        )
    except catalogue.TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("Admin updated template %s: %s", template_id, sorted(changes) or "flags only")
    return updated


@router.post("/templates/{template_id}/reset")
async def reset_template(template_id: str) -> dict[str, Any]:
    """Discard every edit to this template and restore the code default."""
    try:
        return await asyncio.to_thread(catalogue.reset_override, template_id)
    except catalogue.TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------

@router.get("/referrals")
async def referral_programme() -> dict[str, Any]:
    """Programme totals, the leaderboard, and the recent payout trail."""
    return await asyncio.to_thread(referrals.platform_stats)
