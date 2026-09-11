---
title: Nexora AI
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

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

## Templates

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
