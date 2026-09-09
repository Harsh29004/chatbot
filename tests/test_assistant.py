"""
Tests for the dashboard assistant.

Two properties carry the weight here.

**No API key reaches it.** The customer-facing contract is a bot that answers
from a sheet. A general-purpose model on the same key would quietly turn the
product into something else, so every assistant route is session-cookie only
and the first test class exists to keep it that way.

**No customer sees another's conversation.** Threads are scoped by
``customer_id`` in the query itself, not by the caller remembering to filter.
"""

from __future__ import annotations

import pytest

from backend.assistant import context as account_context
from backend.assistant import store
from backend.shared import config
from tests.conftest import DEMO_EMAIL, fake_id


# ---------------------------------------------------------------------------
# It is not on any API-key route
# ---------------------------------------------------------------------------

class TestNotReachableByApiKey:
    ASSISTANT_ROUTES = [
        ("get", "/api/assistant/status"),
        ("get", "/api/assistant/threads"),
        ("post", "/api/assistant/threads"),
        ("get", "/api/assistant/threads/1"),
        ("post", "/api/assistant/threads/1/messages"),
    ]

    @pytest.mark.parametrize("method,path", ASSISTANT_ROUTES)
    def test_a_valid_customer_key_gets_nowhere(self, api_client, method, path):
        """
        A working API key is not a way in.

        ``api_client`` overrides ``verify_api_key``, so this key is as valid as
        one gets. The assistant depends on ``current_customer`` instead, which
        reads the session cookie — so all of these must refuse.
        """
        from backend.shared.api_keys import generate_api_key

        key = generate_api_key("apionly@example.test", "Api Only")["api_key"]
        headers = {"X-Api-Key": key}
        # httpx's GET helper takes no body; only the POST routes get one.
        response = (
            api_client.post(path, headers=headers, json={"message": "hello"})
            if method == "post"
            else api_client.get(path, headers=headers)
        )
        assert response.status_code in {401, 403}

    @pytest.mark.parametrize("method,path", ASSISTANT_ROUTES)
    def test_an_owner_key_gets_nowhere_either(self, api_client, method, path):
        """
        Owner keys are unlimited, not universal.

        They open the cross-tenant ops routes. They are still not a session,
        and the assistant is a signed-in-browser feature.
        """
        from backend.shared.api_keys import create_owner_key

        key = create_owner_key("ownerchat@example.test", "Owner")["api_key"]
        headers = {"X-Api-Key": key}
        # httpx's GET helper takes no body; only the POST routes get one.
        response = (
            api_client.post(path, headers=headers, json={"message": "hello"})
            if method == "post"
            else api_client.get(path, headers=headers)
        )
        assert response.status_code in {401, 403}

    def test_no_assistant_route_is_registered_under_v1(self, api_client):
        """
        ``/v1`` is the customer-integrated surface.

        Nothing from the assistant may appear there, however it is authed —
        that prefix is the promise customers build against.
        """
        paths = api_client.get("/openapi.json").json()["paths"]
        assistant_paths = [p for p in paths if "assistant" in p]
        assert assistant_paths, "the assistant routes should exist at all"
        assert all(p.startswith("/api/assistant") for p in assistant_paths)
        assert not any(p.startswith("/v1") for p in assistant_paths)


# ---------------------------------------------------------------------------
# Conversation storage, scoped
# ---------------------------------------------------------------------------

class TestThreadScoping:
    def test_a_thread_belongs_to_one_customer(self):
        mine = store.create_thread(fake_id(1), "Mine")
        assert store.get_thread(fake_id(1), mine["id"]) is not None
        assert store.get_thread(fake_id(2), mine["id"]) is None

    def test_listing_never_crosses_customers(self):
        store.create_thread(fake_id(10), "A")
        store.create_thread(fake_id(10), "B")
        store.create_thread(fake_id(11), "C")
        assert len(store.list_threads(fake_id(10))) == 2
        assert len(store.list_threads(fake_id(11))) == 1

    def test_messages_cannot_be_appended_to_someone_elses_thread(self):
        """Returns None rather than writing into a conversation that isn't theirs."""
        thread = store.create_thread(fake_id(20), "Theirs")
        assert store.add_message(fake_id(21), thread["id"], store.ROLE_USER, "hello") is None
        assert store.list_messages(fake_id(20), thread["id"]) == []

    def test_messages_are_not_readable_across_customers(self):
        thread = store.create_thread(fake_id(30), "Private")
        store.add_message(fake_id(30), thread["id"], store.ROLE_USER, "a secret plan")
        assert len(store.list_messages(fake_id(30), thread["id"])) == 1
        assert store.list_messages(fake_id(31), thread["id"]) == []

    def test_deleting_removes_the_messages_too(self):
        """A thread whose messages outlived it would be invisible but still stored."""
        thread = store.create_thread(fake_id(40), "Temp")
        store.add_message(fake_id(40), thread["id"], store.ROLE_USER, "hi")
        assert store.delete_thread(fake_id(40), thread["id"]) is True
        assert store.get_thread(fake_id(40), thread["id"]) is None
        assert store.list_messages(fake_id(40), thread["id"]) == []

    def test_deleting_someone_elses_thread_does_nothing(self):
        thread = store.create_thread(fake_id(50), "Safe")
        assert store.delete_thread(fake_id(51), thread["id"]) is False
        assert store.get_thread(fake_id(50), thread["id"]) is not None


