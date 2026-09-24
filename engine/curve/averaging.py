"""The declining delta of a monthly-average contract (commodity conversion plan, Phase 5).

A contract whose root settles on the average of an index over its contract month
(``config/contracts.csv`` ``settlement = 'average'``, contract-master's ``ContractRoot.averaging``)
fixes one business day's price at a time through its averaging period
(``data.contracts.averaging_period``: the month's first and last business day on the root's
exchange calendar). Its sensitivity to the index is the share of the period still to price:

- before the period: 1 (nothing fixed yet);
- inside it: the business days of the period after ``as_of`` / the period's business days, on the
  root's own calendar (a holiday is not a pricing day and counts in neither);
- on or after its last day: 0 (the price is fully set).

Only the delta shrinks: the lots, units and notional stay the whole position.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

from data.contracts import averaging_period
from engine import calendars


def averaging_factor(root, month: int, year: int, as_of: str) -> Tuple[Optional[float], str, str]:
    """(factor, note, reason) for an averaging contract of ``root`` (a ``ContractRoot``) in
    ``month`` / ``year`` on ``as_of``. ``factor`` is None, with ``reason``, when the period
    cannot be worked out; ``note`` says in words what the factor is."""
    try:
        first, last = averaging_period(root, month, year)
        total = calendars.business_days_between(root.calendar, first, last) + 1
        day = date.fromisoformat(str(as_of)[:10])
    except (ValueError, KeyError, OSError) as exc:
        return None, "", f"averaging period of {root.root_id} {year:04d}-{month:02d} not known: {exc}"
    span = f"{first.isoformat()}..{last.isoformat()}, {total} business days on the {root.calendar} calendar"
    if day < first:
        return 1.0, f"averaging contract: its averaging period ({span}) has not started, full delta", ""
    if day >= last:
        return 0.0, f"averaging contract: its averaging period ({span}) is over, the price is set, no delta", ""
    remaining = calendars.business_days_between(root.calendar, day, last)
    factor = remaining / total
    return factor, (f"averaging contract: {remaining} of the {total} business days of its averaging "
                    f"period ({span}) left after {as_of}, delta {factor:.1%} of the position"), ""
