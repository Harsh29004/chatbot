"""
One mailbox, one account.

The rule these tests defend is not "emails are unique" — the column always
said that — but "aliases that reach the same inbox are the same account".
With a free trial and a referral bonus on every signup, the difference between
those two is the difference between a signup form and a credit printer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.billing import db, identity
from backend.billing.identity import canonical_email, is_disposable

PASSWORD = "a-long-enough-password"


@pytest.fixture(autouse=True)
def _tables():
    db.init_billing_tables()


@pytest.fixture()
def client() -> TestClient:
    from backend.server import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _no_signup_throttle(monkeypatch):
    """
    The per-IP cap is tested on its own; everywhere else it would just make
    every test that creates a second account fail for the wrong reason.
    """
    monkeypatch.setattr(identity, "MAX_SIGNUPS_PER_IP_PER_DAY", 0)


def signup(client: TestClient, email: str, name: str = "Tester"):
    return client.post(
        "/api/auth/signup", json={"email": email, "name": name, "password": PASSWORD}
    )


# ---------------------------------------------------------------------------
# Canonical form
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written,mailbox",
    [
        ("Harsh.Panchal@Gmail.com", "harshpanchal@gmail.com"),
        ("  harshpanchal@gmail.com  ", "harshpanchal@gmail.com"),
        ("harshpanchal+ref@gmail.com", "harshpanchal@gmail.com"),
        ("h.a.r.s.h.p.a.n.c.h.a.l@googlemail.com", "harshpanchal@gmail.com"),
        ("first.last+tag@outlook.com", "first.last@outlook.com"),
    ],
    ids=["case", "whitespace", "plus tag", "gmail dots + alias domain", "outlook tag"],
)
def test_aliases_reduce_to_the_mailbox_they_reach(written, mailbox):
    assert canonical_email(written) == mailbox


def test_dots_are_only_stripped_for_gmail():
    """
    Everywhere else a dot is a real character. Collapsing it would merge two
    strangers into one account, which is a far worse bug than the one this
    module exists to fix.
    """
    assert canonical_email("first.last@outlook.com") != canonical_email(
        "firstlast@outlook.com"
    )
    assert canonical_email("a.b@example.com") == "a.b@example.com"


def test_two_different_people_stay_different():
    assert canonical_email("alice@gmail.com") != canonical_email("bob@gmail.com")
    assert canonical_email("me@gmail.com") != canonical_email("me@outlook.com")


def test_disposable_providers_are_recognised_through_an_alias():
    assert is_disposable("someone+x@mailinator.com")
    assert not is_disposable("someone@gmail.com")


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------

def test_the_same_mailbox_cannot_sign_up_twice(client):
    assert signup(client, "harshpanchal@gmail.com").status_code == 201

    for alias in [
        "harshpanchal@gmail.com",
        "Harsh.Panchal@gmail.com",
        "harshpanchal+free@gmail.com",
        "h.a.r.s.h.panchal@googlemail.com",
        "HARSHPANCHAL@GMAIL.COM",
    ]:
        response = signup(client, alias)
        assert response.status_code == 409, f"{alias} got in"
        assert "already exists" in response.json()["detail"]


def test_a_genuinely_different_address_still_works(client):
    assert signup(client, "one@gmail.com").status_code == 201
    assert signup(client, "two@gmail.com").status_code == 201
    assert signup(client, "one@outlook.com").status_code == 201


def test_the_refusal_does_not_advertise_that_the_alias_was_seen_through(client):
    """
    Same message either way. Telling someone "we normalised your address"
    teaches them what to try next.
    """
    signup(client, "someone@gmail.com")
    exact = signup(client, "someone@gmail.com").json()["detail"]
    alias = signup(client, "some.one+x@gmail.com").json()["detail"]
    assert exact == alias


def test_a_temporary_inbox_cannot_open_an_account(client):
    response = signup(client, "burner@mailinator.com")
    assert response.status_code == 400
    assert "temporary" in response.json()["detail"].lower()


def test_the_database_refuses_a_duplicate_even_without_the_check():
    """
    The check in the endpoint is the polite message. The unique index is the
    enforcement — two requests racing past the check still cannot both land.
    """
    db.create_customer("racer@gmail.com", "First", "hash")
    with pytest.raises(db.DuplicateAccountError):
        db.create_customer("r.a.c.e.r+second@gmail.com", "Second", "hash")


def test_lookup_finds_the_account_from_any_alias():
    db.create_customer("lookup@gmail.com", "Lookup", "hash")

    assert db.get_customer_for_mailbox("l.o.o.k.u.p@gmail.com") is not None
    assert db.get_customer_for_mailbox("lookup+tag@googlemail.com") is not None
    assert db.get_customer_for_mailbox("someoneelse@gmail.com") is None

    # Signing in still matches what was typed, which is what people remember.
    assert db.get_customer_by_email("lookup@gmail.com") is not None


def test_signups_from_one_machine_are_capped(client, monkeypatch):
    """
    Distinct real mailboxes, one person, one afternoon — the half of the
    problem canonical email cannot see.
    """
    monkeypatch.setattr(identity, "MAX_SIGNUPS_PER_IP_PER_DAY", 2)

    assert signup(client, "capped1@gmail.com").status_code == 201
    assert signup(client, "capped2@gmail.com").status_code == 201

    blocked = signup(client, "capped3@gmail.com")
    assert blocked.status_code == 429
    assert "network" in blocked.json()["detail"]


def test_a_failed_signup_does_not_count_against_the_cap(client, monkeypatch):
    """Someone who mistyped their password shouldn't burn an attempt."""
    monkeypatch.setattr(identity, "MAX_SIGNUPS_PER_IP_PER_DAY", 2)

    signup(client, "taken@gmail.com")
    signup(client, "taken@gmail.com")  # 409, not a new account
    signup(client, "burner@mailinator.com")  # 400, not a new account

    assert signup(client, "genuine@gmail.com").status_code == 201


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def test_the_unique_index_is_what_enforces_it():
    """
    On MongoDB there is no backfill to get wrong: the canonical form is written
    when the account is created, and a unique index refuses the second one. The
    SQLite version needed a migration to add that guarantee to rows written
    before it existed; starting clean, there are none.
    """
    from backend.shared.mongo import coll

    db.create_customer("indexed@gmail.com", "First", "hash")

    names = {ix["name"] for ix in coll(db.CUSTOMERS).list_indexes()}
    assert "uniq_canonical_email" in names

    with pytest.raises(db.DuplicateAccountError):
        db.create_customer("in.dexed+tag@googlemail.com", "Second", "hash")
