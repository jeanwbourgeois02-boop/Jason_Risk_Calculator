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

DV01 convention (matches ``engine/rates/valuation.py``'s ``BUMP = 1e-4`` sign):
``NPV(+1bp parallel bump of the derived discount_rate AND forecast_rate together) -
NPV(base)``, i.e. a +1bp bump of the two FLAT rates ``inputs.py::derive_curve_inputs``
already produced -- NOT a re-bump of the underlying OIS curve_quotes (that distinction
matters: this is a bump of the derived flat numbers this package's pricers actually take
as input, consistent with the "Flat-curve approximation" documented in
``engine/rates_vol/__init__.py``).

Every other Greek (vega, gamma, theta) for the Black-76 pricers (swaption, cap/floor,
SABR-priced swaption) comes directly from the vendored engine's own bump-and-reprice
Greeks dict (``finite_difference_multi_curve_greeks`` inside ``rates/_engine.py``) at
unit notional, then scaled by ``quantity`` the same way NPV is. The Bermudan pricer's
vega/gamma/theta are NOT vendor-provided (the vendored ``bermudan_swaption.py`` docstring
says so explicitly -- Greeks under a Hull-White tree are expensive per bump and the
vendor does not attempt them) -- this module adds its own bump-and-reprice for Bermudan,
with the real caveat that "vega" there bumps the Hull-White model's own (uncalibrated)
``sigma``, NOT a market-observable lognormal swaption vol. See ``engine/rates_vol/
__init__.py``'s "Bermudan / Hull-White model risk" section before using any Bermudan
Greek for real risk/P&L.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from engine.options.vendor.options_calc.rates import bermudan_swaption as _bermudan
from engine.options.vendor.options_calc.rates import cap_floor as _cap_floor
from engine.options.vendor.options_calc.rates import swaption as _swaption
from engine.options.vendor.options_calc.rates.sabr import sabr_swaption_vol

DV01_BUMP = 1e-4  # 1 basis point, matches engine/rates/valuation.py::BUMP


@dataclass
class RateOptionResult:
    npv_per_unit: float          # long-holder's Black-76/HW-tree value per 1 unit notional
    npv_total: float             # quantity * npv_per_unit -- sign(quantity) already applied
    dv01: float                  # quantity * per-unit DV01 (+1bp parallel bump of discount_rate
                                  # and forecast_rate together)
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
    discount_rate: float,
    forecast_rate: float,
    sigma: float,
    dv01_bump: float = DV01_BUMP,
) -> RateOptionResult:
    """European swaption, Black-76 (vendored ``rates/swaption.py``). `option_type` is
    'PAYER' or 'RECEIVER'. `sigma` is LOGNORMAL vol (decimal) -- see rates/_engine.py's
    module docstring; never normal/bp vol.

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

    raw = _swaption.price(
        fixed_rate, expiry_years, swap_tenor_years, r=discount_rate, sigma=sigma, notional=1.0,
        option_type=ot, discount_rate=discount_rate, forecast_rate=forecast_rate,
    )
    bumped = _swaption.price(
        fixed_rate, expiry_years, swap_tenor_years, r=discount_rate + dv01_bump, sigma=sigma, notional=1.0,
        option_type=ot, discount_rate=discount_rate + dv01_bump, forecast_rate=forecast_rate + dv01_bump,
    )
    dv01_per_unit = bumped["price"] - raw["price"]
    return _scale(quantity, raw["price"], dv01_per_unit, raw)


def price_swaption_sabr(
    quantity: float,
    fixed_rate: float,
    option_type: str,
    expiry_years: float,
    swap_tenor_years: float,
    discount_rate: float,
    forecast_rate: float,
    forward_rate: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
    dv01_bump: float = DV01_BUMP,
) -> RateOptionResult:
    """European swaption priced with a SABR-implied strike-dependent sigma (vendored
    ``rates/sabr.py``) fed straight into the SAME Black-76 pricer `price_swaption` uses --
    SABR only supplies sigma, it does not replace or bypass the pricer (see sabr.py's own
    docstring). Greeks (dv01/vega/gamma/theta) are therefore "sticky lognormal vol"
    Greeks: sigma is solved ONCE from (fixed_rate, forward_rate, SABR params) and then
    held fixed while discount_rate/forecast_rate/time are bumped for dv01/gamma/theta --
    NOT a full SABR-consistent Greek that re-solves the smile at each bump (the vendored
    `price_swaption_sabr` itself returns price only, with the exact same caveat -- see its
    docstring). `forward_rate` is the curve-implied forward the smile is evaluated at
    (typically `CurveInputs.forecast_rate` from `inputs.py`), supplied explicitly rather
    than re-derived here, matching the vendored function's own convention."""
    sigma = sabr_swaption_vol(fixed_rate, forward_rate, expiry_years, alpha, beta, rho, nu)
    return price_swaption(
        quantity, fixed_rate, option_type, expiry_years, swap_tenor_years,
        discount_rate, forecast_rate, sigma, dv01_bump=dv01_bump,
    )


