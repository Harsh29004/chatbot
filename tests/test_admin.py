"""
Tests for the admin panel.

Two things are being guarded. First that the panel is *shut*: it reads every
tenant's data, so a customer session or a customer API key must get nowhere
near it. Second that the numbers it reports are the same numbers the rest of
the system acts on — an admin screen that disagrees with billing is worse than
no admin screen, because someone will trust it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.billing import db, referrals
from backend.billing.plans import PLANS
from backend.shared.api_keys import (
    generate_api_key,
    get_bonus_balance,
    get_user_by_email,
    init_api_key_tables,
    set_daily_credit_limit,
    validate_api_key,
)
from backend.shared.config import ADMIN_API_KEY
from bot import catalogue, store

ADMIN = {"X-Admin-Key": ADMIN_API_KEY}


@pytest.fixture(autouse=True)
def _tables():
    init_api_key_tables()
    db.init_billing_tables()
    store.init_bot_tables()
    catalogue.init_template_tables()
    yield
    # Template edits are global state; a test that changes one must not leave
    # it changed for the next.
    for template in catalogue.list_for_admin():
        catalogue.reset_override(template["id"])


@pytest.fixture()
def client() -> TestClient:
    from backend.server import app

    with TestClient(app) as test_client:
        yield test_client


def _customer(email: str, name: str = "Admin Test") -> dict:
    customer = db.create_customer(email, name, "hash")
    db.start_subscription(customer["id"], PLANS["trial"], db.STATUS_TRIALING, provider="none")
    set_daily_credit_limit(email, PLANS["trial"].daily_credits, name=name)
    return customer


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

ADMIN_ROUTES = [
    "/api/admin/overview",
    "/api/admin/health",
    "/api/admin/users",
    "/api/admin/subscriptions",
    "/api/admin/usage",
    "/api/admin/audit",
    "/api/admin/ai-usage",
    "/api/admin/templates",
    "/api/admin/referrals",
]


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_no_admin_route_opens_without_the_admin_key(client, path):
    assert client.get(path).status_code in {401, 403, 422}


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_a_wrong_admin_key_opens_nothing(client, path):
    assert client.get(path, headers={"X-Admin-Key": "not-the-key"}).status_code == 403


def test_a_customer_api_key_is_not_an_admin_key(client):
    """A valid customer key is valid for the customer's own data, and nothing else."""
    customer = _customer("keyholder@example.com")
    key = generate_api_key(customer["email"], customer["name"])["api_key"]

    assert validate_api_key(key) is not None  # the key genuinely works
    response = client.get("/api/admin/users", headers={"X-Admin-Key": key})
    assert response.status_code == 403


def test_every_admin_route_answers_with_the_key(client):
    for path in ADMIN_ROUTES:
        assert client.get(path, headers=ADMIN).status_code == 200, path


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def test_the_user_list_shows_an_accounts_plan_bot_and_referral_standing(client):
    referrer = _customer("lister-ref@example.com", "Referrer")
    joined = _customer("lister-joiner@example.com", "Joiner")
    referrals.attach(joined, referrals.code_for_customer(referrer["id"]))

    user = get_user_by_email(joined["email"])
    store.get_or_create_bot(user["id"])

    body = client.get(
        "/api/admin/users", params={"q": "lister-joiner"}, headers=ADMIN
    ).json()

    assert body["total"] == 1
    row = body["users"][0]
    assert row["email"] == "lister-joiner@example.com"
    assert row["plan_id"] == "trial"
    assert row["is_entitled"] is True
    assert row["bot_status"] == store.STATUS_DRAFT
    assert row["referred_by_email"] == referrer["email"]
    assert row["bonus_credits"] == referrals.REFERRED_SIGNUP_CREDITS


def test_the_search_filters_rather_than_returning_everyone(client):
    _customer("findme-unique@example.com")
    _customer("someone-else@example.com")

    found = client.get("/api/admin/users", params={"q": "findme-unique"}, headers=ADMIN).json()
    assert found["total"] == 1

    everyone = client.get("/api/admin/users", headers=ADMIN).json()
    assert everyone["total"] > found["total"]


def test_user_detail_carries_the_keys_grants_and_invoices(client):
    customer = _customer("detail@example.com")
    generate_api_key(customer["email"], customer["name"], label="prod")

    client.post(
        f"/api/admin/users/{customer['id']}/credits",
        json={"credits": 250, "note": "outage goodwill"},
        headers=ADMIN,
    )

    detail = client.get(f"/api/admin/users/{customer['id']}", headers=ADMIN).json()
    assert detail["account"]["email"] == customer["email"]
    assert [k["label"] for k in detail["keys"]] == ["prod"]
    assert detail["credit_grants"][0]["amount"] == 250
    assert detail["credit_grants"][0]["note"] == "outage goodwill"


def test_a_missing_customer_is_a_404_not_an_empty_page(client):
    assert client.get("/api/admin/users/999999", headers=ADMIN).status_code == 404


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------

