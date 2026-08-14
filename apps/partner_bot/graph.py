"""
LangGraph retrieval flow for the **Partner** FAQ bot.

Mirrors the customer bot graph but uses the ``partner_faq_index``
collection and the partner-specific decline message.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from shared.config import (
    NEAR_MATCH_SUFFIX,
    NEAR_MATCH_THRESHOLD,
    PARTNER_COLLECTION,
    PARTNER_DECLINE_MESSAGE,
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

    query: str
    session_id: str

    flagged_injection: bool
    is_action_request: bool

    query_embedding: list[float]

    top_matches: list[dict[str, Any]]
    top_score: float
    top_question: str | None
    top_answer: str | None

    response: str
    mode: str
    matched_question: str | None
    confidence: float


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def check_guardrails(state: BotState) -> dict:
    query = state["query"]
    return {
        "flagged_injection": detect_injection(query),
        "is_action_request": detect_action_intent(query),
    }


def embed_query(state: BotState) -> dict:
    embedding = embed_text(state["query"])
    return {"query_embedding": embedding}


def retrieve_top_k(state: BotState) -> dict:
    collection = get_collection(PARTNER_COLLECTION)
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
    return {
        "response": state["top_answer"],
        "mode": "strong",
        "matched_question": state["top_question"],
        "confidence": state["top_score"],
    }


def near_match_response(state: BotState) -> dict:
    log_query(
        bot_type="partner",
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
    log_query(
        bot_type="partner",
        query_text=state["query"],
        top_match_score=state.get("top_score", 0.0),
        top_match_question=state.get("top_question"),
        flagged_injection=state.get("flagged_injection", False),
        session_id=state.get("session_id"),
    )
    return {
        "response": PARTNER_DECLINE_MESSAGE,
        "mode": "decline",
        "matched_question": None,
        "confidence": state.get("top_score", 0.0),
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_after_guardrails(state: BotState) -> str:
    if state.get("is_action_request"):
        return "decline_and_log"
    return "embed_query"


def route_by_score(state: BotState) -> str:
    score = state.get("top_score", 0.0)
    if score >= STRONG_MATCH_THRESHOLD:
        return "strong_response"
    if score >= NEAR_MATCH_THRESHOLD:
        return "near_match_response"
    return "decline_and_log"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_partner_graph() -> StateGraph:
    """Assemble and compile the partner-bot LangGraph."""
    graph = StateGraph(BotState)

    graph.add_node("check_guardrails", check_guardrails)
    graph.add_node("embed_query", embed_query)
    graph.add_node("retrieve_top_k", retrieve_top_k)
    graph.add_node("strong_response", strong_response)
    graph.add_node("near_match_response", near_match_response)
    graph.add_node("decline_and_log", decline_and_log)

    graph.set_entry_point("check_guardrails")

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

    graph.add_edge("strong_response", END)
    graph.add_edge("near_match_response", END)
    graph.add_edge("decline_and_log", END)

    return graph.compile()
