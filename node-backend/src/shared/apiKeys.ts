/**
 * API key management and the credit system.
 *
 * Port of `backend/shared/api_keys.py`.
 *
 * Keys are stored as SHA-256 hashes (like passwords) — if the database leaks,
 * raw keys cannot be recovered. Credits reset at midnight IST daily. Credit
 * costs are variable based on query length (see `config.CREDIT_COST_TIERS`).
 *
 * Accounts vs. keys
 * ------------------
 * Credits are pooled per **user** (identified by `owner_email`), not per key. A
 * user can hold multiple API keys (e.g. one per app/environment) but they all
 * draw from the same daily credit pool — creating extra keys does not grant
 * extra credits.
 *
 * Two pools, spent in this order
 * ------------------------------
 * 1. the **daily allowance** (`daily_credit_limit`), which resets at midnight IST
 * 2. the **bonus balance** (`credit_grants`), which does not reset and does not
 *    expire — referral rewards and manual admin grants land here.
 *
 * Allowance first is deliberate: it is the pool that disappears overnight, so
 * spending the permanent one ahead of it would burn earned credits for nothing.
 *
 * Two roles exist:
 * - `user`  — normal customer account, subject to `daily_credit_limit`.
 * - `owner` — unlimited, no credit checks or deductions at all. Minted only via
 *   `npm run create-owner-key` (never through the public API), for use by the
 *   product owners themselves.
 */

import crypto from "node:crypto";

import { DAILY_CREDIT_LIMIT } from "../config.js";
import {
  atomic,
  coll,
  document,
  documents,
  objectId,
  registerIndexes,
  toObjectId,
  type ClientSession,
  type Doc,
} from "./mongo.js";
import { daysAgoIST, nextResetTime, nowISO, todayIST } from "./time.js";

export const KEY_PREFIX = "nxk_"; // Nexora AI Key
export const OWNER_KEY_PREFIX = "nxo_"; // Nexora AI Owner key — visually distinct in logs

export const ROLE_USER = "user";
export const ROLE_OWNER = "owner";

export const USERS = "users";
export const API_KEYS = "api_keys";
export const DAILY_USAGE = "daily_usage";
export const REQUEST_LOG = "request_log";
export const CREDIT_GRANTS = "credit_grants";

registerIndexes(USERS, [[{ email: 1 }, { unique: true, name: "uniq_email" }]]);

registerIndexes(API_KEYS, [
  // The hash is what a request is validated against, on every single call.
  [{ key_hash: 1 }, { unique: true, name: "uniq_key_hash" }],
  [{ user_id: 1 }, { name: "by_user" }],
]);

registerIndexes(DAILY_USAGE, [
  // One counter per account per day. Unique because the spend path upserts into
  // it concurrently, and two rows for one day would silently double an
  // account's allowance.
  [{ user_id: 1, usage_date: 1 }, { unique: true, name: "uniq_user_day" }],
]);

registerIndexes(REQUEST_LOG, [
  [{ user_id: 1, timestamp: -1 }, { name: "by_user_time" }],
  [{ timestamp: -1 }, { name: "by_time" }],
]);

registerIndexes(CREDIT_GRANTS, [[{ user_id: 1, _id: 1 }, { name: "by_user" }]]);

/** The shape `validateApiKey` returns and the auth middleware passes around. */
export interface KeyRecord {
  id: string;
  key_prefix: string;
  user_id: string;
  key_is_active: number;
  owner_email: string;
  owner_name: string;
  role: string;
  daily_credit_limit: number | null;
  user_is_active: number;
}

// ---------------------------------------------------------------------------
// Key hashing
// ---------------------------------------------------------------------------

/** SHA-256 hash of the raw API key. */
function hashKey(rawKey: string): string {
  return crypto.createHash("sha256").update(rawKey, "utf8").digest("hex");
}

/** Python's `secrets.token_hex(24)` — 24 random bytes as 48 hex characters. */
function tokenHex(bytes: number): string {
  return crypto.randomBytes(bytes).toString("hex");
}

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

/**
 * Look up a user by email, creating them if they don't exist.
 *
 * Repeat calls with the same email reuse the same account (and therefore the
 * same credit pool) instead of creating a new one — this is what keeps credits
 * scoped per-account rather than per-key.
 *
 * Written as one upsert rather than a read-then-insert so that two requests
 * arriving together cannot both decide the account is missing. The unique index
 * on `email` is what makes that guarantee real.
 */
