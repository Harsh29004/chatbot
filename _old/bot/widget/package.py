"""
Build the downloadable install package for one widget theme.

The package holds **no API key**. The key is typed in at install time, so a
zip forwarded to the wrong person — or left in a public repo — hands over a
design, not access.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from bot.widget.themes import WidgetTheme, widget_theme_dict

WIDGET_SOURCE = Path(__file__).resolve().parent / "nexora-widget.js"

PACKAGE_VERSION = "1.0.0"


def _js_literal(value: object) -> str:
    # "</" is escaped so the output stays safe if someone pastes it inline
    # into a <script> block instead of loading the file.
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _ascii_only(source: str) -> str:
    """
    Escape every non-ASCII character as ``\\uXXXX``.

    Customers host this file on servers that often send ``.js`` with no
    charset, and browsers then read UTF-8 as Latin-1 — "×" arrives as "Ã—"
    and every emoji greeting turns to garbage. Pure ASCII reads the same
    under any encoding. Non-ASCII only appears inside string literals and
    comments, where the escape is valid or inert.
    """
    out = []
    for char in source:
        if ord(char) < 128:
            out.append(char)
            continue
        encoded = char.encode("utf-16-be")
        for i in range(0, len(encoded), 2):
            out.append(f"\\u{int.from_bytes(encoded[i:i + 2], 'big'):04x}")
    return "".join(out)


def build_widget_script(theme: WidgetTheme, api_base: str) -> str:
    """The widget source with this theme and API origin baked in."""
    source = WIDGET_SOURCE.read_text(encoding="utf-8")
    source = source.replace("/*__NEXORA_THEME__*/ null", _js_literal(widget_theme_dict(theme)), 1)
    source = source.replace('/*__NEXORA_API_BASE__*/ ""', _js_literal(api_base), 1)
    return _ascii_only(source)


def _demo_html(theme: WidgetTheme) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nexora widget — {theme.name} demo</title>
  <style>
    body {{ margin: 0; min-height: 100vh; font-family: system-ui, sans-serif;
           background: {"#0b0f17" if theme.dark else "#f4f5f8"}; color: {"#e6edf7" if theme.dark else "#1d1f2c"};
           display: grid; place-items: center; padding: 16px; }}
    main {{ max-width: 560px; }}
    code {{ background: rgba(127,127,127,.15); padding: 2px 6px; border-radius: 4px; }}
  </style>
</head>
<body>
  <main>
    <h1>{theme.name} widget demo</h1>
    <p>Replace <code>YOUR_API_KEY</code> below with a key from your Nexora dashboard,
       then open this file in a browser. The chat button appears bottom-right.</p>
  </main>

  <!-- Nexora chat widget -->
  <script src="nexora-widget.js" data-api-key="YOUR_API_KEY" defer></script>
</body>
</html>
"""


