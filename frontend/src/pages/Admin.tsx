import { useState } from "react";
import { Link } from "react-router-dom";

import { Logo } from "../components/Chrome";
import { AdminAi } from "../components/admin/AdminAi";
import { AdminAudit } from "../components/admin/AdminAudit";
import { AdminOverview } from "../components/admin/AdminOverview";
import { AdminReferrals } from "../components/admin/AdminReferrals";
import { AdminSubscriptions } from "../components/admin/AdminSubscriptions";
import { AdminTemplates } from "../components/admin/AdminTemplates";
import { AdminUsage } from "../components/admin/AdminUsage";
import { AdminUsers } from "../components/admin/AdminUsers";
import { adminPanelApi } from "../lib/api";

/**
 * Where the admin key lives.
 *
 * The same storage the support inbox uses, and for the same reason:
 * sessionStorage does not outlive the tab. This key reads every customer's
 * billing, usage and traffic, so retyping it after a browser restart is the
 * right trade.
 */
const KEY_STORAGE = "nexora.adminKey";

function readStoredKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    // Private windows throw on access rather than returning null.
    return "";
  }
}

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "users", label: "Users" },
  { id: "subscriptions", label: "Subscriptions" },
  { id: "usage", label: "Usage" },
  { id: "audit", label: "API audit" },
  { id: "ai", label: "AI usage" },
  { id: "templates", label: "Templates" },
  { id: "referrals", label: "Referrals" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const WINDOWS = [7, 30, 90];

/**
 * The admin panel.
 *
 * A staff tool, not part of the customer app: no session, no nav, its own key
 * gate — the same shape as the support inbox, so there is one thing to learn
 * and one credential to hold.
 *
 * The window selector is deliberately shared across tabs. Reading "480 credits"
 * on one screen and "12 accounts" on another means nothing unless both cover
 * the same days, and per-tab pickers are how that goes wrong.
 */
export function Admin() {
  const [adminKey, setAdminKey] = useState(readStoredKey);
  const [draft, setDraft] = useState("");
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [tab, setTab] = useState<TabId>("overview");
  const [days, setDays] = useState(30);

  // Validate the key by making the cheapest real call, so a rotated key lands
  // on the gate rather than on eight empty panels.
  const unlock = async (candidate: string) => {
    const key = candidate.trim();
    if (!key) return;

    setChecking(true);
    setError(null);
    try {
      await adminPanelApi.health(key);
      try {
        sessionStorage.setItem(KEY_STORAGE, key);
      } catch {
        // Not being able to remember it is survivable; being locked out is not.
      }
      setAdminKey(key);
      setAuthed(true);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "That admin key was not accepted.");
    } finally {
      setChecking(false);
    }
  };

  const lock = () => {
    try {
      sessionStorage.removeItem(KEY_STORAGE);
    } catch {
      /* nothing to clear */
    }
    setAdminKey("");
    setAuthed(false);
  };

  if (!authed) {
    return (
      <div className="admin-gate">
        <div className="auth-card">
          <Logo />
          <h1 className="h-section mt-4">Admin panel</h1>
          <p className="small muted mb-4">
            Staff only. Paste the admin key — it&apos;s kept for this tab only, and
            cleared when you close it.
          </p>
          <div className="field">
            <label className="label" htmlFor="adminkey">
              Admin key
            </label>
            <input
              id="adminkey"
              className="input"
              type="password"
              value={draft || adminKey}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && unlock(draft || adminKey)}
              autoComplete="off"
            />
          </div>
          {error && <p className="error-box mt-3">{error}</p>}
          <button
            className="btn btn-primary btn-block mt-4"
            disabled={checking}
            onClick={() => unlock(draft || adminKey)}
          >
            {checking ? "Checking…" : "Open panel"}
          </button>
          <p className="tiny mt-4 muted">
            Looking for customer messages? That&apos;s the{" "}
            <Link to="/admin/support" className="accent">
              support inbox
            </Link>
            .
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="admin-shell">
      <header className="admin-bar">
        <div className="row gap-4">
          <Logo />
          <span className="badge">Admin</span>
        </div>
        <div className="row gap-4">
          <Link className="btn btn-ghost btn-sm" to="/admin/support">
            Support inbox
          </Link>
          <button className="btn btn-ghost btn-sm" onClick={lock}>
            Lock
          </button>
        </div>
      </header>

      <div className="admin-tabs">
        <div className="chip-row">
          {TABS.map((option) => (
            <button
              key={option.id}
              className="chip"
              data-active={tab === option.id}
              onClick={() => setTab(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>

        {tab !== "templates" && tab !== "referrals" && (
          <div className="chip-row">
            {WINDOWS.map((window) => (
              <button
                key={window}
                className="chip"
                data-active={days === window}
                onClick={() => setDays(window)}
              >
                {window}d
              </button>
            ))}
          </div>
        )}
      </div>

      <main className="admin-main">
        {tab === "overview" && <AdminOverview adminKey={adminKey} days={days} />}
        {tab === "users" && <AdminUsers adminKey={adminKey} days={days} />}
        {tab === "subscriptions" && (
          <AdminSubscriptions adminKey={adminKey} days={days} />
        )}
        {tab === "usage" && <AdminUsage adminKey={adminKey} days={days} />}
        {tab === "audit" && <AdminAudit adminKey={adminKey} days={days} />}
        {tab === "ai" && <AdminAi adminKey={adminKey} days={days} />}
        {tab === "templates" && <AdminTemplates adminKey={adminKey} />}
        {tab === "referrals" && <AdminReferrals adminKey={adminKey} />}
      </main>
    </div>
  );
}
