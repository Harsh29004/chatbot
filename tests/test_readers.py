"""
Tests for the universal importer.

The promise this module makes is "upload what you already have", so the tests
are one per shape a customer's data actually arrives in — not one per branch.
Every case asserts the same thing: two FAQ entries come out with the question
and answer intact, however they went in.
"""

from __future__ import annotations

import io
import json
import zipfile

import pandas as pd
import pytest

from bot.readers import SheetError, load_table


def pairs(frame: pd.DataFrame) -> list[tuple[str, str]]:
    """The (question, answer) rows a frame carries, for comparison."""
    return [
        (str(row["Question"]).strip(), str(row["Answer"]).strip())
        for _, row in frame.iterrows()
    ]


EXPECTED = [("Do you deliver?", "Yes, city-wide."), ("Refunds?", "Within 30 days.")]


def _jsonl(records: list[dict]) -> bytes:
    return "\n".join(json.dumps(r) for r in records).encode("utf-8")


# ---------------------------------------------------------------------------
# Every format we advertise
# ---------------------------------------------------------------------------

CASES: dict[str, tuple[str, bytes]] = {
    "csv": (
        "faq.csv",
        b"Question,Answer\nDo you deliver?,\"Yes, city-wide.\"\nRefunds?,Within 30 days.\n",
    ),
    "semicolon csv": (
        "faq.csv",
        "Question;Answer\nDo you deliver?;Yes, city-wide.\nRefunds?;Within 30 days.\n".encode(),
    ),
    "tsv": (
        "faq.tsv",
        "Question\tAnswer\nDo you deliver?\tYes, city-wide.\nRefunds?\tWithin 30 days.\n".encode(),
    ),
    "headerless csv": (
        "faq.csv",
        b"Do you deliver?,\"Yes, city-wide.\"\nRefunds?,Within 30 days.\n",
    ),
    "json list": (
        "faq.json",
        json.dumps(
            [
                {"question": "Do you deliver?", "answer": "Yes, city-wide."},
                {"question": "Refunds?", "answer": "Within 30 days."},
            ]
        ).encode(),
    ),
    "json wrapped in a container key": (
        "faq.json",
        json.dumps(
            {"faqs": [
                {"q": "Do you deliver?", "a": "Yes, city-wide."},
                {"q": "Refunds?", "a": "Within 30 days."},
            ]}
        ).encode(),
    ),
    "json question -> answer mapping": (
        "faq.json",
        json.dumps(
            {"Do you deliver?": "Yes, city-wide.", "Refunds?": "Within 30 days."}
        ).encode(),
    ),
    "json list of pairs": (
        "faq.json",
        json.dumps(
            [["Do you deliver?", "Yes, city-wide."], ["Refunds?", "Within 30 days."]]
        ).encode(),
    ),
    "jsonl": (
        "faq.jsonl",
        _jsonl(
            [
                {"prompt": "Do you deliver?", "completion": "Yes, city-wide."},
                {"prompt": "Refunds?", "completion": "Within 30 days."},
            ]
        ),
    ),
    "jsonl chat transcripts": (
        "chat.jsonl",
        _jsonl(
            [
                {"messages": [
                    {"role": "user", "content": "Do you deliver?"},
                    {"role": "assistant", "content": "Yes, city-wide."},
                ]},
                {"messages": [
                    {"role": "user", "content": "Refunds?"},
                    {"role": "assistant", "content": "Within 30 days."},
                ]},
            ]
        ),
    ),
    "yaml": (
        "faq.yaml",
        b"faqs:\n  - question: Do you deliver?\n    answer: Yes, city-wide.\n"
        b"  - question: Refunds?\n    answer: Within 30 days.\n",
    ),
    "xml": (
        "faq.xml",
        b"<faqs>"
        b"<entry><question>Do you deliver?</question><answer>Yes, city-wide.</answer></entry>"
        b"<entry><question>Refunds?</question><answer>Within 30 days.</answer></entry>"
        b"</faqs>",
    ),
    "html headings": (
        "faq.html",
        b"<html><body><h2>Do you deliver?</h2><p>Yes, city-wide.</p>"
        b"<h2>Refunds?</h2><p>Within 30 days.</p></body></html>",
    ),
    "markdown headings": (
        "faq.md",
        b"# FAQ\n\n## Do you deliver?\n\nYes, city-wide.\n\n## Refunds?\n\nWithin 30 days.\n",
    ),
    "markdown table": (
        "faq.md",
        b"| Question | Answer |\n|---|---|\n| Do you deliver? | Yes, city-wide. |\n"
        b"| Refunds? | Within 30 days. |\n",
    ),
    "text with Q/A labels": (
        "faq.txt",
        b"Q: Do you deliver?\nA: Yes, city-wide.\n\nQ: Refunds?\nA: Within 30 days.\n",
    ),
    "text with question marks": (
        "faq.txt",
        b"Do you deliver?\nYes, city-wide.\n\nRefunds?\nWithin 30 days.\n",
    ),
    "text with an inline separator": (
        "faq.txt",
        b"Do you deliver? :: Yes, city-wide.\nRefunds? :: Within 30 days.\n",
    ),
}


@pytest.mark.parametrize("filename,data", CASES.values(), ids=list(CASES))
def test_every_advertised_format_yields_the_same_faq(filename, data):
    assert pairs(load_table(filename, data)) == EXPECTED


