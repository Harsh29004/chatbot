/**
 * The gap log: every NEAR_MATCH and NO_MATCH a bot produces.
 *
 * Port of `backend/shared/logging_store.py`.
 *
 * This is the list a customer works from when deciding what to add to their FAQ
 * next, so it is grouped and ranked rather than served raw — the same question
 * asked forty times is one line saying "forty".
 *
 * Stored in MongoDB, one document per miss, in `unmatched_queries`.
 */

import { coll, documents, registerIndexes, type Doc } from "./mongo.js";

export const COLLECTION = "unmatched_queries";

// Every gap-list read filters by bot and date, so that pair is the index.
// Without it the query degrades into a collection scan as misses accumulate
// across every tenant.
registerIndexes(COLLECTION, [
  [{ bot_type: 1, timestamp: -1 }, { name: "bot_time" }],
  [{ bot_type: 1, flagged_injection: 1, timestamp: -1 }, { name: "bot_flagged_time" }],
]);

/**
 * Timestamps here are UTC, not IST.
 *
 * This differs from the billing stores on purpose — it is what the Python
 * module did (`datetime.now(timezone.utc).isoformat()`), and the `$gte`
 * comparisons below are string comparisons against values written the same way.
 * Changing the format would silently break the date window on every existing
 * row.
 */
function nowUTC(): string {
  return new Date().toISOString();
}

function since(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

/**
 * Kept as a no-op entry point.
 *
 * MongoDB creates a collection on first write, so there is no schema to build.
 * The function stays because startup calls it alongside the other stores'
 * initialisers, and because a database that needs no setup should say so out
 * loud rather than by being absent.
 */
export function initDb(): void {
  return;
}

/** Record one miss. */
export async function logQuery(options: {
  botType: string;
  queryText: string;
  topMatchScore?: number | null;
  topMatchQuestion?: string | null;
  flaggedInjection?: boolean;
  sessionId?: string | null;
}): Promise<void> {
  const misses = await coll(COLLECTION);
  await misses.insertOne({
    bot_type: options.botType,
    query_text: options.queryText,
    top_match_score: options.topMatchScore ?? null,
    top_match_question: options.topMatchQuestion ?? null,
    // Stored as 0/1 rather than a boolean so the value reads the same way in
    // the admin aggregations that sum it.
    flagged_injection: options.flaggedInjection ? 1 : 0,
    session_id: options.sessionId ?? null,
    timestamp: nowUTC(),
  });
}

/**
 * Every row, across every bot.
 *
 * Tests and admin debugging only — this is deliberately **not** scoped to a
 * tenant. Never put it behind a customer-facing endpoint: the rows are other
 * people's end-users' questions. Use {@link getGapSummary} for that.
 */
export async function getAllLogs(): Promise<Doc[]> {
  const misses = await coll(COLLECTION);
  return documents(await misses.find().sort({ _id: 1 }).toArray());
}

export interface Gap {
  question: string;
  times_asked: number;
  last_asked: string;
  best_score: number;
}

/**
 * What one bot failed to answer, grouped and ranked by how often it was asked.
 *
 * Scoped to *botLabel* — the caller must pass the label belonging to the
 * signed-in account and nothing else.
 *
 * `best_score` is how close the bot got: high means the answer is nearly there
 * and probably needs an alternate phrasing, low means the sheet does not cover
 * it at all.
 *
 * Injection attempts are excluded. They are attacks, not gaps, and putting them
 * in a customer's to-do list is noise.
 */
export async function getGapSummary(
  botLabel: string,
  options: { days?: number; limit?: number } = {},
): Promise<Gap[]> {
  const days = options.days ?? 30;
  const limit = options.limit ?? 50;

  const collection = await coll(COLLECTION);
  const misses = await collection
    .find({
      bot_type: botLabel,
      timestamp: { $gte: since(days) },
      flagged_injection: 0,
    })
    .toArray();

  // Grouped on the normalised question, so "Do you deliver?" and
  // " do you deliver? " count as one gap, while the text displayed stays
  // whichever casing an actual person typed.
  const gaps = new Map<string, Gap>();
  for (const miss of misses) {
    const key = String(miss.query_text).trim().toLowerCase();
    let gap = gaps.get(key);
    if (gap === undefined) {
      gap = { question: miss.query_text, times_asked: 0, last_asked: "", best_score: 0 };
      gaps.set(key, gap);
    }
    gap.times_asked += 1;
    // Python used `max()` on strings here — lexicographic on an ISO timestamp
    // is chronological, which is why it worked and why it is kept.
    gap.last_asked = gap.last_asked > miss.timestamp ? gap.last_asked : miss.timestamp;
    gap.best_score = Math.max(gap.best_score, Number(miss.top_match_score) || 0);
    gap.question = gap.question > miss.query_text ? gap.question : miss.query_text;
  }

  const ranked = [...gaps.values()].sort((a, b) => {
    // Python's `sorted(..., key=(times_asked, last_asked), reverse=True)`.
    if (a.times_asked !== b.times_asked) return b.times_asked - a.times_asked;
    return a.last_asked < b.last_asked ? 1 : a.last_asked > b.last_asked ? -1 : 0;
  });

  return ranked.slice(0, Math.max(1, limit));
}

/** How many inputs to this bot tripped the injection detector. */
export async function countFlaggedInputs(
  botLabel: string,
  options: { days?: number } = {},
): Promise<number> {
  const days = options.days ?? 30;
  const misses = await coll(COLLECTION);
  return misses.countDocuments({
    bot_type: botLabel,
    timestamp: { $gte: since(days) },
    flagged_injection: 1,
  });
}
