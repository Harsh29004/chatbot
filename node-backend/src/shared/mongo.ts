/**
 * The MongoDB connection, and the small number of conventions every store follows.
 *
 * Port of `backend/shared/mongo.py`. One client, shared: the Node driver owns a
 * connection pool and is safe to share, so creating one per module would be a
 * dozen pools to the same cluster for no reason.
 *
 * Two conventions the whole codebase depends on
 * ---------------------------------------------
 *
 * **Ids are ObjectIds in the database and strings everywhere else.** A document
 * stores `_id: ObjectId(...)`; {@link document} hands callers back `id` as a
 * 24-character string. Nothing above this module should ever see an ObjectId,
 * because the moment one reaches a response body it becomes a serialisation
 * problem at the worst possible time.
 *
 * **Indexes are declared, not created ad hoc.** Every store registers its
 * indexes with {@link registerIndexes} at import, and {@link ensureIndexes}
 * applies them once at startup. Uniqueness that used to be a `UNIQUE` column is
 * a unique index here — which matters, because two of them (one account per
 * mailbox, one referral payout per invoice) are load-bearing rather than tidy.
 *
 * One difference from the Python original worth knowing: PyMongo is
 * synchronous, and every store was called from a worker thread. The Node driver
 * is promise-based, so every store function here is `async` and every call site
 * awaits. That is the single largest mechanical change in the conversion.
 */

import {
  Collection,
  Db,
  Document as MongoDocument,
  IndexSpecification,
  CreateIndexesOptions,
  MongoClient,
  MongoServerError,
  ObjectId,
  ClientSession,
} from "mongodb";

import { MONGO_DB_NAME, MONGO_TIMEOUT_MS, MONGO_URI } from "../config.js";
import { logger } from "./logger.js";

export { ObjectId };

/** Any JSON-shaped record the stores hand back. */
export type Doc = Record<string, any>;

let client: MongoClient | null = null;
let connecting: Promise<MongoClient> | null = null;

type IndexSpec = [IndexSpecification, CreateIndexesOptions];
const indexRegistry = new Map<string, IndexSpec[]>();

// ---------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------

/**
 * The process-wide client, created on first use.
 *
 * Lazy rather than at import so that a missing or wrong `MONGO_URI` fails when
 * something actually needs the database — not while a CLI script that never
 * touches Mongo is starting up.
 *
 * `connecting` is held so that a burst of concurrent first requests share one
 * connect rather than opening a client each. PyMongo connected lazily inside a
 * lock; this is the promise-shaped equivalent.
 */
export async function getClient(): Promise<MongoClient> {
  if (client) return client;
  if (connecting) return connecting;

  connecting = (async () => {
    const created = new MongoClient(MONGO_URI, {
      // Atlas over the public internet is not a local socket; a request should
      // fail with a readable error rather than hang a worker until the client
      // gives up.
      serverSelectionTimeoutMS: MONGO_TIMEOUT_MS,
      appName: "nexora",
      ignoreUndefined: false,
    });
    await created.connect();
    client = created;
    connecting = null;
    logger.info(`MongoDB client created for database ${MONGO_DB_NAME}.`);
    return created;
  })();

  try {
    return await connecting;
  } catch (error) {
    connecting = null;
    throw error;
  }
}

/**
 * Replace the shared client, closing the old one.
 *
 * Exists for tests, which point every store at a throwaway database between
 * cases. Passing `null` drops the client so the next call rebuilds it from the
 * current environment.
 */
export async function resetClient(next: MongoClient | null = null): Promise<void> {
  if (client && next !== client) {
    try {
      await client.close();
    } catch {
      // Closing a dead client is not a failure worth propagating.
    }
  }
  client = next;
  connecting = null;
  transactionsSupported = null;
}

export async function database(): Promise<Db> {
  return (await getClient()).db(MONGO_DB_NAME);
}

/**
 * The named collection.
 *
 * Every store goes through here, never through a module-level handle — a
 * cached `Collection` would keep pointing at the old database after
 * {@link resetClient}.
 */
export async function coll<T extends MongoDocument = MongoDocument>(
  name: string,
): Promise<Collection<T>> {
  return (await database()).collection<T>(name);
}

// ---------------------------------------------------------------------------
// Ids
// ---------------------------------------------------------------------------

/**
 * Coerce a value to an ObjectId, or `null` when it cannot be one.
 *
 * `null` rather than a throw because almost every caller is turning a path
 * parameter or a query string into a lookup, and "no such id" and "not a valid
 * id" deserve the same 404.
 */
export function objectId(value: unknown): ObjectId | null {
  if (value instanceof ObjectId) return value;
  if (value === null || value === undefined) return null;
  const text = String(value);
  if (!ObjectId.isValid(text)) return null;
  try {
    return new ObjectId(text);
  } catch {
    return null;
  }
}

/** Like {@link objectId}, but for internal callers that know it is one. */
export function toObjectId(value: unknown): ObjectId {
  const oid = objectId(value);
  if (oid === null) throw new Error(`${String(value)} is not a valid id.`);
  return oid;
}

export function isObjectId(value: unknown): boolean {
  return objectId(value) !== null;
}

/**
 * A stored document as the rest of the codebase wants it.
 *
 * `_id` becomes a string `id`, and every ObjectId field alongside it
 * (`customer_id`, `user_id`, `referral_id` …) becomes a string too. The layers
 * above this one deal in JSON-safe values only, so the conversion belongs here
 * rather than in thirty call sites that each have to remember.
 */
