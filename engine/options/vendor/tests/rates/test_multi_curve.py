"""Sanity checks for the multi-curve (discount_rate / forecast_rate) upgrade
to options_calc.rates.swaption and options_calc.rates.cap_floor.

Same philosophy as tests/rates/test_swaption.py: structural/identity
checks rather than fixed reference numbers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.rates.swaption import price as swaption_price
from options_calc.rates.cap_floor import price_cap


def test_single_rate_call_still_works_unchanged():
    """Backward compatibility: calling with just `r` (no discount_rate/
    forecast_rate) must still work and give a sane, positive price --
    every pre-existing call site in this codebase uses this form."""
    result = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer")
    assert result["price"] > 0


def test_discount_rate_and_forecast_rate_default_to_r():
    """Explicitly passing discount_rate=forecast_rate=r must reproduce
    the same price as the old single-`r` call (both paths resolve to the
    same two curves internally)."""
    implicit = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer")
    explicit = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                               discount_rate=0.04, forecast_rate=0.04)
    assert abs(implicit["price"] - explicit["price"]) < 1e-6


def test_delta_moves_far_more_from_forecast_bump_than_from_discount_bump():
    """The whole point of the multi-curve upgrade: 'delta' (forecast-rate
    sensitivity) should respond MUCH more strongly to a 100bp move in the
    forecast rate (which directly moves the forward swap rate) than to
    the same-sized move in the discount rate (which only affects delta
    indirectly, through the annuity/discounting of the option payoff).
    In the old single-flat-curve model, a single `r` bump moved both
    curves identically and this asymmetry would not exist."""
    base = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                           discount_rate=0.04, forecast_rate=0.04)
    bump_discount = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                                    discount_rate=0.05, forecast_rate=0.04)
    bump_forecast = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                                    discount_rate=0.04, forecast_rate=0.05)

    delta_move_from_discount = abs(bump_discount["delta"] - base["delta"])
    delta_move_from_forecast = abs(bump_forecast["delta"] - base["delta"])
    assert delta_move_from_discount < 0.5 * delta_move_from_forecast


def test_rho_responds_to_discount_rate_holding_forecast_fixed():
    """'rho' must be sensitive to the discount rate at all -- holding
    forecast_rate fixed and moving discount_rate should move rho by a
    non-trivial amount (NOTE: rho's magnitude also depends on moneyness,
    which is driven by forecast_rate, not discount_rate, so this checks
    only that rho responds to discount_rate -- it does not attempt the
    stronger, moneyness-confounded claim that rho is *more* sensitive to
    discount_rate than to forecast_rate, which held less cleanly than
    delta's analogous asymmetry when checked numerically)."""
    low_discount = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                                   discount_rate=0.02, forecast_rate=0.04)
    high_discount = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                                    discount_rate=0.06, forecast_rate=0.04)
    assert low_discount["rho"] != high_discount["rho"]


def test_delta_and_rho_are_genuinely_different_numbers():
    """With separate curves, delta (per-unit forward-rate sensitivity)
    and rho (per-percentage-point discount sensitivity) are computed from
    independent bumps and are not expected to coincide the way they
    numerically related to each other in the old single-curve model."""
    result = swaption_price(0.04, 5, 10, 0.04, 0.20, option_type="payer",
                             discount_rate=0.03, forecast_rate=0.05)
    assert result["delta"] != result["rho"]


def test_cap_floor_multi_curve_price_is_sane():
    """cap_floor.price_cap also accepts discount_rate/forecast_rate and
    returns a sane positive price with a genuine basis between the two
    curves."""
    result = price_cap(0.04, 1.0, 5, 0.04, 0.20, discount_rate=0.02, forecast_rate=0.05)
    assert result["price"] > 0


def test_cap_price_changes_when_forecast_rate_changes_holding_discount_fixed():
    """Raising the forecast rate (holding discount fixed) raises the
    forward LIBOR-style rate the caplets are struck against, which for an
    at/below-market strike cap should increase the cap's value -- a
    change that a single-flat-curve model could not isolate from a
    simultaneous discounting change."""
    low_forecast = price_cap(0.04, 1.0, 5, 0.04, 0.20, discount_rate=0.04, forecast_rate=0.02)
    high_forecast = price_cap(0.04, 1.0, 5, 0.04, 0.20, discount_rate=0.04, forecast_rate=0.06)
    assert high_forecast["price"] > low_forecast["price"]
