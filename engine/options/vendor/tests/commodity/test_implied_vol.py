"""Sanity checks for options_calc.commodity.implied_vol."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.commodity.european import price
from options_calc.commodity.implied_vol import implied_volatility


def test_round_trip_recovers_known_volatility():
    true_sigma = 0.18
    priced = price(1950, 2000, 0.5, 0.05, true_sigma, "call")
    recovered = implied_volatility(priced["price"], 1950, 2000, 0.5, 0.05, "call")
    assert abs(recovered - true_sigma) < 1e-6


def test_higher_price_implies_higher_volatility():
    low_vol_price = price(1950, 2000, 0.5, 0.05, 0.10, "call")["price"]
    high_vol_price = price(1950, 2000, 0.5, 0.05, 0.30, "call")["price"]
    low_iv = implied_volatility(low_vol_price, 1950, 2000, 0.5, 0.05, "call")
    high_iv = implied_volatility(high_vol_price, 1950, 2000, 0.5, 0.05, "call")
    assert high_iv > low_iv
