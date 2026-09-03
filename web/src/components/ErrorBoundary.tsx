import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches render errors so one broken component doesn't leave a blank page.
 *
 * Without this, any exception thrown during render unmounts the whole tree and
 * the customer sees pure white with nothing in the UI to explain it or act on —
 * the worst possible failure mode for a product people are paying for.
 *
 * Only render errors reach here. Errors inside event handlers and promises do
 * not, which is why the screens handle their own async failures locally.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the stack in the console for whoever is debugging; the customer
    // gets the friendly version below.
    console.error("Unhandled render error:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="shell">
        <main className="auth-page">
          <div className="auth-card">
            <div className="card">
              <h1 className="h-card mb-4">Something broke on this page</h1>
              <p className="small">
                That's our fault, not yours. Reloading usually clears it — your
                account, keys and indexed sheet are unaffected.
              </p>

              <div className="row gap-3 mt-5" style={{ flexWrap: "wrap" }}>
                <button
                  className="btn btn-primary"
                  onClick={() => window.location.reload()}
                >
                  Reload the page
                </button>
                <a className="btn btn-secondary" href="/">
                  Back to the start
                </a>
              </div>

              <details className="mt-5">
                <summary className="tiny" style={{ cursor: "pointer" }}>
                  Technical details
                </summary>
                <pre
                  className="mono tiny mt-3"
                  style={{ whiteSpace: "pre-wrap", overflowX: "auto" }}
                >
                  {this.state.error.message}
                </pre>
              </details>
            </div>
          </div>
        </main>
      </div>
    );
  }
}
