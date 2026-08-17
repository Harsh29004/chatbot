"""
Partner bot FastAPI router.

Provides ``POST /partner-bot/ask`` — the only user-facing endpoint
for this bot.  Authenticated via API key with credit-based billing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.partner_bot.graph import build_partner_graph
from shared.auth import verify_api_key
from shared.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/partner-bot", tags=["Partner Bot"])

_graph = build_partner_graph()


@router.post("/ask", response_model=ChatResponse)
async def ask(
    body: ChatRequest,
    _key: dict = Depends(verify_api_key),
) -> ChatResponse:
    """
    Answer a partner's question using retrieval-only FAQ matching.

    Requires ``X-Api-Key`` header. Each request costs credits based on
    message length (see ``GET /api/keys/pricing``).
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

