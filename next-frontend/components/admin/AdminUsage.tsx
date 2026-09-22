"use client";

import { adminPanelApi } from "../../lib/api-client";
import type { AdminUsage as Usage } from "../../lib/types";

import { BarList, DayChart, Empty, Loading, Panel, Stat, StatRow, useAdminData } from "./bits";

/** Credits and requests: over time, by account, and by endpoint. */
export function AdminUsage({ adminKey, days }: { adminKey: string; days: number }) {
  const { data, error, loading } = useAdminData<Usage>(
    () => adminPanelApi.usage(adminKey, days),
    [adminKey, days],
  );

  if (error) return <div className="error-box">{error}</div>;
  if (loading || !data) return <Loading />;

  return (
    <>
      <Panel title={`Last ${days} days`}>
        <StatRow>
          <Stat label="Requests" value={data.totals.requests} />
          <Stat label="Credits spent" value={data.totals.credits} tone="accent" />
          <Stat label="Accounts active" value={data.totals.active_accounts} />
          <Stat
            label="Credits/day"
            value={Math.round(data.totals.credits / Math.max(1, days))}
            hint="average across the window"
          />
        </StatRow>

        <h3 className="h-card mt-5 mb-3">Credits per day</h3>
        <DayChart
          points={data.per_day.map((day) => ({ date: day.date, value: day.credits }))}
          label="credits"
        />

        <h3 className="h-card mt-5 mb-3">Requests per day</h3>
        <DayChart
          points={data.per_day.map((day) => ({ date: day.date, value: day.requests }))}
          label="requests"
        />
      </Panel>

      <Panel title="Heaviest accounts">
        {data.top_accounts.length === 0 ? (
          <Empty>Nobody has spent a credit in this window.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Role</th>
                  <th>Credits</th>
                  <th>Requests</th>
                </tr>
              </thead>
              <tbody>
                {data.top_accounts.map((account) => (
                  <tr key={account.user_id}>
                    <td>
                      {account.email}
                      <div className="tiny muted">{account.name || "—"}</div>
                    </td>
                    <td className="tiny">{account.role}</td>
                    <td className="mono tiny accent">{account.credits.toLocaleString()}</td>
                    <td className="mono tiny">{account.requests.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="By endpoint">
        <BarList
          rows={data.by_endpoint.map((endpoint) => ({
            label: endpoint.endpoint,
            value: endpoint.requests,
            hint: `(${endpoint.credits} credits, ${endpoint.accounts} accounts)`,
          }))}
          empty="No API traffic in this window."
        />
      </Panel>
    </>
  );
}
