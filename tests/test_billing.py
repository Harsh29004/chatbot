"""
Tests for the paid platform: pricing maths, accounts, entitlement, and the
rules that stop a customer helping themselves to more than they paid for.

These cover the properties that are expensive to get wrong in a billing
system — credit pooling, key ownership, and unpaid activation — rather than
re-testing FastAPI itself.
"""

from __future__ import annotations

import pytest

from apps.billing import db, entitlements, plans
from apps.billing.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from shared import api_keys as ak


@pytest.fixture(autouse=True)
def _billing_tables():
    """The api-key tables come from conftest; billing tables are ours."""
    db.init_billing_tables()
    # Thread-local connections are per-test-path, so drop the cached handle.
    yield
    if hasattr(db._LOCAL, "billing_conn"):
        del db._LOCAL.billing_conn


def _customer(email: str = "c@example.com", name: str = "C"):
    return db.create_customer(email, name, hash_password("a-long-test-password"))


# ---------------------------------------------------------------------------
# Pricing maths
# ---------------------------------------------------------------------------

def test_monthly_and_yearly_prices():
    assert plans.PLANS["monthly"].price_cents == 1500
    assert plans.PLANS["yearly"].price_cents == 14000


def test_yearly_saves_forty_dollars_a_year():
    # 12 x $15 = $180 against $140.
    assert plans.yearly_savings_cents() == 4000


def test_yearly_discount_is_twenty_two_percent():
    assert plans.yearly_discount_percent() == 22


def test_discount_rounds_down_so_we_never_overstate_it():
    # 40/180 = 22.2%; advertising 23% would promise more than we give.
    exact = plans.yearly_savings_cents() * 100 / (plans.PLANS["monthly"].price_cents * 12)
    assert plans.yearly_discount_percent() <= exact


def test_effective_monthly_on_yearly_plan():
    assert plans.format_cents(plans.yearly_effective_monthly_cents()) == "11.67"


def test_format_cents_drops_trailing_zeroes():
    assert plans.format_cents(1500) == "15"
    assert plans.format_cents(14000) == "140"
    assert plans.format_cents(1167) == "11.67"


def test_pricing_payload_is_self_consistent():
    payload = plans.list_plans()
    monthly = next(p for p in payload["plans"] if p["id"] == "monthly")
    yearly = next(p for p in payload["plans"] if p["id"] == "yearly")
    assert monthly["price_cents"] * 12 - yearly["price_cents"] == payload["yearly_savings_cents"]


# ---------------------------------------------------------------------------
# Passwords and sessions
# ---------------------------------------------------------------------------

def test_password_round_trip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("Correct horse battery staple", stored)
    assert not verify_password("", stored)


def test_password_hash_is_salted():
    # Same password, two hashes — a stolen table can't be attacked in bulk.
    assert hash_password("same-password-twice") != hash_password("same-password-twice")


def test_password_verify_survives_a_corrupt_stored_value():
    assert not verify_password("anything", "not-a-real-hash")


def test_session_round_trip_and_revocation():
    customer = _customer()
    token = generate_session_token()
    db.create_session(customer["id"], token)

    assert db.get_session_customer(token)["id"] == customer["id"]
    assert db.get_session_customer("some-other-token") is None

    db.revoke_session(token)
    assert db.get_session_customer(token) is None


def test_dead_sessions_are_purged_but_live_ones_survive():
    """
    Sessions are written on every sign-in and never removed, so the table grows
    forever — and every row is a hashed credential nobody needs any more.
    """
    from datetime import datetime, timedelta

    from shared import config

    customer = _customer("purge@example.com")

    live = generate_session_token()
    db.create_session(customer["id"], live)

    stale = generate_session_token()
    db.create_session(customer["id"], stale)
    long_ago = (datetime.now(config.IST) - timedelta(days=30)).isoformat()
    conn = db._get_conn()
    conn.execute(
        "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
        (long_ago, hash_session_token(stale)),
    )
    conn.commit()

    assert db.purge_dead_sessions() == 1
    assert db.get_session_customer(live) is not None, "live session was purged!"


def test_purging_is_safe_to_repeat():
    customer = _customer("repeat-purge@example.com")
    db.create_session(customer["id"], generate_session_token())

    assert db.purge_dead_sessions() == 0
    assert db.purge_dead_sessions() == 0


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------

def test_trial_entitles_and_is_single_use():
    customer = _customer("trial@example.com")
    assert not db.has_used_trial(customer["id"])

    db.start_subscription(customer["id"], plans.PLANS["trial"], db.STATUS_TRIALING)

    assert db.is_entitled(db.get_current_subscription(customer["id"]))
    assert db.has_used_trial(customer["id"])


