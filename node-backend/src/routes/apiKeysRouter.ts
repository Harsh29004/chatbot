/**
 * API key management router.
 *
 * Port of `backend/api_keys_router.py`.
 *
 * Admin endpoints for generating, listing, and revoking API keys. Customer
 * endpoint for checking usage/credits.
 *
 * Mounted at `/api/keys` by `server.ts`.
 */

import { Router } from "express";
import { z } from "zod";

import {
  generateApiKey,
  getNextResetTime,
  getUsageStats,
  listAllKeys,
  revokeKey,
  validateApiKey,
} from "../shared/apiKeys.js";
import { verifyAdminKey } from "../shared/auth.js";
import { CREDIT_COST_TIERS, DAILY_CREDIT_LIMIT } from "../config.js";
import { asyncHandler, forbidden, notFound, parseBody } from "../shared/http.js";

export const router = Router();

const generateKeySchema = z.object({
  owner_email: z.string().min(3).max(255),
  owner_name: z.string().max(255).default(""),
  label: z.string().max(255).default(""),
});

/** The fixed note Pydantic supplied as a field default on GenerateKeyResponse. */
const GENERATED_KEY_MESSAGE =
  "Save this key — it won't be shown again! Credits are shared across " +
  "every key on this account (identified by owner_email), not per-key.";

// ---------------------------------------------------------------------------
// Admin endpoints (require X-Admin-Key)
// ---------------------------------------------------------------------------

/**
 * Generate a new API key for a customer.
 *
 * The raw key is returned **once** in the response — it cannot be retrieved
 * again (only the hash is stored).
 */
router.post(
  "/generate",
  verifyAdminKey,
  asyncHandler(async (req, res) => {
    const body = parseBody(generateKeySchema, req.body);
    const result = await generateApiKey(body.owner_email, body.owner_name, body.label);
    res.json({ ...result, message: GENERATED_KEY_MESSAGE });
  }),
);

/** List all API keys with their current credit balance (admin only). */
router.get(
  "/",
  verifyAdminKey,
  asyncHandler(async (_req, res) => {
    const keys = await listAllKeys();
    res.json({ total: keys.length, keys });
  }),
);

/** Revoke an API key (admin only). The key becomes immediately unusable. */
router.delete(
  "/:keyId",
  verifyAdminKey,
  asyncHandler(async (req, res) => {
    const keyId = req.params.keyId;
    if (!(await revokeKey(keyId))) {
      throw notFound(`API key with id=${keyId} not found.`);
    }
    res.json({ status: "revoked", key_id: keyId });
  }),
);

// ---------------------------------------------------------------------------
// Customer endpoints (require X-Api-Key)
// ---------------------------------------------------------------------------

/**
 * Check your credit usage and remaining balance.
 *
 * This endpoint does NOT consume credits.
 */
router.get(
  "/usage",
  asyncHandler(async (req, res) => {
    const raw = req.headers["x-api-key"];
    const apiKey = (Array.isArray(raw) ? raw[0] : raw) ?? "";

    const keyRecord = apiKey ? await validateApiKey(apiKey) : null;
    if (keyRecord === null) throw forbidden("Invalid or revoked API key.");

    if (keyRecord.role === "owner") {
      res.json({
        is_unlimited: true,
        credits_remaining: -1,
        credits_used_today: 0,
        credits_daily_limit: -1,
        resets_at: getNextResetTime(),
        total_queries_all_time: 0,
        total_credits_consumed_all_time: 0,
        last_7_days: [],
      });
      return;
    }

    // Usage is account-level: pooled across every key this owner_email holds.
    const stats = await getUsageStats(keyRecord.user_id, keyRecord.daily_credit_limit);
    res.json({ is_unlimited: false, ...stats });
  }),
);

// ---------------------------------------------------------------------------
// Public endpoints
// ---------------------------------------------------------------------------

/**
 * Public endpoint showing credit cost tiers and daily limit.
 *
 * No authentication required.
 */
router.get(
  "/pricing",
  asyncHandler(async (_req, res) => {
    res.json({
      tiers: CREDIT_COST_TIERS.map(([maxChars, cost]) => ({
        max_characters: maxChars,
        credit_cost: cost,
      })),
      daily_limit: DAILY_CREDIT_LIMIT,
    });
  }),
);
