"""Sanity checks for options_calc.equity.structures.iron_condor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.european import price
from options_calc.equity.structures import iron_condor


def test_iron_condor_is_short_gamma_and_vega():
    ic = iron_condor(S=100, K1=80, K2=95, K3=105, K4=120, T=0.5, r=0.05, sigma=0.2)
    assert ic["gamma"] < 0
    assert ic["vega"] < 0


def test_iron_condor_equals_sum_of_its_four_legs():
    long_put = price(100, 80, 0.5, 0.05, 0.2, "put")
    short_put = price(100, 95, 0.5, 0.05, 0.2, "put")
    short_call = price(100, 105, 0.5, 0.05, 0.2, "call")
    long_call = price(100, 120, 0.5, 0.05, 0.2, "call")
    expected_price = (long_put["price"] - short_put["price"]
                       - short_call["price"] + long_call["price"])
    ic = iron_condor(S=100, K1=80, K2=95, K3=105, K4=120, T=0.5, r=0.05, sigma=0.2)
    assert abs(ic["price"] - expected_price) < 1e-9


def test_iron_condor_opens_for_a_net_credit_when_wings_are_far_otm():
    # With K2/K3 near the money and K1/K4 far OTM, the two sold legs are
    # worth more than the two bought wings, so the net price is negative
    # (money received to open the position).
    ic = iron_condor(S=100, K1=70, K2=95, K3=105, K4=130, T=0.5, r=0.05, sigma=0.2)
    assert ic["price"] < 0
