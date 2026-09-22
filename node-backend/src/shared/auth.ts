/**
 * API-key authentication and admin key verification.
 *
 * Port of `backend/shared/auth.py`.
 *
 * - Chat endpoints require a valid `X-Api-Key` header with sufficient credits.
 *   Each request deducts credits based on message length.
 * - Admin endpoints require a separate `X-Admin-Key` header.
 *
 * FastAPI expressed these as `Depends()` parameters that *returned* the key
 * record into the handler's signature. Express has no such mechanism, so each
 * one is middleware that attaches the record to the request instead, and
 * handlers read it through {@link keyRecordOf}. The checks, the order they run
 * in and every status code are unchanged.
 *
 * One ordering note that matters: the credit cost is computed from the request
 * body, so `express.json()` must already have run. It is registered globally in
 * `server.ts`, before any router.
 */

import type { NextFunction, Request, RequestHandler, Response } from "express";

import * as config from "../config.js";
import { DAILY_CREDIT_LIMIT, getCreditCost } from "../config.js";
import {
  ROLE_OWNER,
  consumeCredits,
  getCreditsRemaining,
  getNextResetTime,
  recordRequest,
  validateApiKey,
  type KeyRecord,
} from "./apiKeys.js";
import { asyncHandler, forbidden, paymentRequired, unauthorized } from "./http.js";
import { constantTimeEqual } from "../billing/security.js";

/** Where the authenticated key record is parked on the request. */
declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      keyRecord?: KeyRecord;
    }
  }
}

/**
 * The key record for the current request.
 *
 * Throws rather than returning undefined, because reaching a handler without
 * one means the middleware was not mounted — a wiring bug that should be loud
 * in development, not a silent anonymous request in production.
 */
export function keyRecordOf(req: Request): KeyRecord {
  if (!req.keyRecord) {
    throw new Error("No API key on this request — verifyApiKey middleware is missing.");
  }
  return req.keyRecord;
}

/** The `X-Api-Key` header, or an empty string. */
function apiKeyHeader(req: Request): string {
  const raw = req.headers["x-api-key"];
  if (Array.isArray(raw)) return raw[0] ?? "";
  return raw ?? "";
}

/** The length of the `message` field, or 0 when the body has none. */
function messageLength(req: Request): number {
  try {
    const message = (req.body as Record<string, unknown> | undefined)?.message;
    return typeof message === "string" ? message.length : 0;
  } catch {
    return 0;
  }
}

/**
 * Validate an API key and check credits.
 *
 * - Validates the key against the database
 * - Owner-role keys (see `apiKeys.createOwnerKey`) skip credit checks and
 *   deductions entirely — unlimited, for the product owners only
 * - Otherwise: calculates credit cost from the request body's message length,
 *   checks the account's shared credit pool, and deducts on success
 * - Injects `X-Credits-Remaining`, `X-Credits-Daily-Limit`,
 *   `X-Credits-Reset-At` and `X-Credit-Cost` into the response headers
 */
