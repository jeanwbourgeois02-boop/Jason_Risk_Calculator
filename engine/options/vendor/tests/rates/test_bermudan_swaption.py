"""Sanity checks for options_calc.rates.bermudan_swaption.

Same philosophy as tests/rates/test_swaption.py: structural/identity
checks (the Bermudan analogue of "American >= European" already tested
elsewhere in this package), not fixed reference numbers.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.rates.bermudan_swaption import (
    price_bermudan_swaption,
    price_european_swaption_hw,
)


def test_implausible_hw_volatility_raises():
    # Regression test for a Moderate bug found in audit: an unbounded
    # hw_volatility (e.g. a typo'd 1.0 instead of 0.01) used to silently
    # produce a structurally normal-looking but economically nonsensical
    # price (larger than notional) with no error.
    with pytest.raises(ValueError):
        price_bermudan_swaption(
            0.04, 5, 10, 1.0, 0.04, hw_volatility=1.0,
        )


def test_bermudan_is_worth_at_least_the_equivalent_european_same_model():
    """More exercise opportunities can only add value -- a Bermudan
    swaption exercisable annually must be worth at least as much as the
    European swaption exercisable only at the first date, PRICED UNDER
    THE SAME Hull-White model and tree engine (see module docstring for
    why the comparison must hold the model/engine fixed)."""
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0

    european = price_european_swaption_hw(fixed_rate, first_exercise, swap_tenor, r, notional, "payer")
    bermudan = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional, "payer")

    assert bermudan >= european - 1e-6


def test_more_frequent_exercise_is_worth_at_least_as_much():
    """Exercising quarterly gives strictly more opportunities than
    exercising annually (a superset of dates), so it can only add value."""
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0

    annual = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional, "payer")
    semiannual = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 0.5, r, notional, "payer")

    assert semiannual >= annual - 1e-6


def test_price_is_positive():
    result = price_bermudan_swaption(0.04, 5, 10, 1.0, 0.04, option_type="payer")
    assert result > 0

    result_receiver = price_bermudan_swaption(0.04, 5, 10, 1.0, 0.04, option_type="receiver")
    assert result_receiver > 0


def test_price_roughly_stable_across_tree_step_counts():
    """The trinomial-tree price should converge, not blow up or swing
    wildly, as tree_steps increases across a reasonable range -- a basic
    sanity check on the discretization (see module docstring's note that
    tree_steps controls discretization error)."""
    fixed_rate, first_exercise, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0

    coarse = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                      "payer", tree_steps=40)
    fine = price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, 1.0, r, notional,
                                    "payer", tree_steps=200)

    assert abs(coarse - fine) < 0.05 * max(coarse, fine)


def test_multi_curve_arguments_are_accepted():
    """price_bermudan_swaption should accept the same discount_rate/
    forecast_rate overrides as swaption.py/cap_floor.py."""
    result = price_bermudan_swaption(0.04, 5, 10, 1.0, 0.04, option_type="payer",
                                      discount_rate=0.03, forecast_rate=0.05)
    assert result > 0
