"""Sanity checks for options_calc.fx.rate_curves.

These test the plumbing (lookup, override, pair resolution) only -- the
actual rate VALUES are explicitly illustrative placeholders (see the
module docstring), not something to assert as "correct" in any market
sense.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.rate_curves import get_rate, set_rate, get_domestic_and_foreign_rates


def test_get_rate_rejects_unknown_currency():
    try:
        get_rate("XXX")
        assert False, "expected a ValueError for a non-G10 currency"
    except ValueError:
        pass


def test_set_rate_overrides_the_lookup():
    original = get_rate("USD")
    try:
        set_rate("USD", 0.0999)
        assert get_rate("USD") == 0.0999
    finally:
        set_rate("USD", original)  # restore so other tests aren't affected


def test_get_domestic_and_foreign_rates_matches_pair_orientation():
    # EURUSD: domestic=USD, foreign=EUR (per g10.py's convention)
    domestic_rate, foreign_rate = get_domestic_and_foreign_rates("EURUSD")
    assert domestic_rate == get_rate("USD")
    assert foreign_rate == get_rate("EUR")


def test_get_domestic_and_foreign_rates_opposite_orientation_for_usdjpy():
    # USDJPY: domestic=JPY, foreign=USD -- the reverse orientation
    domestic_rate, foreign_rate = get_domestic_and_foreign_rates("USDJPY")
    assert domestic_rate == get_rate("JPY")
    assert foreign_rate == get_rate("USD")


def test_rates_feed_directly_into_the_fx_pricer():
    from options_calc.fx.european import price

    domestic_rate, foreign_rate = get_domestic_and_foreign_rates("EURUSD")
    result = price(S=1.09, K=1.10, T=0.25, domestic_rate=domestic_rate,
                    foreign_rate=foreign_rate, sigma=0.08, option_type="call")
    assert result["price"] > 0
