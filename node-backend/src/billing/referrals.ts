/**
 * The referral programme: who brought whom, and what that is worth.
 *
 * Port of `backend/billing/referrals.py`.
 *
 * Three payouts, and they are deliberately not the same shape:
 *
 * * **signup** — the moment a referred person creates an account, the referrer
 *   gets `REFERRAL_REFERRER_CREDITS` and the newcomer gets
 *   `REFERRAL_REFERRED_CREDITS` on top of whatever their plan gives them. Paid
 *   once per referral, per side.
 * * **top-up** — every time that person actually pays for something, the
 *   referrer gets `REFERRAL_TOPUP_CREDITS`. Paid once *per invoice*, which is
 *   what makes it safe to call from a webhook that retries.
 *
 * Rewards land in the bonus balance (`credit_grants`), not in the daily
 * allowance. A referral credit that expired at midnight IST would be worth
 * almost nothing to someone who earned it in the evening, and "you have 100
 * credits" has to mean 100 credits.
 *
 * Nothing here trusts a code it was handed. {@link attach} refuses
 * self-referral, refuses a second referrer for someone who already has one, and
 * refuses codes that resolve to nobody — the caller passes user input straight
 * in, so this module is where that stops being user input.
 */

import crypto from "node:crypto";

import {
  getUserByEmail,
  grantBonusCredits,
  setDailyCreditLimit,
} from "../shared/apiKeys.js";
import { logger } from "../shared/logger.js";
import {
  coll,
  document,
  documents,
  isDuplicateKeyError,
  objectId,
  toObjectId,
  type Doc,
} from "../shared/mongo.js";
import { nowISO } from "../shared/time.js";
import * as db from "./db.js";

function envInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

// What each event pays, in credits.
export const REFERRER_SIGNUP_CREDITS = envInt("REFERRAL_REFERRER_CREDITS", 100);
export const REFERRED_SIGNUP_CREDITS = envInt("REFERRAL_REFERRED_CREDITS", 50);
export const TOPUP_CREDITS = envInt("REFERRAL_TOPUP_CREDITS", 100);

export const ROLE_REFERRER = "referrer";
export const ROLE_REFERRED = "referred";

export const KIND_SIGNUP = "signup";
export const KIND_TOPUP = "topup";

export const SOURCE_CODE = "code";
export const SOURCE_INVITE = "invite";

// Unambiguous in a shared link and when read aloud: no O/0, no I/1.
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const CODE_LENGTH = 8;
const CODE_RE = /^[A-Z0-9-]{4,32}$/;

// The collections live in `db.ts` alongside the rest of the billing data, and
// their indexes — the ones that make the payouts idempotent — are declared
// there too.

// ---------------------------------------------------------------------------
// Codes
// ---------------------------------------------------------------------------

/** Fold user input to the stored form, or "" when it could not be one. */
export function normaliseCode(code: string | null | undefined): string {
  if (!code) return "";
  const candidate = code.trim().toUpperCase().replace(/ /g, "");
  return CODE_RE.test(candidate) ? candidate : "";
}

/** One character from the code alphabet, chosen without modulo bias. */
function randomCodeChar(): string {
  // `randomInt` rejects and redraws rather than taking a remainder, which is
  // what keeps every character equally likely.
  return CODE_ALPHABET[crypto.randomInt(0, CODE_ALPHABET.length)];
}

/**
 * This customer's referral code, minted on first use.
 *
 * Generated rather than derived from their email: a code is a public string
 * that gets pasted into chats and posted on forums, and deriving it from an
 * address would put that address in every one of those places.
 */
export async function codeForCustomer(customerId: string): Promise<string> {
  const oid = toObjectId(customerId);
  const codes = await coll(db.REFERRAL_CODES);

  const existing = await codes.findOne({ customer_id: oid });
  if (existing !== null) return existing.code;

  for (let attempt = 0; attempt < 10; attempt += 1) {
    const code = Array.from({ length: CODE_LENGTH }, randomCodeChar).join("");
    try {
      await codes.insertOne({ customer_id: oid, code, created_at: nowISO() });
      return code;
    } catch (error) {
      if (!isDuplicateKeyError(error)) throw error;
      // Either the code collided — vanishingly rare — or another request minted
      // this customer's code first. Re-reading covers both.
      const raced = await codes.findOne({ customer_id: oid });
      if (raced !== null) return raced.code;
    }
  }

  throw new Error("Could not allocate a referral code.");
}

/** Resolve a referral code to the customer who owns it. */
export async function customerForCode(code: string): Promise<Doc | null> {
  const normalised = normaliseCode(code);
  if (!normalised) return null;

  const codes = await coll(db.REFERRAL_CODES);
  const row = await codes.findOne({ code: normalised });
  return row ? db.getCustomerById(row.customer_id) : null;
}

