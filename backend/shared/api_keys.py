"""
API key management and the credit system.

Keys are stored as SHA-256 hashes (like passwords) — if the database leaks,
raw keys cannot be recovered. Credits reset at midnight IST daily.

Credit costs are variable based on query length (see config.CREDIT_COST_TIERS).

Accounts vs. keys
------------------
Credits are pooled per **user** (identified by ``owner_email``), not per key.
A user can hold multiple API keys (e.g. one per app/environment) but they all
draw from the same daily credit pool — creating extra keys does not grant
extra credits.

Two pools, spent in this order
------------------------------
1. the **daily allowance** (``daily_credit_limit``), which resets at midnight IST
2. the **bonus balance** (``credit_grants``), which does not reset and does not
   expire — referral rewards and manual admin grants land here.

Allowance first is deliberate: it is the pool that disappears overnight, so
spending the permanent one ahead of it would burn earned credits for nothing.

Two roles exist:
- ``user``   — normal customer account, subject to ``daily_credit_limit``.
- ``owner``  — unlimited, no credit checks or deductions at all. Minted only
  via ``scripts/create_owner_key.py`` (never through the public API), for use
  by the product owners themselves.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from backend.shared import config
from backend.shared.mongo import (
    atomic,
    coll,
    document,
    documents,
    object_id,
    register_indexes,
    to_object_id,
)

KEY_PREFIX = "nxk_"  # Nexora AI Key
OWNER_KEY_PREFIX = "nxo_"  # Nexora AI Owner key — visually distinct in logs

ROLE_USER = "user"
ROLE_OWNER = "owner"

USERS = "users"
API_KEYS = "api_keys"
DAILY_USAGE = "daily_usage"
REQUEST_LOG = "request_log"
CREDIT_GRANTS = "credit_grants"

register_indexes(USERS, [
    ([("email", ASCENDING)], {"unique": True, "name": "uniq_email"}),
])
register_indexes(API_KEYS, [
    # The hash is what a request is validated against, on every single call.
    ([("key_hash", ASCENDING)], {"unique": True, "name": "uniq_key_hash"}),
    ([("user_id", ASCENDING)], {"name": "by_user"}),
])
register_indexes(DAILY_USAGE, [
    # One counter per account per day. Unique because the spend path upserts
    # into it concurrently, and two rows for one day would silently double an
    # account's allowance.
    ([("user_id", ASCENDING), ("usage_date", ASCENDING)],
     {"unique": True, "name": "uniq_user_day"}),
])
register_indexes(REQUEST_LOG, [
    ([("user_id", ASCENDING), ("timestamp", DESCENDING)], {"name": "by_user_time"}),
    ([("timestamp", DESCENDING)], {"name": "by_time"}),
])
register_indexes(CREDIT_GRANTS, [
    ([("user_id", ASCENDING), ("_id", ASCENDING)], {"name": "by_user"}),
])


def init_api_key_tables() -> None:
    """
    Kept as an entry point for startup; MongoDB needs no schema built.

    The indexes these collections depend on are declared above and applied by
    ``mongo.ensure_indexes()``.
    """
    return None


# ---------------------------------------------------------------------------
# Key hashing
# ---------------------------------------------------------------------------

def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _now() -> str:
    return datetime.now(config.IST).isoformat()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _get_or_create_user(
    email: str, name: str = "", role: str = ROLE_USER, daily_credit_limit: int | None = None
) -> dict[str, Any]:
    """
    Look up a user by email, creating them if they don't exist.

    Repeat calls with the same email reuse the same account (and therefore the
    same credit pool) instead of creating a new one — this is what keeps
    credits scoped per-account rather than per-key.

    Written as one upsert rather than a read-then-insert so that two requests
    arriving together cannot both decide the account is missing. The unique
    index on ``email`` is what makes that guarantee real.
    """
    address = email.lower().strip()
    doc = coll(USERS).find_one_and_update(
        {"email": address},
        {
            "$setOnInsert": {
                "email": address,
                "name": name,
                "role": role,
                "daily_credit_limit": daily_credit_limit,
                "created_at": _now(),
                "is_active": 1,
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return document(doc)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def _issue_key(user: dict[str, Any], raw_key: str, label: str) -> dict[str, Any]:
    result = coll(API_KEYS).insert_one({
        "user_id": to_object_id(user["id"]),
        "key_hash": _hash_key(raw_key),
        "key_prefix": raw_key[:12],
        "label": label,
        "created_at": _now(),
        "is_active": 1,
    })
    return {
        "api_key": raw_key,
        "key_id": str(result.inserted_id),
        "key_prefix": raw_key[:12],
        "owner_email": user["email"],
        "owner_name": user["name"],
    }


def generate_api_key(owner_email: str, owner_name: str = "", label: str = "") -> dict[str, Any]:
    """
    Generate a new API key for a customer account.

    If ``owner_email`` already has an account, the new key is attached to that
    same account and shares its existing credit pool. Returns a dict with
    ``api_key`` (raw — show once!), ``key_id``, ``key_prefix``, ``owner_email``,
    ``owner_name``.
    """
    user = _get_or_create_user(owner_email, owner_name, role=ROLE_USER)
    return _issue_key(user, KEY_PREFIX + secrets.token_hex(24), label)


def create_owner_key(owner_email: str, owner_name: str = "") -> dict[str, Any]:
    """
    Mint an unlimited **owner** key.

    Deliberately NOT exposed via any HTTP endpoint — call this only from
    ``scripts/create_owner_key.py`` run locally by a project owner. Owner keys
    skip credit checks and deductions entirely (see ``backend.shared.auth``).
    """
    user = _get_or_create_user(
        owner_email, owner_name, role=ROLE_OWNER, daily_credit_limit=None
    )
    # An account that existed as a customer is promoted rather than duplicated:
    # the email is the identity, and two accounts for one person would split
    # their credit pool in half.
    coll(USERS).update_one(
        {"_id": to_object_id(user["id"])}, {"$set": {"role": ROLE_OWNER}}
    )

    issued = _issue_key(user, OWNER_KEY_PREFIX + secrets.token_hex(24), "owner key")
    issued["role"] = ROLE_OWNER
    return issued


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

def validate_api_key(raw_key: str) -> dict[str, Any] | None:
    """
    Validate a raw API key.

    Returns a merged key+user record if the key and its owning account are both
    active, else ``None``. The returned dict includes ``role`` and
    ``daily_credit_limit`` so callers can branch on owner vs. normal keys.
    """
    if not raw_key or not (raw_key.startswith(KEY_PREFIX) or raw_key.startswith(OWNER_KEY_PREFIX)):
        return None

    key = coll(API_KEYS).find_one({"key_hash": _hash_key(raw_key)})
    if key is None or not key.get("is_active"):
        return None

    user = coll(USERS).find_one({"_id": key["user_id"]})
    if user is None or not user.get("is_active"):
        return None

    # The shape the auth dependency and every caller downstream expects. Kept
    # flat and explicit rather than nesting the user, because this record is
    # read on the hot path of every single API request.
    return {
        "id": str(key["_id"]),
        "key_prefix": key["key_prefix"],
        "user_id": str(user["_id"]),
        "key_is_active": key.get("is_active", 0),
        "owner_email": user["email"],
        "owner_name": user.get("name", ""),
        "role": user.get("role", ROLE_USER),
        "daily_credit_limit": user.get("daily_credit_limit"),
        "user_is_active": user.get("is_active", 0),
    }


# ---------------------------------------------------------------------------
# Credit management (pooled per user, not per key)
# ---------------------------------------------------------------------------

def _today_ist() -> str:
    """Return today's date string in IST (YYYY-MM-DD)."""
    return datetime.now(config.IST).strftime("%Y-%m-%d")


