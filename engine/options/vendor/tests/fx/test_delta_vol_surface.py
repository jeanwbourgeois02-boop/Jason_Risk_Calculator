"""Sanity checks for options_calc.fx.delta_vol_surface."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.delta_vol_surface import FXDeltaVolSurface
from options_calc.fx.european import price


def _sample_surface():
    return FXDeltaVolSurface(
        S=1.10, domestic_rate=0.045, foreign_rate=0.0325,
        tenors=[0.25, 0.5, 1.0],
        atm_vols=[0.08, 0.085, 0.09],
        rr_25=[-0.015, -0.018, -0.02],
        bf_25=[0.003, 0.0035, 0.004],
    )


def _atm_strike(S, domestic_rate, foreign_rate, T):
    # Matches delta_vol_surface.py's _rounded_T: the surface solves its
    # internal strikes at T rounded to a whole calendar day (same
    # convention _engine.py's pricers use), so an externally-computed
    # reference K_atm must apply the same rounding to land on the same
    # grid point.
    T = int(T * 365 + 0.5) / 365
    return S * math.exp((domestic_rate - foreign_rate) * T)


def test_vol_at_atm_strike_matches_input_atm_vol():
    surface = _sample_surface()
    K_atm = _atm_strike(1.10, 0.045, 0.0325, 0.25)
    assert abs(surface.get_vol(K_atm, 0.25) - 0.08) < 1e-6


def test_25_delta_strikes_actually_price_to_25_delta_short_tenor():
    # Regression test for a bug found in audit: the strike solve used to
    # use the exact nominal T, but every pricer in this package rounds T
    # to a whole calendar day first, so the "25-delta" strike didn't
    # actually price to 0.25 delta once run through fx.european.price --
    # worst for short, non-round tenors like 1M (up to 0.7% relative).
    S, rd, rf, T = 1.10, 0.03, 0.015, 1 / 12
    surface = FXDeltaVolSurface(S, rd, rf, [T], [0.10], [-0.02], [0.005])
    strikes, vols = surface._smiles[0]
    K_put, v_put = strikes[0], vols[0]
    K_call, v_call = strikes[-1], vols[-1]

    call_result = price(S, K_call, T, rd, rf, v_call, "call")
    put_result = price(S, K_put, T, rd, rf, v_put, "put")

    assert abs(call_result["delta"] - 0.25) < 1e-6
    assert abs(put_result["delta"] - (-0.25)) < 1e-6


def test_negative_risk_reversal_makes_low_strikes_richer():
    # rr_25 is negative (puts richer than calls) -- a lower (put-side)
    # strike should show higher vol than a higher (call-side) strike.
    surface = _sample_surface()
    low_strike_vol = surface.get_vol(1.05, 0.25)
    high_strike_vol = surface.get_vol(1.15, 0.25)
    assert low_strike_vol > high_strike_vol


def test_25_delta_strikes_actually_produce_approximately_25_delta():
    # The whole point of the delta-to-strike conversion: pricing at the
    # solved strike with the solved vol should reproduce ~0.25 delta.
    surface = _sample_surface()
    T = 0.25
    vol_call = 0.08 + 0.003 + 0.5 * -0.015  # atm + bf + 0.5*rr
    K_call = surface._smiles[0][0][-1]  # highest strike in the 3mo smile = the 25-delta call
    result = price(1.10, K_call, T, 0.045, 0.0325, vol_call, "call")
    assert abs(result["delta"] - 0.25) < 0.01


def test_tenor_interpolation_lands_between_bracketing_tenors():
    surface = _sample_surface()
    K_atm_3mo = _atm_strike(1.10, 0.045, 0.0325, 0.25)
    vol_3mo = surface.get_vol(K_atm_3mo, 0.25)
    vol_45mo = surface.get_vol(K_atm_3mo, 0.375)
    vol_6mo = surface.get_vol(K_atm_3mo, 0.5)
    assert vol_3mo < vol_45mo < vol_6mo


def test_rejects_mismatched_input_lengths():
    try:
        FXDeltaVolSurface(
            S=1.10, domestic_rate=0.045, foreign_rate=0.0325,
            tenors=[0.25, 0.5], atm_vols=[0.08], rr_25=[-0.01, -0.01], bf_25=[0.003, 0.003],
        )
        assert False, "expected a ValueError for mismatched lengths"
    except ValueError:
        pass


def test_get_vol_plugs_into_existing_pricer():
    surface = _sample_surface()
    K = 1.08
    T = 0.25
    sigma = surface.get_vol(K, T)
    result = price(1.10, K, T, 0.045, 0.0325, sigma, "put")
    assert result["price"] > 0
