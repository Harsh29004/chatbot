"""
One person, one account — as far as an email address can prove it.

The ``customers.email`` column has always been UNIQUE, which sounds like it
settles the matter and does not. Uniqueness on the literal string means
``harsh.panchal@gmail.com``, ``harshpanchal@gmail.com`` and
``harshpanchal+1@gmail.com`` are three different accounts, and all three
deliver to the same inbox. With a trial and a referral bonus attached to every
signup, that is not a curiosity, it is a way to mint credits.

So this module answers a narrower question than "are these strings equal?":
**do these two addresses reach the same mailbox?** The answer is the
*canonical* form, and that is what uniqueness is enforced on.

Three rules, each earning its place:

* **Case and whitespace** — addresses are compared lowercased and trimmed.
* **Subaddressing** — everything from ``+`` to the ``@`` is a tag the provider
  ignores when delivering (RFC 5233). ``you+anything@x`` is ``you@x``.
* **Gmail dots** — Google ignores dots in the local part entirely, so
  ``f.i.r.s.t@gmail.com`` is ``first@gmail.com``. This is Gmail-specific and
  deliberately *not* applied elsewhere: on most providers a dot is a real
  character and collapsing it would merge two strangers into one account.

What this cannot do
-------------------
Someone with genuinely separate mailboxes — a Gmail, an Outlook, a work
address — can still open one account each. No email rule detects that, and
pretending otherwise would be worse than saying it plainly: stopping it needs
identity that costs something to obtain (a card, a phone number, a domain).
What this *does* stop is the cheap version, which is the one that actually
happens: one inbox, unlimited aliases, unlimited free credits.
"""

from __future__ import annotations

import os
import re

# Providers that deliver ``local+tag@domain`` to ``local@domain``. Kept as an
# explicit set rather than applied blindly, because a handful of small hosts
# treat "+" as an ordinary character and collapsing those would lock a real
# person out of their own address.
_PLUS_ADDRESSING_DOMAINS = {
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "pm.me",
    "fastmail.com", "zoho.com", "hey.com", "gmx.com", "mail.com",
    "yandex.com", "tutanota.com", "aol.com",
}

# Domains that are the same mailbox under a different name.
_DOMAIN_ALIASES = {
    "googlemail.com": "gmail.com",
    "ymail.com": "yahoo.com",
    "hotmail.com": "hotmail.com",  # distinct from outlook.com; kept explicit
}

# Only Google ignores dots. Everywhere else they are significant.
_DOT_INSENSITIVE_DOMAINS = {"gmail.com"}

# Throwaway inbox providers. Not exhaustive — no such list is — but it covers
# the services someone reaches for first when they want twenty addresses in a
# minute. Extend with DISPOSABLE_EMAIL_DOMAINS in .env rather than editing here.
_DISPOSABLE_DOMAINS = {
    "10minutemail.com", "20minutemail.com", "guerrillamail.com", "guerrillamail.net",
    "mailinator.com", "maildrop.cc", "yopmail.com", "yopmail.net", "temp-mail.org",
    "tempmail.com", "tempmailo.com", "throwawaymail.com", "trashmail.com",
    "sharklasers.com", "getnada.com", "nada.email", "dispostable.com",
    "fakeinbox.com", "mailnesia.com", "mytemp.email", "spamgourmet.com",
    "emailondeck.com", "moakt.com", "tempr.email", "discard.email",
    "mailcatch.com", "inboxbear.com", "burnermail.io", "grr.la", "spam4.me",
    "mohmal.com", "linshiyouxiang.net", "1secmail.com", "tmpmail.org",
    "minuteinbox.com", "emailfake.com", "luxusmail.org", "vomoto.com",
}

_EXTRA_DISPOSABLE = {
    domain.strip().lower()
    for domain in os.getenv("DISPOSABLE_EMAIL_DOMAINS", "").split(",")
    if domain.strip()
}

BLOCK_DISPOSABLE = os.getenv("BLOCK_DISPOSABLE_EMAILS", "true").lower() == "true"

# Signups allowed from one IP per day. Bulk account creation is the other half
# of the problem: distinct real mailboxes, one person, one afternoon.
MAX_SIGNUPS_PER_IP_PER_DAY = int(os.getenv("MAX_SIGNUPS_PER_IP_PER_DAY", "3"))

_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailPolicyError(ValueError):
    """The address may not open an account, with a reason worth showing."""


def canonical_email(email: str) -> str:
    """
    The mailbox an address actually reaches, as a comparable string.

    Used for *comparison only*. The address the customer typed is what gets
    stored and displayed — nobody wants to be told their email is
    ``firstlast@gmail.com`` when they wrote ``First.Last@gmail.com``.
    """
    address = (email or "").strip().lower()
    if "@" not in address:
        return address

    local, _, domain = address.rpartition("@")
    domain = _DOMAIN_ALIASES.get(domain, domain)

    if domain in _PLUS_ADDRESSING_DOMAINS:
        local = local.split("+", 1)[0]

    if domain in _DOT_INSENSITIVE_DOMAINS:
        local = local.replace(".", "")

    # A local part that was nothing but a tag ("+tag@gmail.com") is not a real
    # address; leave it intact rather than producing "@gmail.com", which would
    # collide with every other malformed one.
    return f"{local}@{domain}" if local else address


def is_disposable(email: str) -> bool:
    """True when the address belongs to a known throwaway-inbox provider."""
    domain = canonical_email(email).rpartition("@")[2]
    return domain in _DISPOSABLE_DOMAINS or domain in _EXTRA_DISPOSABLE


def screen_signup_email(email: str) -> str:
    """
    Check an address is fit to open an account, and return its canonical form.

    Raises :class:`EmailPolicyError` with a message written for the person
    typing it, never a rule name.
    """
    address = (email or "").strip().lower()
    if not _EMAIL_SHAPE.match(address):
        raise EmailPolicyError("That doesn't look like an email address.")

    if BLOCK_DISPOSABLE and is_disposable(address):
        raise EmailPolicyError(
            "Temporary email addresses can't be used to sign up. "
            "Please use an address you'll still have tomorrow."
        )

    return canonical_email(address)
