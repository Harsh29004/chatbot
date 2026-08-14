"""
Customer bot FastAPI router.

Provides ``POST /customer-bot/ask`` — the only user-facing endpoint
for this bot.  Auth, rate limiting, and the LangGraph flow are composed
via FastAPI dependencies.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.customer_bot.graph import build_customer_graph
from shared.auth import require_user_type
from shared.rate_limiter import rate_limit_dependency
from shared.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/customer-bot", tags=["Customer Bot"])

# Compile the graph once at import time
_graph = build_customer_graph()


@router.post("/ask", response_model=ChatResponse)
async def ask(
    body: ChatRequest,
    user: dict = Depends(require_user_type("customer")),
    _rate: dict = Depends(rate_limit_dependency),
) -> ChatResponse:
    """
    Answer a customer's question using retrieval-only FAQ matching.

    The response is always one of:
    - **strong**: verbatim FAQ answer (similarity ≥ 0.85)
    - **near**: FAQ answer + support nudge (0.60 ≤ similarity < 0.85)
    - **decline**: fixed decline message (similarity < 0.60)
    """
    result = _graph.invoke(
        {
            "query": body.message,
            "session_id": body.session_id,
        }
    )
    return ChatResponse(
        response=result["response"],
        mode=result["mode"],
        matched_question=result.get("matched_question"),
        confidence=result.get("confidence", 0.0),
    )
