"""Sanity checks for options_calc.commodity.structures."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.commodity.european import price
from options_calc.commodity.structures import (
    straddle, strangle, risk_reversal, collar, call_spread, put_spread, butterfly,
)


def test_straddle_equals_sum_of_its_two_legs():
    result = straddle(F=1950, K=1950, T=0.5, r=0.05, sigma=0.18)
    call = price(1950, 1950, 0.5, 0.05, 0.18, "call")
    put = price(1950, 1950, 0.5, 0.05, 0.18, "put")
    assert abs(result["price"] - (call["price"] + put["price"])) < 1e-9


def test_straddle_is_long_gamma_and_vega():
    result = straddle(F=1950, K=1950, T=0.5, r=0.05, sigma=0.18)
    assert result["gamma"] > 0
    assert result["vega"] > 0


def test_strangle_cheaper_than_straddle():
    strad = straddle(F=1950, K=1950, T=0.5, r=0.05, sigma=0.18)
    strang = strangle(F=1950, K_put=1900, K_call=2000, T=0.5, r=0.05, sigma=0.18)
    assert strang["price"] < strad["price"]


def test_bullish_risk_reversal_has_positive_delta():
    result = risk_reversal(F=1950, K_put=1900, K_call=2000, T=0.5, r=0.05, sigma=0.18, long_call=True)
    assert result["delta"] > 0


def test_collar_reduces_net_premium_versus_the_put_alone():
    put_alone = price(1950, 1900, 0.5, 0.05, 0.18, "put")
    collared = collar(F=1950, K_put=1900, K_call=2000, T=0.5, r=0.05, sigma=0.18)
    assert collared["price"] < put_alone["price"]


def test_call_spread_cheaper_than_outright_call():
    spread = call_spread(F=1950, K_low=1950, K_high=2000, T=0.5, r=0.05, sigma=0.18)
    outright = price(1950, 1950, 0.5, 0.05, 0.18, "call")
    assert 0 < spread["price"] < outright["price"]


def test_butterfly_is_short_gamma_and_vega():
    result = butterfly(F=1950, K_low=1900, K_mid=1950, K_high=2000, T=0.5, r=0.05, sigma=0.18)
    assert result["gamma"] < 0
    assert result["vega"] < 0
