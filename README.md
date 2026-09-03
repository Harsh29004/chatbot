# Nexora AI

Retrieval-grounded FAQ chatbots, sold as a service.

A customer signs up, picks one of ten templates, uploads their FAQ sheet, and
gets an API key. Their bot answers from that sheet — **verbatim, with zero LLM
generation in the response path** — and refuses anything the sheet and template
don't cover.

The bot cannot invent an answer. Not "is instructed not to" — *cannot*, because
nothing in the pipeline writes prose. It either finds a close enough match in
the customer's own sheet and returns it word for word, or it declines.

## How it works

1. **Pick a template.** It decides what the bot may talk about and the exact
   words it refuses everything else with.
2. **Upload your sheet.** `.xlsx` or `.csv`. It supplies every fact the bot
   knows.
3. **Create an API key.** Up to 10 per account, all sharing one credit pool.
4. **Call `POST /v1/ask`.** One endpoint, one header.

## Architecture

```
  POST /v1/ask  (X-Api-Key)
         |
         v
  +--------------+     +---------------+     +----------------+
  |  Guardrails  |---->| Embed query   |---->| Retrieve top-K |
  |  (injection  |     | (sentence-    |     | (this bot's    |
  |   + action)  |     |  transformers)|     |  collection)   |
  +--------------+     +---------------+     +-------+--------+
        |                                            |
        | action intent                       +------+------+
        | (never *do*, only explain)          v             |
        v                              score >= strong?     |
  +-----------+     +-----------+       yes  |              |
  | DECLINE   |     | STRONG    |<-----------+              |
  | (log +    |     | (verbatim |      score >= near?       |
  |  template |     |  answer)  |            |              |
  |  message) |     +-----------+       yes  |              |
  +-----------+     +-----------+            |              |
        ^           | NEAR      |<-----------+              |
        |           | (answer + |       below near?         |
        +-----------|  handoff) |<--- DECLINE --------------+
                    +-----------+
```

The two thresholds come from the **template**, not from a global setting — the
healthcare and finance templates sit higher than the rest, so they stay quiet
sooner. Every declined or hedged question is logged, which is how a customer
finds out what their sheet is missing.

## Quick start

**Prerequisites:** Python 3.11+, Node 18+

```bash
pip install -r requirements.txt

cp .env.example .env
# Edit .env — set ADMIN_API_KEY

# API (terminal one)
uvicorn server:app --reload --port 8000

# Web app (terminal two)
cd web && npm install && npm run dev   # http://localhost:5173
```

Sign up at http://localhost:5173, pick a template, download the starter sheet,
upload it back, and create a key. There is no seed data to generate — every
bot's content is its owner's sheet.

Vite proxies `/api` to port 8000, so the session cookie is same-origin in
development exactly as it is behind a reverse proxy in production.

### Docker

```bash
export ADMIN_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
docker-compose up --build
```

The image builds the SPA in a Node stage and serves it from the API, so the
whole product runs on one origin at http://localhost:8000 — no CORS, no
cross-site cookie handling. Data lives on named volumes, and the embedding
model is baked into the image so a cold container doesn't stall its first
request downloading it.

## Project layout

```
server.py                  FastAPI app: mounts routers, health, CORS
benchmark.py               Reproduces the performance numbers below

apps/
  bot_engine/
    templates.py           The ten templates — scope, wording, thresholds
    graph.py               The answer pipeline, driven by BotConfig
    ingest.py              Sheet upload -> vectors
    store.py               One bot per account, its own collection
  bots_router.py           Templates, sheet upload, preview, POST /v1/ask
  billing/
    plans.py               Prices; discount is derived, never hand-typed
    db.py                  Customers, sessions, subscriptions, invoices
    security.py            Password hashing, session tokens
    payments.py            Hosted-checkout adapters (Stripe, Razorpay)
    router.py              Auth, billing, dashboard endpoints
  api_keys_router.py       Admin key management

shared/
  api_keys.py              Accounts, keys, pooled daily credits
  auth.py                  X-Api-Key and X-Admin-Key dependencies
  guardrails.py            Injection + action-intent detection
  vector_store.py          ChromaDB helpers
  embeddings.py            sentence-transformers wrapper
  logging_store.py         Unmatched-question log

web/src/
  pages/                   Landing, Auth, Dashboard, CheckoutReturn
  components/              Template picker, sheet uploader, bot tester
  three/HeroScene.tsx      The 3D hero
  lib/                     API client, auth context
```

