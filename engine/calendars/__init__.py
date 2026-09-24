"""Business days per exchange (the exchange-calendars lane, commodity conversion plan,
Phase 1 step 1).

A commodity book trades on exchanges that close on different days: the Chinese
exchanges for the Spring Festival and National Day week, the LME and ICE Europe on UK
holidays, CME and ICE US on US holidays, OSE, SGX, Euronext, Bursa Malaysia, HKEX. This
package says, per calendar, which days are business days and how far apart two dates
are in business days. It is read by the contract-master, expiry-monitor, lme-forwards
and pnl-valuation lanes.

Data: one file per calendar, `config/calendars/<ID>.txt`, one ISO date per line (a
full-day closure), `#` comments and blank lines ignored; anything after a `#` on a date
line is a comment too (`YYYY-MM-DD  # Spring Festival  # unverified`). A header line
`# coverage: YYYY-MM-DD to YYYY-MM-DD` states the span the file was written for
(`coverage`); beyond it the calendar still answers, on weekends alone, so a consumer
that needs to know checks `coverage` itself. The files are read once and cached.

Rules: a Saturday or Sunday is never a business day, whatever the file says. An
unknown calendar id raises `ValueError` naming the ids on file, never a fallback to
another calendar. Ids are matched after `strip().upper()`. Dates may be `date` (or
`datetime`, whose date is used) or ISO strings (`'YYYY-MM-DD'`; a longer ISO timestamp
is read by its first ten characters).

This is not the book's own period calendar: `config/holidays.txt`, read by
`engine/pnl/calendar.py`, belongs to pnl-valuation, and which calendar defines Daily is
the user's decision (open question C7).
"""
from __future__ import annotations

import bisect
import datetime as dt
import re
from functools import lru_cache
from pathlib import Path
from typing import Union

__all__ = [
    "CALENDAR_DIR",
    "calendar_ids",
    "holidays",
    "coverage",
    "is_business_day",
    "add_business_days",
    "business_days_between",
    "last_business_day_of_month",
    "previous_business_day",
    "next_business_day",
]

CALENDAR_DIR = Path(__file__).resolve().parents[2] / "config" / "calendars"

DateLike = Union[dt.date, str]

_COVERAGE_RE = re.compile(
    r"^#\s*coverage:\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE
)
_ONE_DAY = dt.timedelta(days=1)


# --------------------------------------------------------------------------- loading


@lru_cache(maxsize=None)
def _ids_in(dir_str: str) -> tuple[str, ...]:
    d = Path(dir_str)
    if not d.is_dir():
        return ()
    return tuple(sorted(p.stem.upper() for p in d.glob("*.txt")))


@lru_cache(maxsize=None)
def _load(dir_str: str, cal: str) -> tuple[tuple[dt.date, ...], tuple[dt.date, dt.date]]:
    """(sorted holidays, coverage) of one calendar file. A malformed date line raises
    ValueError naming the file and line: a data error is reported, never skipped."""
    path = next(p for p in Path(dir_str).glob("*.txt") if p.stem.upper() == cal)
    days: set[dt.date] = set()
    cover: tuple[dt.date, dt.date] | None = None
    for no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = _COVERAGE_RE.match(line)
            if m:
                cover = (dt.date.fromisoformat(m.group(1)), dt.date.fromisoformat(m.group(2)))
            continue
        token = line.split("#", 1)[0].strip()
        try:
            days.add(dt.date.fromisoformat(token))
        except ValueError:
            raise ValueError(
                f"{path.name} line {no}: {raw.strip()!r} is not an ISO date (YYYY-MM-DD)"
            ) from None
    ordered = tuple(sorted(days))
    if cover is None:
        if ordered:
            cover = (dt.date(ordered[0].year, 1, 1), dt.date(ordered[-1].year, 12, 31))
        else:
            raise ValueError(f"{path.name}: no dates and no '# coverage:' line")
    return ordered, cover


