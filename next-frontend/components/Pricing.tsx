"use client";

import { useEffect, useState } from "react";

import { EVENTS, track } from "../lib/analytics";
import { api } from "../lib/api-client";
import type { Pricing as PricingData } from "../lib/types";

interface Props {
  /** What the buttons do — sign up, or start a checkout from the dashboard. */
  onChoose: (planId: "monthly" | "yearly") => void;
  ctaLabel?: string;
  busyPlan?: string | null;
  /**
   * Prices fetched on the server, so the rendered HTML already contains them.
   * Without this the page ships with an empty pricing table and fills it in a
   * round trip later, which is both a layout shift and — for the landing page
   * — prices a crawler never sees.
   */
  initialPricing?: PricingData | null;
}

/**
 * Prices are never hardcoded here — the server owns them (apps/billing/plans.py)
 * and this renders whatever it returns, including the derived discount. Change
 * a price in one place and every surface follows.
 */
export function PricingSection({
  onChoose,
  ctaLabel = "Get started",
  busyPlan,
  initialPricing = null,
}: Props) {
  const [data, setData] = useState<PricingData | null>(initialPricing);
  const [cycle, setCycle] = useState<"monthly" | "yearly">("yearly");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Already rendered with server data — fetching it again would only fire a
    // second, identical request. The analytics event still needs to happen,
    // because it means "the prices were seen", not "the prices were fetched".
    if (initialPricing) {
      track(EVENTS.VIEW_ITEM_LIST, {
        item_list_name: "pricing",
        currency: initialPricing.currency,
        plan_count: initialPricing.plans.length,
      });
      track(EVENTS.PRICING_VIEWED, { currency: initialPricing.currency });
      return;
    }

    api
      .pricing()
      .then((loaded) => {
        setData(loaded);
        // `view_item_list` is the ecommerce event for "saw the prices", and
        // it is the denominator for every conversion rate below it. Fired
        // on load rather than on mount so it means the prices were actually
        // rendered, not that the component started fetching them.
        track(EVENTS.VIEW_ITEM_LIST, {
          item_list_name: "pricing",
          currency: loaded.currency,
          plan_count: loaded.plans.length,
        });
        track(EVENTS.PRICING_VIEWED, { currency: loaded.currency });
      })
      .catch((e: Error) => {
        setError(e.message);
        track(EVENTS.API_ERROR, { area: "pricing", reason: e.message.slice(0, 100) });
      });
  }, []);

  /**
   * The monthly/yearly switch.
   *
   * Worth its own event: how many people flip to yearly before choosing is
   * the clearest read on whether the discount is persuading anyone, and it
   * cannot be recovered from the purchase alone.
   */
  const chooseCycle = (next: "monthly" | "yearly") => {
    if (next === cycle) return;
    setCycle(next);
    track(EVENTS.PRICING_INTERVAL_TOGGLED, { interval: next, from: cycle });
  };

  if (error) {
    return (
      <div className="card center">
        <p className="small">Couldn't load pricing: {error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="center" style={{ padding: "var(--s8)" }}>
        <div className="spinner wrap-center" />
      </div>
    );
  }

  const trial = data.plans.find((p) => p.id === "trial");
  const paid = data.plans.find((p) => p.id === cycle);
  if (!paid) return null;

  const isYearly = cycle === "yearly";

  return (
    <>
      <div className="center">
        <div className="price-toggle" role="group" aria-label="Billing period">
          <button
            data-active={!isYearly}
            onClick={() => chooseCycle("monthly")}
            aria-pressed={!isYearly}
          >
            Monthly
          </button>
          <button
            data-active={isYearly}
            onClick={() => chooseCycle("yearly")}
            aria-pressed={isYearly}
          >
            Yearly
            <span className="save-pill">−{data.yearly_discount_percent}%</span>
          </button>
        </div>
      </div>

      <div className="price-cards">
        {/* Trial */}
        <div className="card price-card">
          <h3 className="h-card">{trial?.name ?? "Trial"}</h3>
          <p className="small">{trial?.tagline}</p>

          <div className="price-amount">
            <span className="price-value">Free</span>
          </div>
          <p className="small">for {data.trial_days} days</p>

          <ul className="feature-list">
            <li>{trial?.daily_credits.toLocaleString()} credits per day</li>
            <li>Both customer and partner bots</li>
            <li>Full API access, no card required</li>
            <li>Your own FAQ sheet, indexed on upload</li>
          </ul>

          <button
            className="btn btn-secondary btn-block"
            onClick={() => {
              track(EVENTS.PRICING_PLAN_SELECTED, { plan_id: "monthly", from: "trial_card" });
              onChoose("monthly");
            }}
          >
            Start the trial
          </button>
        </div>

        {/* Paid */}
        <div className="card price-card price-card-featured">
          {isYearly && (
            <span className="price-tag">
              save ${data.yearly_savings_display}/yr
            </span>
          )}
          <h3 className="h-card">{paid.name}</h3>
          <p className="small">{paid.tagline}</p>

          <div className="price-amount">
            <span className="price-currency">$</span>
            <span className="price-value">
              {isYearly ? data.yearly_effective_monthly_display : paid.price_display}
            </span>
            <span className="price-period">/month</span>
          </div>
          <p className="small">
            {isYearly
              ? `Billed $${paid.price_display} once a year`
              : "Billed monthly, cancel anytime"}
          </p>

          <ul className="feature-list">
            <li>{paid.daily_credits.toLocaleString()} credits per day</li>
            <li>Up to 10 API keys, one shared credit pool</li>
            <li>Usage dashboard and per-key revocation</li>
            <li>Unmatched-question log for improving your sheet</li>
          </ul>

          <button
            className="btn btn-primary btn-block"
            onClick={() => {
              track(EVENTS.PRICING_PLAN_SELECTED, {
                plan_id: cycle,
                from: "paid_card",
                price_cents: paid.price_cents,
                currency: data.currency,
              });
              onChoose(cycle);
            }}
            disabled={busyPlan === cycle}
          >
            {busyPlan === cycle ? <span className="spinner" /> : ctaLabel}
          </button>
        </div>
      </div>

      <p className="small center mt-5">
        Credits are per account, not per key. Extra keys separate your
        environments — they don't buy extra capacity.
      </p>
    </>
  );
}
