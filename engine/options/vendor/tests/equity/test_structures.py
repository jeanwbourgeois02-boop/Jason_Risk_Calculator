"""Sanity checks for options_calc.equity.structures."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.european import price
from options_calc.equity.structures import straddle, strangle, risk_reversal, collar


def test_straddle_equals_sum_of_its_two_legs():
    result = straddle(S=5500, K=5500, T=0.25, r=0.045, sigma=0.15)
    call = price(5500, 5500, 0.25, 0.045, 0.15, "call")
    put = price(5500, 5500, 0.25, 0.045, 0.15, "put")
    assert abs(result["price"] - (call["price"] + put["price"])) < 1e-9
    assert abs(result["delta"] - (call["delta"] + put["delta"])) < 1e-9


def test_straddle_is_long_gamma_and_vega():
    # A long straddle is a pure bet on a big move -- it must be long
    # gamma and long vega (both legs are long options, so both add).
    result = straddle(S=5500, K=5500, T=0.25, r=0.045, sigma=0.15)
    assert result["gamma"] > 0
    assert result["vega"] > 0


def test_strangle_cheaper_than_straddle_same_underlying():
    # Both legs of a strangle start out-of-the-money, so it must cost
    # less than an at-the-money straddle on the same underlying/expiry.
    strad = straddle(S=5500, K=5500, T=0.25, r=0.045, sigma=0.15)
    strang = strangle(S=5500, K_put=5300, K_call=5700, T=0.25, r=0.045, sigma=0.15)
    assert strang["price"] < strad["price"]


def test_bullish_risk_reversal_has_positive_delta():
    result = risk_reversal(S=5500, K_put=5300, K_call=5700, T=0.25, r=0.045, sigma=0.15, long_call=True)
    assert result["delta"] > 0


def test_bearish_risk_reversal_has_negative_delta():
    result = risk_reversal(S=5500, K_put=5300, K_call=5700, T=0.25, r=0.045, sigma=0.15, long_call=False)
    assert result["delta"] < 0


def test_collar_reduces_net_premium_versus_the_put_alone():
    # The short call finances part of the put's cost, so a collar's net
    # price must be less than buying the protective put outright.
    put_alone = price(5500, 5300, 0.25, 0.045, 0.15, "put")
    collared = collar(S=5500, K_put=5300, K_call=5700, T=0.25, r=0.045, sigma=0.15)
    assert collared["price"] < put_alone["price"]
