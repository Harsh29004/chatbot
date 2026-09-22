/**
 * The client for the Python bot service.
 *
 * Retrieval, sheet ingestion, templates and the widget live in
 * `../../../bot-service` — in Python, because that is where the embedding
 * model, the vector maths and the spreadsheet readers are. This module is the
 * only way Node reaches them.
 *
 * The contract, in one line: **Node decides who you are and what it costs;
 * this service decides what the answer is.** Every call below passes a
 * `user_id` that Node has already resolved from a session cookie or an API
 * key. No credential crosses this boundary, because by the time a call is made
 * there is nothing left to authenticate.
 *
 * Failure handling matters here. A 4xx from the bot service is a real answer
 * about the request — "no such template", "that sheet has no Question column",
 * "upload a sheet first" — and those messages are written for the person who
 * will read them, so they are passed straight through with their status. Only
 * a transport failure or a 5xx becomes a generic 502, because that is Node's
 * problem and not something a customer can act on.
 */

import { Readable } from "node:stream";

import { HTTPException, serviceUnavailable } from "../shared/http.js";
import { logger } from "../shared/logger.js";

const BOT_SERVICE_URL = (
  process.env.BOT_SERVICE_URL ??
  process.env.EMBEDDINGS_URL ??
  "http://127.0.0.1:8001"
).replace(/\/+$/, "");

/**
 * Shared secret, sent on every call.
 *
 * Not a user credential — it answers "did this come from our own backend",
 * nothing more. The bot service binds to loopback and is unreachable from
 * outside anyway; this is the second lock on that door.
 */
const INTERNAL_API_KEY = (process.env.INTERNAL_API_KEY ?? "").trim();

/** Ingest embeds a whole sheet, which on a large upload takes real time. */
const DEFAULT_TIMEOUT_MS = Number(process.env.BOT_SERVICE_TIMEOUT_MS ?? "60000");

function internalHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return INTERNAL_API_KEY
    ? { "X-Internal-Key": INTERNAL_API_KEY, ...extra }
    : { ...extra };
}

/** The API puts its message in `detail`, sometimes as a list. */
function readDetail(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
  }
  return fallback;
}

/**
 * Turn a non-OK response into the right exception.
 *
 * A 4xx is about the request and its message is meant to be read, so it keeps
 * its status and its wording. A 5xx is about the service, and the customer
 * gets a 502 that says so rather than an internal detail.
 */
async function raiseFor(response: Response, path: string): Promise<never> {
  const text = await response.text().catch(() => "");
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }

  if (response.status >= 400 && response.status < 500) {
    throw new HTTPException(
      response.status,
      readDetail(payload, `Request failed (${response.status})`),
    );
  }

  logger.error(
    `Bot service returned ${response.status} for ${path}: ${text.slice(0, 300)}`,
  );
  throw new HTTPException(502, "The bot service is not responding correctly.");
}

