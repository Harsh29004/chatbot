import type { Metadata } from "next";

import { Assistant } from "@/screens/Assistant";
import { serverApi } from "@/lib/api-server";
import { requireCustomer } from "@/lib/guard";

export const metadata: Metadata = { title: "Assistant" };

export default async function AssistantPage() {
  await requireCustomer("/assistant");

  const [status, threads] = await Promise.all([
    serverApi.assistantStatus(),
    serverApi.assistantThreads(),
  ]);

  return <Assistant initialStatus={status} initialThreads={threads} />;
}
