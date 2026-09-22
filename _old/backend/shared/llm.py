"""
LLM access: local Ollama models, then Groq, then Gemini (with key rotation).

Three rules shape this module.

**It is never in the critical path.** Every entry point returns ``None`` (or
an empty stream) rather than raising. If every model is down, slow, or rate
limited, the caller falls back to the behaviour it had before there was a
model — a verbatim answer or a decline. A bot must not stop answering because
an optional component is unavailable.

**One saturated model is not an outage.** Generation walks a chain, in the
provider order ``LLM_PROVIDER_ORDER`` (default ``ollama,groq,gemini``):

- ``OLLAMA_MODELS`` in order;
- ``GROQ_MODELS`` in order;
- ``GEMINI_MODELS`` in order, each tried with every key in
  ``GEMINI_API_KEYS`` before moving to the next model. Free-tier quotas are
  per project *per model*, so a model exhausted on one key usually still
  answers on the next.

A target that answers 429 (rate limited), 503 (busy), 5xx, 404 (missing), or
times out is put on a cooldown and the next one is tried. A key that is
rejected outright (bad, revoked, or disabled) benches every target using it.

**It never sees more than it needs.** The grounded path passes retrieved
passages and nothing else. Note the data-residency trade: the Ollama models
run on this machine, but Groq and Gemini are hosted APIs — when either one
answers, the prompt has left the server. Leave their keys unset to stay local.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Literal

import httpx

from backend.shared import config
from backend.shared.mongo import coll, register_indexes

logger = logging.getLogger(__name__)

Provider = Literal["ollama", "groq", "gemini"]
HOSTED: tuple[str, ...] = ("groq", "gemini")


@dataclass(frozen=True)
class Target:
    provider: Provider
    model: str
    # Which of the provider's keys this target uses. Only Gemini rotates keys;
    # the index (never the key itself) is what appears in logs.
    key_index: int = 0

    @property
    def label(self) -> str:
        if self.provider == "gemini" and len(config.GEMINI_API_KEYS) > 1:
            return f"gemini:{self.model}#key{self.key_index + 1}"
        return f"{self.provider}:{self.model}"

    @property
    def api_key(self) -> str:
        if self.provider == "groq":
            return config.GROQ_API_KEY
        if self.provider == "gemini":
            return config.GEMINI_API_KEYS[self.key_index]
        return ""


class _TryNext(Exception):
    """This target can't answer right now; move down the chain."""

    def __init__(self, reason: str, cooldown: bool = True):
        super().__init__(reason)
        self.cooldown = cooldown


class _BadKey(_TryNext):
    """The provider rejected the key itself; every target using it is benched."""


# Status codes that mean "this model, not this request" — worth trying the
# next model.
_RETRYABLE_STATUS = {404, 408, 409, 413, 429, 500, 502, 503, 504}

# Google answers an invalid or revoked key with 400 INVALID_ARGUMENT, not 401,
# so a 400 has to be read before it can be told apart from a bad request.
_BAD_KEY_TEXT = re.compile(
    r"api[ _]?key|auth(entication)? key|unauthenticated|permission", re.IGNORECASE
)

# One shared client per thread keeps connections warm.
_LOCAL = threading.local()

# Availability is cached: a bot with the feature on would otherwise pay a
# failed connection attempt on every single request while Ollama is down.
_AVAILABILITY_TTL_SECONDS = 30.0
_availability: tuple[bool, float] | None = None
_availability_lock = threading.Lock()

# Cooldowns live in MongoDB so every worker skips the same saturated model and
# a restart doesn't forget it. A TTL index deletes them once they end. If
# MongoDB itself is unreachable, this module must still work: it falls back to
# a per-process dict rather than letting a database error stop generation.
LLM_COOLDOWNS = "llm_cooldowns"

register_indexes(
    LLM_COOLDOWNS,
    [([("until", 1)], {"name": "until_ttl", "expireAfterSeconds": 0})],
)

_fallback_cooldowns: dict[str, float] = {}  # label -> Unix time it ends
_cooldown_lock = threading.Lock()

# The model that produced the most recent successful answer, for display.
_last_used: str | None = None


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------

def _provider_targets(provider: str, first_ollama_model: str | None = None) -> list[Target]:
    if provider == "ollama":
        models = list(config.OLLAMA_MODELS)
        if first_ollama_model:
            models = [first_ollama_model, *[m for m in models if m != first_ollama_model]]
        return [Target("ollama", m) for m in models]
    if provider == "groq" and config.GROQ_API_KEY:
        return [Target("groq", m) for m in config.GROQ_MODELS]
    if provider == "gemini" and config.GEMINI_API_KEYS:
        return [
            Target("gemini", m, i)
            for m in config.GEMINI_MODELS
            for i in range(len(config.GEMINI_API_KEYS))
        ]
    return []


def _chain(first_ollama_model: str | None = None) -> list[Target]:
    targets: list[Target] = []
    for provider in config.LLM_PROVIDER_ORDER:
        targets += _provider_targets(provider, first_ollama_model)
    return targets


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cooling(target: Target) -> bool:
    try:
        doc = coll(LLM_COOLDOWNS).find_one({"_id": target.label}, {"until": 1})
        return bool(doc and doc["until"] > _utcnow())
    except Exception:  # noqa: BLE001 - the database must never stop generation
        with _cooldown_lock:
            return _fallback_cooldowns.get(target.label, 0.0) > time.time()


