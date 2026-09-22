import Link from "next/link";

/**
 * The Vite app sent every unknown path to the landing page, because a SPA
 * fallback cannot tell a typo from a real route. The server can, so a wrong URL
 * now says so — and returns a genuine 404 rather than 200 with the homepage.
 */
export default function NotFound() {
  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>Page not found</h1>
        <p className="muted">That link doesn&apos;t go anywhere.</p>
        <Link className="btn btn-primary" href="/">
          Back to the homepage
        </Link>
      </div>
    </div>
  );
}
