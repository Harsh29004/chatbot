"""
Instant Sahay FAQ Bots — main FastAPI application.

Mounts both bot routers, admin endpoints, and health check.
Run with:  ``uvicorn server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI

from apps.customer_bot.ingest import ingest as customer_ingest
from apps.customer_bot.main import router as customer_router
from apps.partner_bot.ingest import ingest as partner_ingest
from apps.partner_bot.main import router as partner_router
from shared.auth import verify_admin_key
from shared.config import CUSTOMER_COLLECTION, PARTNER_COLLECTION
from shared.logging_store import init_db
from shared.schemas import HealthResponse, ReindexResponse
from shared.vector_store import get_collection


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise the SQLite logging table."""
    init_db()
    yield


app = FastAPI(
    title="Instant Sahay FAQ Bots",
    description=(
        "Retrieval-grounded FAQ chatbot services for the Instant Sahay "
        "customer and partner apps.  Phase 1: pure retrieval, zero generation."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Mount bot routers
# ---------------------------------------------------------------------------
app.include_router(customer_router)
app.include_router(partner_router)


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
