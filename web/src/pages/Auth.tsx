import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { Page } from "../components/Chrome";
import { useAuth } from "../lib/auth";

const MIN_PASSWORD = 12;

function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <Page>
      <main className="auth-page">
        <div className="auth-card fade-up">
          <h1 className="h-section mb-4">{title}</h1>
          <p className="small mb-5">{subtitle}</p>
          <div className="card">{children}</div>
          <p className="small center mt-5">{footer}</p>
        </div>
      </main>
    </Page>
  );
}

export function SignIn() {
  const { customer, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string } };

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (customer) return <Navigate to="/dashboard" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
      navigate(location.state?.from ?? "/dashboard", { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title="Welcome back"
      subtitle="Sign in to manage your keys and usage."
      footer={
        <>
          No account yet? <Link to="/signup" className="accent">Create one</Link>
        </>
      }
    >
      <form onSubmit={submit}>
        {error && <div className="error-box">{error}</div>}

        <div className="field">
          <label className="label" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
          />
        </div>

        <div className="field">
          <label className="label" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••••"
          />
        </div>

        <button className="btn btn-primary btn-block mt-4" disabled={busy}>
          {busy ? <span className="spinner" /> : "Sign in"}
        </button>
      </form>
    </AuthShell>
  );
}

export function SignUp() {
  const { customer, signUp } = useAuth();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (customer) return <Navigate to="/dashboard" replace />;

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password.length < MIN_PASSWORD) {
      setError(`Password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await signUp(email, password, name);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title="Start your trial"
      subtitle="Fourteen days, full API access, no card."
      footer={
        <>
          Already have an account? <Link to="/signin" className="accent">Sign in</Link>
        </>
      }
    >
      <form onSubmit={submit}>
        {error && <div className="error-box">{error}</div>}

        <div className="field">
          <label className="label" htmlFor="name">
            Name or company
          </label>
          <input
            id="name"
            className="input"
            autoComplete="organization"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Retail"
          />
        </div>

        <div className="field">
          <label className="label" htmlFor="email">
            Work email
          </label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
          />
        </div>

        <div className="field">
          <label className="label" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="At least 12 characters"
            aria-describedby="pw-hint"
          />
          <span
            id="pw-hint"
            className="tiny"
            style={{ color: tooShort ? "var(--accent)" : undefined }}
          >
            {tooShort
              ? `${MIN_PASSWORD - password.length} more characters needed`
              : "Length matters more than symbols. A short sentence works well."}
          </span>
        </div>

        <button className="btn btn-primary btn-block mt-4" disabled={busy}>
          {busy ? <span className="spinner" /> : "Create account"}
        </button>
      </form>
    </AuthShell>
  );
}
