"""The roll calendar (expiry-monitor lane, layer 5): the next event of every open commodity
futures position, the business days left to it on the contract's own exchange calendar and
an alert level, so the screens warn before a position is held into delivery.

- ``expiry_schedule(conn, as_of)``: the rows, counts per level and thresholds (``schedule.py``).
- ``levels``: the alert thresholds, in one place (``RED_MAX_BUSINESS_DAYS``,
  ``AMBER_MAX_BUSINESS_DAYS``).

Reads contract-master (dates), exchange-calendars (business days) and the trades on file.
Writes nothing.
"""
from engine.expiry import levels
from engine.expiry.levels import (
    AMBER,
    AMBER_MAX_BUSINESS_DAYS,
    EXPIRED,
    GREEN,
    LEVELS,
    RED,
    RED_MAX_BUSINESS_DAYS,
)
from engine.expiry.schedule import FIRST_NOTICE, LAST_TRADE, expiry_schedule

__all__ = [
    "AMBER", "AMBER_MAX_BUSINESS_DAYS", "EXPIRED", "FIRST_NOTICE", "GREEN", "LAST_TRADE", "LEVELS",
    "RED", "RED_MAX_BUSINESS_DAYS", "expiry_schedule", "levels",
]
