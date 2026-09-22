---
title: Nexora AI
emoji: 🤖
colorFrom: blue
colorTo: gray
sdk: gradio
sdk_version: 5.45.0
python_version: "3.10"
app_file: app.py
pinned: false
---

# Nexora AI

**AI chat bot · Think • Ask • Solve • Together**

FAQ chatbots that answer only from a customer's own sheet, sold as a service.

A customer signs up, picks one of ten templates, uploads their FAQ sheet, and
gets an API key. They go live by installing one of seven ready-made chat
widgets on their site, or by calling the API from their own backend. The bot
answers from the sheet and refuses anything the sheet and template don't cover.

**The bot cannot state a fact the customer didn't write down.**

## Live demo

This Hugging Face Space (`app.py`) runs a standalone demo of the answer
pipeline. It doesn't need a database or any other service.

1. **Pick a template.** Each one defines what the bot is allowed to talk about.
2. **Ask a question.** Try the pre-loaded FAQs, or ask something off-topic.
3. **See the result.** Every response shows its match mode (strong, near or decline) and confidence.

---

## How it works

```
question ──▶ guardrails ──▶ embed ──▶ search the customer's sheet
                                              │
             score ≥ strong ──▶ stored answer, word for word
             score ≥ near   ──▶ stored answer + "contact us" nudge (optionally reworded by an LLM, then fact-checked)
             below near     ──▶ polite refusal, logged as a gap for the owner
```

- **Word for word by default.** No model writes the answer, so the bot has nothing to make up.
- **Optional rewording.** An owner can let an LLM rephrase near matches. The
  model only sees rows that retrieval already found, and its output is checked
  against those rows. If the check fails, the stored answer is sent instead.
- **Isolated tenants.** Each bot's vectors are stored and searched separately.
- **One database.** Everything is stored in MongoDB: accounts, billing, FAQ vectors, support messages, rate limits. The app keeps nothing on local disk.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Templates

A template sets what a bot may talk about, how it refuses everything else, and
how close a match must be before it answers.

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

## Going live on a customer's site

The dashboard's **Put it on your website** section offers two paths.

### 1. Chat widget: seven ready-made designs

Pick a design and download its install package. Each design is independent of
the template, so any bot can use any design.

| Design | Look | Layout | Suits |
|---|---|---|---|
| Aurora | Indigo-to-violet gradient | Corner bubble | SaaS, e-commerce |
| Midnight | Dark with neon cyan | Corner bubble | Developer tools, gaming |
| Paper | Monochrome, square corners | Corner bubble | Fashion, portfolios |
| Sunset | Warm coral, very round | Corner bubble | Restaurants, retail |
| Sage | Calm green | Corner bubble | Clinics, education |
| Harbor | Corporate navy | Full-height side drawer | Fintech, B2B |
| Glass | Frosted, translucent | Corner bubble | Travel, agencies |

The package (`nexora-widget-<design>-1.0.0.zip`) contains:

- `nexora-widget.js`: the widget itself, with no dependencies. It renders in a Shadow DOM, so the host site's CSS can't break it.
- `index.html`: a demo page.
- `README.md`: install steps for plain HTML, React, WordPress and Shopify.
- `server-proxy/`: small proxies for Node (Express), Python (FastAPI) and PHP.

The package contains **no API key**. The widget is switched on by adding the key
at install time:

```html
<script src="/nexora-widget.js" data-api-key="nxk_..." defer></script>
```

When the page loads, the widget calls `GET /v1/widget/activate`, which costs no
credits. The status dot in the chat header turns green once the key is
accepted. If the key is wrong or revoked, or the sheet isn't uploaded yet, the
panel says so.

To skip the upload, customers can load the script from this server instead:
`<script src="https://your-host/widget/v1/aurora.js" data-api-key="..." defer></script>`.

> ⚠️ **A key in a script tag is public.** Anyone can read it from the page
> source and spend that account's credits. For production, customers should use
> `data-endpoint="/api/chat"` together with one of the included server proxies,
> so the key stays on their server.

