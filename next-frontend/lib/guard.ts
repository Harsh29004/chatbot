import "server-only";

import { redirect } from "next/navigation";

import { currentCustomer } from "./api-server";
import type { Customer } from "./types";

/**
 * Server-side gate for the signed-in routes.
 *
 * This is the real improvement the App Router buys over the Vite build. There,
 * `<RequireAuth>` ran in the browser: the visitor downloaded the dashboard
 * bundle, React booted, `/auth/me` came back 401, and only then were they sent
 * to sign in. A signed-out person paid for the whole page before being told
 * they could not see it.
 *
 * Here the check happens before the response starts. Someone without a session
 * gets a redirect and nothing else — no bundle, no flash of a dashboard shell,
 * and no possibility of protected markup reaching a browser that should not
 * have it.
 *
 * `from` is carried so sign-in can return them to where they were heading.
 */
export async function requireCustomer(from: string): Promise<Customer> {
  const customer = await currentCustomer();
  if (!customer) {
    redirect(`/signin?from=${encodeURIComponent(from)}`);
  }
  return customer;
}
