"""
Firebase sign-in — verifying the ID token the browser brings back.

Where this sits
---------------
Firebase Authentication runs the sign-in itself: the Google account chooser,
the email+password form, the password reset mail, the rate limiting on all of
it. What it hands the page at the end is an ID token — a short-lived JWT
signed by Google saying "this is who just signed in".

That token is *not* a session here. The browser posts it once to
``/api/auth/firebase``, this module verifies it, and the endpoint issues the
same httpOnly ``nexora_session`` cookie a password login has always produced.
Everything downstream — billing, credits, referrals, the widget, the admin
panel — keeps resolving identity exactly one way, through that cookie.

Why not just trust the token on every request
---------------------------------------------
Because then there would be two ways to be authenticated, and one of them
lives in JavaScript memory where an injected script can read it. The cookie is
httpOnly and revocable server-side (``db.revoke_session``); a Firebase ID
token is neither. The exchange happens once, at the door.

What makes the verification real
--------------------------------
``firebase_admin.auth.verify_id_token`` checks the signature against Google's
published keys, plus issuer, audience and expiry, against *this* project.
Decoding the JWT without that check would accept any string shaped like one —
and anyone can mint one of those. The project the token must belong to comes
from the service account, so a token from some other Firebase project fails
even though it is genuinely signed by Google.

The email_verified rule
-----------------------
Matching an incoming identity to an existing account by email address is only
sound when somebody has proved they control that mailbox. Google-provider
sign-ins carry that proof. A fresh email+password sign-up does not, until the
user clicks the verification link — so those are refused here until they do,
which is what ``FIREBASE_REQUIRE_VERIFIED_EMAIL`` controls. Without it, anyone
who can type ``someone@example.com`` into the sign-up form walks into the
account that already owns that address.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

from backend.shared import config

logger = logging.getLogger(__name__)

# Firebase sign-in methods, as they appear in the token's provider data.
PROVIDER_GOOGLE = "google.com"
PROVIDER_PASSWORD = "password"

_init_lock = threading.Lock()
_app: Any = None
_init_failed = False


class FirebaseAuthError(Exception):
    """Sign-in could not be completed. The message is safe to show a user."""


class EmailNotVerifiedError(FirebaseAuthError):
    """
    The identity is genuine but the mailbox is unconfirmed.

    Separate from the base error so the endpoint can answer with a specific
    status and the sign-in page can offer "resend verification email" rather
    than a dead end.
    """


def is_configured() -> bool:
    """True when this server holds credentials able to verify a token."""
    return config.FIREBASE_AUTH_ENABLED and not _init_failed


def _credentials() -> Any:
    """
    Build the admin credential from whichever env shape is present.

    Ordered most-explicit first so that setting the discrete fields overrides
    an inherited ``GOOGLE_APPLICATION_CREDENTIALS`` on a Google-hosted box,
    rather than the platform silently winning.
    """
    from firebase_admin import credentials

    if config.FIREBASE_SERVICE_ACCOUNT_JSON:
        try:
            payload = json.loads(config.FIREBASE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError as exc:
            raise FirebaseAuthError(
                "FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON."
            ) from exc
        # A key pasted through a .env file usually arrives with its newlines
        # escaped; leaving them that way makes the PEM unparseable.
        if isinstance(payload.get("private_key"), str):
            payload["private_key"] = payload["private_key"].replace("\\n", "\n")
        return credentials.Certificate(payload)

    if config.FIREBASE_PROJECT_ID and config.FIREBASE_CLIENT_EMAIL and config.FIREBASE_PRIVATE_KEY:
        return credentials.Certificate({
            "type": "service_account",
            "project_id": config.FIREBASE_PROJECT_ID,
            "client_email": config.FIREBASE_CLIENT_EMAIL,
            "private_key": config.FIREBASE_PRIVATE_KEY,
            "token_uri": "https://oauth2.googleapis.com/token",
        })

    if config.FIREBASE_CREDENTIALS_FILE:
        return credentials.Certificate(config.FIREBASE_CREDENTIALS_FILE)

    raise FirebaseAuthError("Firebase sign-in is not configured on this server.")


def _safe_reason(exc: Exception, raw_token: str) -> str:
    """
    An exception message with the submitted credential scrubbed out.

    Google's token errors embed the input: a token that is not three
    segments produces ``Wrong number of segments in token: b'<the token>'``.
    Normally that is harmless, because a malformed token is not a usable
    credential — but this endpoint receives whatever the client put in the
    field, and a client bug that posts a session cookie or an API key there
    writes a live secret into the log, where it outlives the request and
    reaches anyone who can read log files.

    So the token never goes to the log. What does is the exception type, the
    scrubbed message, and a short hash, which is enough to correlate repeated
    failures from one caller without being replayable.
    """
    message = str(exc)
    token = (raw_token or "").strip()
    if token and len(token) > 6:
        message = message.replace(token, "<redacted>")
        # Google wraps the token in a bytes repr, which the plain replace
        # above misses: b'<token>' is a different string to <token>.
        message = message.replace(repr(token.encode()), "<redacted>")
    digest = hashlib.sha256(token.encode()).hexdigest()[:8] if token else "empty"
    return f"{type(exc).__name__}: {message} [token {digest}]"


def _get_app() -> Any:
    """
    Initialise the admin SDK once, lazily.

    Lazily because a deployment that does not use Firebase should not pay for
    the import or fail to boot over a missing key; once because
    ``initialize_app`` raises on a second call with the same name, and under
    uvicorn's reload this module can be imported more than once.
    """
    global _app, _init_failed

    if _app is not None:
        return _app

    with _init_lock:
        if _app is not None:
            return _app
        if _init_failed:
            raise FirebaseAuthError("Firebase sign-in is not available.")

        try:
            import firebase_admin
        except ImportError as exc:  # pragma: no cover - dependency is declared
            _init_failed = True
            raise FirebaseAuthError(
                "The firebase-admin package is not installed on this server."
            ) from exc

        try:
            # Another import of this module, or the host platform, may have
            # already created the default app. Reuse it rather than racing it.
            _app = firebase_admin.get_app()
        except ValueError:
            try:
                _app = firebase_admin.initialize_app(_credentials())
            except FirebaseAuthError:
                _init_failed = True
                raise
            except Exception as exc:  # noqa: BLE001 - a bad key surfaces here
                _init_failed = True
                logger.error("Firebase admin SDK failed to initialise: %s", exc)
                raise FirebaseAuthError(
                    "Firebase sign-in is misconfigured on this server."
                ) from exc

        logger.info("Firebase admin SDK ready (project %s)", _project_id())
        return _app


def _project_id() -> str:
    if config.FIREBASE_PROJECT_ID:
        return config.FIREBASE_PROJECT_ID
    try:
        return json.loads(config.FIREBASE_SERVICE_ACCOUNT_JSON or "{}").get("project_id", "?")
    except json.JSONDecodeError:
        return "?"


def verify_id_token(raw_token: str, *, check_revoked: bool = True) -> dict[str, Any]:
    """
    Verify a Firebase ID token and return its claims.

    ``check_revoked`` costs one call to Firebase but means a user disabled or
    signed-out-everywhere in the Firebase console cannot spend the remaining
    minutes of an already-issued token to open a fresh session here. That is
    worth a round trip on a once-per-sign-in path.

    Raises :class:`FirebaseAuthError` with a message fit to show a user.
    """
    if not raw_token or not raw_token.strip():
        raise FirebaseAuthError("No sign-in token was provided.")

    from firebase_admin import auth as fb_auth

    app = _get_app()

    try:
        return fb_auth.verify_id_token(
            raw_token.strip(),
            app=app,
            check_revoked=check_revoked,
            clock_skew_seconds=config.FIREBASE_CLOCK_SKEW_SECONDS,
        )
    except fb_auth.ExpiredIdTokenError as exc:
        # Firebase ID tokens last an hour. A page left open overnight hits
        # this, and the fix is to get a fresh one, not to sign in again.
        raise FirebaseAuthError("That sign-in has expired. Please try again.") from exc
    except fb_auth.RevokedIdTokenError as exc:
        raise FirebaseAuthError("That session was signed out. Please sign in again.") from exc
    except fb_auth.UserDisabledError as exc:
        raise FirebaseAuthError("That account has been disabled.") from exc
    except fb_auth.InvalidIdTokenError as exc:
        # Wrong project, tampered payload, or not a JWT at all. The detail is
        # useful to us and tells an attacker which guess was closer.
        logger.warning("Firebase ID token rejected: %s", _safe_reason(exc, raw_token))
        raise FirebaseAuthError("That sign-in could not be verified. Please try again.") from exc
    except ValueError as exc:
        logger.warning("Malformed Firebase ID token: %s", _safe_reason(exc, raw_token))
        raise FirebaseAuthError("That sign-in could not be verified. Please try again.") from exc
    except Exception as exc:  # noqa: BLE001 - network failure reaching Google
        logger.warning("Firebase token verification failed: %s", _safe_reason(exc, raw_token))
        raise FirebaseAuthError("Could not reach Firebase. Please try again.") from exc


def _sign_in_provider(claims: dict[str, Any]) -> str:
    """Which method was used for *this* sign-in, per the token's own record."""
    firebase_claims = claims.get("firebase") or {}
    return str(firebase_claims.get("sign_in_provider") or "")


