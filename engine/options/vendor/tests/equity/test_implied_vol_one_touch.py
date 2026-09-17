"""Sanity checks for options_calc.equity.implied_vol's one-touch/no-touch
implied vol solvers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.one_touch import one_touch, no_touch
from options_calc.equity.implied_vol import implied_volatility_one_touch, implied_volatility_no_touch


def test_one_touch_round_trip_recovers_known_volatility():
    true_sigma = 0.2
    priced = one_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=true_sigma, direction="up")
    recovered = implied_volatility_one_touch(priced["price"], 100, 120, 0.5, 0.05, direction="up")
    assert abs(recovered - true_sigma) < 1e-5


def test_no_touch_round_trip_recovers_known_volatility():
    true_sigma = 0.2
    priced = no_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=true_sigma, direction="up")
    recovered = implied_volatility_no_touch(priced["price"], 100, 120, 0.5, 0.05, direction="up")
    assert abs(recovered - true_sigma) < 1e-5


def test_one_touch_higher_price_implies_higher_volatility():
    # One-touch price is monotonically INCREASING in vol (more vol = more
    # likely to touch), so higher price should mean higher implied vol.
    low_vol_price = one_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.15, direction="up")["price"]
    high_vol_price = one_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.35, direction="up")["price"]
    low_iv = implied_volatility_one_touch(low_vol_price, 100, 120, 0.5, 0.05, direction="up")
    high_iv = implied_volatility_one_touch(high_vol_price, 100, 120, 0.5, 0.05, direction="up")
    assert high_iv > low_iv


def test_no_touch_price_is_decreasing_in_volatility():
    # No-touch price is monotonically DECREASING in vol (more vol = more
    # likely to touch = less likely to survive untouched) -- the opposite
    # direction from one-touch, digital, and vanilla options. So the
    # NUMERICALLY HIGHER no-touch price must correspond to the LOWER
    # implied vol, not the higher one.
    price_at_low_vol = no_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.15, direction="up")["price"]
    price_at_high_vol = no_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.35, direction="up")["price"]
    assert price_at_low_vol > price_at_high_vol  # confirms the direction itself

    iv_from_higher_price = implied_volatility_no_touch(price_at_low_vol, 100, 120, 0.5, 0.05, direction="up")
    iv_from_lower_price = implied_volatility_no_touch(price_at_high_vol, 100, 120, 0.5, 0.05, direction="up")
    assert iv_from_higher_price < iv_from_lower_price
