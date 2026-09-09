"""
Support chat — customers talking to the people running the platform.

No model anywhere in this package. The FAQ bot answers from a sheet, the
dashboard assistant answers from a local model, and this one is answered by a
person. Keeping those three apart is the whole reason it is a separate package
rather than another mode on an existing one.

One conversation per customer. A support desk is not a place people start
threads — it is one continuous relationship with whoever is on the other end,
and staff want the history in one place rather than scattered across five
conversations the customer opened on five different days.
"""
