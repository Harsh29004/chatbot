# Nexora AI

Retrieval-grounded FAQ chatbots, sold as a service.

A customer signs up, picks one of ten templates, uploads their FAQ sheet, and
gets an API key. Their bot answers from that sheet and refuses anything the
sheet and template don't cover.

**The bot cannot state a fact the customer didn't write down.** By default it
cannot write prose at all: it finds a close enough match in the customer's own
sheet and returns it word for word, or it declines. Nothing in that path
generates text.

An owner may opt in, per bot, to **grounded rewording** — a local model that
rephrases close matches so they read more naturally. It is off for every bot
until someone turns it on, it only ever sees rows retrieval already found, and
its output is checked back against those rows before anyone sees it. Facts stay
the customer's; only the sentences change. See [Grounded rewording](#grounded-rewording).

## How it works

1. **Pick a template.** It decides what the bot may talk about and the exact
   words it refuses everything else with.
2. **Upload your FAQ.** Whatever you already have — spreadsheet, CSV, JSON,
   JSONL, YAML, XML, HTML, Markdown, plain text, Word, PDF, or a zip of them.
   It supplies every fact the bot knows.
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
                    +-----+-----+
                          |
                          | only if the owner opted in
                          v
                    +-----------+     verified against
                    | GROUNDED  |     the same passages;
                    | (reworded |     fails -> NEAR
                    |  by Ollama)|
                    +-----------+
```

The two thresholds come from the **template**, not from a global setting — the
healthcare and finance templates sit higher than the rest, so they stay quiet
sooner. Every declined or hedged question is logged, which is how a customer
finds out what their sheet is missing.

Note where the model sits: on the **near band only**, behind an opt-in, and
after retrieval has already found something. A strong match is served verbatim
— an exact hit is the best answer available and a model call could only make it
worse. Below the near threshold nothing is generated at all, because there is
nothing to ground on.

## Quick start

**Prerequisites:** Python 3.11+, Node 18+

```bash
pip install -r requirements.txt

cp .env.example .env
# Edit .env — set ADMIN_API_KEY

# API (terminal one)
uvicorn backend.server:app --reload --port 8000

# Web app (terminal two)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Sign up at http://localhost:5173, pick a template, download the starter sheet,
upload it back, and create a key. There is no seed data to generate — every
bot's content is its owner's sheet.

Vite proxies `/api` to port 8000, so the session cookie is same-origin in
development exactly as it is behind a reverse proxy in production.

### Docker

For a full deployment on a free Oracle Cloud ARM box — the one machine that
fits the app and a 7B model together — see [DEPLOY.md](DEPLOY.md).

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

Three top-level folders — **frontend**, **backend**, **bot** — sharing one
Python import root at the project root, so `backend.*` and `bot.*` resolve
whichever entry point you run.

