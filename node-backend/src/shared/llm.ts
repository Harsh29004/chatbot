/**
 * LLM access: local Ollama models, then Groq, then Gemini (with key rotation).
 *
 * Port of `backend/shared/llm.py`. Three rules shape this module.
 *
 * **It is never in the critical path.** Every entry point returns `null` (or an
 * empty stream) rather than throwing. If every model is down, slow, or rate
 * limited, the caller falls back to the behaviour it had before there was a
 * model — a verbatim answer or a decline. A bot must not stop answering because
 * an optional component is unavailable.
 *
 * **One saturated model is not an outage.** Generation walks a chain, in the
 * provider order `LLM_PROVIDER_ORDER` (default `ollama,groq,gemini`):
 *
 * - `OLLAMA_MODELS` in order;
 * - `GROQ_MODELS` in order;
 * - `GEMINI_MODELS` in order, each tried with every key in `GEMINI_API_KEYS`
 *   before moving to the next model. Free-tier quotas are per project *per
 *   model*, so a model exhausted on one key usually still answers on the next.
 *
 * A target that answers 429 (rate limited), 503 (busy), 5xx, 404 (missing), or
 * times out is put on a cooldown and the next one is tried. A key that is
 * rejected outright (bad, revoked, or disabled) benches every target using it.
 *
 * **It never sees more than it needs.** The grounded path passes retrieved
 * passages and nothing else. Note the data-residency trade: the Ollama models
 * run on this machine, but Groq and Gemini are hosted APIs — when either one
 * answers, the prompt has left the server. Leave their keys unset to stay local.
 *
 * Conversion note: httpx becomes `fetch`. Python's thread-local clients have no
 * equivalent and need none — Node reuses sockets through its own agent pool.
 * The `threading.Lock`s around the caches are likewise gone: JavaScript cannot
 * interleave between a read and a write of a plain variable.
 */

import * as config from "../config.js";
import { coll, registerIndexes } from "./mongo.js";
import { logger } from "./logger.js";

export type Provider = "ollama" | "groq" | "gemini";
const HOSTED: readonly string[] = ["groq", "gemini"];

export interface Target {
  provider: Provider;
  model: string;
  /**
   * Which of the provider's keys this target uses. Only Gemini rotates keys;
   * the index (never the key itself) is what appears in logs.
   */
  keyIndex: number;
}

function label(target: Target): string {
  if (target.provider === "gemini" && config.GEMINI_API_KEYS.length > 1) {
    return `gemini:${target.model}#key${target.keyIndex + 1}`;
  }
  return `${target.provider}:${target.model}`;
}

function apiKeyOf(target: Target): string {
  if (target.provider === "groq") return config.GROQ_API_KEY;
  if (target.provider === "gemini") return config.GEMINI_API_KEYS[target.keyIndex] ?? "";
  return "";
}

/** This target can't answer right now; move down the chain. */
class TryNext extends Error {
  readonly cooldown: boolean;
  constructor(reason: string, cooldown = true) {
    super(reason);
    this.name = "TryNext";
    this.cooldown = cooldown;
  }
}

/** The provider rejected the key itself; every target using it is benched. */
class BadKey extends TryNext {
  constructor(reason: string) {
    super(reason);
    this.name = "BadKey";
  }
}

// Status codes that mean "this model, not this request" — worth trying the
// next model.
const RETRYABLE_STATUS = new Set([404, 408, 409, 413, 429, 500, 502, 503, 504]);

// Google answers an invalid or revoked key with 400 INVALID_ARGUMENT, not 401,
// so a 400 has to be read before it can be told apart from a bad request.
const BAD_KEY_TEXT = /api[ _]?key|auth(entication)? key|unauthenticated|permission/i;

// Availability is cached: a bot with the feature on would otherwise pay a
// failed connection attempt on every single request while Ollama is down.
const AVAILABILITY_TTL_SECONDS = 30;
let availabilityCache: { ok: boolean; at: number } | null = null;

