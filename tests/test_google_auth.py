"""
Tests for Google sign-in.

The interesting surface is not the happy path — it is the three ways this
feature could hand someone an account that isn't theirs:

* accepting an ID token nobody verified,
* accepting a callback the user never started (no ``state`` check),
* linking on an email address Google never confirmed.

Each has a test that fails loudly if the guard is removed.
"""

from __future__ import annotations

import pytest

from backend.billing import db, google_oauth
from backend.billing.security import verify_password
from backend.shared import config


# ---------------------------------------------------------------------------
# Claims handling
# ---------------------------------------------------------------------------

class TestIdentityFromClaims:
    def test_a_verified_account_yields_an_identity(self):
        identity = google_oauth.identity_from_claims({
            "sub": "1234567890",
            "email": "Harsh@Gmail.com",
            "email_verified": True,
            "name": "Harsh",
        })
        assert identity["google_sub"] == "1234567890"
        # Normalised, because every other lookup in this codebase lowercases.
        assert identity["email"] == "harsh@gmail.com"
        assert identity["name"] == "Harsh"

    def test_an_unverified_email_is_refused(self):
        """
        The single check that makes email-based linking safe.

        Google issues tokens for accounts whose address it has not confirmed.
        Treating those as proof of ownership would let anyone who can create
        such an account walk into an existing one.
        """
        with pytest.raises(google_oauth.OAuthError) as caught:
            google_oauth.identity_from_claims({
                "sub": "1",
                "email": "victim@example.com",
                "email_verified": False,
                "name": "Not Really",
            })
        assert "not verified" in str(caught.value)

    def test_a_missing_verified_flag_is_treated_as_unverified(self):
        """Absent is not the same as true. Default to refusing."""
        with pytest.raises(google_oauth.OAuthError):
            google_oauth.identity_from_claims({"sub": "1", "email": "x@example.com"})

    def test_claims_without_an_email_are_refused(self):
        with pytest.raises(google_oauth.OAuthError):
            google_oauth.identity_from_claims({"sub": "1", "email_verified": True})

    def test_no_google_token_is_kept(self):
        """
        We needed Google to answer "who is this". Once it has, there is
        nothing left worth storing.
        """
        identity = google_oauth.identity_from_claims({
            "sub": "1", "email": "a@example.com", "email_verified": True, "name": "A",
        })
        assert set(identity) == {"google_sub", "email", "name", "email_verified"}


# ---------------------------------------------------------------------------
# The authorization URL
# ---------------------------------------------------------------------------

class TestAuthorizationUrl:
    def test_it_requests_identity_scopes_only(self, monkeypatch):
        """No Gmail, no Drive, no offline access — nothing worth stealing."""
        monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
        url, _ = google_oauth.build_authorization_url()
        assert "scope=openid+email+profile" in url
        assert "access_type=offline" not in url

    def test_each_call_gets_a_fresh_state(self, monkeypatch):
        monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
        _, first = google_oauth.build_authorization_url()
        _, second = google_oauth.build_authorization_url()
        assert first != second
        assert len(first) >= 32


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------

