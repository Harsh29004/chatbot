"""
Credential containment.

Each test here covers a way a token could escape to somewhere it outlives the
request — a log file, an analytics report, another user's response body. Two
of them are regressions for leaks that were real in this codebase, and they
fail loudly if either comes back.
"""

from __future__ import annotations

import io
import logging

from fastapi.testclient import TestClient

from backend.billing import db
from backend.billing.plans import PLANS
from backend.billing.security import hash_password
from backend.shared import config
from backend.shared.api_keys import generate_api_key

# Field names that must never appear in a response body.
SECRET_MARKERS = (
    "password_hash", "pbkdf2_sha256", "token_hash", "key_hash",
    "private_key", "client_secret", "firebase_uid", "google_sub",
)


def _service_account_key() -> str:
    """
    The PEM body, from whichever of the two supported env shapes holds it.

    Both are valid — discrete fields or the whole JSON — and a test that
    reads only one silently turns into ``"" in body``, which is true of every
    string. That is worse than no assertion: it fails on a healthy response
    and passes on nothing.
    """
    import json

    if config.FIREBASE_PRIVATE_KEY:
        return config.FIREBASE_PRIVATE_KEY
    if config.FIREBASE_SERVICE_ACCOUNT_JSON:
        try:
            return json.loads(config.FIREBASE_SERVICE_ACCOUNT_JSON).get("private_key", "")
        except json.JSONDecodeError:
            return ""
    return ""


def _account(email: str) -> dict:
    customer = db.create_customer(email, "U", hash_password("a-long-password-1"))
    db.start_subscription(
        customer["id"], PLANS["trial"], db.STATUS_TRIALING, provider="none"
    )
    return customer


