"""
Tests for Firebase sign-in.

As with Google sign-in, the happy path is the least interesting part. What
these cover is the set of ways this feature could hand someone an account
that is not theirs:

* accepting a token nobody verified,
* linking on an email address Firebase never confirmed,
* re-pointing an already-linked account at a different Firebase user,
* losing an existing OAuth account behind a duplicate-mailbox refusal when
  the same person arrives through Firebase instead.

Each has a test that fails loudly if the guard is removed.

Token *verification* itself is Google's code and is not re-tested here; what
is tested is that this module calls it, and refuses everything it rejects.
"""

from __future__ import annotations

import pytest

from backend.billing import db, firebase_auth
from backend.billing.security import hash_password
from backend.shared import config


def _claims(**overrides):
    """A verified Google-provider token, as firebase-admin returns it."""
    base = {
        "uid": "firebase-uid-1",
        "sub": "firebase-uid-1",
        "email": "harsh@gmail.com",
        "email_verified": True,
        "name": "Harsh",
        "firebase": {
            "sign_in_provider": "google.com",
            "identities": {
                "google.com": ["109876543210"],
                "email": ["harsh@gmail.com"],
            },
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Claims handling
# ---------------------------------------------------------------------------

class TestIdentityFromClaims:
    def test_a_verified_account_yields_an_identity(self):
        identity = firebase_auth.identity_from_claims(_claims(email="Harsh@Gmail.com"))

        assert identity["firebase_uid"] == "firebase-uid-1"
        # Normalised, because every other lookup in this codebase lowercases.
        assert identity["email"] == "harsh@gmail.com"
        assert identity["name"] == "Harsh"
        assert identity["sign_in_provider"] == "google.com"

    def test_an_unverified_email_is_refused(self):
        """
        The check that makes email-based linking safe.

        A fresh email+password sign-up is unverified until the user clicks
        the link. Treating it as proof of ownership would let anyone who can
        type an address walk into the account that already owns it.
        """
        with pytest.raises(firebase_auth.EmailNotVerifiedError):
            firebase_auth.identity_from_claims(
                _claims(email="victim@example.com", email_verified=False)
            )

    def test_a_missing_verified_flag_is_treated_as_unverified(self):
        """Absent is not the same as true. Default to refusing."""
        claims = _claims()
        del claims["email_verified"]
        with pytest.raises(firebase_auth.EmailNotVerifiedError):
            firebase_auth.identity_from_claims(claims)

    def test_verification_can_be_relaxed_by_configuration(self, monkeypatch):
        """
        The escape hatch exists, and it is off by default.

        A deployment that verifies addresses some other way can turn this
        off; the test pins that the flag is what controls it, so nobody
        "fixes" the refusal by weakening the default.
        """
        monkeypatch.setattr(config, "FIREBASE_REQUIRE_VERIFIED_EMAIL", False)
        identity = firebase_auth.identity_from_claims(_claims(email_verified=False))
        assert identity["email_verified"] is False

    def test_a_token_without_an_email_is_refused(self):
        """Anonymous and phone sign-ins. This platform bills per mailbox."""
        with pytest.raises(firebase_auth.FirebaseAuthError):
            firebase_auth.identity_from_claims(_claims(email=""))

    def test_a_token_without_a_uid_is_refused(self):
        claims = _claims()
        del claims["uid"]
        del claims["sub"]
        with pytest.raises(firebase_auth.FirebaseAuthError):
            firebase_auth.identity_from_claims(claims)

    def test_a_password_signup_falls_back_to_the_local_part_for_a_name(self):
        """
        Firebase gives no display name for an email+password user until one
        is set, and a blank name renders as an empty greeting on every page.
        """
        identity = firebase_auth.identity_from_claims(
            _claims(
                name="",
                email="someone@example.com",
                firebase={"sign_in_provider": "password", "identities": {}},
            )
        )
        assert identity["name"] == "someone"
        assert identity["sign_in_provider"] == "password"

    def test_the_google_sub_is_extracted_for_a_google_sign_in(self):
        """
        The bridge between the two flows.

        An account created by the older server-side OAuth path is keyed on
        Google's ``sub``. Without pulling it back out of the Firebase token,
        the same human arriving through Firebase looks like a stranger and
        collides on the mailbox.
        """
        identity = firebase_auth.identity_from_claims(_claims())
        assert identity["google_sub"] == "109876543210"

    def test_no_google_sub_for_a_password_sign_in(self):
        identity = firebase_auth.identity_from_claims(
            _claims(firebase={"sign_in_provider": "password", "identities": {}})
        )
        assert identity["google_sub"] is None


class TestVerifyIdToken:
    def test_an_empty_token_is_refused_without_calling_firebase(self):
        """
        Refused before the SDK is touched, so an unconfigured server still
        gives the right answer rather than an initialisation error.
        """
        with pytest.raises(firebase_auth.FirebaseAuthError):
            firebase_auth.verify_id_token("")
        with pytest.raises(firebase_auth.FirebaseAuthError):
            firebase_auth.verify_id_token("   ")


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _billing_tables():
    db.init_billing_tables()


class TestFirebaseLinking:
    def test_a_new_uid_creates_an_account_with_no_password(self):
        customer = db.create_customer(
            "new@example.com",
            "New",
            db.NO_PASSWORD,
            firebase_uid="uid-new",
            firebase_provider="google.com",
            auth_provider=db.PROVIDER_FIREBASE,
        )

        assert customer["firebase_uid"] == "uid-new"
        # NO_PASSWORD cannot parse as a hash, so no string authenticates
        # against it. The account is reachable only through Firebase.
        from backend.billing.security import verify_password

        assert verify_password("anything", db.NO_PASSWORD) is False

    def test_lookup_by_uid_finds_the_account(self):
        db.create_customer(
            "u@example.com", "U", db.NO_PASSWORD, firebase_uid="uid-lookup"
        )
        found = db.get_customer_by_firebase_uid("uid-lookup")
        assert found is not None
        assert found["email"] == "u@example.com"

    def test_lookup_by_empty_uid_finds_nothing(self):
        """
        Guards the case where a password-only account stores no uid at all.
        An empty lookup must not match the first such document.
        """
        db.create_customer("p@example.com", "P", hash_password("a-long-password"))
        assert db.get_customer_by_firebase_uid("") is None

    def test_an_existing_password_account_can_be_linked(self):
        """
        The migration path. Somebody who signed up with a password before
        Firebase existed signs in with Google on the same address and keeps
        their account, their keys and their password.
        """
        original = db.create_customer(
            "both@example.com", "Both", hash_password("a-long-password")
        )

        linked = db.link_firebase_account(original["id"], "uid-linked", "google.com")

        assert linked is not None
        assert linked["firebase_uid"] == "uid-linked"
        assert linked["id"] == original["id"]
        # The password still works — they did not lose a way in by gaining one.
        from backend.billing.security import verify_password

        assert verify_password("a-long-password", linked["password_hash"]) is True

    def test_linking_an_already_linked_account_to_someone_else_is_refused(self):
        """
        The takeover this guard exists to stop.

        Once an account belongs to a Firebase user, a *different* uid must
        not be able to claim it. Returning None makes the endpoint answer
        409 rather than silently re-pointing the account.
        """
        customer = db.create_customer(
            "taken@example.com", "Taken", db.NO_PASSWORD, firebase_uid="uid-first"
        )

        result = db.link_firebase_account(customer["id"], "uid-attacker", "password")

        assert result is None
        assert db.get_customer_by_firebase_uid("uid-first")["id"] == customer["id"]

    def test_relinking_the_same_uid_is_idempotent(self):
        """Two tabs, or a retry. Not an error, and not a second account."""
        customer = db.create_customer(
            "same@example.com", "Same", db.NO_PASSWORD, firebase_uid="uid-same"
        )

        result = db.link_firebase_account(customer["id"], "uid-same", "google.com")

        assert result is not None
        assert result["id"] == customer["id"]

    def test_two_accounts_cannot_share_a_firebase_uid(self):
        """
        Enforced by the unique index, not by remembering to check.
        """
        from pymongo.errors import DuplicateKeyError

        db.create_customer("one@example.com", "One", db.NO_PASSWORD, firebase_uid="dup")

        with pytest.raises((db.DuplicateAccountError, DuplicateKeyError)):
            db.create_customer(
                "two@example.com", "Two", db.NO_PASSWORD, firebase_uid="dup"
            )

    def test_password_accounts_do_not_collide_on_a_missing_uid(self):
        """
        The partial index earning its keep.

        Without ``partialFilterExpression`` every account lacking a
        firebase_uid would share the value null and the second signup would
        be rejected as a duplicate.
        """
        db.create_customer("a@example.com", "A", hash_password("a-long-password"))
        db.create_customer("b@example.com", "B", hash_password("a-long-password"))

        assert db.get_customer_by_email("a@example.com") is not None
        assert db.get_customer_by_email("b@example.com") is not None


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------

class TestExchangeEndpoint:
    """
    ``POST /api/auth/firebase`` — the one place a Firebase credential is
    accepted, and the only thing standing between a forged token and a
    session cookie.
    """

    def test_a_token_that_fails_verification_gets_no_session(self, monkeypatch):
        """
        The guard that matters most.

        If verification raises, the endpoint must answer 401 and set no
        cookie. A decoded-but-unverified JWT is a string the sender chose.
        """
        from fastapi.testclient import TestClient

        from backend.billing import firebase_auth as fa
        from backend.server import app

        monkeypatch.setattr(fa, "is_configured", lambda: True)

        def _reject(_token, **_kwargs):
            raise fa.FirebaseAuthError("That sign-in could not be verified.")

        monkeypatch.setattr(fa, "verify_id_token", _reject)

        with TestClient(app) as client:
            response = client.post(
                "/api/auth/firebase", json={"id_token": "forged.token.here"}
            )

        assert response.status_code == 401
        assert "nexora_session" not in response.cookies

    def test_an_unverified_email_answers_403_not_401(self, monkeypatch):
        """
        A distinct status, because it is a distinct situation: the credential
        is good and the mailbox is unconfirmed. The page turns 403 into
        "check your inbox" with a re-send button rather than a dead end.
        """
        from fastapi.testclient import TestClient

        from backend.billing import firebase_auth as fa
        from backend.server import app

        monkeypatch.setattr(fa, "is_configured", lambda: True)
        monkeypatch.setattr(fa, "verify_id_token", lambda _t, **_k: _claims())

        def _unverified(_claims_arg):
            raise fa.EmailNotVerifiedError("Please confirm your email address first")

        monkeypatch.setattr(fa, "identity_from_claims", _unverified)

        with TestClient(app) as client:
            response = client.post("/api/auth/firebase", json={"id_token": "x.y.z"})

        assert response.status_code == 403
        assert "confirm your email" in response.json()["detail"]

    def test_a_verified_token_creates_an_account_and_a_session(self, monkeypatch):
        from fastapi.testclient import TestClient

        from backend.billing import firebase_auth as fa
        from backend.server import app

        monkeypatch.setattr(fa, "is_configured", lambda: True)
        monkeypatch.setattr(fa, "verify_id_token", lambda _t, **_k: _claims())

        with TestClient(app) as client:
            response = client.post("/api/auth/firebase", json={"id_token": "x.y.z"})

            assert response.status_code == 200, response.text
            assert response.json()["email"] == "harsh@gmail.com"
            # The session cookie is the whole point of the exchange.
            assert client.cookies.get("nexora_session")

            # And it works: the cookie alone now authenticates.
            me = client.get("/api/auth/me")
            assert me.status_code == 200
            assert me.json()["email"] == "harsh@gmail.com"

    def test_signing_in_twice_reuses_the_same_account(self, monkeypatch):
        """One Firebase user is one customer, not one customer per sign-in."""
        from fastapi.testclient import TestClient

        from backend.billing import firebase_auth as fa
        from backend.server import app

        monkeypatch.setattr(fa, "is_configured", lambda: True)
        monkeypatch.setattr(fa, "verify_id_token", lambda _t, **_k: _claims())

        with TestClient(app) as client:
            first = client.post("/api/auth/firebase", json={"id_token": "x.y.z"})
            second = client.post("/api/auth/firebase", json={"id_token": "x.y.z"})

        assert first.json()["id"] == second.json()["id"]

    def test_an_existing_oauth_account_is_linked_not_duplicated(self, monkeypatch):
        """
        The bridge between the two sign-in flows.

        Someone who signed up through the old server-side Google OAuth path
        arrives via Firebase Google. Same Google account, different wrapper.
        Without the ``google_sub`` lookup they collide on the mailbox and are
        refused entry to their own account.
        """
        from fastapi.testclient import TestClient

        from backend.billing import firebase_auth as fa
        from backend.server import app

        existing = db.create_customer(
            "harsh@gmail.com",
            "Harsh",
            db.NO_PASSWORD,
            google_sub="109876543210",
            auth_provider=db.PROVIDER_GOOGLE,
        )

        monkeypatch.setattr(fa, "is_configured", lambda: True)
        monkeypatch.setattr(fa, "verify_id_token", lambda _t, **_k: _claims())

        with TestClient(app) as client:
            response = client.post("/api/auth/firebase", json={"id_token": "x.y.z"})

        assert response.status_code == 200, response.text
        assert response.json()["id"] == existing["id"]
        assert db.get_customer_by_firebase_uid("firebase-uid-1")["id"] == existing["id"]

    def test_the_endpoint_is_unavailable_when_unconfigured(self, monkeypatch):
        """
        503, not a 500 from an uninitialised SDK. A deployment without
        Firebase should say so.
        """
        from fastapi.testclient import TestClient

        from backend.billing import firebase_auth as fa
        from backend.server import app

        monkeypatch.setattr(fa, "is_configured", lambda: False)

        with TestClient(app) as client:
            response = client.post("/api/auth/firebase", json={"id_token": "x.y.z"})

        assert response.status_code == 503

    def test_providers_reports_firebase_availability(self, monkeypatch):
        from fastapi.testclient import TestClient

        from backend.billing import firebase_auth as fa
        from backend.server import app

        monkeypatch.setattr(fa, "is_configured", lambda: True)

        with TestClient(app) as client:
            body = client.get("/api/auth/providers").json()

        assert body["firebase"] is True
        assert body["password"] is True
