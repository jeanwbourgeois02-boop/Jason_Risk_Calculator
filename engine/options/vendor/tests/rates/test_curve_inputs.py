"""Sanity checks for the curve-input (discount_curve/forecast_curve) and
explicit-exercise-dates upgrade to options_calc.rates.

Same philosophy as tests/rates/test_swaption.py: structural/identity
checks plus a couple of tight numerical-equivalence checks that are exact
by construction (a curve built from a flat rate must reproduce the
flat-rate price bit-for-bit, since both paths end up building/using the
same kind of QuantLib term structure) rather than hand-computed reference
numbers.
"""

import datetime
import sys
from pathlib import Path

import pytest
import QuantLib as ql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.rates._engine import build_curve, build_forward_swap, DAY_COUNTER
from options_calc.rates.swaption import price as swaption_price
from options_calc.rates.cap_floor import price_cap, price_floor
from options_calc.rates.bermudan_swaption import (
    price_bermudan_swaption,
    price_european_swaption_hw,
    _generate_exercise_dates,
)

_TODAY = ql.Date.todaysDate()


def _ql_to_pydate(d):
    return datetime.date(d.year(), d.month(), d.dayOfMonth())


def _two_node_zero_curve(today, near_rate, far_rate):
    """A genuinely non-flat ql.ZeroCurve: `near_rate` near the front, ramping
    linearly to `far_rate` by 30y out -- enough curve shape for a 5y-into-10y
    swaption (spanning years 5-15) to see a materially different rate than
    either flat endpoint alone."""
    dates = [today, today + ql.Period(2, ql.Years), today + ql.Period(30, ql.Years)]
    rates = [near_rate, near_rate, far_rate]
    curve = ql.ZeroCurve(dates, rates, DAY_COUNTER)
    return ql.YieldTermStructureHandle(curve)


# --------------------------------------------------------------------- (a)


def test_flat_forward_curve_reproduces_flat_rate_price_swaption():
    rate = 0.04
    curve = build_curve(_TODAY, rate)
    flat = swaption_price(0.04, 5, 10, rate, 0.20, option_type="payer", evaluation_date=_TODAY)
    curved = swaption_price(0.04, 5, 10, rate, 0.20, option_type="payer",
                             discount_curve=curve, forecast_curve=curve, evaluation_date=_TODAY)
    assert abs(flat["price"] - curved["price"]) < 1e-10


def test_flat_forward_curve_reproduces_flat_rate_price_cap():
    rate = 0.04
    curve = build_curve(_TODAY, rate)
    flat = price_cap(0.04, 1.0, 5, rate, 0.20, evaluation_date=_TODAY)
    curved = price_cap(0.04, 1.0, 5, rate, 0.20, discount_curve=curve, forecast_curve=curve,
                        evaluation_date=_TODAY)
    assert abs(flat["price"] - curved["price"]) < 1e-10


def test_flat_forward_curve_reproduces_flat_rate_price_floor():
    rate = 0.04
    curve = build_curve(_TODAY, rate)
    flat = price_floor(0.04, 1.0, 5, rate, 0.20, evaluation_date=_TODAY)
    curved = price_floor(0.04, 1.0, 5, rate, 0.20, discount_curve=curve, forecast_curve=curve,
                          evaluation_date=_TODAY)
    assert abs(flat["price"] - curved["price"]) < 1e-10


def test_flat_forward_curve_reproduces_flat_rate_price_bermudan():
    rate = 0.04
    curve = build_curve(_TODAY, rate)
    flat = price_bermudan_swaption(0.04, 5, 10, 1.0, rate, option_type="payer",
                                    evaluation_date=_TODAY)
    curved = price_bermudan_swaption(0.04, 5, 10, 1.0, rate, option_type="payer",
                                      discount_curve=curve, forecast_curve=curve,
                                      evaluation_date=_TODAY)
    assert abs(flat - curved) < 1e-10


# --------------------------------------------------------------------- (b)


def test_non_flat_curve_changes_swaption_price_versus_either_flat_node():
    near_rate, far_rate = 0.02, 0.06
    shaped_curve = _two_node_zero_curve(_TODAY, near_rate, far_rate)

    shaped = swaption_price(0.04, 5, 10, near_rate, 0.20, option_type="payer",
                             discount_curve=shaped_curve, forecast_curve=shaped_curve,
                             evaluation_date=_TODAY)
    flat_near = swaption_price(0.04, 5, 10, near_rate, 0.20, option_type="payer",
                                evaluation_date=_TODAY)
    flat_far = swaption_price(0.04, 5, 10, far_rate, 0.20, option_type="payer",
                               evaluation_date=_TODAY)

    assert shaped["price"] != pytest.approx(flat_near["price"])
    assert shaped["price"] != pytest.approx(flat_far["price"])


# --------------------------------------------------------------------- (c)


def _generated_exercise_dates(fixed_rate, first_exercise, swap_tenor, r, notional, option_type,
                               exercise_frequency):
    _, start_date, swap, _ = build_forward_swap(
        fixed_rate, first_exercise, swap_tenor, r, notional, option_type,
        evaluation_date=_TODAY,
    )
    end_date = swap.fixedSchedule().endDate()
    return _generate_exercise_dates(start_date, end_date, exercise_frequency)


