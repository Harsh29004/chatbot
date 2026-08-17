"""
Instant Sahay FAQ Bots — main FastAPI application.

Mounts both bot routers, admin endpoints, health check, and a
mobile-friendly test chat UI at ``/chat``.

Run with:  ``uvicorn server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from apps.customer_bot.ingest import ingest as customer_ingest
from apps.customer_bot.main import router as customer_router
from apps.customer_bot.graph import build_customer_graph
from apps.partner_bot.ingest import ingest as partner_ingest
from apps.partner_bot.main import router as partner_router
from apps.partner_bot.graph import build_partner_graph
from shared.auth import verify_admin_key
from shared.config import CUSTOMER_COLLECTION, PARTNER_COLLECTION
from shared.logging_store import init_db
from shared.schemas import ChatResponse, HealthResponse, ReindexResponse
from shared.vector_store import get_collection

STATIC_DIR = Path(__file__).resolve().parent / "static"


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
# CORS — allow local testing from any origin
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static files (CSS, JS, logo)
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Mount bot routers
# ---------------------------------------------------------------------------
app.include_router(customer_router)
app.include_router(partner_router)


# ---------------------------------------------------------------------------
# Chat UI — serve the mobile test page
# ---------------------------------------------------------------------------

@app.get("/chat", include_in_schema=False)
async def chat_page():
    """Serve the mobile chat HTML page."""
    return FileResponse(STATIC_DIR / "chat.html", media_type="text/html")


# ---------------------------------------------------------------------------
# Auth-free test endpoint (for manual testing only!)
# ---------------------------------------------------------------------------

class TestChatRequest(BaseModel):
    """Request body for the auth-free test endpoint."""
    message: str = Field(..., min_length=1, max_length=2000)
    bot_type: Literal["customer", "partner"] = Field(default="customer")
    session_id: Optional[str] = Field(default="test-session")


# Build graphs once
_customer_graph = build_customer_graph()
_partner_graph = build_partner_graph()


@app.post("/test-bot/ask", response_model=ChatResponse, tags=["Test"])
async def test_ask(body: TestChatRequest) -> ChatResponse:
    """
    Auth-free test endpoint for the chat UI.

    **WARNING**: Bypasses JWT auth and rate limiting.
    For local testing only — do NOT expose in production.
    """
    graph = _customer_graph if body.bot_type == "customer" else _partner_graph
    result = graph.invoke(
        {
            "query": body.message,
            "session_id": body.session_id or "test-session",
        }
    )
    return ChatResponse(
        response=result["response"],
        mode=result["mode"],
        matched_question=result.get("matched_question"),
        confidence=result.get("confidence", 0.0),
    )


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