### 2. API only

For customers who already have a chat UI, a mobile app or a WhatsApp bot. Call
the API from a server, never from a browser or an app bundle:

```bash
curl -X POST https://your-host/v1/ask \
  -H "X-Api-Key: $NEXORA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "What are your opening hours?", "session_id": "user-123"}'
```

```json
{
  "response": "We are open 9am–9pm, Monday to Saturday.",
  "mode": "strong",
  "matched_question": "What are your opening hours?",
  "confidence": 0.97
}
```

| `mode` | Meaning |
|---|---|
| `strong` | Close match: answered word for word from the sheet |
| `near` | Likely match: answered, with a nudge to contact the business |
| `decline` | Not covered: polite refusal, logged in the dashboard's gap list |

- **Errors:** `403` means the key is invalid or revoked, `402` means the account is out of credits, and `409` means no sheet has been uploaded yet.
- **Credits:** each question costs 1–5 credits depending on its length. Every
  response includes `X-Credits-Remaining`, `X-Credits-Daily-Limit`,
  `X-Credits-Reset-At` and `X-Credit-Cost`. Credits are pooled per account and
  reset at midnight IST.

The `/v1` routes allow calls from any website, so installed widgets work on any
domain. They authenticate only with the `X-Api-Key` header, never with cookies.
The dashboard's `/api` routes still accept only the origins listed in
`WEB_ORIGINS`.

---

## LLM fallback chain

The LLM is optional. It powers two features: grounded rewording (off unless a
bot owner turns it on) and the signed-in dashboard assistant. Instead of relying
on one model, the server tries a chain of models in order:

```
OLLAMA_MODELS (local)  ──all fail──▶  GROQ_MODELS  ──all fail──▶  GEMINI_MODELS × GEMINI_API_KEYS
```

The order is set by `LLM_PROVIDER_ORDER` (default `ollama,groq,gemini`). Remove
a provider from the list to switch it off.

- **When a model is skipped:** if it is rate limited (429), busy (503), missing (404), erroring (5xx) or times out, it is skipped for `LLM_COOLDOWN_SECONDS` and the next model is tried. If Groq sends a `Retry-After` header, that wait is used instead.
- **Gemini key rotation:** `GEMINI_API_KEYS` takes several keys, comma-separated. Each Gemini model is tried on every key before moving to the next model, because free-tier quotas are per project and per model. A rate-limited key is skipped for that model only. A key that Google rejects (invalid, revoked or disabled) is skipped for every model for `LLM_BAD_KEY_COOLDOWN_SECONDS`. Logs show `#key2`, never the key itself.
- **When Ollama itself is down:** if the Ollama server can't be reached, all local models are skipped together, so a request doesn't wait for each one to time out.
- **Streaming:** a streamed reply can switch models only before its first token. It never joins two models' answers together.
- **When every model fails:** nothing crashes. The bot sends the stored answer word for word, and the assistant says the model is unavailable.

> **Privacy:** the Ollama models run on your own machine. When Groq or Gemini
> answers, the prompt, including rows from the customer's sheet, is sent to a
> hosted API. Leave `GROQ_API_KEY` and `GEMINI_API_KEYS` empty to keep
> everything local.

Pull the local models before you enable the chain:

```bash
ollama pull qwen2.5:3b && ollama pull llama3.2:3b && ollama pull gemma2:2b
```

---

## Running locally

**Requirements:** Node 20+, Python 3.10+, and MongoDB (an Atlas cluster or a
local server). All data, including FAQ vectors, is stored there. Ollama and the
Groq/Gemini keys are optional.

Three terminals.

```bash
# 0. Configure — one .env at the repo root, read by all three services
cp .env.example .env          # then set MONGO_URI, ADMIN_API_KEY, etc.

# 1. Bot service: http://localhost:8001 (internal — loopback only)
cd bot-service
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8001

# 2. API: http://localhost:8000
cd node-backend
npm install
npm run dev

# 3. Web: http://localhost:3000 (proxies /api and /v1 to the API)
cd next-frontend
npm install
cp .env.example .env.local    # add your Firebase values
npm run dev
```

