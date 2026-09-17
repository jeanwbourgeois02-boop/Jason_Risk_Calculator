"""Sanity checks for the American implied volatility solver added to
options_calc.fx.implied_vol -- see equity/test_implied_vol_american.py for
the reasoning behind each check (this mirrors it for FX/Garman-Kohlhagen)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.american import price as tree_price
from options_calc._baw_engine import price as baw_price
from options_calc.fx.implied_vol import implied_volatility_american


def test_round_trip_recovers_known_volatility():
    true_sigma = 0.08
    priced = baw_price(1.10, 1.10, 0.5, 0.045, true_sigma, "call", dividend_rate=0.0325)
    recovered = implied_volatility_american(
        priced, 1.10, 1.10, 0.5, 0.045, 0.0325, "call"
    )
    # See tests/equity/test_implied_vol_american.py for why this
    # tolerance is looser than the European/digital/barrier round-trips.
    assert abs(recovered - true_sigma) < 1e-3


def test_round_trip_recovers_known_volatility_put():
    true_sigma = 0.10
    priced = baw_price(1.10, 1.00, 1.0, 0.045, true_sigma, "put", dividend_rate=0.0325)
    recovered = implied_volatility_american(
        priced, 1.10, 1.00, 1.0, 0.045, 0.0325, "put"
    )
    assert abs(recovered - true_sigma) < 1e-3


def test_higher_market_price_implies_higher_volatility():
    low_price = baw_price(1.10, 1.10, 0.5, 0.045, 0.06, "put", dividend_rate=0.0325)
    high_price = baw_price(1.10, 1.10, 0.5, 0.045, 0.16, "put", dividend_rate=0.0325)
    low_iv = implied_volatility_american(low_price, 1.10, 1.10, 0.5, 0.045, 0.0325, "put")
    high_iv = implied_volatility_american(high_price, 1.10, 1.10, 0.5, 0.045, 0.0325, "put")
    assert high_iv > low_iv


# --- BAW vs. tree cross-check -----------------------------------------
#
# Percentage differences observed when this test was written
# ((baw - tree) / tree), across varied strikes/maturities:
#
#   call  ATM      T=0.5 : +0.028%
#   put   ATM      T=0.5 : -0.043%
#   put   OTM      T=1.0 : +2.134%
#   call  OTM      T=1.0 : +0.167%
#   put   ATM      T=2.0 : +0.723%
#
# All within ~2.2% for these FX cases -- comparable to, and generally
# slightly tighter than, the equity cross-check (see
# tests/equity/test_implied_vol_american.py), likely because these test
# cases don't push into the very-long-dated/deep-OTM regime where BAW is
# known to degrade most. That degradation is a property of the BAW
# approximation itself (not something FX- vs equity-specific), so it
# applies here too even though these particular cases don't exercise it.

_CROSS_CHECK_CASES = [
    # (S, K, T, domestic_rate, foreign_rate, sigma, option_type)
    (1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call"),
    (1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "put"),
    (1.10, 1.00, 1.0, 0.045, 0.0325, 0.10, "put"),
    (1.10, 1.25, 1.0, 0.045, 0.0325, 0.10, "call"),
    (1.10, 1.10, 2.0, 0.045, 0.0325, 0.12, "put"),
]


def test_baw_price_is_close_to_tree_price_across_varied_cases():
    for S, K, T, rd, rf, sigma, option_type in _CROSS_CHECK_CASES:
        tree = tree_price(S, K, T, rd, rf, sigma, option_type)["price"]
        baw = baw_price(S, K, T, rd, sigma, option_type, dividend_rate=rf)
        rel_diff = abs(baw - tree) / tree
        assert rel_diff < 0.03, (
            f"BAW vs tree diverged more than expected for S={S} K={K} T={T} "
            f"{option_type}: tree={tree:.6f} baw={baw:.6f} "
            f"({rel_diff * 100:.3f}%)"
        )
