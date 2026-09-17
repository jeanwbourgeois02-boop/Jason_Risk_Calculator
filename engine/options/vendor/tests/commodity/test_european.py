"""Sanity checks for options_calc.commodity.european (Black-76)."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.commodity.european import price


def test_call_matches_independently_computed_black76():
    # F=1950, K=2000, T=0.5y (183/365, matching QuantLib's day-count
    # rounding -- see equity/test_european.py for why), r=5%, vol=18%
    from scipy.stats import norm
    F, K, r, sigma = 1950, 2000, 0.05, 0.18
    T = 183 / 365
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    expected = math.exp(-r * T) * (F * norm.cdf(d1) - K * norm.cdf(d2))

    result = price(F, K, 0.5, r, sigma, "call")
    assert abs(result["price"] - expected) < 0.01


def test_put_call_parity_holds():
    # For Black-76: C - P = exp(-rT) * (F - K). Loose tolerance: QuantLib
    # rounds T=0.5 years to a whole number of days (183, not 182.5) --
    # see equity/test_european.py for the same day-count issue.
    F, K, T, r, sigma = 1950, 2000, 0.5, 0.05, 0.18
    call = price(F, K, T, r, sigma, "call")
    put = price(F, K, T, r, sigma, "put")
    lhs = call["price"] - put["price"]
    rhs = math.exp(-r * T) * (F - K)
    assert abs(lhs - rhs) < 0.005


def test_call_delta_between_zero_and_one():
    result = price(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert 0 <= result["delta"] <= 1


def test_rho_matches_direct_finite_difference():
    # The corrected 'rho' (sum of the two raw engine partials) should
    # match a direct bump of r with dividend_rate tied to it, confirming
    # the fix documented in european.py's docstring is actually correct.
    base = price(1950, 2000, 0.5, 0.05, 0.18, "call")
    bumped = price(1950, 2000, 0.5, 0.06, 0.18, "call")
    approx_rho = (bumped["price"] - base["price"]) / 0.01 / 100
    assert abs(base["rho"] - approx_rho) < 0.01


def test_rho_dividend_field_is_not_exposed():
    # rho_dividend doesn't correspond to a real, independent risk in a
    # single-rate model -- it should be folded into 'rho', not exposed.
    result = price(1950, 2000, 0.5, 0.05, 0.18, "call")
    assert "rho_dividend" not in result
