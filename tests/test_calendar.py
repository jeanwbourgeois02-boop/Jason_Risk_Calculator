"""engine/pnl/calendar.py: business-day calendar helpers, split out of
engine/pnl/aggregate.py on 2026-09-17 ("no bnp fall back", docs/bnp-excel-removal.md)
so the live P&L path (value_book, fx_blotter_rows, header, ledger) no longer shares a
module with -- or transitively imports -- the retired workbook arithmetic in
engine/pnl/pnl.py. Mirrors tests/test_aggregate.py's coverage of the same functions
(kept there too, re-exported, for backward compatibility -- see that file)."""
from __future__ import annotations

import datetime as dt

from engine.pnl.calendar import (
    _is_business_day,
    _last_business_day_of_prev_month,
    _last_business_day_of_prev_year,
    _n_business_days_back,
    _prev_business_day,
    load_holidays,
)


def test_load_holidays_reads_dates(tmp_path):
    p = tmp_path / "holidays.txt"
    p.write_text("2026-01-01\n# comment\n\n2026-12-25\n")
    assert load_holidays(p) == frozenset({"2026-01-01", "2026-12-25"})


def test_is_business_day_skips_weekends_and_holidays():
    holidays = frozenset({"2026-09-11"})  # Friday
    assert _is_business_day(dt.date(2026, 9, 10), holidays)   # Thursday
    assert not _is_business_day(dt.date(2026, 9, 11), holidays)  # holiday Friday
    assert not _is_business_day(dt.date(2026, 9, 12), holidays)  # Saturday
    assert not _is_business_day(dt.date(2026, 9, 13), holidays)  # Sunday
    assert _is_business_day(dt.date(2026, 9, 14), holidays)   # Monday


def test_prev_business_day_skips_a_holiday():
    holidays = frozenset({"2026-09-11"})
    assert _prev_business_day(dt.date(2026, 9, 14), holidays) == dt.date(2026, 9, 10)


def test_n_business_days_back():
    assert _n_business_days_back(dt.date(2026, 9, 17), 5, frozenset()) == dt.date(2026, 9, 10)


def test_last_business_day_of_prev_month():
    assert _last_business_day_of_prev_month(dt.date(2026, 9, 17), frozenset()) == dt.date(2026, 8, 31)


def test_last_business_day_of_prev_year():
    assert _last_business_day_of_prev_year(dt.date(2026, 9, 17), frozenset()) == dt.date(2025, 12, 31)


def test_aggregate_reexports_the_same_functions():
    """engine/pnl/aggregate.py re-exports these names for callers not yet repointed at
    engine.pnl.calendar (see that module's docstring) -- pin that the re-export is the
    exact same object for everything except `load_holidays`, which is deliberately a
    thin wrapper (not a bare re-export) so tests/test_ledger.py's existing
    `monkeypatch.setattr("engine.pnl.aggregate._DEFAULT_HOLIDAYS_PATH", ...)` keeps
    working -- see aggregate.py's own comment on `_DEFAULT_HOLIDAYS_PATH`."""
    from engine.pnl import aggregate, calendar
    assert aggregate._is_business_day is calendar._is_business_day
    assert aggregate._prev_business_day is calendar._prev_business_day
    assert aggregate._n_business_days_back is calendar._n_business_days_back
    assert aggregate._last_business_day_of_prev_month is calendar._last_business_day_of_prev_month
    assert aggregate._last_business_day_of_prev_year is calendar._last_business_day_of_prev_year
    assert aggregate.load_holidays is not calendar.load_holidays
    assert aggregate.load_holidays() == calendar.load_holidays()


# --------------------------------------------------------------------- spot_date (2026-09-22)
# One spot-date rule for the app: the rule data/bloomberg/fwd_curve.py::spot_date_for applied
# to its computed tenor dates, now in engine.pnl.calendar so the valuation's curve pillars
# (engine.pnl.valuation._day_pillars) put spot on the same date.
from engine.pnl.calendar import _SPOT_LAG_ONE_DAY, spot_date  # noqa: E402


def test_spot_date_is_two_weekdays_on_for_a_t_plus_2_pair():
    assert spot_date("2026-06-01", "USDJPY", frozenset()) == dt.date(2026, 6, 3)      # Mon -> Wed
    assert spot_date(dt.date(2026, 6, 4), "EURUSD", frozenset()) == dt.date(2026, 6, 8)  # Thu -> Mon, over the weekend
    assert spot_date("2026-06-01", holidays=frozenset()) == dt.date(2026, 6, 3)        # no pair: T+2


def test_spot_date_is_one_weekday_on_for_the_t_plus_1_pairs():
    assert _SPOT_LAG_ONE_DAY == frozenset({"USDCAD", "USDTRY", "USDPHP", "USDRUB"})
    for pair in _SPOT_LAG_ONE_DAY:
        assert spot_date("2026-06-01", pair, frozenset()) == dt.date(2026, 6, 2)      # Mon -> Tue
    assert spot_date("2026-06-05", "USDCAD", frozenset()) == dt.date(2026, 6, 8)      # Fri -> Mon


def test_spot_date_rolls_forward_off_a_holiday_but_a_holiday_in_between_does_not_count():
    holidays = frozenset({"2026-06-19"})                                               # Friday
    assert spot_date("2026-06-17", "USDJPY", holidays) == dt.date(2026, 6, 22)        # Wed -> Fri, rolled to Mon
    assert spot_date("2026-06-18", "USDCAD", holidays) == dt.date(2026, 6, 22)        # Thu T+1 -> Fri, rolled to Mon
    assert spot_date("2026-06-18", "USDJPY", holidays) == dt.date(2026, 6, 22)        # the holiday in between still counts as a weekday
    assert spot_date("2026-06-17", "USDJPY") == dt.date(2026, 6, 22)                  # config/holidays.txt lists 2026-06-19


def test_spot_date_is_the_historical_curve_builders_own_rule():
    from data.bloomberg.fwd_curve import _SPOT_LAG_ONE_DAY as builder_lag, spot_date_for
    assert builder_lag == _SPOT_LAG_ONE_DAY
    holidays = frozenset({"2026-06-19", "2026-07-03"})
    for day in ("2026-06-16", "2026-06-17", "2026-06-18", "2026-07-01", "2026-07-02"):
        for pair in ("USDJPY", "USDCAD", ""):
            assert spot_date(day, pair, holidays) == spot_date_for(dt.date.fromisoformat(day), pair, holidays)
