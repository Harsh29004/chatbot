"""
Tests for client crash reporting.

This endpoint is unauthenticated by necessity — a crash on the sign-in page
still needs reporting — which makes it the most exposed write path in the
application. The tests here are mostly about that: it must absorb hostile
input, cap what it stores, and never fail in a way that gives a crashing page
something new to crash on.
"""

from __future__ import annotations

import pytest

from backend.shared import config, telemetry_store


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "TELEMETRY_ENABLED", True)
    monkeypatch.setattr(config, "TELEMETRY_MAX_ERRORS_PER_IP_PER_HOUR", 60)


class TestRecording:
    def test_a_report_is_stored_and_read_back(self):
        telemetry_store.record_error(
            message="Cannot read properties of undefined",
            stack="Error\n  at Dashboard (index.js:1:2)",
            kind="react",
            fingerprint="abc123",
            route="/dashboard",
            release="v1",
        )

        rows = telemetry_store.recent_errors()
        assert len(rows) == 1
        assert rows[0]["message"] == "Cannot read properties of undefined"
        assert rows[0]["route"] == "/dashboard"

    def test_long_fields_are_truncated(self):
        """
        The store is the last line of defence, after Pydantic. A 2 MB stack
        reaching MongoDB is a write amplification bug waiting for one broken
        deploy to find it.
        """
        telemetry_store.record_error(
            message="x" * 5000,
            stack="y" * 50000,
            route="z" * 5000,
        )

        row = telemetry_store.recent_errors()[0]
        assert len(row["message"]) == telemetry_store.MAX_MESSAGE
        assert len(row["stack"]) == telemetry_store.MAX_STACK
        assert len(row["route"]) == telemetry_store.MAX_URL

    def test_context_keys_are_flattened_and_capped(self):
        """
        A dotted or ``$``-prefixed key from a browser is a write MongoDB
        rejects outright, so they are rewritten rather than passed through.
        """
        telemetry_store.record_error(
            message="boom",
            context={"a.b": 1, "$set": 2, "ok": "fine"},
        )

        context = telemetry_store.recent_errors()[0]["context"]
        assert "a_b" in context
        assert "_set" in context
        assert context["ok"] == "fine"

    def test_a_non_dict_context_is_ignored(self):
        telemetry_store.record_error(message="boom", context=["not", "a", "dict"])
        assert telemetry_store.recent_errors()[0]["context"] == {}

    def test_recording_never_raises(self):
        """
        The contract this module exists to keep. A failure to log a crash
        must not itself become one — the caller is an endpoint whose whole
        job is absorbing bad news.
        """

        class Hostile:
            def __str__(self):
                raise RuntimeError("nope")

        telemetry_store.record_error(message="ok", context={"bad": Hostile()})
        assert telemetry_store.recent_errors()[0]["context"]["bad"] == ""

    def test_nothing_is_stored_when_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "TELEMETRY_ENABLED", False)
        telemetry_store.record_error(message="boom")
        assert telemetry_store.recent_errors() == []

    def test_every_report_gets_an_expiry(self):
        """
        The TTL index is what stops one broken deploy filling the disk. If
        the field stops being written the index silently keeps nothing.
        """
        telemetry_store.record_error(message="boom")
        from backend.shared.mongo import coll

        stored = coll(telemetry_store.COLLECTION).find_one({})
        assert stored["expires_at"] is not None
        assert stored["expires_at"] > stored["at"]


