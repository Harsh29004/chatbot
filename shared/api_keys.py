"""
API key management and daily credit system.

Keys are stored as SHA-256 hashes (like passwords) — if the DB leaks,
raw keys cannot be recovered.  Credits reset at midnight IST daily.

Credit costs are variable based on query length (see config.CREDIT_COST_TIERS).

Accounts vs. keys
------------------
Credits are pooled per **user** (identified by ``owner_email``), not per key.
A user can hold multiple API keys (e.g. one per app/environment) but they all
draw from the same daily credit pool — creating extra keys does not grant
extra credits.

Two roles exist:
- ``user``   — normal customer account, subject to ``daily_credit_limit``.
- ``owner``  — unlimited, no credit checks or deductions at all. Minted only
  via ``scripts/create_owner_key.py`` (never through the public API), for use
  by the product owners themselves.
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
OWNER_KEY_PREFIX = "iso_"  # Instant Sahay Owner key — visually distinct

ROLE_USER = "user"
ROLE_OWNER = "owner"


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
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _LOCAL.api_conn = conn
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_api_key_tables() -> None:
    """
    Create the user / API key / usage tables if they don't exist.

    If an older schema (pre-``users`` table, credits keyed by ``api_key_id``)
    is found *and it holds no rows*, it is dropped and recreated — safe
    because nothing of value can be lost. If it holds rows, migration is
    refused loudly rather than silently discarding data.
    """
    conn = _get_conn()

    existing_api_keys_cols = _table_columns(conn, "api_keys")
    is_old_schema = existing_api_keys_cols and "user_id" not in existing_api_keys_cols

    if is_old_schema:
        row_count = conn.execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()["n"]
        if row_count > 0:
            raise RuntimeError(
                "shared.api_keys: found the old api_keys schema (no user_id "
                f"column) with {row_count} existing row(s). Refusing to "
                "auto-migrate to avoid data loss — migrate manually first."
            )
        conn.executescript(
            "DROP TABLE IF EXISTS request_log;"
            "DROP TABLE IF EXISTS daily_usage;"
            "DROP TABLE IF EXISTS api_keys;"
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            email               TEXT    NOT NULL UNIQUE,
            name                TEXT    NOT NULL DEFAULT '',
            role                TEXT    NOT NULL DEFAULT 'user',
            daily_credit_limit  INTEGER,
            created_at          TEXT    NOT NULL,
            is_active           INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            key_hash     TEXT    NOT NULL UNIQUE,
            key_prefix   TEXT    NOT NULL,
            label        TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL,
            is_active    INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS daily_usage (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            usage_date   TEXT    NOT NULL,
            credits_used INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, usage_date)
        );

        CREATE TABLE IF NOT EXISTS request_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id   INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            endpoint     TEXT    NOT NULL,
            message_len  INTEGER NOT NULL DEFAULT 0,
            credit_cost  INTEGER NOT NULL DEFAULT 1,
            timestamp    TEXT    NOT NULL,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
        CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(user_id, usage_date);
        CREATE INDEX IF NOT EXISTS idx_request_log_user_id ON request_log(user_id);
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
# Users
# ---------------------------------------------------------------------------

def _get_or_create_user(
    email: str, name: str = "", role: str = ROLE_USER, daily_credit_limit: int | None = None
) -> sqlite3.Row:
    """
    Look up a user by email, creating them if they don't exist.

    Repeat calls with the same email reuse the same account (and therefore
    the same credit pool) instead of creating a new one — this is what
    keeps credits scoped per-account rather than per-key.
    """
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is not None:
        return row

    now = datetime.now(config.IST).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO users (email, name, role, daily_credit_limit, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (email, name, role, daily_credit_limit, now),
    )
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def generate_api_key(owner_email: str, owner_name: str = "", label: str = "") -> dict[str, Any]:
    """
    Generate a new API key for a customer account.

    If ``owner_email`` already has an account, the new key is attached to
    that same account and shares its existing credit pool. Returns a dict
    with ``api_key`` (raw — show once!), ``key_id``, ``key_prefix``,
    ``owner_email``, ``owner_name``.
    """
    user = _get_or_create_user(owner_email, owner_name, role=ROLE_USER)

    raw_key = KEY_PREFIX + secrets.token_hex(24)  # isk_ + 48 hex chars
    key_hash = _hash_key(raw_key)
    prefix = raw_key[:12]
    now = datetime.now(config.IST).isoformat()

    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO api_keys (user_id, key_hash, key_prefix, label, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user["id"], key_hash, prefix, label, now),
    )
    conn.commit()

    return {
        "api_key": raw_key,
        "key_id": cursor.lastrowid,
        "key_prefix": prefix,
        "owner_email": user["email"],
        "owner_name": user["name"],
    }


def create_owner_key(owner_email: str, owner_name: str = "") -> dict[str, Any]:
    """
    Mint an unlimited **owner** key.

    Deliberately NOT exposed via any HTTP endpoint — call this only from
    ``scripts/create_owner_key.py`` run locally by a project owner. Owner
    keys skip credit checks and deductions entirely (see ``shared.auth``).
    """
    user = _get_or_create_user(owner_email, owner_name, role=ROLE_OWNER, daily_credit_limit=None)

    raw_key = OWNER_KEY_PREFIX + secrets.token_hex(24)
    key_hash = _hash_key(raw_key)
    prefix = raw_key[:12]
    now = datetime.now(config.IST).isoformat()

    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO api_keys (user_id, key_hash, key_prefix, label, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user["id"], key_hash, prefix, "owner key", now),
    )
    conn.commit()

    return {
        "api_key": raw_key,
        "key_id": cursor.lastrowid,
        "key_prefix": prefix,
        "owner_email": user["email"],
        "owner_name": user["name"],
        "role": ROLE_OWNER,
    }


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

def validate_api_key(raw_key: str) -> dict[str, Any] | None:
    """
    Validate a raw API key.

    Returns a merged key+user record if the key and its owning account are
    both active, else ``None``. The returned dict includes ``role`` and
    ``daily_credit_limit`` so callers can branch on owner vs. normal keys.
    """
    if not raw_key or not (raw_key.startswith(KEY_PREFIX) or raw_key.startswith(OWNER_KEY_PREFIX)):
        return None

    key_hash = _hash_key(raw_key)
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT
            api_keys.id            AS id,
            api_keys.key_prefix    AS key_prefix,
            api_keys.user_id       AS user_id,
            api_keys.is_active     AS key_is_active,
            users.email            AS owner_email,
            users.name             AS owner_name,
            users.role             AS role,
            users.daily_credit_limit AS daily_credit_limit,
            users.is_active        AS user_is_active
        FROM api_keys
        JOIN users ON users.id = api_keys.user_id
        WHERE api_keys.key_hash = ?
        """,
        (key_hash,),
    ).fetchone()

    if row is None or not row["key_is_active"] or not row["user_is_active"]:
        return None

    return dict(row)


