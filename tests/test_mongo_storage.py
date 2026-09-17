"""
Everything the app stores lives in MongoDB: FAQ vectors, rate limits and LLM
cooldowns included. These tests pin the properties that moving them out of
process memory and Chroma files was for.
"""

from __future__ import annotations

import numpy as np

from backend.shared import rate_limits, vector_store
from backend.shared.mongo import coll


def _unit(*values: float) -> list[float]:
    v = np.zeros(8, dtype=np.float32)
    v[: len(values)] = values
    return (v / np.linalg.norm(v)).tolist()


def _add(name: str, rows: dict[str, list[float]]) -> None:
    handle = vector_store.reset_collection(name)
    vector_store.add_documents(
        handle,
        ids=list(rows),
        embeddings=list(rows.values()),
        documents=list(rows),
        metadatas=[{"question": q, "answer": f"answer to {q}"} for q in rows],
    )


# ---------------------------------------------------------------------------
# Vectors
# ---------------------------------------------------------------------------

def test_vectors_are_stored_in_mongodb_and_searched_by_cosine():
    _add("bot_a", {"refunds": _unit(1, 0), "delivery": _unit(0, 1), "returns": _unit(1, 1)})

    assert coll(vector_store.FAQ_VECTORS).count_documents({"collection": "bot_a"}) == 3

    results = vector_store.query_collection(vector_store.get_collection("bot_a"), _unit(1, 0.1), k=2)
    assert [r["id"] for r in results] == ["refunds", "returns"]
    assert results[0]["metadata"]["answer"] == "answer to refunds"
    assert 0.99 < results[0]["similarity"] <= 1.0


def test_embeddings_are_stored_compactly_as_float32_bytes():
    _add("bot_a", {"q": _unit(1, 2, 3)})
    stored = coll(vector_store.FAQ_VECTORS).find_one({"collection": "bot_a"})
    assert isinstance(stored["embedding"], bytes)
    assert len(stored["embedding"]) == 8 * 4  # 8 dims x 4 bytes


def test_one_bot_never_sees_another_bots_vectors():
    _add("bot_a", {"only in a": _unit(1, 0)})
    _add("bot_b", {"only in b": _unit(1, 0)})

    results = vector_store.query_collection(vector_store.get_collection("bot_a"), _unit(1, 0), k=5)
    assert [r["id"] for r in results] == ["only in a"]


def test_a_reupload_replaces_the_sheet_and_leaves_no_old_rows():
    _add("bot_a", {"old question": _unit(1, 0)})
    _add("bot_a", {"new question": _unit(1, 0)})

    stored = list(coll(vector_store.FAQ_VECTORS).find({"collection": "bot_a"}))
    assert [d["doc_id"] for d in stored] == ["new question"]


def test_the_old_sheet_keeps_answering_until_the_new_one_is_fully_written():
    _add("bot_a", {"old question": _unit(1, 0)})

    # Start a re-upload but don't finish it.
    pending = vector_store.reset_collection("bot_a")
    coll(vector_store.FAQ_VECTORS).insert_one(
        {"collection": "bot_a", "version": pending.pending_version, "doc_id": "half written",
         "document": "", "metadata": {}, "embedding": np.zeros(8, np.float32).tobytes()}
    )

    results = vector_store.query_collection(vector_store.get_collection("bot_a"), _unit(1, 0), k=5)
    assert [r["id"] for r in results] == ["old question"]


def test_another_worker_picks_up_a_reupload():
    _add("bot_a", {"old question": _unit(1, 0)})
    handle = vector_store.get_collection("bot_a")
    assert vector_store.query_collection(handle, _unit(1, 0))[0]["id"] == "old question"

    # A different process re-uploads; this process still has the old matrix
    # cached, and must notice the published version changed.
    _add("bot_a", {"new question": _unit(1, 0)})
    assert vector_store.query_collection(handle, _unit(1, 0))[0]["id"] == "new question"


def test_an_empty_or_unknown_collection_returns_nothing():
    assert vector_store.query_collection(vector_store.get_collection("nobody"), _unit(1)) == []


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------

def test_rate_limit_events_are_shared_through_mongodb_not_process_memory():
    rate_limits.record("signup", "1.2.3.4", 60)
    rate_limits.record("signup", "1.2.3.4", 60)
    rate_limits.record("signup", "5.6.7.8", 60)

    assert rate_limits.count("signup", "1.2.3.4", 60) == 2
    assert coll(rate_limits.RATE_EVENTS).count_documents({"bucket": "signup"}) == 3

    rate_limits.clear("signup", "1.2.3.4")
    assert rate_limits.count("signup", "1.2.3.4", 60) == 0
    assert rate_limits.count("signup", "5.6.7.8", 60) == 1


def test_events_outside_the_window_do_not_count():
    from datetime import datetime, timedelta

    # An event from ten minutes ago, written directly: comparing against "now"
    # with a zero window is flaky on clocks that tick in 15 ms steps.
    old = datetime.utcnow() - timedelta(minutes=10)
    coll(rate_limits.RATE_EVENTS).insert_one(
        {"bucket": "login_failure", "key": "someone", "at": old, "expires_at": old + timedelta(hours=1)}
    )
    assert rate_limits.count("login_failure", "someone", 5 * 60) == 0
    assert rate_limits.count("login_failure", "someone", 15 * 60) == 1


def test_rate_events_expire_via_a_ttl_index():
    indexes = coll(rate_limits.RATE_EVENTS).index_information()
    assert indexes["expires_at_ttl"]["expireAfterSeconds"] == 0


# ---------------------------------------------------------------------------
# LLM cooldowns
# ---------------------------------------------------------------------------

def test_llm_cooldowns_are_stored_in_mongodb():
    from backend.shared import llm

    target = llm.Target("groq", "some-model")
    llm._cool(target, "rate limited", 120)

    doc = coll(llm.LLM_COOLDOWNS).find_one({"_id": target.label})
    assert doc is not None and doc["reason"] == "rate limited"
    assert llm._cooling(target)
