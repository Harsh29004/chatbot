"""
Customer bot FastAPI router.

Provides ``POST /customer-bot/ask`` — the only user-facing endpoint
for this bot.  Authenticated via API key with credit-based billing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.customer_bot.graph import build_customer_graph
from shared.auth import verify_api_key
from shared.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/customer-bot", tags=["Customer Bot"])

# Compile the graph once at import time
_graph = build_customer_graph()


@router.post("/ask", response_model=ChatResponse)
async def ask(
    body: ChatRequest,
    _key: dict = Depends(verify_api_key),
) -> ChatResponse:
    """
    Answer a customer's question using retrieval-only FAQ matching.

    Requires ``X-Api-Key`` header. Each request costs credits based on
    message length (see ``GET /api/keys/pricing``).

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

