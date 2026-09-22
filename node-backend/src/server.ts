/**
 * Nexora AI — main Express application.
 *
 * Port of `backend/server.py`.
 *
 * Mounts the platform API (accounts, billing, dashboard), the bot API
 * (templates, sheet upload, `/v1/ask`), API-key management, and a health check.
 *
 * Run with:  `npm run dev`  (or `npm run build && npm start`)
 */

import express, { type Request, type Response } from "express";
import cookieParser from "cookie-parser";
import cors from "cors";
import fs from "node:fs";
import path from "node:path";

import * as config from "./config.js";
import { errorMiddleware, notFoundMiddleware } from "./shared/http.js";
import { logger } from "./shared/logger.js";
import { ensureIndexes, getClient, resetClient } from "./shared/mongo.js";
import { botService } from "./bot/client.js";

import * as entitlements from "./billing/entitlements.js";
import { purgeDeadSessions } from "./billing/db.js";

import { router as platformRouter } from "./billing/router.js";
import { router as botRouter } from "./bot/router.js";
import { router as widgetRouter } from "./bot/widget/router.js";
import { router as keysRouter } from "./routes/apiKeysRouter.js";
import { router as ownerRouter } from "./routes/ownerRouter.js";
import { router as assistantRouter } from "./assistant/router.js";
import { router as supportRouter } from "./support/router.js";
import { router as adminLoginRouter } from "./admin/loginRouter.js";
import { router as adminRouter } from "./admin/router.js";
import { router as telemetryRouter } from "./routes/telemetryRouter.js";

export const app = express();

// Behind Caddy/nginx, `req.ip` and `req.protocol` must read X-Forwarded-*.
// Without this every rate limiter keys on the proxy's address and limits the
// whole internet as one client.
app.set("trust proxy", Number(process.env.TRUST_PROXY_HOPS ?? "1"));
app.disable("x-powered-by");

// ---------------------------------------------------------------------------
// Body parsing
// ---------------------------------------------------------------------------
// Registered before every router because the API-key middleware computes credit
// cost from `req.body.message`. The raw body is kept on the webhook path only:
// a provider signature is computed over the exact bytes, and a re-serialised
// JSON object is not those bytes.
app.use(
  express.json({
    limit: "1mb",
    verify: (req, _res, buf) => {
      if (req.url?.startsWith("/api/billing/webhook")) {
        (req as Request & { rawBody?: Buffer }).rawBody = Buffer.from(buf);
      }
    },
  }),
);
app.use(express.urlencoded({ extended: false, limit: "1mb" }));
app.use(cookieParser());

// ---------------------------------------------------------------------------
// CORS
// ---------------------------------------------------------------------------
// The dashboard authenticates with a session cookie, and browsers refuse to
// send credentials to a wildcard origin — so the web origins are listed
// explicitly. Set WEB_ORIGINS (comma-separated) in production.

const CREDIT_HEADERS = [
  "X-Credits-Remaining",
  "X-Credits-Daily-Limit",
  "X-Credits-Reset-At",
  "X-Credit-Cost",
];

/**
 * Open CORS for the key-authenticated `/v1` API only.
 *
 * An installed widget calls `/v1` from the customer's own domain, which can't
 * be listed in WEB_ORIGINS ahead of time. That is safe to allow from any origin
 * because these routes authenticate with the `X-Api-Key` header, never the
 * session cookie — credentials are not allowed here, so a hostile page gains
 * nothing it didn't already have. Every other path falls through to the strict,
 * cookie-aware policy below.
 *
 * Registered first so it answers before the strict policy sees the request.
 */
app.use((req, res, next) => {
  if (!req.path.startsWith("/v1/")) {
    next();
    return;
  }

  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Expose-Headers", CREDIT_HEADERS.join(", "));

  if (req.method === "OPTIONS") {
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, X-Api-Key");
    res.setHeader("Access-Control-Max-Age", "600");
    res.status(204).end();
    return;
  }
  next();
});

app.use(
  cors({
    origin: config.WEB_ORIGINS,
    credentials: true,
    methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allowedHeaders: ["Content-Type", "X-Api-Key", "X-Admin-Key"],
    exposedHeaders: CREDIT_HEADERS,
  }),
);

// ---------------------------------------------------------------------------
// Mount routers
// ---------------------------------------------------------------------------
// Order mirrors server.py. The static SPA fallback is registered last so it can
// never shadow an API route.

app.use("/api", platformRouter); // billing/auth/dashboard — declares its own /auth, /billing, …
app.use(botRouter); // /api/templates, /api/bot, /v1/ask
app.use(widgetRouter); // /api/widget, /widget/v1, /v1/widget/activate
app.use("/api/keys", keysRouter);
app.use("/v1/owner", ownerRouter);
app.use("/api/assistant", assistantRouter);
app.use(supportRouter); // /api/support and /api/support/admin
app.use(adminLoginRouter); // /api/admin/login — before the admin router
app.use("/api/admin", adminRouter);
app.use("/api/telemetry", telemetryRouter);

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok" });
});

