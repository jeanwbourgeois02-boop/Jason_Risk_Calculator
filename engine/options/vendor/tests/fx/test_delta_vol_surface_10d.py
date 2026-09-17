"""Sanity checks for options_calc.fx.delta_vol_surface's 10-delta support."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.fx.delta_vol_surface import FXDeltaVolSurface


def _surface_with_10d():
    return FXDeltaVolSurface(
        S=1.10, domestic_rate=0.045, foreign_rate=0.0325,
        tenors=[0.25, 0.5],
        atm_vols=[0.08, 0.085],
        rr_25=[-0.015, -0.018],
        bf_25=[0.003, 0.0035],
        rr_10=[-0.025, -0.03],
        bf_10=[0.006, 0.007],
    )


def test_5_point_smile_when_10d_given():
    surface = _surface_with_10d()
    strikes, vols = surface._smiles[0]
    assert len(strikes) == 5
    assert len(vols) == 5


def test_3_point_smile_when_10d_omitted():
    surface = FXDeltaVolSurface(
        S=1.10, domestic_rate=0.045, foreign_rate=0.0325,
        tenors=[0.25], atm_vols=[0.08], rr_25=[-0.015], bf_25=[0.003],
    )
    strikes, vols = surface._smiles[0]
    assert len(strikes) == 3


def test_10_delta_put_strike_is_lower_than_25_delta_put_strike():
    surface = _surface_with_10d()
    strikes, _ = surface._smiles[0]
    # sorted ascending: [10P, 25P, ATM, 25C, 10C]
    assert strikes[0] < strikes[1] < strikes[2] < strikes[3] < strikes[4]


def test_10_delta_wings_richer_when_butterfly_curvature_dominates():
    # Whether the 10-delta wing is richer than the 25-delta wing depends
    # on whether the butterfly (curvature) term at 10-delta grows enough
    # to offset the risk reversal's (skew) pull -- it is NOT automatic
    # just because RR is negative (a strong enough skew can actually make
    # the call wing's vol decrease further out-of-the-money). Here bf_10
    # is chosen large enough relative to rr_10 that curvature dominates,
    # so both wings are richer at 10-delta than 25-delta.
    surface = FXDeltaVolSurface(
        S=1.10, domestic_rate=0.045, foreign_rate=0.0325,
        tenors=[0.25], atm_vols=[0.08], rr_25=[-0.015], bf_25=[0.003],
        rr_10=[-0.025], bf_10=[0.012],
    )
    _, vols = surface._smiles[0]
    v_10put, v_25put, v_atm, v_25call, v_10call = vols
    assert v_10put > v_25put > v_atm
    assert v_10call > v_25call


def test_10_delta_put_richer_than_25_delta_put():
    # The put-side (skew-favored) direction is more robust: with a
    # negative RR, more negative delta always pulls put vol up further,
    # regardless of the butterfly magnitude.
    surface = _surface_with_10d()
    _, vols = surface._smiles[0]
    v_10put, v_25put, v_atm = vols[0], vols[1], vols[2]
    assert v_10put > v_25put > v_atm


def test_rejects_10d_args_given_without_the_other():
    try:
        FXDeltaVolSurface(
            S=1.10, domestic_rate=0.045, foreign_rate=0.0325,
            tenors=[0.25], atm_vols=[0.08], rr_25=[-0.015], bf_25=[0.003],
            rr_10=[-0.02],
        )
        assert False, "expected a ValueError"
    except ValueError:
        pass


def test_rejects_mismatched_10d_lengths():
    try:
        FXDeltaVolSurface(
            S=1.10, domestic_rate=0.045, foreign_rate=0.0325,
            tenors=[0.25, 0.5], atm_vols=[0.08, 0.085], rr_25=[-0.015, -0.018], bf_25=[0.003, 0.0035],
            rr_10=[-0.02], bf_10=[0.005],
        )
        assert False, "expected a ValueError"
    except ValueError:
        pass


def test_get_vol_still_works_end_to_end_with_10d():
    surface = _surface_with_10d()
    sigma = surface.get_vol(K=1.02, T=0.3)
    assert sigma > 0