The bot service loads the embedding model (`all-MiniLM-L6-v2`) at startup
rather than on first request, so it takes 10–15 seconds to become healthy and
is warm from then on. The other two start immediately.

**Shortcut.** To run the API and the bot service together against a throwaway
in-memory MongoDB — no Atlas, no local mongod, nothing to clean up afterwards:

```bash
cd node-backend && npm run build && node run-stack.mjs
```

**Owner key.** An unlimited `nxo_` key can only be created with a local script.
There is deliberately no HTTP endpoint for it:

```bash
cd node-backend && npm run create-owner-key -- owner@example.com "Your Name"
```

**Hugging Face demo only** — one file, no database, no other service:

```bash
pip install -r requirements.txt && python app.py
```

### Tests

```bash
cd node-backend
npm run build
node smoke-test.mjs      # 66 checks — boots the whole stack
node route-parity.mjs    # every endpoint is still mounted
```

`smoke-test.mjs` starts a throwaway in-memory MongoDB, starts the Python bot
service against it, starts the API against both, and walks the paths a customer
actually takes: sign up, sign in, dashboard, template, sheet upload, API key,
`/v1/ask`, billing, admin, logout. One command, nothing to install, nothing left
behind. If Python or its dependencies are missing, the retrieval steps are
reported as skipped rather than failed.

For the web app:

```bash
cd node-backend  && node run-stack.mjs          # terminal 1
cd next-frontend && npm run build && npm start  # terminal 2
cd next-frontend && node e2e-check.mjs          # terminal 3 — 29 checks
```

Those assert the server-rendered HTML: that prices and templates are *in* it,
that a signed-out visitor to `/dashboard` is redirected before any markup is
sent, and that the admin key never reaches the server.

### Deploying

For a full production setup on an Oracle Cloud Always Free instance (Docker,
Caddy with TLS, swap, optional Ollama profile), follow
[DEPLOY.md](DEPLOY.md). The short version is unchanged — compose builds all
three services:

```bash
docker compose --profile tls up -d --build
```

---

## Configuration

All settings are environment variables. [.env.example](.env.example) documents
every one of them. These are the main ones:

| Variable | Default | What it does |
|---|---|---|
| `MONGO_URI` / `MONGO_DB_NAME` | local / `nexora` | Database |
| `ADMIN_API_KEY` | — | Opens `/admin`, the support inbox and key management. **Change it.** |
| `WEB_ORIGINS` | `http://localhost:3000,…` | Origins allowed to call `/api` with the session cookie |
| `PUBLIC_BASE_URL` | `http://localhost:3000` | Where the frontend is served from |
| `BOT_SERVICE_URL` | `http://127.0.0.1:8001` | Where the Node backend finds the Python bot service |
| `INTERNAL_API_KEY` | empty | Shared secret between those two. Not a user credential — set it in production. |
| `PUBLIC_API_ORIGIN` | request's own URL | API address written into widget packages. Set it when the server is behind a proxy. |
| `DAILY_CREDIT_LIMIT` | `250` | Daily credits for an account with no plan |
| `STRONG_MATCH_THRESHOLD` / `NEAR_MATCH_THRESHOLD` | `0.85` / `0.60` | Default match thresholds. Each template overrides them. |
| `LLM_ENABLED` | `false` | Offers owners the grounded-rewording toggle |
| `ASSISTANT_ENABLED` | `false` | Turns on the dashboard assistant |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Primary local model, always first in the chain |
| `OLLAMA_MODELS` | `qwen2.5:7b,llama3.1:8b,gemma2:9b` | Local fallback models, in order |
| `GROQ_API_KEY` | empty | Enables Groq as the last fallback. Leave empty to stay local. |
| `GROQ_MODELS` | `qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b` | Groq models, in order |
| `GEMINI_API_KEYS` | empty | Comma-separated Gemini keys, rotated per model. Leave empty to switch Gemini off. |
| `GEMINI_MODELS` | `gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.8-flash` | Gemini models, in order |
| `LLM_PROVIDER_ORDER` | `ollama,groq,gemini` | Which providers are tried, and in what order |
| `LLM_COOLDOWN_SECONDS` | `60` | How long a failing model is skipped |
| `LLM_BAD_KEY_COOLDOWN_SECONDS` | `3600` | How long a key the provider rejects is skipped |
| `FIREBASE_PROJECT_ID` / `FIREBASE_CLIENT_EMAIL` / `FIREBASE_PRIVATE_KEY` | empty | Firebase service account. Verifies ID tokens. **Private key — never in a `NEXT_PUBLIC_` variable.** |
| `FIREBASE_REQUIRE_VERIFIED_EMAIL` | `true` | Refuse sign-ins whose mailbox Firebase has not confirmed. Leave on. |
| `NEXT_PUBLIC_FIREBASE_*` | empty | The web app config. Public, inlined into the bundle **at build time**. |
| `TELEMETRY_ENABLED` | `true` | Accept client crash reports at `/api/telemetry/error` |
| `TELEMETRY_RETENTION_DAYS` | `30` | How long crash reports are kept before MongoDB expires them |

