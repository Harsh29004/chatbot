"""
Pydantic request / response models for the FAQ Bot API.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Incoming question from the app user."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The user's question text.",
    )
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Client-generated session identifier for conversation grouping.",
    )


class ChatResponse(BaseModel):
    """Bot response returned to the app."""

    response: str = Field(
        ..., description="The answer text (verbatim, near-match, or decline)."
    )
    mode: Literal["strong", "near", "decline"] = Field(
        ..., description="Which confidence tier was triggered."
    )
    matched_question: Optional[str] = Field(
        None,
        description="The FAQ question that was matched (null on decline).",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Cosine similarity score of the best match.",
    )


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class ReindexResponse(BaseModel):
    """Response from the admin reindex endpoint."""

    status: str
    documents_indexed: int


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Health-check response."""

    status: str = "ok"
    customer_collection_count: int = 0
    partner_collection_count: int = 0
