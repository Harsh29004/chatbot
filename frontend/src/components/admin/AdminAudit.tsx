import { useState } from "react";

import { adminPanelApi, type AdminAudit as Audit } from "../../lib/api";

import { Empty, Loading, Panel, Stat, StatRow, when, useAdminData } from "./bits";

const ROLES = [
  { id: "", label: "All keys" },
  { id: "user", label: "Customer keys" },
  { id: "owner", label: "Owner keys" },
];

/**
 * The API audit trail.
 *
 * Owner keys get their own filter and their own table because they are the
 * ones worth watching: unmetered, cross-tenant, and minted outside the
 * product. They are logged precisely so that this screen can exist.
 */
export function AdminAudit({ adminKey, days }: { adminKey: string; days: number }) {
  const [email, setEmail] = useState("");
  const [emailQuery, setEmailQuery] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [role, setRole] = useState("");

  const { data, error, loading } = useAdminData<Audit>(
    () =>
      adminPanelApi.audit(adminKey, {
        days,
        email: emailQuery,
        endpoint,
        role,
        limit: 200,
      }),
    [adminKey, days, emailQuery, endpoint, role],
  );

  return (
    <>
      <Panel
        title="API calls"
        action={
          <form
            className="row gap-3"
            style={{ flexWrap: "wrap" }}
            onSubmit={(e) => {
              e.preventDefault();
              setEmailQuery(email.trim());
            }}
          >
            <input
              className="input"
              placeholder="Filter by account email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{ width: 200 }}
            />
            <input
              className="input mono"
              placeholder="/v1/ask"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              style={{ width: 140 }}
            />
            <button className="btn btn-secondary btn-sm">Filter</button>
          </form>
        }
      >
        <div className="chip-row mb-4">
          {ROLES.map((option) => (
            <button
              key={option.id || "all"}
              className="chip"
              data-active={role === option.id}
              onClick={() => setRole(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>

        {error && <div className="error-box">{error}</div>}
        {loading || !data ? (
          <Loading />
        ) : (
          <>
            <StatRow>
              <Stat label="Requests" value={data.summary.requests ?? 0} />
              <Stat label="Credits" value={data.summary.credits ?? 0} />
              <Stat label="Accounts" value={data.summary.accounts ?? 0} />
              <Stat label="Keys used" value={data.summary.keys_used ?? 0} />
            </StatRow>

            {data.entries.length === 0 ? (
              <Empty>No calls match that in the last {days} days.</Empty>
            ) : (
              <div className="table-scroll mt-4">
                <table className="table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Account</th>
                      <th>Key</th>
                      <th>Endpoint</th>
                      <th>Chars</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.entries.map((entry) => (
                      <tr key={entry.id}>
                        <td className="tiny">{when(entry.timestamp)}</td>
                        <td className="tiny">
                          {entry.owner_email ?? "—"}
                          {entry.role === "owner" && (
                            <span className="badge badge-warn ml-2">owner</span>
                          )}
                        </td>
                        <td className="mono tiny">
                          {entry.key_prefix ?? "—"}
                          {entry.key_label ? (
                            <div className="muted">{entry.key_label}</div>
                          ) : null}
                        </td>
                        <td className="mono tiny">{entry.endpoint}</td>
                        <td className="mono tiny">{entry.message_len}</td>
                        <td className="mono tiny">{entry.credit_cost}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="tiny mt-3 muted">
              Showing {data.entries.length} of {data.total} matching calls.
            </p>
          </>
        )}
      </Panel>

      {data && (
        <>
          <Panel title="Owner keys">
            {data.owner_keys.length === 0 ? (
              <Empty>No owner keys have been minted.</Empty>
            ) : (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Prefix</th>
                      <th>Holder</th>
                      <th>Created</th>
                      <th>Calls</th>
                      <th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.owner_keys.map((key) => (
                      <tr key={key.id}>
                        <td className="mono tiny">{key.key_prefix}…</td>
                        <td className="tiny">{key.owner_email}</td>
                        <td className="tiny">{when(key.created_at)}</td>
                        <td className="mono tiny">{key.requests.toLocaleString()}</td>
                        <td className="tiny">{key.is_active ? "active" : "revoked"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel title="Inputs the injection detector flagged">
            {data.flagged_inputs.length === 0 ? (
              <Empty>Nothing flagged in the last {days} days.</Empty>
            ) : (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Bot</th>
                      <th>What was sent</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.flagged_inputs.map((flag, index) => (
                      <tr key={`${flag.timestamp}-${index}`}>
                        <td className="tiny">{when(flag.timestamp)}</td>
                        <td className="mono tiny">{flag.bot_type}</td>
                        {/* Written by a member of the public: rendered as
                            text, never interpreted. */}
                        <td className="tiny">{flag.query_text}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      )}
    </>
  );
}
