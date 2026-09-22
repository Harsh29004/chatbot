import { useCallback, useEffect, useState } from "react";

import { EVENTS, track } from "../lib/analytics";
import { api, type ReferralSummary } from "../lib/api";

/**
 * The customer's side of the referral programme.
 *
 * Loads on its own rather than riding on the dashboard payload: it is the one
 * card on the page that nothing else depends on, so a slow or failed referral
 * query should cost the customer a card, not their credit balance.
 */
export function ReferralCard() {
  const [summary, setSummary] = useState<ReferralSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"link" | "code" | null>(null);
  const [invite, setInvite] = useState("");
  const [inviting, setInviting] = useState(false);

  const load = useCallback(() => {
    api
      .referrals()
      .then(setSummary)
      .catch((err) => setError((err as Error).message));
  }, []);

  useEffect(load, [load]);

  const copy = async (value: string, what: "link" | "code") => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(what);
      window.setTimeout(() => setCopied(null), 2000);
      // `share` is GA4's recommended name and feeds its built-in report;
      // the specific one is kept alongside it so the link-vs-code split
      // stays visible, since they are shared in different places.
      track(EVENTS.SHARE, { method: "clipboard", content_type: `referral_${what}` });
      track(EVENTS.REFERRAL_LINK_COPIED, { what });
    } catch {
      // Clipboard access is blocked in some browsers and over plain HTTP.
      // The value is on screen and selectable, so this is not worth an error.
    }
  };

  const sendInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    const email = invite.trim();
    if (!email) return;

    setInviting(true);
    setError(null);
    try {
      const updated = await api.inviteToReferral(email);
      setSummary(updated);
      setInvite("");
      // The invited address is deliberately absent — it is a third party's
      // email, and they never agreed to be in anyone's analytics.
      track(EVENTS.REFERRAL_INVITE_SENT, {
        invites_sent_total: updated.invites?.length ?? undefined,
      });
    } catch (err) {
      setError((err as Error).message);
      track(EVENTS.API_ERROR, {
        area: "referral_invite",
        reason: (err as Error).message.slice(0, 100),
      });
    } finally {
      setInviting(false);
    }
  };

  if (!summary) {
    return (
      <section className="card">
        <h2 className="h-card mb-4">Refer a friend</h2>
        {error ? <div className="error-box">{error}</div> : <div className="spinner" />}
      </section>
    );
  }

  return (
    <section className="card">
      <div className="row row-between mb-4" style={{ flexWrap: "wrap" }}>
        <h2 className="h-card">Refer a friend</h2>
        <span className="badge badge-live">
          <span className="dot" /> {summary.credits_earned} credits earned
        </span>
      </div>

      <p className="small">
        They get <strong className="accent">{summary.referred_signup_credits}</strong>{" "}
        bonus credits for signing up with your link. You get{" "}
        <strong className="accent">{summary.referrer_signup_credits}</strong> the moment
        they join, and <strong className="accent">{summary.topup_credits}</strong> more
        every time they top up. Bonus credits never expire — they are spent after
        each day's allowance runs out.
      </p>

      <div className="field mt-4">
        <label className="label" htmlFor="referral-link">
          Your link
        </label>
        <div className="row gap-3" style={{ flexWrap: "wrap" }}>
          <input
            id="referral-link"
            className="input mono"
            value={summary.link}
            readOnly
            onFocus={(e) => e.currentTarget.select()}
            style={{ flex: "1 1 260px" }}
          />
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => copy(summary.link, "link")}
          >
            {copied === "link" ? "Copied" : "Copy link"}
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => copy(summary.code, "code")}
            title="Share just the code"
          >
            {copied === "code" ? "Copied" : summary.code}
          </button>
        </div>
      </div>

      <form className="field" onSubmit={sendInvite}>
        <label className="label" htmlFor="referral-invite">
          Or invite by email
        </label>
        <div className="row gap-3" style={{ flexWrap: "wrap" }}>
          <input
            id="referral-invite"
            className="input"
            type="email"
            value={invite}
            placeholder="friend@company.com"
            onChange={(e) => setInvite(e.target.value)}
            style={{ flex: "1 1 260px" }}
          />
          <button className="btn btn-secondary btn-sm" disabled={inviting || !invite.trim()}>
            {inviting ? "Adding…" : "Add invite"}
          </button>
        </div>
        <p className="tiny mt-3">
          Anyone who signs up with an address you invited is credited to you, even
          if they never click the link.
        </p>
      </form>

      {error && <div className="error-box">{error}</div>}

      {summary.referred.length > 0 && (
        <div className="table-scroll mt-4">
          <table className="table">
            <thead>
              <tr>
                <th>Joined</th>
                <th>Who</th>
                <th>Top-ups</th>
                <th>Credits</th>
              </tr>
            </thead>
            <tbody>
              {summary.referred.map((person) => (
                <tr key={`${person.email}-${person.joined_at}`}>
                  <td className="tiny">{person.joined_at.slice(0, 10)}</td>
                  <td className="mono tiny">{person.email}</td>
                  <td className="tiny">{person.payments_rewarded}</td>
                  <td className="tiny accent">+{person.credits_earned}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {summary.invites.filter((i) => !i.claimed_at).length > 0 && (
        <p className="tiny mt-3 muted">
          Waiting on:{" "}
          {summary.invites
            .filter((i) => !i.claimed_at)
            .map((i) => i.email)
            .join(", ")}
        </p>
      )}

      {summary.joined_via && (
        <p className="tiny mt-3 muted">You joined through {summary.joined_via}.</p>
      )}
    </section>
  );
}
