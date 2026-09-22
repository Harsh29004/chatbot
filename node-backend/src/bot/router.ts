/**
 * Template picking, sheet upload, and the public ask endpoint.
 *
 * Everything this file used to do itself now happens in the Python bot service.
 * What is left is the half that has to stay here: **deciding who is asking and
 * what it costs.**
 *
 * Every route below follows the same three steps:
 *
 *   1. authenticate — a session cookie for the dashboard routes, an
 *      `X-Api-Key` (with credits metered) for `/v1/ask`;
 *   2. resolve the caller to an API-key account id;
 *   3. forward to the bot service and return what it says.
 *
 * Step 2 is the important one. The bot service takes a `user_id` and trusts it,
 * because it is unreachable from anywhere except this process — so this file is
 * the boundary where a cookie or a key stops being a credential and becomes an
 * identity. Nothing downstream re-checks it, which is exactly why nothing
 * downstream can get it wrong.
 *
 * Errors from the service come back as-is when they are 4xx: "that sheet has no
 * Question column" is written for the person who made the sheet, and wrapping
 * it in a generic message would throw away the only useful part.
 */

import { Router, type Request } from "express";
import multer from "multer";
import { z } from "zod";

import { getUserByEmail } from "../shared/apiKeys.js";
import { keyRecordOf, verifyApiKey } from "../shared/auth.js";
import {
  asyncHandler,
  badRequest,
  conflict,
  parseBody,
  parseQuery,
  HTTPException,
} from "../shared/http.js";
import type { Doc } from "../shared/mongo.js";

import { customerOf, requireCustomer } from "../billing/router.js";
import { botService } from "./client.js";

export const router = Router();

// Sheets are small; anything larger is a mistake worth rejecting before it is
// read into memory — and well before it is forwarded over the wire. The bot
// service checks the same ceiling again on its side.
export const MAX_SHEET_BYTES = 5 * 1024 * 1024; // 5 MB

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: MAX_SHEET_BYTES },
});

// Behind a reverse proxy the request's own base URL is the internal address;
// PUBLIC_API_ORIGIN pins the one customers' sites should call. It is passed to
// the bot service rather than derived there, because that service only ever
// sees a loopback origin and would bake the wrong host into every widget.
const PUBLIC_API_ORIGIN = (process.env.PUBLIC_API_ORIGIN ?? "").trim().replace(/\/+$/, "");

function apiBase(req: Request): string {
  if (PUBLIC_API_ORIGIN) return PUBLIC_API_ORIGIN;
  return `${req.protocol}://${req.get("host")}`;
}

const selectTemplateSchema = z.object({
  template_id: z.string(),
  name: z.string().max(80).default(""),
});

const answeringModeSchema = z.object({ enabled: z.boolean() });

const askSchema = z.object({
  message: z.string().min(1).max(2000),
  session_id: z.string().min(1).max(128),
});

const gapsQuerySchema = z.object({ days: z.coerce.number().int().default(30) });

/**
 * Map a signed-in customer to their API-key account.
 *
 * The two are linked by email: billing knows the person, `shared/apiKeys.ts`
 * knows the account their keys, credits and bot hang off. This is the id the
 * bot service works in.
 */
async function accountUserId(customer: Doc): Promise<string> {
  const user = await getUserByEmail(customer.email);
  if (user === null) {
    throw conflict("No API account yet — create an API key first.");
  }
  return user.id;
}

// ---------------------------------------------------------------------------
// Templates (public — the picker is on the marketing site too)
// ---------------------------------------------------------------------------

/** The ready-made bots a customer can choose from, minus any retired ones. */
router.get(
  "/api/templates",
  asyncHandler(async (_req, res) => {
    res.json(await botService.templates());
  }),
);

/**
 * A pre-filled CSV in the right shape for this template.
 *
 * Streamed straight through with the service's own content type and filename —
 * this is a file the browser will save, and re-deriving either here would only
 * be a chance to get them wrong.
 */
