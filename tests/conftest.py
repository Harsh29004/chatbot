"""
Shared pytest fixtures for the Instant Sahay FAQ bot tests.

Provides:
- Temporary ChromaDB directory (auto-cleaned)
- Temporary SQLite DB (auto-cleaned)
- A mock embedding function (deterministic, no real model needed)
- Pre-ingested collections for both bots
- FastAPI TestClient with API key auth bypass
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

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

# We use a simple hash-based deterministic embedding so tests don't need
# a real sentence-transformers model.  The vectors won't be semantically
# meaningful, but we can still test the full pipeline by controlling what
# gets ingested and what gets queried.

_EMBEDDING_DIM = 384  # matches all-MiniLM-L6-v2


def _mock_embed_text(text: str, **kwargs) -> list[float]:
    """
    Produce a deterministic pseudo-embedding from text using feature hashing.

    Words are hashed to dimension indices, so texts that share words
    produce similar vectors (high cosine similarity) and texts with no
    word overlap produce near-orthogonal vectors (low cosine similarity).
    Stop words are removed and bigrams are added for better discrimination.
    """
    import hashlib
    import math
    import re

    _STOP = {
        "a", "an", "the", "is", "it", "to", "of", "in", "on", "at",
        "do", "my", "me", "if", "or", "am", "be", "so", "we", "he",
        "no", "by", "up", "as",
    }

    # Tokenise: lowercase, split on non-alpha, drop short tokens and stops
    words = [
        w for w in re.split(r"[^a-z0-9]+", text.lower())
        if len(w) >= 2 and w not in _STOP
    ]

    # Build features from unigrams + bigrams
    features = list(words)
    for i in range(len(words) - 1):
        features.append(f"{words[i]}_{words[i+1]}")

    vec = [0.0] * _EMBEDDING_DIM

    for feat in features:
        h = hashlib.md5(feat.encode()).hexdigest()
        idx = int(h[:8], 16) % _EMBEDDING_DIM
        sign = 1.0 if int(h[8:10], 16) % 2 == 0 else -1.0
        vec[idx] += sign

    # L2-normalise so cosine similarity = dot product
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _mock_embed_batch(texts: list[str], **kwargs) -> list[list[float]]:
    return [_mock_embed_text(t, **kwargs) for t in texts]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _temp_dirs(tmp_path: Path, monkeypatch):
    """
    Redirect ChromaDB and SQLite to temp directories for test isolation.
    """
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    sqlite_path = tmp_path / "test.db"

    monkeypatch.setattr("shared.config.CHROMA_PERSIST_DIR", str(chroma_dir))
    monkeypatch.setattr("shared.config.SQLITE_DB_PATH", str(sqlite_path))

    # Clear thread-local SQLite connections so they pick up the new path
    import shared.logging_store as ls
    if hasattr(ls._LOCAL, "conn"):
        del ls._LOCAL.conn

    import shared.api_keys as ak
    if hasattr(ak._LOCAL, "api_conn"):
        del ak._LOCAL.api_conn

    # Initialise the DB in the temp location
    ls.init_db()
    ak.init_api_key_tables()


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch):
    """Patch embedding calls to use deterministic mock embeddings.

    We patch both the source module AND every consuming module that did
    ``from shared.embeddings import embed_text/embed_batch``, because
    Python's ``from X import Y`` creates a local binding that won't be
    affected by patching the source module alone.
    """
    monkeypatch.setattr("shared.embeddings.embed_text", _mock_embed_text)
    monkeypatch.setattr("shared.embeddings.embed_batch", _mock_embed_batch)
    # Patch consuming modules that import directly. Both built-in bots now run
    # on the shared engine, so that is the single query-side patch point.
    monkeypatch.setattr("apps.bot_engine.graph.embed_text", _mock_embed_text)
    monkeypatch.setattr("apps.bot_engine.ingest.embed_batch", _mock_embed_batch)
    monkeypatch.setattr("apps.customer_bot.ingest.embed_batch", _mock_embed_batch)
    monkeypatch.setattr("apps.partner_bot.ingest.embed_batch", _mock_embed_batch)


@pytest.fixture()
def ingested_data():
    """
    Ingest both customer and partner dummy FAQ data into temp ChromaDB.

    Returns a dict with customer and partner question lists.
    """
    from apps.customer_bot.ingest import ingest as customer_ingest
    from apps.partner_bot.ingest import ingest as partner_ingest

    # The ingest functions will read the xlsx files from the data dirs
    # and use mock embeddings
    c_count = customer_ingest()
    p_count = partner_ingest()

    return {
        "customer_count": c_count,
        "partner_count": p_count,
    }


@pytest.fixture()
def customer_graph(ingested_data):
    """Return a compiled customer bot LangGraph."""
    from apps.customer_bot.graph import build_customer_graph
    return build_customer_graph()


@pytest.fixture()
def partner_graph(ingested_data):
    """Return a compiled partner bot LangGraph."""
    from apps.partner_bot.graph import build_partner_graph
    return build_partner_graph()


@pytest.fixture()
def test_client(ingested_data) -> TestClient:
    """
    FastAPI TestClient with API key auth bypassed for test convenience.

    In production, API keys would be validated and credits deducted;
    here we override the dependency to skip auth.
    """
    from server import app
    from shared.auth import verify_api_key

    # Override API key auth to return a fake key record
    async def _fake_api_key():
        return {
            "id": 1,
            "user_id": 1,
            "owner_email": "test@test.com",
            "role": "user",
            "daily_credit_limit": None,
            "key_is_active": 1,
            "user_is_active": 1,
        }

    app.dependency_overrides[verify_api_key] = _fake_api_key

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture()
def customer_client(ingested_data) -> TestClient:
    """TestClient for customer bot endpoints."""
    from server import app
    from shared.auth import verify_api_key

    async def _fake_api_key():
        return {
            "id": 1,
            "user_id": 1,
            "owner_email": "customer@test.com",
            "role": "user",
            "daily_credit_limit": None,
            "key_is_active": 1,
            "user_is_active": 1,
        }

    app.dependency_overrides[verify_api_key] = _fake_api_key

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture()
def partner_client(ingested_data) -> TestClient:
    """TestClient for partner bot endpoints."""
    from server import app
    from shared.auth import verify_api_key

    async def _fake_api_key():
        return {
            "id": 1,
            "user_id": 1,
            "owner_email": "partner@test.com",
            "role": "user",
            "daily_credit_limit": None,
            "key_is_active": 1,
            "user_is_active": 1,
        }

    app.dependency_overrides[verify_api_key] = _fake_api_key

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()