def _readme(theme: WidgetTheme, api_base: str) -> str:
    return f"""# Nexora chat widget — {theme.name}

{theme.description}

Version {PACKAGE_VERSION} · Layout: **{theme.layout}** · API: `{api_base}`

---

## 1. Install (2 minutes)

1. Copy `nexora-widget.js` into your website's public/static folder.
2. Paste this just before `</body>` on every page that should show the chat:

```html
<script src="/nexora-widget.js" data-api-key="YOUR_API_KEY" defer></script>
```

3. Replace `YOUR_API_KEY` with a key from **Dashboard → API keys**.

That's it. On load the widget calls `/v1/widget/activate` to check the key.
The status dot in the header turns **green** when it's activated; if the key
is wrong, revoked, or your FAQ sheet isn't uploaded yet, the panel says so.

Activation is free. Each question costs credits exactly like `POST /v1/ask`.

### Options (all optional)

| Attribute | Example | What it does |
|---|---|---|
| `data-api-key` | `nxk_…` | Activates the widget with your key |
| `data-endpoint` | `/api/chat` | Send questions to **your own server** instead (see section 3) |
| `data-title` | `Acme Support` | Header title |
| `data-greeting` | `Hi! Ask me about orders.` | First message |
| `data-placeholder` | `Ask a question` | Input placeholder |
| `data-primary-color` | `#e11d48` | Swap the brand colour |
| `data-position` | `left` | Launcher on the left instead of the right |
| `data-open` | `true` | Open the panel on page load |
| `data-branding` | `false` | Hide "Powered by Nexora AI" |
| `data-api-base` | `https://api.example.com` | Override the API origin baked into this file |

### Install from JavaScript instead (React, Next.js, Vue…)

Load the file without `data-api-key`, then call `init` yourself:

```html
<script src="/nexora-widget.js" defer></script>
```

```js
window.NexoraWidget.init({{
  apiKey: "YOUR_API_KEY",              // or: endpoint: "/api/chat"
  title: "Acme Support",
  greeting: "Hi! How can I help?",
  suggestions: ["What are your hours?", "How do I return an item?"],
  position: "right",
  theme: {{ primary: "#e11d48" }},        // override any theme colour
}});

// Control it from your own buttons:
window.NexoraWidget.open();
window.NexoraWidget.close();
window.NexoraWidget.ask("Do you ship internationally?");
```

React example:

```jsx
useEffect(() => {{
  const s = document.createElement("script");
  s.src = "/nexora-widget.js";
  s.onload = () => window.NexoraWidget.init({{ apiKey: process.env.NEXT_PUBLIC_NEXORA_KEY }});
  document.body.appendChild(s);
  return () => {{ window.NexoraWidget?.destroy(); s.remove(); }};
}}, []);
```

### WordPress / Shopify / Wix

Upload `nexora-widget.js` to your media/assets, then paste the `<script>` tag
from step 2 into your theme footer (WordPress: *Appearance → Theme File Editor
→ footer.php*, or any "Insert Headers and Footers" plugin; Shopify:
*Online Store → Edit code → theme.liquid*, before `</body>`).

---

## 2. ⚠️ About putting the key in the page

A key in a `<script>` tag is visible to anyone who views your page source,
and anyone holding it can spend your credits. That's fine for trying the
widget out. **For a production site, use section 3** — your key stays on your
server and the browser never sees it.

---

## 3. Keep the key on your server (recommended for production)

Point the widget at your own backend:

```html
<script src="/nexora-widget.js" data-endpoint="/api/chat" defer></script>
```

Your server adds the key and forwards the question. Ready-made proxies are in
`server-proxy/`:

- `server-proxy/node-express.js`
- `server-proxy/python-fastapi.py`
- `server-proxy/php-proxy.php`

Set `NEXORA_API_KEY` in your server's environment and mount the route.

---

## 4. API only — no widget

Already have your own chat UI, app, WhatsApp bot, or backend? Skip the widget
and call the API directly from your server:

```bash
curl -X POST {api_base}/v1/ask \\
  -H "X-Api-Key: $NEXORA_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"message": "What are your opening hours?", "session_id": "user-123"}}'
```

Response:

```json
{{
  "response": "We are open 9am–9pm, Monday to Saturday.",
  "mode": "strong",
  "matched_question": "What are your opening hours?",
  "confidence": 0.97
}}
```

| `mode` | Meaning |
|---|---|
| `strong` | Close match — answered straight from your FAQ sheet |
| `near` | Likely match — answered, with a nudge to contact you if it's not right |
| `decline` | Not covered by your sheet — polite refusal, logged in your dashboard's gap list |

Headers on every response: `X-Credits-Remaining`, `X-Credits-Daily-Limit`,
`X-Credits-Reset-At`, `X-Credit-Cost`.

Errors: `403` invalid/revoked key · `402` out of credits · `409` FAQ sheet not uploaded yet.

Check a key without spending credits: `GET {api_base}/v1/widget/activate` with `X-Api-Key`.
"""


