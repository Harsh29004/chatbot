# Architecture

A folder-by-folder reference for this codebase: what each directory owns, and
why the boundaries fall where they do.

## The one idea the layout serves

**The bot cannot state a fact the customer didn't write down.**

By default it cannot write prose at all: a question is embedded, matched
against the customer's own spreadsheet, and the stored answer is returned
verbatim — or the bot declines. Nothing in that path generates text, so
inventing is not something it is told not to do, it is something it structurally
cannot do.

An owner may opt in, per bot, to **grounded rewording**: a local model that
rephrases near-band matches. That narrows the guarantee from "no prose" to "no
unsourced fact", and not a step further — the model only ever sees rows
retrieval already returned, and its output is verified against those rows
before it is sent (`bot/grounding.py`). Facts stay the customer's; only the
sentences change.

Every boundary below exists to keep that true.

## Why the folders split where they do

Each folder owns exactly one question. If you can answer "which question does
this file belong to?", you know where it goes.

| Folder | Owns the question | Depends on |
|---|---|---|
| `bot/` | *What may this bot say, and what does it say when it doesn't know?* | `backend/shared/` |
| `backend/` | *Who is asking, are they allowed, and have they paid?* | nothing above it |
| `frontend/` | *How does a customer set this up without reading docs?* | the HTTP API only |

The import arrow runs **one way**: `bot/` imports from `backend/shared/` and
never the reverse. The single exception is `backend/server.py`, which mounts the
bot's router — the composition root is the one place allowed to know about both
halves.

```
  frontend/  ──HTTP /api──▶  backend/  ◀──imports shared/──  bot/
                                  └────mounts bot/router.py────▶
                                        (server.py only)
```

Everything below `bot/` can be read without knowing what a subscription is.
Everything in `backend/` can be read without knowing what a template is.

One Python import root sits at the project root, so `backend.*` and `bot.*`
resolve from any entry point.

---

## `bot/` — the product itself

Six files. This is the thing customers actually pay for.

| File | Job | The decision inside it |
|---|---|---|
| `templates.py` | Ten frozen `BotTemplate` dataclasses — the scope contract | A template is **not a personality**. It is a boundary: what the bot may discuss, the exact words it refuses with, and how sure it must be. |
| `graph.py` | The LangGraph `StateGraph` — six nodes, two routing decisions | Nodes **close over** a frozen `BotConfig` rather than reading globals, so a clinic bot at 0.90 and a salon bot at 0.84 run side by side in one process. |
| `readers.py` | Any uploaded file → a Question/Answer table. Spreadsheets, CSV/TSV, JSON, JSONL, YAML, XML, HTML, Markdown, text, Word, PDF, parquet, zip | The extension is a hint, not the truth — an unrecognised name is **sniffed from the bytes**, and anything that isn't a table is read as prose and mined for Q/A pairs. Column names go through an alias table so `prompt`/`completion` and `q`/`a` land on the canonical four. |
| `ingest.py` | That table → vectors | Each phrasing becomes **its own vector**. The answer rides as metadata, never in the searchable text, so it comes back untouched. |
| `store.py` | One bot per account — table keyed `user_id UNIQUE` | `collection_name_for(bot_id)` names each ChromaDB collection from the bot's id. Tenant isolation is structural, not a `WHERE` clause someone can forget. |
| `grounding.py` | The grounded prompt and, more importantly, the check on its output | The prompt is not the safeguard — the verification is. Ungrounded text and unsourced numbers are discarded and the verbatim answer is sent instead. |
| `router.py` | Templates, starter sheet, upload, gaps, preview, the opt-in toggle, and `POST /v1/ask` | `/api/bot/preview` costs no credits — testing your own bot shouldn't bill you. |

### The answer pipeline

```
check_guardrails ──┬─ input policy refusal ─▶ decline_and_log
                   ├─ action intent ────────▶ decline_and_log
                   │ clear
                   ▼
  embed_query ──▶ retrieve_top_k ──┬─ score ≥ strong ──▶ strong_response  (verbatim)
                                   ├─ score ≥ near   ──▶ near_response    (+ handoff, logged)
                                   │                       └─ opted in? ──▶ grounded, verified
                                   │                          fails ──────▶ back to verbatim
                                   └─ below near     ──▶ decline_and_log  (no model call)
```

The action guardrail runs *before* retrieval on purpose. "Cancel my order" is
refused no matter how well it matches the sheet — the bot explains cancelling,
it does not cancel.

### Why one vector per phrasing

Concatenating a question with its alternate wordings drags the embedding away
from all of them at once. A word-for-word sheet question used to score ~0.76 and
get served as a hedged "near" match. Indexing each phrasing separately takes
that to ~1.00.

