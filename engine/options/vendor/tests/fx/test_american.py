"""Sanity checks for options_calc.fx.american -- see equity/test_american.py
for why these check structural properties rather than a fixed reference
price."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.american import price as american
from options_calc.fx.european import price as european


def test_american_call_at_least_as_valuable_as_european_call():
    # With a positive foreign rate (r_f), early exercise of an FX call can
    # be optimal (mirrors the dividend case for equities), so American
    # should be worth at least as much as European.
    american_result = american(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    european_result = european(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    assert american_result["price"] >= european_result["price"] - 0.001


def test_american_put_at_least_as_valuable_as_european_put():
    american_result = american(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "put")
    european_result = european(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "put")
    assert american_result["price"] >= european_result["price"] - 0.001


def test_call_delta_between_zero_and_one():
    result = american(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    assert 0 <= result["delta"] <= 1
