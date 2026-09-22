/**
 * Mint an unlimited owner API key.
 *
 * Port of `backend/scripts/create_owner_key.py`.
 *
 * Owner keys skip all credit checks (see `shared/auth.ts`) and are meant only
 * for the product owners themselves — there is deliberately no HTTP endpoint
 * for this, so it can't be triggered remotely. Run it locally on the machine
 * hosting the database:
 *
 *     npm run create-owner-key -- owner@example.com "Harsh"
 *
 * The raw key is printed once. Store it somewhere safe (password manager, not
 * source control) — only its hash is kept in the database.
 */

import { createOwnerKey } from "../shared/apiKeys.js";
import { ensureIndexes, resetClient } from "../shared/mongo.js";

async function main(): Promise<void> {
  const args = process.argv.slice(2);

  if (args.length < 1) {
    console.log("Usage: npm run create-owner-key -- <email> [name]");
    process.exit(1);
  }

  const email = args[0];
  const name = args[1] ?? "";

  // The unique index on `users.email` is what stops a second account being
  // created for the same mailbox, so it has to exist before the upsert runs.
  await ensureIndexes();

  const result = await createOwnerKey(email, name);

  console.log("Owner key created — copy it now, it will not be shown again:\n");
  console.log(`  ${result.api_key}\n`);
  console.log(`  owner_email: ${result.owner_email}`);
  console.log(`  key_id:      ${result.key_id}`);
  console.log(`  role:        ${result.role}`);

  await resetClient(null);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
