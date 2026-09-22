/**
 * Billing data: customers, sessions, subscriptions, invoices, referrals.
 *
 * Port of `backend/billing/db.py`.
 *
 * Lives in the same MongoDB database as the API-key collections so that
 * granting a paying customer their credit allowance is one round trip rather
 * than a cross-service dance. `shared/apiKeys.ts` still owns `users` and
 * `api_keys`; this module links to them by email.
 *
 * Uniqueness that used to be a `UNIQUE` column is a unique index here, and two
 * of them are load-bearing rather than tidy:
 *
 * * `customers.canonical_email` — one account per *mailbox*, so `you+1@` and
 *   `y.o.u@` cannot open a second one. See `identity.ts`.
 * * `referral_rewards` — a signup bonus pays once per side, and one invoice
 *   pays out once however many times a provider redelivers its webhook.
 */

import {
  coll,
  document,
  documents,
  isDuplicateKeyError,
  objectId,
  registerIndexes,
  toObjectId,
  type Doc,
} from "../shared/mongo.js";
import { addDays, istISO, nowISO, parseISO } from "../shared/time.js";
import type { Plan } from "./plans.js";
import { hashSessionToken } from "./security.js";

export const SESSION_TTL_DAYS = 30;

// Subscription statuses
export const STATUS_TRIALING = "trialing";
export const STATUS_PENDING = "pending";
export const STATUS_ACTIVE = "active";
export const STATUS_CANCELED = "canceled"; // still runs to period end
export const STATUS_EXPIRED = "expired";

export const ENTITLED_STATUSES = [STATUS_TRIALING, STATUS_ACTIVE, STATUS_CANCELED];

export const CUSTOMERS = "customers";
export const SESSIONS = "sessions";
export const SUBSCRIPTIONS = "subscriptions";
export const INVOICES = "invoices";
export const REFERRAL_CODES = "referral_codes";
export const REFERRAL_INVITES = "referral_invites";
export const REFERRALS = "referrals";
export const REFERRAL_REWARDS = "referral_rewards";

/** This mailbox already has an account. */
export class DuplicateAccountError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DuplicateAccountError";
  }
}

// Stored in password_hash for an account that has no password. It cannot parse
// as a hash, so verifyPassword rejects it for every input — there is no string
// a user could type that authenticates against it.
export const NO_PASSWORD = "!google-oauth-no-password";

export const PROVIDER_PASSWORD = "password";
export const PROVIDER_GOOGLE = "google";
// Signed in through Firebase Authentication. The underlying method (Google or
// email+password) is recorded separately in `firebase_provider`, because "how
// Firebase authenticated them" and "that Firebase authenticated them" are
// different questions and only the second one decides how we log them in.
export const PROVIDER_FIREBASE = "firebase";

registerIndexes(CUSTOMERS, [
  [{ email: 1 }, { unique: true, name: "uniq_email" }],
  // The real one-account-per-person rule. Partial so that a document written
  // before the field existed cannot collide with every other such document.
  [
    { canonical_email: 1 },
    {
      unique: true,
      name: "uniq_canonical_email",
      partialFilterExpression: { canonical_email: { $type: "string" } },
    },
  ],
  [
    { google_sub: 1 },
    {
      unique: true,
      name: "uniq_google_sub",
      partialFilterExpression: { google_sub: { $type: "string" } },
    },
  ],
  // Same shape and same reasoning as google_sub: one Firebase account maps to
  // at most one customer, and the partial filter keeps every pre-Firebase
  // document (where the field is absent) out of the uniqueness check.
  [
    { firebase_uid: 1 },
    {
      unique: true,
      name: "uniq_firebase_uid",
      partialFilterExpression: { firebase_uid: { $type: "string" } },
    },
  ],
]);

registerIndexes(SESSIONS, [
  [{ token_hash: 1 }, { unique: true, name: "uniq_token" }],
  [{ customer_id: 1 }, { name: "by_customer" }],
]);

registerIndexes(SUBSCRIPTIONS, [
  [{ customer_id: 1, _id: -1 }, { name: "by_customer" }],
  [{ status: 1, current_period_end: 1 }, { name: "by_status_end" }],
]);

