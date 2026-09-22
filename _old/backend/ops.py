"""
The operational snapshot the owner assistant reasons over.

This is the deliberate exception to tenant isolation. Everywhere else in this
codebase, a query is scoped to one account and crossing that line is the bug.
Here crossing it is the point: "which bots are struggling this week?" cannot
be answered one tenant at a time.

Two things keep that safe rather than reckless:

* it is reachable only behind ``verify_owner_key`` — an ``nxo_`` key, minted
  locally by a script with no HTTP surface, and
* the snapshot carries **aggregates and question text, never identities**.
  Bots appear as ids and templates, not as customer names or emails. An owner
  looking for operational problems does not need to know whose bot it is to
  find them, and the model certainly doesn't.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.shared.logging_store import count_flagged_inputs, get_gap_summary
from bot import store
from bot.catalogue import get_template

# The model gets a bounded slice, not the whole database. A prompt that grows
# with the tenant count would eventually blow the context window and start
# silently truncating the most important rows.
MAX_BOTS_DETAILED = 40
MAX_GAPS_PER_BOT = 8
MAX_GAPS_OVERALL = 30


def build_snapshot(*, days: int = 7) -> dict[str, Any]:
    """
    Gather cross-tenant operational state for the last *days* days.

    Returned as plain data so the caller can hand it to a model, serve it as
    JSON, or assert against it in a test without any of those coupling to the
    other two.
    """
    bots = store.list_all_bots()

    bot_rows: list[dict[str, Any]] = []
    all_gaps: list[dict[str, Any]] = []

    for bot in bots[:MAX_BOTS_DETAILED]:
        label = f"bot:{bot['id']}"
        template = get_template(bot["template_id"])
        gaps = get_gap_summary(label, days=days, limit=MAX_GAPS_PER_BOT)
        flagged = count_flagged_inputs(label, days=days)

        near_threshold = template.near_threshold if template else 0.60
        nearly = sum(1 for gap in gaps if gap["best_score"] >= near_threshold)

        bot_rows.append({
            "bot_id": bot["id"],
            "template": bot["template_id"],
            "status": bot["status"],
            "sheet_rows_indexed": bot["doc_count"],
            "grounded_rewording": bool(bot.get("llm_enabled", 0)),
            "distinct_unanswered": len(gaps),
            "needs_phrasing": nearly,
            "not_covered": len(gaps) - nearly,
            "flagged_inputs": flagged,
        })

        for gap in gaps:
            all_gaps.append({
                "bot_id": bot["id"],
                "template": bot["template_id"],
                "question": gap["question"],
                "times_asked": gap["times_asked"],
                "best_score": round(gap["best_score"], 3),
                "verdict": "needs phrasing" if gap["best_score"] >= near_threshold else "not covered",
            })

    all_gaps.sort(key=lambda gap: gap["times_asked"], reverse=True)

    ready = [row for row in bot_rows if row["status"] == store.STATUS_READY]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "totals": {
            "bots": len(bots),
            "bots_ready": len(ready),
            "bots_draft": len(bots) - len(ready),
            "bots_with_rewording_on": sum(1 for row in bot_rows if row["grounded_rewording"]),
            "distinct_unanswered_questions": len(all_gaps),
            "flagged_inputs": sum(row["flagged_inputs"] for row in bot_rows),
        },
        "bots": bot_rows,
        "top_gaps": all_gaps[:MAX_GAPS_OVERALL],
    }


def render_snapshot(snapshot: dict[str, Any]) -> str:
    """
    Flatten the snapshot into compact text for a model prompt.

    Deliberately terse and tabular rather than JSON: a small local model reads
    a short table far more reliably than nested braces, and every token spent
    on punctuation is one not spent on the actual rows.
    """
    totals = snapshot["totals"]
    lines = [
        f"WINDOW: last {snapshot['window_days']} days (as of {snapshot['generated_at']})",
        "",
        "PLATFORM TOTALS",
        f"  bots: {totals['bots']} ({totals['bots_ready']} ready, {totals['bots_draft']} still draft)",
        f"  bots with grounded rewording on: {totals['bots_with_rewording_on']}",
        f"  distinct unanswered questions: {totals['distinct_unanswered_questions']}",
        f"  inputs flagged by the injection detector: {totals['flagged_inputs']}",
        "",
        "PER BOT",
        "  id | template | status | rows | unanswered | needs-phrasing | not-covered | flagged",
    ]

    for row in snapshot["bots"]:
        lines.append(
            f"  {row['bot_id']} | {row['template']} | {row['status']} | "
            f"{row['sheet_rows_indexed']} | {row['distinct_unanswered']} | "
            f"{row['needs_phrasing']} | {row['not_covered']} | {row['flagged_inputs']}"
        )

    lines += ["", "MOST-ASKED UNANSWERED QUESTIONS"]
    if snapshot["top_gaps"]:
        for gap in snapshot["top_gaps"]:
            lines.append(
                f"  [bot {gap['bot_id']}/{gap['template']}] x{gap['times_asked']} "
                f"(best {gap['best_score']}, {gap['verdict']}): {gap['question']}"
            )
    else:
        lines.append("  (none in this window)")

    return "\n".join(lines)
