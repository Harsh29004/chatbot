"""
Persistence for customer bots.

One account gets one bot. That is a product decision, not a schema limit —
the table is keyed by ``user_id`` so a second bot per account is a migration
away, but "pick a template, upload a sheet, take the key" is the whole
promise and a bot picker would only get in the way of it.

The bot's vector collection is named from its id, so two customers can never
share retrieval space even if their sheets are identical.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from shared import config

from apps.bot_engine.templates import DEFAULT_TEMPLATE_ID

_LOCAL = threading.local()

STATUS_DRAFT = "draft"      # template chosen, no sheet yet — cannot answer
STATUS_READY = "ready"      # sheet indexed, answering


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_LOCAL, "bots_conn", None)
    if conn is None:
        db_path = Path(config.SQLITE_DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _LOCAL.bots_conn = conn
    return conn


def _now() -> str:
    return datetime.now(config.IST).isoformat()


def init_bot_tables() -> None:
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL UNIQUE,
            name              TEXT    NOT NULL DEFAULT '',
            template_id       TEXT    NOT NULL,
            status            TEXT    NOT NULL DEFAULT 'draft',
            doc_count         INTEGER NOT NULL DEFAULT 0,
            sheet_filename    TEXT    NOT NULL DEFAULT '',
            sheet_uploaded_at TEXT,
            categories        TEXT    NOT NULL DEFAULT '',
            created_at        TEXT    NOT NULL,
            updated_at        TEXT    NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_bots_user ON bots(user_id);
        """
    )
    conn.commit()


def collection_name_for(bot_id: int) -> str:
    """Every bot gets its own Chroma collection — no shared retrieval space."""
    return f"bot_{bot_id}_index"


def get_bot(user_id: int) -> dict[str, Any] | None:
    row = _get_conn().execute(
        "SELECT * FROM bots WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    bot = dict(row)
    bot["collection_name"] = collection_name_for(bot["id"])
    return bot


def get_or_create_bot(user_id: int, template_id: str = DEFAULT_TEMPLATE_ID) -> dict[str, Any]:
    existing = get_bot(user_id)
    if existing is not None:
        return existing

    conn = _get_conn()
    now = _now()
    conn.execute(
        """
        INSERT INTO bots (user_id, template_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, template_id, STATUS_DRAFT, now, now),
    )
    conn.commit()
    return get_bot(user_id)  # type: ignore[return-value]


def set_template(user_id: int, template_id: str, name: str | None = None) -> dict[str, Any]:
    """
    Switch the bot's template.

    The indexed sheet is left alone — the template governs scope and wording,
    the sheet governs facts, and changing one should not destroy the other.
    """
    get_or_create_bot(user_id, template_id)
    conn = _get_conn()
    if name is None:
        conn.execute(
            "UPDATE bots SET template_id = ?, updated_at = ? WHERE user_id = ?",
            (template_id, _now(), user_id),
        )
    else:
        conn.execute(
            "UPDATE bots SET template_id = ?, name = ?, updated_at = ? WHERE user_id = ?",
            (template_id, name, _now(), user_id),
        )
    conn.commit()
    return get_bot(user_id)  # type: ignore[return-value]


def record_sheet(
    user_id: int,
    *,
    filename: str,
    doc_count: int,
    categories: list[str],
) -> dict[str, Any]:
    """Mark the bot ready after a successful ingest."""
    conn = _get_conn()
    now = _now()
    conn.execute(
        """
        UPDATE bots
        SET status = ?, doc_count = ?, sheet_filename = ?, sheet_uploaded_at = ?,
            categories = ?, updated_at = ?
        WHERE user_id = ?
        """,
        (STATUS_READY, doc_count, filename, now, ",".join(categories), now, user_id),
    )
    conn.commit()
    return get_bot(user_id)  # type: ignore[return-value]
