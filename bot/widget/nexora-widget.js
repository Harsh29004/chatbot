/*!
 * Nexora chat widget
 * No dependencies. Renders inside a Shadow DOM so the host page's CSS can't
 * reach it, and it can't reach the host page's.
 *
 * Install:
 *   <script src="nexora-widget.js" data-api-key="nxk_..." defer></script>
 * or, with the key kept on your own server:
 *   <script src="nexora-widget.js" data-endpoint="/api/chat" defer></script>
 */
(function () {
  "use strict";

  // Replaced when the package is built. Left as literals so the raw file
  // still parses and runs (with the fallback theme) during development.
  var THEME = /*__NEXORA_THEME__*/ null;
  var DEFAULT_API_BASE = /*__NEXORA_API_BASE__*/ "";

  if (window.NexoraWidget && window.NexoraWidget.__loaded) return;

  var FALLBACK_THEME = {
    id: "aurora", layout: "bubble", header_style: "gradient", launcher_icon: "chat",
    primary: "#5b5bf6", primary_2: "#a855f7", on_primary: "#ffffff",
    background: "#ffffff", surface: "#f5f6fb", text: "#1d1f2c", muted: "#6b7085",
    border: "#e4e6f0", user_bubble: "#5b5bf6", user_text: "#ffffff",
    bot_bubble: "#f0f1f8", bot_text: "#1d1f2c",
    font_family: "Inter, system-ui, sans-serif", radius: 18, dark: false,
    title: "Ask us anything", greeting: "Hi there 👋 How can we help you today?",
    placeholder: "Type your question…"
  };

  var ICONS = {
    chat: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H9l-4.2 3.6c-.3.3-.8 0-.8-.4V5.5Z"/>',
    spark: '<path d="M12 2.5l2.1 5.6 5.9 2-5.9 2L12 17.7l-2.1-5.6-5.9-2 5.9-2L12 2.5Z"/><path d="M18.5 15.5l.9 2.3 2.1.7-2.1.8-.9 2.2-.9-2.2-2.1-.8 2.1-.7.9-2.3Z"/>',
    help: '<circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9.4 9.3a2.7 2.7 0 0 1 5.2.9c0 1.8-2.6 2.2-2.6 3.8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17.2" r="1.2"/>',
    leaf: '<path d="M20 4c-9 0-15 4.5-15 11 0 1.6.5 3 1.3 4.1C8 13.8 12 10.5 16 9c-3.4 2.2-6.3 5.5-8 10.3 1 .5 2.1.7 3.3.7C18 20 20 12.5 20 4Z"/>',
    wave: '<path d="M4 6.5A3.5 3.5 0 0 1 7.5 3h9A3.5 3.5 0 0 1 20 6.5v6a3.5 3.5 0 0 1-3.5 3.5H12l-5 4v-4h.5A3.5 3.5 0 0 1 4 12.5v-6Z"/><circle cx="8.5" cy="9.5" r="1.1" fill="#fff"/><circle cx="12" cy="9.5" r="1.1" fill="#fff"/><circle cx="15.5" cy="9.5" r="1.1" fill="#fff"/>'
  };

  var currentScript = document.currentScript;

  function data(name) {
    return currentScript ? currentScript.getAttribute("data-" + name) : null;
  }

  function storage(kind) {
    try {
      var s = window[kind];
      var probe = "__nexora_probe__";
      s.setItem(probe, "1");
      s.removeItem(probe);
      return s;
    } catch (e) {
      return null;
    }
  }

  function sessionId() {
    var store = storage("localStorage");
    var id = store && store.getItem("nexora_session_id");
    if (!id) {
      id = "w_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      if (store) store.setItem("nexora_session_id", id);
    }
    return id;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function svg(name) {
    return '<svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" aria-hidden="true">' +
      (ICONS[name] || ICONS.chat) + "</svg>";
  }

  function css(t, position) {
    var side = position === "left" ? "left" : "right";
    var drawer = t.layout === "drawer";
    var header =
      t.header_style === "gradient"
        ? "linear-gradient(135deg," + t.primary + "," + t.primary_2 + ")"
        : t.header_style === "glass"
          ? "linear-gradient(135deg," + t.primary + "cc," + t.primary_2 + "cc)"
          : t.primary;
    var r = t.radius;

    return [
      ":host{all:initial}",
      "*{box-sizing:border-box}",
      ".nx{--p:" + t.primary + ";--p2:" + t.primary_2 + ";--on:" + t.on_primary + ";--bg:" + t.background +
        ";--sf:" + t.surface + ";--tx:" + t.text + ";--mu:" + t.muted + ";--bd:" + t.border +
        ";--ub:" + t.user_bubble + ";--ut:" + t.user_text + ";--bb:" + t.bot_bubble + ";--bt:" + t.bot_text +
        ";--r:" + r + "px;font-family:" + t.font_family + ";font-size:14.5px;line-height:1.5;color:var(--tx)}",

      ".launch{position:fixed;bottom:22px;" + side + ":22px;z-index:2147483646;width:60px;height:60px;border:0;border-radius:" +
        (r <= 6 ? r + 4 : 999) + "px;background:" + header + ";color:var(--on);cursor:pointer;display:grid;place-items:center;" +
        "box-shadow:0 10px 30px -8px " + t.primary + "99,0 2px 6px rgba(0,0,0,.15);transition:transform .2s ease}",
      ".launch:hover{transform:translateY(-2px) scale(1.04)}",
      ".launch:focus-visible,.send:focus-visible,.x:focus-visible{outline:3px solid " + t.primary_2 + ";outline-offset:3px}",
      ".launch .close{display:none;font-size:26px;line-height:1}",
      ".launch[aria-expanded=true] .ico{display:none}.launch[aria-expanded=true] .close{display:block}",

      ".panel{position:fixed;z-index:2147483647;display:flex;flex-direction:column;overflow:hidden;background:var(--bg);color:var(--tx);" +
        "border:1px solid var(--bd);box-shadow:0 24px 60px -12px rgba(15,20,40,.35);opacity:0;pointer-events:none;" +
        (t.header_style === "glass" ? "backdrop-filter:blur(18px) saturate(160%);-webkit-backdrop-filter:blur(18px) saturate(160%);" : "") +
        (drawer
          ? "top:0;bottom:0;" + side + ":0;width:400px;max-width:100vw;border-radius:0;transform:translateX(" + (side === "right" ? "" : "-") + "100%);transition:transform .28s ease,opacity .2s ease}"
          : "bottom:96px;" + side + ":22px;width:380px;height:min(600px,calc(100vh - 120px));border-radius:" + r + "px;transform:translateY(12px) scale(.98);transform-origin:bottom " + side + ";transition:transform .2s ease,opacity .2s ease}"),
      ".panel[data-open=true]{opacity:1;pointer-events:auto;transform:none}",

      ".head{display:flex;align-items:center;gap:12px;padding:16px 18px;background:" + header + ";color:var(--on)}",
      ".avatar{width:38px;height:38px;border-radius:" + (r <= 6 ? r : 999) + "px;background:rgba(255,255,255,.18);display:grid;place-items:center;flex:none}",
      ".avatar svg{width:20px;height:20px}",
      ".title{font-weight:700;font-size:15.5px;margin:0}",
      ".status{font-size:12px;opacity:.85;margin:2px 0 0;display:flex;align-items:center;gap:6px}",
      ".dot{width:7px;height:7px;border-radius:50%;background:#34d399;box-shadow:0 0 0 3px rgba(52,211,153,.25)}",
      ".dot[data-state=activating]{background:#fbbf24;box-shadow:0 0 0 3px rgba(251,191,36,.25)}",
      ".dot[data-state=error]{background:#f87171;box-shadow:0 0 0 3px rgba(248,113,113,.25)}",
      ".x{margin-left:auto;background:transparent;border:0;color:inherit;font-size:24px;cursor:pointer;opacity:.85;padding:4px 8px;border-radius:8px}",
      ".x:hover{opacity:1;background:rgba(255,255,255,.14)}",

      ".log{flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:10px;background:" +
        (t.header_style === "glass" ? "transparent" : "var(--bg)") + "}",
      ".msg{max-width:84%;padding:10px 14px;border-radius:var(--r);white-space:pre-wrap;word-wrap:break-word;animation:in .18s ease}",
      ".bot{align-self:flex-start;background:var(--bb);color:var(--bt);border:1px solid var(--bd);border-bottom-left-radius:" + Math.min(r, 6) + "px}",
      ".user{align-self:flex-end;background:var(--ub);color:var(--ut);border-bottom-right-radius:" + Math.min(r, 6) + "px}",
      ".note{align-self:center;text-align:center;font-size:12.5px;color:var(--mu);max-width:92%;padding:8px 12px;border:1px dashed var(--bd);border-radius:10px}",
      ".typing{display:inline-flex;gap:4px;padding:14px}",
      ".typing i{width:6px;height:6px;border-radius:50%;background:var(--mu);animation:b 1s infinite ease-in-out}",
      ".typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}",
      "@keyframes b{0%,80%,100%{transform:translateY(0);opacity:.4}40%{transform:translateY(-4px);opacity:1}}",
      "@keyframes in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}",

      ".chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 18px 10px}",
      ".chip{border:1px solid var(--bd);background:var(--sf);color:var(--tx);font:inherit;font-size:12.5px;padding:6px 11px;border-radius:999px;cursor:pointer}",
      ".chip:hover{border-color:var(--p)}",

      ".compose{display:flex;gap:8px;padding:12px;border-top:1px solid var(--bd);background:var(--sf)}",
      ".input{flex:1;min-width:0;font:inherit;color:var(--tx);background:var(--bg);border:1px solid var(--bd);border-radius:" + Math.max(6, Math.min(r, 14)) + "px;padding:10px 12px;outline:none}",
      ".input::placeholder{color:var(--mu)}",
      ".input:focus{border-color:var(--p);box-shadow:0 0 0 3px " + t.primary + "33}",
      ".send{flex:none;width:44px;border:0;border-radius:" + Math.max(6, Math.min(r, 14)) + "px;background:" + header + ";color:var(--on);cursor:pointer;display:grid;place-items:center}",
      ".send:disabled,.input:disabled{opacity:.5;cursor:not-allowed}",
      ".brand{text-align:center;font-size:11px;color:var(--mu);padding:6px 0 8px;background:var(--sf)}",
      ".brand a{color:inherit}",

      "@media (max-width:480px){.panel{top:0;bottom:0;left:0;right:0;width:100vw;height:auto;border-radius:0}.launch{bottom:16px;" + side + ":16px}}",
      "@media (prefers-reduced-motion:reduce){.panel,.launch,.msg{transition:none;animation:none}}"
    ].join("\n");
  }

  function create(options) {
    var cfg = {
      apiKey: options.apiKey || data("api-key") || "",
      endpoint: options.endpoint || data("endpoint") || "",
      apiBase: (options.apiBase || data("api-base") || DEFAULT_API_BASE || "").replace(/\/+$/, ""),
      position: options.position || data("position") || "right",
      openOnLoad: options.openOnLoad != null ? !!options.openOnLoad : data("open") === "true",
      suggestions: options.suggestions || [],
      showBranding: options.showBranding !== false && data("branding") !== "false"
    };

    var t = {};
    var base = THEME || FALLBACK_THEME;
    for (var k in base) t[k] = base[k];
    var themeOverrides = options.theme || {};
    for (var o in themeOverrides) t[o] = themeOverrides[o];
    t.title = options.title || data("title") || t.title;
    t.greeting = options.greeting || data("greeting") || t.greeting;
    t.placeholder = options.placeholder || data("placeholder") || t.placeholder;
    if (data("primary-color")) t.primary = t.primary_2 = t.user_bubble = data("primary-color");

    var state = { status: "activating", busy: false, open: false, session: sessionId() };

    // -- DOM ----------------------------------------------------------------
    var host = el("div");
    host.id = "nexora-widget";
    var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
    var style = el("style");
    style.textContent = css(t, cfg.position);
    var wrap = el("div", "nx");

    var launcher = el("button", "launch");
    launcher.type = "button";
    launcher.setAttribute("aria-label", "Open chat");
    launcher.setAttribute("aria-expanded", "false");
    launcher.innerHTML = '<span class="ico">' + svg(t.launcher_icon) + '</span><span class="close" aria-hidden="true">×</span>';

    var panel = el("section", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", t.title);
    panel.setAttribute("data-open", "false");

    var head = el("header", "head");
    var avatar = el("div", "avatar");
    avatar.innerHTML = svg(t.launcher_icon);
    var titles = el("div");
    titles.appendChild(el("p", "title", t.title));
    var status = el("p", "status");
    var dot = el("span", "dot");
    var statusText = el("span", null, "Connecting…");
    status.appendChild(dot);
    status.appendChild(statusText);
    titles.appendChild(status);
    var closeBtn = el("button", "x", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close chat");
    head.appendChild(avatar);
    head.appendChild(titles);
    head.appendChild(closeBtn);

    var log = el("div", "log");
    log.setAttribute("aria-live", "polite");
    var chips = el("div", "chips");

    var form = el("form", "compose");
    var input = el("input", "input");
    input.type = "text";
    input.maxLength = 2000;
    input.placeholder = t.placeholder;
    input.setAttribute("aria-label", "Your question");
    var send = el("button", "send");
    send.type = "submit";
    send.setAttribute("aria-label", "Send");
    send.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M3.4 20.4 21 12 3.4 3.6 3.4 10l12.6 2-12.6 2z"/></svg>';
    form.appendChild(input);
    form.appendChild(send);

    panel.appendChild(head);
    panel.appendChild(log);
    panel.appendChild(chips);
    panel.appendChild(form);
    if (cfg.showBranding) {
      var brand = el("div", "brand");
      brand.textContent = "Powered by Nexora AI";
      panel.appendChild(brand);
    }

    wrap.appendChild(launcher);
    wrap.appendChild(panel);
    root.appendChild(style);
    root.appendChild(wrap);
    document.body.appendChild(host);

    // -- Behaviour ----------------------------------------------------------
    function scroll() {
      log.scrollTop = log.scrollHeight;
    }

    function add(kind, text) {
      var node = el("div", "msg " + kind, text);
      if (kind === "note") node.className = "note";
      log.appendChild(node);
      scroll();
      return node;
    }

    function setStatus(next, text) {
      state.status = next;
      dot.setAttribute("data-state", next);
      statusText.textContent = text;
      var usable = next === "ready";
      input.disabled = !usable;
      send.disabled = !usable || state.busy;
    }

    function renderChips(list) {
      chips.innerHTML = "";
      (list || []).slice(0, 4).forEach(function (q) {
        var chip = el("button", "chip", q);
        chip.type = "button";
        chip.addEventListener("click", function () {
          ask(q);
        });
        chips.appendChild(chip);
      });
    }

    function explain(status, detail) {
      if (status === 0) return "Can't reach the chat server right now. Please try again shortly.";
      if (status === 401 || status === 403) return "This chat widget isn't activated: the API key is missing, invalid or revoked.";
      if (status === 402) return "This chat has reached its usage limit for today. Please try again later.";
      if (status === 409) return "This assistant is still being set up. Please check back soon.";
      if (status === 429) return "Too many messages at once — please wait a moment.";
      return (detail && typeof detail === "string" ? detail : "Something went wrong.") + "";
    }

    function readDetail(body) {
      if (!body) return null;
      var d = body.detail;
      if (typeof d === "string") return d;
      if (d && typeof d.message === "string") return d.message;
      return null;
    }

    function activate() {
      if (cfg.endpoint) {
        // The customer's own server holds the key; there is nothing to check
        // from the browser.
        setStatus("ready", "Online");
        return;
      }
      if (!cfg.apiKey) {
        setStatus("error", "Not activated");
        add("note", "Add your API key to activate this widget: data-api-key=\"nxk_…\"");
        console.warn("[Nexora] No API key. Add data-api-key to the script tag, or data-endpoint for a server proxy.");
        return;
      }
      if (!cfg.apiBase) {
        setStatus("error", "Not configured");
        add("note", "Missing API base URL. Set data-api-base on the script tag.");
        return;
      }

      fetch(cfg.apiBase + "/v1/widget/activate", { headers: { "X-Api-Key": cfg.apiKey } })
        .then(function (res) {
          return res.json().catch(function () { return null; }).then(function (body) {
            return { status: res.status, ok: res.ok, body: body };
          });
        })
        .then(function (r) {
          if (!r.ok) {
            setStatus("error", "Not activated");
            add("note", explain(r.status, readDetail(r.body)));
            console.warn("[Nexora] Activation failed (" + r.status + ").", r.body);
            return;
          }
          if (!r.body.bot_ready) {
            setStatus("error", "Setting up");
            add("note", explain(409));
            return;
          }
          setStatus("ready", "Online · replies instantly");
        })
        .catch(function () {
          setStatus("error", "Offline");
          add("note", explain(0));
        });
    }

    function ask(text) {
      text = (text || "").trim();
      if (!text || state.busy || state.status !== "ready") return;

      chips.innerHTML = "";
      add("user", text);
      input.value = "";
      state.busy = true;
      send.disabled = true;

      var typing = el("div", "msg bot typing");
      typing.innerHTML = "<i></i><i></i><i></i>";
      log.appendChild(typing);
      scroll();

      var url = cfg.endpoint || cfg.apiBase + "/v1/ask";
      var headers = { "Content-Type": "application/json" };
      if (!cfg.endpoint) headers["X-Api-Key"] = cfg.apiKey;

      fetch(url, {
        method: "POST",
        headers: headers,
        body: JSON.stringify({ message: text, session_id: state.session })
      })
        .then(function (res) {
          return res.json().catch(function () { return null; }).then(function (body) {
            return { status: res.status, ok: res.ok, body: body };
          });
        })
        .then(function (r) {
          typing.remove();
          if (r.ok && r.body && typeof r.body.response === "string") {
            add("bot", r.body.response);
          } else {
            add("note", explain(r.status, readDetail(r.body)));
          }
        })
        .catch(function () {
          typing.remove();
          add("note", explain(0));
        })
        .then(function () {
          state.busy = false;
          send.disabled = state.status !== "ready";
          if (state.open) input.focus();
        });
    }

    function setOpen(open) {
      state.open = open;
      panel.setAttribute("data-open", String(open));
      launcher.setAttribute("aria-expanded", String(open));
      launcher.setAttribute("aria-label", open ? "Close chat" : "Open chat");
      if (open && !input.disabled) setTimeout(function () { input.focus(); }, 60);
    }

    launcher.addEventListener("click", function () { setOpen(!state.open); });
    closeBtn.addEventListener("click", function () { setOpen(false); });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      ask(input.value);
    });
    root.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && state.open) setOpen(false);
    });

    add("bot", t.greeting);
    renderChips(cfg.suggestions);
    setStatus("activating", "Connecting…");
    activate();
    if (cfg.openOnLoad) setOpen(true);

    return {
      open: function () { setOpen(true); },
      close: function () { setOpen(false); },
      toggle: function () { setOpen(!state.open); },
      ask: ask,
      destroy: function () { host.remove(); }
    };
  }

  var instance = null;

  var api = {
    __loaded: true,
    init: function (options) {
      if (instance) instance.destroy();
      var run = function () { instance = create(options || {}); };
      if (document.body) run();
      else document.addEventListener("DOMContentLoaded", run);
      return api;
    },
    open: function () { instance && instance.open(); },
    close: function () { instance && instance.close(); },
    toggle: function () { instance && instance.toggle(); },
    ask: function (q) { instance && instance.ask(q); },
    destroy: function () { instance && instance.destroy(); instance = null; }
  };
  window.NexoraWidget = api;

  // Script-tag install: activate straight away when a key or endpoint is given.
  // Without either, wait for NexoraWidget.init({...}) from the page's own code.
  if (data("api-key") || data("endpoint")) api.init({});
})();
