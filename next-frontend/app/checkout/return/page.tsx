import type { Metadata } from "next";

import { CheckoutReturn } from "@/screens/CheckoutReturn";
import { requireCustomer } from "@/lib/guard";

export const metadata: Metadata = { title: "Checkout" };

/**
 * Where the payment provider sends the customer back to.
 *
 * Gated but not pre-fetched: the whole job of this screen is to confirm and
 * then poll, which is a client concern. Rendering the subscription here would
 * only show the state from *before* the confirmation.
 */
export default async function CheckoutReturnPage() {
  await requireCustomer("/checkout/return");
  return <CheckoutReturn />;
}
