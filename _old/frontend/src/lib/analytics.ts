/**
 * The event catalogue, and the one way to send an event.
 *
 * Why a catalogue instead of calling logEvent() at each site
 * ----------------------------------------------------------
 * GA4 keeps whatever name it is first sent. A typo — `bot_creted`, or
 * `botCreated` next to `bot_created` — becomes a permanent second event that
 * quietly splits the funnel in two, and there is no rename. Naming every
 * event in one place, as a typed constant, makes that mistake a build error.
 *
 * It also means the analytics plan is readable. Scrolling this file tells you
 * what the product measures, which no amount of grepping for `logEvent` does.
 *
 * Naming rules, which GA4 enforces and will not tell you about
 * -----------------------------------------------------------
 * - `snake_case`, starting with a letter, ≤40 characters.
 * - ≤25 parameters per event; parameter names ≤40 chars, values ≤100 chars.
 * - Some names are reserved (`session_start`, `first_open`, `user_engagement`
 *   and friends). Sending one is silently dropped.
 * - Custom parameters do not appear in reports until registered as custom
 *   dimensions in the GA4 admin. The data is collected either way, so this is
 *   a one-time setup step, not a code change — see README.
 *
 * What must never be sent
 * -----------------------
 * **No email addresses, names, API keys, message contents or raw queries.**
 * Google's terms prohibit PII in Analytics, and an account could be
 * terminated over it. `sanitise()` below strips anything that looks like an
 * address as a backstop, but the real rule is upstream: pass counts, ids,
 * durations and categories — never the thing the user typed.
 */

import { logEvent, setUserId, setUserProperties } from "firebase/analytics";
import { trace as perfTrace } from "firebase/performance";

import { RELEASE, analytics, analyticsReady, performance } from "./firebase";

// ---------------------------------------------------------------------------
// The catalogue
// ---------------------------------------------------------------------------
// Grouped by the surface they happen on. GA4's own recommended names are used
// where one fits (`login`, `sign_up`, `purchase`, `search`, `share`,
// `exception`, `page_view`), because those light up the standard reports
// instead of needing a custom exploration built for each.

export const EVENTS = {
  // --- Navigation -------------------------------------------------------
  PAGE_VIEW: "page_view",
  // Fired once per session on the first route that is not a reload. Answers
  // "where do people arrive", which page_view alone cannot once the SPA takes
  // over navigation.
  SESSION_LANDING: "session_landing",

  // --- Landing / marketing ---------------------------------------------
  LANDING_CTA_CLICK: "landing_cta_click",
  PRICING_VIEWED: "pricing_viewed",
  PRICING_PLAN_SELECTED: "pricing_plan_selected",
  PRICING_INTERVAL_TOGGLED: "pricing_interval_toggled",
  DEMO_WIDGET_OPENED: "demo_widget_opened",

  // --- Auth -------------------------------------------------------------
  // GA4 recommended events. `method` is the conventional parameter and drives
  // the built-in breakdown, so it is always one of AUTH_METHODS below.
  LOGIN: "login",
  SIGN_UP: "sign_up",
  LOGOUT: "logout",
  AUTH_STARTED: "auth_started",
  AUTH_FAILED: "auth_failed",
  // Split out from auth_failed: this one is a product problem (the user is
  // stuck behind an unread email), not a wrong password.
  AUTH_EMAIL_UNVERIFIED: "auth_email_unverified",
  VERIFICATION_EMAIL_SENT: "verification_email_sent",
  PASSWORD_RESET_REQUESTED: "password_reset_requested",
  PASSWORD_RESET_SENT: "password_reset_sent",
  SESSION_EXPIRED: "session_expired",

  // --- Onboarding / bot setup ------------------------------------------
  ONBOARDING_STARTED: "onboarding_started",
  ONBOARDING_STEP_COMPLETED: "onboarding_step_completed",
  ONBOARDING_COMPLETED: "onboarding_completed",
  TEMPLATE_VIEWED: "template_viewed",
  TEMPLATE_SELECTED: "template_selected",
  BOT_CREATED: "bot_created",
  BOT_UPDATED: "bot_updated",
  BOT_DELETED: "bot_deleted",
  SHEET_UPLOAD_STARTED: "sheet_upload_started",
  SHEET_UPLOAD_SUCCEEDED: "sheet_upload_succeeded",
  SHEET_UPLOAD_FAILED: "sheet_upload_failed",
  BOT_TESTED: "bot_tested",
  WIDGET_SNIPPET_COPIED: "widget_snippet_copied",
  WIDGET_PREVIEWED: "widget_previewed",

  // --- API keys ---------------------------------------------------------
  API_KEY_CREATED: "api_key_created",
  API_KEY_COPIED: "api_key_copied",
  API_KEY_REVOKED: "api_key_revoked",

  // --- Billing ----------------------------------------------------------
  // GA4 ecommerce names, so revenue lands in the standard monetisation
  // reports without a custom funnel.
  VIEW_ITEM_LIST: "view_item_list",
  BEGIN_CHECKOUT: "begin_checkout",
  PURCHASE: "purchase",
  CHECKOUT_ABANDONED: "checkout_abandoned",
  CHECKOUT_FAILED: "checkout_failed",
  TRIAL_STARTED: "trial_started",
  TRIAL_ENDED: "trial_ended",
  SUBSCRIPTION_CANCELLED: "subscription_cancelled",
  INVOICE_VIEWED: "invoice_viewed",
  CREDITS_EXHAUSTED: "credits_exhausted",
  CREDITS_LOW_WARNING: "credits_low_warning",

  // --- Referrals --------------------------------------------------------
  SHARE: "share",
  REFERRAL_LINK_COPIED: "referral_link_copied",
  REFERRAL_INVITE_SENT: "referral_invite_sent",
  REFERRAL_CODE_APPLIED: "referral_code_applied",
  REFERRAL_REWARD_EARNED: "referral_reward_earned",

  // --- Chat / assistant -------------------------------------------------
  CHAT_OPENED: "chat_opened",
  CHAT_CLOSED: "chat_closed",
  CHAT_MESSAGE_SENT: "chat_message_sent",
  CHAT_RESPONSE_RECEIVED: "chat_response_received",
  CHAT_RESPONSE_FAILED: "chat_response_failed",
  CHAT_FEEDBACK_GIVEN: "chat_feedback_given",
  CHAT_CLEARED: "chat_cleared",
  ASSISTANT_OPENED: "assistant_opened",
  ASSISTANT_MESSAGE_SENT: "assistant_message_sent",
  ASSISTANT_RESPONSE_RECEIVED: "assistant_response_received",
  ASSISTANT_RATE_LIMITED: "assistant_rate_limited",

  // --- Gap list ---------------------------------------------------------
  GAP_LIST_VIEWED: "gap_list_viewed",
  GAP_EXPORTED: "gap_exported",

  // --- Support ----------------------------------------------------------
  SUPPORT_OPENED: "support_opened",
  SUPPORT_TICKET_CREATED: "support_ticket_created",
  SUPPORT_REPLY_SENT: "support_reply_sent",

  // --- Search -----------------------------------------------------------
  SEARCH: "search",

  // --- Health -----------------------------------------------------------
  // `exception` is GA4's recommended crash event and feeds its own report.
  EXCEPTION: "exception",
  API_ERROR: "api_error",
  // A Performance custom trace finished. Duration is on the trace itself;
  // this is the event that makes it visible in Analytics too.
  PERF_TRACE: "perf_trace",
  FEATURE_USED: "feature_used",
} as const;

