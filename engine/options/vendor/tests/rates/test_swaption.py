"""Sanity checks for options_calc.rates.swaption.

No fixed reference numbers (see tests/equity/test_american.py and
tests/fx/test_barrier.py for why -- day-count rounding from converting a
T-in-years into whole calendar days causes small, expected numerical
discrepancies vs. any hand-computed "textbook" value). Instead these check
a real, well-known mathematical identity plus a couple of structural/
monotonicity properties.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.rates.swaption import price as swaption_price
from options_calc.rates._engine import build_forward_swap


def _forward_swap_npv(fixed_rate, expiry, swap_tenor, r, notional):
    """The forward-starting swap value the parity identity checks
    against -- pay-fixed convention, so this matches a *payer* swaption's
    "underlying". Internal helper, not part of the public API; used here
    the same way tests/equity and tests/fx reach into _engine.py."""
    _, _, swap, _ = build_forward_swap(fixed_rate, expiry, swap_tenor, r, notional, "payer")
    return swap.NPV()


def test_payer_minus_receiver_equals_forward_swap():
    """Put-call-parity analogue for swaptions: a payer swaption minus a
    receiver swaption (same strike/expiry/tenor) equals the value of
    entering the forward-starting PAYER swap today. This holds regardless
    of volatility (it falls out of replication: payer - receiver = being
    obligated to pay fixed no matter what, i.e. the forward swap itself),
    so it is a strong, vol-independent check."""
    fixed_rate, expiry, swap_tenor, r, sigma, notional = 0.04, 5, 10, 0.04, 0.20, 1_000_000.0

    payer = swaption_price(fixed_rate, expiry, swap_tenor, r, sigma, notional, "payer")
    receiver = swaption_price(fixed_rate, expiry, swap_tenor, r, sigma, notional, "receiver")
    forward_swap = _forward_swap_npv(fixed_rate, expiry, swap_tenor, r, notional)

    assert abs((payer["price"] - receiver["price"]) - forward_swap) < 1.0


def test_parity_holds_at_a_different_vol_too():
    """The parity identity is vol-independent -- re-check at a different
    sigma to make sure it isn't a coincidence of one particular vol."""
    fixed_rate, expiry, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0

    payer = swaption_price(fixed_rate, expiry, swap_tenor, r, 0.35, notional, "payer")
    receiver = swaption_price(fixed_rate, expiry, swap_tenor, r, 0.35, notional, "receiver")
    forward_swap = _forward_swap_npv(fixed_rate, expiry, swap_tenor, r, notional)

    assert abs((payer["price"] - receiver["price"]) - forward_swap) < 1.0


def test_price_increases_with_volatility():
    """Both payer and receiver swaptions are long optionality, so their
    price must be monotonically increasing in vol (standard Black-76
    property -- vega > 0 for a European option away from the T=0/sigma=0
    degenerate corner)."""
    fixed_rate, expiry, swap_tenor, r, notional = 0.04, 5, 10, 0.04, 1_000_000.0

    low_vol = swaption_price(fixed_rate, expiry, swap_tenor, r, 0.10, notional, "payer")
    high_vol = swaption_price(fixed_rate, expiry, swap_tenor, r, 0.30, notional, "payer")
    assert high_vol["price"] > low_vol["price"]


def test_atm_payer_and_receiver_are_close_in_value():
    """At the money (fixed_rate == the forward swap's fair rate, roughly
    r itself under a flat curve), a payer and receiver swaption should be
    priced fairly close to each other -- neither side has a strong
    structural advantage right at the forward. This is a loose sanity
    check, not an exact identity (the forward swap rate isn't exactly
    equal to the flat rate r once compounding/annuity effects are
    accounted for, so "close" rather than "equal")."""
    fixed_rate, expiry, swap_tenor, r, sigma, notional = 0.04, 5, 10, 0.04, 0.20, 1_000_000.0

    payer = swaption_price(fixed_rate, expiry, swap_tenor, r, sigma, notional, "payer")
    receiver = swaption_price(fixed_rate, expiry, swap_tenor, r, sigma, notional, "receiver")
    assert abs(payer["price"] - receiver["price"]) < 0.1 * max(payer["price"], receiver["price"])


def test_prices_are_positive():
    result_payer = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer")
    result_receiver = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="receiver")
    assert result_payer["price"] > 0
    assert result_receiver["price"] > 0
