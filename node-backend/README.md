# Nexora AI — Node.js backend

Express + TypeScript. Converted from the FastAPI application in `../backend`;
the bot pipeline it used to contain now lives in `../bot-service`, still in
Python.

## What runs where

| Process | Language | Port | What it does |
|---|---|---|---|
| `node-backend` | Node 20+ / TypeScript | 8000 | Accounts, billing, admin, support, assistant |
| `bot-service` | Python / FastAPI | 8001 | Retrieval, sheet ingest, templates, widget |
| MongoDB | — | 27017 / Atlas | Unchanged |

**This server is the only thing the internet talks to.** It authenticates every
request (session cookie or `X-Api-Key`), meters credits, resolves which account
is asking, and then calls the bot service over loopback with a plain `user_id`.
The bot service has no users, no cookies and no concept of a credit.

The split is by what each language is actually good for. Everything here is
accounts, money and HTTP plumbing. Everything there is sentence-transformers,
numpy and pandas — libraries with no equivalent worth migrating, and the
producers of every vector already in your database. See
`../bot-service/README.md`.

Collections are owned rather than shared. This server writes the account,
billing, support, assistant and telemetry collections, and never touches
`bots`, `faq_vectors`, `vector_sets`, `template_overrides` or
`unmatched_queries`. It reads a few of them for admin analytics — joining bots
against subscriptions is inherently cross-domain — but writes none.

## Running it

```bash
# 1. the bot service, in its own terminal
cd ../bot-service
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8001

# 2. this server
cd ../node-backend
npm install
npm run dev            # tsx watch, reloads on change
```

Or, for a throwaway run of both against an in-memory MongoDB:

```bash
npm run build && node run-stack.mjs
```

`npm run build && npm start` for production.

Configuration is read from `../.env` — the same file the Python build used, with
every variable keeping its original name. A `.env` in this directory is also
honoured and takes precedence.

The only new variables point at the bot service:

```
BOT_SERVICE_URL=http://127.0.0.1:8001
INTERNAL_API_KEY=                     # shared secret; both processes need it
BOT_SERVICE_TIMEOUT_MS=60000
BOT_SERVICE_INGEST_TIMEOUT_MS=300000  # embedding a large sheet is the slow one
```

See `.env.example` for the full annotated list.

## Verifying it

```bash
node smoke-test.mjs      # 66 checks, boots the whole stack
node route-parity.mjs    # every FastAPI route is still mounted
```

The smoke test boots an in-memory MongoDB, starts the Python bot service against
it, starts this server against both, and walks the paths a customer actually
takes — sign up, sign in, dashboard, template, sheet upload, API key, `/v1/ask`,
billing, admin, logout. One command. If Python or its dependencies are missing,
the retrieval steps are reported as skipped rather than failed.

It also asserts the thing the whole migration rests on: that this server
verifies a password hash produced by the original Python code. Section 1.

## Minting an owner key

```bash
npm run create-owner-key -- owner@example.com "Your Name"
```

Deliberately has no HTTP surface, exactly as before.

## How the conversion maps

### Moved to TypeScript

| Python | TypeScript |
|---|---|
| `backend/server.py` | `src/server.ts` |
| `backend/shared/config.py` | `src/config.ts` |
| `backend/shared/mongo.py` | `src/shared/mongo.ts` |
| `backend/shared/api_keys.py` | `src/shared/apiKeys.ts` |
| `backend/shared/auth.py` | `src/shared/auth.ts` |
| `backend/shared/llm.py` | `src/shared/llm.ts` |
| `backend/shared/guardrails.py` | `src/shared/guardrails.ts` |
| `backend/shared/input_policy.py` | `src/shared/inputPolicy.ts` |
| `backend/shared/logging_store.py` | `src/shared/loggingStore.ts` |
| `backend/shared/telemetry_store.py` | `src/shared/telemetryStore.ts` |
| `backend/shared/rate_limits.py` | `src/shared/rateLimits.ts` |
| `backend/billing/*` | `src/billing/*` |
| `backend/admin/*` | `src/admin/*` |
| `backend/assistant/*` | `src/assistant/*` |
| `backend/support/*` | `src/support/*` |
| `backend/api_keys_router.py` | `src/routes/apiKeysRouter.ts` |
| `backend/owner_router.py` | `src/routes/ownerRouter.ts` |
| `backend/telemetry_router.py` | `src/routes/telemetryRouter.ts` |

### Stayed Python, behind a client

| Python | Reached through |
|---|---|
| `bot/graph.py`, `grounding.py`, `ingest.py`, `readers.py` | `src/bot/client.ts` |
| `bot/store.py`, `templates.py`, `catalogue.py` | `src/bot/client.ts` |
| `bot/widget/*` | `src/bot/widget/router.ts` |
| `backend/shared/embeddings.py`, `vector_store.py` | never called directly |
| `backend/ops.py` | `src/ops.ts` |

`src/bot/router.ts` is what is left of the old bot router: it authenticates,
charges, resolves the account, and forwards. Roughly 200 lines where there were
1,400, because the 1,200 that decided *what the answer is* went back to Python.

### Library substitutions

| Python | Node | Note |
|---|---|---|
| FastAPI | Express 4 | `Depends()` became middleware that attaches to `req` |
| Pydantic | Zod | Request bodies only; responses are TypeScript interfaces |
| PyMongo | `mongodb` driver | Sync → async; every store function is now `async` |
| `hashlib.pbkdf2_hmac` | `crypto.pbkdf2` | **Byte-identical.** Existing passwords work |
| `httpx` | `fetch` | Including SSE streaming, hand-rolled in `llm.ts` |
| `firebase-admin` (Python) | `firebase-admin` (Node) | Same product, same verification |

`llm.py` exists in both services, and that is deliberate rather than an
oversight: each one talks to its own model providers, and they cooperate
through the shared `llm_cooldowns` collection — so when a model is rate
limited, both back off. The same applies to `input_policy`: each service
screens the input it is about to act on.

## One difference from the original

`assistant/context.py` read `usage["daily_credit_limit"]`, a key the usage
object does not have — it is `credits_daily_limit` — so the assistant's prompt
always said "of None". `src/assistant/context.ts` reads the field that exists.
Prompt text only.

Everything else behaves as it did, including every upload format: the readers
are the original Python ones, so `.parquet` and the rest work exactly as before.
