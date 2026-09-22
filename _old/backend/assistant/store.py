"""
Conversation storage for the dashboard assistant.

Threads and messages, in the same MongoDB database as everything else. Two
things here are worth knowing.

**Every read is scoped by customer_id.** Not "the caller should pass the right
id" — the queries take it and filter on it, so there is no accessor that can
return someone else's conversation by being called carelessly.

**History is capped when it is read, not when it is written.** The full thread
is kept for the person to scroll; only the last N turns are handed to the
model. Those are different jobs and a single limit would do one of them badly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from backend.shared import config
from backend.shared.mongo import (
    coll,
    document,
    documents,
    object_id,
    register_indexes,
    to_object_id,
)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

THREADS = "assistant_threads"
MESSAGES = "assistant_messages"

register_indexes(THREADS, [
    ([("customer_id", ASCENDING), ("updated_at", DESCENDING)], {"name": "by_customer"}),
])
register_indexes(MESSAGES, [
    ([("thread_id", ASCENDING), ("_id", ASCENDING)], {"name": "by_thread"}),
    # count_messages_today filters on all three at once.
    ([("customer_id", ASCENDING), ("role", ASCENDING), ("created_at", ASCENDING)],
     {"name": "by_customer_role_day"}),
])


def _now() -> str:
    return datetime.now(config.IST).isoformat()


def init_assistant_tables() -> None:
    """Kept as an entry point for startup; MongoDB needs no schema built."""
    return None


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

def create_thread(customer_id: Any, title: str = "New chat") -> dict[str, Any]:
    now = _now()
    doc = {
        "customer_id": to_object_id(customer_id),
        "title": title[:120] or "New chat",
        "created_at": now,
        "updated_at": now,
    }
    result = coll(THREADS).insert_one(doc)
    return document({**doc, "_id": result.inserted_id})  # type: ignore[return-value]


def get_thread(customer_id: Any, thread_id: Any) -> dict[str, Any] | None:
    """One thread, or None. Scoped — another customer's id simply doesn't match."""
    thread_oid = object_id(thread_id)
    customer_oid = object_id(customer_id)
    if thread_oid is None or customer_oid is None:
        return None
    return document(
        coll(THREADS).find_one({"_id": thread_oid, "customer_id": customer_oid})
    )


def list_threads(customer_id: Any, limit: int = 50) -> list[dict[str, Any]]:
    oid = object_id(customer_id)
    if oid is None:
        return []
    return documents(
        coll(THREADS)
        .find({"customer_id": oid})
        .sort("updated_at", DESCENDING)
        .limit(limit)
    )


def rename_thread(customer_id: Any, thread_id: Any, title: str) -> dict[str, Any] | None:
    thread_oid = object_id(thread_id)
    customer_oid = object_id(customer_id)
    if thread_oid is None or customer_oid is None:
        return None

    # Scoped in the update itself rather than checked first, so there is no
    # window in which the ownership test and the write disagree.
    return document(coll(THREADS).find_one_and_update(
        {"_id": thread_oid, "customer_id": customer_oid},
        {"$set": {"title": title[:120] or "New chat", "updated_at": _now()}},
        return_document=ReturnDocument.AFTER,
    ))


def delete_thread(customer_id: Any, thread_id: Any) -> bool:
    thread_oid = object_id(thread_id)
    customer_oid = object_id(customer_id)
    if thread_oid is None or customer_oid is None:
        return False

    deleted = coll(THREADS).delete_one(
        {"_id": thread_oid, "customer_id": customer_oid}
    ).deleted_count

    # Messages are removed explicitly. MongoDB has no cascade, and a thread
    # whose messages outlived it would be invisible but still stored.
    if deleted:
        coll(MESSAGES).delete_many({"thread_id": thread_oid})
    return bool(deleted)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(
    customer_id: Any, thread_id: Any, role: str, content: str
) -> dict[str, Any] | None:
    """
    Append a message, if the thread belongs to *customer_id*.

    Returns None when it doesn't, rather than writing into someone else's
    conversation.
    """
    if get_thread(customer_id, thread_id) is None:
        return None

    now = _now()
    doc = {
        "thread_id": to_object_id(thread_id),
        # Denormalised so "how many did this person send today?" is one indexed
        # read instead of a join. Messages are never reassigned to another
        # customer, so the copy cannot drift from the thread it belongs to.
        "customer_id": to_object_id(customer_id),
        "role": role,
        "content": content,
        "created_at": now,
    }
    result = coll(MESSAGES).insert_one(doc)
    coll(THREADS).update_one(
        {"_id": to_object_id(thread_id)}, {"$set": {"updated_at": now}}
    )
    return document({**doc, "_id": result.inserted_id})


def list_messages(customer_id: Any, thread_id: Any) -> list[dict[str, Any]]:
    """The whole conversation, for display."""
    if get_thread(customer_id, thread_id) is None:
        return []
    return documents(
        coll(MESSAGES).find({"thread_id": object_id(thread_id)}).sort("_id", ASCENDING)
    )


def history_for_model(customer_id: Any, thread_id: Any) -> list[dict[str, str]]:
    """
    The tail of the conversation, shaped for Ollama's ``messages`` array.

    Capped at ``ASSISTANT_HISTORY_TURNS`` because every extra turn is more
    prompt for a CPU to re-read on each reply, and a 7B model loses the thread
    long before it runs out of context window.
    """
    messages = list_messages(customer_id, thread_id)
    tail = messages[-config.ASSISTANT_HISTORY_TURNS:]
    return [{"role": message["role"], "content": message["content"]} for message in tail]


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

def count_messages_today(customer_id: Any) -> int:
    """
    How many questions this customer has asked today (IST).

    Generation on two ARM cores is the scarcest thing this box has, so it is
    rationed per person rather than left open.
    """
    oid = object_id(customer_id)
    if oid is None:
        return 0

    today = datetime.now(config.IST).date().isoformat()
    return coll(MESSAGES).count_documents({
        "customer_id": oid,
        "role": ROLE_USER,
        # created_at is an ISO timestamp, so "today" is a prefix range. Stated
        # as a range rather than a regex so the index can serve it.
        "created_at": {"$gte": today, "$lt": today + "￿"},
    })
