/**
 * Firebase, initialised once and degrading to nothing when unconfigured.
 *
 * What is here
 * ------------
 * - **Auth** — the sign-in itself. Google popup and email+password, plus
 *   password reset and email verification, all run by Firebase.
 * - **Analytics** — GA4 events. See `analytics.ts` for the event catalogue.
 * - **Performance** — automatic page-load and network traces, plus the custom
 *   traces `analytics.ts` exposes.
 *
 * What is *not* here, and cannot be
 * ---------------------------------
 * **Crashlytics.** It has no Web SDK — Firebase ships it for Android, iOS,
 * Flutter and Unity only. The browser equivalent lives in `errorReporting.ts`:
 * a GA4 `exception` event so the crash rate shows up in the Firebase console,
 * plus a POST to `/api/telemetry/error` carrying the stack trace, which is the
 * part Analytics will not store for you.
 *
 * Why everything is lazy and nullable
 * -----------------------------------
 * A missing measurement id, a browser with tracking blocked, a private window
 * with IndexedDB disabled, or a server-side render all make one of these SDKs
 * unavailable. None of those is a reason for the product to stop working, so
 * every accessor returns null instead of throwing and every caller treats
 * analytics as optional. Sign-in is the exception: if auth is unconfigured the
 * UI hides the Firebase buttons rather than pretending they work.
 *
 * The config below is public by design. Vite inlines `VITE_*` into the bundle,
 * and a Firebase web apiKey is a project identifier rather than a secret —
 * access is controlled by Auth, Security Rules and API key restrictions. The
 * service account private key is a different thing entirely and lives only in
 * the server's environment.
 */

import { initializeApp, type FirebaseApp } from "firebase/app";
import {
  browserLocalPersistence,
  getAuth,
  setPersistence,
  type Auth,
} from "firebase/auth";
import { getAnalytics, isSupported, type Analytics } from "firebase/analytics";
import { getPerformance, type FirebasePerformance } from "firebase/performance";

const env = process.env;

const firebaseConfig = {
  apiKey: env.NEXT_PUBLIC_FIREBASE_API_KEY ?? "",
  authDomain: env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN ?? "",
  projectId: env.NEXT_PUBLIC_FIREBASE_PROJECT_ID ?? "",
  storageBucket: env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET ?? "",
  messagingSenderId: env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID ?? "",
  appId: env.NEXT_PUBLIC_FIREBASE_APP_ID ?? "",
  measurementId: env.NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID ?? "",
};

/** The build this is, echoed on every event and crash report. */
export const RELEASE: string = env.NEXT_PUBLIC_APP_RELEASE ?? "dev";

/**
 * Enough config to sign someone in.
 *
 * `appId` matters as much as `apiKey`: with the key alone the SDK initialises
 * and then fails on the first call, which reads as "sign-in is broken" rather
 * than "sign-in is not set up".
 */
export const isFirebaseConfigured: boolean = Boolean(
  firebaseConfig.apiKey && firebaseConfig.appId && firebaseConfig.projectId,
);

const analyticsWanted =
  env.NEXT_PUBLIC_FIREBASE_ANALYTICS_ENABLED !== "false" &&
  Boolean(firebaseConfig.measurementId);

const performanceWanted = env.NEXT_PUBLIC_FIREBASE_PERFORMANCE_ENABLED !== "false";

let app: FirebaseApp | null = null;
let authInstance: Auth | null = null;
let analyticsInstance: Analytics | null = null;
let performanceInstance: FirebasePerformance | null = null;

/** Callbacks waiting for Analytics, which resolves asynchronously. */
let analyticsReadyResolvers: ((a: Analytics | null) => void)[] = [];
let analyticsSettled = false;

function getApp(): FirebaseApp | null {
  if (!isFirebaseConfigured) return null;
  if (!app) app = initializeApp(firebaseConfig);
  return app;
}

/**
 * The Auth instance, or null when Firebase is not configured.
 *
 * Persistence is set to local (IndexedDB, falling back internally) so a
 * refresh does not sign the user out of Firebase while our own session cookie
 * is still valid — which would otherwise leave the two halves disagreeing
 * about whether anyone is signed in.
 */
export function firebaseAuth(): Auth | null {
  const instance = getApp();
  if (!instance) return null;

  if (!authInstance) {
    authInstance = getAuth(instance);
    // Fire-and-forget: a browser that refuses storage still signs people in,
    // it just forgets them on reload, and that is better than failing here.
    setPersistence(authInstance, browserLocalPersistence).catch(() => {});
  }
  return authInstance;
}

/**
 * Start Analytics and Performance.
 *
 * Called once from `main.tsx`. Analytics support is checked rather than
 * assumed: `isSupported()` returns false in environments without the APIs GA4
 * needs (some in-app webviews, private modes, SSR), and calling `getAnalytics`
 * there throws.
 */
export function initFirebaseTelemetry(): void {
  const instance = getApp();
  if (!instance) {
    settleAnalytics(null);
    return;
  }

  if (analyticsWanted) {
    isSupported()
      .then((supported) => {
        if (!supported) {
          settleAnalytics(null);
          return;
        }
        try {
          analyticsInstance = getAnalytics(instance);
          settleAnalytics(analyticsInstance);
        } catch {
          // Blocked by an extension, or storage refused. Not an error worth
          // showing anyone — the product does not depend on it.
          settleAnalytics(null);
        }
      })
      .catch(() => settleAnalytics(null));
  } else {
    settleAnalytics(null);
  }

  if (performanceWanted) {
    try {
      // Automatic traces start here: page load, first paint, and every fetch
      // the page makes. The custom ones are in `analytics.ts`.
      performanceInstance = getPerformance(instance);
    } catch {
      performanceInstance = null;
    }
  }
}

function settleAnalytics(value: Analytics | null): void {
  analyticsSettled = true;
  analyticsInstance = value;
  analyticsReadyResolvers.forEach((resolve) => resolve(value));
  analyticsReadyResolvers = [];
}

/** Analytics if it has finished starting, otherwise null. Never throws. */
export function analytics(): Analytics | null {
  return analyticsInstance;
}

/**
 * Analytics once it has settled, one way or the other.
 *
 * Needed because events fire during the first render, before `isSupported()`
 * has resolved. Without this the first few events of every session — the ones
 * covering landing and sign-in, which are the interesting ones — are silently
 * dropped.
 */
export function analyticsReady(): Promise<Analytics | null> {
  if (analyticsSettled) return Promise.resolve(analyticsInstance);
  return new Promise((resolve) => analyticsReadyResolvers.push(resolve));
}

/** Performance Monitoring, or null when off or unsupported. */
export function performance(): FirebasePerformance | null {
  return performanceInstance;
}