> Never commit `.env`. It is already in `.gitignore`. If a key has been pasted
> anywhere public, rotate it.

---

## Firebase

Firebase runs the **sign-in**. It does not run the **session**.

The browser signs in with Firebase (Google popup, or email and password),
gets an ID token, and posts it once to `/api/auth/firebase`. The server
verifies that token against this project's keys and issues the same httpOnly
`nexora_session` cookie a password login has always produced. Everything
downstream — billing, credits, referrals, the widget, the admin panel — keeps
resolving identity exactly one way.

That is deliberate. Trusting the ID token on every request would mean two
ways to be authenticated, and one of them lives in JavaScript memory where an
injected script can read it. The cookie is httpOnly and revocable
server-side; a Firebase ID token is neither.

Accounts that predate Firebase keep working. A password account signing in
with Google on the same (verified) address is *linked*, not duplicated, and
keeps its password — see `_resolve_firebase_customer` in
[backend/billing/router.py](backend/billing/router.py) for the four cases.

### Console setup

The code is complete, but four things have to be switched on by hand. None of
them can be done from here.

1. **Register a web app** — Firebase Console → Project Settings → General →
   Your apps → Web. Copy the config into the `NEXT_PUBLIC_FIREBASE_*` variables
   in `next-frontend/.env.local`. Without `NEXT_PUBLIC_FIREBASE_API_KEY` and
   `NEXT_PUBLIC_FIREBASE_APP_ID` the app
   silently falls back to password-only sign-in.

2. **Enable the providers** — Authentication → Sign-in method → enable
   **Google** and **Email/Password**. Neither is on in a new project, and a
   button for a disabled provider fails with
   `auth/operation-not-allowed`.

3. **Authorise your domains** — Authentication → Settings → Authorized
   domains. `localhost` is there by default; add the production domain or
   sign-in fails with `auth/unauthorized-domain`.

4. **Register the custom dimensions** — GA4 Admin → Custom definitions. GA4
   *collects* every custom parameter immediately but will not show one in a
   report until it is registered. Worth doing first: `method`, `plan_id`,
   `template_id`, `error_kind`, `feature`, `release`.

### What is *not* here: Crashlytics

Firebase Crashlytics has no Web SDK. It ships for Android, iOS, Flutter and
Unity only, and there is no `firebase/crashlytics` import for a browser app.

The equivalent is built from the two things that do exist:

- **A GA4 `exception` event** — Google's recommended crash event, which feeds
  its own report in the Firebase console. Gives the rate, the trend and the
  affected release. Does *not* give a stack trace; GA4 truncates parameters
  at 100 characters.
- **`POST /api/telemetry/error`** — carries the full stack, the component
  stack, the route and the release into MongoDB, grouped by fingerprint and
  TTL-expired. This is the half you read when something breaks.