```
backend/                   The API server, accounts, billing, infrastructure
  server.py                FastAPI app: mounts routers, health, CORS, SPA
  api_keys_router.py       Admin key management
  billing/
    plans.py               Prices; discount is derived, never hand-typed
    db.py                  Customers, sessions, subscriptions, invoices
    referrals.py           Who referred whom, and what it paid out
    security.py            Password hashing, session tokens
    payments.py            Hosted-checkout adapters (Stripe, Razorpay)
    router.py              Auth, billing, dashboard endpoints
  ops.py                   Cross-tenant operational snapshot (owner only)
  owner_router.py          The owner ops assistant
  admin/
    queries.py             Cross-tenant reads for the admin panel
    router.py              /api/admin/*, behind the admin key
  assistant/
    store.py               Chat threads and messages, scoped per customer
    context.py             What the assistant knows about *this* customer
    router.py              Threads + the streaming chat endpoint
  support/
    store.py               Support conversations, one per customer
    router.py              Customer side (cookie) + staff inbox (admin key)
  shared/
    config.py              Settings, read from .env
    mongo.py               The MongoDB client, id conventions, index registry
    llm.py                 Ollama client — optional, never in the critical path
    input_policy.py        What may reach the model, and what never may
    api_keys.py            Accounts, keys, pooled daily credits
    auth.py                X-Api-Key and X-Admin-Key dependencies
    guardrails.py          Injection + action-intent detection
    vector_store.py        ChromaDB helpers
    embeddings.py          sentence-transformers wrapper
    logging_store.py       Unmatched-question log
  scripts/
    create_owner_key.py    Mints an unlimited owner key, locally only

bot/                       The answer engine — everything a bot knows and says
  templates.py             The ten templates — scope, wording, thresholds
  catalogue.py             Those templates plus the admin's live edits
  readers.py               Any uploaded file -> a Question/Answer table
  graph.py                 The answer pipeline, driven by BotConfig
  grounding.py             Grounded rewording + the check that verifies it
  ingest.py                Sheet upload -> vectors
  store.py                 One bot per account, its own collection
  router.py                Templates, sheet upload, preview, POST /v1/ask

frontend/                  The React SPA (Vite)
  src/pages/               Landing, Auth, Dashboard, CheckoutReturn,
                           Assistant, Support, Admin, AdminSupport
  src/components/          Template picker, sheet uploader, bot tester,
                           referral card
  src/components/admin/    The admin panel's eight tabs
  src/three/HeroScene.tsx  The 3D hero
  src/lib/                 API client, auth context

tests/                     One suite; it spans backend and bot
benchmark.py               Reproduces the performance numbers below
```

`bot/` imports from `backend/shared/` (embeddings, vector store, guardrails,
the gap log) and never the other way round — the only backend module that
reaches into the bot is `backend/server.py`, which mounts `bot/router.py`.

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

Upload whatever the answers already live in — the importer works out the rest.

| Kind | Extensions | How it is read |
|------|------------|----------------|
| Spreadsheets | `.xlsx` `.xlsm` `.xls` `.ods` | Every tab, not just the first; tabs without Question/Answer columns are skipped |
| Delimited | `.csv` `.tsv` `.psv` `.txt` | Delimiter sniffed, so semicolon and tab exports work unchanged |
| Structured | `.json` `.jsonl` `.ndjson` `.yaml` `.xml` | Lists of objects, `{"faqs": […]}` wrappers, `{question: answer}` maps, and chat transcripts (`messages` / `conversations`) |
| Documents | `.md` `.html` `.txt` `.docx` `.pdf` | `Q:`/`A:` blocks, Markdown headings and tables, or "the line ending in ? is the question" |
| Other | `.parquet` `.zip` | A zip is unpacked and every readable member imported |

The name is only a hint: a file called `export` or `faq.dat` is identified
from its bytes instead. `.pdf` needs `pypdf`, `.parquet` needs `pyarrow`, and
`.xls` needs `xlrd` — without them the upload fails with a message saying so.

Column names are matched case- and space-insensitively, through an alias
table: `q`/`a`, `prompt`/`completion`, `user`/`assistant`, `tags`, `synonyms`
and friends all land on the four canonical columns. A two-column file with no
header at all is read as question-then-answer.

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

## Grounded rewording

Off for every bot until its owner turns it on, from the dashboard or
`PUT /api/bot/answering`. Requires `LLM_ENABLED=true` and a reachable Ollama;
the endpoint **refuses to enable** otherwise, because a setting that silently
does nothing is worse than one that says no.

### What it changes, and what it doesn't

|  | Rewording off (default) | Rewording on |
|---|---|---|
| Where facts come from | the sheet | the sheet |
| Who writes the sentence | the customer | a local model |
| Strong match | verbatim | verbatim (unchanged) |
| Near match | answer + handoff | reworded from the retrieved rows |
| Below near | decline | decline, no model call |
| Response `mode` | `strong` / `near` / `decline` | adds `grounded` |

### Why it can't invent

Three layers, and the third is the one that matters:

1. **It only ever sees retrieved passages.** The model is never asked an open
   question. No passages, no call.
