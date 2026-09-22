/**
 * The operational snapshot the owner assistant reasons over.
 *
 * The snapshot itself is built in the Python bot service, because it is
 * assembled entirely from bot-owned data: every bot on the platform, each
 * one's indexed row count, and the questions their retrieval could not answer.
 * Node has no business counting rows in a collection it never writes.
 *
 * What stays here is the shape of the call. This module is the seam the owner
 * router talks to, so that route did not have to learn where the data lives.
 *
 * On the cross-tenant exception
 * -----------------------------
 * This is the one deliberate hole in tenant isolation. Everywhere else a query
 * is scoped to one account and crossing that line is the bug; here crossing it
 * is the point, because "which bots are struggling this week?" cannot be
 * answered one tenant at a time.
 *
 * Two things keep that safe rather than reckless, and both are unchanged by the
 * move to a separate service:
 *
 * * it is reachable only behind `verifyOwnerKey` — an `nxo_` key, minted
 *   locally by a script with no HTTP surface, and
 * * the snapshot carries **aggregates and question text, never identities**.
 *   Bots appear as ids and templates, not as customer names or emails. An owner
 *   looking for operational problems does not need to know whose bot it is to
 *   find them, and the model certainly doesn't.
 */

import { botService } from "./bot/client.js";

export interface Snapshot {
  generated_at: string;
  window_days: number;
  totals: Record<string, number>;
  bots: Array<Record<string, unknown>>;
  top_gaps: Array<Record<string, unknown>>;
}

/**
 * Gather cross-tenant operational state for the last *days* days.
 *
 * Returned as plain data so the caller can hand it to a model, serve it as
 * JSON, or assert against it in a test without any of those coupling to the
 * other two.
 */
export async function buildSnapshot(options: { days?: number } = {}): Promise<Snapshot> {
  const result = await botService.snapshot(options.days ?? 7);
  return result.snapshot as Snapshot;
}

/**
 * The snapshot and the flattened text a model reads, in one round trip.
 *
 * The owner endpoint needs both — the prose for the model, the numbers for the
 * response so the answer is checkable — and fetching them separately would
 * build the snapshot twice.
 */
export async function buildSnapshotWithPrompt(
  options: { days?: number } = {},
): Promise<{ snapshot: Snapshot; rendered: string }> {
  const result = await botService.snapshot(options.days ?? 7);
  return { snapshot: result.snapshot as Snapshot, rendered: result.rendered };
}

/**
 * Flatten a snapshot into compact text for a model prompt.
 *
 * Kept as an export because the owner router's signature reads better for it,
 * but the rendering happens in the bot service alongside the data — terse and
 * tabular rather than JSON, because a small local model reads a short table far
 * more reliably than nested braces, and every token spent on punctuation is one
 * not spent on the actual rows.
 *
 * Prefer {@link buildSnapshotWithPrompt}, which gets both in one call.
 */
export async function renderSnapshot(snapshot: Snapshot): Promise<string> {
  const result = await botService.snapshot(snapshot.window_days);
  return result.rendered;
}
