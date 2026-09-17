"""Sanity checks for options_calc.fx._conventions.add_forward_delta,
verified across every fx pricer it's wired into."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.european import price as european_price
from options_calc.fx.american import price as american_price
from options_calc.fx.digital import price as digital_price
from options_calc.fx.structures import straddle


def test_forward_delta_matches_formula_for_european():
    result = european_price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    expected = result["delta"] * math.exp(0.0325 * 0.5)
    assert abs(result["delta_forward"] - expected) < 1e-12


def test_forward_delta_premium_adjusted_matches_formula():
    result = european_price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    expected = result["delta_premium_adjusted"] * math.exp(0.0325 * 0.5)
    assert abs(result["delta_forward_premium_adjusted"] - expected) < 1e-12


def test_forward_delta_is_larger_magnitude_than_spot_delta_for_positive_foreign_rate():
    # Forward delta removes the exp(-r_f*T) discount factor, so with a
    # positive foreign rate, |forward delta| > |spot delta|.
    result = european_price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    assert abs(result["delta_forward"]) > abs(result["delta"])


def test_forward_delta_present_on_american_and_digital():
    american = american_price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    digital = digital_price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    assert "delta_forward" in american
    assert "delta_forward" in digital


def test_forward_delta_propagates_through_structures():
    result = straddle(S=1.10, K=1.10, T=0.5, domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    assert "delta_forward" in result
    assert "delta_forward_premium_adjusted" in result
