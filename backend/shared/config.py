"""
Configuration constants for Nexora AI.

All tunable thresholds and environment-dependent settings live here.
Values are loaded from environment variables with sensible defaults.

Per-bot settings — which collection to search, how sure it has to be, what it
says when it isn't — live on the template instead (``bot/``).
The thresholds here are only the defaults a template inherits when it doesn't
override them.
"""

import os
from datetime import timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root (resolved relative to this file)
# ---------------------------------------------------------------------------
# backend/shared/config.py -> backend/shared -> backend -> the project root.
# Three levels, not two: this file used to live at shared/config.py, and the
# move into backend/ silently pointed .env loading and the default data paths
# one directory too deep.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------
# Without this every os.getenv below silently falls back to its default, so a
# deployment that carefully set ADMIN_API_KEY in .env would still be running
# on the published default. Real environment variables win over the file, so
# container/systemd config still overrides it.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover - dotenv is an optional convenience
    pass

# ---------------------------------------------------------------------------
# Timezone — IST (UTC+5:30)
# ---------------------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Retrieval confidence thresholds (tune after seeing real query data)
# ---------------------------------------------------------------------------
STRONG_MATCH_THRESHOLD: float = float(
    os.getenv("STRONG_MATCH_THRESHOLD", "0.85")
)
NEAR_MATCH_THRESHOLD: float = float(
    os.getenv("NEAR_MATCH_THRESHOLD", "0.60")
)

# ---------------------------------------------------------------------------
# Embedding model (sentence-transformers, runs on CPU)
# ---------------------------------------------------------------------------
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# ---------------------------------------------------------------------------
# Vector storage
# ---------------------------------------------------------------------------
# FAQ vectors are stored in MongoDB with everything else (backend.shared.
# vector_store), so there is no separate data directory to configure or back up.

# ---------------------------------------------------------------------------
# Retrieval settings
# ---------------------------------------------------------------------------
TOP_K: int = int(os.getenv("TOP_K", "3"))

# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
# The connection itself lives in backend.shared.mongo, which reads these. They
# are mirrored here so that every tunable setting is still findable in one
# file, which is the whole point of this module.
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "nexora")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "admin-change-me")

# Username and password for the admin panel and support inbox. Signing in
# returns a signed session token that expires after ADMIN_SESSION_HOURS. An
# empty ADMIN_PASSWORD disables password sign-in; the admin key still works
# for scripts.
ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
ADMIN_SESSION_HOURS: float = float(os.getenv("ADMIN_SESSION_HOURS", "12"))

# ---------------------------------------------------------------------------
# Credit system
# ---------------------------------------------------------------------------
DAILY_CREDIT_LIMIT: int = int(os.getenv("DAILY_CREDIT_LIMIT", "250"))

# Variable credit cost based on query length (characters)
# Each tier: (max_chars, credit_cost)
# e.g. 1-200 chars = 1 credit, 201-500 = 2 credits, etc.
CREDIT_COST_TIERS: list[tuple[int, int]] = [
    (200, 1),    # Short queries: 1 credit
    (500, 2),    # Medium queries: 2 credits
    (1000, 3),   # Long queries: 3 credits
    (2000, 5),   # Very long queries: 5 credits
]


def get_credit_cost(message_length: int) -> int:
    """Return the credit cost for a message of the given character length."""
    for max_chars, cost in CREDIT_COST_TIERS:
        if message_length <= max_chars:
            return cost
    # Longer than all tiers — use the last tier's cost
    return CREDIT_COST_TIERS[-1][1]


# ---------------------------------------------------------------------------
# Google sign-in
# ---------------------------------------------------------------------------
# The server-side authorization-code flow, not the browser JS one. The client
# secret never reaches the page, the code is exchanged server to server, and
# what the browser ends up holding is the same httpOnly session cookie a
# password login produces. One session mechanism, not two.
#
# Enabled only when both halves of the credential are present: a half-configured
# OAuth button that 500s is worse than no button.
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Must match a redirect URI registered on the OAuth client in Google Cloud,
# character for character — Google rejects anything else, which is the whole
# point of registering it.
GOOGLE_REDIRECT_URI: str = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback"
)