# ---------------------------------------------------------------------------
# Credit management (pooled per user, not per key)
# ---------------------------------------------------------------------------

def _today_ist() -> str:
    """Return today's date string in IST (YYYY-MM-DD)."""
    return datetime.now(config.IST).strftime("%Y-%m-%d")


def _effective_limit(daily_credit_limit: int | None) -> int:
    """Resolve a user's configured limit, falling back to the global default."""
    return config.DAILY_CREDIT_LIMIT if daily_credit_limit is None else daily_credit_limit


def get_credits_used_today(user_id: int) -> int:
    """Return how many credits a user's account has used today (IST), across all their keys."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT credits_used FROM daily_usage WHERE user_id = ? AND usage_date = ?",
        (user_id, _today_ist()),
    ).fetchone()
    return row["credits_used"] if row else 0


def get_credits_remaining(user_id: int, daily_credit_limit: int | None = None) -> int:
    """Return how many credits remain for today (IST) for this user's account."""
    used = get_credits_used_today(user_id)
    return max(0, _effective_limit(daily_credit_limit) - used)


def consume_credits(
    user_id: int, api_key_id: int, cost: int, endpoint: str, message_len: int
) -> int:
    """
    Consume *cost* credits against *user_id*'s shared pool (charged
    regardless of which of the user's keys made the request).

    Returns the remaining credits after consumption.
    """
    today = _today_ist()
    conn = _get_conn()

    conn.execute(
        """
        INSERT INTO daily_usage (user_id, usage_date, credits_used)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, usage_date)
        DO UPDATE SET credits_used = credits_used + ?
        """,
        (user_id, today, cost, cost),
    )

    now = datetime.now(config.IST).isoformat()
    conn.execute(
        """
        INSERT INTO request_log (api_key_id, user_id, endpoint, message_len, credit_cost, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (api_key_id, user_id, endpoint, message_len, cost, now),
    )
    conn.commit()

    return get_credits_remaining(user_id)


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

def get_usage_stats(user_id: int, daily_credit_limit: int | None = None) -> dict[str, Any]:
    """Return account-level usage statistics (aggregated across all of a user's keys)."""
    conn = _get_conn()
    limit = _effective_limit(daily_credit_limit)

    credits_used_today = get_credits_used_today(user_id)
    credits_remaining = max(0, limit - credits_used_today)

    row = conn.execute(
        "SELECT COUNT(*) as total, COALESCE(SUM(credit_cost), 0) as total_credits "
        "FROM request_log WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    from datetime import timedelta
    week_ago = (datetime.now(config.IST) - timedelta(days=7)).strftime("%Y-%m-%d")
    history = conn.execute(
        "SELECT usage_date, credits_used FROM daily_usage "
        "WHERE user_id = ? AND usage_date >= ? ORDER BY usage_date",
        (user_id, week_ago),
    ).fetchall()

    return {
        "credits_remaining": credits_remaining,
        "credits_used_today": credits_used_today,
        "credits_daily_limit": limit,
        "resets_at": get_next_reset_time(),
        "total_queries_all_time": row["total"],
        "total_credits_consumed_all_time": row["total_credits"],
        "last_7_days": [dict(r) for r in history],
    }


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------

def list_all_keys() -> list[dict[str, Any]]:
    """List all API keys with owner/role info (for admin). Does NOT expose the key hash."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT
            api_keys.id          AS id,
            api_keys.key_prefix  AS key_prefix,
            api_keys.label       AS label,
            api_keys.created_at  AS created_at,
            api_keys.is_active   AS is_active,
            api_keys.user_id     AS user_id,
            users.email          AS owner_email,
            users.name           AS owner_name,
            users.role           AS role,
            users.daily_credit_limit AS daily_credit_limit
        FROM api_keys
        JOIN users ON users.id = api_keys.user_id
        ORDER BY api_keys.id
        """
    ).fetchall()

    result = []
    for row in rows:
        row_dict = dict(row)
        row_dict["credits_remaining"] = (
            get_credits_remaining(row["user_id"], row["daily_credit_limit"])
            if row["is_active"]
            else 0
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


# ---------------------------------------------------------------------------
# Account-scoped operations (used by the self-serve billing dashboard)
# ---------------------------------------------------------------------------

def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Return the account row for *email*, or None if it doesn't exist yet."""
    row = _get_conn().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def set_daily_credit_limit(email: str, limit: int | None, name: str = "") -> dict[str, Any]:
    """
    Set an account's daily credit allowance, creating the account if needed.

    This is how a subscription tier becomes an entitlement: activate a plan,
    write its ``daily_credits`` here. ``None`` restores the global default.
    """
    user = _get_or_create_user(email, name, role=ROLE_USER)
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET daily_credit_limit = ? WHERE id = ?",
        (limit, user["id"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())


def list_keys_for_email(email: str) -> list[dict[str, Any]]:
    """List the API keys belonging to one account (never exposes the hash)."""
    rows = _get_conn().execute(
        """
        SELECT
            api_keys.id         AS id,
            api_keys.key_prefix AS key_prefix,
            api_keys.label      AS label,
            api_keys.created_at AS created_at,
            api_keys.is_active  AS is_active
        FROM api_keys
        JOIN users ON users.id = api_keys.user_id
        WHERE users.email = ?
        ORDER BY api_keys.id DESC
        """,
        (email,),
    ).fetchall()
    return [dict(r) for r in rows]


def revoke_key_for_email(key_id: int, email: str) -> bool:
    """
    Revoke a key **only if** it belongs to *email*.

    Scoping the UPDATE by owner (rather than checking then updating) is what
    stops one customer revoking another's key by guessing an id.
    """
    conn = _get_conn()
    cursor = conn.execute(
        """
        UPDATE api_keys SET is_active = 0
        WHERE id = ?
          AND user_id = (SELECT id FROM users WHERE email = ?)
        """,
        (key_id, email),
    )
    conn.commit()
    return cursor.rowcount > 0


def count_active_keys_for_email(email: str) -> int:
    row = _get_conn().execute(
        """
        SELECT COUNT(*) AS n FROM api_keys
        JOIN users ON users.id = api_keys.user_id
        WHERE users.email = ? AND api_keys.is_active = 1
        """,
        (email,),
    ).fetchone()
    return row["n"]
