"""Sanity checks for the barrier/digital implied volatility solvers added
to options_calc.fx.implied_vol -- see equity/test_implied_vol_extended.py
for the reasoning behind each check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.digital import price as digital_price
from options_calc.fx.barrier import price as barrier_price
from options_calc.fx.implied_vol import (
    implied_volatility_digital,
    implied_volatility_barrier,
)


def test_digital_round_trip_recovers_known_volatility():
    true_sigma = 0.08
    priced = digital_price(1.10, 1.10, 0.5, 0.045, 0.0325, true_sigma, "call", cash_payout=1.0)
    recovered = implied_volatility_digital(
        priced["price"], 1.10, 1.10, 0.5, 0.045, 0.0325, "call", cash_payout=1.0
    )
    assert abs(recovered - true_sigma) < 1e-6


def test_barrier_knock_in_round_trip_recovers_known_volatility():
    true_sigma = 0.08
    priced = barrier_price(1.10, 1.10, 1.20, 0.5, 0.045, 0.0325, true_sigma, "call", "up-and-in")
    recovered = implied_volatility_barrier(
        priced["price"], 1.10, 1.10, 1.20, 0.5, 0.045, 0.0325, "call", "up-and-in"
    )
    assert abs(recovered - true_sigma) < 1e-6


def test_barrier_knock_out_can_fail_because_price_is_not_monotonic_in_vol():
    priced = barrier_price(1.10, 1.10, 1.20, 0.5, 0.045, 0.0325, 0.08, "call", "up-and-out")
    try:
        implied_volatility_barrier(
            priced["price"], 1.10, 1.10, 1.20, 0.5, 0.045, 0.0325, "call", "up-and-out"
        )
    except ValueError as exc:
        assert "not monotonic" in str(exc).lower()
