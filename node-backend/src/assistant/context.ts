/**
 * What the assistant knows about the person it is talking to.
 *
 * Port of `backend/assistant/context.py`.
 *
 * This is the difference between a generic chat window bolted onto a dashboard
 * and something worth having in one: asked *"why is my bot declining so much?"*,
 * it can actually look.
 *
 * Scope rule, and it is absolute: **everything here is derived from one
 * `customer_id`.** There is no parameter that widens it, no "all bots" branch,
 * and no code path that reaches the cross-tenant snapshot in `ops.ts` — that
 * one is owner-only and lives behind a different auth check for exactly this
 * reason.
 *
 * The block is small (a few hundred tokens) and is attached to every
 * conversation rather than being fetched when some classifier decides the
 * question is account-related. A 7B model doing intent classification would get
 * that wrong often enough to be worse than useless, and the cost of always
 * including it is a few tokens of prompt.
 */

import * as billingDb from "../billing/db.js";
import { botService } from "../bot/client.js";
import { getUsageStats, getUserByEmail, listKeysForEmail } from "../shared/apiKeys.js";
import { logger } from "../shared/logger.js";
import type { Doc } from "../shared/mongo.js";

export const MAX_GAPS = 8;

/**
 * Gather this one customer's account state.
 *
 * Never throws. A dashboard chat that 500s because the billing collection was
 * briefly unhappy is worse than one that answers without knowing the plan, so
 * every section degrades to absent rather than failing.
 */
export async function build(customer: Doc): Promise<Record<string, any>> {
  const context: Record<string, any> = { email: customer.email, name: customer.name };

  try {
    const subscription = await billingDb.getCurrentSubscription(customer.id);
    if (subscription) {
      context.plan = {
        plan_id: subscription.plan_id,
        status: subscription.status,
        entitled: billingDb.isEntitled(subscription),
        period_end: subscription.current_period_end,
      };
    }
  } catch (error) {
    logger.debug(`Assistant context: subscription unavailable. ${String(error)}`);
  }

  let user: Doc | null = null;
  try {
    user = await getUserByEmail(customer.email);
  } catch (error) {
    logger.debug(`Assistant context: account lookup failed. ${String(error)}`);
  }

  if (user === null) {
    // Signed up but never created a key — a real state, and the assistant
    // should be able to say so rather than inventing a bot.
    context.has_api_account = false;
    return context;
  }

  context.has_api_account = true;

  try {
    const usage = await getUsageStats(user.id, user.daily_credit_limit);
    context.credits = {
      used_today: usage.credits_used_today,
      remaining: usage.credits_remaining,
      // The Python version read `daily_credit_limit` here, which the stats
      // object does not carry — it is `credits_daily_limit` — so the rendered
      // line always said "of undefined". Reading the field that exists is the
      // one behavioural fix in this port, and it only affects prompt text.
      daily_limit: usage.credits_daily_limit,
    };
    context.active_keys = (await listKeysForEmail(customer.email)).length;
  } catch (error) {
    logger.debug(`Assistant context: usage unavailable. ${String(error)}`);
  }

  // The bot and its recent misses come from the bot service, which owns both.
  // One call rather than two: the gap list is only meaningful alongside the
  // thresholds it is judged against, and those live on the template.
  try {
    const account = await botService.accountBot(user.id, 30, MAX_GAPS);
    context.bot = account.bot;
    if (account.bot) context.top_unanswered = account.top_unanswered;
  } catch (error) {
    // A dashboard chat that 500s because the bot service was briefly busy is
    // worse than one that answers without knowing about the bot.
    logger.debug(`Assistant context: bot lookup failed. ${String(error)}`);
    context.bot = null;
  }

  return context;
}

/**
 * Flatten the context into the compact block the model reads.
 *
 * Terse and tabular rather than JSON: a 7B model follows a short labelled list
 * far more reliably than nested braces, and punctuation is prompt a CPU has to
 * chew through on every single turn.
 */
export function render(context: Record<string, any>): string {
  const lines: string[] = [
    `Signed in as: ${context.name || "unknown"} <${context.email}>`,
  ];

  const plan = context.plan;
  if (plan) {
    const entitled = plan.entitled ? "active" : "not active";
    lines.push(
      `Plan: ${plan.plan_id} (${plan.status}, ${entitled}, ` +
        `period ends ${plan.period_end})`,
    );
  } else {
    lines.push("Plan: none on record");
  }

  if (!context.has_api_account) {
    lines.push("API account: not created yet (no keys, no bot)");
    return lines.join("\n");
  }

  const credits = context.credits;
  if (credits) {
    lines.push(
      `Credits today: ${credits.used_today} used, ${credits.remaining} ` +
        `remaining of ${credits.daily_limit}`,
    );
  }
  if (context.active_keys !== undefined && context.active_keys !== null) {
    lines.push(`Active API keys: ${context.active_keys}`);
  }

  const bot = context.bot;
  if (!bot) {
    lines.push("Bot: not set up yet");
    return lines.join("\n");
  }

  lines.push(
    "",
    `Their bot: ${bot.template_name} template (${bot.template}), status ${bot.status}`,
    `  sheet: ${bot.sheet_filename || "none"} — ${bot.sheet_rows_indexed} rows indexed`,
    `  thresholds: answers verbatim at ${bot.strong_threshold}, ` +
      `hedges down to ${bot.near_threshold}, declines below`,
    `  grounded rewording: ${bot.grounded_rewording ? "on" : "off"}`,
  );

  const gaps = context.top_unanswered;
  if (gaps && gaps.length > 0) {
    lines.push("", "Questions their bot could not answer (last 30 days):");
    for (const gap of gaps) {
      lines.push(
        `  x${gap.times_asked} (best ${gap.best_score}, ${gap.verdict}): ${gap.question}`,
      );
    }
  } else if (gaps !== undefined && gaps !== null) {
    lines.push("", "Their bot has no unanswered questions logged in the last 30 days.");
  }

  return lines.join("\n");
}
