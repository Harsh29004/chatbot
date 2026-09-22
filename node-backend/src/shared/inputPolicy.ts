/**
 * Input policy — what is allowed to reach the model, and what never is.
 *
 * Port of `backend/shared/input_policy.py`.
 *
 * Why this module exists
 * ----------------------
 * `guardrails.ts` was written for a pipeline with **no LLM in it**. Its
 * injection detector is deliberately logging-only, and that was the correct
 * call: user text only ever became an embedding vector, so there was no prompt
 * to inject into, and a *blocking* detector would have been a denial-of-service
 * lever rather than a defence.
 *
 * The moment a local model sees user text, that reasoning stops holding. This
 * module is the gate for that path, and it is intentionally separate from
 * `guardrails.ts` so the verbatim path keeps its original, gentler contract.
 *
 * Three categories are refused:
 *
 * * **code and markup** — a question is prose. Anything shaped like a program,
 *   a query, a shell line or a tag is not a question about someone's FAQ.
 * * **secrets and personal identifiers** — card numbers, national IDs, keys,
 *   passwords. These are refused on *both* paths, and the offending text is
 *   never written to the gap log; storing it would be the leak we are trying to
 *   avoid.
 * * **instruction-shaped text** — the existing injection patterns, promoted
 *   from "log it" to "refuse it" for the model path only.
 *
 * Everything here is a regex over plain data. It is a filter, not a classifier,
 * and it is the *first* of several defences — see `bot/grounding.ts` for the
 * one that catches what gets through.
 */

import { detectInjection } from "./guardrails.js";

// ---------------------------------------------------------------------------
// Verdict
// ---------------------------------------------------------------------------

export const CATEGORY_CODE = "code";
export const CATEGORY_SECRET = "secret";
export const CATEGORY_INJECTION = "injection";
export const CATEGORY_LENGTH = "length";

/** The result of screening one piece of user input. */
export interface PolicyVerdict {
  allowed: boolean;
  category: string | null;
  /**
   * Shown to the end user. Written to be actionable rather than accusatory:
   * most people who trip these are pasting, not attacking.
   */
  message: string | null;
  /**
   * False when the text itself is the problem. The caller must not persist the
   * raw query — not to the gap list, not to the request log.
   */
  safe_to_log: boolean;
}

export const ALLOWED: PolicyVerdict = {
  allowed: true,
  category: null,
  message: null,
  safe_to_log: true,
};

/** Python had this as a property on the dataclass. */
export function refused(verdict: PolicyVerdict): boolean {
  return !verdict.allowed;
}

// ---------------------------------------------------------------------------
// Code and markup
// ---------------------------------------------------------------------------
// Tuned to need genuine code *shape*, not merely a keyword. "How do I import my
// contacts?" and "Can I select multiple items?" are real FAQ questions and must
// survive; `SELECT * FROM users` must not.

const CODE_PATTERNS: string[] = [
  // Written as \x60 rather than a literal backtick so the pattern can live
  // inside a template literal without terminating it. Same character.
  "\\x60\\x60\\x60", // fenced block (three backticks)
  String.raw`~~~`,
  String.raw`<\s*/?\s*(script|iframe|style|img|svg|div|span|body|html|a\b)`,
  String.raw`<\?php\b`,
  String.raw`</\w+>`, // any closing tag
  String.raw`\bselect\b[\s\S]{0,80}?\bfrom\b\s+\w`, // SQL projection
  String.raw`\b(drop|truncate|alter)\s+table\b`,
  String.raw`\bunion\s+(all\s+)?select\b`,
  String.raw`\binsert\s+into\b\s+\w`,
  String.raw`\bdelete\s+from\b\s+\w`,
  String.raw`'\s*or\s*'?1'?\s*=\s*'?1`, // classic tautology
  String.raw`--\s*$`, // trailing SQL comment
  String.raw`\brm\s+-rf\b`,
  String.raw`\b(curl|wget)\s+(-\w+\s+)*https?://`,
  String.raw`\|\s*(sh|bash|zsh)\b`,
  String.raw`\bsudo\s+\w+`,
  String.raw`\bchmod\s+[0-7]{3}\b`,
  String.raw`\$\([^)]+\)`, // shell substitution
  "\\x60[^\\x60\\n]{4,}\\x60", // backtick substitution
  String.raw`\bdef\s+\w+\s*\(`,
  String.raw`\bfunction\s+\w*\s*\([^)]*\)\s*\{`,
  String.raw`\bclass\s+\w+\s*[:({]`,
  String.raw`\b(import|from)\s+[\w.]+\s+(import|require)\b`,
  String.raw`\brequire\s*\(\s*['"]`,
  String.raw`=>\s*[\{\(]`, // arrow function
  String.raw`\bconsole\.(log|error)\s*\(`,
  String.raw`\b(for|while)\s*\([^;)]*;[^;)]*;`, // C-style loop
  String.raw`\{\s*"[\w-]+"\s*:`, // JSON object literal
  String.raw`^\s*[\w-]+:\s*$`, // bare YAML key line
  String.raw`</?\w+\s+\w+\s*=\s*["']`, // tag with attributes
];

