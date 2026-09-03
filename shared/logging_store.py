"""
SQLite logging for unmatched / near-matched queries.

Every NEAR_MATCH and NO_MATCH response writes a row here — this is the
gap-analysis list for deciding what to add to the FAQ sheet next.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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
    conn.executescript(
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
        );

        -- Every gap-list read filters by bot and date. Without this the query
        -- degrades into a full scan as the table grows across all tenants.
        CREATE INDEX IF NOT EXISTS idx_unmatched_bot_time
            ON unmatched_queries(bot_type, timestamp);
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
    """
    Read every row, across every bot.

    Tests and admin debugging only — this is deliberately **not** scoped to a
    tenant. Never put it behind a customer-facing endpoint: the rows are other
    people's end-users' questions. Use ``get_gap_summary`` for that.
    """
    conn = _get_conn()
    cursor = conn.execute(
        "SELECT id, bot_type, query_text, top_match_score, "
        "top_match_question, flagged_injection, session_id, timestamp "
        "FROM unmatched_queries ORDER BY id"
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_gap_summary(
    bot_label: str, *, days: int = 30, limit: int = 50
) -> list[dict]:
    """
    What one bot failed to answer, grouped and ranked by how often it was asked.

    Scoped to *bot_label* — the caller must pass the label belonging to the
    signed-in account and nothing else.

    Grouped rather than raw: the same question asked forty times is one line
    saying "forty", not forty lines. A raw feed of every miss is unreadable and
    nobody acts on it, which is the whole point of collecting this.

    ``best_score`` is how close the bot got. High means the answer is nearly
    there and probably just needs an alternate phrasing; low means the sheet
    doesn't cover it at all.

    Injection attempts are excluded — they are attacks, not gaps, and putting
    them in a customer's to-do list is noise.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    conn = _get_conn()
    cursor = conn.execute(
        """
        SELECT
            MAX(query_text)                   AS question,
            COUNT(*)                          AS times_asked,
            MAX(timestamp)                    AS last_asked,
            MAX(COALESCE(top_match_score, 0)) AS best_score
        FROM unmatched_queries
        WHERE bot_type = ?
          AND timestamp >= ?
          AND flagged_injection = 0
        GROUP BY LOWER(TRIM(query_text))
        ORDER BY times_asked DESC, last_asked DESC
        LIMIT ?
        """,
        (bot_label, since, limit),
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def count_flagged_inputs(bot_label: str, *, days: int = 30) -> int:
    """How many inputs to this bot tripped the injection detector."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    row = _get_conn().execute(
        """
        SELECT COUNT(*) FROM unmatched_queries
        WHERE bot_type = ? AND timestamp >= ? AND flagged_injection = 1
        """,
        (bot_label, since),
    ).fetchone()
    return row[0]
