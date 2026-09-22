"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

/**
 * Shared pieces for the admin panel.
 *
 * Every tab does the same three things — fetch with the admin key, show a
 * spinner, show the server's message if it failed — so that lives here once.
 * The tabs themselves are then only about their own numbers.
 */

/** Load admin data for the current key, re-fetching when *deps* change. */
export function useAdminData<T>(
  load: () => Promise<T>,
  deps: unknown[],
): {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  // The loader closes over the caller's key and filters; listing it as a
  // dependency would re-fetch on every render, since it is a new function
  // each time.
  const runner = useCallback(load, deps);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    runner()
      .then((result) => {
        if (alive) {
          setData(result);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "Request failed.");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [runner, nonce]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}

export function Loading() {
  return (
    <div className="wrap-center" style={{ padding: "var(--s6)" }}>
      <div className="spinner" />
    </div>
  );
}

export function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card mb-5">
      <div className="row row-between mb-4" style={{ flexWrap: "wrap", gap: "var(--s3)" }}>
        <h2 className="h-card">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** A headline number with its label. Values are pre-formatted by the caller. */
export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "accent" | "warn";
}) {
  return (
    <div className="admin-stat">
      <span className="admin-stat-label">{label}</span>
      <span className={`admin-stat-value${tone ? ` ${tone}` : ""}`}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </span>
      {hint && <span className="tiny muted">{hint}</span>}
    </div>
  );
}

export function StatRow({ children }: { children: ReactNode }) {
  return <div className="admin-stats">{children}</div>;
}

/** Horizontal bars for a small set of labelled numbers — no chart library. */
export function BarList({
  rows,
  empty = "Nothing yet.",
}: {
  rows: { label: string; value: number; hint?: string }[];
  empty?: string;
}) {
  if (rows.length === 0) return <p className="small muted">{empty}</p>;
  const peak = Math.max(...rows.map((r) => r.value), 1);

  return (
    <div className="admin-bars">
      {rows.map((row) => (
        <div className="admin-bar-row" key={row.label}>
          <span className="admin-bar-label" title={row.label}>
            {row.label}
          </span>
          <span className="admin-bar-track">
            <span
              className="admin-bar-fill"
              style={{ width: `${Math.max(2, (row.value / peak) * 100)}%` }}
            />
          </span>
          <span className="admin-bar-value mono tiny">
            {row.value.toLocaleString()}
            {row.hint ? ` ${row.hint}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * A day-by-day sparkline, drawn as bars.
 *
 * Deliberately not a charting library: this is one series of small integers,
 * and a dependency to draw it would be larger than the panel it appears in.
 */
export function DayChart({
  points,
  label,
}: {
  points: { date: string; value: number }[];
  label: string;
}) {
  if (points.length === 0) return <p className="small muted">No activity in this window.</p>;
  const peak = Math.max(...points.map((p) => p.value), 1);

  return (
    <div className="admin-chart" role="img" aria-label={`${label} by day`}>
      {points.map((point) => (
        <span
          className="admin-chart-col"
          key={point.date}
          title={`${point.date}: ${point.value.toLocaleString()} ${label}`}
        >
          <span
            className="admin-chart-bar"
            style={{ height: `${Math.max(2, (point.value / peak) * 100)}%` }}
          />
        </span>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="small muted">{children}</p>;
}

/** `2026-09-08T16:32:28+05:30` -> `2026-09-08 16:32`, or "—" when absent. */
export function when(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 16).replace("T", " ");
}

export function money(display: string, currency: string): string {
  return currency === "USD" ? `$${display}` : `${display} ${currency}`;
}
