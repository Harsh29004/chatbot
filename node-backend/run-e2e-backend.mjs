/**
 * Boot the backend against a throwaway in-memory MongoDB and hold it open.
 *
 * This is the API half of an end-to-end run: start it, then start the Next.js
 * app against it. The in-memory server means no Atlas credentials, no leftover
 * state between runs, and nothing to clean up afterwards.
 *
 *     node run-e2e-backend.mjs
 *
 * It seeds one signed-in-able account so the protected pages have something to
 * render. Development only — it prints a password to the console.
 */

import { MongoMemoryServer } from "mongodb-memory-server";

const mongod = await MongoMemoryServer.create();

process.env.MONGO_URI = mongod.getUri();
process.env.MONGO_DB_NAME = "nexora_e2e";
process.env.PORT = "8000";
process.env.HOST = "127.0.0.1";
process.env.PBKDF2_ITERATIONS = "10000";
process.env.ADMIN_API_KEY = "e2e-admin-key";
process.env.ADMIN_USERNAME = "admin";
process.env.ADMIN_PASSWORD = "e2e-admin-password";
process.env.BILLING_PROVIDER = "manual";
process.env.BILLING_ALLOW_MANUAL = "true";
process.env.LLM_ENABLED = "false";
process.env.ASSISTANT_ENABLED = "false";
process.env.WEB_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000";
process.env.PUBLIC_BASE_URL = "http://localhost:3000";
process.env.LOG_LEVEL = "warn";
process.env.NEXORA_START = "0";

const { app, startup } = await import("./dist/server.js");

await startup();

app.listen(8000, "127.0.0.1", async () => {
  // Seed an account so the dashboard has something to server-render.
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

  console.log("BACKEND_READY http://127.0.0.1:8000");
  console.log("SEED_ACCOUNT demo@example.com / demo-password-123");
  console.log("SEED_COOKIE " + (cookie ?? "none"));
});
