"""The alert thresholds of the roll calendar: the one place to change them.

A level is read from the business days left to a contract's next event, counted on the
contract's own exchange calendar (``engine.calendars.business_days_between``):

- ``EXPIRED``: the event date is already past and the position is still open;
- ``RED``: the event is at most ``RED_MAX_BUSINESS_DAYS`` business days away (today counts as 0);
- ``AMBER``: at most ``AMBER_MAX_BUSINESS_DAYS`` business days away;
- ``GREEN``: further out.

The exchange rules the alerts follow sit here too, so the user changes them in the same place:
``NO_FIRST_NOTICE_EXCHANGES`` (exchanges with no first notice day, keyed on the last trading
day whatever date Bloomberg gives) and ``DELIVERY_LOT_MULTIPLES`` (a position held into the
delivery month must be a whole multiple of this many lots).
"""
from __future__ import annotations

from typing import Optional

RED_MAX_BUSINESS_DAYS = 3
AMBER_MAX_BUSINESS_DAYS = 10

# Exchanges with no first notice day (user, 2026-10-02): the Chinese exchanges and Japan's OSE
# deliver after the last trading day, with no notice period before it. A physical contract of
# theirs is keyed on its last trading day, never first notice, even when Bloomberg stores a
# first notice date for it (``config/contracts.csv`` column ``exchange``).
NO_FIRST_NOTICE_EXCHANGES = frozenset({"SHFE", "INE", "DCE", "ZCE", "GFEX", "OSE"})

# Delivery-month lot multiples (user, 2026-10-02): on SHFE copper, aluminium, zinc and lead a
# position held into the delivery month must be a whole multiple of the delivery unit (25 t,
# 5 lots) by the close of the last trading day of the month before. Root id -> lots.
DELIVERY_LOT_MULTIPLES = {"SHFE:CU": 5, "SHFE:AL": 5, "SHFE:ZN": 5, "SHFE:PB": 5}

EXPIRED = "EXPIRED"
RED = "RED"
AMBER = "AMBER"
GREEN = "GREEN"

# Worst first: the order rows are sorted in and ``counts`` is listed in.
LEVELS = (EXPIRED, RED, AMBER, GREEN)


def thresholds() -> dict:
    """The thresholds in force, for a screen to show: ``{'RED': 3, 'AMBER': 10}``."""
    return {RED: RED_MAX_BUSINESS_DAYS, AMBER: AMBER_MAX_BUSINESS_DAYS}


def level_for(business_days: Optional[int], event_past: bool) -> str:
    """The alert level for a count of business days to the event.

    ``event_past`` wins over the count (a past event is ``EXPIRED`` whatever the count says).
    A count that could not be made (``None``) is ``RED``: an unknown distance to delivery is
    treated as close, never as safe.
    """
    if event_past:
        return EXPIRED
    if business_days is None or business_days <= RED_MAX_BUSINESS_DAYS:
        return RED
    if business_days <= AMBER_MAX_BUSINESS_DAYS:
        return AMBER
    return GREEN
