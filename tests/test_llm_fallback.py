"""
The model fallback chain: local Ollama models in order, then Groq.

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
    monkeypatch.setattr(config, "LLM_COOLDOWN_SECONDS", 60)
    llm.reset_availability_cache()
    yield
    llm.reset_availability_cache()


def _install(monkeypatch, behaviour):
    """
    Route every provider call through ``behaviour(provider, model, stream)``,
    which returns ``(status, body)``. Returns the list of calls made.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        provider = "groq" if "chat/completions" in request.url.path else "ollama"
        model = payload["model"]
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
    monkeypatch.setattr(
        llm, "_groq_client", lambda: httpx.Client(base_url="http://groq.test", transport=transport)
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