def _effective_limit(daily_credit_limit: int | None) -> int:
    """Resolve a user's configured limit, falling back to the global default."""
    return config.DAILY_CREDIT_LIMIT if daily_credit_limit is None else daily_credit_limit


def get_credits_used_today(user_id: Any) -> int:
    """How many credits this account has used today (IST), across all its keys."""
    doc = coll(DAILY_USAGE).find_one(
        {"user_id": object_id(user_id), "usage_date": _today_ist()}
    )
    return int(doc["credits_used"]) if doc else 0


def get_daily_credits_remaining(user_id: Any, daily_credit_limit: int | None = None) -> int:
    """What is left of *today's* allowance, ignoring any bonus balance."""
    used = get_credits_used_today(user_id)
    return max(0, _effective_limit(daily_credit_limit) - used)


def get_bonus_balance(user_id: Any) -> int:
    """
    Unspent bonus credits — referral rewards and manual grants.

    Bonus credits are a *balance*, not an allowance: they do not reset at
    midnight and they do not expire. Rewarding someone with credits that vanish
    at midnight IST would be rewarding them with almost nothing if they happened
    to earn them at 11pm.
    """
    result = list(coll(CREDIT_GRANTS).aggregate([
        {"$match": {"user_id": object_id(user_id)}},
        {"$group": {"_id": None, "total": {"$sum": "$remaining"}}},
    ]))
    return int(result[0]["total"]) if result else 0


def get_credits_remaining(user_id: Any, daily_credit_limit: int | None = None) -> int:
    """
    Everything this account can still spend right now: today's allowance plus
    whatever bonus balance it is carrying.

    This is the number the credit check and the ``X-Credits-Remaining`` header
    use, because it is the honest answer to "can this request go through?".
    """
    return get_daily_credits_remaining(user_id, daily_credit_limit) + get_bonus_balance(user_id)


