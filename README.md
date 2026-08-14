# Instant Sahay FAQ Bots

Retrieval-grounded FAQ chatbot services for the Instant Sahay customer and partner apps. **Phase 1: pure retrieval, zero generation** — the answer is always either the FAQ sheet's answer verbatim, a near-match template with a support nudge, or a fixed decline message. No LLM is used in the response path.

## Architecture

```
User Question
     │
     ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ Guardrails  │────▶│ Embed Query  │────▶│ Retrieve     │
│ (injection  │     │ (Ollama)     │     │ Top-K        │
│  + action)  │     └──────────────┘     │ (ChromaDB)   │
└─────────────┘                          └──────┬───────┘
      │ action                                  │
      │ intent                           ┌──────┴───────┐
      ▼                                  ▼              │
┌─────────────┐     ┌──────────────┐  score ≥ 0.85?    │
│ DECLINE     │     │ STRONG       │◄── yes             │
│ (log + msg) │     │ (verbatim)   │                    │
└─────────────┘     └──────────────┘  score ≥ 0.60?    │
                    ┌──────────────┐◄── yes             │
                    │ NEAR MATCH   │                    │
                    │ (answer +    │    score < 0.60?   │
                    │  nudge)      │◄── DECLINE ────────┘
                    └──────────────┘
```

## Quick Start

### Prerequisites
- Python 3.11+
- Ollama running with `nomic-embed-text` model pulled

### Setup

```bash
# Clone and install
cd instant-sahay-faq-bots
pip install -r requirements.txt

# Copy env and configure
cp .env.example .env
# Edit .env with your Ollama URL, JWT secret, admin key

# Generate dummy FAQ data (or place your real Excel sheets)
python generate_dummy_data.py

# Ingest FAQ data into ChromaDB
python -m apps.customer_bot.ingest
python -m apps.partner_bot.ingest

# Run the server
uvicorn server:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker-compose up --build
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/customer-bot/ask` | JWT (customer) | Ask the customer FAQ bot |
| POST | `/partner-bot/ask` | JWT (partner) | Ask the partner FAQ bot |
| POST | `/admin/reindex/{bot_type}` | X-Admin-Key | Re-ingest Excel → ChromaDB |
| GET | `/health` | None | Health check with collection counts |

### Request/Response

```bash
# Ask a question
curl -X POST http://localhost:8000/customer-bot/ask \
  -H "Authorization: Bearer <JWT>" \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I cancel a booking?", "session_id": "abc123"}'

# Response
{
  "response": "To cancel a booking, go to My Bookings...",
  "mode": "strong",
  "matched_question": "How do I cancel a booking?",
  "confidence": 0.92
}

# Reindex
curl -X POST http://localhost:8000/admin/reindex/customer \
  -H "X-Admin-Key: your-admin-key"
```

## Testing

```bash
pytest tests/ -v
```

## Configuration

All thresholds and settings are in `.env` / `shared/config.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `STRONG_MATCH_THRESHOLD` | 0.85 | Similarity score for verbatim answer |
| `NEAR_MATCH_THRESHOLD` | 0.60 | Similarity score for answer + nudge |
| `OLLAMA_BASE_URL` | http://localhost:11434 | Ollama API URL |
| `OLLAMA_MODEL` | nomic-embed-text | Embedding model |
| `RATE_LIMIT_PER_MINUTE` | 30 | Per-user rate limit |

## Excel Sheet Format

Both `customer_faq.xlsx` and `partner_faq.xlsx` must have these columns:

| Column | Description |
|--------|-------------|
| `Question` | The canonical FAQ question |
| `Alt_Phrasings` | Semicolon-separated alternate phrasings |
| `Category` | Category label (Bookings, Payments, etc.) |
| `Answer` | The approved answer text (returned verbatim) |

## Phase 2 (not yet implemented)

An optional LLM rephrasing layer to make answers more conversational. The architecture is designed to accommodate this as an additional node in the LangGraph flow, between retrieval and response output.

## Security

- **No prompt injection risk**: User input is only used as an embedding query and logged data — never concatenated into any instruction or prompt
- **Injection detection**: Regex-based pattern detector flags suspicious input for review (logging only, doesn't gate pipeline)
- **Action blocking**: Requests to "do something" (refund, cancel, change) are declined regardless of FAQ match score
- **Isolated data**: Separate ChromaDB collections per bot — no cross-contamination possible
- **Auth separation**: User JWT ≠ admin API key, different routes, different auth mechanisms