// ---------------------------------------------------------------------------
// Invites
// ---------------------------------------------------------------------------

/**
 * Record that this customer invited *email*.
 *
 * An invite is the second way a referral can be attributed: someone who signs
 * up with an invited address is credited to the inviter even though they never
 * clicked a link with a code in it — which is what "they signed up with that
 * email" means in practice.
 */
export async function invite(referrerCustomerId: string, email: string): Promise<Doc> {
  const address = email.toLowerCase().trim();
  const oid = toObjectId(referrerCustomerId);
  const invites = await coll(db.REFERRAL_INVITES);

  // Upsert rather than insert: inviting the same person twice is a person being
  // thorough, not an error, and it must not reset their claimed status.
  await invites.updateOne(
    { referrer_customer_id: oid, email: address },
    {
      $setOnInsert: {
        referrer_customer_id: oid,
        email: address,
        created_at: nowISO(),
        claimed_at: null,
      },
    },
    { upsert: true },
  );

  return document(await invites.findOne({ referrer_customer_id: oid, email: address }))!;
}

export async function listInvites(referrerCustomerId: string): Promise<Doc[]> {
  const invites = await coll(db.REFERRAL_INVITES);
  const rows = await invites
    .find({ referrer_customer_id: objectId(referrerCustomerId) })
    .sort({ _id: -1 })
    .toArray();
  return documents(rows);
}

/** The oldest unclaimed invite for *email*, if anyone invited them. */
async function inviteForEmail(email: string): Promise<Doc | null> {
  const invites = await coll(db.REFERRAL_INVITES);
  return document(
    await invites.findOne(
      { email: email.toLowerCase().trim(), claimed_at: null },
      { sort: { _id: 1 } },
    ),
  );
}

// ---------------------------------------------------------------------------
// Attribution
// ---------------------------------------------------------------------------

export async function getReferralFor(referredCustomerId: string): Promise<Doc | null> {
  const oid = objectId(referredCustomerId);
  if (oid === null) return null;
  const referrals = await coll(db.REFERRALS);
  return document(await referrals.findOne({ referred_customer_id: oid }));
}

/**
 * Attribute a brand-new account to a referrer and pay both sides.
 *
 * Called from the signup paths. Returns the referral row, or `null` when there
 * was nobody to attribute it to — which is the common case and not an error
 * worth failing a signup over.
 *
 * Refusals, all silent by design (a signup must never fail because of a typo'd
 * referral code):
 *
 * * a code nobody owns,
 * * your own code,
 * * an account that already has a referrer.
 */
export async function attach(
  newCustomer: Doc,
  code: string | null = null,
): Promise<Doc | null> {
  if ((await getReferralFor(newCustomer.id)) !== null) return null;

  let referrer = code ? await customerForCode(code) : null;
  let source = SOURCE_CODE;
  let claimedInvite: Doc | null = null;

  if (referrer === null) {
    claimedInvite = await inviteForEmail(newCustomer.email);
    if (claimedInvite !== null) {
      referrer = await db.getCustomerById(claimedInvite.referrer_customer_id);
      source = SOURCE_INVITE;
    }
  }

  if (referrer === null || referrer.id === newCustomer.id) return null;

  const record = {
    referrer_customer_id: toObjectId(referrer.id),
    referred_customer_id: toObjectId(newCustomer.id),
    code: normaliseCode(code),
    source,
    created_at: nowISO(),
  };

  const referrals = await coll(db.REFERRALS);
  let insertedId;
  try {
    const result = await referrals.insertOne({ ...record });
    insertedId = result.insertedId;
  } catch (error) {
    if (isDuplicateKeyError(error)) {
      // Someone already claimed this account between the check above and here.
      // One referrer per person, and the first one won.
      return null;
    }
    throw error;
  }

  if (claimedInvite !== null) {
    const invites = await coll(db.REFERRAL_INVITES);
    await invites.updateOne(
      { _id: toObjectId(claimedInvite.id) },
      { $set: { claimed_at: nowISO() } },
    );
  }

  const referral = document({ ...record, _id: insertedId })!;

  await pay(referral, referrer, ROLE_REFERRER, KIND_SIGNUP, REFERRER_SIGNUP_CREDITS, {
    note: `referred ${newCustomer.email}`,
  });
  await pay(referral, newCustomer, ROLE_REFERRED, KIND_SIGNUP, REFERRED_SIGNUP_CREDITS, {
    note: `joined via ${referrer.email}`,
  });

  logger.info(
    `Referral recorded: customer_id=${referrer.id} referred ` +
      `customer_id=${newCustomer.id} via ${source}.`,
  );
  return referral;
}

/**
 * Pay the referrer for a payment their referred customer just made.
 *
 * One payout per invoice, enforced by a unique index rather than by this
 * function remembering — payment webhooks are delivered more than once as a
 * matter of course, and "at least once" only becomes "exactly once" if the
 * database says so. Returns the credits actually paid out.
 */
