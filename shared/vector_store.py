"""
ChromaDB vector-store helpers.

Provides collection management and query utilities.  Each bot gets its
own collection — there is zero shared retrieval space.
"""

from __future__ import annotations

from typing import Any

import chromadb
from chromadb.config import Settings

from shared import config


def _get_client() -> chromadb.ClientAPI:
    """Return a persistent ChromaDB client."""
    return chromadb.PersistentClient(
        path=config.CHROMA_PERSIST_DIR,
        settings=Settings(anonymized_telemetry=False),
    )


def get_collection(name: str) -> chromadb.Collection:
    """
    Return (or create) a ChromaDB collection by *name*.

    Uses cosine distance — ChromaDB stores *distances*, so we convert
    to similarity (``1 - distance``) at query time.
    """
    client = _get_client()
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def add_documents(
    collection: chromadb.Collection,
    ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    """Upsert documents into the collection."""
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )


def query_collection(
    collection: chromadb.Collection,
    embedding: list[float],
    k: int = 3,
) -> list[dict[str, Any]]:
    """
    Query *collection* with the given *embedding* and return the top-*k*
    results as a list of dicts with keys:

    - ``id``
    - ``document``       (the searchable text)
    - ``metadata``       (contains ``answer``, ``question``, ``category``)
    - ``similarity``     (1 − cosine distance, range 0–1)
    """
    results = collection.query(
        query_embeddings=[embedding],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    items: list[dict[str, Any]] = []
    if not results["ids"] or not results["ids"][0]:
        return items

    for i, doc_id in enumerate(results["ids"][0]):
        distance = results["distances"][0][i] if results["distances"] else 1.0
        items.append(
            {
                "id": doc_id,
                "document": (results["documents"][0][i] if results["documents"] else ""),
                "metadata": (results["metadatas"][0][i] if results["metadatas"] else {}),
                "similarity": 1.0 - distance,
            }
        )
    return items


def reset_collection(name: str) -> chromadb.Collection:
    """
    Delete and re-create a collection (used by the reindex endpoint).
    """
    client = _get_client()
    try:
        client.delete_collection(name)
    except (ValueError, Exception):
        pass  # collection didn't exist (ValueError in older ChromaDB, NotFoundError in 1.x)
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )
