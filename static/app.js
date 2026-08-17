/**
 * Instant Sahay — Mobile Chat UI Logic
 *
 * Handles message sending, typing indicators, bot switching,
 * and suggestion chips. Calls /test-bot/ask (auth-free endpoint).
 */

(function () {
  "use strict";

  // ── DOM refs ──────────────────────────────────────────────
  const chatMessages  = document.getElementById("chatMessages");
  const chatInput     = document.getElementById("chatInput");
  const sendBtn       = document.getElementById("sendBtn");
  const btnCustomer   = document.getElementById("btnCustomer");
  const btnPartner    = document.getElementById("btnPartner");
  const headerStatus  = document.getElementById("headerStatus");
  const welcomeCard   = document.getElementById("welcomeCard");
  const suggestions   = document.getElementById("suggestions");

  // ── State ─────────────────────────────────────────────────
  let currentBot = "customer";
  let isLoading  = false;
  let sessionId  = "session-" + Date.now();

  // ── Suggestion chips per bot ──────────────────────────────
  const SUGGESTIONS = {
    customer: [
      "How do I cancel a booking?",
      "How do I get a refund?",
      "What payment methods?",
      "How to reschedule?",
      "Change phone number?",
    ],
    partner: [
      "KYC status?",
      "When do I get paid?",
      "How are jobs assigned?",
      "How is my rating calculated?",
      "Job cancellation penalty?",
    ],
  };

  // ── Init ──────────────────────────────────────────────────
  function init() {
    renderSuggestions();
    sendBtn.addEventListener("click", handleSend);
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });
    btnCustomer.addEventListener("click", () => switchBot("customer"));
    btnPartner.addEventListener("click",  () => switchBot("partner"));
  }

  // ── Bot Switching ─────────────────────────────────────────
  function switchBot(bot) {
    if (bot === currentBot) return;
    currentBot = bot;
    sessionId  = "session-" + Date.now();

    // Update button styles
    btnCustomer.classList.toggle("active", bot === "customer");
    btnPartner.classList.toggle("active",  bot === "partner");

    // Update header
    headerStatus.textContent = bot === "customer"
      ? "Customer Support Bot"
      : "Partner Support Bot";

    // Clear chat and show welcome
    clearChat();
    renderSuggestions();
  }

  function clearChat() {
    chatMessages.innerHTML = "";
    // Re-create welcome card
    const card = document.createElement("div");
    card.className = "welcome-card";
    card.id = "welcomeCard";

    const subtitle = currentBot === "customer"
      ? "I'm your Instant Sahay support assistant. Ask me anything about bookings, payments, or your account."
      : "I'm your Instant Sahay partner assistant. Ask me about KYC, payouts, jobs, and ratings.";

    card.innerHTML = `
      <img src="/static/logo.png" alt="Instant Sahay" class="welcome-logo" />
      <h1 class="welcome-title">Hi there! 👋</h1>
      <p class="welcome-text">${subtitle}</p>
      <div class="suggestions" id="suggestions"></div>
    `;
    chatMessages.appendChild(card);

    // Re-bind suggestions ref
    const newSuggestions = document.getElementById("suggestions");
    renderSuggestions(newSuggestions);
  }

  // ── Suggestions ───────────────────────────────────────────
  function renderSuggestions(container) {
    const el = container || suggestions;
    if (!el) return;
    el.innerHTML = "";
    SUGGESTIONS[currentBot].forEach((text) => {
      const chip = document.createElement("button");
      chip.className = "suggestion-chip";
      chip.textContent = text;
      chip.addEventListener("click", () => {
        chatInput.value = text;
        handleSend();
      });
      el.appendChild(chip);
    });
  }

  // ── Send Message ──────────────────────────────────────────
  async function handleSend() {
    const text = chatInput.value.trim();
    if (!text || isLoading) return;

    // Hide welcome card
    const wc = document.getElementById("welcomeCard");
    if (wc) wc.remove();

    // Append user bubble
    appendMessage("user", text);
    chatInput.value = "";
    chatInput.focus();

    // Show typing indicator
    isLoading = true;
    sendBtn.disabled = true;
    const typingEl = showTypingIndicator();

    try {
      const res = await fetch("/test-bot/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          bot_type: currentBot,
          session_id: sessionId,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${res.status}`);
      }

      const data = await res.json();
      typingEl.remove();
      appendBotMessage(data);
    } catch (err) {
      typingEl.remove();
      appendMessage("bot", "⚠️ " + (err.message || "Something went wrong. Please try again."));
    } finally {
      isLoading = false;
      sendBtn.disabled = false;
    }
  }

  // ── Render Messages ───────────────────────────────────────
  function appendMessage(role, text) {
    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    if (role === "bot") {
      row.innerHTML = `
        <img src="/static/logo.png" alt="Bot" class="msg-avatar bot-avatar" />
        <div class="message-bubble">${escapeHtml(text)}</div>
      `;
    } else {
      row.innerHTML = `
        <div class="message-bubble">${escapeHtml(text)}</div>
      `;
    }

    chatMessages.appendChild(row);
    scrollToBottom();
  }

  function appendBotMessage(data) {
    const row = document.createElement("div");
    row.className = "message-row bot";

    const modeLabels = {
      strong:  "✓ Strong Match",
      near:    "~ Near Match",
      decline: "✗ No Match",
    };
    const modeLabel = modeLabels[data.mode] || data.mode;
    const confidence = Math.round(data.confidence * 100);

    let metaHtml = "";
    if (data.mode) {
      metaHtml = `
        <div class="msg-meta">
          <span class="confidence-badge ${data.mode}">${modeLabel}</span>
          <span class="confidence-score">${confidence}% confidence</span>
        </div>
      `;
    }

    row.innerHTML = `
      <img src="/static/logo.png" alt="Bot" class="msg-avatar bot-avatar" />
      <div class="message-bubble">
        ${escapeHtml(data.response)}
        ${metaHtml}
      </div>
    `;

    chatMessages.appendChild(row);
    scrollToBottom();
  }

  // ── Typing Indicator ──────────────────────────────────────
  function showTypingIndicator() {
    const el = document.createElement("div");
    el.className = "typing-indicator";
    el.innerHTML = `
      <img src="/static/logo.png" alt="Bot" class="msg-avatar bot-avatar" />
      <div class="typing-dots">
        <span></span><span></span><span></span>
      </div>
    `;
    chatMessages.appendChild(el);
    scrollToBottom();
    return el;
  }

  // ── Helpers ───────────────────────────────────────────────
  function scrollToBottom() {
    requestAnimationFrame(() => {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    });
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Boot ──────────────────────────────────────────────────
  init();
})();
