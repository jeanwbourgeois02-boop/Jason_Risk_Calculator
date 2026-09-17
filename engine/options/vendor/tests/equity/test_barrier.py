"""Sanity checks for options_calc.equity.barrier.

The strongest available check is knock-in/knock-out parity: an "up-and-in"
plus an "up-and-out" with the same barrier must exactly equal the vanilla
option, since exactly one of "touched the barrier" or "didn't" happens --
this is a genuine no-arbitrage identity, not an approximation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.barrier import price as barrier_price
from options_calc.equity.european import price as vanilla_price


def test_up_and_out_plus_up_and_in_equals_vanilla():
    out = barrier_price(100, 100, 120, 0.5, 0.05, 0.2, "call", "up-and-out")
    inn = barrier_price(100, 100, 120, 0.5, 0.05, 0.2, "call", "up-and-in")
    vanilla = vanilla_price(100, 100, 0.5, 0.05, 0.2, "call")
    assert abs((out["price"] + inn["price"]) - vanilla["price"]) < 0.05


def test_down_and_out_plus_down_and_in_equals_vanilla():
    out = barrier_price(100, 100, 80, 0.5, 0.05, 0.2, "put", "down-and-out")
    inn = barrier_price(100, 100, 80, 0.5, 0.05, 0.2, "put", "down-and-in")
    vanilla = vanilla_price(100, 100, 0.5, 0.05, 0.2, "put")
    assert abs((out["price"] + inn["price"]) - vanilla["price"]) < 0.05


def test_up_and_out_cheaper_than_vanilla():
    # Giving up the scenarios where the barrier is breached can only
    # reduce value relative to the unrestricted vanilla.
    out = barrier_price(100, 100, 120, 0.5, 0.05, 0.2, "call", "up-and-out")
    vanilla = vanilla_price(100, 100, 0.5, 0.05, 0.2, "call")
    assert out["price"] < vanilla["price"]


def test_rejects_unknown_barrier_type():
    try:
        barrier_price(100, 100, 120, 0.5, 0.05, 0.2, "call", "sideways-out")
        assert False, "expected a ValueError for an unknown barrier_type"
    except ValueError:
        pass
