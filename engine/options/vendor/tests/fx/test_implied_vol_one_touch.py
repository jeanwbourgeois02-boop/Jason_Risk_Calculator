"""Sanity checks for options_calc.fx.implied_vol's one-touch/no-touch
implied vol solvers -- see equity/test_implied_vol_one_touch.py for the
reasoning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.one_touch import one_touch, no_touch
from options_calc.fx.implied_vol import implied_volatility_one_touch, implied_volatility_no_touch


def test_one_touch_round_trip_recovers_known_volatility():
    true_sigma = 0.08
    priced = one_touch(S=1.10, barrier=1.20, T=0.5, domestic_rate=0.045,
                        foreign_rate=0.0325, sigma=true_sigma, direction="up")
    recovered = implied_volatility_one_touch(
        priced["price"], 1.10, 1.20, 0.5, 0.045, 0.0325, direction="up"
    )
    assert abs(recovered - true_sigma) < 1e-5


def test_no_touch_round_trip_recovers_known_volatility():
    true_sigma = 0.08
    priced = no_touch(S=1.10, barrier=1.20, T=0.5, domestic_rate=0.045,
                       foreign_rate=0.0325, sigma=true_sigma, direction="up")
    recovered = implied_volatility_no_touch(
        priced["price"], 1.10, 1.20, 0.5, 0.045, 0.0325, direction="up"
    )
    assert abs(recovered - true_sigma) < 1e-5
