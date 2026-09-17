"""
The model fallback chain: local Ollama models, then Groq, then Gemini with
several keys.

No real model is called — both providers are replaced by an in-process
transport that answers however each test scripts it.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.shared import config, llm


@pytest.fixture(autouse=True)
def _chain_config(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "OLLAMA_MODELS", ["m1", "m2", "m3"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(config, "GROQ_MODELS", ["g1"])
    monkeypatch.setattr(config, "GEMINI_API_KEYS", [])
    monkeypatch.setattr(config, "GEMINI_MODELS", ["gm1", "gm2"])
    monkeypatch.setattr(config, "LLM_PROVIDER_ORDER", ["ollama", "groq", "gemini"])
    monkeypatch.setattr(config, "LLM_COOLDOWN_SECONDS", 60)
    llm.reset_availability_cache()
    yield
    llm.reset_availability_cache()


GEMINI_KEYS = ["gem_key_a", "gem_key_b", "gem_key_c"]


def _install(monkeypatch, behaviour):
    """
    Route every provider call through ``behaviour(provider, model, stream)``,
    which returns ``(status, body)``. Returns the list of calls made.

    Gemini calls are recorded as ``gemini:<model>#<n>``, where n is the
    1-based index of the key that was sent.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        host = request.url.host
        provider = "gemini" if "gemini" in host else "groq" if "groq" in host else "ollama"
        model = payload["model"]
        if provider == "gemini":
            key = request.headers["authorization"].removeprefix("Bearer ")
            calls.append(f"gemini:{model}#{GEMINI_KEYS.index(key) + 1}")
            model = f"{model}#{GEMINI_KEYS.index(key) + 1}"
        else:
            calls.append(f"{provider}:{model}")
        if provider == "groq":
            assert request.headers["authorization"] == "Bearer gsk_test"
        status, body = behaviour(provider, model, payload.get("stream", False))
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        llm, "_ollama_client", lambda: httpx.Client(base_url="http://ollama.test", transport=transport)
    )
    monkeypatch.setattr(config, "GROQ_BASE_URL", "http://groq.test")
    monkeypatch.setattr(config, "GEMINI_BASE_URL", "http://gemini.test")
    monkeypatch.setattr(
        llm,
        "_hosted_client",
        lambda provider: httpx.Client(base_url=llm._hosted_base_url(provider), transport=transport),
    )
    monkeypatch.setattr(
        llm, "_async_client", lambda limit: httpx.AsyncClient(transport=transport, timeout=limit)
    )
    return calls


def _ollama_ok(text):
    return 200, {"response": text}


def _groq_ok(text):
    return 200, {"choices": [{"message": {"content": text}}]}


def test_first_local_model_answers(monkeypatch):
    calls = _install(monkeypatch, lambda p, m, s: _ollama_ok(f"from {m}"))
    assert llm.generate(system="s", prompt="p") == "from m1"
    assert calls == ["ollama:m1"]
    assert llm.model_name() == "ollama:m1"


def test_rate_limited_model_falls_through_to_the_next(monkeypatch):
    def behaviour(provider, model, stream):
        return (429, {"error": "rate limit"}) if model == "m1" else _ollama_ok(f"from {model}")

    calls = _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "from m2"
    assert calls == ["ollama:m1", "ollama:m2"]


def test_groq_is_used_only_after_every_local_model_fails(monkeypatch):
    def behaviour(provider, model, stream):
        if provider == "ollama":
            return 503, {"error": "busy"}
        return _groq_ok("from groq")

    calls = _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "from groq"
    assert calls == ["ollama:m1", "ollama:m2", "ollama:m3", "groq:g1"]
    assert llm.model_name() == "groq:g1"


def test_a_failed_model_is_skipped_during_its_cooldown(monkeypatch):
    def behaviour(provider, model, stream):
        return (429, {}) if model == "m1" else _ollama_ok("ok")

    calls = _install(monkeypatch, behaviour)
    llm.generate(system="s", prompt="p")
    calls.clear()

    llm.generate(system="s", prompt="p")
    assert calls == ["ollama:m2"]


def test_timeouts_count_as_failures(monkeypatch):
    def behaviour(provider, model, stream):
        if model == "m1":
            raise httpx.ReadTimeout("slow")
        return _ollama_ok("second")

    _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "second"


def test_everything_failing_returns_none_not_an_exception(monkeypatch):
    _install(monkeypatch, lambda p, m, s: (500, {"error": "down"}))
    assert llm.generate(system="s", prompt="p") is None


def test_without_a_groq_key_the_chain_stays_local(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    calls = _install(monkeypatch, lambda p, m, s: (503, {}))
    assert llm.generate(system="s", prompt="p") is None
    assert all(c.startswith("ollama:") for c in calls)


def test_a_groq_key_makes_the_chain_available_without_ollama(monkeypatch):
    def refuse():
        raise httpx.ConnectError("no ollama")

    monkeypatch.setattr(llm, "_ollama_client", lambda: type("C", (), {"get": lambda *a, **k: refuse()})())
    assert llm.available() is True


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def _collect(**kwargs) -> str:
    async def run():
        return "".join([d async for d in llm.chat_stream(system="s", messages=[], **kwargs)])

    return asyncio.run(run())


def _ollama_stream(*parts):
    lines = [json.dumps({"message": {"content": p}, "done": False}) for p in parts]
    lines.append(json.dumps({"done": True}))
    return 200, "\n".join(lines)


def _groq_stream(*parts):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}" for p in parts]
    lines.append("data: [DONE]")
    return 200, "\n\n".join(lines)


