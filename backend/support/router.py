"""
Support chat endpoints.

Two audiences, two auth schemes, deliberately separated in the path as well as
the dependency:

* ``/api/support/...`` — the customer's own conversation, session cookie. A
  customer can only ever reach their own; no route here takes a conversation id.
* ``/api/support/admin/...`` — the staff inbox, ``X-Admin-Key``. Cross-customer
  by definition, which is exactly why it is a separate prefix behind a separate
  header.

That the customer routes take no conversation id is the point. There is no id
to guess, no ownership check to forget, and no way to ask for someone else's
conversation because the parameter does not exist.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.billing.router import current_customer
from backend.shared import input_policy
from backend.shared.auth import verify_admin_key
from backend.support import store

router = APIRouter(tags=["Support"])

MAX_BODY_CHARS = 4000


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SupportMessage(BaseModel):
    id: str
    sender: str
    body: str
    created_at: str


class SendMessageRequest(BaseModel):
    body: str = Field(..., min_length=1)


class CustomerConversation(BaseModel):
    messages: list[SupportMessage]
    unread: int


class ConversationSummary(BaseModel):
    id: str
    customer_id: str
    customer_email: str
    customer_name: str
    last_message_at: str
    last_message_preview: str
    unread: int


class InboxResponse(BaseModel):
    conversations: list[ConversationSummary]
    total_unread: int


class StaffConversation(BaseModel):
    conversation: ConversationSummary
    messages: list[SupportMessage]


def _screen(body: str) -> str:
    """
    Shared checks for both sides.

    Only credentials are refused. Support chat is where people paste error
    messages, config snippets and stack traces — refusing code here would break
    the one thing a support channel exists for. A password or a card number is
    different: it would sit in this table forever, readable by staff, long
    after the conversation stopped mattering.
    """
    body = body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Write something first.")
    if len(body) > MAX_BODY_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"That message is {len(body)} characters; the limit is {MAX_BODY_CHARS}.",
        )
    verdict = input_policy.screen(body, for_model=False)
    if verdict.refused:
        raise HTTPException(status_code=400, detail=verdict.message)
    return body


# ---------------------------------------------------------------------------
# Customer side — session cookie, own conversation only
# ---------------------------------------------------------------------------

@router.get("/api/support/messages", response_model=CustomerConversation)
async def my_messages(
    after_id: str = "",
    customer: dict = Depends(current_customer),
) -> CustomerConversation:
    """
    This customer's conversation with support.

    ``after_id`` returns only newer messages, which is what makes the page
    cheap to poll — it asks for what it hasn't seen rather than re-downloading
    the history every few seconds.
    """
    conversation = store.get_or_create_conversation(customer)
    return CustomerConversation(
        messages=[
            SupportMessage(**message)
            for message in store.list_messages(conversation["id"], after_id)
        ],
        unread=store.unread_count(conversation["id"], store.SENDER_CUSTOMER),
    )


@router.post("/api/support/messages", response_model=SupportMessage, status_code=201)
async def send_to_support(
    body: SendMessageRequest,
    customer: dict = Depends(current_customer),
) -> SupportMessage:
    """Send a message to the team."""
    text = _screen(body.body)
    conversation = store.get_or_create_conversation(customer)
    return SupportMessage(
        **store.add_message(conversation["id"], store.SENDER_CUSTOMER, text)
    )


@router.post("/api/support/read")
async def mark_my_conversation_read(
    customer: dict = Depends(current_customer),
) -> dict[str, Any]:
    """Clear this customer's unread badge once they've looked at the thread."""
    conversation = store.get_or_create_conversation(customer)
    store.mark_read(conversation["id"], store.SENDER_CUSTOMER)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Staff side — X-Admin-Key, every conversation
# ---------------------------------------------------------------------------

@router.get(
    "/api/support/admin/conversations",
    response_model=InboxResponse,
    dependencies=[Depends(verify_admin_key)],
)
async def inbox() -> InboxResponse:
    """Every conversation, most recently active first."""
    conversations = store.list_conversations()
    return InboxResponse(
        conversations=[ConversationSummary(**row) for row in conversations],
        total_unread=store.total_unread_for_staff(),
    )


@router.get(
    "/api/support/admin/conversations/{conversation_id}",
    response_model=StaffConversation,
    dependencies=[Depends(verify_admin_key)],
)
async def read_conversation(conversation_id: str, after_id: str = "") -> StaffConversation:
    conversation = _conversation_or_404(conversation_id)
    return StaffConversation(
        conversation=ConversationSummary(
            **conversation,
            unread=store.unread_count(conversation_id, store.SENDER_STAFF),
        ),
        messages=[
            SupportMessage(**message)
            for message in store.list_messages(conversation_id, after_id)
        ],
    )


@router.post(
    "/api/support/admin/conversations/{conversation_id}/messages",
    response_model=SupportMessage,
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
async def reply(conversation_id: str, body: SendMessageRequest) -> SupportMessage:
    """Reply to a customer."""
    _conversation_or_404(conversation_id)
    text = _screen(body.body)
    return SupportMessage(
        **store.add_message(conversation_id, store.SENDER_STAFF, text)
    )


@router.post(
    "/api/support/admin/conversations/{conversation_id}/read",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
async def mark_conversation_read(conversation_id: str) -> dict[str, Any]:
    _conversation_or_404(conversation_id)
    store.mark_read(conversation_id, store.SENDER_STAFF)
    return {"ok": True, "total_unread": store.total_unread_for_staff()}


def _conversation_or_404(conversation_id: str) -> dict[str, Any]:
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such conversation.")
    # ``unread`` is computed per caller, so it must not arrive pre-set from the
    # row and collide with the keyword the response model is built with.
    conversation.pop("unread", None)
    return conversation
