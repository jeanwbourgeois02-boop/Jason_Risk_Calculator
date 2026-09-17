"""Sanity checks for options_calc.fx.one_touch -- see
equity/test_one_touch.py for the reasoning behind each check."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.one_touch import one_touch, no_touch


def test_one_touch_plus_no_touch_equals_discounted_payout():
    ot = one_touch(S=1.10, barrier=1.20, T=0.5, domestic_rate=0.045, foreign_rate=0.0325,
                    sigma=0.08, cash_payout=1.0, direction="up")
    nt = no_touch(S=1.10, barrier=1.20, T=0.5, domestic_rate=0.045, foreign_rate=0.0325,
                   sigma=0.08, cash_payout=1.0, direction="up")
    discounted_payout = math.exp(-0.045 * 0.5)
    assert abs((ot["price"] + nt["price"]) - discounted_payout) < 1e-4


def test_closer_barrier_makes_one_touch_more_valuable():
    near = one_touch(S=1.10, barrier=1.13, T=0.5, domestic_rate=0.045, foreign_rate=0.0325,
                      sigma=0.08, cash_payout=1.0, direction="up")
    far = one_touch(S=1.10, barrier=1.30, T=0.5, domestic_rate=0.045, foreign_rate=0.0325,
                     sigma=0.08, cash_payout=1.0, direction="up")
    assert near["price"] > far["price"]


def test_output_includes_fx_specific_fields():
    ot = one_touch(S=1.10, barrier=1.20, T=0.5, domestic_rate=0.045, foreign_rate=0.0325,
                    sigma=0.08, cash_payout=1.0, direction="up")
    assert "rho_foreign" in ot
    assert "delta_premium_adjusted" in ot
