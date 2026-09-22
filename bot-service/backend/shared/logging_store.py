"""
The gap log: every NEAR_MATCH and NO_MATCH a bot produces.

This is the list a customer works from when deciding what to add to their FAQ
next, so it is grouped and ranked rather than served raw — the same question
asked forty times is one line saying "forty".

Stored in MongoDB, one document per miss, in ``unmatched_queries``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.shared.mongo import coll, documents, register_indexes

COLLECTION = "unmatched_queries"

# Every gap-list read filters by bot and date, so that pair is the index.
# Without it the query degrades into a collection scan as misses accumulate
# across every tenant.
register_indexes(COLLECTION, [
    ([("bot_type", 1), ("timestamp", -1)], {"name": "bot_time"}),
    ([("bot_type", 1), ("flagged_injection", 1), ("timestamp", -1)],
     {"name": "bot_flagged_time"}),
])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def init_db() -> None:
    """
    Kept as a no-op entry point.

    MongoDB creates a collection on first write, so there is no schema to
    build. The function stays because startup calls it alongside the other
    stores' initialisers, and because a database that needs no setup should
    say so out loud rather than by being absent.
    """
    return None


def log_query(
    *,
    bot_type: str,
    query_text: str,
    top_match_score: float | None = None,
    top_match_question: str | None = None,
    flagged_injection: bool = False,
    session_id: str | None = None,
) -> None:
    """Record one miss."""
    coll(COLLECTION).insert_one({
        "bot_type": bot_type,
        "query_text": query_text,
        "top_match_score": top_match_score,
        "top_match_question": top_match_question,
        # Stored as 0/1 rather than a boolean so the value reads the same way
        # in the admin aggregations that sum it.
        "flagged_injection": int(flagged_injection),
        "session_id": session_id,
        "timestamp": _now(),
    })


def get_all_logs() -> list[dict[str, Any]]:
    """
    Every row, across every bot.

    Tests and admin debugging only — this is deliberately **not** scoped to a
    tenant. Never put it behind a customer-facing endpoint: the rows are other
    people's end-users' questions. Use :func:`get_gap_summary` for that.
    """
    return documents(coll(COLLECTION).find().sort("_id", 1))


def get_gap_summary(bot_label: str, *, days: int = 30, limit: int = 50) -> list[dict[str, Any]]:
    """
    What one bot failed to answer, grouped and ranked by how often it was asked.

    Scoped to *bot_label* — the caller must pass the label belonging to the
    signed-in account and nothing else.

    ``best_score`` is how close the bot got: high means the answer is nearly
    there and probably needs an alternate phrasing, low means the sheet does
    not cover it at all.

    Injection attempts are excluded. They are attacks, not gaps, and putting
    them in a customer's to-do list is noise.
    """
    misses = coll(COLLECTION).find({
        "bot_type": bot_label,
        "timestamp": {"$gte": _since(days)},
        "flagged_injection": 0,
    })

    # Grouped on the normalised question, so "Do you deliver?" and
    # " do you deliver? " count as one gap, while the text displayed stays
    # whichever casing an actual person typed.
    gaps: dict[str, dict[str, Any]] = {}
    for miss in misses:
        key = miss["query_text"].strip().lower()
        gap = gaps.setdefault(key, {
            "question": miss["query_text"],
            "times_asked": 0,
            "last_asked": "",
            "best_score": 0.0,
        })
        gap["times_asked"] += 1
        gap["last_asked"] = max(gap["last_asked"], miss["timestamp"])
        gap["best_score"] = max(gap["best_score"], miss.get("top_match_score") or 0)
        gap["question"] = max(gap["question"], miss["query_text"])

    ranked = sorted(
        gaps.values(),
        key=lambda gap: (gap["times_asked"], gap["last_asked"]),
        reverse=True,
    )
    return ranked[:max(1, limit)]


def count_flagged_inputs(bot_label: str, *, days: int = 30) -> int:
    """How many inputs to this bot tripped the injection detector."""
    return coll(COLLECTION).count_documents({
        "bot_type": bot_label,
        "timestamp": {"$gte": _since(days)},
        "flagged_injection": 1,
    })