2. **The prompt frames passages and question as data**, both delimited, with
   the system prompt saying in as many words that text inside them is never an
   instruction.
3. **The output is checked against the passages before anyone sees it.** An
   answer whose content words don't trace back is discarded and the verbatim
   answer is sent instead. Numbers are checked strictly and separately — a
   price or a window the sheet never gave is the most damaging thing a
   summariser can produce, and word-overlap alone would wave it through.

Layer 3 is what makes 1 and 2 survivable. A model that ignores its instructions
produces text that doesn't overlap the passages; a model talked into reciting
its own system prompt produces text that doesn't overlap them either. Both fail
the same check, which is why the check is worth more than the prompt wording.

Every failure — Ollama down, timeout, refusal, failed verification — falls back
to the answer the bot already had. Nothing about this path can stop a bot
answering.

### The input policy

Enabling rewording creates a prompt-injection surface the verbatim path does
not have, so the filter in front of it is stricter:

| Refused | Verbatim path | Model path |
|---|---|---|
| Code, markup, SQL, shell | allowed | **refused** |
| Instruction-shaped text | logged only | **refused** |
| Cards, Aadhaar, SSNs, keys, passwords | **refused** | **refused** |

Code is deliberately *not* refused on the verbatim path — nothing there reads
it as anything but a vector, so refusing it would be theatre with a real cost
in lost answers. The injection detector's status changes with the pipeline, not
with the text.

Identifiers are checksum-validated, not matched on shape: cards by Luhn,
Aadhaar by Verhoeff plus the issuing rules (never a repdigit, never leading 0
or 1). Without that, every 12-digit tracking number reads as a national ID and
the filter starts refusing the most common question the logistics and
e-commerce templates exist to answer. A query carrying a secret is refused on
**both** paths and is never written to the gap log — it is replaced with a
placeholder, because a gap list is not worth a stored credential.

### Running it

```bash
ollama serve
ollama pull llama3.2:3b

# then, in .env
LLM_ENABLED=true
```

Ollama is expected on loopback. That is the data-residency position in one
line: questions and sheet rows go to a process on the same host rather than to
a vendor. Pointing `OLLAMA_BASE_URL` at a remote host trades that away — do it
knowingly.

## The dashboard assistant

A general-purpose chat assistant inside the web app — ask it anything, and it
also knows your own account: your bot, your plan, and the questions your bot
couldn't answer.

**It is website-only.** Every route is session-cookie authenticated and lives
under `/api/assistant`. No API key reaches it — not a customer key, not an
owner key — and nothing from it is registered under `/v1`. That prefix is what
customers build against, and what they integrate is a bot that answers from
their sheet. A general-purpose model on the same key would quietly turn the
product into something else.

