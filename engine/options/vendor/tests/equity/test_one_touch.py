"""Sanity checks for options_calc.equity.one_touch.

The strongest available check is the same kind of no-arbitrage identity
used for barrier knock-in/knock-out parity: one-touch + no-touch (same
barrier, otherwise identical) must sum to the discounted cash payout,
since exactly one of "touched" or "never touched" happens.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.one_touch import one_touch, no_touch


def test_one_touch_plus_no_touch_equals_discounted_payout():
    ot = one_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.2, cash_payout=1.0, direction="up")
    nt = no_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.2, cash_payout=1.0, direction="up")
    discounted_payout = math.exp(-0.05 * 0.5)
    assert abs((ot["price"] + nt["price"]) - discounted_payout) < 1e-4


def test_one_touch_and_no_touch_work_for_down_direction_too():
    ot = one_touch(S=100, barrier=80, T=0.5, r=0.05, sigma=0.2, cash_payout=1.0, direction="down")
    nt = no_touch(S=100, barrier=80, T=0.5, r=0.05, sigma=0.2, cash_payout=1.0, direction="down")
    discounted_payout = math.exp(-0.05 * 0.5)
    assert abs((ot["price"] + nt["price"]) - discounted_payout) < 1e-4


def test_price_scales_linearly_with_cash_payout():
    ot1 = one_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.2, cash_payout=1.0, direction="up")
    ot10 = one_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.2, cash_payout=10.0, direction="up")
    assert abs(ot10["price"] - ot1["price"] * 10) < 1e-3


def test_closer_barrier_makes_one_touch_more_valuable():
    # A barrier closer to spot is easier to touch, so a one-touch on it
    # should be worth more than one on a farther barrier.
    near = one_touch(S=100, barrier=105, T=0.5, r=0.05, sigma=0.2, cash_payout=1.0, direction="up")
    far = one_touch(S=100, barrier=130, T=0.5, r=0.05, sigma=0.2, cash_payout=1.0, direction="up")
    assert near["price"] > far["price"]


def test_rejects_unknown_direction():
    try:
        one_touch(S=100, barrier=120, T=0.5, r=0.05, sigma=0.2, direction="sideways")
        assert False, "expected a ValueError for an unknown direction"
    except ValueError:
        pass
