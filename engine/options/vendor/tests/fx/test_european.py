"""Sanity checks for options_calc.fx.european against known reference values."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.european import price


def test_call_matches_closed_form_garman_kohlhagen():
    # EUR/USD spot 1.10, strike 1.10, 1y, domestic (USD) rate 5%,
    # foreign (EUR) rate 3%, vol 10% -> independently computed closed-form
    # Garman-Kohlhagen value
    result = price(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    assert abs(result["price"] - 0.053556) < 0.0005


def test_put_call_parity_holds():
    call = price(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    put = price(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "put")
    # C - P = S*exp(-r_f*T) - K*exp(-r_d*T)
    lhs = call["price"] - put["price"]
    rhs = 1.10 * math.exp(-0.03 * 1.0) - 1.10 * math.exp(-0.05 * 1.0)
    assert abs(lhs - rhs) < 1e-6


def test_rho_and_rho_foreign_are_distinct_and_opposite_sign_for_call():
    # Raising the domestic rate helps a call (higher rho); raising the
    # foreign rate hurts it (negative rho_foreign), since a higher foreign
    # rate discounts the foreign-currency leg of the payoff more heavily.
    result = price(1.10, 1.10, 1.0, 0.05, 0.03, 0.10, "call")
    assert result["rho"] > 0
    assert result["rho_foreign"] < 0
    assert result["rho"] != result["rho_foreign"]


def test_rho_foreign_matches_bumping_foreign_rate_directly():
    base = price(1.10, 1.10, 0.25, 0.045, 0.0325, 0.08, "call")
    bumped = price(1.10, 1.10, 0.25, 0.045, 0.0425, 0.08, "call")
    approx_rho_foreign = (bumped["price"] - base["price"]) / 0.01 / 100
    assert abs(base["rho_foreign"] - approx_rho_foreign) < 0.001


def test_delta_premium_adjusted_matches_formula():
    result = price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    expected = result["delta"] - result["price"] / 1.10
    assert abs(result["delta_premium_adjusted"] - expected) < 1e-12
