import type { Metadata } from "next";

import { Admin } from "@/screens/Admin";

export const metadata: Metadata = { title: "Admin", robots: { index: false } };

/**
 * The staff admin panel.
 *
 * Deliberately *not* server-rendered and deliberately not behind
 * `requireCustomer`. It authenticates with `X-Admin-Key`, held in
 * sessionStorage so it dies with the tab — that credential opens every
 * customer's data, and keeping it out of the request the server sees is the
 * point. A server-rendered version would have to accept it as a cookie, which
 * would make it survive the tab closing and ride along on every request to
 * every route.
 *
 * So this route renders a shell and the client fetches with the key it holds.
 */
export default function AdminPage() {
  return <Admin />;
}
