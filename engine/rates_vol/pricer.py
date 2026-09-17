"""Scalar pricer wrappers over ``engine/options/vendor/options_calc/rates``.

Every function here prices at UNIT notional first (the vendored pricers are exactly
linear in notional for a Black-76 swaption/cap/floor and for the Hull-White tree
Bermudan -- the payoff and every Greek scale with notional, so pricing once at
notional=1.0 and scaling afterward is exact, not an approximation), then scales by the
SIGNED trade ``quantity`` directly: ``+`` = long the option (bought). The result's
``npv_total``/``dv01``/``vega``/``gamma``/``theta`` fields are the POSITION's already
sign-adjusted totals -- nothing downstream needs to re-apply ``sign(quantity)`` (unlike
``engine/options``'s FX-option DELTA mark, which is deliberately left per-unit for the
cash-ladder query to scale itself; this package's convention is different and is
restated here on purpose so nobody assumes it matches).

CURVE INPUTS (2026-09-17, replaces the earlier flat-rate approximation)
--------------------------------------------------------------------------
Every pricer here now takes a single bootstrapped OIS curve handle (``inputs.py``'s
``CurveInputs.curve``, a ``ql.YieldTermStructureHandle``) and passes it as BOTH
``discount_curve`` and ``forecast_curve`` to the vendored engine -- this package prices
off a genuine bootstrapped term structure, not a flat zero rate / forward par rate
derived from it (see ``engine/rates_vol/__init__.py``'s "Curve inputs" section for what
this does and does not still simplify: single-curve OIS discounting/forecasting off the
SAME curve, matching ``engine/rates``'s own swap pricer, still no convexity adjustment /
CMS measure change / cross-currency basis). ``evaluation_date`` is passed through
explicitly (as `as_of_date`, converted via ``engine.rates.qlmap.ql_date``) rather than
relying on any ambient QuantLib global.

DV01 convention (matches ``engine/rates/valuation.py``'s ``BUMP = 1e-4`` sign):
``NPV(+1bp parallel bump of the curve) - NPV(base)``. The bump is a PARALLEL
continuously-compounded zero-rate spread of the curve (``_bump_curve`` below, using
``ql.ZeroSpreadedTermStructure`` fed a ``ql.SimpleQuote`` -- the SAME curve-spread-bump
TECHNIQUE the vendored engine's own ``finite_difference_curve_greeks``/``_bumped_curve``
use internally, see ``options_calc/rates/_engine.py``'s "GREEKS UNDER CURVE INPUTS"
docstring section), applied to BOTH the discount and forecast role AT ONCE -- not the
vendored engine's own delta/rho split (which independently bumps ``forecast_curve`` and
``discount_curve``, holding the other fixed; that split is only meaningful for a genuine
two-curve setup, which this package does not have: our discount and forecast curve are
literally the same object). Bumping both together is what corresponds to "the OIS curve
moved 1bp," matching ``engine/rates/valuation.py``'s own DV01 (which bumps every pillar
of the same single curve). Sign: a positive-DV01 payer position loses value when rates
fall (``NPV(bumped_up) - NPV(base) > 0`` for a payer), identical to
``engine/rates/valuation.py``'s documented convention.

Every other Greek (vega, gamma, theta) for the Black-76 pricers (swaption, cap/floor,
SABR-priced swaption) comes directly from the vendored engine's own bump-and-reprice
Greeks dict (``finite_difference_curve_greeks`` inside ``rates/_engine.py``, used
whenever curve args are given) at unit notional, then scaled by ``quantity`` the same
way NPV is. The Bermudan pricer's vega/gamma/theta are NOT vendor-provided (the vendored
``bermudan_swaption.py`` docstring says so explicitly -- Greeks under a Hull-White tree
are expensive per bump and the vendor does not attempt them) -- this module adds its own
bump-and-reprice for Bermudan, with the real caveat that "vega" there bumps the
Hull-White model's own (uncalibrated, or ``calibration.py``-fit) ``sigma``, NOT a
market-observable lognormal swaption vol. See ``engine/rates_vol/__init__.py``'s
"Bermudan / Hull-White model risk" section before using any Bermudan Greek for real
risk/P&L.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import QuantLib as ql

from engine.rates import qlmap

from engine.options.vendor.options_calc.rates import bermudan_swaption as _bermudan
from engine.options.vendor.options_calc.rates import cap_floor as _cap_floor
from engine.options.vendor.options_calc.rates import swaption as _swaption
from engine.options.vendor.options_calc.rates._engine import price_cap_floor_only, price_swaption_only
from engine.options.vendor.options_calc.rates.sabr import sabr_swaption_vol

DV01_BUMP = 1e-4  # 1 basis point, matches engine/rates/valuation.py::BUMP


def _bump_curve(curve_handle, spread_value: float):
    """Parallel continuously-compounded zero-rate bump of `curve_handle` by
    `spread_value` (decimal, e.g. 1e-4 = +1bp), via ql.ZeroSpreadedTermStructure -- the
    same technique the vendored engine's finite_difference_curve_greeks uses internally
    (see this module's docstring "DV01 convention" section for why this package applies
    the bump to BOTH curve roles at once rather than reusing the vendored engine's own
    split delta/rho bumps)."""
    spread_quote = ql.QuoteHandle(ql.SimpleQuote(spread_value))
    return ql.YieldTermStructureHandle(ql.ZeroSpreadedTermStructure(curve_handle, spread_quote))


@dataclass
class RateOptionResult:
    npv_per_unit: float          # long-holder's Black-76/HW-tree value per 1 unit notional
    npv_total: float             # quantity * npv_per_unit -- sign(quantity) already applied
    dv01: float                  # quantity * per-unit DV01 (+1bp parallel curve bump)
    vega: float                  # quantity * per-unit vega
    gamma: float                 # quantity * per-unit gamma
    theta: float                 # quantity * per-unit theta (one-day decay)
    raw_per_unit: Dict[str, float] = field(default_factory=dict)  # vendor's own Greeks dict, unit notional


def _option_type_str(option_type: str) -> str:
    mapping = {"PAYER": "payer", "RECEIVER": "receiver"}
    if option_type not in mapping:
        raise ValueError(f"option_type must be 'PAYER' or 'RECEIVER', got {option_type!r}")
    return mapping[option_type]


def _scale(quantity: float, npv_per_unit: float, dv01_per_unit: float, raw: Dict[str, float],
           gamma: Optional[float] = None, theta: Optional[float] = None, vega: Optional[float] = None) -> RateOptionResult:
    gamma = raw.get("gamma", 0.0) if gamma is None else gamma
    theta = raw.get("theta", 0.0) if theta is None else theta
    vega = raw.get("vega", 0.0) if vega is None else vega
    return RateOptionResult(
        npv_per_unit=npv_per_unit,
        npv_total=quantity * npv_per_unit,
        dv01=quantity * dv01_per_unit,
        vega=quantity * vega,
        gamma=quantity * gamma,
        theta=quantity * theta,
        raw_per_unit=raw,
    )


def price_swaption(
    quantity: float,
    fixed_rate: float,
    option_type: str,
    expiry_years: float,
    swap_tenor_years: float,
    curve,
    sigma: float,
    as_of_date: datetime.date,
    dv01_bump: float = DV01_BUMP,
) -> RateOptionResult:
    """European swaption, Black-76 (vendored ``rates/swaption.py``), priced off `curve`
    (a ``ql.YieldTermStructureHandle`` -- the bootstrapped OIS curve, typically
    ``CurveInputs.curve``), passed as BOTH ``discount_curve`` and ``forecast_curve`` --
    see this module's docstring "CURVE INPUTS" section. `option_type` is 'PAYER' or
    'RECEIVER'. `sigma` is LOGNORMAL vol (decimal) -- see rates/_engine.py's module
    docstring; never normal/bp vol. `as_of_date` is the pricing evaluation date, passed
    through explicitly to the vendored engine (restored on exit regardless).

    `swap_tenor_years` is ROUNDED TO THE NEAREST WHOLE YEAR before being passed to the
    vendored engine -- ``options_calc.rates._engine.build_forward_swap`` constructs
    ``ql.Period(swap_tenor_years, ql.Years)``, which requires a QuantLib Integer, not a
    fractional year (unlike ``expiry_years``, which the vendored engine itself converts
    via day-count rounding). A real underlying tenor is virtually always a whole number
    of years in practice (5Y, 10Y, ...); this only bites a trade whose stored
    ``underlying_end - underlying_start`` is not an exact whole-year span, in which case
    the nearest whole year is used and the sub-year residual is silently absorbed --
    flagged here rather than left implicit."""
    if quantity == 0:
        raise ValueError("quantity is 0 -- no position to price")
    ot = _option_type_str(option_type)
    swap_tenor_years = max(int(round(swap_tenor_years)), 1)
    eval_date = qlmap.ql_date(as_of_date)

    raw = _swaption.price(
        fixed_rate, expiry_years, swap_tenor_years, r=0.0, sigma=sigma, notional=1.0,
        option_type=ot, discount_curve=curve, forecast_curve=curve, evaluation_date=eval_date,
    )
    bumped_curve = _bump_curve(curve, dv01_bump)
    bumped_price = price_swaption_only(
        fixed_rate, expiry_years, swap_tenor_years, r=0.0, sigma=sigma, notional=1.0,
        option_type=ot, discount_curve=bumped_curve, forecast_curve=bumped_curve, evaluation_date=eval_date,
    )
    dv01_per_unit = bumped_price - raw["price"]
    return _scale(quantity, raw["price"], dv01_per_unit, raw)


def price_swaption_sabr(
    quantity: float,
    fixed_rate: float,
    option_type: str,
    expiry_years: float,
    swap_tenor_years: float,
    curve,
    forward_rate: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
    as_of_date: datetime.date,
    dv01_bump: float = DV01_BUMP,
) -> RateOptionResult:
    """European swaption priced with a SABR-implied strike-dependent sigma (vendored
    ``rates/sabr.py``) fed straight into the SAME curve-input Black-76 pricer
    `price_swaption` uses -- SABR only supplies sigma, it does not replace or bypass the
    pricer (see sabr.py's own docstring). Greeks (dv01/vega/gamma/theta) are therefore
    "sticky lognormal vol" Greeks: sigma is solved ONCE from (fixed_rate, forward_rate,
    SABR params) and then held fixed while the curve/time are bumped for dv01/gamma/theta
    -- NOT a full SABR-consistent Greek that re-solves the smile at each bump. `curve` is
    the same bootstrapped OIS curve handle `price_swaption` takes; `forward_rate` is the
    curve-implied forward the smile is evaluated at (typically `CurveInputs.forward_rate`
    from `inputs.py`), supplied explicitly rather than re-derived here, matching the
    vendored function's own convention."""
    sigma = sabr_swaption_vol(fixed_rate, forward_rate, expiry_years, alpha, beta, rho, nu)
    return price_swaption(
        quantity, fixed_rate, option_type, expiry_years, swap_tenor_years,
        curve, sigma, as_of_date, dv01_bump=dv01_bump,
    )


def price_cap_floor(
    quantity: float,
    payoff: str,
    strike: float,
    start_years: float,
    tenor_years: float,
    curve,
    sigma: float,
    as_of_date: datetime.date,
    freq_months: int = 6,
    dv01_bump: float = DV01_BUMP,
) -> RateOptionResult:
    """Interest rate cap (payoff='CAP') or floor (payoff='FLOOR'), Black-76 strip
    (vendored ``rates/cap_floor.py``), priced off `curve` (both discount_curve and
    forecast_curve -- see this module's docstring "CURVE INPUTS" section). `sigma` is
    ONE flat lognormal vol applied to every caplet/floorlet in the strip -- see that
    module's "FLAT VOL ACROSS THE STRIP" caveat, inherited unchanged here."""
    if quantity == 0:
        raise ValueError("quantity is 0 -- no position to price")
    if payoff not in ("CAP", "FLOOR"):
        raise ValueError(f"payoff must be 'CAP' or 'FLOOR', got {payoff!r}")
    fn = _cap_floor.price_cap if payoff == "CAP" else _cap_floor.price_floor
    is_cap = payoff == "CAP"
    eval_date = qlmap.ql_date(as_of_date)

    raw = fn(strike, start_years, tenor_years, r=0.0, sigma=sigma, notional=1.0, freq_months=freq_months,
             discount_curve=curve, forecast_curve=curve, evaluation_date=eval_date)
    bumped_curve = _bump_curve(curve, dv01_bump)
    bumped_price = price_cap_floor_only(
        1.0, strike, start_years, tenor_years, r=0.0, sigma=sigma, freq_months=freq_months, cap=is_cap,
        discount_curve=bumped_curve, forecast_curve=bumped_curve, evaluation_date=eval_date,
    )
    dv01_per_unit = bumped_price - raw["price"]
    return _scale(quantity, raw["price"], dv01_per_unit, raw)


_HW_SIGMA_MAX = 0.05  # matches vendor bermudan_swaption.py::_HW_VOLATILITY_PLAUSIBLE_MAX


def price_bermudan_swaption(
    quantity: float,
    fixed_rate: float,
    option_type: str,
    first_exercise_years: float,
    swap_tenor_years: float,
    exercise_dates: List[datetime.date],
    curve,
    hw_mean_reversion: float,
    hw_sigma: float,
    as_of_date: datetime.date,
    tree_steps: int = 100,
    dv01_bump: float = DV01_BUMP,
    vega_bump: float = 1e-3,
) -> RateOptionResult:
    """Bermudan swaption, one-factor Hull-White + trinomial tree (vendored
    ``rates/bermudan_swaption.py``), priced off `curve` (both discount_curve and
    forecast_curve). See ``engine/rates_vol/__init__.py``'s "Bermudan / Hull-White model
    risk" section before using ANY output of this function for real risk/P&L --
    uncalibrated (or ``calibration.py``-fit but still one-factor) model parameters,
    one-factor curve dynamics, and a fixed (un-converged) tree step count are all real,
    unresolved limitations inherited from the vendored library.

    `exercise_dates`: the trade's OWN staged exercise dates (``datetime.date``), REQUIRED
    and must be non-empty -- passed straight through to the vendored engine's
    ``exercise_dates=`` parameter (bypassing its mechanically-generated, evenly-spaced
    default schedule entirely). This is the 2026-09-17 replacement for the earlier
    ``exercise_frequency_years``-inferred-from-average-spacing approximation -- see
    ``store.py`` for the "no exercise dates -> skip" rule (this function itself only
    validates non-empty; the richer skip reasoning lives in ``store.py`` since it has the
    trade context). `first_exercise_years` still determines the underlying swap's OWN
    start date (via the vendored engine's ``floored_start_date``) independently of
    `exercise_dates` -- the two must be consistent with each other (the trade's own
    `underlying_start` and staged `exercise_dates` should agree), which this function
    does not cross-validate; the vendored engine's own `_validate_explicit_exercise_dates`
    checks `exercise_dates` only against the evaluation date and the swap's maturity.

    `hw_sigma` is validated by the vendored engine itself against `(0, 0.05]` (a
    plausible one-factor Hull-White short-rate vol range) -- an out-of-range value
    raises `ValueError` from the vendored code, which this function does NOT catch (the
    caller, `store.py`, turns that into a structured skip -- never a silently-clamped
    number).

    Greeks here are THIS PACKAGE'S OWN bump-and-reprice additions (the vendored pricer
    returns price only -- see its docstring): dv01 bumps the curve (see this module's
    docstring "DV01 convention"); gamma is the second difference of the same bump; theta
    is a one-day reduction of `first_exercise_years`; vega bumps `hw_sigma` itself
    (clamped to stay inside the vendored engine's own valid range) -- explicitly NOT a
    market-observable lognormal swaption vol sensitivity, see the caveat above.
    """
    if quantity == 0:
        raise ValueError("quantity is 0 -- no position to price")
    if not exercise_dates:
        raise ValueError("exercise_dates must be a non-empty list of dates")
    ot = _option_type_str(option_type)
    swap_tenor_years = max(int(round(swap_tenor_years)), 1)  # see price_swaption's docstring -- ql.Period needs an Integer
    eval_date = qlmap.ql_date(as_of_date)

    def _reprice(t_exercise, curve_, sigma, dates_):
        return _bermudan.price_bermudan_swaption(
            fixed_rate, t_exercise, swap_tenor_years, 1.0,  # exercise_frequency ignored -- exercise_dates given
            r=0.0, notional=1.0, option_type=ot,
            discount_curve=curve_, forecast_curve=curve_, evaluation_date=eval_date,
            exercise_dates=dates_,
            hw_mean_reversion=hw_mean_reversion, hw_volatility=sigma, tree_steps=tree_steps,
        )

    npv_per_unit = _reprice(first_exercise_years, curve, hw_sigma, exercise_dates)

    up_curve = _bump_curve(curve, dv01_bump)
    down_curve = _bump_curve(curve, -dv01_bump)
    up_rate_npv = _reprice(first_exercise_years, up_curve, hw_sigma, exercise_dates)
    down_rate_npv = _reprice(first_exercise_years, down_curve, hw_sigma, exercise_dates)
    dv01_per_unit = up_rate_npv - npv_per_unit
    gamma_per_unit = (up_rate_npv - 2 * npv_per_unit + down_rate_npv) / (dv01_bump ** 2)

    up_sigma = min(hw_sigma + vega_bump, _HW_SIGMA_MAX)
    if up_sigma > hw_sigma:
        vega_reprice = _reprice(first_exercise_years, curve, up_sigma, exercise_dates)
        vega_per_unit = (vega_reprice - npv_per_unit) / (up_sigma - hw_sigma)
    else:  # hw_sigma already at the vendored engine's max -- cannot bump upward
        vega_per_unit = 0.0

    h_T = 1.0 / 365.0
    theta_reprice = _reprice(max(first_exercise_years - h_T, 1e-6), curve, hw_sigma, exercise_dates)
    theta_per_unit = theta_reprice - npv_per_unit

    raw = {"price": npv_per_unit, "gamma": gamma_per_unit, "theta": theta_per_unit, "vega": vega_per_unit}
    return _scale(quantity, npv_per_unit, dv01_per_unit, raw,
                  gamma=gamma_per_unit, theta=theta_per_unit, vega=vega_per_unit)
