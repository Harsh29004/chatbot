import type { Metadata } from "next";

import { Dashboard } from "@/screens/Dashboard";
import { serverApi } from "@/lib/api-server";
import { requireCustomer } from "@/lib/guard";

export const metadata: Metadata = { title: "Dashboard" };

/**
 * The dashboard — gated and populated on the server.
 *
 * Four reads, all in parallel. Two are session-scoped (the dashboard payload
 * and this account's bot) and two are public catalogue reads the page renders
 * inside it. Doing them here rather than in four `useEffect`s means the page
 * arrives with its numbers already in it.
 *
 * `catch(() => null)` on the bot is deliberate and matches the old client-side
 * behaviour: a bot only exists once the account has an API identity, and the
 * setup card handles the absence. A failure there must not take down a page
 * that is otherwise fine.
 */
export default async function DashboardPage() {
  await requireCustomer("/dashboard");

  const [dashboard, bot, templates, pricing, referrals] = await Promise.all([
    serverApi.dashboard(),
    serverApi.bot().catch(() => null),
    serverApi.templates().catch(() => null),
    serverApi.pricing().catch(() => null),
    // Same reasoning as the bot: a slow or failing referral query should cost
    // the customer a card, not the page.
    serverApi.referrals().catch(() => null),
  ]);

  return (
    <Dashboard
      initialDashboard={dashboard}
      initialBot={bot}
      initialTemplates={templates}
      initialPricing={pricing}
      initialReferrals={referrals}
    />
  );
}
