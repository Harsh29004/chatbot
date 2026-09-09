"""
Storage for support conversations.

One conversation per customer, created on first use. Two read markers per
conversation — one for each side — because "unread" means something different
depending on who is asking, and a single ``last_read_at`` would have staff and
customer overwriting each other's badge.

Read position is a **message id**, not a timestamp. Wall-clock time is the
wrong tool: on Windows two consecutive now() calls routinely return the same
value, so a message arriving in the same tick as a read marker would compare as
already-read and never show as unread. ObjectIds keep that property — they
carry a timestamp and a counter and compare in generation order — so the marker
survived the move to MongoDB unchanged.
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

SENDER_CUSTOMER = "customer"
SENDER_STAFF = "staff"

CONVERSATIONS = "support_conversations"
MESSAGES = "support_messages"

register_indexes(CONVERSATIONS, [
    ([("customer_id", ASCENDING)], {"unique": True, "name": "uniq_customer"}),
    ([("last_message_at", DESCENDING)], {"name": "by_recent"}),
])
register_indexes(MESSAGES, [
    ([("conversation_id", ASCENDING), ("_id", ASCENDING)], {"name": "by_conversation"}),
])

# Which marker belongs to which side. Keyed rather than branched at each call
# site, so a new participant type is one entry instead of five if-statements.
_READ_MARKER = {
    SENDER_CUSTOMER: "customer_read_msg_id",
    SENDER_STAFF: "staff_read_msg_id",
}


def _now() -> str:
    return datetime.now(config.IST).isoformat()


def init_support_tables() -> None:
    """Kept as an entry point for startup; MongoDB needs no schema built."""
    return None


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def get_or_create_conversation(customer: dict[str, Any]) -> dict[str, Any]:
    """
    This customer's conversation, created if they've never written before.

    The email and name are copied onto the document rather than joined at read
    time. Staff need to know who they are talking to, and the inbox listing
    should not have to reach into the billing collections to render a name.
    """
    now = _now()
    doc = coll(CONVERSATIONS).find_one_and_update(
        {"customer_id": to_object_id(customer["id"])},
        {"$setOnInsert": {
            "customer_id": to_object_id(customer["id"]),
            "customer_email": customer.get("email") or "",
            "customer_name": customer.get("name") or "",
            "last_message_at": now,
            "last_message_preview": "",
            "customer_read_msg_id": None,
            "staff_read_msg_id": None,
            "created_at": now,
        }},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return document(doc)  # type: ignore[return-value]


def get_conversation(conversation_id: Any) -> dict[str, Any] | None:
    """Staff-side lookup by conversation id. Not reachable from a customer route."""
    oid = object_id(conversation_id)
    return document(coll(CONVERSATIONS).find_one({"_id": oid})) if oid else None


def list_conversations(limit: int = 200) -> list[dict[str, Any]]:
    """
    Every conversation, most recently active first, with staff's unread count.

    Cross-customer by definition — this is the staff inbox, and it is the only
    function here that is. It sits behind the admin key.
    """
    conversations = documents(
        coll(CONVERSATIONS)
        .find()
        # _id breaks the tie when two conversations share a timestamp, which
        # coarse clocks make common rather than exotic.
        .sort([("last_message_at", DESCENDING), ("_id", DESCENDING)])
        .limit(limit)
    )

    for conversation in conversations:
        conversation["unread"] = _unread_after(
            conversation["id"], SENDER_CUSTOMER, conversation.get("staff_read_msg_id")
        )
    return conversations


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(conversation_id: Any, sender: str, body: str) -> dict[str, Any]:
    """
    Append a message and roll the conversation's summary forward.

    The sender's own read marker moves too: you have obviously read what you
    just wrote, and without this the author's own message counts as unread to
    them.
    """
    conversation_oid = to_object_id(conversation_id)
    now = _now()

    doc = {
        "conversation_id": conversation_oid,
        "sender": sender,
        "body": body,
        "created_at": now,
    }
    result = coll(MESSAGES).insert_one(doc)

    coll(CONVERSATIONS).update_one(
        {"_id": conversation_oid},
        {"$set": {
            "last_message_at": now,
            "last_message_preview": body[:140],
            _READ_MARKER.get(sender, "staff_read_msg_id"): result.inserted_id,
        }},
    )
    return document({**doc, "_id": result.inserted_id})  # type: ignore[return-value]


def list_messages(conversation_id: Any, after_id: Any = None) -> list[dict[str, Any]]:
    """
    Messages in a conversation, optionally only those newer than *after_id*.

    ``after_id`` is what makes polling cheap: the page asks for what it hasn't
    seen rather than re-downloading the whole history every few seconds. An
    absent or unparseable marker means "from the beginning", which is the right
    reading of a client that has not seen anything yet.
    """
    oid = object_id(conversation_id)
    if oid is None:
        return []

    query: dict[str, Any] = {"conversation_id": oid}
    after = object_id(after_id)
    if after is not None:
        query["_id"] = {"$gt": after}

    return documents(coll(MESSAGES).find(query).sort("_id", ASCENDING))


def mark_read(conversation_id: Any, reader: str) -> None:
    """Move one side's read marker to the newest message in the conversation."""
    oid = object_id(conversation_id)
    if oid is None:
        return

    newest = coll(MESSAGES).find_one(
        {"conversation_id": oid}, sort=[("_id", DESCENDING)], projection={"_id": 1}
    )
    coll(CONVERSATIONS).update_one(
        {"_id": oid},
        {"$set": {
            _READ_MARKER.get(reader, "staff_read_msg_id"):
                newest["_id"] if newest else None
        }},
    )


def _unread_after(conversation_id: Any, sender: str, marker: Any) -> int:
    """Messages from *sender* in this conversation newer than *marker*."""
    oid = object_id(conversation_id)
    if oid is None:
        return 0

    query: dict[str, Any] = {"conversation_id": oid, "sender": sender}
    after = object_id(marker)
    if after is not None:
        query["_id"] = {"$gt": after}
    return coll(MESSAGES).count_documents(query)


def unread_count(conversation_id: Any, reader: str) -> int:
    """How many messages *from the other side* this reader hasn't seen."""
    conversation = get_conversation(conversation_id)
    if conversation is None:
        return 0

    other = SENDER_STAFF if reader == SENDER_CUSTOMER else SENDER_CUSTOMER
    marker = conversation.get(_READ_MARKER.get(reader, "staff_read_msg_id"))
    return _unread_after(conversation_id, other, marker)


def total_unread_for_staff() -> int:
    """Everything waiting on a reply, across all customers — the inbox badge."""
    return sum(
        _unread_after(c["_id"], SENDER_CUSTOMER, c.get("staff_read_msg_id"))
        for c in coll(CONVERSATIONS).find({}, {"_id": 1, "staff_read_msg_id": 1})
    )
