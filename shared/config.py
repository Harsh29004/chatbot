"""
Configuration constants for Instant Sahay FAQ Bots.

All tunable thresholds and environment-dependent settings live here.
Values are loaded from environment variables with sensible defaults.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root (resolved relative to this file)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
# Ollama embedding configuration
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "nomic-embed-text")

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
JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "admin-change-me")

# ---------------------------------------------------------------------------
# Rate limiting (per user)
# ---------------------------------------------------------------------------
RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

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