### Why a bad row is not fatal

Blank and duplicate rows are **reported, not rejected** — one bad row shouldn't
cost someone their whole upload. Re-uploading **replaces** the index, because
the sheet is the source of truth and you must be able to remove an answer.

### The catalogue is code; the edits are data

`bot/templates.py` is ten frozen dataclasses, and `bot/catalogue.py` lays the
admin's overrides on top. They are two modules because `bot/store.py` imports
the templates, so the templates cannot import the store back — everything that
needs the *live* catalogue imports `catalogue`, and `templates` stays a pure
constant table that tests can read without a database.

Every override column is nullable on purpose: NULL means "no opinion, use the
code value". That is what makes an override a patch rather than a copy — a
template edited today keeps inheriting improvements to the fields nobody
touched, and deleting the row restores the original exactly.

### Thresholds are a product decision, stored as data

A wrong answer about a haircut costs an apology. A wrong answer about a drug
interaction or a loan penalty costs considerably more — so `clinic` and
`fintech` demand a closer match before they speak, and fall silent sooner.

| Template | near | strong |
|---|---|---|
| `clinic` | 0.72 | **0.90** |
| `fintech` | 0.72 | **0.90** |
| `realestate` | 0.65 | **0.87** |
| `education` | 0.62 | **0.85** |
| `travel` | 0.62 | **0.85** |
| `saas` | 0.60 | **0.85** |
| `logistics` | 0.60 | **0.85** |
| `ecommerce` | 0.60 | **0.84** |
| `restaurant` | 0.60 | **0.84** |
| `services` | 0.60 | **0.84** |

Below `near` the bot declines and logs the gap; between `near` and `strong` it
answers with a handoff nudge and logs it; at or above `strong` it answers
verbatim.

The global defaults in `backend/shared/config.py` are `0.60` and `0.85`, but
every template overrides them — the defaults only matter to a bot that hasn't
picked one yet.

---

## `backend/` — everything around the product

Nothing here knows what a template is. It knows who you are, whether you've
paid, and how many credits you have left today.

### `shared/mongo.py` — the one place that knows about the database

Eight modules used to each own a thread-local SQLite handle. They now share one
`MongoClient`, which is thread-safe and pools connections, so eight pools to
one cluster would have bought nothing.

It carries two conventions the whole codebase leans on. Ids are ObjectIds
inside and strings outside, converted at exactly one boundary. And indexes are
*declared* by each store at import and applied once at startup — including the
two that are constraints rather than optimisations, which is why a failure to
build them stops the server rather than being logged and shrugged off.

`ensure_indexes()` imports the store modules itself before applying anything.
Registration happens at import, so without that the indexes created would
depend on what the caller had imported first, and a silently absent unique
index is a missing constraint, not a missing optimisation.

### `shared/` — infrastructure both halves need

| File | Job | Worth knowing |
|---|---|---|
| `config.py` | Env settings, IST timezone, credit tiers | Holds only **defaults**; per-bot values live on templates. |
| `api_keys.py` (561 ln) | Users, keys, the credit pool | Credits pool **per account, not per key**. Ten keys separate staging from prod; they don't buy ten times the capacity. |
| `auth.py` | `verify_api_key` / `verify_owner_key` / `verify_admin_key` | Three trust levels that never share a code path. The owner check is role-based, and refuses a valid customer key with the same message a garbage one gets. |
| `guardrails.py` | Injection + action-intent detection | Asymmetric on purpose — see below. |
| `embeddings.py` | sentence-transformers wrapper | Lazy thread-safe singleton. The model costs 10–15s to load, once. |
| `vector_store.py` | ChromaDB helpers | Collection-scoped. There is no shared retrieval space to leak across. |
| `logging_store.py` | The unmatched-question log | This is the gap list — the loop that makes a bot get better. |
| `input_policy.py` | What may reach the model, and what never may | Separate from `guardrails.py` on purpose, so the verbatim path keeps its original, gentler contract. |
| `llm.py` | Ollama client (sync + streaming) | Every entry point returns `None`, or an empty stream, rather than raising. A bot must not stop answering because an optional component is down. |

**The guardrails asymmetry.** On the verbatim path the injection detector is
**logging-only**; the action detector **blocks**. That isn't an oversight. If
the injection detector gated the pipeline it would become an attack surface —
trip it deliberately, deny service — and since user input never enters a prompt
there, injection has nothing to inject *into*. Worth recording; not worth
blocking on.

That reasoning stops holding the moment a model reads the text, which is why
`input_policy.py` exists as a separate gate and promotes the same detector to a
hard block for bots that opted in. **The detector's status changes with the
pipeline, not with the text.**

