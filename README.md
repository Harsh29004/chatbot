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

**Requirements:** Python 3.10+, Node 20+, and MongoDB (an Atlas cluster or a
local server). All data, including FAQ vectors, is stored there. Ollama and the
Groq/Gemini keys are optional.

```bash
# 1. Configure
cp .env.example .env          # then set MONGO_URI, ADMIN_API_KEY, etc.

# 2. Backend: http://localhost:8000 (API docs at /docs)
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.server:app --reload --port 8000

# 3. Frontend: http://localhost:5173 (proxies /api to the backend)
cd frontend
npm install
npm run dev
```

The first request takes 10–15 seconds while the embedding model
(`all-MiniLM-L6-v2`) loads. After that, it stays in memory.

**Owner key.** An unlimited `nxo_` key can only be created with a local script.
There is deliberately no HTTP endpoint for it:

```bash
python backend/scripts/create_owner_key.py owner@example.com "Your Name"
```

**Hugging Face demo only:**

```bash
pip install gradio && python app.py
```

### Tests

```bash
pytest
```

The test suite needs no MongoDB server, Ollama, Groq, Gemini or embedding model. It uses
`mongomock`, deterministic fake embeddings, and a fake HTTP transport for LLM
providers.

### Deploying

For a full production setup on an Oracle Cloud Always Free instance (Docker,
Caddy with TLS, swap, optional Ollama profile), follow
[DEPLOY.md](DEPLOY.md). The short version:

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
| `WEB_ORIGINS` | `http://localhost:5173,…` | Origins allowed to call `/api` with the session cookie |
| `PUBLIC_BASE_URL` | `http://localhost:5173` | Where the frontend is served from |
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
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | empty | Optional Google sign-in |

> Never commit `.env`. It is already in `.gitignore`. If a key has been pasted
> anywhere public, rotate it.

---

## Project layout

```
bot/                 what the bot may say
  templates.py         the ten scope templates
  graph.py             the LangGraph answer pipeline
  grounding.py         optional LLM rewording and its fact check
  readers.py, ingest.py  any uploaded file → Q/A rows → vectors
  router.py            template picking, sheet upload, preview, POST /v1/ask
  widget/              the seven chat designs, install packages, activation
backend/             who is asking, whether they may, and whether they've paid
  server.py            app setup: routers, CORS, SPA hosting
  shared/              config, API keys & credits, guardrails, embeddings, llm.py
  billing/             plans, checkout, sessions, Google sign-in, referrals
  assistant/           the signed-in dashboard assistant
  support/             customer ↔ staff messaging
  admin/               cross-tenant admin panel (admin key)
frontend/            React 18 + Vite + TypeScript dashboard and landing page
tests/               backend and bot tests together
app.py               Hugging Face Spaces demo
```

The rule for where code goes: code that decides what the bot **says** goes in
`bot/`. Code that decides whether someone may **ask** goes in `backend/`.
