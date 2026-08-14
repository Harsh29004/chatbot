"""
Test out-of-scope questions.

Completely unrelated questions should:
1. Return the fixed decline message
2. Have mode = "decline"
3. Be logged to the unmatched_queries SQLite table
"""

from __future__ import annotations

import pytest

from shared.config import CUSTOMER_DECLINE_MESSAGE, PARTNER_DECLINE_MESSAGE
from shared.logging_store import get_all_logs


OUT_OF_SCOPE_QUERIES = [
    "What's the weather today?",
    "Tell me a joke",
    "Who is the president of the United States?",
    "What is the capital of France?",
    "Can you write me a Python script?",
    "Explain quantum mechanics",
    "What's 2 + 2?",
]


# -- Graph-level tests -----------------------------------------------------

@pytest.mark.parametrize("query", OUT_OF_SCOPE_QUERIES)
def test_customer_out_of_scope(customer_graph, query):
    """Out-of-scope queries should decline, not attempt to be helpful."""
    result = customer_graph.invoke(
        {"query": query, "session_id": "oos-test"}
    )
    assert result["mode"] == "decline", (
        f"Expected decline for '{query}', got '{result['mode']}'"
    )
    assert result["response"] == CUSTOMER_DECLINE_MESSAGE
    assert result["matched_question"] is None


@pytest.mark.parametrize("query", OUT_OF_SCOPE_QUERIES)
def test_partner_out_of_scope(partner_graph, query):
    """Out-of-scope queries on partner bot should use partner decline msg."""
    result = partner_graph.invoke(
        {"query": query, "session_id": "oos-test"}
    )
    assert result["mode"] == "decline"
    assert result["response"] == PARTNER_DECLINE_MESSAGE


# -- Logging tests ---------------------------------------------------------

def test_out_of_scope_logged(customer_graph):
    """Declined queries must be logged to the unmatched_queries table."""
    customer_graph.invoke(
        {"query": "What's the weather today?", "session_id": "log-test"}
    )
    logs = get_all_logs()
    assert len(logs) >= 1
    latest = logs[-1]
    assert latest["bot_type"] == "customer"
    assert "weather" in latest["query_text"].lower()


# -- Action-intent tests (decline regardless of topic match) ---------------

ACTION_REQUESTS = [
    "Refund me ₹5000 right now",
    "Cancel my booking immediately",
    "Change my phone number to 9876543210",
    "Update my account email please",
    "Delete my account permanently",
    "Please refund the payment",
    "Process my cancellation",
]


@pytest.mark.parametrize("query", ACTION_REQUESTS)
def test_action_intent_declined(customer_graph, query):
    """Action requests should be declined regardless of topic similarity."""
    result = customer_graph.invoke(
        {"query": query, "session_id": "action-test"}
    )
    assert result["mode"] == "decline", (
        f"Expected decline for action request '{query}', got '{result['mode']}'"
    )
    assert result["response"] == CUSTOMER_DECLINE_MESSAGE


# -- API endpoint tests ----------------------------------------------------

def test_out_of_scope_api(customer_client):
    """Out-of-scope via HTTP should return 200 with decline mode."""
    resp = customer_client.post(
        "/customer-bot/ask",
        json={"message": "What's the weather today?", "session_id": "api-oos"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "decline"
    assert data["response"] == CUSTOMER_DECLINE_MESSAGE


# -- Cross-bot isolation tests ---------------------------------------------

def test_customer_cannot_answer_partner_question(customer_graph):
    """
    Customer bot should NOT be able to surface partner-only FAQ answers.
    Asking about KYC status (a partner topic) should decline.
    """
    result = customer_graph.invoke(
        {"query": "How do I check my KYC status?", "session_id": "cross-test"}
    )
    # With mock embeddings, KYC content is only in partner collection
    # This should decline or at best near-match to an unrelated customer FAQ
    # The key: it must NOT return the partner's KYC answer
    if result["mode"] != "decline":
        assert "KYC Status" not in result["response"], (
            "Customer bot returned partner-specific KYC answer!"
        )


def test_partner_cannot_answer_customer_question(partner_graph):
    """
    Partner bot should NOT surface customer-only FAQ answers.
    """
    result = partner_graph.invoke(
        {"query": "How do I reschedule a booking?", "session_id": "cross-test"}
    )
    if result["mode"] != "decline":
        assert "Reschedule" not in result["response"], (
            "Partner bot returned customer-specific booking answer!"
        )
