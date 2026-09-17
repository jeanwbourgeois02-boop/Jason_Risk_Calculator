"""Sanity checks for options_calc.equity.digital."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.equity.digital import price


def test_price_scales_linearly_with_cash_payout():
    d1 = price(100, 100, 0.5, 0.05, 0.2, "call", cash_payout=1.0)
    d2 = price(100, 100, 0.5, 0.05, 0.2, "call", cash_payout=10.0)
    assert abs(d2["price"] - d1["price"] * 10) < 1e-6


def test_deep_itm_call_price_approaches_discounted_payout():
    # Deep in-the-money, the option is nearly certain to pay out, so its
    # price should approach the payout discounted back at the risk-free rate.
    import math
    d = price(1000, 100, 0.5, 0.05, 0.2, "call", cash_payout=1.0)
    discounted_payout = math.exp(-0.05 * 0.5)
    assert abs(d["price"] - discounted_payout) < 0.01


def test_deep_otm_call_price_approaches_zero():
    d = price(10, 1000, 0.5, 0.05, 0.2, "call", cash_payout=1.0)
    assert d["price"] < 0.01


def test_call_and_put_prices_sum_to_discounted_payout():
    # Exactly one of "finishes above K" or "finishes below K" happens, so
    # a call and put digital (same strike/expiry) must sum to the
    # discounted cash payout -- a no-arbitrage identity, like put-call
    # parity is for vanillas. Tolerance is loose (not 1e-9) because
    # QuantLib rounds T=0.5 years to a whole number of days (183, not
    # 182.5), so the *actual* day count used internally differs very
    # slightly from a naive T=0.5 in this assertion -- see
    # equity/test_european.py for the same day-count issue.
    import math
    call = price(100, 100, 0.5, 0.05, 0.2, "call", cash_payout=1.0)
    put = price(100, 100, 0.5, 0.05, 0.2, "put", cash_payout=1.0)
    discounted_payout = math.exp(-0.05 * 0.5)
    assert abs((call["price"] + put["price"]) - discounted_payout) < 1e-4
