"use client";

import { useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { ChatWidget } from "@/components/ChatWidget";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { SupportButton } from "@/components/SupportButton";
import { EVENTS, track, trackPageView } from "@/lib/analytics";
import { AuthProvider } from "@/lib/auth";
import { initFirebaseTelemetry } from "@/lib/firebase";
import { installErrorReporting } from "@/lib/errorReporting";
import type { Customer } from "@/lib/types";

/**
 * Everything that has to run in the browser, mounted once.
 *
 * This is the client half of the root layout, and it replaces the Vite app's
 * `main.tsx` plus the router-level effects that lived in `App.tsx`. It sits
 * below the layout rather than inside each page so that the providers, the chat
 * widget and the support button survive navigation instead of remounting and
 * dropping their state every time a link is clicked — which is the same reason
 * the old `App.tsx` kept them outside `<Routes>`.
 */
export function AppShell({
  children,
  initialCustomer,
}: {
  children: ReactNode;
  initialCustomer: Customer | null;
}) {
  return (
    <ErrorBoundary>
      <AuthProvider initialCustomer={initialCustomer}>
        {/* useSearchParams forces a Suspense boundary in the App Router;
            without one, every page that mounts this would opt out of static
            rendering entirely. */}
        <Suspense fallback={null}>
          <Telemetry />
          <ScrollBehaviour />
          <AnalyticsRouteTracking />
        </Suspense>

        {children}

        {/* Outside the page on purpose: they must survive navigation rather
            than remount and drop the conversation every time a link is
            clicked. */}
        <ChatWidget />
        <SupportButton />
      </AuthProvider>
    </ErrorBoundary>
  );
}

/**
 * Crash reporting and Firebase, in this order.
 *
 * The error handlers go on first so that a crash *during* the first render is
 * caught rather than lost — which is exactly when you most want to hear about
 * one. Firebase follows, so that the `exception` event those handlers log has
 * somewhere to go.
 *
 * In an effect rather than at module scope because both touch `window`, and a
 * module body runs during server rendering too.
 */
function Telemetry() {
  const installed = useRef(false);

  useEffect(() => {
    if (installed.current) return;
    installed.current = true;
    installErrorReporting();
    initFirebaseTelemetry();
  }, []);

  return null;
}

/**
 * Router-level scroll behaviour: jump to an #anchor when there is one,
 * otherwise start new pages at the top.
 *
 * Next restores scroll on back/forward and scrolls to top on a push, but it
 * does not handle the hash on a client-side navigation — so the anchor half of
 * this is still ours. Without it, the "Pricing" link in the header lands at the
 * top of the landing page rather than at the pricing section.
 */
function ScrollBehaviour() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    const hash = window.location.hash;
    if (hash) {
      const target = document.querySelector(hash);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }, [pathname, searchParams]);

  return null;
}

/**
 * Page views, which GA4 cannot see on its own here.
 *
 * Its automatic `page_view` fires once, on the initial document load, and never
 * again — the App Router changes the URL with `history.pushState`, which is not
 * a navigation as far as the browser or the measurement snippet is concerned.
 * Without this, every route after the landing page is invisible and the whole
 * funnel reads as one long session on `/`.
 *
 * The first route is also recorded separately as the session's entry point.
 * That question — "where do people arrive" — cannot be answered from
 * `page_view` alone once the app starts emitting them for every hop.
 */
function AnalyticsRouteTracking() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const isFirstRoute = useRef(true);

  useEffect(() => {
    const search = searchParams.toString();
    const url = search ? `${pathname}?${search}` : pathname;

    // The title is set by whichever page just mounted, and that happens in its
    // own effect. A frame's delay means the event carries the new title rather
    // than the previous page's.
    const timer = window.setTimeout(() => {
      trackPageView(url);

      if (isFirstRoute.current) {
        isFirstRoute.current = false;
        track(EVENTS.SESSION_LANDING, {
          entry_path: pathname,
          // Truncated hard: a referrer can carry a full URL with a query
          // string, and GA4 caps values at 100 characters anyway.
          referrer: document.referrer ? new URL(document.referrer).hostname : "direct",
          // The UTM triplet, when the link carried one. Campaign attribution is
          // the main reason anyone asks for entry-point tracking.
          utm_source: searchParams.get("utm_source") ?? undefined,
          utm_medium: searchParams.get("utm_medium") ?? undefined,
          utm_campaign: searchParams.get("utm_campaign") ?? undefined,
        });
      }
    }, 0);

    return () => window.clearTimeout(timer);
  }, [pathname, searchParams]);

  return null;
}
