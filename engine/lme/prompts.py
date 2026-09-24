"""LME prompt dates on the LME's own calendar (lme-forwards lane, commodity conversion plan
Phase 5).

LME base metals trade as forwards to a prompt date, not as monthly futures. The rules below
are the LME's published prompt-date structure (LME Rulebook, Part 3 "Trading", and the LME's
"Prompt date structure" guide), written from memory on 2026-09-24 and to be checked against
that publication before they are relied on; every detail that is not certain says
``# unverified`` where it is applied.

- **Business day**: a day the LME is open, `engine.calendars` id ``'LME'``
  (`config/calendars/LME.txt`, England and Wales bank holidays). Every prompt date is one.
  Whether a New York holiday also stops a prompt date (USD settlement) is not applied here.
  # unverified: the LME publishes its own list of non-prompt days; this module knows only
  # the LME calendar file.
- **Cash** (the ``cash_date``): the second LME business day after the trade date (T+2). A day
  that is not a business day on the way does not count.
- **3 months** (``three_month_date``): the same day of the month three calendar months after
  the trade date (not after the cash date). When that day is not a business day, the next
  business day, unless that falls in the following month, in which case the previous business
  day (the LME's rule, the "modified following" of the money markets). A day that does not
  exist in the target month (30 November -> 30 February) is taken as that month's last
  calendar day first.  # unverified: the month-end clamp
- **Daily prompts**: every business day from the cash date to the 3-month date, both included.
- **Weekly prompts**: every Wednesday after the 3-month date up to and including the 6-month
  date (``six_month_date``, the 3-month rule with six months). A Wednesday that is not a
  business day moves to the next business day.  # unverified: the holiday roll of a weekly
- **Monthly prompts**: the third Wednesday of every month beyond the 6-month date (out to 123
  months for copper and aluminium, fewer for the others: not enforced here). A third Wednesday
  that is not a business day moves to the next business day.  # unverified: the holiday roll
- **Before cash**: Tom (T+1) and today are prompts the LME allows for carries and same-day
  settlement, but a new outright ticket is dealt from cash onwards, so ``is_valid_prompt``
  answers False for them.  # unverified: whether a blotter ever carries a Tom prompt

The calendar file covers 2026-2027 (`engine.calendars.coverage('LME')`); beyond it only
weekends are known to be closed, so a prompt computed there can sit on a bank holiday.

Nothing here reads the database or Bloomberg.
"""
from __future__ import annotations

import calendar as _pycal
import datetime as dt
from dataclasses import dataclass
from typing import Union

from engine import calendars

__all__ = [
    "LME_CALENDAR",
    "MONTHLY_PILLARS",
    "Pillar",
    "cash_date",
    "three_month_date",
    "six_month_date",
    "third_wednesday",
    "monthly_prompt",
    "is_valid_prompt",
    "prompt_zone",
    "prompt_structure",
]

LME_CALENDAR = "LME"
MONTHLY_PILLARS = 27  # third Wednesdays in the standard curve (brief of 2026-09-24)
_WEDNESDAY = 2

DateLike = Union[dt.date, str]


def _d(day: DateLike) -> dt.date:
    if isinstance(day, dt.datetime):
        return day.date()
    if isinstance(day, dt.date):
        return day
    if isinstance(day, str):
        try:
            return dt.date.fromisoformat(day.strip()[:10])
        except ValueError:
            raise ValueError(f"not an ISO date (YYYY-MM-DD): {day!r}") from None
    raise ValueError(f"not a date or ISO date string: {day!r}")


def _is_bd(day: dt.date) -> bool:
    return calendars.is_business_day(LME_CALENDAR, day)


def _on_or_after(day: dt.date) -> dt.date:
    """`day` if it is an LME business day, else the next one."""
    return day if _is_bd(day) else calendars.next_business_day(LME_CALENDAR, day)


def _add_months(day: dt.date, months: int) -> dt.date:
    """Same day of the month `months` on; the month's last day when that day does not exist
    (# unverified: the LME's month-end clamp)."""
    m0 = day.month - 1 + months
    year, month = day.year + m0 // 12, m0 % 12 + 1
    last = _pycal.monthrange(year, month)[1]
    return dt.date(year, month, min(day.day, last))


def _modified_following(day: dt.date) -> dt.date:
    """The LME's roll of the 3-month date: next business day, unless that is in the next
    month, then the previous business day."""
    if _is_bd(day):
        return day
    nxt = calendars.next_business_day(LME_CALENDAR, day)
    if nxt.month == day.month:
        return nxt
    return calendars.previous_business_day(LME_CALENDAR, day)


