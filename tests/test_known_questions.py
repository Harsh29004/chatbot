"""
Test known questions — exact wording from the FAQ sheet.

Every exact question from the sheet should hit the retrieval pipeline
and return a response (strong or near, depending on mock embedding
fidelity).  The key assertion is that the *correct answer text* is
returned and the mode is NOT "decline".
"""

from __future__ import annotations

import pytest


# -- Customer bot: exact questions ----------------------------------------

CUSTOMER_QUESTIONS = [
    (
        "How do I cancel a booking?",
        "Cancel Booking",  # substring of the expected answer
    ),
    (
        "How do I get a refund?",
        "Refunds are processed",
    ),
    (
        "How do I change my phone number?",
        "Edit Profile",
    ),
    (
        "What payment methods do you accept?",
        "UPI",
    ),
    (
        "How do I reschedule a booking?",
        "Reschedule",
    ),
]


@pytest.mark.parametrize("question,expected_substr", CUSTOMER_QUESTIONS)
def test_customer_known_question_via_graph(
    customer_graph, question, expected_substr
):
    """Exact FAQ questions should return matching answers, not decline."""
    result = customer_graph.invoke(
        {"query": question, "session_id": "test-session"}
    )
    assert result["mode"] in ("strong", "near"), (
        f"Expected strong/near for '{question}', got '{result['mode']}'"
    )
    assert expected_substr in result["response"], (
        f"Expected '{expected_substr}' in response for '{question}'"
    )


# -- Partner bot: exact questions ------------------------------------------

PARTNER_QUESTIONS = [
    (
        "How do I check my KYC status?",
        "KYC Status",
    ),
    (
        "When do I receive my payout?",
        "Payouts are processed",
    ),
    (
        "How are jobs assigned to me?",
        "proximity",
    ),
    (
        "How is my rating calculated?",
        "rolling average",
    ),
    (
        "What happens if I cancel a job after accepting?",
        "penalty",
    ),
]


@pytest.mark.parametrize("question,expected_substr", PARTNER_QUESTIONS)
def test_partner_known_question_via_graph(
    partner_graph, question, expected_substr
):
    """Exact FAQ questions should return matching answers, not decline."""
    result = partner_graph.invoke(
        {"query": question, "session_id": "test-session"}
    )
    assert result["mode"] in ("strong", "near"), (
        f"Expected strong/near for '{question}', got '{result['mode']}'"
    )
    assert expected_substr in result["response"], (
        f"Expected '{expected_substr}' in response for '{question}'"
    )


# -- API endpoint tests ----------------------------------------------------

@pytest.mark.parametrize("question,expected_substr", CUSTOMER_QUESTIONS)
def test_customer_api_known_question(customer_client, question, expected_substr):
    """Test via the actual HTTP endpoint."""
    resp = customer_client.post(
        "/customer-bot/ask",
        json={"message": question, "session_id": "api-test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] in ("strong", "near")
    assert expected_substr in data["response"]


@pytest.mark.parametrize("question,expected_substr", PARTNER_QUESTIONS)
def test_partner_api_known_question(partner_client, question, expected_substr):
    """Test via the actual HTTP endpoint."""
    resp = partner_client.post(
        "/partner-bot/ask",
        json={"message": question, "session_id": "api-test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] in ("strong", "near")
    assert expected_substr in data["response"]