### `billing/` — the money

| File | Job | Worth knowing |
|---|---|---|
| `plans.py` | Single source of truth for prices | **Integer cents everywhere, never floats.** `0.1 + 0.2 != 0.3`, and that eventually lands in an invoice. The yearly discount is derived and rounded **down**, so the advertised number is never better than what the customer gets. |
| `db.py` | Customers, sessions, subscriptions, invoices, referrals | Same MongoDB database as the key collections, so granting a paying customer their allowance is one round trip, not a cross-service dance. Uniqueness that used to be a `UNIQUE` column is a unique index, including the two that are constraints rather than optimisations. |
| `security.py` | PBKDF2-HMAC-SHA256, session tokens | Tokens are opaque and stored **as hashes** — a leaked database can't be replayed as live sessions, and unlike a JWT they're revocable without extra machinery. |
| `payments.py` | Hosted-checkout adapters | **No card number ever touches this server.** Both real providers currently raise rather than silently granting access. |
| `entitlements.py` (61 ln) | The bridge between *paying* and *may use* | The most important file in the folder — see below. |
| `router.py` | Auth, billing, dashboard, key issue/revoke | Session is an **httpOnly cookie, not localStorage**. A stored token is readable by any injected script; an httpOnly cookie is not. |
| `google_oauth.py` | Google sign-in, server-side code flow | The browser never holds a Google credential. Three guards: verified signature, checked `state`, and linking only on `email_verified`. |

**Why `entitlements.py` exists at all.** Two stores have to agree: billing knows
whether a subscription is live, `api_keys` knows the daily allowance. Its own
docstring records the bug that created it — the first version only wired the
**grant** direction. Paying raised your allowance; lapsing did nothing, so a
cancelled customer kept paid credits forever. Every transition now goes through
one place.

### `server.py` — the composition root

Initialises four table sets, runs a startup sweep to retire plans that lapsed
while the server was down, starts the background sweep loop, mounts three
routers, then registers the SPA fallback **last** so it can never shadow an API
route. The fallback carries an `is_relative_to(WEB_DIST)` containment check —
without it, a crafted path escapes the bundle.

### `assistant/` — the other answer path

A general-purpose chat assistant for signed-in customers, running `qwen2.5:7b`
locally. It is not the FAQ bot and shares none of its rules: a bot answers
strangers on a customer's behalf, so it may only repeat what its owner wrote;
this answers the owner themselves, in their own dashboard, about anything.

| File | Job | Worth knowing |
|---|---|---|
| `store.py` | Threads and messages | Every read is **scoped by `customer_id` in the query itself**, not by the caller remembering to filter. |
| `context.py` | What it knows about *this* customer | No cross-tenant branch exists in the module. The one that reads every tenant is `ops.py`, behind different auth. |
| `router.py` | Threads, and the streaming chat endpoint | Session cookie only, under `/api/assistant`. Nothing is registered under `/v1`. |

**Why it is not on any API-key route.** What customers integrate is a bot that
answers from their sheet. A general-purpose model reachable with the same key
would quietly turn the product into something else, so the exclusion is
structural — a different prefix, a different auth dependency, and a test that
asserts no assistant path appears under `/v1`.

**Why history is capped on read, not on write.** The person scrolls the whole
conversation; the model gets the last `ASSISTANT_HISTORY_TURNS`. Those are
different jobs, and one limit would do one of them badly.

**Why one generation at a time.** Two concurrent generations on two ARM cores
don't run in parallel in any useful sense — they halve both speeds. Requests
queue, then are turned away with a reason. Queueing is honest; pretending to
multitask is not.

### `support/` — the third answer path

Customers and staff talking to each other. No model: the bot answers from a
sheet, the assistant from a local model, and this one from a person. Keeping
the three apart is why it is a package rather than another mode on an existing
one.

| File | Job | Worth knowing |
|---|---|---|
| `store.py` | Conversations and messages | One conversation per customer. Read position is a **message id**, not a timestamp. |
| `router.py` | Customer side + staff inbox | Two prefixes, two headers. `/api/support/...` is cookie-auth; `/api/support/admin/...` is admin-key. |

**The customer routes take no conversation id.** That is the whole isolation
story: nothing to guess, no ownership check to forget, because the parameter
does not exist. A test asserts no customer support path contains one.

**Read markers are message ids because clocks tie.** On Windows, consecutive
`datetime.now()` calls return the same value. With timestamp markers, a message
written in the same tick as a read would compare as already-read and never
surface as unread. Ids are monotonic; the inbox ordering breaks ties on id too.

### `admin/` — the second deliberate exception

