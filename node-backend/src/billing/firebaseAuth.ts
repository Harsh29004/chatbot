/**
 * Firebase sign-in — verifying the ID token the browser brings back.
 *
 * Port of `backend/billing/firebase_auth.py`. The Node `firebase-admin` SDK is
 * the same product as the Python one, so the verification this performs is
 * identical: signature against Google's published keys, plus issuer, audience
 * and expiry, against *this* project.
 *
 * Where this sits
 * ---------------
 * Firebase Authentication runs the sign-in itself: the Google account chooser,
 * the email+password form, the password reset mail, the rate limiting on all of
 * it. What it hands the page at the end is an ID token — a short-lived JWT
 * signed by Google saying "this is who just signed in".
 *
 * That token is *not* a session here. The browser posts it once to
 * `/api/auth/firebase`, this module verifies it, and the endpoint issues the
 * same httpOnly `nexora_session` cookie a password login has always produced.
 * Everything downstream — billing, credits, referrals, the widget, the admin
 * panel — keeps resolving identity exactly one way, through that cookie.
 *
 * Why not just trust the token on every request
 * ---------------------------------------------
 * Because then there would be two ways to be authenticated, and one of them
 * lives in JavaScript memory where an injected script can read it. The cookie
 * is httpOnly and revocable server-side (`db.revokeSession`); a Firebase ID
 * token is neither. The exchange happens once, at the door.
 *
 * The email_verified rule
 * -----------------------
 * Matching an incoming identity to an existing account by email address is only
 * sound when somebody has proved they control that mailbox. Google-provider
 * sign-ins carry that proof. A fresh email+password sign-up does not, until the
 * user clicks the verification link — so those are refused here until they do,
 * which is what `FIREBASE_REQUIRE_VERIFIED_EMAIL` controls. Without it, anyone
 * who can type `someone@example.com` into the sign-up form walks into the
 * account that already owns that address.
 */

import crypto from "node:crypto";
import type { App } from "firebase-admin/app";
import type { DecodedIdToken } from "firebase-admin/auth";

import * as config from "../config.js";
import { logger } from "../shared/logger.js";

// Firebase sign-in methods, as they appear in the token's provider data.
export const PROVIDER_GOOGLE = "google.com";
export const PROVIDER_PASSWORD = "password";

let app: App | null = null;
let initPromise: Promise<App> | null = null;
let initFailed = false;

/** Sign-in could not be completed. The message is safe to show a user. */
export class FirebaseAuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FirebaseAuthError";
  }
}

/**
 * The identity is genuine but the mailbox is unconfirmed.
 *
 * Separate from the base error so the endpoint can answer with a specific
 * status and the sign-in page can offer "resend verification email" rather than
 * a dead end.
 */
export class EmailNotVerifiedError extends FirebaseAuthError {
  constructor(message: string) {
    super(message);
    this.name = "EmailNotVerifiedError";
  }
}

/** True when this server holds credentials able to verify a token. */
export function isConfigured(): boolean {
  return config.FIREBASE_AUTH_ENABLED && !initFailed;
}

/**
 * Build the admin credential from whichever env shape is present.
 *
 * Ordered most-explicit first so that setting the discrete fields overrides an
 * inherited `GOOGLE_APPLICATION_CREDENTIALS` on a Google-hosted box, rather
 * than the platform silently winning.
 */
async function buildCredential() {
  const { cert } = await import("firebase-admin/app");

  if (config.FIREBASE_SERVICE_ACCOUNT_JSON) {
    let payload: Record<string, any>;
    try {
      payload = JSON.parse(config.FIREBASE_SERVICE_ACCOUNT_JSON);
    } catch {
      throw new FirebaseAuthError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON.");
    }
    // A key pasted through a .env file usually arrives with its newlines
    // escaped; leaving them that way makes the PEM unparseable.
    if (typeof payload.private_key === "string") {
      payload.private_key = payload.private_key.replace(/\\n/g, "\n");
    }
    return cert(payload);
  }

  if (
    config.FIREBASE_PROJECT_ID &&
    config.FIREBASE_CLIENT_EMAIL &&
    config.FIREBASE_PRIVATE_KEY
  ) {
    return cert({
      projectId: config.FIREBASE_PROJECT_ID,
      clientEmail: config.FIREBASE_CLIENT_EMAIL,
      privateKey: config.FIREBASE_PRIVATE_KEY,
    });
  }

  if (config.FIREBASE_CREDENTIALS_FILE) {
    return cert(config.FIREBASE_CREDENTIALS_FILE);
  }

  throw new FirebaseAuthError("Firebase sign-in is not configured on this server.");
}

