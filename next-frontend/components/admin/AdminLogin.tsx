"use client";

import { useState, type ReactNode } from "react";

import { adminLogin } from "../../lib/api-client";
import { Logo } from "../Chrome";

/**
 * The staff sign-in gate for the admin panel and the support inbox.
 *
 * Username and password go to the server, which answers with a signed session
 * token. That token, not the password, is what the page keeps and sends.
 */
export function AdminLogin({
  title,
  onToken,
  footer,
}: {
  title: string;
  /** Called with the new token; should throw if the token doesn't open the page. */
  onToken: (token: string) => Promise<void>;
  footer?: ReactNode;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { token } = await adminLogin(username.trim(), password);
      await onToken(token);
      setPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-gate">
      <form className="auth-card" onSubmit={submit}>
        <Logo />
        <h1 className="h-section mt-4">{title}</h1>
        <p className="small muted mb-4">
          Staff only. Your session lasts for this tab and ends when you close it.
        </p>

        <div className="field">
          <label className="label" htmlFor="admin-username">
            Username
          </label>
          <input
            id="admin-username"
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            autoFocus
          />
        </div>

        <div className="field mt-3">
          <label className="label" htmlFor="admin-password">
            Password
          </label>
          <input
            id="admin-password"
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="off"
          />
        </div>

        {error && <p className="error-box mt-3">{error}</p>}

        <button className="btn btn-primary btn-block mt-4" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        {footer && <p className="tiny mt-4 muted">{footer}</p>}
      </form>
    </div>
  );
}
