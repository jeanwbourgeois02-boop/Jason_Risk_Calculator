"""Sanity checks for options_calc.fx.calendars."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import QuantLib as ql
from options_calc.fx.calendars import currency_calendar, joint_calendar, spot_lag_days, spot_date


def test_currency_calendar_rejects_unknown_currency():
    try:
        currency_calendar("XXX")
        assert False, "expected a ValueError for a non-G10 currency"
    except ValueError:
        pass


def test_default_spot_lag_is_two_business_days():
    assert spot_lag_days("EURUSD") == 2
    assert spot_lag_days("GBPJPY") == 2


def test_usdcad_spot_lag_is_one_business_day():
    # Well-known standard exception to the general T+2 FX spot rule.
    assert spot_lag_days("USDCAD") == 1


def test_spot_date_skips_weekend():
    thursday = ql.Date(4, ql.January, 2024)
    sd = spot_date("EURUSD", thursday)
    # Thursday + 2 business days, skipping Sat/Sun, lands on the following Monday
    assert sd == ql.Date(8, ql.January, 2024)


def test_spot_date_skips_holidays_in_either_currency():
    # Dec 24 2024 (Tuesday) + 2 business days must skip Dec 25 (holiday in
    # both US and TARGET) AND Dec 26 (a TARGET-specific holiday, not a US
    # one) -- proving the JOINT calendar is used, not just one currency's.
    dec24 = ql.Date(24, ql.December, 2024)
    sd = spot_date("EURUSD", dec24)
    assert sd == ql.Date(30, ql.December, 2024)


def test_joint_calendar_is_stricter_than_either_single_calendar():
    # Dec 26 must be a holiday in the EURUSD joint calendar (TARGET closes
    # it) even though it need not be a US-only holiday.
    calendar = joint_calendar("EURUSD")
    dec26 = ql.Date(26, ql.December, 2024)
    assert not calendar.isBusinessDay(dec26)
