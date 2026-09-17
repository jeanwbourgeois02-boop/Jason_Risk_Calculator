"""Sanity checks for options_calc.rates.sabr.

Same philosophy as tests/rates/test_swaption.py: structural/identity
checks, not fixed reference numbers -- plus one sanity check specific to
SABR's own well-known degenerate case (nu=0 collapses the smile).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from options_calc.rates.sabr import sabr_swaption_vol, price_swaption_sabr
from options_calc.rates.swaption import price as swaption_price


def test_zero_vol_of_vol_gives_a_flat_ish_smile():
    """With nu (vol-of-vol) = 0, SABR's smile collapses -- the implied vol
    at different strikes around the forward should be very close to each
    other (Hagan's own paper: nu=0 removes the stochastic-vol-driven
    smile/skew entirely, leaving only the CEV backbone, which is close to
    flat for strikes reasonably near the forward)."""
    forward, expiry = 0.04, 5.0
    alpha, beta, rho, nu = 0.04, 0.5, 0.0, 0.0

    vol_atm = sabr_swaption_vol(0.04, forward, expiry, alpha, beta, rho, nu)
    vol_below = sabr_swaption_vol(0.03, forward, expiry, alpha, beta, rho, nu)
    vol_above = sabr_swaption_vol(0.05, forward, expiry, alpha, beta, rho, nu)

    assert abs(vol_below - vol_atm) < 0.1 * vol_atm
    assert abs(vol_above - vol_atm) < 0.1 * vol_atm


def test_positive_vol_of_vol_creates_a_smile():
    """With nu > 0, strikes away from the forward should have HIGHER
    implied vol than the at-the-money strike -- the defining feature of a
    smile (this holds even with rho=0, i.e. no skew, isolating nu's
    curvature effect)."""
    forward, expiry = 0.04, 5.0
    alpha, beta, rho, nu = 0.04, 0.5, 0.0, 0.6

    vol_atm = sabr_swaption_vol(0.04, forward, expiry, alpha, beta, rho, nu)
    vol_below = sabr_swaption_vol(0.02, forward, expiry, alpha, beta, rho, nu)
    vol_above = sabr_swaption_vol(0.07, forward, expiry, alpha, beta, rho, nu)

    assert vol_below > vol_atm
    assert vol_above > vol_atm


def test_negative_rho_creates_skew():
    """rho != 0 breaks the symmetry of the smile: with rho < 0 (the
    common sign for rates skew), a downside (low-strike) vol should be
    higher than the equivalent-distance upside (high-strike) vol."""
    forward, expiry = 0.04, 5.0
    alpha, beta, nu = 0.04, 0.5, 0.4

    vol_down = sabr_swaption_vol(0.03, forward, expiry, alpha, beta, -0.5, nu)
    vol_up = sabr_swaption_vol(0.05, forward, expiry, alpha, beta, -0.5, nu)
    assert vol_down > vol_up


def test_sabr_vol_matches_flat_vol_pricer_when_fed_through():
    """price_swaption_sabr, given a SABR vol, should reproduce EXACTLY
    the same price as calling the flat-vol swaption.price() with that
    same vol as sigma directly -- SABR here only supplies the sigma, it
    does not change how the swaption itself is priced."""
    fixed_rate, forward, expiry, swap_tenor, r, notional = 0.04, 0.04, 5, 10, 0.04, 1_000_000.0
    alpha, beta, rho, nu = 0.04, 0.5, -0.3, 0.5

    sigma = sabr_swaption_vol(fixed_rate, forward, expiry, alpha, beta, rho, nu)
    sabr_price = price_swaption_sabr(
        fixed_rate, forward, expiry, swap_tenor, r, notional, "payer", alpha, beta, rho, nu
    )
    flat_price = swaption_price(fixed_rate, expiry, swap_tenor, r, sigma, notional, "payer")["price"]

    assert sabr_price == pytest.approx(flat_price, rel=1e-9)


def test_invalid_nonpositive_strike_or_forward_raises():
    with pytest.raises(ValueError):
        sabr_swaption_vol(-0.01, 0.04, 5.0, 0.04, 0.5, 0.0, 0.3)
    with pytest.raises(ValueError):
        sabr_swaption_vol(0.04, 0.0, 5.0, 0.04, 0.5, 0.0, 0.3)


def test_breakdown_regime_raises_instead_of_returning_negative_vol():
    # Regression test for a Critical bug found in audit: Hagan's
    # approximation can return a negative vol for parameter combinations
    # well within the documented "valid" ranges (long expiry, high
    # vol-of-vol, strongly negative rho) -- e.g. this exact combination
    # used to silently return -0.04 instead of raising.
    with pytest.raises(ValueError):
        sabr_swaption_vol(0.04, 0.04, 30, 0.04, 0.5, -0.9, 1.0)
