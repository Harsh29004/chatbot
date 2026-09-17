"""
Staff sign-in with a username and password.

The token it returns must open the same admin routes the raw key does, expire
on time, stop working when the password changes, and the endpoint must refuse
to be brute-forced.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.admin import session
from backend.shared import config


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(config, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "correct horse battery")
    monkeypatch.setattr(config, "ADMIN_SESSION_HOURS", 12)
    # verify_admin_key imported the key at module load; keep it in step.
    monkeypatch.setattr("backend.shared.auth.ADMIN_API_KEY", "test-admin-key")
    yield


@pytest.fixture()
def client() -> TestClient:
    from backend.server import app

    with TestClient(app) as test_client:
        yield test_client


def _login(client, username="admin", password="correct horse battery"):
    return client.post("/api/admin/login", json={"username": username, "password": password})


def test_right_credentials_return_a_token_that_opens_admin_routes(client):
    response = _login(client)
    assert response.status_code == 200
    token = response.json()["token"]
    assert token.startswith("nxa.")

    health = client.get("/api/admin/health", headers={"X-Admin-Key": token})
    assert health.status_code == 200

    inbox = client.get("/api/support/admin/conversations", headers={"X-Admin-Key": token})
    assert inbox.status_code == 200


def test_the_raw_admin_key_still_works_for_scripts(client):
    assert client.get("/api/admin/health", headers={"X-Admin-Key": "test-admin-key"}).status_code == 200


@pytest.mark.parametrize("username,password", [("admin", "wrong"), ("root", "correct horse battery")])
def test_wrong_credentials_are_refused_with_one_message(client, username, password):
    response = _login(client, username, password)
    assert response.status_code == 401
    assert response.json()["detail"] == "Wrong username or password."


def test_a_forged_token_is_refused(client):
    token = _login(client).json()["token"]
    forged = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get("/api/admin/health", headers={"X-Admin-Key": forged}).status_code == 403


def test_tokens_expire():
    token, expires_at = session.issue_token(now=1_000_000)
    assert session.verify_token(token, now=expires_at - 1)
    assert not session.verify_token(token, now=expires_at + 1)


def test_changing_the_password_invalidates_issued_tokens(monkeypatch):
    token, _ = session.issue_token()
    assert session.verify_token(token)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "a brand new password")
    assert not session.verify_token(token)


def test_repeated_failures_lock_the_address_out(client):
    for _ in range(session.MAX_FAILURES):
        assert _login(client, password="nope").status_code == 401
    # Even the right password is refused while locked out.
    assert _login(client).status_code == 429


def test_password_sign_in_is_off_without_a_password(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    assert _login(client).status_code == 503
    token, _ = session.issue_token()
    assert not session.verify_token(token)


def test_signup_accepts_an_eight_character_password(client, monkeypatch):
    # Like the other signup tests: the per-network cap would otherwise count
    # these accounts against later tests in the same run.
    from backend.billing import identity

    monkeypatch.setattr(identity, "MAX_SIGNUPS_PER_IP_PER_DAY", 0)
    ok = client.post(
        "/api/auth/signup",
        json={"email": "eight@example.com", "password": "abcd1234", "name": "Eight"},
    )
    assert ok.status_code in (200, 201), ok.text

    short = client.post(
        "/api/auth/signup",
        json={"email": "seven@example.com", "password": "abc1234", "name": "Seven"},
    )
    assert short.status_code == 422
