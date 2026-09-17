"""Sanity checks for the call_spread/put_spread/butterfly structures
added to options_calc.fx.structures -- see
equity/test_structures_extended.py for the reasoning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.european import price
from options_calc.fx.structures import call_spread, put_spread, butterfly


def test_call_spread_cheaper_than_outright_call():
    spread = call_spread(S=1.10, K_low=1.10, K_high=1.15, T=0.5, domestic_rate=0.045,
                          foreign_rate=0.0325, sigma=0.08)
    outright = price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    assert spread["price"] < outright["price"]
    assert spread["price"] > 0


def test_butterfly_is_short_gamma_and_vega():
    b = butterfly(S=1.10, K_low=1.05, K_mid=1.10, K_high=1.15, T=0.5,
                   domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    assert b["gamma"] < 0
    assert b["vega"] < 0