async function getOrCreateUser(
  email: string,
  name = "",
  role: string = ROLE_USER,
  dailyCreditLimit: number | null = null,
): Promise<Doc> {
  const address = email.toLowerCase().trim();
  const users = await coll(USERS);
  const doc = await users.findOneAndUpdate(
    { email: address },
    {
      $setOnInsert: {
        email: address,
        name,
        role,
        daily_credit_limit: dailyCreditLimit,
        created_at: nowISO(),
        is_active: 1,
      },
    },
    { upsert: true, returnDocument: "after" },
  );
  return document(doc)!;
}

// ---------------------------------------------------------------------------
// Key generation
// ---------------------------------------------------------------------------

export interface IssuedKey {
  api_key: string;
  key_id: string;
  key_prefix: string;
  owner_email: string;
  owner_name: string;
  role?: string;
}

async function issueKey(user: Doc, rawKey: string, label: string): Promise<IssuedKey> {
  const keys = await coll(API_KEYS);
  const result = await keys.insertOne({
    user_id: toObjectId(user.id),
    key_hash: hashKey(rawKey),
    key_prefix: rawKey.slice(0, 12),
    label,
    created_at: nowISO(),
    is_active: 1,
  });

  return {
    api_key: rawKey,
    key_id: result.insertedId.toHexString(),
    key_prefix: rawKey.slice(0, 12),
    owner_email: user.email,
    owner_name: user.name,
  };
}

/**
 * Generate a new API key for a customer account.
 *
 * If `ownerEmail` already has an account, the new key is attached to that same
 * account and shares its existing credit pool.
 */
export async function generateApiKey(
  ownerEmail: string,
  ownerName = "",
  label = "",
): Promise<IssuedKey> {
  const user = await getOrCreateUser(ownerEmail, ownerName, ROLE_USER);
  return issueKey(user, KEY_PREFIX + tokenHex(24), label);
}

/**
 * Mint an unlimited **owner** key.
 *
 * Deliberately NOT exposed via any HTTP endpoint — call this only from
 * `src/scripts/createOwnerKey.ts`, run locally by a project owner. Owner keys
 * skip credit checks and deductions entirely (see `shared/auth.ts`).
 */
export async function createOwnerKey(ownerEmail: string, ownerName = ""): Promise<IssuedKey> {
  const user = await getOrCreateUser(ownerEmail, ownerName, ROLE_OWNER, null);

  // An account that existed as a customer is promoted rather than duplicated:
  // the email is the identity, and two accounts for one person would split
  // their credit pool in half.
  const users = await coll(USERS);
  await users.updateOne({ _id: toObjectId(user.id) }, { $set: { role: ROLE_OWNER } });

  const issued = await issueKey(user, OWNER_KEY_PREFIX + tokenHex(24), "owner key");
  issued.role = ROLE_OWNER;
  return issued;
}

// ---------------------------------------------------------------------------
// Key validation
// ---------------------------------------------------------------------------

/**
 * Validate a raw API key.
 *
 * Returns a merged key+user record if the key and its owning account are both
 * active, else `null`. The returned object includes `role` and
 * `daily_credit_limit` so callers can branch on owner vs. normal keys.
 */
export async function validateApiKey(rawKey: string): Promise<KeyRecord | null> {
  if (!rawKey || !(rawKey.startsWith(KEY_PREFIX) || rawKey.startsWith(OWNER_KEY_PREFIX))) {
    return null;
  }

  const keys = await coll(API_KEYS);
  const key = await keys.findOne({ key_hash: hashKey(rawKey) });
  if (key === null || !key.is_active) return null;

  const users = await coll(USERS);
  const user = await users.findOne({ _id: key.user_id });
  if (user === null || !user.is_active) return null;

  // The shape the auth middleware and every caller downstream expects. Kept
  // flat and explicit rather than nesting the user, because this record is read
  // on the hot path of every single API request.
  return {
    id: key._id.toHexString(),
    key_prefix: key.key_prefix,
    user_id: user._id.toHexString(),
    key_is_active: key.is_active ?? 0,
    owner_email: user.email,
    owner_name: user.name ?? "",
    role: user.role ?? ROLE_USER,
    daily_credit_limit: user.daily_credit_limit ?? null,
    user_is_active: user.is_active ?? 0,
  };
}

// ---------------------------------------------------------------------------
// Credit management (pooled per user, not per key)
// ---------------------------------------------------------------------------

