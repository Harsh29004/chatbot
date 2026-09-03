"""
Shared pytest fixtures.

Provides:
- Temporary ChromaDB directory and SQLite DB (auto-cleaned)
- A mock embedding function (deterministic, no real model needed)
- A demo bot — template + indexed sheet — standing in for a real customer's
- FastAPI TestClient with API-key auth overridden
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Mock embedding function
# ---------------------------------------------------------------------------

# A simple hash-based deterministic embedding so tests don't need a real
# sentence-transformers model. The vectors aren't semantically meaningful, but
# texts that share words produce similar vectors and texts with no overlap
# produce near-orthogonal ones, which is enough to exercise the full pipeline.

_EMBEDDING_DIM = 384  # matches all-MiniLM-L6-v2


def _mock_embed_text(text: str, **kwargs) -> list[float]:
    """Produce a deterministic pseudo-embedding from text using feature hashing."""
    import hashlib
    import math
    import re

    _STOP = {
        "a", "an", "the", "is", "it", "to", "of", "in", "on", "at",
        "do", "my", "me", "if", "or", "am", "be", "so", "we", "he",
        "no", "by", "up", "as",
    }

    words = [
        w for w in re.split(r"[^a-z0-9]+", text.lower())
        if len(w) >= 2 and w not in _STOP
    ]

    features = list(words)
    for i in range(len(words) - 1):
        features.append(f"{words[i]}_{words[i+1]}")

    vec = [0.0] * _EMBEDDING_DIM

    for feat in features:
        h = hashlib.md5(feat.encode()).hexdigest()
        idx = int(h[:8], 16) % _EMBEDDING_DIM
        sign = 1.0 if int(h[8:10], 16) % 2 == 0 else -1.0
        vec[idx] += sign

    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _mock_embed_batch(texts: list[str], **kwargs) -> list[list[float]]:
    return [_mock_embed_text(t, **kwargs) for t in texts]


# ---------------------------------------------------------------------------
# The demo sheet
# ---------------------------------------------------------------------------
# Stands in for a real customer's upload. Generic online-shop content, so the
# tests exercise the engine rather than any particular business.

DEMO_SHEET_ROWS: list[list[str]] = [
    ["Question", "Alt_Phrasings", "Category", "Answer"],
    [
        "How do I cancel an order?",
        # Deliberately not "cancel my order" — that phrasing is a *request to
        # act*, and the action guardrail refuses it by design.
        "order cancellation; cancellation policy; call off an order",
        "Orders",
        "Open My Orders, select the order and choose Cancel Order. "
        "Cancellation is free any time before dispatch.",
    ],
    [
        "How do I get a refund?",
        "refund process; money back; when is my refund paid",
        "Refunds",
        "Refunds are processed within 5 to 7 working days once the returned "
        "item reaches our warehouse.",
    ],
    [
        "How do I change my phone number?",
        "update phone number; edit my mobile; change contact number",
        "Account",
        "Open Settings and tap Edit Profile to update your phone number. "
        "You will receive an OTP to confirm the change.",
    ],
    [
        "What payment methods do you accept?",
        "payment options; how can I pay; accepted cards",
        "Payments",
        "We accept UPI, credit and debit cards, net banking, and cash on delivery.",
    ],
    [
        "How do I track my order?",
        "order tracking; where is my order; delivery status",
        "Orders",
        "Open My Orders and tap the order to see live tracking. Tracking "
        "updates within 24 hours of dispatch.",
    ],
    [
        "What are your delivery charges?",
        "shipping cost; delivery fee; postage",
        "Shipping",
        "Delivery is free on orders above 499. Below that, a flat fee of 49 applies.",
    ],
    [
        "How do I return an item?",
        "start a return; send it back; return policy",
        "Returns",
        "Go to My Orders, select the item and tap Return. Returns are accepted "
        "within 7 days of delivery if the item is unused.",
    ],
]

DEMO_TEMPLATE_ID = "ecommerce"
DEMO_EMAIL = "demo@example.test"


def demo_sheet_bytes() -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(DEMO_SHEET_ROWS)
    return buffer.getvalue().encode()


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _temp_dirs(tmp_path: Path, monkeypatch):
    """Redirect ChromaDB and SQLite to temp directories for test isolation."""
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    sqlite_path = tmp_path / "test.db"

    monkeypatch.setattr("shared.config.CHROMA_PERSIST_DIR", str(chroma_dir))
    monkeypatch.setattr("shared.config.SQLITE_DB_PATH", str(sqlite_path))

    # Clear thread-local SQLite connections so they pick up the new path.
    import shared.logging_store as ls
    if hasattr(ls._LOCAL, "conn"):
        del ls._LOCAL.conn

    import shared.api_keys as ak
    if hasattr(ak._LOCAL, "api_conn"):
        del ak._LOCAL.api_conn

    from apps.billing import db as billing_db
    if hasattr(billing_db._LOCAL, "billing_conn"):
        del billing_db._LOCAL.billing_conn

    from apps.bot_engine import store
    if hasattr(store._LOCAL, "bots_conn"):
        del store._LOCAL.bots_conn

    ls.init_db()
    ak.init_api_key_tables()
    billing_db.init_billing_tables()
    store.init_bot_tables()


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch):
    """Patch embedding calls to use deterministic mock embeddings.

    Both the source module and the modules that did ``from shared.embeddings
    import ...`` need patching, because that form creates a local binding the
    source-module patch won't reach.
    """
    monkeypatch.setattr("shared.embeddings.embed_text", _mock_embed_text)
    monkeypatch.setattr("shared.embeddings.embed_batch", _mock_embed_batch)
    monkeypatch.setattr("apps.bot_engine.graph.embed_text", _mock_embed_text)
    monkeypatch.setattr("apps.bot_engine.ingest.embed_batch", _mock_embed_batch)


# ---------------------------------------------------------------------------
# A ready demo bot
# ---------------------------------------------------------------------------

@pytest.fixture()
def demo_bot() -> dict[str, Any]:
    """
    A customer account with a template chosen and the demo sheet indexed.

    Returns the bot record, plus ``user_id`` and ``template`` for convenience.
    """
    from apps.bot_engine import store
    from apps.bot_engine.ingest import ingest_sheet
    from apps.bot_engine.templates import get_template
    from shared.api_keys import get_user_by_email, set_daily_credit_limit

    set_daily_credit_limit(DEMO_EMAIL, None, name="Demo Shop")
    user = get_user_by_email(DEMO_EMAIL)

    store.get_or_create_bot(user["id"])
    store.set_template(user["id"], DEMO_TEMPLATE_ID, "Demo Shop")
    bot = store.get_bot(user["id"])

    ingest_sheet(
        collection_name=bot["collection_name"],
        filename="demo.csv",
        data=demo_sheet_bytes(),
    )
    bot = store.record_sheet(
        user["id"],
        filename="demo.csv",
        doc_count=len(DEMO_SHEET_ROWS) - 1,
        categories=["Orders", "Refunds", "Account", "Payments", "Shipping", "Returns"],
    )

    return {
        **bot,
        "user_id": user["id"],
        "template": get_template(DEMO_TEMPLATE_ID),
    }


@pytest.fixture()
def demo_graph(demo_bot):
    """A compiled graph for the demo bot, invoked directly (no HTTP)."""
    from apps.bot_engine.graph import BotConfig, build_graph

    template = demo_bot["template"]
    return build_graph(
        BotConfig(
            collection_name=demo_bot["collection_name"],
            decline_message=template.decline_message,
            near_match_suffix=template.near_match_suffix,
            strong_threshold=template.strong_threshold,
            near_threshold=template.near_threshold,
            log_label="test",
            extra_action_patterns=template.extra_action_patterns,
        )
    )


@pytest.fixture()
def demo_template(demo_bot):
    """The template the demo bot is running."""
    return demo_bot["template"]


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client(demo_bot) -> TestClient:
    """
    TestClient for ``POST /v1/ask`` with API-key auth overridden.

    In production the key is validated and credits deducted; here the
    dependency is replaced so tests exercise the bot, not the billing path
    (which has its own tests).
    """
    from server import app
    from shared.auth import verify_api_key

    async def _fake_api_key():
        return {
            "id": 1,
            "user_id": demo_bot["user_id"],
            "owner_email": DEMO_EMAIL,
            "role": "user",
            "daily_credit_limit": None,
            "key_is_active": 1,
            "user_is_active": 1,
        }

    app.dependency_overrides[verify_api_key] = _fake_api_key

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
