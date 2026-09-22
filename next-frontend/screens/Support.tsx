"use client";

import { useEffect, useRef, useState } from "react";

import { Page } from "../components/Chrome";
import { EVENTS, track } from "../lib/analytics";
import { api } from "../lib/api-client";
import type { SupportMessage } from "../lib/types";

/** How often to check for a reply while the page is open. */
const POLL_MS = 5000;

/**
 * The customer's support chat.
 *
 * One continuous conversation with the team — no thread list, because there is
 * only ever one. Deliberately shaped like the assistant next door so the two
 * feel like the same product, but nothing here touches a model: on the other
 * end is a person.
 */
interface SupportProps {
  /** The conversation so far, read on the server. Polling takes over after. */
  initialMessages?: SupportMessage[] | null;
}

export function Support({ initialMessages = null }: SupportProps) {
  const [messages, setMessages] = useState<SupportMessage[]>(initialMessages ?? []);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(initialMessages === null);
  const [error, setError] = useState<string | null>(null);

  const logRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Highest id we've seen. Polling asks for what's newer than this rather than
  // re-downloading the whole conversation every five seconds.
  // Newest message seen. An ObjectId string, "" before the first one.
  const lastIdRef = useRef("");
  const pinnedRef = useRef(true);

  const onScroll = () => {
    const el = logRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    if (pinnedRef.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  useEffect(() => {
    let alive = true;

    const pull = async () => {
      try {
        const { messages: fresh } = await api.supportMessages(lastIdRef.current);
        if (!alive) return;
        if (fresh.length) {
          lastIdRef.current = fresh[fresh.length - 1].id;
          setMessages((current) => [...current, ...fresh]);
          // Seeing it counts as reading it — otherwise the badge stays lit
          // while the customer is looking straight at the reply.
          api.markSupportRead().catch(() => undefined);
        }
        setError(null);
      } catch {
        // A dropped poll is not worth an error banner; the next one recovers.
      } finally {
        if (alive) setLoading(false);
      }
    };

    void pull();
    const timer = setInterval(pull, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;

    setSending(true);
    setError(null);
    pinnedRef.current = true;
    try {
      const sent = await api.sendSupportMessage(text);
      lastIdRef.current = sent.id;
      setMessages((current) => [...current, sent]);
      setDraft("");
      if (textareaRef.current) textareaRef.current.style.height = "auto";
      // Length only. A support message is the single most sensitive free
      // text in this product — it is where people paste keys, invoices and
      // whatever just went wrong for them.
      track(
        messages.length === 0
          ? EVENTS.SUPPORT_TICKET_CREATED
          : EVENTS.SUPPORT_REPLY_SENT,
        { message_length: text.length, thread_length: messages.length },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send that message.");
      track(EVENTS.API_ERROR, {
        area: "support_send",
        reason: err instanceof Error ? err.message.slice(0, 100) : "unknown",
      });
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const grow = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };

  return (
    <Page>
      <main className="wrap assistant-page">
        <div className="assistant-head">
          <div>
            <h1 className="h-section">Support</h1>
            <p className="small muted">
              Talk to the Nexora team. A real person reads these — replies usually
              arrive within a working day.
            </p>
          </div>
        </div>

        <section className="chat-main chat-solo">
          <div className="chat-log" ref={logRef} onScroll={onScroll}>
            {loading && <p className="small muted">Loading…</p>}

            {!loading && messages.length === 0 && (
              <div className="chat-empty">
                <p className="muted">
                  Nothing here yet. Ask us anything — billing, your bot, a sheet that
                  won&apos;t upload.
                </p>
              </div>
            )}

            {messages.map((message) => (
              <div
                key={message.id}
                className={`bubble bubble-${message.sender === "staff" ? "assistant" : "user"}`}
              >
                {message.sender === "staff" && (
                  <span className="chat-who">Nexora team</span>
                )}
                <p>{message.body}</p>
              </div>
            ))}

            <div ref={bottomRef} />
          </div>

          {error && <p className="error-box chat-error">{error}</p>}

          <div className="chat-composer">
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={grow}
              onKeyDown={onKeyDown}
              placeholder="Write a message…"
              rows={1}
              disabled={sending}
            />
            <button
              className="btn btn-primary"
              onClick={send}
              disabled={!draft.trim() || sending}
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </section>
      </main>
    </Page>
  );
}