export const verifyApiKey: RequestHandler = asyncHandler(
  async (req: Request, res: Response, next: NextFunction) => {
    const rawKey = apiKeyHeader(req);
    if (!rawKey) {
      // FastAPI's `Header(...)` made the header required and answered 422 when
      // it was absent. A missing credential is more honestly a 403 here, and
      // matches what a wrong key gets — a prober learns nothing either way.
      throw forbidden("Invalid or revoked API key.");
    }

    const keyRecord = await validateApiKey(rawKey);
    if (keyRecord === null) {
      throw forbidden("Invalid or revoked API key.");
    }

    // Owner keys bypass credit checks entirely — unlimited access. They are
    // still logged: not charging is deliberate, leaving no audit trail is not.
    if (keyRecord.role === ROLE_OWNER) {
      const messageLen = messageLength(req);
      await recordRequest(keyRecord.user_id, keyRecord.id, req.path, messageLen);

      res.setHeader("X-Credits-Remaining", "unlimited");
      res.setHeader("X-Credits-Daily-Limit", "unlimited");
      res.setHeader("X-Credits-Reset-At", getNextResetTime());
      res.setHeader("X-Credit-Cost", "0");

      req.keyRecord = keyRecord;
      next();
      return;
    }

    const messageLen = messageLength(req);
    const creditCost = getCreditCost(messageLen);

    // Check credits (pooled across all of this account's keys).
    const userId = keyRecord.user_id;
    const dailyLimit = keyRecord.daily_credit_limit;
    const remaining = await getCreditsRemaining(userId, dailyLimit);

    if (remaining < creditCost) {
      throw paymentRequired({
        error: "Insufficient credits",
        credits_remaining: remaining,
        credit_cost: creditCost,
        resets_at: getNextResetTime(),
        message:
          `This query costs ${creditCost} credits but you only ` +
          `have ${remaining} remaining. Credits reset at midnight IST.`,
      });
    }

    // Deduct credits. The account's own limit is passed in: without it the
    // returned figure — and therefore the X-Credits-Remaining header — would be
    // computed against the global default, understating what a paid account has
    // left.
    const newRemaining = await consumeCredits(
      userId,
      keyRecord.id,
      creditCost,
      req.path,
      messageLen,
      dailyLimit,
    );

    res.setHeader("X-Credits-Remaining", String(newRemaining));
    res.setHeader(
      "X-Credits-Daily-Limit",
      String(dailyLimit === null ? DAILY_CREDIT_LIMIT : dailyLimit),
    );
    res.setHeader("X-Credits-Reset-At", getNextResetTime());
    res.setHeader("X-Credit-Cost", String(creditCost));

    req.keyRecord = keyRecord;
    next();
  },
);

/**
 * Authenticate the internal owner endpoints.
 *
 * Stricter than {@link verifyApiKey}: a valid customer key is **not** enough,
 * the key must carry the owner role. These routes read across every tenant, so
 * the check is role-based rather than "is this key valid".
 *
 * No credits are consumed — owner keys are unmetered by design — but the
 * request is still written to the audit log. An unlimited key that can read
 * every tenant's data is the one most worth a trail.
 */
export const verifyOwnerKey: RequestHandler = asyncHandler(
  async (req: Request, _res: Response, next: NextFunction) => {
    const rawKey = apiKeyHeader(req);
    const keyRecord = rawKey ? await validateApiKey(rawKey) : null;

    if (keyRecord === null || keyRecord.role !== ROLE_OWNER) {
      // Deliberately the same message a bad key gets. A customer key holder
      // probing this route learns only that it didn't work, not that they found
      // a real endpoint gated on a role they don't have.
      throw forbidden("Invalid or revoked API key.");
    }

    await recordRequest(keyRecord.user_id, keyRecord.id, req.path, messageLength(req));

    req.keyRecord = keyRecord;
    next();
  },
);

/**
 * Authenticate admin endpoints.
 *
 * Accepts either the configured `ADMIN_API_KEY` (for scripts) or an unexpired
 * session token from `POST /api/admin/login` (for the staff pages), both in the
 * `X-Admin-Key` header.
 *
 * The key is read off `config` per call rather than bound at import. A
 * module-level capture takes a snapshot, and a snapshot silently drifts:
 * whatever the value happened to be the first time this module was imported is
 * what the check uses forever, even after the setting changes.
 */
export const verifyAdminKey: RequestHandler = asyncHandler(
  async (req: Request, _res: Response, next: NextFunction) => {
    // Imported here rather than at module scope: admin imports shared, and a
    // top-level import back the other way would be a cycle.
    const session = await import("../admin/session.js");

    const raw = req.headers["x-admin-key"];
    const adminKey = (Array.isArray(raw) ? raw[0] : raw) ?? "";

    const keyOk = adminKey.length > 0 && constantTimeEqual(adminKey, config.ADMIN_API_KEY());
    if (!keyOk && !session.verifyToken(adminKey)) {
      throw forbidden("Invalid admin API key.");
    }
    next();
  },
);

/** Exported for the few places that need the raw check without the middleware. */
export function adminKeyIsValid(adminKey: string): boolean {
  return adminKey.length > 0 && constantTimeEqual(adminKey, config.ADMIN_API_KEY());
}

export { unauthorized };
