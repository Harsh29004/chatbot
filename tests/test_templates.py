"""
Tests for the template catalogue, sheet ingestion, and — the point of the
whole feature — that a bot answers from its own sheet and refuses everything
else in its template's words.
"""

from __future__ import annotations

import csv
import io

import pytest

from apps.bot_engine import store
from apps.bot_engine.graph import BotConfig, build_graph
from apps.bot_engine.ingest import MAX_ROWS, SheetError, ingest_sheet
from apps.bot_engine.templates import (
    DEFAULT_TEMPLATE_ID,
    REQUIRED_SHEET_COLUMNS,
    TEMPLATES,
    get_template,
    list_templates,
    starter_sheet_rows,
)
from shared import api_keys as ak


@pytest.fixture(autouse=True)
def _bot_tables():
    store.init_bot_tables()
    yield
    if hasattr(store._LOCAL, "bots_conn"):
        del store._LOCAL.bots_conn


def _csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------

def test_there_are_ten_templates():
    assert len(TEMPLATES) == 10


def test_template_ids_are_unique():
    ids = [t.id for t in TEMPLATES]
    assert len(ids) == len(set(ids))


def test_default_template_exists():
    assert get_template(DEFAULT_TEMPLATE_ID) is not None


def test_unknown_template_returns_none():
    assert get_template("does-not-exist") is None


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_every_template_is_fully_specified(template):
    assert template.name and template.tagline and template.description
    assert template.scope_label
    assert template.sample_questions and template.starter_categories
    assert template.starter_rows, "a template without example rows can't seed a sheet"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_decline_message_names_what_the_bot_does_cover(template):
    """A refusal that doesn't say what it *can* answer just frustrates people."""
    assert "only" in template.decline_message.lower()
    assert len(template.decline_message) > 40


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_thresholds_are_ordered_and_sane(template):
    assert 0.0 < template.near_threshold < template.strong_threshold <= 1.0


def test_high_stakes_templates_are_stricter_than_the_rest():
    """A wrong answer about medication or a loan costs more than a haircut."""
    clinic = get_template("clinic")
    fintech = get_template("fintech")
    salon = get_template("services")

    assert clinic.strong_threshold > salon.strong_threshold
    assert fintech.strong_threshold > salon.strong_threshold
    assert clinic.near_threshold > salon.near_threshold


def test_clinic_refuses_to_give_medical_advice_in_its_decline_text():
    assert "medical advice" in get_template("clinic").decline_message.lower()


def test_public_payload_never_leaks_internal_patterns():
    """extra_action_patterns are our regexes, not customer-facing content."""
    for payload in list_templates():
        assert "extra_action_patterns" not in payload
        assert "starter_rows" not in payload


def test_public_payload_labels_strictness():
    by_id = {t["id"]: t for t in list_templates()}
    assert by_id["clinic"]["strictness"] == "strict"
    assert by_id["fintech"]["strictness"] == "strict"


# ---------------------------------------------------------------------------
# Starter sheets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_starter_sheet_has_the_documented_header(template):
    rows = starter_sheet_rows(template)
    assert rows[0] == ["Question", "Alt_Phrasings", "Category", "Answer"]
    assert len(rows) > 1


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_starter_sheet_can_be_ingested_as_is(template):
    """The sheet we hand out must be one our own importer accepts."""
    result = ingest_sheet(
        collection_name=f"test_starter_{template.id}",
        filename="starter.csv",
        data=_csv_bytes(starter_sheet_rows(template)),
    )
    assert result.documents_indexed == len(template.starter_rows)
    assert result.skipped_rows == 0


# ---------------------------------------------------------------------------
# Sheet ingestion
# ---------------------------------------------------------------------------

def test_minimal_sheet_needs_only_question_and_answer():
    data = _csv_bytes([["Question", "Answer"], ["Do you deliver?", "Yes, city-wide."]])
    result = ingest_sheet(collection_name="test_min", filename="a.csv", data=data)
    assert result.documents_indexed == 1


def test_missing_required_column_is_rejected_with_a_useful_message():
    data = _csv_bytes([["Question", "Reply"], ["Hi?", "Hello"]])
    with pytest.raises(SheetError) as exc:
        ingest_sheet(collection_name="test_bad", filename="a.csv", data=data)
    assert "Answer" in str(exc.value)


def test_column_names_are_matched_case_and_space_insensitively():
    data = _csv_bytes([["question", "ANSWER"], ["Do you deliver?", "Yes."]])
    result = ingest_sheet(collection_name="test_case", filename="a.csv", data=data)
    assert result.documents_indexed == 1


def test_rows_missing_a_question_or_answer_are_skipped_not_fatal():
    data = _csv_bytes(
        [
            ["Question", "Answer"],
            ["Real question?", "Real answer."],
            ["", "Orphan answer"],
            ["Orphan question?", ""],
        ]
    )
    result = ingest_sheet(collection_name="test_skip", filename="a.csv", data=data)
    assert result.documents_indexed == 1
    assert result.skipped_rows == 2


