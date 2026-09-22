import { useEffect, useState, type FormEvent } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

import { Page } from "../components/Chrome";
import { EVENTS, track } from "../lib/analytics";
import { EmailVerificationPending, useAuth } from "../lib/auth";
import { resendVerificationEmail, sendPasswordReset } from "../lib/firebaseSignIn";

const MIN_PASSWORD = 8;

const GOOGLE_MARK = (
  <svg className="google-mark" viewBox="0 0 18 18" aria-hidden="true">
    <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.88 2.69-6.62z" />
    <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.81.54-1.84.86-3.05.86-2.34 0-4.33-1.58-5.04-3.71H.96v2.33A9 9 0 0 0 9 18z" />
    <path fill="#FBBC05" d="M3.96 10.71a5.41 5.41 0 0 1 0-3.42V4.96H.96a9 9 0 0 0 0 8.08l3-2.33z" />
    <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.96l3 2.33C4.67 5.16 6.66 3.58 9 3.58z" />
  </svg>
);

/**
 * "Continue with Google", via a Firebase popup.
 *
 * A button, not a link: nothing navigates. Firebase opens the account
 * chooser, handles the token and hands it back in JavaScript, and the app
 * keeps its state throughout.
 *
 * `signInWithGoogle` falls back to a full-page redirect on its own when the
 * browser blocks popups — some in-app webviews (Instagram, LinkedIn) refuse
 * them outright, and there a redirect is the only thing that works. The
 * result of that redirect is collected on the next load by the auth
 * provider's boot sequence.
 *
 * Renders nothing when Firebase is not configured on both sides. A sign-in
 * button that fails on click is worse than no button.
 */
function GoogleButton({
  label,
  referralCode = "",
  onError,
}: {
  label: string;
  referralCode?: string;
  onError: (message: string) => void;
}) {
  const { firebaseReady, signInWithGoogleAccount } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  if (!firebaseReady) return null;

  const click = async () => {
    setBusy(true);
    onError("");
    try {
      await signInWithGoogleAccount(referralCode);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-secondary btn-block google-btn"
        onClick={click}
        disabled={busy}
      >
        {busy ? <span className="spinner" /> : GOOGLE_MARK}
        {busy ? "Opening Google…" : label}
      </button>
      <div className="auth-divider"><span>or</span></div>
    </>
  );
}

/**
 * "Check your inbox", shown after a sign-up that needs email verification.
 *
 * This is a success state wearing a warning's clothes. The account exists and
 * nothing failed — the server simply will not issue a session until Firebase
 * confirms the mailbox, which is the rule that stops someone signing up as an
 * address they do not own. So it gets its own panel with the one action that
 * moves it forward, rather than a red error box implying something is broken.
 */
function VerificationNotice({ email }: { email: string }) {
  const [sent, setSent] = useState(false);
  const [problem, setProblem] = useState("");

  const resend = async () => {
    setProblem("");
    try {
      await resendVerificationEmail();
      setSent(true);
      track(EVENTS.VERIFICATION_EMAIL_SENT, { trigger: "resend" });
    } catch (err) {
      setProblem((err as Error).message);
    }
  };

  return (
    <div className="referral-banner">
      <strong>Check your inbox</strong>
      <p className="small mt-3">
        We've sent a confirmation link to <strong>{email}</strong>. Click it,
        then come back and sign in — your trial starts the moment you do.
      </p>
      {problem && <p className="small mt-3">{problem}</p>}
      <div className="row gap-3 mt-4" style={{ flexWrap: "wrap" }}>
        <Link className="btn btn-primary" to="/signin">
          Go to sign in
        </Link>
        <button type="button" className="btn btn-secondary" onClick={resend} disabled={sent}>
          {sent ? "Email sent" : "Re-send email"}
        </button>
      </div>
    </div>
  );
}

/**
 * "Forgot password", inline rather than on its own route.
 *
 * Firebase sends the mail and hosts the reset page, so there is nothing here
 * but an address field. Always reports success, including for an address with
 * no account — otherwise this form becomes a way to test which emails are
 * registered, which is the same reason Firebase itself stopped distinguishing
 * them on sign-in.
 */
function ForgotPassword({ initialEmail }: { initialEmail: string }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState(initialEmail);
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [problem, setProblem] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setState("sending");
    setProblem("");
    track(EVENTS.PASSWORD_RESET_REQUESTED, {});
    try {
      await sendPasswordReset(email);
      setState("sent");
      track(EVENTS.PASSWORD_RESET_SENT, {});
    } catch (err) {
      setProblem((err as Error).message);
      setState("idle");
    }
  };

  if (!open) {
    return (
      <p className="tiny center mt-4">
        <button
          type="button"
          className="link-button accent"
          onClick={() => {
            setOpen(true);
            setEmail(initialEmail);
          }}
        >
          Forgot your password?
        </button>
      </p>
    );
  }

  if (state === "sent") {
    return (
      <p className="tiny center mt-4">
        If an account exists for that address, a reset link is on its way.
      </p>
    );
  }

  return (
    <div className="field mt-4">
      <label className="label" htmlFor="reset-email">
        Send a reset link to
      </label>
      <input
        id="reset-email"
        className="input"
        type="email"
        autoComplete="off"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@company.com"
      />
      {problem && <span className="tiny">{problem}</span>}
      <div className="row gap-3 mt-3" style={{ flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={submit}
          disabled={state === "sending" || !email}
        >
          {state === "sending" ? <span className="spinner" /> : "Send reset link"}
        </button>
        <button type="button" className="btn btn-secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
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
  const { customer, signIn, firebaseReady } = useAuth();
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
      <GoogleButton label="Continue with Google" onError={setError} />
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
            autoComplete="off"
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
            autoComplete="off"
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

      {/* Only offered when Firebase is running the passwords, because it is
          Firebase that sends the mail and hosts the reset page. The legacy
          path has no reset flow to point at. */}
      {firebaseReady && <ForgotPassword initialEmail={email} />}
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
  // Set when the account was created but needs its mailbox confirmed. It
  // replaces the form entirely: leaving the fields up invites the person to
  // submit again, which would only tell them the email is already taken.
  const [awaitingVerification, setAwaitingVerification] = useState(false);

  // Recorded once, when someone actually arrives on the signup form with a
  // code — not on every render, and not at the moment the reward lands.
  useEffect(() => {
    if (referralCode) track(EVENTS.REFERRAL_CODE_APPLIED, { code: referralCode });
  }, [referralCode]);

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
      // The account was created; it just cannot be used until the link in
      // the email is clicked. A different outcome, so a different screen.
      if (err instanceof EmailVerificationPending) {
        setAwaitingVerification(true);
        setBusy(false);
        return;
      }
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
      {awaitingVerification ? (
        <VerificationNotice email={email} />
      ) : (
        <>
      {referralCode && (
        <div className="referral-banner">
          You were invited — code <code className="mono accent">{referralCode}</code>{" "}
          adds bonus credits to your account.
        </div>
      )}
      <GoogleButton
        label="Sign up with Google"
        referralCode={referralCode}
        onError={setError}
      />
      <form onSubmit={submit}>
        {error && <div className="error-box">{error}</div>}

        <div className="field">
          <label className="label" htmlFor="name">
            Name or company
          </label>
          <input
            id="name"
            className="input"
            autoComplete="off"
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
            autoComplete="off"
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
            autoComplete="off"
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
        </>
      )}
    </AuthShell>
  );
}