export async function rewardPayment(customerId: string, invoices: Doc[]): Promise<number> {
  const referral = await getReferralFor(customerId);
  if (referral === null || invoices.length === 0) return 0;

  const referrer = await db.getCustomerById(referral.referrer_customer_id);
  if (referrer === null) return 0;

  let paid = 0;
  for (const invoice of invoices) {
    const didPay = await pay(referral, referrer, ROLE_REFERRER, KIND_TOPUP, TOPUP_CREDITS, {
      invoiceId: invoice.id,
      note: `top-up by ${invoice.plan_id ?? "plan"} invoice #${invoice.id}`,
    });
    if (didPay) paid += TOPUP_CREDITS;
  }
  return paid;
}

/**
 * Record one payout and move the credits, or do nothing if already paid.
 *
 * The reward row is written *first*: its unique index is what makes this
 * idempotent, so the insert has to be the thing that fails on a repeat. Were
 * the credits granted first, a retry would hand them out again and only then
 * discover it shouldn't have.
 */
async function pay(
  referral: Doc,
  customer: Doc,
  role: string,
  kind: string,
  credits: number,
  options: { invoiceId?: string | null; note?: string } = {},
): Promise<boolean> {
  if (credits <= 0) return false;

  const rewards = await coll(db.REFERRAL_REWARDS);
  try {
    await rewards.insertOne({
      referral_id: toObjectId(referral.id),
      customer_id: toObjectId(customer.id),
      role,
      kind,
      credits,
      invoice_id: objectId(options.invoiceId ?? null),
      created_at: nowISO(),
    });
  } catch (error) {
    if (isDuplicateKeyError(error)) {
      // The unique index rejected it: this reward has already been paid.
      return false;
    }
    throw error;
  }

  let user = await getUserByEmail(customer.email);
  if (user === null) {
    // The account exists in billing but has no API-key account yet, which means
    // nothing has ever granted it an allowance. Rare, and not worth losing the
    // credits over: create it by granting against the email.
    user = await setDailyCreditLimit(customer.email, null, customer.name ?? "");
  }

  await grantBonusCredits(user.id, credits, `referral_${kind}_${role}`, options.note ?? "");
  return true;
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

/**
 * `jordan@example.com` -> `jo•••@example.com`.
 *
 * The referrer already knows who they invited, so this is not secrecy — it is
 * not putting a third party's full address on a screen that may be shared or
 * screenshotted.
 */
export function maskEmail(email: string): string {
  const at = email.indexOf("@");
  if (at === -1) return email;

  const name = email.slice(0, at);
  const domain = email.slice(at + 1);
  if (!domain) return email;

  const head = name.length > 2 ? name.slice(0, 2) : name.slice(0, 1);
  return `${head}•••@${domain}`;
}

/** Everything the customer's referral card shows, in one query set. */
export async function summaryFor(customerId: string): Promise<Doc> {
  const oid = toObjectId(customerId);

  // Fetched and assembled here rather than as one pipeline. A referrer has tens
  // of referrals, not millions, and three indexed reads that anyone can follow
  // beat a $lookup chain that only runs on a replica set.
  const referralsColl = await coll(db.REFERRALS);
  const referralsOut = await referralsColl
    .find({ referrer_customer_id: oid })
    .sort({ _id: -1 })
    .toArray();
  const referralIds = referralsOut.map((r) => r._id);

  const customersColl = await coll(db.CUSTOMERS);
  const peopleRows = await customersColl
    .find({ _id: { $in: referralsOut.map((r) => r.referred_customer_id) } })
    .toArray();
  const people = new Map(peopleRows.map((c) => [c._id.toHexString(), c]));

  const rewardsColl = await coll(db.REFERRAL_REWARDS);
  const rewardRows = await rewardsColl.find({ referral_id: { $in: referralIds } }).toArray();

  const earnedByReferral = new Map<string, number>();
  const topupsByReferral = new Map<string, number>();
  for (const reward of rewardRows) {
    const key = reward.referral_id.toHexString();
    if (reward.role === ROLE_REFERRER) {
      earnedByReferral.set(key, (earnedByReferral.get(key) ?? 0) + reward.credits);
    }
    if (reward.kind === KIND_TOPUP) {
      topupsByReferral.set(key, (topupsByReferral.get(key) ?? 0) + 1);
    }
  }

  const referred = referralsOut.map((r) => {
    const person: Doc = people.get(r.referred_customer_id.toHexString()) ?? {};
    const key = r._id.toHexString();
    return {
      email: person.email ?? "",
      name: person.name ?? "",
      joined_at: r.created_at,
      source: r.source,
      credits_earned: earnedByReferral.get(key) ?? 0,
      payments_rewarded: topupsByReferral.get(key) ?? 0,
    };
  });

  const ownRewards = await rewardsColl
    .find({ customer_id: oid, role: ROLE_REFERRER })
    .toArray();
  const earned = ownRewards.reduce((total, reward) => total + reward.credits, 0);

  const joinedVia = await getReferralFor(customerId);
  const inviter = joinedVia ? await db.getCustomerById(joinedVia.referrer_customer_id) : null;

  const invites = await listInvites(customerId);

  return {
    code: await codeForCustomer(customerId),
    credits_earned: Math.trunc(earned),
    referrer_signup_credits: REFERRER_SIGNUP_CREDITS,
    referred_signup_credits: REFERRED_SIGNUP_CREDITS,
    topup_credits: TOPUP_CREDITS,
    referred: referred.map((row) => ({ ...row, email: maskEmail(row.email) })),
    invites: invites.map((inv) => ({
      email: inv.email,
      created_at: inv.created_at,
      claimed_at: inv.claimed_at,
    })),
    joined_via: inviter ? maskEmail(inviter.email) : null,
  };
}

/** Programme-wide totals for the admin panel. */
export async function platformStats(): Promise<Doc> {
  const rewardsColl = await coll(db.REFERRAL_REWARDS);
  const referralsColl = await coll(db.REFERRALS);
  const invitesColl = await coll(db.REFERRAL_INVITES);
  const customersColl = await coll(db.CUSTOMERS);

  async function sumCredits(match: Record<string, unknown>): Promise<number> {
    const rows = await rewardsColl
      .aggregate([{ $match: match }, { $group: { _id: null, n: { $sum: "$credits" } } }])
      .toArray();
    return rows.length > 0 ? Math.trunc(rows[0].n) : 0;
  }

  const totals = {
    referrals: await referralsColl.countDocuments({}),
    invites_sent: await invitesColl.countDocuments({}),
    invites_claimed: await invitesColl.countDocuments({ claimed_at: { $ne: null } }),
    credits_paid: await sumCredits({}),
    credits_paid_on_topups: await sumCredits({ kind: KIND_TOPUP }),
    rewarded_payments: await rewardsColl.countDocuments({ kind: KIND_TOPUP }),
  };

  // Same reasoning as summaryFor: two reads and a map, rather than a pipeline
  // that not every deployment can run and a reviewer cannot check.
  const referredCounts = new Map<string, number>();
  const referralRows = await referralsColl
    .find({}, { projection: { referrer_customer_id: 1 } })
    .toArray();
  for (const referral of referralRows) {
    const key = referral.referrer_customer_id.toHexString();
    referredCounts.set(key, (referredCounts.get(key) ?? 0) + 1);
  }

  const earnedByCustomer = new Map<string, number>();
  const referrerRewards = await rewardsColl.find({ role: ROLE_REFERRER }).toArray();
  for (const reward of referrerRewards) {
    const key = reward.customer_id.toHexString();
    earnedByCustomer.set(key, (earnedByCustomer.get(key) ?? 0) + reward.credits);
  }

  const leaderRows = await customersColl
    .find({ _id: { $in: [...referredCounts.keys()].map((id) => toObjectId(id)) } })
    .toArray();
  const people = new Map(leaderRows.map((c) => [c._id.toHexString(), c]));

  const leaders = [...referredCounts.entries()]
    .map(([customerId, count]) => ({
      customer_id: customerId,
      email: people.get(customerId)?.email ?? "",
      name: people.get(customerId)?.name ?? "",
      referred_count: count,
      credits_earned: earnedByCustomer.get(customerId) ?? 0,
    }))
    .sort((a, b) => {
      if (a.referred_count !== b.referred_count) return b.referred_count - a.referred_count;
      return b.credits_earned - a.credits_earned;
    })
    .slice(0, 25);

  const recentRewards = await rewardsColl.find().sort({ _id: -1 }).limit(50).toArray();
  const rewardPeopleRows = await customersColl
    .find({ _id: { $in: recentRewards.map((r) => r.customer_id) } })
    .toArray();
  const rewardPeople = new Map(rewardPeopleRows.map((c) => [c._id.toHexString(), c]));

  const recent = recentRewards.map((reward) => ({
    id: reward._id.toHexString(),
    kind: reward.kind,
    role: reward.role,
    credits: reward.credits,
    created_at: reward.created_at,
    invoice_id: reward.invoice_id ? reward.invoice_id.toHexString() : null,
    email: rewardPeople.get(reward.customer_id.toHexString())?.email ?? "",
  }));

  return {
    totals,
    rewards: {
      referrer_signup_credits: REFERRER_SIGNUP_CREDITS,
      referred_signup_credits: REFERRED_SIGNUP_CREDITS,
      topup_credits: TOPUP_CREDITS,
    },
    leaderboard: leaders,
    recent_rewards: recent,
  };
}