def test_duplicate_questions_are_dropped():
    data = _csv_bytes(
        [
            ["Question", "Answer"],
            ["Same question?", "First answer."],
            ["same QUESTION?", "Second answer."],
        ]
    )
    result = ingest_sheet(collection_name="test_dupe", filename="a.csv", data=data)
    assert result.documents_indexed == 1
    assert any("duplicate" in w.lower() for w in result.warnings)


def test_a_sheet_with_no_usable_rows_is_rejected():
    data = _csv_bytes([["Question", "Answer"], ["", ""]])
    with pytest.raises(SheetError):
        ingest_sheet(collection_name="test_empty", filename="a.csv", data=data)


def test_oversized_sheet_is_rejected():
    rows = [["Question", "Answer"]] + [[f"Q{i}?", f"A{i}"] for i in range(MAX_ROWS + 1)]
    with pytest.raises(SheetError) as exc:
        ingest_sheet(collection_name="test_big", filename="a.csv", data=_csv_bytes(rows))
    assert str(MAX_ROWS) in str(exc.value)


def test_unsupported_file_type_is_rejected():
    with pytest.raises(SheetError):
        ingest_sheet(collection_name="test_pdf", filename="faq.pdf", data=b"%PDF-1.4")


def test_categories_are_collected_for_the_dashboard():
    data = _csv_bytes(
        [
            ["Question", "Alt_Phrasings", "Category", "Answer"],
            ["Where is my order?", "tracking", "Orders", "Check My Orders."],
            ["How do I return?", "", "Returns", "Open a return request."],
        ]
    )
    result = ingest_sheet(collection_name="test_cats", filename="a.csv", data=data)
    assert result.categories == ["Orders", "Returns"]


def test_reupload_replaces_rather_than_merges():
    """The sheet is the source of truth, so removing a row must remove an answer."""
    first = _csv_bytes([["Question", "Answer"], ["Old question?", "Old answer."]])
    second = _csv_bytes([["Question", "Answer"], ["New question?", "New answer."]])

    ingest_sheet(collection_name="test_replace", filename="a.csv", data=first)
    result = ingest_sheet(collection_name="test_replace", filename="a.csv", data=second)

    assert result.documents_indexed == 1

    from shared.vector_store import get_collection

    questions = {
        m["question"]
        for m in get_collection("test_replace").get(include=["metadatas"])["metadatas"]
    }
    assert questions == {"New question?"}


# ---------------------------------------------------------------------------
# Scope: answers come from the sheet, everything else is refused
# ---------------------------------------------------------------------------

def _ready_bot(template_id: str, rows: list[list[str]], collection: str):
    template = get_template(template_id)
    ingest_sheet(collection_name=collection, filename="a.csv", data=_csv_bytes(rows))
    return template, build_graph(
        BotConfig(
            collection_name=collection,
            decline_message=template.decline_message,
            near_match_suffix=template.near_match_suffix,
            strong_threshold=template.strong_threshold,
            near_threshold=template.near_threshold,
            log_label=f"test:{template_id}",
            extra_action_patterns=template.extra_action_patterns,
        )
    )


def test_a_question_answered_by_the_sheet_is_returned_verbatim():
    rows = [
        ["Question", "Answer"],
        ["What are your delivery charges?", "Delivery is free above 499 rupees."],
    ]
    _, graph = _ready_bot("ecommerce", rows, "test_scope_hit")

    result = graph.invoke(
        {"query": "What are your delivery charges?", "session_id": "s1"}
    )
    assert result["mode"] == "strong"
    assert result["response"] == "Delivery is free above 499 rupees."


def test_asking_a_sheet_question_word_for_word_is_a_strong_match():
    """
    Regression guard. Phrasings are indexed as separate vectors; when they
    were concatenated into one, asking a question verbatim scored ~0.76 and
    was served as a weak "near" match with an unnecessary handoff attached.
    """
    rows = [
        ["Question", "Alt_Phrasings", "Category", "Answer"],
        [
            "What are your delivery charges?",
            "shipping cost; delivery fee",
            "Shipping",
            "Delivery is free above 499.",
        ],
    ]
    _, graph = _ready_bot("ecommerce", rows, "test_exact_match")

    result = graph.invoke(
        {"query": "What are your delivery charges?", "session_id": "s"}
    )
    assert result["mode"] == "strong"
    assert result["confidence"] > 0.95


def test_an_alternate_phrasing_also_matches_strongly():
    rows = [
        ["Question", "Alt_Phrasings", "Category", "Answer"],
        [
            "What are your delivery charges?",
            "shipping cost; delivery fee",
            "Shipping",
            "Delivery is free above 499.",
        ],
    ]
    _, graph = _ready_bot("ecommerce", rows, "test_alt_match")

    result = graph.invoke({"query": "shipping cost", "session_id": "s"})
    assert result["mode"] == "strong"
    assert result["response"] == "Delivery is free above 499."