registerIndexes(INVOICES, [
  [{ customer_id: 1, _id: -1 }, { name: "by_customer" }],
  [{ status: 1 }, { name: "by_status" }],
]);

registerIndexes(REFERRAL_CODES, [
  [{ code: 1 }, { unique: true, name: "uniq_code" }],
  [{ customer_id: 1 }, { unique: true, name: "uniq_customer" }],
]);

registerIndexes(REFERRAL_INVITES, [
  [{ referrer_customer_id: 1, email: 1 }, { unique: true, name: "uniq_referrer_email" }],
  [{ email: 1, claimed_at: 1 }, { name: "by_email" }],
]);

registerIndexes(REFERRALS, [
  // One referrer per person, ever, and only at signup.
  [{ referred_customer_id: 1 }, { unique: true, name: "uniq_referred" }],
  [{ referrer_customer_id: 1 }, { name: "by_referrer" }],
]);

registerIndexes(REFERRAL_REWARDS, [
  // Idempotency, enforced by the database rather than by remembering to check.
  // A signup bonus is paid once per side of a referral...
  [
    { referral_id: 1, role: 1, kind: 1 },
    {
      unique: true,
      name: "uniq_signup_reward",
      partialFilterExpression: { kind: "signup" },
    },
  ],
  // ...and one invoice triggers one payout, however many times the provider
  // decides to deliver its webhook.
  [
    { referral_id: 1, role: 1, invoice_id: 1 },
    {
      unique: true,
      name: "uniq_invoice_reward",
      partialFilterExpression: { invoice_id: { $type: "objectId" } },
    },
  ],
  [{ customer_id: 1 }, { name: "by_customer" }],
]);

function now(): Date {
  return new Date();
}

/**
 * Kept as an entry point for startup; MongoDB needs no schema built.
 *
 * The indexes are declared above and applied by `mongo.ensureIndexes()`.
 */
export function initBillingTables(): void {
  return;
}

// ---------------------------------------------------------------------------
// Customers
// ---------------------------------------------------------------------------

export async function createCustomer(
  email: string,
  name: string,
  passwordHash: string,
  options: {
    googleSub?: string | null;
    firebaseUid?: string | null;
    firebaseProvider?: string | null;
    authProvider?: string;
  } = {},
): Promise<Doc> {
  const { canonicalEmail } = await import("./identity.js");

  const address = email.toLowerCase().trim();
  const doc: Record<string, unknown> = {
    email: address,
    canonical_email: canonicalEmail(address),
    name,
    password_hash: passwordHash,
    created_at: nowISO(),
    is_active: 1,
    google_sub: options.googleSub ?? null,
    auth_provider: options.authProvider ?? PROVIDER_PASSWORD,
  };

  // Written only when present, so the partial unique index on firebase_uid
  // ignores password-only accounts instead of treating a shared null as a
  // collision.
  if (options.firebaseUid) doc.firebase_uid = options.firebaseUid;
  if (options.firebaseProvider) doc.firebase_provider = options.firebaseProvider;

  const customers = await coll(CUSTOMERS);
  try {
    const result = await customers.insertOne({ ...doc });
    return document({ ...doc, _id: result.insertedId })!;
  } catch (error) {
    if (isDuplicateKeyError(error)) {
      // The unique index is the enforcement; the caller's check is only the
      // polite version of this message.
      throw new DuplicateAccountError("An account already exists for that email address.");
    }
    throw error;
  }
}

/**
 * Look up by Google's stable subject id.
 *
 * Sign-in now runs through Firebase, so nothing writes this field any more. It
 * is still read, and must stay: accounts created by the old server-side OAuth
 * flow are keyed on it, and it is the only thing that recognises one of those
 * people when they arrive through Firebase Google instead. Without this lookup
 * they collide on the mailbox and are refused entry to their own account.
 */
export async function getCustomerByGoogleSub(googleSub: string): Promise<Doc | null> {
  const customers = await coll(CUSTOMERS);
  return document(await customers.findOne({ google_sub: googleSub }));
}

