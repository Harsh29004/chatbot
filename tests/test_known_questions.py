"""
Known questions — exact wording from the customer's sheet.

Every question written in the sheet should come back with its own answer,
and never with a decline. If this fails, the product's core promise is
broken: a customer wrote an answer down and the bot refused to give it.
"""

from __future__ import annotations

import pytest

# (question as written in the sheet, a substring of its expected answer)
KNOWN_QUESTIONS = [
    ("How do I cancel an order?", "Cancel Order"),
    ("How do I get a refund?", "Refunds are processed"),
    ("How do I change my phone number?", "Edit Profile"),
    ("What payment methods do you accept?", "UPI"),
    ("How do I track my order?", "live tracking"),
    ("What are your delivery charges?", "Delivery is free"),
    ("How do I return an item?", "within 7 days"),
]


@pytest.mark.parametrize("question,expected_substr", KNOWN_QUESTIONS)
def test_known_question_via_graph(demo_graph, question, expected_substr):
    result = demo_graph.invoke({"query": question, "session_id": "test-session"})

    assert result["mode"] in ("strong", "near"), (
        f"Expected strong/near for '{question}', got '{result['mode']}'"
    )
    assert expected_substr in result["response"], (
        f"Expected '{expected_substr}' in response for '{question}'"
    )


@pytest.mark.parametrize("question,expected_substr", KNOWN_QUESTIONS)
def test_known_question_via_api(api_client, question, expected_substr):
    """The same questions, through the endpoint customers actually call."""
    resp = api_client.post(
        "/v1/ask",
        json={"message": question, "session_id": "api-test"},
        headers={"X-Api-Key": "test-key"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] in ("strong", "near")
    assert expected_substr in data["response"]


@pytest.mark.parametrize("question,_expected", KNOWN_QUESTIONS)
def test_known_question_reports_the_question_it_matched(demo_graph, question, _expected):
    """Customers log this field to find out which entry answered."""
    result = demo_graph.invoke({"query": question, "session_id": "test-session"})
    assert result["matched_question"] == question


def test_an_exact_question_is_a_strong_match_not_a_hedged_one(demo_graph):
    """
    Verbatim questions should not come back with a "contact support" nudge
    attached — that reads as uncertainty about an answer we are certain of.
    """
    result = demo_graph.invoke(
        {"query": "What payment methods do you accept?", "session_id": "s"}
    )
    assert result["mode"] == "strong"
    assert "contact" not in result["response"].lower()
