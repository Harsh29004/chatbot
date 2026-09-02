import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Page } from "../components/Chrome";
import { api } from "../lib/api";

/**
 * Where the payment provider sends people back to.
 *
 * This page never claims a payment succeeded — it reports what the server
 * says. With the development provider nothing is charged at all, and the
 * copy says exactly that rather than showing a fake receipt.
 */
export function CheckoutReturn() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reference = params.get("reference");
  const isDevCheckout = reference?.startsWith("manual_") ?? false;

  const confirmDev = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.confirmManual();
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page>
      <main className="auth-page">
        <div className="auth-card fade-up">
          <div className="card">
            {isDevCheckout ? (
              <>
                <h1 className="h-card mb-4">Development checkout</h1>
                <p className="small">
                  No payment provider is configured, so nothing was charged and
                  your plan is still pending. This screen exists so the flow can
                  be tested end to end.
                </p>
                <p className="tiny mt-4">
                  Reference <code className="mono">{reference}</code>
                </p>

                {error && <div className="error-box mt-4">{error}</div>}

                <div className="row gap-3 mt-5" style={{ flexWrap: "wrap" }}>
                  <button
                    className="btn btn-secondary"
                    onClick={confirmDev}
                    disabled={busy}
                  >
                    {busy ? <span className="spinner" /> : "Activate without payment (dev)"}
                  </button>
                  <Link to="/dashboard" className="btn btn-ghost">
                    Back to dashboard
                  </Link>
                </div>
                <p className="tiny mt-4">
                  The server refuses this unless <code className="mono">BILLING_ALLOW_MANUAL=true</code>.
                </p>
              </>
            ) : (
              <>
                <h1 className="h-card mb-4">Thanks — we're confirming your payment</h1>
                <p className="small">
                  Your provider is finishing up. Plans activate as soon as the
                  payment is confirmed, which is usually a few seconds. Your
                  dashboard will show the new plan once it lands.
                </p>
                <div className="mt-5">
                  <Link to="/dashboard" className="btn btn-primary">
                    Go to dashboard
                  </Link>
                </div>
              </>
            )}
          </div>
        </div>
      </main>
    </Page>
  );
}
