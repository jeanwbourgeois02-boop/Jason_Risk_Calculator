"""Sanity checks for options_calc.fx.structures -- see
equity/test_structures.py for the full explanation of each structure."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.european import price
from options_calc.fx.structures import straddle, strangle, risk_reversal, collar


def test_straddle_equals_sum_of_its_two_legs():
    result = straddle(S=1.10, K=1.10, T=0.25, domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    call = price(1.10, 1.10, 0.25, 0.045, 0.0325, 0.08, "call")
    put = price(1.10, 1.10, 0.25, 0.045, 0.0325, 0.08, "put")
    assert abs(result["price"] - (call["price"] + put["price"])) < 1e-12


def test_straddle_is_long_gamma_and_vega():
    result = straddle(S=1.10, K=1.10, T=0.25, domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    assert result["gamma"] > 0
    assert result["vega"] > 0


def test_strangle_cheaper_than_straddle():
    strad = straddle(S=1.10, K=1.10, T=0.25, domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    strang = strangle(S=1.10, K_put=1.08, K_call=1.12, T=0.25, domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    assert strang["price"] < strad["price"]


def test_bullish_risk_reversal_has_positive_delta():
    result = risk_reversal(S=1.10, K_put=1.08, K_call=1.12, T=0.25, domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08, long_call=True)
    assert result["delta"] > 0


def test_collar_reduces_net_premium_versus_the_put_alone():
    put_alone = price(1.10, 1.08, 0.25, 0.045, 0.0325, 0.08, "put")
    collared = collar(S=1.10, K_put=1.08, K_call=1.12, T=0.25, domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    assert collared["price"] < put_alone["price"]
