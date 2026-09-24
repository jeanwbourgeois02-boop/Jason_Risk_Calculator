"""engine/calendars: business days per exchange (exchange-calendars lane, commodity
conversion plan Phase 1 step 1)."""
from __future__ import annotations

import datetime as dt

import pytest

import engine.calendars as cals
from engine.calendars import (
    add_business_days,
    business_days_between,
    calendar_ids,
    coverage,
    holidays,
    is_business_day,
    last_business_day_of_month,
    next_business_day,
    previous_business_day,
)

D = dt.date
EXPECTED_IDS = {
    "US", "ICE_US", "ICE_EU", "LME", "CN", "JP", "SG", "EURONEXT", "MY", "HK", "EEX", "AE",
}


def test_every_calendar_loads_and_covers_2026_and_2027():
    assert EXPECTED_IDS <= set(calendar_ids())
    for cal in calendar_ids():
        first, last = coverage(cal)
        assert first <= D(2026, 1, 1) and last >= D(2027, 12, 31), cal
        days = holidays(cal)
        assert days and all(isinstance(h, D) for h in days), cal
        # a listed closure on a weekend or outside coverage is a typo in the file
        assert all(h.weekday() < 5 and first <= h <= last for h in days), cal


def test_weekends_are_never_business_days():
    weekend = [D(2026, 10, 3), D(2026, 10, 4), D(2027, 2, 6), D(2030, 6, 1)]
    for cal in calendar_ids():
        assert not any(is_business_day(cal, d) for d in weekend), cal


def test_china_national_day_week_2026_closed_on_cn_open_on_us():
    week = [D(2026, 10, d) for d in (1, 2, 5, 6, 7)]
    assert not any(is_business_day("CN", d) for d in week)
    assert all(is_business_day("US", d) for d in week)
    assert is_business_day("CN", D(2026, 10, 8))


def test_uk_christmas_and_boxing_day_closed_on_lme():
    for d in (D(2026, 12, 25), D(2026, 12, 28), D(2027, 12, 27), D(2027, 12, 28)):
        assert not is_business_day("LME", d), d
    assert is_business_day("LME", D(2026, 12, 29))
    assert is_business_day("US", D(2026, 12, 28))


def test_add_business_days_across_a_holiday():
    assert add_business_days("CN", D(2026, 9, 30), 1) == D(2026, 10, 8)
    assert add_business_days("CN", D(2026, 10, 8), -1) == D(2026, 9, 30)
    assert add_business_days("US", "2026-11-25", 1) == D(2026, 11, 27)  # Thanksgiving
    assert add_business_days("US", D(2026, 11, 25), 3) == D(2026, 12, 1)
    assert add_business_days("LME", D(2026, 12, 24), 2) == D(2026, 12, 30)
    assert add_business_days("US", D(2026, 11, 26), 0) == D(2026, 11, 26)  # n = 0: unchanged


def test_business_days_between_sign_and_inclusivity():
    # after start, up to and including end: the Thanksgiving Thursday is skipped
    assert business_days_between("US", D(2026, 11, 25), D(2026, 11, 27)) == 1
    assert business_days_between("US", D(2026, 11, 27), D(2026, 11, 25)) == -1
    assert business_days_between("US", D(2026, 11, 25), D(2026, 11, 25)) == 0
    assert business_days_between("CN", "2026-09-30", "2026-10-08") == 1
    # a weekend end counts nothing past Friday
    assert business_days_between("US", D(2026, 9, 28), D(2026, 10, 3)) == 4
    # antisymmetric, and the inverse of add_business_days from a business day
    start = D(2026, 1, 5)
    for cal in ("US", "CN", "LME", "JP"):
        for n in (-40, -7, -1, 1, 5, 23, 260):
            end = add_business_days(cal, start, n)
            assert business_days_between(cal, start, end) == n, (cal, n)
            assert business_days_between(cal, end, start) == -n, (cal, n)


def test_last_business_day_of_month_skips_a_holiday():
    assert last_business_day_of_month("LME", 2026, 8) == D(2026, 8, 28)  # 31 Aug bank holiday
    assert last_business_day_of_month("US", 2026, 8) == D(2026, 8, 31)
    assert last_business_day_of_month("JP", 2026, 12) == D(2026, 12, 30)  # 31 Dec closed
    assert last_business_day_of_month("US", 2027, 12) == D(2027, 12, 31)
    assert last_business_day_of_month("CN", 2026, 10) == D(2026, 10, 30)


def test_previous_and_next_business_day_are_strict():
    assert next_business_day("CN", D(2026, 9, 30)) == D(2026, 10, 8)
    assert previous_business_day("CN", D(2026, 10, 8)) == D(2026, 9, 30)
    assert next_business_day("US", D(2026, 11, 24)) == D(2026, 11, 25)


def test_unknown_calendar_raises_naming_the_ones_on_file():
    with pytest.raises(ValueError, match=r"NYMEX.*LME"):
        is_business_day("NYMEX", D(2026, 10, 1))
    with pytest.raises(ValueError, match="calendars on file"):
        add_business_days("XX", D(2026, 10, 1), 0)
    assert is_business_day(" lme ", "2026-12-29")  # id matched after strip / upper


def test_malformed_date_in_a_file_is_reported(tmp_path, monkeypatch):
    (tmp_path / "BAD.txt").write_text("# coverage: 2026-01-01 to 2026-12-31\n2026-13-01\n")
    monkeypatch.setattr(cals, "CALENDAR_DIR", tmp_path)
    with pytest.raises(ValueError, match="BAD.txt line 2"):
        holidays("BAD")
