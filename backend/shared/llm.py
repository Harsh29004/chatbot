"""
LLM access: a chain of local Ollama models, then Groq as the last resort.

Three rules shape this module.

**It is never in the critical path.** Every entry point returns ``None`` (or
an empty stream) rather than raising. If every model is down, slow, or rate
limited, the caller falls back to the behaviour it had before there was a
model — a verbatim answer or a decline. A bot must not stop answering because
an optional component is unavailable.

**One saturated model is not an outage.** Generation walks a chain:
``OLLAMA_MODELS`` in order, then ``GROQ_MODELS``. A model that answers 429
(rate limited), 503 (busy), 5xx, 404 (not pulled), or times out is put on a
short cooldown and the next one is tried. The cooldown means a model we know
is saturated isn't re-hit on every request for the next minute.

**It never sees more than it needs.** The grounded path passes retrieved
passages and nothing else. Note the data-residency trade: the Ollama models
run on this machine, but when the chain reaches Groq the prompt goes to a
hosted API. Leave ``GROQ_API_KEY`` unset to keep every generation local.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import AsyncIterator, Literal

import httpx

from backend.shared import config

logger = logging.getLogger(__name__)

Provider = Literal["ollama", "groq"]


@dataclass(frozen=True)
class Target:
    provider: Provider
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class _TryNext(Exception):
    """This target can't answer right now; move down the chain."""

    def __init__(self, reason: str, cooldown: bool = True):
        super().__init__(reason)
        self.cooldown = cooldown


# Status codes that mean "this model, not this request" — worth trying the
# next model. A 400 would fail identically everywhere, but even that is safer
# to pass along than to give up on, since providers disagree on validation.
_RETRYABLE_STATUS = {404, 408, 409, 413, 429, 500, 502, 503, 504}

# One shared client per thread keeps the Ollama connection warm.
_LOCAL = threading.local()

# Availability is cached: a bot with the feature on would otherwise pay a
# failed connection attempt on every single request while Ollama is down.
_AVAILABILITY_TTL_SECONDS = 30.0
_availability: tuple[bool, float] | None = None
_availability_lock = threading.Lock()

# target label -> monotonic time the cooldown ends
_cooldowns: dict[str, float] = {}
_cooldown_lock = threading.Lock()

# The model that produced the most recent successful answer, for display.
_last_used: str | None = None


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------

def _chain(first_ollama_model: str | None = None) -> list[Target]:
    models = list(config.OLLAMA_MODELS)
    if first_ollama_model:
        models = [first_ollama_model, *[m for m in models if m != first_ollama_model]]
    targets = [Target("ollama", m) for m in models]
    if config.GROQ_API_KEY:
        targets += [Target("groq", m) for m in config.GROQ_MODELS]
    return targets


def _cooling(target: Target) -> bool:
    with _cooldown_lock:
        until = _cooldowns.get(target.label)
        if until is None:
            return False
        if time.monotonic() >= until:
            del _cooldowns[target.label]
            return False
        return True


def _cool(target: Target, reason: str, retry_after: float | None = None) -> None:
    seconds = retry_after if retry_after and retry_after > 0 else config.LLM_COOLDOWN_SECONDS
    with _cooldown_lock:
        _cooldowns[target.label] = time.monotonic() + seconds
    logger.warning("LLM %s unavailable (%s); skipping it for %.0fs.", target.label, reason, seconds)


def _cool_after_error(target: Target, exc: Exception, detail: str) -> None:
    """
    Cool a model after a transport error.

    If the Ollama *server* can't be reached, every local model is down, not
    just this one — so they are all benched together rather than each one
    paying its own connect timeout on the same request.
    """
    if target.provider == "ollama" and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        for model in config.OLLAMA_MODELS:
            _cool(Target("ollama", model), f"Ollama server unreachable: {detail}")
        return
    _cool(target, detail)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None


