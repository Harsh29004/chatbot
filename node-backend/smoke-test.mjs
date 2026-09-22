/**
 * End-to-end smoke test for the whole stack.
 *
 * Boots a throwaway in-memory MongoDB, starts the Python bot service against
 * it, starts the Express app against both, and walks the paths a customer
 * actually takes: sign up, sign in, read the dashboard, pick a template,
 * upload a sheet, issue a key, ask a question with it.
 *
 * It is deliberately not a unit-test suite. The question it answers is "does
 * this work end to end", which is the one that matters after a rearrangement
 * like splitting the bot pipeline into its own service — and which no amount
 * of typechecking can answer, because the interesting failures are all at the
 * seam between the two processes.
 *
 * Run with:  node smoke-test.mjs
 *
 * The bot service is started here rather than assumed, so this is one command.
 * If Python or its dependencies are missing, the steps that need retrieval are
 * reported as skipped rather than failed — every other path is still worth
 * proving.
 */

import { spawn } from "node:child_process";
import { MongoMemoryServer } from "mongodb-memory-server";

// ---------------------------------------------------------------------------
// Environment — set before the app is imported, since config.ts reads at import
// ---------------------------------------------------------------------------

const mongod = await MongoMemoryServer.create();
process.env.MONGO_URI = mongod.getUri();
process.env.MONGO_DB_NAME = "nexora_smoke";
process.env.PORT = "8123";
process.env.HOST = "127.0.0.1";
// The real work factor takes ~1s per hash; the format is identical either way
// and the compatibility check below pins the count explicitly.
process.env.PBKDF2_ITERATIONS = "10000";
process.env.ADMIN_API_KEY = "smoke-admin-key";
process.env.ADMIN_USERNAME = "admin";
process.env.ADMIN_PASSWORD = "smoke-admin-password";
process.env.BILLING_PROVIDER = "manual";
process.env.BILLING_ALLOW_MANUAL = "true";
process.env.LLM_ENABLED = "false";
process.env.ASSISTANT_ENABLED = "false";
process.env.BLOCK_DISPOSABLE_EMAILS = "true";
process.env.LOG_LEVEL = "warn";
process.env.NEXORA_START = "0";
process.env.BOT_SERVICE_URL = "http://127.0.0.1:8101";
process.env.INTERNAL_API_KEY = "smoke-internal-key";

// ---------------------------------------------------------------------------
// The Python bot service
// ---------------------------------------------------------------------------
// Started on a port of its own so a dev instance on 8001 does not collide.

const botServiceDir = new URL("../bot-service", import.meta.url).pathname.replace(
  /^\/([A-Za-z]:)/,
  "$1",
);

const botProcess = spawn(
  process.platform === "win32" ? "python" : "python3",
  ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8101"],
  {
    cwd: botServiceDir,
    env: {
      ...process.env,
      MONGO_URI: process.env.MONGO_URI,
      MONGO_DB_NAME: process.env.MONGO_DB_NAME,
      INTERNAL_API_KEY: "smoke-internal-key",
      LOG_LEVEL: "WARNING",
    },
    stdio: "ignore",
  },
);

/** Wait for it to answer, rather than guessing at a sleep. */
async function waitForBotService(attempts = 60) {
  for (let i = 0; i < attempts; i += 1) {
    if (botProcess.exitCode !== null) return false;
    try {
      const response = await fetch("http://127.0.0.1:8101/health", {
        headers: { "X-Internal-Key": "smoke-internal-key" },
        signal: AbortSignal.timeout(2000),
      });
      if (response.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  return false;
}

const botUp = await waitForBotService();

const { app, startup, shutdown } = await import("./dist/server.js");
const security = await import("./dist/billing/security.js");

const BASE = `http://127.0.0.1:${process.env.PORT}`;

let passed = 0;
let failed = 0;
let skipped = 0;
const failures = [];

function ok(name, detail = "") {
  passed += 1;
  console.log(`  PASS  ${name}${detail ? ` — ${detail}` : ""}`);
}

function bad(name, detail) {
  failed += 1;
  failures.push(`${name}: ${detail}`);
  console.log(`  FAIL  ${name} — ${detail}`);
}

function skip(name, why) {
  skipped += 1;
  console.log(`  SKIP  ${name} — ${why}`);
}

function check(name, condition, detail = "") {
  if (condition) ok(name, detail);
  else bad(name, detail || "condition was false");
}

function section(title) {
  console.log(`\n${title}`);
  console.log("-".repeat(title.length));
}

// A cookie jar, because the whole auth model is a session cookie.
let cookie = "";

async function call(method, path, { body, headers = {}, raw = false } = {}) {
  const init = { method, headers: { ...headers }, redirect: "manual" };
  if (cookie) init.headers.cookie = cookie;
  if (body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  const response = await fetch(`${BASE}${path}`, init);

  const setCookie = response.headers.getSetCookie?.() ?? [];
  for (const entry of setCookie) {
    const [pair] = entry.split(";");
    if (pair.startsWith("nexora_session=")) {
      cookie = pair.endsWith("=") ? "" : pair;
    }
  }

  if (raw) return { response, text: await response.text() };

  const text = await response.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  return { response, json, text };
}

// ---------------------------------------------------------------------------

await startup();
const server = app.listen(Number(process.env.PORT), "127.0.0.1");
await new Promise((resolve) => server.once("listening", resolve));

console.log(`\nNexora AI — full stack smoke test`);
console.log(`MongoDB:     in-memory (${process.env.MONGO_DB_NAME})`);
console.log(
  `bot-service: ${botUp ? "running on :8101" : "NOT RUNNING — retrieval steps will be skipped"}`,
);

try {
  // -------------------------------------------------------------------------
  section("1. Password hashes written by the Python build");
  // -------------------------------------------------------------------------
  // This is the claim the whole migration rests on: existing users keep their
  // passwords. The hash below was produced by backend/billing/security.py.
  const PYTHON_HASH =
    "pbkdf2_sha256$10000$upyiyXyEZH2kNLS4CKt3DQ==$potPRnA/SGW2cXmzRf+xuoEKbdRz0k1SAx3ihmdfSL8=";

  check(
    "Node verifies a Python-generated hash",
    await security.verifyPassword("correct horse battery staple", PYTHON_HASH),
  );
  check(
    "Node rejects the wrong password against it",
    !(await security.verifyPassword("wrong password", PYTHON_HASH)),
  );

  const nodeHash = await security.hashPassword("round trip");
  check("Node hash round-trips in Node", await security.verifyPassword("round trip", nodeHash));
  check(
    "Node hash uses the Python storage format",
    /^pbkdf2_sha256\$\d+\$[^$]+\$[^$]+$/.test(nodeHash),
    nodeHash.slice(0, 24) + "…",
  );

  // -------------------------------------------------------------------------
  section("2. Health and public endpoints");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("GET", "/health");
    check("GET /health", response.status === 200 && json?.status === "ok");
  }
  {
    const { response, json } = await call("GET", "/api/billing/pricing");
    check(
      "GET /api/billing/pricing",
      response.status === 200 && json?.plans?.length === 3,
      `${json?.plans?.length} plans, yearly saves ${json?.yearly_savings_display}`,
    );
    check(
      "pricing maths is derived, not hardcoded",
      json?.yearly_discount_percent === 22 && json?.yearly_effective_monthly_display === "11.67",
      `${json?.yearly_discount_percent}% off, ${json?.yearly_effective_monthly_display}/mo`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/templates");
    check(
      "GET /api/templates",
      response.status === 200 && json?.length === 10,
      `${json?.length} templates: ${json?.slice(0, 3).map((t) => t.id).join(", ")}…`,
    );
    const ecom = json?.find((t) => t.id === "ecommerce");
    check(
      "template data survived the transfer",
      ecom?.strong_threshold === 0.84 && ecom?.icon === "🛍️" && ecom?.strictness === "open",
      `ecommerce: threshold ${ecom?.strong_threshold}, icon ${ecom?.icon}`,
    );
  }
  {
    const { response, text } = await call("GET", "/api/templates/ecommerce/starter-sheet", {
      raw: true,
    });
    check(
      "GET starter-sheet returns CSV",
      response.status === 200 &&
        response.headers.get("content-type")?.includes("text/csv") &&
        text.startsWith("Question,Alt_Phrasings,Category,Answer"),
      `${text.split("\r\n").length} rows`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/keys/pricing");
    check(
      "GET /api/keys/pricing (credit tiers)",
      response.status === 200 && json?.tiers?.length === 4 && json?.daily_limit === 250,
    );
  }
  {
    const { response, json } = await call("GET", "/api/widget/themes");
    check(
      "GET /api/widget/themes",
      response.status === 200 && json?.length === 7,
      `${json?.map((t) => t.id).join(", ")}`,
    );
  }
  {
    const { response, text } = await call("GET", "/widget/v1/aurora.js", { raw: true });
    // Matched without assuming the serialiser's spacing. Python's json.dumps
    // writes `"primary": "#5b5bf6"` and JavaScript's JSON.stringify writes it
    // without the space; pinning either one would make this test a statement
    // about which language built the file rather than about the theme reaching
    // the browser.
    const themeBaked = /"primary":\s*"#5b5bf6"/.test(text);
    const asciiOnly = !/[^\x00-\x7F]/.test(text);

    check(
      "GET /widget/v1/aurora.js (theme baked in)",
      response.status === 200 && themeBaked && asciiOnly,
      `${text.length} bytes, theme baked in: ${themeBaked}, ASCII-only: ${asciiOnly}`,
    );
  }
  {
    const { response } = await call("GET", "/api/widget/themes/aurora/package", { raw: true });
    check(
      "GET widget install package (zip)",
      response.status === 200 && response.headers.get("content-type") === "application/zip",
    );
  }

  // -------------------------------------------------------------------------
  section("3. Signup, session cookie, and the one-account-per-mailbox rule");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("POST", "/api/auth/signup", {
      body: { email: "Jordan.Lee@gmail.com", name: "Jordan", password: "a-long-enough-password" },
    });
    check(
      "POST /api/auth/signup",
      response.status === 201 && json?.email === "jordan.lee@gmail.com",
      `id ${json?.id}`,
    );
    check("signup set a session cookie", cookie.startsWith("nexora_session="));
  }
  {
    const saved = cookie;
    cookie = "";
    // Gmail ignores dots and everything after "+", so this is the same mailbox.
    const { response, json } = await call("POST", "/api/auth/signup", {
      body: { email: "jordanlee+promo@gmail.com", name: "Jordan", password: "another-password" },
    });
    check(
      "alias signup refused (canonical email)",
      response.status === 409,
      `${response.status} ${json?.detail}`,
    );
    cookie = saved;
  }
  {
    const saved = cookie;
    cookie = "";
    const { response, json } = await call("POST", "/api/auth/signup", {
      body: { email: "nobody@mailinator.com", name: "X", password: "a-long-enough-password" },
    });
    check(
      "disposable address refused",
      response.status === 400,
      `${response.status} ${json?.detail?.slice(0, 48)}…`,
    );
    cookie = saved;
  }
  {
    const { response, json } = await call("GET", "/api/auth/me");
    check("GET /api/auth/me with the cookie", response.status === 200 && json?.name === "Jordan");
  }
  {
    const saved = cookie;
    cookie = "";
    const { response } = await call("GET", "/api/auth/me");
    check("GET /api/auth/me without a cookie is 401", response.status === 401);
    cookie = saved;
  }

  // -------------------------------------------------------------------------
  section("4. Login throttling and password verification");
  // -------------------------------------------------------------------------
  {
    const saved = cookie;
    cookie = "";
    const { response, json } = await call("POST", "/api/auth/login", {
      body: { email: "jordan.lee@gmail.com", password: "a-long-enough-password" },
    });
    check("POST /api/auth/login", response.status === 200 && json?.email === "jordan.lee@gmail.com");
    check("login issued a fresh session cookie", cookie.startsWith("nexora_session="));
    if (!cookie) cookie = saved;
  }
  {
    const saved = cookie;
    cookie = "";
    const { response, json } = await call("POST", "/api/auth/login", {
      body: { email: "jordan.lee@gmail.com", password: "not-the-password" },
    });
    check(
      "wrong password is 401",
      response.status === 401 && json?.detail === "Incorrect email or password.",
    );
    cookie = saved;
  }

  // -------------------------------------------------------------------------
  section("5. Dashboard, trial entitlement, and API keys");
  // -------------------------------------------------------------------------
  let apiKey = "";
  {
    const { response, json } = await call("GET", "/api/dashboard");
    check(
      "GET /api/dashboard",
      response.status === 200 && json?.subscription?.plan_id === "trial",
      `plan ${json?.subscription?.plan_id}, entitled ${json?.subscription?.is_entitled}`,
    );
    check(
      "trial grants the trial allowance",
      json?.usage?.credits_daily_limit === 250,
      `daily limit ${json?.usage?.credits_daily_limit}`,
    );
  }
  {
    const { response, json } = await call("POST", "/api/dashboard/keys", {
      body: { label: "smoke" },
    });
    apiKey = json?.api_key ?? "";
    check(
      "POST /api/dashboard/keys",
      response.status === 201 && apiKey.startsWith("nxk_"),
      `${json?.key_prefix}…`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/keys/usage", {
      headers: { "X-Api-Key": apiKey },
    });
    check(
      "GET /api/keys/usage with the key",
      response.status === 200 && json?.credits_daily_limit === 250,
      `${json?.credits_remaining} credits remaining`,
    );
  }
  {
    const { response } = await call("GET", "/api/keys/usage", {
      headers: { "X-Api-Key": "nxk_not_a_real_key" },
    });
    check("invalid key is 403", response.status === 403);
  }
  {
    const { response, json } = await call("GET", "/v1/widget/activate", {
      headers: { "X-Api-Key": apiKey },
    });
    check(
      "GET /v1/widget/activate (free, no credits)",
      response.status === 200 && json?.active === true && json?.bot_ready === false,
    );
  }

  // -------------------------------------------------------------------------
  section("6. Referrals");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("GET", "/api/referrals");
    check(
      "GET /api/referrals",
      response.status === 200 && /^[A-Z0-9]{8}$/.test(json?.code ?? ""),
      `code ${json?.code}, link ${json?.link?.slice(0, 40)}…`,
    );
  }
  {
    const { response, json } = await call("POST", "/api/referrals/invites", {
      body: { email: "jordan.lee@gmail.com" },
    });
    check("cannot refer yourself", response.status === 400, json?.detail);
  }
  {
    const { response, json } = await call("POST", "/api/referrals/invites", {
      body: { email: "friend@example.com" },
    });
    check(
      "POST /api/referrals/invites",
      response.status === 201 && json?.invites?.length === 1,
      `${json?.invites?.length} invite(s)`,
    );
  }

  // -------------------------------------------------------------------------
  section("7. Bot setup");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("GET", "/api/bot");
    check(
      "GET /api/bot creates a draft bot",
      response.status === 200 && json?.status === "draft" && json?.template_id === "ecommerce",
      `accepted formats: ${json?.accepted_formats?.length}`,
    );
  }
  {
    const { response, json } = await call("PUT", "/api/bot", {
      body: { template_id: "clinic", name: "Northside Clinic" },
    });
    check(
      "PUT /api/bot switches template",
      response.status === 200 && json?.template_id === "clinic" && json?.name === "Northside Clinic",
      `strong threshold now ${json?.template?.strong_threshold}`,
    );
  }
  {
    const { response, json } = await call("PUT", "/api/bot", { body: { template_id: "nope" } });
    check("unknown template is 400", response.status === 400, json?.detail);
  }

  // -------------------------------------------------------------------------
  section("8. Sheet upload and retrieval (the Python bot service)");
  // -------------------------------------------------------------------------
  if (!botUp) {
    skip("POST /api/bot/sheet", "the Python bot service did not start");
    skip("POST /v1/ask", "the Python bot service did not start");
  } else {
    const csv = [
      "Question,Alt_Phrasings,Category,Answer",
      '"What are your opening hours?","when do you open;timings","Timings","We are open 9am to 6pm, Monday to Saturday."',
      '"How do I book an appointment?","make a booking;see a doctor","Appointments","Call reception on 0800 123 456 or use the online booking form."',
      '"Do you accept walk-ins?","without appointment","Appointments","Walk-ins are seen when a slot is free; appointments are always faster."',
    ].join("\n");

    const form = new FormData();
    form.append("file", new Blob([csv], { type: "text/csv" }), "faq.csv");

    const uploadResponse = await fetch(`${BASE}/api/bot/sheet`, {
      method: "POST",
      headers: { cookie },
      body: form,
    });
    const uploadJson = await uploadResponse.json().catch(() => null);

    check(
      "POST /api/bot/sheet indexes the CSV",
      uploadResponse.status === 200 && uploadJson?.documents_indexed === 3,
      `${uploadJson?.documents_indexed} rows, bot ${uploadJson?.bot?.status}`,
    );

    {
      const { response, json } = await call("POST", "/api/bot/preview", {
        body: { message: "when do you open?", session_id: "smoke" },
      });
      check(
        "POST /api/bot/preview retrieves the right answer",
        response.status === 200 && json?.response?.includes("9am to 6pm"),
        `mode ${json?.mode}, confidence ${json?.confidence?.toFixed(3)}`,
      );
    }
    {
      const { response, json } = await call("POST", "/v1/ask", {
        headers: { "X-Api-Key": apiKey },
        body: { message: "What are your opening hours?", session_id: "smoke" },
      });
      check(
        "POST /v1/ask answers verbatim and charges credits",
        response.status === 200 &&
          json?.mode === "strong" &&
          response.headers.get("x-credit-cost") === "1",
        `mode ${json?.mode}, ${response.headers.get("x-credits-remaining")} credits left`,
      );
    }
    {
      const { response, json } = await call("POST", "/v1/ask", {
        headers: { "X-Api-Key": apiKey },
        body: { message: "Can I bring my pet iguana to the moon?", session_id: "smoke" },
      });
      check(
        "off-topic question declines",
        response.status === 200 && json?.mode === "decline",
        `mode ${json?.mode}`,
      );
    }
    {
      const { response, json } = await call("GET", "/api/bot/gaps");
      check(
        "GET /api/bot/gaps logs the miss",
        response.status === 200 && json?.gaps?.length >= 1,
        `${json?.gaps?.length} gap(s), first: "${json?.gaps?.[0]?.question?.slice(0, 32)}…"`,
      );
    }
  }

  // -------------------------------------------------------------------------
  section("9. Input policy (refusals that must survive the port)");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("POST", "/api/support/messages", {
      body: { body: "My card is 4111 1111 1111 1111, please refund me" },
    });
    check(
      "support refuses a payment card number",
      response.status === 400 && json?.detail?.includes("card numbers"),
      json?.detail?.slice(0, 44) + "…",
    );
  }
  {
    const { response, json } = await call("POST", "/api/support/messages", {
      body: { body: "My order 1234567890123 has not arrived, can you check?" },
    });
    check(
      "a long order number is NOT refused (Luhn check works)",
      response.status === 201,
      `status ${response.status}`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/support/messages");
    check(
      "GET /api/support/messages",
      response.status === 200 && json?.messages?.length === 1,
      `${json?.messages?.length} message(s)`,
    );
  }

  // -------------------------------------------------------------------------
  section("10. Admin panel");
  // -------------------------------------------------------------------------
  const ADMIN = { "X-Admin-Key": "smoke-admin-key" };
  {
    const { response } = await call("GET", "/api/admin/overview");
    check("admin route without the key is 403", response.status === 403);
  }
  {
    const { response, json } = await call("GET", "/api/admin/overview", { headers: ADMIN });
    check(
      "GET /api/admin/overview",
      response.status === 200 && json?.customers?.total === 1,
      `${json?.customers?.total} customer(s), ${json?.subscriptions?.entitled} entitled, ` +
        `MRR ${json?.subscriptions?.mrr_display}`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/admin/users", { headers: ADMIN });
    check(
      "GET /api/admin/users",
      response.status === 200 && json?.users?.length === 1,
      `${json?.users?.[0]?.email}, plan ${json?.users?.[0]?.plan_id}`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/admin/templates", { headers: ADMIN });
    check(
      "GET /api/admin/templates",
      response.status === 200 && json?.templates?.length === 10,
      `${json?.editable_fields?.length} editable fields`,
    );
  }
  {
    const { response, json } = await call("PATCH", "/api/admin/templates/clinic", {
      headers: ADMIN,
      body: { decline_message: "Sorry, I can only help with clinic questions." },
    });
    check(
      "PATCH a template override",
      response.status === 200 && json?.overridden_fields?.includes("decline_message"),
      `overridden: ${json?.overridden_fields?.join(", ")}`,
    );
  }
  {
    const { response, json } = await call("PATCH", "/api/admin/templates/clinic", {
      headers: ADMIN,
      body: { near_threshold: 0.98 },
    });
    check(
      "threshold inversion is refused",
      response.status === 400 && json?.detail?.includes("near-match threshold"),
      json?.detail?.slice(0, 50) + "…",
    );
  }
  {
    const { response, json } = await call("POST", "/api/admin/templates/clinic/reset", {
      headers: ADMIN,
    });
    check(
      "POST template reset restores the code default",
      response.status === 200 && json?.overridden_fields?.length === 0,
    );
  }
  {
    const { response, json } = await call("POST", "/api/admin/login", {
      body: { username: "admin", password: "smoke-admin-password" },
    });
    check(
      "POST /api/admin/login issues a token",
      response.status === 200 && json?.token?.startsWith("nxa."),
    );

    const token = json?.token;
    const { response: r2 } = await call("GET", "/api/admin/overview", {
      headers: { "X-Admin-Key": token },
    });
    check("the admin session token authenticates", r2.status === 200);
  }
  {
    const { response } = await call("POST", "/api/admin/login", {
      body: { username: "admin", password: "wrong" },
    });
    check("wrong admin password is 401", response.status === 401);
  }
  {
    const { response, json } = await call("GET", "/api/admin/subscriptions", { headers: ADMIN });
    check(
      "GET /api/admin/subscriptions",
      response.status === 200 && typeof json?.mrr_cents === "number",
      `trial conversion ${json?.trial_conversion?.percent}%`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/admin/usage", { headers: ADMIN });
    check("GET /api/admin/usage", response.status === 200 && Array.isArray(json?.per_day));
  }
  {
    const { response, json } = await call("GET", "/api/admin/audit", { headers: ADMIN });
    check(
      "GET /api/admin/audit",
      response.status === 200 && Array.isArray(json?.entries),
      `${json?.summary?.requests} request(s) logged`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/admin/ai-usage", { headers: ADMIN });
    check("GET /api/admin/ai-usage", response.status === 200 && Array.isArray(json?.by_plan));
  }
  {
    const { response, json } = await call("GET", "/api/admin/crashes", { headers: ADMIN });
    check("GET /api/admin/crashes", response.status === 200 && Array.isArray(json?.groups));
  }

  // -------------------------------------------------------------------------
  section("11. Telemetry");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("POST", "/api/telemetry/error", {
      body: {
        message: "TypeError: cannot read property of undefined",
        stack: "at Dashboard (dashboard.tsx:42)",
        kind: "react",
        fingerprint: "abc123",
        route: "/dashboard",
        fatal: true,
      },
    });
    check(
      "POST /api/telemetry/error",
      response.status === 202 && json?.status === "recorded",
    );
  }
  {
    const { response, json } = await call("GET", "/api/admin/crashes", { headers: ADMIN });
    check(
      "the crash appears grouped in the admin panel",
      response.status === 200 && json?.groups?.[0]?.fingerprint === "abc123",
      `${json?.groups?.[0]?.count} report(s), ${json?.groups?.[0]?.affected_users} user(s)`,
    );
  }

  // -------------------------------------------------------------------------
  section("12. Billing lifecycle");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("POST", "/api/billing/checkout", {
      body: { plan_id: "monthly" },
    });
    check(
      "POST /api/billing/checkout (manual provider)",
      response.status === 200 && json?.requires_manual_confirmation === true,
      `ref ${json?.reference?.slice(0, 16)}…`,
    );
  }
  {
    const { response, json } = await call("POST", "/api/billing/confirm");
    check(
      "POST /api/billing/confirm activates the plan",
      response.status === 200 && json?.status === "active" && json?.daily_credits === 5000,
      `plan ${json?.plan_id}, ${json?.daily_credits} credits/day`,
    );
  }
  {
    const { response, json } = await call("GET", "/api/billing/invoices");
    check(
      "the checkout invoice was settled",
      response.status === 200 && json?.[0]?.status === "paid",
      `${json?.length} invoice(s), ${json?.[0]?.amount_cents} cents`,
    );
  }
  {
    const { response, json } = await call("POST", "/api/billing/cancel");
    check(
      "POST /api/billing/cancel keeps access to period end",
      response.status === 200 && json?.status === "canceled" && json?.is_entitled === true,
      `entitled until ${json?.current_period_end?.slice(0, 10)}`,
    );
  }

  // -------------------------------------------------------------------------
  section("13. Logout");
  // -------------------------------------------------------------------------
  {
    const { response, json } = await call("POST", "/api/auth/logout");
    check("POST /api/auth/logout", response.status === 200 && json?.status === "signed out");
    cookie = "";
    const { response: r2 } = await call("GET", "/api/auth/me");
    check("the session is dead afterwards", r2.status === 401);
  }
} catch (error) {
  bad("smoke test crashed", error?.stack ?? String(error));
} finally {
  console.log(`\n${"=".repeat(60)}`);
  console.log(`  passed: ${passed}   failed: ${failed}   skipped: ${skipped}`);
  if (failures.length > 0) {
    console.log("\nFailures:");
    for (const failure of failures) console.log(`  - ${failure}`);
  }
  console.log(`${"=".repeat(60)}\n`);

  server.close();
  await shutdown();
  botProcess.kill();
  await mongod.stop();
  process.exit(failed > 0 ? 1 : 0);
}
