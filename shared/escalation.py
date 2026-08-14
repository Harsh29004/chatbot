"""
Escalation stub — ticket creation hook for the #TK#### system.

This module is a placeholder.  When your existing Help & Support ticket
API is available, wire it up here so NEAR_MATCH and NO_MATCH responses
automatically create a support ticket.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def create_support_ticket(
    *,
    bot_type: str,
    user_id: str,
    query: str,
    session_id: str,
) -> str:
    """
    Create a support ticket in the #TK#### system.

    **Currently a stub** — logs the intent and returns a placeholder
    ticket ID.  Replace the body of this function with an HTTP call
    to your existing ticket-creation API when ready.

    Parameters
    ----------
    bot_type : str
        ``"customer"`` or ``"partner"``.
    user_id : str
        The authenticated user's ID.
    query : str
        The question that could not be satisfactorily answered.
    session_id : str
        The chat session ID for traceability.

    Returns
    -------
    str
        A placeholder ticket ID (e.g. ``"TK0000"``).
    """
    logger.info(
        "[ESCALATION STUB] Would create ticket — "
        "bot=%s user=%s session=%s query=%r",
        bot_type,
        user_id,
        session_id,
        query[:120],
    )
    return "TK0000"