// `m` carries Python's re.MULTILINE, which the `--\s*$` and bare-YAML-key
// patterns depend on: without it `$` would only match the very end of the input
// and a command hidden on line three would pass.
const CODE_RE = new RegExp(CODE_PATTERNS.map((p) => `(?:${p})`).join("|"), "im");

const CODE_MESSAGE =
  "I can only read questions written in plain words — code, markup and " +
  "commands aren't accepted here. Please describe what you need in a " +
  "sentence.";

/** True when *text* is shaped like code, markup, SQL, or a shell command. */
export function containsCode(text: string): boolean {
  return CODE_RE.test(text);
}

// ---------------------------------------------------------------------------
// Secrets and personal identifiers
// ---------------------------------------------------------------------------

const SECRET_PATTERNS: string[] = [
  String.raw`-----BEGIN [A-Z ]*PRIVATE KEY-----`,
  String.raw`\bnx[ko]_[A-Za-z0-9_\-]{16,}`, // our own key formats
  String.raw`\bsk-[A-Za-z0-9]{20,}`, // OpenAI-style
  String.raw`\bAKIA[0-9A-Z]{16}\b`, // AWS access key id
  String.raw`\bgh[pousr]_[A-Za-z0-9]{30,}`, // GitHub tokens
  String.raw`\bxox[baprs]-[A-Za-z0-9-]{10,}`, // Slack tokens
  String.raw`\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.`, // JWT
  String.raw`\b\d{3}-\d{2}-\d{4}\b`, // US SSN
  String.raw`\b(password|passwd|pwd|pin|cvv|otp)\s*(is|:|=)\s*\S+`,
];

const SECRET_RE = new RegExp(SECRET_PATTERNS.map((p) => `(?:${p})`).join("|"), "i");

// Digit runs that *might* be a card or an Aadhaar number. Checked against a
// checksum below rather than refused on shape alone — order ids and tracking
// numbers are also long digit strings, and refusing those would break the most
// common question the e-commerce and logistics templates exist to answer.
const DIGIT_RUN_RE = /\b(?:\d[ \-]?){11,19}\d\b/g;

const SECRET_MESSAGE =
  "For your own safety I can't accept card numbers, ID numbers, passwords " +
  "or access keys. Please remove that detail and ask again — and if you've " +
  "shared a password anywhere, change it.";

/** Luhn checksum — what payment card numbers satisfy and random ids don't. */
function luhnOk(digits: string): boolean {
  let total = 0;
  for (let index = 0; index < digits.length; index += 1) {
    // Python iterated `reversed(digits)`; the index parity is what matters.
    let value = Number(digits[digits.length - 1 - index]);
    if (index % 2 === 1) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    total += value;
  }
  return total % 10 === 0;
}

