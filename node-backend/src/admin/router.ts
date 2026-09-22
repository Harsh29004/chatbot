/**
 * The admin panel's HTTP surface.
 *
 * Port of `backend/admin/router.py`. Mounted at `/api/admin`.
 *
 * Gated on `X-Admin-Key` — the same header and the same shared secret the staff
 * support inbox already uses, so there is one staff credential rather than two.
 * Every route in this module reads or writes across *all* tenants, which is why
 * the check is applied once with `router.use(verifyAdminKey)` rather than per
 * route: a new endpoint added here is protected by default, and forgetting the
 * middleware cannot quietly open a cross-tenant hole.
 *
 * Reads live in `queries.ts`. Writes deliberately do not: granting credits goes
 * through `apiKeys`, changing a template goes through `bot/catalogue`, and
 * deactivating an account goes through both stores that have an opinion about
 * it. This module's job is to validate input, call the module that owns the
 * change, and say what happened.
 */

import { Router } from "express";
import { z } from "zod";

import * as billingDb from "../billing/db.js";
import { listPlans } from "../billing/plans.js";
import * as referrals from "../billing/referrals.js";
import { botService } from "../bot/client.js";
import {
  getUserByEmail,
  grantBonusCredits,
  listCreditGrants,
  revokeKey,
  setDailyCreditLimit,
} from "../shared/apiKeys.js";
import { verifyAdminKey } from "../shared/auth.js";
import {
  asyncHandler,
  badRequest,
  conflict,
  notFound,
  parseBody,
  parseQuery,
} from "../shared/http.js";
import * as llm from "../shared/llm.js";
import { logger } from "../shared/logger.js";
import type { Doc } from "../shared/mongo.js";
import * as telemetryStore from "../shared/telemetryStore.js";
import * as queries from "./queries.js";

export const router = Router();

// Declared once, for every route below. See the module comment.
router.use(verifyAdminKey);

// ---------------------------------------------------------------------------
// Request bodies
// ---------------------------------------------------------------------------

const grantCreditsSchema = z.object({
  // Bounded so a slipped keypress cannot mint a million credits.
  credits: z.number().int().gt(0).max(1_000_000),
  note: z.string().max(200).default(""),
});

const setLimitSchema = z.object({
  // null restores the platform default rather than setting zero — "no override"
  // and "no credits" are very different instructions.
  daily_credit_limit: z.number().int().min(0).max(10_000_000).nullable().default(null),
});

const setActiveSchema = z.object({
  is_active: z.boolean(),
});

/**
 * Every field optional, and absent means "leave it alone".
 *
 * Resetting one field back to the code default is a separate, explicit list
 * (`reset`) precisely because absence already means something here.
 */
const templateUpdateSchema = z.object({
  enabled: z.boolean().nullable().optional(),
  name: z.string().max(80).nullable().optional(),
  tagline: z.string().max(200).nullable().optional(),
  description: z.string().max(1000).nullable().optional(),
  scope_label: z.string().max(200).nullable().optional(),
  decline_message: z.string().max(600).nullable().optional(),
  near_match_suffix: z.string().max(600).nullable().optional(),
  strong_threshold: z.number().min(0).max(1).nullable().optional(),
  near_threshold: z.number().min(0).max(1).nullable().optional(),
  reset: z.array(z.string()).default([]),
});

const daysSchema = z.object({ days: z.coerce.number().int().default(30) });

