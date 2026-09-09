"""
Tests for the support chat.

The property under test is the split. Customers reach exactly one conversation
— their own — and reach it without naming it, because the customer routes take
no conversation id at all. Staff reach every conversation, behind a different
header on a different path prefix.

Anything that erodes that split should fail here.
"""

from __future__ import annotations

import pytest

from backend.shared.config import ADMIN_API_KEY
from tests.conftest import fake_id

from backend.support import store


def _customer(cid: int, email: str = "c@example.test", name: str = "C") -> dict:
    """A customer as the routes see one. Ids are ObjectId strings now."""
    return {"id": fake_id(cid), "email": email, "name": name}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

class TestConversations:
    def test_a_customer_gets_one_conversation_reused(self):
        """
        A support desk is one relationship, not a series of tickets.

        Calling twice must not leave the customer with two conversations and
        staff wondering which one to answer.
        """
        first = store.get_or_create_conversation(_customer(1))
        second = store.get_or_create_conversation(_customer(1))
        assert first["id"] == second["id"]
        assert len(store.list_conversations()) == 1

    def test_identity_is_copied_onto_the_row(self):
        """Staff need to see who they're talking to without a join."""
        conversation = store.get_or_create_conversation(
            _customer(2, "harsh@example.test", "Harsh")
        )
        assert conversation["customer_email"] == "harsh@example.test"
        assert conversation["customer_name"] == "Harsh"

    def test_the_inbox_is_ordered_by_recent_activity(self):
        a = store.get_or_create_conversation(_customer(10, "a@x.test", "A"))
        b = store.get_or_create_conversation(_customer(11, "b@x.test", "B"))
        store.add_message(a["id"], store.SENDER_CUSTOMER, "first")
        store.add_message(b["id"], store.SENDER_CUSTOMER, "second, so newer")

        inbox = store.list_conversations()
        assert inbox[0]["id"] == b["id"]

    def test_the_preview_follows_the_latest_message(self):
        conversation = store.get_or_create_conversation(_customer(12))
        store.add_message(conversation["id"], store.SENDER_CUSTOMER, "older")
        store.add_message(conversation["id"], store.SENDER_STAFF, "newest line")
        assert store.list_conversations()[0]["last_message_preview"] == "newest line"


# ---------------------------------------------------------------------------
# Unread accounting
# ---------------------------------------------------------------------------

class TestUnread:
    def test_each_side_has_its_own_marker(self):
        """
        One shared ``last_read_at`` would have the two sides overwriting each
        other's badge — staff opening a thread would clear the customer's.
        """
        conversation = store.get_or_create_conversation(_customer(20))
        store.add_message(conversation["id"], store.SENDER_CUSTOMER, "hello?")

        assert store.unread_count(conversation["id"], store.SENDER_STAFF) == 1
        assert store.unread_count(conversation["id"], store.SENDER_CUSTOMER) == 0

        store.mark_read(conversation["id"], store.SENDER_STAFF)
        assert store.unread_count(conversation["id"], store.SENDER_STAFF) == 0

    def test_your_own_message_is_never_unread_to_you(self):
        """Sending marks your own side read; otherwise you light up your own badge."""
        conversation = store.get_or_create_conversation(_customer(21))
        store.add_message(conversation["id"], store.SENDER_STAFF, "any update?")
        assert store.unread_count(conversation["id"], store.SENDER_STAFF) == 0
        assert store.unread_count(conversation["id"], store.SENDER_CUSTOMER) == 1

    def test_total_unread_counts_across_customers(self):
        for cid in (30, 31, 32):
            conversation = store.get_or_create_conversation(_customer(cid, f"{cid}@x.test"))
            store.add_message(conversation["id"], store.SENDER_CUSTOMER, "hi")
        assert store.total_unread_for_staff() == 3

    def test_unread_survives_a_same_tick_read(self):
        """
        Read position is a message id, not a timestamp.

        Windows' clock returns the same value for consecutive calls, so with
        timestamp markers a message written in the same tick as a read would
        compare as already-read and never show up as unread. Ids can't tie.
        """
        conversation = store.get_or_create_conversation(_customer(41))
        store.mark_read(conversation["id"], store.SENDER_STAFF)
        # No sleep — deliberately in the same clock tick as the read above.
        store.add_message(conversation["id"], store.SENDER_CUSTOMER, "urgent")
        assert store.unread_count(conversation["id"], store.SENDER_STAFF) == 1

    def test_inbox_order_is_stable_when_timestamps_tie(self):
        """Coarse clocks make ties common, so id has to break them."""
        a = store.get_or_create_conversation(_customer(42, "a2@x.test"))
        b = store.get_or_create_conversation(_customer(43, "b2@x.test"))
        store.add_message(a["id"], store.SENDER_CUSTOMER, "first")
        store.add_message(b["id"], store.SENDER_CUSTOMER, "second")
        assert store.list_conversations()[0]["id"] == b["id"]

    def test_after_id_returns_only_what_is_new(self):
        """This is what makes polling cheap rather than a full re-download."""
        conversation = store.get_or_create_conversation(_customer(40))
        first = store.add_message(conversation["id"], store.SENDER_CUSTOMER, "one")
        store.add_message(conversation["id"], store.SENDER_STAFF, "two")

        fresh = store.list_messages(conversation["id"], after_id=first["id"])
        assert [m["body"] for m in fresh] == ["two"]


