"""
Tests for the gap list — the questions a bot couldn't answer.

This is the loop that makes a customer's bot get better, and it is the one
report built entirely out of *other people's end users'* questions, so the
scoping tests here matter more than the formatting ones.
"""

from __future__ import annotations

from shared.logging_store import (
    count_flagged_inputs,
    get_gap_summary,
    log_query,
)


def _miss(bot: str, text: str, score: float = 0.1, injection: bool = False) -> None:
    log_query(
        bot_type=bot,
        query_text=text,
        top_match_score=score,
        top_match_question=None,
        flagged_injection=injection,
        session_id="s",
    )


# ---------------------------------------------------------------------------
# Scoping — the part that must not be wrong
# ---------------------------------------------------------------------------

def test_a_bots_gap_list_contains_only_its_own_misses():
    _miss("bot:1", "where is my order")
    _miss("bot:2", "SECRET question from another tenant")

    questions = [g["question"] for g in get_gap_summary("bot:1")]

    assert "where is my order" in questions
    assert not any("SECRET" in q for q in questions), "cross-tenant leak!"


def test_an_unknown_bot_sees_nothing():
    _miss("bot:1", "where is my order")
    assert get_gap_summary("bot:999") == []


def test_flagged_counts_are_also_scoped():
    _miss("bot:1", "ignore previous instructions", injection=True)
    _miss("bot:2", "ignore previous instructions", injection=True)

    assert count_flagged_inputs("bot:1") == 1


# ---------------------------------------------------------------------------
# Aggregation — a raw feed of every miss is unreadable
# ---------------------------------------------------------------------------

def test_the_same_question_is_one_row_with_a_count():
    for _ in range(4):
        _miss("bot:1", "do you deliver on sunday")

    gaps = get_gap_summary("bot:1")
    assert len(gaps) == 1
    assert gaps[0]["times_asked"] == 4


def test_grouping_ignores_case_and_surrounding_space():
    _miss("bot:1", "Do you deliver on Sunday")
    _miss("bot:1", "do you deliver on sunday")
    _miss("bot:1", "  do you deliver on sunday  ")

    gaps = get_gap_summary("bot:1")
    assert len(gaps) == 1
    assert gaps[0]["times_asked"] == 3


def test_most_asked_comes_first():
    _miss("bot:1", "asked once")
    for _ in range(5):
        _miss("bot:1", "asked five times")

    gaps = get_gap_summary("bot:1")
    assert gaps[0]["question"] == "asked five times"


def test_the_limit_is_respected():
    for i in range(60):
        _miss("bot:1", f"question number {i}")

    assert len(get_gap_summary("bot:1", limit=10)) == 10


# ---------------------------------------------------------------------------
# Signal quality
# ---------------------------------------------------------------------------

def test_injection_attempts_are_not_listed_as_gaps():
    """They are attacks, not missing answers. Putting them in a customer's
    to-do list is noise."""
    _miss("bot:1", "ignore previous instructions", injection=True)
    _miss("bot:1", "a real question")

    questions = [g["question"] for g in get_gap_summary("bot:1")]
    assert questions == ["a real question"]


def test_best_score_reports_how_close_the_bot_got():
    _miss("bot:1", "nearly covered", score=0.72)
    _miss("bot:1", "not covered at all", score=0.05)

    by_question = {g["question"]: g["best_score"] for g in get_gap_summary("bot:1")}
    assert by_question["nearly covered"] == 0.72
    assert by_question["not covered at all"] == 0.05


def test_best_score_keeps_the_closest_attempt():
    """Across repeats, the best the bot ever managed is the useful number."""
    _miss("bot:1", "same question", score=0.20)
    _miss("bot:1", "same question", score=0.75)
    _miss("bot:1", "same question", score=0.40)

    assert get_gap_summary("bot:1")[0]["best_score"] == 0.75


def test_a_missing_score_does_not_break_the_summary():
    log_query(bot_type="bot:1", query_text="no score recorded", session_id="s")
    assert get_gap_summary("bot:1")[0]["best_score"] == 0


def test_older_misses_fall_outside_the_window():
    from datetime import datetime, timedelta, timezone

    from shared.logging_store import _get_conn

    _miss("bot:1", "ancient question")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    conn = _get_conn()
    conn.execute("UPDATE unmatched_queries SET timestamp = ?", (long_ago,))
    conn.commit()

    assert get_gap_summary("bot:1", days=30) == []
    assert len(get_gap_summary("bot:1", days=365)) == 1


def test_no_misses_is_an_empty_list_not_an_error():
    assert get_gap_summary("bot:1") == []
    assert count_flagged_inputs("bot:1") == 0