Turn it on with `ASSISTANT_ENABLED=true`. That is independent of
`LLM_ENABLED`, which only offers *bot owners* the grounded-rewording toggle.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/assistant/status` | Enabled, reachable, model, messages left today |
| `GET` | `/api/assistant/threads` | Your conversations |
| `POST` | `/api/assistant/threads` | Start one |
| `GET` | `/api/assistant/threads/{id}` | One conversation and its messages |
| `PUT` | `/api/assistant/threads/{id}` | Rename |
| `DELETE` | `/api/assistant/threads/{id}` | Delete it and its messages |
| `POST` | `/api/assistant/threads/{id}/messages` | Send a message; **streams** the reply |

### What it can and can't do

It runs `qwen2.5:7b` on your own hardware. **It is not ChatGPT, Gemini or
Claude, and it will not match them** — those are hundreds of billions of
parameters on datacenter GPUs. This is 7 billion on two free ARM cores. It is
genuinely good at chat, drafting, summarising and straightforward reasoning,
and noticeably weaker on hard reasoning, long documents and code.

What *is* the same is the experience: streaming replies, multi-turn memory,
markdown, and no subject restrictions.

### Scoping and limits

- **Threads are scoped by `customer_id` in the query itself**, not by the
  caller remembering to filter. Another customer's thread id is
  indistinguishable from one that never existed.
- **Account context is one customer's, always.** `backend/assistant/context.py`
  has no cross-tenant branch; the module that reads every tenant is
  `backend/ops.py`, and it sits behind a different auth dependency.
- **One generation at a time** (`ASSISTANT_CONCURRENCY`). A second request
  queues for `ASSISTANT_QUEUE_WAIT_SECONDS` and is then turned away with a
  reason. On two cores, admitting it would make both people slower rather than
  either one faster.
- **`ASSISTANT_DAILY_MESSAGES` per account per day.** This runs on hardware you
  own, so it is rationed rather than metered.
- **Code is allowed here**, unlike on a bot. A general assistant that refuses a
  pasted stack trace is useless, and the person asking is the account holder in
  their own dashboard. Credentials are still refused — they would otherwise sit
  in the conversation table forever.
- **Model output is sanitised before rendering.** It is markdown-rendered
  through DOMPurify with an allowlist: a reply can contain whatever was pasted
  into the conversation, and putting that straight into `innerHTML` would be a
  stored-XSS hole that happens to be written by a model.

## Google sign-in

Free, optional, and off until you add credentials — the button hides itself
when they're missing, so no configuration means password-only rather than a
broken button.

### Setting it up

1. [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. **Create credentials → OAuth client ID → Web application**
3. Under *Authorised redirect URIs* add, exactly:
   - dev: `http://localhost:8000/api/auth/google/callback`
   - prod: `https://your.domain/api/auth/google/callback`
4. Put the client ID and secret in `.env`:

```bash
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback
```

The redirect URI must match what you registered character for character —
Google rejects anything else, which is the point of registering it.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/providers` | Which sign-in methods this server supports |
| `GET` | `/api/auth/google` | Redirect to Google's consent screen |
| `GET` | `/api/auth/google/callback` | Where Google sends the browser back |

### Why the server-side flow

Google's JavaScript library hands the *page* an ID token and lets the frontend
decide what to do with it. That would mean a second way to be authenticated,
running alongside the session cookie, with the trust decision made somewhere a
user can tamper with it.

Here the browser never holds a Google credential. It is redirected to Google,
Google redirects back with a one-time code, and the server exchanges that code
over its own connection. What the browser ends up with is exactly the httpOnly
session cookie a password login produces — **one session mechanism, one place
that decides who someone is.**

### What is stored, and what isn't

Stored: Google's subject id, the email, and the display name. **No Google
token is kept** — we needed Google to answer "who is this", and once it has,
there is nothing left worth storing. The scopes requested are `openid email
profile` and nothing else: no Gmail, no Drive, no offline access.

Your customers' passwords never existed to leak, because a Google-only account
has none. It stores a placeholder that cannot parse as a hash, so no input
authenticates against it.

### The three guards

1. **The ID token's signature is verified** against Google's published keys,
   along with issuer, audience and expiry, via `google-auth`. A decoded-but-
   unverified JWT is just a string the sender chose.
2. **`state` is checked** against a short-lived httpOnly cookie. Without it, an
   attacker can complete a sign-in *they* started in a victim's browser and
   leave the victim holding a session for the attacker's account.
3. **Linking requires `email_verified`.** Google issues tokens for accounts
   whose address it has not confirmed; treating those as proof of ownership
   would let anyone who can create such an account walk into an existing one.

### Which account you land in

| Situation | What happens |
|---|---|
| Same Google account as before | Signed into it. Matched on Google's `sub`, not email — someone who changes their Gmail address stays in the same account |
| Email already has a password account | **Linked**, because Google confirmed they own that mailbox. The password keeps working — linking adds a way in, it doesn't remove one |
| Neither | New account, no password, trial started, same as any signup |
| Email not verified by Google | Refused, with an explanation |

## Support chat

A place for customers and the team to talk. No model on either side — the FAQ
bot answers from a sheet, the assistant answers from a local model, and this
one is answered by a person.

**One conversation per customer.** A support desk is one continuing
relationship, not a series of tickets someone opened on five different days.

| Where | Who | Auth |
|---|---|---|
| `/support` in the dashboard | the customer, their own conversation | session cookie |
| `/admin/support` | you, every conversation | `X-Admin-Key` |

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/support/messages` | cookie | Your conversation (`?after_id=` for new only) |
| `POST` | `/api/support/messages` | cookie | Write to the team |
| `POST` | `/api/support/read` | cookie | Clear your unread badge |
| `GET` | `/api/support/admin/conversations` | admin | The inbox, newest first, with unread counts |
| `GET` | `/api/support/admin/conversations/{id}` | admin | One conversation |
| `POST` | `/api/support/admin/conversations/{id}/messages` | admin | Reply |
| `POST` | `/api/support/admin/conversations/{id}/read` | admin | Mark it read |

