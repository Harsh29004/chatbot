import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SignUp } from "@/screens/Auth";
import { currentCustomer } from "@/lib/api-server";

export const metadata: Metadata = {
  title: "Create an account",
  description: "Start the free trial. No card needed.",
};

export default async function SignUpPage() {
  if (await currentCustomer()) redirect("/dashboard");
  return <SignUp />;
}
