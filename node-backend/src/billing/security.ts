/**
 * Password hashing and session tokens.
 *
 * Port of `backend/billing/security.py`, and deliberately byte-for-byte
 * compatible with it. PBKDF2-HMAC-SHA256 is a standard construction, so a hash
 * produced by Python's `hashlib.pbkdf2_hmac` and one produced by Node's
 * `crypto.pbkdf2` are the same bytes for the same inputs. That is what lets
 * this server run against the existing `customers` collection: **every password
 * already stored keeps working, with no reset and no migration.**
 *
 * The stored format is unchanged too — `pbkdf2_sha256$iterations$salt$hash`,
 * salt and hash base64 — so a row written by the Python build verifies here and
 * a row written here verifies there. That matters during a staged cutover when
 * both are briefly live.
 *
 * Session tokens are opaque random strings. Only their SHA-256 hash is stored,
 * so a leaked database cannot be replayed as live sessions. They are revocable
 * (a JWT is not, without extra machinery).
 */

import crypto from "node:crypto";

import { PBKDF2_ITERATIONS } from "../config.js";

const ALGO = "pbkdf2_sha256";
const SALT_BYTES = 16;
// SHA-256's digest length. Python's pbkdf2_hmac defaults dklen to the hash
// size; Node makes it an explicit argument, so it is spelled out here.
const KEY_LENGTH = 32;

function pbkdf2(password: string, salt: Buffer, iterations: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    crypto.pbkdf2(password, salt, iterations, KEY_LENGTH, "sha256", (err, derived) => {
      if (err) reject(err);
      else resolve(derived);
    });
  });
}

/** Return a self-describing hash: `pbkdf2_sha256$iterations$salt$hash`. */
export async function hashPassword(password: string): Promise<string> {
  const salt = crypto.randomBytes(SALT_BYTES);
  const derived = await pbkdf2(password, salt, PBKDF2_ITERATIONS);
  return [
    ALGO,
    PBKDF2_ITERATIONS,
    salt.toString("base64"),
    derived.toString("base64"),
  ].join("$");
}

/**
 * Constant-time check of *password* against a stored hash.
 *
 * The iteration count is read out of the stored string rather than taken from
 * config, so raising `PBKDF2_ITERATIONS` does not lock out everyone who
 * registered before the change. Their hashes keep verifying at the count they
 * were written with.
 */
export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  try {
    const parts = stored.split("$");
    if (parts.length !== 4) return false;

    const [algo, iterationsText, saltB64, hashB64] = parts;
    if (algo !== ALGO) return false;

    const iterations = Number.parseInt(iterationsText, 10);
    if (!Number.isFinite(iterations) || iterations <= 0) return false;

    const salt = Buffer.from(saltB64, "base64");
    const expected = Buffer.from(hashB64, "base64");
    const derived = await pbkdf2(password, salt, iterations);

    // timingSafeEqual throws on a length mismatch rather than returning false,
    // so the lengths are compared first — and a mismatch here is a corrupt row,
    // not a wrong password.
    if (derived.length !== expected.length) return false;
    return crypto.timingSafeEqual(derived, expected);
  } catch {
    return false;
  }
}

/** A fresh opaque session token (raw value — only ever sent to its owner). */
export function generateSessionToken(): string {
  // Python's `secrets.token_urlsafe(32)`: 32 random bytes, base64url, unpadded.
  return crypto.randomBytes(32).toString("base64url");
}

/** SHA-256 of a session token. Only this goes in the database. */
export function hashSessionToken(token: string): string {
  return crypto.createHash("sha256").update(token, "utf8").digest("hex");
}

/**
 * Constant-time string comparison, for secrets that are compared literally
 * (the admin key, the admin username and password).
 *
 * Node's `timingSafeEqual` throws when the buffers differ in length, which on
 * its own would leak length through an exception. Hashing both sides first
 * gives two equal-length digests to compare, so only equality is observable.
 */
export function constantTimeEqual(a: string, b: string): boolean {
  const left = crypto.createHash("sha256").update(a, "utf8").digest();
  const right = crypto.createHash("sha256").update(b, "utf8").digest();
  return crypto.timingSafeEqual(left, right);
}