async function call<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BOT_SERVICE_URL}${path}`, {
      ...init,
      headers: internalHeaders(init.headers as Record<string, string> | undefined),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    logger.error(
      `Bot service unreachable at ${BOT_SERVICE_URL}${path}` +
        (timedOut ? ` (timed out after ${timeoutMs}ms)` : ""),
      error,
    );
    throw serviceUnavailable(
      "The bot service isn't reachable right now. " +
        "Retrieval, sheet uploads and templates are unavailable until it is back.",
    );
  }

  if (!response.ok) await raiseFor(response, path);
  if (response.status === 204) return undefined as T;

  return (await response.json()) as T;
}

async function getJson<T>(path: string, query: Record<string, string | number> = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) search.set(key, String(value));
  const qs = search.toString();
  return call<T>(`${path}${qs ? `?${qs}` : ""}`);
}

async function postJson<T>(path: string, body: unknown) {
  return call<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function putJson<T>(path: string, body: unknown) {
  return call<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Fetch a binary or text body and hand back the raw pieces.
 *
 * Used for the starter-sheet CSV, the widget script and the install zip —
 * three responses that are files, not JSON, and whose content type and
 * filename the browser needs to receive unchanged.
 */
async function fetchRaw(
  path: string,
  query: Record<string, string | number> = {},
): Promise<{ body: Buffer; contentType: string; disposition: string | null }> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) search.set(key, String(value));
  const qs = search.toString();

  let response: Response;
  try {
    response = await fetch(`${BOT_SERVICE_URL}${path}${qs ? `?${qs}` : ""}`, {
      headers: internalHeaders(),
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });
  } catch (error) {
    logger.error(`Bot service unreachable at ${BOT_SERVICE_URL}${path}`, error);
    throw serviceUnavailable("The bot service isn't reachable right now.");
  }

  if (!response.ok) await raiseFor(response, path);

  return {
    body: Buffer.from(await response.arrayBuffer()),
    contentType: response.headers.get("content-type") ?? "application/octet-stream",
    disposition: response.headers.get("content-disposition"),
  };
}

// ---------------------------------------------------------------------------
// The shapes the bot service returns
// ---------------------------------------------------------------------------

export interface BotTemplate {
  id: string;
  name: string;
  category: string;
  icon: string;
  tagline: string;
  description: string;
  scope_label: string;
  decline_message: string;
  sample_questions: string[];
  starter_categories: string[];
  strong_threshold: number;
  near_threshold: number;
  strictness: "strict" | "balanced" | "open";
  enabled?: boolean;
}

export interface Bot {
  id: string;
  name: string;
  template_id: string;
  template: BotTemplate | null;
  status: "draft" | "ready";
  doc_count: number;
  sheet_filename: string;
  sheet_uploaded_at: string | null;
  categories: string[];
  required_columns: string[];
  optional_columns: string[];
  accepted_formats: string[];
  llm_enabled: boolean;
  llm_available: boolean;
}

export interface Answer {
  response: string;
  mode: "strong" | "near" | "grounded" | "decline";
  matched_question: string | null;
  confidence: number;
}

export interface SheetUpload {
  documents_indexed: number;
  skipped_rows: number;
  warnings: string[];
  categories: string[];
  bot: Bot;
}

export interface Gaps {
  gaps: Array<{
    question: string;
    times_asked: number;
    last_asked: string;
    best_score: number;
    verdict: "nearly" | "missing";
  }>;
  days: number;
  flagged_inputs: number;
}

export interface WidgetTheme {
  id: string;
  name: string;
  tagline: string;
  description: string;
  best_for: string[];
  [key: string]: unknown;
}

export interface AccountBot {
  bot: Record<string, unknown> | null;
  top_unanswered: Array<{
    question: string;
    times_asked: number;
    best_score: number;
    verdict: string;
  }>;
}

export interface Snapshot {
  snapshot: Record<string, any>;
  rendered: string;
}

export interface AdminTemplates {
  templates: Array<Record<string, unknown>>;
  editable_fields: string[];
}

// ---------------------------------------------------------------------------
// The calls
// ---------------------------------------------------------------------------

export const botService = {
  // --- templates (public through Node) ---
  templates: () => getJson<BotTemplate[]>("/internal/templates"),

  starterSheet: (templateId: string) =>
    fetchRaw(`/internal/templates/${encodeURIComponent(templateId)}/starter-sheet`),

  // --- one account's bot ---
  bot: (userId: string) => getJson<Bot>("/internal/bot", { user_id: userId }),

  selectTemplate: (userId: string, templateId: string, name = "") =>
    putJson<Bot>("/internal/bot", {
      user_id: userId,
      template_id: templateId,
      name,
    }),

  setAnsweringMode: (userId: string, enabled: boolean) =>
    putJson<Bot>("/internal/bot/answering", { user_id: userId, enabled }),

  gaps: (userId: string, days = 30) =>
    getJson<Gaps>("/internal/bot/gaps", { user_id: userId, days }),

  /**
   * Forward an uploaded sheet.
   *
   * The file is re-wrapped as multipart rather than streamed through, because
   * multer has already buffered it to decide it is under the size limit. At
   * 5 MB that is a copy nobody will notice.
   */
  uploadSheet: async (userId: string, filename: string, data: Buffer) => {
    const form = new FormData();
    form.append("file", new Blob([data]), filename || "sheet");

    return call<SheetUpload>(
      `/internal/bot/sheet?user_id=${encodeURIComponent(userId)}`,
      { method: "POST", body: form },
      // Embedding a few thousand rows is the slowest thing either service
      // does, so this one call gets a longer leash than the rest.
      Number(process.env.BOT_SERVICE_INGEST_TIMEOUT_MS ?? "300000"),
    );
  },

  // --- answering ---
  ask: (userId: string, message: string, sessionId: string) =>
    postJson<Answer>("/internal/ask", {
      user_id: userId,
      message,
      session_id: sessionId,
    }),

  // --- widget ---
  widgetThemes: () => getJson<WidgetTheme[]>("/internal/widget/themes"),

  widgetPackage: (themeId: string, apiBase: string) =>
    fetchRaw(`/internal/widget/themes/${encodeURIComponent(themeId)}/package`, {
      api_base: apiBase,
    }),

  widgetScript: (themeId: string, apiBase: string) =>
    fetchRaw(`/internal/widget/script/${encodeURIComponent(themeId)}`, {
      api_base: apiBase,
    }),

  widgetActivation: (userId: string) =>
    getJson<{ bot_ready: boolean; bot_name: string | null; template_name: string | null }>(
      "/internal/widget/activation",
      { user_id: userId },
    ),

  // --- admin ---
  adminTemplates: () => getJson<AdminTemplates>("/internal/admin/templates"),

  updateTemplate: (templateId: string, changes: Record<string, unknown>) =>
    call<Record<string, unknown>>(
      `/internal/admin/templates/${encodeURIComponent(templateId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      },
    ),

  resetTemplate: (templateId: string) =>
    postJson<Record<string, unknown>>(
      `/internal/admin/templates/${encodeURIComponent(templateId)}/reset`,
      {},
    ),

  // --- owner ops / assistant context ---
  snapshot: (days = 7) => getJson<Snapshot>("/internal/snapshot", { days }),

  accountBot: (userId: string, days = 30, maxGaps = 8) =>
    getJson<AccountBot>("/internal/account-bot", {
      user_id: userId,
      days,
      max_gaps: maxGaps,
    }),

  /**
   * Check the service is up and serving the model this deployment expects.
   *
   * Called once at startup. A mismatch is a warning rather than a failure: the
   * server is still useful for billing, the dashboard and the admin panel with
   * retrieval degraded, and refusing to boot would take all of that down too.
   */
  health: async (): Promise<boolean> => {
    const expected = process.env.EMBEDDING_MODEL ?? "all-MiniLM-L6-v2";
    try {
      const response = await fetch(`${BOT_SERVICE_URL}/health`, {
        headers: internalHeaders(),
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) {
        logger.warn(`Bot service at ${BOT_SERVICE_URL} answered ${response.status}.`);
        return false;
      }

      const body = (await response.json()) as { model?: string };
      if (body.model && body.model !== expected) {
        logger.warn(
          `Bot service is serving "${body.model}" but this server expects ` +
            `"${expected}". Stored vectors were built with the latter — ` +
            `retrieval will be unreliable until they match.`,
        );
        return false;
      }

      logger.info(`Bot service ready at ${BOT_SERVICE_URL} (${body.model ?? expected}).`);
      return true;
    } catch {
      logger.warn(
        `Bot service is not reachable at ${BOT_SERVICE_URL}. Retrieval, sheet ` +
          `uploads and templates will fail until it is started ` +
          `(see bot-service/README.md).`,
      );
      return false;
    }
  },
};

export { Readable };
