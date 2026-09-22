"""
Billing data: customers, sessions, subscriptions, invoices, referrals.

Lives in the same MongoDB database as the API-key collections so that granting
a paying customer their credit allowance is one round trip rather than a
cross-service dance. ``backend.shared.api_keys`` still owns ``users`` and
``api_keys``; this module links to them by email.

Uniqueness that used to be a ``UNIQUE`` column is a unique index here, and two
of them are load-bearing rather than tidy:

* ``customers.canonical_email`` — one account per *mailbox*, so ``you+1@`` and
  ``y.o.u@`` cannot open a second one. See ``backend.billing.identity``.
* ``referral_rewards`` — a signup bonus pays once per side, and one invoice
  pays out once however many times a provider redelivers its webhook.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING

from backend.shared import config
from backend.shared.mongo import (
    DuplicateKeyError,
    coll,
    document,
    documents,
    object_id,
    register_indexes,
    to_object_id,
)

from backend.billing.plans import Plan
from backend.billing.security import hash_session_token

logger = logging.getLogger(__name__)

SESSION_TTL_DAYS = 30

# Subscription statuses
STATUS_TRIALING = "trialing"
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_CANCELED = "canceled"   # still runs to period end
STATUS_EXPIRED = "expired"

ENTITLED_STATUSES = (STATUS_TRIALING, STATUS_ACTIVE, STATUS_CANCELED)

CUSTOMERS = "customers"
SESSIONS = "sessions"
SUBSCRIPTIONS = "subscriptions"
INVOICES = "invoices"
REFERRAL_CODES = "referral_codes"
REFERRAL_INVITES = "referral_invites"
REFERRALS = "referrals"
REFERRAL_REWARDS = "referral_rewards"


class DuplicateAccountError(ValueError):
    """This mailbox already has an account."""


# Stored in password_hash for an account that has no password. It cannot parse
# as a hash, so verify_password rejects it for every input — there is no string
# a user could type that authenticates against it.
NO_PASSWORD = "!google-oauth-no-password"

PROVIDER_PASSWORD = "password"
PROVIDER_GOOGLE = "google"
# Signed in through Firebase Authentication. The underlying method (Google or
# email+password) is recorded separately in ``firebase_provider``, because
# "how Firebase authenticated them" and "that Firebase authenticated them" are
# different questions and only the second one decides how we log them in.
PROVIDER_FIREBASE = "firebase"


register_indexes(CUSTOMERS, [
    ([("email", ASCENDING)], {"unique": True, "name": "uniq_email"}),
    # The real one-account-per-person rule. Partial so that a document written
    # before the field existed cannot collide with every other such document.
    ([("canonical_email", ASCENDING)], {
        "unique": True,
        "name": "uniq_canonical_email",
        "partialFilterExpression": {"canonical_email": {"$type": "string"}},
    }),
    ([("google_sub", ASCENDING)], {
        "unique": True,
        "name": "uniq_google_sub",
        "partialFilterExpression": {"google_sub": {"$type": "string"}},
    }),
    # Same shape and same reasoning as google_sub: one Firebase account maps
    # to at most one customer, and the partial filter keeps every pre-Firebase
    # document (where the field is absent) out of the uniqueness check.
    ([("firebase_uid", ASCENDING)], {
        "unique": True,
        "name": "uniq_firebase_uid",
        "partialFilterExpression": {"firebase_uid": {"$type": "string"}},
    }),
])
register_indexes(SESSIONS, [
    ([("token_hash", ASCENDING)], {"unique": True, "name": "uniq_token"}),
    ([("customer_id", ASCENDING)], {"name": "by_customer"}),
])
register_indexes(SUBSCRIPTIONS, [
    ([("customer_id", ASCENDING), ("_id", DESCENDING)], {"name": "by_customer"}),
    ([("status", ASCENDING), ("current_period_end", ASCENDING)], {"name": "by_status_end"}),
])
register_indexes(INVOICES, [
    ([("customer_id", ASCENDING), ("_id", DESCENDING)], {"name": "by_customer"}),
    ([("status", ASCENDING)], {"name": "by_status"}),
])
register_indexes(REFERRAL_CODES, [
    ([("code", ASCENDING)], {"unique": True, "name": "uniq_code"}),
    ([("customer_id", ASCENDING)], {"unique": True, "name": "uniq_customer"}),
])
register_indexes(REFERRAL_INVITES, [
    ([("referrer_customer_id", ASCENDING), ("email", ASCENDING)],
     {"unique": True, "name": "uniq_referrer_email"}),
    ([("email", ASCENDING), ("claimed_at", ASCENDING)], {"name": "by_email"}),
])
register_indexes(REFERRALS, [
    # One referrer per person, ever, and only at signup.
    ([("referred_customer_id", ASCENDING)], {"unique": True, "name": "uniq_referred"}),
    ([("referrer_customer_id", ASCENDING)], {"name": "by_referrer"}),
])
register_indexes(REFERRAL_REWARDS, [
    # Idempotency, enforced by the database rather than by remembering to
    # check. A signup bonus is paid once per side of a referral...
    ([("referral_id", ASCENDING), ("role", ASCENDING), ("kind", ASCENDING)], {
        "unique": True,
        "name": "uniq_signup_reward",
        "partialFilterExpression": {"kind": "signup"},
    }),
    # ...and one invoice triggers one payout, however many times the provider
    # decides to deliver its webhook.
    ([("referral_id", ASCENDING), ("role", ASCENDING), ("invoice_id", ASCENDING)], {
        "unique": True,
        "name": "uniq_invoice_reward",
        "partialFilterExpression": {"invoice_id": {"$type": "objectId"}},
    }),
    ([("customer_id", ASCENDING)], {"name": "by_customer"}),
])


def _now() -> datetime:
    return datetime.now(config.IST)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def init_billing_tables() -> None:
    """
    Kept as an entry point for startup; MongoDB needs no schema built.

    The indexes are declared above and applied by ``mongo.ensure_indexes()``.
    """
    return None


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def create_customer(
    email: str,
    name: str,
    password_hash: str,
    *,
    google_sub: str | None = None,
    firebase_uid: str | None = None,
    firebase_provider: str | None = None,
    auth_provider: str = PROVIDER_PASSWORD,
) -> dict[str, Any]:
    from backend.billing.identity import canonical_email

    address = email.lower().strip()
    doc = {
        "email": address,
        "canonical_email": canonical_email(address),
        "name": name,
        "password_hash": password_hash,
        "created_at": _iso(_now()),
        "is_active": 1,
        "google_sub": google_sub,
        "auth_provider": auth_provider,
    }

    # Written only when present, so the partial unique index on firebase_uid
    # ignores password-only accounts instead of treating a shared null as a
    # collision.
    if firebase_uid:
        doc["firebase_uid"] = firebase_uid
    if firebase_provider:
        doc["firebase_provider"] = firebase_provider

    try:
        result = coll(CUSTOMERS).insert_one(doc)
    except DuplicateKeyError as exc:
        # The unique index is the enforcement; the caller's check is only the
        # polite version of this message.
        raise DuplicateAccountError(
            "An account already exists for that email address."
        ) from exc

    return document({**doc, "_id": result.inserted_id})  # type: ignore[return-value]


def get_customer_by_google_sub(google_sub: str) -> dict[str, Any] | None:
    """
    Look up by Google's stable subject id.

    Sign-in now runs through Firebase, so nothing writes this field any more.
    It is still read, and must stay: accounts created by the old server-side
    OAuth flow are keyed on it, and it is the only thing that recognises one
    of those people when they arrive through Firebase Google instead. Without
    this lookup they collide on the mailbox and are refused entry to their
    own account. See ``_resolve_firebase_customer``.
    """
    return document(coll(CUSTOMERS).find_one({"google_sub": google_sub}))


def get_customer_by_firebase_uid(firebase_uid: str) -> dict[str, Any] | None:
    """
    Look up by Firebase's stable uid.

    Tried first on the sign-in path, because the uid is the identifier that
    survives a user changing their email address.
    """
    if not firebase_uid:
        return None
    return document(coll(CUSTOMERS).find_one({"firebase_uid": firebase_uid}))


def link_firebase_account(
    customer_id: Any, firebase_uid: str, provider: str | None = None
) -> dict[str, Any] | None:
    """
    Attach a Firebase identity to an existing account.

    Only ever called once the email behind that identity is confirmed — see
    ``firebase_auth.identity_from_claims``, where the unverified case is
    refused before it can reach here.

    The filter requires ``firebase_uid`` to be absent or null, so this can
    never re-point an account that is already linked to a *different*
    Firebase user. Two people, one mailbox claim, and the second silently
    taking over the first is exactly the failure this guards.

    ``auth_provider`` is deliberately left alone: an account that had a
    password keeps it, and keeps both routes in.
    """
    oid = object_id(customer_id)
    if oid is None:
        return None

    updates: dict[str, Any] = {"firebase_uid": firebase_uid}
    if provider:
        updates["firebase_provider"] = provider

    result = coll(CUSTOMERS).update_one(
        {
            "_id": oid,
            "$or": [{"firebase_uid": None}, {"firebase_uid": {"$exists": False}}],
        },
        {"$set": updates},
    )

    if result.matched_count == 0:
        # Already linked. Fine if it is the same uid (a re-run, a race between
        # two tabs); a refusal if it is someone else's.
        existing = get_customer_by_id(customer_id)
        if existing is not None and existing.get("firebase_uid") != firebase_uid:
            return None
        return existing

    return get_customer_by_id(customer_id)


def get_customer_for_mailbox(email: str) -> dict[str, Any] | None:
    """
    The account that owns this *mailbox*, whatever alias was typed.

    This is the lookup signup must use. :func:`get_customer_by_email` compares
    the literal string, which is right for signing in — people type the address
    they registered — and wrong for "is this already taken", where
    ``you+1@gmail.com`` has to find the account registered as ``you@gmail.com``.
    """
    from backend.billing.identity import canonical_email

    address = (email or "").strip().lower()
    return document(coll(CUSTOMERS).find_one({
        "$or": [{"canonical_email": canonical_email(address)}, {"email": address}]
    }))


def get_customer_by_email(email: str) -> dict[str, Any] | None:
    return document(coll(CUSTOMERS).find_one({"email": (email or "").lower().strip()}))


def get_customer_by_id(customer_id: Any) -> dict[str, Any] | None:
    oid = object_id(customer_id)
    return document(coll(CUSTOMERS).find_one({"_id": oid})) if oid else None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(customer_id: Any, raw_token: str) -> str:
    """Persist a session for *raw_token* and return its expiry ISO timestamp."""
    now = _now()
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    coll(SESSIONS).insert_one({
        "customer_id": to_object_id(customer_id),
        "token_hash": hash_session_token(raw_token),
        "created_at": _iso(now),
        "expires_at": _iso(expires),
        "revoked": 0,
    })
    return _iso(expires)


def get_session_customer(raw_token: str) -> dict[str, Any] | None:
    """Resolve a session token to its customer, or ``None`` if invalid/expired."""
    if not raw_token:
        return None

    session = coll(SESSIONS).find_one({
        "token_hash": hash_session_token(raw_token), "revoked": 0
    })
    if session is None:
        return None
    if datetime.fromisoformat(session["expires_at"]) <= _now():
        return None

    customer = coll(CUSTOMERS).find_one({"_id": session["customer_id"]})
    if customer is None or not customer.get("is_active"):
        return None

    resolved = document(customer)
    assert resolved is not None
    resolved["session_expires_at"] = session["expires_at"]
    return resolved


def purge_dead_sessions(keep_days: int = 7) -> int:
    """
    Delete sessions that expired or were revoked more than *keep_days* ago.

    Sessions are written on every sign-in and never removed otherwise, so the
    collection grows forever — and every document is a (hashed) credential
    nobody needs. A short grace period keeps recent ones around for debugging
    "why was I logged out".
    """
    cutoff = _iso(_now() - timedelta(days=keep_days))
    return coll(SESSIONS).delete_many({
        "$or": [
            {"expires_at": {"$lt": cutoff}},
            {"revoked": 1, "created_at": {"$lt": cutoff}},
        ]
    }).deleted_count


def revoke_session(raw_token: str) -> None:
    coll(SESSIONS).update_one(
        {"token_hash": hash_session_token(raw_token)}, {"$set": {"revoked": 1}}
    )


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def get_current_subscription(customer_id: Any) -> dict[str, Any] | None:
    """The customer's most recent subscription, whatever its status."""
    oid = object_id(customer_id)
    if oid is None:
        return None
    return document(
        coll(SUBSCRIPTIONS).find_one({"customer_id": oid}, sort=[("_id", DESCENDING)])
    )


