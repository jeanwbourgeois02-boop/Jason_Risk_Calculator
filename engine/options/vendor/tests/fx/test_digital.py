"""Sanity checks for options_calc.fx.digital -- see equity/test_digital.py
for the reasoning behind each check."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.digital import price


def test_price_scales_linearly_with_cash_payout():
    d1 = price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call", cash_payout=1.0)
    d2 = price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call", cash_payout=10.0)
    assert abs(d2["price"] - d1["price"] * 10) < 1e-6


def test_call_and_put_prices_sum_to_domestic_discounted_payout():
    # Loose tolerance: QuantLib's day-count rounding of T=0.5 years to a
    # whole number of days -- see equity/test_digital.py for the same issue.
    call = price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "call", cash_payout=1.0)
    put = price(1.10, 1.10, 0.5, 0.045, 0.0325, 0.08, "put", cash_payout=1.0)
    discounted_payout = math.exp(-0.045 * 0.5)
    assert abs((call["price"] + put["price"]) - discounted_payout) < 1e-4
