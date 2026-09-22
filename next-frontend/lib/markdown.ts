"use client";

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
 *
 * On server rendering
 * -------------------
 * DOMPurify needs a DOM. In a browser it finds `window` and works; imported on
 * the server it loads a stub with no `addHook` and no working `sanitize`. The
 * Vite build never server-rendered, so this module only ever ran where a DOM
 * existed and could call `addHook` at import time.
 *
 * Under the App Router that is no longer true — even a `"use client"` module is
 * executed once on the server to produce the initial HTML — so both the hook
 * and the sanitise call are now deferred until there is a DOM to use.
 *
 * The important half is what happens when there isn't one: {@link renderMarkdown}
 * returns *escaped plain text* rather than unsanitised HTML. Failing closed
 * matters here. Falling back to raw `marked` output on the server would ship
 * exactly the unsanitised markup this module exists to prevent, and it would do
 * it in the server-rendered HTML, which is the copy that runs before any
 * client-side code could correct it.
 */
marked.setOptions({
  breaks: true, // chat messages use single newlines as line breaks
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

/** True only where DOMPurify has a real DOM to sanitise against. */
function sanitiserReady(): boolean {
  return typeof window !== "undefined" && typeof DOMPurify.sanitize === "function";
}

let hookInstalled = false;

/**
 * Force every rendered link to open in a new tab, without trusting the model to
 * have written `target` itself — and add the rel that stops the opened page
 * reaching back through `window.opener`.
 *
 * Installed on first use rather than at import, because at import time on the
 * server there is no `addHook` to call.
 */
function installLinkHook(): void {
  if (hookInstalled || typeof DOMPurify.addHook !== "function") return;
  hookInstalled = true;

  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow");
    }
  });
}

/** Escape the five characters that can start markup. The safe fallback. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function renderMarkdown(source: string): string {
  // No DOM, no sanitiser, no HTML. The text still reads correctly; it simply
  // renders unformatted until the client takes over and re-renders it.
  if (!sanitiserReady()) {
    return `<p>${escapeHtml(source)}</p>`;
  }

  installLinkHook();

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
