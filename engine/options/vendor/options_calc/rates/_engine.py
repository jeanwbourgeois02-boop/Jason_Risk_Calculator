"""Shared QuantLib plumbing for interest-rate derivatives (swaptions,
caps/floors).

This is a DELIBERATELY SEPARATE parallel to options_calc/_engine.py, not an
extension of it. Equity/FX pricers in this package price a spot-based
underlying (Black-Scholes-Merton / Garman-Kohlhagen): a single quoted price
S evolves lognormally and the option pays off against a strike on that
price. Rates derivatives are fundamentally different: there is no "spot" --
a swaption pays off based on a FORWARD swap rate observed at the option's
expiry, and a cap/floor is a strip of options on FORWARD LIBOR-style rates,
one per accrual period. Both are priced here with the market-standard
Black-76 lognormal-forward-rate model (forward rate is lognormal under its
own annuity/forward measure), not Black-Scholes-Merton on a spot. Forcing
this into the existing S/K/dividend_rate-shaped engine would have been
misleading, so it gets its own module instead.

MULTI-CURVE SUPPORT (discount_rate vs forecast_rate), AND WHAT'S STILL
SIMPLIFIED ABOUT IT
--------------------------------------------------------------------------
Real desks build a discounting curve (e.g. OIS/SOFR) and a separate
forecasting curve per index tenor (e.g. 3M or 6M LIBOR/term-SOFR), each
bootstrapped from market instruments. This module now supports TWO
independent flat rates -- `discount_rate` (used by every
ql.DiscountingSwapEngine / as the curve fed to ql.BlackSwaptionEngine /
ql.BlackCapFloorEngine) and `forecast_rate` (used only to build the
floating-rate index that the floating leg / caplets forecast off of).
Every public pricer in swaption.py / cap_floor.py still defaults
`forecast_rate` to `discount_rate` when not supplied, and both still
default to the single `r` argument when neither is supplied -- so every
existing single-rate call site is completely unaffected. Consequences,
stated plainly:
  - With two distinct rates, this module's "delta" (sensitivity to the
    forecast/floating-index rate) and "rho" (sensitivity to the discount
    rate) are now genuinely separable risks -- bumping one leaves the
    other's curve untouched. See
    _engine.py's finite_difference_multi_curve_greeks docstring for
    exactly how each Greek is computed under two curves, and
    finite_difference_rate_greeks for the original single-curve
    behavior/caveat this replaces when only `r` is given.
  - BOTH curves are still flat ql.FlatForward curves, not bootstrapped
    from real market instruments (bills, futures, swaps, OIS) the way a
    real curve is built. This models a *level* discounting/forecasting
    basis, not a real curve shape or its term structure of basis. A
    genuinely production curve-build is a further step, out of scope
    here.
  - No convexity adjustment, no CMS-style measure change. This module
    also does not attempt to model the LIBOR-discounting-vs-OIS-
    discounting historical transition or any cross-currency basis --
    `discount_rate` here is simply "the rate used to discount," whatever
    curve a caller intends that to represent (OIS, SOFR, or otherwise).

VOLATILITY CONVENTION: LOGNORMAL (SHIFTED-LOGNORMAL WITH ZERO SHIFT), NOT
NORMAL/BASIS-POINT VOL
--------------------------------------------------------------------------
Rates markets quote vol BOTH ways: lognormal ("Black") vol as a decimal
(e.g. 0.20 = 20%), and normal ("Bachelier") vol in basis points on the
rate itself (e.g. 80bp). This module exclusively uses LOGNORMAL vol, fed
straight into QuantLib's ql.BlackSwaptionEngine / ql.BlackCapFloorEngine
(displacement=0, i.e. standard Black-76, not shifted-lognormal). Every
`sigma` argument in this asset class means lognormal vol as a decimal
(0.20, not 20 and not "20bp"). Silently mixing the two conventions is a
classic, real desk-blowup-grade bug -- doubly worth flagging here because
this module's sibling asset classes (equity/FX) also use a lognormal
sigma, but on a spot price rather than a forward rate, so the *number*
looks familiar while the thing it's a volatility OF is completely
different.

NO BOOTSTRAPPED CURVE, NO SABR / TERM-STRUCTURE VOL MODEL
-----------------------------------------------------------
Flat vol only (one sigma per call, like every other pricer in this
package) -- no smile, no skew, no SABR. A real swaption/cap desk needs a
vol cube (expiry x tenor x strike) and frequently a SABR fit per
expiry/tenor bucket to handle the smile. That is explicitly OUT OF SCOPE
for this first pass; see MODELS.md's roadmap note.

DAY COUNT / CALENDAR
---------------------
Actual365Fixed and NullCalendar, matching the rest of options_calc, for
the same reason: a project-wide simplifying convention rather than a
currency-correct market convention (e.g. real USD swaps use Actual/360
floating legs and 30/360 fixed legs; real calendars are currency-specific
holiday calendars). Documented here so nobody mistakes this for a
production-grade convention-accurate swap.
"""

