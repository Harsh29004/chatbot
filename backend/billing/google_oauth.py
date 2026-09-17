"""
Google sign-in — the server-side authorization-code flow.

Why this flow and not the browser one
-------------------------------------
Google's JavaScript library hands the page an ID token and lets the frontend
decide what to do with it. That would mean a second way to be authenticated,
running alongside the session cookie, with the trust decision made somewhere a
user can tamper with it.

Here the browser never holds a Google credential. It is redirected to Google,
Google redirects back with a one-time code, and the server exchanges that code
for tokens over its own connection. What the browser ends up with is exactly
the httpOnly session cookie a password login produces — one session mechanism,
one place that decides who someone is.

The three things that make it safe
----------------------------------
1. **The ID token's signature is verified**, against Google's published keys,
   along with issuer, audience and expiry. A decoded-but-unverified JWT is
   just a string the sender chose; ``google.oauth2.id_token`` does the real
   check and caches the key set.

2. **``state`` is checked** against a short-lived httpOnly cookie. Without it,
   an attacker can complete a login flow *they* started in a victim's browser
   and leave the victim signed into the attacker's account.

3. **Linking requires ``email_verified``.** This is the one that actually
   matters. Google will issue tokens for accounts whose address it has not
   confirmed; treating those as proof of ownership would let anyone who can
   create such an account walk into an existing one. Verified means Google
   confirmed the person controls that mailbox, and only then is it the same
   human.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from backend.shared import config

logger = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Identity only. No Gmail, no Drive, no offline access — this asks for the
# least that answers "who is this", and nothing that would need a refresh
# token worth storing.
SCOPES = ["openid", "email", "profile"]

STATE_COOKIE = "nx_oauth_state"


class OAuthError(Exception):
    """Sign-in could not be completed. The message is safe to show a user."""


def is_configured() -> bool:
    return config.GOOGLE_OAUTH_ENABLED


def build_authorization_url() -> tuple[str, str]:
    """
    Return ``(url, state)`` for the redirect to Google's consent screen.

    The caller stores *state* in a short-lived httpOnly cookie and compares it
    on the way back.
    """
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        # "select_account" alone isn't enough: with one Google account in the
        # browser and consent already given, Google skips the chooser and signs
        # straight in, so the person never sees which account was used. Adding
        # "consent" makes Google show its screens every time. GOOGLE_PROMPT can
        # relax this (e.g. to "select_account").
        "prompt": config.GOOGLE_PROMPT,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}", state


def exchange_code(code: str) -> dict[str, Any]:
    """
    Trade the one-time code for tokens, and verify the ID token.

    Returns the verified claims. Raises ``OAuthError`` with a message fit to
    show a user for anything that goes wrong.
    """
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "redirect_uri": config.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001 - network failures are expected here
        logger.warning("Google token exchange failed to connect: %s", exc)
        raise OAuthError("Could not reach Google. Please try again.") from exc

    if response.status_code != 200:
        # Google's body names the real cause (bad redirect_uri, reused code)
        # and belongs in our logs, not on a user's screen.
        logger.warning(
            "Google token exchange returned %s: %s", response.status_code, response.text[:400]
        )
        raise OAuthError("Google could not complete the sign-in. Please try again.")

    raw_id_token = response.json().get("id_token")
    if not raw_id_token:
        raise OAuthError("Google's response did not include an identity token.")

    try:
        # This is the real check: signature against Google's current keys,
        # plus issuer, audience and expiry. Decoding without it would accept
        # anything shaped like a JWT.
        claims = google_id_token.verify_oauth2_token(
            raw_id_token,
            google_requests.Request(),
            config.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=config.GOOGLE_CLOCK_SKEW_SECONDS,
        )
    except ValueError as exc:
        logger.warning("Google ID token failed verification: %s", exc)
        raise OAuthError("That sign-in could not be verified. Please try again.") from exc

    return claims


def identity_from_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """
    Pull out what we store, and refuse anything unusable.

    Only three fields are kept: the subject id, the email, and the display
    name. No Google token is stored — we needed Google to answer "who is
    this", and once it has, there is nothing left to keep.
    """
    subject = claims.get("sub")
    email = (claims.get("email") or "").lower().strip()

    if not subject or not email:
        raise OAuthError("Google did not return an email address for that account.")

    if not claims.get("email_verified", False):
        # The whole security of matching on email rests on this flag.
        raise OAuthError(
            "That Google account's email address is not verified, so we can't "
            "use it to sign in. Verify it with Google first, or sign up with a "
            "password."
        )

    return {
        "google_sub": str(subject),
        "email": email,
        "name": (claims.get("name") or "").strip()[:120],
        "email_verified": True,
    }
