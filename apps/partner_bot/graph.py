"""
The built-in **Partner** FAQ bot.

Same engine as the customer bot and every template-driven bot; only the
collection and the decline wording differ.
"""

from __future__ import annotations

from shared.config import (
    NEAR_MATCH_SUFFIX,
    NEAR_MATCH_THRESHOLD,
    PARTNER_COLLECTION,
    PARTNER_DECLINE_MESSAGE,
    STRONG_MATCH_THRESHOLD,
)

from apps.bot_engine.graph import BotConfig, BotState, build_graph

__all__ = ["BotState", "build_partner_graph"]

PARTNER_CONFIG = BotConfig(
    collection_name=PARTNER_COLLECTION,
    decline_message=PARTNER_DECLINE_MESSAGE,
    near_match_suffix=NEAR_MATCH_SUFFIX,
    strong_threshold=STRONG_MATCH_THRESHOLD,
    near_threshold=NEAR_MATCH_THRESHOLD,
    log_label="partner",
)


def build_partner_graph():
    """
    Assemble and compile the partner-bot graph.

    Invoke it with::

        result = graph.invoke({"query": "...", "session_id": "..."})
    """
    return build_graph(PARTNER_CONFIG)
