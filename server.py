"""
Instant Sahay FAQ Bots — main FastAPI application.

Mounts both bot routers, API key management, admin endpoints,
health check, and a mobile-friendly test chat UI at ``/chat``.

Run with:  ``uvicorn server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.api_keys_router import router as keys_router
from apps.customer_bot.ingest import ingest as customer_ingest
from apps.customer_bot.main import router as customer_router
from apps.partner_bot.ingest import ingest as partner_ingest
from apps.partner_bot.main import router as partner_router
from shared.api_keys import init_api_key_tables
from shared.auth import verify_admin_key
from shared.config import CUSTOMER_COLLECTION, PARTNER_COLLECTION
from shared.logging_store import init_db
from shared.schemas import HealthResponse, ReindexResponse
from shared.vector_store import get_collection

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup tasks:
    1. Initialise SQLite tables (logging + API keys)
    2. Auto-ingest FAQ data into ChromaDB
    """
    # Initialise databases
    init_db()
    init_api_key_tables()
    logger.info("Database tables initialised.")

    # Auto-ingest FAQ data (idempotent — rebuilds collections)
    try:
        c_count = customer_ingest()
        logger.info("Customer FAQ: ingested %d documents.", c_count)
    except Exception as exc:
        logger.warning("Customer FAQ ingest failed: %s", exc)

    try:
        p_count = partner_ingest()
        logger.info("Partner FAQ: ingested %d documents.", p_count)
    except Exception as exc:
        logger.warning("Partner FAQ ingest failed: %s", exc)

    yield


app = FastAPI(
    title="Instant Sahay FAQ Bots",
    description=(
        "Retrieval-grounded FAQ chatbot API with multi-tenant API key "
        "authentication and credit-based billing.  Each API key gets "
        "250 credits/day (resets at midnight IST)."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow requests from your frontend
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
# Static files (CSS, JS, logo)
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------
app.include_router(keys_router)
app.include_router(customer_router)
app.include_router(partner_router)


# ---------------------------------------------------------------------------
# Chat UI — serve the mobile test page
# ---------------------------------------------------------------------------

@app.get("/chat", include_in_schema=False)
async def chat_page():
    """Serve the mobile chat HTML page."""
    html_path = STATIC_DIR / "chat.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Chat UI not available. Use the API endpoints directly."}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/admin/reindex/{bot_type}",
    response_model=ReindexResponse,
    tags=["Admin"],
)
async def reindex(
    bot_type: Literal["customer", "partner"],
    _admin: bool = Depends(verify_admin_key),
) -> ReindexResponse:
    """
    Re-run ingestion from the current Excel file for the specified bot.

    Requires ``X-Admin-Key`` header.
    """
    if bot_type == "customer":
        count = customer_ingest()
    else:
        count = partner_ingest()

    return ReindexResponse(
        status="ok",
        documents_indexed=count,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    """Basic health check with collection document counts."""
    try:
        c_count = get_collection(CUSTOMER_COLLECTION).count()
    except Exception:
        c_count = 0
    try:
        p_count = get_collection(PARTNER_COLLECTION).count()
    except Exception:
        p_count = 0

    return HealthResponse(
        status="ok",
        customer_collection_count=c_count,
        partner_collection_count=p_count,
    )

