"""Sanity checks for options_calc.fx.implied_vol.implied_volatility_asian
-- see equity/test_implied_vol_asian.py for the reasoning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc._asian_approx_engine import price as tw_price
from options_calc.fx.implied_vol import implied_volatility_asian


def test_round_trip_recovers_known_volatility_against_turnbull_wakeman():
    true_sigma = 0.08
    priced = tw_price(1.10, 1.10, 0.5, 0.045, true_sigma, "call", 0.0325, 12)
    recovered = implied_volatility_asian(
        priced, 1.10, 1.10, 0.5, 0.045, 0.0325, "call", n_fixings=12
    )
    assert abs(recovered - true_sigma) < 1e-6
