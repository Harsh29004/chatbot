import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../lib/auth";

export function Logo() {
  return (
    <Link to="/" className="logo" aria-label="Nexora AI home">
      <span className="logo-mark" aria-hidden="true">
        N
      </span>
      Nexora AI
    </Link>
  );
}

export function Nav() {
  const { customer, signOut } = useAuth();
  const navigate = useNavigate();
  const [scrolled, setScrolled] = useState(false);

  // The nav only grows a border once it has actually detached from the top.
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
      <div className="wrap nav-inner">
        <Logo />
        <nav className="nav-links">
          <Link to="/#how" className="nav-link nav-link-hide">
            How it works
          </Link>
          <Link to="/#templates" className="nav-link nav-link-hide">
            Templates
          </Link>
          <Link to="/#pricing" className="nav-link nav-link-hide">
            Pricing
          </Link>
          <Link to="/#docs" className="nav-link nav-link-hide">
            Docs
          </Link>
          {customer ? (
            <>
              <Link to="/dashboard" className="nav-link">
                Dashboard
              </Link>
              <button className="btn btn-secondary btn-sm" onClick={handleSignOut}>
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
        </nav>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap footer-inner">
        <div className="col gap-2">
          <Logo />
          <p className="tiny">
            Retrieval-grounded FAQ bots. Answers come from your sheet, not from a
            model's imagination.
          </p>
        </div>
        <div className="row gap-5">
          <Link to="/#pricing" className="nav-link">
            Pricing
          </Link>
          <Link to="/#docs" className="nav-link">
            Docs
          </Link>
          <Link to="/signin" className="nav-link">
            Sign in
          </Link>
        </div>
      </div>
    </footer>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
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
