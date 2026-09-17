"""Sanity checks for options_calc.fx.barrier -- see
equity/test_barrier.py for the knock-in/knock-out parity explanation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.barrier import price as barrier_price
from options_calc.fx.european import price as vanilla_price


def test_up_and_out_plus_up_and_in_equals_vanilla():
    out = barrier_price(1.10, 1.10, 1.20, 0.5, 0.045, 0.0325, 0.08, "call", "up-and-out")
    inn = barrier_price(1.10, 1.10, 1.20, 0.5, 0.045, 0.0325, 0.08, "call", "up-and-in")
    vanilla = vanilla_price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    assert abs((out["price"] + inn["price"]) - vanilla["price"]) < 0.001


def test_up_and_out_cheaper_than_vanilla():
    out = barrier_price(1.10, 1.10, 1.20, 0.5, 0.045, 0.0325, 0.08, "call", "up-and-out")
    vanilla = vanilla_price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call")
    assert out["price"] < vanilla["price"]
