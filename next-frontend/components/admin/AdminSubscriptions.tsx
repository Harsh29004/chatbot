"use client";

import { adminPanelApi } from "../../lib/api-client";
import type { AdminSubscriptions as Subs } from "../../lib/types";

import { BarList, Empty, Loading, Panel, Stat, StatRow, when, useAdminData } from "./bits";

/**
 * The subscription book.
 *
 * "Count subscriptions" is really three questions — how many are live, what
 * are they worth, and how many are leaving — so all three are on one screen
 * rather than behind a filter.
 */
export function AdminSubscriptions({ adminKey, days }: { adminKey: string; days: number }) {
  const { data, error, loading } = useAdminData<Subs>(
    () => adminPanelApi.subscriptions(adminKey, days),
    [adminKey, days],
  );

  if (error) return <div className="error-box">{error}</div>;
  if (loading || !data) return <Loading />;

  return (
    <>
      <Panel title="Where the book stands">
        <StatRow>
          <Stat label="Paying" value={data.counts.paying} tone="accent" />
          <Stat
            label="Entitled"
            value={data.counts.entitled}
            hint="paying plus live trials"
          />
          <Stat label="With a subscription" value={data.counts.customers_with_a_subscription} />
          <Stat label="MRR" value={data.mrr_display} hint="yearly counted as a twelfth" />
          <Stat
            label="Trial → paid"
            value={`${data.trial_conversion.percent}%`}
            hint={`${data.trial_conversion.converted} of ${data.trial_conversion.trialled}`}
          />
          <Stat
            label={`Churn in ${days}d`}
            value={data.churn_in_window.canceled + data.churn_in_window.expired}
            hint={`${data.churn_in_window.canceled} cancelled, ${data.churn_in_window.expired} lapsed`}
            tone={data.churn_in_window.expired > 0 ? "warn" : undefined}
          />
        </StatRow>
      </Panel>

      <Panel title="By plan">
        <BarList
          rows={data.by_plan.map((plan) => ({
            label: plan.plan_id,
            value: plan.count,
            hint: `(${plan.entitled} live)`,
          }))}
        />
      </Panel>

      <Panel title="By status">
        <BarList
          rows={Object.entries(data.counts.by_status).map(([status, count]) => ({
            label: status,
            value: count,
          }))}
        />
      </Panel>

      <Panel title="Revenue by month">
        {data.revenue_by_month.length === 0 ? (
          <Empty>No invoices have been paid yet.</Empty>
        ) : (
          <BarList
            rows={data.revenue_by_month.map((month) => ({
              label: month.month,
              value: month.amount_cents / 100,
              hint: `(${month.invoices} invoices)`,
            }))}
          />
        )}
      </Panel>

      <Panel title="Every current subscription">
        {data.subscriptions.length === 0 ? (
          <Empty>Nobody has subscribed yet.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Plan</th>
                  <th>Status</th>
                  <th>Period ends</th>
                  <th>Monthly value</th>
                </tr>
              </thead>
              <tbody>
                {data.subscriptions.map((sub) => (
                  <tr key={sub.id}>
                    <td>
                      {sub.email}
                      <div className="tiny muted">{sub.name || "—"}</div>
                    </td>
                    <td className="tiny">{sub.plan_id}</td>
                    <td>
                      <span className={`badge ${sub.is_entitled ? "badge-live" : "badge-off"}`}>
                        <span className="dot" />
                        {sub.status}
                      </span>
                    </td>
                    <td className="tiny">{when(sub.current_period_end)}</td>
                    <td className="mono tiny">
                      {sub.monthly_value_cents
                        ? (sub.monthly_value_cents / 100).toFixed(2)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
