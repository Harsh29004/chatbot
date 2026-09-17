import { useEffect, useState, type FormEvent } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

import { Page } from "../components/Chrome";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

const MIN_PASSWORD = 8;

/**
 * "Continue with Google", shown only when the server actually has credentials.
 *
 * A plain link, not a fetch: this starts a browser navigation to Google's
 * consent screen, and the whole flow depends on the browser following
 * redirects and carrying cookies. XHR would break both.
 */
function GoogleButton({ label, referralCode = "" }: { label: string; referralCode?: string }) {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    api
      .authProviders()
      .then((p) => setEnabled(p.google))
      .catch(() => setEnabled(false));
  }, []);

  if (!enabled) return null;

  return (
    <>
      <a
        className="btn btn-secondary btn-block google-btn"
        href={
          referralCode
            ? `/api/auth/google?ref=${encodeURIComponent(referralCode)}`
            : "/api/auth/google"
        }
      >
        <svg className="google-mark" viewBox="0 0 18 18" aria-hidden="true">
          <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.88 2.69-6.62z" />
          <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.81.54-1.84.86-3.05.86-2.34 0-4.33-1.58-5.04-3.71H.96v2.33A9 9 0 0 0 9 18z" />
          <path fill="#FBBC05" d="M3.96 10.71a5.41 5.41 0 0 1 0-3.42V4.96H.96a9 9 0 0 0 0 8.08l3-2.33z" />
          <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.96l3 2.33C4.67 5.16 6.66 3.58 9 3.58z" />
        </svg>
        {label}
      </a>
      <div className="auth-divider"><span>or</span></div>
    </>
  );
}

/**
 * The referral code from ``/signup?ref=CODE``, remembered across a reload.
 *
 * Kept in sessionStorage because the trip to Google's consent screen and back
 * loses the query string, and someone who clicks a friend's link, wanders off
 * to read the pricing page and comes back should still be credited to them.
 * Session-scoped rather than permanent: a code should not follow a browser
 * around for months attaching itself to unrelated signups.
 */
const REFERRAL_STORAGE = "nexora.referralCode";

function useReferralCode(): string {
  const [params] = useSearchParams();
  const fromUrl = (params.get("ref") ?? "").trim().toUpperCase();

  const [code] = useState(() => {
    try {
      if (fromUrl) {
        sessionStorage.setItem(REFERRAL_STORAGE, fromUrl);
        return fromUrl;
      }
      return sessionStorage.getItem(REFERRAL_STORAGE) ?? "";
    } catch {
      // Private windows throw rather than returning null; the URL is still
      // good enough on its own.
      return fromUrl;
    }
  });

  return code;
}


/**
 * The OAuth callback redirects here with ?error=... when sign-in fails.
 *
 * It has to arrive in the URL: a failed redirect lands on a freshly loaded
 * page with no React state to have put a message into.
 */
function useOAuthError(): string | null {
  const [params, setParams] = useSearchParams();
  const [message] = useState(() => params.get("error"));

  useEffect(() => {
    if (!params.get("error")) return;
    // Clear it so a refresh doesn't re-show a stale failure.
    const next = new URLSearchParams(params);
    next.delete("error");
    setParams(next, { replace: true });
  }, [params, setParams]);

  return message;
}

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
  const oauthError = useOAuthError();
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
      <GoogleButton label="Continue with Google" />
      <form onSubmit={submit}>
        {(error || oauthError) && <div className="error-box">{error ?? oauthError}</div>}

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
  const referralCode = useReferralCode();

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
      await signUp(email, password, name, referralCode);
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
      {referralCode && (
        <div className="referral-banner">
          You were invited — code <code className="mono accent">{referralCode}</code>{" "}
          adds bonus credits to your account.
        </div>
      )}
      <GoogleButton label="Sign up with Google" referralCode={referralCode} />
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
            placeholder={`At least ${MIN_PASSWORD} characters`}
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