/** Resolve a user's configured limit, falling back to the global default. */
function effectiveLimit(dailyCreditLimit: number | null | undefined): number {
  return dailyCreditLimit === null || dailyCreditLimit === undefined
    ? DAILY_CREDIT_LIMIT
    : dailyCreditLimit;
}

/** How many credits this account has used today (IST), across all its keys. */
export async function getCreditsUsedToday(userId: unknown): Promise<number> {
  const usage = await coll(DAILY_USAGE);
  const doc = await usage.findOne({ user_id: objectId(userId), usage_date: todayIST() });
  return doc ? Number(doc.credits_used) : 0;
}

/** What is left of *today's* allowance, ignoring any bonus balance. */
export async function getDailyCreditsRemaining(
  userId: unknown,
  dailyCreditLimit: number | null = null,
): Promise<number> {
  const used = await getCreditsUsedToday(userId);
  return Math.max(0, effectiveLimit(dailyCreditLimit) - used);
}

/**
 * Unspent bonus credits — referral rewards and manual grants.
 *
 * Bonus credits are a *balance*, not an allowance: they do not reset at
 * midnight and they do not expire. Rewarding someone with credits that vanish
 * at midnight IST would be rewarding them with almost nothing if they happened
 * to earn them at 11pm.
 */
export async function getBonusBalance(userId: unknown): Promise<number> {
  const grants = await coll(CREDIT_GRANTS);
  const result = await grants
    .aggregate([
      { $match: { user_id: objectId(userId) } },
      { $group: { _id: null, total: { $sum: "$remaining" } } },
    ])
    .toArray();
  return result.length > 0 ? Number(result[0].total) : 0;
}

/**
 * Everything this account can still spend right now: today's allowance plus
 * whatever bonus balance it is carrying.
 *
 * This is the number the credit check and the `X-Credits-Remaining` header use,
 * because it is the honest answer to "can this request go through?".
 */
export async function getCreditsRemaining(
  userId: unknown,
  dailyCreditLimit: number | null = null,
): Promise<number> {
  const [daily, bonus] = await Promise.all([
    getDailyCreditsRemaining(userId, dailyCreditLimit),
    getBonusBalance(userId),
  ]);
  return daily + bonus;
}

/**
 * Add *amount* bonus credits to an account and say why.
 *
 * Grants are documents rather than a single balance field so that "where did
 * these credits come from?" has an answer — which referral, which admin, which
 * day. Spending draws them down oldest-first.
 */
export async function grantBonusCredits(
  userId: unknown,
  amount: number,
  reason: string,
  note = "",
): Promise<Doc> {
  if (amount <= 0) throw new Error("A credit grant must be positive.");

  const grant = {
    user_id: toObjectId(userId),
    amount,
    remaining: amount,
    reason,
    note,
    created_at: nowISO(),
  };
  const grants = await coll(CREDIT_GRANTS);
  const result = await grants.insertOne({ ...grant });
  return document({ ...grant, _id: result.insertedId })!;
}

/** This account's grant history, newest first. */
export async function listCreditGrants(userId: unknown, limit = 50): Promise<Doc[]> {
  const grants = await coll(CREDIT_GRANTS);
  const rows = await grants
    .find({ user_id: objectId(userId) })
    .sort({ _id: -1 })
    .limit(limit)
    .toArray();
  return documents(rows);
}

/**
 * Draw *amount* down from this account's grants, oldest first.
 *
 * Oldest-first so a grant that came with a story ("your friend subscribed") is
 * the one that gets used, rather than sitting behind a newer one forever.
 * Returns how much was actually taken, which is less than *amount* only when
 * the balance ran out.
 */
async function spendBonus(
  userId: unknown,
  amount: number,
  session?: ClientSession,
): Promise<number> {
  let taken = 0;
  const oid = objectId(userId);
  const grants = await coll(CREDIT_GRANTS);

  const cursor = grants
    .find({ user_id: oid, remaining: { $gt: 0 } }, { session })
    .sort({ _id: 1 });

  for await (const grant of cursor) {
    if (taken >= amount) break;
    const take = Math.min(Number(grant.remaining), amount - taken);
    await grants.updateOne({ _id: grant._id }, { $inc: { remaining: -take } }, { session });
    taken += take;
  }
  return taken;
}