// ---------------------------------------------------------------------------
// Static web app (production only)
// ---------------------------------------------------------------------------
// In development Next.js serves the app on :3000 and proxies /api here. In a
// container a static export can be copied to `frontend/dist` and served from
// this same origin, which is why the session cookie needs no cross-site
// handling in production. Registered last so it can never shadow an API route.
//
// A Next.js app using SSR runs its own server instead, and this block simply
// finds no directory and does nothing — which is the intended arrangement for
// the App Router build.

const WEB_DIST = path.resolve(config.PROJECT_ROOT, "frontend", "dist");

if (fs.existsSync(WEB_DIST) && fs.statSync(WEB_DIST).isDirectory()) {
  app.use(express.static(WEB_DIST, { index: false }));

  /**
   * Serve the SPA shell for any non-API path.
   *
   * Client-side routes like /dashboard are not files on disk, so anything that
   * isn't a real asset gets index.html and the router takes over.
   */
  app.get("*", (req: Request, res: Response, next) => {
    // `express.static` above already answered for real files; anything reaching
    // here is either a client route or a genuine 404 on an API path.
    if (req.path.startsWith("/api/") || req.path.startsWith("/v1/")) {
      next();
      return;
    }
    res.sendFile(path.join(WEB_DIST, "index.html"));
  });

  logger.info(`Serving the web app from ${WEB_DIST}`);
} else {
  logger.info("No frontend/dist bundle found — API only (use the Next.js dev server).");
}

// Registered after every router, in this order.
app.use(notFoundMiddleware);
app.use(errorMiddleware);

// ---------------------------------------------------------------------------
// Startup / shutdown
// ---------------------------------------------------------------------------

/** Withdraw lapsed entitlements and clear out dead sessions. */
async function sweepOnce(): Promise<[number, number]> {
  return [await entitlements.sync(), await purgeDeadSessions()];
}

let sweepTimer: NodeJS.Timeout | null = null;

/** Periodically expire lapsed plans and tidy the sessions collection. */
function startBackgroundSweep(): void {
  sweepTimer = setInterval(() => {
    void (async () => {
      try {
        const [withdrawn, purged] = await sweepOnce();
        if (withdrawn || purged) {
          logger.info(
            `Sweep: withdrew ${withdrawn} account(s), purged ${purged} dead session(s).`,
          );
        }
      } catch (error) {
        // A failed sweep must never take the server down with it; the next tick
        // retries, and the dashboard syncs on read regardless.
        logger.exception("Background sweep failed.", error);
      }
    })();
  }, config.ENTITLEMENT_SWEEP_SECONDS * 1000);

  // Without this the interval keeps the event loop alive and the process never
  // exits on its own.
  sweepTimer.unref();
}

/**
 * Startup tasks:
 * 1. Apply the MongoDB indexes every store declares
 * 2. Retire any subscriptions that lapsed while the server was down
 * 3. Start the periodic entitlement sweep
 */
export async function startup(): Promise<void> {
  // MongoDB builds collections on first write, so there is no schema step — but
  // the indexes are not optional. Two of them (one account per mailbox, one
  // referral payout per invoice) are constraints the application relies on
  // rather than optimisations, so a failure here must stop startup rather than
  // leave the server running without them.
  await ensureIndexes();
  logger.info("MongoDB indexes ensured.");

  const [withdrawn, purged] = await sweepOnce();
  if (withdrawn || purged) {
    logger.info(
      `Startup sweep: withdrew ${withdrawn} account(s), purged ${purged} dead session(s).`,
    );
  }

  startBackgroundSweep();

  // Not fatal: accounts, billing, support and the admin panel all work without
  // the bot service, and refusing to boot would take those down too. It warns
  // loudly instead, and the routes that need it answer 503 with a reason.
  void botService.health();
}

export async function shutdown(): Promise<void> {
  if (sweepTimer) clearInterval(sweepTimer);
  await resetClient(null);
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
// Guarded so importing this module in a test does not bind a port.

const isEntryPoint =
  process.argv[1] !== undefined &&
  import.meta.url === new URL(`file://${process.argv[1].replace(/\\/g, "/")}`).href;

if (isEntryPoint || process.env.NEXORA_START === "1") {
  // Connect before listening, so a bad MONGO_URI fails at boot with a readable
  // error rather than on the first request.
  await getClient();
  await startup();

  const server = app.listen(config.PORT, config.HOST, () => {
    logger.info(`Nexora AI listening on http://${config.HOST}:${config.PORT}`);
  });

  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.on(signal, () => {
      logger.info(`${signal} received — shutting down.`);
      server.close(() => {
        void shutdown().then(() => process.exit(0));
      });
    });
  }
}
