"""
Ollama embedding wrapper.

Calls the local (or VPS-hosted) Ollama HTTP API to produce embeddings.
No LangChain dependency — raw ``httpx`` for full control and transparency.
"""

from __future__ import annotations

import httpx

from shared.config import OLLAMA_BASE_URL, OLLAMA_MODEL


def embed_text(text: str, *, model: str | None = None, base_url: str | None = None) -> list[float]:
    """
    Embed a single text string via Ollama's ``/api/embeddings`` endpoint.

    Returns the embedding vector as a list of floats.
    """
    url = f"{base_url or OLLAMA_BASE_URL}/api/embeddings"
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": text,
    }
    resp = httpx.post(url, json=payload, timeout=30.0)
    resp.raise_for_status()
    return resp.json()["embedding"]


def embed_batch(
    texts: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> list[list[float]]:
    """
    Embed multiple texts sequentially.

    Ollama doesn't natively batch embeddings, so we loop.  For the
    typical FAQ sheet size (< 200 rows) this completes in seconds.
    """
    return [embed_text(t, model=model, base_url=base_url) for t in texts]
