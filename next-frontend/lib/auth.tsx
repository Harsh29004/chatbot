"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";

import { AUTH_METHODS, EVENTS, identify, measure, track } from "./analytics";
import { api, ApiError } from "./api-client";
import type { Customer } from "./types";
import { reportError } from "./errorReporting";
import {
  consumeRedirectResult,
  currentIdToken,
  isFirebaseConfigured,
  signInWithGoogle,
  signInWithPassword,
  signOutFirebase,
  signUpWithPassword,
} from "./firebaseSignIn";

/**
 * Raised when a sign-up succeeded but the mailbox is not confirmed yet.
 *
 * Not an error in the usual sense — the account exists and everything worked.
 * It is a distinct type so the sign-in page can render "check your inbox"
 * with a re-send button, instead of showing it in the red box next to
 * "incorrect password".
 */
export class EmailVerificationPending extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EmailVerificationPending";
  }
}

interface AuthState {
  customer: Customer | null;
  loading: boolean;
  /** Whether the Firebase buttons should be offered at all. */
  firebaseReady: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string,
    password: string,
    name: string,
    referralCode?: string,
  ) => Promise<void>;
  signInWithGoogleAccount: (referralCode?: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

/**
 * @param initialCustomer Resolved on the server by the root layout, which has
 *   already read the session cookie. Passing it in is the whole point of
 *   server-rendering this app: the first paint knows who is signed in, so there
 *   is no signed-out flash and no "who am I" round trip on every page load.
 *   `undefined` means the server did not look (only the case in tests);
 *   `null` means it looked and nobody was signed in.
 */
export function AuthProvider({
  children,
  initialCustomer,
}: {
  children: ReactNode;
  initialCustomer?: Customer | null;
}) {
  const [customer, setCustomer] = useState<Customer | null>(initialCustomer ?? null);
  // Nothing to wait for when the server already answered. The only case that
  // still boots asynchronously is a visitor whose session cookie has expired
  // but whose Firebase credential has not — see the effect below.
  const [loading, setLoading] = useState(initialCustomer === undefined);

  // Used to re-run the server components after the cookie changes. Signing in
  // or out alters what every Server Component on the page would render, and
  // they do not re-run on their own just because React state moved.
  const router = useRouter();

  // Firebase needs both halves: config in this bundle, and a service account
  // on the server able to verify what it issues. Either one missing and the
  // buttons would fail on click, so they are not shown.
  const [serverHasFirebase, setServerHasFirebase] = useState(false);
  const firebaseReady = isFirebaseConfigured && serverHasFirebase;

  /**
   * Adopt a customer and tell Analytics who they are.
   *
   * The id, never the email — see `identify()` in analytics.ts for why that
   * distinction is not optional.
   */
  const adopt = useCallback((next: Customer | null) => {
    setCustomer(next);
    identify(next?.id ?? null, next ? { account_created: next.created_at } : {});
  }, []);

  useEffect(() => {
    api
      .authProviders()
      .then((providers) => setServerHasFirebase(Boolean(providers.firebase)))
      .catch(() => setServerHasFirebase(false));
  }, []);

  /**
   * Work out who is signed in, on boot.
   *
   * Server-rendering removed most of this. The layout has already resolved the
   * session cookie and handed the answer down as `initialCustomer`, so the
   * "do we have a cookie" round trip the Vite app made on every single load is
   * gone. Two cases still need the browser, because only the browser can see
   * them:
   *
   * 1. This load is the tail of a Google *redirect* sign-in. The token exists
   *    only on this one load, in this tab, and is gone after it — the server
   *    never sees it.
   * 2. The cookie is gone but Firebase still considers the user signed in,
   *    which is what a returning visitor looks like once the cookie's 30 days
   *    are up. Rather than making them sign in again, a fresh ID token is
   *    exchanged silently. This is the single biggest thing Firebase buys this
   *    app over the old flow.
   *
   * Case 2 is only worth attempting when the server said nobody is signed in.
   */
  useEffect(() => {
    let cancelled = false;

    const boot = async () => {
      try {
        const redirectToken = await consumeRedirectResult().catch(() => null);
        if (redirectToken) {
          const me = await api.firebaseExchange(redirectToken, storedReferral());
          if (!cancelled) {
            adopt(me);
            track(EVENTS.LOGIN, { method: AUTH_METHODS.GOOGLE_REDIRECT });
            // The server rendered this page as signed-out; now it isn't.
            router.refresh();
          }
          return;
        }

        // The server already answered, and it answered "yes".
        if (initialCustomer) return;

        const token = await currentIdToken();
        if (!token) return;

        const me = await api.firebaseExchange(token);
        if (!cancelled) {
          adopt(me);
          track(EVENTS.SESSION_EXPIRED, { recovered: true });
          // Re-render the server components now that the cookie is live again,
          // so the page fills in without a manual reload.
          router.refresh();
        }
      } catch {
        // A failed silent recovery is not an error the user needs to see; they
        // are simply signed out, which is what the server already said.
        if (!cancelled && !initialCustomer) adopt(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void boot();
    return () => {
      cancelled = true;
    };
  }, [adopt, initialCustomer, router]);

  /**
   * Email and password.
   *
   * Through Firebase when it is available, and through the original
   * `/auth/login` endpoint when it is not. The fallback is not dead code: it
   * keeps every account that predates Firebase — and every deployment that
   * has not configured it — able to sign in, and it is what runs if the
   * Firebase project is ever unreachable.
   */
  const signIn = useCallback(
    async (email: string, password: string) => {
      track(EVENTS.AUTH_STARTED, {
        method: AUTH_METHODS.PASSWORD,
        provider: firebaseReady ? "firebase" : "local",
      });

      try {
        const me = await measure("auth_password_sign_in", async () => {
          if (!firebaseReady) return api.login(email, password);
          const idToken = await signInWithPassword(email, password);
          return api.firebaseExchange(idToken);
        });

        adopt(me);
        track(EVENTS.LOGIN, { method: AUTH_METHODS.PASSWORD });
      } catch (err) {
        throw handleAuthFailure(err, AUTH_METHODS.PASSWORD);
      }
    },
    [adopt, firebaseReady],
  );

  /**
   * Create an account.
   *
   * With Firebase the flow gains a step that the password-only one never had:
   * the user exists but cannot be exchanged for a session until they click
   * the link in their email. That is a deliberate server-side rule
   * (`FIREBASE_REQUIRE_VERIFIED_EMAIL`), and the `EmailVerificationPending`
   * thrown here is how the page knows to say so rather than reporting a
   * failure.
   */
  const signUp = useCallback(
    async (email: string, password: string, name: string, referralCode = "") => {
      track(EVENTS.AUTH_STARTED, {
        method: AUTH_METHODS.PASSWORD,
        intent: "sign_up",
        has_referral: Boolean(referralCode),
      });

      try {
        if (!firebaseReady) {
          const me = await api.signup(email, password, name, referralCode);
          adopt(me);
          track(EVENTS.SIGN_UP, {
            method: AUTH_METHODS.PASSWORD,
            has_referral: Boolean(referralCode),
          });
          return;
        }

        const { idToken } = await signUpWithPassword(email, password, name);

        try {
          const me = await api.firebaseExchange(idToken, referralCode);
          adopt(me);
          track(EVENTS.SIGN_UP, {
            method: AUTH_METHODS.PASSWORD,
            has_referral: Boolean(referralCode),
          });
        } catch (err) {
          if (err instanceof ApiError && err.status === 403) {
            track(EVENTS.AUTH_EMAIL_UNVERIFIED, { method: AUTH_METHODS.PASSWORD });
            track(EVENTS.VERIFICATION_EMAIL_SENT, { trigger: "sign_up" });
            throw new EmailVerificationPending(err.message);
          }
          throw err;
        }
      } catch (err) {
        if (err instanceof EmailVerificationPending) throw err;
        throw handleAuthFailure(err, AUTH_METHODS.PASSWORD, "sign_up");
      }
    },
    [adopt, firebaseReady],
  );

  /** Google, through Firebase. Popup, with a redirect fallback inside. */
  const signInWithGoogleAccount = useCallback(
    async (referralCode = "") => {
      track(EVENTS.AUTH_STARTED, { method: AUTH_METHODS.GOOGLE });

      try {
        const me = await measure("auth_google_sign_in", async () => {
          const idToken = await signInWithGoogle();
          return api.firebaseExchange(idToken, referralCode);
        });

        adopt(me);
        // Whether this was a first sign-in is the server's to know, so both
        // are recorded against the same method and separated in the reports
        // by whether the account is new.
        track(EVENTS.LOGIN, { method: AUTH_METHODS.GOOGLE });
      } catch (err) {
        throw handleAuthFailure(err, AUTH_METHODS.GOOGLE);
      }
    },
    [adopt],
  );

  const signOut = useCallback(async () => {
    track(EVENTS.LOGOUT, {});
    try {
      // Both halves, and in this order. The server call revokes the session
      // and the Firebase refresh tokens; clearing the local Firebase
      // credential first would cost us the ability to identify which user to
      // revoke.
      await api.logout();
    } catch {
      // The user asked to leave. A failed network call does not change that.
    } finally {
      await signOutFirebase();
      adopt(null);
      // The cookie is gone; every Server Component on the page was rendered
      // against it and now renders differently.
      router.refresh();
    }
  }, [adopt, router]);

  const value = useMemo(
    () => ({
      customer,
      loading,
      firebaseReady,
      signIn,
      signUp,
      signInWithGoogleAccount,
      signOut,
    }),
    [customer, loading, firebaseReady, signIn, signUp, signInWithGoogleAccount, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Record a failed sign-in, then hand the error back for the page to show.
 *
 * Failures are split by cause, because they mean different things: a wrong
 * password is a user event and expected at some steady rate, while a
 * misconfigured Firebase project or an unreachable API is an outage. Lumping
 * them into one `auth_failed` count hides the second behind the first.
 */
function handleAuthFailure(
  err: unknown,
  method: string,
  intent = "sign_in",
): unknown {
  const code =
    typeof err === "object" && err !== null && "code" in err
      ? String((err as { code: unknown }).code)
      : err instanceof ApiError
        ? `api/${err.status}`
        : "unknown";

  track(EVENTS.AUTH_FAILED, { method, intent, reason: code });

  // Wrong credentials and cancelled popups are the system working. Only the
  // unexpected ones are worth a crash report.
  const expected = new Set([
    "auth/invalid-credential",
    "auth/wrong-password",
    "auth/user-not-found",
    "auth/invalid-email",
    "auth/email-already-in-use",
    "auth/weak-password",
    "auth/popup-closed-by-user",
    "auth/cancelled-popup-request",
    "auth/too-many-requests",
    "api/401",
    "api/403",
    "api/409",
    "api/400",
  ]);

  if (!expected.has(code)) {
    reportError({
      error: err,
      kind: "api",
      context: { area: "auth", method, intent, code },
    });
  }

  return err;
}

/** The referral code stashed by the sign-up page, if any. */
function storedReferral(): string {
  try {
    return sessionStorage.getItem("nexora.referralCode") ?? "";
  } catch {
    return "";
  }
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/**
 * Client-side gate for signed-in-only routes.
 *
 * **This is the second line of defence, not the first.** The real gate is
 * server-side: each protected route calls `requireCustomer()` in its
 * `page.tsx`, which redirects before a single byte of the page is sent. That
 * is strictly better than what the Vite app could do — there, the browser
 * downloaded the dashboard bundle and only then discovered it had no session.
 *
 * What this still catches is the session ending *while the tab is open*: the
 * cookie's 30 days elapsing, an admin disabling the account, a sign-out in
 * another tab. The server cannot re-check a page it has already sent.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { customer, loading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !customer) {
      track(EVENTS.SESSION_EXPIRED, {
        recovered: false,
        blocked_route: pathname,
      });
      // `from` is carried so sign-in can send them back where they were.
      router.replace(`/signin?from=${encodeURIComponent(pathname)}`);
    }
  }, [loading, customer, pathname, router]);

  if (loading || !customer) {
    return (
      <div className="auth-page">
        <div className="spinner" />
      </div>
    );
  }

  return <>{children}</>;
}
