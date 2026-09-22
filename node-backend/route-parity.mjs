/**
 * Print every route the Express app actually serves, and compare it with the
 * FastAPI app's route table.
 *
 * A typecheck proves the code compiles; a smoke test proves the paths it walks
 * work. Neither proves that nothing was *dropped* — a route nobody thought to
 * test is exactly the one a port loses silently. This walks the router stack
 * and lists what is really mounted.
 *
 *     node route-parity.mjs
 */

process.env.NEXORA_START = "0";
process.env.LOG_LEVEL = "error";
// Never connected to — the app is imported for its route table, not run.
process.env.MONGO_URI = "mongodb://127.0.0.1:27017";

const { app } = await import("./dist/server.js");

// server.ts only mounts the static SPA fallback when a built bundle exists, so
// whether to expect that route is a question about the filesystem.
const { existsSync } = await import("node:fs");
const { resolve } = await import("node:path");
const hasStaticBundle = existsSync(resolve(process.cwd(), "..", "frontend", "dist"));

/** Turn Express's internal path regexp back into something readable. */
function pathOf(layer, prefix = "") {
  if (layer.route) return prefix + layer.route.path;
  if (!layer.regexp) return prefix;

  const source = layer.regexp.source;
  if (source === "^\\/?(?=\\/|$)") return prefix; // a mount at "/"

  const cleaned = source
    .replace("^\\/", "/")
    .replace("\\/?(?=\\/|$)", "")
    .replace(/\\\//g, "/")
    .replace(/\(\?:\(\[\^\\\/]\+\?\)\)/g, ":param")
    .replace(/\$$/, "");
  return prefix + cleaned;
}

const routes = [];

function walk(stack, prefix = "") {
  for (const layer of stack) {
    if (layer.route) {
      const methods = Object.keys(layer.route.methods)
        .filter((m) => m !== "_all")
        .map((m) => m.toUpperCase());
      for (const method of methods) {
        routes.push(`${method} ${prefix}${layer.route.path}`);
      }
    } else if (layer.name === "router" && layer.handle?.stack) {
      walk(layer.handle.stack, pathOf(layer, prefix));
    }
  }
}

walk(app._router.stack);

const mounted = [...new Set(routes)].sort();

// The FastAPI route table, transcribed from the Python source. Written out
// rather than derived so this check fails loudly if a path is renamed on one
// side and not the other.
const fastapi = [
  "GET /health",

  // auth
  "POST /api/auth/signup",
  "POST /api/auth/login",
  "POST /api/auth/logout",
  "POST /api/auth/firebase",
  "GET /api/auth/me",
  "GET /api/auth/providers",

  // billing
  "GET /api/billing/pricing",
  "GET /api/billing/subscription",
  "POST /api/billing/checkout",
  "POST /api/billing/confirm",
  "POST /api/billing/cancel",
  "GET /api/billing/invoices",
  "POST /api/billing/webhook",

  // dashboard
  "GET /api/dashboard",
  "GET /api/dashboard/keys",
  "POST /api/dashboard/keys",
  "DELETE /api/dashboard/keys/:param",

  // referrals
  "GET /api/referrals",
  "POST /api/referrals/invites",

  // templates and bot
  "GET /api/templates",
  "GET /api/templates/:param/starter-sheet",
  "GET /api/bot",
  "PUT /api/bot",
  "PUT /api/bot/answering",
  "POST /api/bot/sheet",
  "GET /api/bot/gaps",
  "POST /api/bot/preview",

  // widget
  "GET /api/widget/themes",
  "GET /api/widget/themes/:param/package",
  "GET /widget/v1/:param",
  "GET /v1/widget/activate",

  // public bot API
  "POST /v1/ask",

  // owner
  "POST /v1/owner/ask",
  "GET /v1/owner/snapshot",

  // api keys
  "POST /api/keys/generate",
  "GET /api/keys",
  "DELETE /api/keys/:param",
  "GET /api/keys/usage",
  "GET /api/keys/pricing",

  // assistant
  "GET /api/assistant/status",
  "GET /api/assistant/threads",
  "POST /api/assistant/threads",
  "GET /api/assistant/threads/:param",
  "PUT /api/assistant/threads/:param",
  "DELETE /api/assistant/threads/:param",
  "POST /api/assistant/threads/:param/messages",

  // support — customer
  "GET /api/support/messages",
  "POST /api/support/messages",
  "POST /api/support/read",

  // support — staff (these use multi-line decorators in the Python source,
  // which is why a naive grep of it misses them)
  "GET /api/support/admin/conversations",
  "GET /api/support/admin/conversations/:param",
  "POST /api/support/admin/conversations/:param/messages",
  "POST /api/support/admin/conversations/:param/read",

  // admin
  "POST /api/admin/login",
  "GET /api/admin/overview",
  "GET /api/admin/health",
  "GET /api/admin/users",
  "GET /api/admin/users/:param",
  "POST /api/admin/users/:param/credits",
  "POST /api/admin/users/:param/limit",
  "POST /api/admin/users/:param/active",
  "DELETE /api/admin/keys/:param",
  "GET /api/admin/subscriptions",
  "GET /api/admin/crashes",
  "GET /api/admin/crashes/recent",
  "GET /api/admin/usage",
  "GET /api/admin/audit",
  "GET /api/admin/ai-usage",
  "GET /api/admin/templates",
  "PATCH /api/admin/templates/:param",
  "POST /api/admin/templates/:param/reset",
  "GET /api/admin/referrals",

  // telemetry
  "POST /api/telemetry/error",

  // The SPA fallback, registered last so it can never shadow an API route.
  // Python had it as `@app.get("/{full_path:path}")`, and like the Python one
  // it only exists when a `frontend/dist` build is present on disk — so this
  // entry is expected conditionally.
  ...(hasStaticBundle ? ["GET *"] : []),
].sort();

/** Express normalises a trailing "/" mount; compare on a canonical form. */
const normalise = (route) => route.replace(/\/$/, "").replace(/\/:\w+/g, "/:param");

const mountedSet = new Set(mounted.map(normalise));
const expectedSet = new Set(fastapi.map(normalise));

const missing = [...expectedSet].filter((r) => !mountedSet.has(r)).sort();
const extra = [...mountedSet].filter((r) => !expectedSet.has(r)).sort();

console.log(`\nExpress routes mounted: ${mountedSet.size}`);
console.log(`FastAPI routes expected: ${expectedSet.size}\n`);

for (const route of mounted) console.log("  " + route);

if (missing.length > 0) {
  console.log("\nMISSING from the Node app:");
  for (const route of missing) console.log("  - " + route);
}
if (extra.length > 0) {
  console.log("\nEXTRA in the Node app (not in FastAPI):");
  for (const route of extra) console.log("  + " + route);
}

console.log("\n" + "=".repeat(60));
if (missing.length === 0 && extra.length === 0) {
  console.log("  Route parity: COMPLETE — every FastAPI route is mounted.");
} else {
  console.log(`  Route parity: ${missing.length} missing, ${extra.length} extra.`);
}
console.log("=".repeat(60) + "\n");

process.exit(missing.length > 0 ? 1 : 0);