/**
 * Look up by Firebase's stable uid.
 *
 * Tried first on the sign-in path, because the uid is the identifier that
 * survives a user changing their email address.
 */
export async function getCustomerByFirebaseUid(firebaseUid: string): Promise<Doc | null> {
  if (!firebaseUid) return null;
  const customers = await coll(CUSTOMERS);
  return document(await customers.findOne({ firebase_uid: firebaseUid }));
}

/**
 * Attach a Firebase identity to an existing account.
 *
 * Only ever called once the email behind that identity is confirmed — see
 * `firebaseAuth.identityFromClaims`, where the unverified case is refused
 * before it can reach here.
 *
 * The filter requires `firebase_uid` to be absent or null, so this can never
 * re-point an account that is already linked to a *different* Firebase user.
 * Two people, one mailbox claim, and the second silently taking over the first
 * is exactly the failure this guards.
 *
 * `auth_provider` is deliberately left alone: an account that had a password
 * keeps it, and keeps both routes in.
 */
export async function linkFirebaseAccount(
  customerId: unknown,
  firebaseUid: string,
  provider?: string | null,
): Promise<Doc | null> {
  const oid = objectId(customerId);
  if (oid === null) return null;

  const updates: Record<string, unknown> = { firebase_uid: firebaseUid };
  if (provider) updates.firebase_provider = provider;

  const customers = await coll(CUSTOMERS);
  const result = await customers.updateOne(
    {
      _id: oid,
      $or: [{ firebase_uid: null }, { firebase_uid: { $exists: false } }],
    },
    { $set: updates },
  );

  if (result.matchedCount === 0) {
    // Already linked. Fine if it is the same uid (a re-run, a race between two
    // tabs); a refusal if it is someone else's.
    const existing = await getCustomerById(customerId);
    if (existing !== null && existing.firebase_uid !== firebaseUid) return null;
    return existing;
  }

  return getCustomerById(customerId);
}

/**
 * The account that owns this *mailbox*, whatever alias was typed.
 *
 * This is the lookup signup must use. {@link getCustomerByEmail} compares the
 * literal string, which is right for signing in — people type the address they
 * registered — and wrong for "is this already taken", where `you+1@gmail.com`
 * has to find the account registered as `you@gmail.com`.
 */
export async function getCustomerForMailbox(email: string): Promise<Doc | null> {
  const { canonicalEmail } = await import("./identity.js");

  const address = (email ?? "").trim().toLowerCase();
  const customers = await coll(CUSTOMERS);
  return document(
    await customers.findOne({
      $or: [{ canonical_email: canonicalEmail(address) }, { email: address }],
    }),
  );
}

export async function getCustomerByEmail(email: string): Promise<Doc | null> {
  const customers = await coll(CUSTOMERS);
  return document(await customers.findOne({ email: (email ?? "").toLowerCase().trim() }));
}

