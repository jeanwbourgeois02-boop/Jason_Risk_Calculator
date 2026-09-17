"""Sanity checks for options_calc.rates.cap_floor.

Same philosophy as tests/rates/test_swaption.py: structural/identity
checks instead of fixed reference numbers, for the usual day-count-
rounding reason (see that file's docstring)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.rates.cap_floor import price_cap, price_floor


def test_cap_price_increases_with_volatility():
    low_vol = price_cap(0.04, 1.0, 5, 0.04, 0.10)
    high_vol = price_cap(0.04, 1.0, 5, 0.04, 0.30)
    assert high_vol["price"] > low_vol["price"]


def test_floor_price_increases_with_volatility():
    low_vol = price_floor(0.04, 1.0, 5, 0.04, 0.10)
    high_vol = price_floor(0.04, 1.0, 5, 0.04, 0.30)
    assert high_vol["price"] > low_vol["price"]


def test_longer_tenor_cap_is_worth_more():
    """More caplets (a longer strip, same per-period terms) can only add
    non-negative value -- extending a 5y cap to 10y just appends more
    optional periods on top of the original ones, none of which can have
    negative value. Strictly increasing in almost every realistic case
    (it's non-strict only in the pathological case of a zero-value added
    period), so a strict '>' is a safe check here."""
    shorter = price_cap(0.04, 1.0, 5, 0.04, 0.20)
    longer = price_cap(0.04, 1.0, 10, 0.04, 0.20)
    assert longer["price"] > shorter["price"]


def test_longer_tenor_floor_is_worth_more():
    shorter = price_floor(0.04, 1.0, 5, 0.04, 0.20)
    longer = price_floor(0.04, 1.0, 10, 0.04, 0.20)
    assert longer["price"] > shorter["price"]


def test_higher_strike_cap_is_cheaper():
    """A cap struck higher pays off less often / by less, so raising the
    strike can only reduce its value (mirrors a call option's price being
    decreasing in strike)."""
    low_strike = price_cap(0.02, 1.0, 5, 0.04, 0.20)
    high_strike = price_cap(0.06, 1.0, 5, 0.04, 0.20)
    assert high_strike["price"] < low_strike["price"]


def test_higher_strike_floor_is_more_expensive():
    """A floor struck higher pays off more often / by more, so raising
    the strike can only increase its value (mirrors a put option's price
    being increasing in strike)."""
    low_strike = price_floor(0.02, 1.0, 5, 0.04, 0.20)
    high_strike = price_floor(0.06, 1.0, 5, 0.04, 0.20)
    assert high_strike["price"] > low_strike["price"]


def test_prices_are_positive():
    cap = price_cap(0.04, 1.0, 5, 0.04, 0.20)
    floor = price_floor(0.04, 1.0, 5, 0.04, 0.20)
    assert cap["price"] > 0
    assert floor["price"] > 0