function projectId(): string {
  if (config.FIREBASE_PROJECT_ID) return config.FIREBASE_PROJECT_ID;
  try {
    return JSON.parse(config.FIREBASE_SERVICE_ACCOUNT_JSON || "{}").project_id ?? "?";
  } catch {
    return "?";
  }
}

/**
 * An exception message with the submitted credential scrubbed out.
 *
 * Token errors embed the input. Normally that is harmless, because a malformed
 * token is not a usable credential — but this endpoint receives whatever the
 * client put in the field, and a client bug that posts a session cookie or an
 * API key there writes a live secret into the log, where it outlives the
 * request and reaches anyone who can read log files.
 *
 * So the token never goes to the log. What does is the error name, the scrubbed
 * message, and a short hash, which is enough to correlate repeated failures
 * from one caller without being replayable.
 */
function safeReason(error: unknown, rawToken: string): string {
  let message = error instanceof Error ? error.message : String(error);
  const name = error instanceof Error ? error.name : "Error";
  const token = (rawToken ?? "").trim();

  if (token && token.length > 6) {
    message = message.split(token).join("<redacted>");
  }
  const digest = token
    ? crypto.createHash("sha256").update(token).digest("hex").slice(0, 8)
    : "empty";
  return `${name}: ${message} [token ${digest}]`;
}

/**
 * Initialise the admin SDK once, lazily.
 *
 * Lazily because a deployment that does not use Firebase should not pay for the
 * import or fail to boot over a missing key; once because `initializeApp`
 * throws on a second call with the same name, and under a watch-mode reload
 * this module can be imported more than once.
 */
async function getApp(): Promise<App> {
  if (app !== null) return app;
  if (initFailed) throw new FirebaseAuthError("Firebase sign-in is not available.");
  if (initPromise !== null) return initPromise;

  initPromise = (async () => {
    const { getApps, initializeApp } = await import("firebase-admin/app");

    // Another import of this module, or the host platform, may have already
    // created the default app. Reuse it rather than racing it.
    const existing = getApps();
    if (existing.length > 0) {
      app = existing[0];
      return app;
    }

    try {
      app = initializeApp({ credential: await buildCredential() });
    } catch (error) {
      initFailed = true;
      initPromise = null;
      if (error instanceof FirebaseAuthError) throw error;
      logger.error(`Firebase admin SDK failed to initialise: ${String(error)}`);
      throw new FirebaseAuthError("Firebase sign-in is misconfigured on this server.");
    }

    logger.info(`Firebase admin SDK ready (project ${projectId()})`);
    return app;
  })();

  try {
    return await initPromise;
  } catch (error) {
    initPromise = null;
    throw error;
  }
}

/**
 * Verify a Firebase ID token and return its claims.
 *
 * `checkRevoked` costs one call to Firebase but means a user disabled or
 * signed-out-everywhere in the Firebase console cannot spend the remaining
 * minutes of an already-issued token to open a fresh session here. That is
 * worth a round trip on a once-per-sign-in path.
 *
 * Throws {@link FirebaseAuthError} with a message fit to show a user.
 */
export async function verifyIdToken(
  rawToken: string,
  options: { checkRevoked?: boolean } = {},
): Promise<DecodedIdToken> {
  const checkRevoked = options.checkRevoked ?? true;

  if (!rawToken || !rawToken.trim()) {
    throw new FirebaseAuthError("No sign-in token was provided.");
  }

  const { getAuth } = await import("firebase-admin/auth");
  const auth = getAuth(await getApp());

  try {
    return await auth.verifyIdToken(rawToken.trim(), checkRevoked);
  } catch (error) {
    // The Node SDK reports these through an error `code` rather than distinct
    // exception classes, which is the one shape difference from the Python
    // original. The mapping to user-facing messages is unchanged.
    const code = (error as { code?: string })?.code ?? "";

    if (code === "auth/id-token-expired") {
      // Firebase ID tokens last an hour. A page left open overnight hits this,
      // and the fix is to get a fresh one, not to sign in again.
      throw new FirebaseAuthError("That sign-in has expired. Please try again.");
    }
    if (code === "auth/id-token-revoked" || code === "auth/session-cookie-revoked") {
      throw new FirebaseAuthError("That session was signed out. Please sign in again.");
    }
    if (code === "auth/user-disabled") {
      throw new FirebaseAuthError("That account has been disabled.");
    }
    if (code === "auth/argument-error" || code === "auth/invalid-id-token") {
      // Wrong project, tampered payload, or not a JWT at all. The detail is
      // useful to us and tells an attacker which guess was closer.
      logger.warn(`Firebase ID token rejected: ${safeReason(error, rawToken)}`);
      throw new FirebaseAuthError("That sign-in could not be verified. Please try again.");
    }

    if (error instanceof FirebaseAuthError) throw error;

    logger.warn(`Firebase token verification failed: ${safeReason(error, rawToken)}`);
    throw new FirebaseAuthError("Could not reach Firebase. Please try again.");
  }
}

