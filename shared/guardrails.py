"""
Guardrails — injection-pattern detector and action-intent detector.

Design principles
-----------------
* **Injection detector** is *logging-only*.  It flags suspicious input so
  you can review it later — it does NOT gate the retrieval pipeline.
  This avoids the detector itself becoming an attack surface.
* **Action-intent detector** is *blocking*.  If the user asks the bot to
  *do* something (refund, cancel, change account), the decline path is
  forced regardless of how well the query matches an FAQ entry.
* Pattern lists are plain data — easy to extend without touching logic.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Injection patterns (case-insensitive, checked via regex)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?prior\s+instructions",
    r"disregard\s+(the\s+)?(above|previous|prior)",
    r"forget\s+(all\s+)?(your\s+)?instructions",
    r"you\s+are\s+now",
    r"act\s+as\b",
    r"pretend\s+(to\s+be|you\s+are)",
    r"system\s*prompt",
    r"reveal\s+(your\s+)?instructions",
    r"show\s+(me\s+)?(your\s+)?prompt",
    r"what\s+(are|is)\s+your\s+(instructions|prompt|rules)",
    r"override\s+(your\s+)?rules",
    r"new\s+instructions?\s*:",
    r"jailbreak",
    r"DAN\s+mode",
    r"developer\s+mode",
    r"sudo\s+mode",
    r"\bdo\s+anything\s+now\b",
]

_INJECTION_RE = re.compile(
    "|".join(f"(?:{p})" for p in _INJECTION_PATTERNS),
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Action-intent patterns (user asking the bot to *do* something)
# ---------------------------------------------------------------------------

_ACTION_PATTERNS: list[str] = [
    r"\b(refund|reimburse)\s+(me|my|the|this)\b",
    r"\b(cancel|delete|remove)\s+(my|the|this)\s+(booking|order|account|subscription)",
    r"\bchange\s+(my|the)\s+(phone|number|email|password|address|name|account)",
    r"\bupdate\s+(my|the)\s+(phone|number|email|password|address|name|account|kyc|bank)",
    r"\bdo\s+(this|it|that)\s+for\s+me\b",
    r"\bprocess\s+(my|the|a)\s+(refund|cancellation|change|update)",
    r"\bmodify\s+(my|the)\s+(booking|order|account|profile)",
    r"\bplease\s+(refund|cancel|delete|change|update|modify)\b",
    r"\b(give|send)\s+me\s+(a\s+)?(refund|money|payout)",
    r"\bblock\s+(my|the|this)\s+(account|card|partner)",
    r"\bdeactivate\s+(my|the)\s+account",
    r"\breset\s+(my|the)\s+(password|pin)",
]

_ACTION_RE = re.compile(
    "|".join(f"(?:{p})" for p in _ACTION_PATTERNS),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Instructional prefixes — questions asking "how to" are informational,
# not action requests.
# ---------------------------------------------------------------------------

_INSTRUCTIONAL_RE = re.compile(
    r"^\s*(?:how\s+(?:do|can|should|would)\s+I|how\s+to|what\s+happens?\s+if)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_injection(text: str) -> bool:
    """
    Return ``True`` if *text* matches any known prompt-injection pattern.

    This is a **signal for logging**, not a pipeline blocker.
    """
    return bool(_INJECTION_RE.search(text))


def detect_action_intent(text: str) -> bool:
    """
    Return ``True`` if *text* looks like the user is asking the bot to
    perform a real-world action (refund, cancel, modify, etc.).

    Questions phrased as *"how do I …"*, *"how to …"*, *"how can I …"*,
    or *"what happens if …"* are treated as **informational** (the user
    is asking for guidance, not demanding the bot do it), so they are
    excluded from action-intent detection.

    When ``True``, the bot MUST take the decline path regardless of
    retrieval score.
    """
    # Instructional prefixes → user is asking for info, not requesting action
    if _INSTRUCTIONAL_RE.match(text):
        return False
    return bool(_ACTION_RE.search(text))
