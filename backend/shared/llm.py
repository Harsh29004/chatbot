"""
Local LLM access, via Ollama.

Two rules shape this module.

**It is never in the critical path.** Every entry point returns ``None``
rather than raising. If Ollama is down, slow, or not installed, the caller
falls back to the behaviour it had before there was a model — a verbatim
answer or a decline. A bot must not stop answering because an optional
component is unavailable, and a customer must never see a stack trace
because we added a summariser.

**It never sees more than it needs.** The grounded path passes retrieved
passages and nothing else: no other tenant's rows, no account details, no
conversation history (there isn't any). The owner path passes aggregates the
owner already has permission to read.

Ollama is expected on loopback. That is the data-residency story in one line:
the text is handed to a process on the same machine rather than shipped to a
vendor. ``OLLAMA_BASE_URL`` can be pointed elsewhere, and doing so trades that
property away knowingly.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import AsyncIterator

import httpx

from backend.shared import config

logger = logging.getLogger(__name__)

# Ollama's first response after a cold start includes loading the model into
# memory, which is far slower than steady state. One shared client keeps the
# connection warm rather than paying TCP setup per request.
_LOCAL = threading.local()

# Availability is cached: a bot with the feature on would otherwise pay a
# failed connection attempt on every single request while Ollama is down.
_AVAILABILITY_TTL_SECONDS = 30.0
_availability: tuple[bool, float] | None = None
_availability_lock = threading.Lock()


def _client() -> httpx.Client:
    client = getattr(_LOCAL, "client", None)
    if client is None:
        client = httpx.Client(
            base_url=config.OLLAMA_BASE_URL,
            timeout=httpx.Timeout(config.OLLAMA_TIMEOUT_SECONDS, connect=2.0),
        )
        _LOCAL.client = client
    return client


def available() -> bool:
    """
    Whether a local model is reachable right now.

    Cached for a few seconds. Callers use this to decide whether to attempt
    a model path at all, so it must be cheap and must never raise.

    Gated on *either* feature flag, not just ``LLM_ENABLED``. The two are
    documented as independent — bot rewording and the dashboard assistant —
    and checking only one here made the assistant permanently unavailable
    whenever the other was off. Each caller still enforces its own flag:
    ``generate`` checks ``LLM_ENABLED``, and the assistant router checks
    ``ASSISTANT_ENABLED``. This function answers a narrower question: is there
    a reachable model, and does anything here want one.
    """
    global _availability

    if not (config.LLM_ENABLED or config.ASSISTANT_ENABLED):
        return False

    now = time.monotonic()
    cached = _availability
    if cached is not None and now - cached[1] < _AVAILABILITY_TTL_SECONDS:
        return cached[0]

    with _availability_lock:
        # Another thread may have refreshed while we waited for the lock.
        cached = _availability
        if cached is not None and time.monotonic() - cached[1] < _AVAILABILITY_TTL_SECONDS:
            return cached[0]

        ok = False
        try:
            response = _client().get("/api/tags", timeout=2.0)
            ok = response.status_code == 200
        except Exception as exc:  # noqa: BLE001 - availability check, never fatal
            logger.debug("Ollama unavailable at %s: %s", config.OLLAMA_BASE_URL, exc)

        _availability = (ok, time.monotonic())
        return ok


def generate(
    *,
    system: str,
    prompt: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """
    Ask the local model for a completion.

    Returns the trimmed text, or ``None`` if anything at all went wrong —
    disabled, unreachable, timed out, non-200, empty. The caller is expected
    to have a working answer without us.
    """
    if not config.LLM_ENABLED:
        return None

    payload = {
        "model": config.OLLAMA_MODEL,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": config.LLM_TEMPERATURE if temperature is None else temperature,
            "num_predict": config.LLM_MAX_TOKENS if max_tokens is None else max_tokens,
        },
    }

    started = time.monotonic()
    try:
        response = _client().post("/api/generate", json=payload)
        if response.status_code != 200:
            logger.warning(
                "Ollama returned %s for model %s.",
                response.status_code,
                config.OLLAMA_MODEL,
            )
            return None
        text = (response.json().get("response") or "").strip()
    except Exception:
        # Timeouts are the common case here and are not worth a traceback in
        # production logs on every slow generation.
        logger.warning(
            "Ollama call failed after %.1fs (model=%s, url=%s).",
            time.monotonic() - started,
            config.OLLAMA_MODEL,
            config.OLLAMA_BASE_URL,
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )
        return None

    if not text:
        return None

    logger.debug("Ollama generated %d chars in %.2fs.", len(text), time.monotonic() - started)
    return text


async def chat_stream(
    *,
    system: str,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> AsyncIterator[str]:
    """
    Stream a multi-turn reply, yielding text deltas as they arrive.

    Async, unlike the rest of this module, because streaming is the one place
    where blocking a worker for the whole generation would actually hurt: on
    CPU that is a minute or more, and the point of streaming is that the reader
    is not waiting for it.

    Yields nothing at all if the model is unavailable or fails. The caller
    decides what to show for an empty stream — this function never raises into
    a response that has already started.
    """
    payload = {
        "model": model or config.ASSISTANT_MODEL,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
        "options": {
            "temperature": config.ASSISTANT_TEMPERATURE if temperature is None else temperature,
            "num_predict": config.ASSISTANT_MAX_TOKENS if max_tokens is None else max_tokens,
        },
    }

    limit = httpx.Timeout(
        timeout or config.ASSISTANT_TIMEOUT_SECONDS,
        connect=5.0,
        # No read timeout between tokens beyond the overall budget: a slow
        # first token on a cold model is normal, not a failure.
        read=timeout or config.ASSISTANT_TIMEOUT_SECONDS,
    )

    try:
        async with httpx.AsyncClient(base_url=config.OLLAMA_BASE_URL, timeout=limit) as client:
            async with client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code != 200:
                    logger.warning("Ollama chat returned %s.", response.status_code)
                    return
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        # Ollama emits newline-delimited JSON; a partial line
                        # is not worth aborting a working stream over.
                        continue
                    delta = (chunk.get("message") or {}).get("content") or ""
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        return
    except Exception:
        logger.warning(
            "Ollama stream failed (model=%s).",
            model or config.ASSISTANT_MODEL,
            exc_info=logger.isEnabledFor(logging.DEBUG),
        )
        return


def model_name() -> str:
    """The model tag answers are currently coming from."""
    return config.OLLAMA_MODEL


def reset_availability_cache() -> None:
    """Forget the cached health result. Used by tests and after config changes."""
    global _availability
    _availability = None