/** Which method was used for *this* sign-in, per the token's own record. */
function signInProvider(claims: DecodedIdToken): string {
  return String(claims.firebase?.sign_in_provider ?? "");
}

export interface FirebaseIdentity {
  firebase_uid: string;
  email: string;
  name: string;
  email_verified: boolean;
  sign_in_provider: string;
  google_sub: string | null;
}

/**
 * Pull out what we store, and refuse anything unusable.
 *
 * Four fields are kept: the Firebase uid, the email, a display name, and which
 * provider signed them in. No Firebase token is stored — Firebase answered "who
 * is this", and once it has, there is nothing left to keep.
 */
export function identityFromClaims(claims: DecodedIdToken): FirebaseIdentity {
  const uid = claims.uid ?? claims.sub;
  const email = (claims.email ?? "").toLowerCase().trim();
  const provider = signInProvider(claims);

  if (!uid) {
    throw new FirebaseAuthError("That sign-in did not identify an account.");
  }

  if (!email) {
    // Anonymous and phone sign-ins land here. This platform bills per account
    // and mails invoices, so an account without an address is not something it
    // can carry.
    throw new FirebaseAuthError(
      "That sign-in method does not provide an email address, which this " +
        "account needs. Please use Google or email and password.",
    );
  }

  const verified = Boolean(claims.email_verified);
  if (config.FIREBASE_REQUIRE_VERIFIED_EMAIL && !verified) {
    throw new EmailNotVerifiedError(
      "Please confirm your email address first — we've sent you a " +
        "verification link. Check your inbox, then sign in again.",
    );
  }

  // Firebase gives no name for a password sign-up until one is set, so fall
  // back to the local part rather than storing an empty string that then shows
  // up as a blank greeting on the dashboard.
  const name =
    (claims.name ?? "").trim().slice(0, 120) || email.split("@")[0].slice(0, 120);

  return {
    firebase_uid: String(uid),
    email,
    name,
    email_verified: verified,
    sign_in_provider: provider,
    // Kept so an account created through the older server-side OAuth flow still
    // matches when the same human now arrives via Firebase Google.
    google_sub: googleSub(claims),
  };
}

/**
 * The Google subject id behind a Firebase Google sign-in, when there is one.
 *
 * This is the bridge to the retired server-side OAuth flow. Accounts it created
 * are keyed on Google's `sub`; the same person arriving through Firebase has a
 * *different* identifier (the Firebase uid), and without this they would look
 * like a stranger and be refused as a duplicate mailbox. Firebase carries the
 * original provider id in `identities`.
 *
 * Nothing writes `google_sub` any more, but it must keep being read for as long
 * as those accounts exist.
 */
function googleSub(claims: DecodedIdToken): string | null {
  if (signInProvider(claims) !== PROVIDER_GOOGLE) return null;

  const identities = claims.firebase?.identities ?? {};
  const subs = identities[PROVIDER_GOOGLE];
  if (Array.isArray(subs) && subs.length > 0) return String(subs[0]);
  return null;
}

/**
 * Sign a user out of Firebase everywhere.
 *
 * Called on logout so the browser cannot mint a fresh ID token from a refresh
 * token it still holds and silently re-open a session the user believes they
 * closed. Best effort: failing to reach Firebase must not stop us clearing our
 * own cookie, which is the part that matters.
 */
export async function revokeRefreshTokens(uid: string): Promise<void> {
  if (!isConfigured()) return;
  try {
    const { getAuth } = await import("firebase-admin/auth");
    await getAuth(await getApp()).revokeRefreshTokens(uid);
  } catch (error) {
    // Logout must not fail on this.
    logger.warn(`Could not revoke Firebase refresh tokens for ${uid}: ${String(error)}`);
  }
}
