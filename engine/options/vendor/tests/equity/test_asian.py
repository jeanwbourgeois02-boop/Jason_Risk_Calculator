"""Sanity checks for options_calc.equity.asian.

Priced by Monte Carlo, so exact-value assertions aren't meaningful (the
same inputs can give slightly different results run to run, or across
QuantLib versions, even with a fixed seed). These check structural
properties instead:
  - an Asian call must be worth less than a vanilla European call with the
    same strike (averaging reduces volatility exposure relative to a
    point-in-time payoff, so Asian options are always cheaper)
  - price must be non-negative
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.asian import price as asian
from options_calc.equity.european import price as european


def test_asian_call_cheaper_than_european_call():
    asian_result = asian(100, 105, 0.5, 0.05, 0.2, "call")
    european_result = european(100, 105, 0.5, 0.05, 0.2, "call")
    assert asian_result["price"] < european_result["price"]


def test_price_non_negative():
    result = asian(100, 105, 0.5, 0.05, 0.2, "call")
    assert result["price"] >= 0
