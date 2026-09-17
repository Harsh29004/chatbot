import { useEffect, useId, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../lib/auth";

/**
 * The Nexora mark, redrawn as vector from the brand logo: a brushed-silver
 * angular N inside an electric-blue ring, with the ring's glowing "eye".
 * Vector rather than the raster logo because the nav shows it at 34px, where
 * the full illustration's detail turns to mush.
 */
export function LogoMark({ size = 34 }: { size?: number }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg
      className="logo-mark"
      width={size}
      height={size}
      viewBox="0 0 64 64"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`${id}-metal`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.55" stopColor="#c9d2df" />
          <stop offset="1" stopColor="#8a96a8" />
        </linearGradient>
        <linearGradient id={`${id}-ring`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#5cc6ff" />
          <stop offset="1" stopColor="#1467c9" />
        </linearGradient>
      </defs>
      <circle cx="32" cy="32" r="29" fill="none" stroke={`url(#${id}-ring)`} strokeWidth="2" />
      <path d="M3 32l2.2-1.2L3 29.6 1 30.8z M61 32l2-1.2-2-1.2-2.2 1.2z" fill="#5cc6ff" />
      <path d="M15 12h10l14 27V19l8-7v40H37L23 25v27h-8z" fill={`url(#${id}-metal)`} />
      <path d="M23 25l14 27h3L25 22z" fill="#1f8fff" opacity="0.8" />
      <circle cx="45" cy="22" r="4.4" fill="#030509" stroke="#5cc6ff" strokeWidth="2.2" />
    </svg>
  );
}

export function Logo({ withSub = false }: { withSub?: boolean }) {
  return (
    <Link to="/" className="logo" aria-label="Nexora AI home">
      <LogoMark />
      <span>
        <span className="logo-word">
          NEXOR<span className="logo-a">A</span>
        </span>
        {withSub && <span className="logo-sub">AI CHAT BOT</span>}
      </span>
    </Link>
  );
}

/** The brand line from the logo. */
export function Tagline() {
  return (
    <span className="tagline" aria-label="Think, ask, solve, together">
      Think <i aria-hidden="true" /> Ask <i aria-hidden="true" /> Solve{" "}
      <i aria-hidden="true" /> Together
    </span>
  );
}

/**
 * The announcement strip.
 *
 * Dismissal is remembered per message id, so changing ANNOUNCE.id brings the
 * bar back for people who closed the previous one instead of silently never
 * showing again.
 */
const ANNOUNCE = {
  id: "early-access-2026",
  text: "Nexora AI is in early access.",
  detail: "Fourteen days free, no card.",
  cta: "Start free",
  href: "/signup",
};

export function AnnounceBar() {
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    try {
      setDismissed(localStorage.getItem("announce-seen") === ANNOUNCE.id);
    } catch {
      setDismissed(false);
    }
  }, []);

  const close = () => {
    setDismissed(true);
    try {
      localStorage.setItem("announce-seen", ANNOUNCE.id);
    } catch {
      /* Private browsing. The bar simply returns next visit. */
    }
  };

  if (dismissed) return null;

  return (
    <div className="announce">
      <div className="announce-inner">
        <span>
          <strong>{ANNOUNCE.text}</strong> {ANNOUNCE.detail}
        </span>
        <Link to={ANNOUNCE.href} className="announce-cta">
          {ANNOUNCE.cta} <span aria-hidden="true">→</span>
        </Link>
      </div>
      <button className="announce-close" onClick={close} aria-label="Dismiss announcement">
        ✕
      </button>
    </div>
  );
}

export function Nav() {
  const { customer, signOut } = useAuth();
  const navigate = useNavigate();
  const [scrolled, setScrolled] = useState(false);

  // The pill only grows its background once it has actually detached from the
  // top. Over the hero it stays invisible so the headline owns the screen.
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const handleSignOut = async () => {
    await signOut();
    navigate("/");
  };

  return (
    <header className={`nav ${scrolled ? "nav-scrolled" : ""}`}>
      <div className="wrap">
        <div className="nav-pill">
          <Logo />

          {/* The centre group points at landing-page sections. From the
              dashboard those anchors go nowhere useful, and dropping them
              keeps the signed-in pill from overflowing. */}
          {customer ? (
            <span />
          ) : (
            <nav className="nav-center">
              <Link to="/#how" className="nav-link">
                How it works
              </Link>
              <Link to="/#templates" className="nav-link">
                Templates
              </Link>
              <Link to="/#pricing" className="nav-link">
                Pricing
              </Link>
              <Link to="/#docs" className="nav-link">
                Docs
              </Link>
            </nav>
          )}

          <div className="nav-actions">
            {customer ? (
              <>
                <Link to="/assistant" className="nav-link nav-link-hide">
                  Assistant
                </Link>
                <Link to="/support" className="nav-link nav-link-hide">
                  Support
                </Link>
                <Link to="/dashboard" className="btn btn-secondary btn-sm">
                  Dashboard
                </Link>
                <button className="btn btn-ghost btn-sm" onClick={handleSignOut}>
                  Sign out
                </button>
              </>
            ) : (
              <>
                <Link to="/signin" className="nav-link">
                  Sign in
                </Link>
                <Link to="/signup" className="btn btn-primary btn-sm">
                  Start free
                </Link>
              </>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap">
        <div className="footer-inner">
          <div className="footer-brand">
            <Logo withSub />
            <Tagline />
            <p className="tiny">
              Retrieval-grounded FAQ bots. Answers come from your sheet, not from
              a model's imagination.
            </p>
          </div>

          <div className="footer-col">
            <span className="footer-head">Product</span>
            <Link to="/#how" className="nav-link">
              How it works
            </Link>
            <Link to="/#templates" className="nav-link">
              Templates
            </Link>
            <Link to="/#pricing" className="nav-link">
              Pricing
            </Link>
          </div>

          <div className="footer-col">
            <span className="footer-head">Developers</span>
            <Link to="/#docs" className="nav-link">
              API reference
            </Link>
            <Link to="/#faq" className="nav-link">
              FAQ
            </Link>
            <Link to="/support" className="nav-link">
              Support
            </Link>
          </div>

          <div className="footer-col">
            <span className="footer-head">Account</span>
            <Link to="/signin" className="nav-link">
              Sign in
            </Link>
            <Link to="/signup" className="nav-link">
              Create account
            </Link>
            <Link to="/dashboard" className="nav-link">
              Dashboard
            </Link>
          </div>
        </div>

        <div className="footer-bottom">
          <span className="tiny">
            © {new Date().getFullYear()} Nexora AI. All rights reserved.
          </span>
          <span className="tiny">Built for teams who already wrote the answers.</span>
        </div>
      </div>
    </footer>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <AnnounceBar />
      <Nav />
      {children}
      <Footer />
    </div>
  );
}

/** Minimal syntax highlighting — enough to give code shape, no parser needed. */
export function CodeBlock({
  title,
  code,
}: {
  title: string;
  code: { text: string; tone?: "key" | "str" | "cmt" }[][];
}) {
  const [copied, setCopied] = useState(false);

  const plain = code.map((line) => line.map((t) => t.text).join("")).join("\n");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plain);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="code">
      <div className="code-head">
        <span className="code-title">{title}</span>
        <button className="btn btn-ghost btn-sm" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>
        <code>
          {code.map((line, i) => (
            <div key={i}>
              {line.map((token, j) => (
                <span key={j} className={token.tone ? `tok-${token.tone}` : undefined}>
                  {token.text}
                </span>
              ))}
              {line.length === 0 ? " " : null}
            </div>
          ))}
        </code>
      </pre>
    </div>
  );
}
