"""
LangGraph retrieval flow for the **Customer** FAQ bot.

Nodes
-----
1. ``check_guardrails``  — injection flagging + action-intent blocking
2. ``embed_query``       — call Ollama to embed the user's question
3. ``retrieve_top_k``    — search the customer FAQ ChromaDB collection
4. ``score_router``      — conditional edge based on confidence thresholds
5. ``strong_response``   — return the FAQ answer verbatim
6. ``near_match_response`` — return answer + support-nudge template
7. ``decline_and_log``   — return fixed decline message, log to SQLite

Phase 1: zero LLM calls in the response path.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from shared.config import (
    CUSTOMER_COLLECTION,
    CUSTOMER_DECLINE_MESSAGE,
    NEAR_MATCH_SUFFIX,
    NEAR_MATCH_THRESHOLD,
    STRONG_MATCH_THRESHOLD,
    TOP_K,
)
from shared.embeddings import embed_text
from shared.guardrails import detect_action_intent, detect_injection
from shared.logging_store import log_query
from shared.vector_store import get_collection, query_collection


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class BotState(TypedDict, total=False):
    """Mutable state threaded through the graph."""

    # Inputs
    query: str
    session_id: str

    # Guardrail flags
    flagged_injection: bool
    is_action_request: bool

    # Embedding
    query_embedding: list[float]

    # Retrieval results
    top_matches: list[dict[str, Any]]
    top_score: float
    top_question: str | None
    top_answer: str | None

    # Output
    response: str
    mode: str  # "strong" | "near" | "decline"
    matched_question: str | None
    confidence: float


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def check_guardrails(state: BotState) -> dict:
    """Flag injection attempts and detect action-intent phrasing."""
    query = state["query"]
    return {
        "flagged_injection": detect_injection(query),
        "is_action_request": detect_action_intent(query),
    }


def embed_query(state: BotState) -> dict:
    """Embed the user's question via Ollama."""
    embedding = embed_text(state["query"])
    return {"query_embedding": embedding}


def retrieve_top_k(state: BotState) -> dict:
    """Search the customer FAQ collection."""
    collection = get_collection(CUSTOMER_COLLECTION)
    matches = query_collection(collection, state["query_embedding"], k=TOP_K)

    if matches:
        best = matches[0]
        return {
            "top_matches": matches,
            "top_score": best["similarity"],
            "top_question": best["metadata"].get("question"),
            "top_answer": best["metadata"].get("answer"),
        }
    return {
        "top_matches": [],
        "top_score": 0.0,
        "top_question": None,
        "top_answer": None,
    }


def strong_response(state: BotState) -> dict:
    """Return the FAQ answer verbatim — high confidence match."""
    return {
        "response": state["top_answer"],
        "mode": "strong",
        "matched_question": state["top_question"],
        "confidence": state["top_score"],
    }


def near_match_response(state: BotState) -> dict:
    """Return the answer + support nudge — moderate confidence."""
    log_query(
        bot_type="customer",
        query_text=state["query"],
        top_match_score=state["top_score"],
        top_match_question=state["top_question"],
        flagged_injection=state.get("flagged_injection", False),
        session_id=state.get("session_id"),
    )
    return {
        "response": state["top_answer"] + NEAR_MATCH_SUFFIX,
        "mode": "near",
        "matched_question": state["top_question"],
        "confidence": state["top_score"],
    }


def decline_and_log(state: BotState) -> dict:
    """Return the fixed decline message and log to SQLite."""
    log_query(
        bot_type="customer",
        query_text=state["query"],
        top_match_score=state.get("top_score", 0.0),
        top_match_question=state.get("top_question"),
        flagged_injection=state.get("flagged_injection", False),
        session_id=state.get("session_id"),
    )
    return {
        "response": CUSTOMER_DECLINE_MESSAGE,
        "mode": "decline",
        "matched_question": None,
        "confidence": state.get("top_score", 0.0),
    }


# ---------------------------------------------------------------------------
# Routing logic
# ---------------------------------------------------------------------------

def route_after_guardrails(state: BotState) -> str:
    """If the user is asking for an action, skip retrieval → decline."""
    if state.get("is_action_request"):
        return "decline_and_log"
    return "embed_query"


def route_by_score(state: BotState) -> str:
    """Three-tier confidence routing."""
    score = state.get("top_score", 0.0)
    if score >= STRONG_MATCH_THRESHOLD:
        return "strong_response"
    if score >= NEAR_MATCH_THRESHOLD:
        return "near_match_response"
    return "decline_and_log"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_customer_graph() -> StateGraph:
    """
    Assemble and compile the customer-bot LangGraph.

    Returns a compiled graph that can be invoked with::

        result = graph.invoke({"query": "...", "session_id": "..."})
    """
    graph = StateGraph(BotState)

    # Add nodes
    graph.add_node("check_guardrails", check_guardrails)
    graph.add_node("embed_query", embed_query)
    graph.add_node("retrieve_top_k", retrieve_top_k)
    graph.add_node("strong_response", strong_response)
    graph.add_node("near_match_response", near_match_response)
    graph.add_node("decline_and_log", decline_and_log)

    # Entry
    graph.set_entry_point("check_guardrails")

    # Edges
    graph.add_conditional_edges(
        "check_guardrails",
        route_after_guardrails,
        {
            "embed_query": "embed_query",
            "decline_and_log": "decline_and_log",
        },
    )
    graph.add_edge("embed_query", "retrieve_top_k")
    graph.add_conditional_edges(
        "retrieve_top_k",
        route_by_score,
        {
            "strong_response": "strong_response",
            "near_match_response": "near_match_response",
            "decline_and_log": "decline_and_log",
        },
    )

    # Terminal edges
    graph.add_edge("strong_response", END)
    graph.add_edge("near_match_response", END)
    graph.add_edge("decline_and_log", END)

    return graph.compile()
