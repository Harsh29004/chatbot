"""
Sentence-transformers embedding wrapper.

Uses a lightweight CPU-based model (all-MiniLM-L6-v2 by default) to
produce embeddings.  No Ollama or external service required.

The model is loaded lazily on first call and reused for all subsequent
requests (singleton pattern).
"""

from __future__ import annotations

import threading

from shared.config import EMBEDDING_MODEL

_lock = threading.Lock()
_model = None


def _get_model():
    """Lazy-load the sentence-transformers model (thread-safe singleton)."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:  # double-check after acquiring lock
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def embed_text(text: str) -> list[float]:
    """
    Embed a single text string.

    Returns the embedding vector as a list of floats.
    """
    model = _get_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts in a single batch (much faster than looping).

    The sentence-transformers library handles batching natively,
    so this is significantly faster than the old sequential Ollama calls.
    """
    model = _get_model()
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return embeddings.tolist()

