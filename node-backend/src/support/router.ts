/**
 * Support chat endpoints.
 *
 * Port of `backend/support/router.py`.
 *
 * Two audiences, two auth schemes, deliberately separated in the path as well
 * as the middleware:
 *
 * * `/api/support/...` — the customer's own conversation, session cookie. A
 *   customer can only ever reach their own; no route here takes a conversation
 *   id.
 * * `/api/support/admin/...` — the staff inbox, `X-Admin-Key`. Cross-customer
 *   by definition, which is exactly why it is a separate prefix behind a
 *   separate header.
 *
 * That the customer routes take no conversation id is the point. There is no id
 * to guess, no ownership check to forget, and no way to ask for someone else's
 * conversation because the parameter does not exist.
 *
 * Route-order note: the staff routes are registered **before** the customer
 * ones. Express matches in registration order and `/api/support/admin/...` does
 * not collide with any customer path here, but keeping the more specific prefix
 * first is what stops a future `/api/support/:something` from swallowing it.
 */

import { Router } from "express";
import { z } from "zod";

import { customerOf, requireCustomer } from "../billing/router.js";
import { verifyAdminKey } from "../shared/auth.js";
import { asyncHandler, badRequest, notFound, parseBody, parseQuery } from "../shared/http.js";
import * as inputPolicy from "../shared/inputPolicy.js";
import type { Doc } from "../shared/mongo.js";
import * as store from "./store.js";

export const router = Router();

export const MAX_BODY_CHARS = 4000;

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

const sendMessageSchema = z.object({
  body: z.string().min(1),
});

const afterIdSchema = z.object({
  after_id: z.string().default(""),
});

/**
 * Shared checks for both sides.
 *
 * Only credentials are refused. Support chat is where people paste error
 * messages, config snippets and stack traces — refusing code here would break
 * the one thing a support channel exists for. A password or a card number is
 * different: it would sit in this collection forever, readable by staff, long
 * after the conversation stopped mattering.
 */
function screen(input: string): string {
  const body = input.trim();
  if (!body) throw badRequest("Write something first.");
  if (body.length > MAX_BODY_CHARS) {
    throw badRequest(
      `That message is ${body.length} characters; the limit is ${MAX_BODY_CHARS}.`,
    );
  }

  const verdict = inputPolicy.screen(body, { forModel: false });
  if (inputPolicy.refused(verdict)) throw badRequest(verdict.message);
  return body;
}

async function conversationOr404(conversationId: string): Promise<Doc> {
  const conversation = await store.getConversation(conversationId);
  if (conversation === null) throw notFound("No such conversation.");
  // `unread` is computed per caller, so it must not arrive pre-set from the
  // row and collide with the value the response is built with.
  delete conversation.unread;
  return conversation;
}

// ---------------------------------------------------------------------------
// Staff side — X-Admin-Key, every conversation
// ---------------------------------------------------------------------------

/** Every conversation, most recently active first. */
router.get(
  "/api/support/admin/conversations",
  verifyAdminKey,
  asyncHandler(async (_req, res) => {
    res.json({
      conversations: await store.listConversations(),
      total_unread: await store.totalUnreadForStaff(),
    });
  }),
);

router.get(
  "/api/support/admin/conversations/:conversationId",
  verifyAdminKey,
  asyncHandler(async (req, res) => {
    const query = parseQuery(afterIdSchema, req.query);
    const conversationId = req.params.conversationId;

    const conversation = await conversationOr404(conversationId);

    res.json({
      conversation: {
        ...conversation,
        unread: await store.unreadCount(conversationId, store.SENDER_STAFF),
      },
      messages: await store.listMessages(conversationId, query.after_id),
    });
  }),
);

/** Reply to a customer. */
router.post(
  "/api/support/admin/conversations/:conversationId/messages",
  verifyAdminKey,
  asyncHandler(async (req, res) => {
    const body = parseBody(sendMessageSchema, req.body);
    const conversationId = req.params.conversationId;

    await conversationOr404(conversationId);
    const text = screen(body.body);

    res.status(201).json(await store.addMessage(conversationId, store.SENDER_STAFF, text));
  }),
);

router.post(
  "/api/support/admin/conversations/:conversationId/read",
  verifyAdminKey,
  asyncHandler(async (req, res) => {
    const conversationId = req.params.conversationId;
    await conversationOr404(conversationId);
    await store.markRead(conversationId, store.SENDER_STAFF);
    res.json({ ok: true, total_unread: await store.totalUnreadForStaff() });
  }),
);

// ---------------------------------------------------------------------------
// Customer side — session cookie, own conversation only
// ---------------------------------------------------------------------------

/**
 * This customer's conversation with support.
 *
 * `after_id` returns only newer messages, which is what makes the page cheap to
 * poll — it asks for what it hasn't seen rather than re-downloading the history
 * every few seconds.
 */
router.get(
  "/api/support/messages",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const query = parseQuery(afterIdSchema, req.query);
    const conversation = await store.getOrCreateConversation(customerOf(req));

    res.json({
      messages: await store.listMessages(conversation.id, query.after_id),
      unread: await store.unreadCount(conversation.id, store.SENDER_CUSTOMER),
    });
  }),
);

/** Send a message to the team. */
router.post(
  "/api/support/messages",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const body = parseBody(sendMessageSchema, req.body);
    const text = screen(body.body);

    const conversation = await store.getOrCreateConversation(customerOf(req));
    res.status(201).json(
      await store.addMessage(conversation.id, store.SENDER_CUSTOMER, text),
    );
  }),
);

/** Clear this customer's unread badge once they've looked at the thread. */
router.post(
  "/api/support/read",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const conversation = await store.getOrCreateConversation(customerOf(req));
    await store.markRead(conversation.id, store.SENDER_CUSTOMER);
    res.json({ ok: true });
  }),
);
