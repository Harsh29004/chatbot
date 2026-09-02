"""
Config-driven retrieval flow, shared by every bot on the platform.

This is the same three-tier pipeline the built-in bots have always used —
guardrails, embed, retrieve, route on confidence — with the parts that differ
per customer lifted into ``BotConfig``: which collection to search, how sure
it has to be, and what it says when it isn't sure enough.

Still zero LLM calls in the response path. The bot cannot say anything that
is not already written in the customer's sheet, which is what makes the
"never invented" promise structural rather than aspirational.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from shared.config import NEAR_MATCH_THRESHOLD, STRONG_MATCH_THRESHOLD, TOP_K
from shared.embeddings import embed_text
from shared.guardrails import detect_action_intent, detect_injection
from shared.logging_store import log_query
from shared.vector_store import get_collection, query_collection


@dataclass(frozen=True)
class BotConfig:
    """Everything that makes one bot different from another."""

    collection_name: str
    decline_message: str
    near_match_suffix: str
    strong_threshold: float = STRONG_MATCH_THRESHOLD
    near_threshold: float = NEAR_MATCH_THRESHOLD
    top_k: int = TOP_K
    # Used as ``bot_type`` in the unmatched-query log, so a customer can see
    # which of their bots produced the gap.
    log_label: str = "bot"
    extra_action_patterns: tuple[str, ...] = field(default_factory=tuple)


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
    mode: str  # "strong" | "near" | "decline"
    matched_question: str | None
    confidence: float


def build_graph(config: BotConfig):
    """
    Compile a bot graph for *config*.

    Nodes close over the config rather than reading globals, so two bots with
    different thresholds can run side by side in the same process.
    """
    # Vertical-specific "don't do things, only explain them" patterns, compiled
    # once per bot rather than per request.
    extra_action_re = (
        re.compile("|".join(f"(?:{p})" for p in config.extra_action_patterns), re.IGNORECASE)
        if config.extra_action_patterns
        else None
    )

    def check_guardrails(state: BotState) -> dict:
        query = state["query"]
        is_action = detect_action_intent(query)
        if not is_action and extra_action_re is not None:
            is_action = bool(extra_action_re.search(query))
        return {
            "flagged_injection": detect_injection(query),
            "is_action_request": is_action,
        }

    def embed_query(state: BotState) -> dict:
        return {"query_embedding": embed_text(state["query"])}

    def retrieve_top_k(state: BotState) -> dict:
        collection = get_collection(config.collection_name)
        matches = query_collection(collection, state["query_embedding"], k=config.top_k)

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
            bot_type=config.log_label,
            query_text=state["query"],
            top_match_score=state["top_score"],
            top_match_question=state["top_question"],
            flagged_injection=state.get("flagged_injection", False),
            session_id=state.get("session_id"),
        )
        return {
            "response": state["top_answer"] + config.near_match_suffix,
            "mode": "near",
            "matched_question": state["top_question"],
            "confidence": state["top_score"],
        }

    def decline_and_log(state: BotState) -> dict:
        log_query(
            bot_type=config.log_label,
            query_text=state["query"],
            top_match_score=state.get("top_score", 0.0),
            top_match_question=state.get("top_question"),
            flagged_injection=state.get("flagged_injection", False),
            session_id=state.get("session_id"),
        )
        return {
            "response": config.decline_message,
            "mode": "decline",
            "matched_question": None,
            "confidence": state.get("top_score", 0.0),
        }

    def route_after_guardrails(state: BotState) -> str:
        if state.get("is_action_request"):
            return "decline_and_log"
        return "embed_query"

    def route_by_score(state: BotState) -> str:
        score = state.get("top_score", 0.0)
        if score >= config.strong_threshold:
            return "strong_response"
        if score >= config.near_threshold:
            return "near_match_response"
        return "decline_and_log"

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
        {"embed_query": "embed_query", "decline_and_log": "decline_and_log"},
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