### How a customer is kept to their own conversation

**The customer routes take no conversation id.** Not "the id is checked against
the session" — the parameter does not exist. There is nothing to guess and no
ownership check to forget, and a test asserts no customer support path contains
a path parameter.

The staff routes are a separate prefix behind a separate header, so the two
audiences never share a code path.

### Unread, and why it's a message id

Each side has its own read marker, because "unread" means something different
depending on who is asking — one shared marker would have staff clearing the
customer's badge by opening the thread.

Those markers are **message ids, not timestamps**. Wall-clock time is the wrong
tool: on Windows two consecutive `now()` calls routinely return the identical
value, so a message arriving in the same tick as a read would compare as
already-read and silently never show as unread. Ids are monotonic and cannot
tie. The inbox ordering breaks ties on id for the same reason.

### The staff key

`/admin/support` is not part of the customer app — no nav, no session, its own
gate. The admin key is kept in **`sessionStorage`**, which is cleared when the
tab closes. This is the key that opens every customer's conversation, and it
should not outlive the sitting; the cost is retyping it after a browser
restart, which is the right trade.

Messages refuse credentials on both sides — a card number or password would
otherwise sit in this table forever, readable by staff. Code is allowed: a
support channel that rejects stack traces defeats its own purpose.

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

`mode` is `strong` (verbatim answer), `near` (answer plus a handoff nudge),
`grounded` (a reworded near-match, only for a bot that opted in), or
`decline` (out of scope, or refused by the input policy). Credit balance comes back in the
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
| `GET` | `/api/bot/gaps` | cookie | What the bot couldn't answer |
| `PUT` | `/api/bot/answering` | cookie | Turn grounded rewording on or off |

### The gap list

Every declined or hedged question is logged, and `GET /api/bot/gaps` (plus a
dashboard card) turns that into the list of rows worth adding to the sheet.
This is the loop that makes a bot get better: someone asks something the sheet
doesn't cover → it appears here → the owner adds a row → it's answered next
time.

Questions are grouped case-insensitively and ranked by how often they were
asked, because a raw feed of every miss is unreadable and nobody acts on it.
Each row carries a verdict derived from how close the bot got:

- **Needs phrasing** — scored above the template's near threshold, so the sheet
  nearly covers it. Add the wording people actually used to `Alt_Phrasings`.
- **Not covered** — nothing close. Write a new row.

Injection attempts are counted separately and kept out of the list; they are
attacks, not missing answers. The query is scoped to the signed-in account's
bot — these are real end-users' questions, so leaking them across tenants would
be leaking someone else's customers.

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

### Owner ops (`X-Api-Key` with an `nxo_` key)

Internal, unmetered, and the one place tenant isolation is deliberately
crossed — "which bots are struggling this week?" cannot be answered one tenant
at a time.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/owner/ask` | Ask a question about platform state; answered from the snapshot |
| `GET` | `/v1/owner/snapshot` | The same operational data, no model involved |

```bash
curl -X POST http://localhost:8000/v1/owner/ask \
  -H "X-Api-Key: nxo_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"message": "which bots keep declining questions they nearly cover?", "days": 7}'
