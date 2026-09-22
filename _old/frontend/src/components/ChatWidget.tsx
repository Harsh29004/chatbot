import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { EVENTS, track } from "../lib/analytics";
import { api, type AssistantMessage, type AssistantStatus } from "../lib/api";
import { useAuth } from "../lib/auth";
import { renderMarkdown } from "../lib/markdown";

/**
 * The assistant, reachable from anywhere.
 *
 * Same conversation as the `/assistant` page, not a second one: it talks to
 * the same threads through the same endpoints, so a question asked from the
 * bubble is there when you open the full page, and the daily limit counts once
 * rather than twice.
 *
 * It opens the *most recent* thread instead of starting a new one each time.
 * A help bubble that forgets the last thing you asked every time it closes is
 * a worse product than a page, and this exists to be better than the page for
 * one job: a quick question without losing the screen you were on.
 *
 * Hidden entirely when signed out, and when the server says the assistant is
 * off. A button that opens a panel saying "unavailable" is a button that
 * should not have been rendered.
 */

/** Routes that get no bubble. */
function suppressed(pathname: string): boolean {
  return (
    // Staff tools: a different credential, not a customer session.
    pathname.startsWith("/admin") ||
    // The full page already is this chat; two of it on one screen is silly.
    pathname.startsWith("/assistant")
  );
}