def test_granting_credits_actually_moves_the_balance(client):
    customer = _customer("granted@example.com")
    user = get_user_by_email(customer["email"])

    response = client.post(
        f"/api/admin/users/{customer['id']}/credits",
        json={"credits": 500, "note": "support call"},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    assert get_bonus_balance(user["id"]) == 500


def test_a_credit_grant_has_to_be_a_positive_number(client):
    customer = _customer("badgrant@example.com")
    for amount in (0, -100):
        response = client.post(
            f"/api/admin/users/{customer['id']}/credits",
            json={"credits": amount},
            headers=ADMIN,
        )
        assert response.status_code == 422


def test_setting_a_daily_limit_and_clearing_it_again(client):
    customer = _customer("limited@example.com")

    client.post(
        f"/api/admin/users/{customer['id']}/limit",
        json={"daily_credit_limit": 9999},
        headers=ADMIN,
    )
    assert get_user_by_email(customer["email"])["daily_credit_limit"] == 9999

    client.post(
        f"/api/admin/users/{customer['id']}/limit",
        json={"daily_credit_limit": None},
        headers=ADMIN,
    )
    assert get_user_by_email(customer["email"])["daily_credit_limit"] is None


def test_disabling_an_account_closes_both_halves_of_it(client):
    """
    Half a disable is the worst outcome: someone who cannot sign in but whose
    bot is still answering the public.
    """
    customer = _customer("disabled@example.com")
    key = generate_api_key(customer["email"], customer["name"])["api_key"]

    response = client.post(
        f"/api/admin/users/{customer['id']}/active", json={"is_active": False}, headers=ADMIN
    )
    assert response.status_code == 200

    assert db.get_customer_by_id(customer["id"])["is_active"] == 0
    assert validate_api_key(key) is None  # the key stops working too

    client.post(
        f"/api/admin/users/{customer['id']}/active", json={"is_active": True}, headers=ADMIN
    )
    assert validate_api_key(key) is not None


def test_admin_can_revoke_any_key(client):
    customer = _customer("revokee@example.com")
    created = generate_api_key(customer["email"], customer["name"])

    assert client.delete(f"/api/admin/keys/{created['key_id']}", headers=ADMIN).status_code == 200
    assert validate_api_key(created["api_key"]) is None
    assert client.delete("/api/admin/keys/999999", headers=ADMIN).status_code == 404


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def test_editing_a_template_reaches_the_bots_already_running_on_it(client):
    """The reason this feature exists: fixing wording without a deploy."""
    response = client.patch(
        "/api/admin/templates/ecommerce",
        json={"decline_message": "We only answer order questions here."},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    assert "decline_message" in response.json()["overridden_fields"]

    assert catalogue.get_template("ecommerce").decline_message == (
        "We only answer order questions here."
    )


def test_resetting_a_template_restores_the_code_default(client):
    from bot.templates import get_template as code_template

    original = code_template("ecommerce").tagline
    client.patch(
        "/api/admin/templates/ecommerce", json={"tagline": "Changed"}, headers=ADMIN
    )
    assert catalogue.get_template("ecommerce").tagline == "Changed"

    client.post("/api/admin/templates/ecommerce/reset", headers=ADMIN)
    assert catalogue.get_template("ecommerce").tagline == original


def test_a_threshold_that_would_break_matching_is_refused(client):
    response = client.patch(
        "/api/admin/templates/ecommerce",
        json={"near_threshold": 0.99, "strong_threshold": 0.5},
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert "strong" in response.json()["detail"]


def test_a_retired_template_disappears_from_the_customer_picker(client):
    """Retiring is about new bots. Existing ones must keep working."""
    before = {t["id"] for t in catalogue.list_templates()}
    assert "services" in before

    client.patch("/api/admin/templates/services", json={"enabled": False}, headers=ADMIN)

    assert "services" not in {t["id"] for t in catalogue.list_templates()}
    assert catalogue.get_template("services") is not None  # still resolvable for live bots
    assert "services" in {t["id"] for t in catalogue.list_for_admin()}


def test_the_template_screen_shows_how_many_bots_each_one_carries(client):
    customer = _customer("adopter@example.com")
    user = get_user_by_email(customer["email"])
    store.get_or_create_bot(user["id"])
    store.set_template(user["id"], "clinic", "Clinic bot")

    templates = client.get("/api/admin/templates", headers=ADMIN).json()["templates"]
    clinic = next(t for t in templates if t["id"] == "clinic")
    assert clinic["bots"] >= 1


def test_editing_a_field_that_is_not_editable_is_refused(client):
    response = client.patch(
        "/api/admin/templates/ecommerce", json={"reset": ["starter_rows"]}, headers=ADMIN
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_the_overview_counts_the_same_subscriptions_billing_does(client):
    _customer("counted-1@example.com")
    _customer("counted-2@example.com")

    overview = client.get("/api/admin/overview", headers=ADMIN).json()
    subscriptions = client.get("/api/admin/subscriptions", headers=ADMIN).json()

    assert overview["subscriptions"]["entitled"] == subscriptions["counts"]["entitled"]
    assert overview["customers"]["total"] >= 2


def test_a_paid_invoice_shows_up_as_revenue(client):
    customer = _customer("payer@example.com")
    sub = db.start_subscription(customer["id"], PLANS["monthly"], db.STATUS_ACTIVE)
    db.create_invoice(customer["id"], sub["id"], PLANS["monthly"], "USD", "open")

    before = client.get("/api/admin/overview", headers=ADMIN).json()["revenue"]["all_time_cents"]
    db.mark_open_invoices_paid(customer["id"], sub["id"])
    after = client.get("/api/admin/overview", headers=ADMIN).json()["revenue"]["all_time_cents"]

    assert after - before == PLANS["monthly"].price_cents


def test_mrr_counts_a_yearly_plan_as_a_twelfth(client):
    """A year paid up front is cash, not a monthly run rate."""
    customer = _customer("yearly@example.com")
    db.start_subscription(customer["id"], PLANS["yearly"], db.STATUS_ACTIVE)

    before = client.get("/api/admin/overview", headers=ADMIN).json()["subscriptions"]["mrr_cents"]
    other = _customer("yearly-2@example.com")
    db.start_subscription(other["id"], PLANS["yearly"], db.STATUS_ACTIVE)
    after = client.get("/api/admin/overview", headers=ADMIN).json()["subscriptions"]["mrr_cents"]

    assert after - before == round(PLANS["yearly"].price_cents / 12)


def test_the_audit_log_records_which_key_made_a_request(client, demo_bot):
    """
    The audit screen has to answer "who called this, with which key?".

    Deliberately *not* using the ``api_client`` fixture: that one overrides
    ``verify_api_key``, and writing the audit row is something the real
    dependency does. A test that skipped it would pass while the trail stayed
    empty in production.
    """
    from tests.conftest import DEMO_EMAIL

    created = generate_api_key(DEMO_EMAIL, "Demo Shop", label="audited")

    answered = client.post(
        "/v1/ask",
        json={"message": "How do I get a refund?", "session_id": "audit-test"},
        headers={"X-Api-Key": created["api_key"]},
    )
    assert answered.status_code == 200, answered.text

    audit = client.get("/api/admin/audit", params={"days": 1}, headers=ADMIN).json()
    entry = next(e for e in audit["entries"] if e["endpoint"] == "/v1/ask")

    assert entry["key_prefix"] == created["key_prefix"]
    assert entry["owner_email"] == DEMO_EMAIL
    assert entry["credit_cost"] >= 1
    assert audit["summary"]["requests"] >= 1


def test_the_audit_log_can_be_narrowed_to_owner_keys(client):
    from backend.shared.api_keys import create_owner_key

    create_owner_key("auditowner@example.com", "Owner")
    entries = client.get(
        "/api/admin/audit", params={"role": "owner", "days": 7}, headers=ADMIN
    ).json()
    assert all(entry["role"] == "owner" for entry in entries["entries"])
    assert any(k["owner_email"] == "auditowner@example.com" for k in entries["owner_keys"])


def test_ai_usage_is_grouped_by_the_plan_people_are_on(client):
    _customer("ai-usage@example.com")
    body = client.get("/api/admin/ai-usage", headers=ADMIN).json()

    plans = {bucket["plan_id"] for bucket in body["by_plan"]}
    assert "trial" in plans
    assert "rewording" in body and "retrieval" in body


def test_the_referral_screen_reports_the_programme(client):
    referrer = _customer("prog-ref@example.com", "Programme")
    joiner = _customer("prog-joiner@example.com")
    referrals.attach(joiner, referrals.code_for_customer(referrer["id"]))

    body = client.get("/api/admin/referrals", headers=ADMIN).json()
    assert body["totals"]["referrals"] >= 1
    assert body["rewards"]["referrer_signup_credits"] == referrals.REFERRER_SIGNUP_CREDITS
    assert any(row["email"] == referrer["email"] for row in body["leaderboard"])


def test_a_trial_is_entitled_but_is_not_a_paying_customer(client):
    """
    The two numbers must stay apart. A dashboard that counts trials as revenue
    is a dashboard that flatters itself.
    """
    _customer("on-trial@example.com")
    payer = _customer("really-paying@example.com")
    db.start_subscription(payer["id"], PLANS["monthly"], db.STATUS_ACTIVE)

    overview = client.get("/api/admin/overview", headers=ADMIN).json()["subscriptions"]
    subs = client.get("/api/admin/subscriptions", headers=ADMIN).json()["counts"]

    assert overview["paying"] == 1
    assert overview["on_trial"] == 1
    assert overview["entitled"] == overview["paying"] + overview["on_trial"]
    assert subs["paying"] == overview["paying"]
