"""
The bridge between *paying* and *being able to use the thing*.

Two stores have to agree: ``backend.billing`` knows whether a subscription is
live, and ``backend.shared.api_keys`` knows what an account's daily credit allowance
is. Nothing keeps them in sync automatically, so every transition between them
goes through this module — one place to read when asking "why does this
account have these credits?".

It exists because the first version only wired the grant direction. Paying
raised your allowance to the paid tier; lapsing did nothing at all, so a
cancelled customer kept the paid allowance and a working key indefinitely.
Granting without a matching revoke is not half a feature, it is a hole.
"""

from __future__ import annotations

import logging

from backend.shared.api_keys import set_daily_credit_limit

from backend.billing import db

logger = logging.getLogger(__name__)


def grant(email: str, name: str, daily_credits: int) -> None:
    """Raise an account's allowance to what its plan pays for."""
    set_daily_credit_limit(email, daily_credits, name=name)


def revoke(email: str, name: str = "") -> None:
    """
    Drop an account back to the free default allowance.

    Deliberately *not* zero and deliberately not "deactivate their keys".
    Someone whose card expired should find their bot throttled, not silently
    broken in production with an integration that needs rebuilding when they
    come back. They land on the free tier, which is where they started.
    """
    set_daily_credit_limit(email, None, name=name)


def sync() -> int:
    """
    Expire lapsed subscriptions and withdraw what they were paying for.

    Idempotent and cheap when nothing has lapsed (one indexed SELECT that
    returns no rows). Returns how many accounts were withdrawn.
    """
    lapsed = db.expire_lapsed_subscriptions()

    for customer in lapsed:
        revoke(customer["email"], customer["name"])
        logger.info(
            "Subscription lapsed for customer_id=%s — allowance reset to the "
            "free default.",
            customer["id"],
        )

    return len(lapsed)