def identity_from_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """
    Pull out what we store, and refuse anything unusable.

    Four fields are kept: the Firebase uid, the email, a display name, and
    which provider signed them in. No Firebase token is stored — Firebase
    answered "who is this", and once it has, there is nothing left to keep.
    """
    uid = claims.get("uid") or claims.get("sub")
    email = (claims.get("email") or "").lower().strip()
    provider = _sign_in_provider(claims)

    if not uid:
        raise FirebaseAuthError("That sign-in did not identify an account.")

    if not email:
        # Anonymous and phone sign-ins land here. This platform bills per
        # account and mails invoices, so an account without an address is not
        # something it can carry.
        raise FirebaseAuthError(
            "That sign-in method does not provide an email address, which this "
            "account needs. Please use Google or email and password."
        )

    verified = bool(claims.get("email_verified", False))
    if config.FIREBASE_REQUIRE_VERIFIED_EMAIL and not verified:
        raise EmailNotVerifiedError(
            "Please confirm your email address first — we've sent you a "
            "verification link. Check your inbox, then sign in again."
        )

    # Firebase gives no name for a password sign-up until one is set, so fall
    # back to the local part rather than storing an empty string that then
    # shows up as a blank greeting on the dashboard.
    name = (claims.get("name") or "").strip()[:120] or email.split("@", 1)[0][:120]

    return {
        "firebase_uid": str(uid),
        "email": email,
        "name": name,
        "email_verified": verified,
        "sign_in_provider": provider,
        # Kept so an account created through the older server-side OAuth flow
        # still matches when the same human now arrives via Firebase Google.
        "google_sub": _google_sub(claims),
    }


