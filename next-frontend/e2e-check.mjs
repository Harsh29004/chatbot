/**
 * End-to-end check for the Next.js frontend against a live backend.
 *
 * Asserts the thing that is actually new here: that the *server-rendered HTML*
 * — not the page after JavaScript boots — already contains the data and the
 * access decisions. A client-rendered app would pass none of these.
 *
 * Start the API first (node-backend/run-e2e-backend.mjs), then this app
 * (npm start), then:
 *
 *     node e2e-check.mjs
 */

const WEB = process.env.WEB_ORIGIN ?? "http://127.0.0.1:3000";
const API = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

let passed = 0;
let failed = 0;
const failures = [];

function check(name, condition, detail = "") {
  if (condition) {
    passed += 1;
    console.log(`  PASS  ${name}${detail ? ` — ${detail}` : ""}`);
  } else {
    failed += 1;
    failures.push(`${name}: ${detail}`);
    console.log(`  FAIL  ${name} — ${detail}`);
  }
}

function section(title) {
  console.log(`\n${title}\n${"-".repeat(title.length)}`);
}

async function get(path, cookie = "") {
  const response = await fetch(`${WEB}${path}`, {
    headers: cookie ? { cookie } : {},
    redirect: "manual",
  });
  const body = response.status === 200 ? await response.text() : "";
  return { status: response.status, location: response.headers.get("location"), body };
}

// ---------------------------------------------------------------------------
// Sign in against the API to get a real session cookie.
// ---------------------------------------------------------------------------

const login = await fetch(`${API}/api/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "demo@example.com", password: "demo-password-123" }),
});

const cookie = (login.headers.getSetCookie?.() ?? [])
  .map((c) => c.split(";")[0])
  .find((c) => c.startsWith("nexora_session=")) ?? "";

console.log("\nNexora AI — Next.js frontend end-to-end check");
console.log(`web: ${WEB}   api: ${API}`);
console.log(`session: ${cookie ? "acquired" : "NONE — is the backend seeded?"}`);

// ---------------------------------------------------------------------------
section("1. The landing page is rendered on the server");
// ---------------------------------------------------------------------------
{
  const { status, body } = await get("/");
  check("GET / is 200", status === 200);

  // These come from the API. If they are in the HTML, the server fetched them.
  check(
    "live prices are in the HTML",
    body.includes("Cancel anytime") && body.includes("11.67"),
    "monthly/yearly copy and the derived per-month figure",
  );
  check(
    "the template catalogue is in the HTML",
    body.includes("E-commerce Support") && body.includes("Logistics"),
  );
  check(
    "no empty-state placeholder shipped instead",
    !body.includes("Loading prices") && !body.includes("Loading templates"),
  );
  check("the page has a real <title>", /<title>[^<]*Nexora AI[^<]*<\/title>/.test(body));
  check(
    "fonts are self-hosted, not fetched from Google at runtime",
    !body.includes("fonts.googleapis.com"),
  );
}

// ---------------------------------------------------------------------------
section("2. Protected routes are decided before the response is sent");
// ---------------------------------------------------------------------------
for (const route of ["/dashboard", "/assistant", "/support", "/checkout/return"]) {
  const { status, location, body } = await get(route);
  check(
    `${route} redirects a signed-out visitor`,
    status === 307 && (location ?? "").includes("/signin?from="),
    `${status} -> ${location}`,
  );
  check(`${route} leaks no markup while doing it`, body === "");
}

{
  const { status, location } = await get("/signin", cookie);
  check(
    "/signin sends a signed-in visitor to the dashboard",
    status === 307 && (location ?? "").endsWith("/dashboard"),
    `${status} -> ${location}`,
  );
}

{
  const { status } = await get("/nope-not-a-route");
  check("an unknown path is a real 404, not the homepage", status === 404, String(status));
}

// ---------------------------------------------------------------------------
section("3. Signed-in pages arrive populated");
// ---------------------------------------------------------------------------
{
  const { status, body } = await get("/dashboard", cookie);
  check("GET /dashboard is 200 with a session", status === 200);
  check("the account is in the server-rendered HTML", body.includes("demo@example.com"));
  check(
    "the plan and allowance are in the HTML",
    body.includes("Trial") && body.includes("250"),
    "trial plan, 250 daily credits",
  );
  check(
    "the template picker is server-rendered too",
    body.includes("E-commerce Support"),
  );
  check("no spinner shipped as the initial state", !body.includes('class="spinner"'));
}

{
  const { status, body } = await get("/support", cookie);
  check("GET /support is 200 with a session", status === 200);
  check("the support screen rendered", body.includes("Support") || body.length > 5000);
}

{
  const { status } = await get("/assistant", cookie);
  check("GET /assistant is 200 with a session", status === 200);
}

// ---------------------------------------------------------------------------
section("4. The admin panel stays client-only");
// ---------------------------------------------------------------------------
{
  const { status, body } = await get("/admin");
  check("GET /admin is 200 without a session", status === 200, "it has its own gate");
  check(
    "no customer data is server-rendered into it",
    !body.includes("demo@example.com"),
    "the admin key lives in sessionStorage and never reaches the server",
  );
}

// ---------------------------------------------------------------------------
section("5. The API is reachable through the same origin");
// ---------------------------------------------------------------------------
{
  const health = await fetch(`${WEB}/health`);
  check("GET /health proxies to the API", health.ok, String(health.status));

  const pricing = await fetch(`${WEB}/api/billing/pricing`);
  const payload = await pricing.json();
  check(
    "GET /api/... proxies to the API",
    pricing.ok && payload.plans?.length === 3,
    `${payload.plans?.length} plans`,
  );

  const widget = await fetch(`${WEB}/widget/v1/aurora.js`);
  check("GET /widget/... proxies to the API", widget.ok, String(widget.status));
}

// ---------------------------------------------------------------------------
console.log(`\n${"=".repeat(60)}`);
console.log(`  passed: ${passed}   failed: ${failed}`);
if (failures.length > 0) {
  console.log("\nFailures:");
  for (const f of failures) console.log(`  - ${f}`);
}
console.log(`${"=".repeat(60)}\n`);

process.exit(failed > 0 ? 1 : 0);