export type EventName = (typeof EVENTS)[keyof typeof EVENTS];

/** Values for the `method` parameter on auth events. Keep the set small. */
export const AUTH_METHODS = {
  GOOGLE: "google",
  PASSWORD: "password",
  GOOGLE_REDIRECT: "google_redirect",
} as const;

// ---------------------------------------------------------------------------
// URL scrubbing
// ---------------------------------------------------------------------------

/**
 * Query parameters that are safe to record. Everything else is dropped.
 *
 * An allowlist, not a blocklist, and deliberately so. A blocklist has to
 * predict every credential anyone will ever put in a URL, and it only has to
 * be wrong once — the failure mode is a live secret sitting in an analytics
 * report or a log file.
 *
 * The parameter that forced this: Firebase's own account-management links
 * carry `oobCode`, a one-time code that **resets a password**. A crash or a
 * page view on a reset page would otherwise ship it to GA4 and to our crash
 * log, where it outlives the request and is replayable by anyone who can
 * read either. The same applies to `apiKey`, `token`, `id_token` and
 * whatever the next integration invents.
 */
const SAFE_QUERY_PARAMS = new Set([
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "ref",
  "plan",
  "tab",
]);

/**
 * A URL fit to record: origin and path kept, query filtered, fragment dropped.
 *
 * The fragment goes unconditionally. OAuth implicit flows and several
 * Firebase paths return credentials in it (`#access_token=...`), it is never
 * sent to a server anyway, and nothing here needs it.
 *
 * Unknown parameters are replaced rather than deleted, so a report still
 * shows that the page carried state without revealing what.
 */
export function safeUrl(href: string): string {
  try {
    const parsed = new URL(href, window.location.origin);
    const kept = new URLSearchParams();

    parsed.searchParams.forEach((value, key) => {
      kept.set(key, SAFE_QUERY_PARAMS.has(key) ? value : "<redacted>");
    });

    const query = kept.toString();
    return `${parsed.origin}${parsed.pathname}${query ? `?${query}` : ""}`;
  } catch {
    // Not parseable as a URL. Returning the path alone is the safe answer —
    // never the original string, which is what we were trying to scrub.
    return window.location.pathname;
  }
}

