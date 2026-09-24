"""The alert thresholds of the roll calendar: the one place to change them.

A level is read from the business days left to a contract's next event, counted on the
contract's own exchange calendar (``engine.calendars.business_days_between``):

- ``EXPIRED``: the event date is already past and the position is still open;
- ``RED``: the event is at most ``RED_MAX_BUSINESS_DAYS`` business days away (today counts as 0);
- ``AMBER``: at most ``AMBER_MAX_BUSINESS_DAYS`` business days away;
- ``GREEN``: further out.
"""
from __future__ import annotations

from typing import Optional

RED_MAX_BUSINESS_DAYS = 3
AMBER_MAX_BUSINESS_DAYS = 10

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