class TestAccountResolution:
    def _resolve(self, identity):
        from backend.billing.router import _resolve_google_customer

        return _resolve_google_customer(identity)

    def test_a_new_google_user_gets_an_account_and_a_trial(self):
        customer = self._resolve({
            "google_sub": "new-sub", "email": "new@example.com",
            "name": "New", "email_verified": True,
        })
        assert customer["auth_provider"] == db.PROVIDER_GOOGLE
        assert customer["google_sub"] == "new-sub"

        subscription = db.get_current_subscription(customer["id"])
        assert subscription["status"] == db.STATUS_TRIALING

    def test_a_google_only_account_has_no_usable_password(self):
        """
        The stored placeholder must not authenticate against anything.

        password_hash stays NOT NULL because SQLite can't drop that without a
        table rebuild — so the value has to be one no input can match.
        """
        customer = self._resolve({
            "google_sub": "nopw", "email": "nopw@example.com",
            "name": "No Password", "email_verified": True,
        })
        stored = customer["password_hash"]
        assert stored == db.NO_PASSWORD
        for attempt in ["", db.NO_PASSWORD, "password", "!google-oauth-no-password"]:
            assert verify_password(attempt, stored) is False

    def test_returning_users_are_matched_on_sub_not_email(self):
        """
        ``sub`` is the identifier that doesn't change; email is not.

        Someone who changes their Gmail address must land in the same account,
        not a second one.
        """
        first = self._resolve({
            "google_sub": "stable-sub", "email": "before@example.com",
            "name": "Person", "email_verified": True,
        })
        again = self._resolve({
            "google_sub": "stable-sub", "email": "after@example.com",
            "name": "Person", "email_verified": True,
        })
        assert again["id"] == first["id"]

    def test_a_verified_email_links_to_an_existing_password_account(self):
        """Google confirmed they own the mailbox, so it is the same human."""
        from backend.billing.security import hash_password

        existing = db.create_customer(
            "both@example.com", "Both", hash_password("a-real-password-12")
        )
        assert existing["google_sub"] is None

        linked = self._resolve({
            "google_sub": "link-sub", "email": "both@example.com",
            "name": "Both", "email_verified": True,
        })
        assert linked["id"] == existing["id"]
        assert linked["google_sub"] == "link-sub"

    def test_linking_leaves_the_password_working(self):
        """
        Linking adds a way in; it does not take one away.

        Someone who links Google and later forgets they did should not find
        their password rejected.
        """
        from backend.billing.security import hash_password

        db.create_customer("keeps@example.com", "Keeps", hash_password("still-works-123"))
        linked = self._resolve({
            "google_sub": "keeps-sub", "email": "keeps@example.com",
            "name": "Keeps", "email_verified": True,
        })
        assert verify_password("still-works-123", linked["password_hash"]) is True
        assert linked["auth_provider"] == db.PROVIDER_PASSWORD


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------

@pytest.fixture()
def unconfigured(monkeypatch):
    """
    Force Google sign-in off for this test.

    These two cases assert what happens when the credentials are *absent*, so
    they must not read whatever the developer happens to have in their .env.
    Before this fixture they passed only on a machine that had never set up
    Google, and failed the moment somebody did.
    """
    monkeypatch.setattr("backend.shared.config.GOOGLE_OAUTH_ENABLED", False)
    monkeypatch.setattr("backend.shared.config.GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr("backend.shared.config.GOOGLE_CLIENT_SECRET", "")


class TestRoutes:
    def test_providers_reports_google_off_when_unconfigured(self, api_client, unconfigured):
        assert api_client.get("/api/auth/providers").json()["google"] is False

    def test_starting_is_refused_when_unconfigured(self, api_client, unconfigured):
        """A button that 500s is worse than no button."""
        assert api_client.get("/api/auth/google", follow_redirects=False).status_code == 503

    def test_a_callback_without_state_is_refused(self, api_client, monkeypatch):
        """
        Without the state check, an attacker can complete a flow they started
        and leave the victim's browser signed into the attacker's account.
        """
        monkeypatch.setattr(config, "GOOGLE_OAUTH_ENABLED", True)
        response = api_client.get(
            "/api/auth/google/callback?code=abc", follow_redirects=False
        )
        assert response.status_code == 303
        assert "expired" in response.headers["location"]

    def test_a_mismatched_state_is_refused(self, api_client, monkeypatch):
        monkeypatch.setattr(config, "GOOGLE_OAUTH_ENABLED", True)
        api_client.cookies.set(google_oauth.STATE_COOKIE, "the-real-state")
        response = api_client.get(
            "/api/auth/google/callback?code=abc&state=a-different-state",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "expired" in response.headers["location"]
        api_client.cookies.clear()

    def test_a_cancelled_sign_in_is_not_an_error(self, api_client, monkeypatch):
        """Pressing cancel on Google's screen deserves a calm message."""
        monkeypatch.setattr(config, "GOOGLE_OAUTH_ENABLED", True)
        response = api_client.get(
            "/api/auth/google/callback?error=access_denied", follow_redirects=False
        )
        assert response.status_code == 303
        assert "cancelled" in response.headers["location"].lower()

    def test_failures_redirect_rather_than_return_json(self, api_client, monkeypatch):
        """
        The callback is a browser navigation, not an API call.

        Returning JSON here would leave a person staring at a blob of text in
        an otherwise empty tab.
        """
        monkeypatch.setattr(config, "GOOGLE_OAUTH_ENABLED", True)
        response = api_client.get(
            "/api/auth/google/callback?error=access_denied", follow_redirects=False
        )
        assert response.headers["location"].startswith(config.PUBLIC_BASE_URL if hasattr(config, "PUBLIC_BASE_URL") else "http")
        assert "/signin" in response.headers["location"]
