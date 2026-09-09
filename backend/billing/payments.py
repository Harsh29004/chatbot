"""
Payment provider adapters.

Design rule: **we never see a card number.** Every real provider here is a
*hosted* checkout — we create a session server-side, redirect the customer to
the provider's own page, and learn the outcome from a signed webhook. That
keeps card data entirely outside this codebase (and keeps PCI scope to
SAQ-A). Do not add a card form to this project; if a provider needs one, use
their hosted fields/iframe so the data never hits our origin.

Switch providers with ``BILLING_PROVIDER``:

  manual   — development only. No money moves. Activation must be confirmed
             explicitly and is refused unless BILLING_ALLOW_MANUAL=true.
  stripe   — Stripe Checkout (see StripeProvider for the wiring steps).
  razorpay — Razorpay hosted checkout / payment links.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

from backend.billing.plans import Plan

BILLING_PROVIDER = os.getenv("BILLING_PROVIDER", "manual").lower()

# The manual provider hands out entitlements with no payment. That is fine on
# a laptop and a disaster in production, so it stays behind its own flag.
BILLING_ALLOW_MANUAL = os.getenv("BILLING_ALLOW_MANUAL", "false").lower() == "true"


@dataclass(frozen=True)
class CheckoutSession:
    """Where to send the customer, and how we'll recognise them coming back."""

    url: str
    reference: str
    provider: str
    requires_manual_confirmation: bool = False


class PaymentProvider(Protocol):
    name: str

    def create_checkout(
        self, *, customer: dict[str, Any], plan: Plan, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        ...


class ManualProvider:
    """
    Development stand-in. Creates a reference and points the browser at our
    own confirmation screen. No payment is taken and none is simulated —
    the subscription stays ``pending`` until something explicitly confirms it.
    """

    name = "manual"

    def create_checkout(
        self, *, customer: dict[str, Any], plan: Plan, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        reference = f"manual_{secrets.token_hex(12)}"
        return CheckoutSession(
            url=f"{success_url}?reference={reference}&plan={plan.id}",
            reference=reference,
            provider=self.name,
            requires_manual_confirmation=True,
        )


class StripeProvider:
    """
    Stripe Checkout.

    To finish wiring this up:

    1. ``pip install stripe`` and set ``STRIPE_SECRET_KEY``.
    2. In the Stripe dashboard create one Product with two recurring Prices
       ($15/month and $140/year) and put their price IDs in
       ``STRIPE_PRICE_MONTHLY`` / ``STRIPE_PRICE_YEARLY``.
    3. Replace the body of ``create_checkout`` with::

           session = stripe.checkout.Session.create(
               mode="subscription",
               customer_email=customer["email"],
               line_items=[{"price": price_id, "quantity": 1}],
               success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
               cancel_url=cancel_url,
           )
           return CheckoutSession(url=session.url, reference=session.id,
                                  provider=self.name)

    4. Point a webhook at ``POST /api/billing/webhook`` for
       ``checkout.session.completed`` and ``customer.subscription.deleted``,
       set ``STRIPE_WEBHOOK_SECRET``, and verify the signature in
       ``verify_webhook`` below before trusting anything in the payload.

    Until then this raises rather than silently granting access.
    """

    name = "stripe"

    def create_checkout(
        self, *, customer: dict[str, Any], plan: Plan, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        raise NotImplementedError(
            "StripeProvider is not wired up yet — see the class docstring for "
            "the four steps. Set BILLING_PROVIDER=manual for local development."
        )


class RazorpayProvider:
    """
    Razorpay hosted checkout — the usual choice for INR billing in India.

    Wiring is the same shape as Stripe: create a Subscription against a Plan
    ID, redirect to ``short_url``, and confirm via the
    ``subscription.charged`` webhook with signature verification.
    """

    name = "razorpay"

    def create_checkout(
        self, *, customer: dict[str, Any], plan: Plan, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        raise NotImplementedError(
            "RazorpayProvider is not wired up yet. Set BILLING_PROVIDER=manual "
            "for local development."
        )


_PROVIDERS: dict[str, PaymentProvider] = {
    "manual": ManualProvider(),
    "stripe": StripeProvider(),
    "razorpay": RazorpayProvider(),
}


def get_provider() -> PaymentProvider:
    provider = _PROVIDERS.get(BILLING_PROVIDER)
    if provider is None:
        raise RuntimeError(
            f"Unknown BILLING_PROVIDER={BILLING_PROVIDER!r}. "
            f"Expected one of: {', '.join(_PROVIDERS)}"
        )
    return provider


def manual_activation_allowed() -> bool:
    """Manual activation requires both the manual provider and its opt-in flag."""
    return BILLING_PROVIDER == "manual" and BILLING_ALLOW_MANUAL