/**
 * Consume *cost* credits against *userId*'s shared pool (charged regardless of
 * which of the user's keys made the request).
 *
 * The daily allowance is spent **first** and the bonus balance only covers what
 * is left over. That ordering matters: allowance expires at midnight and bonus
 * credits do not, so spending bonus first would quietly burn the balance
 * someone earned while their free allowance went unused.
 *
 * The deduction and its audit row go in one transaction where the deployment
 * supports it, so a request can never be charged without being logged.
 *
 * Returns the total remaining credits (allowance + bonus) after the charge.
 */
export async function consumeCredits(
  userId: unknown,
  apiKeyId: unknown,
  cost: number,
  endpoint: string,
  messageLen: number,
  dailyCreditLimit: number | null = null,
): Promise<number> {
  const oid = toObjectId(userId);
  const fromDaily = Math.min(cost, await getDailyCreditsRemaining(userId, dailyCreditLimit));
  const fromBonus = cost - fromDaily;

  await atomic(async (session) => {
    if (fromDaily) {
      const usage = await coll(DAILY_USAGE);
      await usage.updateOne(
        { user_id: oid, usage_date: todayIST() },
        { $inc: { credits_used: fromDaily } },
        { upsert: true, session },
      );
    }
    if (fromBonus) {
      await spendBonus(oid, fromBonus, session);
    }

    const log = await coll(REQUEST_LOG);
    await log.insertOne(
      {
        api_key_id: objectId(apiKeyId),
        user_id: oid,
        endpoint,
        message_len: messageLen,
        credit_cost: cost,
        timestamp: nowISO(),
      },
      { session },
    );
  });

  return getCreditsRemaining(userId, dailyCreditLimit);
}

/**
 * Log a request without touching the credit pool.
 *
 * Used for owner keys, which skip billing entirely. Skipping the *charge* is
 * intended; skipping the audit trail is not — an unlimited key that leaves no
 * record of what it did is exactly the key you most want a record of.
 */
export async function recordRequest(
  userId: unknown,
  apiKeyId: unknown,
  endpoint: string,
  messageLen: number,
  cost = 0,
): Promise<void> {
  const log = await coll(REQUEST_LOG);
  await log.insertOne({
    api_key_id: objectId(apiKeyId),
    user_id: objectId(userId),
    endpoint,
    message_len: messageLen,
    credit_cost: cost,
    timestamp: nowISO(),
  });
}

/** The next midnight IST as an ISO timestamp. */
export function getNextResetTime(): string {
  return nextResetTime();
}

// ---------------------------------------------------------------------------
// Usage stats
// ---------------------------------------------------------------------------

export interface UsageStats {
  credits_remaining: number;
  credits_used_today: number;
  credits_daily_limit: number;
  bonus_credits: number;
  resets_at: string;
  total_queries_all_time: number;
  total_credits_consumed_all_time: number;
  last_7_days: Array<{ usage_date: string; credits_used: number }>;
}

/** Account-level usage statistics, aggregated across all of a user's keys. */
export async function getUsageStats(
  userId: unknown,
  dailyCreditLimit: number | null = null,
): Promise<UsageStats> {
  const oid = objectId(userId);
  const limit = effectiveLimit(dailyCreditLimit);

  const creditsUsedToday = await getCreditsUsedToday(userId);
  const bonusCredits = await getBonusBalance(userId);
  const creditsRemaining = Math.max(0, limit - creditsUsedToday) + bonusCredits;

  const log = await coll(REQUEST_LOG);
  const totals = await log
    .aggregate([
      { $match: { user_id: oid } },
      {
        $group: {
          _id: null,
          total: { $sum: 1 },
          total_credits: { $sum: "$credit_cost" },
        },
      },
    ])
    .toArray();

  const totalQueries = totals.length > 0 ? Number(totals[0].total) : 0;
  const totalCredits = totals.length > 0 ? Number(totals[0].total_credits) : 0;

  const weekAgo = daysAgoIST(7);
  const usage = await coll(DAILY_USAGE);
  const rows = await usage
    .find({ user_id: oid, usage_date: { $gte: weekAgo } })
    .sort({ usage_date: 1 })
    .toArray();

  return {
    credits_remaining: creditsRemaining,
    credits_used_today: creditsUsedToday,
    credits_daily_limit: limit,
    bonus_credits: bonusCredits,
    resets_at: getNextResetTime(),
    total_queries_all_time: totalQueries,
    total_credits_consumed_all_time: totalCredits,
    last_7_days: rows.map((row) => ({
      usage_date: row.usage_date,
      credits_used: row.credits_used,
    })),
  };
}