import QuantLib as ql

DAY_COUNTER = ql.Actual365Fixed()
CALENDAR = ql.NullCalendar()
CURRENCY = ql.USDCurrency()  # arbitrary -- NullCalendar means no currency-specific holidays apply anyway
IBOR_SETTLEMENT_DAYS = 2

# Minimum gap (in days) enforced between the evaluation date and any
# schedule's start date. Without this, a cap/floor or swap whose first
# floating period starts "today" ends up needing a fixing dated a couple
# of business days IN THE PAST (index fixing lag), which QuantLib refuses
# to forecast (it wants a historical fixing that was never recorded, since
# this library has no fixing history). A few days of headroom keeps every
# forecasted fixing date safely in the future. This only matters for
# start dates very close to "now" -- it is invisible for any realistic
# forward-starting instrument (a 5y-into-10y swaption, a cap starting in
# 3 months, etc).
_MIN_START_LAG_DAYS = IBOR_SETTLEMENT_DAYS + 3


def year_fraction_to_period(T):
    """Convert a time-to-expiry in years into a QuantLib Period of whole
    days, rounding half-up. Mirrors options_calc/_engine.py's
    year_fraction_to_date -- same day-count-rounding trade-off (small
    numerical discrepancies vs. hand-computed "textbook" T values are
    expected and are why this package's tests check identities/structural
    properties instead of fixed reference numbers)."""
    return ql.Period(int(T * 365 + 0.5), ql.Days)


def build_curve(evaluation_date, r):
    """A single flat YieldTermStructureHandle used for both discounting
    and (via build_index) forecasting the floating leg."""
    return ql.YieldTermStructureHandle(
        ql.FlatForward(evaluation_date, r, DAY_COUNTER)
    )


def build_index(curve_handle, tenor=ql.Period(6, ql.Months)):
    """A generic Ibor-style index (e.g. a stand-in for 6M term SOFR /
    LIBOR) forecasting off the same flat curve used for discounting.
    Not tied to any real published index -- there is no live-fixings
    dependency, which is deliberate: every price in this module is fully
    reproducible from (r, sigma, dates) alone."""
    return ql.IborIndex(
        "GenericIndex", tenor, IBOR_SETTLEMENT_DAYS, CURRENCY,
        CALENDAR, ql.ModifiedFollowing, False, DAY_COUNTER, curve_handle,
    )


def floored_start_date(today, T_start):
    """Turn a time-to-start in years into an absolute start Date, floored
    so it sits at least _MIN_START_LAG_DAYS ahead of `today` (see that
    constant's docstring). For any realistic forward-starting instrument
    this floor never binds."""
    requested = today + int(T_start * 365 + 0.5)
    minimum = CALENDAR.advance(today, ql.Period(_MIN_START_LAG_DAYS, ql.Days))
    return max(requested, minimum)


def _resolve_curve_rates(r, discount_rate, forecast_rate):
    """Shared default-resolution for the two-curve upgrade (see MULTI-CURVE
    SUPPORT note below): `discount_rate`/`forecast_rate` are optional and
    each independently fall back to `r` when omitted, so every existing
    single-rate caller (`r` alone) is completely unaffected -- it still
    gets one flat rate used for both discounting and forecasting, exactly
    as before. Passing both explicitly is what actually introduces a
    discounting/forecasting basis."""
    d_rate = r if discount_rate is None else discount_rate
    f_rate = r if forecast_rate is None else forecast_rate
    return d_rate, f_rate


