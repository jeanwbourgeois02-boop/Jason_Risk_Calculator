"""Sanity checks for options_calc.fx.implied_vol -- see
equity/test_implied_vol.py for why these are round-trip tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.european import price
from options_calc.fx.implied_vol import implied_volatility


def test_round_trip_recovers_known_volatility():
    true_sigma = 0.12
    priced = price(1.10, 1.10, 1.0, 0.05, 0.03, true_sigma, "call")
    recovered = implied_volatility(priced["price"], 1.10, 1.10, 1.0, 0.05, 0.03, "call")
    assert abs(recovered - true_sigma) < 1e-6