/**
 * Cooldowns live in MongoDB so every worker skips the same saturated model and
 * a restart doesn't forget it. A TTL index deletes them once they end. If
 * MongoDB itself is unreachable, this module must still work: it falls back to
 * a per-process map rather than letting a database error stop generation.
 */
export const LLM_COOLDOWNS = "llm_cooldowns";

registerIndexes(LLM_COOLDOWNS, [
  [{ until: 1 }, { name: "until_ttl", expireAfterSeconds: 0 }],
]);

const fallbackCooldowns = new Map<string, number>(); // label -> ms epoch it ends

/** The model that produced the most recent successful answer, for display. */
let lastUsed: string | null = null;

// ---------------------------------------------------------------------------
// The chain
// ---------------------------------------------------------------------------

function providerTargets(provider: string, firstOllamaModel?: string | null): Target[] {
  if (provider === "ollama") {
    let models = [...config.OLLAMA_MODELS];
    if (firstOllamaModel) {
      models = [firstOllamaModel, ...models.filter((m) => m !== firstOllamaModel)];
    }
    return models.map((model) => ({ provider: "ollama" as const, model, keyIndex: 0 }));
  }
  if (provider === "groq" && config.GROQ_API_KEY) {
    return config.GROQ_MODELS.map((model) => ({
      provider: "groq" as const,
      model,
      keyIndex: 0,
    }));
  }
  if (provider === "gemini" && config.GEMINI_API_KEYS.length > 0) {
    const targets: Target[] = [];
    for (const model of config.GEMINI_MODELS) {
      for (let i = 0; i < config.GEMINI_API_KEYS.length; i += 1) {
        targets.push({ provider: "gemini", model, keyIndex: i });
      }
    }
    return targets;
  }
  return [];
}

function chain(firstOllamaModel?: string | null): Target[] {
  const targets: Target[] = [];
  for (const provider of config.LLM_PROVIDER_ORDER) {
    targets.push(...providerTargets(provider, firstOllamaModel));
  }
  return targets;
}

async function cooling(target: Target): Promise<boolean> {
  const name = label(target);
  try {
    const cooldowns = await coll(LLM_COOLDOWNS);
    const doc = await cooldowns.findOne({ _id: name as any }, { projection: { until: 1 } });
    return Boolean(doc && (doc.until as Date).getTime() > Date.now());
  } catch {
    // The database must never stop generation.
    return (fallbackCooldowns.get(name) ?? 0) > Date.now();
  }
}

async function cool(target: Target, reason: string, seconds?: number | null): Promise<void> {
  const duration = seconds && seconds > 0 ? seconds : config.LLM_COOLDOWN_SECONDS;
  const name = label(target);
  const until = new Date(Date.now() + duration * 1000);

  try {
    const cooldowns = await coll(LLM_COOLDOWNS);
    await cooldowns.updateOne(
      { _id: name as any },
      { $set: { until, reason: reason.slice(0, 200) } },
      { upsert: true },
    );
  } catch {
    fallbackCooldowns.set(name, Date.now() + duration * 1000);
  }

  logger.warn(
    `LLM ${name} unavailable (${reason}); skipping it for ${duration.toFixed(0)}s.`,
  );
}

/** A rejected key won't start working in a minute: bench it for every model. */
async function benchKey(target: Target, reason: string): Promise<void> {
  for (const other of providerTargets(target.provider)) {
    if (other.keyIndex === target.keyIndex) {
      await cool(other, reason, config.LLM_BAD_KEY_COOLDOWN_SECONDS);
    }
  }
}

/**
 * Cool a model after a transport error.
 *
 * If the Ollama *server* can't be reached, every local model is down, not just
 * this one — so they are all benched together rather than each one paying its
 * own connect timeout on the same request.
 */
async function coolAfterError(target: Target, error: unknown, detail: string): Promise<void> {
  const isConnectFailure =
    error instanceof Error &&
    (error.name === "TypeError" || // fetch's "failed to fetch" shape
      /ECONNREFUSED|ENOTFOUND|EHOSTUNREACH|ECONNRESET/.test(String((error as any).cause ?? "")));

  if (target.provider === "ollama" && isConnectFailure) {
    for (const model of config.OLLAMA_MODELS) {
      await cool(
        { provider: "ollama", model, keyIndex: 0 },
        `Ollama server unreachable: ${detail}`,
      );
    }
    return;
  }
  await cool(target, detail);
}

