"""Sanity checks for options_calc.equity.european against known reference values."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.european import price


def test_call_matches_closed_form_black_scholes():
    # S=100, K=105, T=0.5y, r=5%, sigma=20% -> independently computed
    # closed-form Black-Scholes value (Actual365Fixed day count, matching
    # QuantLib's date-based convention: T=183/365, not exactly 0.5)
    result = price(100, 105, 0.5, 0.05, 0.2, "call")
    assert abs(result["price"] - 4.592213) < 0.001


def test_put_matches_closed_form_black_scholes():
    result = price(100, 105, 0.5, 0.05, 0.2, "put")
    assert abs(result["price"] - 6.992740) < 0.001


def test_put_call_parity_holds():
    call = price(100, 100, 1.0, 0.03, 0.25, "call")
    put = price(100, 100, 1.0, 0.03, 0.25, "put")
    # C - P = S - K*exp(-rT)
    lhs = call["price"] - put["price"]
    rhs = 100 - 100 * math.exp(-0.03 * 1.0)
    assert abs(lhs - rhs) < 1e-6


def test_call_delta_between_zero_and_one():
    result = price(100, 105, 0.5, 0.05, 0.2, "call")
    assert 0 <= result["delta"] <= 1


def test_put_delta_between_minus_one_and_zero():
    result = price(100, 105, 0.5, 0.05, 0.2, "put")
    assert -1 <= result["delta"] <= 0


def test_deep_itm_call_delta_approaches_one():
    result = price(1000, 100, 0.5, 0.05, 0.2, "call")
    assert result["delta"] > 0.99


def test_deep_otm_call_delta_approaches_zero():
    result = price(10, 1000, 0.5, 0.05, 0.2, "call")
    assert result["delta"] < 0.01
