/**
 * The pieces of FastAPI this conversion had to rebuild by hand.
 *
 * FastAPI gave three things for free that Express does not:
 *
 *   1. `HTTPException` — throw from anywhere, get a JSON error response.
 *   2. `async def` route handlers whose rejections become 500s rather than
 *      unhandled promise rejections that take the process down.
 *   3. Pydantic request-body validation with a 422 and a field-level report.
 *
 * All three are here, shaped so a converted route reads as closely as possible
 * to the Python one it replaces.
 */

import type { NextFunction, Request, RequestHandler, Response } from "express";
import { ZodError, type ZodTypeAny, type infer as ZodInfer } from "zod";

import { logger } from "./logger.js";

/**
 * FastAPI's `HTTPException`.
 *
 * `detail` is deliberately `unknown`: several routes (the credit check most
 * importantly) answer with a structured object rather than a string, and the
 * frontend reads fields out of it.
 */
export class HTTPException extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly headers: Record<string, string>;

  constructor(status: number, detail: unknown, headers: Record<string, string> = {}) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.name = "HTTPException";
    this.status = status;
    this.detail = detail;
    this.headers = headers;
  }
}

/** Shorthands for the statuses this codebase actually raises. */
export const badRequest = (detail: unknown) => new HTTPException(400, detail);
export const unauthorized = (detail: unknown) => new HTTPException(401, detail);
export const paymentRequired = (detail: unknown) => new HTTPException(402, detail);
export const forbidden = (detail: unknown) => new HTTPException(403, detail);
export const notFound = (detail: unknown) => new HTTPException(404, detail);
export const conflict = (detail: unknown) => new HTTPException(409, detail);
export const unprocessable = (detail: unknown) => new HTTPException(422, detail);
export const tooManyRequests = (detail: unknown, headers?: Record<string, string>) =>
  new HTTPException(429, detail, headers);
export const serverError = (detail: unknown) => new HTTPException(500, detail);
export const serviceUnavailable = (detail: unknown) => new HTTPException(503, detail);

/**
 * Wrap an async handler so a rejected promise reaches the error middleware.
 *
 * Express 4 does not await handlers, so without this an `await` that throws
 * becomes an unhandled rejection and the client hangs until it times out. Every
 * route in this codebase is wrapped.
 */
export function asyncHandler(
  handler: (req: Request, res: Response, next: NextFunction) => Promise<unknown>,
): RequestHandler {
  return (req, res, next) => {
    handler(req, res, next).catch(next);
  };
}

/**
 * Validate and coerce a request body, the way a Pydantic model parameter did.
 *
 * Throws a 422 whose `detail` mirrors FastAPI's validation-error shape — a list
 * of `{loc, msg, type}` — so an existing frontend that reads `detail[0].msg`
 * keeps working.
 */
export function parseBody<S extends ZodTypeAny>(schema: S, body: unknown): ZodInfer<S> {
  const result = schema.safeParse(body ?? {});
  if (result.success) return result.data;
  throw new HTTPException(422, zodToDetail(result.error));
}

/** Same as {@link parseBody}, for query strings. */
export function parseQuery<S extends ZodTypeAny>(schema: S, query: unknown): ZodInfer<S> {
  const result = schema.safeParse(query ?? {});
  if (result.success) return result.data;
  throw new HTTPException(422, zodToDetail(result.error));
}

function zodToDetail(error: ZodError): Array<Record<string, unknown>> {
  return error.issues.map((issue) => ({
    loc: ["body", ...issue.path],
    msg: issue.message,
    type: issue.code,
  }));
}

/**
 * The client's address, as the rate limiters key on it.
 *
 * `X-Forwarded-For` is only consulted because this server is expected to run
 * behind Caddy/nginx in production; `app.set("trust proxy", ...)` in server.ts
 * is what makes `req.ip` respect it. The first entry is the original client,
 * the rest are proxies.
 */
export function clientAddress(req: Request): string {
  const forwarded = req.headers["x-forwarded-for"];
  if (typeof forwarded === "string" && forwarded.length > 0) {
    const first = forwarded.split(",")[0]?.trim();
    if (first) return first;
  }
  return req.ip ?? req.socket.remoteAddress ?? "unknown";
}

/**
 * The error middleware. Registered last, after every router.
 *
 * An `HTTPException` becomes its own status and detail. Anything else is a bug:
 * it is logged with its stack and answered with a flat 500, because an
 * exception message can carry internals that should not cross the wire.
 */
export function errorMiddleware(
  error: unknown,
  req: Request,
  res: Response,
  next: NextFunction,
): void {
  if (res.headersSent) {
    next(error);
    return;
  }

  if (error instanceof HTTPException) {
    for (const [name, value] of Object.entries(error.headers)) {
      res.setHeader(name, value);
    }
    res.status(error.status).json({ detail: error.detail });
    return;
  }

  // Multer's own errors are the only third-party ones worth translating: they
  // are a client mistake (file too large, wrong field) rather than a bug here.
  if (error instanceof Error && error.name === "MulterError") {
    res.status(400).json({ detail: error.message });
    return;
  }

  logger.exception(`Unhandled error on ${req.method} ${req.originalUrl}`, error);
  res.status(500).json({ detail: "Internal server error." });
}

/** 404 for anything no router claimed. Registered just before the error handler. */
export function notFoundMiddleware(req: Request, res: Response): void {
  res.status(404).json({ detail: `No route for ${req.method} ${req.originalUrl}` });
}
