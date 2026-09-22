import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { initFirebaseTelemetry } from "./lib/firebase";
import { installErrorReporting } from "./lib/errorReporting";
import "./styles.css";

// Before render, and in this order. The error handlers go on first so that a
// crash *during* the first render is caught rather than lost — which is
// exactly when you most want to hear about one. Firebase follows, so that the
// `exception` event those handlers log has somewhere to go.
installErrorReporting();
initFirebaseTelemetry();

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
);
