"""FX calendar-aware year fraction and spot-date helpers, wiring the
vendored ``options_calc.fx.calendars`` into this package's own date math
(Phase 7, options_calc merge).

**What this replaces.** ``pricer.py``'s original ``year_fraction(as_of,
expiry)`` is a plain Act/365 CALENDAR-day count -- every day counts,
weekends and holidays included. ``calendar_year_fraction`` below instead
counts BUSINESS days on the pair's joint settlement calendar
(``options_calc.fx.calendars.joint_calendar`` -- a day only counts if it's
a business day in BOTH currencies' home markets) over a 252-business-
day year (``ql.Business252``). The two conventions land close in
magnitude (252/365 roughly tracks the fraction of calendar days that are
business days) but are NOT identical, and differ MORE when a holiday
falls inside the window -- exactly the gap this module exists to close.

**Where this is used.** ``pricer.py``'s ``price_fx_*`` functions take an
optional ``pair`` argument; when given (and ``calendar_aware=True``, the
default), they call ``calendar_year_fraction`` instead of the plain
day count. ``pair=None`` (every existing call site that predates this
phase) keeps the old plain-days behavior unchanged -- this is the
"explicit flag for tests" / backward-compatibility path, not a new
default (see pricer.py's ``_resolve_T``). ``store.py``'s dispatch always
passes ``pair``, so calendar-aware dating IS the production path.

**Non-G10 pairs.** ``joint_calendar`` (via ``options_calc.fx.g10.
pair_convention``) only recognizes the 45 G10 pairs. A pair outside that
set (e.g. USDTWD) raises ``ValueError`` here, which ``pricer.py`` catches
and falls back to the plain day count -- documented via
``OptionPriceResult.delta_convention == 'UNKNOWN'`` on the same trade
(see pricer.py), not a silent behavior change.

**Spot/delivery dates.** ``spot_date`` below is a thin, tested passthrough
to ``options_calc.fx.calendars.spot_date`` (correct spot lag: T+2 for
every G10 pair except USD/CAD's well-known T+1 exception). Garman-
Kohlhagen pricing itself needs no spot date (only T to the option's own
expiry) so nothing in this phase's pricer wiring consumes it yet; exposed
here so a future caller (e.g. a forward-starting structure, or
engine/ladder settlement-date logic) has a correct, tested reference
function to call rather than reinventing spot-lag business-day math.
"""
from __future__ import annotations

import datetime


def calendar_year_fraction(pair: str, as_of: datetime.date, expiry: datetime.date) -> float:
    """Business-day year fraction (Business252, joint calendar) from
    `as_of` to `expiry` for a recognized G10 `pair`. Raises ValueError if
    `expiry` is not after `as_of` (mirrors pricer.year_fraction's own
    check) or if `pair` is not a recognized G10 pair (propagated from
    options_calc.fx.calendars.joint_calendar / fx.g10.pair_convention) --
    callers fall back to the plain day count on the latter."""
    if (expiry - as_of).days <= 0:
        raise ValueError(f"expiry {expiry.isoformat()} is not after as_of {as_of.isoformat()}")

    import QuantLib as ql

    from .vendor.options_calc.fx.calendars import joint_calendar

    cal = joint_calendar(pair)  # raises ValueError for a non-G10 pair
    d1 = ql.Date(as_of.day, as_of.month, as_of.year)
    d2 = ql.Date(expiry.day, expiry.month, expiry.year)
    return ql.Business252(cal).yearFraction(d1, d2)


def spot_date(pair: str, trade_date: datetime.date) -> datetime.date:
    """Spot settlement date for a trade in `pair` done on `trade_date`:
    `trade_date` advanced by the pair's spot lag (2 business days, or 1
    for USD/CAD) on the joint calendar. Raises ValueError for a non-G10
    pair (same propagation as calendar_year_fraction)."""
    import QuantLib as ql

    from .vendor.options_calc.fx.calendars import spot_date as _vendor_spot_date

    trade_ql = ql.Date(trade_date.day, trade_date.month, trade_date.year)
    result_ql = _vendor_spot_date(pair, trade_ql)
    return datetime.date(result_ql.year(), result_ql.month(), result_ql.dayOfMonth())