def test_explicit_exercise_dates_matching_schedule_reproduces_generated_price():
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0

    ql_dates = _generated_exercise_dates(fixed_rate, first_exercise, swap_tenor, r, notional,
                                          "payer", exercise_frequency=1.0)
    explicit_dates = [_ql_to_pydate(d) for d in ql_dates]

    generated = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                         "payer", evaluation_date=_TODAY)
    explicit = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                        "payer", evaluation_date=_TODAY,
                                        exercise_dates=explicit_dates)
    assert abs(generated - explicit) < 1e-9


def test_exercise_dates_subset_prices_between_european_and_full_schedule():
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0

    ql_dates = _generated_exercise_dates(fixed_rate, first_exercise, swap_tenor, r, notional,
                                          "payer", exercise_frequency=1.0)
    assert len(ql_dates) >= 4, "test needs a full schedule with room for a strict subset"
    subset_dates = [_ql_to_pydate(d) for d in ql_dates[::2]]

    full = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                    "payer", evaluation_date=_TODAY)
    subset = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                      "payer", evaluation_date=_TODAY,
                                      exercise_dates=subset_dates)
    european = price_european_swaption_hw(fixed_rate, first_exercise, swap_tenor, r, notional,
                                           "payer", evaluation_date=_TODAY)

    assert subset <= full + 1e-6
    assert subset >= european - 1e-6


# --------------------------------------------------------------------- (d)


def test_both_discount_rate_and_discount_curve_raises():
    curve = build_curve(_TODAY, 0.04)
    with pytest.raises(ValueError):
        swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                        discount_rate=0.04, discount_curve=curve)


def test_both_forecast_rate_and_forecast_curve_raises():
    curve = build_curve(_TODAY, 0.04)
    with pytest.raises(ValueError):
        swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                        forecast_rate=0.04, forecast_curve=curve)


def test_cap_floor_curve_and_rate_together_raises():
    curve = build_curve(_TODAY, 0.04)
    with pytest.raises(ValueError):
        price_cap(0.04, 1.0, 5, 0.04, 0.20, discount_rate=0.03, discount_curve=curve)


def test_unsorted_exercise_dates_raises():
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0
    ql_dates = _generated_exercise_dates(fixed_rate, first_exercise, swap_tenor, r, notional,
                                          "payer", exercise_frequency=1.0)
    dates = [_ql_to_pydate(d) for d in ql_dates]
    dates[0], dates[1] = dates[1], dates[0]  # break the strictly-increasing order
    with pytest.raises(ValueError):
        price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                 "payer", evaluation_date=_TODAY, exercise_dates=dates)


def test_exercise_date_after_maturity_raises():
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0
    ql_dates = _generated_exercise_dates(fixed_rate, first_exercise, swap_tenor, r, notional,
                                          "payer", exercise_frequency=1.0)
    dates = [_ql_to_pydate(d) for d in ql_dates]
    dates.append(_ql_to_pydate(ql_dates[-1] + ql.Period(50, ql.Years)))  # far past maturity
    with pytest.raises(ValueError):
        price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                 "payer", evaluation_date=_TODAY, exercise_dates=dates)


def test_exercise_date_before_evaluation_date_raises():
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0
    ql_dates = _generated_exercise_dates(fixed_rate, first_exercise, swap_tenor, r, notional,
                                          "payer", exercise_frequency=1.0)
    dates = [_ql_to_pydate(_TODAY - 10)] + [_ql_to_pydate(d) for d in ql_dates]
    with pytest.raises(ValueError):
        price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                 "payer", evaluation_date=_TODAY, exercise_dates=dates)


def test_empty_exercise_dates_raises():
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0
    with pytest.raises(ValueError):
        price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                 "payer", evaluation_date=_TODAY, exercise_dates=[])


# --------------------------------------------------------------------- (e)


def test_evaluation_date_restored_after_swaption_call():
    settings = ql.Settings.instance()
    before = settings.evaluationDate
    other_date = before + ql.Period(1, ql.Years)

    swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer", evaluation_date=other_date)

    assert settings.evaluationDate == before


def test_evaluation_date_restored_after_cap_call():
    settings = ql.Settings.instance()
    before = settings.evaluationDate
    other_date = before + ql.Period(1, ql.Years)

    price_cap(0.04, 1.0, 5, 0.04, 0.20, evaluation_date=other_date)

    assert settings.evaluationDate == before


def test_evaluation_date_restored_after_bermudan_call():
    settings = ql.Settings.instance()
    before = settings.evaluationDate
    other_date = before + ql.Period(1, ql.Years)

    price_bermudan_swaption(0.04, 5, 10, 1.0, 0.04, option_type="payer", evaluation_date=other_date)

    assert settings.evaluationDate == before


def test_evaluation_date_restored_even_when_default_today_is_used():
    """Calling with no evaluation_date at all (the historical default) must
    still leave the global evaluation date exactly as it found it -- this
    is the regression test for the "app found this bit it" quirk: this
    module used to permanently overwrite the global evaluation date with
    ql.Date.todaysDate() on every call and never put back whatever a
    caller with its own cached curve had set."""
    settings = ql.Settings.instance()
    settings.evaluationDate = _TODAY - 5  # simulate a caller with a curve pinned to a past as_of date
    before = settings.evaluationDate

    swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer")

    assert settings.evaluationDate == before
    settings.evaluationDate = _TODAY  # restore the module-level anchor for any other test in this run