/** Seconds the provider asked us to wait: Retry-After, or Google's retryDelay. */
function retryAfter(response: Response, bodyText: string): number | null {
  const header = response.headers.get("retry-after");
  if (header) {
    const parsed = Number.parseFloat(header);
    if (Number.isFinite(parsed)) return parsed;
  }
  const match = /"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"/.exec(bodyText || "");
  return match ? Number.parseFloat(match[1]) : null;
}

/**
 * Turn a non-200 into the right control-flow exception.
 *
 * Takes the body text as an argument rather than reading it: a `Response` body
 * can only be consumed once, and the streaming paths have already read it by
 * the time they call this.
 */
async function check(response: Response, target: Target, bodyText: string): Promise<void> {
  const status = response.status;
  if (status === 200) return;

  if (
    status === 401 ||
    status === 403 ||
    (status === 400 && HOSTED.includes(target.provider) && BAD_KEY_TEXT.test(bodyText || ""))
  ) {
    throw new BadKey(`key rejected (${status})`);
  }
  if (status === 429) {
    await cool(target, "rate limited", retryAfter(response, bodyText));
    throw new TryNext("rate limited", false);
  }
  if (RETRYABLE_STATUS.has(status) || status >= 500) {
    throw new TryNext(`HTTP ${status}`);
  }
  throw new TryNext(`HTTP ${status}`, false);
}

async function handleFailure(target: Target, error: TryNext): Promise<void> {
  if (error instanceof BadKey) {
    await benchKey(target, error.message);
  } else if (error.cooldown) {
    await cool(target, error.message);
  }
}

function hostedBaseUrl(provider: string): string {
  return provider === "gemini" ? config.GEMINI_BASE_URL : config.GROQ_BASE_URL;
}

