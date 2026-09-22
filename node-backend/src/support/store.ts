/**
 * Storage for support conversations.
 *
 * Port of `backend/support/store.py`.
 *
 * One conversation per customer, created on first use. Two read markers per
 * conversation — one for each side — because "unread" means something different
 * depending on who is asking, and a single `last_read_at` would have staff and
 * customer overwriting each other's badge.
 *
 * Read position is a **message id**, not a timestamp. Wall-clock time is the
 * wrong tool: on Windows two consecutive now() calls routinely return the same
 * value, so a message arriving in the same tick as a read marker would compare
 * as already-read and never show as unread. ObjectIds keep that property — they
 * carry a timestamp and a counter and compare in generation order — so the
 * marker survived the move to MongoDB unchanged, and survives this port too.
 */

import {
  coll,
  document,
  documents,
  objectId,
  registerIndexes,
  toObjectId,
  type Doc,
} from "../shared/mongo.js";
import { nowISO } from "../shared/time.js";

export const SENDER_CUSTOMER = "customer";
export const SENDER_STAFF = "staff";

export const CONVERSATIONS = "support_conversations";
export const MESSAGES = "support_messages";

registerIndexes(CONVERSATIONS, [
  [{ customer_id: 1 }, { unique: true, name: "uniq_customer" }],
  [{ last_message_at: -1 }, { name: "by_recent" }],
]);

registerIndexes(MESSAGES, [
  [{ conversation_id: 1, _id: 1 }, { name: "by_conversation" }],
]);

// Which marker belongs to which side. Keyed rather than branched at each call
// site, so a new participant type is one entry instead of five if-statements.
const READ_MARKER: Record<string, string> = {
  [SENDER_CUSTOMER]: "customer_read_msg_id",
  [SENDER_STAFF]: "staff_read_msg_id",
};

function markerFor(role: string): string {
  return READ_MARKER[role] ?? "staff_read_msg_id";
}

/** Kept as an entry point for startup; MongoDB needs no schema built. */
export function initSupportTables(): void {
  return;
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

/**
 * This customer's conversation, created if they've never written before.
 *
 * The email and name are copied onto the document rather than joined at read
 * time. Staff need to know who they are talking to, and the inbox listing
 * should not have to reach into the billing collections to render a name.
 */
export async function getOrCreateConversation(customer: Doc): Promise<Doc> {
  const now = nowISO();
  const conversations = await coll(CONVERSATIONS);

  const doc = await conversations.findOneAndUpdate(
    { customer_id: toObjectId(customer.id) },
    {
      $setOnInsert: {
        customer_id: toObjectId(customer.id),
        customer_email: customer.email ?? "",
        customer_name: customer.name ?? "",
        last_message_at: now,
        last_message_preview: "",
        customer_read_msg_id: null,
        staff_read_msg_id: null,
        created_at: now,
      },
    },
    { upsert: true, returnDocument: "after" },
  );

  return document(doc)!;
}

/** Staff-side lookup by conversation id. Not reachable from a customer route. */
export async function getConversation(conversationId: unknown): Promise<Doc | null> {
  const oid = objectId(conversationId);
  if (oid === null) return null;
  const conversations = await coll(CONVERSATIONS);
  return document(await conversations.findOne({ _id: oid }));
}

/**
 * Every conversation, most recently active first, with staff's unread count.
 *
 * Cross-customer by definition — this is the staff inbox, and it is the only
 * function here that is. It sits behind the admin key.
 */
export async function listConversations(limit = 200): Promise<Doc[]> {
  const collection = await coll(CONVERSATIONS);
  const rows = documents(
    await collection
      .find()
      // _id breaks the tie when two conversations share a timestamp, which
      // coarse clocks make common rather than exotic.
      .sort({ last_message_at: -1, _id: -1 })
      .limit(limit)
      .toArray(),
  );

  for (const conversation of rows) {
    conversation.unread = await unreadAfter(
      conversation.id,
      SENDER_CUSTOMER,
      conversation.staff_read_msg_id,
    );
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

/**
 * Append a message and roll the conversation's summary forward.
 *
 * The sender's own read marker moves too: you have obviously read what you just
 * wrote, and without this the author's own message counts as unread to them.
 */
export async function addMessage(
  conversationId: unknown,
  sender: string,
  body: string,
): Promise<Doc> {
  const conversationOid = toObjectId(conversationId);
  const now = nowISO();

  const doc = {
    conversation_id: conversationOid,
    sender,
    body,
    created_at: now,
  };

  const messages = await coll(MESSAGES);
  const result = await messages.insertOne({ ...doc });

  const conversations = await coll(CONVERSATIONS);
  await conversations.updateOne(
    { _id: conversationOid },
    {
      $set: {
        last_message_at: now,
        last_message_preview: body.slice(0, 140),
        [markerFor(sender)]: result.insertedId,
      },
    },
  );

  return document({ ...doc, _id: result.insertedId })!;
}

/**
 * Messages in a conversation, optionally only those newer than *afterId*.
 *
 * `afterId` is what makes polling cheap: the page asks for what it hasn't seen
 * rather than re-downloading the whole history every few seconds. An absent or
 * unparseable marker means "from the beginning", which is the right reading of
 * a client that has not seen anything yet.
 */
export async function listMessages(
  conversationId: unknown,
  afterId: unknown = null,
): Promise<Doc[]> {
  const oid = objectId(conversationId);
  if (oid === null) return [];

  const query: Record<string, unknown> = { conversation_id: oid };
  const after = objectId(afterId);
  if (after !== null) query._id = { $gt: after };

  const messages = await coll(MESSAGES);
  return documents(await messages.find(query).sort({ _id: 1 }).toArray());
}

/** Move one side's read marker to the newest message in the conversation. */
export async function markRead(conversationId: unknown, reader: string): Promise<void> {
  const oid = objectId(conversationId);
  if (oid === null) return;

  const messages = await coll(MESSAGES);
  const newest = await messages.findOne(
    { conversation_id: oid },
    { sort: { _id: -1 }, projection: { _id: 1 } },
  );

  const conversations = await coll(CONVERSATIONS);
  await conversations.updateOne(
    { _id: oid },
    { $set: { [markerFor(reader)]: newest ? newest._id : null } },
  );
}

/** Messages from *sender* in this conversation newer than *marker*. */
async function unreadAfter(
  conversationId: unknown,
  sender: string,
  marker: unknown,
): Promise<number> {
  const oid = objectId(conversationId);
  if (oid === null) return 0;

  const query: Record<string, unknown> = { conversation_id: oid, sender };
  const after = objectId(marker);
  if (after !== null) query._id = { $gt: after };

  const messages = await coll(MESSAGES);
  return messages.countDocuments(query);
}

/** How many messages *from the other side* this reader hasn't seen. */
export async function unreadCount(conversationId: unknown, reader: string): Promise<number> {
  const conversation = await getConversation(conversationId);
  if (conversation === null) return 0;

  const other = reader === SENDER_CUSTOMER ? SENDER_STAFF : SENDER_CUSTOMER;
  return unreadAfter(conversationId, other, conversation[markerFor(reader)]);
}

/** Everything waiting on a reply, across all customers — the inbox badge. */
export async function totalUnreadForStaff(): Promise<number> {
  const conversations = await coll(CONVERSATIONS);
  const rows = await conversations
    .find({}, { projection: { _id: 1, staff_read_msg_id: 1 } })
    .toArray();

  let total = 0;
  for (const row of rows) {
    total += await unreadAfter(row._id, SENDER_CUSTOMER, row.staff_read_msg_id);
  }
  return total;
}