## Templates

- **The template** decides what the bot is *allowed* to talk about, and the
  exact words it uses to refuse everything else.
- **The sheet** supplies the facts. Nothing else does.

Anything outside both gets the template's decline message.

| # | Template | Covers | Strictness |
|---|----------|--------|------------|
| 1 | 🛍️ E-commerce Support | Orders, delivery, returns, refunds | open |
| 2 | 🍽️ Restaurant & Food Delivery | Menu, timings, delivery areas, bookings | open |
| 3 | 🩺 Clinic & Healthcare | Timings, appointments, fees, insurance | **strict** |
| 4 | 🏢 Real Estate & Property | Listings, site visits, payment plans | balanced |
| 5 | 🎓 Coaching & EdTech | Courses, fees, batches, admissions | open |
| 6 | 💻 SaaS & App Support | Features, billing, integrations | open |
| 7 | ✈️ Travel & Hotel Booking | Bookings, check-in, cancellation | open |
| 8 | 🏦 Banking & Fintech | Charges, KYC, limits, statements | **strict** |
| 9 | 📦 Logistics & Courier | Tracking, delivery, claims | open |
| 10 | 💇 Salon, Spa & Local Services | Services, prices, timings | open |

Thresholds are not uniform. A wrong answer about a haircut costs an apology; a
wrong answer about a drug interaction or a loan penalty costs considerably
more, so healthcare and finance demand a closer match before they speak (0.90
vs 0.84) and fall silent sooner.

Those two also refuse vertical-specific requests outright: the clinic bot
explains how to book an appointment but will never appear to book one, and the
fintech bot won't be drawn into "which fund should I pick" even if the sheet
has a matching row.

Switching template keeps the indexed sheet — scope and facts are independent.

## The sheet

Uploaded as `.xlsx` or `.csv`. Column names are matched case- and
space-insensitively.

| Column | Required | Description |
|--------|----------|-------------|
| `Question` | **yes** | The canonical question |
| `Answer` | **yes** | The approved answer, returned verbatim |
| `Alt_Phrasings` | no | Semicolon-separated alternate wordings |
| `Category` | no | Category label, surfaced in the dashboard |

Every template ships a pre-filled starter CSV —
`GET /api/templates/{id}/starter-sheet`, or the download button in the
dashboard.

Each phrasing is indexed as **its own vector** rather than concatenated into
one. Concatenating drags the embedding away from all of its phrasings at once:
asking a sheet question word-for-word used to score ~0.76 and get served as a
hedged "near" match. One vector per phrasing takes that to ~1.00.

Blank and duplicate rows are reported rather than fatal, so one bad row doesn't
cost someone their upload. Re-uploading **replaces** the index — the sheet is
the source of truth, so a re-upload has to be able to remove an answer.

One thing worth knowing when writing `Alt_Phrasings`: the action guardrail runs
*before* retrieval, so listing a phrasing like "cancel my order" won't make the
bot answer it — that phrasing asks the bot to *cancel*, not to explain
cancelling, and is refused by design. Write alternates as questions ("order
cancellation", "cancellation policy").

## API

### The bot API — what customers integrate

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/v1/ask` | `X-Api-Key` | Ask this account's bot a question |
| `GET` | `/api/keys/usage` | `X-Api-Key` | Credit usage; costs nothing |
| `GET` | `/api/keys/pricing` | — | Credit cost tiers |
| `GET` | `/health` | — | Health check |

```bash
curl -X POST http://localhost:8000/v1/ask \
  -H "X-Api-Key: nxk_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I cancel an order?", "session_id": "u_1182"}'