# ---------------------------------------------------------------------------
# History handed to the model
# ---------------------------------------------------------------------------

class TestHistory:
    def test_history_is_capped_but_the_thread_is_not(self, monkeypatch):
        """
        Two different jobs, two different limits.

        The person scrolls the whole conversation; the model gets the tail.
        A single limit would either truncate their history or blow the prompt.
        """
        monkeypatch.setattr(config, "ASSISTANT_HISTORY_TURNS", 4)
        thread = store.create_thread(fake_id(60), "Long")
        for i in range(10):
            store.add_message(fake_id(60), thread["id"], store.ROLE_USER, f"message {i}")

        assert len(store.list_messages(fake_id(60), thread["id"])) == 10
        history = store.history_for_model(fake_id(60), thread["id"])
        assert len(history) == 4
        assert history[-1]["content"] == "message 9"

    def test_history_is_shaped_for_ollama(self):
        thread = store.create_thread(fake_id(70), "Shape")
        store.add_message(fake_id(70), thread["id"], store.ROLE_USER, "hello")
        store.add_message(fake_id(70), thread["id"], store.ROLE_ASSISTANT, "hi there")
        history = store.history_for_model(fake_id(70), thread["id"])
        assert history == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    def test_daily_count_only_counts_this_customer_asking(self):
        thread = store.create_thread(fake_id(80), "Counting")
        store.add_message(fake_id(80), thread["id"], store.ROLE_USER, "one")
        store.add_message(fake_id(80), thread["id"], store.ROLE_ASSISTANT, "reply")
        store.add_message(fake_id(80), thread["id"], store.ROLE_USER, "two")

        other = store.create_thread(fake_id(81), "Other")
        store.add_message(fake_id(81), other["id"], store.ROLE_USER, "not mine")

        assert store.count_messages_today(fake_id(80)) == 2
        assert store.count_messages_today(fake_id(81)) == 1


# ---------------------------------------------------------------------------
# Account context
# ---------------------------------------------------------------------------

class TestAccountContext:
    def test_context_is_built_for_one_customer_only(self, demo_bot):
        """
        The assistant's context has no cross-tenant branch at all.

        ``backend/ops.py`` is the module that reads every tenant, and it lives
        behind a different auth dependency for exactly this reason.
        """
        built = account_context.build({"id": fake_id(1), "email": DEMO_EMAIL, "name": "Demo"})
        assert built["email"] == DEMO_EMAIL
        assert built["bot"]["template"] == demo_bot["template_id"]

    def test_a_customer_with_no_api_account_is_described_honestly(self):
        """Signed up but never made a key is a real state, not an error."""
        built = account_context.build(
            {"id": fake_id(999), "email": "nobody@example.test", "name": "Nobody"}
        )
        assert built["has_api_account"] is False
        assert "not created yet" in account_context.render(built)

    def test_render_produces_a_compact_block(self, demo_bot):
        rendered = account_context.render(
            account_context.build({"id": fake_id(1), "email": DEMO_EMAIL, "name": "Demo"})
        )
        assert "Signed in as" in rendered
        assert "Their bot" in rendered
        # Terse on purpose: every token is prompt a CPU re-reads each turn.
        assert len(rendered) < 4000

    def test_context_never_raises_on_a_broken_backend(self, monkeypatch):
        """
        A chat window that 500s because billing hiccuped is worse than one
        that answers without knowing the plan.
        """
        def _boom(*args, **kwargs):
            raise RuntimeError("database on fire")

        monkeypatch.setattr(account_context.billing_db, "get_current_subscription", _boom)
        monkeypatch.setattr(account_context, "get_user_by_email", _boom)

        built = account_context.build({"id": fake_id(1), "email": DEMO_EMAIL, "name": "Demo"})
        assert built["has_api_account"] is False
        assert account_context.render(built)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

class TestInputHandling:
    def test_code_is_allowed_for_the_assistant(self):
        """
        A general assistant that refuses code is useless.

        Code is refused for *bots*, where a model answers the public on a
        customer's behalf. Here the account holder is asking in their own
        dashboard — pasting a stack trace is the job.
        """
        from backend.shared.input_policy import screen

        verdict = screen("why does SELECT * FROM orders fail here?", for_model=False)
        assert verdict.allowed is True

    def test_credentials_are_still_refused(self):
        """They would otherwise sit in the conversation table forever."""
        from backend.shared.input_policy import screen

        verdict = screen("my card is 4111 1111 1111 1111, is it valid?", for_model=False)
        assert verdict.refused is True
        assert verdict.safe_to_log is False