export async function getCustomerById(customerId: unknown): Promise<Doc | null> {
  const oid = objectId(customerId);
  if (oid === null) return null;
  const customers = await coll(CUSTOMERS);
  return document(await customers.findOne({ _id: oid }));
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

/** Persist a session for *rawToken* and return its expiry ISO timestamp. */
export async function createSession(customerId: unknown, rawToken: string): Promise<string> {
  const issued = now();
  const expires = addDays(issued, SESSION_TTL_DAYS);

  const sessions = await coll(SESSIONS);
  await sessions.insertOne({
    customer_id: toObjectId(customerId),
    token_hash: hashSessionToken(rawToken),
    created_at: istISO(issued),
    expires_at: istISO(expires),
    revoked: 0,
  });
  return istISO(expires);
}

/** Resolve a session token to its customer, or `null` if invalid/expired. */
export async function getSessionCustomer(rawToken: string): Promise<Doc | null> {
  if (!rawToken) return null;

  const sessions = await coll(SESSIONS);
  const session = await sessions.findOne({
    token_hash: hashSessionToken(rawToken),
    revoked: 0,
  });
  if (session === null) return null;

  const expiresAt = parseISO(session.expires_at);
  if (expiresAt === null || expiresAt.getTime() <= Date.now()) return null;

  const customers = await coll(CUSTOMERS);
  const customer = await customers.findOne({ _id: session.customer_id });
  if (customer === null || !customer.is_active) return null;

  const resolved = document(customer)!;
  resolved.session_expires_at = session.expires_at;
  return resolved;
}

/**
 * Delete sessions that expired or were revoked more than *keepDays* ago.
 *
 * Sessions are written on every sign-in and never removed otherwise, so the
 * collection grows forever — and every document is a (hashed) credential nobody
 * needs. A short grace period keeps recent ones around for debugging "why was I
 * logged out".
 */
export async function purgeDeadSessions(keepDays = 7): Promise<number> {
  const cutoff = istISO(addDays(now(), -keepDays));
  const sessions = await coll(SESSIONS);
  const result = await sessions.deleteMany({
    $or: [
      { expires_at: { $lt: cutoff } },
      { revoked: 1, created_at: { $lt: cutoff } },
    ],
  });
  return result.deletedCount;
}

export async function revokeSession(rawToken: string): Promise<void> {
  const sessions = await coll(SESSIONS);
  await sessions.updateOne(
    { token_hash: hashSessionToken(rawToken) },
    { $set: { revoked: 1 } },
  );
}

// ---------------------------------------------------------------------------
// Subscriptions
// ---------------------------------------------------------------------------

/** The customer's most recent subscription, whatever its status. */
export async function getCurrentSubscription(customerId: unknown): Promise<Doc | null> {
  const oid = objectId(customerId);
  if (oid === null) return null;

  const subscriptions = await coll(SUBSCRIPTIONS);
  return document(await subscriptions.findOne({ customer_id: oid }, { sort: { _id: -1 } }));
}

/**
 * True when this subscription currently grants access.
 *
 * `canceled` still counts until the paid period actually runs out — the
 * customer paid for it.
 */
export function isEntitled(subscription: Doc | null): boolean {
  if (subscription === null) return false;
  if (!ENTITLED_STATUSES.includes(subscription.status)) return false;

  const end = parseISO(subscription.current_period_end);
  return end !== null && end.getTime() > Date.now();
}

/** Open a new subscription period for *plan* starting now. */
export async function startSubscription(
  customerId: unknown,
  plan: Plan,
  status: string,
  provider = "manual",
  providerRef: string | null = null,
): Promise<Doc> {
  const started = now();
  const doc = {
    customer_id: toObjectId(customerId),
    plan_id: plan.id,
    status,
    current_period_start: istISO(started),
    current_period_end: istISO(addDays(started, plan.interval_days)),
    provider,
    provider_ref: providerRef,
    created_at: istISO(started),
    updated_at: istISO(started),
  };

  const subscriptions = await coll(SUBSCRIPTIONS);
  const result = await subscriptions.insertOne({ ...doc });
  return document({ ...doc, _id: result.insertedId })!;
}

export async function setSubscriptionStatus(
  subscriptionId: unknown,
  status: string,
): Promise<void> {
  const oid = objectId(subscriptionId);
  if (oid === null) return;

  const subscriptions = await coll(SUBSCRIPTIONS);
  await subscriptions.updateOne(
    { _id: oid },
    { $set: { status, updated_at: nowISO() } },
  );
}

export async function hasUsedTrial(customerId: unknown): Promise<boolean> {
  const oid = objectId(customerId);
  if (oid === null) return false;

  const subscriptions = await coll(SUBSCRIPTIONS);
  const count = await subscriptions.countDocuments(
    { customer_id: oid, plan_id: "trial" },
    { limit: 1 },
  );
  return count > 0;
}

// ---------------------------------------------------------------------------
// Invoices
// ---------------------------------------------------------------------------

export async function createInvoice(
  customerId: unknown,
  subscriptionId: unknown | null,
  plan: Plan,
  currency: string,
  status: string,
  provider = "manual",
  providerRef: string | null = null,
): Promise<Doc> {
  const issued = nowISO();
  const doc = {
    customer_id: toObjectId(customerId),
    subscription_id: objectId(subscriptionId),
    plan_id: plan.id,
    amount_cents: plan.price_cents,
    currency,
    status,
    provider,
    provider_ref: providerRef,
    issued_at: issued,
    paid_at: status === "paid" ? issued : null,
  };

  const invoices = await coll(INVOICES);
  const result = await invoices.insertOne({ ...doc });
  return document({ ...doc, _id: result.insertedId })!;
}

export async function getInvoice(invoiceId: unknown): Promise<Doc | null> {
  const oid = objectId(invoiceId);
  if (oid === null) return null;
  const invoices = await coll(INVOICES);
  return document(await invoices.findOne({ _id: oid }));
}

export async function listInvoices(customerId: unknown): Promise<Doc[]> {
  const oid = objectId(customerId);
  if (oid === null) return [];
  const invoices = await coll(INVOICES);
  return documents(await invoices.find({ customer_id: oid }).sort({ _id: -1 }).toArray());
}

/**
 * Settle this customer's open invoices and hand back what was settled.
 *
 * Activation used to leave the invoice it created sitting at `open` forever,
 * which made "how much have we actually billed?" unanswerable and left the
 * referral payout with no payment to hang off. Returning the documents is what
 * lets the caller reward a referral exactly once per invoice.
 */
export async function markOpenInvoicesPaid(
  customerId: unknown,
  subscriptionId: unknown = null,
): Promise<Doc[]> {
  const oid = objectId(customerId);
  if (oid === null) return [];

  const query: Record<string, unknown> = { customer_id: oid, status: "open" };
  const subOid = objectId(subscriptionId);
  if (subOid !== null) {
    query.$or = [{ subscription_id: subOid }, { subscription_id: null }];
  }

  const invoices = await coll(INVOICES);
  const openRows = await invoices.find(query, { projection: { _id: 1 } }).toArray();
  const openIds = openRows.map((inv) => inv._id);
  if (openIds.length === 0) return [];

  await invoices.updateMany(
    { _id: { $in: openIds } },
    { $set: { status: "paid", paid_at: nowISO() } },
  );
  return documents(await invoices.find({ _id: { $in: openIds } }).toArray());
}

export async function markInvoicePaid(
  invoiceId: unknown,
  providerRef: string | null = null,
): Promise<void> {
  const oid = objectId(invoiceId);
  if (oid === null) return;

  const update: Record<string, unknown> = { status: "paid", paid_at: nowISO() };
  if (providerRef !== null) update.provider_ref = providerRef;

  const invoices = await coll(INVOICES);
  await invoices.updateOne({ _id: oid }, { $set: update });
}

// ---------------------------------------------------------------------------
// Expiry sweep
// ---------------------------------------------------------------------------

/**
 * Flip any subscription whose period has ended to `expired`.
 *
 * Returns the affected customers as `{id, email, name}` so the caller can
 * withdraw whatever the plan was paying for. It hands them back rather than
 * withdrawing directly because this module has no business knowing what a
 * subscription *entitles* — that lives in `entitlements.ts`.
 */
export async function expireLapsedSubscriptions(): Promise<
  Array<{ id: string; email: string; name: string }>
> {
  const cutoff = nowISO();
  const query = {
    status: { $in: [STATUS_TRIALING, STATUS_ACTIVE, STATUS_CANCELED] },
    current_period_end: { $lte: cutoff },
  };

  const subscriptions = await coll(SUBSCRIPTIONS);
  // Read the affected customers before the update: afterwards they are
  // indistinguishable from subscriptions that expired last month.
  const customerIds = await subscriptions.distinct("customer_id", query);
  if (customerIds.length === 0) return [];

  await subscriptions.updateMany(query, {
    $set: { status: STATUS_EXPIRED, updated_at: cutoff },
  });

  const customers = await coll(CUSTOMERS);
  const rows = await customers.find({ _id: { $in: customerIds } }).toArray();

  return rows.map((c) => ({
    id: c._id.toHexString(),
    email: c.email,
    name: c.name ?? "",
  }));
}