def _workbook(tabs: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in tabs.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return buffer.getvalue()


def test_a_workbook_is_read_across_all_of_its_tabs():
    """FAQs are kept one tab per team; a tab of notes must not break the import."""
    data = _workbook({
        "Orders": pd.DataFrame({"Question": ["Do you deliver?"], "Answer": ["Yes, city-wide."]}),
        "Billing": pd.DataFrame({"Question": ["Refunds?"], "Answer": ["Within 30 days."]}),
        "Notes": pd.DataFrame({"note": ["internal, ignore"]}),
    })
    assert pairs(load_table("faq.xlsx", data)) == EXPECTED


def test_a_zip_imports_every_readable_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("orders.csv", 'Question,Answer\nDo you deliver?,"Yes, city-wide."\n')
        archive.writestr("billing.json", json.dumps([{"q": "Refunds?", "a": "Within 30 days."}]))
        archive.writestr("logo.png", b"\x89PNG not an faq")
    assert pairs(load_table("export.zip", buffer.getvalue())) == EXPECTED


# ---------------------------------------------------------------------------
# Naming and sniffing
# ---------------------------------------------------------------------------

def test_column_aliases_are_recognised():
    data = b"prompt,completion,tags,synonyms\nDo you deliver?,Yes.,Orders,ship;delivery\n"
    frame = load_table("faq.csv", data)
    row = frame.iloc[0]
    assert row["Question"] == "Do you deliver?"
    assert row["Answer"] == "Yes."
    assert row["Category"] == "Orders"
    assert row["Alt_Phrasings"] == "ship;delivery"


def test_the_real_question_column_wins_over_a_weaker_alias():
    data = b"Title,Question,Answer\nOrders FAQ,Do you deliver?,Yes.\n"
    assert load_table("faq.csv", data).iloc[0]["Question"] == "Do you deliver?"


def test_a_list_cell_becomes_semicolon_separated_alt_phrasings():
    """Downstream splits Alt_Phrasings on ";", so a JSON array has to join on it."""
    data = json.dumps([{
        "question": "Do you deliver?",
        "answer": "Yes.",
        "alt_phrasings": ["shipping?", "delivery?"],
    }]).encode()
    assert load_table("faq.json", data).iloc[0]["Alt_Phrasings"] == "shipping?; delivery?"


@pytest.mark.parametrize(
    "data",
    [
        b'[{"question": "Do you deliver?", "answer": "Yes, city-wide."}]',
        b"Q: Do you deliver?\nA: Yes, city-wide.\n",
        b'Question,Answer\nDo you deliver?,"Yes, city-wide."\n',
    ],
    ids=["json", "labelled text", "csv"],
)
def test_a_file_with_no_extension_is_identified_from_its_bytes(data):
    assert pairs(load_table("export", data))[0] == EXPECTED[0]


def test_a_wrong_extension_does_not_beat_the_bytes():
    """`.dat` tells us nothing, so the content decides."""
    data = b'{"Do you deliver?": "Yes, city-wide."}'
    assert pairs(load_table("faq.dat", data))[0] == EXPECTED[0]


def test_utf16_is_decoded_rather_than_rejected():
    data = 'Question,Answer\nDo you deliver?,"Yes, city-wide."\n'.encode("utf-16")
    assert load_table("faq.csv", data).iloc[0]["Question"] == "Do you deliver?"


# ---------------------------------------------------------------------------
# Failure is a sentence, never a traceback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,data",
    [
        ("faq.csv", b""),
        ("faq.txt", b"nothing here that reads as a question"),
        ("faq.json", b"{not json at all"),
        ("faq.xlsx", b"not really a workbook"),
        ("export.zip", b"PK\x03\x04 truncated"),
    ],
    ids=["empty", "prose with no pairs", "broken json", "broken workbook", "broken zip"],
)
def test_unreadable_files_raise_a_showable_message(filename, data):
    with pytest.raises(SheetError) as exc:
        load_table(filename, data)
    assert str(exc.value)
    assert "Traceback" not in str(exc.value)


# ---------------------------------------------------------------------------
# End to end: the widest input a customer can hand the upload endpoint
# ---------------------------------------------------------------------------

def test_a_jsonl_upload_reaches_the_bot_through_the_api(demo_bot):
    """
    The point of the importer, exercised the way a customer meets it: a chat
    export goes in through ``POST /api/bot/sheet`` and comes back out of the
    bot as its own answer, verbatim.
    """
    from fastapi.testclient import TestClient

    from backend.billing.router import current_customer
    from backend.server import app

    app.dependency_overrides[current_customer] = lambda: {
        "id": demo_bot["user_id"],
        "email": "demo@example.test",
    }
    try:
        with TestClient(app) as client:
            upload = client.post(
                "/api/bot/sheet",
                files={"file": ("chat.jsonl", _jsonl([
                    {"messages": [
                        {"role": "user", "content": "Do you deliver?"},
                        {"role": "assistant", "content": "Yes, city-wide by 6pm."},
                    ]},
                ]), "application/json")},
            )
            assert upload.status_code == 200, upload.text
            body = upload.json()
            assert body["documents_indexed"] == 1
            assert ".jsonl" in body["bot"]["accepted_formats"]

            answered = client.post(
                "/api/bot/preview",
                json={"message": "Do you deliver?", "session_id": "test-session"},
            )
            assert answered.status_code == 200, answered.text
            replied = answered.json()
            assert replied["mode"] == "strong"
            assert "Yes, city-wide by 6pm." in replied["response"]
    finally:
        app.dependency_overrides.clear()
