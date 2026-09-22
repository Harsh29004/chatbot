import { adminPanelApi } from "../../lib/api-client";
import type { AdminAiUsage } from "../../lib/types";

import { BarList, Empty, Loading, Panel, Stat, StatRow, useAdminData } from "./bits";

/**
 * AI usage, split by what people pay.
 *
 * Two different things are shown apart on purpose. *Retrieval* is every
 * question asked — matching, and what it failed to match. *Grounded
 * rewording* is the optional local model on top, which most bots never turn
 * on. Adding them together would produce a number that means nothing.
 */
export function AdminAi({ adminKey, days }: { adminKey: string; days: number }) {
  const { data, error, loading } = useAdminData<AdminAiUsage>(
    () => adminPanelApi.aiUsage(adminKey, days),
    [adminKey, days],
  );

  if (error) return <div className="error-box">{error}</div>;
  if (loading || !data) return <Loading />;

  return (
    <>
      <Panel title="Usage by plan">
        {data.by_plan.length === 0 ? (
          <Empty>No accounts yet.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Plan</th>
                  <th>Accounts</th>
                  <th>Paying</th>
                  <th>Requests</th>
                  <th>Credits</th>
                  <th>Credits/account</th>
                  <th>Rewording on</th>
                </tr>
              </thead>
              <tbody>
                {data.by_plan.map((plan) => (
                  <tr key={plan.plan_id}>
                    <td className="tiny">{plan.plan_id}</td>
                    <td className="mono tiny">{plan.accounts}</td>
                    <td className="mono tiny">{plan.entitled_accounts}</td>
                    <td className="mono tiny">{plan.requests.toLocaleString()}</td>
                    <td className="mono tiny accent">{plan.credits.toLocaleString()}</td>
                    <td className="mono tiny">{plan.credits_per_account}</td>
                    <td className="mono tiny">{plan.bots_with_rewording}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="tiny mt-3 muted">
          Credits are charged for retrieval — every question a customer's bot answers.
        </p>
      </Panel>

      <Panel title="Grounded rewording">
        <StatRow>
          <Stat label="Bots" value={data.rewording.bots} />
          <Stat label="Opted in" value={data.rewording.enabled} tone="accent" />
          <Stat
            label="Opted in and live"
            value={data.rewording.enabled_and_ready}
            hint="have a sheet indexed too"
          />
        </StatRow>
        <p className="tiny mt-3 muted">
          Off by default. When on, the model may only reword the customer's own
          answer, and output that isn't grounded in it is discarded.
        </p>
      </Panel>

      <Panel title="Retrieval quality">
        <StatRow>
          <Stat
            label={`Unanswered in ${days}d`}
            value={data.retrieval.unanswered}
            tone={data.retrieval.unanswered > 0 ? "warn" : undefined}
          />
          <Stat label="Bots affected" value={data.retrieval.bots_affected} />
          <Stat label="Flagged inputs" value={data.retrieval.flagged_inputs} />
        </StatRow>

        <h3 className="h-card mt-5 mb-3">Most-asked questions nobody's bot could answer</h3>
        {data.top_unanswered.length === 0 ? (
          <Empty>Every question in this window found an answer.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Question</th>
                  <th>Bot</th>
                  <th>Asked</th>
                  <th>Best match</th>
                </tr>
              </thead>
              <tbody>
                {data.top_unanswered.map((gap, index) => (
                  <tr key={`${gap.query_text}-${index}`}>
                    {/* Public-written text. Displayed, never obeyed. */}
                    <td className="tiny">{gap.query_text}</td>
                    <td className="mono tiny">{gap.bot_type}</td>
                    <td className="mono tiny">{gap.times_asked}</td>
                    <td className="mono tiny">{gap.best_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="By template">
        <BarList
          rows={data.by_template.map((template) => ({
            label: template.template_id,
            value: template.bots,
            hint: `(${template.ready} live, ${template.requests} requests)`,
          }))}
          empty="No bots have been created yet."
        />
      </Panel>
    </>
  );
}