def _check(response: httpx.Response, target: Target) -> None:
    if response.status_code == 200:
        return
    if response.status_code in (401, 403):
        # A bad Groq key won't fix itself in a minute, but cooling it keeps
        # the log from repeating the same error on every request.
        raise _TryNext(f"auth failed ({response.status_code})")
    if response.status_code in _RETRYABLE_STATUS or response.status_code >= 500:
        if response.status_code == 429:
            _cool(target, "rate limited", _retry_after(response))
            raise _TryNext("rate limited", cooldown=False)
        raise _TryNext(f"HTTP {response.status_code}")
    raise _TryNext(f"HTTP {response.status_code}", cooldown=False)


def _groq_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.GROQ_API_KEY}"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def _ollama_client() -> httpx.Client:
    client = getattr(_LOCAL, "client", None)
    if client is None:
        client = httpx.Client(
            base_url=config.OLLAMA_BASE_URL,
            timeout=httpx.Timeout(config.OLLAMA_TIMEOUT_SECONDS, connect=2.0),
        )
        _LOCAL.client = client
    return client


def _groq_client() -> httpx.Client:
    client = getattr(_LOCAL, "groq", None)
    if client is None:
        client = httpx.Client(
            base_url=config.GROQ_BASE_URL,
            timeout=httpx.Timeout(config.GROQ_TIMEOUT_SECONDS, connect=5.0),
        )
        _LOCAL.groq = client
    return client


def _async_client(limit: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=limit)


def available() -> bool:
    """
    Whether any model in the chain is reachable right now.

    Cached for a few seconds, and must never raise. True when Ollama answers
    its health check, or when a Groq key is configured to fall back on.

    Gated on *either* feature flag, not just ``LLM_ENABLED``: bot rewording
    and the dashboard assistant are independent, and each caller still
    enforces its own flag.
    """
    global _availability

    if not (config.LLM_ENABLED or config.ASSISTANT_ENABLED):
        return False

    now = time.monotonic()
    cached = _availability
    if cached is not None and now - cached[1] < _AVAILABILITY_TTL_SECONDS:
        return cached[0]

    with _availability_lock:
        cached = _availability
        if cached is not None and time.monotonic() - cached[1] < _AVAILABILITY_TTL_SECONDS:
            return cached[0]

        ok = False
        try:
            response = _ollama_client().get("/api/tags", timeout=2.0)
            ok = response.status_code == 200
        except Exception as exc:  # noqa: BLE001 - availability check, never fatal
            logger.debug("Ollama unavailable at %s: %s", config.OLLAMA_BASE_URL, exc)

        # Groq is checked by configuration, not by a network call: pinging a
        # hosted API every 30s would spend its rate limit on health checks.
        ok = ok or bool(config.GROQ_API_KEY and config.GROQ_MODELS)

        _availability = (ok, time.monotonic())
        return ok


# ---------------------------------------------------------------------------
# One-shot generation
# ---------------------------------------------------------------------------

def _generate_ollama(target: Target, system: str, prompt: str, temperature: float, max_tokens: int) -> str:
    response = _ollama_client().post(
        "/api/generate",
        json={
            "model": target.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        },
    )
    _check(response, target)
    return (response.json().get("response") or "").strip()


def _generate_groq(target: Target, system: str, prompt: str, temperature: float, max_tokens: int) -> str:
    response = _groq_client().post(
        "/chat/completions",
        headers=_groq_headers(),
        json={
            "model": target.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        },
    )
    _check(response, target)
    choices = response.json().get("choices") or []
    return ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""


