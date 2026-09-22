/**
 * Client-side crash and error reports.
 *
 * Port of `backend/shared/telemetry_store.py`.
 *
 * Why this exists at all
 * ----------------------
 * Firebase Crashlytics does not ship a Web SDK — it is Android, iOS, Flutter
 * and Unity only. For a browser app the crash-reporting half has to be built,
 * and this is the server end of it: the page catches what it can
 * (`window.onerror`, unhandled promise rejections, the React error boundary),
 * logs a GA4 `exception` event so the rate shows up in the Firebase console,
 * and posts the stack trace here so there is somewhere to read *what actually
 * broke*.
 *
 * An `exception` event in Analytics tells you a crash happened and roughly
 * where. It does not carry a stack trace, and Google truncates its parameters.
 * That is the gap this fills.
 *
 * What is deliberately not stored
 * -------------------------------
 * No cookies, no session token, no request bodies. The customer id is recorded
 * only when the browser was already signed in, because "which accounts hit
 * this" is the first question worth asking about a crash and an anonymous pile
 * of stack traces cannot answer it.
 *
 * Everything is length-capped on write. This endpoint is necessarily
 * unauthenticated — a crash on the sign-in page still needs reporting — so it
 * is treated as hostile input throughout, and TTL-expired so a bad deploy
 * cannot fill the disk.
 */

import * as config from "../config.js";
import { coll, documents, registerIndexes, type Doc } from "./mongo.js";

export const COLLECTION = "client_errors";

// Field caps. A minified stack from a bundled SPA is long, so 8 KB rather than
// something tidier — truncating the frame that names the cause would defeat the
// point of collecting it at all.
const MAX_MESSAGE = 500;
const MAX_STACK = 8000;
const MAX_URL = 500;
const MAX_UA = 300;
const MAX_COMPONENT_STACK = 4000;
const MAX_CONTEXT_KEYS = 20;
const MAX_CONTEXT_VALUE = 300;

registerIndexes(COLLECTION, [
  // The two reads that exist: newest-first overall, and newest-first for one
  // release when triaging whether a deploy caused it.
  [{ at: -1 }, { name: "by_time" }],
  [{ release: 1, at: -1 }, { name: "by_release_time" }],
  // Grouping identical crashes is the whole job of a crash reporter, and
  // fingerprint is what groups them.
  [{ fingerprint: 1, at: -1 }, { name: "by_fingerprint_time" }],
  // MongoDB expires these on its own. Without it one broken deploy writes until
  // the disk is full.
  [{ expires_at: 1 }, { name: "expires_ttl", expireAfterSeconds: 0 }],
]);

/** Coerce to a bounded string. Anything unstringifiable becomes empty. */
function clip(value: unknown, limit: number): string {
  if (value === null || value === undefined) return "";
  try {
    return String(value).slice(0, limit);
  } catch {
    // A hostile toString must not crash logging.
    return "";
  }
}

/**
 * Keep a bounded, flat, string-valued copy of whatever the page attached.
 *
 * Flattened rather than stored as-is because nested client-controlled documents
 * are how a log collection grows keys nobody indexed, and because a dotted or
 * `$`-prefixed key from a browser is a write MongoDB rejects.
 */
function cleanContext(context: unknown): Record<string, string> {
  if (typeof context !== "object" || context === null || Array.isArray(context)) {
    return {};
  }

  const cleaned: Record<string, string> = {};
  for (const [key, value] of Object.entries(context).slice(0, MAX_CONTEXT_KEYS)) {
    const name = clip(key, 40).replace(/\./g, "_").replace(/\$/g, "_");
    if (name) cleaned[name] = clip(value, MAX_CONTEXT_VALUE);
  }
  return cleaned;
}

export interface ErrorReport {
  message: string;
  stack?: string;
  kind?: string;
  fingerprint?: string;
  route?: string;
  url?: string;
  userAgent?: string;
  release?: string;
  componentStack?: string;
  fatal?: boolean;
  customerId?: string | null;
  clientIp?: string;
  context?: unknown;
}

/**
 * Store one client-side error.
 *
 * Never throws. A failure to log a crash must not itself become a crash, and
 * the caller is an endpoint whose entire job is to absorb bad news.
 */
