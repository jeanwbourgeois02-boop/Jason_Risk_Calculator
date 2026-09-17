"""Sanity checks for options_calc.fx.asian -- see equity/test_asian.py for
why these check structural properties rather than a fixed reference price."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.asian import price as asian
from options_calc.fx.european import price as european


def test_asian_call_cheaper_than_european_call():
    asian_result = asian(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    european_result = european(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    assert asian_result["price"] < european_result["price"]


def test_price_non_negative():
    result = asian(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    assert result["price"] >= 0