function clampDays(days: number): number {
  return Math.max(1, Math.min(days, 365));
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

/** Headline platform numbers: accounts, plans, revenue, usage, referrals. */
router.get(
  "/overview",
  asyncHandler(async (req, res) => {
    const { days } = parseQuery(daysSchema, req.query);
    res.json(await queries.overview(clampDays(days)));
  }),
);

/**
 * What the panel needs to explain a number, rather than just show it.
 *
 * "Grounded rewording is on for 12 bots" means something different when no
 * model is reachable, so the model's actual state belongs on the same screen.
 */
router.get(
  "/health",
  asyncHandler(async (_req, res) => {
    const available = await llm.available();
    res.json({
      llm_available: available,
      llm_model: available ? llm.modelName() : null,
      plans: listPlans(),
      referral_rewards: {
        referrer_signup_credits: referrals.REFERRER_SIGNUP_CREDITS,
        referred_signup_credits: referrals.REFERRED_SIGNUP_CREDITS,
        topup_credits: referrals.TOPUP_CREDITS,
      },
    });
  }),
);

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

const usersQuerySchema = z.object({
  q: z.string().default(""),
  status_filter: z.string().default(""),
  days: z.coerce.number().int().default(30),
  sort: z.string().default("created"),
  limit: z.coerce.number().int().default(queries.DEFAULT_PAGE),
  offset: z.coerce.number().int().default(0),
});

/**
 * Every customer, with plan, usage, bot and referral standing.
 *
 * `status_filter` takes a subscription status, or `entitled` (can use the
 * product now), `paying` (on a paid plan), or `disabled`.
 */
router.get(
  "/users",
  asyncHandler(async (req, res) => {
    const query = parseQuery(usersQuerySchema, req.query);
    res.json(
      await queries.listUsers({
        query: query.q,
        status: query.status_filter,
        days: clampDays(query.days),
        sort: query.sort,
        limit: query.limit,
        offset: Math.max(0, query.offset),
      }),
    );
  }),
);

router.get(
  "/users/:customerId",
  asyncHandler(async (req, res) => {
    const { days } = parseQuery(daysSchema, req.query);
    const detail = await queries.userDetail(req.params.customerId, clampDays(days));
    if (detail === null) throw notFound("No such customer.");
    res.json(detail);
  }),
);

/** The billing customer and its API-key account, or 404 / 409. */
async function accountFor(customerId: string): Promise<{ customer: Doc; user: Doc }> {
  const customer = await billingDb.getCustomerById(customerId);
  if (customer === null) throw notFound("No such customer.");

  const user = await getUserByEmail(customer.email);
  if (user === null) {
    throw conflict(
      "That customer has no API account yet — it is created when " +
        "their plan first grants an allowance.",
    );
  }
  return { customer, user };
}

/**
 * Add bonus credits to an account — goodwill, an outage, a support call.
 *
 * They land in the same balance referral rewards use: permanent, spent only
 * after the day's allowance runs out.
 */
router.post(
  "/users/:customerId/credits",
  asyncHandler(async (req, res) => {
    const body = parseBody(grantCreditsSchema, req.body);
    const customerId = req.params.customerId;
    const { customer, user } = await accountFor(customerId);

    const grant = await grantBonusCredits(user.id, body.credits, "admin_grant", body.note);
    logger.info(
      `Admin granted ${body.credits} credits to customer_id=${customerId} ` +
        `(${body.note || "no note"}).`,
    );

    res.json({
      granted: grant,
      grants: await listCreditGrants(user.id),
      customer_email: customer.email,
    });
  }),
);

/**
 * Override an account's daily allowance, or clear the override.
 *
 * Note this is not sticky against billing: the next plan change writes the
 * plan's allowance over it. It is for a temporary raise, not a permanent plan
 * of its own.
 */
router.post(
  "/users/:customerId/limit",
  asyncHandler(async (req, res) => {
    const body = parseBody(setLimitSchema, req.body);
    const customerId = req.params.customerId;
    const { customer } = await accountFor(customerId);

    const updated = await setDailyCreditLimit(
      customer.email,
      body.daily_credit_limit,
      customer.name,
    );
    logger.info(
      `Admin set daily_credit_limit=${body.daily_credit_limit} for customer_id=${customerId}.`,
    );

    res.json({ user: updated });
  }),
);

/**
 * Enable or disable an account.
 *
 * Both halves are flipped together — the billing customer (which gates signing
 * in) and the API account (which gates the keys). Disabling only one leaves
 * someone who cannot log in but whose bot is still answering, which is the
 * worst of both.
 */
router.post(
  "/users/:customerId/active",
  asyncHandler(async (req, res) => {
    const body = parseBody(setActiveSchema, req.body);
    const customerId = req.params.customerId;
    const { customer, user } = await accountFor(customerId);

    await queries.setAccountActive(customerId, user.id, body.is_active);

    logger.warn(
      `Admin set is_active=${body.is_active} for customer_id=${customerId} (${customer.email}).`,
    );
    res.json({ customer_id: customerId, is_active: body.is_active });
  }),
);

/** Revoke any API key on the platform. Takes effect on the next request. */
router.delete(
  "/keys/:keyId",
  asyncHandler(async (req, res) => {
    const keyId = req.params.keyId;
    if (!(await revokeKey(keyId))) throw notFound("No such key.");
    logger.warn(`Admin revoked api_key id=${keyId}.`);
    res.json({ status: "revoked", key_id: keyId });
  }),
);

// ---------------------------------------------------------------------------
// Subscriptions, usage, audit, AI
// ---------------------------------------------------------------------------

/** Plan counts, MRR, revenue by month, churn and trial conversion. */
router.get(
  "/subscriptions",
  asyncHandler(async (req, res) => {
    const { days } = parseQuery(z.object({ days: z.coerce.number().int().default(90) }), req.query);
    res.json(await queries.subscriptions(clampDays(days)));
  }),
);

/**
 * Client-side crashes, grouped — the Crashlytics-shaped view.
 *
 * Crashlytics has no Web SDK, so this is where browser crashes are read.
 * Grouped by fingerprint and ranked by frequency, because a raw feed of a
 * thousand reports is one bug a thousand times and nobody acts on it.
 *
 * `affected_users` counts distinct accounts rather than reports: one person
 * reloading a broken page twenty times is one person with a problem, not twenty.
 */
router.get(
  "/crashes",
  asyncHandler(async (req, res) => {
    const query = parseQuery(
      z.object({
        days: z.coerce.number().int().default(7),
        limit: z.coerce.number().int().default(50),
      }),
      req.query,
    );
    const days = clampDays(query.days);
    res.json({
      groups: await telemetryStore.errorGroups(days, query.limit),
      window_days: days,
    });
  }),
);

/**
 * The raw feed, newest first, with full stacks.
 *
 * What you open after `/crashes` tells you which group to care about. `release`
 * narrows it to one build, which is the first thing worth checking when a crash
 * rate jumps after a deploy.
 */
router.get(
  "/crashes/recent",
  asyncHandler(async (req, res) => {
    const query = parseQuery(
      z.object({
        limit: z.coerce.number().int().default(100),
        release: z.string().default(""),
      }),
      req.query,
    );
    res.json({ errors: await telemetryStore.recentErrors(query.limit, query.release.trim()) });
  }),
);

/** Credits and requests over time, by account and by endpoint. */
router.get(
  "/usage",
  asyncHandler(async (req, res) => {
    const { days } = parseQuery(daysSchema, req.query);
    res.json(await queries.usage(clampDays(days)));
  }),
);

/**
 * The API request trail, plus the inputs the injection detector flagged.
 *
 * Filter by `role=owner` to see only what the unmetered cross-tenant keys have
 * been doing.
 */
router.get(
  "/audit",
  asyncHandler(async (req, res) => {
    const query = parseQuery(
      z.object({
        days: z.coerce.number().int().default(7),
        email: z.string().default(""),
        endpoint: z.string().default(""),
        role: z.string().default(""),
        limit: z.coerce.number().int().default(200),
        offset: z.coerce.number().int().default(0),
      }),
      req.query,
    );

    res.json(
      await queries.audit({
        days: clampDays(query.days),
        email: query.email,
        endpoint: query.endpoint,
        role: query.role,
        limit: query.limit,
        offset: Math.max(0, query.offset),
      }),
    );
  }),
);

/** AI usage split by the plan the account is on, plus retrieval quality. */
router.get(
  "/ai-usage",
  asyncHandler(async (req, res) => {
    const { days } = parseQuery(daysSchema, req.query);
    res.json(await queries.aiUsage(clampDays(days)));
  }),
);

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

/**
 * The catalogue with its live values, its code defaults, and adoption counts.
 *
 * Adoption is on the same payload because the number that matters when editing
 * a template is how many live bots the edit will reach.
 */
router.get(
  "/templates",
  asyncHandler(async (_req, res) => {
    // The catalogue, its code defaults and the adoption counts all come from
    // the bot service — it owns both the overrides and the bots that use them,
    // so it is the only place the two can be joined without a second opinion.
    res.json(await botService.adminTemplates());
  }),
);

/**
 * Edit one template. Named fields are set; fields in `reset` go back to code.
 *
 * Edits reach every bot on the template immediately, including bots already
 * running — which is the point, since this exists so a wrong decline message
 * can be fixed without a deploy.
 */
router.patch(
  "/templates/:templateId",
  asyncHandler(async (req, res) => {
    const body = parseBody(templateUpdateSchema, req.body);
    const templateId = req.params.templateId;

    // Forwarded whole. The validation that matters — which fields are
    // editable, and that a near threshold can never end up above a strong one
    // — lives with the catalogue in the bot service, and duplicating it here
    // would be two rules to keep in step instead of one.
    const updated = await botService.updateTemplate(templateId, body);

    const named = Object.entries(body)
      .filter(([field, value]) => field !== "reset" && value !== null && value !== undefined)
      .map(([field]) => field);
    logger.info(
      `Admin updated template ${templateId}: ` +
        `${named.length > 0 ? named.sort().join(", ") : "flags only"}`,
    );
    res.json(updated);
  }),
);

/** Discard every edit to this template and restore the code default. */
router.post(
  "/templates/:templateId/reset",
  asyncHandler(async (req, res) => {
    res.json(await botService.resetTemplate(req.params.templateId));
  }),
);

// ---------------------------------------------------------------------------
// Referrals
// ---------------------------------------------------------------------------

/** Programme totals, the leaderboard, and the recent payout trail. */
router.get(
  "/referrals",
  asyncHandler(async (_req, res) => {
    res.json(await referrals.platformStats());
  }),
);
