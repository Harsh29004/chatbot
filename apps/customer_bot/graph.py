"""
The built-in **Customer** FAQ bot.

Kept as a thin configuration over ``apps.bot_engine.graph``, which is the
same pipeline every template-driven customer bot runs on — guardrails,
embed, retrieve, route on confidence, decline rather than guess. There is no
second implementation to keep in sync.
"""

from __future__ import annotations

from shared.config import (
    CUSTOMER_COLLECTION,
    CUSTOMER_DECLINE_MESSAGE,
    NEAR_MATCH_SUFFIX,
    NEAR_MATCH_THRESHOLD,
    STRONG_MATCH_THRESHOLD,
)

from apps.bot_engine.graph import BotConfig, BotState, build_graph

__all__ = ["BotState", "build_customer_graph"]

CUSTOMER_CONFIG = BotConfig(
    collection_name=CUSTOMER_COLLECTION,
    decline_message=CUSTOMER_DECLINE_MESSAGE,
    near_match_suffix=NEAR_MATCH_SUFFIX,
    strong_threshold=STRONG_MATCH_THRESHOLD,
    near_threshold=NEAR_MATCH_THRESHOLD,
    log_label="customer",
)


def build_customer_graph():
    """
    Assemble and compile the customer-bot graph.

    Invoke it with::

        result = graph.invoke({"query": "...", "session_id": "..."})
    """
    return build_graph(CUSTOMER_CONFIG)
