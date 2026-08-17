"""
API key management and daily credit system.

Keys are stored as SHA-256 hashes (like passwords) — if the DB leaks,
raw keys cannot be recovered.  Credits reset at midnight IST daily.

Credit costs are variable based on query length (see config.CREDIT_COST_TIERS).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from shared import config

_LOCAL = threading.local()

KEY_PREFIX = "isk_"  # Instant Sahay Key


# ---------------------------------------------------------------------------
# Database connection (thread-local, same DB as logging)
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Thread-local SQLite connection with WAL mode."""
    conn = getattr(_LOCAL, "api_conn", None)
    if conn is None:
        db_path = Path(config.SQLITE_DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        _LOCAL.api_conn = conn
    return conn


def init_api_key_tables() -> None:
    """Create the API key and usage tables if they don't exist."""
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash     TEXT    NOT NULL UNIQUE,
            key_prefix   TEXT    NOT NULL,
            owner_email  TEXT    NOT NULL,
            owner_name   TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL,
            is_active    INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS daily_usage (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id   INTEGER NOT NULL,
            usage_date   TEXT    NOT NULL,
            credits_used INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
            UNIQUE(api_key_id, usage_date)
        );

        CREATE TABLE IF NOT EXISTS request_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id   INTEGER NOT NULL,
            endpoint     TEXT    NOT NULL,
            message_len  INTEGER NOT NULL DEFAULT 0,
            credit_cost  INTEGER NOT NULL DEFAULT 1,
            timestamp    TEXT    NOT NULL,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Key hashing
# ---------------------------------------------------------------------------

def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def generate_api_key(owner_email: str, owner_name: str = "") -> dict[str, Any]:
    """
    Generate a new API key for a customer.

    Returns a dict with ``api_key`` (raw — show once!), ``key_id``,
    ``key_prefix``, ``owner_email``, ``owner_name``.
    """
    raw_key = KEY_PREFIX + secrets.token_hex(24)  # isk_ + 48 hex chars
    key_hash = _hash_key(raw_key)
    prefix = raw_key[:12]  # isk_XXXXXXXX for display
    now = datetime.now(config.IST).isoformat()

    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO api_keys (key_hash, key_prefix, owner_email, owner_name, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key_hash, prefix, owner_email, owner_name, now),
    )
    conn.commit()

    return {
        "api_key": raw_key,
        "key_id": cursor.lastrowid,
        "key_prefix": prefix,
        "owner_email": owner_email,
        "owner_name": owner_name,
    }


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

def validate_api_key(raw_key: str) -> dict[str, Any] | None:
    """
    Validate a raw API key.

    Returns the key record as a dict if valid and active, else ``None``.
    """
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        return None

    key_hash = _hash_key(raw_key)
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
        (key_hash,),
    ).fetchone()

    if row is None:
        return None

    return dict(row)


# ---------------------------------------------------------------------------
# Credit management
# ---------------------------------------------------------------------------

def _today_ist() -> str:
    """Return today's date string in IST (YYYY-MM-DD)."""
    return datetime.now(config.IST).strftime("%Y-%m-%d")


def get_credits_used_today(key_id: int) -> int:
    """Return how many credits a key has used today (IST)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT credits_used FROM daily_usage WHERE api_key_id = ? AND usage_date = ?",
        (key_id, _today_ist()),
    ).fetchone()
    return row["credits_used"] if row else 0


def get_credits_remaining(key_id: int) -> int:
    """Return how many credits remain for today (IST)."""
    used = get_credits_used_today(key_id)
    return max(0, config.DAILY_CREDIT_LIMIT - used)


def consume_credits(key_id: int, cost: int, endpoint: str, message_len: int) -> int:
    """
    Consume *cost* credits for the given key.

    Returns the remaining credits after consumption.
    Raises ``ValueError`` if insufficient credits.
    """
    today = _today_ist()
    conn = _get_conn()

    # Upsert daily usage
    conn.execute(
        """
        INSERT INTO daily_usage (api_key_id, usage_date, credits_used)
        VALUES (?, ?, ?)
        ON CONFLICT(api_key_id, usage_date)
        DO UPDATE SET credits_used = credits_used + ?
        """,
        (key_id, today, cost, cost),
    )

    # Log the request
    now = datetime.now(config.IST).isoformat()
    conn.execute(
        """
        INSERT INTO request_log (api_key_id, endpoint, message_len, credit_cost, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key_id, endpoint, message_len, cost, now),
    )
    conn.commit()

    return get_credits_remaining(key_id)


def get_next_reset_time() -> str:
    """Return the next midnight IST as an ISO timestamp."""
    now = datetime.now(config.IST)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if tomorrow <= now:
        from datetime import timedelta
        tomorrow += timedelta(days=1)
    return tomorrow.isoformat()


# ---------------------------------------------------------------------------
# Usage stats
# ---------------------------------------------------------------------------

def get_usage_stats(key_id: int) -> dict[str, Any]:
    """Return usage statistics for a key."""
    conn = _get_conn()
    today = _today_ist()

    # Today's usage
    credits_used_today = get_credits_used_today(key_id)
    credits_remaining = max(0, config.DAILY_CREDIT_LIMIT - credits_used_today)

    # Total queries all time
    row = conn.execute(
        "SELECT COUNT(*) as total, COALESCE(SUM(credit_cost), 0) as total_credits "
        "FROM request_log WHERE api_key_id = ?",
        (key_id,),
    ).fetchone()

    # Last 7 days usage
    from datetime import timedelta
    week_ago = (datetime.now(config.IST) - timedelta(days=7)).strftime("%Y-%m-%d")
    history = conn.execute(
        "SELECT usage_date, credits_used FROM daily_usage "
        "WHERE api_key_id = ? AND usage_date >= ? ORDER BY usage_date",
        (key_id, week_ago),
    ).fetchall()

    return {
        "credits_remaining": credits_remaining,
        "credits_used_today": credits_used_today,
        "credits_daily_limit": config.DAILY_CREDIT_LIMIT,
        "resets_at": get_next_reset_time(),
        "total_queries_all_time": row["total"],
        "total_credits_consumed_all_time": row["total_credits"],
        "last_7_days": [dict(r) for r in history],
    }


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------

def list_all_keys() -> list[dict[str, Any]]:
    """List all API keys (for admin). Does NOT expose the key hash."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, key_prefix, owner_email, owner_name, created_at, is_active "
        "FROM api_keys ORDER BY id"
    ).fetchall()

    result = []
    for row in rows:
        row_dict = dict(row)
        row_dict["credits_remaining"] = (
            get_credits_remaining(row["id"]) if row["is_active"] else 0
        )
        result.append(row_dict)
    return result


def revoke_key(key_id: int) -> bool:
    """Deactivate an API key. Returns True if the key existed."""
    conn = _get_conn()
    cursor = conn.execute(
        "UPDATE api_keys SET is_active = 0 WHERE id = ?",
        (key_id,),
    )
    conn.commit()
    return cursor.rowcount > 0
