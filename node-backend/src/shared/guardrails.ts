/**
 * Guardrails — injection-pattern detector and action-intent detector.
 *
 * Port of `backend/shared/guardrails.py`.
 *
 * Design principles
 * -----------------
 * * **Injection detector** is *logging-only*. It flags suspicious input so you
 *   can review it later — it does NOT gate the retrieval pipeline. This avoids
 *   the detector itself becoming an attack surface.
 * * **Action-intent detector** is *blocking*. If the user asks the bot to *do*
 *   something (refund, cancel, change account), the decline path is forced
 *   regardless of how well the query matches an FAQ entry.
 * * Pattern lists are plain data — easy to extend without touching logic.
 *
 * The patterns are transcribed unchanged. Python and JavaScript share this much
 * regex syntax exactly, so every one of them matches the same strings it did
 * before; `re.IGNORECASE` becomes the `i` flag and nothing else moves.
 */

// ---------------------------------------------------------------------------
// Injection patterns (case-insensitive, checked via regex)
// ---------------------------------------------------------------------------

const INJECTION_PATTERNS: string[] = [
  String.raw`ignore\s+(all\s+)?previous\s+instructions`,
  String.raw`ignore\s+(all\s+)?prior\s+instructions`,
  String.raw`disregard\s+(the\s+)?(above|previous|prior)`,
  String.raw`forget\s+(all\s+)?(your\s+)?instructions`,
  String.raw`you\s+are\s+now`,
  String.raw`act\s+as\b`,
  String.raw`pretend\s+(to\s+be|you\s+are)`,
  String.raw`system\s*prompt`,
  String.raw`reveal\s+(your\s+)?instructions`,
  String.raw`show\s+(me\s+)?(your\s+)?prompt`,
  String.raw`what\s+(are|is)\s+your\s+(instructions|prompt|rules)`,
  String.raw`override\s+(your\s+)?rules`,
  String.raw`new\s+instructions?\s*:`,
  String.raw`jailbreak`,
  String.raw`DAN\s+mode`,
  String.raw`developer\s+mode`,
  String.raw`sudo\s+mode`,
  String.raw`\bdo\s+anything\s+now\b`,
];

const INJECTION_RE = new RegExp(INJECTION_PATTERNS.map((p) => `(?:${p})`).join("|"), "i");

// ---------------------------------------------------------------------------
// Action-intent patterns (user asking the bot to *do* something)
// ---------------------------------------------------------------------------

const ACTION_PATTERNS: string[] = [
  String.raw`\b(refund|reimburse)\s+(me|my|the|this)\b`,
  String.raw`\b(cancel|delete|remove)\s+(my|the|this)\s+(booking|order|account|subscription)`,
  String.raw`\bchange\s+(my|the)\s+(phone|number|email|password|address|name|account)`,
  String.raw`\bupdate\s+(my|the)\s+(phone|number|email|password|address|name|account|kyc|bank)`,
  String.raw`\bdo\s+(this|it|that)\s+for\s+me\b`,
  String.raw`\bprocess\s+(my|the|a)\s+(refund|cancellation|change|update)`,
  String.raw`\bmodify\s+(my|the)\s+(booking|order|account|profile)`,
  String.raw`\bplease\s+(refund|cancel|delete|change|update|modify)\b`,
  String.raw`\b(give|send)\s+me\s+(a\s+)?(refund|money|payout)`,
  String.raw`\bblock\s+(my|the|this)\s+(account|card|partner)`,
  String.raw`\bdeactivate\s+(my|the)\s+account`,
  String.raw`\breset\s+(my|the)\s+(password|pin)`,
];

const ACTION_RE = new RegExp(ACTION_PATTERNS.map((p) => `(?:${p})`).join("|"), "i");

// ---------------------------------------------------------------------------
// Instructional prefixes — questions asking "how to" are informational, not
// action requests.
// ---------------------------------------------------------------------------

const INSTRUCTIONAL_RE =
  /^\s*(?:how\s+(?:do|can|should|would)\s+I|how\s+to|what\s+happens?\s+if)\b/i;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * True if *text* matches any known prompt-injection pattern.
 *
 * This is a **signal for logging**, not a pipeline blocker.
 */
export function detectInjection(text: string): boolean {
  return INJECTION_RE.test(text);
}

/**
 * True if *text* looks like the user is asking the bot to perform a real-world
 * action (refund, cancel, modify, etc.).
 *
 * Questions phrased as *"how do I …"*, *"how to …"*, *"how can I …"*, or *"what
 * happens if …"* are treated as **informational** (the user is asking for
 * guidance, not demanding the bot do it), so they are excluded from
 * action-intent detection.
 *
 * When true, the bot MUST take the decline path regardless of retrieval score.
 */
export function detectActionIntent(text: string): boolean {
  // Instructional prefixes → user is asking for info, not requesting action.
  // Python used `re.match`, which anchors at the start; the `^` in the pattern
  // is what carries that over.
  if (INSTRUCTIONAL_RE.test(text)) return false;
  return ACTION_RE.test(text);
}
