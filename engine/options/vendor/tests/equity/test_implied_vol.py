"""Sanity checks for options_calc.equity.implied_vol.

The core test is a round trip: price an option at a known volatility, feed
the resulting price back into the solver, and confirm it recovers the same
volatility. This is the standard way to validate a solver when there's no
independent "reference" implied vol to check against -- the model's own
forward pricer is the ground truth here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.european import price
from options_calc.equity.implied_vol import implied_volatility


def test_round_trip_recovers_known_volatility():
    true_sigma = 0.27
    priced = price(100, 105, 0.5, 0.05, true_sigma, "call")
    recovered = implied_volatility(priced["price"], 100, 105, 0.5, 0.05, "call")
    assert abs(recovered - true_sigma) < 1e-6


def test_round_trip_holds_for_put():
    true_sigma = 0.18
    priced = price(100, 95, 0.75, 0.03, true_sigma, "put")
    recovered = implied_volatility(priced["price"], 100, 95, 0.75, 0.03, "put")
    assert abs(recovered - true_sigma) < 1e-6


def test_higher_price_implies_higher_volatility():
    # Vega is always positive for a vanilla option, so price and implied
    # vol must move in the same direction.
    low_vol_price = price(100, 105, 0.5, 0.05, 0.15, "call")["price"]
    high_vol_price = price(100, 105, 0.5, 0.05, 0.35, "call")["price"]
    low_iv = implied_volatility(low_vol_price, 100, 105, 0.5, 0.05, "call")
    high_iv = implied_volatility(high_vol_price, 100, 105, 0.5, 0.05, "call")
    assert high_iv > low_iv
