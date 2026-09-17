"""Sanity checks for options_calc.fx.g10."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.european import price
from options_calc.fx.g10 import (
    pair_convention,
    domestic_and_foreign_currency,
    recommended_delta,
)


def test_eurusd_has_eur_as_base_usd_as_quote():
    convention = pair_convention("EURUSD")
    assert convention["base"] == "EUR"
    assert convention["quote"] == "USD"


def test_usdjpy_has_usd_as_base_jpy_as_quote():
    # The opposite orientation from EURUSD -- USD is base here, not quote.
    convention = pair_convention("USDJPY")
    assert convention["base"] == "USD"
    assert convention["quote"] == "JPY"


def test_domestic_and_foreign_currency_matches_package_convention():
    # domestic = quote currency, foreign = base currency, matching every
    # fx/*.py pricer's domestic_rate / foreign_rate arguments.
    assert domestic_and_foreign_currency("EURUSD") == ("USD", "EUR")
    assert domestic_and_foreign_currency("USDJPY") == ("JPY", "USD")


def test_accepts_slash_and_lowercase_input():
    assert pair_convention("eur/usd") == pair_convention("EURUSD")


def test_rejects_unknown_pair():
    try:
        pair_convention("EURTRY")
        assert False, "expected a ValueError for a non-G10 pair"
    except ValueError:
        pass


def test_recommended_delta_uses_premium_adjusted_for_eur_base_pairs():
    result = price(1.09, 1.10, 0.25, 0.045, 0.0325, 0.08, "call")
    assert recommended_delta(result, "EURUSD") == result["delta_premium_adjusted"]


def test_recommended_delta_uses_raw_delta_for_usd_base_pairs():
    result = price(1.09, 1.10, 0.25, 0.045, 0.0325, 0.08, "call")
    assert recommended_delta(result, "USDJPY") == result["delta"]


def test_all_nine_standard_g10_usd_pairs_are_covered():
    expected = {
        "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD",
        "USDJPY", "USDCHF", "USDCAD", "USDSEK", "USDNOK",
    }
    for pair in expected:
        convention = pair_convention(pair)
        assert "USD" in (convention["base"], convention["quote"])


def test_all_45_g10_pairs_are_covered():
    # 10 currencies choose 2 = 45 unique pairs
    from options_calc.fx.g10 import _ALL_G10_PAIRS
    assert len(_ALL_G10_PAIRS) == 45


def test_eur_outranks_gbp_as_base_in_cross():
    convention = pair_convention("EURGBP")
    assert convention["base"] == "EUR"
    assert convention["quote"] == "GBP"


def test_jpy_is_always_quote_currency_in_crosses():
    for pair in ["EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY", "SEKJPY", "NOKJPY"]:
        convention = pair_convention(pair)
        assert convention["quote"] == "JPY"


def test_reversed_orientation_is_rejected():
    # Only the canonical orientation (e.g. EURUSD, not USDEUR) is a valid key.
    try:
        pair_convention("USDEUR")
        assert False, "expected a ValueError for the reversed orientation"
    except ValueError:
        pass


def test_domestic_and_foreign_currency_works_for_a_cross():
    # EUR/GBP: EUR is base/foreign, GBP is quote/domestic
    assert domestic_and_foreign_currency("EURGBP") == ("GBP", "EUR")
