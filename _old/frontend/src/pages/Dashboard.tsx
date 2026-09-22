import { useCallback, useEffect, useRef, useState } from "react";

import { AnsweringMode, BotTester, SheetUploader } from "../components/BotSetup";
import { CodeBlock, Page } from "../components/Chrome";
import { GapList } from "../components/GapList";
import { PricingSection } from "../components/Pricing";
import { ReferralCard } from "../components/ReferralCard";
import { TemplatePicker } from "../components/TemplatePicker";
import { WidgetGallery } from "../components/WidgetGallery";
import { EVENTS, measure, track } from "../lib/analytics";
import {
  api,
  PUBLIC_API_ORIGIN,
  type Bot,
  type CreatedKey,
  type Dashboard as DashboardData,
} from "../lib/api";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function StatusBadge({ status, entitled }: { status: string; entitled: boolean }) {
  if (entitled && status === "trialing") {
    return (
      <span className="badge badge-warn">
        <span className="dot" /> Trial
      </span>
    );
  }
  if (entitled && status === "canceled") {
    return (
      <span className="badge badge-warn">
        <span className="dot" /> Ends soon
      </span>
    );
  }
  if (entitled) {
    return (
      <span className="badge badge-live">
        <span className="dot" /> Active
      </span>
    );
  }
  return (
    <span className="badge badge-off">
      <span className="dot" /> {status === "pending" ? "Awaiting payment" : "Inactive"}
    </span>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [bot, setBot] = useState<Bot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newKey, setNewKey] = useState<CreatedKey | null>(null);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [busyTemplate, setBusyTemplate] = useState<string | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);
  const [copied, setCopied] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.dashboard());
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  const loadBot = useCallback(async () => {
    try {
      setBot(await api.bot());
    } catch {
      // A bot only exists once the account has an API identity; the setup
      // card handles that case, so a failure here is not worth shouting about.
      setBot(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadBot();
  }, [loadBot, data?.keys.length]);

  /**
   * The state of the account, recorded once per dashboard load.
   *
   * These are the numbers that predict churn — how much of the allowance is
   * gone, whether the trial is nearly up, whether the account ever got as far
   * as creating a key. Counting them here rather than server-side means they
   * arrive attached to the same session as the behaviour they explain.
   *
   * The guard matters: without it this fires on every re-render and the
   * "credits exhausted" count becomes a count of renders.
   */
  const reported = useRef(false);
  useEffect(() => {
    if (!data || reported.current) return;
    reported.current = true;

    const { usage, subscription, keys } = data;
    const limit = usage.credits_daily_limit || 1;
    const usedFraction = usage.credits_used_today / limit;

    track(EVENTS.FEATURE_USED, {
      feature: "dashboard",
      plan_id: subscription.plan_id ?? "none",
      status: subscription.status,
      entitled: subscription.is_entitled,
      key_count: keys.length,
      credits_used_pct: Math.round(usedFraction * 100),
      has_bot: Boolean(bot),
    });

    if (usage.credits_remaining <= 0) {
      // The moment someone is blocked. If upgrades cluster after this event,
      // the limit is doing its job; if churn does, it is set too low.
      track(EVENTS.CREDITS_EXHAUSTED, {
        plan_id: subscription.plan_id ?? "none",
        daily_limit: usage.credits_daily_limit,
      });
    } else if (usedFraction >= 0.8) {
      track(EVENTS.CREDITS_LOW_WARNING, {
        plan_id: subscription.plan_id ?? "none",
        credits_remaining: usage.credits_remaining,
        used_pct: Math.round(usedFraction * 100),
      });
    }

    if (subscription.status === "trialing") {
      track(EVENTS.TRIAL_STARTED, {
        // Not the start of the trial — the observation that one is running.
        // `ends_at` is what makes the two distinguishable in a report.
        observed: true,
        ends_at: subscription.current_period_end ?? undefined,
      });
    }
  }, [data, bot]);

  const chooseTemplate = async (templateId: string) => {
    setBusyTemplate(templateId);
    setError(null);
    try {
      const previous = bot?.template_id;
      setBot(await api.selectTemplate(templateId));
      setShowTemplates(false);
      track(EVENTS.TEMPLATE_SELECTED, {
        template_id: templateId,
        previous_template_id: previous,
        // The first choice is onboarding; a later one is someone deciding
        // the industry they picked was wrong. Same click, different meaning.
        is_change: Boolean(previous && previous !== templateId),
      });
      if (!previous) {
        track(EVENTS.ONBOARDING_STEP_COMPLETED, {
          step: "template",
          template_id: templateId,
        });
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyTemplate(null);
    }
  };

  const createKey = async () => {
    setCreating(true);
    setError(null);
    try {
      const created = await api.createKey(label.trim());
      setNewKey(created);
      setLabel("");
      await load();
      // Never the key, never the label — a label is free text customers
      // routinely fill with a client's name. The count is what tells you
      // whether people get past this step.
      track(EVENTS.API_KEY_CREATED, {
        labelled: Boolean(label.trim()),
        total_keys: (data?.keys.length ?? 0) + 1,
      });
      if ((data?.keys.length ?? 0) === 0) {
        track(EVENTS.ONBOARDING_STEP_COMPLETED, { step: "first_api_key" });
      }
    } catch (err) {
      setError((err as Error).message);
      track(EVENTS.API_ERROR, {
        area: "create_key",
        reason: (err as Error).message.slice(0, 100),
      });
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (id: string, prefix: string) => {
    if (!window.confirm(`Revoke ${prefix}…? Any app using it stops working immediately.`)) {
      return;
    }
    try {
      await api.revokeKey(id);
      await load();
      track(EVENTS.API_KEY_REVOKED, {
        remaining_keys: Math.max((data?.keys.length ?? 1) - 1, 0),
      });
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const startCheckout = async (planId: "monthly" | "yearly") => {
    setBusyPlan(planId);
    setError(null);
    // GA4's own ecommerce name, with the parameters its funnel expects.
    // Using `begin_checkout` rather than something custom is what puts this
    // in the standard monetisation report instead of a bespoke exploration.
    track(EVENTS.BEGIN_CHECKOUT, {
      plan_id: planId,
      items: planId,
      from_status: data?.subscription?.status ?? "none",
    });

    try {
      const session = await measure(
        "billing_checkout_start",
        () => api.checkout(planId),
        { plan_id: planId },
      );
      if (session.requires_manual_confirmation) {
        // Development provider: nothing was charged, so say so plainly
        // rather than pretending a payment happened.
        setNotice(session.message);
      } else {
        window.location.href = session.checkout_url;
      }
    } catch (err) {
      setError((err as Error).message);
      track(EVENTS.CHECKOUT_FAILED, {
        plan_id: planId,
        reason: (err as Error).message.slice(0, 100),
      });
    } finally {
      setBusyPlan(null);
    }
  };

  const copyKey = async () => {
    if (!newKey) return;
    try {
      await navigator.clipboard.writeText(newKey.api_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      track(EVENTS.API_KEY_COPIED, {});
    } catch {
      setCopied(false);
      // Clipboard access is refused outside a user gesture and in some
      // embedded browsers. Worth counting: the key is shown exactly once, so
      // a failed copy means it is gone.
      track(EVENTS.API_ERROR, { area: "clipboard", reason: "copy_refused" });
    }
  };

  if (!data) {
    return (
      <Page>
        <main className="dash">
          <div className="wrap center">
            {error ? <div className="error-box">{error}</div> : <div className="spinner wrap-center" />}
          </div>
        </main>
      </Page>
    );
  }

  const { customer, subscription, keys, usage } = data;
  const activeKeys = keys.filter((k) => k.is_active);
  const limit = usage.credits_daily_limit || 1;
  const usedPct = Math.min(100, Math.round((usage.credits_used_today / limit) * 100));
  const remainingPct = 100 - usedPct;
  const level = remainingPct < 10 ? "low" : remainingPct < 30 ? "warn" : "ok";

  return (
    <Page>
      <main className="dash">
        <div className="wrap">
          <div className="dash-head">
            <div>
              <p className="eyebrow eyebrow-muted">Dashboard</p>
              <h1 className="h-section">{customer.name || customer.email}</h1>
            </div>
            <StatusBadge status={subscription.status} entitled={subscription.is_entitled} />
          </div>

          {error && <div className="error-box">{error}</div>}
          {notice && <div className="notice-box mb-5">{notice}</div>}

          <div className="dash-grid">
            {/* Usage --------------------------------------------------- */}
            <section className="card">
              <div className="row row-between mb-4">
                <h2 className="h-card">Today's credits</h2>
                <span className="small mono">
                  {usage.credits_used_today.toLocaleString()} /{" "}
                  {usage.credits_daily_limit.toLocaleString()}
                </span>
              </div>

              <div className="meter" role="meter" aria-valuenow={usedPct} aria-valuemin={0} aria-valuemax={100}>
                <div
                  className="meter-fill"
                  data-level={level === "ok" ? undefined : level}
                  style={{ width: `${remainingPct}%` }}
                />
              </div>
              <p className="tiny mt-3">
                {usage.credits_remaining.toLocaleString()} credits remaining · resets
                midnight IST
              </p>
              {!!usage.bonus_credits && (
                <p className="tiny mt-3 accent">
                  Includes {usage.bonus_credits.toLocaleString()} bonus credits, which
                  don't reset — they're spent after today's allowance runs out.
                </p>
              )}

              <div className="mt-5">
                <div className="stat-row">
                  <span className="stat-label">Plan</span>
                  <span className="stat-value">{subscription.plan_name ?? "None"}</span>
                </div>
                <div className="stat-row">
                  <span className="stat-label">
                    {subscription.status === "canceled" ? "Access ends" : "Renews"}
                  </span>
                  <span className="stat-value">
                    {formatDate(subscription.current_period_end)}
                  </span>
                </div>
                <div className="stat-row">
                  <span className="stat-label">Questions answered</span>
                  <span className="stat-value">
                    {(usage.total_queries_all_time ?? 0).toLocaleString()}
                  </span>
                </div>
                <div className="stat-row">
                  <span className="stat-label">Active keys</span>
                  <span className="stat-value">{activeKeys.length}</span>
                </div>
              </div>
            </section>

            {/* Quickstart ---------------------------------------------- */}
            <section className="card">
              <h2 className="h-card mb-4">Send your first question</h2>
              <CodeBlock
                title="curl"
                code={[
                  [{ text: "curl -X POST \\" }],
                  [{ text: `  ${PUBLIC_API_ORIGIN}/v1/ask \\` }],
                  [
                    { text: "  -H " },
                    {
                      text: `"X-Api-Key: ${activeKeys[0]?.key_prefix ?? "nxk_…"}…"`,
                      tone: "str" as const,
                    },
                    { text: " \\" },
                  ],
                  [
                    { text: "  -d '{" },
                    { text: '"message"', tone: "key" as const },
                    { text: ": " },
                    { text: '"How do I cancel?"', tone: "str" as const },
                    { text: ", " },
                    { text: '"session_id"', tone: "key" as const },
                    { text: ": " },
                    { text: '"u_1"', tone: "str" as const },
                    { text: "}'" },
                  ],
                ]}
              />
              <p className="tiny mt-3">
                Credit balance comes back on every response in the
                <code className="mono"> X-Credits-Remaining </code> header.
              </p>
            </section>
          </div>

          {/* Referrals ------------------------------------------------- */}
          <div className="mb-5">
            <ReferralCard />
          </div>

          {/* Bot setup ------------------------------------------------- */}
          <section className="card mb-5">
            <div className="row row-between mb-4" style={{ flexWrap: "wrap" }}>
              <div>
                <h2 className="h-card">Your bot</h2>
                <p className="tiny mt-3">
                  A template sets what your bot is allowed to talk about. Your
                  sheet supplies the answers. Anything outside both gets refused.
                </p>
              </div>
              {bot?.template && (
                <span className="badge">
                  {bot.template.icon} {bot.template.name}
                </span>
              )}
            </div>

            {/* Step 1 — template */}
            <div className="setup-step" data-done={!!bot} data-active={!bot}>
              <span className="setup-badge">{bot ? "✓" : "1"}</span>
              <div className="setup-body">
                <div className="row row-between" style={{ flexWrap: "wrap" }}>
                  <div>
                    <h3 className="h-card">Choose a template</h3>
                    <p className="small mt-3">
                      {bot?.template
                        ? `Answers about ${bot.template.scope_label}.`
                        : "Ten ready-made bots, one for each kind of business."}
                    </p>
                  </div>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setShowTemplates((v) => !v)}
                  >
                    {showTemplates ? "Close" : bot?.template ? "Change" : "Browse templates"}
                  </button>
                </div>

                {bot?.template && !showTemplates && (
                  <p className="tiny mt-3 template-decline">
                    Refuses with: “{bot.template.decline_message}”
                  </p>
                )}

                {showTemplates && (
                  <div className="mt-5">
                    <TemplatePicker
                      selectedId={bot?.template_id}
                      busyId={busyTemplate}
                      onSelect={(t) => void chooseTemplate(t.id)}
                    />
                  </div>
                )}
              </div>
            </div>

            {/* Step 2 — sheet */}
            <div
              className="setup-step"
              data-done={bot?.status === "ready"}
              data-active={!!bot && bot.status !== "ready"}
            >
              <span className="setup-badge">
                {bot?.status === "ready" ? "✓" : "2"}
              </span>
              <div className="setup-body">
                <h3 className="h-card">Upload your FAQ sheet</h3>
                {bot?.status === "ready" ? (
                  <>
                    <p className="small mt-3">
                      {bot.doc_count} question{bot.doc_count === 1 ? "" : "s"} indexed
                      from <code className="mono">{bot.sheet_filename}</code>.
                      {bot.categories.length > 0 &&
                        ` Categories: ${bot.categories.join(", ")}.`}
                    </p>
                    <details className="mt-4">
                      <summary className="small" style={{ cursor: "pointer" }}>
                        Replace the sheet
                      </summary>
                      <div className="mt-4">
                        <SheetUploader bot={bot} onUploaded={(r) => setBot(r.bot)} />
                      </div>
                    </details>
                  </>
                ) : bot ? (
                  <div className="mt-4">
                    <SheetUploader bot={bot} onUploaded={(r) => setBot(r.bot)} />
                  </div>
                ) : (
                  <p className="small mt-3">Pick a template first.</p>
                )}
              </div>
            </div>

            {/* Step 3 — key */}
            <div
              className="setup-step"
              data-done={activeKeys.length > 0}
              data-active={bot?.status === "ready" && activeKeys.length === 0}
            >
              <span className="setup-badge">
                {activeKeys.length > 0 ? "✓" : "3"}
              </span>
              <div className="setup-body">
                <h3 className="h-card">Create an API key</h3>
                <p className="small mt-3">
                  {activeKeys.length > 0
                    ? `${activeKeys.length} active key${
                        activeKeys.length === 1 ? "" : "s"
                      }. Point your app at POST /v1/ask with the key in X-Api-Key.`
                    : "Create one below, then call POST /v1/ask with it."}
                </p>
              </div>
            </div>

            {/* Tester */}
            {bot?.status === "ready" && (
              <div className="mt-5" style={{ paddingTop: "var(--s5)", borderTop: "1px solid var(--line)" }}>
                <h3 className="h-card mb-4">Try it</h3>
                <AnsweringMode bot={bot} onChange={setBot} />
                <BotTester bot={bot} />
              </div>
            )}
          </section>

          {/* Go live ---------------------------------------------------- */}
          <section className="card mb-5" id="install">
            <div className="mb-5">
              <h2 className="h-card">Put it on your website</h2>
              <p className="tiny mt-3">
                Pick a ready-made design and download its install package, or skip
                the widget and call the API from your own backend. Either way, your
                API key is what switches it on.
              </p>
            </div>
            <WidgetGallery
              keyPrefix={activeKeys[0]?.key_prefix}
              botReady={bot?.status === "ready"}
            />
          </section>

          {/* Keys ------------------------------------------------------ */}
          <section className="card card-flush mb-5">
            <div className="row row-between" style={{ padding: "var(--s5)" }}>
              <div>
                <h2 className="h-card">API keys</h2>
                <p className="tiny mt-3">
                  All keys share one credit pool. Use separate keys per
                  environment so you can revoke one without breaking the rest.
                </p>
              </div>
            </div>

            {newKey && (
              <div style={{ padding: "0 var(--s5) var(--s5)" }}>
                <div className="notice-box">
                  <strong>{newKey.message}</strong>
                  <div className="key-reveal">
                    <code className="key-value">{newKey.api_key}</code>
                    <button className="btn btn-secondary btn-sm" onClick={copyKey}>
                      {copied ? "Copied" : "Copy"}
                    </button>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => setNewKey(null)}
                    >
                      Done
                    </button>
                  </div>
                </div>
              </div>
            )}

            <div className="divider" />

            {activeKeys.length === 0 ? (
              <div className="empty">
                <p className="small">No keys yet.</p>
              </div>
            ) : (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Key</th>
                      <th>Label</th>
                      <th>Created</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {activeKeys.map((key) => (
                      <tr key={key.id}>
                        <td>
                          <code className="mono" style={{ color: "var(--bone)" }}>
                            {key.key_prefix}
                            <span style={{ color: "var(--faint)" }}>…</span>
                          </code>
                        </td>
                        <td>{key.label || <span className="tiny">—</span>}</td>
                        <td className="tiny">{formatDate(key.created_at)}</td>
                        <td style={{ textAlign: "right" }}>
                          <button
                            className="btn btn-danger btn-sm"
                            onClick={() => revoke(key.id, key.key_prefix)}
                          >
                            Revoke
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="divider" />

            <div style={{ padding: "var(--s5)" }}>
              {subscription.is_entitled ? (
                <div className="row gap-3" style={{ flexWrap: "wrap" }}>
                  <input
                    className="input"
                    style={{ maxWidth: 260 }}
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="Label (e.g. production)"
                    maxLength={60}
                  />
                  <button
                    className="btn btn-primary"
                    onClick={createKey}
                    disabled={creating}
                  >
                    {creating ? <span className="spinner" /> : "Create key"}
                  </button>
                </div>
              ) : (
                <p className="small">
                  Your plan is not active, so new keys can't be issued. Pick a plan
                  below to continue.
                </p>
              )}
            </div>
          </section>

          {/* Gaps ------------------------------------------------------ */}
          {bot?.status === "ready" && (
            <section className="card card-flush mb-5">
              <div style={{ padding: "var(--s5)" }}>
                <h2 className="h-card">What your bot couldn't answer</h2>
                <p className="tiny mt-3">
                  Real questions from the last 30 days that your sheet doesn't
                  cover. Add them and the bot answers them next time.
                </p>
              </div>
              <div className="divider" />
              <div style={{ padding: "var(--s5)" }}>
                <GapList />
              </div>
            </section>
          )}

          {/* Plan ------------------------------------------------------ */}
          <section className="section-tight" id="plan">
            <div className="center mb-6">
              <p className="eyebrow eyebrow-muted">
                {subscription.is_entitled ? "Change plan" : "Choose a plan"}
              </p>
              <h2 className="h-section">
                {subscription.status === "trialing"
                  ? "Keep it running after the trial."
                  : "Your plan"}
              </h2>
            </div>
            <PricingSection
              onChoose={startCheckout}
              ctaLabel="Continue to checkout"
              busyPlan={busyPlan}
            />
          </section>
        </div>
      </main>
    </Page>
  );
}