def build_forward_swap(fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
                        discount_rate=None, forecast_rate=None):
    """Build the forward-starting VanillaSwap underlying a swaption:
    starts at T_expiry (years from today), runs for swap_tenor_years.

    option_type: 'payer' -> pay fixed / receive float, 'receiver' -> the
    reverse -- the swaption's exercise decision is "do I want to enter
    this swap", so a payer swaption's underlying is a payer swap and
    likewise for receiver.

    MULTI-CURVE SUPPORT: `discount_rate` and `forecast_rate` are optional
    overrides. If omitted, both default to `r` (the original single-flat-
    curve behavior -- see _engine.py module docstring). If supplied, the
    swap's floating leg is forecast off a SEPARATE flat curve
    (`forecast_rate`) from the one used to discount both legs
    (`discount_rate`), representing a discounting/forecasting basis (still
    both flat -- not bootstrapped from real market instruments, that is a
    further step beyond this module's scope). This is what makes "delta"
    (forecast-rate sensitivity) and "rho" (discount-rate sensitivity)
    genuinely separable risks instead of two views of the same bump.

    SIMPLIFICATION: the floating leg is assumed to reprice at its own
    forecasting curve with zero spread -- a standard simplifying
    assumption for a first-pass swaption pricer, now with two curves
    instead of one. Fixed leg is annual, 30/360-free (this project's
    Actual365Fixed everywhere); floating leg is semiannual off the generic
    6M index. Both are simplifications of real market swap conventions
    (see module docstring).

    Returns (today, expiry_date, swap, discount_curve_handle). The
    forecast curve is baked into the swap's index/floating leg already and
    is not returned separately -- callers that need to price the swap or
    a swaption on it only ever need the discount curve for the pricing
    engine.
    """
    d_rate, f_rate = _resolve_curve_rates(r, discount_rate, forecast_rate)

    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    discount_curve = build_curve(today, d_rate)
    forecast_curve = build_curve(today, f_rate)
    index = build_index(forecast_curve)

    start_date = floored_start_date(today, T_expiry)
    swap_type = ql.VanillaSwap.Payer if option_type == "payer" else ql.VanillaSwap.Receiver

    swap = ql.MakeVanillaSwap(
        ql.Period(swap_tenor_years, ql.Years),
        index,
        fixed_rate,
        forwardStart=ql.Period(0, ql.Days),
        effectiveDate=start_date,
        nominal=notional,
        swapType=swap_type,
        pricingEngine=ql.DiscountingSwapEngine(discount_curve),
    )
    return today, start_date, swap, discount_curve


def build_swaption(fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
                    discount_rate=None, forecast_rate=None):
    """Build the ql.Swaption object (unpriced -- no engine attached yet).
    See build_forward_swap for discount_rate/forecast_rate semantics.
    Returns (today, expiry_date, swap, swaption, discount_curve_handle)."""
    today, expiry_date, swap, discount_curve = build_forward_swap(
        fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
        discount_rate=discount_rate, forecast_rate=forecast_rate,
    )
    exercise = ql.EuropeanExercise(expiry_date)
    swaption = ql.Swaption(swap, exercise)
    return today, expiry_date, swap, swaption, discount_curve


def price_swaption_only(fixed_rate, T_expiry, swap_tenor_years, r, sigma, notional, option_type,
                         discount_rate=None, forecast_rate=None):
    """Price-only helper (no Greeks) -- used both directly and as the
    bumped re-pricer inside price_swaption's finite-difference Greeks.
    See build_forward_swap for discount_rate/forecast_rate semantics."""
    _, _, _, swaption, discount_curve = build_swaption(
        fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
        discount_rate=discount_rate, forecast_rate=forecast_rate,
    )
    vol_handle = ql.QuoteHandle(ql.SimpleQuote(sigma))
    swaption.setPricingEngine(ql.BlackSwaptionEngine(discount_curve, vol_handle))
    return swaption.NPV()


def build_cap_floor(notional, strike, T_start, tenor_years, r, freq_months, cap,
                     discount_rate=None, forecast_rate=None):
    """Build the ql.Cap or ql.Floor instrument (unpriced). A cap/floor is
    a strip of caplets/floorlets, one per accrual period of the generated
    schedule -- each is effectively a call/put on the forward rate that
    resets for that period.

    See build_forward_swap for discount_rate/forecast_rate semantics
    (defaulting to `r` for both when omitted).

    Returns (today, start_date, end_date, instrument, discount_curve_handle).
    """
    d_rate, f_rate = _resolve_curve_rates(r, discount_rate, forecast_rate)

    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    discount_curve = build_curve(today, d_rate)
    forecast_curve = build_curve(today, f_rate)
    index = build_index(forecast_curve, tenor=ql.Period(freq_months, ql.Months))

    start_date = floored_start_date(today, T_start)
    end_date = CALENDAR.advance(start_date, ql.Period(int(round(tenor_years * 12)), ql.Months))
    schedule = ql.Schedule(
        start_date, end_date, ql.Period(freq_months, ql.Months), CALENDAR,
        ql.ModifiedFollowing, ql.ModifiedFollowing, ql.DateGeneration.Forward, False,
    )
    leg = ql.IborLeg([notional], schedule, index)
    instrument = ql.Cap(leg, [strike]) if cap else ql.Floor(leg, [strike])
    return today, start_date, end_date, instrument, discount_curve


