"""
Billing data layer: customers, sessions, subscriptions, invoices.

Lives in the same SQLite file as the API-key tables so that granting a paying
customer their credit allowance is a local transaction rather than a
cross-service dance. ``shared.api_keys`` still owns the ``users`` /
``api_keys`` tables; this module links to them by email.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared import config

from apps.billing.plans import Plan, get_plan
from apps.billing.security import hash_session_token

_LOCAL = threading.local()

SESSION_TTL_DAYS = 30

# Subscription statuses
STATUS_TRIALING = "trialing"
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_CANCELED = "canceled"   # still runs to period end
STATUS_EXPIRED = "expired"

ENTITLED_STATUSES = (STATUS_TRIALING, STATUS_ACTIVE, STATUS_CANCELED)


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_LOCAL, "billing_conn", None)
    if conn is None:
        db_path = Path(config.SQLITE_DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _LOCAL.billing_conn = conn
    return conn


def _now() -> datetime:
    return datetime.now(config.IST)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def init_billing_tables() -> None:
    """Create the billing tables if they don't exist."""
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT    NOT NULL UNIQUE,
            name          TEXT    NOT NULL DEFAULT '',
            password_hash TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            is_active     INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            token_hash  TEXT    NOT NULL UNIQUE,
            created_at  TEXT    NOT NULL,
            expires_at  TEXT    NOT NULL,
            revoked     INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id          INTEGER NOT NULL,
            plan_id              TEXT    NOT NULL,
            status               TEXT    NOT NULL,
            current_period_start TEXT    NOT NULL,
            current_period_end   TEXT    NOT NULL,
            provider             TEXT    NOT NULL DEFAULT 'manual',
            provider_ref         TEXT,
            created_at           TEXT    NOT NULL,
            updated_at           TEXT    NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id     INTEGER NOT NULL,
            subscription_id INTEGER,
            plan_id         TEXT    NOT NULL,
            amount_cents    INTEGER NOT NULL,
            currency        TEXT    NOT NULL DEFAULT 'USD',
            status          TEXT    NOT NULL,
            provider        TEXT    NOT NULL DEFAULT 'manual',
            provider_ref    TEXT,
            issued_at       TEXT    NOT NULL,
            paid_at         TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
        CREATE INDEX IF NOT EXISTS idx_subs_customer ON subscriptions(customer_id);
        CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def create_customer(email: str, name: str, password_hash: str) -> dict[str, Any]:
    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO customers (email, name, password_hash, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (email.lower().strip(), name, password_hash, _iso(_now())),
    )
    conn.commit()
    return get_customer_by_id(cursor.lastrowid)  # type: ignore[return-value]


def get_customer_by_email(email: str) -> dict[str, Any] | None:
    row = _get_conn().execute(
        "SELECT * FROM customers WHERE email = ?", (email.lower().strip(),)
    ).fetchone()
    return dict(row) if row else None


def get_customer_by_id(customer_id: int) -> dict[str, Any] | None:
    row = _get_conn().execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(customer_id: int, raw_token: str) -> str:
    """Persist a session for *raw_token* and return its expiry ISO timestamp."""
    now = _now()
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO sessions (customer_id, token_hash, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (customer_id, hash_session_token(raw_token), _iso(now), _iso(expires)),
    )
    conn.commit()
    return _iso(expires)


def get_session_customer(raw_token: str) -> dict[str, Any] | None:
    """Resolve a session token to its customer, or ``None`` if invalid/expired."""
    if not raw_token:
        return None
    row = _get_conn().execute(
        """
        SELECT customers.*, sessions.expires_at AS session_expires_at
        FROM sessions
        JOIN customers ON customers.id = sessions.customer_id
        WHERE sessions.token_hash = ? AND sessions.revoked = 0
        """,
        (hash_session_token(raw_token),),
    ).fetchone()

    if row is None or not row["is_active"]:
        return None
    if datetime.fromisoformat(row["session_expires_at"]) <= _now():
        return None
    return dict(row)


def revoke_session(raw_token: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET revoked = 1 WHERE token_hash = ?",
        (hash_session_token(raw_token),),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def get_current_subscription(customer_id: int) -> dict[str, Any] | None:
    """The customer's most recent subscription row, whatever its status."""
    row = _get_conn().execute(
        """
        SELECT * FROM subscriptions
        WHERE customer_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (customer_id,),
    ).fetchone()
    return dict(row) if row else None


def is_entitled(subscription: dict[str, Any] | None) -> bool:
    """True when this subscription currently grants access.

    ``canceled`` still counts until the paid period actually runs out — the
    customer paid for it.
    """
    if subscription is None:
        return False
    if subscription["status"] not in ENTITLED_STATUSES:
        return False
    return datetime.fromisoformat(subscription["current_period_end"]) > _now()


def start_subscription(
    customer_id: int,
    plan: Plan,
    status: str,
    provider: str = "manual",
    provider_ref: str | None = None,
) -> dict[str, Any]:
    """Open a new subscription period for *plan* starting now."""
    now = _now()
    end = now + timedelta(days=plan.interval_days)
    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO subscriptions (
            customer_id, plan_id, status, current_period_start,
            current_period_end, provider, provider_ref, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id, plan.id, status, _iso(now), _iso(end),
            provider, provider_ref, _iso(now), _iso(now),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


def set_subscription_status(subscription_id: int, status: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE subscriptions SET status = ?, updated_at = ? WHERE id = ?",
        (status, _iso(_now()), subscription_id),
    )
    conn.commit()


def has_used_trial(customer_id: int) -> bool:
    row = _get_conn().execute(
        "SELECT 1 FROM subscriptions WHERE customer_id = ? AND plan_id = 'trial' LIMIT 1",
        (customer_id,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

def create_invoice(
    customer_id: int,
    subscription_id: int | None,
    plan: Plan,
    currency: str,
    status: str,
    provider: str = "manual",
    provider_ref: str | None = None,
) -> dict[str, Any]:
    now = _now()
    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO invoices (
            customer_id, subscription_id, plan_id, amount_cents, currency,
            status, provider, provider_ref, issued_at, paid_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id, subscription_id, plan.id, plan.price_cents, currency,
            status, provider, provider_ref, _iso(now),
            _iso(now) if status == "paid" else None,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM invoices WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


def list_invoices(customer_id: int) -> list[dict[str, Any]]:
    rows = _get_conn().execute(
        "SELECT * FROM invoices WHERE customer_id = ? ORDER BY id DESC",
        (customer_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_invoice_paid(invoice_id: int, provider_ref: str | None = None) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE invoices SET status = 'paid', paid_at = ?, provider_ref = COALESCE(?, provider_ref) WHERE id = ?",
        (_iso(_now()), provider_ref, invoice_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Expiry sweep
# ---------------------------------------------------------------------------

def expire_lapsed_subscriptions() -> int:
    """Flip any subscription whose period has ended to ``expired``.

    Returns the number of rows changed. Called on startup and before
    entitlement reads so a lapsed plan never keeps serving traffic just
    because no cron job ran.
    """
    conn = _get_conn()
    cursor = conn.execute(
        """
        UPDATE subscriptions
        SET status = ?, updated_at = ?
        WHERE status IN (?, ?, ?) AND current_period_end <= ?
        """,
        (
            STATUS_EXPIRED, _iso(_now()),
            STATUS_TRIALING, STATUS_ACTIVE, STATUS_CANCELED,
            _iso(_now()),
        ),
    )
    conn.commit()
    return cursor.rowcount