def price_cap_floor(
    quantity: float,
    payoff: str,
    strike: float,
    start_years: float,
    tenor_years: float,
    discount_rate: float,
    forecast_rate: float,
    sigma: float,
    freq_months: int = 6,
    dv01_bump: float = DV01_BUMP,
) -> RateOptionResult:
    """Interest rate cap (payoff='CAP') or floor (payoff='FLOOR'), Black-76 strip
    (vendored ``rates/cap_floor.py``). `sigma` is ONE flat lognormal vol applied to every
    caplet/floorlet in the strip -- see that module's "FLAT VOL ACROSS THE STRIP"
    caveat, inherited unchanged here."""
    if quantity == 0:
        raise ValueError("quantity is 0 -- no position to price")
    if payoff not in ("CAP", "FLOOR"):
        raise ValueError(f"payoff must be 'CAP' or 'FLOOR', got {payoff!r}")
    fn = _cap_floor.price_cap if payoff == "CAP" else _cap_floor.price_floor

    raw = fn(strike, start_years, tenor_years, r=discount_rate, sigma=sigma, notional=1.0,
             freq_months=freq_months, discount_rate=discount_rate, forecast_rate=forecast_rate)
    bumped = fn(strike, start_years, tenor_years, r=discount_rate + dv01_bump, sigma=sigma, notional=1.0,
                freq_months=freq_months, discount_rate=discount_rate + dv01_bump, forecast_rate=forecast_rate + dv01_bump)
    dv01_per_unit = bumped["price"] - raw["price"]
    return _scale(quantity, raw["price"], dv01_per_unit, raw)


_HW_SIGMA_MAX = 0.05  # matches vendor bermudan_swaption.py::_HW_VOLATILITY_PLAUSIBLE_MAX


def price_bermudan_swaption(
    quantity: float,
    fixed_rate: float,
    option_type: str,
    first_exercise_years: float,
    swap_tenor_years: float,
    exercise_frequency_years: float,
    discount_rate: float,
    forecast_rate: float,
    hw_mean_reversion: float,
    hw_sigma: float,
    tree_steps: int = 100,
    dv01_bump: float = DV01_BUMP,
    vega_bump: float = 1e-3,
) -> RateOptionResult:
    """Bermudan swaption, one-factor Hull-White + trinomial tree (vendored
    ``rates/bermudan_swaption.py``). See ``engine/rates_vol/__init__.py``'s "Bermudan /
    Hull-White model risk" section before using ANY output of this function for real
    risk/P&L -- uncalibrated model parameters, one-factor curve dynamics, a fixed
    (un-converged) tree step count, and mechanically-generated exercise dates are all
    real, unresolved limitations inherited from the vendored library.

    `hw_sigma` is validated by the vendored engine itself against `(0, 0.05]` (a
    plausible one-factor Hull-White short-rate vol range) -- an out-of-range value
    raises `ValueError` from the vendored code, which this function does NOT catch (the
    caller, `store.py`, turns that into a structured skip -- never a silently-clamped
    number).

    Greeks here are THIS PACKAGE'S OWN bump-and-reprice additions (the vendored pricer
    returns price only -- see its docstring): dv01 bumps discount_rate/forecast_rate
    together (as elsewhere in this module); gamma is the second difference of the same
    bump; theta is a one-day reduction of `first_exercise_years`; vega bumps
    `hw_sigma` itself (clamped to stay inside the vendored engine's own valid range) --
    explicitly NOT a market-observable lognormal swaption vol sensitivity, see the
    caveat above.
    """
    if quantity == 0:
        raise ValueError("quantity is 0 -- no position to price")
    ot = _option_type_str(option_type)
    swap_tenor_years = max(int(round(swap_tenor_years)), 1)  # see price_swaption's docstring -- ql.Period needs an Integer

    def _reprice(t_exercise, d_rate, f_rate, sigma):
        return _bermudan.price_bermudan_swaption(
            fixed_rate, t_exercise, swap_tenor_years, exercise_frequency_years,
            r=d_rate, notional=1.0, option_type=ot,
            discount_rate=d_rate, forecast_rate=f_rate,
            hw_mean_reversion=hw_mean_reversion, hw_volatility=sigma, tree_steps=tree_steps,
        )

    npv_per_unit = _reprice(first_exercise_years, discount_rate, forecast_rate, hw_sigma)

    up_rate_npv = _reprice(first_exercise_years, discount_rate + dv01_bump, forecast_rate + dv01_bump, hw_sigma)
    down_rate_npv = _reprice(first_exercise_years, discount_rate - dv01_bump, forecast_rate - dv01_bump, hw_sigma)
    dv01_per_unit = up_rate_npv - npv_per_unit
    gamma_per_unit = (up_rate_npv - 2 * npv_per_unit + down_rate_npv) / (dv01_bump ** 2)

    up_sigma = min(hw_sigma + vega_bump, _HW_SIGMA_MAX)
    if up_sigma > hw_sigma:
        vega_reprice = _reprice(first_exercise_years, discount_rate, forecast_rate, up_sigma)
        vega_per_unit = (vega_reprice - npv_per_unit) / (up_sigma - hw_sigma)
    else:  # hw_sigma already at the vendored engine's max -- cannot bump upward
        vega_per_unit = 0.0

    h_T = 1.0 / 365.0
    theta_reprice = _reprice(max(first_exercise_years - h_T, 1e-6), discount_rate, forecast_rate, hw_sigma)
    theta_per_unit = theta_reprice - npv_per_unit

    raw = {"price": npv_per_unit, "gamma": gamma_per_unit, "theta": theta_per_unit, "vega": vega_per_unit}
    return _scale(quantity, npv_per_unit, dv01_per_unit, raw,
                  gamma=gamma_per_unit, theta=theta_per_unit, vega=vega_per_unit)
