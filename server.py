"""
Nexora AI — main FastAPI application.

Mounts the platform API (accounts, billing, dashboard), the bot API
(templates, sheet upload, ``/v1/ask``), API-key management, and a health
check.

Run with:  ``uvicorn server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api_keys_router import router as keys_router
from apps.billing.db import expire_lapsed_subscriptions, init_billing_tables
from apps.billing.router import router as platform_router
from apps.bot_engine.store import init_bot_tables
from apps.bots_router import router as bots_router
from shared.api_keys import init_api_key_tables
from shared.logging_store import init_db
from shared.schemas import HealthResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup tasks:
    1. Initialise SQLite tables (logging + API keys + billing + bots)
    2. Retire any subscriptions that lapsed while the server was down
    """
    init_db()
    init_api_key_tables()
    init_billing_tables()
    init_bot_tables()
    logger.info("Database tables initialised.")

    lapsed = expire_lapsed_subscriptions()
    if lapsed:
        logger.info("Expired %d lapsed subscription(s) on startup.", lapsed)

    yield


app = FastAPI(
    title="Nexora AI",
    description=(
        "Retrieval-grounded FAQ chatbots, sold as a service. Customers pick a "
        "template, upload their FAQ sheet, and get an API key. Answers come "
        "from their own sheet — there is no generation step, so the bot "
        "cannot invent one."
    ),
    version="3.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# The dashboard authenticates with a session cookie, and browsers refuse to
# send credentials to a wildcard origin — so the web origins are listed
# explicitly. Set WEB_ORIGINS (comma-separated) in production.
WEB_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "WEB_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=WEB_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Credits-Remaining",
        "X-Credits-Daily-Limit",
        "X-Credits-Reset-At",
        "X-Credit-Cost",
    ],
)


# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------
app.include_router(platform_router)
app.include_router(bots_router)
app.include_router(keys_router)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    """Basic health check."""
    return HealthResponse(status="ok")
