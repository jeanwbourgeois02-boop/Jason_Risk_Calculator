"""Sanity checks for options_calc.equity.implied_vol.implied_volatility_asian.

Priced against the Turnbull-Wakeman approximation, not the Monte Carlo
pricer -- these tests validate that solver, and separately document (not
assert away) the real, expected discrepancy against Monte Carlo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc._asian_approx_engine import price as tw_price
from options_calc.equity.asian import price as mc_price
from options_calc.equity.implied_vol import implied_volatility_asian


def test_round_trip_recovers_known_volatility_against_turnbull_wakeman():
    true_sigma = 0.2
    priced = tw_price(100, 105, 0.5, 0.05, true_sigma, "call", 0.0, 12)
    recovered = implied_volatility_asian(priced, 100, 105, 0.5, 0.05, "call", n_fixings=12)
    assert abs(recovered - true_sigma) < 1e-6


def test_higher_price_implies_higher_volatility():
    low_vol_price = tw_price(100, 105, 0.5, 0.05, 0.10, "call", 0.0, 12)
    high_vol_price = tw_price(100, 105, 0.5, 0.05, 0.30, "call", 0.0, 12)
    low_iv = implied_volatility_asian(low_vol_price, 100, 105, 0.5, 0.05, "call", n_fixings=12)
    high_iv = implied_volatility_asian(high_vol_price, 100, 105, 0.5, 0.05, "call", n_fixings=12)
    assert high_iv > low_iv


def test_turnbull_wakeman_and_monte_carlo_agree_reasonably_but_not_exactly():
    # Documents the real, expected gap between the two engines rather than
    # asserting a false exact match -- see _asian_approx_engine.py's
    # docstring for the measured ~1.9% discrepancy on this exact case.
    tw = tw_price(100, 105, 0.5, 0.05, 0.2, "call", 0.0, 12)
    mc = mc_price(100, 105, 0.5, 0.05, 0.2, "call", n_fixings=12)["price"]
    relative_diff = abs(tw - mc) / mc
    assert relative_diff < 0.05  # within 5%, but not asserting near-zero