def grant_bonus_credits(
    user_id: Any, amount: int, reason: str, note: str = ""
) -> dict[str, Any]:
    """
    Add *amount* bonus credits to an account and say why.

    Grants are documents rather than a single balance field so that "where did
    these credits come from?" has an answer — which referral, which admin, which
    day. Spending draws them down oldest-first.
    """
    if amount <= 0:
        raise ValueError("A credit grant must be positive.")

    grant = {
        "user_id": to_object_id(user_id),
        "amount": amount,
        "remaining": amount,
        "reason": reason,
        "note": note,
        "created_at": _now(),
    }
    result = coll(CREDIT_GRANTS).insert_one(grant)
    return document({**grant, "_id": result.inserted_id})  # type: ignore[return-value]


def list_credit_grants(user_id: Any, limit: int = 50) -> list[dict[str, Any]]:
    """This account's grant history, newest first."""
    return documents(
        coll(CREDIT_GRANTS)
        .find({"user_id": object_id(user_id)})
        .sort("_id", DESCENDING)
        .limit(limit)
    )


def _spend_bonus(user_id: Any, amount: int, session: Any = None) -> int:
    """
    Draw *amount* down from this account's grants, oldest first.

    Oldest-first so a grant that came with a story ("your friend subscribed") is
    the one that gets used, rather than sitting behind a newer one forever.
    Returns how much was actually taken, which is less than *amount* only when
    the balance ran out.
    """
    taken = 0
    oid = object_id(user_id)
    grants = coll(CREDIT_GRANTS).find(
        {"user_id": oid, "remaining": {"$gt": 0}}, session=session
    ).sort("_id", ASCENDING)

    for grant in grants:
        if taken >= amount:
            break
        take = min(int(grant["remaining"]), amount - taken)
        coll(CREDIT_GRANTS).update_one(
            {"_id": grant["_id"]}, {"$inc": {"remaining": -take}}, session=session
        )
        taken += take
    return taken


def consume_credits(
    user_id: Any,
    api_key_id: Any,
    cost: int,
    endpoint: str,
    message_len: int,
    daily_credit_limit: int | None = None,
) -> int:
    """
    Consume *cost* credits against *user_id*'s shared pool (charged regardless
    of which of the user's keys made the request).

    The daily allowance is spent **first** and the bonus balance only covers
    what is left over. That ordering matters: allowance expires at midnight and
    bonus credits do not, so spending bonus first would quietly burn the balance
    someone earned while their free allowance went unused.

    The deduction and its audit row go in one transaction where the deployment
    supports it, so a request can never be charged without being logged.

    Returns the total remaining credits (allowance + bonus) after the charge.
    """
    oid = to_object_id(user_id)
    from_daily = min(cost, get_daily_credits_remaining(user_id, daily_credit_limit))
    from_bonus = cost - from_daily

    with atomic() as session:
        if from_daily:
            coll(DAILY_USAGE).update_one(
                {"user_id": oid, "usage_date": _today_ist()},
                {"$inc": {"credits_used": from_daily}},
                upsert=True,
                session=session,
            )
        if from_bonus:
            _spend_bonus(oid, from_bonus, session=session)

        coll(REQUEST_LOG).insert_one({
            "api_key_id": object_id(api_key_id),
            "user_id": oid,
            "endpoint": endpoint,
            "message_len": message_len,
            "credit_cost": cost,
            "timestamp": _now(),
        }, session=session)

    return get_credits_remaining(user_id, daily_credit_limit)


def record_request(
    user_id: Any, api_key_id: Any, endpoint: str, message_len: int, cost: int = 0
) -> None:
    """
    Log a request without touching the credit pool.

    Used for owner keys, which skip billing entirely. Skipping the *charge* is
    intended; skipping the audit trail is not — an unlimited key that leaves no
    record of what it did is exactly the key you most want a record of.
    """
    coll(REQUEST_LOG).insert_one({
        "api_key_id": object_id(api_key_id),
        "user_id": object_id(user_id),
        "endpoint": endpoint,
        "message_len": message_len,
        "credit_cost": cost,
        "timestamp": _now(),
    })


def get_next_reset_time() -> str:
    """Return the next midnight IST as an ISO timestamp."""
    now = datetime.now(config.IST)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if tomorrow <= now:
        tomorrow += timedelta(days=1)
    return tomorrow.isoformat()


# ---------------------------------------------------------------------------
# Usage stats
# ---------------------------------------------------------------------------

