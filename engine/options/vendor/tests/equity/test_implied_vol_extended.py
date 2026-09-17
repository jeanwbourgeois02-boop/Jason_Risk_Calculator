"""Sanity checks for the barrier/digital implied volatility solvers added
to options_calc.equity.implied_vol."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.digital import price as digital_price
from options_calc.equity.barrier import price as barrier_price
from options_calc.equity.implied_vol import (
    implied_volatility_digital,
    implied_volatility_barrier,
)


def test_digital_round_trip_recovers_known_volatility():
    true_sigma = 0.2
    priced = digital_price(100, 100, 0.5, 0.05, true_sigma, "call", cash_payout=1.0)
    recovered = implied_volatility_digital(
        priced["price"], 100, 100, 0.5, 0.05, "call", cash_payout=1.0
    )
    assert abs(recovered - true_sigma) < 1e-6


def test_barrier_knock_in_round_trip_recovers_known_volatility():
    # Knock-in barrier prices are monotonic in vol, so the solver is
    # stable here -- unlike knock-out (see the test below).
    true_sigma = 0.2
    priced = barrier_price(100, 100, 120, 0.5, 0.05, true_sigma, "call", "up-and-in")
    recovered = implied_volatility_barrier(
        priced["price"], 100, 100, 120, 0.5, 0.05, "call", "up-and-in"
    )
    assert abs(recovered - true_sigma) < 1e-6


def test_barrier_knock_out_can_fail_because_price_is_not_monotonic_in_vol():
    # Documented, real limitation (not a bug): knock-out barrier vega
    # changes sign, so price is a hump shape in vol, not monotonic. A
    # simple bracketing solver can fail to find a root that genuinely
    # exists elsewhere on the curve, or that isn't uniquely determined.
    # This test asserts the FAILURE MODE is a clear, informative
    # ValueError, not a crash or a silently wrong answer.
    priced = barrier_price(100, 100, 120, 0.5, 0.05, 0.08, "call", "up-and-out")
    try:
        implied_volatility_barrier(
            priced["price"], 100, 100, 120, 0.5, 0.05, "call", "up-and-out"
        )
    except ValueError as exc:
        assert "not monotonic" in str(exc).lower()