def generate(
    *,
    system: str,
    prompt: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """
    Ask the chain for a completion.

    Returns the trimmed text from the first model that answers, or ``None``
    if every model failed or the feature is off. The caller is expected to
    have a working answer without us.
    """
    global _last_used

    if not config.LLM_ENABLED:
        return None

    temperature = config.LLM_TEMPERATURE if temperature is None else temperature
    max_tokens = config.LLM_MAX_TOKENS if max_tokens is None else max_tokens

    for target in _chain():
        if _cooling(target):
            continue
        started = time.monotonic()
        try:
            call = _generate_groq if target.provider == "groq" else _generate_ollama
            text = call(target, system, prompt, temperature, max_tokens)
        except _TryNext as exc:
            if exc.cooldown:
                _cool(target, str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - timeouts, connection errors
            _cool_after_error(target, exc, f"{type(exc).__name__} after {time.monotonic() - started:.1f}s")
            continue

        if not text:
            # An empty completion is this model's problem, not a reason to
            # bench it — but the next model may still do better.
            continue

        _last_used = target.label
        logger.debug("%s generated %d chars in %.2fs.", target.label, len(text), time.monotonic() - started)
        return text

    logger.warning("Every model in the LLM chain failed or is cooling down.")
    return None


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

async def _stream_ollama(client: httpx.AsyncClient, target: Target, payload: dict) -> AsyncIterator[str]:
    body = {
        "model": target.model,
        "messages": payload["messages"],
        "stream": True,
        "options": {"temperature": payload["temperature"], "num_predict": payload["max_tokens"]},
    }
    async with client.stream("POST", f"{config.OLLAMA_BASE_URL}/api/chat", json=body) as response:
        if response.status_code != 200:
            await response.aread()
            _check(response, target)
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise _TryNext(str(chunk["error"])[:120])
            delta = (chunk.get("message") or {}).get("content") or ""
            if delta:
                yield delta
            if chunk.get("done"):
                return


async def _stream_groq(client: httpx.AsyncClient, target: Target, payload: dict) -> AsyncIterator[str]:
    body = {
        "model": target.model,
        "messages": payload["messages"],
        "temperature": payload["temperature"],
        "max_tokens": payload["max_tokens"],
        "stream": True,
    }
    async with client.stream(
        "POST", f"{config.GROQ_BASE_URL}/chat/completions", json=body, headers=_groq_headers()
    ) as response:
        if response.status_code != 200:
            await response.aread()
            _check(response, target)
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            delta = ((choices[0].get("delta") or {}).get("content") or "") if choices else ""
            if delta:
                yield delta


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

    Walks the same chain as ``generate``, with ``model`` (or
    ``ASSISTANT_MODEL``) tried first. A model can be swapped out only
    **before its first token**: once text has reached the reader, a failure
    ends the stream rather than splicing a second model's answer onto the
    first one's half-sentence.

    Yields nothing at all if every model fails. Never raises.
    """
    global _last_used

    payload = {
        "messages": [{"role": "system", "content": system}, *messages],
        "temperature": config.ASSISTANT_TEMPERATURE if temperature is None else temperature,
        "max_tokens": config.ASSISTANT_MAX_TOKENS if max_tokens is None else max_tokens,
    }
    budget = timeout or config.ASSISTANT_TIMEOUT_SECONDS
    limit = httpx.Timeout(budget, connect=5.0, read=budget)

    async with _async_client(limit) as client:
        for target in _chain(model or config.ASSISTANT_MODEL):
            if _cooling(target):
                continue
            streamer = _stream_groq if target.provider == "groq" else _stream_ollama
            started_output = False
            try:
                async for delta in streamer(client, target, payload):
                    started_output = True
                    yield delta
            except _TryNext as exc:
                if exc.cooldown:
                    _cool(target, str(exc))
                if started_output:
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - timeouts, connection errors
                _cool_after_error(target, exc, type(exc).__name__)
                if started_output:
                    return
                continue

            if started_output:
                _last_used = target.label
                return

    logger.warning("Every model in the LLM chain failed or is cooling down (stream).")


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

def model_name() -> str:
    """The model that answered most recently, or the first in the chain."""
    if _last_used:
        return _last_used
    chain = _chain()
    return chain[0].label if chain else config.OLLAMA_MODEL


def chain_status() -> list[dict[str, object]]:
    """Each model in order, and whether it is currently being skipped."""
    return [
        {"provider": t.provider, "model": t.model, "cooling_down": _cooling(t)}
        for t in _chain()
    ]


def reset_availability_cache() -> None:
    """Forget cached health and cooldowns. Used by tests and after config changes."""
    global _availability, _last_used
    _availability = None
    _last_used = None
    with _cooldown_lock:
        _cooldowns.clear()