def cash_date(trade_date: DateLike) -> dt.date:
    """The cash prompt of a trade dealt on `trade_date`: T+2 LME business days."""
    return calendars.add_business_days(LME_CALENDAR, _d(trade_date), 2)


def three_month_date(trade_date: DateLike) -> dt.date:
    """The 3-month prompt: `trade_date` + 3 calendar months, rolled modified following on
    the LME calendar."""
    return _modified_following(_add_months(_d(trade_date), 3))


def six_month_date(trade_date: DateLike) -> dt.date:
    """The end of the weekly zone: `trade_date` + 6 calendar months, rolled like the 3-month
    date.  # unverified: the LME states the weekly zone as "3 to 6 months"; the roll of its
    end is taken from the 3-month rule."""
    return _modified_following(_add_months(_d(trade_date), 6))


def third_wednesday(year: int, month: int) -> dt.date:
    """The third Wednesday of `year`-`month` (calendar date, not rolled)."""
    first = dt.date(int(year), int(month), 1)
    return first + dt.timedelta(days=(_WEDNESDAY - first.weekday()) % 7 + 14)


def monthly_prompt(year: int, month: int) -> dt.date:
    """The monthly prompt of `year`-`month`: its third Wednesday, or the next LME business
    day when that is a holiday (# unverified: the holiday roll)."""
    return _on_or_after(third_wednesday(year, month))


def prompt_zone(day: DateLike, trade_date: DateLike) -> str:
    """Which zone of the prompt structure `day` sits in, seen from `trade_date`:
    'BEFORE_CASH', 'DAILY' (cash to 3M), 'WEEKLY' (after 3M to 6M) or 'MONTHLY' (after 6M).
    Says nothing of whether `day` is itself a prompt (see ``is_valid_prompt``)."""
    d, t = _d(day), _d(trade_date)
    if d < cash_date(t):
        return "BEFORE_CASH"
    if d <= three_month_date(t):
        return "DAILY"
    if d <= six_month_date(t):
        return "WEEKLY"
    return "MONTHLY"


def is_valid_prompt(day: DateLike, trade_date: DateLike) -> bool:
    """True when `day` is a prompt date an outright dealt on `trade_date` can have: any LME
    business day from cash to 3M; in the weekly zone a Wednesday (rolled off a holiday);
    beyond it the month's third Wednesday (rolled off a holiday). The 3-month date itself is
    always a prompt. No upper limit on the term is applied."""
    d, t = _d(day), _d(trade_date)
    if not _is_bd(d):
        return False
    zone = prompt_zone(d, t)
    if zone == "BEFORE_CASH":
        return False
    if zone == "DAILY":
        return True
    if d == monthly_prompt(d.year, d.month):
        return True  # a third Wednesday is a prompt in the weekly zone and beyond
    if zone == "MONTHLY":
        return False
    # WEEKLY: `d` is the roll of the Wednesday on or before it (a roll moves a few days
    # at most, so the Wednesday of this week or of the one before)
    back = (d.weekday() - _WEDNESDAY) % 7
    for wed in (d - dt.timedelta(days=back), d - dt.timedelta(days=back + 7)):
        if wed > three_month_date(t) and _on_or_after(wed) == d:
            return True
    return False


@dataclass(frozen=True)
class Pillar:
    """One standard point of an LME curve: kind 'CASH', '3M' or 'MONTHLY', its prompt date,
    and a label ('CASH', '3M', or 'YYYY-MM' for a monthly)."""

    kind: str
    date: dt.date
    label: str


def prompt_structure(as_of: DateLike, months: int = MONTHLY_PILLARS) -> list[Pillar]:
    """The standard pillars of the LME curve quoted on `as_of` (the curve's trade date):
    cash, 3M, and the monthly prompts (third Wednesdays) of the next `months` months, counted
    from the first month whose monthly prompt falls after the cash date. Sorted by date, one
    per date: a monthly prompt on the 3-month date is left to the 3M pillar (the LME's
    benchmark price)."""
    t = _d(as_of)
    cash, three_m = cash_date(t), three_month_date(t)
    out = [Pillar("CASH", cash, "CASH"), Pillar("3M", three_m, "3M")]
    year, month = t.year, t.month
    while monthly_prompt(year, month) <= cash:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    for _ in range(int(months)):
        p = monthly_prompt(year, month)
        if p != three_m:
            out.append(Pillar("MONTHLY", p, f"{year:04d}-{month:02d}"))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return sorted(out, key=lambda p: p.date)