// ---------------------------------------------------------------------------
// Admin operations
// ---------------------------------------------------------------------------

/** Every API key with owner/role info (for admin). Never exposes the hash. */
export async function listAllKeys(): Promise<Doc[]> {
  const usersColl = await coll(USERS);
  const userRows = await usersColl.find().toArray();
  const usersById = new Map(userRows.map((u) => [u._id.toHexString(), u]));

  const keysColl = await coll(API_KEYS);
  const keyRows = await keysColl.find().sort({ _id: 1 }).toArray();

  const result: Doc[] = [];
  for (const key of keyRows) {
    const user: Doc = usersById.get(key.user_id?.toHexString?.() ?? "") ?? {};
    result.push({
      id: key._id.toHexString(),
      key_prefix: key.key_prefix,
      label: key.label ?? "",
      created_at: key.created_at,
      is_active: key.is_active ?? 0,
      user_id: key.user_id?.toHexString?.() ?? "",
      owner_email: user.email ?? "",
      owner_name: user.name ?? "",
      role: user.role ?? ROLE_USER,
      daily_credit_limit: user.daily_credit_limit ?? null,
      credits_remaining: key.is_active
        ? await getCreditsRemaining(key.user_id, user.daily_credit_limit ?? null)
        : 0,
    });
  }
  return result;
}

/** Deactivate an API key. Returns true if the key existed. */
export async function revokeKey(keyId: unknown): Promise<boolean> {
  const oid = objectId(keyId);
  if (oid === null) return false;
  const keys = await coll(API_KEYS);
  const result = await keys.updateOne({ _id: oid }, { $set: { is_active: 0 } });
  return result.matchedCount > 0;
}

// ---------------------------------------------------------------------------
// Account-scoped operations (used by the self-serve billing dashboard)
// ---------------------------------------------------------------------------

/** The account for *email*, or null if it doesn't exist yet. */
export async function getUserByEmail(email: string): Promise<Doc | null> {
  const users = await coll(USERS);
  return document(await users.findOne({ email: email.toLowerCase().trim() }));
}

/**
 * Set an account's daily credit allowance, creating the account if needed.
 *
 * This is how a subscription tier becomes an entitlement: activate a plan,
 * write its `daily_credits` here. `null` restores the global default.
 */
export async function setDailyCreditLimit(
  email: string,
  limit: number | null,
  name = "",
): Promise<Doc> {
  const user = await getOrCreateUser(email, name, ROLE_USER);
  const users = await coll(USERS);
  const updated = await users.findOneAndUpdate(
    { _id: toObjectId(user.id) },
    { $set: { daily_credit_limit: limit } },
    { returnDocument: "after" },
  );
  return document(updated)!;
}

/** The API keys belonging to one account (never exposes the hash). */
export async function listKeysForEmail(email: string): Promise<Doc[]> {
  const users = await coll(USERS);
  const user = await users.findOne({ email: email.toLowerCase().trim() });
  if (user === null) return [];

  const keys = await coll(API_KEYS);
  const rows = await keys.find({ user_id: user._id }).sort({ _id: -1 }).toArray();

  return rows.map((key) => ({
    id: key._id.toHexString(),
    key_prefix: key.key_prefix,
    label: key.label ?? "",
    created_at: key.created_at,
    is_active: key.is_active ?? 0,
  }));
}

/**
 * Revoke a key **only if** it belongs to *email*.
 *
 * Scoping the update by owner (rather than checking then updating) is what
 * stops one customer revoking another's key by guessing an id.
 */
export async function revokeKeyForEmail(keyId: unknown, email: string): Promise<boolean> {
  const oid = objectId(keyId);
  const users = await coll(USERS);
  const user = await users.findOne({ email: email.toLowerCase().trim() });
  if (oid === null || user === null) return false;

  const keys = await coll(API_KEYS);
  const result = await keys.updateOne(
    { _id: oid, user_id: user._id },
    { $set: { is_active: 0 } },
  );
  return result.matchedCount > 0;
}

export async function countActiveKeysForEmail(email: string): Promise<number> {
  const users = await coll(USERS);
  const user = await users.findOne({ email: email.toLowerCase().trim() });
  if (user === null) return 0;
  const keys = await coll(API_KEYS);
  return keys.countDocuments({ user_id: user._id, is_active: 1 });
}
