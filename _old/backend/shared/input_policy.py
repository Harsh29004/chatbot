"""
Input policy — what is allowed to reach the model, and what never is.

Why this module exists
----------------------
``guardrails.py`` was written for a pipeline with **no LLM in it**. Its
injection detector is deliberately logging-only, and that was the correct
call: user text only ever became an embedding vector, so there was no prompt
to inject into, and a *blocking* detector would have been a denial-of-service
lever rather than a defence.

The moment a local model sees user text, that reasoning stops holding. This
module is the gate for that path, and it is intentionally separate from
``guardrails.py`` so the verbatim path keeps its original, gentler contract.

Three categories are refused:

* **code and markup** — a question is prose. Anything shaped like a program,
  a query, a shell line or a tag is not a question about someone's FAQ.
* **secrets and personal identifiers** — card numbers, national IDs, keys,
  passwords. These are refused on *both* paths, and the offending text is
  never written to the gap log; storing it would be the leak we are trying
  to avoid.
* **instruction-shaped text** — the existing injection patterns, promoted
  from "log it" to "refuse it" for the model path only.

Everything here is a regex over plain data. It is a filter, not a classifier,
and it is the *first* of several defences — see ``bot/grounding.py`` for the
one that catches what gets through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.shared.guardrails import detect_injection

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

CATEGORY_CODE = "code"
CATEGORY_SECRET = "secret"
CATEGORY_INJECTION = "injection"
CATEGORY_LENGTH = "length"


@dataclass(frozen=True)
class PolicyVerdict:
    """The result of screening one piece of user input."""

    allowed: bool
    category: str | None = None
    # Shown to the end user. Written to be actionable rather than accusatory:
    # most people who trip these are pasting, not attacking.
    message: str | None = None
    # False when the text itself is the problem. The caller must not persist
    # the raw query — not to the gap list, not to the request log.
    safe_to_log: bool = True

    @property
    def refused(self) -> bool:
        return not self.allowed


ALLOWED = PolicyVerdict(allowed=True)


# ---------------------------------------------------------------------------
# Code and markup
# ---------------------------------------------------------------------------
# Tuned to need genuine code *shape*, not merely a keyword. "How do I import
# my contacts?" and "Can I select multiple items?" are real FAQ questions and
# must survive; ``SELECT * FROM users`` must not.

_CODE_PATTERNS: list[str] = [
    r"```",                                    # fenced block
    r"~~~",
    r"<\s*/?\s*(script|iframe|style|img|svg|div|span|body|html|a\b)",
    r"<\?php\b",
    r"</\w+>",                                 # any closing tag
    r"\bselect\b[\s\S]{0,80}?\bfrom\b\s+\w",   # SQL projection
    r"\b(drop|truncate|alter)\s+table\b",
    r"\bunion\s+(all\s+)?select\b",
    r"\binsert\s+into\b\s+\w",
    r"\bdelete\s+from\b\s+\w",
    r"'\s*or\s*'?1'?\s*=\s*'?1",               # classic tautology
    r"--\s*$",                                 # trailing SQL comment
    r"\brm\s+-rf\b",
    r"\b(curl|wget)\s+(-\w+\s+)*https?://",
    r"\|\s*(sh|bash|zsh)\b",
    r"\bsudo\s+\w+",
    r"\bchmod\s+[0-7]{3}\b",
    r"\$\([^)]+\)",                            # shell substitution
    r"`[^`\n]{4,}`",                           # backtick substitution
    r"\bdef\s+\w+\s*\(",
    r"\bfunction\s+\w*\s*\([^)]*\)\s*\{",
    r"\bclass\s+\w+\s*[:({]",
    r"\b(import|from)\s+[\w.]+\s+(import|require)\b",
    r"\brequire\s*\(\s*['\"]",
    r"=>\s*[\{\(]",                            # arrow function
    r"\bconsole\.(log|error)\s*\(",
    r"\b(for|while)\s*\([^;)]*;[^;)]*;",       # C-style loop
    r"\{\s*\"[\w-]+\"\s*:",                    # JSON object literal
    r"^\s*[\w-]+:\s*$",                        # bare YAML key line
    r"</?\w+\s+\w+\s*=\s*[\"']",               # tag with attributes
]

_CODE_RE = re.compile("|".join(f"(?:{p})" for p in _CODE_PATTERNS), re.IGNORECASE | re.MULTILINE)

_CODE_MESSAGE = (
    "I can only read questions written in plain words — code, markup and "
    "commands aren't accepted here. Please describe what you need in a "
    "sentence."
)


def contains_code(text: str) -> bool:
    """True when *text* is shaped like code, markup, SQL, or a shell command."""
    return bool(_CODE_RE.search(text))


# ---------------------------------------------------------------------------
# Secrets and personal identifiers
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[str] = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bnx[ko]_[A-Za-z0-9_\-]{16,}",           # our own key formats
    r"\bsk-[A-Za-z0-9]{20,}",                  # OpenAI-style
    r"\bAKIA[0-9A-Z]{16}\b",                   # AWS access key id
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",           # GitHub tokens
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}",         # Slack tokens
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.",   # JWT
    r"\b\d{3}-\d{2}-\d{4}\b",                  # US SSN
    r"\b(password|passwd|pwd|pin|cvv|otp)\s*(is|:|=)\s*\S+",
]

_SECRET_RE = re.compile("|".join(f"(?:{p})" for p in _SECRET_PATTERNS), re.IGNORECASE)

# Digit runs that *might* be a card or an Aadhaar number. Checked against a
# checksum below rather than refused on shape alone — order ids and tracking
# numbers are also long digit strings, and refusing those would break the most
# common question the e-commerce and logistics templates exist to answer.
_DIGIT_RUN_RE = re.compile(r"\b(?:\d[ \-]?){11,19}\d\b")

_SECRET_MESSAGE = (
    "For your own safety I can't accept card numbers, ID numbers, passwords "
    "or access keys. Please remove that detail and ask again — and if you've "
    "shared a password anywhere, change it."
)


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — what payment card numbers satisfy and random ids don't."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# Verhoeff tables — the checksum Aadhaar numbers carry. Worth the twenty lines
# of table: without it, any 12-digit tracking number reads as a national ID and
# the filter starts refusing legitimate questions.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6), (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4), (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2), (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def _verhoeff_ok(digits: str) -> bool:
    """Verhoeff checksum — satisfied by a real Aadhaar number."""
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[index % 8][int(char)]]
    return check == 0


def _looks_like_aadhaar(digits: str) -> bool:
    """
    Twelve digits that could actually have been issued.

    The checksum alone is not sufficient: roughly one in ten arbitrary 12-digit
    strings satisfies Verhoeff by chance, and "999999999999" is one of them.
    UIDAI never issues a number beginning 0 or 1, and never a repdigit, so both
    are excluded — that is what separates a national ID from a tracking number
    that happened to check out.
    """
    if len(digits) != 12 or digits[0] in "01":
        return False
    if len(set(digits)) == 1:
        return False
    return _verhoeff_ok(digits)


def contains_secrets(text: str) -> bool:
    """
    True when *text* carries a credential or a government identifier.

    Checked on **both** answer paths, not just the model path: the gap list
    stores the questions a bot could not answer, and a card number sitting in
    that table forever is exactly the leak this product should not create.
    """
    if _SECRET_RE.search(text):
        return True

    for match in _DIGIT_RUN_RE.finditer(text):
        digits = re.sub(r"[ \-]", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True          # payment card
        if _looks_like_aadhaar(digits):
            return True          # Aadhaar
    return False


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

_INJECTION_MESSAGE = (
    "That request looks like an attempt to change how I work rather than a "
    "question about this business. Ask me about what's in their FAQ and I'll "
    "help."
)

_LENGTH_MESSAGE = (
    "That question is longer than I can read. Please shorten it to the part "
    "you'd like answered."
)


def screen(text: str, *, for_model: bool, max_chars: int = 2000) -> PolicyVerdict:
    """
    Screen one user query.

    ``for_model=False`` is the verbatim retrieval path: only secrets are
    refused, because nothing else can hurt an embedding lookup and refusing
    more would cost real answers.

    ``for_model=True`` is the path where a local model will see the text. Code
    and instruction-shaped input are refused as well.

    Order matters. Secrets are checked first so that a query carrying one is
    marked unloggable no matter what else it also trips.
    """
    if contains_secrets(text):
        return PolicyVerdict(
            allowed=False,
            category=CATEGORY_SECRET,
            message=_SECRET_MESSAGE,
            safe_to_log=False,
        )

    if not for_model:
        return ALLOWED

    if len(text) > max_chars:
        return PolicyVerdict(
            allowed=False, category=CATEGORY_LENGTH, message=_LENGTH_MESSAGE
        )

    if contains_code(text):
        return PolicyVerdict(
            allowed=False, category=CATEGORY_CODE, message=_CODE_MESSAGE
        )

    if detect_injection(text):
        return PolicyVerdict(
            allowed=False, category=CATEGORY_INJECTION, message=_INJECTION_MESSAGE
        )

    return ALLOWED
