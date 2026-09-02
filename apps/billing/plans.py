"""
Plan catalogue and pricing maths.

This module is the single source of truth for what we charge. The frontend
renders whatever ``list_plans()`` returns, so prices are never hardcoded in
two places — change them here and the pricing page follows.

Money is handled in integer cents everywhere. Never floats: 0.1 + 0.2 is not
0.3, and that lands in an invoice eventually.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

PlanId = Literal["monthly", "yearly", "trial"]

CURRENCY = os.getenv("BILLING_CURRENCY", "USD")

# Daily credit allowance granted to an account while its subscription is live.
PAID_DAILY_CREDITS: int = int(os.getenv("PAID_DAILY_CREDITS", "5000"))
TRIAL_DAILY_CREDITS: int = int(os.getenv("TRIAL_DAILY_CREDITS", "250"))
TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "14"))


@dataclass(frozen=True)
class Plan:
    id: PlanId
    name: str
    price_cents: int
    interval: Literal["month", "year", "trial"]
    interval_days: int
    daily_credits: int
    tagline: str


PLANS: dict[str, Plan] = {
    "trial": Plan(
        id="trial",
        name="Trial",
        price_cents=0,
        interval="trial",
        interval_days=TRIAL_DAYS,
        daily_credits=TRIAL_DAILY_CREDITS,
        tagline=f"{TRIAL_DAYS} days, no card needed",
    ),
    "monthly": Plan(
        id="monthly",
        name="Monthly",
        price_cents=1500,  # $15.00
        interval="month",
        interval_days=30,
        daily_credits=PAID_DAILY_CREDITS,
        tagline="Cancel anytime",
    ),
    "yearly": Plan(
        id="yearly",
        name="Yearly",
        price_cents=14000,  # $140.00
        interval="year",
        interval_days=365,
        daily_credits=PAID_DAILY_CREDITS,
        tagline="Two months and change, free",
    ),
}

BILLABLE_PLAN_IDS = ("monthly", "yearly")


def get_plan(plan_id: str) -> Plan | None:
    return PLANS.get(plan_id)


# ---------------------------------------------------------------------------
# Pricing maths — derived, never hand-typed
# ---------------------------------------------------------------------------

def yearly_savings_cents() -> int:
    """Cents saved per year by paying yearly instead of monthly."""
    twelve_months = PLANS["monthly"].price_cents * 12
    return twelve_months - PLANS["yearly"].price_cents


def yearly_discount_percent() -> int:
    """Whole-percent discount of the yearly plan vs. 12x monthly (rounded down).

    Rounded down so the number we advertise is never larger than the discount
    a customer actually receives.
    """
    twelve_months = PLANS["monthly"].price_cents * 12
    if twelve_months == 0:
        return 0
    return (yearly_savings_cents() * 100) // twelve_months


def yearly_effective_monthly_cents() -> int:
    """What the yearly plan works out to per month, rounded to the nearest cent."""
    return round(PLANS["yearly"].price_cents / 12)


def format_cents(cents: int) -> str:
    """Render cents as a plain price string: 1500 -> '15', 1167 -> '11.67'."""
    if cents % 100 == 0:
        return str(cents // 100)
    return f"{cents / 100:.2f}"


def plan_public_dict(plan: Plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "price_cents": plan.price_cents,
        "price_display": format_cents(plan.price_cents),
        "currency": CURRENCY,
        "interval": plan.interval,
        "daily_credits": plan.daily_credits,
        "tagline": plan.tagline,
    }


def list_plans() -> dict[str, Any]:
    """The full pricing payload the frontend renders."""
    return {
        "currency": CURRENCY,
        "plans": [plan_public_dict(PLANS[p]) for p in ("trial", "monthly", "yearly")],
        "yearly_savings_cents": yearly_savings_cents(),
        "yearly_savings_display": format_cents(yearly_savings_cents()),
        "yearly_discount_percent": yearly_discount_percent(),
        "yearly_effective_monthly_display": format_cents(yearly_effective_monthly_cents()),
        "trial_days": TRIAL_DAYS,
    }
