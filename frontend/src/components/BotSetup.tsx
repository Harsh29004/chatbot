import { useRef, useState, type DragEvent } from "react";

import { api, type Answer, type Bot, type SheetUpload } from "../lib/api";

/* -------------------------------------------------------------------------- */
/* Sheet upload                                                               */
/* -------------------------------------------------------------------------- */

export function SheetUploader({
  bot,
  onUploaded,
}: {
  bot: Bot;
  onUploaded: (result: SheetUpload) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SheetUpload | null>(null);
  const [dragging, setDragging] = useState(false);

  // The server is the authority on what the importer can read; the fallback
  // only covers a client talking to an older build.
  const accepted =
    bot.accepted_formats && bot.accepted_formats.length > 0
      ? bot.accepted_formats
      : [".xlsx", ".xlsm", ".csv"];

  const send = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const uploaded = await api.uploadSheet(file);
      setResult(uploaded);
      onUploaded(uploaded);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void send(file);
  };

  return (
    <div>
      <div
        className="dropzone"
        data-dragging={dragging}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accepted.join(",")}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void send(file);
            e.target.value = "";
          }}
        />

        {busy ? (
          <>
            <div className="spinner wrap-center mb-4" />
            <p className="small">Indexing your sheet…</p>
          </>
        ) : (
          <>
            <span className="dropzone-icon" aria-hidden="true">
              ⬆
            </span>
            <p className="h-card">Drop your FAQ file here</p>
            <p className="small mt-3">
              Spreadsheet, CSV, JSON, JSONL, YAML, Markdown, text, Word, PDF or a
              zip of them. A table needs{" "}
              {bot.required_columns.map((col, i) => (
                <span key={col}>
                  {i > 0 && " and "}
                  <code className="mono accent">{col}</code>
                </span>
              ))}{" "}
              columns.{" "}
              {bot.optional_columns.map((col, i) => (
                <span key={col}>
                  {i > 0 && ", "}
                  <code className="mono">{col}</code>
                </span>
              ))}{" "}
              are optional but improve matching. Common alternatives —{" "}
              <code className="mono">q</code>/<code className="mono">a</code>,{" "}
              <code className="mono">prompt</code>/
              <code className="mono">completion</code> — are recognised too, and a
              document is read as <code className="mono">Q:</code> /{" "}
              <code className="mono">A:</code> pairs.
            </p>
            <p className="tiny mt-3">Re-uploading replaces everything indexed before.</p>
          </>
        )}
      </div>

      <div className="row gap-3 mt-4" style={{ flexWrap: "wrap" }}>
        <a
          className="btn btn-secondary btn-sm"
          href={api.starterSheetUrl(bot.template_id)}
          download
        >
          Download starter sheet
        </a>
        <span className="tiny">
          Pre-filled for {bot.template?.name ?? "this template"} — fill in your own
          answers and upload it back.
        </span>
      </div>

      {error && <div className="error-box mt-4">{error}</div>}

      {result && (
        <div className="notice-box mt-4">
          <strong>
            Indexed {result.documents_indexed} question
            {result.documents_indexed === 1 ? "" : "s"}.
          </strong>
          {result.categories.length > 0 && (
            <p className="tiny mt-3">Categories: {result.categories.join(", ")}</p>
          )}
          {result.warnings.length > 0 && (
            <ul className="warning-list mt-3">
              {result.warnings.map((w) => (
                <li key={w} className="tiny">
                  {w}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Answering mode                                                             */
/* -------------------------------------------------------------------------- */

/**
 * The opt-in switch for grounded rewording.
 *
 * The copy here is doing real work. Someone turning this on is trading a
 * guarantee they may not know they have — that every answer is word-for-word
 * theirs — for answers that read more naturally. That trade is stated plainly
 * rather than sold, and the switch is disabled outright when no local model is
 * running so nobody enables a setting that silently does nothing.
 */
export function AnsweringMode({
  bot,
  onChange,
}: {
  bot: Bot;
  onChange: (bot: Bot) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.setAnsweringMode(!bot.llm_enabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the setting.");
    } finally {
      setBusy(false);
    }
  };

  const unavailable = !bot.llm_available && !bot.llm_enabled;

  return (
    <div className="answer-mode">
      <div className="answer-mode-text">
        <div className="answer-mode-title">
          <strong>Reword answers</strong>
          <span className="badge" data-mode={bot.llm_enabled ? "grounded" : "decline"}>
            {bot.llm_enabled ? "On" : "Off"}
          </span>
        </div>
        <p className="small muted">
          {bot.llm_enabled
            ? "Close matches are rephrased by a model running on this server. Facts still come only from your sheet — every answer is checked against it before sending, and anything that doesn't match falls back to your exact wording."
            : "Answers are sent exactly as written in your sheet. Turn this on to have close matches rephrased so they read more naturally — the facts stay yours, the sentences won't."}
        </p>
        {unavailable && (
          <p className="small muted">No local model is running, so this can't be enabled yet.</p>
        )}
        {error && <p className="small error">{error}</p>}
      </div>
      <button
        type="button"
        className={bot.llm_enabled ? "btn btn-ghost" : "btn btn-secondary"}
        onClick={toggle}
        disabled={busy || unavailable}
      >
        {busy ? "Saving…" : bot.llm_enabled ? "Turn off" : "Turn on"}
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Live tester                                                                */
/* -------------------------------------------------------------------------- */

const MODE_LABEL: Record<Answer["mode"], string> = {
  strong: "Answered from your sheet",
  near: "Close match + handoff",
  // Named so the difference is unmissable in testing: the facts came from the
  // sheet, but the sentence did not. An owner should never discover that by
  // reading it in production.
  grounded: "Reworded from your sheet",
  decline: "Out of scope — refused",
};

/**
 * Ask the bot a question from the dashboard. Costs no credits: testing your
 * own bot is exactly the behaviour we want before someone goes live.
 */
export function BotTester({ bot }: { bot: Bot }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ask = async (text: string) => {
    const value = text.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api.preview(value));
    } catch (err) {
      setError((err as Error).message);
      setAnswer(null);
    } finally {
      setBusy(false);
    }
  };

  const samples = bot.template?.sample_questions.slice(0, 3) ?? [];

  return (
    <div>
      <form
        className="row gap-3"
        style={{ flexWrap: "wrap" }}
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
      >
        <input
          className="input"
          style={{ flex: 1, minWidth: 220 }}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask your bot something…"
          maxLength={2000}
        />
        <button className="btn btn-primary" disabled={busy || !question.trim()}>
          {busy ? <span className="spinner" /> : "Ask"}
        </button>
      </form>

      {samples.length > 0 && (
        <div className="chip-row mt-3">
          {samples.map((s) => (
            <button
              key={s}
              className="chip"
              onClick={() => {
                setQuestion(s);
                void ask(s);
              }}
            >
              {s}
            </button>
          ))}
          <button
            className="chip"
            onClick={() => {
              const probe = "What is the capital of France?";
              setQuestion(probe);
              void ask(probe);
            }}
            title="Check that off-topic questions get refused"
          >
            Try something off-topic
          </button>
        </div>
      )}

      {error && <div className="error-box mt-4">{error}</div>}

      {answer && (
        <div className="answer-card mt-4" data-mode={answer.mode}>
          <div className="row row-between mb-3">
            <span className="badge" data-mode={answer.mode}>
              {MODE_LABEL[answer.mode]}
            </span>
            <span className="tiny mono">
              confidence {answer.confidence.toFixed(2)}
            </span>
          </div>
          <p className="small">{answer.response}</p>
          {answer.matched_question && (
            <p className="tiny mt-3">Matched: “{answer.matched_question}”</p>
          )}
        </div>
      )}

      <p className="tiny mt-4">Previews here don't cost credits.</p>
    </div>
  );
}
