"""
The dashboard assistant — general-purpose chat for signed-in customers.

Not the FAQ bot, and deliberately nothing like it. A customer's bot answers
strangers on their behalf, so it may only repeat what its owner wrote. This
answers the owner themselves, inside their own dashboard, and can talk about
anything.

It is reachable only with a session cookie. There is no API-key route to it
and there will not be one: what customers integrate is a bot that answers from
their sheet, and that contract does not change because we added a chat window.
"""
