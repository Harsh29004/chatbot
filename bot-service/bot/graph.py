"""
Config-driven retrieval flow, shared by every bot on the platform.

This is the same three-tier pipeline the built-in bots have always used —
guardrails, embed, retrieve, route on confidence — with the parts that differ
per customer lifted into ``BotConfig``: which collection to search, how sure
it has to be, and what it says when it isn't sure enough.

Facts always come from the customer's sheet. With ``llm_enabled`` off — the
default, and the behaviour every existing bot keeps — no prose is written at
all: answers are returned verbatim or not at all.

With it on, a local model may **reword** the passages retrieval found, on the
near band only, and only after its output is checked back against those
passages (``bot/grounding.py``). A strong match is still served verbatim: an
exact hit is already the best answer available and spending a model call on it
can only make it worse. So the promise narrows precisely, from "no prose" to
"no fact that isn't in the sheet", and never further.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from backend.shared import input_policy
from backend.shared.config import (
    LLM_MAX_INPUT_CHARS,
    NEAR_MATCH_THRESHOLD,
    STRONG_MATCH_THRESHOLD,
    TOP_K,
)
from backend.shared.embeddings import embed_text
from backend.shared.guardrails import detect_action_intent, detect_injection
from backend.shared.logging_store import log_query
from backend.shared.vector_store import get_collection, query_collection

from bot import grounding

# What the unmatched-query log stores in place of a question that carried a
# card number or a credential. The gap list is meant to show an owner what to
# add to their sheet; it is not worth keeping a secret forever to do that.
REDACTED = "[redacted — contained sensitive data]"


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

    # Opt-in, per bot, default off. Turning it on lets a local model reword
    # near-band matches, and tightens the input filter for this bot because
    # the model is a surface the verbatim path doesn't have.
    llm_enabled: bool = False


class BotState(TypedDict, total=False):
    """Mutable state threaded through the graph."""

    query: str
    session_id: str

    flagged_injection: bool
    is_action_request: bool

    # Set when the input policy refused the query outright. Carries the
    # wording shown to the user, which is more specific than the template's
    # generic decline — "don't paste your card number" is worth saying plainly.
    policy_refusal: str | None
    policy_category: str | None
    # False when the query itself must not be persisted anywhere.
    safe_to_log: bool

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

        # The input policy runs first and can end the request on its own. It
        # is stricter for a bot with the model enabled, because code and
        # instruction-shaped text only matter once something reads them as
        # anything other than a vector.
        verdict = input_policy.screen(
            query,
            for_model=config.llm_enabled,
            max_chars=LLM_MAX_INPUT_CHARS,
        )

        is_action = detect_action_intent(query)
        if not is_action and extra_action_re is not None:
            is_action = bool(extra_action_re.search(query))

        return {
            "flagged_injection": detect_injection(query),
            "is_action_request": is_action,
            "policy_refusal": verdict.message,
            "policy_category": verdict.category,
            "safe_to_log": verdict.safe_to_log,
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

    def _log(state: BotState) -> None:
        """
        Record the miss for the owner's gap list.

        A near match and a decline are both worth logging — one says the sheet
        nearly covers this, the other says it doesn't at all. The query text is
        replaced when the policy marked it unloggable, because a gap list is
        not worth a stored credential.
        """
        log_query(
            bot_type=config.log_label,
            query_text=state["query"] if state.get("safe_to_log", True) else REDACTED,
            top_match_score=state.get("top_score", 0.0),
            top_match_question=state.get("top_question"),
            flagged_injection=state.get("flagged_injection", False),
            session_id=state.get("session_id"),
        )

    def near_match_response(state: BotState) -> dict:
        _log(state)

        verbatim = state["top_answer"] + config.near_match_suffix

        # Grounded rewording, only for a bot that opted in, and only ever as
        # an improvement on an answer we already have. Anything at all going
        # wrong — model down, output unverifiable — lands back on `verbatim`.
        if config.llm_enabled:
            generated = grounding.summarise(state["query"], state.get("top_matches") or [])
            if generated:
                return {
                    "response": generated + config.near_match_suffix,
                    "mode": "grounded",
                    "matched_question": state["top_question"],
                    "confidence": state["top_score"],
                }

        return {
            "response": verbatim,
            "mode": "near",
            "matched_question": state["top_question"],
            "confidence": state["top_score"],
        }

    def decline_and_log(state: BotState) -> dict:
        _log(state)
        return {
            # A policy refusal explains itself. Falling back to the template's
            # generic "I can only answer questions about X" would leave someone
            # who pasted a card number with no idea why they were turned away.
            "response": state.get("policy_refusal") or config.decline_message,
            "mode": "decline",
            "matched_question": None,
            "confidence": state.get("top_score", 0.0),
        }

    def route_after_guardrails(state: BotState) -> str:
        if state.get("policy_refusal"):
            return "decline_and_log"
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
