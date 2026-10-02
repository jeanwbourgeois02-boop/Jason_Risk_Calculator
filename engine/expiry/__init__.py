"""The roll calendar (expiry-monitor lane, layer 5): the next event of every open commodity
position (futures: first notice or last trade; options on futures: ``OPTION_EXPIRY``; LME
forwards: ``LME_PROMPT``, alerted from the day the prompt becomes the cash date), the business
days left to it on the contract's own exchange calendar and an alert level, so the screens warn
before a position is held into delivery.

- ``expiry_schedule(conn, as_of)``: the rows, counts per level, thresholds and the expired
  contracts the ledger has settled (``settled_expired``, no alert) (``schedule.py``); one row
  builder per product in ``schedule._BUILDERS``.
- ``levels``: the alert thresholds and exchange rules, in one place (``RED_MAX_BUSINESS_DAYS``,
  ``AMBER_MAX_BUSINESS_DAYS``, ``NO_FIRST_NOTICE_EXCHANGES``, ``DELIVERY_LOT_MULTIPLES``).

Reads contract-master (dates), exchange-calendars (business days), the trades on file and the
ledger's ``realised_pnl`` rows.
Writes nothing.
"""
from engine.expiry import levels
from engine.expiry.levels import (
    AMBER,
    AMBER_MAX_BUSINESS_DAYS,
    DELIVERY_LOT_MULTIPLES,
    EXPIRED,
    GREEN,
    LEVELS,
    NO_FIRST_NOTICE_EXCHANGES,
    RED,
    RED_MAX_BUSINESS_DAYS,
)
from engine.expiry.schedule import (
    EVENT_LABELS,
    FIRST_NOTICE,
    LAST_TRADE,
    LME_CASH_DAYS,
    LME_PROMPT,
    LOT_MULTIPLE,
    OPTION_EXPIRY,
    expiry_schedule,
    has_first_notice,
)

__all__ = [
    "AMBER", "AMBER_MAX_BUSINESS_DAYS", "DELIVERY_LOT_MULTIPLES", "EVENT_LABELS", "EXPIRED", "FIRST_NOTICE",
    "GREEN", "LAST_TRADE", "LEVELS", "LME_CASH_DAYS", "LME_PROMPT", "LOT_MULTIPLE", "NO_FIRST_NOTICE_EXCHANGES",
    "OPTION_EXPIRY", "RED", "RED_MAX_BUSINESS_DAYS", "expiry_schedule", "has_first_notice", "levels",
]
