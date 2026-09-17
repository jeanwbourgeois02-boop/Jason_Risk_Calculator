"""Sanity checks for options_calc.fx.structures.iron_condor -- see
equity/test_iron_condor.py for the reasoning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.structures import iron_condor


def test_iron_condor_is_short_gamma_and_vega():
    ic = iron_condor(S=1.10, K1=1.00, K2=1.07, K3=1.13, K4=1.20, T=0.5,
                      domestic_rate=0.045, foreign_rate=0.0325, sigma=0.08)
    assert ic["gamma"] < 0
    assert ic["vega"] < 0
