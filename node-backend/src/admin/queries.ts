/**
 * Cross-tenant reads for the admin panel.
 *
 * Port of `backend/admin/queries.py`.
 *
 * This is the second deliberate exception to tenant isolation, after `ops.ts`.
 * The difference between them is who they are for and what they show: `ops`
 * feeds a model and therefore carries aggregates *without identities*, while
 * this module feeds a person who runs the platform and has to be able to answer
 * "which customer is this?". So it does return names, emails and per-account
 * numbers — and it is gated on the admin key, never reachable with a customer
 * session or a customer API key.
 *
 * **On shape.** The SQLite version was one query per screen, built from
 * correlated subqueries. The MongoDB version reads each collection plainly and
 * joins in application code. That is a deliberate trade, not a translation
 * failure:
 *
 * * the joins are across five collections at four different grains, which as an
 *   aggregation pipeline becomes something no reviewer can check by eye;
 * * `$lookup` with sub-pipelines does not run on every deployment, so the tests
 *   could not exercise the real query;
 * * the volumes are hundreds to low thousands of documents, where the
 *   difference between a pipeline and a loop is not measurable.
 *
 * Where a single-collection `$group` does the job — summing credits by day, by
 * endpoint — it is used, because that one is both clearer and cheaper.
 *
 * The queries are written to survive a half-populated database. A customer with
 * no API-key account, an account with no bot, a bot with no sheet: all normal
 * on day one, all rendered as zero rather than as a missing row.
 */

import { DAILY_CREDIT_LIMIT } from "../config.js";
import { ROLE_OWNER } from "../shared/apiKeys.js";
import { coll, documents, objectId, type Doc } from "../shared/mongo.js";
import { daysAgoIST, istISO, nowISO, todayIST } from "../shared/time.js";
import * as billingDb from "../billing/db.js";
import * as referrals from "../billing/referrals.js";
import { BILLABLE_PLAN_IDS, CURRENCY, PLANS, formatCents } from "../billing/plans.js";

// A page of accounts. Large enough that most deployments never paginate, small
// enough that the panel stays responsive when one day they do.
export const DEFAULT_PAGE = 50;
export const MAX_PAGE = 500;

const USERS = "users";
const API_KEYS = "api_keys";
const DAILY_USAGE = "daily_usage";
const REQUEST_LOG = "request_log";
const CREDIT_GRANTS = "credit_grants";
const UNMATCHED = "unmatched_queries";
const BOTS = "bots";

/** ISO cutoff *days* ago, for the `created_at`/`timestamp` fields. */
function since(days: number): string {
  return istISO(new Date(Date.now() - days * 86_400_000));
}

/** `YYYY-MM-DD` cutoff, for the date-keyed `daily_usage` documents. */
function dateSince(days: number): string {
  return daysAgoIST(days);
}

function today(): string {
  return todayIST();
}