export function ChatWidget() {
  const { customer } = useAuth();
  const { pathname } = useLocation();

  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Ask once per sign-in whether the assistant is even on. Cheap, and it
  // decides whether the button exists at all.
  useEffect(() => {
    if (!customer) {
      setStatus(null);
      return;
    }
    let alive = true;
    api
      .assistantStatus()
      .then((s) => alive && setStatus(s))
      .catch(() => alive && setStatus(null));
    return () => {
      alive = false;
    };
  }, [customer]);

  // Load the latest thread the first time the panel is opened, not on mount:
  // most visits never open it, and this is two requests.
  const load = useCallback(async () => {
    setError(null);
    try {
      const threads = await api.assistantThreads();
      if (threads.length === 0) {
        setThreadId(null);
        setMessages([]);
        return;
      }
      const latest = threads[0];
      const detail = await api.assistantThread(latest.id);
      setThreadId(latest.id);
      setMessages(detail.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the chat.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (open && !loaded) void load();
  }, [open, loaded, load]);

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, streaming, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Escape closes it, the way every other panel on the web does.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;

    let id = threadId;
    if (id === null) {
      try {
        const thread = await api.createAssistantThread();
        id = thread.id;
        setThreadId(thread.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not start a chat.");
        track(EVENTS.CHAT_RESPONSE_FAILED, {
          stage: "create_thread",
          reason: err instanceof Error ? err.message.slice(0, 100) : "unknown",
        });
        return;
      }
    }

    setError(null);
    setDraft("");
    setBusy(true);

    // Show the question straight away. The server's real document arrives on
    // the next load; waiting for it makes the panel feel like it dropped the
    // message. "pending-" cannot collide with an ObjectId, which is hex only.
    setMessages((current) => [
      ...current,
      {
        id: `pending-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      },
    ]);

    // The length, never the text. What someone asks a support assistant is
    // among the most sensitive things they type into this product.
    track(EVENTS.ASSISTANT_MESSAGE_SENT, {
      message_length: text.length,
      turn_index: messages.length,
      is_first_message: messages.length === 0,
    });

    const startedAt = Date.now();
    let firstTokenAt = 0;
    let accumulated = "";
    try {
      await api.sendAssistantMessage(id, text, (delta) => {
        // Time to first token, which is what "feels slow" actually measures
        // on a streaming reply. Total duration hides a ten-second stall
        // behind a fast stream once it starts.
        if (!firstTokenAt) firstTokenAt = Date.now();
        accumulated += delta;
        setStreaming(accumulated);
      });
      setMessages((current) => [
        ...current,
        {
          id: `pending-${Date.now()}-reply`,
          role: "assistant",
          content: accumulated,
          created_at: new Date().toISOString(),
        },
      ]);
      track(EVENTS.ASSISTANT_RESPONSE_RECEIVED, {
        response_length: accumulated.length,
        ttft_ms: firstTokenAt ? firstTokenAt - startedAt : 0,
        duration_ms: Date.now() - startedAt,
        turn_index: messages.length,
      });
    } catch (err) {
      const reason = err instanceof Error ? err.message : "That message didn't send.";
      setError(reason);
      // Queue exhaustion and the daily cap are the two expected failures and
      // they mean something quite different from a crash: the product is
      // working and the person cannot use it. Separated so capacity pressure
      // is visible as itself rather than as an error rate.
      const rateLimited = /queue|limit|busy|too many/i.test(reason);
      track(rateLimited ? EVENTS.ASSISTANT_RATE_LIMITED : EVENTS.CHAT_RESPONSE_FAILED, {
        stage: "stream",
        reason: reason.slice(0, 100),
        duration_ms: Date.now() - startedAt,
      });
    } finally {
      setStreaming("");
      setBusy(false);
      // Re-read the count so the header stays honest and the limit is
      // enforced in this session rather than only after a reload.
      api.assistantStatus().then(setStatus).catch(() => {});
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  // Signed out, assistant off, or a page that owns the chat already.
  if (!customer || !status?.enabled || suppressed(pathname)) return null;

  const spent = status.daily_limit > 0 && status.messages_today >= status.daily_limit;

  return (
    <>
      <button
        className="chat-fab"
        data-open={open}
        onClick={() => {
          setOpen((v) => {
            // Both directions, because "opened and immediately closed" and
            // "opened and asked three questions" are the two outcomes this
            // widget lives or dies by, and only the pair distinguishes them.
            track(v ? EVENTS.CHAT_CLOSED : EVENTS.CHAT_OPENED, {
              surface: "assistant_bubble",
              message_count: messages.length,
            });
            return !v;
          });
        }}
        aria-label={open ? "Close the assistant" : "Ask the assistant"}
        aria-expanded={open}
      >
        {open ? "✕" : "💬"}
      </button>

      {open && (
        <section className="chat-panel" role="dialog" aria-label="Assistant">
          <header className="chat-panel-head">
            <div>
              <p className="chat-panel-title">Assistant</p>
              <p className="tiny muted">
                {status.available
                  ? `Asks about your account · ${status.messages_today}/${status.daily_limit} today`
                  : "The model isn't running right now"}
              </p>
            </div>
            <Link
              to="/assistant"
              className="btn btn-ghost btn-sm"
              onClick={() => setOpen(false)}
              title="Open the full page"
            >
              Expand
            </Link>
          </header>

          <div className="chat-panel-log">
            {!loaded ? (
              <div className="wrap-center" style={{ padding: "var(--s5)" }}>
                <div className="spinner" />
              </div>
            ) : messages.length === 0 && !streaming ? (
              <p className="small muted chat-panel-empty">
                Ask about your plan, your credits, your bot, or what your
                customers couldn&apos;t get answered.
              </p>
            ) : (
              messages.map((message) => (
                <div key={message.id} className={`bubble bubble-${message.role}`}>
                  {message.role === "assistant" ? (
                    <div
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
                    />
                  ) : (
                    <p>{message.content}</p>
                  )}
                </div>
              ))
            )}

            {streaming && (
              <div className="bubble bubble-assistant">
                <div dangerouslySetInnerHTML={{ __html: renderMarkdown(streaming) }} />
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {error && <div className="error-box chat-panel-error">{error}</div>}

          <div className="chat-panel-compose">
            <textarea
              ref={inputRef}
              className="input"
              rows={2}
              value={draft}
              placeholder={spent ? "Daily limit reached" : "Ask a question…"}
              disabled={busy || spent || !status.available}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <button
              className="btn btn-primary btn-sm"
              disabled={busy || spent || !draft.trim() || !status.available}
              onClick={() => void send()}
            >
              {busy ? "…" : "Send"}
            </button>
          </div>
        </section>
      )}
    </>
  );
}
