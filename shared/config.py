"""
Configuration constants for Instant Sahay FAQ Bots.

All tunable thresholds and environment-dependent settings live here.
Values are loaded from environment variables with sensible defaults.
"""

import os
from datetime import timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root (resolved relative to this file)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
# ChromaDB persistence
# ---------------------------------------------------------------------------
CHROMA_PERSIST_DIR: str = os.getenv(
    "CHROMA_PERSIST_DIR",
    str(PROJECT_ROOT / "chroma_data"),
)

# Customer and partner collection names — never shared
CUSTOMER_COLLECTION: str = "customer_faq_index"
PARTNER_COLLECTION: str = "partner_faq_index"

# ---------------------------------------------------------------------------
# Retrieval settings
# ---------------------------------------------------------------------------
TOP_K: int = int(os.getenv("TOP_K", "3"))

# ---------------------------------------------------------------------------
# SQLite logging
# ---------------------------------------------------------------------------
SQLITE_DB_PATH: str = os.getenv(
    "SQLITE_DB_PATH",
    str(PROJECT_ROOT / "logs" / "faq_bot.db"),
)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "admin-change-me")

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
# Fixed response templates
# ---------------------------------------------------------------------------
CUSTOMER_DECLINE_MESSAGE: str = (
    "I can only help with questions about Instant Sahay bookings, payments "
    "and your account. Try rephrasing, or contact support."
)
PARTNER_DECLINE_MESSAGE: str = (
    "I can only help with questions about your Instant Sahay partner account, "
    "jobs, payouts, and KYC. Try rephrasing, or contact partner support."
)
NEAR_MATCH_SUFFIX: str = (
    "\n\nIf this doesn't fully answer your question, please contact support "
    "and we'll help directly."
)
