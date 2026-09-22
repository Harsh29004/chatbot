"use client";

import { useEffect, useMemo, useState } from "react";

import { EVENTS, track } from "../lib/analytics";
import { api } from "../lib/api-client";
import type { BotTemplate } from "../lib/types";

const STRICTNESS_COPY: Record<string, string> = {
  strict: "Answers only on a very close match",
  balanced: "Answers on a close match",
  open: "Answers on a reasonable match",
};

interface Props {
  selectedId?: string | null;
  onSelect?: (template: BotTemplate) => void;
  /** Landing page uses the read-only variant — no selection, no CTA. */
  readOnly?: boolean;
  busyId?: string | null;
  /** Fetched on the server so the cards are in the first painted frame. */
  initialTemplates?: BotTemplate[] | null;
}

/**
 * The ten ready-made bots.
 *
 * Each card leads with what the bot *refuses*, not just what it covers —
 * that boundary is the product, and it is the thing a buyer is actually
 * deciding between.
 */
export function TemplatePicker({
  selectedId,
  onSelect,
  readOnly,
  busyId,
  initialTemplates = null,
}: Props) {
  const [templates, setTemplates] = useState<BotTemplate[] | null>(initialTemplates);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<string>("All");
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    if (initialTemplates) return; // already rendered from the server
    api
      .templates()
      .then(setTemplates)
      .catch((e: Error) => setError(e.message));
  }, [initialTemplates]);

  const categories = useMemo(() => {
    if (!templates) return ["All"];
    return ["All", ...Array.from(new Set(templates.map((t) => t.category)))];
  }, [templates]);

  const visible = useMemo(() => {
    if (!templates) return [];
    return category === "All"
      ? templates
      : templates.filter((t) => t.category === category);
  }, [templates, category]);

  if (error) {
    return <div className="error-box">Couldn't load templates: {error}</div>;
  }

  if (!templates) {
    return (
      <div className="center" style={{ padding: "var(--s8)" }}>
        <div className="spinner wrap-center" />
      </div>
    );
  }

  return (
    <>
      <div className="chip-row" role="group" aria-label="Filter by industry">
        {categories.map((c) => (
          <button
            key={c}
            className="chip"
            data-active={c === category}
            onClick={() => setCategory(c)}
          >
            {c}
          </button>
        ))}
      </div>

      <div className="template-grid">
        {visible.map((template) => {
          const isSelected = template.id === selectedId;
          const isOpen = expanded === template.id;

          return (
            <article
              key={template.id}
              className="template-card"
              data-selected={isSelected}
            >
              <div className="template-head">
                <span className="template-icon" aria-hidden="true">
                  {template.icon}
                </span>
                <div>
                  <h3 className="template-name">{template.name}</h3>
                  <p className="tiny">{template.tagline}</p>
                </div>
                {isSelected && <span className="badge badge-live">In use</span>}
              </div>

              <p className="small template-desc">{template.description}</p>

              <dl className="template-meta">
                <div>
                  <dt className="tiny">Answers about</dt>
                  <dd className="small">{template.scope_label}</dd>
                </div>
                <div>
                  <dt className="tiny">Everything else</dt>
                  <dd className="small template-decline">
                    “{template.decline_message}”
                  </dd>
                </div>
              </dl>

              {isOpen && (
                <div className="template-expand">
                  <p className="tiny mb-3">Questions it handles from your sheet</p>
                  <ul className="sample-list">
                    {template.sample_questions.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>

                  <p className="tiny mt-4 mb-3">Suggested sheet categories</p>
                  <div className="chip-row">
                    {template.starter_categories.map((c) => (
                      <span className="chip chip-static" key={c}>
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="template-foot">
                <span className="tiny" title={`match ≥ ${template.strong_threshold}`}>
                  {STRICTNESS_COPY[template.strictness]}
                </span>
                <div className="row gap-2">
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => {
                      setExpanded(isOpen ? null : template.id);
                      // Expanding is the step before choosing, and the ratio
                      // between them is what says whether the template
                      // descriptions are doing their job.
                      if (!isOpen) {
                        track(EVENTS.TEMPLATE_VIEWED, {
                          template_id: template.id,
                          category: template.category,
                        });
                      }
                    }}
                    aria-expanded={isOpen}
                  >
                    {isOpen ? "Less" : "Details"}
                  </button>
                  {!readOnly && (
                    <button
                      className={`btn btn-sm ${isSelected ? "btn-secondary" : "btn-primary"}`}
                      onClick={() => onSelect?.(template)}
                      disabled={busyId === template.id || isSelected}
                    >
                      {busyId === template.id ? (
                        <span className="spinner" />
                      ) : isSelected ? (
                        "Selected"
                      ) : (
                        "Use this"
                      )}
                    </button>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </>
  );
}
