"""
Near-match phrasing — reworded questions and alternate wordings.

Real users don't type the sheet's wording. These tests check that the
pipeline handles rewordings without falling over, and that the alternate
phrasings a customer lists in their sheet actually route to the right answer.

The mock embeddings are hash-based rather than semantic, so a *paraphrase*
with no shared words can't be expected to match — the real model handles
that. What is asserted here is the wiring: valid modes, non-empty answers,
and exact alternate phrasings landing on their own row.
"""

from __future__ import annotations

import pytest

# Rewordings of questions in the demo sheet.
REWORDED = [
    "How can I cancel my order?",
    "I want to cancel a purchase",
    "Where is my refund?",
    "I haven't received my money back",
    "How to update my mobile number?",
    "What are the payment options?",
    "Can I pay with UPI?",
    "How much is shipping?",
    "I need to send an item back",
]


@pytest.mark.parametrize("query", REWORDED)
def test_reworded_question_returns_a_valid_answer(demo_graph, query):
    """Whatever tier it lands in, it must return a real response."""
    result = demo_graph.invoke({"query": query, "session_id": "near-match-test"})

    assert result["mode"] in ("strong", "near", "decline")
    assert isinstance(result["response"], str)
    assert len(result["response"]) > 0


@pytest.mark.parametrize("query", REWORDED)
def test_reworded_question_reports_a_confidence(demo_graph, query):
    """Customers route on this score, so it must always be present and sane."""
    result = demo_graph.invoke({"query": query, "session_id": "near-match-test"})

    assert 0.0 <= result["confidence"] <= 1.0


# -- Alternate phrasings from the sheet ------------------------------------
# These are listed in the customer's own Alt_Phrasings column, so they are
# indexed as their own vectors and should route to that row's answer.

ALT_PHRASINGS = [
    ("order cancellation", "Cancel Order"),
    ("money back", "Refunds are processed"),
    ("update phone number", "Edit Profile"),
    ("payment options", "UPI"),
    ("order tracking", "live tracking"),
    ("shipping cost", "Delivery is free"),
    ("return policy", "within 7 days"),
]


@pytest.mark.parametrize("phrasing,expected_substr", ALT_PHRASINGS)
def test_alternate_phrasing_finds_its_row(demo_graph, phrasing, expected_substr):
    result = demo_graph.invoke({"query": phrasing, "session_id": "alt-test"})

    assert result["mode"] in ("strong", "near"), (
        f"Alternate phrasing '{phrasing}' should not decline"
    )
    assert expected_substr in result["response"]


def test_an_action_phrased_alternate_still_declines(demo_graph, demo_template):
    """
    Worth knowing when writing a sheet: the action guardrail runs *before*
    retrieval, so listing "cancel my order" under Alt_Phrasings does not make
    the bot answer it. That is intended — the phrasing asks the bot to cancel,
    not to explain cancelling — but it surprises people, so it is pinned here.
    """
    result = demo_graph.invoke({"query": "cancel my order", "session_id": "alt-action"})

    assert result["mode"] == "decline"
    assert result["response"] == demo_template.decline_message

    # The informational form of the same question is answered normally.
    informational = demo_graph.invoke(
        {"query": "How do I cancel an order?", "session_id": "alt-action-2"}
    )
    assert informational["mode"] != "decline"


# -- The near tier attaches a handoff --------------------------------------

def _graph_with_thresholds(bot, template, *, strong: float, near: float):
    """The demo bot's own collection, re-read at a chosen confidence band."""
    from apps.bot_engine.graph import BotConfig, build_graph

    return build_graph(
        BotConfig(
            collection_name=bot["collection_name"],
            decline_message=template.decline_message,
            near_match_suffix=template.near_match_suffix,
            strong_threshold=strong,
            near_threshold=near,
        )
    )


def test_a_near_match_appends_the_templates_handoff(demo_bot, demo_template):
    """
    A hedged answer should say it is hedged. Driving the tier with thresholds
    is more honest than hunting for a query that happens to land in it.
    """
    # Nothing can reach strong; almost anything clears near.
    graph = _graph_with_thresholds(demo_bot, demo_template, strong=1.01, near=0.10)

    result = graph.invoke(
        {"query": "How do I get a refund?", "session_id": "near-forced"}
    )

    assert result["mode"] == "near"
    assert result["response"].endswith(demo_template.near_match_suffix)
    assert "Refunds are processed" in result["response"]


def test_a_strong_match_does_not_append_the_handoff(demo_bot, demo_template):
    """The same answer, above the strong threshold, comes back clean."""
    graph = _graph_with_thresholds(demo_bot, demo_template, strong=0.10, near=0.05)

    result = graph.invoke(
        {"query": "How do I get a refund?", "session_id": "strong-forced"}
    )

    assert result["mode"] == "strong"
    assert not result["response"].endswith(demo_template.near_match_suffix)


def test_everything_below_the_near_threshold_declines(demo_bot, demo_template):
    """Raise the floor above any achievable score and even a perfect match is refused."""
    graph = _graph_with_thresholds(demo_bot, demo_template, strong=1.02, near=1.01)

    result = graph.invoke(
        {"query": "How do I get a refund?", "session_id": "decline-forced"}
    )

    assert result["mode"] == "decline"
    assert result["response"] == demo_template.decline_message