{
  "response": "Open My Orders, select the order and choose Cancel Order...",
  "mode": "strong",
  "matched_question": "How do I cancel an order?",
  "confidence": 0.98
}
```

`mode` is `strong` (verbatim answer), `near` (answer plus a handoff nudge), or
`decline` (out of scope). Credit balance comes back in the
`X-Credits-Remaining`, `X-Credits-Daily-Limit`, `X-Credits-Reset-At` and
`X-Credit-Cost` response headers. Insufficient credits returns `402` with the
reset time, so a client can fall back to a support form instead of erroring.

### Templates and bot setup (session cookie)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/templates` | — | The ten templates |
| `GET` | `/api/templates/{id}/starter-sheet` | — | Pre-filled CSV |
| `GET` | `/api/bot` | cookie | This account's bot |
| `PUT` | `/api/bot` | cookie | Pick or switch template |
| `POST` | `/api/bot/sheet` | cookie | Upload the FAQ sheet |
| `POST` | `/api/bot/preview` | cookie | Test a question — costs no credits |

### Accounts and billing (session cookie)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/auth/signup` | — | Create an account, start the trial |
| `POST` | `/api/auth/login` | — | Exchange credentials for a session cookie |
| `POST` | `/api/auth/logout` | cookie | Revoke the session |
| `GET` | `/api/auth/me` | cookie | Current account |
| `GET` | `/api/billing/pricing` | — | Plans and derived discount |
| `GET` | `/api/billing/subscription` | cookie | Current plan and entitlement |
| `POST` | `/api/billing/checkout` | cookie | Start a hosted checkout |
| `POST` | `/api/billing/cancel` | cookie | Cancel; access runs to period end |
| `GET` | `/api/billing/invoices` | cookie | Invoice history |
| `GET` | `/api/dashboard` | cookie | Everything the dashboard needs |
| `POST` | `/api/dashboard/keys` | cookie | Issue a key (needs an active plan) |
| `DELETE` | `/api/dashboard/keys/{id}` | cookie | Revoke your own key |

### Admin (`X-Admin-Key`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/keys/generate` | Issue a key for an account |
| `GET` | `/api/keys` | List all API keys |
| `DELETE` | `/api/keys/{id}` | Revoke any API key |

## Plans and credits

| Plan | Price | Daily credits |
|------|-------|---------------|
| Trial | Free for 14 days | 250 |
| Monthly | $15/month | 5,000 |
| Yearly | $140/year (**$11.67/mo — save $40, 22% off**) | 5,000 |

Prices live in one place, `apps/billing/plans.py`. The discount and effective
monthly rate are *derived* from them, so changing a price updates the pricing
page, the dashboard, and the API together. The discount is rounded **down**, so
the advertised number is never better than what the customer actually gets.

Credits are pooled **per account**, not per key. An account can hold up to 10
keys and they all draw on the same daily allowance — extra keys separate
environments, they don't buy extra capacity. Credits reset at midnight IST.

When a plan lapses the account drops back to the free allowance — deliberately
not to zero, and its keys are **not** revoked. Someone whose card expired
should find their bot throttled, not silently broken in production with an
integration to rebuild when they return. `apps/billing/entitlements.py` owns
every transition between "is paying" and "may use", so the two can't drift
apart; a sweep runs on startup and every `ENTITLEMENT_SWEEP_SECONDS`.

| Message length | Credit cost |
|---------------|-------------|
| 1–200 chars | 1 credit |
| 201–500 chars | 2 credits |
| 501–1000 chars | 3 credits |
| 1001–2000 chars | 5 credits |

Revoking a key is scoped to its owner, so one customer cannot revoke another's
key by guessing an id.

### Owner keys (unlimited, internal only)

An **owner key** (prefix `nxo_`, versus `nxk_` for customers) skips credit
checks entirely. It exists only for the people running this, and is minted
locally — there is deliberately no HTTP endpoint that can create one:

```bash
python scripts/create_owner_key.py owner@example.com "Your Name"
```

The raw key prints once. Use it exactly like a normal `X-Api-Key`.

## Payments

**No card details ever touch this server.** Every provider is a *hosted*
checkout: we create a session, redirect to the provider's page, and learn the
result from a signed webhook. Do not add a card form to this project.