def _cal(cal: str) -> tuple[tuple[dt.date, ...], tuple[dt.date, dt.date]]:
    if not isinstance(cal, str):
        raise ValueError(f"calendar id must be a string, got {cal!r}")
    key = cal.strip().upper()
    dir_str = str(CALENDAR_DIR)
    ids = _ids_in(dir_str)
    if key not in ids:
        have = ", ".join(ids) if ids else f"none (no files in {CALENDAR_DIR})"
        raise ValueError(f"unknown exchange calendar {cal!r}; calendars on file: {have}")
    return _load(dir_str, key)


def _as_date(d: DateLike) -> dt.date:
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if isinstance(d, str):
        try:
            return dt.date.fromisoformat(d.strip()[:10])
        except ValueError:
            raise ValueError(f"not an ISO date (YYYY-MM-DD): {d!r}") from None
    raise ValueError(f"not a date or ISO date string: {d!r}")


def _weekdays_after_upto(a: dt.date, b: dt.date) -> int:
    """Monday-to-Friday days in (a, b], for a <= b."""
    def through(x: dt.date) -> int:  # weekdays from a fixed Monday up to and including x
        n = x.toordinal() - 1  # ordinal 1, 1 January of year 1, is a Monday
        weeks, rem = divmod(n + 1, 7)
        return weeks * 5 + min(rem, 5)
    return through(b) - through(a)


# --------------------------------------------------------------------------- public API


def calendar_ids() -> list[str]:
    """The calendar ids on file, sorted (`config/calendars/<ID>.txt`)."""
    return list(_ids_in(str(CALENDAR_DIR)))


def holidays(cal: str) -> frozenset[dt.date]:
    """Every full-day closure listed for `cal`."""
    return frozenset(_cal(cal)[0])


def coverage(cal: str) -> tuple[dt.date, dt.date]:
    """(first, last) date the file for `cal` was written to cover. Outside it only
    weekends are known to be closed."""
    return _cal(cal)[1]


def is_business_day(cal: str, d: DateLike) -> bool:
    """True on a Monday to Friday that is not a listed closure of `cal`."""
    day = _as_date(d)
    if day.weekday() >= 5:
        return False
    days = _cal(cal)[0]
    i = bisect.bisect_left(days, day)
    return not (i < len(days) and days[i] == day)


def next_business_day(cal: str, d: DateLike) -> dt.date:
    """The first business day strictly after `d`."""
    day = _as_date(d) + _ONE_DAY
    while not is_business_day(cal, day):
        day += _ONE_DAY
    return day


def previous_business_day(cal: str, d: DateLike) -> dt.date:
    """The last business day strictly before `d`."""
    day = _as_date(d) - _ONE_DAY
    while not is_business_day(cal, day):
        day -= _ONE_DAY
    return day


def add_business_days(cal: str, d: DateLike, n: int) -> dt.date:
    """`d` moved `n` business days (n < 0 moves back). The start day itself is never
    counted, business day or not; `n == 0` returns `d` unchanged."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError(f"n must be an integer number of business days, got {n!r}")
    day = _as_date(d)
    _cal(cal)  # an unknown id raises even when n == 0
    step = next_business_day if n > 0 else previous_business_day
    for _ in range(abs(n)):
        day = step(cal, day)
    return day


def business_days_between(cal: str, start: DateLike, end: DateLike) -> int:
    """Business days after `start` up to and including `end`; when `end` is before
    `start`, minus the business days after `end` up to and including `start`. So the
    count is antisymmetric (between(a, b) == -between(b, a)) and same-day is 0. When
    `start` and `end` are both business days it is the inverse of add_business_days:
    add_business_days(cal, start, between(start, end)) == end."""
    a, b = _as_date(start), _as_date(end)
    sign = 1
    if b < a:
        a, b, sign = b, a, -1
    days = _cal(cal)[0]
    lo = bisect.bisect_right(days, a)
    hi = bisect.bisect_right(days, b)
    closed = sum(1 for h in days[lo:hi] if h.weekday() < 5)
    return sign * (_weekdays_after_upto(a, b) - closed)


def last_business_day_of_month(cal: str, year: int, month: int) -> dt.date:
    """The last business day of `year`-`month` on `cal`."""
    dt.date(year, month, 1)  # an impossible month raises here
    first_next = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return previous_business_day(cal, first_next)