def _capture_firebase_log():
    """Attach a buffer to the firebase_auth logger. Returns (buffer, detach)."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    log = logging.getLogger("backend.billing.firebase_auth")
    log.addHandler(handler)
    log.setLevel(logging.WARNING)
    return buffer, lambda: log.removeHandler(handler)


class TestNothingSecretReachesTheLog:
    def test_a_credential_posted_as_an_id_token_is_not_logged(self):
        """
        Regression.

        Google's token errors quote the input back, as in ``Wrong number of
        segments in token: b'<the token>'``. Harmless for a genuinely
        malformed token — but this endpoint receives whatever the client put
        in the field, and a client bug posting a session cookie or an API key
        there would write a live secret into the log, where it outlives the
        request and is readable by anyone with log access.
        """
        from backend.billing import firebase_auth as fa

        buffer, detach = _capture_firebase_log()
        try:
            secret = "nexora_session_Ab3xQ_A_REAL_LOOKING_SECRET"
            try:
                fa.verify_id_token(secret)
            except fa.FirebaseAuthError:
                pass

            written = buffer.getvalue()
            assert secret not in written
            assert "<redacted>" in written
            # Still useful: the failure type survives, and the short digest
            # lets repeated failures from one caller be correlated.
            assert "InvalidIdTokenError" in written
        finally:
            detach()

    def test_the_admin_credentials_are_never_logged(self):
        from backend.billing import firebase_auth as fa

        buffer, detach = _capture_firebase_log()
        try:
            for secret in (config.ADMIN_API_KEY, config.ADMIN_PASSWORD):
                if not secret:
                    continue
                try:
                    fa.verify_id_token(secret)
                except fa.FirebaseAuthError:
                    pass

            written = buffer.getvalue()
            assert config.ADMIN_API_KEY not in written
            if config.ADMIN_PASSWORD:
                assert config.ADMIN_PASSWORD not in written
        finally:
            detach()


class TestNoResponseCarriesAnotherAccountsSecrets:
    def test_the_customer_api_returns_no_credential_material(self):
        from backend.billing.router import current_customer
        from backend.server import app

        with TestClient(app) as client:
            _account("victim@example.com")
            victim_key = generate_api_key("victim@example.com", "U", "k")["api_key"]
            attacker = _account("attacker@example.com")

            app.dependency_overrides[current_customer] = lambda: attacker
            try:
                for path in [
                    "/api/auth/me", "/api/dashboard", "/api/dashboard/keys",
                    "/api/billing/subscription", "/api/billing/invoices",
                    "/api/referrals", "/api/support/messages",
                    "/api/assistant/threads", "/api/auth/providers",
                ]:
                    response = client.get(path)
                    if response.status_code != 200:
                        continue
                    body = response.text
                    for marker in SECRET_MARKERS:
                        assert marker not in body, f"{path} leaked '{marker}'"
                    assert victim_key not in body, f"{path} leaked another key"
                    assert "victim@example.com" not in body, f"{path} leaked an email"
                    assert config.ADMIN_API_KEY not in body
                    service_key = _service_account_key()
                    assert service_key, "no service account key configured to test against"
                    assert service_key[:40] not in body, f"{path} leaked the service key"
            finally:
                app.dependency_overrides.clear()

    def test_the_admin_api_returns_no_credential_material(self):
        from backend.server import app

        headers = {"X-Admin-Key": config.ADMIN_API_KEY}
        with TestClient(app) as client:
            customer = _account("adminscan@example.com")
            raw_key = generate_api_key("adminscan@example.com", "U", "k")["api_key"]

            for path in [
                "/api/admin/overview", "/api/admin/users",
                f"/api/admin/users/{customer['id']}", "/api/admin/subscriptions",
                "/api/admin/usage", "/api/admin/audit", "/api/admin/ai-usage",
                "/api/admin/crashes", "/api/admin/crashes/recent",
                "/api/admin/templates", "/api/admin/health",
            ]:
                response = client.get(path, headers=headers)
                if response.status_code != 200:
                    continue
                body = response.text
                for marker in SECRET_MARKERS:
                    assert marker not in body, f"{path} leaked '{marker}'"
                assert raw_key not in body, f"{path} leaked a raw API key"
                assert config.ADMIN_API_KEY not in body, f"{path} echoed the admin key"


class TestOneAccountCannotReachAnother:
    def test_revoking_someone_elses_key_is_refused(self):
        """Guess an id, kill a competitor's integration. A scoped update stops it."""
        from backend.billing.router import current_customer
        from backend.server import app
        from backend.shared.api_keys import validate_api_key

        with TestClient(app) as client:
            _account("v2@example.com")
            made = generate_api_key("v2@example.com", "V", "k")
            attacker = _account("a2@example.com")

            app.dependency_overrides[current_customer] = lambda: attacker
            try:
                response = client.delete(f"/api/dashboard/keys/{made['key_id']}")
                assert response.status_code == 404
            finally:
                app.dependency_overrides.clear()

            # The refusal was not a silent revoke.
            assert validate_api_key(made["api_key"]) is not None

    def test_reading_someone_elses_conversation_is_refused(self):
        from backend.assistant import store
        from backend.billing.router import current_customer
        from backend.server import app

        with TestClient(app) as client:
            victim = _account("v3@example.com")
            attacker = _account("a3@example.com")
            thread = store.create_thread(victim["id"], "Private conversation")

            app.dependency_overrides[current_customer] = lambda: attacker
            try:
                for method, path in [
                    ("get", f"/api/assistant/threads/{thread['id']}"),
                    ("put", f"/api/assistant/threads/{thread['id']}"),
                    ("delete", f"/api/assistant/threads/{thread['id']}"),
                ]:
                    kwargs = {"json": {"title": "x"}} if method == "put" else {}
                    response = getattr(client, method)(path, **kwargs)
                    # 404 rather than 403 on purpose: another customer's id
                    # should be indistinguishable from one that never existed.
                    assert response.status_code in (404, 503)
                    assert "Private conversation" not in response.text
            finally:
                app.dependency_overrides.clear()
