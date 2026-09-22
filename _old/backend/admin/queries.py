"""
Cross-tenant reads for the admin panel.

This is the second deliberate exception to tenant isolation, after
``backend.ops``. The difference between them is who they are for and what they
show: ``ops`` feeds a model and therefore carries aggregates *without
identities*, while this module feeds a person who runs the platform and has to
be able to answer "which customer is this?". So it does return names, emails
and per-account numbers — and it is gated on the admin key, never reachable
with a customer session or a customer API key.

**On shape.** The SQLite version was one query per screen, built from
correlated subqueries. The MongoDB version reads each collection plainly and
joins in Python. That is a deliberate trade, not a translation failure:

* the joins are across five collections at four different grains, which as an
  aggregation pipeline becomes something no reviewer can check by eye;
* ``$lookup`` with sub-pipelines does not run on every deployment, so the tests
  could not exercise the real query;
* the volumes are hundreds to low thousands of documents, where the difference
  between a pipeline and a dict comprehension is not measurable.

Where a single-collection ``$group`` does the job — summing credits by day, by
endpoint — it is used, because that one is both clearer and cheaper.

The queries are written to survive a half-populated database. A customer with
no API-key account, an account with no bot, a bot with no sheet: all normal on
day one, all rendered as zero rather than as a missing row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING

from backend.shared import config
from backend.shared.api_keys import ROLE_OWNER
from backend.shared.mongo import coll, document, documents, object_id
from backend.billing import db as billing_db
from backend.billing import referrals
from backend.billing.plans import BILLABLE_PLAN_IDS, CURRENCY, PLANS, format_cents

# A page of accounts. Large enough that most deployments never paginate, small
# enough that the panel stays responsive when one day they do.
DEFAULT_PAGE = 50
MAX_PAGE = 500

USERS = "users"
API_KEYS = "api_keys"
DAILY_USAGE = "daily_usage"
REQUEST_LOG = "request_log"
CREDIT_GRANTS = "credit_grants"
UNMATCHED = "unmatched_queries"
BOTS = "bots"


def _now() -> datetime:
    return datetime.now(config.IST)


def _since(days: int) -> str:
    """ISO cutoff *days* ago, for the ``created_at``/``timestamp`` fields."""
    return (_now() - timedelta(days=days)).isoformat()


def _date_since(days: int) -> str:
    """``YYYY-MM-DD`` cutoff, for the date-keyed ``daily_usage`` documents."""
    return (_now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _sum_by(collection: str, match: dict[str, Any], group_by: str, field: str) -> dict[Any, int]:
    """``{key: total}`` for one collection — the one shape a pipeline suits."""
    return {
        row["_id"]: int(row["total"])
        for row in coll(collection).aggregate([
            {"$match": match},
            {"$group": {"_id": f"${group_by}", "total": {"$sum": f"${field}"}}},
        ])
    }


def _count_by(collection: str, match: dict[str, Any], group_by: str) -> dict[Any, int]:
    return {
        row["_id"]: int(row["n"])
        for row in coll(collection).aggregate([
            {"$match": match},
            {"$group": {"_id": f"${group_by}", "n": {"$sum": 1}}},
        ])
    }


# ---------------------------------------------------------------------------
# Shared assembly
# ---------------------------------------------------------------------------

def _current_subscriptions() -> dict[Any, dict[str, Any]]:
    """
    Each customer's *current* subscription, keyed by customer id.

    "Latest wins" is the same rule the customer's own dashboard uses, so the
    two screens can never disagree about what plan somebody is on.
    """
    current: dict[Any, dict[str, Any]] = {}
    for sub in coll(billing_db.SUBSCRIPTIONS).find().sort("_id", ASCENDING):
        current[sub["customer_id"]] = sub  # later documents overwrite earlier
    return current


def _is_live(sub: dict[str, Any] | None) -> bool:
    if not sub:
        return False
    return (
        sub.get("status") in billing_db.ENTITLED_STATUSES
        and sub.get("current_period_end", "") > _now().isoformat()
    )


def _monthly_value_cents(plan_id: str) -> int:
    """
    One subscription's contribution to monthly recurring revenue.

    A yearly plan counts as a twelfth of its price, not its full price: MRR
    that spikes when someone pays for a year is not a run rate, it is a cash
    receipt wearing a run rate's name.
    """
    plan = PLANS.get(plan_id)
    if plan is None or plan.price_cents == 0:
        return 0
    return plan.price_cents if plan.interval == "month" else round(plan.price_cents / 12)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def overview(days: int = 30) -> dict[str, Any]:
    """The headline numbers."""
    since = _since(days)
    customers = list(coll(billing_db.CUSTOMERS).find())

    subs = _current_subscriptions()
    by_status: dict[str, int] = {}
    by_plan: dict[str, int] = {}
    entitled = paying = trialing = mrr_cents = 0

    for sub in subs.values():
        by_status[sub["status"]] = by_status.get(sub["status"], 0) + 1
        by_plan[sub["plan_id"]] = by_plan.get(sub["plan_id"], 0) + 1
        if _is_live(sub):
            entitled += 1
            # Entitled and paying are not the same thing — a trial is entitled
            # and worth nothing. Reporting them as one number is how a
            # dashboard ends up flattering itself.
            if sub["plan_id"] in BILLABLE_PLAN_IDS:
                paying += 1
            else:
                trialing += 1
            mrr_cents += _monthly_value_cents(sub["plan_id"])

    paid_invoices = list(coll(billing_db.INVOICES).find({"status": "paid"}))
    revenue_all = sum(i["amount_cents"] for i in paid_invoices)
    revenue_window = sum(
        i["amount_cents"] for i in paid_invoices if (i.get("paid_at") or "") >= since
    )

    requests = list(coll(REQUEST_LOG).find({"timestamp": {"$gte": since}}))
    credits_today = sum(
        d["credits_used"] for d in coll(DAILY_USAGE).find({"usage_date": _today()})
    )

    bots = list(coll(BOTS).find())
    ready = sum(1 for b in bots if b.get("status") == "ready")

    grants = list(coll(CREDIT_GRANTS).find())

    return {
        "window_days": days,
        "generated_at": _now().isoformat(),
        "customers": {
            "total": len(customers),
            "active": sum(1 for c in customers if c.get("is_active")),
            "new_in_window": sum(1 for c in customers if c.get("created_at", "") >= since),
            "via_google": sum(
                1 for c in customers if c.get("auth_provider") == billing_db.PROVIDER_GOOGLE
            ),
        },
        "subscriptions": {
            "entitled": entitled,
            "paying": paying,
            "on_trial": trialing,
            "by_status": by_status,
            "by_plan": by_plan,
            "mrr_cents": mrr_cents,
            "mrr_display": format_cents(mrr_cents),
            "currency": CURRENCY,
        },
        "revenue": {
            "all_time_cents": revenue_all,
            "all_time_display": format_cents(revenue_all),
            "in_window_cents": revenue_window,
            "in_window_display": format_cents(revenue_window),
            "invoices_paid": len(paid_invoices),
        },
        "usage": {
            "requests_in_window": len(requests),
            "credits_in_window": sum(r.get("credit_cost", 0) for r in requests),
            "active_accounts": len({r["user_id"] for r in requests}),
            "credits_today": credits_today,
        },
        "bots": {
            "total": len(bots),
            "ready": ready,
            "draft": len(bots) - ready,
            "rewording_on": sum(1 for b in bots if b.get("llm_enabled")),
            "indexed_rows": sum(b.get("doc_count", 0) for b in bots),
        },
        "keys": {
            "total": coll(API_KEYS).count_documents({}),
            "active": coll(API_KEYS).count_documents({"is_active": 1}),
        },
        "bonus_credits": {
            "granted_all_time": sum(g["amount"] for g in grants),
            "outstanding": sum(g["remaining"] for g in grants),
        },
        "referrals": referrals.platform_stats()["totals"],
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _account_rows(days: int, customer_filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    One row per customer, with everything the list view shows.

    Assembled from six reads rather than six joins. Each aggregate is over a
    different grain — keys per account, credits per account-day, requests per
    account — and combining them in one query is what silently multiplies rows
    and inflates every total on the screen.
    """
    customers = list(coll(billing_db.CUSTOMERS).find(customer_filter or {}))
    if not customers:
        return []

    emails = [c["email"].lower() for c in customers]
    users = {u["email"]: u for u in coll(USERS).find({"email": {"$in": emails}})}
    user_ids = [u["_id"] for u in users.values()]

    bots = {b["user_id"]: b for b in coll(BOTS).find({"user_id": {"$in": user_ids}})}
    subs = _current_subscriptions()

    bonus = _sum_by(CREDIT_GRANTS, {"user_id": {"$in": user_ids}}, "user_id", "remaining")
    credits_today = _sum_by(
        DAILY_USAGE,
        {"user_id": {"$in": user_ids}, "usage_date": _today()},
        "user_id", "credits_used",
    )
    credits_window = _sum_by(
        DAILY_USAGE,
        {"user_id": {"$in": user_ids}, "usage_date": {"$gte": _date_since(days)}},
        "user_id", "credits_used",
    )
    requests_window = _count_by(
        REQUEST_LOG,
        {"user_id": {"$in": user_ids}, "timestamp": {"$gte": _since(days)}},
        "user_id",
    )
    active_keys = _count_by(API_KEYS, {"user_id": {"$in": user_ids}, "is_active": 1}, "user_id")

    last_request: dict[Any, str] = {}
    for row in coll(REQUEST_LOG).aggregate([
        {"$match": {"user_id": {"$in": user_ids}}},
        {"$group": {"_id": "$user_id", "last": {"$max": "$timestamp"}}},
    ]):
        last_request[row["_id"]] = row["last"]

    # Referral standing, both directions.
    referred_counts = _count_by(billing_db.REFERRALS, {}, "referrer_customer_id")
    referred_by: dict[Any, Any] = {
        r["referred_customer_id"]: r["referrer_customer_id"]
        for r in coll(billing_db.REFERRALS).find()
    }
    referrer_emails = {
        c["_id"]: c["email"]
        for c in coll(billing_db.CUSTOMERS).find(
            {"_id": {"$in": list(set(referred_by.values()))}}, {"email": 1}
        )
    }

    rows = []
    for customer in customers:
        user = users.get(customer["email"].lower())
        user_id = user["_id"] if user else None
        bot = bots.get(user_id) if user_id else None
        sub = subs.get(customer["_id"])
        limit = user.get("daily_credit_limit") if user else None

        rows.append({
            "customer_id": str(customer["_id"]),
            "user_id": str(user_id) if user_id else None,
            "email": customer["email"],
            "name": customer.get("name", ""),
            "created_at": customer.get("created_at"),
            "is_active": customer.get("is_active", 0),
            "auth_provider": customer.get("auth_provider", billing_db.PROVIDER_PASSWORD),
            "role": user.get("role") if user else None,
            "daily_credit_limit": limit,
            "effective_daily_limit": config.DAILY_CREDIT_LIMIT if limit is None else limit,
            "bonus_credits": bonus.get(user_id, 0),
            "credits_today": credits_today.get(user_id, 0),
            "credits_in_window": credits_window.get(user_id, 0),
            "requests_in_window": requests_window.get(user_id, 0),
            "last_request_at": last_request.get(user_id),
            "active_keys": active_keys.get(user_id, 0),
            "bot_id": str(bot["_id"]) if bot else None,
            "template_id": bot.get("template_id") if bot else None,
            "bot_status": bot.get("status") if bot else None,
            "bot_doc_count": bot.get("doc_count") if bot else None,
            "bot_llm_enabled": bot.get("llm_enabled") if bot else None,
            "plan_id": sub.get("plan_id") if sub else None,
            "subscription_status": sub.get("status") if sub else None,
            "current_period_end": sub.get("current_period_end") if sub else None,
            "is_entitled": _is_live(sub),
            "referred_count": referred_counts.get(customer["_id"], 0),
            "referred_by_email": referrer_emails.get(referred_by.get(customer["_id"])),
        })
    return rows