def is_entitled(subscription: dict[str, Any] | None) -> bool:
    """
    True when this subscription currently grants access.

    ``canceled`` still counts until the paid period actually runs out — the
    customer paid for it.
    """
    if subscription is None:
        return False
    if subscription["status"] not in ENTITLED_STATUSES:
        return False
    return datetime.fromisoformat(subscription["current_period_end"]) > _now()


def start_subscription(
    customer_id: Any,
    plan: Plan,
    status: str,
    provider: str = "manual",
    provider_ref: str | None = None,
) -> dict[str, Any]:
    """Open a new subscription period for *plan* starting now."""
    now = _now()
    doc = {
        "customer_id": to_object_id(customer_id),
        "plan_id": plan.id,
        "status": status,
        "current_period_start": _iso(now),
        "current_period_end": _iso(now + timedelta(days=plan.interval_days)),
        "provider": provider,
        "provider_ref": provider_ref,
        "created_at": _iso(now),
        "updated_at": _iso(now),
    }
    result = coll(SUBSCRIPTIONS).insert_one(doc)
    return document({**doc, "_id": result.inserted_id})  # type: ignore[return-value]


def set_subscription_status(subscription_id: Any, status: str) -> None:
    oid = object_id(subscription_id)
    if oid is None:
        return
    coll(SUBSCRIPTIONS).update_one(
        {"_id": oid}, {"$set": {"status": status, "updated_at": _iso(_now())}}
    )


