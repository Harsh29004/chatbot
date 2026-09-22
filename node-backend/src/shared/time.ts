/**
 * IST timestamps, in the exact string shape the Python build wrote.
 *
 * This module exists because the conversion has to stay compatible with data
 * that is already in MongoDB. The Python code stored timestamps by calling
 * `datetime.now(IST).isoformat()`, which produces an offset-aware string like
 * `2026-09-21T18:30:00.123456+05:30`, and then compared them by parsing them
 * back with `datetime.fromisoformat`. Session expiry, subscription periods and
 * invoice dates are all decided by those comparisons.
 *
 * JavaScript has no fixed-offset timezone object and `Date.toISOString()`
 * always renders UTC with a `Z`. Writing `Z` timestamps into the same fields
 * would still *parse*, but the strings would no longer sort alongside the
 * existing ones — and several queries here filter on a string range
 * (`expires_at: {$lt: cutoff}`, `current_period_end: {$lte: now}`) rather than
 * on a date type. A mixed collection would silently mis-sort at the boundary.
 *
 * So: same offset, same layout, microsecond field included.
 */

import { IST_OFFSET_MINUTES } from "../config.js";

const MS_PER_MINUTE = 60_000;

function pad(value: number, width = 2): string {
  return String(Math.trunc(Math.abs(value))).padStart(width, "0");
}

/** The fixed `+05:30` suffix, derived from the configured offset. */
function offsetSuffix(): string {
  const sign = IST_OFFSET_MINUTES >= 0 ? "+" : "-";
  const hours = Math.trunc(Math.abs(IST_OFFSET_MINUTES) / 60);
  const minutes = Math.abs(IST_OFFSET_MINUTES) % 60;
  return `${sign}${pad(hours)}:${pad(minutes)}`;
}

/**
 * Render a moment as an IST ISO-8601 string with the offset attached.
 *
 * The microsecond field is padded to six digits because that is what Python
 * emitted; JavaScript only has millisecond resolution, so the last three are
 * zeros. That is a resolution difference, not a format difference — the
 * strings still compare and parse identically.
 */
export function istISO(date: Date = new Date()): string {
  // Shift the instant so that reading it with the UTC getters yields IST
  // wall-clock values, then label it with the IST offset.
  const shifted = new Date(date.getTime() + IST_OFFSET_MINUTES * MS_PER_MINUTE);

  const yyyy = shifted.getUTCFullYear();
  const mm = pad(shifted.getUTCMonth() + 1);
  const dd = pad(shifted.getUTCDate());
  const hh = pad(shifted.getUTCHours());
  const mi = pad(shifted.getUTCMinutes());
  const ss = pad(shifted.getUTCSeconds());
  const micro = pad(shifted.getUTCMilliseconds(), 3) + "000";

  return `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}.${micro}${offsetSuffix()}`;
}

/** `datetime.now(IST).isoformat()` — the value every store writes. */
export function nowISO(): string {
  return istISO();
}

/** Today in IST as `YYYY-MM-DD`, the key the daily-usage counter is stored under. */
export function todayIST(date: Date = new Date()): string {
  const shifted = new Date(date.getTime() + IST_OFFSET_MINUTES * MS_PER_MINUTE);
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(
    shifted.getUTCDate(),
  )}`;
}

/** `YYYY-MM-DD` for *days* ago in IST. Used for the seven-day usage window. */
export function daysAgoIST(days: number, from: Date = new Date()): string {
  return todayIST(new Date(from.getTime() - days * 86_400_000));
}

/**
 * The next midnight IST, as an ISO string.
 *
 * This is the credit-reset boundary shown in `X-Credits-Reset-At`. Python
 * built it by taking now-in-IST, zeroing the time fields, and adding a day if
 * that landed in the past — reproduced exactly.
 */
export function nextResetTime(from: Date = new Date()): string {
  const shifted = new Date(from.getTime() + IST_OFFSET_MINUTES * MS_PER_MINUTE);
  // Midnight of the current IST day, expressed back as a real instant.
  const midnightShifted = Date.UTC(
    shifted.getUTCFullYear(),
    shifted.getUTCMonth(),
    shifted.getUTCDate(),
  );
  let midnight = midnightShifted - IST_OFFSET_MINUTES * MS_PER_MINUTE;
  if (midnight <= from.getTime()) {
    midnight += 86_400_000;
  }
  return istISO(new Date(midnight));
}

/** Add whole days to an instant. */
export function addDays(date: Date, days: number): Date {
  return new Date(date.getTime() + days * 86_400_000);
}

/** Add seconds to an instant. */
export function addSeconds(date: Date, seconds: number): Date {
  return new Date(date.getTime() + seconds * 1000);
}

/**
 * Parse a stored timestamp back to an instant.
 *
 * Stored values always carry an offset, so `new Date(...)` is unambiguous.
 * A value that cannot be parsed returns `null` rather than an Invalid Date,
 * because every caller is deciding "has this expired?" and an Invalid Date
 * compares false against everything — which would quietly answer "no".
 */
export function parseISO(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** A naive UTC `Date`, matching Python's `datetime.now(utc).replace(tzinfo=None)`. */
export function nowUTCNaive(): Date {
  return new Date();
}
