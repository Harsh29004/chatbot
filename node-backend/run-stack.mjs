/**
 * Boot the whole stack against a throwaway in-memory MongoDB.
 *
 * Three processes now, because the bot pipeline is Python again:
 *
 *   mongod (in-memory)  ←  bot-service (:8001, Python)
 *          ↑                      ↑
 *          └──────  node-backend (:8000, this process)
 *
 * Both services get the same MONGO_URI and write disjoint halves of it. This
 * script owns the database and the Python child, so one Ctrl-C cleans up
 * everything.
 *
 *     node run-stack.mjs
 *
 * It seeds one account so the dashboard has something to show. Development
 * only — it prints a password to the console.
 */

import { spawn } from "node:child_process";
import { MongoMemoryServer } from "mongodb-memory-server";

const mongod = await MongoMemoryServer.create();
const MONGO_URI = mongod.getUri();

// Shared between the two services. Not a user credential — it only answers
// "did this come from our own backend".
const INTERNAL_API_KEY = "dev-internal-key";

const shared = {
  MONGO_URI,
  MONGO_DB_NAME: "nexora_stack",
  INTERNAL_API_KEY,
  LLM_ENABLED: "false",
  ASSISTANT_ENABLED: "false",
  EMBEDDING_MODEL: "all-MiniLM-L6-v2",
};

// ---------------------------------------------------------------------------
// The Python bot service
// ---------------------------------------------------------------------------

console.log("starting bot-service (Python) on :8001 …");

const botService = spawn(
  process.platform === "win32" ? "python" : "python3",
  ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001"],
  {
    cwd: new URL("../bot-service", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
    env: { ...process.env, ...shared, LOG_LEVEL: "WARNING" },
    stdio: ["ignore", "pipe", "pipe"],
  },
);

botService.stdout.on("data", (chunk) => process.stdout.write(`[bot] ${chunk}`));
botService.stderr.on("data", (chunk) => process.stdout.write(`[bot] ${chunk}`));

/** Wait for the service to answer, rather than guessing at a sleep. */
async function waitForBotService(attempts = 90) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      const response = await fetch("http://127.0.0.1:8001/health", {
        headers: { "X-Internal-Key": INTERNAL_API_KEY },
        signal: AbortSignal.timeout(2000),
      });
      if (response.ok) return await response.json();
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error("bot-service did not become healthy");
}

const health = await waitForBotService();
console.log(`bot-service ready — model ${health.model}`);

// ---------------------------------------------------------------------------
// The Node backend, in this process
// ---------------------------------------------------------------------------

Object.assign(process.env, shared, {
  PORT: "8000",
  HOST: "127.0.0.1",
  BOT_SERVICE_URL: "http://127.0.0.1:8001",
  PBKDF2_ITERATIONS: "10000",
  ADMIN_API_KEY: "e2e-admin-key",
  ADMIN_USERNAME: "admin",
  ADMIN_PASSWORD: "e2e-admin-password",
  BILLING_PROVIDER: "manual",
  BILLING_ALLOW_MANUAL: "true",
  WEB_ORIGINS: "http://localhost:3000,http://127.0.0.1:3000",
  PUBLIC_BASE_URL: "http://localhost:3000",
  LOG_LEVEL: "warn",
  NEXORA_START: "0",
});

const { app, startup } = await import("./dist/server.js");
await startup();

const server = app.listen(8000, "127.0.0.1", async () => {
  const response = await fetch("http://127.0.0.1:8000/api/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: "demo@example.com",
      name: "Demo",
      password: "demo-password-123",
    }),
  });

  const cookie = (response.headers.getSetCookie?.() ?? [])
    .map((c) => c.split(";")[0])
    .find((c) => c.startsWith("nexora_session="));

  console.log("\nSTACK_READY");
  console.log("  api          http://127.0.0.1:8000");
  console.log("  bot-service  http://127.0.0.1:8001");
  console.log("  account      demo@example.com / demo-password-123");
  console.log("  cookie       " + (cookie ?? "none"));
});

// ---------------------------------------------------------------------------

async function shutdown() {
  console.log("\nshutting down …");
  server.close();
  botService.kill();
  await mongod.stop();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