```

Gated on the **owner role**, not merely a valid key — a customer key gets the
same 403 as a garbage one, so it cannot be used to confirm the route exists.
Every call is written to the audit log even though none of them are charged: an
unlimited key that reads every tenant is the one most worth a trail.

The snapshot carries aggregates and question text, never identities. Bots
appear as ids and templates; finding an operational problem never requires
knowing whose bot it is. If Ollama isn't running, `/v1/owner/ask` still returns
the snapshot and reports `answered_by_model: false` — the numbers were always
the valuable part.

## The database

MongoDB, one database, one collection per thing that used to be a table:

| Collection | Holds |
|---|---|
| `customers`, `sessions` | Accounts and their sign-ins |
| `subscriptions`, `invoices` | The billing book |
| `referral_codes`, `referral_invites`, `referrals`, `referral_rewards` | The referral programme |
| `users`, `api_keys`, `daily_usage`, `request_log`, `credit_grants` | API accounts, keys, credits and the audit trail |
| `bots`, `template_overrides` | One bot per account, plus the admin's template edits |
| `assistant_threads`, `assistant_messages`, `support_conversations`, `support_messages` | The two chat surfaces |
| `unmatched_queries` | The gap log |

Vectors stay in ChromaDB. That is a different job — approximate nearest
neighbour over embeddings — and MongoDB is not being asked to do it.

**Ids are ObjectIds in the database and strings everywhere else.** A document
stores `_id: ObjectId(...)`; `mongo.document()` converts it, and every id
field beside it, to a 24-character string on the way out. Nothing above that
function sees an ObjectId, because the moment one reaches a response model it
becomes a serialisation error at the worst possible time.

**Indexes are declared, not created ad hoc.** Each store calls
`register_indexes` at import; startup calls `ensure_indexes()` once. Two of
them are constraints the application relies on rather than optimisations:

- `customers.canonical_email` unique — one account per *mailbox*, so `you+1@`
  and `y.o.u@` cannot open a second one.
- `referral_rewards` unique on `(referral_id, role, invoice_id)` — one payout
  per invoice, however many times a provider redelivers its webhook.

Both are partial indexes, so the many documents with no `google_sub` and the
signup rewards with no `invoice_id` do not collide with each other.

**Transactions where the deployment has them.** `mongo.atomic()` opens one on a
replica set (Atlas is one) and yields `None` otherwise, so the calling code is
identical either way. The writes that use it — spending credits, paying a
referral — are ordered so that the worst a partial failure can do is
under-charge or leave a reward unpaid.

**Joins are done in Python, not in `$lookup`.** The admin panel reads five
collections at four different grains; as one pipeline that is unreviewable, and
sub-pipeline lookups do not run on every deployment, so the tests could not
exercise the real query. At hundreds to low thousands of documents the
difference is not measurable. Single-collection `$group` is still used where it
is both clearer and cheaper.

### Running it

Set `MONGO_URI` (and optionally `MONGO_DB_NAME`, default `nexora`). The test
suite needs neither: `mongomock` stands in, so `pytest` runs on a laptop with
nothing installed. It is a genuine test double — no transactions, partial
aggregation support — which is the second reason the stores avoid pipelines
only a live cluster can run.

## Plans and credits

| Plan | Price | Daily credits |
|------|-------|---------------|
| Trial | Free for 14 days | 250 |
| Monthly | $15/month | 5,000 |
| Yearly | $140/year (**$11.67/mo — save $40, 22% off**) | 5,000 |

Prices live in one place, `backend/billing/plans.py`. The discount and effective
monthly rate are *derived* from them, so changing a price updates the pricing
page, the dashboard, and the API together. The discount is rounded **down**, so
the advertised number is never better than what the customer actually gets.

Credits are pooled **per account**, not per key. An account can hold up to 10
keys and they all draw on the same daily allowance — extra keys separate
environments, they don't buy extra capacity. Credits reset at midnight IST.

On top of the allowance sits a **bonus balance** — referral rewards and manual
admin grants. It does not reset and does not expire, and it is spent only once
the day's allowance is gone. That order matters: the allowance disappears at
midnight and the bonus does not, so spending the permanent pool first would
quietly burn credits someone earned while their free allowance went unused.

When a plan lapses the account drops back to the free allowance — deliberately
not to zero, and its keys are **not** revoked. Someone whose card expired
should find their bot throttled, not silently broken in production with an
integration to rebuild when they return. `backend/billing/entitlements.py` owns
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
python backend/scripts/create_owner_key.py owner@example.com "Your Name"
```

