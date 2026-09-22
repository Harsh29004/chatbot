"use client";

/**
 * The browser half of Firebase sign-in.
 *
 * Every function here ends the same way: with a Firebase ID token, which the
 * caller hands to `api.firebaseExchange()` for the session cookie that is the
 * app's real credential. Firebase runs the sign-in; it does not run the
 * session. See `backend/billing/firebase_auth.py` for why.
 *
 * Firebase's error codes are the reason this module exists rather than the
 * three SDK calls being inlined. `auth/invalid-credential` in front of a user
 * is not an error message, it is a support ticket — so every code that a real
 * person can reach is mapped to a sentence that tells them what to do next.
 */

import {
  GoogleAuthProvider,
  createUserWithEmailAndPassword,
  getRedirectResult,
  sendEmailVerification,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signInWithRedirect,
  signOut as firebaseSignOut,
  updateProfile,
  type User,
  type UserCredential,
} from "firebase/auth";

import { firebaseAuth, isFirebaseConfigured } from "./firebase";

export { isFirebaseConfigured };

/** A failure worth showing a user, with the raw code kept for telemetry. */
export class FirebaseSignInError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "FirebaseSignInError";
    this.code = code;
  }
}

/**
 * Firebase error codes, translated.
 *
 * `invalid-credential` is the interesting one: modern Firebase projects have
 * email enumeration protection on, which collapses "no such user" and "wrong
 * password" into this single code deliberately — so an attacker cannot use
 * the sign-in form to discover which addresses are registered. The message
 * below has to stay equally vague to preserve that, however much less helpful
 * it is.
 */
const MESSAGES: Record<string, string> = {
  "auth/invalid-credential": "Incorrect email or password.",
  "auth/invalid-email": "That doesn't look like an email address.",
  "auth/user-disabled": "That account has been disabled.",
  "auth/user-not-found": "Incorrect email or password.",
  "auth/wrong-password": "Incorrect email or password.",
  "auth/email-already-in-use":
    "An account with that email already exists. Try signing in instead.",
  "auth/weak-password": "That password is too short — use at least 8 characters.",
  "auth/too-many-requests":
    "Too many attempts. Wait a few minutes and try again, or reset your password.",
  "auth/network-request-failed": "Can't reach Firebase. Check your connection and try again.",
  "auth/popup-closed-by-user": "Sign-in was cancelled.",
  "auth/cancelled-popup-request": "Sign-in was cancelled.",
  "auth/popup-blocked": "Your browser blocked the sign-in window.",
  "auth/account-exists-with-different-credential":
    "That email is already registered with a different sign-in method. " +
    "Sign in the way you did the first time, then link Google from settings.",
  "auth/operation-not-allowed":
    "That sign-in method is not enabled for this project. Enable it in the " +
    "Firebase console under Authentication → Sign-in method.",
  "auth/unauthorized-domain":
    "This domain is not authorised for sign-in. Add it in the Firebase console " +
    "under Authentication → Settings → Authorized domains.",
  "auth/requires-recent-login": "Please sign in again to continue.",
};

function toSignInError(err: unknown): FirebaseSignInError {
  const code =
    typeof err === "object" && err !== null && "code" in err
      ? String((err as { code: unknown }).code)
      : "auth/unknown";

  return new FirebaseSignInError(
    code,
    MESSAGES[code] ?? "Sign-in failed. Please try again.",
  );
}

function requireAuth() {
  const auth = firebaseAuth();
  if (!auth) {
    throw new FirebaseSignInError(
      "auth/not-configured",
      "Sign-in is not configured on this site yet.",
    );
  }
  return auth;
}

function googleProvider(): GoogleAuthProvider {
  const provider = new GoogleAuthProvider();
  // Always show the account chooser. Without it, a browser with one Google
  // account signs straight in and the person never sees which account was
  // used — which is confusing at best and wrong at worst on a shared machine.
  provider.setCustomParameters({ prompt: "select_account" });
  return provider;
}

/**
 * Google sign-in, by popup, falling back to a full-page redirect.
 *
 * The popup is much the better experience — the app keeps its state and the
 * user comes straight back. But popups are blocked outright in some
 * in-app browsers (Instagram, LinkedIn, several webviews), and there the only
 * thing that works is a redirect. `signInWithRedirect` never returns: it
 * navigates away, and the result is collected by `consumeRedirectResult()`
 * on the next page load.
 */