/** Escape a user-supplied string so it matches literally inside `$regex`. */
function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** A stable map key for an ObjectId-or-null. */
function idKey(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

/** `{key: total}` for one collection — the one shape a pipeline suits. */
async function sumBy(
  collection: string,
  match: Record<string, unknown>,
  groupBy: string,
  field: string,
): Promise<Map<string, number>> {
  const c = await coll(collection);
  const rows = await c
    .aggregate([
      { $match: match },
      { $group: { _id: `$${groupBy}`, total: { $sum: `$${field}` } } },
    ])
    .toArray();
  return new Map(rows.map((row) => [idKey(row._id), Math.trunc(row.total)]));
}

async function countBy(
  collection: string,
  match: Record<string, unknown>,
  groupBy: string,
): Promise<Map<string, number>> {
  const c = await coll(collection);
  const rows = await c
    .aggregate([{ $match: match }, { $group: { _id: `$${groupBy}`, n: { $sum: 1 } } }])
    .toArray();
  return new Map(rows.map((row) => [idKey(row._id), Math.trunc(row.n)]));
}

// ---------------------------------------------------------------------------
// Shared assembly
// ---------------------------------------------------------------------------

/**
 * Each customer's *current* subscription, keyed by customer id.
 *
 * "Latest wins" is the same rule the customer's own dashboard uses, so the two
 * screens can never disagree about what plan somebody is on.
 */
async function currentSubscriptions(): Promise<Map<string, Doc>> {
  const subs = await coll(billingDb.SUBSCRIPTIONS);
  const rows = await subs.find().sort({ _id: 1 }).toArray();

  const current = new Map<string, Doc>();
  for (const sub of rows) {
    current.set(idKey(sub.customer_id), sub as Doc); // later documents overwrite earlier
  }
  return current;
}

function isLive(sub: Doc | undefined | null): boolean {
  if (!sub) return false;
  return (
    billingDb.ENTITLED_STATUSES.includes(sub.status) &&
    (sub.current_period_end ?? "") > nowISO()
  );
}

/**
 * One subscription's contribution to monthly recurring revenue.
 *
 * A yearly plan counts as a twelfth of its price, not its full price: MRR that
 * spikes when someone pays for a year is not a run rate, it is a cash receipt
 * wearing a run rate's name.
 */
function monthlyValueCents(planId: string): number {
  const plan = PLANS[planId];
  if (plan === undefined || plan.price_cents === 0) return 0;
  return plan.interval === "month" ? plan.price_cents : Math.round(plan.price_cents / 12);
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

/** The headline numbers. */
export async function overview(days = 30): Promise<Doc> {
  const cutoff = since(days);

  const customersColl = await coll(billingDb.CUSTOMERS);
  const customers = await customersColl.find().toArray();

  const subs = await currentSubscriptions();
  const byStatus: Record<string, number> = {};
  const byPlan: Record<string, number> = {};
  let entitled = 0;
  let paying = 0;
  let trialing = 0;
  let mrrCents = 0;

  for (const sub of subs.values()) {
    byStatus[sub.status] = (byStatus[sub.status] ?? 0) + 1;
    byPlan[sub.plan_id] = (byPlan[sub.plan_id] ?? 0) + 1;

    if (isLive(sub)) {
      entitled += 1;
      // Entitled and paying are not the same thing — a trial is entitled and
      // worth nothing. Reporting them as one number is how a dashboard ends up
      // flattering itself.
      if ((BILLABLE_PLAN_IDS as readonly string[]).includes(sub.plan_id)) {
        paying += 1;
      } else {
        trialing += 1;
      }
      mrrCents += monthlyValueCents(sub.plan_id);
    }
  }

  const invoicesColl = await coll(billingDb.INVOICES);
  const paidInvoices = await invoicesColl.find({ status: "paid" }).toArray();
  const revenueAll = paidInvoices.reduce((sum, i) => sum + i.amount_cents, 0);
  const revenueWindow = paidInvoices
    .filter((i) => (i.paid_at ?? "") >= cutoff)
    .reduce((sum, i) => sum + i.amount_cents, 0);

  const requestLog = await coll(REQUEST_LOG);
  const requests = await requestLog.find({ timestamp: { $gte: cutoff } }).toArray();

  const dailyUsage = await coll(DAILY_USAGE);
  const todayRows = await dailyUsage.find({ usage_date: today() }).toArray();
  const creditsToday = todayRows.reduce((sum, d) => sum + d.credits_used, 0);

  const botsColl = await coll(BOTS);
  const bots = await botsColl.find().toArray();
  const ready = bots.filter((b) => b.status === "ready").length;

  const grantsColl = await coll(CREDIT_GRANTS);
  const grants = await grantsColl.find().toArray();

  const keysColl = await coll(API_KEYS);
  const referralTotals = (await referrals.platformStats()).totals;

  return {
    window_days: days,
    generated_at: nowISO(),
    customers: {
      total: customers.length,
      active: customers.filter((c) => c.is_active).length,
      new_in_window: customers.filter((c) => (c.created_at ?? "") >= cutoff).length,
      via_google: customers.filter((c) => c.auth_provider === billingDb.PROVIDER_GOOGLE)
        .length,
    },
    subscriptions: {
      entitled,
      paying,
      on_trial: trialing,
      by_status: byStatus,
      by_plan: byPlan,
      mrr_cents: mrrCents,
      mrr_display: formatCents(mrrCents),
      currency: CURRENCY,
    },
    revenue: {
      all_time_cents: revenueAll,
      all_time_display: formatCents(revenueAll),
      in_window_cents: revenueWindow,
      in_window_display: formatCents(revenueWindow),
      invoices_paid: paidInvoices.length,
    },
    usage: {
      requests_in_window: requests.length,
      credits_in_window: requests.reduce((sum, r) => sum + (r.credit_cost ?? 0), 0),
      active_accounts: new Set(requests.map((r) => idKey(r.user_id))).size,
      credits_today: creditsToday,
    },
    bots: {
      total: bots.length,
      ready,
      draft: bots.length - ready,
      rewording_on: bots.filter((b) => b.llm_enabled).length,
      indexed_rows: bots.reduce((sum, b) => sum + (b.doc_count ?? 0), 0),
    },
    keys: {
      total: await keysColl.countDocuments({}),
      active: await keysColl.countDocuments({ is_active: 1 }),
    },
    bonus_credits: {
      granted_all_time: grants.reduce((sum, g) => sum + g.amount, 0),
      outstanding: grants.reduce((sum, g) => sum + g.remaining, 0),
    },
    referrals: referralTotals,
  };
}

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

/**
 * One row per customer, with everything the list view shows.
 *
 * Assembled from six reads rather than six joins. Each aggregate is over a
 * different grain — keys per account, credits per account-day, requests per
 * account — and combining them in one query is what silently multiplies rows
 * and inflates every total on the screen.
 */
async function accountRows(
  days: number,
  customerFilter: Record<string, unknown> = {},
): Promise<Doc[]> {
  const customersColl = await coll(billingDb.CUSTOMERS);
  const customers = await customersColl.find(customerFilter).toArray();
  if (customers.length === 0) return [];

  const emails = customers.map((c) => String(c.email).toLowerCase());

  const usersColl = await coll(USERS);
  const userRows = await usersColl.find({ email: { $in: emails } }).toArray();
  const users = new Map(userRows.map((u) => [String(u.email), u]));
  const userIds = userRows.map((u) => u._id);

  const botsColl = await coll(BOTS);
  const botRows = await botsColl.find({ user_id: { $in: userIds } }).toArray();
  const bots = new Map(botRows.map((b) => [idKey(b.user_id), b]));

  const subs = await currentSubscriptions();

  const bonus = await sumBy(
    CREDIT_GRANTS,
    { user_id: { $in: userIds } },
    "user_id",
    "remaining",
  );
  const creditsToday = await sumBy(
    DAILY_USAGE,
    { user_id: { $in: userIds }, usage_date: today() },
    "user_id",
    "credits_used",
  );
  const creditsWindow = await sumBy(
    DAILY_USAGE,
    { user_id: { $in: userIds }, usage_date: { $gte: dateSince(days) } },
    "user_id",
    "credits_used",
  );
  const requestsWindow = await countBy(
    REQUEST_LOG,
    { user_id: { $in: userIds }, timestamp: { $gte: since(days) } },
    "user_id",
  );
  const activeKeys = await countBy(
    API_KEYS,
    { user_id: { $in: userIds }, is_active: 1 },
    "user_id",
  );

  const requestLog = await coll(REQUEST_LOG);
  const lastRequestRows = await requestLog
    .aggregate([
      { $match: { user_id: { $in: userIds } } },
      { $group: { _id: "$user_id", last: { $max: "$timestamp" } } },
    ])
    .toArray();
  const lastRequest = new Map(lastRequestRows.map((r) => [idKey(r._id), r.last as string]));

  // Referral standing, both directions.
  const referredCounts = await countBy(billingDb.REFERRALS, {}, "referrer_customer_id");

  const referralsColl = await coll(billingDb.REFERRALS);
  const referralRows = await referralsColl.find().toArray();
  const referredBy = new Map(
    referralRows.map((r) => [idKey(r.referred_customer_id), r.referrer_customer_id]),
  );

  const referrerIds = [...new Set(referralRows.map((r) => idKey(r.referrer_customer_id)))]
    .filter((id) => id)
    .map((id) => objectId(id))
    .filter((oid): oid is NonNullable<typeof oid> => oid !== null);

  const referrerRows = await customersColl
    .find({ _id: { $in: referrerIds } }, { projection: { email: 1 } })
    .toArray();
  const referrerEmails = new Map(referrerRows.map((c) => [idKey(c._id), c.email as string]));

  const rows: Doc[] = [];
  for (const customer of customers) {
    const user = users.get(String(customer.email).toLowerCase());
    const userId = user ? user._id : null;
    const userKey = idKey(userId);
    const bot = userId ? bots.get(userKey) : undefined;
    const sub = subs.get(idKey(customer._id));
    const limit = user ? (user.daily_credit_limit ?? null) : null;

    rows.push({
      customer_id: idKey(customer._id),
      user_id: userId ? userKey : null,
      email: customer.email,
      name: customer.name ?? "",
      created_at: customer.created_at,
      is_active: customer.is_active ?? 0,
      auth_provider: customer.auth_provider ?? billingDb.PROVIDER_PASSWORD,
      role: user ? (user.role ?? null) : null,
      daily_credit_limit: limit,
      effective_daily_limit: limit === null ? DAILY_CREDIT_LIMIT : limit,
      bonus_credits: bonus.get(userKey) ?? 0,
      credits_today: creditsToday.get(userKey) ?? 0,
      credits_in_window: creditsWindow.get(userKey) ?? 0,
      requests_in_window: requestsWindow.get(userKey) ?? 0,
      last_request_at: lastRequest.get(userKey) ?? null,
      active_keys: activeKeys.get(userKey) ?? 0,
      bot_id: bot ? idKey(bot._id) : null,
      template_id: bot ? bot.template_id : null,
      bot_status: bot ? bot.status : null,
      bot_doc_count: bot ? bot.doc_count : null,
      bot_llm_enabled: bot ? bot.llm_enabled : null,
      plan_id: sub ? sub.plan_id : null,
      subscription_status: sub ? sub.status : null,
      current_period_end: sub ? sub.current_period_end : null,
      is_entitled: isLive(sub),
      referred_count: referredCounts.get(idKey(customer._id)) ?? 0,
      referred_by_email:
        referrerEmails.get(idKey(referredBy.get(idKey(customer._id)))) ?? null,
    });
  }
  return rows;
}

const SORTS: Record<string, (row: Doc) => string | number> = {
  created: (r) => r.created_at ?? "",
  email: (r) => String(r.email).toLowerCase(),
  credits: (r) => r.credits_in_window,
  requests: (r) => r.requests_in_window,
  referrals: (r) => r.referred_count,
};

/**
 * Every customer, with their plan, usage, bot and referral standing.
 *
 * *status* filters on the current subscription (`entitled` means "can use the
 * product right now", which is not the same as any single status value).
 */
export async function listUsers(options: {
  query?: string;
  status?: string;
  days?: number;
  sort?: string;
  limit?: number;
  offset?: number;
}): Promise<Doc> {
  const query = options.query ?? "";
  const status = options.status ?? "";
  const days = options.days ?? 30;
  const sort = options.sort ?? "created";
  const limit = Math.max(1, Math.min(options.limit ?? DEFAULT_PAGE, MAX_PAGE));
  const offset = options.offset ?? 0;

  const customerFilter: Record<string, unknown> = {};
  if (query) {
    // Anchored on neither end: staff search for a fragment of an address as
    // often as its start. Escaped, because a customer's email is not a pattern
    // and a stray "+" would otherwise change the search.
    const pattern = escapeRegex(query.toLowerCase().trim());
    customerFilter.$or = [
      { email: { $regex: pattern, $options: "i" } },
      { name: { $regex: pattern, $options: "i" } },
    ];
  }
  if (status === "disabled") customerFilter.is_active = 0;

  let rows = await accountRows(days, customerFilter);

  if (status === "entitled") {
    rows = rows.filter((r) => r.is_entitled);
  } else if (status === "paying") {
    rows = rows.filter(
      (r) => r.is_entitled && (BILLABLE_PLAN_IDS as readonly string[]).includes(r.plan_id),
    );
  } else if (status && status !== "disabled") {
    rows = rows.filter((r) => r.subscription_status === status);
  }

  const key = SORTS[sort] ?? SORTS.created;
  const descending = sort !== "email";
  rows.sort((a, b) => {
    const left = key(a);
    const right = key(b);
    const cmp = left < right ? -1 : left > right ? 1 : 0;
    return descending ? -cmp : cmp;
  });

  return {
    total: rows.length,
    limit,
    offset,
    window_days: days,
    users: rows.slice(offset, offset + limit),
  };
}

/** One account, in full: plan history, invoices, keys, credits, referrals. */
export async function userDetail(customerId: unknown, days = 30): Promise<Doc | null> {
  const oid = objectId(customerId);
  if (oid === null) return null;

  const rows = await accountRows(days, { _id: oid });
  if (rows.length === 0) return null;
  const account = rows[0];

  const userOid = account.user_id ? objectId(account.user_id) : null;

  const keysColl = await coll(API_KEYS);
  const keys = userOid
    ? (await keysColl.find({ user_id: userOid }).sort({ _id: -1 }).toArray()).map((k) => ({
        id: idKey(k._id),
        key_prefix: k.key_prefix,
        label: k.label ?? "",
        created_at: k.created_at,
        is_active: k.is_active ?? 0,
      }))
    : [];

  // Read the log once, then label each entry with the key that made it.
  const requestLog = await coll(REQUEST_LOG);
  const rawRequests = userOid
    ? await requestLog.find({ user_id: userOid }).sort({ _id: -1 }).limit(50).toArray()
    : [];

  const prefixes = new Map(keys.map((k) => [k.id, k.key_prefix]));
  const recentRequests = rawRequests.map((r) => ({
    id: idKey(r._id),
    endpoint: r.endpoint,
    message_len: r.message_len ?? 0,
    credit_cost: r.credit_cost ?? 0,
    timestamp: r.timestamp,
    key_prefix: prefixes.get(idKey(r.api_key_id)) ?? null,
  }));

  const subsColl = await coll(billingDb.SUBSCRIPTIONS);
  const invoicesColl = await coll(billingDb.INVOICES);
  const grantsColl = await coll(CREDIT_GRANTS);
  const dailyUsage = await coll(DAILY_USAGE);

  return {
    account,
    subscriptions: documents(
      await subsColl.find({ customer_id: oid }).sort({ _id: -1 }).limit(25).toArray(),
    ),
    invoices: documents(
      await invoicesColl.find({ customer_id: oid }).sort({ _id: -1 }).limit(25).toArray(),
    ),
    keys,
    credit_grants: userOid
      ? documents(
          await grantsColl.find({ user_id: userOid }).sort({ _id: -1 }).limit(50).toArray(),
        )
      : [],
    daily_usage: userOid
      ? (
          await dailyUsage
            .find({ user_id: userOid, usage_date: { $gte: dateSince(days) } })
            .sort({ usage_date: 1 })
            .toArray()
        ).map((d) => ({ usage_date: d.usage_date, credits_used: d.credits_used }))
      : [],
    recent_requests: recentRequests,
    referrals: await referrals.summaryFor(String(customerId)),
  };
}

// ---------------------------------------------------------------------------
// Subscriptions
// ---------------------------------------------------------------------------

/**
 * The subscription book: what is live, what it is worth, what churned.
 *
 * Trial-to-paid conversion is counted per customer rather than per subscription
 * document, because one customer trialling twice is not two conversions.
 */
export async function subscriptions(days = 90): Promise<Doc> {
  const cutoff = since(days);
  const current = await currentSubscriptions();

  const customerOids = [...current.keys()]
    .map((id) => objectId(id))
    .filter((oid): oid is NonNullable<typeof oid> => oid !== null);

  const customersColl = await coll(billingDb.CUSTOMERS);
  const peopleRows = await customersColl.find({ _id: { $in: customerOids } }).toArray();
  const people = new Map(peopleRows.map((c) => [idKey(c._id), c]));

  const rows: Doc[] = [];
  const byStatus: Record<string, number> = {};
  const byPlan: Record<string, Doc> = {};

  for (const [customerKey, sub] of current) {
    const live = isLive(sub);
    const value = live ? monthlyValueCents(sub.plan_id) : 0;
    const person: Doc = people.get(customerKey) ?? {};

    rows.push({
      id: idKey(sub._id),
      customer_id: customerKey,
      email: person.email ?? "",
      name: person.name ?? "",
      plan_id: sub.plan_id,
      status: sub.status,
      current_period_end: sub.current_period_end,
      is_entitled: live,
      monthly_value_cents: value,
    });

    byStatus[sub.status] = (byStatus[sub.status] ?? 0) + 1;

    let bucket = byPlan[sub.plan_id];
    if (bucket === undefined) {
      bucket = { plan_id: sub.plan_id, count: 0, entitled: 0, mrr_cents: 0 };
      byPlan[sub.plan_id] = bucket;
    }
    bucket.count += 1;
    if (live) {
      bucket.entitled += 1;
      bucket.mrr_cents += value;
    }
  }

  rows.sort((a, b) => (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));

  const invoicesColl = await coll(billingDb.INVOICES);
  const paidInvoices = await invoicesColl.find({ status: "paid" }).toArray();

  const revenueByMonth = new Map<string, Doc>();
  for (const invoice of paidInvoices) {
    const month = String(invoice.paid_at ?? "").slice(0, 7);
    if (!month) continue;

    let bucket = revenueByMonth.get(month);
    if (bucket === undefined) {
      bucket = { month, invoices: 0, amount_cents: 0 };
      revenueByMonth.set(month, bucket);
    }
    bucket.invoices += 1;
    bucket.amount_cents += invoice.amount_cents;
  }

  const months = [...revenueByMonth.values()]
    .sort((a, b) => (a.month < b.month ? 1 : a.month > b.month ? -1 : 0))
    .slice(0, 24);
  for (const month of months) {
    month.amount_display = formatCents(month.amount_cents);
  }

  const started = await countBy(
    billingDb.SUBSCRIPTIONS,
    { created_at: { $gte: cutoff } },
    "plan_id",
  );
  const churn = await countBy(
    billingDb.SUBSCRIPTIONS,
    {
      updated_at: { $gte: cutoff },
      status: { $in: [billingDb.STATUS_CANCELED, billingDb.STATUS_EXPIRED] },
    },
    "status",
  );

  const subsColl = await coll(billingDb.SUBSCRIPTIONS);
  const trialled = (await subsColl.distinct("customer_id", { plan_id: "trial" })).length;
  const converted = (
    await subsColl.distinct("customer_id", {
      plan_id: { $in: [...BILLABLE_PLAN_IDS] },
      status: { $in: [billingDb.STATUS_ACTIVE, billingDb.STATUS_CANCELED] },
    })
  ).length;

  const mrr = rows.reduce((sum, r) => sum + r.monthly_value_cents, 0);

  return {
    window_days: days,
    counts: {
      customers_with_a_subscription: rows.length,
      entitled: rows.filter((r) => r.is_entitled).length,
      paying: rows.filter(
        (r) => r.is_entitled && (BILLABLE_PLAN_IDS as readonly string[]).includes(r.plan_id),
      ).length,
      by_status: byStatus,
    },
    by_plan: Object.values(byPlan).sort((a, b) => b.mrr_cents - a.mrr_cents),
    mrr_cents: mrr,
    mrr_display: formatCents(mrr),
    revenue_by_month: months,
    started_in_window: Object.fromEntries(started),
    churn_in_window: {
      canceled: churn.get(billingDb.STATUS_CANCELED) ?? 0,
      expired: churn.get(billingDb.STATUS_EXPIRED) ?? 0,
    },
    trial_conversion: {
      trialled,
      converted,
      percent: trialled ? Math.round((converted * 1000) / trialled) / 10 : 0.0,
    },
    subscriptions: rows.slice(0, MAX_PAGE),
  };
}

// ---------------------------------------------------------------------------
// Usage
// ---------------------------------------------------------------------------

/** Credits and requests over time, by account, and by endpoint. */
export async function usage(days = 30): Promise<Doc> {
  const dateCutoff = dateSince(days);
  const cutoff = since(days);

  const perDayMap = new Map<string, Doc>();

  const dailyUsage = await coll(DAILY_USAGE);
  const usageRows = await dailyUsage.find({ usage_date: { $gte: dateCutoff } }).toArray();
  for (const row of usageRows) {
    let bucket = perDayMap.get(row.usage_date);
    if (bucket === undefined) {
      bucket = { date: row.usage_date, credits: 0, accounts: 0, requests: 0 };
      perDayMap.set(row.usage_date, bucket);
    }
    bucket.credits += row.credits_used;
    bucket.accounts += 1;
  }

  const requestLog = await coll(REQUEST_LOG);
  const requests = await requestLog.find({ timestamp: { $gte: cutoff } }).toArray();

  for (const request of requests) {
    const day = String(request.timestamp).slice(0, 10);
    let bucket = perDayMap.get(day);
    if (bucket === undefined) {
      bucket = { date: day, credits: 0, accounts: 0, requests: 0 };
      perDayMap.set(day, bucket);
    }
    bucket.requests += 1;
  }

  const perDay = [...perDayMap.values()].sort((a, b) => (a.date < b.date ? -1 : 1));

  const usersColl = await coll(USERS);
  const userRows = await usersColl.find().toArray();
  const users = new Map(userRows.map((u) => [idKey(u._id), u]));

  const creditsByUser = await sumBy(
    DAILY_USAGE,
    { usage_date: { $gte: dateCutoff } },
    "user_id",
    "credits_used",
  );

  const requestsByUser = new Map<string, number>();
  for (const request of requests) {
    const key = idKey(request.user_id);
    requestsByUser.set(key, (requestsByUser.get(key) ?? 0) + 1);
  }

  const everyUserId = new Set([...creditsByUser.keys(), ...requestsByUser.keys()]);
  const topAccounts = [...everyUserId]
    .map((userId) => ({
      user_id: userId,
      email: users.get(userId)?.email ?? "",
      name: users.get(userId)?.name ?? "",
      role: users.get(userId)?.role ?? "",
      credits: creditsByUser.get(userId) ?? 0,
      requests: requestsByUser.get(userId) ?? 0,
    }))
    .sort((a, b) => {
      if (a.credits !== b.credits) return b.credits - a.credits;
      return b.requests - a.requests;
    })
    .slice(0, 25);

  const byEndpointMap = new Map<
    string,
    { endpoint: string; requests: number; credits: number; accounts: Set<string>; total_len: number }
  >();

  for (const request of requests) {
    let bucket = byEndpointMap.get(request.endpoint);
    if (bucket === undefined) {
      bucket = {
        endpoint: request.endpoint,
        requests: 0,
        credits: 0,
        accounts: new Set(),
        total_len: 0,
      };
      byEndpointMap.set(request.endpoint, bucket);
    }
    bucket.requests += 1;
    bucket.credits += request.credit_cost ?? 0;
    bucket.accounts.add(idKey(request.user_id));
    bucket.total_len += request.message_len ?? 0;
  }

  const byEndpoint = [...byEndpointMap.values()]
    .map((b) => ({
      endpoint: b.endpoint,
      requests: b.requests,
      credits: b.credits,
      accounts: b.accounts.size,
      avg_message_len: b.requests ? Math.floor(b.total_len / b.requests) : 0,
    }))
    .sort((a, b) => b.requests - a.requests);

  return {
    window_days: days,
    totals: {
      requests: requests.length,
      credits: requests.reduce((sum, r) => sum + (r.credit_cost ?? 0), 0),
      active_accounts: new Set(requests.map((r) => idKey(r.user_id))).size,
    },
    per_day: perDay,
    top_accounts: topAccounts,
    by_endpoint: byEndpoint,
  };
}

// ---------------------------------------------------------------------------
// API audit
// ---------------------------------------------------------------------------

/**
 * The API request trail: who called what, with which key, at what cost.
 *
 * Owner keys are unmetered but still logged, and this is the screen that exists
 * for them — an unlimited cross-tenant key is the one whose activity most needs
 * to be visible. `role` filters to exactly that.
 */
export async function audit(options: {
  days?: number;
  email?: string;
  endpoint?: string;
  role?: string;
  limit?: number;
  offset?: number;
}): Promise<Doc> {
  const days = options.days ?? 7;
  const email = options.email ?? "";
  const endpoint = options.endpoint ?? "";
  const role = options.role ?? "";
  const limit = Math.max(1, Math.min(options.limit ?? 200, 1000));
  const offset = options.offset ?? 0;

  const userFilter: Record<string, unknown> = {};
  if (email) {
    userFilter.email = {
      $regex: escapeRegex(email.toLowerCase().trim()),
      $options: "i",
    };
  }
  if (role) userFilter.role = role;

  const usersColl = await coll(USERS);
  const hasUserFilter = Object.keys(userFilter).length > 0;

  let userRows = await usersColl.find(userFilter).toArray();
  let users = new Map(userRows.map((u) => [idKey(u._id), u]));

  const query: Record<string, unknown> = { timestamp: { $gte: since(days) } };
  if (hasUserFilter) {
    query.user_id = { $in: userRows.map((u) => u._id) };
  }
  if (endpoint) {
    query.endpoint = { $regex: escapeRegex(endpoint.trim()), $options: "i" };
  }

  const requestLog = await coll(REQUEST_LOG);
  const total = await requestLog.countDocuments(query);
  const matched = await requestLog
    .find(query)
    .sort({ _id: -1 })
    .skip(offset)
    .limit(limit)
    .toArray();

  if (!hasUserFilter) {
    userRows = await usersColl
      .find({ _id: { $in: matched.map((m) => m.user_id) } })
      .toArray();
    users = new Map(userRows.map((u) => [idKey(u._id), u]));
  }

  const keysColl = await coll(API_KEYS);
  const keyRows = await keysColl
    .find({ _id: { $in: matched.map((m) => m.api_key_id).filter((id) => id) } })
    .toArray();
  const keys = new Map(keyRows.map((k) => [idKey(k._id), k]));

  const entries = matched.map((row) => {
    const user: Doc = users.get(idKey(row.user_id)) ?? {};
    const key: Doc = keys.get(idKey(row.api_key_id)) ?? {};
    return {
      id: idKey(row._id),
      timestamp: row.timestamp,
      endpoint: row.endpoint,
      message_len: row.message_len ?? 0,
      credit_cost: row.credit_cost ?? 0,
      owner_email: user.email ?? null,
      role: user.role ?? null,
      key_prefix: key.key_prefix ?? null,
      key_label: key.label ?? null,
      key_is_active: key.is_active ?? null,
    };
  });

  const allMatching = await requestLog.find(query).toArray();
  const summary = {
    requests: allMatching.length,
    credits: allMatching.reduce((sum, r) => sum + (r.credit_cost ?? 0), 0),
    accounts: new Set(allMatching.map((r) => idKey(r.user_id))).size,
    keys_used: new Set(allMatching.map((r) => idKey(r.api_key_id))).size,
  };

  const ownerUserRows = await usersColl.find({ role: ROLE_OWNER }).toArray();
  const ownerUsers = new Map(ownerUserRows.map((u) => [idKey(u._id), u]));

  const ownerKeyRows = await keysColl
    .find({ user_id: { $in: ownerUserRows.map((u) => u._id) } })
    .sort({ _id: -1 })
    .toArray();

  const ownerKeys = [];
  for (const k of ownerKeyRows) {
    ownerKeys.push({
      id: idKey(k._id),
      key_prefix: k.key_prefix,
      label: k.label ?? "",
      created_at: k.created_at,
      is_active: k.is_active ?? 0,
      owner_email: ownerUsers.get(idKey(k.user_id))?.email ?? "",
      requests: await requestLog.countDocuments({ api_key_id: k._id }),
    });
  }

  const unmatchedColl = await coll(UNMATCHED);
  const flaggedRows = await unmatchedColl
    .find({ flagged_injection: 1, timestamp: { $gte: since(days) } })
    .sort({ _id: -1 })
    .limit(100)
    .toArray();

  const flagged = flaggedRows.map((f) => ({
    bot_type: f.bot_type,
    query_text: f.query_text,
    top_match_score: f.top_match_score ?? null,
    session_id: f.session_id ?? null,
    timestamp: f.timestamp,
  }));

  return {
    window_days: days,
    total,
    limit,
    offset,
    summary,
    entries,
    flagged_inputs: flagged,
    owner_keys: ownerKeys,
  };
}

// ---------------------------------------------------------------------------
// AI usage, by subscription
// ---------------------------------------------------------------------------

/**
 * What the AI side of the product is actually doing, split by what people pay.
 *
 * Two different things get called "AI usage" here and they are kept apart on
 * purpose:
 *
 * * **retrieval** — every `/v1/ask`: embedding the question and matching it.
 *   Every customer request is this, and it is what credits are charged for.
 * * **grounded rewording** — the optional local model on top. Off by default,
 *   opted into per bot, and the only part that generates prose.
 *
 * Grouping by plan is the answer to "are the paying accounts the ones using
 * it?", which is the question that decides whether the pricing is right.
 */
export async function aiUsage(days = 30): Promise<Doc> {
  const cutoff = since(days);
  const dateCutoff = dateSince(days);

  const usersColl = await coll(USERS);
  const users = await usersColl.find().toArray();

  const customersColl = await coll(billingDb.CUSTOMERS);
  const customerRows = await customersColl.find().toArray();
  const customers = new Map(customerRows.map((c) => [String(c.email), c]));

  const subs = await currentSubscriptions();

  const botsColl = await coll(BOTS);
  const botRows = await botsColl.find().toArray();
  const bots = new Map(botRows.map((b) => [idKey(b.user_id), b]));

  const requestsByUser = await countBy(REQUEST_LOG, { timestamp: { $gte: cutoff } }, "user_id");
  const creditsByUser = await sumBy(
    DAILY_USAGE,
    { usage_date: { $gte: dateCutoff } },
    "user_id",
    "credits_used",
  );

  const byPlan: Record<string, Doc> = {};
  for (const user of users) {
    const customer = customers.get(String(user.email));
    const sub = customer ? subs.get(idKey(customer._id)) : undefined;
    const bot = bots.get(idKey(user._id));

    const key = sub?.plan_id ?? (user.role === ROLE_OWNER ? "owner" : "none");

    let bucket = byPlan[key];
    if (bucket === undefined) {
      bucket = {
        plan_id: key,
        accounts: 0,
        entitled_accounts: 0,
        requests: 0,
        credits: 0,
        bots_with_rewording: 0,
      };
      byPlan[key] = bucket;
    }

    bucket.accounts += 1;
    bucket.entitled_accounts += isLive(sub) ? 1 : 0;
    bucket.requests += requestsByUser.get(idKey(user._id)) ?? 0;
    bucket.credits += creditsByUser.get(idKey(user._id)) ?? 0;
    bucket.bots_with_rewording += bot && bot.llm_enabled ? 1 : 0;
  }

  for (const bucket of Object.values(byPlan)) {
    bucket.credits_per_account = bucket.accounts
      ? Math.round((bucket.credits / bucket.accounts) * 10) / 10
      : 0.0;
  }

  const unmatchedColl = await coll(UNMATCHED);
  const unanswered = await unmatchedColl.find({ timestamp: { $gte: cutoff } }).toArray();

  const gaps = new Map<string, Doc>();
  for (const row of unanswered) {
    if (row.flagged_injection) continue;

    const key = String(row.query_text).trim().toLowerCase();
    let bucket = gaps.get(key);
    if (bucket === undefined) {
      bucket = {
        query_text: row.query_text,
        bot_type: row.bot_type,
        times_asked: 0,
        best_score: 0.0,
      };
      gaps.set(key, bucket);
    }
    bucket.times_asked += 1;
    bucket.best_score =
      Math.round(Math.max(bucket.best_score, row.top_match_score ?? 0) * 1000) / 1000;
  }

  const topUnanswered = [...gaps.values()]
    .sort((a, b) => b.times_asked - a.times_asked)
    .slice(0, 25);

  const byTemplate: Record<string, Doc> = {};
  for (const bot of botRows) {
    let bucket = byTemplate[bot.template_id];
    if (bucket === undefined) {
      bucket = {
        template_id: bot.template_id,
        bots: 0,
        ready: 0,
        rewording_on: 0,
        indexed_rows: 0,
        requests: 0,
      };
      byTemplate[bot.template_id] = bucket;
    }
    bucket.bots += 1;
    bucket.ready += bot.status === "ready" ? 1 : 0;
    bucket.rewording_on += bot.llm_enabled ? 1 : 0;
    bucket.indexed_rows += bot.doc_count ?? 0;
    bucket.requests += requestsByUser.get(idKey(bot.user_id)) ?? 0;
  }

  return {
    window_days: days,
    by_plan: Object.values(byPlan).sort((a, b) => b.requests - a.requests),
    rewording: {
      bots: botRows.length,
      enabled: botRows.filter((b) => b.llm_enabled).length,
      enabled_and_ready: botRows.filter((b) => b.llm_enabled && b.status === "ready").length,
    },
    retrieval: {
      unanswered: unanswered.length,
      bots_affected: new Set(unanswered.map((u) => u.bot_type)).size,
      flagged_inputs: unanswered.filter((u) => u.flagged_injection).length,
    },
    top_unanswered: topUnanswered,
    by_template: Object.values(byTemplate).sort((a, b) => b.bots - a.bots),
  };
}

// ---------------------------------------------------------------------------
// Template adoption (used by the admin router's template screen)
// ---------------------------------------------------------------------------

/** How many bots each template carries, and how many are live. */
export async function templateAdoption(): Promise<Record<string, Record<string, number>>> {
  const botsColl = await coll(BOTS);
  const rows = await botsColl.find().toArray();

  const adoption: Record<string, Record<string, number>> = {};
  for (const bot of rows) {
    let bucket = adoption[bot.template_id];
    if (bucket === undefined) {
      bucket = { bots: 0, ready: 0, rewording_on: 0 };
      adoption[bot.template_id] = bucket;
    }
    bucket.bots += 1;
    bucket.ready += bot.status === "ready" ? 1 : 0;
    bucket.rewording_on += bot.llm_enabled ? 1 : 0;
  }
  return adoption;
}

/**
 * Enable or disable both halves of an account.
 *
 * Lives here rather than in the router because it is the one admin write that
 * spans two collections, and doing half of it leaves someone who cannot sign in
 * but whose bot is still answering.
 */
export async function setAccountActive(
  customerId: unknown,
  userId: unknown,
  isActive: boolean,
): Promise<void> {
  const flag = isActive ? 1 : 0;

  const customerOid = objectId(customerId);
  if (customerOid === null) return;

  const customersColl = await coll(billingDb.CUSTOMERS);
  await customersColl.updateOne({ _id: customerOid }, { $set: { is_active: flag } });

  const userOid = objectId(userId);
  if (userOid !== null) {
    const usersColl = await coll(USERS);
    await usersColl.updateOne({ _id: userOid }, { $set: { is_active: flag } });
  }
}
