# Chatbot API Server

Pure API bot server for retrieval-grounded FAQ chatbots. **Phase 1: zero LLM generation** — answers come directly from the FAQ sheet (verbatim, near-match with nudge, or fixed decline). No generation model in the response path.

## Architecture

```
  Client Request (X-Api-Key)
         |
         v
  +--------------+     +---------------+     +----------------+
  |  Guardrails  |---->| Embed Query   |---->| Retrieve Top-K |
  |  (injection  |     | (sentence-    |     | (ChromaDB)     |
  |   + action)  |     |  transformers)|     +-------+--------+
  +--------------+     +---------------+             |
        |                                     +------+------+
        | action                              v             |
        | intent                        score >= 0.85?      |
        v                                    |              |
  +-----------+     +-----------+       yes  |              |
  | DECLINE   |     | STRONG    |<-----------+              |
  | (log+msg) |     | (verbatim)|       score >= 0.60?      |
  +-----------+     +-----------+            |              |
                    +-----------+       yes  |              |
                    | NEAR      |<-----------+              |
                    | (answer + |       score < 0.60?       |
                    |  nudge)   |<--- DECLINE --------------+
                    +-----------+
```

## Performance

| Metric | Value |
|--------|-------|
| Avg end-to-end latency | **25ms** |
| Throughput (single-threaded) | **39.5 queries/sec** |
| Guardrails | ~0ms (regex) |
| Embedding | ~5ms (CPU) |
| ChromaDB Retrieval | ~1ms |

Run `python benchmark.py` to reproduce.

## Quick Start

### Prerequisites
- Python 3.11+

### Setup

```bash
cd chatbot
pip install -r requirements.txt

# Copy env and configure
cp .env.example .env
# Edit .env — set ADMIN_API_KEY

# Generate dummy FAQ data (or place your real Excel sheets)
python generate_dummy_data.py

# Run the server (auto-ingests FAQ data on startup)
uvicorn server:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker-compose up --build
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/customer-bot/ask` | `X-Api-Key` | Ask the customer FAQ bot |
| `POST` | `/partner-bot/ask` | `X-Api-Key` | Ask the partner FAQ bot |
| `POST` | `/api/keys/generate` | `X-Admin-Key` | Generate a new API key |
| `GET` | `/api/keys` | `X-Admin-Key` | List all API keys |
| `DELETE` | `/api/keys/{id}` | `X-Admin-Key` | Revoke an API key |
| `GET` | `/api/keys/usage` | `X-Api-Key` | Check credit usage |
| `GET` | `/api/keys/pricing` | None | View credit cost tiers |
| `POST` | `/admin/reindex/{bot_type}` | `X-Admin-Key` | Re-ingest Excel data |
| `GET` | `/health` | None | Health check |

### Example Usage

```bash
# 1. Generate an API key (admin)
curl -X POST http://localhost:8000/api/keys/generate \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"owner_email": "app@example.com", "owner_name": "My App"}'

# 2. Ask a question (using the generated API key)
curl -X POST http://localhost:8000/customer-bot/ask \
  -H "X-Api-Key: isk_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I cancel a booking?", "session_id": "abc123"}'

# Response
{
  "response": "To cancel a booking, go to My Bookings...",
  "mode": "strong",
  "matched_question": "How do I cancel a booking?",
  "confidence": 0.92
}

# 3. Check credit usage
curl http://localhost:8000/api/keys/usage \
  -H "X-Api-Key: isk_xxxxxxxxxxxxxxxx"

# 4. Reindex FAQ data (admin)
curl -X POST http://localhost:8000/admin/reindex/customer \
  -H "X-Admin-Key: your-admin-key"
```

## Credit System

Credits are pooled **per account** (identified by `owner_email`), not per key. Each account gets **250 credits/day** (resets at midnight IST) shared across every API key that account holds — creating extra keys does not grant extra credits. Credit cost depends on message length:

| Message Length | Credit Cost |
|---------------|-------------|
| 1-200 chars | 1 credit |
| 201-500 chars | 2 credits |
| 501-1000 chars | 3 credits |
| 1001-2000 chars | 5 credits |

Credit info is returned in response headers: `X-Credits-Remaining`, `X-Credits-Daily-Limit`, `X-Credits-Reset-At`, `X-Credit-Cost`.

### Owner keys (unlimited, internal use only)

An **owner key** (prefix `iso_`) skips credit checks entirely — no limit, no deduction. It exists only for the project owners themselves and is minted locally, never via an HTTP endpoint:

```bash
python scripts/create_owner_key.py owner@example.com "Harsh"
```

Print the raw key once, store it privately, and use it exactly like a normal `X-Api-Key`. Regular customer keys are unaffected — they still go through `POST /api/keys/generate` as before.

## Testing

```bash
pytest tests/ -v
```

97 tests covering: known questions, near-match phrasing, out-of-scope rejection, injection payloads, and action-intent blocking.

## Configuration

All settings in `.env` / `shared/config.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_API_KEY` | `admin-change-me` | Admin key for management endpoints |
| `STRONG_MATCH_THRESHOLD` | `0.85` | Similarity score for verbatim answer |
| `NEAR_MATCH_THRESHOLD` | `0.60` | Similarity score for answer + nudge |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model (CPU) |
| `TOP_K` | `3` | Number of retrieval results |
| `DAILY_CREDIT_LIMIT` | `250` | Credits per API key per day |

## Excel Sheet Format

Both `customer_faq.xlsx` and `partner_faq.xlsx` must have:

| Column | Description |
|--------|-------------|
| `Question` | The canonical FAQ question |
| `Alt_Phrasings` | Semicolon-separated alternate phrasings |
| `Category` | Category label (Bookings, Payments, etc.) |
| `Answer` | The approved answer text (returned verbatim) |

## Security

- **No prompt injection risk** — user input is only used as embedding query input, never in any LLM prompt
- **Injection detection** — regex-based pattern detector flags suspicious input for review
- **Action blocking** — requests to "do something" (refund, cancel, change) are declined regardless of FAQ match
- **Isolated data** — separate ChromaDB collections per bot, zero cross-contamination
- **API key hashing** — keys are stored as SHA-256 hashes, raw keys shown only once at generation
- **Auth separation** — user API keys and admin keys use different headers and routes