def test_stream_falls_back_to_groq(monkeypatch):
    def behaviour(provider, model, stream):
        assert stream is True
        if provider == "ollama":
            return 429, {}
        return _groq_stream("Hel", "lo")

    monkeypatch.setattr(config, "ASSISTANT_MODEL", "m1")
    calls = _install(monkeypatch, behaviour)
    assert _collect() == "Hello"
    assert calls == ["ollama:m1", "ollama:m2", "ollama:m3", "groq:g1"]


def test_stream_uses_the_first_healthy_local_model(monkeypatch):
    def behaviour(provider, model, stream):
        return (404, {"error": "model not found"}) if model == "m1" else _ollama_stream("a", "b")

    monkeypatch.setattr(config, "ASSISTANT_MODEL", "m1")
    _install(monkeypatch, behaviour)
    assert _collect() == "ab"
    assert llm.model_name() == "ollama:m2"


def test_an_unreachable_ollama_server_benches_every_local_model_at_once(monkeypatch):
    def behaviour(provider, model, stream):
        if provider == "ollama":
            raise httpx.ConnectError("connection refused")
        return _groq_ok("from groq")

    calls = _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "from groq"
    assert calls == ["ollama:m1", "groq:g1"]


# ---------------------------------------------------------------------------
# Gemini and key rotation
# ---------------------------------------------------------------------------

@pytest.fixture()
def gemini_only(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEYS", list(GEMINI_KEYS))
    monkeypatch.setattr(config, "LLM_PROVIDER_ORDER", ["gemini"])


def test_gemini_comes_after_groq(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEYS", list(GEMINI_KEYS))

    def behaviour(provider, model, stream):
        return _groq_ok("from gemini") if provider == "gemini" else (503, {})

    calls = _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "from gemini"
    assert calls == ["ollama:m1", "ollama:m2", "ollama:m3", "groq:g1", "gemini:gm1#1"]
    assert llm.model_name() == "gemini:gm1#key1"


def test_a_rate_limited_gemini_key_switches_to_the_next_key(monkeypatch, gemini_only):
    def behaviour(provider, model, stream):
        return (429, {}) if model in ("gm1#1", "gm1#2") else _groq_ok(f"answered by {model}")

    calls = _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "answered by gm1#3"
    assert calls == ["gemini:gm1#1", "gemini:gm1#2", "gemini:gm1#3"]

    # The two exhausted keys are skipped for this model on the next request.
    calls.clear()
    llm.generate(system="s", prompt="p")
    assert calls == ["gemini:gm1#3"]


def test_every_key_exhausted_on_a_model_moves_to_the_next_model(monkeypatch, gemini_only):
    def behaviour(provider, model, stream):
        return (429, {}) if model.startswith("gm1") else _groq_ok("second model")

    calls = _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "second model"
    assert calls == ["gemini:gm1#1", "gemini:gm1#2", "gemini:gm1#3", "gemini:gm2#1"]


def test_a_rejected_key_is_benched_for_every_model(monkeypatch, gemini_only):
    # Google reports an invalid key as 400, not 401.
    bad_key = (400, [{"error": {"code": 400, "message": "Invalid Auth key.", "status": "INVALID_ARGUMENT"}}])

    def behaviour(provider, model, stream):
        return bad_key if model.endswith("#1") else _groq_ok("ok")

    calls = _install(monkeypatch, behaviour)
    assert llm.generate(system="s", prompt="p") == "ok"
    assert calls == ["gemini:gm1#1", "gemini:gm1#2"]

    status = {row["target"]: row["cooling_down"] for row in llm.chain_status()}
    assert status["gemini:gm1#key1"] and status["gemini:gm2#key1"]
    assert not status["gemini:gm1#key2"]


def test_a_plain_bad_request_does_not_bench_the_key(monkeypatch, gemini_only):
    def behaviour(provider, model, stream):
        return (400, {"error": {"message": "max_tokens too large"}}) if model == "gm1#1" else _groq_ok("ok")

    _install(monkeypatch, behaviour)
    llm.generate(system="s", prompt="p")
    status = {row["target"]: row["cooling_down"] for row in llm.chain_status()}
    assert not status["gemini:gm2#key1"]


def test_googles_retry_delay_sets_the_cooldown(monkeypatch, gemini_only):
    body = [{"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}}]
    response = httpx.Response(429, json=body)
    assert llm._retry_after(response) == 7.0


def test_gemini_streams_on_the_next_key(monkeypatch, gemini_only):
    def behaviour(provider, model, stream):
        return (503, {}) if model == "gm1#1" else _groq_stream("Hi ", "there")

    calls = _install(monkeypatch, behaviour)
    assert _collect() == "Hi there"
    assert calls == ["gemini:gm1#1", "gemini:gm1#2"]


def test_removing_a_provider_from_the_order_skips_it(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEYS", list(GEMINI_KEYS))
    monkeypatch.setattr(config, "LLM_PROVIDER_ORDER", ["gemini", "ollama"])
    assert [t.provider for t in llm._chain()][:1] == ["gemini"]
    assert "groq" not in {t.provider for t in llm._chain()}


def test_keys_never_appear_in_labels(monkeypatch, gemini_only):
    for target in llm._chain():
        assert not any(key in target.label for key in GEMINI_KEYS)
