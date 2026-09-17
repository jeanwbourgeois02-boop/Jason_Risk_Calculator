"""Sanity checks for options_calc.commodity.american.

Unlike equity's zero-dividend case (where only puts have early-exercise
value), a futures option under Black-76 has zero NET cost-of-carry
regardless of option type, so early exercise can be optimal for BOTH
calls and puts -- American must be >= European for both, not just puts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.commodity.american import price as american
from options_calc.commodity.european import price as european


def test_american_call_at_least_as_valuable_as_european_call():
    american_result = american(1950, 2000, 0.5, 0.05, 0.18, "call")
    european_result = european(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert american_result["price"] >= european_result["price"] - 0.01


def test_american_put_at_least_as_valuable_as_european_put():
    american_result = american(1950, 2000, 0.5, 0.05, 0.18, "put")
    european_result = european(1950, 2000, 0.5, 0.05, 0.18, "put")
    assert american_result["price"] >= european_result["price"] - 0.01


def test_call_delta_between_zero_and_one():
    result = american(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert 0 <= result["delta"] <= 1


def test_rho_dividend_field_is_not_exposed():
    result = american(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert "rho_dividend" not in result