def price_cap_floor_only(notional, strike, T_start, tenor_years, r, sigma, freq_months, cap,
                          discount_rate=None, forecast_rate=None):
    """Price-only helper (no Greeks) for a cap (cap=True) or floor
    (cap=False) -- used directly and as the bumped re-pricer for
    finite-difference Greeks. See build_forward_swap for
    discount_rate/forecast_rate semantics."""
    _, _, _, instrument, discount_curve = build_cap_floor(
        notional, strike, T_start, tenor_years, r, freq_months, cap,
        discount_rate=discount_rate, forecast_rate=forecast_rate,
    )
    vol_handle = ql.QuoteHandle(ql.SimpleQuote(sigma))
    instrument.setPricingEngine(ql.BlackCapFloorEngine(discount_curve, vol_handle))
    return instrument.NPV()


def finite_difference_rate_greeks(price_only_fn, T, r, sigma):
    """Bump-and-reprice Greeks shared by price_swaption / price_cap /
    price_floor. Same reasoning as options_calc/_engine.py's
    finite_difference_greeks for WHY the bump sizes are this large (avoid
    measuring discretization noise instead of real curvature) -- see that
    function's docstring for the full explanation, it applies unchanged
    here.

    price_only_fn(T_, r_, sigma_) -> price, with every other parameter
    (notional, strike/fixed_rate, tenor, option_type, ...) already bound
    by the caller.

    IMPORTANT CAVEAT (see module docstring): because this module uses a
    SINGLE flat curve for both discounting and forecasting, bumping `r`
    moves the underlying forward rate AND the discount factor together.
    "delta" and "rho" below are therefore measuring sensitivity to the
    SAME underlying bump, not to two independent curves the way a real
    desk's delta (forward-rate risk) and rho (discounting risk) would be.
    They are reported as separate fields for interface consistency with
    the rest of this package, but in this single-curve model they will be
    numerically identical (both are d(price)/dr, just scaled differently
    -- rho is per 1 percentage point, delta is the raw per-unit
    derivative). Treat "rho" here as redundant with "delta" rather than
    as an independent discounting-only risk; a two-curve version of this
    module would be needed to separate them for real.
    """
    h_r = 1e-2
    h_sigma = 1e-2
    h_T = 1 / 365  # one day

    price_mid = price_only_fn(T, r, sigma)

    price_up_r = price_only_fn(T, r + h_r, sigma)
    price_down_r = price_only_fn(T, r - h_r, sigma)
    delta = (price_up_r - price_down_r) / (2 * h_r)
    gamma = (price_up_r - 2 * price_mid + price_down_r) / (h_r ** 2)
    rho = (price_up_r - price_down_r) / (2 * h_r) / 100

    price_less_T = price_only_fn(max(T - h_T, 1e-6), r, sigma)
    theta = price_less_T - price_mid  # decay over one day, already "per day"

    price_up_sigma = price_only_fn(T, r, sigma + h_sigma)
    price_down_sigma = price_only_fn(T, r, sigma - h_sigma)
    vega = (price_up_sigma - price_down_sigma) / (2 * h_sigma) / 100

    return {
        "price": price_mid,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
    }


