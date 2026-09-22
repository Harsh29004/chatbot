import { useEffect, useRef } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { ChatWidget } from "./components/ChatWidget";
import { SupportButton } from "./components/SupportButton";
import { EVENTS, track, trackPageView } from "./lib/analytics";
import { AuthProvider, RequireAuth } from "./lib/auth";
import { SignIn, SignUp } from "./pages/Auth";
import { CheckoutReturn } from "./pages/CheckoutReturn";
import { Admin } from "./pages/Admin";
import { AdminSupport } from "./pages/AdminSupport";
import { Assistant } from "./pages/Assistant";
import { Dashboard } from "./pages/Dashboard";
import { Landing } from "./pages/Landing";
import { Support } from "./pages/Support";

/**
 * Router-level scroll behaviour: jump to an #anchor when there is one,
 * otherwise start new pages at the top. Without this, navigating from the
 * bottom of the landing page lands you at the bottom of the next one.
 */
function ScrollBehaviour() {
  const { pathname, hash } = useLocation();

  useEffect(() => {
    if (hash) {
      const target = document.querySelector(hash);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    }
    window.scrollTo(0, 0);
  }, [pathname, hash]);

  return null;
}

/**
 * Page views, which GA4 cannot see on its own here.
 *
 * Its automatic `page_view` fires once, on the initial document load, and
 * never again — React Router changes the URL with `history.pushState`, which
 * is not a navigation as far as the browser or the measurement snippet is
 * concerned. Without this, every route after the landing page is invisible
 * and the whole funnel reads as one long session on `/`.
 *
 * The first route is also recorded separately as the session's entry point.
 * That question — "where do people arrive" — cannot be answered from
 * `page_view` alone once the SPA starts emitting them for every hop.
 */
function AnalyticsRouteTracking() {
  const { pathname, search } = useLocation();
  const isFirstRoute = useRef(true);

  useEffect(() => {
    // The title is set by whichever page just mounted, and that happens in
    // its own effect. A frame's delay means the event carries the new title
    // rather than the previous page's.
    const timer = window.setTimeout(() => {
      trackPageView(pathname + search);

      if (isFirstRoute.current) {
        isFirstRoute.current = false;
        track(EVENTS.SESSION_LANDING, {
          entry_path: pathname,
          // Truncated hard: a referrer can carry a full URL with a query
          // string, and GA4 caps values at 100 characters anyway.
          referrer: document.referrer ? new URL(document.referrer).hostname : "direct",
          // The UTM triplet, when the link carried one. Campaign attribution
          // is the main reason anyone asks for entry-point tracking.
          utm_source: new URLSearchParams(search).get("utm_source") ?? undefined,
          utm_medium: new URLSearchParams(search).get("utm_medium") ?? undefined,
          utm_campaign: new URLSearchParams(search).get("utm_campaign") ?? undefined,
        });
      }
    }, 0);

    return () => window.clearTimeout(timer);
  }, [pathname, search]);

  return null;
}

export default function App() {
  return (
    <AuthProvider>
      <ScrollBehaviour />
      <AnalyticsRouteTracking />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/signin" element={<SignIn />} />
        <Route path="/signup" element={<SignUp />} />
        <Route
          path="/dashboard"
          element={
            <RequireAuth>
              <Dashboard />
            </RequireAuth>
          }
        />
        <Route
          path="/assistant"
          element={
            <RequireAuth>
              <Assistant />
            </RequireAuth>
          }
        />
        <Route
          path="/support"
          element={
            <RequireAuth>
              <Support />
            </RequireAuth>
          }
        />
        {/* Staff tools. Not behind RequireAuth: they have no session at all,
            they authenticate with the admin key on their own gate. */}
        <Route path="/admin" element={<Admin />} />
        <Route path="/admin/support" element={<AdminSupport />} />
        <Route
          path="/checkout/return"
          element={
            <RequireAuth>
              <CheckoutReturn />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Landing />} />
      </Routes>
      {/* Outside <Routes> on purpose: it must survive navigation rather than
          remount and drop the conversation every time a link is clicked. */}
      <ChatWidget />
      <SupportButton />
    </AuthProvider>
  );
}