The raw key prints once. Use it exactly like a normal `X-Api-Key`.

## Referrals

Every account has a referral code and a link (`/signup?ref=CODE`). Three
payouts, all landing in the bonus balance:

| When | Who is paid | Default |
|------|-------------|---------|
| Someone signs up with your link or from an address you invited | the referrer | **100** |
| The same moment | the newcomer, on top of their plan | **50** |
| Every time that person pays for anything | the referrer, again | **100** |

Amounts are configuration (`REFERRAL_REFERRER_CREDITS`,
`REFERRAL_REFERRED_CREDITS`, `REFERRAL_TOPUP_CREDITS`); set one to 0 to switch
that payout off.

Two ways a referral is attributed. A **code** — carried through password
signup, and through the Google round trip in a short-lived cookie, since the
query string does not survive the trip to Google's consent screen. Or an
**invite** — the referrer enters a friend's email, and whoever signs up with
that address is credited to them even if they never click a link.

What cannot happen, enforced in the database rather than remembered:

- you cannot refer yourself,
- an account can only ever have one referrer, and only at signup,
- a signup bonus is paid once per referral per side,
- **one invoice pays out once**, however many times the payment provider
  delivers its webhook. `referral_rewards` has a unique index on
  `(referral_id, role, invoice_id)`, and the reward row is written *before* the
  credits move — so a retry fails at the insert rather than after the payout.

A mistyped code never fails a signup. It goes unattributed, silently, because
the alternative is refusing to create an account over a typo in a marketing
link.

When you wire up a real payment provider, its webhook must call the same two
lines `POST /api/billing/confirm` does:

```python
paid = db.mark_open_invoices_paid(customer_id, subscription_id)
referrals.reward_payment(customer_id, paid)
```

## Admin panel

`/admin`, gated on `X-Admin-Key` — the same key and the same gate as the
support inbox at `/admin/support`, held in `sessionStorage` so it does not
outlive the tab. No customer session and no customer API key opens it, however
valid; every route reads across all tenants.

| Tab | What it answers |
|-----|-----------------|
| Overview | Is the business growing, is the platform busy, is the model up |
| Users | Every account: plan, usage, bot, bonus balance, who referred them. Grant credits, override the daily limit, disable an account, revoke any key |
| Subscriptions | Counts by plan and status, MRR, revenue by month, churn, trial→paid conversion |
| Usage | Credits and requests per day, heaviest accounts, per endpoint |
| API audit | Who called what with which key, filterable to owner keys, plus everything the injection detector flagged |
| AI usage | Retrieval and grounded rewording, split by the plan each account is on, plus what the matcher failed to answer |
| Templates | Edit any template's wording or thresholds, retire one from the picker, reset it to the code default |
| Referrals | Programme totals, who is referring, and the payout trail |

Two distinctions the panel keeps rather than flattening:

- **Entitled ≠ paying.** A live trial is entitled and worth nothing. They are
  separate numbers because a dashboard that counts trials as revenue is a
  dashboard that flatters itself.
- **MRR counts a yearly plan as a twelfth of its price.** A year paid up front
  is cash, not a run rate.

### Editing templates

`bot/templates.py` stays the source of truth; the panel writes a *patch* over
it in `template_overrides`. Name, tagline, description, scope wording, decline
lines and both thresholds can be changed, and each field shows its code default
next to it so any edit can be undone by someone who never saw the original.

