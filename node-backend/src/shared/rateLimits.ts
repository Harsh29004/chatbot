/**
 * Sliding-window counters, stored in MongoDB.
 *
 * Port of `backend/shared/rate_limits.py`.
 *
 * Login lockouts, the signup cap and the admin sign-in lockout all need the
 * same thing: "how many times has this key done this in the last N seconds?".
 * They used to keep that in a dict inside the process, which lost every count
 * on restart and gave each worker its own separate limit. Here the events live
 * in one collection that every worker shares.
 *
 * Each event carries an `expires_at`, and a TTL index on it lets MongoDB delete
 * old events on its own, so the collection never grows without bound. The TTL
 * sweep runs about once a minute, which is why reads still filter by time
 * rather than trusting that expired events are already gone.
 */

import { coll, registerIndexes } from "./mongo.js";

export const RATE_EVENTS = "rate_events";

registerIndexes(RATE_EVENTS, [
  [{ bucket: 1, key: 1, at: 1 }, { name: "bucket_key_at" }],
  [{ expires_at: 1 }, { name: "expires_at_ttl", expireAfterSeconds: 0 }],
]);

function now(): Date {
  return new Date();
}

/** Events for `key` in `bucket` within the last `windowSeconds`. */
export async function count(
  bucket: string,
  key: string,
  windowSeconds: number,
): Promise<number> {
  const since = new Date(now().getTime() - windowSeconds * 1000);
  const events = await coll(RATE_EVENTS);
  return events.countDocuments({ bucket, key, at: { $gte: since } });
}

/** Record one event, kept for as long as the window it counts toward. */
export async function record(
  bucket: string,
  key: string,
  windowSeconds: number,
): Promise<void> {
  const at = now();
  const events = await coll(RATE_EVENTS);
  await events.insertOne({
    bucket,
    key,
    at,
    expires_at: new Date(at.getTime() + windowSeconds * 1000),
  });
}

/** Forget every event for one key — what a successful sign-in does to failures. */
export async function clear(bucket: string, key: string): Promise<void> {
  const events = await coll(RATE_EVENTS);
  await events.deleteMany({ bucket, key });
}
