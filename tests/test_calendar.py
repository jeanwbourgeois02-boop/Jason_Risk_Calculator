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