def test_pending_subscription_does_not_entitle():
    """An unpaid checkout must not hand out access."""
    customer = _customer("pending@example.com")
    db.start_subscription(customer["id"], plans.PLANS["yearly"], db.STATUS_PENDING)
    assert not db.is_entitled(db.get_current_subscription(customer["id"]))


def test_cancelled_plan_still_entitles_until_period_end():
    """They paid for the period; cancelling shouldn't cut it short."""
    customer = _customer("cancel@example.com")
    sub = db.start_subscription(customer["id"], plans.PLANS["monthly"], db.STATUS_ACTIVE)
    db.set_subscription_status(sub["id"], db.STATUS_CANCELED)
    assert db.is_entitled(db.get_current_subscription(customer["id"]))


def _lapse(subscription_id: int) -> None:
    """Rewind a subscription's period end so it counts as lapsed."""
    from datetime import datetime, timedelta

    from shared import config

    past = (datetime.now(config.IST) - timedelta(days=1)).isoformat()
    conn = db._get_conn()
    conn.execute(
        "UPDATE subscriptions SET current_period_end = ? WHERE id = ?",
        (past, subscription_id),
    )
    conn.commit()


def test_lapsed_subscription_is_expired_and_stops_entitling():
    customer = _customer("lapsed@example.com")
    sub = db.start_subscription(customer["id"], plans.PLANS["monthly"], db.STATUS_ACTIVE)
    _lapse(sub["id"])

    lapsed = db.expire_lapsed_subscriptions()
    assert len(lapsed) == 1
    assert lapsed[0]["email"] == "lapsed@example.com"

    current = db.get_current_subscription(customer["id"])
    assert current["status"] == db.STATUS_EXPIRED
    assert not db.is_entitled(current)


def test_expiry_returns_the_customer_so_their_allowance_can_be_withdrawn():
    """The sweep has to name who lapsed, or nothing can act on it."""
    customer = _customer("named@example.com", "Named Co")
    sub = db.start_subscription(customer["id"], plans.PLANS["monthly"], db.STATUS_ACTIVE)
    _lapse(sub["id"])

    lapsed = db.expire_lapsed_subscriptions()
    assert lapsed[0]["id"] == customer["id"]
    assert lapsed[0]["name"] == "Named Co"


def test_nothing_lapsed_returns_nothing():
    customer = _customer("current@example.com")
    db.start_subscription(customer["id"], plans.PLANS["monthly"], db.STATUS_ACTIVE)
    assert db.expire_lapsed_subscriptions() == []


# ---------------------------------------------------------------------------
# Entitlement withdrawal — paying for it is what buys the allowance
# ---------------------------------------------------------------------------

def test_a_lapsed_plan_loses_its_paid_allowance():
    """
    Regression guard for a revenue hole: the first version granted the paid
    allowance on activation and never took it back, so a cancelled customer
    kept 5,000 credits/day and a working key indefinitely.
    """
    email = "lapse-credits@example.com"
    customer = _customer(email, "Lapser")
    sub = db.start_subscription(customer["id"], plans.PLANS["yearly"], db.STATUS_ACTIVE)
    entitlements.grant(email, "Lapser", plans.PLANS["yearly"].daily_credits)

    assert ak.get_user_by_email(email)["daily_credit_limit"] == plans.PAID_DAILY_CREDITS

    _lapse(sub["id"])
    assert entitlements.sync() == 1

    # Back on the free default, not still on the paid tier.
    assert ak.get_user_by_email(email)["daily_credit_limit"] is None


def test_a_lapsed_customer_keeps_their_key_but_on_the_free_allowance():
    """
    Withdrawal throttles; it does not break their integration. Someone whose
    card expired should find their bot rate-limited, not returning 403 to
    their users.
    """
    from shared.config import DAILY_CREDIT_LIMIT

    email = "throttled@example.com"
    customer = _customer(email, "Throttled")
    sub = db.start_subscription(customer["id"], plans.PLANS["monthly"], db.STATUS_ACTIVE)
    entitlements.grant(email, "Throttled", plans.PLANS["monthly"].daily_credits)
    key = ak.generate_api_key(email, "Throttled")

    _lapse(sub["id"])
    entitlements.sync()

    record = ak.validate_api_key(key["api_key"])
    assert record is not None, "the key should still authenticate"
    assert ak.get_credits_remaining(record["user_id"], record["daily_credit_limit"]) == (
        DAILY_CREDIT_LIMIT
    )