def _cool(target: Target, reason: str, seconds: float | None = None) -> None:
    seconds = seconds if seconds and seconds > 0 else config.LLM_COOLDOWN_SECONDS
    until = _utcnow() + timedelta(seconds=seconds)
    try:
        coll(LLM_COOLDOWNS).update_one(
            {"_id": target.label},
            {"$set": {"until": until, "reason": reason[:200]}},
            upsert=True,
        )
    except Exception:  # noqa: BLE001 - see _cooling
        with _cooldown_lock:
            _fallback_cooldowns[target.label] = time.time() + seconds
    logger.warning("LLM %s unavailable (%s); skipping it for %.0fs.", target.label, reason, seconds)


def _bench_key(target: Target, reason: str) -> None:
    """A rejected key won't start working in a minute: bench it for every model."""
    for other in _provider_targets(target.provider):
        if other.key_index == target.key_index:
            _cool(other, reason, config.LLM_BAD_KEY_COOLDOWN_SECONDS)


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
    """Seconds the provider asked us to wait: Retry-After, or Google's retryDelay."""
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        pass
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', response.text or "")
    return float(match.group(1)) if match else None


def _check(response: httpx.Response, target: Target) -> None:
    status = response.status_code
    if status == 200:
        return
    if status in (401, 403) or (
        status == 400 and target.provider in HOSTED and _BAD_KEY_TEXT.search(response.text or "")
    ):
        raise _BadKey(f"key rejected ({status})")
    if status == 429:
        _cool(target, "rate limited", _retry_after(response))
        raise _TryNext("rate limited", cooldown=False)
    if status in _RETRYABLE_STATUS or status >= 500:
        raise _TryNext(f"HTTP {status}")
    raise _TryNext(f"HTTP {status}", cooldown=False)


def _handle_failure(target: Target, exc: _TryNext) -> None:
    if isinstance(exc, _BadKey):
        _bench_key(target, str(exc))
    elif exc.cooldown:
        _cool(target, str(exc))


def _hosted_base_url(provider: str) -> str:
    return config.GEMINI_BASE_URL if provider == "gemini" else config.GROQ_BASE_URL


def _hosted_timeout(provider: str) -> float:
    return config.GEMINI_TIMEOUT_SECONDS if provider == "gemini" else config.GROQ_TIMEOUT_SECONDS


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


def _hosted_client(provider: str) -> httpx.Client:
    """Groq and Gemini both speak the OpenAI chat-completions protocol."""
    client = getattr(_LOCAL, provider, None)
    if client is None:
        client = httpx.Client(
            base_url=_hosted_base_url(provider),
            timeout=httpx.Timeout(_hosted_timeout(provider), connect=5.0),
        )
        setattr(_LOCAL, provider, client)
    return client


def _async_client(limit: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=limit)


def available() -> bool:
    """
    Whether any model in the chain is reachable right now.

    Cached for a few seconds, and must never raise. True when Ollama answers
    its health check, or when a Groq or Gemini key is configured.

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

        # Hosted providers are checked by configuration, not by a network call:
        # pinging them every 30s would spend their rate limits on health checks.
        ok = ok or any(t.provider in HOSTED for t in _chain())

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


def _generate_hosted(target: Target, system: str, prompt: str, temperature: float, max_tokens: int) -> str:
    response = _hosted_client(target.provider).post(
        "/chat/completions",
        headers={"Authorization": f"Bearer {target.api_key}"},
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
            call = _generate_hosted if target.provider in HOSTED else _generate_ollama
            text = call(target, system, prompt, temperature, max_tokens)
        except _TryNext as exc:
            _handle_failure(target, exc)
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


async def _stream_hosted(client: httpx.AsyncClient, target: Target, payload: dict) -> AsyncIterator[str]:
    body = {
        "model": target.model,
        "messages": payload["messages"],
        "temperature": payload["temperature"],
        "max_tokens": payload["max_tokens"],
        "stream": True,
    }
    async with client.stream(
        "POST",
        f"{_hosted_base_url(target.provider)}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {target.api_key}"},
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
            streamer = _stream_hosted if target.provider in HOSTED else _stream_ollama
            started_output = False
            try:
                async for delta in streamer(client, target, payload):
                    started_output = True
                    yield delta
            except _TryNext as exc:
                _handle_failure(target, exc)
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
        {"target": t.label, "provider": t.provider, "model": t.model, "cooling_down": _cooling(t)}
        for t in _chain()
    ]


def reset_availability_cache() -> None:
    """Forget cached health and cooldowns. Used by tests and after config changes."""
    global _availability, _last_used
    _availability = None
    _last_used = None
    with _cooldown_lock:
        _fallback_cooldowns.clear()
    try:
        coll(LLM_COOLDOWNS).delete_many({})
    except Exception:  # noqa: BLE001 - nothing to clear if the database is down
        pass
