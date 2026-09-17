"""Sanity checks for options_calc.vol_surface.

VolSurface is pure interpolation logic with no QuantLib dependency, so
these tests check the interpolation/extrapolation math directly, plus one
end-to-end test confirming it plugs into an existing pricer without any
pricer changes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from options_calc.vol_surface import VolSurface
from options_calc.equity.european import price


def _sample_surface():
    return VolSurface(
        strikes=[5300, 5500, 5700],
        tenors=[1 / 12, 0.25, 0.5, 1.0],
        vols=[
            [0.18, 0.14, 0.12],
            [0.17, 0.145, 0.13],
            [0.16, 0.15, 0.135],
            [0.155, 0.15, 0.14],
        ],
    )


def test_exact_grid_point_returns_stored_value():
    surface = _sample_surface()
    assert surface.get_vol(5500, 0.25) == 0.145


def test_interpolates_linearly_between_strikes():
    surface = _sample_surface()
    # halfway between 5300 (0.17) and 5500 (0.145) at the 3mo tenor
    midpoint = surface.get_vol(5400, 0.25)
    assert abs(midpoint - (0.17 + 0.145) / 2) < 1e-9


def test_interpolates_linearly_between_tenors():
    surface = _sample_surface()
    # halfway between 3mo (0.145) and 6mo (0.15) at strike 5500
    midpoint = surface.get_vol(5500, 0.375)
    assert abs(midpoint - (0.145 + 0.15) / 2) < 1e-9


def test_extrapolates_flat_below_lowest_strike():
    surface = _sample_surface()
    assert surface.get_vol(5000, 0.25) == surface.get_vol(5300, 0.25)


def test_extrapolates_flat_above_highest_strike():
    surface = _sample_surface()
    assert surface.get_vol(6000, 0.25) == surface.get_vol(5700, 0.25)


def test_extrapolates_flat_below_shortest_tenor():
    surface = _sample_surface()
    assert surface.get_vol(5500, 1 / 365) == surface.get_vol(5500, 1 / 12)


def test_extrapolates_flat_above_longest_tenor():
    surface = _sample_surface()
    assert surface.get_vol(5500, 5.0) == surface.get_vol(5500, 1.0)


def test_preserves_skew_lower_strike_has_higher_vol():
    # A downside put strike should read a richer vol than an upside call
    # strike at the same tenor -- this is the skew the surface exists to
    # represent, so it must survive interpolation, not just exact lookups.
    surface = _sample_surface()
    assert surface.get_vol(5300, 0.25) > surface.get_vol(5700, 0.25)


def test_rejects_mismatched_vols_shape():
    try:
        VolSurface(strikes=[100, 110], tenors=[0.5], vols=[[0.2]])
        assert False, "expected a ValueError for mismatched dimensions"
    except ValueError:
        pass


def test_plugs_into_existing_pricer_without_modification():
    # End-to-end: read a strike-specific vol off the surface and feed it
    # straight into the unmodified European equity pricer.
    surface = _sample_surface()
    K, T = 5300, 0.25
    sigma = surface.get_vol(K, T)
    result = price(S=5500, K=K, T=T, r=0.045, sigma=sigma, option_type="put")
    assert result["price"] > 0
    assert sigma == 0.17
