"""
Partner bot FastAPI router.

Provides ``POST /partner-bot/ask`` — the only user-facing endpoint
for this bot.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.partner_bot.graph import build_partner_graph
from shared.auth import require_user_type
from shared.rate_limiter import rate_limit_dependency
from shared.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/partner-bot", tags=["Partner Bot"])

_graph = build_partner_graph()


@router.post("/ask", response_model=ChatResponse)
async def ask(
    body: ChatRequest,
    user: dict = Depends(require_user_type("partner")),
    _rate: dict = Depends(rate_limit_dependency),
) -> ChatResponse:
    """
    Answer a partner's question using retrieval-only FAQ matching.
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
