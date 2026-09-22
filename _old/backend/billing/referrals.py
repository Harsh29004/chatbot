"""
The referral programme: who brought whom, and what that is worth.

Three payouts, and they are deliberately not the same shape:

* **signup** — the moment a referred person creates an account, the referrer
  gets ``REFERRAL_REFERRER_CREDITS`` and the newcomer gets
  ``REFERRAL_REFERRED_CREDITS`` on top of whatever their plan gives them.
  Paid once per referral, per side.
* **top-up** — every time that person actually pays for something, the
  referrer gets ``REFERRAL_TOPUP_CREDITS``. Paid once *per invoice*, which is
  what makes it safe to call from a webhook that retries.

Rewards land in the bonus balance (``api_keys.credit_grants``), not in the
daily allowance. A referral credit that expired at midnight IST would be worth
almost nothing to someone who earned it in the evening, and "you have 100
credits" has to mean 100 credits.

Nothing here trusts a code it was handed. ``attach`` refuses self-referral,
refuses a second referrer for someone who already has one, and refuses codes
that resolve to nobody — the caller passes user input straight in, so this
module is where that stops being user input.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING

from backend.shared import config
from backend.shared.api_keys import get_user_by_email, grant_bonus_credits
from backend.shared.mongo import (
    DuplicateKeyError,
    coll,
    document,
    documents,
    object_id,
    to_object_id,
)

from backend.billing import db

logger = logging.getLogger(__name__)

# What each event pays, in credits.
REFERRER_SIGNUP_CREDITS = int(os.getenv("REFERRAL_REFERRER_CREDITS", "100"))
REFERRED_SIGNUP_CREDITS = int(os.getenv("REFERRAL_REFERRED_CREDITS", "50"))
TOPUP_CREDITS = int(os.getenv("REFERRAL_TOPUP_CREDITS", "100"))

ROLE_REFERRER = "referrer"
ROLE_REFERRED = "referred"

KIND_SIGNUP = "signup"
KIND_TOPUP = "topup"

SOURCE_CODE = "code"
SOURCE_INVITE = "invite"

# Unambiguous in a shared link and when read aloud: no O/0, no I/1.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
_CODE_RE = re.compile(r"^[A-Z0-9-]{4,32}$")


def _now() -> str:
    return datetime.now(config.IST).isoformat()


# The collections live in backend.billing.db alongside the rest of the billing
# data, and their indexes — the ones that make the payouts idempotent — are
# declared there too.


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------

def normalise_code(code: str | None) -> str:
    """Fold user input to the stored form, or "" when it could not be one."""
    if not code:
        return ""
    candidate = code.strip().upper().replace(" ", "")
    return candidate if _CODE_RE.match(candidate) else ""


def code_for_customer(customer_id: str) -> str:
    """
    This customer's referral code, minted on first use.

    Generated rather than derived from their email: a code is a public string
    that gets pasted into chats and posted on forums, and deriving it from an
    address would put that address in every one of those places.
    """
    oid = to_object_id(customer_id)
    existing = coll(db.REFERRAL_CODES).find_one({"customer_id": oid})
    if existing is not None:
        return existing["code"]

    for _ in range(10):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        try:
            coll(db.REFERRAL_CODES).insert_one({
                "customer_id": oid, "code": code, "created_at": _now(),
            })
            return code
        except DuplicateKeyError:
            # Either the code collided — vanishingly rare — or another request
            # minted this customer's code first. Re-reading covers both.
            existing = coll(db.REFERRAL_CODES).find_one({"customer_id": oid})
            if existing is not None:
                return existing["code"]
            continue

    raise RuntimeError("Could not allocate a referral code.")


def customer_for_code(code: str) -> dict[str, Any] | None:
    """Resolve a referral code to the customer who owns it."""
    normalised = normalise_code(code)
    if not normalised:
        return None
    row = coll(db.REFERRAL_CODES).find_one({"code": normalised})
    return db.get_customer_by_id(row["customer_id"]) if row else None


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

def invite(referrer_customer_id: str, email: str) -> dict[str, Any]:
    """
    Record that this customer invited *email*.

    An invite is the second way a referral can be attributed: someone who
    signs up with an invited address is credited to the inviter even though
    they never clicked a link with a code in it — which is what "they signed
    up with that email" means in practice.
    """
    address = email.lower().strip()
    oid = to_object_id(referrer_customer_id)

    # Upsert rather than insert: inviting the same person twice is a person
    # being thorough, not an error, and it must not reset their claimed status.
    coll(db.REFERRAL_INVITES).update_one(
        {"referrer_customer_id": oid, "email": address},
        {"$setOnInsert": {
            "referrer_customer_id": oid,
            "email": address,
            "created_at": _now(),
            "claimed_at": None,
        }},
        upsert=True,
    )
    return document(coll(db.REFERRAL_INVITES).find_one(
        {"referrer_customer_id": oid, "email": address}
    ))


def list_invites(referrer_customer_id: str) -> list[dict[str, Any]]:
    return documents(
        coll(db.REFERRAL_INVITES)
        .find({"referrer_customer_id": object_id(referrer_customer_id)})
        .sort("_id", DESCENDING)
    )


def _invite_for_email(email: str) -> dict[str, Any] | None:
    """The oldest unclaimed invite for *email*, if anyone invited them."""
    return document(coll(db.REFERRAL_INVITES).find_one(
        {"email": email.lower().strip(), "claimed_at": None},
        sort=[("_id", ASCENDING)],
    ))


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

def get_referral_for(referred_customer_id: str) -> dict[str, Any] | None:
    oid = object_id(referred_customer_id)
    if oid is None:
        return None
    return document(coll(db.REFERRALS).find_one({"referred_customer_id": oid}))


def attach(new_customer: dict[str, Any], code: str | None = None) -> dict[str, Any] | None:
    """
    Attribute a brand-new account to a referrer and pay both sides.

    Called from the signup paths. Returns the referral row, or ``None`` when
    there was nobody to attribute it to — which is the common case and not an
    error worth failing a signup over.

    Refusals, all silent by design (a signup must never fail because of a
    typo'd referral code):

    * a code nobody owns,
    * your own code,
    * an account that already has a referrer.
    """
    if get_referral_for(new_customer["id"]) is not None:
        return None

    referrer = customer_for_code(code) if code else None
    source = SOURCE_CODE
    claimed_invite: dict[str, Any] | None = None

    if referrer is None:
        claimed_invite = _invite_for_email(new_customer["email"])
        if claimed_invite is not None:
            referrer = db.get_customer_by_id(claimed_invite["referrer_customer_id"])
            source = SOURCE_INVITE

    if referrer is None or referrer["id"] == new_customer["id"]:
        return None

    record = {
        "referrer_customer_id": to_object_id(referrer["id"]),
        "referred_customer_id": to_object_id(new_customer["id"]),
        "code": normalise_code(code),
        "source": source,
        "created_at": _now(),
    }
    try:
        result = coll(db.REFERRALS).insert_one(record)
    except DuplicateKeyError:
        # Someone already claimed this account between the check above and
        # here. One referrer per person, and the first one won.
        return None

    if claimed_invite is not None:
        coll(db.REFERRAL_INVITES).update_one(
            {"_id": to_object_id(claimed_invite["id"])},
            {"$set": {"claimed_at": _now()}},
        )

    referral = document({**record, "_id": result.inserted_id})
    assert referral is not None

    _pay(
        referral, referrer, ROLE_REFERRER, KIND_SIGNUP, REFERRER_SIGNUP_CREDITS,
        note=f"referred {new_customer['email']}",
    )
    _pay(
        referral, new_customer, ROLE_REFERRED, KIND_SIGNUP, REFERRED_SIGNUP_CREDITS,
        note=f"joined via {referrer['email']}",
    )
    logger.info(
        "Referral recorded: customer_id=%s referred customer_id=%s via %s.",
        referrer["id"], new_customer["id"], source,
    )
    return referral


def reward_payment(customer_id: str, invoices: list[dict[str, Any]]) -> int:
    """
    Pay the referrer for a payment their referred customer just made.

    One payout per invoice, enforced by a unique index rather than by this
    function remembering — payment webhooks are delivered more than once as a
    matter of course, and "at least once" only becomes "exactly once" if the
    database says so. Returns the credits actually paid out.
    """
    referral = get_referral_for(customer_id)
    if referral is None or not invoices:
        return 0

    referrer = db.get_customer_by_id(referral["referrer_customer_id"])
    if referrer is None:
        return 0

    paid = 0
    for invoice in invoices:
        if _pay(
            referral, referrer, ROLE_REFERRER, KIND_TOPUP, TOPUP_CREDITS,
            invoice_id=invoice["id"],
            note=f"top-up by {invoice.get('plan_id', 'plan')} invoice #{invoice['id']}",
        ):
            paid += TOPUP_CREDITS
    return paid


def _pay(
    referral: dict[str, Any],
    customer: dict[str, Any],
    role: str,
    kind: str,
    credits: int,
    *,
    invoice_id: str | None = None,
    note: str = "",
) -> bool:
    """
    Record one payout and move the credits, or do nothing if already paid.

    The reward row is written *first*: its unique index is what makes this
    idempotent, so the insert has to be the thing that fails on a repeat. Were
    the credits granted first, a retry would hand them out again and only then
    discover it shouldn't have.
    """
    if credits <= 0:
        return False

    try:
        coll(db.REFERRAL_REWARDS).insert_one({
            "referral_id": to_object_id(referral["id"]),
            "customer_id": to_object_id(customer["id"]),
            "role": role,
            "kind": kind,
            "credits": credits,
            "invoice_id": object_id(invoice_id),
            "created_at": _now(),
        })
    except DuplicateKeyError:
        # The unique index rejected it: this reward has already been paid.
        return False

    user = get_user_by_email(customer["email"])
    if user is None:
        # The account exists in billing but has no API-key account yet, which
        # means nothing has ever granted it an allowance. Rare, and not worth
        # losing the credits over: create it by granting against the email.
        from backend.shared.api_keys import set_daily_credit_limit

        user = set_daily_credit_limit(customer["email"], None, name=customer.get("name", ""))

    grant_bonus_credits(
        user["id"], credits, reason=f"referral_{kind}_{role}", note=note
    )
    return True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def mask_email(email: str) -> str:
    """
    ``jordan@example.com`` -> ``jo•••@example.com``.

    The referrer already knows who they invited, so this is not secrecy — it
    is not putting a third party's full address on a screen that may be shared
    or screenshotted.
    """
    name, _, domain = email.partition("@")
    if not domain:
        return email
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}•••@{domain}"


def summary_for(customer_id: str) -> dict[str, Any]:
    """Everything the customer's referral card shows, in one query set."""
    oid = to_object_id(customer_id)

    # Fetched and assembled here rather than as one pipeline. A referrer has
    # tens of referrals, not millions, and three indexed reads that anyone can
    # follow beat a $lookup chain that only runs on a replica set.
    referrals_out = list(
        coll(db.REFERRALS).find({"referrer_customer_id": oid}).sort("_id", DESCENDING)
    )
    referral_ids = [r["_id"] for r in referrals_out]

    people = {
        c["_id"]: c
        for c in coll(db.CUSTOMERS).find(
            {"_id": {"$in": [r["referred_customer_id"] for r in referrals_out]}}
        )
    }

    earned_by_referral: dict[Any, int] = {}
    topups_by_referral: dict[Any, int] = {}
    for reward in coll(db.REFERRAL_REWARDS).find({"referral_id": {"$in": referral_ids}}):
        if reward["role"] == ROLE_REFERRER:
            earned_by_referral[reward["referral_id"]] = (
                earned_by_referral.get(reward["referral_id"], 0) + reward["credits"]
            )
        if reward["kind"] == KIND_TOPUP:
            topups_by_referral[reward["referral_id"]] = (
                topups_by_referral.get(reward["referral_id"], 0) + 1
            )

    referred = [
        {
            "email": people.get(r["referred_customer_id"], {}).get("email", ""),
            "name": people.get(r["referred_customer_id"], {}).get("name", ""),
            "joined_at": r["created_at"],
            "source": r["source"],
            "credits_earned": earned_by_referral.get(r["_id"], 0),
            "payments_rewarded": topups_by_referral.get(r["_id"], 0),
        }
        for r in referrals_out
    ]

    earned = sum(
        reward["credits"]
        for reward in coll(db.REFERRAL_REWARDS).find(
            {"customer_id": oid, "role": ROLE_REFERRER}
        )
    )

    joined_via = get_referral_for(customer_id)
    inviter = (
        db.get_customer_by_id(joined_via["referrer_customer_id"]) if joined_via else None
    )

    return {
        "code": code_for_customer(customer_id),
        "credits_earned": int(earned),
        "referrer_signup_credits": REFERRER_SIGNUP_CREDITS,
        "referred_signup_credits": REFERRED_SIGNUP_CREDITS,
        "topup_credits": TOPUP_CREDITS,
        "referred": [
            {**row, "email": mask_email(row["email"])} for row in referred
        ],
        "invites": [
            {
                "email": inv["email"],
                "created_at": inv["created_at"],
                "claimed_at": inv["claimed_at"],
            }
            for inv in list_invites(customer_id)
        ],
        "joined_via": mask_email(inviter["email"]) if inviter else None,
    }


def platform_stats() -> dict[str, Any]:
    """Programme-wide totals for the admin panel."""
    def _sum(match: dict[str, Any]) -> int:
        rows = list(coll(db.REFERRAL_REWARDS).aggregate([
            {"$match": match},
            {"$group": {"_id": None, "n": {"$sum": "$credits"}}},
        ]))
        return int(rows[0]["n"]) if rows else 0

    totals = {
        "referrals": coll(db.REFERRALS).count_documents({}),
        "invites_sent": coll(db.REFERRAL_INVITES).count_documents({}),
        "invites_claimed": coll(db.REFERRAL_INVITES).count_documents(
            {"claimed_at": {"$ne": None}}
        ),
        "credits_paid": _sum({}),
        "credits_paid_on_topups": _sum({"kind": KIND_TOPUP}),
        "rewarded_payments": coll(db.REFERRAL_REWARDS).count_documents(
            {"kind": KIND_TOPUP}
        ),
    }

    # Same reasoning as summary_for: two reads and a dict, rather than a
    # pipeline that mongomock cannot run and a reviewer cannot check.
    referred_counts: dict[Any, int] = {}
    for referral in coll(db.REFERRALS).find({}, {"referrer_customer_id": 1}):
        key = referral["referrer_customer_id"]
        referred_counts[key] = referred_counts.get(key, 0) + 1

    earned_by_customer: dict[Any, int] = {}
    for reward in coll(db.REFERRAL_REWARDS).find({"role": ROLE_REFERRER}):
        key = reward["customer_id"]
        earned_by_customer[key] = earned_by_customer.get(key, 0) + reward["credits"]

    people = {
        c["_id"]: c
        for c in coll(db.CUSTOMERS).find({"_id": {"$in": list(referred_counts)}})
    }

    leaders = sorted(
        (
            {
                "customer_id": str(customer_oid),
                "email": people.get(customer_oid, {}).get("email", ""),
                "name": people.get(customer_oid, {}).get("name", ""),
                "referred_count": count,
                "credits_earned": earned_by_customer.get(customer_oid, 0),
            }
            for customer_oid, count in referred_counts.items()
        ),
        key=lambda row: (row["referred_count"], row["credits_earned"]),
        reverse=True,
    )[:25]

    recent_rewards = list(
        coll(db.REFERRAL_REWARDS).find().sort("_id", DESCENDING).limit(50)
    )
    reward_people = {
        c["_id"]: c
        for c in coll(db.CUSTOMERS).find(
            {"_id": {"$in": [r["customer_id"] for r in recent_rewards]}}
        )
    }
    recent = [
        {
            "id": str(reward["_id"]),
            "kind": reward["kind"],
            "role": reward["role"],
            "credits": reward["credits"],
            "created_at": reward["created_at"],
            "invoice_id": str(reward["invoice_id"]) if reward.get("invoice_id") else None,
            "email": reward_people.get(reward["customer_id"], {}).get("email", ""),
        }
        for reward in recent_rewards
    ]

    return {
        "totals": totals,
        "rewards": {
            "referrer_signup_credits": REFERRER_SIGNUP_CREDITS,
            "referred_signup_credits": REFERRED_SIGNUP_CREDITS,
            "topup_credits": TOPUP_CREDITS,
        },
        "leaderboard": leaders,
        "recent_rewards": recent,
    }
