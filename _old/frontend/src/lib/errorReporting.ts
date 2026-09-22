/**
 * Crash reporting for the web, since Crashlytics does not do it.
 *
 * Firebase Crashlytics ships for Android, iOS, Flutter and Unity. There is no
 * `firebase/crashlytics` import for a browser app and there is not going to
 * be one, so this module is the replacement, built from the two things that
 * *are* available:
 *
 * 1. **A GA4 `exception` event**, which is Google's recommended crash event
 *    and feeds its own report in the Firebase console. This gives the rate,
 *    the trend, the affected version and — because it is an ordinary event —
 *    the ability to see which crashes precede a drop-off. What it does not
 *    give is a stack trace; GA4 truncates parameters at 100 characters.
 *
 * 2. **A POST to `/api/telemetry/error`**, which carries the full stack, the
 *    component stack, the route and the release into MongoDB, grouped by
 *    fingerprint. This is the part you actually read when something breaks.
 *
 * What is captured
 * ----------------
 * - `window.onerror` — uncaught synchronous errors.
 * - `unhandledrejection` — the async half, and by far the more common one in
 *   a React app, since a promise rejected in an effect reaches nothing else.
 * - The React `<ErrorBoundary>`, which is the only path that sees a render
 *   error together with the component stack that caused it.
 * - Explicit `reportError()` calls from code that catches something it wants
 *   recorded but can recover from.
 *
 * Deliberate omissions
 * --------------------
 * **Nothing the user typed.** No message bodies, no search text, no form
 * fields, no API keys. A crash report is worth having; a crash report that
 * quietly exfiltrates a support conversation is not.
 */

import { EVENTS, safeCurrentUrl, track } from "./analytics";
import { RELEASE } from "./firebase";

const ENDPOINT = "/api/telemetry/error";

/** Beyond this many reports per page load, stop sending. See below. */
const MAX_REPORTS_PER_SESSION = 25;

/** Identical crashes within this window count once. */
const DEDUPE_WINDOW_MS = 10_000;

let sent = 0;
const recent = new Map<string, number>();
let installed = false;

export interface ErrorReport {
  error: unknown;
  kind?: "error" | "unhandledrejection" | "react" | "api" | "manual";
  /** True when the user was left looking at a broken page. */
  fatal?: boolean;
  componentStack?: string;
  context?: Record<string, string | number | boolean>;
}

/**
 * A stable id for "the same crash", so repeats group instead of flooding.
 *
 * Built from the message with digits stripped and the first stack frame.
 * Stripping digits is what makes `Cannot read x of undefined at row 41` and
 * `...at row 87` one bug rather than two hundred. A hash rather than the text
 * so it is a bounded, indexable key.
 */
function fingerprint(message: string, stack: string): string {
  const normalised = message.replace(/\d+/g, "#").slice(0, 200);
  const topFrame = stack.split("\n")[1]?.trim().slice(0, 200) ?? "";
  const input = `${normalised}|${topFrame}`;

  // FNV-1a. Not cryptographic and does not need to be — it is a grouping key,
  // and the only property that matters is that the same crash gives the same
  // string.
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function describe(error: unknown): { message: string; stack: string } {
  if (error instanceof Error) {
    return { message: error.message || error.name, stack: error.stack ?? "" };
  }
  if (typeof error === "string") return { message: error, stack: "" };
  try {
    return { message: JSON.stringify(error).slice(0, 500), stack: "" };
  } catch {
    return { message: String(error), stack: "" };
  }
}

/**
 * Record one error. Safe to call from anywhere, including a failing handler.
 *
 * Returns nothing and never throws: a crash reporter that can itself crash
 * turns one broken page into an infinite loop.
 */
export function reportError({
  error,
  kind = "manual",
  fatal = false,
  componentStack = "",
  context = {},
}: ErrorReport): void {
  try {
    const { message, stack } = describe(error);
    if (!message) return;

    const id = fingerprint(message, stack);
    const now = Date.now();

    // A component that throws on every render remounts and throws again,
    // hundreds of times a second. Without these two guards the first such bug
    // to reach production DDoSes our own telemetry endpoint — and the report
    // is identical every time, so nothing is lost by dropping the repeats.
    const lastSeen = recent.get(id);
    if (lastSeen !== undefined && now - lastSeen < DEDUPE_WINDOW_MS) return;
    recent.set(id, now);

    if (sent >= MAX_REPORTS_PER_SESSION) return;
    sent += 1;

    // The Firebase console half. `description` and `fatal` are the parameter
    // names GA4's exception report expects.
    track(EVENTS.EXCEPTION, {
      description: message.slice(0, 100),
      fatal,
      error_kind: kind,
      fingerprint: id,
      page_path: window.location.pathname,
      ...context,
    });

    // The readable half.
    void fetch(ENDPOINT, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      // Survives the page being torn down mid-request, which is exactly what
      // happens when the error was fatal.
      keepalive: true,
      body: JSON.stringify({
        message: message.slice(0, 2000),
        stack: stack.slice(0, 20000),
        kind,
        fingerprint: id,
        route: window.location.pathname,
        // Scrubbed — see safeUrl(). A crash on a password-reset page
        // must not put the reset code in the log.
        url: safeCurrentUrl().slice(0, 1000),
        release: RELEASE,
        component_stack: componentStack.slice(0, 10000),
        fatal,
        context,
      }),
    }).catch(() => {
      // The network is down, or the API is the thing that broke. Either way
      // there is nowhere left to report it to.
    });
  } catch {
    // Reporting must not become the failure.
  }
}

/**
 * Attach the global handlers. Called once, from `main.tsx`.
 *
 * Both listeners re-throw nothing and return nothing, so the browser's own
 * console logging still happens — this adds reporting, it does not swallow
 * errors that a developer needs to see in devtools.
 */
export function installErrorReporting(): void {
  if (installed) return;
  installed = true;

  window.addEventListener("error", (event: ErrorEvent) => {
    // A failed <img>/<script> load also fires "error" on window, with no
    // `error` property. Those are worth knowing about but are not crashes,
    // and reporting them as such buries the real ones.
    if (!event.error) {
      if (event.target && event.target !== window) {
        const element = event.target as HTMLElement;
        track(EVENTS.API_ERROR, {
          error_kind: "resource_load",
          tag: element.tagName?.toLowerCase(),
          resource: (element as HTMLImageElement).src?.slice(0, 100),
        });
      }
      return;
    }

    reportError({
      error: event.error,
      kind: "error",
      fatal: true,
      context: {
        source: `${event.filename}:${event.lineno}:${event.colno}`.slice(0, 200),
      },
    });
  }, true);

  window.addEventListener("unhandledrejection", (event: PromiseRejectionEvent) => {
    reportError({
      error: event.reason,
      kind: "unhandledrejection",
      // Not fatal: the page is usually still usable. Reported all the same,
      // because this is where most real bugs in an async React app surface.
      fatal: false,
    });
  });
}