export function document<T extends MongoDocument>(doc: T | null | undefined): Doc | null {
  if (doc === null || doc === undefined) return null;

  const out: Doc = {};
  for (const [key, value] of Object.entries(doc)) {
    if (key === "_id") {
      out.id = value instanceof ObjectId ? value.toHexString() : value;
    } else if (value instanceof ObjectId) {
      out[key] = value.toHexString();
    } else if (Array.isArray(value)) {
      out[key] = value.map((v) => (v instanceof ObjectId ? v.toHexString() : v));
    } else {
      out[key] = value;
    }
  }
  return out;
}

export function documents<T extends MongoDocument>(docs: Iterable<T>): Doc[] {
  const out: Doc[] = [];
  for (const doc of docs) {
    const converted = document(doc);
    if (converted !== null) out.push(converted);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Indexes
// ---------------------------------------------------------------------------

/**
 * Declare the indexes a collection needs.
 *
 * Called at module import so the declaration sits next to the queries that rely
 * on it, and applied later by {@link ensureIndexes}. Registering twice for the
 * same collection replaces the previous list rather than appending, which keeps
 * a re-imported module from stacking duplicates.
 */
export function registerIndexes(collection: string, specs: IndexSpec[]): void {
  indexRegistry.set(collection, specs);
}

/**
 * Every module that registers indexes.
 *
 * Imported by {@link ensureIndexes} before it applies anything, because
 * registration happens at import: without this, the indexes that get created
 * depend on which modules the caller happened to have imported first, and the
 * one most likely to be missing is the one nothing has touched yet. A silently
 * absent unique index is not a missing optimisation, it is a missing
 * constraint.
 *
 * The bot-side collections — `bots`, `faq_vectors`, `vector_sets`,
 * `template_overrides`, `unmatched_queries` — are not here. They belong to the
 * Python bot service, which applies their indexes at its own startup. The two
 * services share one database and write disjoint halves of it; whoever writes a
 * collection is who declares its constraints.
 */
const INDEX_OWNING_MODULES = [
  "./apiKeys.js",
  "../billing/db.js",
  "../assistant/store.js",
  "../support/store.js",
  "./rateLimits.js",
  "./llm.js",
  "./telemetryStore.js",
];

/**
 * Apply every registered index. Safe to run on each startup.
 *
 * `createIndex` is idempotent for an identical spec, and raises when a name
 * exists with *different* options — which is the useful failure, since it means
 * a deploy changed an index definition and somebody has to decide what happens
 * to the old one.
 */
export async function ensureIndexes(): Promise<Record<string, number>> {
  for (const specifier of INDEX_OWNING_MODULES) {
    await import(specifier);
  }

  const applied: Record<string, number> = {};
  for (const [name, specs] of indexRegistry) {
    const collection = await coll(name);
    for (const [keys, options] of specs) {
      await collection.createIndex(keys, options);
    }
    applied[name] = specs.length;
  }
  logger.info(`Ensured indexes on ${Object.keys(applied).length} collections.`);
  return applied;
}

// ---------------------------------------------------------------------------
// Duplicate keys
// ---------------------------------------------------------------------------

/**
 * True for the error a unique index raises on a collision.
 *
 * PyMongo had a `DuplicateKeyError` class the stores could catch by type. The
 * Node driver reports it as a `MongoServerError` with code 11000, so the type
 * test is a function instead.
 */
export function isDuplicateKeyError(error: unknown): boolean {
  return error instanceof MongoServerError && error.code === 11000;
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

// Whether the connected deployment can run multi-document transactions. Atlas
// and any replica set can; a standalone mongod cannot. Probed once on first use
// rather than assumed, because the answer differs between a developer's laptop
// and production and neither should have to configure it.
let transactionsSupported: boolean | null = null;

async function supportsTransactions(): Promise<boolean> {
  if (transactionsSupported !== null) return transactionsSupported;

  try {
    const admin = (await getClient()).db("admin");
    const hello = await admin.command({ hello: 1 });
    const info = await admin.command({ buildInfo: 1 });
    const major = Number.parseInt(String(info.version ?? "0").split(".")[0], 10);
    transactionsSupported =
      Boolean(hello.setName || hello.msg === "isdbgrid") && major >= 4;
  } catch {
    transactionsSupported = false;
  }

  if (!transactionsSupported) {
    logger.info(
      "MongoDB deployment does not support transactions — multi-document " +
        "writes will be applied in order instead of atomically.",
    );
  }
  return transactionsSupported;
}

/**
 * Run a block of writes in one transaction where the deployment allows it.
 *
 * Hands the callback a session to pass to each write, or `undefined` when
 * transactions are unavailable — the Node driver accepts `session: undefined`
 * everywhere, so the calling code is identical either way and does not sprout a
 * branch per write.
 *
 * This is deliberately quiet about the difference. The writes that use it
 * (spending credits, paying a referral) are already ordered so that the worst a
 * partial failure can do is under-charge or leave a reward unpaid, both of
 * which are recoverable and neither of which loses money. On a replica set they
 * cannot partially apply at all.
 */
export async function atomic<T>(
  work: (session: ClientSession | undefined) => Promise<T>,
): Promise<T> {
  if (!(await supportsTransactions())) {
    return work(undefined);
  }

  const session = (await getClient()).startSession();
  try {
    let result!: T;
    await session.withTransaction(async () => {
      result = await work(session);
    });
    return result;
  } finally {
    await session.endSession();
  }
}

export type { ClientSession };
