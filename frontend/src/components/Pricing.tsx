import { useEffect, useState } from "react";

import { api, type Pricing as PricingData } from "../lib/api";

interface Props {
  /** What the buttons do — sign up, or start a checkout from the dashboard. */
  onChoose: (planId: "monthly" | "yearly") => void;
  ctaLabel?: string;
  busyPlan?: string | null;
}

/**
 * Prices are never hardcoded here — the server owns them (apps/billing/plans.py)
 * and this renders whatever it returns, including the derived discount. Change
 * a price in one place and every surface follows.
 */
export function PricingSection({ onChoose, ctaLabel = "Get started", busyPlan }: Props) {
  const [data, setData] = useState<PricingData | null>(null);
  const [cycle, setCycle] = useState<"monthly" | "yearly">("yearly");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .pricing()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

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
            onClick={() => setCycle("monthly")}
            aria-pressed={!isYearly}
          >
            Monthly
          </button>
          <button
            data-active={isYearly}
            onClick={() => setCycle("yearly")}
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

          <button className="btn btn-secondary btn-block" onClick={() => onChoose("monthly")}>
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
            onClick={() => onChoose(cycle)}
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
