import type { Metadata } from "next";

import { Landing } from "@/screens/Landing";
import { serverApi } from "@/lib/api-server";

/**
 * The landing page — server-rendered.
 *
 * Pricing and the template catalogue are public reads, so they are fetched here
 * and passed down as props. The HTML a crawler (or a first-time visitor on a
 * slow connection) receives already contains the prices and all ten templates,
 * which the Vite build could not do: there, the page shipped empty and filled
 * itself in two round trips after the bundle booted.
 *
 * Both reads are revalidated rather than per-request, because they are the same
 * for everybody and change when an admin edits a template, not when someone
 * visits.
 */

export const metadata: Metadata = {
  description:
    "Upload your FAQ sheet, get an API key, ship a support bot that only " +
    "answers from your own approved answers. Ten ready-made templates.",
};

export default async function HomePage() {
  // In parallel: neither depends on the other, and a landing page should not
  // pay for them one after the next.
  const [pricing, templates] = await Promise.all([
    serverApi.pricing().catch(() => null),
    serverApi.templates().catch(() => null),
  ]);

  return <Landing initialPricing={pricing} initialTemplates={templates} />;
}
