"""Sanity checks for options_calc.structures (the generic combine()) --
asset-class-specific structure builders are tested in
tests/equity/test_structures.py and tests/fx/test_structures.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from options_calc.structures import combine


def test_combine_sums_long_legs():
    leg_a = {"price": 10, "delta": 0.5, "gamma": 0.01, "theta": -0.1, "vega": 1.0, "rho": 0.2}
    leg_b = {"price": 5, "delta": -0.3, "gamma": 0.02, "theta": -0.05, "vega": 0.5, "rho": 0.1}
    result = combine((leg_a, 1.0), (leg_b, 1.0))
    assert result["price"] == 15
    assert abs(result["delta"] - 0.2) < 1e-12
    assert abs(result["gamma"] - 0.03) < 1e-12


def test_combine_subtracts_short_legs():
    leg_a = {"price": 10, "delta": 0.5, "gamma": 0.01, "theta": -0.1, "vega": 1.0, "rho": 0.2}
    leg_b = {"price": 5, "delta": -0.3, "gamma": 0.02, "theta": -0.05, "vega": 0.5, "rho": 0.1}
    result = combine((leg_a, 1.0), (leg_b, -1.0))
    assert result["price"] == 5
    assert abs(result["delta"] - 0.8) < 1e-12


def test_combine_handles_arbitrary_quantities():
    leg = {"price": 10, "delta": 0.5, "gamma": 0.01, "theta": -0.1, "vega": 1.0, "rho": 0.2}
    result = combine((leg, 3.0))
    assert result["price"] == 30
    assert abs(result["delta"] - 1.5) < 1e-12
