import { adminPanelApi, type AdminHealth, type AdminOverview as Overview } from "../../lib/api";

import { BarList, Loading, Panel, Stat, StatRow, money, useAdminData } from "./bits";

/**
 * The first screen: is the business growing, is the platform busy, is the
 * model up.
 *
 * Every number here is repeated in more detail on another tab. This one exists
 * so that the question "is anything wrong right now?" can be answered without
 * opening any of them.
 */
export function AdminOverview({ adminKey, days }: { adminKey: string; days: number }) {
  const { data, error, loading } = useAdminData<[Overview, AdminHealth]>(
    () =>
      Promise.all([
        adminPanelApi.overview(adminKey, days),
        adminPanelApi.health(adminKey),
      ]),
    [adminKey, days],
  );

  if (error) return <div className="error-box">{error}</div>;
  if (loading || !data) return <Loading />;

  const [overview, health] = data;
  const currency = overview.subscriptions.currency;

  return (
    <>
      <Panel title="Accounts">
        <StatRow>
          <Stat label="Customers" value={overview.customers.total} />
          <Stat
            label={`New in ${days}d`}
            value={overview.customers.new_in_window}
            tone="accent"
          />
          <Stat label="Active" value={overview.customers.active} />
          <Stat
            label="Via Google"
            value={overview.customers.via_google}
            hint="signed up with Google"
          />
          <Stat label="API keys live" value={overview.keys.active} />
        </StatRow>
      </Panel>

      <Panel title="Money">
        <StatRow>
          <Stat
            label="MRR"
            value={money(overview.subscriptions.mrr_display, currency)}
            hint="yearly plans counted as a twelfth"
            tone="accent"
          />
          <Stat label="Paying" value={overview.subscriptions.paying} />
          <Stat
            label="On trial"
            value={overview.subscriptions.on_trial}
            hint="entitled, worth nothing yet"
          />
          <Stat
            label={`Billed in ${days}d`}
            value={money(overview.revenue.in_window_display, currency)}
          />
          <Stat
            label="Billed all time"
            value={money(overview.revenue.all_time_display, currency)}
            hint={`${overview.revenue.invoices_paid} paid invoices`}
          />
        </StatRow>

        <div className="mt-4">
          <BarList
            rows={Object.entries(overview.subscriptions.by_plan).map(([plan, count]) => ({
              label: plan,
              value: count,
            }))}
            empty="Nobody has a subscription yet."
          />
        </div>
      </Panel>

      <Panel title="Usage">
        <StatRow>
          <Stat label="Credits today" value={overview.usage.credits_today} />
          <Stat label={`Credits in ${days}d`} value={overview.usage.credits_in_window} />
          <Stat label={`Requests in ${days}d`} value={overview.usage.requests_in_window} />
          <Stat label="Accounts active" value={overview.usage.active_accounts} />
        </StatRow>
      </Panel>

      <Panel
        title="Bots and the model"
        action={
          <span className={`badge ${health.llm_available ? "badge-live" : "badge-off"}`}>
            <span className="dot" />
            {health.llm_available
              ? `Model up — ${health.llm_model}`
              : "No model reachable"}
          </span>
        }
      >
        <StatRow>
          <Stat label="Bots" value={overview.bots.total} />
          <Stat label="Answering" value={overview.bots.ready} hint="sheet indexed" />
          <Stat label="Still draft" value={overview.bots.draft} />
          <Stat
            label="Rewording on"
            value={overview.bots.rewording_on}
            tone={overview.bots.rewording_on > 0 && !health.llm_available ? "warn" : undefined}
            hint={
              overview.bots.rewording_on > 0 && !health.llm_available
                ? "opted in, but no model is running"
                : undefined
            }
          />
          <Stat label="FAQ rows indexed" value={overview.bots.indexed_rows} />
        </StatRow>
      </Panel>

      <Panel title="Referrals and bonus credits">
        <StatRow>
          <Stat label="Referrals" value={overview.referrals.referrals ?? 0} />
          <Stat
            label="Rewarded payments"
            value={overview.referrals.rewarded_payments ?? 0}
            hint="top-ups that paid a referrer"
          />
          <Stat
            label="Credits paid out"
            value={overview.referrals.credits_paid ?? 0}
            tone="accent"
          />
          <Stat
            label="Bonus outstanding"
            value={overview.bonus_credits.outstanding}
            hint={`${overview.bonus_credits.granted_all_time.toLocaleString()} granted all time`}
          />
        </StatRow>
      </Panel>
    </>
  );
}