def get_usage_stats(user_id: Any, daily_credit_limit: int | None = None) -> dict[str, Any]:
    """Account-level usage statistics, aggregated across all of a user's keys."""
    oid = object_id(user_id)
    limit = _effective_limit(daily_credit_limit)

    credits_used_today = get_credits_used_today(user_id)
    bonus_credits = get_bonus_balance(user_id)
    credits_remaining = max(0, limit - credits_used_today) + bonus_credits

    totals = list(coll(REQUEST_LOG).aggregate([
        {"$match": {"user_id": oid}},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "total_credits": {"$sum": "$credit_cost"},
        }},
    ]))
    total_queries = int(totals[0]["total"]) if totals else 0
    total_credits = int(totals[0]["total_credits"]) if totals else 0

    week_ago = (datetime.now(config.IST) - timedelta(days=7)).strftime("%Y-%m-%d")
    history = [
        {"usage_date": row["usage_date"], "credits_used": row["credits_used"]}
        for row in coll(DAILY_USAGE)
        .find({"user_id": oid, "usage_date": {"$gte": week_ago}})
        .sort("usage_date", ASCENDING)
    ]

    return {
        "credits_remaining": credits_remaining,
        "credits_used_today": credits_used_today,
        "credits_daily_limit": limit,
        "bonus_credits": bonus_credits,
        "resets_at": get_next_reset_time(),
        "total_queries_all_time": total_queries,
        "total_credits_consumed_all_time": total_credits,
        "last_7_days": history,
    }


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------

def list_all_keys() -> list[dict[str, Any]]:
    """Every API key with owner/role info (for admin). Never exposes the hash."""
    users = {u["_id"]: u for u in coll(USERS).find()}

    result = []
    for key in coll(API_KEYS).find().sort("_id", ASCENDING):
        user = users.get(key["user_id"], {})
        result.append({
            "id": str(key["_id"]),
            "key_prefix": key["key_prefix"],
            "label": key.get("label", ""),
            "created_at": key["created_at"],
            "is_active": key.get("is_active", 0),
            "user_id": str(key["user_id"]),
            "owner_email": user.get("email", ""),
            "owner_name": user.get("name", ""),
            "role": user.get("role", ROLE_USER),
            "daily_credit_limit": user.get("daily_credit_limit"),
            "credits_remaining": (
                get_credits_remaining(key["user_id"], user.get("daily_credit_limit"))
                if key.get("is_active")
                else 0
            ),
        })
    return result


def revoke_key(key_id: Any) -> bool:
    """Deactivate an API key. Returns True if the key existed."""
    oid = object_id(key_id)
    if oid is None:
        return False
    return coll(API_KEYS).update_one(
        {"_id": oid}, {"$set": {"is_active": 0}}
    ).matched_count > 0


# ---------------------------------------------------------------------------
# Account-scoped operations (used by the self-serve billing dashboard)
# ---------------------------------------------------------------------------

def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Return the account for *email*, or None if it doesn't exist yet."""
    return document(coll(USERS).find_one({"email": email.lower().strip()}))


def set_daily_credit_limit(email: str, limit: int | None, name: str = "") -> dict[str, Any]:
    """
    Set an account's daily credit allowance, creating the account if needed.

    This is how a subscription tier becomes an entitlement: activate a plan,
    write its ``daily_credits`` here. ``None`` restores the global default.
    """
    user = _get_or_create_user(email, name, role=ROLE_USER)
    updated = coll(USERS).find_one_and_update(
        {"_id": to_object_id(user["id"])},
        {"$set": {"daily_credit_limit": limit}},
        return_document=ReturnDocument.AFTER,
    )
    return document(updated)  # type: ignore[return-value]


def list_keys_for_email(email: str) -> list[dict[str, Any]]:
    """The API keys belonging to one account (never exposes the hash)."""
    user = coll(USERS).find_one({"email": email.lower().strip()})
    if user is None:
        return []

    return [
        {
            "id": str(key["_id"]),
            "key_prefix": key["key_prefix"],
            "label": key.get("label", ""),
            "created_at": key["created_at"],
            "is_active": key.get("is_active", 0),
        }
        for key in coll(API_KEYS).find({"user_id": user["_id"]}).sort("_id", DESCENDING)
    ]


def revoke_key_for_email(key_id: Any, email: str) -> bool:
    """
    Revoke a key **only if** it belongs to *email*.

    Scoping the update by owner (rather than checking then updating) is what
    stops one customer revoking another's key by guessing an id.
    """
    oid = object_id(key_id)
    user = coll(USERS).find_one({"email": email.lower().strip()})
    if oid is None or user is None:
        return False

    return coll(API_KEYS).update_one(
        {"_id": oid, "user_id": user["_id"]}, {"$set": {"is_active": 0}}
    ).matched_count > 0


def count_active_keys_for_email(email: str) -> int:
    user = coll(USERS).find_one({"email": email.lower().strip()})
    if user is None:
        return 0
    return coll(API_KEYS).count_documents({"user_id": user["_id"], "is_active": 1})