def has_used_trial(customer_id: Any) -> bool:
    oid = object_id(customer_id)
    if oid is None:
        return False
    return coll(SUBSCRIPTIONS).count_documents(
        {"customer_id": oid, "plan_id": "trial"}, limit=1
    ) > 0


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

def create_invoice(
    customer_id: Any,
    subscription_id: Any | None,
    plan: Plan,
    currency: str,
    status: str,
    provider: str = "manual",
    provider_ref: str | None = None,
) -> dict[str, Any]:
    now = _iso(_now())
    doc = {
        "customer_id": to_object_id(customer_id),
        "subscription_id": object_id(subscription_id),
        "plan_id": plan.id,
        "amount_cents": plan.price_cents,
        "currency": currency,
        "status": status,
        "provider": provider,
        "provider_ref": provider_ref,
        "issued_at": now,
        "paid_at": now if status == "paid" else None,
    }
    result = coll(INVOICES).insert_one(doc)
    return document({**doc, "_id": result.inserted_id})  # type: ignore[return-value]


def get_invoice(invoice_id: Any) -> dict[str, Any] | None:
    oid = object_id(invoice_id)
    return document(coll(INVOICES).find_one({"_id": oid})) if oid else None


def list_invoices(customer_id: Any) -> list[dict[str, Any]]:
    oid = object_id(customer_id)
    if oid is None:
        return []
    return documents(coll(INVOICES).find({"customer_id": oid}).sort("_id", DESCENDING))


