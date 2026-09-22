/**
 * The bridge between *paying* and *being able to use the thing*.
 *
 * Port of `backend/billing/entitlements.py`.
 *
 * Two stores have to agree: `billing/db.ts` knows whether a subscription is
 * live, and `shared/apiKeys.ts` knows what an account's daily credit allowance
 * is. Nothing keeps them in sync automatically, so every transition between
 * them goes through this module — one place to read when asking "why does this
 * account have these credits?".
 *
 * It exists because the first version only wired the grant direction. Paying
 * raised your allowance to the paid tier; lapsing did nothing at all, so a
 * cancelled customer kept the paid allowance and a working key indefinitely.
 * Granting without a matching revoke is not half a feature, it is a hole.
 */

import { setDailyCreditLimit } from "../shared/apiKeys.js";
import { logger } from "../shared/logger.js";
import { expireLapsedSubscriptions } from "./db.js";

/** Raise an account's allowance to what its plan pays for. */
export async function grant(
  email: string,
  name: string,
  dailyCredits: number,
): Promise<void> {
  await setDailyCreditLimit(email, dailyCredits, name);
}

/**
 * Drop an account back to the free default allowance.
 *
 * Deliberately *not* zero and deliberately not "deactivate their keys". Someone
 * whose card expired should find their bot throttled, not silently broken in
 * production with an integration that needs rebuilding when they come back.
 * They land on the free tier, which is where they started.
 */
export async function revoke(email: string, name = ""): Promise<void> {
  await setDailyCreditLimit(email, null, name);
}

/**
 * Expire lapsed subscriptions and withdraw what they were paying for.
 *
 * Idempotent and cheap when nothing has lapsed (one indexed query that returns
 * no rows). Returns how many accounts were withdrawn.
 */
export async function sync(): Promise<number> {
  const lapsed = await expireLapsedSubscriptions();

  for (const customer of lapsed) {
    await revoke(customer.email, customer.name);
    logger.info(
      `Subscription lapsed for customer_id=${customer.id} — allowance reset ` +
        `to the free default.`,
    );
  }

  return lapsed.length;
}
