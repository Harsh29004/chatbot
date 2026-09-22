/**
 * Where the browser reports its own crashes.
 *
 * Port of `backend/telemetry_router.py`. Mounted at `/api/telemetry`.
 *
 * Firebase Crashlytics has no Web SDK, so the page does the catching and this
 * is where the stack trace lands. The Analytics `exception` event fires from
 * the browser in parallel and gives the Firebase console a rate to alarm on;
 * this endpoint gives a human something to read when that alarm goes off.
 *
 * Unauthenticated on purpose
 * --------------------------
 * The crashes worth hearing about most are the ones that happen *before*
 * sign-in — a broken bundle, a failing auth redirect, a page that throws on
 * first paint. Requiring a session would filter out exactly those.
 *
 * So it is treated as a public write endpoint, which means:
 *
 * * **Rate limited per IP**, in MongoDB, so the limit survives a restart and is
 *   shared across workers. A page stuck in a render-crash-remount loop can emit
 *   hundreds of reports a second, and that is a self-inflicted flood before it
 *   is ever an attack.
 * * **Every field length-capped** on the way in (`telemetryStore`).
 * * **Always answers 202**, whatever happened. A reporter that returns errors
 *   invites a retry loop on top of the crash loop it is already reporting, and
 *   tells a prober whether its input was interesting.
 */

import { Router, type Request } from "express";
import { z } from "zod";

import * as db from "../billing/db.js";
import * as config from "../config.js";
import { asyncHandler } from "../shared/http.js";
import { logger } from "../shared/logger.js";
import * as rateLimits from "../shared/rateLimits.js";
import * as telemetryStore from "../shared/telemetryStore.js";

export const router = Router();

const SESSION_COOKIE = config.SESSION_COOKIE_NAME;

const ERROR_BUCKET = "client_error";
const ERROR_WINDOW_SECONDS = 3600;

/**
 * One crash, as the browser saw it.
 *
 * The limits here are the first line of defence and are set well above what a
 * real report needs — the store clips again on write. Rejecting an oversized
 * field costs nothing; a 2 MB stack reaching MongoDB does.
 */
const clientErrorSchema = z.object({
  message: z.string().max(2000).default(""),
  stack: z.string().max(20000).default(""),
  // "error" | "unhandledrejection" | "react" | "api" — free-form, since the
  // page may grow new sources and an unknown one should still be recorded.
  kind: z.string().max(40).default("error"),
  fingerprint: z.string().max(64).default(""),
  route: z.string().max(500).default(""),
  url: z.string().max(1000).default(""),
  release: z.string().max(64).default(""),
  component_stack: z.string().max(10000).default(""),
  // Whether the user was left looking at a broken page, as opposed to an error
  // that was caught and recovered from. Mirrors the GA4 flag.
  fatal: z.boolean().default(false),
  context: z.record(z.unknown()).nullable().default(null),
});

/**
 * The caller's address, trusting the proxy's first hop only.
 *
 * Same approach as the signup limiter: behind Caddy the socket address is the
 * proxy, so `X-Forwarded-For` is what identifies the browser. Only the leftmost
 * entry is used, and it is bounded, because the rest of that header is whatever
 * the client chose to send.
 */
function clientIp(req: Request): string {
  const forwarded = req.headers["x-forwarded-for"];
  const header = Array.isArray(forwarded) ? forwarded[0] : forwarded;
  if (header) return header.split(",")[0].trim().slice(0, 64);
  return (req.socket.remoteAddress ?? "unknown").slice(0, 64);
}

/**
 * Record a client-side crash.
 *
 * Answers 202 in every case, including when the report was dropped. The browser
 * has nothing useful to do with a failure here, and a page that is already
 * crashing should not also be handling errors from its crash reporter.
 */
router.post(
  "/error",
  asyncHandler(async (req, res) => {
    if (!config.TELEMETRY_ENABLED) {
      res.status(202).json({ status: "disabled" });
      return;
    }

    // Parsed leniently rather than through `parseBody`: this endpoint must not
    // answer 422 to a crashing page. A malformed report is recorded with
    // whatever fields did validate.
    const parsed = clientErrorSchema.safeParse(req.body ?? {});
    const body = parsed.success ? parsed.data : clientErrorSchema.parse({});

    const ip = clientIp(req);

    try {
      const seen = await rateLimits.count(ERROR_BUCKET, ip, ERROR_WINDOW_SECONDS);
      if (seen >= config.TELEMETRY_MAX_ERRORS_PER_IP_PER_HOUR) {
        // Logged once per over-limit report at debug, not warning: a crash loop
        // would otherwise move the flood from MongoDB to the log file, which is
        // not an improvement.
        logger.debug(`Client error report rate limited for ${ip}`);
        res.status(202).json({ status: "throttled" });
        return;
      }
      await rateLimits.record(ERROR_BUCKET, ip, ERROR_WINDOW_SECONDS);
    } catch {
      // A limiter outage must not lose reports.
      logger.debug("Telemetry rate limiter unavailable; accepting report");
    }

    // Attributing the crash to an account when there is one. A failure to
    // resolve the session is not interesting — the report is still worth
    // keeping without a name on it.
    let customerId: string | null = null;
    const token = req.cookies?.[SESSION_COOKIE];
    if (token) {
      try {
        const customer = await db.getSessionCustomer(token);
        if (customer) customerId = String(customer.id);
      } catch {
        customerId = null;
      }
    }

    await telemetryStore.recordError({
      message: body.message,
      stack: body.stack,
      kind: body.kind,
      fingerprint: body.fingerprint,
      route: body.route,
      url: body.url,
      userAgent: req.headers["user-agent"] ?? "",
      release: body.release,
      componentStack: body.component_stack,
      fatal: body.fatal,
      customerId,
      clientIp: ip,
      context: body.context,
    });

    res.status(202).json({ status: "recorded" });
  }),
);
