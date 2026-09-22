"""
The vector store, in MongoDB.

Each bot's vectors live in the ``faq_vectors`` collection, tagged with the
bot's collection name — there is no shared retrieval space: every query is
filtered to one collection before any similarity is computed.

**How a sheet is stored.** One document per searchable phrasing: its text, the
metadata (answer, question, category) and the embedding. Embeddings are
normalised and stored as float32 bytes rather than a list of doubles, which
is about a third of the size — it matters on a 512 MB Atlas free cluster.

**How a re-upload replaces a sheet without a gap.** Every upload writes under
a fresh ``version``. Only once all of it is written does ``vector_sets`` point
the collection at the new version, and then the old version is deleted. A
question asked mid-upload is answered from the old sheet, never from an empty
or half-written one.

**How search works.** A bot holds at most a few thousand vectors, so the whole
set is loaded into one numpy matrix and scored with a single dot product
(cosine similarity, since everything is normalised). The matrix is cached per
process and reused until ``vector_sets`` shows a newer version, so every
worker picks up a re-upload on its next query.

The API mirrors the ChromaDB helpers this replaced, so callers did not change.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
from bson.binary import Binary

from backend.shared.mongo import coll, register_indexes

FAQ_VECTORS = "faq_vectors"
VECTOR_SETS = "vector_sets"

register_indexes(
    FAQ_VECTORS,
    [([("collection", 1), ("version", 1)], {"name": "collection_version"})],
)

_INSERT_BATCH = 500


@dataclass
class VectorCollection:
    """A handle on one bot's vectors."""

    name: str
    # Set by reset_collection: the version being written, not yet published.
    pending_version: str | None = None

    def get(self, include: list[str] | None = None) -> dict[str, list[Any]]:
        """Every stored item for the published version (Chroma-style shape)."""
        version = _published_version(self.name)
        rows = (
            list(coll(FAQ_VECTORS).find({"collection": self.name, "version": version}))
            if version
            else []
        )
        return {
            "ids": [r["doc_id"] for r in rows],
            "documents": [r["document"] for r in rows],
            "metadatas": [r["metadata"] for r in rows],
        }

    def count(self) -> int:
        version = _published_version(self.name)
        if not version:
            return 0
        return coll(FAQ_VECTORS).count_documents({"collection": self.name, "version": version})


# ---------------------------------------------------------------------------
# Per-process cache of loaded matrices
# ---------------------------------------------------------------------------

@dataclass
class _Loaded:
    version: str
    matrix: np.ndarray            # shape (n, dim), rows normalised
    items: list[dict[str, Any]]   # id, document, metadata — row-aligned


_cache: dict[str, _Loaded] = {}
_cache_lock = threading.Lock()


def _published_version(name: str) -> str | None:
    doc = coll(VECTOR_SETS).find_one({"_id": name}, {"version": 1})
    return doc["version"] if doc else None


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def _load(name: str) -> _Loaded | None:
    version = _published_version(name)
    if version is None:
        with _cache_lock:
            _cache.pop(name, None)
        return None

    with _cache_lock:
        cached = _cache.get(name)
    if cached is not None and cached.version == version:
        return cached

    rows = list(coll(FAQ_VECTORS).find({"collection": name, "version": version}))
    if rows:
        matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    else:
        matrix = np.zeros((0, 0), dtype=np.float32)
    loaded = _Loaded(
        version=version,
        matrix=matrix,
        items=[{"id": r["doc_id"], "document": r["document"], "metadata": r["metadata"]} for r in rows],
    )
    with _cache_lock:
        _cache[name] = loaded
    return loaded


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_collection(name: str) -> VectorCollection:
    """A handle on the named collection. Nothing is created until data is added."""
    return VectorCollection(name=name)


def reset_collection(name: str) -> VectorCollection:
    """
    Start replacing a collection's contents.

    Returns a handle carrying a new, unpublished version. The current version
    keeps answering until :func:`add_documents` finishes writing the new one.
    """
    return VectorCollection(name=name, pending_version=uuid.uuid4().hex)


def add_documents(
    collection: VectorCollection,
    ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    """Write documents, publish them, and remove the previous version."""
    name = collection.name
    version = collection.pending_version or _published_version(name) or uuid.uuid4().hex
    collection.pending_version = version

    if ids:
        vectors = _normalise(np.asarray(embeddings, dtype=np.float32))
        target = coll(FAQ_VECTORS)
        # Upsert semantics within one version: re-adding an id replaces it.
        target.delete_many({"collection": name, "version": version, "doc_id": {"$in": list(ids)}})
        batch: list[dict[str, Any]] = []
        for doc_id, vector, text, metadata in zip(ids, vectors, documents, metadatas):
            batch.append(
                {
                    "collection": name,
                    "version": version,
                    "doc_id": doc_id,
                    "document": text,
                    "metadata": metadata,
                    "embedding": Binary(vector.astype(np.float32).tobytes()),
                }
            )
            if len(batch) >= _INSERT_BATCH:
                target.insert_many(batch, ordered=False)
                batch = []
        if batch:
            target.insert_many(batch, ordered=False)

    coll(VECTOR_SETS).update_one(
        {"_id": name},
        {"$set": {"version": version, "updated_at": datetime.now(timezone.utc).replace(tzinfo=None)}},
        upsert=True,
    )
    coll(FAQ_VECTORS).delete_many({"collection": name, "version": {"$ne": version}})


def query_collection(
    collection: VectorCollection,
    embedding: list[float],
    k: int = 3,
) -> list[dict[str, Any]]:
    """
    The top-*k* items by cosine similarity, as dicts with keys ``id``,
    ``document``, ``metadata`` and ``similarity`` (range -1..1, in practice 0..1).
    """
    loaded = _load(collection.name)
    if loaded is None or not loaded.items:
        return []

    query = _normalise(np.asarray(embedding, dtype=np.float32).reshape(1, -1))[0]
    scores = loaded.matrix @ query
    k = max(0, min(k, len(scores)))
    if k == 0:
        return []
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return [
        {**loaded.items[i], "similarity": float(scores[i])}
        for i in top
    ]


def delete_collection(name: str) -> None:
    """Remove every vector for a collection."""
    coll(FAQ_VECTORS).delete_many({"collection": name})
    coll(VECTOR_SETS).delete_one({"_id": name})
    with _cache_lock:
        _cache.pop(name, None)


def clear_cache() -> None:
    """Drop every loaded matrix. Used by tests between databases."""
    with _cache_lock:
        _cache.clear()