_SORTS = {
    "created": lambda r: r.get("created_at") or "",
    "email": lambda r: r["email"].lower(),
    "credits": lambda r: r["credits_in_window"],
    "requests": lambda r: r["requests_in_window"],
    "referrals": lambda r: r["referred_count"],
}


def list_users(
    *,
    query: str = "",
    status: str = "",
    days: int = 30,
    sort: str = "created",
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Every customer, with their plan, usage, bot and referral standing.

    *status* filters on the current subscription (``entitled`` means "can use
    the product right now", which is not the same as any single status value).
    """
    limit = max(1, min(limit, MAX_PAGE))

    customer_filter: dict[str, Any] = {}
    if query:
        term = query.lower().strip()
        # Anchored on neither end: staff search for a fragment of an address as
        # often as its start. Escaped, because a customer's email is not a
        # pattern and a stray "+" would otherwise change the search.
        import re

        pattern = re.escape(term)
        customer_filter["$or"] = [
            {"email": {"$regex": pattern, "$options": "i"}},
            {"name": {"$regex": pattern, "$options": "i"}},
        ]
    if status == "disabled":
        customer_filter["is_active"] = 0

    rows = _account_rows(days, customer_filter)

    if status == "entitled":
        rows = [r for r in rows if r["is_entitled"]]
    elif status == "paying":
        rows = [r for r in rows if r["is_entitled"] and r["plan_id"] in BILLABLE_PLAN_IDS]
    elif status and status != "disabled":
        rows = [r for r in rows if r["subscription_status"] == status]

    key = _SORTS.get(sort, _SORTS["created"])
    rows.sort(key=key, reverse=sort != "email")

    return {
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "window_days": days,
        "users": rows[offset:offset + limit],
    }


def user_detail(customer_id: Any, days: int = 30) -> dict[str, Any] | None:
    """One account, in full: plan history, invoices, keys, credits, referrals."""
    oid = object_id(customer_id)
    if oid is None:
        return None

    rows = _account_rows(days, {"_id": oid})
    if not rows:
        return None
    account = rows[0]

    user_oid = object_id(account["user_id"]) if account["user_id"] else None
    keys = (
        [
            {
                "id": str(k["_id"]),
                "key_prefix": k["key_prefix"],
                "label": k.get("label", ""),
                "created_at": k["created_at"],
                "is_active": k.get("is_active", 0),
            }
            for k in coll(API_KEYS).find({"user_id": user_oid}).sort("_id", DESCENDING)
        ]
        if user_oid
        else []
    )

    # Read the log once, then label each entry with the key that made it.
    raw_requests = (
        list(coll(REQUEST_LOG).find({"user_id": user_oid}).sort("_id", DESCENDING).limit(50))
        if user_oid
        else []
    )
    prefixes = {k["id"]: k["key_prefix"] for k in keys}
    recent_requests = [
        {
            "id": str(r["_id"]),
            "endpoint": r["endpoint"],
            "message_len": r.get("message_len", 0),
            "credit_cost": r.get("credit_cost", 0),
            "timestamp": r["timestamp"],
            "key_prefix": prefixes.get(str(r.get("api_key_id"))),
        }
        for r in raw_requests
    ]

    return {
        "account": account,
        "subscriptions": documents(
            coll(billing_db.SUBSCRIPTIONS).find({"customer_id": oid})
            .sort("_id", DESCENDING).limit(25)
        ),
        "invoices": documents(
            coll(billing_db.INVOICES).find({"customer_id": oid})
            .sort("_id", DESCENDING).limit(25)
        ),
        "keys": keys,
        "credit_grants": documents(
            coll(CREDIT_GRANTS).find({"user_id": user_oid})
            .sort("_id", DESCENDING).limit(50)
        ) if user_oid else [],
        "daily_usage": [
            {"usage_date": d["usage_date"], "credits_used": d["credits_used"]}
            for d in coll(DAILY_USAGE)
            .find({"user_id": user_oid, "usage_date": {"$gte": _date_since(days)}})
            .sort("usage_date", ASCENDING)
        ] if user_oid else [],
        "recent_requests": recent_requests,
        "referrals": referrals.summary_for(customer_id),
    }


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def subscriptions(days: int = 90) -> dict[str, Any]:
    """
    The subscription book: what is live, what it is worth, what churned.

    Trial-to-paid conversion is counted per customer rather than per
    subscription document, because one customer trialling twice is not two
    conversions.
    """
    since = _since(days)
    current = _current_subscriptions()

    people = {
        c["_id"]: c
        for c in coll(billing_db.CUSTOMERS).find({"_id": {"$in": list(current)}})
    }

    rows = []
    by_status: dict[str, int] = {}
    by_plan: dict[str, dict[str, Any]] = {}

    for customer_oid, sub in current.items():
        live = _is_live(sub)
        value = _monthly_value_cents(sub["plan_id"]) if live else 0
        person = people.get(customer_oid, {})

        rows.append({
            "id": str(sub["_id"]),
            "customer_id": str(customer_oid),
            "email": person.get("email", ""),
            "name": person.get("name", ""),
            "plan_id": sub["plan_id"],
            "status": sub["status"],
            "current_period_end": sub["current_period_end"],
            "is_entitled": live,
            "monthly_value_cents": value,
        })

        by_status[sub["status"]] = by_status.get(sub["status"], 0) + 1
        bucket = by_plan.setdefault(
            sub["plan_id"],
            {"plan_id": sub["plan_id"], "count": 0, "entitled": 0, "mrr_cents": 0},
        )
        bucket["count"] += 1
        if live:
            bucket["entitled"] += 1
            bucket["mrr_cents"] += value

    rows.sort(key=lambda r: r["id"], reverse=True)

    revenue_by_month: dict[str, dict[str, Any]] = {}
    for invoice in coll(billing_db.INVOICES).find({"status": "paid"}):
        month = (invoice.get("paid_at") or "")[:7]
        if not month:
            continue
        bucket = revenue_by_month.setdefault(
            month, {"month": month, "invoices": 0, "amount_cents": 0}
        )
        bucket["invoices"] += 1
        bucket["amount_cents"] += invoice["amount_cents"]
    months = sorted(revenue_by_month.values(), key=lambda m: m["month"], reverse=True)[:24]
    for month in months:
        month["amount_display"] = format_cents(month["amount_cents"])

    started = _count_by(billing_db.SUBSCRIPTIONS, {"created_at": {"$gte": since}}, "plan_id")
    churn = _count_by(
        billing_db.SUBSCRIPTIONS,
        {"updated_at": {"$gte": since},
         "status": {"$in": [billing_db.STATUS_CANCELED, billing_db.STATUS_EXPIRED]}},
        "status",
    )

    trialled = len(coll(billing_db.SUBSCRIPTIONS).distinct("customer_id", {"plan_id": "trial"}))
    converted = len(coll(billing_db.SUBSCRIPTIONS).distinct("customer_id", {
        "plan_id": {"$in": list(BILLABLE_PLAN_IDS)},
        "status": {"$in": [billing_db.STATUS_ACTIVE, billing_db.STATUS_CANCELED]},
    }))

    mrr = sum(r["monthly_value_cents"] for r in rows)
    return {
        "window_days": days,
        "counts": {
            "customers_with_a_subscription": len(rows),
            "entitled": sum(1 for r in rows if r["is_entitled"]),
            "paying": sum(
                1 for r in rows if r["is_entitled"] and r["plan_id"] in BILLABLE_PLAN_IDS
            ),
            "by_status": by_status,
        },
        "by_plan": sorted(by_plan.values(), key=lambda p: p["mrr_cents"], reverse=True),
        "mrr_cents": mrr,
        "mrr_display": format_cents(mrr),
        "revenue_by_month": months,
        "started_in_window": started,
        "churn_in_window": {
            "canceled": churn.get(billing_db.STATUS_CANCELED, 0),
            "expired": churn.get(billing_db.STATUS_EXPIRED, 0),
        },
        "trial_conversion": {
            "trialled": trialled,
            "converted": converted,
            "percent": round(converted * 100 / trialled, 1) if trialled else 0.0,
        },
        "subscriptions": rows[:MAX_PAGE],
    }


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

def usage(days: int = 30) -> dict[str, Any]:
    """Credits and requests over time, by account, and by endpoint."""
    date_since = _date_since(days)
    since = _since(days)

    per_day_map: dict[str, dict[str, Any]] = {}
    for row in coll(DAILY_USAGE).find({"usage_date": {"$gte": date_since}}):
        bucket = per_day_map.setdefault(
            row["usage_date"],
            {"date": row["usage_date"], "credits": 0, "accounts": 0, "requests": 0},
        )
        bucket["credits"] += row["credits_used"]
        bucket["accounts"] += 1

    requests = list(coll(REQUEST_LOG).find({"timestamp": {"$gte": since}}))
    for request in requests:
        day = request["timestamp"][:10]
        bucket = per_day_map.setdefault(
            day, {"date": day, "credits": 0, "accounts": 0, "requests": 0}
        )
        bucket["requests"] += 1
    per_day = sorted(per_day_map.values(), key=lambda d: d["date"])

    users = {u["_id"]: u for u in coll(USERS).find()}
    credits_by_user = _sum_by(
        DAILY_USAGE, {"usage_date": {"$gte": date_since}}, "user_id", "credits_used"
    )
    requests_by_user: dict[Any, int] = {}
    for request in requests:
        requests_by_user[request["user_id"]] = requests_by_user.get(request["user_id"], 0) + 1

    top_accounts = sorted(
        (
            {
                "user_id": str(user_id),
                "email": users.get(user_id, {}).get("email", ""),
                "name": users.get(user_id, {}).get("name", ""),
                "role": users.get(user_id, {}).get("role", ""),
                "credits": credits_by_user.get(user_id, 0),
                "requests": requests_by_user.get(user_id, 0),
            }
            for user_id in set(credits_by_user) | set(requests_by_user)
        ),
        key=lambda row: (row["credits"], row["requests"]),
        reverse=True,
    )[:25]

    by_endpoint_map: dict[str, dict[str, Any]] = {}
    for request in requests:
        bucket = by_endpoint_map.setdefault(request["endpoint"], {
            "endpoint": request["endpoint"], "requests": 0, "credits": 0,
            "accounts": set(), "total_len": 0,
        })
        bucket["requests"] += 1
        bucket["credits"] += request.get("credit_cost", 0)
        bucket["accounts"].add(request["user_id"])
        bucket["total_len"] += request.get("message_len", 0)

    by_endpoint = sorted(
        (
            {
                "endpoint": b["endpoint"],
                "requests": b["requests"],
                "credits": b["credits"],
                "accounts": len(b["accounts"]),
                "avg_message_len": b["total_len"] // b["requests"] if b["requests"] else 0,
            }
            for b in by_endpoint_map.values()
        ),
        key=lambda row: row["requests"],
        reverse=True,
    )

    return {
        "window_days": days,
        "totals": {
            "requests": len(requests),
            "credits": sum(r.get("credit_cost", 0) for r in requests),
            "active_accounts": len({r["user_id"] for r in requests}),
        },
        "per_day": per_day,
        "top_accounts": top_accounts,
        "by_endpoint": by_endpoint,
    }


# ---------------------------------------------------------------------------
# API audit
# ---------------------------------------------------------------------------

def audit(
    *,
    days: int = 7,
    email: str = "",
    endpoint: str = "",
    role: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """
    The API request trail: who called what, with which key, at what cost.

    Owner keys are unmetered but still logged, and this is the screen that
    exists for them — an unlimited cross-tenant key is the one whose activity
    most needs to be visible. ``role`` filters to exactly that.
    """
    limit = max(1, min(limit, 1000))

    user_filter: dict[str, Any] = {}
    if email:
        import re

        user_filter["email"] = {"$regex": re.escape(email.lower().strip()), "$options": "i"}
    if role:
        user_filter["role"] = role

    users = {u["_id"]: u for u in coll(USERS).find(user_filter)}
    query: dict[str, Any] = {"timestamp": {"$gte": _since(days)}}
    if user_filter:
        query["user_id"] = {"$in": list(users)}
    if endpoint:
        import re

        query["endpoint"] = {"$regex": re.escape(endpoint.strip()), "$options": "i"}

    total = coll(REQUEST_LOG).count_documents(query)
    matched = list(
        coll(REQUEST_LOG).find(query).sort("_id", DESCENDING).skip(offset).limit(limit)
    )

    if not user_filter:
        users = {
            u["_id"]: u
            for u in coll(USERS).find({"_id": {"$in": [m["user_id"] for m in matched]}})
        }
    keys = {
        k["_id"]: k
        for k in coll(API_KEYS).find({"_id": {"$in": [m.get("api_key_id") for m in matched]}})
    }

    entries = []
    for row in matched:
        user = users.get(row["user_id"], {})
        key = keys.get(row.get("api_key_id"), {})
        entries.append({
            "id": str(row["_id"]),
            "timestamp": row["timestamp"],
            "endpoint": row["endpoint"],
            "message_len": row.get("message_len", 0),
            "credit_cost": row.get("credit_cost", 0),
            "owner_email": user.get("email"),
            "role": user.get("role"),
            "key_prefix": key.get("key_prefix"),
            "key_label": key.get("label"),
            "key_is_active": key.get("is_active"),
        })

    all_matching = list(coll(REQUEST_LOG).find(query))
    summary = {
        "requests": len(all_matching),
        "credits": sum(r.get("credit_cost", 0) for r in all_matching),
        "accounts": len({r["user_id"] for r in all_matching}),
        "keys_used": len({r.get("api_key_id") for r in all_matching}),
    }

    owner_users = {u["_id"]: u for u in coll(USERS).find({"role": ROLE_OWNER})}
    owner_keys = [
        {
            "id": str(k["_id"]),
            "key_prefix": k["key_prefix"],
            "label": k.get("label", ""),
            "created_at": k["created_at"],
            "is_active": k.get("is_active", 0),
            "owner_email": owner_users.get(k["user_id"], {}).get("email", ""),
            "requests": coll(REQUEST_LOG).count_documents({"api_key_id": k["_id"]}),
        }
        for k in coll(API_KEYS).find({"user_id": {"$in": list(owner_users)}})
        .sort("_id", DESCENDING)
    ]

    flagged = [
        {
            "bot_type": f["bot_type"],
            "query_text": f["query_text"],
            "top_match_score": f.get("top_match_score"),
            "session_id": f.get("session_id"),
            "timestamp": f["timestamp"],
        }
        for f in coll(UNMATCHED)
        .find({"flagged_injection": 1, "timestamp": {"$gte": _since(days)}})
        .sort("_id", DESCENDING)
        .limit(100)
    ]

    return {
        "window_days": days,
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": summary,
        "entries": entries,
        "flagged_inputs": flagged,
        "owner_keys": owner_keys,
    }


# ---------------------------------------------------------------------------
# AI usage, by subscription
# ---------------------------------------------------------------------------

def ai_usage(days: int = 30) -> dict[str, Any]:
    """
    What the AI side of the product is actually doing, split by what people pay.

    Two different things get called "AI usage" here and they are kept apart on
    purpose:

    * **retrieval** — every ``/v1/ask``: embedding the question and matching it.
      Every customer request is this, and it is what credits are charged for.
    * **grounded rewording** — the optional local model on top. Off by default,
      opted into per bot, and the only part that generates prose.

    Grouping by plan is the answer to "are the paying accounts the ones using
    it?", which is the question that decides whether the pricing is right.
    """
    since = _since(days)
    date_since = _date_since(days)

    users = list(coll(USERS).find())
    customers = {c["email"]: c for c in coll(billing_db.CUSTOMERS).find()}
    subs = _current_subscriptions()
    bots = {b["user_id"]: b for b in coll(BOTS).find()}

    requests_by_user = _count_by(REQUEST_LOG, {"timestamp": {"$gte": since}}, "user_id")
    credits_by_user = _sum_by(
        DAILY_USAGE, {"usage_date": {"$gte": date_since}}, "user_id", "credits_used"
    )

    by_plan: dict[str, dict[str, Any]] = {}
    for user in users:
        customer = customers.get(user["email"])
        sub = subs.get(customer["_id"]) if customer else None
        bot = bots.get(user["_id"])

        key = (sub or {}).get("plan_id") or (
            "owner" if user.get("role") == ROLE_OWNER else "none"
        )
        bucket = by_plan.setdefault(key, {
            "plan_id": key, "accounts": 0, "entitled_accounts": 0,
            "requests": 0, "credits": 0, "bots_with_rewording": 0,
        })
        bucket["accounts"] += 1
        bucket["entitled_accounts"] += 1 if _is_live(sub) else 0
        bucket["requests"] += requests_by_user.get(user["_id"], 0)
        bucket["credits"] += credits_by_user.get(user["_id"], 0)
        bucket["bots_with_rewording"] += 1 if bot and bot.get("llm_enabled") else 0

    for bucket in by_plan.values():
        bucket["credits_per_account"] = (
            round(bucket["credits"] / bucket["accounts"], 1) if bucket["accounts"] else 0.0
        )

    all_bots = list(coll(BOTS).find())
    unanswered = list(coll(UNMATCHED).find({"timestamp": {"$gte": since}}))

    gaps: dict[str, dict[str, Any]] = {}
    for row in unanswered:
        if row.get("flagged_injection"):
            continue
        key = row["query_text"].strip().lower()
        bucket = gaps.setdefault(key, {
            "query_text": row["query_text"], "bot_type": row["bot_type"],
            "times_asked": 0, "best_score": 0.0,
        })
        bucket["times_asked"] += 1
        bucket["best_score"] = round(
            max(bucket["best_score"], row.get("top_match_score") or 0), 3
        )
    top_unanswered = sorted(
        gaps.values(), key=lambda g: g["times_asked"], reverse=True
    )[:25]

    by_template: dict[str, dict[str, Any]] = {}
    for bot in all_bots:
        bucket = by_template.setdefault(bot["template_id"], {
            "template_id": bot["template_id"], "bots": 0, "ready": 0,
            "rewording_on": 0, "indexed_rows": 0, "requests": 0,
        })
        bucket["bots"] += 1
        bucket["ready"] += 1 if bot.get("status") == "ready" else 0
        bucket["rewording_on"] += 1 if bot.get("llm_enabled") else 0
        bucket["indexed_rows"] += bot.get("doc_count", 0)
        bucket["requests"] += requests_by_user.get(bot["user_id"], 0)

    return {
        "window_days": days,
        "by_plan": sorted(by_plan.values(), key=lambda p: p["requests"], reverse=True),
        "rewording": {
            "bots": len(all_bots),
            "enabled": sum(1 for b in all_bots if b.get("llm_enabled")),
            "enabled_and_ready": sum(
                1 for b in all_bots if b.get("llm_enabled") and b.get("status") == "ready"
            ),
        },
        "retrieval": {
            "unanswered": len(unanswered),
            "bots_affected": len({u["bot_type"] for u in unanswered}),
            "flagged_inputs": sum(1 for u in unanswered if u.get("flagged_injection")),
        },
        "top_unanswered": top_unanswered,
        "by_template": sorted(by_template.values(), key=lambda t: t["bots"], reverse=True),
    }


# ---------------------------------------------------------------------------
# Template adoption (used by the admin router's template screen)
# ---------------------------------------------------------------------------

def template_adoption() -> dict[str, dict[str, int]]:
    """How many bots each template carries, and how many are live."""
    adoption: dict[str, dict[str, int]] = {}
    for bot in coll(BOTS).find():
        bucket = adoption.setdefault(
            bot["template_id"], {"bots": 0, "ready": 0, "rewording_on": 0}
        )
        bucket["bots"] += 1
        bucket["ready"] += 1 if bot.get("status") == "ready" else 0
        bucket["rewording_on"] += 1 if bot.get("llm_enabled") else 0
    return adoption


def set_account_active(customer_id: Any, user_id: Any, is_active: bool) -> None:
    """
    Enable or disable both halves of an account.

    Lives here rather than in the router because it is the one admin write that
    spans two collections, and doing half of it leaves someone who cannot sign
    in but whose bot is still answering.
    """
    flag = 1 if is_active else 0
    coll(billing_db.CUSTOMERS).update_one(
        {"_id": object_id(customer_id)}, {"$set": {"is_active": flag}}
    )
    if user_id:
        coll(USERS).update_one(
            {"_id": object_id(user_id)}, {"$set": {"is_active": flag}}
        )
