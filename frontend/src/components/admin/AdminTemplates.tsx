import { useState } from "react";

import { adminPanelApi, type AdminTemplate } from "../../lib/api";

import { Loading, Panel, when, useAdminData } from "./bits";

const TEXT_FIELDS: { id: keyof AdminTemplate & string; label: string; hint: string }[] = [
  { id: "name", label: "Name", hint: "Shown in the picker." },
  { id: "tagline", label: "Tagline", hint: "One line under the name." },
  { id: "description", label: "Description", hint: "The longer blurb." },
  {
    id: "scope_label",
    label: "Scope",
    hint: "What the bot is allowed to be about — quoted back when it refuses.",
  },
  {
    id: "decline_message",
    label: "Decline message",
    hint: "Said word-for-word when a question is out of scope.",
  },
];

/**
 * Template management.
 *
 * The code catalogue stays the baseline; this screen writes a patch over it.
 * That is why every field shows its code default and why "reset" is always
 * available — an edit made here is meant to be undoable by anyone, without a
 * deploy and without knowing what the original text was.
 */
export function AdminTemplates({ adminKey }: { adminKey: string }) {
  const { data, error, loading, reload } = useAdminData<{
    templates: AdminTemplate[];
    editable_fields: string[];
  }>(() => adminPanelApi.templates(adminKey), [adminKey]);

  const [open, setOpen] = useState<string | null>(null);

  if (error) return <div className="error-box">{error}</div>;
  if (loading || !data) return <Loading />;

  return (
    <Panel title="Templates">
      <p className="small mb-4">
        Edits reach every bot on the template immediately, including ones already
        answering. Starter sheets, sample questions and the per-vertical action
        guards stay in code — those carry a template's safety reasoning, not its
        copy.
      </p>

      {data.templates.map((template) => (
        <TemplateRow
          key={template.id}
          adminKey={adminKey}
          template={template}
          open={open === template.id}
          onToggle={() => setOpen(open === template.id ? null : template.id)}
          onChanged={reload}
        />
      ))}
    </Panel>
  );
}

function TemplateRow({
  adminKey,
  template,
  open,
  onToggle,
  onChanged,
}: {
  adminKey: string;
  template: AdminTemplate;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const value = (field: string) =>
    draft[field] ?? String((template as unknown as Record<string, unknown>)[field] ?? "");

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setDraft({});
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That edit was refused.");
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    const changes: Record<string, unknown> = {};
    for (const [field, text] of Object.entries(draft)) {
      changes[field] =
        field.endsWith("_threshold") ? Number(text) : text;
    }
    if (Object.keys(changes).length === 0) return;
    return run(() => adminPanelApi.updateTemplate(adminKey, template.id, changes));
  };

  return (
    <div className="admin-template" data-open={open}>
      <button className="admin-template-head" onClick={onToggle}>
        <span className="admin-template-icon" aria-hidden="true">
          {template.icon}
        </span>
        <span className="admin-template-name">
          {template.name}
          <span className="tiny muted">
            {template.bots} bot{template.bots === 1 ? "" : "s"} · {template.bots_ready} live ·
            strong {template.strong_threshold} / near {template.near_threshold}
          </span>
        </span>
        <span className="row gap-3">
          {template.overridden_fields.length > 0 && (
            <span className="badge badge-warn">
              edited {when(template.updated_at)}
            </span>
          )}
          <span className={`badge ${template.enabled ? "badge-live" : "badge-off"}`}>
            <span className="dot" />
            {template.enabled ? "offered" : "retired"}
          </span>
        </span>
      </button>

      {open && (
        <div className="admin-template-body">
          {error && <div className="error-box">{error}</div>}

          {TEXT_FIELDS.map((field) => {
            const overridden = template.overridden_fields.includes(field.id);
            return (
              <div className="field" key={field.id}>
                <label className="label" htmlFor={`${template.id}-${field.id}`}>
                  {field.label}
                  {overridden && <span className="accent"> · edited</span>}
                </label>
                <textarea
                  id={`${template.id}-${field.id}`}
                  className="input"
                  rows={field.id === "description" || field.id === "decline_message" ? 3 : 2}
                  value={value(field.id)}
                  onChange={(e) => setDraft({ ...draft, [field.id]: e.target.value })}
                />
                <p className="tiny mt-3 muted">
                  {field.hint}
                  {overridden && (
                    <>
                      {" "}Code default: <em>{String(template.defaults[field.id] ?? "")}</em>
                    </>
                  )}
                </p>
              </div>
            );
          })}

          <div className="row gap-4" style={{ flexWrap: "wrap" }}>
            <div className="field" style={{ flex: "1 1 160px" }}>
              <label className="label" htmlFor={`${template.id}-strong`}>
                Strong threshold
              </label>
              <input
                id={`${template.id}-strong`}
                className="input mono"
                value={value("strong_threshold")}
                onChange={(e) => setDraft({ ...draft, strong_threshold: e.target.value })}
              />
              <p className="tiny mt-3 muted">
                How close a match must be before the bot answers at all.
              </p>
            </div>
            <div className="field" style={{ flex: "1 1 160px" }}>
              <label className="label" htmlFor={`${template.id}-near`}>
                Near threshold
              </label>
              <input
                id={`${template.id}-near`}
                className="input mono"
                value={value("near_threshold")}
                onChange={(e) => setDraft({ ...draft, near_threshold: e.target.value })}
              />
              <p className="tiny mt-3 muted">
                Below this it declines instead of offering a near match.
              </p>
            </div>
          </div>

          <div className="row gap-3 mt-4" style={{ flexWrap: "wrap" }}>
            <button
              className="btn btn-primary btn-sm"
              disabled={busy || Object.keys(draft).length === 0}
              onClick={save}
            >
              {busy ? "Saving…" : "Save changes"}
            </button>
            <button
              className="btn btn-secondary btn-sm"
              disabled={busy}
              onClick={() =>
                run(() =>
                  adminPanelApi.updateTemplate(adminKey, template.id, {
                    enabled: !template.enabled,
                  }),
                )
              }
            >
              {template.enabled ? "Retire from the picker" : "Offer again"}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={busy || template.overridden_fields.length === 0}
              onClick={() => run(() => adminPanelApi.resetTemplate(adminKey, template.id))}
            >
              Reset to code defaults
            </button>
          </div>

          <p className="tiny mt-3 muted">
            Retiring hides a template from new bots. Bots already running on it keep
            working exactly as they are.
          </p>
        </div>
      )}
    </div>
  );
}
