import { adminPanelApi } from "../../lib/api-client";
import type { AdminReferrals as Referrals } from "../../lib/types";

import { Empty, Loading, Panel, Stat, StatRow, when, useAdminData } from "./bits";

/** The referral programme: what it has cost, and who is driving it. */
export function AdminReferrals({ adminKey }: { adminKey: string }) {
  const { data, error, loading } = useAdminData<Referrals>(
    () => adminPanelApi.referrals(adminKey),
    [adminKey],
  );

  if (error) return <div className="error-box">{error}</div>;
  if (loading || !data) return <Loading />;

  return (
    <>
      <Panel
        title="Programme"
        action={
          <span className="tiny muted">
            {data.rewards.referrer_signup_credits} on signup ·{" "}
            {data.rewards.referred_signup_credits} to the newcomer ·{" "}
            {data.rewards.topup_credits} per top-up
          </span>
        }
      >
        <StatRow>
          <Stat label="Referrals" value={data.totals.referrals ?? 0} />
          <Stat
            label="Invites sent"
            value={data.totals.invites_sent ?? 0}
            hint={`${data.totals.invites_claimed ?? 0} claimed`}
          />
          <Stat
            label="Credits paid"
            value={data.totals.credits_paid ?? 0}
            tone="accent"
          />
          <Stat
            label="Paid on top-ups"
            value={data.totals.credits_paid_on_topups ?? 0}
            hint={`${data.totals.rewarded_payments ?? 0} payments rewarded`}
          />
        </StatRow>
      </Panel>

      <Panel title="Who is referring">
        {data.leaderboard.length === 0 ? (
          <Empty>Nobody has referred anyone yet.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Referred</th>
                  <th>Credits earned</th>
                </tr>
              </thead>
              <tbody>
                {data.leaderboard.map((row) => (
                  <tr key={row.customer_id}>
                    <td>
                      {row.email}
                      <div className="tiny muted">{row.name || "—"}</div>
                    </td>
                    <td className="mono tiny">{row.referred_count}</td>
                    <td className="mono tiny accent">{row.credits_earned.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Recent payouts">
        {data.recent_rewards.length === 0 ? (
          <Empty>No credits have been paid out.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Paid to</th>
                  <th>Why</th>
                  <th>Credits</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_rewards.map((reward) => (
                  <tr key={reward.id}>
                    <td className="tiny">{when(reward.created_at)}</td>
                    <td className="tiny">{reward.email}</td>
                    <td className="tiny">
                      {reward.kind === "topup" ? "their referral paid" : "signup"}
                      <span className="muted"> · {reward.role}</span>
                      {reward.invoice_id ? (
                        <span className="muted"> · invoice #{reward.invoice_id}</span>
                      ) : null}
                    </td>
                    <td className="mono tiny accent">+{reward.credits}</td>
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
