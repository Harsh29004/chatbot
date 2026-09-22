import type { Metadata } from "next";

import { Support } from "@/screens/Support";
import { serverApi } from "@/lib/api-server";
import { requireCustomer } from "@/lib/guard";

export const metadata: Metadata = { title: "Support" };

/**
 * The conversation is rendered server-side; the client takes over polling for
 * replies from there.
 */
export default async function SupportPage() {
  await requireCustomer("/support");

  const conversation = await serverApi.supportMessages();
  return <Support initialMessages={conversation?.messages ?? null} />;
}