router.get(
  "/api/templates/:templateId/starter-sheet",
  asyncHandler(async (req, res) => {
    const file = await botService.starterSheet(req.params.templateId);

    res.setHeader("Content-Type", file.contentType);
    if (file.disposition) res.setHeader("Content-Disposition", file.disposition);
    res.send(file.body);
  }),
);

// ---------------------------------------------------------------------------
// The customer's bot (dashboard, session cookie)
// ---------------------------------------------------------------------------

router.get(
  "/api/bot",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const userId = await accountUserId(customerOf(req));
    res.json(await botService.bot(userId));
  }),
);

/**
 * Pick or switch the template.
 *
 * Switching keeps the indexed sheet — the template decides scope and wording,
 * the sheet decides facts. Both of those rules live in the bot service; this
 * only says who is asking.
 */
router.put(
  "/api/bot",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const body = parseBody(selectTemplateSchema, req.body);
    const userId = await accountUserId(customerOf(req));
    res.json(await botService.selectTemplate(userId, body.template_id, body.name));
  }),
);

/**
 * Turn grounded rewording on or off for this bot.
 *
 * The bot service refuses this when no local model is reachable, because
 * silently accepting a setting that cannot take effect is how someone ends up
 * believing a feature is live when it isn't. That 503 passes through.
 */
router.put(
  "/api/bot/answering",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const body = parseBody(answeringModeSchema, req.body);
    const userId = await accountUserId(customerOf(req));
    res.json(await botService.setAnsweringMode(userId, body.enabled));
  }),
);

/**
 * Upload the FAQ. This replaces whatever was indexed before.
 *
 * The file is buffered here only long enough to check its size and attach the
 * account it belongs to, then forwarded. Everything that decides what is *in*
 * it — the format sniffing, the column aliasing, the embedding — is the bot
 * service's job.
 */
router.post(
  "/api/bot/sheet",
  requireCustomer,
  upload.single("file"),
  asyncHandler(async (req, res) => {
    const userId = await accountUserId(customerOf(req));

    const file = req.file;
    if (!file) throw badRequest("No file was uploaded.");

    if (file.size > MAX_SHEET_BYTES) {
      throw new HTTPException(
        413,
        `That file is larger than ${Math.floor(MAX_SHEET_BYTES / (1024 * 1024))} MB.`,
      );
    }

    res.json(await botService.uploadSheet(userId, file.originalname ?? "", file.buffer));
  }),
);

/**
 * What your bot couldn't answer — the list of rows worth adding to your sheet.
 *
 * Scoped to this account's bot by the id resolved above. These are questions
 * real users asked, so leaking them across tenants would be leaking someone
 * else's customers — and the only id the service ever receives is this one.
 */
router.get(
  "/api/bot/gaps",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const query = parseQuery(gapsQuerySchema, req.query);
    const userId = await accountUserId(customerOf(req));
    res.json(await botService.gaps(userId, Math.max(1, Math.min(query.days, 365))));
  }),
);

/**
 * Try a question from the dashboard without spending credits.
 *
 * Testing your own bot shouldn't cost you anything — that is exactly the
 * activity we want people doing before they go live. Note there is no
 * `verifyApiKey` here: that middleware is what charges, and its absence is the
 * whole feature.
 */
router.post(
  "/api/bot/preview",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const body = parseBody(askSchema, req.body);
    const customer = customerOf(req);
    const userId = await accountUserId(customer);

    res.json(await botService.ask(userId, body.message, `preview:${customer.id}`));
  }),
);

// ---------------------------------------------------------------------------
// The public endpoint customers integrate against
// ---------------------------------------------------------------------------

/**
 * Ask this account's bot a question.
 *
 * Requires `X-Api-Key`. `verifyApiKey` has already validated the key, checked
 * the account's pooled credits, charged for the message by length and set the
 * `X-Credits-*` headers by the time this handler runs — so all that is left is
 * to hand the question to the service that can answer it.
 */
router.post(
  "/v1/ask",
  verifyApiKey,
  asyncHandler(async (req, res) => {
    const body = parseBody(askSchema, req.body);
    const keyRecord = keyRecordOf(req);

    res.json(await botService.ask(keyRecord.user_id, body.message, body.session_id));
  }),
);

export { apiBase };
