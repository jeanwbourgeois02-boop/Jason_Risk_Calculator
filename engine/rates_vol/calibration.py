"""SABR and Hull-White calibration to staged ``rate_vols`` market data.

THIS IS NEW MATH OWNED BY THIS APP, NOT THE VENDOR
--------------------------------------------------------------------------
``engine/options/vendor/options_calc/rates/sabr.py`` and ``bermudan_swaption.py`` both
document, explicitly, that they take model parameters DIRECTLY from the caller and
perform NO CALIBRATION (see ``MODELS.md``'s "Planned (not yet built)" section: "SABR
calibration to market data ... Hull-White calibration to a co-terminal European swaption
basket"). This module fills exactly that gap, entirely in application code -- it is not
a re-vendored or patched copy of anything under ``engine/options/vendor/``.

- ``calibrate_sabr``: fits (alpha, rho, nu) -- with `beta` FIXED by the caller, per SABR
  convention (beta is a desk choice, not something fit from a single expiry/tenor's
  strike smile with so few points) -- via ``scipy.optimize.least_squares`` against the
  vendored ``sabr_swaption_vol`` (Hagan's closed-form approximation, called by this
  module exactly as any other caller would -- no vendor code is touched).
- ``calibrate_hull_white``: fits (a, sigma) via QuantLib's OWN ``ql.HullWhite`` +
  ``ql.SwaptionHelper`` + ``ql.LevenbergMarquardt`` calibration machinery -- standard
  QuantLib calibration objects, used directly, NOT the vendored ``bermudan_swaption.py``
  (which has no calibration routine to call). The vendored pricer is only reused
  downstream, at pricing time, once these parameters are staged.

Both write into ``rate_model_params`` with ``source='CALIBRATED'`` -- ``inputs.py::
get_rate_model_params`` prefers a ``MANUAL`` row over a ``CALIBRATED`` one for the same
param when both exist (see that function's docstring for why: an explicit desk override
must never be silently superseded by an automated fit).

Neither function ever raises for a data-quality problem (insufficient strikes, no ATM
grid, an implausible fitted Hull-White sigma) -- each returns a result dataclass with
``success=False`` and a ``reason``, matching this package's "structured skip, never a
fabricated number" philosophy (``store.py``'s ``PricingOutcome``). A genuine programmer
error (e.g. calling with a `conn` missing the schema) still raises normally.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import List, Tuple

from .inputs import (
    ATM_KEY,
    _forward_par_rate,
    _read_curve_quotes,
    ensure_rate_model_params_table,
    ensure_rate_vols_table,
    set_rate_model_param,
    year_fraction,
)

_HW_SIGMA_MAX = 0.05  # matches vendor bermudan_swaption.py::_HW_VOLATILITY_PLAUSIBLE_MAX
                      # (pricer.py's own _HW_SIGMA_MAX) -- a calibrated fit outside this
                      # range is rejected here for the SAME reason the vendored pricer
                      # itself refuses to price with it (see that module's docstring).
_MIN_SABR_STRIKES = 3


def _tenor_years(tenor: str) -> int:
    """'5Y' -> 5, '10Y' -> 10 -- the inverse of store.py's
    ``f"{round(swap_tenor_years)}Y"`` formatting. Whole years only, matching this
    package's rounding convention for swap tenors (see pricer.py's swaption docstring)."""
    tenor = tenor.strip().upper()
    if not tenor.endswith("Y") or not tenor[:-1].isdigit():
        raise ValueError(f"Cannot parse underlying_tenor {tenor!r} as whole years (expected e.g. '5Y', '10Y')")
    return int(tenor[:-1])


# --------------------------------------------------------------------------- SABR


@dataclass
class SabrCalibrationResult:
    success: bool
    alpha: float = 0.0
    beta: float = 0.0
    rho: float = 0.0
    nu: float = 0.0
    n_strikes: int = 0
    rmse: float = 0.0
    reason: str = ""


def _read_sabr_strikes(
    conn: sqlite3.Connection, as_of: str, ccy: str, index: str, expiry_tenor: str, underlying_tenor: str,
) -> List[Tuple[str, float]]:
    """[(strike_or_ATM key, vol)] for LOGNORMAL rows only -- NORMAL rows are silently
    excluded from the calibration sample (not converted, not counted as unusable data
    requiring a skip; this mirrors every other vol consumer in this package treating
    NORMAL as simply not usable by the lognormal Hagan/Black-76 machinery, see
    inputs.py's module docstring)."""
    ensure_rate_vols_table(conn)
    return conn.execute(
        'SELECT strike_or_ATM, vol FROM rate_vols WHERE as_of_date=? AND ccy=? AND "index"=? '
        "AND expiry_tenor_or_date=? AND underlying_tenor=? AND vol_type='LOGNORMAL'",
        (as_of, ccy, index, expiry_tenor, underlying_tenor),
    ).fetchall()


def calibrate_sabr(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    index: str,
    expiry_tenor: str,
    underlying_tenor: str,
    beta: float = 0.5,
) -> SabrCalibrationResult:
    """Fit SABR (alpha, rho, nu) -- `beta` fixed by the caller -- to the strike-offset
    LOGNORMAL vol quotes staged in ``rate_vols`` for this exact (as_of, ccy, index,
    expiry_tenor, underlying_tenor) bucket, via ``scipy.optimize.least_squares`` against
    the vendored ``sabr_swaption_vol``.

    `expiry_tenor` is the SAME key as ``rate_vols.expiry_tenor_or_date`` -- in this
    phase that is the option's own ISO expiry date (e.g. '2031-08-17'), not a generic
    tenor bucket (no vol cube / interpolation -- see ``inputs.py``'s ``rate_vols`` DDL
    docstring). `underlying_tenor` is e.g. '10Y'.

    Needs >= 3 distinct strikes INCLUDING an 'ATM'-keyed quote (resolved to the
    curve-implied forward par rate on [expiry_date, expiry_date + underlying_tenor], via
    the SAME OIS CurveSet / forward-rate machinery ``inputs.py::derive_curve_inputs``
    uses) -- otherwise a structured skip (``success=False``), never a fit forced from too
    few points. `beta` is NOT fit (SABR convention: beta is a desk-chosen backbone
    exponent, not something a handful of strikes at one expiry/tenor can reliably pin
    down) -- it is written to ``rate_model_params`` alongside the fitted alpha/rho/nu so
    the full parameter set the vendored pricer needs is staged together.
    """
    from scipy.optimize import least_squares

    from engine.options.vendor.options_calc.rates.sabr import sabr_swaption_vol

    raw_rows = _read_sabr_strikes(conn, as_of, ccy, index, expiry_tenor, underlying_tenor)
    if len(raw_rows) < _MIN_SABR_STRIKES:
        return SabrCalibrationResult(
            success=False,
            reason=(
                f"only {len(raw_rows)} LOGNORMAL strike(s) staged in rate_vols for "
                f"({ccy}, {index}, expiry={expiry_tenor}, tenor={underlying_tenor}) -- "
                f"SABR calibration needs >= {_MIN_SABR_STRIKES} strikes including ATM"
            ),
        )
    if not any(key == ATM_KEY for key, _ in raw_rows):
        return SabrCalibrationResult(
            success=False,
            reason=(
                f"no ATM-keyed LOGNORMAL vol staged in rate_vols for ({ccy}, {index}, "
                f"expiry={expiry_tenor}, tenor={underlying_tenor}) -- SABR calibration "
                f"needs an explicit ATM quote to anchor the forward, not just >= 3 "
                f"off-ATM strikes"
            ),
        )

    quotes, quote_source = _read_curve_quotes(conn, as_of, ccy, index)
    if not quotes:
        return SabrCalibrationResult(success=False, reason=f"no curve_quotes for ({ccy}, {index}) as of {as_of}")

    from engine.rates.curves import build_curve_set

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry_date = datetime.date.fromisoformat(expiry_tenor)
    underlying_end = expiry_date + datetime.timedelta(days=round(_tenor_years(underlying_tenor) * 365.25))
    curve_set = build_curve_set(quotes, as_of_date, ccy, index)
    forward = _forward_par_rate(curve_set, as_of_date, expiry_date, underlying_end)
    expiry_years = year_fraction(as_of_date, expiry_date)

    strikes: List[float] = []
    market_vols: List[float] = []
    for key, vol in raw_rows:
        strike = forward if key == ATM_KEY else float(key)
        strikes.append(strike)
        market_vols.append(vol)

    def _residuals(params):
        alpha, rho, nu = params
        out = []
        for k, mv in zip(strikes, market_vols):
            try:
                model_vol = sabr_swaption_vol(k, forward, expiry_years, alpha, beta, rho, nu)
            except ValueError:
                out.append(10.0)  # push the optimizer away from Hagan's breakdown regime
                continue
            out.append(model_vol - mv)
        return out

    atm_vol = market_vols[strikes.index(forward)] if forward in strikes else max(market_vols)
    alpha0 = max(atm_vol * forward ** (1 - beta), 1e-6)
    x0 = [alpha0, 0.0, 0.3]
    result = least_squares(_residuals, x0, bounds=([1e-6, -0.999, 0.0], [10.0, 0.999, 10.0]))
    alpha_fit, rho_fit, nu_fit = result.x

    try:
        for k in strikes:
            sabr_swaption_vol(k, forward, expiry_years, alpha_fit, beta, rho_fit, nu_fit)
    except ValueError as exc:
        return SabrCalibrationResult(success=False, reason=f"SABR fit landed in Hagan's breakdown regime: {exc}")

    residuals = result.fun
    rmse = (sum(r * r for r in residuals) / len(residuals)) ** 0.5

    set_rate_model_param(conn, as_of, ccy, index, "SABR", "alpha", alpha_fit, source="CALIBRATED")
    set_rate_model_param(conn, as_of, ccy, index, "SABR", "beta", beta, source="CALIBRATED")
    set_rate_model_param(conn, as_of, ccy, index, "SABR", "rho", rho_fit, source="CALIBRATED")
    set_rate_model_param(conn, as_of, ccy, index, "SABR", "nu", nu_fit, source="CALIBRATED")

    return SabrCalibrationResult(
        success=True, alpha=alpha_fit, beta=beta, rho=rho_fit, nu=nu_fit,
        n_strikes=len(strikes), rmse=rmse,
    )


# --------------------------------------------------------------------------- Hull-White


@dataclass
class HullWhiteCalibrationResult:
    success: bool
    a: float = 0.0
    sigma: float = 0.0
    n_helpers: int = 0
    reason: str = ""


def calibrate_hull_white(conn: sqlite3.Connection, as_of: str, ccy: str, index: str) -> HullWhiteCalibrationResult:
    """Fit one-factor Hull-White (a, sigma) to every ATM LOGNORMAL swaption vol staged in
    ``rate_vols`` for (as_of, ccy, index) (across ALL expiry/tenor buckets present -- no
    expiry/tenor filter, unlike ``calibrate_sabr``), via QuantLib's OWN
    ``ql.HullWhite`` + ``ql.SwaptionHelper`` + ``ql.LevenbergMarquardt`` calibration
    objects (NOT the vendored ``bermudan_swaption.py``, which has no calibration routine
    -- see module docstring), against the bootstrapped OIS curve.

    Needs >= 2 ATM grid points (a 1-parameter-per-helper Hull-White fit with 2 free
    parameters is under-determined with fewer than 2 -- QuantLib's own
    ``CalibratedModel.calibrate`` raises "less functions than available variables" in
    that case, which this function catches and turns into a structured skip rather than
    letting a RuntimeError propagate). Rejects (skip, does not write) a fit whose sigma
    falls outside (0, 0.05] -- the SAME plausibility range the vendored
    ``bermudan_swaption.py`` itself enforces before pricing (see that module's
    ``_HW_VOLATILITY_PLAUSIBLE_MAX``) -- so a nonsensical fit is never silently staged
    for a later pricing call to pick up.
    """
    import QuantLib as ql

    from engine.rates import qlmap
    from engine.rates.curves import build_curve_set
    from engine.options.vendor.options_calc.rates._engine import CALENDAR, DAY_COUNTER, build_index

    ensure_rate_model_params_table(conn)
    ensure_rate_vols_table(conn)

    quotes, _quote_source = _read_curve_quotes(conn, as_of, ccy, index)
    if not quotes:
        return HullWhiteCalibrationResult(success=False, reason=f"no curve_quotes for ({ccy}, {index}) as of {as_of}")

    as_of_date = datetime.date.fromisoformat(as_of)
    curve_set = build_curve_set(quotes, as_of_date, ccy, index)

    rows = conn.execute(
        'SELECT expiry_tenor_or_date, underlying_tenor, vol FROM rate_vols '
        'WHERE as_of_date=? AND ccy=? AND "index"=? AND strike_or_ATM=? AND vol_type=?',
        (as_of, ccy, index, ATM_KEY, "LOGNORMAL"),
    ).fetchall()
    if len(rows) < 2:
        return HullWhiteCalibrationResult(
            success=False,
            reason=(
                f"only {len(rows)} ATM LOGNORMAL swaption vol(s) staged in rate_vols for "
                f"({ccy}, {index}) as of {as_of} -- Hull-White (a, sigma) calibration "
                f"needs >= 2 grid points (2 free parameters)"
            ),
        )

    ql.Settings.instance().evaluationDate = qlmap.ql_date(as_of_date)
    curve_handle = curve_set.discount
    gen_index = build_index(curve_handle)  # generic 6M ibor-style forecasting index, same as the vendored engine's own

    helpers = []
    for expiry_iso, underlying_tenor, vol in rows:
        expiry_date = datetime.date.fromisoformat(expiry_iso)
        expiry_years = max(int(round(year_fraction(as_of_date, expiry_date))), 1)
        tenor_years = _tenor_years(underlying_tenor)
        vol_handle = ql.QuoteHandle(ql.SimpleQuote(vol))
        helper = ql.SwaptionHelper(
            ql.Period(expiry_years, ql.Years), ql.Period(tenor_years, ql.Years),
            vol_handle, gen_index, ql.Period(1, ql.Years), DAY_COUNTER, DAY_COUNTER, curve_handle,
        )
        helpers.append(helper)

    model = ql.HullWhite(curve_handle, 0.03, 0.01)  # (a0, sigma0) starting guess -- "reasonable", not fit
    engine = ql.JamshidianSwaptionEngine(model)
    for helper in helpers:
        helper.setPricingEngine(engine)

    optimizer = ql.LevenbergMarquardt()
    end_criteria = ql.EndCriteria(1000, 100, 1e-8, 1e-8, 1e-8)
    try:
        model.calibrate(helpers, optimizer, end_criteria)
    except RuntimeError as exc:
        return HullWhiteCalibrationResult(success=False, n_helpers=len(helpers), reason=f"QuantLib calibration failed: {exc}")

    params = model.params()
    a_fit, sigma_fit = float(params[0]), float(params[1])

    if not (0 < sigma_fit <= _HW_SIGMA_MAX):
        return HullWhiteCalibrationResult(
            success=False, a=a_fit, sigma=sigma_fit, n_helpers=len(helpers),
            reason=(
                f"calibrated Hull-White sigma={sigma_fit!r} is outside the plausible "
                f"(0, {_HW_SIGMA_MAX}] range (matches the vendored pricer's own "
                f"pre-pricing check) -- refusing to stage this fit"
            ),
        )

    set_rate_model_param(conn, as_of, ccy, index, "HULL_WHITE", "a", a_fit, source="CALIBRATED")
    set_rate_model_param(conn, as_of, ccy, index, "HULL_WHITE", "sigma", sigma_fit, source="CALIBRATED")

    return HullWhiteCalibrationResult(success=True, a=a_fit, sigma=sigma_fit, n_helpers=len(helpers))
