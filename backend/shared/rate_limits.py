"""
Sliding-window counters, stored in MongoDB.

Login lockouts, the signup cap and the admin sign-in lockout all need the same
thing: "how many times has this key done this in the last N seconds?". They
used to keep that in a dict inside the process, which lost every count on
restart and gave each worker its own separate limit. Here the events live in
one collection that every worker shares.

Each event carries an ``expires_at``, and a TTL index on it lets MongoDB delete
old events on its own, so the collection never grows without bound. The TTL
sweep runs about once a minute, which is why reads still filter by time rather
than trusting that expired events are already gone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.shared.mongo import coll, register_indexes

RATE_EVENTS = "rate_events"

register_indexes(
    RATE_EVENTS,
    [
        ([("bucket", 1), ("key", 1), ("at", 1)], {"name": "bucket_key_at"}),
        ([("expires_at", 1)], {"name": "expires_at_ttl", "expireAfterSeconds": 0}),
    ],
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def count(bucket: str, key: str, window_seconds: float) -> int:
    """Events for ``key`` in ``bucket`` within the last ``window_seconds``."""
    since = _now() - timedelta(seconds=window_seconds)
    return coll(RATE_EVENTS).count_documents(
        {"bucket": bucket, "key": key, "at": {"$gte": since}}
    )


def record(bucket: str, key: str, window_seconds: float) -> None:
    """Record one event, kept for as long as the window it counts toward."""
    now = _now()
    coll(RATE_EVENTS).insert_one(
        {
            "bucket": bucket,
            "key": key,
            "at": now,
            "expires_at": now + timedelta(seconds=window_seconds),
        }
    )


def clear(bucket: str, key: str) -> None:
    """Forget every event for ``key`` — e.g. after a successful sign-in."""
    coll(RATE_EVENTS).delete_many({"bucket": bucket, "key": key})
