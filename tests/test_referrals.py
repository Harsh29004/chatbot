"""
Tests for the referral programme.

The rules being pinned down here are the ones a customer would notice if they
broke: both sides get paid at signup, the referrer gets paid again every time
their referral pays, and none of it can be claimed twice or claimed for
yourself. The payout amounts themselves are configuration and are read from
the module rather than hardcoded, so changing the numbers does not break the
tests that guard the mechanism.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.billing import db, referrals
from backend.billing.plans import PLANS
from backend.shared.api_keys import (
    get_bonus_balance,
    get_credits_remaining,
    get_user_by_email,
    grant_bonus_credits,
    init_api_key_tables,
    set_daily_credit_limit,
)


@pytest.fixture(autouse=True)
def _tables():
    init_api_key_tables()
    db.init_billing_tables()


def _customer(email: str, name: str = "Test") -> dict:
    """A signed-up customer with an allowance, the way signup leaves them."""
    customer = db.create_customer(email, name, "hash")
    db.start_subscription(customer["id"], PLANS["trial"], db.STATUS_TRIALING, provider="none")
    set_daily_credit_limit(email, PLANS["trial"].daily_credits, name=name)
    return customer


def _balance(email: str) -> int:
    user = get_user_by_email(email)
    return get_bonus_balance(user["id"]) if user else 0


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------

def test_both_sides_are_paid_when_a_code_is_used():
    referrer = _customer("alice-ref@example.test", "Alice")
    code = referrals.code_for_customer(referrer["id"])

    joiner = _customer("bob-ref@example.test", "Bob")
    referral = referrals.attach(joiner, code)

    assert referral is not None
    assert _balance(referrer["email"]) == referrals.REFERRER_SIGNUP_CREDITS
    assert _balance(joiner["email"]) == referrals.REFERRED_SIGNUP_CREDITS


def test_referral_credits_are_spendable_on_top_of_the_daily_allowance():
    """
    The point of the reward: it *raises the ceiling*, it doesn't just decorate
    the dashboard.
    """
    referrer = _customer("ceiling-ref@example.test")
    joiner = _customer("ceiling-joiner@example.test")
    referrals.attach(joiner, referrals.code_for_customer(referrer["id"]))

    user = get_user_by_email(joiner["email"])
    allowance = PLANS["trial"].daily_credits

    assert get_credits_remaining(user["id"], allowance) == (
        allowance + referrals.REFERRED_SIGNUP_CREDITS
    )


def test_a_code_nobody_owns_is_ignored_rather_than_failing_the_signup():
    joiner = _customer("nocode@example.test")
    assert referrals.attach(joiner, "ZZZZZZZZ") is None
    assert _balance(joiner["email"]) == 0


def test_you_cannot_refer_yourself():
    solo = _customer("solo@example.test")
    assert referrals.attach(solo, referrals.code_for_customer(solo["id"])) is None
    assert _balance(solo["email"]) == 0


def test_a_second_referrer_cannot_claim_someone_already_referred():
    first = _customer("first-ref@example.test")
    second = _customer("second-ref@example.test")
    joiner = _customer("contested@example.test")

    referrals.attach(joiner, referrals.code_for_customer(first["id"]))
    assert referrals.attach(joiner, referrals.code_for_customer(second["id"])) is None

    assert _balance(first["email"]) == referrals.REFERRER_SIGNUP_CREDITS
    assert _balance(second["email"]) == 0
    # And the joiner is not paid twice for joining once.
    assert _balance(joiner["email"]) == referrals.REFERRED_SIGNUP_CREDITS


def test_an_invited_email_is_attributed_without_a_code():
    """"They signed up with that email" is the other way a referral happens."""
    referrer = _customer("inviter@example.test")
    referrals.invite(referrer["id"], "Invited@Example.test")

    joiner = _customer("invited@example.test")
    referral = referrals.attach(joiner, code=None)

    assert referral is not None
    assert referral["source"] == referrals.SOURCE_INVITE
    assert _balance(referrer["email"]) == referrals.REFERRER_SIGNUP_CREDITS
    assert referrals.list_invites(referrer["id"])[0]["claimed_at"] is not None


# ---------------------------------------------------------------------------
# Top-ups
# ---------------------------------------------------------------------------

def _pay(customer: dict) -> list[dict]:
    """Bill and settle one monthly invoice, as activation does."""
    sub = db.start_subscription(customer["id"], PLANS["monthly"], db.STATUS_ACTIVE)
    db.create_invoice(customer["id"], sub["id"], PLANS["monthly"], "USD", "open")
    return db.mark_open_invoices_paid(customer["id"], sub["id"])


def test_the_referrer_is_paid_every_time_their_referral_pays():
    referrer = _customer("topup-ref@example.test")
    joiner = _customer("topup-joiner@example.test")
    referrals.attach(joiner, referrals.code_for_customer(referrer["id"]))

    for month in range(1, 4):
        referrals.reward_payment(joiner["id"], _pay(joiner))
        assert _balance(referrer["email"]) == (
            referrals.REFERRER_SIGNUP_CREDITS + referrals.TOPUP_CREDITS * month
        )


def test_the_same_invoice_never_pays_twice():
    """
    Payment webhooks are delivered more than once. The reward has to be keyed
    on the invoice, not on the call.
    """
    referrer = _customer("dupe-ref@example.test")
    joiner = _customer("dupe-joiner@example.test")
    referrals.attach(joiner, referrals.code_for_customer(referrer["id"]))

    invoices = _pay(joiner)
    referrals.reward_payment(joiner["id"], invoices)
    referrals.reward_payment(joiner["id"], invoices)
    referrals.reward_payment(joiner["id"], invoices)

    assert _balance(referrer["email"]) == (
        referrals.REFERRER_SIGNUP_CREDITS + referrals.TOPUP_CREDITS
    )


def test_a_payment_from_someone_nobody_referred_pays_nothing():
    lone = _customer("lone-payer@example.test")
    assert referrals.reward_payment(lone["id"], _pay(lone)) == 0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_the_summary_masks_the_addresses_of_people_you_referred():
    referrer = _customer("privacy-ref@example.test")
    joiner = _customer("jordan@example.test", "Jordan")
    referrals.attach(joiner, referrals.code_for_customer(referrer["id"]))

    summary = referrals.summary_for(referrer["id"])
    listed = summary["referred"][0]["email"]

    assert "jordan@example.test" not in listed
    assert listed.endswith("@example.test")
    assert summary["credits_earned"] == referrals.REFERRER_SIGNUP_CREDITS


def test_the_summary_tells_a_referred_customer_who_brought_them():
    referrer = _customer("origin-ref@example.test")
    joiner = _customer("origin-joiner@example.test")
    referrals.attach(joiner, referrals.code_for_customer(referrer["id"]))

    assert referrals.summary_for(joiner["id"])["joined_via"] is not None
    assert referrals.summary_for(referrer["id"])["joined_via"] is None


def test_a_code_is_stable_once_minted():
    customer = _customer("stable-code@example.test")
    assert referrals.code_for_customer(customer["id"]) == referrals.code_for_customer(
        customer["id"]
    )


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------

def test_signup_with_a_referral_code_pays_both_sides_through_the_api():
    from backend.server import app

    referrer = _customer("api-ref@example.com")
    code = referrals.code_for_customer(referrer["id"])

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/signup",
            json={
                "email": "api-joiner@example.com",
                "name": "Joiner",
                "password": "a-long-enough-password",
                "referral_code": code.lower(),  # codes are case-insensitive
            },
        )
        assert response.status_code == 201, response.text

        # And the new account can see where it stands.
        referral_view = client.get("/api/referrals")
        assert referral_view.status_code == 200, referral_view.text
        body = referral_view.json()
        assert body["joined_via"] is not None
        assert body["code"]
        assert body["link"].endswith(f"?ref={body['code']}")

    assert _balance(referrer["email"]) == referrals.REFERRER_SIGNUP_CREDITS
    assert _balance("api-joiner@example.com") == referrals.REFERRED_SIGNUP_CREDITS


def test_inviting_yourself_is_refused_with_a_reason():
    from backend.billing.router import current_customer
    from backend.server import app

    customer = _customer("self-invite@example.com")
    app.dependency_overrides[current_customer] = lambda: customer
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/referrals/invites", json={"email": customer["email"]}
            )
            assert response.status_code == 400
            assert "yourself" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_bonus_credits_are_spent_only_after_the_daily_allowance():
    """
    Allowance first, bonus second. Spending the permanent pool while the one
    that expires at midnight sits unused would quietly waste the reward.
    """
    from backend.shared.api_keys import consume_credits, generate_api_key

    customer = _customer("spend-order@example.test")
    key = generate_api_key(customer["email"], "Spender")
    user = get_user_by_email(customer["email"])
    grant_bonus_credits(user["id"], 50, reason="test")

    consume_credits(user["id"], key["key_id"], 3, "/v1/ask", 20, daily_credit_limit=10)

    assert get_bonus_balance(user["id"]) == 50          # untouched
    assert get_credits_remaining(user["id"], 10) == 57  # 7 of today's + 50 bonus

    # Now overshoot the allowance: the remaining 7 come from the day, 5 from bonus.
    consume_credits(user["id"], key["key_id"], 12, "/v1/ask", 20, daily_credit_limit=10)
    assert get_bonus_balance(user["id"]) == 45
    assert get_credits_remaining(user["id"], 10) == 45
