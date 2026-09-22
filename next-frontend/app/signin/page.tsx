import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SignIn } from "@/screens/Auth";
import { currentCustomer } from "@/lib/api-server";

export const metadata: Metadata = { title: "Sign in" };

/**
 * Signed-in visitors are sent to the dashboard before the page renders.
 *
 * Server-side, so they never see the sign-in form flash up first.
 */
export default async function SignInPage() {
  if (await currentCustomer()) redirect("/dashboard");
  return <SignIn />;
}