_NODE_PROXY = """// Nexora chat proxy for Express. Keeps your API key on the server.
//   npm install express
//   NEXORA_API_KEY=nxk_... node node-express.js
// Then install the widget with data-endpoint="/api/chat".

const express = require("express");

const NEXORA_API = "__API_BASE__";
const app = express();
app.use(express.json({ limit: "16kb" }));

app.post("/api/chat", async (req, res) => {
  const { message, session_id } = req.body || {};
  if (typeof message !== "string" || !message.trim()) {
    return res.status(400).json({ detail: "message is required" });
  }
  try {
    const upstream = await fetch(`${NEXORA_API}/v1/ask`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Api-Key": process.env.NEXORA_API_KEY,
      },
      body: JSON.stringify({ message: message.slice(0, 2000), session_id: String(session_id || "anon").slice(0, 128) }),
    });
    res.status(upstream.status).json(await upstream.json());
  } catch (err) {
    res.status(502).json({ detail: "Chat service unavailable" });
  }
});

app.listen(process.env.PORT || 3000);
"""

_PYTHON_PROXY = '''"""
Nexora chat proxy for FastAPI. Keeps your API key on the server.

    pip install fastapi uvicorn httpx
    NEXORA_API_KEY=nxk_... uvicorn python-fastapi:app

Then install the widget with data-endpoint="/api/chat".
"""

import os

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

NEXORA_API = "__API_BASE__"

app = FastAPI()


class Ask(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field("anon", max_length=128)


@app.post("/api/chat")
async def chat(body: Ask):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            upstream = await client.post(
                f"{NEXORA_API}/v1/ask",
                headers={"X-Api-Key": os.environ["NEXORA_API_KEY"]},
                json=body.model_dump(),
            )
    except httpx.HTTPError:
        return JSONResponse({"detail": "Chat service unavailable"}, status_code=502)
    return JSONResponse(upstream.json(), status_code=upstream.status_code)
'''

_PHP_PROXY = """<?php
// Nexora chat proxy for PHP. Keeps your API key on the server.
// Put this file at e.g. /api/chat.php, set NEXORA_API_KEY in the environment,
// and install the widget with data-endpoint="/api/chat.php".

header('Content-Type: application/json');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['detail' => 'POST only']);
    exit;
}

$input = json_decode(file_get_contents('php://input'), true) ?: [];
$message = trim((string)($input['message'] ?? ''));
if ($message === '') {
    http_response_code(400);
    echo json_encode(['detail' => 'message is required']);
    exit;
}

$ch = curl_init('__API_BASE__/v1/ask');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 30,
    CURLOPT_HTTPHEADER => [
        'Content-Type: application/json',
        'X-Api-Key: ' . getenv('NEXORA_API_KEY'),
    ],
    CURLOPT_POSTFIELDS => json_encode([
        'message' => mb_substr($message, 0, 2000),
        'session_id' => mb_substr((string)($input['session_id'] ?? 'anon'), 0, 128),
    ]),
]);
$body = curl_exec($ch);
$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

if ($body === false) {
    http_response_code(502);
    echo json_encode(['detail' => 'Chat service unavailable']);
    exit;
}
http_response_code($status);
echo $body;
"""


def package_filename(theme: WidgetTheme) -> str:
    return f"nexora-widget-{theme.id}-{PACKAGE_VERSION}.zip"


def build_widget_package(theme: WidgetTheme, api_base: str) -> bytes:
    """A zip the customer unpacks straight into their site."""
    folder = f"nexora-widget-{theme.id}"
    config = {
        "name": f"nexora-widget-{theme.id}",
        "version": PACKAGE_VERSION,
        "api_base": api_base,
        "theme": widget_theme_dict(theme),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{folder}/nexora-widget.js", build_widget_script(theme, api_base))
        archive.writestr(f"{folder}/index.html", _demo_html(theme))
        archive.writestr(f"{folder}/README.md", _readme(theme, api_base))
        archive.writestr(f"{folder}/nexora.config.json", json.dumps(config, indent=2, ensure_ascii=False))
        archive.writestr(f"{folder}/server-proxy/node-express.js", _NODE_PROXY.replace("__API_BASE__", api_base))
        archive.writestr(f"{folder}/server-proxy/python-fastapi.py", _PYTHON_PROXY.replace("__API_BASE__", api_base))
        archive.writestr(f"{folder}/server-proxy/php-proxy.php", _PHP_PROXY.replace("__API_BASE__", api_base))
    return buffer.getvalue()
