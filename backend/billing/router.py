"""
Account, billing, and dashboard endpoints for the self-serve platform.

Auth is a session cookie (httpOnly, SameSite=Lax) rather than a token in
localStorage: a stored token is readable by any script that gets injected
into the page, and an httpOnly cookie is not. The SPA talks to this API
through a same-origin path (Vite proxies /api in development), so Lax is
sufficient and no CSRF-prone cross-site posting is required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections import defaultdict
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from backend.shared.config import OAUTH_STATE_TTL_SECONDS
from backend.shared.api_keys import (
    count_active_keys_for_email,
    generate_api_key,
    get_usage_stats,
    get_user_by_email,
    list_keys_for_email,
    revoke_key_for_email,
)

from backend.billing import db, entitlements, google_oauth, identity, referrals
from backend.billing.payments import get_provider, manual_activation_allowed
from backend.billing.plans import PLANS, get_plan, list_plans
from backend.billing.schemas import (
    CheckoutRequest,
    ReferralInviteRequest,
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
from backend.billing.security import (
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
# Signup throttling
# ---------------------------------------------------------------------------
# Canonical email stops one *mailbox* opening many accounts. It cannot stop one
# *person* with several real mailboxes, and the cheap version of that is a
# handful of signups from one machine in one sitting. This caps that.
#
# In-process and therefore per-worker, like the login throttle above — a blunt
# instrument that raises the cost of bulk signups without a shared store. Move
# both to Redis before running more than one worker.

_SIGNUPS_BY_IP: dict[str, list[float]] = defaultdict(list)
_SIGNUP_WINDOW_SECONDS = 24 * 60 * 60


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_signup_rate(request: Request) -> None:
    if identity.MAX_SIGNUPS_PER_IP_PER_DAY <= 0:
        return  # explicitly disabled

    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _SIGNUPS_BY_IP[ip] if now - t < _SIGNUP_WINDOW_SECONDS]
    _SIGNUPS_BY_IP[ip] = recent

    if len(recent) >= identity.MAX_SIGNUPS_PER_IP_PER_DAY:
        logger.warning("Signup rate limit hit from %s.", ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many accounts have been created from this network today. "
                "If you need another one, contact support."
            ),
        )


def _record_signup(request: Request) -> None:
    _SIGNUPS_BY_IP[_client_ip(request)].append(time.time())


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


def _subscription_view(customer_id: str) -> SubscriptionResponse:
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


# Grant/revoke live in backend.billing.entitlements — see that module for why.


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.post("/auth/signup", response_model=CustomerResponse, status_code=201)
async def signup(
    body: SignupRequest, request: Request, response: Response
) -> CustomerResponse:
    """
    Create an account and start the free trial.

    One account per *mailbox*, not per string: ``you+1@gmail.com`` and
    ``y.o.u@gmail.com`` are the address that already signed up, and are refused
    as such. See ``backend.billing.identity`` for why that is worth doing.
    """
    email = body.email.lower().strip()

    try:
        identity.screen_signup_email(email)
    except identity.EmailPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Deliberately the same message whichever alias they tried, and phrased so
    # it reads as "you already have one", not "we saw through your alias".
    if db.get_customer_for_mailbox(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    _check_signup_rate(request)

    pw_hash = await asyncio.to_thread(hash_password, body.password)
    try:
        customer = db.create_customer(email, body.name, pw_hash)
    except db.DuplicateAccountError as exc:
        # Two requests raced past the check above; the index caught it.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    _record_signup(request)

    # Everyone starts on the trial — no card, no checkout.
    trial = PLANS["trial"]
    db.start_subscription(customer["id"], trial, db.STATUS_TRIALING, provider="none")
    entitlements.grant(email, body.name, trial.daily_credits)

    # After the allowance is granted, so the bonus credits land on an account
    # that exists. A bad code is not an error: the signup still succeeds.
    referrals.attach(customer, body.referral_code)

    token = generate_session_token()
    db.create_session(customer["id"], token)
    _set_session_cookie(response, token)

    return _customer_public(customer)


@router.get("/auth/providers")
async def auth_providers() -> dict[str, bool]:
    """
    Which sign-in methods this server actually supports.

    Public, and deliberately so: the sign-in page needs it before anyone is
    authenticated. It leaks nothing — whether a Google button exists is
    visible from the button.
    """
    return {"password": True, "google": google_oauth.is_configured()}


# ---------------------------------------------------------------------------
# Google sign-in
# ---------------------------------------------------------------------------

def _start_trial(
    customer: dict[str, Any], name: str, referral_code: str = ""
) -> None:
    """Everyone starts on the trial, however they signed up."""
    trial = PLANS["trial"]
    db.start_subscription(customer["id"], trial, db.STATUS_TRIALING, provider="none")
    entitlements.grant(customer["email"], name, trial.daily_credits)
    referrals.attach(customer, referral_code)


REFERRAL_COOKIE = "nexora_ref"


@router.get("/auth/google")
async def google_start(ref: str = "") -> RedirectResponse:
    """
    Send the browser to Google's consent screen.

    The ``state`` goes out in a short-lived httpOnly cookie and is checked on
    the way back. Without that check, someone can complete a sign-in flow they
    started and leave a victim's browser holding a session for the attacker's
    account.
    """
    if not google_oauth.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured on this server.",
        )

    url, state = google_oauth.build_authorization_url()
    response = RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    response.set_cookie(
        key=google_oauth.STATE_COOKIE,
        value=state,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=OAUTH_STATE_TTL_SECONDS,
        path="/",
    )

    # The referral code has to survive a round trip through Google, and the
    # only thing that comes back is the state parameter. A short-lived cookie
    # is the simplest place to leave it; it is worthless to anyone who steals
    # it, being a public code the referrer hands out on purpose.
    code = referrals.normalise_code(ref)
    if code:
        response.set_cookie(
            key=REFERRAL_COOKIE,
            value=code,
            httponly=True,
            samesite="lax",
            secure=COOKIE_SECURE,
            max_age=OAUTH_STATE_TTL_SECONDS,
            path="/",
        )
    return response


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """
    Where Google sends the browser back.

    Ends in a redirect either way — this is a browser navigation, not an API
    call, so a failure has to land somewhere a person can read rather than
    returning JSON to an empty tab.
    """
    if not google_oauth.is_configured():
        return _oauth_failure("Google sign-in is not configured on this server.")

    # The user pressed cancel on Google's screen. Not an error worth a scary
    # message — just put them back where they started.
    if error:
        return _oauth_failure("Sign-in was cancelled.")

    expected_state = request.cookies.get(google_oauth.STATE_COOKIE)
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        return _oauth_failure("That sign-in link has expired. Please try again.")

    if not code:
        return _oauth_failure("Google did not return a sign-in code.")

    try:
        claims = google_oauth.exchange_code(code)
        google_identity = google_oauth.identity_from_claims(claims)
    except google_oauth.OAuthError as exc:
        return _oauth_failure(str(exc))

    customer = _resolve_google_customer(
        google_identity, request.cookies.get(REFERRAL_COOKIE, "")
    )
    if customer is None:
        return _oauth_failure(
            "That email already has an account. Sign in with your password instead."
        )

    if not customer["is_active"]:
        return _oauth_failure("That account has been deactivated.")

    token = generate_session_token()
    db.create_session(customer["id"], token)

    response = RedirectResponse(
        f"{PUBLIC_BASE_URL}/dashboard", status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(response, token)
    # The state and referral cookies have done their job; leaving them behind
    # is two more things sitting in the browser for no reason.
    response.delete_cookie(google_oauth.STATE_COOKIE, path="/")
    response.delete_cookie(REFERRAL_COOKIE, path="/")
    return response


def _resolve_google_customer(
    google_identity: dict[str, Any], referral_code: str = ""
) -> dict[str, Any] | None:
    """
    Find or create the account for a verified Google identity.

    Three cases, in this order:

    1. **Known ``sub``** — the same Google account as last time. Email may have
       changed since; the subject id is what does not.
    2. **Known email** — an existing account, linked now. Only reachable
       because ``identity_from_claims`` already refused unverified addresses:
       Google has confirmed this person controls that mailbox, so it is the
       same human. Their password keeps working.
    3. **Neither** — a new account, with no password and the trial started.
    """
    existing = db.get_customer_by_google_sub(google_identity["google_sub"])
    if existing is not None:
        return existing

    # Matched on the mailbox, so an account registered as "you@gmail.com" is
    # found even when Google reports "y.o.u@gmail.com".
    by_email = db.get_customer_for_mailbox(google_identity["email"])
    if by_email is not None:
        return db.link_google_account(by_email["id"], google_identity["google_sub"])

    try:
        customer = db.create_customer(
            google_identity["email"],
            google_identity["name"],
            db.NO_PASSWORD,
            google_sub=google_identity["google_sub"],
            auth_provider=db.PROVIDER_GOOGLE,
        )
    except db.DuplicateAccountError:
        # The mailbox is taken by an account this Google identity is not
        # linked to. Refusing is right: linking on an unverified match is how
        # one person ends up inside another's account.
        return None

    _start_trial(customer, google_identity["name"], referral_code)
    return customer


def _oauth_failure(message: str) -> RedirectResponse:
    """Send the browser back to sign-in with something readable in the URL."""
    return RedirectResponse(
        f"{PUBLIC_BASE_URL}/signin?error={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/auth/login", response_model=CustomerResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> CustomerResponse:
    """Exchange email + password for a session cookie."""
    email = body.email.lower().strip()
    throttle_key = _throttle_key(email, request)
    _check_throttle(throttle_key)

    customer = db.get_customer_by_email(email)

    # Same error and roughly the same work either way, so this doesn't become
    # an oracle for which emails are registered.
    pw_ok = await asyncio.to_thread(
        verify_password, body.password, customer["password_hash"]
    ) if customer is not None else False
    if customer is None or not pw_ok:
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

    # Checkout opened an invoice; activation is what settles it. Leaving it
    # open made "how much have we billed?" unanswerable and left the referral
    # payout with no payment to key off.
    paid_invoices = db.mark_open_invoices_paid(customer["id"], sub["id"])
    referrals.reward_payment(customer["id"], paid_invoices)

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

    When you do wire one up, a successful payment must do the same three
    things ``confirm_manual`` does — activate the subscription, grant the
    entitlement, then::

        paid = db.mark_open_invoices_paid(customer_id, subscription_id)
        referrals.reward_payment(customer_id, paid)

    Both are safe to call twice: the reward is keyed on the invoice, and
    providers deliver webhooks more than once as a matter of course.
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
    key_id: str,
    customer: dict = Depends(current_customer),
) -> dict[str, Any]:
    """Revoke one of *your own* keys. Takes effect on the next request."""
    if not revoke_key_for_email(key_id, customer["email"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such key on this account.",
        )
    return {"status": "revoked", "key_id": key_id}


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------

@router.get("/referrals")
async def my_referrals(customer: dict = Depends(current_customer)) -> dict[str, Any]:
    """
    This account's referral code, who used it, and what it has earned.

    The referred customers' addresses come back masked — the referrer knows
    who they invited, but this screen gets shared and screenshotted.
    """
    summary = referrals.summary_for(customer["id"])
    summary["link"] = f"{PUBLIC_BASE_URL}/signup?ref={summary['code']}"
    return summary


@router.post("/referrals/invites", status_code=201)
async def invite_by_email(
    body: ReferralInviteRequest,
    customer: dict = Depends(current_customer),
) -> dict[str, Any]:
    """
    Record an invite so a friend who signs up with that address is credited
    even if they never click a link carrying the code.

    Inviting yourself is refused here rather than at signup, because at signup
    it would be a silent no-op and the person would never learn why they were
    not credited.
    """
    address = body.email.lower().strip()
    if address == customer["email"].lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot refer yourself.",
        )
    if db.get_customer_by_email(address) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email already has an account.",
        )

    referrals.invite(customer["id"], address)
    return referrals.summary_for(customer["id"])