// Verhoeff tables — the checksum Aadhaar numbers carry. Worth the twenty lines
// of table: without it, any 12-digit tracking number reads as a national ID and
// the filter starts refusing legitimate questions.
const VERHOEFF_D: number[][] = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
  [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
  [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
  [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
  [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
  [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
  [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
  [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
  [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
  [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
];

const VERHOEFF_P: number[][] = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
  [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
  [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
  [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
  [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
  [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
  [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
  [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
];

/** Verhoeff checksum — satisfied by a real Aadhaar number. */
function verhoeffOk(digits: string): boolean {
  let check = 0;
  for (let index = 0; index < digits.length; index += 1) {
    const digit = Number(digits[digits.length - 1 - index]);
    check = VERHOEFF_D[check][VERHOEFF_P[index % 8][digit]];
  }
  return check === 0;
}

/**
 * Twelve digits that could actually have been issued.
 *
 * The checksum alone is not sufficient: roughly one in ten arbitrary 12-digit
 * strings satisfies Verhoeff by chance, and "999999999999" is one of them.
 * UIDAI never issues a number beginning 0 or 1, and never a repdigit, so both
 * are excluded — that is what separates a national ID from a tracking number
 * that happened to check out.
 */
function looksLikeAadhaar(digits: string): boolean {
  if (digits.length !== 12 || digits[0] === "0" || digits[0] === "1") return false;
  if (new Set(digits).size === 1) return false;
  return verhoeffOk(digits);
}

/**
 * True when *text* carries a credential or a government identifier.
 *
 * Checked on **both** answer paths, not just the model path: the gap list
 * stores the questions a bot could not answer, and a card number sitting in
 * that table forever is exactly the leak this product should not create.
 */
export function containsSecrets(text: string): boolean {
  if (SECRET_RE.test(text)) return true;

  // A `g` regex carries lastIndex between calls, so it is reset before use —
  // otherwise every other call would start scanning from where the last one
  // stopped and silently miss the first match.
  DIGIT_RUN_RE.lastIndex = 0;
  for (const match of text.matchAll(DIGIT_RUN_RE)) {
    const digits = match[0].replace(/[ \-]/g, "");
    if (digits.length >= 13 && digits.length <= 19 && luhnOk(digits)) {
      return true; // payment card
    }
    if (looksLikeAadhaar(digits)) {
      return true; // Aadhaar
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// The gate
// ---------------------------------------------------------------------------

const INJECTION_MESSAGE =
  "That request looks like an attempt to change how I work rather than a " +
  "question about this business. Ask me about what's in their FAQ and I'll " +
  "help.";

const LENGTH_MESSAGE =
  "That question is longer than I can read. Please shorten it to the part " +
  "you'd like answered.";

/**
 * Screen one user query.
 *
 * `forModel: false` is the verbatim retrieval path: only secrets are refused,
 * because nothing else can hurt an embedding lookup and refusing more would
 * cost real answers.
 *
 * `forModel: true` is the path where a local model will see the text. Code and
 * instruction-shaped input are refused as well.
 *
 * Order matters. Secrets are checked first so that a query carrying one is
 * marked unloggable no matter what else it also trips.
 */
export function screen(
  text: string,
  options: { forModel: boolean; maxChars?: number },
): PolicyVerdict {
  const maxChars = options.maxChars ?? 2000;

  if (containsSecrets(text)) {
    return {
      allowed: false,
      category: CATEGORY_SECRET,
      message: SECRET_MESSAGE,
      safe_to_log: false,
    };
  }

  if (!options.forModel) return ALLOWED;

  if (text.length > maxChars) {
    return {
      allowed: false,
      category: CATEGORY_LENGTH,
      message: LENGTH_MESSAGE,
      safe_to_log: true,
    };
  }

  if (containsCode(text)) {
    return {
      allowed: false,
      category: CATEGORY_CODE,
      message: CODE_MESSAGE,
      safe_to_log: true,
    };
  }

  if (detectInjection(text)) {
    return {
      allowed: false,
      category: CATEGORY_INJECTION,
      message: INJECTION_MESSAGE,
      safe_to_log: true,
    };
  }

  return ALLOWED;
}
