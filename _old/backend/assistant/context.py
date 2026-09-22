"""
What the assistant knows about the person it is talking to.

This is the difference between a generic chat window bolted onto a dashboard
and something worth having in one: asked *"why is my bot declining so much?"*,
it can actually look.

Scope rule, and it is absolute: **everything here is derived from one
``customer_id``.** There is no parameter that widens it, no "all bots" branch,
and no code path that reaches the cross-tenant snapshot in ``backend/ops.py``
— that one is owner-only and lives behind a different auth dependency for
exactly this reason.

The block is small (a few hundred tokens) and is attached to every
conversation rather than being fetched when some classifier decides the
question is account-related. A 7B model doing intent classification would get
that wrong often enough to be worse than useless, and the cost of always
including it is a few tokens of prompt.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.billing import db as billing_db
from backend.shared.api_keys import get_usage_stats, get_user_by_email, list_keys_for_email
from backend.shared.logging_store import get_gap_summary
from bot import store as bot_store
from bot.catalogue import get_template

logger = logging.getLogger(__name__)

MAX_GAPS = 8


def build(customer: dict[str, Any]) -> dict[str, Any]:
    """
    Gather this one customer's account state.

    Never raises. A dashboard chat that 500s because the billing table was
    briefly unhappy is worse than one that answers without knowing the plan,
    so every section degrades to absent rather than failing.
    """
    context: dict[str, Any] = {"email": customer.get("email"), "name": customer.get("name")}

    try:
        subscription = billing_db.get_current_subscription(customer["id"])
        if subscription:
            context["plan"] = {
                "plan_id": subscription["plan_id"],
                "status": subscription["status"],
                "entitled": billing_db.is_entitled(subscription),
                "period_end": subscription["current_period_end"],
            }
    except Exception:
        logger.debug("Assistant context: subscription unavailable.", exc_info=True)

    user = None
    try:
        user = get_user_by_email(customer["email"])
    except Exception:
        logger.debug("Assistant context: account lookup failed.", exc_info=True)

    if user is None:
        # Signed up but never created a key — a real state, and the assistant
        # should be able to say so rather than inventing a bot.
        context["has_api_account"] = False
        return context

    context["has_api_account"] = True

    try:
        usage = get_usage_stats(user["id"], user["daily_credit_limit"])
        context["credits"] = {
            "used_today": usage.get("credits_used_today"),
            "remaining": usage.get("credits_remaining"),
            "daily_limit": usage.get("daily_credit_limit"),
        }
        context["active_keys"] = len(list_keys_for_email(customer["email"]))
    except Exception:
        logger.debug("Assistant context: usage unavailable.", exc_info=True)

    try:
        bot = bot_store.get_bot(user["id"])
    except Exception:
        bot = None
        logger.debug("Assistant context: bot lookup failed.", exc_info=True)

    if bot is None:
        context["bot"] = None
        return context

    template = get_template(bot["template_id"])
    context["bot"] = {
        "template": bot["template_id"],
        "template_name": template.name if template else bot["template_id"],
        "status": bot["status"],
        "sheet_rows_indexed": bot["doc_count"],
        "sheet_filename": bot["sheet_filename"],
        "grounded_rewording": bool(bot.get("llm_enabled", 0)),
        "strong_threshold": template.strong_threshold if template else None,
        "near_threshold": template.near_threshold if template else None,
    }

    try:
        near = template.near_threshold if template else 0.60
        gaps = get_gap_summary(f"bot:{bot['id']}", days=30, limit=MAX_GAPS)
        context["top_unanswered"] = [
            {
                "question": gap["question"],
                "times_asked": gap["times_asked"],
                "best_score": round(gap["best_score"], 3),
                "verdict": "needs phrasing" if gap["best_score"] >= near else "not covered",
            }
            for gap in gaps
        ]
    except Exception:
        logger.debug("Assistant context: gap list unavailable.", exc_info=True)

    return context


def render(context: dict[str, Any]) -> str:
    """
    Flatten the context into the compact block the model reads.

    Terse and tabular rather than JSON: a 7B model follows a short labelled
    list far more reliably than nested braces, and punctuation is prompt a CPU
    has to chew through on every single turn.
    """
    lines = [f"Signed in as: {context.get('name') or 'unknown'} <{context.get('email')}>"]

    plan = context.get("plan")
    if plan:
        entitled = "active" if plan["entitled"] else "not active"
        lines.append(
            f"Plan: {plan['plan_id']} ({plan['status']}, {entitled}, "
            f"period ends {plan['period_end']})"
        )
    else:
        lines.append("Plan: none on record")

    if not context.get("has_api_account"):
        lines.append("API account: not created yet (no keys, no bot)")
        return "\n".join(lines)

    credits = context.get("credits")
    if credits:
        lines.append(
            f"Credits today: {credits['used_today']} used, {credits['remaining']} "
            f"remaining of {credits['daily_limit']}"
        )
    if context.get("active_keys") is not None:
        lines.append(f"Active API keys: {context['active_keys']}")

    bot = context.get("bot")
    if not bot:
        lines.append("Bot: not set up yet")
        return "\n".join(lines)

    lines += [
        "",
        f"Their bot: {bot['template_name']} template ({bot['template']}), status {bot['status']}",
        f"  sheet: {bot['sheet_filename'] or 'none'} — {bot['sheet_rows_indexed']} rows indexed",
        f"  thresholds: answers verbatim at {bot['strong_threshold']}, "
        f"hedges down to {bot['near_threshold']}, declines below",
        f"  grounded rewording: {'on' if bot['grounded_rewording'] else 'off'}",
    ]

    gaps = context.get("top_unanswered")
    if gaps:
        lines += ["", "Questions their bot could not answer (last 30 days):"]
        for gap in gaps:
            lines.append(
                f"  x{gap['times_asked']} (best {gap['best_score']}, {gap['verdict']}): "
                f"{gap['question']}"
            )
    elif gaps is not None:
        lines += ["", "Their bot has no unanswered questions logged in the last 30 days."]

    return "\n".join(lines)