Both fire from [frontend/src/lib/errorReporting.ts](frontend/src/lib/errorReporting.ts),
which hooks `window.onerror`, `unhandledrejection` and the React error
boundary. Read the grouped view at `GET /api/admin/crashes`, and the raw feed
with stacks at `GET /api/admin/crashes/recent`.

Identical crashes are deduplicated within 10 seconds and capped at 25 reports
per page load. A component that throws on every render remounts and throws
again hundreds of times a second, and without those guards the first such bug
to reach production would flood its own telemetry endpoint.

### Analytics

Every event name lives in one typed catalogue,
[frontend/src/lib/analytics.ts](frontend/src/lib/analytics.ts). GA4 keeps
whatever name it is first sent and offers no rename, so a typo becomes a
permanent second event that quietly splits a funnel in two — naming them in
one place makes that a build error instead.

GA4's own recommended names (`login`, `sign_up`, `purchase`,
`begin_checkout`, `view_item_list`, `share`, `search`, `exception`,
`page_view`) are used wherever one fits, so the standard reports light up
rather than needing a custom exploration per question.

**No PII reaches Analytics.** No email addresses, names, API keys, message
bodies or search text — Google's terms prohibit it and an account can be
terminated over it. `sanitise()` redacts anything shaped like an address as a
backstop, but the rule is upstream: send counts, ids, durations and
categories, never the thing the user typed. `identify()` sets the *customer
id*, never the email.

Page views are tracked manually. GA4's automatic `page_view` fires once on
the initial document load and never again, because React Router changes the
URL without a navigation — so every route after the landing page would
otherwise be invisible.

### Performance

`getPerformance()` starts the automatic traces (page load, first paint, every
fetch). `measure()` in the analytics module adds custom traces around the
slow paths that matter — sheet indexing, checkout, sign-in — and records the
duration as an Analytics event too, because Performance gives you the
distribution across real users and Analytics lets the duration sit beside the
rest of the funnel. Neither alone tells you "checkout is slow for the people
who then abandon it".

### Building for production

`NEXT_PUBLIC_*` variables are inlined **at build time**, not read at run time.
The web Dockerfile takes them as build args and `docker-compose.yml` passes
them through; set them in the `.env` next to the compose file. Miss this and
the image ships with sign-in silently disabled and nothing in any log to say
why.

Everything else — `MONGO_URI`, the admin key, the Firebase *service account* —
is read at run time by the Node and Python services, so those can be changed
with a restart rather than a rebuild.

---

## Project layout

```
bot-service/         Python — what the bot may say
  main.py              the internal API the Node backend calls
  bot/
    templates.py         the ten scope templates
    graph.py             the LangGraph answer pipeline
    grounding.py         optional LLM rewording and its fact check
    readers.py, ingest.py  any uploaded file → Q/A rows → vectors
    widget/              the seven chat designs and install packages
  backend/shared/      embeddings, vector store, guardrails, input policy, llm

node-backend/        TypeScript — who is asking, and whether they've paid
  src/server.ts        app setup: routers, CORS, startup sweep
  src/shared/          config, API keys & credits, auth, rate limits, llm
  src/billing/         plans, checkout, sessions, Firebase sign-in, referrals
  src/assistant/       the signed-in dashboard assistant
  src/support/         customer ↔ staff messaging
  src/admin/           cross-tenant admin panel (admin key)
  src/bot/             client.ts + router.ts — authenticate, charge, forward

next-frontend/       Next.js 15 — the dashboard and landing page
  app/                 routes; each one fetches on the server
  screens/             the page components
  lib/                 api-server.ts (RSC), api-client.ts (browser)

_old/                the original FastAPI + Vite code, for reference
app.py               Hugging Face Spaces demo (standalone)
```

The rule for where code goes is unchanged, and is now a process boundary rather
than a folder convention: code that decides what the bot **says** lives in
`bot-service/`. Code that decides whether someone may **ask** lives in
`node-backend/`. The two share a database and write disjoint halves of it.
