"""Sanity checks for options_calc.equity.american.

No simple closed-form reference exists for American options, so these
tests check known structural properties instead of matching a fixed number:
  - with zero dividends, an American call is never optimal to exercise
    early, so it must price the same as its European counterpart
  - an American put must be worth at least as much as its European
    counterpart (early exercise can only add value, never remove it)
  - an American option must be worth at least its intrinsic value
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.american import price as american
from options_calc.equity.european import price as european


def test_zero_dividend_american_call_matches_european_call():
    american_result = american(100, 105, 0.5, 0.05, 0.2, "call", dividend_yield=0.0)
    european_result = european(100, 105, 0.5, 0.05, 0.2, "call", dividend_yield=0.0)
    assert abs(american_result["price"] - european_result["price"]) < 0.05


def test_american_put_at_least_as_valuable_as_european_put():
    american_result = american(100, 105, 0.5, 0.05, 0.2, "put", dividend_yield=0.0)
    european_result = european(100, 105, 0.5, 0.05, 0.2, "put", dividend_yield=0.0)
    assert american_result["price"] >= european_result["price"] - 0.05


def test_deep_itm_put_at_least_intrinsic_value():
    result = american(50, 105, 0.5, 0.05, 0.2, "put", dividend_yield=0.0)
    intrinsic = 105 - 50
    assert result["price"] >= intrinsic - 0.05


def test_call_delta_between_zero_and_one():
    result = american(100, 105, 0.5, 0.05, 0.2, "call", dividend_yield=0.0)
    assert 0 <= result["delta"] <= 1