def finite_difference_multi_curve_greeks(price_only_fn, T, discount_rate, forecast_rate, sigma):
    """Two-curve Greeks: the multi-curve analogue of
    finite_difference_rate_greeks above, used whenever a caller has
    supplied (or defaulted into) separate discount_rate/forecast_rate
    curves -- see _engine.py module docstring's MULTI-CURVE SUPPORT
    section and build_forward_swap.

    price_only_fn(T_, discount_rate_, forecast_rate_, sigma_) -> price,
    with every other parameter already bound by the caller.

    Unlike finite_difference_rate_greeks, delta and rho now come from
    bumping GENUINELY DIFFERENT inputs:
      - delta = d(price)/d(forecast_rate), holding discount_rate fixed --
        the floating-index forward-rate risk.
      - rho   = d(price)/d(discount_rate), holding forecast_rate fixed,
        scaled per 1 percentage point -- the discounting-curve risk.
    When a caller passes the same rate for both curves (the module's
    backward-compatible default), delta and rho are computed from two
    independent bumps of what happens to be the same rate value, and will
    still generally differ numerically from each other (they are
    sensitivities to different roles the curve plays -- forecasting vs.
    discounting -- evaluated at the same rate level, not the combined
    "bump everything at once" derivative finite_difference_rate_greeks
    reports). This is the fix for that function's documented
    delta/rho-non-independence caveat.

    Same bump-size reasoning as finite_difference_rate_greeks (see that
    docstring, and options_calc/_engine.py's finite_difference_greeks, for
    why the bump sizes are this large).
    """
    h_r = 1e-2
    h_sigma = 1e-2
    h_T = 1 / 365  # one day

    price_mid = price_only_fn(T, discount_rate, forecast_rate, sigma)

    price_up_f = price_only_fn(T, discount_rate, forecast_rate + h_r, sigma)
    price_down_f = price_only_fn(T, discount_rate, forecast_rate - h_r, sigma)
    delta = (price_up_f - price_down_f) / (2 * h_r)
    gamma = (price_up_f - 2 * price_mid + price_down_f) / (h_r ** 2)

    price_up_d = price_only_fn(T, discount_rate + h_r, forecast_rate, sigma)
    price_down_d = price_only_fn(T, discount_rate - h_r, forecast_rate, sigma)
    rho = (price_up_d - price_down_d) / (2 * h_r) / 100

    price_less_T = price_only_fn(max(T - h_T, 1e-6), discount_rate, forecast_rate, sigma)
    theta = price_less_T - price_mid  # decay over one day, already "per day"

    price_up_sigma = price_only_fn(T, discount_rate, forecast_rate, sigma + h_sigma)
    price_down_sigma = price_only_fn(T, discount_rate, forecast_rate, sigma - h_sigma)
    vega = (price_up_sigma - price_down_sigma) / (2 * h_sigma) / 100

    return {
        "price": price_mid,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
    }


_IV_MAX_EVALUATIONS = 1000
_IV_MIN_VOL = 1e-4
_IV_MAX_VOL = 5.0
_IV_ACCURACY = 1e-8
_IV_INITIAL_GUESS = 0.20


def implied_volatility_swaption(market_price, fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type):
    """Back out the lognormal vol implied by a swaption's market price,
    using QuantLib's own Newton solver on the Swaption object
    (Swaption.impliedVolatility), the same closed-form-backed approach
    options_calc/_engine.py uses for European vanillas."""
    today, expiry_date, swap, swaption, curve = build_swaption(
        fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type
    )
    try:
        return swaption.impliedVolatility(
            market_price, curve, _IV_INITIAL_GUESS, _IV_ACCURACY,
            _IV_MAX_EVALUATIONS, _IV_MIN_VOL, _IV_MAX_VOL,
        )
    except RuntimeError as exc:
        raise ValueError(
            f"Could not solve for implied volatility (price={market_price}, "
            f"fixed_rate={fixed_rate}, T_expiry={T_expiry}): {exc}"
        ) from exc


def implied_volatility_cap_floor(market_price, notional, strike, T_start, tenor_years, r, freq_months, cap):
    """Back out the lognormal vol implied by a cap's or floor's market
    price, using QuantLib's CapFloor.impliedVolatility (Newton solver
    against the same flat-vol Black-76 model price_cap_floor_only uses)."""
    today, start_date, end_date, instrument, curve = build_cap_floor(
        notional, strike, T_start, tenor_years, r, freq_months, cap
    )
    try:
        return instrument.impliedVolatility(
            market_price, curve, _IV_INITIAL_GUESS, _IV_ACCURACY,
            _IV_MAX_EVALUATIONS, _IV_MIN_VOL, _IV_MAX_VOL,
        )
    except RuntimeError as exc:
        raise ValueError(
            f"Could not solve for implied volatility (price={market_price}, "
            f"strike={strike}, T_start={T_start}, tenor_years={tenor_years}): {exc}"
        ) from exc