`BILLING_PROVIDER=manual` is the development default — it takes no payment and
leaves the plan `pending`. Activating without payment additionally requires
`BILLING_ALLOW_MANUAL=true`, so it cannot become a free-access hole in
production. `apps/billing/payments.py` has step-by-step wiring notes for Stripe
and Razorpay; both currently raise rather than silently granting access.

**Before going live:** set `BILLING_COOKIE_SECURE=true`, point `WEB_ORIGINS` at
your real domain, and verify webhook signatures in `POST /api/billing/webhook`
(it returns 501 until you do).

## Configuration

All settings in `.env` / `shared/config.py`. `.env` is loaded automatically;
real environment variables take precedence over the file.

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_API_KEY` | `admin-change-me` | Admin key for management endpoints |
| `STRONG_MATCH_THRESHOLD` | `0.85` | Default strong threshold (templates override) |
| `NEAR_MATCH_THRESHOLD` | `0.60` | Default near threshold (templates override) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model (CPU) |
| `TOP_K` | `3` | Number of retrieval results |
| `DAILY_CREDIT_LIMIT` | `250` | Default credits per **account** per day |
| `WEB_ORIGINS` | `localhost:5173` | Origins allowed to send the session cookie |
| `PUBLIC_BASE_URL` | `localhost:5173` | Used to build checkout return URLs |
| `BILLING_PROVIDER` | `manual` | `manual` (dev), `stripe`, or `razorpay` |
| `BILLING_ALLOW_MANUAL` | `false` | Allows activation with no payment — dev only |
| `BILLING_COOKIE_SECURE` | `false` | Send the session cookie over HTTPS only |
| `PAID_DAILY_CREDITS` | `5000` | Credits granted while a paid plan is active |
| `TRIAL_DAYS` / `TRIAL_DAILY_CREDITS` | `14` / `250` | Trial length and allowance |
| `MAX_KEYS_PER_ACCOUNT` | `10` | Cap on active keys per account |
| `ENTITLEMENT_SWEEP_SECONDS` | `900` | How often lapsed plans are withdrawn |

## Testing

```bash
pytest tests/ -v
```

227 tests covering the template catalogue, sheet ingestion, per-template scope
enforcement, tenant isolation, injection payloads, action-intent blocking,
pricing maths, entitlement grant/withdrawal, credit pooling, and
key-ownership scoping.

## Performance

Measured on CPU with `python benchmark.py` (10-entry sheet, 30 indexed
phrasings, e-commerce template):

| Metric | Value |
|--------|-------|
| Answered query, end to end | **~18ms** |
| Throughput (single-threaded) | **~55 queries/sec** |
| Guardrails | ~0ms (regex) |
| Embedding | ~6ms (CPU, steady state) |
| ChromaDB retrieval | ~1ms |

Declines and near-matches run slower (~33–45ms) because they write a row to the
unmatched-question log — that is the customer's gap list, and it is worth the
milliseconds.

The embedding model loads once at process start and takes **10–15s**; the first
request after a cold boot pays that. Keep the process warm.

## Security

- **No prompt injection risk** — user input is only ever an embedding query. It is never placed in an LLM prompt, because there is no LLM in the response path
- **Injection detection** — regex pattern detector flags suspicious input for review (logging only, so the detector isn't itself an attack surface)
- **Action blocking** — requests to "do something" (refund, cancel, change) are declined regardless of how well they match the sheet. Templates add their own vertical-specific patterns
- **Tenant isolation** — every bot gets its own ChromaDB collection, named from its id. One customer's bot cannot retrieve another's answers
- **API key hashing** — keys are stored as SHA-256 hashes and shown once at generation. Revocation is scoped by owner
- **Passwords** — PBKDF2-HMAC-SHA256, 600k iterations, salted per user
- **Sessions** — opaque tokens stored as hashes in an httpOnly cookie; revocable server-side
- **Login throttling** — 8 failed attempts per email+IP per 15 minutes
- **No card data** — hosted checkout only
- **Auth separation** — customer keys, admin keys, and dashboard sessions use different headers and routes
- **Owner-key auditing** — owner keys skip billing but are still written to the request log; an unlimited key is the one most worth a trail
- **Session hygiene** — expired and revoked sessions are purged on a sweep rather than accumulating as hashed credentials forever
