"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Logo } from "../components/Chrome";
import { AdminLogin } from "../components/admin/AdminLogin";
import { adminApi } from "../lib/api-client";
import type { SupportConversation, SupportMessage } from "../lib/types";

const POLL_MS = 5000;

/**
 * Where the admin key lives.
 *
 * sessionStorage, not localStorage: this key opens every customer's
 * conversation, and it should not outlive the tab it was typed into. The cost
 * is retyping it after a browser restart, which is the right trade for a
 * secret with this reach.
 */
const KEY_STORAGE = "nexora.adminKey";

function readStoredKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    // Private windows and locked-down browsers throw on access rather than
    // returning null, so the page must survive having no storage at all.
    return "";
  }
}

/**
 * The staff support inbox.
 *
 * Not part of the customer app: no nav, no session, its own key. It is a
 * separate tool that happens to be served from the same origin, and it looks
 * like one.
 */
export function AdminSupport() {
  const [key, setKey] = useState(readStoredKey);
  const [authed, setAuthed] = useState(false);

  const [conversations, setConversations] = useState<SupportConversation[]>([]);
  const [totalUnread, setTotalUnread] = useState(0);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<SupportMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The newest message this tab has seen. An ObjectId string now, and ""
  // until the first message arrives — both mean "give me everything".
  const lastIdRef = useRef("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  const onScroll = () => {
    const el = logRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  useEffect(() => {
    if (pinnedRef.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const refreshInbox = useCallback(
    async (withKey: string) => {
      const data = await adminApi.inbox(withKey);
      setConversations(data.conversations);
      setTotalUnread(data.total_unread);
      return data;
    },
    [],
  );

  // Validate whatever key we already have before showing the inbox — a stored
  // key that has since been rotated should land on the sign-in box, not on an
  // empty list that looks like "no customers have written".
  useEffect(() => {
    if (!key) return;
    refreshInbox(key)
      .then(() => setAuthed(true))
      .catch(() => {
        // An expired session: back to the sign-in form, quietly.
        setAuthed(false);
        setKey("");
      });
  }, [key, refreshInbox]);

  const signIn = async (candidate: string) => {
    setError(null);
    await refreshInbox(candidate);
    try {
      sessionStorage.setItem(KEY_STORAGE, candidate);
    } catch {
      /* Storage can be unavailable; the session still works for this page load. */
    }
    setKey(candidate);
    setAuthed(true);
  };

  const signOut = () => {
    try {
      sessionStorage.removeItem(KEY_STORAGE);
    } catch {
      /* nothing to clear */
    }
    setKey("");
    setAuthed(false);
    setConversations([]);
    setMessages([]);
    setActiveId(null);
  };

  const openConversation = useCallback(
    async (id: string) => {
      setActiveId(id);
      lastIdRef.current = "";
      pinnedRef.current = true;
      try {
        const data = await adminApi.conversation(key, id);
        setMessages(data.messages);
        if (data.messages.length) {
          lastIdRef.current = data.messages[data.messages.length - 1].id;
        }
        const read = await adminApi.markRead(key, id);
        setTotalUnread(read.total_unread);
        setConversations((current) =>
          current.map((c) => (c.id === id ? { ...c, unread: 0 } : c)),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not open that conversation.");
      }
    },
    [key],
  );

  // Poll the inbox for new customer messages, and the open conversation for
  // new lines in it.
  useEffect(() => {
    if (!authed || !key) return;
    let alive = true;

    const tick = async () => {
      try {
        await refreshInbox(key);
        if (!alive || activeId === null) return;
        const data = await adminApi.conversation(key, activeId, lastIdRef.current);
        if (!alive || data.messages.length === 0) return;
        lastIdRef.current = data.messages[data.messages.length - 1].id;
        setMessages((current) => [...current, ...data.messages]);
        await adminApi.markRead(key, activeId);
      } catch {
        // A dropped poll recovers on the next tick.
      }
    };

    const timer = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [authed, key, activeId, refreshInbox]);

  const send = async () => {
    const text = draft.trim();
    if (!text || activeId === null || sending) return;
    setSending(true);
    setError(null);
    pinnedRef.current = true;
    try {
      const sent = await adminApi.reply(key, activeId, text);
      lastIdRef.current = sent.id;
      setMessages((current) => [...current, sent]);
      setDraft("");
      await refreshInbox(key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send that reply.");
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

  if (!authed) {
    return <AdminLogin title="Support inbox" onToken={signIn} />;
  }

  const active = conversations.find((c) => c.id === activeId) ?? null;

  return (
    <div className="admin-shell">
      <header className="admin-bar">
        <Logo />
        <div className="row gap-4">
          <span className="small muted">
            {totalUnread > 0 ? `${totalUnread} unread` : "All caught up"}
          </span>
          <button className="btn btn-ghost btn-sm" onClick={signOut}>
            Lock
          </button>
        </div>
      </header>

      <main className="wrap assistant-page">
        <div className="chat-shell">
          <aside className="chat-threads">
            <div className="chat-thread-list">
              {conversations.length === 0 && (
                <p className="small muted">No customer has written yet.</p>
              )}
              {conversations.map((conversation) => (
                <div
                  key={conversation.id}
                  className={`chat-thread ${conversation.id === activeId ? "is-active" : ""}`}
                >
                  <button
                    className="chat-thread-open admin-thread"
                    onClick={() => openConversation(conversation.id)}
                  >
                    <span className="admin-thread-name">
                      {conversation.customer_name || conversation.customer_email}
                      {conversation.unread > 0 && (
                        <span className="unread-dot">{conversation.unread}</span>
                      )}
                    </span>
                    <span className="admin-thread-preview">
                      {conversation.last_message_preview || "—"}
                    </span>
                  </button>
                </div>
              ))}
            </div>
          </aside>

          <section className="chat-main">
            {active && (
              <div className="admin-thread-head">
                <strong>{active.customer_name || "Customer"}</strong>
                <span className="small muted">{active.customer_email}</span>
              </div>
            )}

            <div className="chat-log" ref={logRef} onScroll={onScroll}>
              {activeId === null && (
                <div className="chat-empty">
                  <p className="muted">Pick a conversation on the left.</p>
                </div>
              )}
              {messages.map((message) => (
                <div
                  key={message.id}
                  // Inverted against the customer's view on purpose: here
                  // "mine" is staff, so the team's replies sit on the right.
                  className={`bubble bubble-${message.sender === "staff" ? "user" : "assistant"}`}
                >
                  {message.sender === "customer" && (
                    <span className="chat-who">
                      {active?.customer_name || "Customer"}
                    </span>
                  )}
                  <p>{message.body}</p>
                </div>
              ))}
              <div ref={bottomRef} />
            </div>

            {error && <p className="error-box chat-error">{error}</p>}

            {activeId !== null && (
              <div className="chat-composer">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder="Reply…"
                  rows={1}
                  disabled={sending}
                />
                <button
                  className="btn btn-primary"
                  onClick={send}
                  disabled={!draft.trim() || sending}
                >
                  {sending ? "Sending…" : "Reply"}
                </button>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