def test_sync_is_idempotent():
    email = "idempotent@example.com"
    customer = _customer(email)
    sub = db.start_subscription(customer["id"], plans.PLANS["monthly"], db.STATUS_ACTIVE)
    entitlements.grant(email, "", plans.PLANS["monthly"].daily_credits)
    _lapse(sub["id"])

    assert entitlements.sync() == 1
    assert entitlements.sync() == 0, "a second sweep must not re-withdraw"


def test_resubscribing_restores_the_paid_allowance():
    email = "returning@example.com"
    customer = _customer(email, "Returning")
    sub = db.start_subscription(customer["id"], plans.PLANS["monthly"], db.STATUS_ACTIVE)
    entitlements.grant(email, "Returning", plans.PLANS["monthly"].daily_credits)

    _lapse(sub["id"])
    entitlements.sync()
    assert ak.get_user_by_email(email)["daily_credit_limit"] is None

    db.start_subscription(customer["id"], plans.PLANS["yearly"], db.STATUS_ACTIVE)
    entitlements.grant(email, "Returning", plans.PLANS["yearly"].daily_credits)

    assert ak.get_user_by_email(email)["daily_credit_limit"] == plans.PAID_DAILY_CREDITS


def test_an_active_plan_is_left_alone_by_the_sweep():
    email = "safe@example.com"
    customer = _customer(email, "Safe")
    db.start_subscription(customer["id"], plans.PLANS["yearly"], db.STATUS_ACTIVE)
    entitlements.grant(email, "Safe", plans.PLANS["yearly"].daily_credits)

    assert entitlements.sync() == 0
    assert ak.get_user_by_email(email)["daily_credit_limit"] == plans.PAID_DAILY_CREDITS


def test_no_subscription_means_no_entitlement():
    assert not db.is_entitled(None)


# ---------------------------------------------------------------------------
# Credits are pooled per account — the whole point of the users table
# ---------------------------------------------------------------------------

def test_two_keys_on_one_account_share_one_credit_pool():
    k1 = ak.generate_api_key("pool@example.com", "Pool", label="prod")
    k2 = ak.generate_api_key("pool@example.com", "Pool", label="staging")

    r1 = ak.validate_api_key(k1["api_key"])
    r2 = ak.validate_api_key(k2["api_key"])
    assert r1["user_id"] == r2["user_id"]

    ak.consume_credits(r1["user_id"], r1["id"], 10, "/customer-bot/ask", 5)

    # Spending through key 1 is visible to key 2 — no free second allowance.
    assert ak.get_credits_used_today(r2["user_id"]) == 10


def test_extra_keys_do_not_raise_the_daily_limit():
    ak.set_daily_credit_limit("cap@example.com", 100, name="Cap")
    k1 = ak.generate_api_key("cap@example.com", "Cap")
    ak.generate_api_key("cap@example.com", "Cap")
    ak.generate_api_key("cap@example.com", "Cap")

    record = ak.validate_api_key(k1["api_key"])
    assert ak.get_credits_remaining(record["user_id"], record["daily_credit_limit"]) == 100


def test_plan_activation_sets_the_accounts_allowance():
    ak.set_daily_credit_limit("grant@example.com", plans.PAID_DAILY_CREDITS, name="G")
    user = ak.get_user_by_email("grant@example.com")
    assert user["daily_credit_limit"] == plans.PAID_DAILY_CREDITS


def test_dropping_the_allowance_restores_the_default():
    ak.set_daily_credit_limit("reset@example.com", 9999, name="R")
    ak.set_daily_credit_limit("reset@example.com", None, name="R")
    user = ak.get_user_by_email("reset@example.com")
    assert user["daily_credit_limit"] is None

    from shared.config import DAILY_CREDIT_LIMIT

    assert ak.get_credits_remaining(user["id"], None) == DAILY_CREDIT_LIMIT


# ---------------------------------------------------------------------------
# Key ownership — one customer must not touch another's keys
# ---------------------------------------------------------------------------

def test_a_customer_cannot_revoke_someone_elses_key():
    victim = ak.generate_api_key("victim@example.com", "Victim")
    ak.generate_api_key("attacker@example.com", "Attacker")

    assert not ak.revoke_key_for_email(victim["key_id"], "attacker@example.com")
    assert ak.validate_api_key(victim["api_key"]) is not None


