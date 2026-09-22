"""
Client-side crash and error reports.

Why this exists at all
----------------------
Firebase Crashlytics does not ship a Web SDK — it is Android, iOS, Flutter and
Unity only. For a browser app the crash-reporting half has to be built, and
this is the server end of it: the page catches what it can (``window.onerror``,
unhandled promise rejections, the React error boundary), logs a GA4
``exception`` event so the rate shows up in the Firebase console, and posts the
stack trace here so there is somewhere to read *what actually broke*.

An ``exception`` event in Analytics tells you a crash happened and roughly
where. It does not carry a stack trace, and Google truncates its parameters.
That is the gap this fills.

What is deliberately not stored
-------------------------------
No cookies, no session token, no request bodies. The customer id is recorded
only when the browser was already signed in, because "which accounts hit this"
is the first question worth asking about a crash and an anonymous pile of
stack traces cannot answer it.

Everything is length-capped on write. This endpoint is necessarily
unauthenticated — a crash on the sign-in page still needs reporting — so it is
treated as hostile input throughout, and TTL-expired so a bad deploy cannot
fill the disk.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.shared import config
from backend.shared.mongo import coll, documents, register_indexes

COLLECTION = "client_errors"

# Field caps. A minified stack from a bundled SPA is long, so 8 KB rather than
# something tidier — truncating the frame that names the cause would defeat
# the point of collecting it at all.
MAX_MESSAGE = 500
MAX_STACK = 8000
MAX_URL = 500
MAX_UA = 300
MAX_COMPONENT_STACK = 4000
MAX_CONTEXT_KEYS = 20
MAX_CONTEXT_VALUE = 300

register_indexes(COLLECTION, [
    # The two reads that exist: newest-first overall, and newest-first for one
    # release when triaging whether a deploy caused it.
    ([("at", -1)], {"name": "by_time"}),
    ([("release", 1), ("at", -1)], {"name": "by_release_time"}),
    # Grouping identical crashes is the whole job of a crash reporter, and
    # fingerprint is what groups them.
    ([("fingerprint", 1), ("at", -1)], {"name": "by_fingerprint_time"}),
    # MongoDB expires these on its own. Without it one broken deploy writes
    # until the disk is full.
    ([("expires_at", 1)], {"name": "expires_ttl", "expireAfterSeconds": 0}),
])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clip(value: Any, limit: int) -> str:
    """Coerce to a bounded string. Anything unstringifiable becomes empty."""
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - a hostile __str__ must not crash logging
        return ""
    return text[:limit]


def _clean_context(context: Any) -> dict[str, str]:
    """
    Keep a bounded, flat, string-valued copy of whatever the page attached.

    Flattened rather than stored as-is because nested client-controlled
    documents are how a log collection grows keys nobody indexed, and because
    a dotted or ``$``-prefixed key from a browser is a write MongoDB rejects.
    """
    if not isinstance(context, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in list(context.items())[:MAX_CONTEXT_KEYS]:
        name = _clip(key, 40).replace(".", "_").replace("$", "_")
        if name:
            cleaned[name] = _clip(value, MAX_CONTEXT_VALUE)
    return cleaned


def record_error(
    *,
    message: str,
    stack: str = "",
    kind: str = "error",
    fingerprint: str = "",
    route: str = "",
    url: str = "",
    user_agent: str = "",
    release: str = "",
    component_stack: str = "",
    fatal: bool = False,
    customer_id: str | None = None,
    client_ip: str = "",
    context: Any = None,
) -> None:
    """
    Store one client-side error.

    Never raises. A failure to log a crash must not itself become a crash, and
    the caller is an endpoint whose entire job is to absorb bad news.
    """
    if not config.TELEMETRY_ENABLED:
        return

    now = _now()
    try:
        coll(COLLECTION).insert_one({
            "at": now,
            "kind": _clip(kind, 40),
            "message": _clip(message, MAX_MESSAGE),
            "stack": _clip(stack, MAX_STACK),
            "component_stack": _clip(component_stack, MAX_COMPONENT_STACK),
            # Computed by the browser so identical crashes group even when the
            # message carries a varying id or number.
            "fingerprint": _clip(fingerprint, 64),
            "route": _clip(route, MAX_URL),
            "url": _clip(url, MAX_URL),
            "user_agent": _clip(user_agent, MAX_UA),
            "release": _clip(release, 64),
            "fatal": bool(fatal),
            "customer_id": _clip(customer_id, 64) or None,
            "client_ip": _clip(client_ip, 64),
            "context": _clean_context(context),
            "expires_at": now + timedelta(days=config.TELEMETRY_RETENTION_DAYS),
        })
    except Exception:  # noqa: BLE001 - logging must never break the caller
        pass


def recent_errors(limit: int = 100, release: str = "") -> list[dict[str, Any]]:
    """The newest reports, optionally narrowed to one release."""
    query: dict[str, Any] = {}
    if release:
        query["release"] = release
    return documents(
        coll(COLLECTION).find(query).sort("at", -1).limit(max(1, min(limit, 500)))
    )


# How many recent reports one grouping pass will read. The collection is
# already bounded by the TTL and the per-IP rate limit, but "already bounded"
# is not the same as "small", and an admin page must not pull a month of a bad
# week into memory to draw a table of twenty rows.
_GROUPING_SCAN_LIMIT = 5000


def error_groups(days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
    """
    Distinct crashes, most frequent first — the view worth looking at.

    A raw feed of a thousand reports is one bug a thousand times. Grouping by
    fingerprint turns it back into "these six things are broken, and this one
    is hitting four hundred people".

    Grouped in Python rather than with an aggregation pipeline. The natural
    pipeline needs ``$addToSet`` with ``$setDifference`` to count distinct
    users, and mongomock — which the test suite runs on — implements neither,
    so that version is untestable and fails silently on a laptop while
    appearing to work in production. Reading at most
    ``_GROUPING_SCAN_LIMIT`` capped documents and counting them here is a few
    milliseconds either way, and it behaves identically in both places.
    """
    since = _now() - timedelta(days=days)

    cursor = (
        coll(COLLECTION)
        .find(
            {"at": {"$gte": since}},
            # Stacks are up to 8 KB each and only one per group is kept, but
            # they have to be read to keep that one. Everything not shown in
            # the grouped view is left on the server.
            {"component_stack": 0, "context": 0, "user_agent": 0, "client_ip": 0},
        )
        .sort("at", -1)
        .limit(_GROUPING_SCAN_LIMIT)
    )

    groups: dict[str, dict[str, Any]] = {}
    users: dict[str, set[str]] = {}

    for row in cursor:
        key = row.get("fingerprint") or row.get("message", "")[:64]
        at = row.get("at")

        group = groups.get(key)
        if group is None:
            # The cursor is newest-first, so the first document seen for a
            # fingerprint is the most recent one — which is the version of
            # the message and stack worth showing.
            group = groups[key] = {
                "fingerprint": key,
                "count": 0,
                "message": row.get("message", ""),
                "kind": row.get("kind", ""),
                "route": row.get("route", ""),
                "release": row.get("release", ""),
                "stack": row.get("stack", ""),
                "fatal": False,
                "first_seen": at,
                "last_seen": at,
            }
            users[key] = set()

        group["count"] += 1
        group["fatal"] = group["fatal"] or bool(row.get("fatal"))

        if at is not None:
            if group["first_seen"] is None or at < group["first_seen"]:
                group["first_seen"] = at
            if group["last_seen"] is None or at > group["last_seen"]:
                group["last_seen"] = at

        # Distinct accounts, not reports. One person reloading a broken page
        # twenty times is one affected user, and anonymous reports are not
        # users at all.
        customer_id = row.get("customer_id")
        if customer_id:
            users[key].add(customer_id)

    ranked = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
    ranked = ranked[: max(1, min(limit, 200))]

    for group in ranked:
        group["affected_users"] = len(users[group["fingerprint"]])
        for field in ("first_seen", "last_seen"):
            value = group.get(field)
            if isinstance(value, datetime):
                group[field] = value.replace(tzinfo=timezone.utc).isoformat()

    return ranked
