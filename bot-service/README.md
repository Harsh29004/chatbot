# Nexora AI — bot service

The retrieval half of the product. Python, FastAPI, and the same modules the
original app used — copied across unchanged.

## Why this is Python

Not sentiment. Every piece of this service is a Python library with no
equivalent worth migrating to:

- **sentence-transformers** produced every vector already in your database. An
  embedding is only meaningful relative to the model that made it, so replacing
  the implementation would put queries and stored rows in different vector
  spaces. The 0.85/0.60 thresholds would stop corresponding to anything and
  every sheet a customer has uploaded would need re-indexing.
- **pandas, openpyxl, pypdf and the rest** read the dozen formats customers
  actually send. `readers.py` dispatches on extension, and on the bytes when
  the extension lies.
- **numpy** scores the vectors.
- **LangGraph** is the retrieval pipeline.

The Node backend owns accounts, sessions, billing, credits, referrals, the
admin panel, support and the dashboard assistant — none of which needed any of
the above.

## Where the line is

**Node is the only thing the internet talks to.** It authenticates the request
(session cookie or `X-Api-Key`), meters credits, resolves *which account this
is*, then calls this service over loopback with a plain `user_id`.

Nothing here checks a password, reads a cookie, or knows what a credit is. By
the time a request arrives, all of that is settled — which is why these routes
take a `user_id` and trust it. It is not a credential and was never sent by a
browser; it is Node's answer to "who is this".

**Collections are owned, not shared.** One database, two services, no
overlapping writers:

| Written by this service | Written by Node |
|---|---|
| `bots` | `customers`, `users`, `sessions` |
| `faq_vectors`, `vector_sets` | `api_keys`, `daily_usage`, `request_log`, `credit_grants` |
| `template_overrides` | `subscriptions`, `invoices`, referral collections |
| `unmatched_queries` | `support_*`, `assistant_*`, `client_errors`, `rate_events` |
| `llm_cooldowns` | `llm_cooldowns` |

`llm_cooldowns` is the one both touch, and deliberately: when a model is rate
limited, both services should know. That is the entire point of keeping it in
the database rather than in a process.

Node reads a few bot-owned collections for admin analytics — joining bots
against subscriptions is inherently cross-domain — but writes none of them.

## Running it

```bash
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8001
```

The model loads at startup rather than on first request, so the first customer
query after a deploy does not pay several seconds for it and look broken.

**Bind to loopback.** There is no user-facing authentication here because there
are no users here. Set `INTERNAL_API_KEY` to the same value as the Node backend
and every call is checked against it; leave it unset and the service answers
anyone who can reach the port, which startup warns about.

Configuration comes from the repo-root `.env` — the same file the rest of the
stack reads, with every variable keeping its original name. A `bot-service/.env`
is also honoured and wins.

## What it exposes

Everything under `/internal` is for Node. `/health` and the two `/embed`
endpoints are the exceptions.

| Method | Path | What |
|---|---|---|
| GET | `/health` | liveness, loaded model, whether an LLM is reachable |
| GET | `/internal/templates` | the ten templates, minus retired ones |
| GET | `/internal/templates/{id}/starter-sheet` | pre-filled CSV |
| GET | `/internal/bot` | one account's bot, created on first read |
| PUT | `/internal/bot` | pick or switch template |
| PUT | `/internal/bot/answering` | grounded rewording on/off |
| POST | `/internal/bot/sheet` | upload and index a sheet |
| GET | `/internal/bot/gaps` | what this bot could not answer |
| POST | `/internal/ask` | **the answer path** |
| GET | `/internal/widget/themes` | the seven designs |
| GET | `/internal/widget/themes/{id}/package` | install zip |
| GET | `/internal/widget/script/{id}` | hosted widget script |
| GET | `/internal/widget/activation` | is this account's bot ready |
| GET | `/internal/admin/templates` | catalogue + defaults + adoption |
| PATCH | `/internal/admin/templates/{id}` | edit a template |
| POST | `/internal/admin/templates/{id}/reset` | restore code defaults |
| GET | `/internal/snapshot` | cross-tenant ops snapshot |
| GET | `/internal/account-bot` | one account's bot, for the assistant prompt |
| POST | `/embed`, `/embed-batch` | raw embeddings |

`/internal/widget/*` takes an `api_base` parameter rather than deriving it,
because this service only ever sees a loopback origin and would otherwise bake
the wrong host into every customer's install.

## What's in here

`main.py` is the only new file. Everything else was copied from the original
application with its package path intact, so `from backend.shared.config import
...` and `from bot.graph import ...` resolve exactly as they did:

```
backend/shared/   config, mongo, embeddings, vector_store,
                  guardrails, input_policy, llm, logging_store
backend/ops.py    the cross-tenant snapshot
bot/              templates, catalogue, store, graph, grounding, ingest, readers
bot/widget/       themes, package, nexora-widget.js
```

Two edits were needed and both are commented where they are:

1. `mongo.py` — the index registry lists only the collections this service
   owns. The rest belong to Node, which applies their indexes itself.
2. `config.py` — `.env` is now one directory up, so both locations are tried.

`bot/store.py` gained `template_adoption()`, which moved here from the admin
query module: it is a pure read of the `bots` collection, and that collection
now has exactly one writer.