def test_each_phrasing_becomes_its_own_vector():
    data = _csv_bytes(
        [
            ["Question", "Alt_Phrasings", "Answer"],
            ["Where is my order?", "track order; order status", "Check My Orders."],
        ]
    )
    result = ingest_sheet(collection_name="test_vectors", filename="a.csv", data=data)

    # One FAQ entry as far as the customer is concerned...
    assert result.documents_indexed == 1
    # ...but three searchable phrasings behind it.
    assert result.vectors_indexed == 3


def test_a_question_outside_the_sheet_is_declined_in_the_templates_words():
    rows = [
        ["Question", "Answer"],
        ["What are your delivery charges?", "Delivery is free above 499 rupees."],
    ]
    template, graph = _ready_bot("ecommerce", rows, "test_scope_miss")

    result = graph.invoke(
        {"query": "Who won the cricket world cup in 1983?", "session_id": "s1"}
    )
    assert result["mode"] == "decline"
    assert result["response"] == template.decline_message


def test_the_decline_message_follows_the_chosen_template():
    """Same off-topic question, two templates, two different refusals."""
    rows = [["Question", "Answer"], ["What are your timings?", "9 to 5."]]

    shop_template, shop = _ready_bot("ecommerce", rows, "test_voice_shop")
    clinic_template, clinic = _ready_bot("clinic", rows, "test_voice_clinic")

    off_topic = {"query": "What is the capital of France?", "session_id": "s"}
    shop_reply = shop.invoke(off_topic)["response"]
    clinic_reply = clinic.invoke(off_topic)["response"]

    assert shop_reply == shop_template.decline_message
    assert clinic_reply == clinic_template.decline_message
    assert shop_reply != clinic_reply


def test_template_specific_action_requests_are_refused_before_retrieval():
    """The clinic bot explains appointments; it must never appear to book one."""
    rows = [
        ["Question", "Answer"],
        ["How do I book an appointment?", "Book from the Appointments page."],
    ]
    template, graph = _ready_bot("clinic", rows, "test_action_clinic")

    result = graph.invoke({"query": "book me an appointment for tomorrow", "session_id": "s"})
    assert result["mode"] == "decline"
    assert result["response"] == template.decline_message


def test_fintech_refuses_investment_advice_even_with_a_matching_sheet():
    rows = [
        ["Question", "Answer"],
        ["Which fund should I pick?", "We offer three fund categories."],
    ]
    template, graph = _ready_bot("fintech", rows, "test_action_fintech")

    result = graph.invoke({"query": "which fund should I invest in?", "session_id": "s"})
    assert result["mode"] == "decline"
    assert result["response"] == template.decline_message


# ---------------------------------------------------------------------------
# Bot records
# ---------------------------------------------------------------------------

def test_an_account_gets_exactly_one_bot():
    ak.set_daily_credit_limit("bot@example.com", None, name="Bot Co")
    user = ak.get_user_by_email("bot@example.com")

    first = store.get_or_create_bot(user["id"])
    second = store.get_or_create_bot(user["id"])
    assert first["id"] == second["id"]


def test_a_new_bot_starts_as_a_draft_and_cannot_answer_yet():
    ak.set_daily_credit_limit("draft@example.com", None, name="Draft")
    user = ak.get_user_by_email("draft@example.com")

    bot = store.get_or_create_bot(user["id"])
    assert bot["status"] == store.STATUS_DRAFT
    assert bot["doc_count"] == 0


def test_each_bot_gets_its_own_collection():
    ak.set_daily_credit_limit("a@example.com", None, name="A")
    ak.set_daily_credit_limit("b@example.com", None, name="B")
    bot_a = store.get_or_create_bot(ak.get_user_by_email("a@example.com")["id"])
    bot_b = store.get_or_create_bot(ak.get_user_by_email("b@example.com")["id"])

    assert bot_a["collection_name"] != bot_b["collection_name"]


def test_switching_template_keeps_the_indexed_sheet():
    """Template governs scope and wording; the sheet governs facts."""
    ak.set_daily_credit_limit("switch@example.com", None, name="S")
    user = ak.get_user_by_email("switch@example.com")

    store.get_or_create_bot(user["id"])
    store.record_sheet(user["id"], filename="faq.csv", doc_count=12, categories=["Orders"])

    switched = store.set_template(user["id"], "restaurant")

    assert switched["template_id"] == "restaurant"
    assert switched["status"] == store.STATUS_READY
    assert switched["doc_count"] == 12


def test_recording_a_sheet_marks_the_bot_ready():
    ak.set_daily_credit_limit("ready@example.com", None, name="R")
    user = ak.get_user_by_email("ready@example.com")
    store.get_or_create_bot(user["id"])

    bot = store.record_sheet(
        user["id"], filename="faq.xlsx", doc_count=40, categories=["A", "B"]
    )
    assert bot["status"] == store.STATUS_READY
    assert bot["doc_count"] == 40
    assert bot["sheet_filename"] == "faq.xlsx"


def test_required_columns_are_the_two_we_document():
    assert REQUIRED_SHEET_COLUMNS == ("Question", "Answer")
