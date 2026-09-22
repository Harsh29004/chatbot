/**
 * Payment provider adapters.
 *
 * Port of `backend/billing/payments.py`.
 *
 * Design rule: **we never see a card number.** Every real provider here is a
 * *hosted* checkout — we create a session server-side, redirect the customer to
 * the provider's own page, and learn the outcome from a signed webhook. That
 * keeps card data entirely outside this codebase (and keeps PCI scope to
 * SAQ-A). Do not add a card form to this project; if a provider needs one, use
 * their hosted fields/iframe so the data never hits our origin.
 *
 * Switch providers with `BILLING_PROVIDER`:
 *
 *   manual   — development only. No money moves. Activation must be confirmed
 *              explicitly and is refused unless BILLING_ALLOW_MANUAL=true.
 *   stripe   — Stripe Checkout (see StripeProvider for the wiring steps).
 *   razorpay — Razorpay hosted checkout / payment links.
 */

import crypto from "node:crypto";

import type { Plan } from "./plans.js";
import type { Doc } from "../shared/mongo.js";

export const BILLING_PROVIDER = (process.env.BILLING_PROVIDER ?? "manual").toLowerCase();

// The manual provider hands out entitlements with no payment. That is fine on a
// laptop and a disaster in production, so it stays behind its own flag.
export const BILLING_ALLOW_MANUAL =
  (process.env.BILLING_ALLOW_MANUAL ?? "false").toLowerCase() === "true";

/** Where to send the customer, and how we'll recognise them coming back. */
export interface CheckoutSession {
  url: string;
  reference: string;
  provider: string;
  requires_manual_confirmation: boolean;
}

export interface CheckoutArgs {
  customer: Doc;
  plan: Plan;
  successUrl: string;
  cancelUrl: string;
}

export interface PaymentProvider {
  readonly name: string;
  createCheckout(args: CheckoutArgs): CheckoutSession;
}

/**
 * Development stand-in. Creates a reference and points the browser at our own
 * confirmation screen. No payment is taken and none is simulated — the
 * subscription stays `pending` until something explicitly confirms it.
 */
class ManualProvider implements PaymentProvider {
  readonly name = "manual";

  createCheckout({ plan, successUrl }: CheckoutArgs): CheckoutSession {
    const reference = `manual_${crypto.randomBytes(12).toString("hex")}`;
    return {
      url: `${successUrl}?reference=${reference}&plan=${plan.id}`,
      reference,
      provider: this.name,
      requires_manual_confirmation: true,
    };
  }
}

/**
 * Stripe Checkout.
 *
 * To finish wiring this up:
 *
 * 1. `npm install stripe` and set `STRIPE_SECRET_KEY`.
 * 2. In the Stripe dashboard create one Product with two recurring Prices
 *    ($15/month and $140/year) and put their price IDs in
 *    `STRIPE_PRICE_MONTHLY` / `STRIPE_PRICE_YEARLY`.
 * 3. Replace the body of `createCheckout` with:
 *
 *        const session = await stripe.checkout.sessions.create({
 *          mode: "subscription",
 *          customer_email: customer.email,
 *          line_items: [{ price: priceId, quantity: 1 }],
 *          success_url: successUrl + "?session_id={CHECKOUT_SESSION_ID}",
 *          cancel_url: cancelUrl,
 *        });
 *        return { url: session.url, reference: session.id,
 *                 provider: this.name, requires_manual_confirmation: false };
 *
 *    Note this makes the method async — change the interface to return a
 *    Promise and await it at the one call site in `router.ts`.
 * 4. Point a webhook at `POST /api/billing/webhook` for
 *    `checkout.session.completed` and `customer.subscription.deleted`, set
 *    `STRIPE_WEBHOOK_SECRET`, and verify the signature with
 *    `stripe.webhooks.constructEvent` before trusting anything in the payload.
 *    The raw-body capture the route needs is already wired in `server.ts`.
 *
 * Until then this throws rather than silently granting access.
 */
class StripeProvider implements PaymentProvider {
  readonly name = "stripe";

  createCheckout(_args: CheckoutArgs): CheckoutSession {
    throw new Error(
      "StripeProvider is not wired up yet — see the class doc comment for " +
        "the four steps. Set BILLING_PROVIDER=manual for local development.",
    );
  }
}

/**
 * Razorpay hosted checkout — the usual choice for INR billing in India.
 *
 * Wiring is the same shape as Stripe: create a Subscription against a Plan ID,
 * redirect to `short_url`, and confirm via the `subscription.charged` webhook
 * with signature verification.
 */
class RazorpayProvider implements PaymentProvider {
  readonly name = "razorpay";

  createCheckout(_args: CheckoutArgs): CheckoutSession {
    throw new Error(
      "RazorpayProvider is not wired up yet. Set BILLING_PROVIDER=manual " +
        "for local development.",
    );
  }
}

const PROVIDERS: Record<string, PaymentProvider> = {
  manual: new ManualProvider(),
  stripe: new StripeProvider(),
  razorpay: new RazorpayProvider(),
};

export function getProvider(): PaymentProvider {
  const provider = PROVIDERS[BILLING_PROVIDER];
  if (provider === undefined) {
    throw new Error(
      `Unknown BILLING_PROVIDER=${JSON.stringify(BILLING_PROVIDER)}. ` +
        `Expected one of: ${Object.keys(PROVIDERS).join(", ")}`,
    );
  }
  return provider;
}

/** Manual activation requires both the manual provider and its opt-in flag. */
export function manualActivationAllowed(): boolean {
  return BILLING_PROVIDER === "manual" && BILLING_ALLOW_MANUAL;
}
