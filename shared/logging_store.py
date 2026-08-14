"""
SQLite logging for unmatched / near-matched queries.

Every NEAR_MATCH and NO_MATCH response writes a row here — this is the
gap-analysis list for deciding what to add to the FAQ sheet next.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from shared import config

_LOCAL = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Thread-local SQLite connection with WAL mode."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        db_path = Path(config.SQLITE_DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        _LOCAL.conn = conn
    return conn


def init_db() -> None:
    """Create the ``unmatched_queries`` table if it doesn't exist."""
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS unmatched_queries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_type        TEXT    NOT NULL,
            query_text      TEXT    NOT NULL,
            top_match_score REAL,
            top_match_question TEXT,
            flagged_injection INTEGER NOT NULL DEFAULT 0,
            session_id      TEXT,
            timestamp       TEXT    NOT NULL
        )
        """
    )
    conn.commit()


def log_query(
    *,
    bot_type: str,
    query_text: str,
    top_match_score: float | None = None,
    top_match_question: str | None = None,
    flagged_injection: bool = False,
    session_id: str | None = None,
) -> None:
    """Insert one row into the ``unmatched_queries`` table."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO unmatched_queries
            (bot_type, query_text, top_match_score, top_match_question,
             flagged_injection, session_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bot_type,
            query_text,
            top_match_score,
            top_match_question,
            int(flagged_injection),
            session_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def get_all_logs() -> list[dict]:
    """Read all rows — utility for tests and admin debugging."""
    conn = _get_conn()
    cursor = conn.execute(
        "SELECT id, bot_type, query_text, top_match_score, "
        "top_match_question, flagged_injection, session_id, timestamp "
        "FROM unmatched_queries ORDER BY id"
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
