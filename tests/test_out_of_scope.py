"""
Out-of-scope questions.

Anything the customer's sheet doesn't cover must return the template's
decline message, be marked ``decline``, and be logged so the customer can
see what their sheet is missing. This is the boundary the whole product is
sold on.
"""

from __future__ import annotations

import csv
import io

import pytest

from bot.graph import BotConfig, build_graph
from bot.ingest import ingest_sheet
from bot.templates import get_template
from backend.shared.logging_store import get_all_logs

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
def test_out_of_scope_declines(demo_graph, demo_template, query):
    """Out-of-scope queries decline rather than reaching for something close."""
    result = demo_graph.invoke({"query": query, "session_id": "oos-test"})

    assert result["mode"] == "decline", (
        f"Expected decline for '{query}', got '{result['mode']}'"
    )
    assert result["response"] == demo_template.decline_message
    assert result["matched_question"] is None


def test_the_decline_message_is_the_templates_own_words(demo_graph, demo_template):
    """Swap the template and the refusal changes — that is the point of them."""
    result = demo_graph.invoke({"query": "Tell me a joke", "session_id": "s"})
    assert result["response"] == demo_template.decline_message
    assert demo_template.scope_label in result["response"]


# -- Logging tests ---------------------------------------------------------

def test_out_of_scope_is_logged(demo_graph):
    """Declines are the customer's gap list — they have to be recorded."""
    demo_graph.invoke({"query": "What's the weather today?", "session_id": "log-test"})

    logs = get_all_logs()
    assert len(logs) >= 1
    assert "weather" in logs[-1]["query_text"].lower()


# -- Action-intent tests (decline regardless of topic match) ---------------

ACTION_REQUESTS = [
    "Refund me 5000 rupees right now",
    "Cancel my order immediately",
    "Change my phone number to 9876543210",
    "Update my account email please",
    "Delete my account permanently",
    "Please refund the payment",
    "Process my cancellation",
]


@pytest.mark.parametrize("query", ACTION_REQUESTS)
def test_action_requests_are_declined(demo_graph, demo_template, query):
    """
    The bot explains how to do things; it must never look like it did them.

    These all sit close to real sheet entries, so retrieval alone would
    happily answer them — the guardrail is what stops it.
    """
    result = demo_graph.invoke({"query": query, "session_id": "action-test"})

    assert result["mode"] == "decline", (
        f"Expected decline for action request '{query}', got '{result['mode']}'"
    )
    assert result["response"] == demo_template.decline_message


def test_how_do_i_questions_are_still_answered(demo_graph):
    """"How do I cancel" is a request for instructions, not for us to cancel."""
    result = demo_graph.invoke(
        {"query": "How do I cancel an order?", "session_id": "instructional"}
    )
    assert result["mode"] != "decline"


# -- API endpoint tests ----------------------------------------------------

def test_out_of_scope_via_api(api_client, demo_template):
    """Out of scope is a successful request with a refusal in it, not an error."""
    resp = api_client.post(
        "/v1/ask",
        json={"message": "What's the weather today?", "session_id": "api-oos"},
        headers={"X-Api-Key": "test-key"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "decline"
    assert data["response"] == demo_template.decline_message


# -- Tenant isolation ------------------------------------------------------

def test_one_customers_bot_cannot_answer_from_anothers_sheet(demo_graph):
    """
    Every bot has its own collection. A question that only another tenant's
    sheet could answer must decline here, not leak across.
    """
    other_rows = [
        ["Question", "Answer"],
        [
            "How do I check my KYC status?",
            "SECRET-TENANT-B-ANSWER: open the Compliance tab.",
        ],
    ]
    buffer = io.StringIO()
    csv.writer(buffer).writerows(other_rows)
    ingest_sheet(
        collection_name="tenant_b_index",
        filename="b.csv",
        data=buffer.getvalue().encode(),
    )

    result = demo_graph.invoke(
        {"query": "How do I check my KYC status?", "session_id": "cross-tenant"}
    )

    assert "SECRET-TENANT-B-ANSWER" not in result["response"], (
        "One tenant's bot surfaced another tenant's answer!"
    )


def test_two_bots_on_different_collections_answer_independently():
    template = get_template("ecommerce")

    def make(collection: str, answer: str):
        buffer = io.StringIO()
        csv.writer(buffer).writerows(
            [["Question", "Answer"], ["What is your return window?", answer]]
        )
        ingest_sheet(
            collection_name=collection, filename="x.csv", data=buffer.getvalue().encode()
        )
        return build_graph(
            BotConfig(
                collection_name=collection,
                decline_message=template.decline_message,
                near_match_suffix=template.near_match_suffix,
                strong_threshold=template.strong_threshold,
                near_threshold=template.near_threshold,
            )
        )

    shop_a = make("tenant_a_index", "Returns accepted for 7 days.")
    shop_b = make("tenant_c_index", "Returns accepted for 30 days.")

    question = {"query": "What is your return window?", "session_id": "s"}
    assert "7 days" in shop_a.invoke(question)["response"]
    assert "30 days" in shop_b.invoke(question)["response"]