def mark_open_invoices_paid(
    customer_id: Any, subscription_id: Any | None = None
) -> list[dict[str, Any]]:
    """
    Settle this customer's open invoices and hand back what was settled.

    Activation used to leave the invoice it created sitting at ``open`` forever,
    which made "how much have we actually billed?" unanswerable and left the
    referral payout with no payment to hang off. Returning the documents is what
    lets the caller reward a referral exactly once per invoice.
    """
    oid = object_id(customer_id)
    if oid is None:
        return []

    query: dict[str, Any] = {"customer_id": oid, "status": "open"}
    sub_oid = object_id(subscription_id)
    if sub_oid is not None:
        query["$or"] = [{"subscription_id": sub_oid}, {"subscription_id": None}]

    open_ids = [inv["_id"] for inv in coll(INVOICES).find(query, {"_id": 1})]
    if not open_ids:
        return []

    coll(INVOICES).update_many(
        {"_id": {"$in": open_ids}},
        {"$set": {"status": "paid", "paid_at": _iso(_now())}},
    )
    return documents(coll(INVOICES).find({"_id": {"$in": open_ids}}))


def mark_invoice_paid(invoice_id: Any, provider_ref: str | None = None) -> None:
    oid = object_id(invoice_id)
    if oid is None:
        return
    update: dict[str, Any] = {"status": "paid", "paid_at": _iso(_now())}
    if provider_ref is not None:
        update["provider_ref"] = provider_ref
    coll(INVOICES).update_one({"_id": oid}, {"$set": update})


# ---------------------------------------------------------------------------
# Expiry sweep
# ---------------------------------------------------------------------------

def expire_lapsed_subscriptions() -> list[dict[str, Any]]:
    """
    Flip any subscription whose period has ended to ``expired``.

    Returns the affected customers as ``{"id", "email", "name"}`` so the caller
    can withdraw whatever the plan was paying for. It hands them back rather
    than withdrawing directly because this module has no business knowing what
    a subscription *entitles* — that lives in ``backend.billing.entitlements``.
    """
    now = _iso(_now())
    query = {
        "status": {"$in": [STATUS_TRIALING, STATUS_ACTIVE, STATUS_CANCELED]},
        "current_period_end": {"$lte": now},
    }

    # Read the affected customers before the update: afterwards they are
    # indistinguishable from subscriptions that expired last month.
    customer_ids = coll(SUBSCRIPTIONS).distinct("customer_id", query)
    if not customer_ids:
        return []

    coll(SUBSCRIPTIONS).update_many(
        query, {"$set": {"status": STATUS_EXPIRED, "updated_at": now}}
    )

    return [
        {"id": str(c["_id"]), "email": c["email"], "name": c.get("name", "")}
        for c in coll(CUSTOMERS).find({"_id": {"$in": customer_ids}})
    ]