export async function recordError(report: ErrorReport): Promise<void> {
  if (!config.TELEMETRY_ENABLED) return;

  const now = new Date();
  try {
    const errors = await coll(COLLECTION);
    await errors.insertOne({
      at: now,
      kind: clip(report.kind ?? "error", 40),
      message: clip(report.message, MAX_MESSAGE),
      stack: clip(report.stack ?? "", MAX_STACK),
      component_stack: clip(report.componentStack ?? "", MAX_COMPONENT_STACK),
      // Computed by the browser so identical crashes group even when the
      // message carries a varying id or number.
      fingerprint: clip(report.fingerprint ?? "", 64),
      route: clip(report.route ?? "", MAX_URL),
      url: clip(report.url ?? "", MAX_URL),
      user_agent: clip(report.userAgent ?? "", MAX_UA),
      release: clip(report.release ?? "", 64),
      fatal: Boolean(report.fatal),
      customer_id: clip(report.customerId, 64) || null,
      client_ip: clip(report.clientIp ?? "", 64),
      context: cleanContext(report.context),
      expires_at: new Date(now.getTime() + config.TELEMETRY_RETENTION_DAYS * 86_400_000),
    });
  } catch {
    // Logging must never break the caller.
  }
}

/** The newest reports, optionally narrowed to one release. */
export async function recentErrors(limit = 100, release = ""): Promise<Doc[]> {
  const query: Record<string, unknown> = {};
  if (release) query.release = release;

  const errors = await coll(COLLECTION);
  const rows = await errors
    .find(query)
    .sort({ at: -1 })
    .limit(Math.max(1, Math.min(limit, 500)))
    .toArray();
  return documents(rows);
}

// How many recent reports one grouping pass will read. The collection is
// already bounded by the TTL and the per-IP rate limit, but "already bounded"
// is not the same as "small", and an admin page must not pull a month of a bad
// week into memory to draw a table of twenty rows.
const GROUPING_SCAN_LIMIT = 5000;

export interface ErrorGroup {
  fingerprint: string;
  count: number;
  message: string;
  kind: string;
  route: string;
  release: string;
  stack: string;
  fatal: boolean;
  first_seen: string | Date | null;
  last_seen: string | Date | null;
  affected_users?: number;
}

/**
 * Distinct crashes, most frequent first — the view worth looking at.
 *
 * A raw feed of a thousand reports is one bug a thousand times. Grouping by
 * fingerprint turns it back into "these six things are broken, and this one is
 * hitting four hundred people".
 *
 * Grouped in application code rather than with an aggregation pipeline, for the
 * same reason the Python version was: the natural pipeline needs `$addToSet`
 * with `$setDifference` to count distinct users, which not every deployment
 * (and no test double) implements consistently. Reading at most
 * `GROUPING_SCAN_LIMIT` capped documents and counting them here is a few
 * milliseconds either way, and it behaves identically everywhere.
 */
export async function errorGroups(days = 7, limit = 50): Promise<ErrorGroup[]> {
  const since = new Date(Date.now() - days * 86_400_000);

  const errors = await coll(COLLECTION);
  const rows = await errors
    .find(
      { at: { $gte: since } },
      {
        // Stacks are up to 8 KB each and only one per group is kept, but they
        // have to be read to keep that one. Everything not shown in the grouped
        // view is left on the server.
        projection: { component_stack: 0, context: 0, user_agent: 0, client_ip: 0 },
      },
    )
    .sort({ at: -1 })
    .limit(GROUPING_SCAN_LIMIT)
    .toArray();

  const groups = new Map<string, ErrorGroup>();
  const users = new Map<string, Set<string>>();

  for (const row of rows) {
    const key: string = row.fingerprint || String(row.message ?? "").slice(0, 64);
    const at: Date | null = row.at ?? null;

    let group = groups.get(key);
    if (group === undefined) {
      // The cursor is newest-first, so the first document seen for a
      // fingerprint is the most recent one — which is the version of the
      // message and stack worth showing.
      group = {
        fingerprint: key,
        count: 0,
        message: row.message ?? "",
        kind: row.kind ?? "",
        route: row.route ?? "",
        release: row.release ?? "",
        stack: row.stack ?? "",
        fatal: false,
        first_seen: at,
        last_seen: at,
      };
      groups.set(key, group);
      users.set(key, new Set());
    }

    group.count += 1;
    group.fatal = group.fatal || Boolean(row.fatal);

    if (at !== null) {
      const first = group.first_seen as Date | null;
      const last = group.last_seen as Date | null;
      if (first === null || at < first) group.first_seen = at;
      if (last === null || at > last) group.last_seen = at;
    }

    // Distinct accounts, not reports. One person reloading a broken page twenty
    // times is one affected user, and anonymous reports are not users at all.
    if (row.customer_id) {
      users.get(key)!.add(row.customer_id);
    }
  }

  const ranked = [...groups.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, Math.max(1, Math.min(limit, 200)));

  for (const group of ranked) {
    group.affected_users = users.get(group.fingerprint)?.size ?? 0;
    for (const field of ["first_seen", "last_seen"] as const) {
      const value = group[field];
      if (value instanceof Date) group[field] = value.toISOString();
    }
  }

  return ranked;
}