Cross-tenant, like `ops.py`, and different from it in the way that matters:
`ops` feeds a *model*, so it carries aggregates without identities; `admin`
feeds a *person who runs the platform*, so it must be able to answer "which
customer is this?" and does return names, emails and per-account numbers.

The admin key gates it, declared once on the router rather than per route — a
new endpoint added here is protected by default, and forgetting a decorator
cannot quietly open a cross-tenant hole.

Reads live in `queries.py`; writes deliberately do not. Granting credits goes
through `api_keys`, editing a template through `bot.catalogue`, disabling an
account through both stores that have an opinion about it. The router
validates, delegates, and reports — it owns no state of its own.

One shape worth copying: the per-account query is a pile of correlated
subqueries, not a pile of `LEFT JOIN`s. Each aggregate is over a different
grain, and joining them would multiply rows and silently inflate every number
on the screen.

### `billing/referrals.py` — policy, next to the data it moves

The split mirrors `entitlements.py`: `db.py` owns the tables, this module owns
what the programme *pays*. Rewards are written before the credits move, so the
unique index — not a remembered check — is what makes a repeated webhook a
no-op. Anything that reads "we already paid this" in application code is a race
waiting for a provider retry.

Rewards land in a **bonus balance** rather than the daily allowance. A referral
credit that expired at midnight IST would be worth nothing to someone who
earned it at 11pm, and "you have 100 credits" has to mean 100 credits.

### `ops.py` and `owner_router.py` — the deliberate exception

Everywhere else, a query scoped to one account is the rule and crossing it is
the bug. Here crossing it is the point: *"which bots are struggling this
week?"* cannot be answered one tenant at a time.

Two things keep that safe rather than reckless. It sits behind
`verify_owner_key` — an owner-role check, not merely a valid key, and a
customer key gets the same 403 as a garbage one so it can't be used to confirm
the route exists. And the snapshot carries **aggregates and question text,
never identities**: bots appear as ids and templates, because finding an
operational problem never requires knowing whose bot it is.

### `scripts/create_owner_key.py`

Mints an unlimited `nxo_` key. It is a local script and **deliberately not an
HTTP endpoint** — an endpoint that creates unlimited keys can be reached
remotely; a script on the database host cannot.

---

## `frontend/` — React 18 · Vite · TypeScript

`main.tsx → ErrorBoundary → BrowserRouter → App`. Routes: `/`, `/signin`,
`/signup`, and `/dashboard` + `/checkout/return` behind `RequireAuth`.

| File | Job | Worth knowing |
|---|---|---|
| `lib/api.ts` | Thin fetch wrapper | `credentials: "include"` on every call so the cookie rides along. Throws a typed `ApiError` carrying the server's real message, so screens show what went wrong instead of "Something failed". It skips `Content-Type` for `FormData` — that header carries the multipart boundary, and overriding it makes uploads unparseable server-side. |
| `lib/auth.tsx` | Auth context + route guard | One `/me` call on boot. A 401 there is the normal signed-out case, not an error worth surfacing. |
| `pages/Dashboard.tsx` (549 ln) | Template picker, upload, live tester, gaps, keys, billing | The largest file in the repo, and the screen the whole product funnels into. |
| `three/HeroScene.tsx` | The 3D hero | Code-split into its own chunk so three.js stays out of the initial payload. |

**Why Vite proxies `/api` in development.** It makes the session cookie
same-origin locally, exactly as it is behind a reverse proxy in production. That
removes the entire "works locally, breaks deployed" class of cookie bugs — and
it's the same reason the container serves the built SPA from the API origin
instead of a separate host.

---

## What stayed at the root, and why

| Path | What | Why it isn't inside a folder |
|---|---|---|
| `tests/` | 406 tests across 13 files | **Deliberately not split per-folder.** They exercise backend and bot together — auth, retrieval and billing land in a single request. |
| `benchmark.py` | Reproduces the performance numbers | Spans both halves. Points its database at a temp file so a run doesn't dump fake gaps into a real customer's gap list. |
| `Dockerfile` | Node stage builds `frontend/`; Python stage copies `backend/` + `bot/` | One image, one origin — no CORS, no cross-site cookies. The embedding model is baked in so a cold container doesn't stall 15s on its first request. |
| `chroma_data/`, `logs/` | Runtime data (~1.8 MB) | Gitignored. The container uses named volumes instead, so data never lives in the image. |
| `requirements.txt` | Dependencies | Two pins carry comments explaining *why* they're capped. |

---

## The rule to keep

If it decides what the bot **says**, it goes in `bot/`.
If it decides whether someone may **ask**, it goes in `backend/`.

That test resolves nearly every "where does this file go" question you'll hit
later.
