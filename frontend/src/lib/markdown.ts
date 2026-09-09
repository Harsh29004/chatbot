import DOMPurify from "dompurify";
import { marked } from "marked";

/**
 * Render model output as HTML, safely.
 *
 * The sanitiser is not optional and not belt-and-braces. Model output is
 * untrusted text: a reply can contain whatever was in the conversation, and a
 * conversation can contain whatever someone pasted into it. Rendering that
 * straight into innerHTML is a stored-XSS hole that happens to be written by a
 * language model instead of an attacker — which makes it no safer at all.
 *
 * So: marked turns markdown into HTML, then DOMPurify removes anything that
 * can execute. Tags are allowlisted rather than blocklisted, because a
 * blocklist is a promise to have thought of everything.
 */
marked.setOptions({
  breaks: true,   // chat messages use single newlines as line breaks
  gfm: true,
});

const ALLOWED_TAGS = [
  "p", "br", "hr",
  "strong", "em", "del", "code", "pre",
  "ul", "ol", "li",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "blockquote",
  "a",
  "table", "thead", "tbody", "tr", "th", "td",
  "span",
];

export function renderMarkdown(source: string): string {
  const raw = marked.parse(source, { async: false }) as string;

  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ["href", "title", "class", "start"],
    // No javascript:, no data: — a link in a chat message should go to a page.
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|#|\/)/i,
    // Even allowlisted, these would be a way to smuggle script content in.
    FORBID_TAGS: ["style", "script", "iframe", "object", "embed", "form", "input"],
    FORBID_ATTR: ["style", "srcset", "formaction"],
  });
}

/**
 * Force every rendered link to open in a new tab, without trusting the model
 * to have written `target` itself — and add the rel that stops the opened page
 * reaching back through `window.opener`.
 */
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer nofollow");
  }
});
