/**
 * Configuration constants for Nexora AI.
 *
 * Port of `backend/shared/config.py`. Every environment variable keeps the
 * exact name it had in the Python build, so an existing `.env` file works
 * against this server with no edits.
 *
 * Per-bot settings — which collection to search, how sure it has to be, what
 * it says when it isn't — live on the template instead (`src/bot/`). The
 * thresholds here are only the defaults a template inherits when it doesn't
 * override them.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// src/config.ts -> src -> node-backend -> the project root, which is where the
// single shared .env lives (the Python build read the same file).
export const PROJECT_ROOT = path.resolve(__dirname, "..", "..");

// Real environment variables win over the file, so container/systemd config
// still overrides it — that is dotenv's default and is what we want.
dotenv.config({ path: path.join(PROJECT_ROOT, ".env") });
// A backend-local .env is also honoured, for people who prefer to keep the
// Node server's settings next to the Node server.
dotenv.config({ path: path.resolve(__dirname, "..", ".env") });

// ---------------------------------------------------------------------------
// Small parsing helpers
// ---------------------------------------------------------------------------

function str(name: string, fallback = ""): string {
  const value = process.env[name];
  return value === undefined ? fallback : value;
}

function num(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function int(name: string, fallback: number): number {
  return Math.trunc(num(name, fallback));
}

function bool(name: string, fallback: boolean): boolean {
  const raw = process.env[name];
  if (raw === undefined) return fallback;
  return raw.trim().toLowerCase() === "true";
}

/** Split a comma-separated environment value, dropping blanks. */
export function csv(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/** De-duplicate while preserving first-seen order (Python's dict.fromkeys). */
function unique(items: string[]): string[] {
  return [...new Set(items)];
}

// ---------------------------------------------------------------------------
// Timezone — IST (UTC+5:30)
// ---------------------------------------------------------------------------
// Python had `timezone(timedelta(hours=5, minutes=30))` and used it for every
// user-facing timestamp and for the credit-reset boundary. JavaScript has no
// fixed-offset timezone object, so the offset is carried as minutes and the
// helpers in `src/shared/time.ts` apply it.
export const IST_OFFSET_MINUTES = 5 * 60 + 30;

// ---------------------------------------------------------------------------
// Retrieval confidence thresholds (tune after seeing real query data)
// ---------------------------------------------------------------------------
export const STRONG_MATCH_THRESHOLD = num("STRONG_MATCH_THRESHOLD", 0.85);
export const NEAR_MATCH_THRESHOLD = num("NEAR_MATCH_THRESHOLD", 0.6);

// ---------------------------------------------------------------------------
// Embedding model
// ---------------------------------------------------------------------------
// Served by the Python sidecar (`embeddings-service/`), which loads
// sentence-transformers exactly as the old in-process code did. Keeping the
// model there rather than reimplementing it in Node is what guarantees the
// vectors already in MongoDB stay valid — a different model means a different
// vector space, and every stored embedding would have to be recomputed.
export const EMBEDDING_MODEL = str("EMBEDDING_MODEL", "all-MiniLM-L6-v2");
export const EMBEDDINGS_URL = str("EMBEDDINGS_URL", "http://127.0.0.1:8001");
export const EMBEDDINGS_TIMEOUT_SECONDS = num("EMBEDDINGS_TIMEOUT_SECONDS", 30);
// Ingest embeds a whole sheet at once. Batching keeps one upload from building
// a request body the sidecar has to hold entirely in memory.
export const EMBEDDINGS_BATCH_SIZE = int("EMBEDDINGS_BATCH_SIZE", 128);

// ---------------------------------------------------------------------------
// Retrieval settings
// ---------------------------------------------------------------------------
export const TOP_K = int("TOP_K", 3);

// ---------------------------------------------------------------------------
// MongoDB
// ---------------------------------------------------------------------------
export const MONGO_URI = str("MONGO_URI", "mongodb://localhost:27017");
export const MONGO_DB_NAME = str("MONGO_DB_NAME", "nexora");
export const MONGO_TIMEOUT_MS = int("MONGO_TIMEOUT_MS", 8000);

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------
export const PORT = int("PORT", 8000);
export const HOST = str("HOST", "0.0.0.0");

// The dashboard authenticates with a session cookie, and browsers refuse to
// send credentials to a wildcard origin — so the web origins are listed
// explicitly. Next.js dev runs on :3000; the Vite default is kept so a mixed
// setup during the migration still works.
export const WEB_ORIGINS = csv(
  str(
    "WEB_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
  ),
);

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
// Read through the exported getters below rather than captured at import, for
// the same reason the Python module read them off `config` per call: a
// snapshot taken at import silently outlives any change to the setting, which
// cost a confusing afternoon in the Python test suite.
export function ADMIN_API_KEY(): string {
  return str("ADMIN_API_KEY", "admin-change-me");
}
export function ADMIN_USERNAME(): string {
  return str("ADMIN_USERNAME", "admin").trim();
}
export function ADMIN_PASSWORD(): string {
  return str("ADMIN_PASSWORD", "");
}
export function ADMIN_SESSION_HOURS(): number {
  return num("ADMIN_SESSION_HOURS", 12);
}

// PBKDF2 work factor. OWASP's floor for PBKDF2-HMAC-SHA256 (2023 guidance).
// Override with PBKDF2_ITERATIONS in .env for faster dev signup (e.g. 10000).
export const PBKDF2_ITERATIONS = int("PBKDF2_ITERATIONS", 600_000);

// Session cookie. `secure` is off by default so plain-HTTP local development
// works; set COOKIE_SECURE=true behind TLS.
export const SESSION_COOKIE_NAME = str("SESSION_COOKIE_NAME", "nexora_session");
export const COOKIE_SECURE = bool("COOKIE_SECURE", false);
export const COOKIE_SAMESITE = str("COOKIE_SAMESITE", "lax") as "lax" | "strict" | "none";

// ---------------------------------------------------------------------------
// Credit system
// ---------------------------------------------------------------------------
export const DAILY_CREDIT_LIMIT = int("DAILY_CREDIT_LIMIT", 250);

// Variable credit cost based on query length (characters).
// Each tier: [maxChars, creditCost]
export const CREDIT_COST_TIERS: Array<[number, number]> = [
  [200, 1], // Short queries: 1 credit
  [500, 2], // Medium queries: 2 credits
  [1000, 3], // Long queries: 3 credits
  [2000, 5], // Very long queries: 5 credits
];

/** Return the credit cost for a message of the given character length. */
export function getCreditCost(messageLength: number): number {
  for (const [maxChars, cost] of CREDIT_COST_TIERS) {
    if (messageLength <= maxChars) return cost;
  }
  // Longer than all tiers — use the last tier's cost.
  return CREDIT_COST_TIERS[CREDIT_COST_TIERS.length - 1][1];
}

// ---------------------------------------------------------------------------
// Firebase Authentication (server side)
// ---------------------------------------------------------------------------
// Firebase is the *front door* only. The browser signs in with Firebase
// (Google popup or email+password), hands us the resulting ID token, and this
// server verifies it and issues the same httpOnly session cookie a password
// login produces. There is still exactly one thing that decides who someone is
// on this API: `nexora_session`. A Firebase ID token is never accepted as
// authentication for anything except the one exchange endpoint.
export const FIREBASE_SERVICE_ACCOUNT_JSON = str("FIREBASE_SERVICE_ACCOUNT_JSON").trim();
export const FIREBASE_PROJECT_ID = str("FIREBASE_PROJECT_ID").trim();
export const FIREBASE_CLIENT_EMAIL = str("FIREBASE_CLIENT_EMAIL").trim();

// A PEM private key cannot survive a .env file with its newlines intact, so it
// is stored with literal backslash-n and unescaped here. Without this the key
// parses as a single line and every token verification fails with an opaque
// error.
export const FIREBASE_PRIVATE_KEY = str("FIREBASE_PRIVATE_KEY").replace(/\\n/g, "\n").trim();

export const FIREBASE_CREDENTIALS_FILE = str("GOOGLE_APPLICATION_CREDENTIALS").trim();

export const FIREBASE_AUTH_ENABLED = Boolean(
  FIREBASE_SERVICE_ACCOUNT_JSON ||
    (FIREBASE_PROJECT_ID && FIREBASE_CLIENT_EMAIL && FIREBASE_PRIVATE_KEY) ||
    FIREBASE_CREDENTIALS_FILE,
);

// How far this server's clock may drift from Google's when checking an ID
// token's timestamps.
export const FIREBASE_CLOCK_SKEW_SECONDS = int("FIREBASE_CLOCK_SKEW_SECONDS", 30);

// Refuse an ID token whose email Firebase has not confirmed. Matching an
// incoming identity to an existing account by email address is only sound if
// somebody proved they control that mailbox. Turning this off lets anyone who
// can type an address into your sign-up form take over the account that
// already owns it. Leave it on.
export const FIREBASE_REQUIRE_VERIFIED_EMAIL = bool("FIREBASE_REQUIRE_VERIFIED_EMAIL", true);

// ---------------------------------------------------------------------------
// Client telemetry (Analytics exception events / error log)
// ---------------------------------------------------------------------------
export const TELEMETRY_ENABLED = bool("TELEMETRY_ENABLED", true);
export const TELEMETRY_MAX_ERRORS_PER_IP_PER_HOUR = int(
  "TELEMETRY_MAX_ERRORS_PER_IP_PER_HOUR",
  60,
);
export const TELEMETRY_RETENTION_DAYS = int("TELEMETRY_RETENTION_DAYS", 30);

// ---------------------------------------------------------------------------
// Local LLM (Ollama) — optional, and off unless explicitly enabled
// ---------------------------------------------------------------------------
// The retrieval path never needs this. It is used for two things only:
//   1. Grounded summarisation on the *near* band of a bot that has opted in.
//   2. The owner ops assistant, which is internal and nxo_-key only.
export const LLM_ENABLED = bool("LLM_ENABLED", false);
export const OLLAMA_BASE_URL = str("OLLAMA_BASE_URL", "http://127.0.0.1:11434");
export const OLLAMA_MODEL = str("OLLAMA_MODEL", "qwen2.5:7b");
export const OLLAMA_TIMEOUT_SECONDS = num("OLLAMA_TIMEOUT_SECONDS", 20);

// The fallback chain, tried in order. OLLAMA_MODEL stays first so an existing
// .env keeps its primary model.
export const OLLAMA_MODELS = unique([
  OLLAMA_MODEL,
  ...csv(str("OLLAMA_MODELS", "qwen2.5:7b,llama3.1:8b,gemma2:9b")),
]);

// Groq is the last resort, and it is a hosted API: the prompt — including
// retrieved sheet rows — leaves this machine when it is used. Unset
// GROQ_API_KEY to keep every generation local.
export const GROQ_API_KEY = str("GROQ_API_KEY").trim();
export const GROQ_BASE_URL = str("GROQ_BASE_URL", "https://api.groq.com/openai/v1");
export const GROQ_MODELS = csv(
  str("GROQ_MODELS", "qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b"),
);
export const GROQ_TIMEOUT_SECONDS = num("GROQ_TIMEOUT_SECONDS", 30);

// Gemini, reached through Google's OpenAI-compatible endpoint. Several keys
// may be given (comma-separated): each model is tried on every key before the
// next model, because free-tier quotas are per project per model.
export const GEMINI_API_KEYS = unique([
  ...csv(str("GEMINI_API_KEYS")),
  ...csv(str("GEMINI_API_KEY")),
]);
export const GEMINI_BASE_URL = str(
  "GEMINI_BASE_URL",
  "https://generativelanguage.googleapis.com/v1beta/openai",
);
export const GEMINI_MODELS = csv(
  str("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.8-flash"),
);
export const GEMINI_TIMEOUT_SECONDS = num("GEMINI_TIMEOUT_SECONDS", 30);

/** Which providers are tried, in order. Drop one to disable it entirely. */
export const LLM_PROVIDER_ORDER = csv(str("LLM_PROVIDER_ORDER", "ollama,groq,gemini"))
  .map((p) => p.toLowerCase())
  .filter((p) => p === "ollama" || p === "groq" || p === "gemini");

// How long a rate-limited or failing model is skipped before being tried
// again. Without it every request would re-hit a model we know is saturated.
export const LLM_COOLDOWN_SECONDS = num("LLM_COOLDOWN_SECONDS", 60);

// A key the provider rejects outright (invalid, revoked, disabled) is skipped
// much longer: it won't start working on its own.
export const LLM_BAD_KEY_COOLDOWN_SECONDS = num("LLM_BAD_KEY_COOLDOWN_SECONDS", 3600);

// Sampling. Low temperature because the job is faithful rephrasing, not
// invention — creativity here is the failure mode, not the feature.
export const LLM_TEMPERATURE = num("LLM_TEMPERATURE", 0.1);
export const LLM_MAX_TOKENS = int("LLM_MAX_TOKENS", 400);

// Hard ceiling on what may be handed to the model. Not a policy filter — the
// policy filter is inputPolicy.ts — just the engineering limit that stops one
// request from building an unbounded prompt.
export const LLM_MAX_INPUT_CHARS = int("LLM_MAX_INPUT_CHARS", 2000);

// A grounded answer is rejected if too little of it traces back to the source
// passages. See `bot/grounding.ts` — 0.0 accepts anything, 1.0 accepts only
// near-verbatim copies.
export const LLM_MIN_GROUNDEDNESS = num("LLM_MIN_GROUNDEDNESS", 0.72);

// ---------------------------------------------------------------------------
// The dashboard assistant
// ---------------------------------------------------------------------------
// A general-purpose chat assistant, reachable **only** from the signed-in web
// app. It is deliberately not on any API-key route: customers integrate a bot
// that answers from their sheet, and that contract does not change.
export const ASSISTANT_ENABLED = bool("ASSISTANT_ENABLED", false);
export const ASSISTANT_MODEL = str("ASSISTANT_MODEL", OLLAMA_MODEL);
export const ASSISTANT_TIMEOUT_SECONDS = num("ASSISTANT_TIMEOUT_SECONDS", 180);
export const ASSISTANT_TEMPERATURE = num("ASSISTANT_TEMPERATURE", 0.7);
export const ASSISTANT_MAX_TOKENS = int("ASSISTANT_MAX_TOKENS", 1024);
export const ASSISTANT_HISTORY_TURNS = int("ASSISTANT_HISTORY_TURNS", 12);
export const ASSISTANT_MAX_MESSAGE_CHARS = int("ASSISTANT_MAX_MESSAGE_CHARS", 4000);

// Two ARM cores generate for one person at a time. Letting a third and fourth
// request in doesn't make them faster, it makes everyone slower — so they
// queue, and past the queue they are turned away with a reason.
export const ASSISTANT_CONCURRENCY = int("ASSISTANT_CONCURRENCY", 1);
export const ASSISTANT_QUEUE_WAIT_SECONDS = num("ASSISTANT_QUEUE_WAIT_SECONDS", 45);
export const ASSISTANT_DAILY_MESSAGES = int("ASSISTANT_DAILY_MESSAGES", 100);

// ---------------------------------------------------------------------------
// Identity / signup policy (backend/billing/identity.py)
// ---------------------------------------------------------------------------
export const EXTRA_DISPOSABLE_DOMAINS = csv(str("DISPOSABLE_EMAIL_DOMAINS")).map((d) =>
  d.toLowerCase(),
);
export const BLOCK_DISPOSABLE = bool("BLOCK_DISPOSABLE_EMAILS", true);
export const MAX_SIGNUPS_PER_IP_PER_DAY = int("MAX_SIGNUPS_PER_IP_PER_DAY", 3);

// ---------------------------------------------------------------------------
// Billing (backend/billing/plans.py)
// ---------------------------------------------------------------------------
export const CURRENCY = str("BILLING_CURRENCY", "USD");
export const PAID_DAILY_CREDITS = int("PAID_DAILY_CREDITS", 5000);
export const TRIAL_DAILY_CREDITS = int("TRIAL_DAILY_CREDITS", 250);
export const TRIAL_DAYS = int("TRIAL_DAYS", 14);

// How often to retire lapsed subscriptions. The dashboard also syncs on read,
// but nobody should keep a paid allowance just because they stopped logging in.
export const ENTITLEMENT_SWEEP_SECONDS = int("ENTITLEMENT_SWEEP_SECONDS", 900);

// ---------------------------------------------------------------------------
// Fallback response wording
// ---------------------------------------------------------------------------
// Every template carries its own decline message and handoff nudge, written in
// that industry's voice. These are only used by a bot whose template somehow
// doesn't supply them.
export const DEFAULT_DECLINE_MESSAGE =
  "I can only answer questions covered by this business's FAQ. Try " +
  "rephrasing, or contact the team and a person will help you directly.";

export const NEAR_MATCH_SUFFIX =
  "\n\nIf this doesn't fully answer your question, please contact support " +
  "and we'll help directly.";
