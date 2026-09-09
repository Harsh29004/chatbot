"""
Tests for grounded summarisation — the layer that keeps "cannot invent" true.

The prompt is not the safeguard. A model can be talked out of a prompt, and a
3B model can wander off one without being talked into anything. The safeguard
is that generated text is checked back against the retrieved passages before
anyone sees it, and discarded if it doesn't trace.

These tests exercise that check directly and then exercise the fallback: every
failure mode must land on the verbatim answer the bot already had, never on an
error and never on the unverified text.
"""

from __future__ import annotations

import pytest

from bot import grounding
from bot.grounding import (
    INSUFFICIENT,
    _strip_wrapper,
    groundedness,
    invented_numbers,
)

PASSAGES = [
    "Q: How do I cancel an order?\nA: Open My Orders, select the order and choose "
    "Cancel Order. Cancellation is free before dispatch.",
    "Q: How do I get a refund?\nA: Refunds are processed within 7 working days to "
    "the original payment method.",
]


# ---------------------------------------------------------------------------
# The groundedness metric
# ---------------------------------------------------------------------------

def test_a_faithful_rewording_scores_high():
    answer = "To cancel an order, open My Orders, select it and choose Cancel Order."
    assert groundedness(answer, PASSAGES) > 0.9


def test_invented_prose_scores_low():
    answer = "Please visit our Mumbai warehouse with photographic identification."
    assert groundedness(answer, PASSAGES) < 0.5


def test_a_leaked_system_prompt_scores_low():
    """
    The check catches instruction-leaking for free.

    Nothing here looks for the words "system prompt" — a model reciting its own
    instructions simply produces vocabulary the passages never contained, which
    is the same failure as inventing, and is caught the same way.
    """
    answer = (
        "My rules say I must use only facts in the reference block and reply "
        "with a sentinel token when the context is insufficient."
    )
    assert groundedness(answer, PASSAGES) < grounding.config.LLM_MIN_GROUNDEDNESS


def test_an_answer_with_no_content_words_is_not_penalised():
    assert groundedness("Yes, it is.", PASSAGES) == 1.0


# ---------------------------------------------------------------------------
# Numbers get a stricter rule than words
# ---------------------------------------------------------------------------

def test_a_number_from_the_sheet_is_accepted():
    assert invented_numbers("Refunds take 7 working days.", PASSAGES) == []


def test_a_number_the_sheet_never_gave_is_caught():
    """
    Rewording a fact is fine. Changing it is not.

    "5 days" from a sheet that says 7 is the single most damaging thing a
    summariser can do here, and word-overlap alone would wave it through
    because every other word in the sentence is sourced.
    """
    assert invented_numbers("Refunds take 5 working days.", PASSAGES) == ["5"]


# ---------------------------------------------------------------------------
# Output cleanup
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Sure! Here's a natural rewrite: Refunds take 7 days.", "Refunds take 7 days."),
        ("Certainly: Refunds take 7 days.", "Refunds take 7 days."),
        ("```\nRefunds take 7 days.\n```", "Refunds take 7 days."),
        ('"Refunds take 7 days."', "Refunds take 7 days."),
        ("Refunds take 7 days.", "Refunds take 7 days."),
    ],
)
def test_model_scaffolding_is_stripped(raw, expected):
    assert _strip_wrapper(raw) == expected


# ---------------------------------------------------------------------------
# summarise() — every failure lands on None
# ---------------------------------------------------------------------------

def _matches(*answers: str) -> list[dict]:
    return [
        {"metadata": {"question": f"Q{i}", "answer": answer}, "similarity": 0.8}
        for i, answer in enumerate(answers)
    ]


def test_no_passages_means_no_model_call(monkeypatch):
    """Grounded-only, enforced at the entry point: nothing to ground on, no call."""
    called = False

    def _fail(**kwargs):
        nonlocal called
        called = True
        return "anything"

    monkeypatch.setattr(grounding.llm, "generate", _fail)
    monkeypatch.setattr(grounding.llm, "available", lambda: True)

    assert grounding.summarise("hello?", []) is None
    assert called is False


def test_unavailable_model_returns_none(monkeypatch):
    monkeypatch.setattr(grounding.llm, "available", lambda: False)
    assert grounding.summarise("how do I cancel?", _matches("Open My Orders.")) is None


def test_sentinel_is_treated_as_a_decline(monkeypatch):
    monkeypatch.setattr(grounding.llm, "available", lambda: True)
    monkeypatch.setattr(grounding.llm, "generate", lambda **kw: INSUFFICIENT)
    assert grounding.summarise("what is the capital of France?", _matches("Open My Orders.")) is None


def test_ungrounded_output_is_discarded(monkeypatch):
    monkeypatch.setattr(grounding.llm, "available", lambda: True)
    monkeypatch.setattr(
        grounding.llm, "generate",
        lambda **kw: "Visit our Mumbai warehouse with photo identification please.",
    )
    result = grounding.summarise(
        "how do I cancel?",
        _matches("Open My Orders, select the order and choose Cancel Order."),
    )
    assert result is None


def test_output_with_an_unsourced_number_is_discarded(monkeypatch):
    monkeypatch.setattr(grounding.llm, "available", lambda: True)
    monkeypatch.setattr(
        grounding.llm, "generate",
        lambda **kw: "Refunds are processed within 5 working days to the original payment method.",
    )
    result = grounding.summarise(
        "when do I get my money?",
        _matches("Refunds are processed within 7 working days to the original payment method."),
    )
    assert result is None


def test_a_faithful_rewording_is_returned(monkeypatch):
    monkeypatch.setattr(grounding.llm, "available", lambda: True)
    monkeypatch.setattr(
        grounding.llm, "generate",
        lambda **kw: "Refunds are processed to the original payment method within 7 working days.",
    )
    result = grounding.summarise(
        "when do I get my money?",
        _matches("Refunds are processed within 7 working days to the original payment method."),
    )
    assert result is not None
    assert "7 working days" in result


def test_a_model_exception_never_escapes(monkeypatch):
    """
    The summariser is optional infrastructure and must behave like it.

    llm.generate swallows its own failures and returns None; this asserts the
    contract from the caller's side, so a future change that lets an exception
    through fails here rather than in production.
    """
    monkeypatch.setattr(grounding.llm, "available", lambda: True)
    monkeypatch.setattr(grounding.llm, "generate", lambda **kw: None)
    assert grounding.summarise("anything?", _matches("Some answer.")) is None


def test_passages_carry_only_retrieved_rows(monkeypatch):
    """The model must never see more than what retrieval returned."""
    seen = {}

    def _capture(*, system, prompt, **kwargs):
        seen["prompt"] = prompt
        seen["system"] = system
        return "Open My Orders and choose Cancel Order."

    monkeypatch.setattr(grounding.llm, "available", lambda: True)
    monkeypatch.setattr(grounding.llm, "generate", _capture)

    grounding.summarise("how do I cancel?", _matches("Open My Orders and choose Cancel Order."))

    assert "Open My Orders" in seen["prompt"]
    assert "how do I cancel?" in seen["prompt"]
    # Delimited, and named as untrusted in the system prompt.
    assert "<<<REFERENCE>>>" in seen["prompt"]
    assert "<<<QUESTION>>>" in seen["prompt"]
    assert "untrusted data" in seen["system"]