def _google_sub(claims: dict[str, Any]) -> str | None:
    """
    The Google subject id behind a Firebase Google sign-in, when there is one.

    This is the bridge to the retired server-side OAuth flow. Accounts it
    created are keyed on Google's ``sub``; the same person arriving through
    Firebase has a *different* identifier (the Firebase uid), and without
    this they would look like a stranger and be refused as a duplicate
    mailbox. Firebase carries the original provider id in ``identities``.

    Nothing writes ``google_sub`` any more, but it must keep being read for
    as long as those accounts exist.
    """
    if _sign_in_provider(claims) != PROVIDER_GOOGLE:
        return None
    identities = ((claims.get("firebase") or {}).get("identities") or {})
    subs = identities.get(PROVIDER_GOOGLE) or []
    if isinstance(subs, list) and subs:
        return str(subs[0])
    return None


def revoke_refresh_tokens(uid: str) -> None:
    """
    Sign a user out of Firebase everywhere.

    Called on logout so the browser cannot mint a fresh ID token from a
    refresh token it still holds and silently re-open a session the user
    believes they closed. Best effort: failing to reach Firebase must not
    stop us clearing our own cookie, which is the part that matters.
    """
    if not is_configured():
        return
    try:
        from firebase_admin import auth as fb_auth

        fb_auth.revoke_refresh_tokens(uid, app=_get_app())
    except Exception as exc:  # noqa: BLE001 - logout must not fail on this
        logger.warning("Could not revoke Firebase refresh tokens for %s: %s", uid, exc)
