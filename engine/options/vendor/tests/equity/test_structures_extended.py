"""Sanity checks for the call_spread/put_spread/butterfly structures
added to options_calc.equity.structures."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.european import price
from options_calc.equity.structures import call_spread, put_spread, butterfly


def test_call_spread_cheaper_than_outright_call():
    spread = call_spread(S=100, K_low=100, K_high=110, T=0.5, r=0.05, sigma=0.2)
    outright = price(100, 100, 0.5, 0.05, 0.2, "call")
    assert spread["price"] < outright["price"]
    assert spread["price"] > 0


def test_put_spread_cheaper_than_outright_put():
    spread = put_spread(S=100, K_low=90, K_high=100, T=0.5, r=0.05, sigma=0.2)
    outright = price(100, 100, 0.5, 0.05, 0.2, "put")
    assert spread["price"] < outright["price"]
    assert spread["price"] > 0


def test_butterfly_is_short_gamma_and_vega():
    # A long butterfly bets on LOW realized vol (pinning near K_mid) --
    # the opposite of a straddle, so gamma and vega must be negative.
    b = butterfly(S=100, K_low=90, K_mid=100, K_high=110, T=0.5, r=0.05, sigma=0.2)
    assert b["gamma"] < 0
    assert b["vega"] < 0


def test_butterfly_equals_sum_of_its_three_legs():
    low = price(100, 90, 0.5, 0.05, 0.2, "call")
    mid = price(100, 100, 0.5, 0.05, 0.2, "call")
    high = price(100, 110, 0.5, 0.05, 0.2, "call")
    expected_price = low["price"] - 2 * mid["price"] + high["price"]
    b = butterfly(S=100, K_low=90, K_mid=100, K_high=110, T=0.5, r=0.05, sigma=0.2)
    assert abs(b["price"] - expected_price) < 1e-9


def test_butterfly_price_is_positive_and_bounded():
    # A long butterfly's max possible payoff is (K_mid - K_low), so its
    # price (with positive time value) should be a small positive number
    # well under that bound for reasonable inputs.
    b = butterfly(S=100, K_low=90, K_mid=100, K_high=110, T=0.5, r=0.05, sigma=0.2)
    assert 0 < b["price"] < 10
