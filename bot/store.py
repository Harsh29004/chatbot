"""
Persistence for customer bots.

One account gets one bot. That is a product decision, not a schema limit — the
documents are keyed by ``user_id`` so a second bot per account is a migration
away, but "pick a template, upload a sheet, take the key" is the whole promise
and a bot picker would only get in the way of it.

The bot's vector collection is named from its id, so two customers can never
share retrieval space even if their sheets are identical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ASCENDING, ReturnDocument

from backend.shared import config
from backend.shared.mongo import (
    coll,
    document,
    documents,
    object_id,
    register_indexes,
    to_object_id,
)

from bot.templates import DEFAULT_TEMPLATE_ID

BOTS = "bots"

STATUS_DRAFT = "draft"      # template chosen, no sheet yet — cannot answer
STATUS_READY = "ready"      # sheet indexed, answering

register_indexes(BOTS, [
    # One bot per account, enforced by the database rather than by the
    # get-or-create that reads first. Two requests arriving together for a new
    # customer would otherwise both decide there is no bot yet.
    ([("user_id", ASCENDING)], {"unique": True, "name": "uniq_user"}),
])


def _now() -> str:
    return datetime.now(config.IST).isoformat()


def init_bot_tables() -> None:
    """Kept as an entry point for startup; MongoDB needs no schema built."""
    return None


def collection_name_for(bot_id: Any) -> str:
    """Every bot gets its own Chroma collection — no shared retrieval space."""
    return f"bot_{bot_id}_index"


def _with_collection(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    doc["collection_name"] = collection_name_for(doc["id"])
    return doc


def get_bot(user_id: Any) -> dict[str, Any] | None:
    oid = object_id(user_id)
    if oid is None:
        return None
    return _with_collection(document(coll(BOTS).find_one({"user_id": oid})))


def get_or_create_bot(user_id: Any, template_id: str = DEFAULT_TEMPLATE_ID) -> dict[str, Any]:
    """
    This account's bot, created on first use.

    One upsert rather than read-then-insert: the unique index on ``user_id``
    means a race cannot produce two bots for one account, and two bots would
    mean two Chroma collections with only one of them ever read.
    """
    now = _now()
    doc = coll(BOTS).find_one_and_update(
        {"user_id": to_object_id(user_id)},
        {"$setOnInsert": {
            "user_id": to_object_id(user_id),
            "name": "",
            "template_id": template_id,
            "status": STATUS_DRAFT,
            "doc_count": 0,
            "sheet_filename": "",
            "sheet_uploaded_at": None,
            "categories": "",
            "llm_enabled": 0,
            "created_at": now,
            "updated_at": now,
        }},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _with_collection(document(doc))  # type: ignore[return-value]


def _update(user_id: Any, changes: dict[str, Any]) -> dict[str, Any]:
    doc = coll(BOTS).find_one_and_update(
        {"user_id": to_object_id(user_id)},
        {"$set": {**changes, "updated_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return _with_collection(document(doc))  # type: ignore[return-value]


def set_template(user_id: Any, template_id: str, name: str | None = None) -> dict[str, Any]:
    """
    Switch the bot's template.

    The indexed sheet is left alone — the template governs scope and wording,
    the sheet governs facts, and changing one should not destroy the other.
    """
    get_or_create_bot(user_id, template_id)
    changes: dict[str, Any] = {"template_id": template_id}
    if name is not None:
        changes["name"] = name
    return _update(user_id, changes)


def list_all_bots() -> list[dict[str, Any]]:
    """
    Every bot on the platform.

    Cross-tenant, so it is **not** reachable from any customer-facing route —
    only the owner ops endpoints and the admin panel call this, behind their
    own key checks.
    """
    return [
        _with_collection(bot)  # type: ignore[misc]
        for bot in documents(coll(BOTS).find().sort("_id", ASCENDING))
    ]


def set_llm_enabled(user_id: Any, enabled: bool) -> dict[str, Any]:
    """
    Turn grounded rewording on or off for this account's bot.

    Bumps ``updated_at``, which is half of the compiled-graph cache key — so
    the next request rebuilds the graph with the new setting instead of serving
    a stale one.
    """
    get_or_create_bot(user_id)
    return _update(user_id, {"llm_enabled": 1 if enabled else 0})


def record_sheet(
    user_id: Any,
    *,
    filename: str,
    doc_count: int,
    categories: list[str],
) -> dict[str, Any]:
    """Mark the bot ready after a successful ingest."""
    return _update(user_id, {
        "status": STATUS_READY,
        "doc_count": doc_count,
        "sheet_filename": filename,
        "sheet_uploaded_at": _now(),
        "categories": ",".join(categories),
    })