def test_a_customer_can_revoke_their_own_key():
    key = ak.generate_api_key("owner@example.com", "Owner")
    assert ak.revoke_key_for_email(key["key_id"], "owner@example.com")
    assert ak.validate_api_key(key["api_key"]) is None


def test_revoking_one_key_leaves_the_accounts_other_keys_working():
    k1 = ak.generate_api_key("multi@example.com", "Multi")
    k2 = ak.generate_api_key("multi@example.com", "Multi")

    ak.revoke_key_for_email(k1["key_id"], "multi@example.com")

    assert ak.validate_api_key(k1["api_key"]) is None
    assert ak.validate_api_key(k2["api_key"]) is not None


def test_key_listing_is_scoped_to_the_account_and_hides_hashes():
    ak.generate_api_key("scoped@example.com", "Scoped", label="one")
    ak.generate_api_key("other@example.com", "Other")

    keys = ak.list_keys_for_email("scoped@example.com")
    assert len(keys) == 1
    assert "key_hash" not in keys[0]


def test_active_key_count_ignores_revoked_keys():
    k1 = ak.generate_api_key("count@example.com", "Count")
    ak.generate_api_key("count@example.com", "Count")
    assert ak.count_active_keys_for_email("count@example.com") == 2

    ak.revoke_key_for_email(k1["key_id"], "count@example.com")
    assert ak.count_active_keys_for_email("count@example.com") == 1


# ---------------------------------------------------------------------------
# Owner keys
# ---------------------------------------------------------------------------

def test_owner_key_is_unlimited_and_marked_as_owner():
    owner = ak.create_owner_key("boss@example.com", "Boss")
    record = ak.validate_api_key(owner["api_key"])

    assert record["role"] == ak.ROLE_OWNER
    assert record["daily_credit_limit"] is None
    assert owner["api_key"].startswith(ak.OWNER_KEY_PREFIX)


def test_customer_keys_are_not_owner_keys():
    key = ak.generate_api_key("normal@example.com", "Normal")
    assert ak.validate_api_key(key["api_key"])["role"] == ak.ROLE_USER


def test_owner_usage_is_logged_without_spending_credits():
    """
    Owner keys skip billing, which is intended. Leaving no trace of what an
    unlimited key did is not — that is precisely the key worth auditing.
    """
    owner = ak.create_owner_key("audited@example.com", "Owner")
    record = ak.validate_api_key(owner["api_key"])

    ak.record_request(record["user_id"], record["id"], "/v1/ask", message_len=42)

    # Logged...
    conn = ak._get_conn()
    row = conn.execute(
        "SELECT endpoint, message_len, credit_cost FROM request_log WHERE user_id = ?",
        (record["user_id"],),
    ).fetchone()
    assert row["endpoint"] == "/v1/ask"
    assert row["message_len"] == 42
    assert row["credit_cost"] == 0

    # ...but nothing was charged.
    assert ak.get_credits_used_today(record["user_id"]) == 0


# ---------------------------------------------------------------------------
# Payment provider guards
# ---------------------------------------------------------------------------

def test_manual_activation_is_off_unless_explicitly_enabled(monkeypatch):
    """The unpaid-activation path needs both switches thrown."""
    import apps.billing.payments as payments

    monkeypatch.setattr(payments, "BILLING_PROVIDER", "manual")
    monkeypatch.setattr(payments, "BILLING_ALLOW_MANUAL", False)
    assert not payments.manual_activation_allowed()

    monkeypatch.setattr(payments, "BILLING_ALLOW_MANUAL", True)
    assert payments.manual_activation_allowed()

    # Never allowed once a real provider is configured, flag or no flag.
    monkeypatch.setattr(payments, "BILLING_PROVIDER", "stripe")
    assert not payments.manual_activation_allowed()


def test_unconfigured_real_provider_refuses_rather_than_granting_access():
    from apps.billing.payments import StripeProvider

    with pytest.raises(NotImplementedError):
        StripeProvider().create_checkout(
            customer={"email": "a@b.c"},
            plan=plans.PLANS["yearly"],
            success_url="http://x/ok",
            cancel_url="http://x/no",
        )


def test_invoice_records_the_plan_price():
    customer = _customer("inv@example.com")
    sub = db.start_subscription(customer["id"], plans.PLANS["yearly"], db.STATUS_ACTIVE)
    invoice = db.create_invoice(
        customer["id"], sub["id"], plans.PLANS["yearly"], "USD", "paid"
    )

    assert invoice["amount_cents"] == 14000
    assert invoice["status"] == "paid"
    assert db.list_invoices(customer["id"])[0]["id"] == invoice["id"]