GOOGLE_OAUTH_ENABLED: bool = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# How long a sign-in attempt may sit half-finished. This bounds the window in
# which a stolen state cookie is worth anything.
OAUTH_STATE_TTL_SECONDS: int = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))

# How far this server's clock may drift from Google's when checking an ID
# token's issued-at and expiry times. Zero rejects a token Google issued one
# second "in the future" by our clock — which a laptop or VM a second behind
# hits on every sign-in. Signature, issuer and audience are still checked.
GOOGLE_CLOCK_SKEW_SECONDS: int = int(os.getenv("GOOGLE_CLOCK_SKEW_SECONDS", "30"))

# Which Google screens to show on every sign-in. "select_account consent"
# always shows the account chooser and the permission screen; "select_account"
# lets Google skip both when the browser has one account that already agreed.
GOOGLE_PROMPT: str = os.getenv("GOOGLE_PROMPT", "select_account consent").strip() or "select_account"

# ---------------------------------------------------------------------------
# Local LLM (Ollama) — optional, and off unless explicitly enabled
# ---------------------------------------------------------------------------
# The retrieval path never needs this. It is used for two things only:
#
#   1. Grounded summarisation on the *near* band of a bot that has opted in.
#      The model may only rephrase passages retrieval already found; it is
#      never asked an open question and never sees the internet.
#   2. The owner ops assistant, which is internal and nxo_-key only.
#
# Ollama is expected to be reachable on the loopback interface. That is the
# whole data-residency story: the query and the sheet rows are handed to a
# process on the same host, so nothing leaves the machine the way it would
# with a hosted model API. Point OLLAMA_BASE_URL somewhere remote and you have
# given that up — deliberately, and you should know you did.
LLM_ENABLED: bool = os.getenv("LLM_ENABLED", "false").lower() == "true"
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT_SECONDS: float = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "20"))


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


# The fallback chain, tried in order. When a model is rate limited, busy,
# timing out or missing, the next one answers instead — first the local
# Ollama models, then Groq once every local model has failed.
#
# OLLAMA_MODEL stays first so an existing .env keeps its primary model.
OLLAMA_MODELS: list[str] = list(dict.fromkeys(
    [OLLAMA_MODEL, *_csv(os.getenv("OLLAMA_MODELS", "qwen2.5:7b,llama3.1:8b,gemma2:9b"))]
))

# Groq is the last resort, and it is a hosted API: the prompt — including
# retrieved sheet rows — leaves this machine when it is used. Unset
# GROQ_API_KEY to keep every generation local.
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "").strip()
GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODELS: list[str] = _csv(
    os.getenv("GROQ_MODELS", "qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b")
)
GROQ_TIMEOUT_SECONDS: float = float(os.getenv("GROQ_TIMEOUT_SECONDS", "30"))

# Gemini, reached through Google's OpenAI-compatible endpoint. Several keys
# may be given (comma-separated): each model is tried on every key before the
# next model, because free-tier quotas are per project per model. Hosted, like
# Groq — prompts leave this machine when Gemini answers.
GEMINI_API_KEYS: list[str] = list(dict.fromkeys(
    _csv(os.getenv("GEMINI_API_KEYS", "")) + _csv(os.getenv("GEMINI_API_KEY", ""))
))
GEMINI_BASE_URL: str = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
)
GEMINI_MODELS: list[str] = _csv(
    os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.8-flash")
)
GEMINI_TIMEOUT_SECONDS: float = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))

# Which providers are tried, in order. Drop one to disable it entirely.
LLM_PROVIDER_ORDER: list[str] = [
    p.lower() for p in _csv(os.getenv("LLM_PROVIDER_ORDER", "ollama,groq,gemini"))
    if p.lower() in ("ollama", "groq", "gemini")
]