# ---------------------------------------------------------------------------
# The customer side
# ---------------------------------------------------------------------------

class TestCustomerRoutes:
    def test_customer_routes_take_no_conversation_id(self, api_client):
        """
        The strongest guarantee in this feature.

        There is no id to guess, no ownership check to forget, and no way to
        ask for someone else's conversation, because the parameter does not
        exist on any customer route.
        """
        paths = api_client.get("/openapi.json").json()["paths"]
        customer_paths = [
            p for p in paths
            if p.startswith("/api/support") and not p.startswith("/api/support/admin")
        ]
        assert customer_paths
        assert not any("{" in p for p in customer_paths)

    def test_a_signed_out_visitor_gets_nothing(self, api_client):
        assert api_client.get("/api/support/messages").status_code == 401
        assert api_client.post(
            "/api/support/messages", json={"body": "hello"}
        ).status_code == 401

    def test_an_api_key_is_not_a_way_in(self, api_client):
        """Support chat is a website feature, like the assistant."""
        from backend.shared.api_keys import generate_api_key

        key = generate_api_key("supportkey@example.test", "K")["api_key"]
        response = api_client.get("/api/support/messages", headers={"X-Api-Key": key})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# The staff side
# ---------------------------------------------------------------------------

class TestStaffRoutes:
    def test_the_inbox_needs_the_admin_key(self, api_client):
        assert api_client.get("/api/support/admin/conversations").status_code == 422
        assert api_client.get(
            "/api/support/admin/conversations", headers={"X-Admin-Key": "wrong"}
        ).status_code == 403

    def test_a_customer_key_does_not_open_the_inbox(self, api_client):
        """The admin key is a different secret, not a stronger API key."""
        from backend.shared.api_keys import generate_api_key

        key = generate_api_key("nosy@example.test", "Nosy")["api_key"]
        response = api_client.get(
            "/api/support/admin/conversations", headers={"X-Admin-Key": key}
        )
        assert response.status_code == 403

    def test_staff_can_read_and_reply(self, api_client):
        conversation = store.get_or_create_conversation(_customer(50, "real@x.test", "Real"))
        store.add_message(conversation["id"], store.SENDER_CUSTOMER, "my sheet won't upload")

        headers = {"X-Admin-Key": ADMIN_API_KEY}

        inbox = api_client.get("/api/support/admin/conversations", headers=headers)
        assert inbox.status_code == 200
        assert inbox.json()["total_unread"] >= 1

        detail = api_client.get(
            f"/api/support/admin/conversations/{conversation['id']}", headers=headers
        )
        assert detail.status_code == 200
        assert detail.json()["messages"][0]["body"] == "my sheet won't upload"

        reply = api_client.post(
            f"/api/support/admin/conversations/{conversation['id']}/messages",
            headers=headers,
            json={"body": "Send me the file and I'll look."},
        )
        assert reply.status_code == 201
        assert reply.json()["sender"] == "staff"

    def test_replying_to_a_conversation_that_does_not_exist_is_a_404(self, api_client):
        response = api_client.post(
            "/api/support/admin/conversations/99999/messages",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"body": "hello?"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Message content
# ---------------------------------------------------------------------------

class TestMessageContent:
    def test_code_is_allowed(self, api_client):
        """
        A support channel that refuses stack traces defeats its own purpose.

        This is where people paste the error they're stuck on.
        """
        conversation = store.get_or_create_conversation(_customer(60, "dev@x.test", "Dev"))
        response = api_client.post(
            f"/api/support/admin/conversations/{conversation['id']}/messages",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"body": "Run: SELECT * FROM bots WHERE user_id = 4;"},
        )
        assert response.status_code == 201

    def test_credentials_are_refused(self, api_client):
        """
        They would sit in this table forever, readable by staff.

        Refusing is kinder than storing it and asking them to rotate later.
        """
        conversation = store.get_or_create_conversation(_customer(61, "oops@x.test", "Oops"))
        response = api_client.post(
            f"/api/support/admin/conversations/{conversation['id']}/messages",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"body": "your card 4111 1111 1111 1111 was declined"},
        )
        assert response.status_code == 400
        assert "card numbers" in response.json()["detail"]

    def test_an_empty_message_is_refused(self, api_client):
        conversation = store.get_or_create_conversation(_customer(62))
        response = api_client.post(
            f"/api/support/admin/conversations/{conversation['id']}/messages",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"body": "    "},
        )
        assert response.status_code == 400