function hostedTimeout(provider: string): number {
  return provider === "gemini"
    ? config.GEMINI_TIMEOUT_SECONDS
    : config.GROQ_TIMEOUT_SECONDS;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/**
 * Whether any model in the chain is reachable right now.
 *
 * Cached for a few seconds, and must never throw. True when Ollama answers its
 * health check, or when a Groq or Gemini key is configured.
 *
 * Gated on *either* feature flag, not just `LLM_ENABLED`: bot rewording and the
 * dashboard assistant are independent, and each caller still enforces its own
 * flag.
 */
export async function available(): Promise<boolean> {
  if (!(config.LLM_ENABLED || config.ASSISTANT_ENABLED)) return false;

  const now = Date.now();
  if (availabilityCache !== null && now - availabilityCache.at < AVAILABILITY_TTL_SECONDS * 1000) {
    return availabilityCache.ok;
  }

  let ok = false;
  try {
    const response = await fetch(`${config.OLLAMA_BASE_URL}/api/tags`, {
      signal: AbortSignal.timeout(2000),
    });
    ok = response.status === 200;
  } catch (error) {
    logger.debug(`Ollama unavailable at ${config.OLLAMA_BASE_URL}: ${String(error)}`);
  }

  // Hosted providers are checked by configuration, not by a network call:
  // pinging them every 30s would spend their rate limits on health checks.
  ok = ok || chain().some((t) => HOSTED.includes(t.provider));

  availabilityCache = { ok, at: Date.now() };
  return ok;
}

// ---------------------------------------------------------------------------
// One-shot generation
// ---------------------------------------------------------------------------

async function generateOllama(
  target: Target,
  system: string,
  prompt: string,
  temperature: number,
  maxTokens: number,
): Promise<string> {
  const response = await fetch(`${config.OLLAMA_BASE_URL}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: target.model,
      system,
      prompt,
      stream: false,
      options: { temperature, num_predict: maxTokens },
    }),
    signal: AbortSignal.timeout(config.OLLAMA_TIMEOUT_SECONDS * 1000),
  });

  const text = await response.text();
  await check(response, target, text);

  const body = JSON.parse(text) as { response?: string };
  return (body.response ?? "").trim();
}

async function generateHosted(
  target: Target,
  system: string,
  prompt: string,
  temperature: number,
  maxTokens: number,
): Promise<string> {
  const response = await fetch(`${hostedBaseUrl(target.provider)}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKeyOf(target)}`,
    },
    body: JSON.stringify({
      model: target.model,
      messages: [
        { role: "system", content: system },
        { role: "user", content: prompt },
      ],
      temperature,
      max_tokens: maxTokens,
      stream: false,
    }),
    signal: AbortSignal.timeout(hostedTimeout(target.provider) * 1000),
  });

  const text = await response.text();
  await check(response, target, text);

  const body = JSON.parse(text) as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  const choices = body.choices ?? [];
  return choices.length > 0 ? (choices[0].message?.content ?? "").trim() : "";
}

/**
 * Ask the chain for a completion.
 *
 * Returns the trimmed text from the first model that answers, or `null` if
 * every model failed or the feature is off. The caller is expected to have a
 * working answer without us.
 */
export async function generate(options: {
  system: string;
  prompt: string;
  temperature?: number | null;
  maxTokens?: number | null;
}): Promise<string | null> {
  if (!config.LLM_ENABLED) return null;

  const temperature = options.temperature ?? config.LLM_TEMPERATURE;
  const maxTokens = options.maxTokens ?? config.LLM_MAX_TOKENS;

  for (const target of chain()) {
    if (await cooling(target)) continue;

    const started = Date.now();
    let text: string;
    try {
      text = HOSTED.includes(target.provider)
        ? await generateHosted(target, options.system, options.prompt, temperature, maxTokens)
        : await generateOllama(target, options.system, options.prompt, temperature, maxTokens);
    } catch (error) {
      if (error instanceof TryNext) {
        await handleFailure(target, error);
      } else {
        const elapsed = ((Date.now() - started) / 1000).toFixed(1);
        const name = error instanceof Error ? error.name : "Error";
        await coolAfterError(target, error, `${name} after ${elapsed}s`);
      }
      continue;
    }

    if (!text) {
      // An empty completion is this model's problem, not a reason to bench it —
      // but the next model may still do better.
      continue;
    }

    lastUsed = label(target);
    logger.debug(
      `${lastUsed} generated ${text.length} chars in ` +
        `${((Date.now() - started) / 1000).toFixed(2)}s.`,
    );
    return text;
  }

  logger.warn("Every model in the LLM chain failed or is cooling down.");
  return null;
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: string;
  content: string;
}

interface StreamPayload {
  messages: ChatMessage[];
  temperature: number;
  maxTokens: number;
}

/**
 * Read a response body as lines.
 *
 * httpx had `aiter_lines()`; `fetch` gives a byte stream, so the split has to
 * be done here. A chunk boundary can fall mid-line, which is why the remainder
 * is carried over rather than yielded.
 */
async function* iterLines(response: Response): AsyncGenerator<string> {
  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        yield buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }
    if (buffer.length > 0) yield buffer;
  } finally {
    reader.releaseLock();
  }
}

async function* streamOllama(
  target: Target,
  payload: StreamPayload,
  signal: AbortSignal,
): AsyncGenerator<string> {
  const response = await fetch(`${config.OLLAMA_BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: target.model,
      messages: payload.messages,
      stream: true,
      options: { temperature: payload.temperature, num_predict: payload.maxTokens },
    }),
    signal,
  });

  if (response.status !== 200) {
    await check(response, target, await response.text());
  }

  for await (const line of iterLines(response)) {
    if (!line.trim()) continue;

    let chunk: any;
    try {
      chunk = JSON.parse(line);
    } catch {
      continue;
    }

    if (chunk.error) throw new TryNext(String(chunk.error).slice(0, 120));

    const delta: string = chunk.message?.content ?? "";
    if (delta) yield delta;
    if (chunk.done) return;
  }
}

