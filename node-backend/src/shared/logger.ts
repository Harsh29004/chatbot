/**
 * A very small logger.
 *
 * Python had the standard `logging` module configured by uvicorn. Rather than
 * pull a logging framework into the conversion, this writes the same four
 * levels to the console in a stable, greppable shape. Swap the body for pino or
 * winston later without touching a call site.
 *
 * `LOG_LEVEL` accepts debug | info | warn | error (default info).
 */

type Level = "debug" | "info" | "warn" | "error";

const ORDER: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };

const configured = (process.env.LOG_LEVEL ?? "info").trim().toLowerCase();
const threshold = ORDER[(configured as Level) in ORDER ? (configured as Level) : "info"];

function emit(level: Level, message: string, error?: unknown): void {
  if (ORDER[level] < threshold) return;

  const line = `${new Date().toISOString()} ${level.toUpperCase().padEnd(5)} ${message}`;
  const sink = level === "error" || level === "warn" ? console.error : console.log;
  sink(line);

  // Stack traces go to stderr alongside the message they belong to. Python's
  // `logger.exception` did this by default and several call sites relied on it.
  if (error !== undefined) {
    console.error(error instanceof Error ? (error.stack ?? error.message) : error);
  }
}

export const logger = {
  debug: (message: string) => emit("debug", message),
  info: (message: string) => emit("info", message),
  warn: (message: string, error?: unknown) => emit("warn", message, error),
  error: (message: string, error?: unknown) => emit("error", message, error),
  /** `logger.exception` in Python — an error plus the stack that caused it. */
  exception: (message: string, error: unknown) => emit("error", message, error),
};