export async function signInWithGoogle(): Promise<string> {
  const auth = requireAuth();

  let credential: UserCredential;
  try {
    credential = await signInWithPopup(auth, googleProvider());
  } catch (err) {
    const error = toSignInError(err);

    if (
      error.code === "auth/popup-blocked" ||
      error.code === "auth/operation-not-supported-in-this-environment"
    ) {
      await signInWithRedirect(auth, googleProvider());
      // Unreachable in practice; the line above navigates away.
      return new Promise<string>(() => {});
    }
    throw error;
  }

  return credential.user.getIdToken();
}

/**
 * Collect the result of a redirect sign-in, if this load is one.
 *
 * Returns the ID token when the page was reached by coming back from Google,
 * and null on an ordinary load. Called once on boot.
 */
export async function consumeRedirectResult(): Promise<string | null> {
  const auth = firebaseAuth();
  if (!auth) return null;

  try {
    const result = await getRedirectResult(auth);
    if (!result) return null;
    return await result.user.getIdToken();
  } catch (err) {
    throw toSignInError(err);
  }
}

/** Email and password sign-in. Returns an ID token. */
export async function signInWithPassword(
  email: string,
  password: string,
): Promise<string> {
  const auth = requireAuth();
  try {
    const credential = await signInWithEmailAndPassword(auth, email, password);
    return await credential.user.getIdToken();
  } catch (err) {
    throw toSignInError(err);
  }
}

export interface SignUpResult {
  idToken: string;
  emailVerified: boolean;
}

/**
 * Create an account with email and password.
 *
 * Sends the verification email immediately, because the backend refuses an
 * unverified identity (`FIREBASE_REQUIRE_VERIFIED_EMAIL`) and a sign-up that
 * silently dead-ends at "please verify" with no email sent is the worst
 * version of this flow.
 *
 * `emailVerified` comes back false here essentially always — Firebase has
 * just created the user and nobody has clicked anything yet. The caller uses
 * it to show "check your inbox" rather than attempting an exchange that is
 * going to be refused.
 */
export async function signUpWithPassword(
  email: string,
  password: string,
  displayName: string,
): Promise<SignUpResult> {
  const auth = requireAuth();
  try {
    const credential = await createUserWithEmailAndPassword(auth, email, password);

    if (displayName.trim()) {
      // Best effort: the account exists either way, and a missing display
      // name is not worth failing a sign-up over.
      await updateProfile(credential.user, {
        displayName: displayName.trim().slice(0, 120),
      }).catch(() => {});
    }

    await sendEmailVerification(credential.user).catch(() => {});

    return {
      idToken: await credential.user.getIdToken(true),
      emailVerified: credential.user.emailVerified,
    };
  } catch (err) {
    throw toSignInError(err);
  }
}

/** Re-send the verification email to whoever is currently signed in. */
export async function resendVerificationEmail(): Promise<void> {
  const auth = requireAuth();
  const user = auth.currentUser;
  if (!user) {
    throw new FirebaseSignInError(
      "auth/no-current-user",
      "Sign in first, then we can re-send the verification email.",
    );
  }
  try {
    await sendEmailVerification(user);
  } catch (err) {
    throw toSignInError(err);
  }
}

/**
 * Send a password reset email.
 *
 * Resolves even when no account exists for the address. That is intentional
 * and matches Firebase's own enumeration protection: telling the caller
 * "no such user" turns this form into a way to test which addresses are
 * registered.
 */
export async function sendPasswordReset(email: string): Promise<void> {
  const auth = requireAuth();
  try {
    await sendPasswordResetEmail(auth, email);
  } catch (err) {
    const error = toSignInError(err);
    if (error.code === "auth/user-not-found") return;
    throw error;
  }
}

/**
 * A fresh ID token for the current Firebase user, if there is one.
 *
 * Used to re-establish our session cookie when it has expired but Firebase
 * still considers the user signed in — the common case for someone returning
 * the next morning. `forceRefresh` because a cached token may be minutes from
 * expiry, and the exchange would then fail for no good reason.
 */
export async function currentIdToken(forceRefresh = true): Promise<string | null> {
  const auth = firebaseAuth();
  if (!auth?.currentUser) return null;
  try {
    return await auth.currentUser.getIdToken(forceRefresh);
  } catch {
    return null;
  }
}

/** Whoever Firebase currently considers signed in, or null. */
export function currentFirebaseUser(): User | null {
  return firebaseAuth()?.currentUser ?? null;
}

/**
 * Sign out of Firebase.
 *
 * Only half of signing out — the server session is revoked separately by
 * `api.logout()`. Both are needed: clearing one and not the other leaves the
 * app able to silently re-authenticate the user it just signed out.
 */
export async function signOutFirebase(): Promise<void> {
  const auth = firebaseAuth();
  if (!auth) return;
  try {
    await firebaseSignOut(auth);
  } catch {
    // The local credential is cleared regardless; nothing useful to do here.
  }
}