async function* streamHosted(
  target: Target,
  payload: StreamPayload,
  signal: AbortSignal,
): AsyncGenerator<string> {
  const response = await fetch(`${hostedBaseUrl(target.provider)}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKeyOf(target)}`,
    },
    body: JSON.stringify({
      model: target.model,
      messages: payload.messages,
      temperature: payload.temperature,
      max_tokens: payload.maxTokens,
      stream: true,
    }),
    signal,
  });

  if (response.status !== 200) {
    await check(response, target, await response.text());
  }

  for await (const line of iterLines(response)) {
    if (!line.startsWith("data:")) continue;

    const data = line.slice(5).trim();
    if (data === "[DONE]") return;

    let chunk: any;
    try {
      chunk = JSON.parse(data);
    } catch {
      continue;
    }

    const choices = chunk.choices ?? [];
    const delta: string = choices.length > 0 ? (choices[0].delta?.content ?? "") : "";
    if (delta) yield delta;
  }
}

/**
 * Stream a multi-turn reply, yielding text deltas as they arrive.
 *
 * Walks the same chain as {@link generate}, with `model` (or `ASSISTANT_MODEL`)
 * tried first. A model can be swapped out only **before its first token**: once
 * text has reached the reader, a failure ends the stream rather than splicing a
 * second model's answer onto the first one's half-sentence.
 *
 * Yields nothing at all if every model fails. Never throws.
 */
export async function* chatStream(options: {
  system: string;
  messages: ChatMessage[];
  model?: string | null;
  temperature?: number | null;
  maxTokens?: number | null;
  timeout?: number | null;
}): AsyncGenerator<string> {
  const payload: StreamPayload = {
    messages: [{ role: "system", content: options.system }, ...options.messages],
    temperature: options.temperature ?? config.ASSISTANT_TEMPERATURE,
    maxTokens: options.maxTokens ?? config.ASSISTANT_MAX_TOKENS,
  };

  const budget = options.timeout ?? config.ASSISTANT_TIMEOUT_SECONDS;

  for (const target of chain(options.model ?? config.ASSISTANT_MODEL)) {
    if (await cooling(target)) continue;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), budget * 1000);
    let startedOutput = false;

    try {
      const streamer = HOSTED.includes(target.provider) ? streamHosted : streamOllama;
      for await (const delta of streamer(target, payload, controller.signal)) {
        startedOutput = true;
        yield delta;
      }
    } catch (error) {
      if (error instanceof TryNext) {
        await handleFailure(target, error);
      } else {
        const name = error instanceof Error ? error.name : "Error";
        await coolAfterError(target, error, name);
      }
      if (startedOutput) return;
      continue;
    } finally {
      clearTimeout(timer);
    }

    if (startedOutput) {
      lastUsed = label(target);
      return;
    }
  }

  logger.warn("Every model in the LLM chain failed or is cooling down (stream).");
}

// ---------------------------------------------------------------------------
// Introspection
// ---------------------------------------------------------------------------

/** The model that answered most recently, or the first in the chain. */
export function modelName(): string {
  if (lastUsed) return lastUsed;
  const targets = chain();
  return targets.length > 0 ? label(targets[0]) : config.OLLAMA_MODEL;
}

/** Each model in order, and whether it is currently being skipped. */
export async function chainStatus(): Promise<
  Array<{ target: string; provider: string; model: string; cooling_down: boolean }>
> {
  const out = [];
  for (const target of chain()) {
    out.push({
      target: label(target),
      provider: target.provider,
      model: target.model,
      cooling_down: await cooling(target),
    });
  }
  return out;
}

/** Forget cached health and cooldowns. Used by tests and after config changes. */
export async function resetAvailabilityCache(): Promise<void> {
  availabilityCache = null;
  lastUsed = null;
  fallbackCooldowns.clear();
  try {
    const cooldowns = await coll(LLM_COOLDOWNS);
    await cooldowns.deleteMany({});
  } catch {
    // Nothing to clear if the database is down.
  }
}
