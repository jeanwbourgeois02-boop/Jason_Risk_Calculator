"""Sanity checks for options_calc.commodity.structures.iron_condor -- see
equity/test_iron_condor.py for the reasoning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.commodity.structures import iron_condor


def test_iron_condor_is_short_gamma_and_vega():
    ic = iron_condor(F=1950, K1=1800, K2=1900, K3=2000, K4=2100, T=0.5, r=0.05, sigma=0.18)
    assert ic["gamma"] < 0
    assert ic["vega"] < 0