# How long a rate-limited or failing model is skipped before being tried
# again. Without it every request would re-hit a model we know is saturated.
LLM_COOLDOWN_SECONDS: float = float(os.getenv("LLM_COOLDOWN_SECONDS", "60"))

# A key the provider rejects outright (invalid, revoked, disabled) is skipped
# much longer: it won't start working on its own.
LLM_BAD_KEY_COOLDOWN_SECONDS: float = float(os.getenv("LLM_BAD_KEY_COOLDOWN_SECONDS", "3600"))

# Sampling. Low temperature because the job is faithful rephrasing, not
# invention — creativity here is the failure mode, not the feature.
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "400"))

# Hard ceiling on what may be handed to the model. Not a policy filter — the
# policy filter is input_policy.py — just the engineering limit that stops one
# request from building an unbounded prompt.
LLM_MAX_INPUT_CHARS: int = int(os.getenv("LLM_MAX_INPUT_CHARS", "2000"))

# A grounded answer is rejected if too little of it traces back to the source
# passages. See ``bot.grounding.groundedness`` — 0.0 accepts anything, 1.0
# accepts only near-verbatim copies.
LLM_MIN_GROUNDEDNESS: float = float(os.getenv("LLM_MIN_GROUNDEDNESS", "0.72"))

# ---------------------------------------------------------------------------
# The dashboard assistant
# ---------------------------------------------------------------------------
# A general-purpose chat assistant, reachable **only** from the signed-in web
# app. It is deliberately not on any API-key route: customers integrate a
# bot that answers from their sheet, and that contract does not change.
#
# It shares OLLAMA_MODEL by default rather than loading a second model. On a
# 12 GB box, two resident models plus the app is the difference between
# comfortable and swapping.
ASSISTANT_ENABLED: bool = os.getenv("ASSISTANT_ENABLED", "false").lower() == "true"
ASSISTANT_MODEL: str = os.getenv("ASSISTANT_MODEL", OLLAMA_MODEL)

# Generation is slow on CPU, so a long answer needs a long ceiling — but the
# reply streams, so the user reads it as it arrives rather than waiting.
ASSISTANT_TIMEOUT_SECONDS: float = float(os.getenv("ASSISTANT_TIMEOUT_SECONDS", "180"))
ASSISTANT_TEMPERATURE: float = float(os.getenv("ASSISTANT_TEMPERATURE", "0.7"))
ASSISTANT_MAX_TOKENS: int = int(os.getenv("ASSISTANT_MAX_TOKENS", "1024"))

# How many previous messages ride along as context. Every extra turn is more
# prompt to re-read on a CPU, and 7B models lose the thread well before they
# run out of window anyway.
ASSISTANT_HISTORY_TURNS: int = int(os.getenv("ASSISTANT_HISTORY_TURNS", "12"))
ASSISTANT_MAX_MESSAGE_CHARS: int = int(os.getenv("ASSISTANT_MAX_MESSAGE_CHARS", "4000"))

# Two ARM cores generate for one person at a time. Letting a third and fourth
# request in doesn't make them faster, it makes everyone slower — so they
# queue, and past the queue they are turned away with a reason.
ASSISTANT_CONCURRENCY: int = int(os.getenv("ASSISTANT_CONCURRENCY", "1"))
ASSISTANT_QUEUE_WAIT_SECONDS: float = float(os.getenv("ASSISTANT_QUEUE_WAIT_SECONDS", "45"))
ASSISTANT_DAILY_MESSAGES: int = int(os.getenv("ASSISTANT_DAILY_MESSAGES", "100"))

# ---------------------------------------------------------------------------
# Fallback response wording
# ---------------------------------------------------------------------------
# Every template carries its own decline message and handoff nudge, written in
# that industry's voice. These are only used by a bot whose template somehow
# doesn't supply them.
DEFAULT_DECLINE_MESSAGE: str = (
    "I can only answer questions covered by this business's FAQ. Try "
    "rephrasing, or contact the team and a person will help you directly."
)
NEAR_MATCH_SUFFIX: str = (
    "\n\nIf this doesn't fully answer your question, please contact support "
    "and we'll help directly."
)
