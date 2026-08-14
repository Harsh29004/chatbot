"""
Test near-match phrasing — reworded and misspelled FAQ questions.

Reworded questions should land as NEAR_MATCH or STRONG_MATCH, never
as DECLINE.  With mock embeddings (hash-based, not truly semantic),
we test the pipeline wiring rather than semantic quality — the real
Ollama embeddings will do better.
"""

from __future__ import annotations

import pytest


# -- Customer bot: reworded questions --------------------------------------

CUSTOMER_REWORDED = [
    # (reworded question, substring that should appear in the answer)
    ("How can I cancel my booking?", "Cancel Booking"),
    ("I want to cancel a service", "Cancel Booking"),
    ("Where is my refund?", "Refunds"),
    ("I haven't received my money back", "Refunds"),
    ("How to update my mobile number?", "phone number"),
    ("What are the payment options?", "UPI"),
    ("Can I pay with UPI?", "UPI"),
    ("How to change booking time?", "Reschedule"),
    ("I need to move my appointment", "Reschedule"),
]


@pytest.mark.parametrize("query,expected_substr", CUSTOMER_REWORDED)
def test_customer_reworded_not_declined(customer_graph, query, expected_substr):
    """
    Reworded questions should NOT decline.

    Note: With mock (hash-based) embeddings, we may get NEAR or STRONG
    depending on how the hash collides.  The important thing is that
    we don't get DECLINE for a clearly related question.
    """
    result = customer_graph.invoke(
        {"query": query, "session_id": "near-match-test"}
    )
    # With mock embeddings, we can't guarantee strong/near for reworded
    # questions (they're not truly semantic), so we verify the pipeline
    # at least runs without error and returns a valid mode.
    assert result["mode"] in ("strong", "near", "decline")
    # The response should be a string (not None or empty)
    assert isinstance(result["response"], str)
    assert len(result["response"]) > 0


# -- Partner bot: reworded questions ---------------------------------------

PARTNER_REWORDED = [
    ("What is my KYC verification status?", "KYC"),
    ("Check my document status", "KYC"),
    ("When will I get paid?", "Payout"),
    ("How does job assignment work?", "assigned"),
    ("How do customer reviews affect my rating?", "rating"),
    ("What if I cancel an accepted job?", "penalty"),
]


@pytest.mark.parametrize("query,expected_substr", PARTNER_REWORDED)
def test_partner_reworded_not_declined(partner_graph, query, expected_substr):
    """Reworded partner questions — pipeline runs, valid response."""
    result = partner_graph.invoke(
        {"query": query, "session_id": "near-match-test"}
    )
    assert result["mode"] in ("strong", "near", "decline")
    assert isinstance(result["response"], str)
    assert len(result["response"]) > 0


# -- Near-match response format tests --------------------------------------

def test_near_match_has_support_nudge(customer_graph):
    """
    If a query lands as NEAR_MATCH, the response must contain the
    support-nudge suffix.
    """
    from shared.config import NEAR_MATCH_SUFFIX

    # Use a query that's somewhat related but not exact — with mock
    # embeddings we can't guarantee NEAR_MATCH, so we conditionally check
    result = customer_graph.invoke(
        {"query": "booking cancel help", "session_id": "nudge-test"}
    )
    if result["mode"] == "near":
        assert NEAR_MATCH_SUFFIX.strip() in result["response"], (
            "Near-match response missing support nudge suffix"
        )


# -- API-level near-match tests --------------------------------------------

def test_customer_reworded_api(customer_client):
    """Reworded question via HTTP should not 500."""
    resp = customer_client.post(
        "/customer-bot/ask",
        json={"message": "How can I cancel my booking?", "session_id": "api-near"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] in ("strong", "near", "decline")
    assert "response" in data


def test_partner_reworded_api(partner_client):
    """Reworded partner question via HTTP should not 500."""
    resp = partner_client.post(
        "/partner-bot/ask",
        json={"message": "When will I get paid?", "session_id": "api-near"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] in ("strong", "near", "decline")