/** The path and safe query of the current page. Never the fragment. */
export function safeCurrentUrl(): string {
  return safeUrl(window.location.href);
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

export type EventParams = Record<string, string | number | boolean | undefined | null>;

// Looks like an email address. Used to catch one being passed by accident,
// because the cost of noticing late is a policy violation rather than a bug.
const EMAIL_SHAPE = /[^\s@]+@[^\s@]+\.[^\s@]+/;

const MAX_PARAM_VALUE = 100;
const MAX_PARAMS = 25;

/**
 * Make a parameter bag safe and GA4-legal.
 *
 * Drops empties (so reports are not full of `undefined`), redacts anything
 * shaped like an email, truncates to GA4's limits, and caps the count. Doing
 * this centrally means no call site has to remember any of it.
 */
function sanitise(params: EventParams): Record<string, string | number | boolean> {
  const clean: Record<string, string | number | boolean> = {};
  let count = 0;

  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (count >= MAX_PARAMS) break;

    const name = key.slice(0, 40);

    if (typeof value === "string") {
      // Not an exception, not a thrown error: a dropped parameter is better
      // than a policy violation, and the console note is for whoever added it.
      if (EMAIL_SHAPE.test(value)) {
        if (import.meta.env.DEV) {
          console.warn(
            `[analytics] Parameter "${key}" looked like an email address and was redacted. ` +
              `Send an id or a category instead — PII must not reach Analytics.`,
          );
        }
        clean[name] = "[redacted]";
      } else {
        clean[name] = value.slice(0, MAX_PARAM_VALUE);
      }
    } else {
      clean[name] = value;
    }
    count += 1;
  }

  return clean;
}

/**
 * Record one event. Never throws, never blocks, never awaited by a caller.
 *
 * Analytics may not have finished starting when the first events fire, so
 * this queues behind `analyticsReady()` rather than dropping them. If
 * Analytics turns out to be unavailable — blocked, unsupported, unconfigured
 * — the promise resolves to null and the event is discarded silently, which
 * is the correct outcome and not a failure.
 */
export function track(name: EventName | string, params: EventParams = {}): void {
  const payload = sanitise({ release: RELEASE, ...params });

  const send = (): void => {
    const instance = analytics();
    if (!instance) return;
    try {
      logEvent(instance, name as string, payload);
    } catch (err) {
      if (import.meta.env.DEV) console.warn("[analytics] logEvent failed", name, err);
    }
  };

  if (analytics()) {
    send();
    return;
  }

  // Not ready yet — most likely the first events of the session.
  void analyticsReady().then((instance) => {
    if (instance) send();
  });
}

/**
 * Tie events to an account, once signed in.
 *
 * The *customer id*, never the email. GA4's user id is for joining sessions
 * across devices; putting an address in it is the PII violation in its purest
 * form.
 */
export function identify(
  customerId: string | null,
  properties: EventParams = {},
): void {
  void analyticsReady().then((instance) => {
    if (!instance) return;
    try {
      setUserId(instance, customerId);
      const clean = sanitise(properties);
      if (Object.keys(clean).length > 0) setUserProperties(instance, clean);
    } catch {
      // Same reasoning as track(): telemetry never breaks the product.
    }
  });
}

/**
 * Page views for a single-page app.
 *
 * GA4's automatic page_view fires on the initial document load and then never
 * again, because React Router changes the URL without a navigation. Every
 * route change after the first would be invisible without this.
 */
export function trackPageView(path: string, title?: string): void {
  track(EVENTS.PAGE_VIEW, {
    // Scrubbed: a reset or verification link puts a one-time credential in
    // the query string, and GA4 is not a place to keep one.
    page_path: safeUrl(path),
    page_title: title ?? document.title,
    page_location: safeCurrentUrl(),
  });
}

// ---------------------------------------------------------------------------
// Performance
// ---------------------------------------------------------------------------

/**
 * Time an operation as a Performance custom trace *and* an Analytics event.
 *
 * Both, because they answer different questions: Performance gives the
 * distribution across real users (p50, p95, by country and connection),
 * Analytics lets the duration sit beside the rest of the funnel. Neither
 * alone tells you "checkout is slow for the people who then abandon it".
 *
 * The timing is measured here rather than taken from the trace so that a
 * duration is still recorded when Performance Monitoring is unavailable.
 */
export async function measure<T>(
  name: string,
  operation: () => Promise<T>,
  params: EventParams = {},
): Promise<T> {
  const perf = performance();
  const started = Date.now();

  let customTrace: ReturnType<typeof perfTrace> | null = null;
  if (perf) {
    try {
      customTrace = perfTrace(perf, name);
      customTrace.start();
    } catch {
      customTrace = null;
    }
  }

  let ok = true;
  try {
    return await operation();
  } catch (err) {
    ok = false;
    throw err;
  } finally {
    const durationMs = Date.now() - started;
    if (customTrace) {
      try {
        // Recorded on the trace so it is filterable in the Performance
        // console, where a failed run's timing would otherwise skew the
        // successful ones.
        customTrace.putAttribute("outcome", ok ? "success" : "error");
        customTrace.stop();
      } catch {
        // A trace that cannot be stopped is simply not reported.
      }
    }
    track(EVENTS.PERF_TRACE, {
      trace_name: name,
      duration_ms: durationMs,
      success: ok,
      ...params,
    });
  }
}
