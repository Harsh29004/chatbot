/**
 * Plan catalogue and pricing maths.
 *
 * Port of `backend/billing/plans.py`.
 *
 * This module is the single source of truth for what we charge. The frontend
 * renders whatever {@link listPlans} returns, so prices are never hardcoded in
 * two places — change them here and the pricing page follows.
 *
 * Money is handled in integer cents everywhere. Never floats: 0.1 + 0.2 is not
 * 0.3, and that lands in an invoice eventually.
 */

import {
  CURRENCY,
  PAID_DAILY_CREDITS,
  TRIAL_DAILY_CREDITS,
  TRIAL_DAYS,
} from "../config.js";

// Re-exported so callers read pricing settings from the pricing module, which
// is where plans.py kept them.
export { CURRENCY };

export type PlanId = "monthly" | "yearly" | "trial";

export interface Plan {
  id: PlanId;
  name: string;
  price_cents: number;
  interval: "month" | "year" | "trial";
  interval_days: number;
  daily_credits: number;
  tagline: string;
}

export const PLANS: Record<string, Plan> = {
  trial: {
    id: "trial",
    name: "Trial",
    price_cents: 0,
    interval: "trial",
    interval_days: TRIAL_DAYS,
    daily_credits: TRIAL_DAILY_CREDITS,
    tagline: `${TRIAL_DAYS} days, no card needed`,
  },
  monthly: {
    id: "monthly",
    name: "Monthly",
    price_cents: 1500, // $15.00
    interval: "month",
    interval_days: 30,
    daily_credits: PAID_DAILY_CREDITS,
    tagline: "Cancel anytime",
  },
  yearly: {
    id: "yearly",
    name: "Yearly",
    price_cents: 14000, // $140.00
    interval: "year",
    interval_days: 365,
    daily_credits: PAID_DAILY_CREDITS,
    tagline: "Two months and change, free",
  },
};

export const BILLABLE_PLAN_IDS = ["monthly", "yearly"] as const;

export function getPlan(planId: string): Plan | null {
  return PLANS[planId] ?? null;
}

// ---------------------------------------------------------------------------
// Pricing maths — derived, never hand-typed
// ---------------------------------------------------------------------------

/** Cents saved per year by paying yearly instead of monthly. */
export function yearlySavingsCents(): number {
  const twelveMonths = PLANS.monthly.price_cents * 12;
  return twelveMonths - PLANS.yearly.price_cents;
}

/**
 * Whole-percent discount of the yearly plan vs. 12x monthly (rounded down).
 *
 * Rounded down so the number we advertise is never larger than the discount a
 * customer actually receives. Python's `//` on positive integers is
 * `Math.floor` of the division, which is what this reproduces.
 */
export function yearlyDiscountPercent(): number {
  const twelveMonths = PLANS.monthly.price_cents * 12;
  if (twelveMonths === 0) return 0;
  return Math.floor((yearlySavingsCents() * 100) / twelveMonths);
}

/**
 * What the yearly plan works out to per month, rounded to the nearest cent.
 *
 * Python's `round()` uses banker's rounding (ties to even) and JavaScript's
 * rounds half away from zero. For the one value this is called on — 14000/12 =
 * 1166.67 — they agree, and any future price that lands exactly on a half-cent
 * would differ by one cent in a display string only.
 */
export function yearlyEffectiveMonthlyCents(): number {
  return Math.round(PLANS.yearly.price_cents / 12);
}

/** Render cents as a plain price string: 1500 -> '15', 1167 -> '11.67'. */
export function formatCents(cents: number): string {
  if (cents % 100 === 0) return String(Math.trunc(cents / 100));
  return (cents / 100).toFixed(2);
}

export interface PublicPlan {
  id: string;
  name: string;
  price_cents: number;
  price_display: string;
  currency: string;
  interval: string;
  daily_credits: number;
  tagline: string;
}

export function planPublicDict(plan: Plan): PublicPlan {
  return {
    id: plan.id,
    name: plan.name,
    price_cents: plan.price_cents,
    price_display: formatCents(plan.price_cents),
    currency: CURRENCY,
    interval: plan.interval,
    daily_credits: plan.daily_credits,
    tagline: plan.tagline,
  };
}

/** The full pricing payload the frontend renders. */
export function listPlans() {
  return {
    currency: CURRENCY,
    plans: (["trial", "monthly", "yearly"] as const).map((id) => planPublicDict(PLANS[id])),
    yearly_savings_cents: yearlySavingsCents(),
    yearly_savings_display: formatCents(yearlySavingsCents()),
    yearly_discount_percent: yearlyDiscountPercent(),
    yearly_effective_monthly_display: formatCents(yearlyEffectiveMonthlyCents()),
    trial_days: TRIAL_DAYS,
  };
}