Edits reach bots that are already answering — that is the point, since it means
a wrong decline message is a two-minute fix rather than a deploy. Starter
sheets, sample questions and the per-vertical action guards stay in code:
those carry a template's safety reasoning, not its copy.

Retiring a template hides it from the picker for *new* bots. Bots already
running on it keep working exactly as they were, because withdrawing a product
someone already configured is not a catalogue change.

## Payments

**No card details ever touch this server.** Every provider is a *hosted*
checkout: we create a session, redirect to the provider's page, and learn the
result from a signed webhook. Do not add a card form to this project.

`BILLING_PROVIDER=manual` is the development default — it takes no payment and
leaves the plan `pending`. Activating without payment additionally requires
`BILLING_ALLOW_MANUAL=true`, so it cannot become a free-access hole in
production. `backend/billing/payments.py` has step-by-step wiring notes for Stripe
and Razorpay; both currently raise rather than silently granting access.

**Before going live:** set `BILLING_COOKIE_SECURE=true`, point `WEB_ORIGINS` at
your real domain, and verify webhook signatures in `POST /api/billing/webhook`
(it returns 501 until you do).

## Configuration

All settings in `.env` / `backend/shared/config.py`. `.env` is loaded automatically;
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
| `LLM_ENABLED` | `false` | Master switch for the local model. Bots still opt in individually |
| `OLLAMA_BASE_URL` | `127.0.0.1:11434` | Where Ollama is. Loopback keeps data on this host |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model tag used for rewording and owner questions |
| `OLLAMA_TIMEOUT_SECONDS` | `20` | After this, fall back to the verbatim answer |
| `LLM_TEMPERATURE` | `0.1` | Low — the job is faithful rephrasing, not writing |
| `LLM_MAX_TOKENS` | `400` | Cap on a generated answer |
| `LLM_MAX_INPUT_CHARS` | `2000` | Hard ceiling on what may be handed to the model |
| `LLM_MIN_GROUNDEDNESS` | `0.72` | Below this, the generated answer is discarded |
| `ASSISTANT_ENABLED` | `false` | The dashboard chat assistant. Website-only |
| `ASSISTANT_MODEL` | `OLLAMA_MODEL` | Shares one model rather than loading a second |
| `ASSISTANT_HISTORY_TURNS` | `12` | Turns of context sent with each message |
| `ASSISTANT_CONCURRENCY` | `1` | Generations at once. Extra requests queue |
| `ASSISTANT_DAILY_MESSAGES` | `100` | Per account per day |

## Testing

```bash
pytest tests/ -v
```

406 tests covering the template catalogue, sheet ingestion, per-template scope
enforcement, tenant isolation, injection payloads, action-intent blocking,
pricing maths, entitlement grant/withdrawal, credit pooling, key-ownership
scoping, gap-list tenant scoping, the input policy (including its
false-positive cases), groundedness verification, owner-role gating, and
the assistant's thread scoping and API-key exclusion, and the support
chat's two-sided unread accounting, and Google sign-in's state, signature
and email-verification guards.

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

- **No prompt injection risk on the default path** — with rewording off, user input is only ever an embedding query. It is never placed in a prompt, because nothing reads it as one
- **Layered defence on the model path** — for a bot that opted in, input is screened before the model sees it (code, markup, SQL, shell, credentials, instruction-shaped text are all refused), the prompt frames retrieved rows and the question as untrusted data, and the output is verified against those rows before it is sent. A model that ignores its instructions produces text that doesn't trace back to the passages, and is discarded by the same check that catches invention
- **Injection detection** — regex pattern detector. Logging-only on the verbatim path, where a blocking detector would be a denial-of-service lever and nothing else; promoted to a hard block on the model path, where the risk is real
- **Secrets never stored** — card numbers (Luhn-checked), Aadhaar (Verhoeff-checked), SSNs, API keys and passwords are refused on *both* paths, and the query is replaced with a placeholder in the gap log rather than persisted
- **The model runs locally** — Ollama on loopback. Questions and sheet rows go to a process on the same host, not to a third-party API
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
