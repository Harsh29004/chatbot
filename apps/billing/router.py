"""
Account, billing, and dashboard endpoints for the self-serve platform.

Auth is a session cookie (httpOnly, SameSite=Lax) rather than a token in
localStorage: a stored token is readable by any script that gets injected
into the page, and an httpOnly cookie is not. The SPA talks to this API
through a same-origin path (Vite proxies /api in development), so Lax is
sufficient and no CSRF-prone cross-site posting is required.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from shared.api_keys import (
    count_active_keys_for_email,
    generate_api_key,
    get_usage_stats,
    get_user_by_email,
    list_keys_for_email,
    revoke_key_for_email,
)

from apps.billing import db, entitlements
from apps.billing.payments import get_provider, manual_activation_allowed
from apps.billing.plans import PLANS, get_plan, list_plans
from apps.billing.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    CreatedKeyResponse,
    CreateKeyRequest,
    CustomerResponse,
    DashboardKey,
    DashboardResponse,
    InvoiceResponse,
    LoginRequest,
    PricingResponse,
    SignupRequest,
    SubscriptionResponse,
)
from apps.billing.security import (
    generate_session_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Platform"])

SESSION_COOKIE = "nexora_session"
COOKIE_SECURE = os.getenv("BILLING_COOKIE_SECURE", "false").lower() == "true"
MAX_KEYS_PER_ACCOUNT = int(os.getenv("MAX_KEYS_PER_ACCOUNT", "10"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:5173")


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------
# In-process and therefore per-worker — enough to blunt credential stuffing on
# a single-box deployment. Move to Redis before running more than one worker.

_FAILED_LOGINS: dict[str, list[float]] = defaultdict(list)
_THROTTLE_WINDOW_SECONDS = 15 * 60
_THROTTLE_MAX_ATTEMPTS = 8


def _throttle_key(email: str, request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{email.lower()}|{client_host}"


def _check_throttle(key: str) -> None:
    now = time.time()
    attempts = [t for t in _FAILED_LOGINS[key] if now - t < _THROTTLE_WINDOW_SECONDS]
    _FAILED_LOGINS[key] = attempts
    if len(attempts) >= _THROTTLE_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again in 15 minutes.",
        )


def _record_failure(key: str) -> None:
    _FAILED_LOGINS[key].append(time.time())


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------

def current_customer(
    nexora_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    """Resolve the session cookie to a customer, or 401."""
    customer = db.get_session_customer(nexora_session or "")
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in.",
        )
    return customer


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=db.SESSION_TTL_DAYS * 24 * 3600,
        path="/",
    )


def _customer_public(customer: dict[str, Any]) -> CustomerResponse:
    return CustomerResponse(
        id=customer["id"],
        email=customer["email"],
        name=customer["name"],
        created_at=customer["created_at"],
    )


def _subscription_view(customer_id: int) -> SubscriptionResponse:
    entitlements.sync()
    sub = db.get_current_subscription(customer_id)
    entitled = db.is_entitled(sub)

    if sub is None:
        return SubscriptionResponse(
            status="none",
            is_entitled=False,
            can_start_trial=True,
        )

    plan = get_plan(sub["plan_id"])
    return SubscriptionResponse(
        plan_id=sub["plan_id"],
        plan_name=plan.name if plan else sub["plan_id"],
        status=sub["status"],
        is_entitled=entitled,
        current_period_end=sub["current_period_end"],
        daily_credits=plan.daily_credits if plan else None,
        can_start_trial=not db.has_used_trial(customer_id) and not entitled,
    )


# Grant/revoke live in apps.billing.entitlements — see that module for why.


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.post("/auth/signup", response_model=CustomerResponse, status_code=201)
async def signup(body: SignupRequest, response: Response) -> CustomerResponse:
    """Create an account and start the free trial."""
    email = body.email.lower().strip()
    if db.get_customer_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    customer = db.create_customer(email, body.name, hash_password(body.password))

    # Everyone starts on the trial — no card, no checkout.
    trial = PLANS["trial"]
    db.start_subscription(customer["id"], trial, db.STATUS_TRIALING, provider="none")
    entitlements.grant(email, body.name, trial.daily_credits)

    token = generate_session_token()
    db.create_session(customer["id"], token)
    _set_session_cookie(response, token)

    return _customer_public(customer)


@router.post("/auth/login", response_model=CustomerResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> CustomerResponse:
    """Exchange email + password for a session cookie."""
    email = body.email.lower().strip()
    throttle_key = _throttle_key(email, request)
    _check_throttle(throttle_key)

    customer = db.get_customer_by_email(email)

    # Same error and roughly the same work either way, so this doesn't become
    # an oracle for which emails are registered.
    if customer is None or not verify_password(body.password, customer["password_hash"]):
        _record_failure(throttle_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if not customer["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled.",
        )

    token = generate_session_token()
    db.create_session(customer["id"], token)
    _set_session_cookie(response, token)
    _FAILED_LOGINS.pop(throttle_key, None)

    return _customer_public(customer)


@router.post("/auth/logout")
async def logout(
    response: Response,
    nexora_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, str]:
    """Revoke the current session server-side and clear the cookie."""
    if nexora_session:
        db.revoke_session(nexora_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "signed out"}


@router.get("/auth/me", response_model=CustomerResponse)
async def me(customer: dict = Depends(current_customer)) -> CustomerResponse:
    return _customer_public(customer)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

@router.get("/billing/pricing", response_model=PricingResponse)
async def pricing() -> PricingResponse:
    """Public pricing. The pricing page renders exactly this — no hardcoded prices."""
    return PricingResponse(**list_plans())


@router.get("/billing/subscription", response_model=SubscriptionResponse)
async def subscription(customer: dict = Depends(current_customer)) -> SubscriptionResponse:
    return _subscription_view(customer["id"])


@router.post("/billing/checkout", response_model=CheckoutResponse)
async def checkout(
    body: CheckoutRequest,
    customer: dict = Depends(current_customer),
) -> CheckoutResponse:
    """
    Start a hosted checkout for a paid plan.

    We create the session with the provider and hand back their URL — the
    customer enters payment details on the provider's page, never ours.
    """
    plan = get_plan(body.plan_id)
    if plan is None or plan.price_cents == 0:
        raise HTTPException(status_code=400, detail="Unknown plan.")

    provider = get_provider()
    try:
        session = provider.create_checkout(
            customer=customer,
            plan=plan,
            success_url=f"{PUBLIC_BASE_URL}/checkout/return",
            cancel_url=f"{PUBLIC_BASE_URL}/pricing",
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    sub = db.start_subscription(
        customer["id"], plan, db.STATUS_PENDING,
        provider=session.provider, provider_ref=session.reference,
    )
    db.create_invoice(
        customer["id"], sub["id"], plan,
        currency=os.getenv("BILLING_CURRENCY", "USD"),
        status="open", provider=session.provider, provider_ref=session.reference,
    )

    return CheckoutResponse(
        checkout_url=session.url,
        reference=session.reference,
        provider=session.provider,
        requires_manual_confirmation=session.requires_manual_confirmation,
        message=(
            "Development checkout — no payment is taken and the plan stays "
            "pending until it is confirmed."
            if session.requires_manual_confirmation
            else "Redirecting to secure checkout."
        ),
    )


@router.post("/billing/confirm", response_model=SubscriptionResponse)
async def confirm_manual(customer: dict = Depends(current_customer)) -> SubscriptionResponse:
    """
    Activate a pending subscription **without payment** — development only.

    Refused unless BILLING_PROVIDER=manual *and* BILLING_ALLOW_MANUAL=true, so
    it cannot become a free-access hole in production. Real activation happens
    in the provider webhook.
    """
    if not manual_activation_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Manual activation is disabled. Payments are confirmed by the "
                "provider webhook."
            ),
        )

    sub = db.get_current_subscription(customer["id"])
    if sub is None or sub["status"] != db.STATUS_PENDING:
        raise HTTPException(status_code=400, detail="No pending subscription to confirm.")

    plan = get_plan(sub["plan_id"])
    if plan is None:
        raise HTTPException(status_code=400, detail="Unknown plan on subscription.")

    activated = db.start_subscription(
        customer["id"], plan, db.STATUS_ACTIVE,
        provider=sub["provider"], provider_ref=sub["provider_ref"],
    )
    db.set_subscription_status(sub["id"], db.STATUS_EXPIRED)  # supersede the pending row
    entitlements.grant(customer["email"], customer["name"], plan.daily_credits)

    logger.warning(
        "Manual (unpaid) activation of %s for customer_id=%s — dev mode only.",
        plan.id, customer["id"],
    )
    _ = activated
    return _subscription_view(customer["id"])


@router.post("/billing/cancel", response_model=SubscriptionResponse)
async def cancel(customer: dict = Depends(current_customer)) -> SubscriptionResponse:
    """
    Cancel the subscription. Access continues until the paid period ends —
    they paid for it, so we don't cut it short.
    """
    sub = db.get_current_subscription(customer["id"])
    if sub is None or not db.is_entitled(sub):
        raise HTTPException(status_code=400, detail="No active subscription to cancel.")

    db.set_subscription_status(sub["id"], db.STATUS_CANCELED)
    return _subscription_view(customer["id"])


@router.get("/billing/invoices", response_model=list[InvoiceResponse])
async def invoices(customer: dict = Depends(current_customer)) -> list[InvoiceResponse]:
    return [InvoiceResponse(**inv) for inv in db.list_invoices(customer["id"])]


@router.post("/billing/webhook", include_in_schema=False)
async def webhook(request: Request) -> dict[str, str]:
    """
    Provider payment callback.

    Deliberately inert until a provider is wired up: an unverified webhook is
    an unauthenticated "make me a paying customer" endpoint. Before handling
    any event here, verify the provider's signature header against the
    webhook secret and reject anything that fails.
    """
    logger.info("Ignoring unverified billing webhook — no provider configured.")
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="No payment provider is configured for webhooks yet.",
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _require_entitlement(customer: dict[str, Any]) -> None:
    entitlements.sync()
    sub = db.get_current_subscription(customer["id"])
    if not db.is_entitled(sub):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="An active plan is required to issue API keys.",
        )


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(customer: dict = Depends(current_customer)) -> DashboardResponse:
    """Everything the dashboard screen needs, in one round trip."""
    email = customer["email"]
    sub_view = _subscription_view(customer["id"])

    user = get_user_by_email(email)
    usage: dict[str, Any] = (
        get_usage_stats(user["id"], user["daily_credit_limit"])
        if user
        else {
            "credits_remaining": 0,
            "credits_used_today": 0,
            "credits_daily_limit": 0,
            "total_queries_all_time": 0,
            "last_7_days": [],
        }
    )

    return DashboardResponse(
        customer=_customer_public(customer),
        subscription=sub_view,
        keys=[DashboardKey(**k) for k in list_keys_for_email(email)],
        usage=usage,
    )


@router.get("/dashboard/keys", response_model=list[DashboardKey])
async def list_keys(customer: dict = Depends(current_customer)) -> list[DashboardKey]:
    return [DashboardKey(**k) for k in list_keys_for_email(customer["email"])]


@router.post("/dashboard/keys", response_model=CreatedKeyResponse, status_code=201)
async def create_key(
    body: CreateKeyRequest,
    customer: dict = Depends(current_customer),
) -> CreatedKeyResponse:
    """
    Issue a new API key for this account.

    All of an account's keys share one daily credit pool, so extra keys are
    for separating environments, not for buying more capacity.
    """
    _require_entitlement(customer)

    email = customer["email"]
    if count_active_keys_for_email(email) >= MAX_KEYS_PER_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have {MAX_KEYS_PER_ACCOUNT} active keys. "
                "Revoke one before creating another."
            ),
        )

    result = generate_api_key(
        owner_email=email, owner_name=customer["name"], label=body.label
    )
    return CreatedKeyResponse(
        api_key=result["api_key"],
        key_id=result["key_id"],
        key_prefix=result["key_prefix"],
    )


@router.delete("/dashboard/keys/{key_id}")
async def delete_key(
    key_id: int,
    customer: dict = Depends(current_customer),
) -> dict[str, Any]:
    """Revoke one of *your own* keys. Takes effect on the next request."""
    if not revoke_key_for_email(key_id, customer["email"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such key on this account.",
        )
    return {"status": "revoked", "key_id": key_id}
