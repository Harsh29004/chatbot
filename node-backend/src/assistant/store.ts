/**
 * Conversation storage for the dashboard assistant.
 *
 * Port of `backend/assistant/store.py`.
 *
 * Threads and messages, in the same MongoDB database as everything else. Two
 * things here are worth knowing.
 *
 * **Every read is scoped by customer_id.** Not "the caller should pass the
 * right id" — the queries take it and filter on it, so there is no accessor
 * that can return someone else's conversation by being called carelessly.
 *
 * **History is capped when it is read, not when it is written.** The full
 * thread is kept for the person to scroll; only the last N turns are handed to
 * the model. Those are different jobs and a single limit would do one of them
 * badly.
 */

import { ASSISTANT_HISTORY_TURNS } from "../config.js";
import {
  coll,
  document,
  documents,
  objectId,
  registerIndexes,
  toObjectId,
  type Doc,
} from "../shared/mongo.js";
import { nowISO, todayIST } from "../shared/time.js";

export const ROLE_USER = "user";
export const ROLE_ASSISTANT = "assistant";

export const THREADS = "assistant_threads";
export const MESSAGES = "assistant_messages";

registerIndexes(THREADS, [
  [{ customer_id: 1, updated_at: -1 }, { name: "by_customer" }],
]);

registerIndexes(MESSAGES, [
  [{ thread_id: 1, _id: 1 }, { name: "by_thread" }],
  // countMessagesToday filters on all three at once.
  [
    { customer_id: 1, role: 1, created_at: 1 },
    { name: "by_customer_role_day" },
  ],
]);

/** Kept as an entry point for startup; MongoDB needs no schema built. */
export function initAssistantTables(): void {
  return;
}

// ---------------------------------------------------------------------------
// Threads
// ---------------------------------------------------------------------------

export async function createThread(customerId: unknown, title = "New chat"): Promise<Doc> {
  const now = nowISO();
  const doc = {
    customer_id: toObjectId(customerId),
    title: title.slice(0, 120) || "New chat",
    created_at: now,
    updated_at: now,
  };

  const threads = await coll(THREADS);
  const result = await threads.insertOne({ ...doc });
  return document({ ...doc, _id: result.insertedId })!;
}

/** One thread, or null. Scoped — another customer's id simply doesn't match. */
export async function getThread(customerId: unknown, threadId: unknown): Promise<Doc | null> {
  const threadOid = objectId(threadId);
  const customerOid = objectId(customerId);
  if (threadOid === null || customerOid === null) return null;

  const threads = await coll(THREADS);
  return document(await threads.findOne({ _id: threadOid, customer_id: customerOid }));
}

export async function listThreads(customerId: unknown, limit = 50): Promise<Doc[]> {
  const oid = objectId(customerId);
  if (oid === null) return [];

  const threads = await coll(THREADS);
  return documents(
    await threads.find({ customer_id: oid }).sort({ updated_at: -1 }).limit(limit).toArray(),
  );
}

export async function renameThread(
  customerId: unknown,
  threadId: unknown,
  title: string,
): Promise<Doc | null> {
  const threadOid = objectId(threadId);
  const customerOid = objectId(customerId);
  if (threadOid === null || customerOid === null) return null;

  // Scoped in the update itself rather than checked first, so there is no
  // window in which the ownership test and the write disagree.
  const threads = await coll(THREADS);
  return document(
    await threads.findOneAndUpdate(
      { _id: threadOid, customer_id: customerOid },
      { $set: { title: title.slice(0, 120) || "New chat", updated_at: nowISO() } },
      { returnDocument: "after" },
    ),
  );
}

export async function deleteThread(customerId: unknown, threadId: unknown): Promise<boolean> {
  const threadOid = objectId(threadId);
  const customerOid = objectId(customerId);
  if (threadOid === null || customerOid === null) return false;

  const threads = await coll(THREADS);
  const deleted = (await threads.deleteOne({ _id: threadOid, customer_id: customerOid }))
    .deletedCount;

  // Messages are removed explicitly. MongoDB has no cascade, and a thread whose
  // messages outlived it would be invisible but still stored.
  if (deleted) {
    const messages = await coll(MESSAGES);
    await messages.deleteMany({ thread_id: threadOid });
  }
  return Boolean(deleted);
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

/**
 * Append a message, if the thread belongs to *customerId*.
 *
 * Returns null when it doesn't, rather than writing into someone else's
 * conversation.
 */
export async function addMessage(
  customerId: unknown,
  threadId: unknown,
  role: string,
  content: string,
): Promise<Doc | null> {
  if ((await getThread(customerId, threadId)) === null) return null;

  const now = nowISO();
  const doc = {
    thread_id: toObjectId(threadId),
    // Denormalised so "how many did this person send today?" is one indexed
    // read instead of a join. Messages are never reassigned to another
    // customer, so the copy cannot drift from the thread it belongs to.
    customer_id: toObjectId(customerId),
    role,
    content,
    created_at: now,
  };

  const messages = await coll(MESSAGES);
  const result = await messages.insertOne({ ...doc });

  const threads = await coll(THREADS);
  await threads.updateOne({ _id: toObjectId(threadId) }, { $set: { updated_at: now } });

  return document({ ...doc, _id: result.insertedId });
}

/** The whole conversation, for display. */
export async function listMessages(customerId: unknown, threadId: unknown): Promise<Doc[]> {
  if ((await getThread(customerId, threadId)) === null) return [];

  const messages = await coll(MESSAGES);
  return documents(
    await messages.find({ thread_id: objectId(threadId) }).sort({ _id: 1 }).toArray(),
  );
}

/**
 * The tail of the conversation, shaped for the model's `messages` array.
 *
 * Capped at `ASSISTANT_HISTORY_TURNS` because every extra turn is more prompt
 * for a CPU to re-read on each reply, and a 7B model loses the thread long
 * before it runs out of context window.
 */
export async function historyForModel(
  customerId: unknown,
  threadId: unknown,
): Promise<Array<{ role: string; content: string }>> {
  const messages = await listMessages(customerId, threadId);
  const tail = messages.slice(-ASSISTANT_HISTORY_TURNS);
  return tail.map((message) => ({ role: message.role, content: message.content }));
}

// ---------------------------------------------------------------------------
// Usage
// ---------------------------------------------------------------------------

/**
 * How many questions this customer has asked today (IST).
 *
 * Generation on two ARM cores is the scarcest thing this box has, so it is
 * rationed per person rather than left open.
 */
export async function countMessagesToday(customerId: unknown): Promise<number> {
  const oid = objectId(customerId);
  if (oid === null) return 0;

  const today = todayIST();
  const messages = await coll(MESSAGES);
  return messages.countDocuments({
    customer_id: oid,
    role: ROLE_USER,
    // created_at is an ISO timestamp, so "today" is a prefix range. Stated as a
    // range rather than a regex so the index can serve it. U+FFFF sorts above
    // every character a timestamp can contain.
    created_at: { $gte: today, $lt: today + "￿" },
  });
}