class TestGrouping:
    def test_identical_fingerprints_group_into_one_row(self):
        """
        The whole job of a crash reporter. A raw feed of four hundred
        reports is one bug four hundred times, and nobody acts on it.
        """
        for _ in range(5):
            telemetry_store.record_error(
                message="same bug", fingerprint="same", route="/dashboard"
            )
        telemetry_store.record_error(
            message="other bug", fingerprint="other", route="/signin"
        )

        groups = telemetry_store.error_groups()
        by_id = {g["fingerprint"]: g for g in groups}

        assert by_id["same"]["count"] == 5
        assert by_id["other"]["count"] == 1
        # Most frequent first, so the worst thing is the first thing read.
        assert groups[0]["fingerprint"] == "same"

    def test_affected_users_counts_people_not_reports(self):
        """
        One person reloading a broken page twenty times is one person with a
        problem, not twenty. Anonymous reports must not inflate the count.
        """
        for _ in range(3):
            telemetry_store.record_error(
                message="bug", fingerprint="f", customer_id="cust-1"
            )
        telemetry_store.record_error(message="bug", fingerprint="f", customer_id="cust-2")
        telemetry_store.record_error(message="bug", fingerprint="f", customer_id=None)

        group = telemetry_store.error_groups()[0]
        assert group["count"] == 5
        assert group["affected_users"] == 2


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------

class TestEndpoint:
    """
    ``POST /api/telemetry/error`` — unauthenticated by necessity, because a
    crash on the sign-in page still needs reporting. That makes it the most
    exposed write path in the application.
    """

    def test_a_report_is_accepted_and_stored(self):
        from fastapi.testclient import TestClient

        from backend.server import app

        with TestClient(app) as client:
            response = client.post(
                "/api/telemetry/error",
                json={
                    "message": "Cannot read properties of undefined",
                    "stack": "Error\n  at Dashboard",
                    "kind": "react",
                    "fingerprint": "abc123",
                    "route": "/dashboard",
                    "release": "v1",
                    "fatal": True,
                },
            )

        assert response.status_code == 202
        stored = telemetry_store.recent_errors()
        assert len(stored) == 1
        assert stored[0]["fingerprint"] == "abc123"
        assert stored[0]["fatal"] is True
        # The user agent comes from the request, not the body — a client
        # cannot claim to be someone else's browser.
        assert stored[0]["user_agent"]

    def test_an_oversized_payload_is_rejected_before_storage(self):
        """
        Pydantic's caps are the first line; the store clips again. A 2 MB
        stack must not reach MongoDB just because someone asked nicely.
        """
        from fastapi.testclient import TestClient

        from backend.server import app

        with TestClient(app) as client:
            response = client.post(
                "/api/telemetry/error",
                json={"message": "x" * 5000, "stack": "y" * 100000},
            )

        assert response.status_code == 422
        assert telemetry_store.recent_errors() == []

    def test_reports_past_the_hourly_cap_are_dropped_not_errored(self, monkeypatch):
        """
        A page stuck in a render-crash loop emits hundreds of reports a
        second. It is throttled — and still answered 202, because a crashing
        page must not also be handling errors from its crash reporter.
        """
        from fastapi.testclient import TestClient

        from backend.server import app

        monkeypatch.setattr(config, "TELEMETRY_MAX_ERRORS_PER_IP_PER_HOUR", 3)

        with TestClient(app) as client:
            for i in range(6):
                response = client.post(
                    "/api/telemetry/error", json={"message": f"boom {i}"}
                )
                assert response.status_code == 202

        assert len(telemetry_store.recent_errors()) == 3

    def test_nothing_is_stored_when_telemetry_is_disabled(self, monkeypatch):
        from fastapi.testclient import TestClient

        from backend.server import app

        monkeypatch.setattr(config, "TELEMETRY_ENABLED", False)

        with TestClient(app) as client:
            response = client.post("/api/telemetry/error", json={"message": "boom"})

        assert response.status_code == 202
        assert response.json()["status"] == "disabled"
        assert telemetry_store.recent_errors() == []

    def test_an_anonymous_report_is_still_kept(self):
        """No session, no problem — the stack is the point, not the name."""
        from fastapi.testclient import TestClient

        from backend.server import app

        with TestClient(app) as client:
            client.post("/api/telemetry/error", json={"message": "boom"})

        assert telemetry_store.recent_errors()[0]["customer_id"] is None
