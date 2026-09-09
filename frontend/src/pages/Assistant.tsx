import { useCallback, useEffect, useRef, useState } from "react";

import { Page } from "../components/Chrome";
import {
  ApiError,
  api,
  type AssistantMessage,
  type AssistantStatus,
  type AssistantThread,
} from "../lib/api";
import { renderMarkdown } from "../lib/markdown";

/**
 * The dashboard assistant.
 *
 * Deliberately shaped like the chat apps people already know — thread list on
 * the left, conversation on the right, answer streaming in as it is written.
 * The streaming is not decoration: this runs a 7B model on two CPU cores, so a
 * long answer takes a minute or more, and watching it arrive is the difference
 * between "thinking" and "broken".
 */
export function Assistant() {
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [threads, setThreads] = useState<AssistantThread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Follow the answer as it streams, but only while the reader is already at
  // the bottom — yanking the viewport away from someone scrolled up reading an
  // earlier message is worse than not following at all.
  const pinnedRef = useRef(true);
  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  useEffect(() => {
    if (pinnedRef.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, streaming]);

  useEffect(() => {
    api.assistantStatus().then(setStatus).catch(() => setStatus(null));
    api.assistantThreads().then(setThreads).catch(() => setThreads([]));
  }, []);

  const openThread = useCallback(async (id: string) => {
    setActiveId(id);
    setError(null);
    setStreaming("");
    try {
      const detail = await api.assistantThread(id);
      setMessages(detail.messages);
    } catch {
      setMessages([]);
    }
  }, []);

  const newThread = async () => {
    setError(null);
    try {
      const thread = await api.createAssistantThread();
      setThreads((current) => [thread, ...current]);
      setActiveId(thread.id);
      setMessages([]);
      setStreaming("");
      textareaRef.current?.focus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start a chat.");
    }
  };

  const removeThread = async (id: string) => {
    try {
      await api.deleteAssistantThread(id);
      setThreads((current) => current.filter((t) => t.id !== id));
      if (activeId === id) {
        setActiveId(null);
        setMessages([]);
      }
    } catch {
      setError("Could not delete that conversation.");
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;

    let threadId = activeId;
    if (threadId === null) {
      try {
        const thread = await api.createAssistantThread();
        setThreads((current) => [thread, ...current]);
        threadId = thread.id;
        setActiveId(thread.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not start a chat.");
        return;
      }
    }

    setError(null);
    clearDraft();
    setBusy(true);
    pinnedRef.current = true;

    // Show the question immediately with a placeholder id. The server's real
    // document arrives on the next load; waiting for it would make the UI feel
    // like it dropped the message. The "pending-" prefix cannot collide with
    // an ObjectId, which is hex only.
    setMessages((current) => [
      ...current,
      {
        id: `pending-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    let accumulated = "";

    try {
      await api.sendAssistantMessage(
        threadId,
        text,
        (delta) => {
          accumulated += delta;
          setStreaming(accumulated);
        },
        controller.signal,
      );
      setMessages((current) => [
        ...current,
        {
          id: `pending-${Date.now()}-reply`,
          role: "assistant",
          content: accumulated,
          created_at: new Date().toISOString(),
        },
      ]);
      api.assistantThreads().then(setThreads).catch(() => undefined);
      api.assistantStatus().then(setStatus).catch(() => undefined);
    } catch (err) {
      if (controller.signal.aborted) {
        // Stopped on purpose. Keep whatever arrived — the server saved it too.
        if (accumulated) {
          setMessages((current) => [
            ...current,
            {
              id: `pending-${Date.now()}-partial`,
              role: "assistant",
              content: accumulated,
              created_at: new Date().toISOString(),
            },
          ]);
        }
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setStreaming("");
      setBusy(false);
      abortRef.current = null;
    }
  };

  // Named helper so the draft clear reads intentionally next to the optimistic
  // append above, rather than looking like a stray setState.
  function clearDraft() {
    setDraft("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }

  const stop = () => abortRef.current?.abort();

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter is a newline — the convention every chat app
    // has trained people into.
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

  const unavailable = status && (!status.enabled || !status.available);
  const remaining = status ? status.daily_limit - status.messages_today : null;

  return (
    <Page>
      <main className="wrap assistant-page">
        <div className="assistant-head">
          <div>
            <h1 className="h-section">Assistant</h1>
            <p className="small muted">
              Ask anything. It also knows your account — your bot, your plan, and the
              questions your bot couldn&apos;t answer.
            </p>
          </div>
          {status?.available && (
            <div className="assistant-meta">
              <span className="badge">{status.model}</span>
              {remaining !== null && (
                <span className="small muted">{remaining} messages left today</span>
              )}
            </div>
          )}
        </div>

        {unavailable && (
          <div className="notice-box mb-4">
            {!status?.enabled
              ? "The assistant is switched off on this server. Set ASSISTANT_ENABLED=true to turn it on."
              : "The assistant's model isn't reachable. Check that Ollama is running."}
          </div>
        )}

        <div className="chat-shell">
          <aside className="chat-threads">
            <button className="btn btn-secondary btn-sm btn-block" onClick={newThread}>
              New chat
            </button>
            <div className="chat-thread-list">
              {threads.length === 0 && (
                <p className="small muted">No conversations yet.</p>
              )}
              {threads.map((thread) => (
                <div
                  key={thread.id}
                  className={`chat-thread ${thread.id === activeId ? "is-active" : ""}`}
                >
                  <button
                    className="chat-thread-open"
                    onClick={() => openThread(thread.id)}
                    title={thread.title}
                  >
                    {thread.title}
                  </button>
                  <button
                    className="chat-thread-del"
                    onClick={() => removeThread(thread.id)}
                    aria-label={`Delete ${thread.title}`}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </aside>

          <section className="chat-main">
            <div className="chat-log" onScroll={onScroll}>
              {messages.length === 0 && !streaming && (
                <div className="chat-empty">
                  <p className="muted">Ask a question to get started.</p>
                  <div className="assistant-suggestions">
                    {[
                      "Why is my bot declining so many questions?",
                      "Write a polite refund policy for my FAQ sheet",
                      "Explain how vector search works, simply",
                    ].map((suggestion) => (
                      <button
                        key={suggestion}
                        className="assistant-suggestion"
                        // Filling a composer that can't send from is worse than
                        // offering nothing: it looks like the app swallowed
                        // the message.
                        disabled={!!unavailable}
                        onClick={() => {
                          setDraft(suggestion);
                          textareaRef.current?.focus();
                        }}
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((message) => (
                <div key={message.id} className={`bubble bubble-${message.role}`}>
                  {message.role === "assistant" ? (
                    <div
                      className="md"
                      // Sanitised in renderMarkdown — see the note there on why
                      // model output is treated as untrusted input.
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
                    />
                  ) : (
                    <p>{message.content}</p>
                  )}
                </div>
              ))}

              {streaming && (
                <div className="bubble bubble-assistant">
                  <div
                    className="md"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(streaming) }}
                  />
                </div>
              )}

              {busy && !streaming && (
                <div className="bubble bubble-assistant">
                  <span className="thinking" aria-label="Thinking">
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
              )}

              <div ref={bottomRef} />
            </div>

            {error && <p className="error-box chat-error">{error}</p>}

            <div className="chat-composer">
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={grow}
                onKeyDown={onKeyDown}
                placeholder="Ask anything…"
                rows={1}
                disabled={busy || !!unavailable}
              />
              {busy ? (
                <button className="btn btn-ghost" onClick={stop}>
                  Stop
                </button>
              ) : (
                <button
                  className="btn btn-primary"
                  onClick={send}
                  disabled={!draft.trim() || !!unavailable}
                >
                  Send
                </button>
              )}
            </div>
          </section>
        </div>
      </main>
    </Page>
  );
}
