"""Sanity checks for options_calc.commodity.asian."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.commodity.asian import price as asian
from options_calc.commodity.european import price as european


def test_asian_call_cheaper_than_european_call():
    asian_result = asian(1950, 2000, 0.5, 0.05, 0.18, "call")
    european_result = european(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert asian_result["price"] < european_result["price"]


def test_price_non_negative():
    result = asian(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert result["price"] >= 0


def test_rho_dividend_field_is_not_exposed():
    result = asian(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert "rho_dividend" not in result
