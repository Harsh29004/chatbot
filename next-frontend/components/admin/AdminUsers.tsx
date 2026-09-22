"use client";

import { useState } from "react";

import { adminPanelApi } from "../../lib/api-client";
import type { AdminUser, AdminUserDetail, AdminUserPage } from "../../lib/types";

import { Empty, Loading, Panel, Stat, StatRow, when, useAdminData } from "./bits";

const STATUS_FILTERS = [
  { id: "", label: "Everyone" },
  { id: "entitled", label: "Can use it now" },
  { id: "paying", label: "Paying" },
  { id: "trialing", label: "On trial" },
  { id: "expired", label: "Lapsed" },
  { id: "disabled", label: "Disabled" },
];

const SORTS = [
  { id: "created", label: "Newest" },
  { id: "credits", label: "Most credits" },
  { id: "requests", label: "Most requests" },
  { id: "referrals", label: "Most referrals" },
  { id: "email", label: "A–Z" },
];

/**
 * Every account on the platform, and the actions you take on one.
 *
 * The list is the answer to "who is here?"; the drawer is the answer to "what
 * is going on with this one?". They are one screen because in practice you
 * always arrive at the second through the first.
 */
export function AdminUsers({ adminKey, days }: { adminKey: string; days: number }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("created");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

  const limit = 50;
  const { data, error, loading, reload } = useAdminData<AdminUserPage>(
    () =>
      adminPanelApi.users(adminKey, {
        q: search,
        status_filter: status,
        days,
        sort,
        limit,
        offset,
      }),
    [adminKey, search, status, sort, days, offset],
  );

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setOffset(0);
    setSearch(query.trim());
  };

  return (
    <>
      <Panel
        title="Accounts"
        action={
          <form className="row gap-3" onSubmit={submitSearch} style={{ flexWrap: "wrap" }}>
            <input
              className="input"
              placeholder="Search email or name"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: 220 }}
            />
            <button className="btn btn-secondary btn-sm">Search</button>
          </form>
        }
      >
        <div className="chip-row mb-4">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.id || "all"}
              className="chip"
              data-active={status === filter.id}
              onClick={() => {
                setOffset(0);
                setStatus(filter.id);
              }}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <div className="chip-row mb-4">
          {SORTS.map((option) => (
            <button
              key={option.id}
              className="chip"
              data-active={sort === option.id}
              onClick={() => setSort(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>

        {error && <div className="error-box">{error}</div>}
        {loading && !data ? (
          <Loading />
        ) : !data || data.users.length === 0 ? (
          <Empty>No accounts match that.</Empty>
        ) : (
          <>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Plan</th>
                    <th>Bot</th>
                    <th>Credits ({days}d)</th>
                    <th>Requests</th>
                    <th>Bonus</th>
                    <th>Referrals</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.users.map((user) => (
                    <UserRow
                      key={user.customer_id}
                      user={user}
                      onOpen={() => setSelected(user.customer_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            <div className="row row-between mt-4">
              <span className="tiny muted">
                {offset + 1}–{Math.min(offset + limit, data.total)} of {data.total}
              </span>
              <div className="row gap-3">
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                >
                  Previous
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={offset + limit >= data.total}
                  onClick={() => setOffset(offset + limit)}
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </Panel>

      {selected !== null && (
        <UserDetail
          adminKey={adminKey}
          customerId={selected}
          days={days}
          onClose={() => setSelected(null)}
          onChanged={reload}
        />
      )}
    </>
  );
}

function UserRow({ user, onOpen }: { user: AdminUser; onOpen: () => void }) {
  return (
    <tr>
      <td>
        <button className="linkish" onClick={onOpen}>
          {user.email}
        </button>
        <div className="tiny muted">
          {user.name || "—"}
          {user.is_active ? "" : " · disabled"}
          {user.referred_by_email ? ` · via ${user.referred_by_email}` : ""}
        </div>
      </td>
      <td>
        <span className={`badge ${user.is_entitled ? "badge-live" : "badge-off"}`}>
          <span className="dot" />
          {user.plan_id ?? "none"}
        </span>
        <div className="tiny muted">{user.subscription_status ?? "—"}</div>
      </td>
      <td className="tiny">
        {user.bot_status ?? "—"}
        {user.bot_doc_count ? ` · ${user.bot_doc_count} rows` : ""}
        <div className="muted">{user.template_id ?? ""}</div>
      </td>
      <td className="mono tiny">
        {user.credits_in_window.toLocaleString()}
        <div className="muted">of {user.effective_daily_limit.toLocaleString()}/day</div>
      </td>
      <td className="mono tiny">
        {user.requests_in_window.toLocaleString()}
        <div className="muted">{when(user.last_request_at)}</div>
      </td>
      <td className="mono tiny accent">{user.bonus_credits.toLocaleString()}</td>
      <td className="mono tiny">{user.referred_count}</td>
      <td>
        <button className="btn btn-ghost btn-sm" onClick={onOpen}>
          Open
        </button>
      </td>
    </tr>
  );
}

function UserDetail({
  adminKey,
  customerId,
  days,
  onClose,
  onChanged,
}: {
  adminKey: string;
  customerId: string;
  days: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { data, error, loading, reload } = useAdminData<AdminUserDetail>(
    () => adminPanelApi.user(adminKey, customerId, days),
    [adminKey, customerId, days],
  );

  const [credits, setCredits] = useState("100");
  const [note, setNote] = useState("");
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      reload();
      onChanged();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-drawer">
      <div className="row row-between mb-4">
        <h2 className="h-card">{data?.account.email ?? "Account"}</h2>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      </div>

      {error && <div className="error-box">{error}</div>}
      {loading || !data ? (
        <Loading />
      ) : (
        <>
          <StatRow>
            <Stat label="Plan" value={data.account.plan_id ?? "none"} />
            <Stat
              label="Daily limit"
              value={data.account.effective_daily_limit}
              hint={data.account.daily_credit_limit === null ? "platform default" : "override"}
            />
            <Stat label="Bonus credits" value={data.account.bonus_credits} tone="accent" />
            <Stat label={`Credits ${days}d`} value={data.account.credits_in_window} />
            <Stat label="Referrals" value={data.account.referred_count} />
          </StatRow>

          {actionError && <div className="error-box mt-4">{actionError}</div>}

          <div className="admin-actions mt-4">
            <div className="field">
              <label className="label">Grant bonus credits</label>
              <div className="row gap-3" style={{ flexWrap: "wrap" }}>
                <input
                  className="input mono"
                  value={credits}
                  onChange={(e) => setCredits(e.target.value)}
                  style={{ width: 100 }}
                />
                <input
                  className="input"
                  placeholder="Why (shown in the grant log)"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  style={{ flex: "1 1 200px" }}
                />
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={busy || !Number(credits)}
                  onClick={() =>
                    run(() =>
                      adminPanelApi.grantCredits(
                        adminKey,
                        customerId,
                        Number(credits),
                        note,
                      ),
                    )
                  }
                >
                  Grant
                </button>
              </div>
            </div>

            <div className="field">
              <label className="label">Daily credit limit</label>
              <div className="row gap-3" style={{ flexWrap: "wrap" }}>
                <input
                  className="input mono"
                  placeholder={String(data.account.effective_daily_limit)}
                  value={limit}
                  onChange={(e) => setLimit(e.target.value)}
                  style={{ width: 120 }}
                />
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={busy || limit.trim() === ""}
                  onClick={() =>
                    run(() =>
                      adminPanelApi.setLimit(adminKey, customerId, Number(limit)),
                    )
                  }
                >
                  Set
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={busy}
                  onClick={() => run(() => adminPanelApi.setLimit(adminKey, customerId, null))}
                >
                  Use platform default
                </button>
              </div>
              <p className="tiny mt-3 muted">
                A plan change overwrites this — it is a temporary raise, not a plan.
              </p>
            </div>

            <div className="field">
              <label className="label">Account</label>
              <button
                className="btn btn-ghost btn-sm"
                disabled={busy}
                onClick={() =>
                  run(() =>
                    adminPanelApi.setActive(
                      adminKey,
                      customerId,
                      !data.account.is_active,
                    ),
                  )
                }
              >
                {data.account.is_active ? "Disable account" : "Re-enable account"}
              </button>
              <p className="tiny mt-3 muted">
                Disabling stops both sign-in and every API key on the account.
              </p>
            </div>
          </div>

          <h3 className="h-card mt-5 mb-3">API keys</h3>
          {data.keys.length === 0 ? (
            <Empty>No keys issued.</Empty>
          ) : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Prefix</th>
                    <th>Label</th>
                    <th>Created</th>
                    <th>State</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.keys.map((key) => (
                    <tr key={key.id}>
                      <td className="mono tiny">{key.key_prefix}…</td>
                      <td className="tiny">{key.label || "—"}</td>
                      <td className="tiny">{when(key.created_at)}</td>
                      <td className="tiny">{key.is_active ? "active" : "revoked"}</td>
                      <td>
                        {!!key.is_active && (
                          <button
                            className="btn btn-ghost btn-sm"
                            disabled={busy}
                            onClick={() =>
                              run(() => adminPanelApi.revokeKey(adminKey, key.id))
                            }
                          >
                            Revoke
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="h-card mt-5 mb-3">Credit grants</h3>
          {data.credit_grants.length === 0 ? (
            <Empty>No bonus credits have been granted.</Empty>
          ) : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Reason</th>
                    <th>Granted</th>
                    <th>Left</th>
                  </tr>
                </thead>
                <tbody>
                  {data.credit_grants.map((grant) => (
                    <tr key={grant.id}>
                      <td className="tiny">{when(grant.created_at)}</td>
                      <td className="tiny">
                        {grant.reason}
                        {grant.note ? <div className="muted">{grant.note}</div> : null}
                      </td>
                      <td className="mono tiny">{grant.amount}</td>
                      <td className="mono tiny accent">{grant.remaining}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="h-card mt-5 mb-3">Invoices</h3>
          {data.invoices.length === 0 ? (
            <Empty>No invoices.</Empty>
          ) : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Issued</th>
                    <th>Plan</th>
                    <th>Amount</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.invoices.map((invoice) => (
                    <tr key={String(invoice.id)}>
                      <td className="tiny">{when(String(invoice.issued_at ?? ""))}</td>
                      <td className="tiny">{String(invoice.plan_id)}</td>
                      <td className="mono tiny">
                        {(Number(invoice.amount_cents) / 100).toFixed(2)}{" "}
                        {String(invoice.currency)}
                      </td>
                      <td className="tiny">{String(invoice.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="h-card mt-5 mb-3">Recent API calls</h3>
          {data.recent_requests.length === 0 ? (
            <Empty>This account has not called the API.</Empty>
          ) : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Endpoint</th>
                    <th>Key</th>
                    <th>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_requests.slice(0, 25).map((entry) => (
                    <tr key={entry.id}>
                      <td className="tiny">{when(entry.timestamp)}</td>
                      <td className="mono tiny">{entry.endpoint}</td>
                      <td className="mono tiny">{entry.key_prefix ?? "—"}</td>
                      <td className="mono tiny">{entry.credit_cost}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
