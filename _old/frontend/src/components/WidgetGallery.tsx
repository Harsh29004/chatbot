import { useEffect, useState } from "react";

import { EVENTS, track } from "../lib/analytics";
import { api, PUBLIC_API_ORIGIN, type WidgetTheme } from "../lib/api";
import { CodeBlock } from "./Chrome";

const API_ORIGIN = PUBLIC_API_ORIGIN;

const plain = (source: string) => source.split("\n").map((text) => [{ text }]);

interface Props {
  /** Shown in snippets so the customer recognises which key goes where. */
  keyPrefix?: string;
  botReady: boolean;
}

/**
 * Seven ready-made chat designs, and the two ways to go live.
 *
 * "Widget" hands over an install package: drop in one script, add a key, and
 * the chat appears on the customer's site. "API only" is for anyone with their
 * own UI or backend, who needs nothing from us but the endpoint.
 */
export function WidgetGallery({ keyPrefix, botReady }: Props) {
  const [themes, setThemes] = useState<WidgetTheme[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState("aurora");
  const [mode, setMode] = useState<"widget" | "api">("widget");

  useEffect(() => {
    api
      .widgetThemes()
      .then(setThemes)
      .catch((e: Error) => setError(e.message));
  }, []);

  const key = keyPrefix ? `${keyPrefix}…` : "YOUR_API_KEY";
  const selected = themes?.find((t) => t.id === selectedId) ?? themes?.[0];

  return (
    <>
      <div className="chip-row" role="tablist" aria-label="Integration method">
        <button
          className="chip"
          role="tab"
          aria-selected={mode === "widget"}
          data-active={mode === "widget"}
          onClick={() => {
            setMode("widget");
            track(EVENTS.FEATURE_USED, { feature: "integration_tab", tab: "widget" });
          }}
        >
          Chat widget · 7 designs
        </button>
        <button
          className="chip"
          role="tab"
          aria-selected={mode === "api"}
          data-active={mode === "api"}
          onClick={() => {
            setMode("api");
            track(EVENTS.FEATURE_USED, { feature: "integration_tab", tab: "api" });
          }}
        >
          API only · your own backend
        </button>
      </div>

      {!botReady && (
        <p className="tiny mt-4 accent">
          Upload your FAQ sheet first — an installed widget shows “still being
          set up” until your bot has answers.
        </p>
      )}

      {mode === "widget" ? (
        error ? (
          <div className="error-box mt-5">Couldn't load designs: {error}</div>
        ) : !themes || !selected ? (
          <div className="center" style={{ padding: "var(--s8)" }}>
            <div className="spinner wrap-center" />
          </div>
        ) : (
          <>
            <div className="widget-grid">
              {themes.map((theme) => (
                <button
                  key={theme.id}
                  className="widget-card"
                  data-selected={theme.id === selected.id}
                  onClick={() => {
                    setSelectedId(theme.id);
                    track(EVENTS.WIDGET_PREVIEWED, { theme_id: theme.id });
                  }}
                  aria-pressed={theme.id === selected.id}
                >
                  <WidgetPreview theme={theme} />
                  <div className="widget-card-body">
                    <div className="row row-between">
                      <h3 className="template-name">{theme.name}</h3>
                      <span className="chip chip-static">{theme.layout}</span>
                    </div>
                    <p className="tiny mt-3">{theme.tagline}</p>
                    <p className="tiny mt-3 muted">{theme.best_for.join(" · ")}</p>
                  </div>
                </button>
              ))}
            </div>

            <div className="widget-install">
              <div className="row row-between" style={{ flexWrap: "wrap", gap: "var(--s3)" }}>
                <div>
                  <h3 className="h-card">Install {selected.name}</h3>
                  <p className="small mt-3">{selected.description}</p>
                </div>
                <a
                  className="btn btn-primary"
                  href={api.widgetPackageUrl(selected.id)}
                  download
                >
                  Download package
                </a>
              </div>

              <ol className="widget-steps">
                <li>
                  <strong>Unzip</strong> and copy <code className="mono">nexora-widget.js</code>{" "}
                  into your site's public folder.
                </li>
                <li>
                  <strong>Paste</strong> the script tag before <code className="mono">&lt;/body&gt;</code>{" "}
                  with your API key — the widget activates itself on load.
                </li>
                <li>
                  <strong>Done.</strong> A green dot in the chat header means the key
                  was accepted. The package README covers React, WordPress and Shopify.
                </li>
              </ol>

              <CodeBlock
                title="HTML — from the package"
                code={plain(
                  `<script src="/nexora-widget.js" data-api-key="${key}" defer></script>`,
                )}
              />

              <div className="mt-4">
                <CodeBlock
                  title="HTML — or load it hosted, no upload needed"
                  code={plain(
                    `<script src="${API_ORIGIN}/widget/v1/${selected.id}.js"\n        data-api-key="${key}" defer></script>`,
                  )}
                />
              </div>

              <p className="tiny mt-4">
                ⚠️ A key in a script tag is visible in your page source. For a live
                site, use <code className="mono">data-endpoint="/api/chat"</code> and the
                ready-made server proxy in the package (Node, Python, PHP) so the key
                stays on your server.
              </p>
            </div>
          </>
        )
      ) : (
        <div className="widget-install">
          <p className="small">
            Already have a chat UI, a mobile app or a WhatsApp bot? Call the API from
            your server and render the reply however you like. Keep the key in an
            environment variable — never ship it to a browser or app bundle.
          </p>

          <div className="widget-code-grid mt-5">
            <CodeBlock
              title="Node.js"
              code={plain(`const res = await fetch("${API_ORIGIN}/v1/ask", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Api-Key": process.env.NEXORA_API_KEY,
  },
  body: JSON.stringify({ message, session_id: userId }),
});
const { response, mode } = await res.json();`)}
            />
            <CodeBlock
              title="Python"
              code={plain(`import os, requests

r = requests.post(
    "${API_ORIGIN}/v1/ask",
    headers={"X-Api-Key": os.environ["NEXORA_API_KEY"]},
    json={"message": message, "session_id": user_id},
    timeout=30,
)
answer = r.json()["response"]`)}
            />
            <CodeBlock
              title="PHP"
              code={plain(`$ch = curl_init("${API_ORIGIN}/v1/ask");
curl_setopt_array($ch, [
  CURLOPT_POST => true,
  CURLOPT_RETURNTRANSFER => true,
  CURLOPT_HTTPHEADER => [
    "Content-Type: application/json",
    "X-Api-Key: " . getenv("NEXORA_API_KEY"),
  ],
  CURLOPT_POSTFIELDS => json_encode([
    "message" => $message, "session_id" => $userId,
  ]),
]);
$answer = json_decode(curl_exec($ch), true)["response"];`)}
            />
            <CodeBlock
              title="Response"
              code={plain(`{
  "response": "We're open 9am–9pm, Mon–Sat.",
  "mode": "strong",        // strong | near | decline
  "matched_question": "What are your hours?",
  "confidence": 0.97
}

403 bad key · 402 out of credits · 409 no sheet yet`)}
            />
          </div>
        </div>
      )}
    </>
  );
}

/** A static miniature of the widget, drawn from the theme's own tokens. */
function WidgetPreview({ theme }: { theme: WidgetTheme }) {
  const header =
    theme.header_style === "solid"
      ? theme.primary
      : `linear-gradient(135deg, ${theme.primary}, ${theme.primary_2})`;
  const small = Math.min(theme.radius, 6);
  const drawer = theme.layout === "drawer";

  return (
    <div
      className="widget-preview"
      style={{
        background:
          theme.header_style === "glass"
            ? "linear-gradient(135deg, #fbc2eb, #a6c1ee 60%, #c2e9fb)"
            : theme.dark
              ? "#05070c"
              : "#e9ebf1",
      }}
      aria-hidden="true"
    >
      <div
        className="widget-preview-panel"
        data-layout={theme.layout}
        style={{
          background: theme.background,
          borderColor: theme.border,
          borderRadius: drawer ? 0 : Math.min(theme.radius, 16),
          fontFamily: theme.font_family,
          backdropFilter: theme.header_style === "glass" ? "blur(8px)" : undefined,
        }}
      >
        <div className="widget-preview-head" style={{ background: header, color: theme.on_primary }}>
          <span className="widget-preview-avatar" />
          {theme.title}
        </div>
        <div className="widget-preview-log">
          <span
            className="widget-preview-msg"
            style={{
              background: theme.bot_bubble,
              color: theme.bot_text,
              border: `1px solid ${theme.border}`,
              borderRadius: theme.radius / 1.6,
              borderBottomLeftRadius: small,
            }}
          >
            {theme.greeting}
          </span>
          <span
            className="widget-preview-msg widget-preview-user"
            style={{
              background: theme.user_bubble,
              color: theme.user_text,
              borderRadius: theme.radius / 1.6,
              borderBottomRightRadius: small,
            }}
          >
            What are your hours?
          </span>
        </div>
        <div
          className="widget-preview-input"
          style={{
            background: theme.surface,
            borderColor: theme.border,
            color: theme.muted,
          }}
        >
          {theme.placeholder}
        </div>
      </div>
      {!drawer && (
        <span
          className="widget-preview-launcher"
          style={{
            background: header,
            borderRadius: theme.radius <= 6 ? theme.radius + 2 : 999,
          }}
        />
      )}
    </div>
  );
}
