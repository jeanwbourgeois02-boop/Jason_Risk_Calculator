"""Sanity checks for the American implied volatility solver added to
options_calc.equity.implied_vol.

implied_volatility_american() solves against the Barone-Adesi-Whaley
approximation (options_calc/_baw_engine.py), not equity/american.py's
800-step binomial tree -- rebuilding that tree on every Newton iteration
would be far too slow to use as a root-finder's inner pricer. BAW is a
fast, well-known quasi-analytic approximation, but it IS an approximation:
these tests both confirm the solver round-trips internally consistently,
and quantify (not just assert away) how far BAW's price sits from the
tree's price across a range of moneyness/maturity/dividend combinations.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.american import price as tree_price
from options_calc._baw_engine import price as baw_price
from options_calc.equity.implied_vol import implied_volatility_american


def test_round_trip_recovers_known_volatility():
    true_sigma = 0.2
    priced = baw_price(100, 100, 0.5, 0.05, true_sigma, "call", dividend_rate=0.0)
    recovered = implied_volatility_american(priced, 100, 100, 0.5, 0.05, "call", dividend_yield=0.0)
    # Looser tolerance than the European/digital/barrier round-trip tests
    # (which hit 1e-6): QuantLib's impliedVolatility() solver estimates
    # vega by bumping the BAW engine's own price (BAW has no analytic
    # vega), so Newton's method converges to a slightly less precise
    # root here. Still comfortably tight for practical use.
    assert abs(recovered - true_sigma) < 1e-3


def test_round_trip_recovers_known_volatility_put_with_dividend():
    true_sigma = 0.25
    priced = baw_price(100, 100, 1.0, 0.05, true_sigma, "put", dividend_rate=0.03)
    recovered = implied_volatility_american(priced, 100, 100, 1.0, 0.05, "put", dividend_yield=0.03)
    assert abs(recovered - true_sigma) < 1e-3


def test_higher_market_price_implies_higher_volatility():
    low_price = baw_price(100, 100, 0.5, 0.05, 0.15, "put", dividend_rate=0.02)
    high_price = baw_price(100, 100, 0.5, 0.05, 0.35, "put", dividend_rate=0.02)
    low_iv = implied_volatility_american(low_price, 100, 100, 0.5, 0.05, "put", dividend_yield=0.02)
    high_iv = implied_volatility_american(high_price, 100, 100, 0.5, 0.05, "put", dividend_yield=0.02)
    assert high_iv > low_iv


# --- BAW vs. tree cross-check -----------------------------------------
#
# Cases chosen to span moneyness, time-to-expiry, and dividend yield.
# Percentage differences observed when this test was written (BAW vs.
# tree, (baw - tree) / tree):
#
#   call  ATM   T=0.5  div=0.00 : +0.026%
#   put   ATM   T=0.5  div=0.00 : -0.100%
#   put   ATM   T=0.5  div=0.03 : -0.005%
#   put   OTM   T=1.0  div=0.02 : +1.826%
#   call  ITM   T=1.0  div=0.02 : -0.010%
#   put   ATM   T=2.0  div=0.04 : +0.911%
#   put   ATM   T=5.0  div=0.04 : +2.660%  <- long-dated, BAW noticeably looser
#   call  deep OTM T=0.25 div=0.06 : +1.193%
#
# All of the above are within ~3% for T <= 2 years. The T=5 case is the
# largest of the "normal" cases at ~2.7%, consistent with BAW being known
# to degrade for long-dated American options. A separate, deliberately
# extreme deep-OTM put (K=60 on S=100, T=0.25) is documented but NOT
# asserted on a tight relative tolerance: its tree price is ~0.005, so a
# ~0.001 absolute difference reads as a ~17% relative error despite both
# prices being economically negligible. Relative-tolerance tests on
# near-zero prices are not a meaningful way to judge approximation
# quality, so that case is checked on an absolute tolerance instead.

_CROSS_CHECK_CASES = [
    # (S, K, T, r, sigma, option_type, dividend_yield)
    (100, 100, 0.5, 0.05, 0.20, "call", 0.00),
    (100, 100, 0.5, 0.05, 0.20, "put", 0.00),
    (100, 100, 0.5, 0.05, 0.20, "put", 0.03),
    (100, 80, 1.0, 0.05, 0.25, "put", 0.02),
    (100, 120, 1.0, 0.05, 0.25, "call", 0.02),
    (100, 100, 2.0, 0.05, 0.30, "put", 0.04),
]

_LONG_DATED_CASE = (100, 100, 5.0, 0.05, 0.30, "put", 0.04)
_DEEP_OTM_CASE = (100, 60, 0.25, 0.05, 0.35, "put", 0.00)


def test_baw_price_is_close_to_tree_price_across_varied_cases():
    for S, K, T, r, sigma, option_type, div in _CROSS_CHECK_CASES:
        tree = tree_price(S, K, T, r, sigma, option_type, div)["price"]
        baw = baw_price(S, K, T, r, sigma, option_type, dividend_rate=div)
        rel_diff = abs(baw - tree) / tree
        assert rel_diff < 0.03, (
            f"BAW vs tree diverged more than expected for S={S} K={K} T={T} "
            f"div={div} {option_type}: tree={tree:.5f} baw={baw:.5f} "
            f"({rel_diff * 100:.3f}%)"
        )


def test_baw_is_less_accurate_but_still_reasonable_for_long_dated_options():
    # Documented, real limitation: BAW's approximation error grows with
    # time to expiry. This still holds it to a looser, explicit bound
    # rather than silently ignoring the degradation.
    S, K, T, r, sigma, option_type, div = _LONG_DATED_CASE
    tree = tree_price(S, K, T, r, sigma, option_type, div)["price"]
    baw = baw_price(S, K, T, r, sigma, option_type, dividend_rate=div)
    rel_diff = abs(baw - tree) / tree
    assert rel_diff < 0.05


def test_baw_vs_tree_deep_otm_checked_on_absolute_not_relative_tolerance():
    # Both prices are economically negligible (~0.5 cents on a 100 spot),
    # so a tight relative tolerance is the wrong tool here -- see the
    # comment block above.
    S, K, T, r, sigma, option_type, div = _DEEP_OTM_CASE
    tree = tree_price(S, K, T, r, sigma, option_type, div)["price"]
    baw = baw_price(S, K, T, r, sigma, option_type, dividend_rate=div)
    assert abs(baw - tree) < 0.01
