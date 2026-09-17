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
CURVE-INPUT SUPPORT (discount_curve / forecast_curve), ON TOP OF THE FLAT
discount_rate / forecast_rate ABOVE
--------------------------------------------------------------------------
Every builder/pricer below now also accepts `discount_curve` /
`forecast_curve` -- optional `ql.YieldTermStructureHandle` overrides that,
when given, are used DIRECTLY (relinked into the discounting engine / the
floating-rate index) instead of building a `FlatForward` curve from
`discount_rate`/`forecast_rate`. This lets a caller feed in a genuinely
bootstrapped curve (e.g. this project's own OIS bootstrap) instead of a
single flat level, while every existing flat-rate call site is completely
unaffected (curve args default to None, in which case behavior is
byte-for-byte the same as before this change). A rate and its curve
counterpart are mutually exclusive: passing `discount_rate` AND
`discount_curve` together (or `forecast_rate` AND `forecast_curve`) raises
`ValueError` -- see `_validate_curve_and_rate` -- rather than silently
picking one.

GREEKS UNDER CURVE INPUTS: SPREAD-BUMP, NOT REBUILD
------------------------------------------------------
`finite_difference_multi_curve_greeks` (unchanged, still used for the pure
flat-rate path) bumps `discount_rate`/`forecast_rate` by rebuilding a new
`FlatForward` curve at `rate + h`. That rebuild trick only makes sense for
a FLAT curve -- there is no single scalar "rate" on a genuine (possibly
non-flat) curve to add `h` to and rebuild from. Whenever either curve
input is used, `finite_difference_curve_greeks` below bumps instead by
wrapping the curve in a `ql.ZeroSpreadedTermStructure` fed a `ql.SimpleQuote`
spread of `+-h` -- a parallel shift of every zero rate on the curve by `h`
(continuously compounded), which is the standard DV01-style curve bump and
works identically whether the underlying curve is flat or genuinely
shaped. See `_bumped_curve` / `finite_difference_curve_greeks` below.
Verified numerically equivalent to a rebuilt FlatForward when the input
curve IS flat (same zero rate / discount factor to within float epsilon at
every date), so this generalizes the old flat-rate bump without changing
it for the flat case.

EVALUATION DATE: EXPLICIT, ALWAYS RESTORED ON EXIT
------------------------------------------------------
`ql.Settings.instance().evaluationDate` is a process-wide singleton, not
scoped to one curve or one pricer call. This module used to reset it to
`ql.Date.todaysDate()` on every single build call and leave it there --
silently poisoning any caller-held curve/CurveSet built at a different
evaluation date if a rates/ pricer call happened in between (a real bug
found integrating this module into a caller that caches curves by
`as_of` date). Every self-contained pricing entry point below now accepts
an optional `evaluation_date` (a `ql.Date`; default `None` reproduces the
old "use today" behavior) and runs inside `evaluation_date_scope`, which
sets the evaluation date for the duration of the call and puts back
whatever was there before on exit -- so calling any function in `rates/`
never leaves the global evaluation date different from how it found it.
"""

import contextlib

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


@contextlib.contextmanager
def evaluation_date_scope(evaluation_date=None):
    """Set ql.Settings.instance().evaluationDate for the duration of the
    `with` block and restore whatever it was set to before, on exit --
    see this module's EVALUATION DATE docstring section for why this
    matters (the evaluation date is a process-wide singleton that used to
    get reset-and-left on every call).

    `evaluation_date`: a ql.Date, or None to reproduce the historical
    default of every pricer in this module -- `ql.Date.todaysDate()`.

    Yields the resolved ql.Date actually installed, so callers can thread
    the SAME resolved date into nested build_* calls instead of each of
    them independently re-deriving "today" (which, across a multi-call
    finite-difference Greeks loop, is what keeps every bump evaluated
    against a consistent anchor date)."""
    settings = ql.Settings.instance()
    previous = settings.evaluationDate
    resolved = evaluation_date if evaluation_date is not None else ql.Date.todaysDate()
    settings.evaluationDate = resolved
    try:
        yield resolved
    finally:
        settings.evaluationDate = previous


def _validate_curve_and_rate(rate, curve, label):
    """A flat rate and its curve counterpart (e.g. discount_rate and
    discount_curve) are mutually exclusive -- passing both is ambiguous
    about which the caller actually wants used, so this refuses rather
    than silently preferring one."""
    if rate is not None and curve is not None:
        raise ValueError(
            f"Cannot pass both {label}_rate and {label}_curve -- they are "
            f"mutually exclusive (pass a flat rate OR a curve, not both)"
        )


def _resolve_curve(today, r, explicit_rate, curve, label):
    """Resolve the discount- or forecast-side YieldTermStructureHandle for
    a builder call: `curve`, if given, is used AS-IS (see CURVE-INPUT
    SUPPORT docstring section); otherwise a flat ql.FlatForward is built
    from `explicit_rate` (falling back to the shared `r` when that is also
    None), exactly reproducing the pre-curve-support behavior."""
    _validate_curve_and_rate(explicit_rate, curve, label)
    if curve is not None:
        return curve
    rate = r if explicit_rate is None else explicit_rate
    return build_curve(today, rate)


def _bumped_curve(curve_handle, spread_value):
    """Wrap `curve_handle` in a ql.ZeroSpreadedTermStructure applying a
    constant continuously-compounded zero-rate spread of `spread_value`
    (decimal, e.g. 0.01 = +100bp) across every date on the curve -- the
    parallel-shift bump `finite_difference_curve_greeks` uses in place of
    rebuilding a FlatForward (see this module's GREEKS UNDER CURVE INPUTS
    docstring section). Works whether `curve_handle` originated from a
    caller-supplied curve or from a FlatForward built internally from a
    flat rate -- verified numerically identical to rebuilding a bumped
    FlatForward in the flat case (same zero rate / discount factor to
    float epsilon at every date)."""
    spread_quote = ql.QuoteHandle(ql.SimpleQuote(spread_value))
    return ql.YieldTermStructureHandle(ql.ZeroSpreadedTermStructure(curve_handle, spread_quote))


def build_forward_swap(fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
                        discount_rate=None, forecast_rate=None,
                        discount_curve=None, forecast_curve=None,
                        evaluation_date=None):
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

    CURVE-INPUT SUPPORT: `discount_curve` / `forecast_curve` are optional
    `ql.YieldTermStructureHandle` overrides -- when given, used directly in
    place of building a FlatForward from `discount_rate`/`forecast_rate`
    (mutually exclusive with the matching flat-rate argument -- see
    _engine.py's CURVE-INPUT SUPPORT docstring section and
    `_validate_curve_and_rate`). `evaluation_date`: an optional ql.Date;
    None reproduces the historical "use today" behavior. This function
    itself does not restore the evaluation date on exit -- callers that
    want that (every public pricer in this package) wrap their call in
    `evaluation_date_scope` (see that context manager's docstring).

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
    today = evaluation_date if evaluation_date is not None else ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    discount_curve_handle = _resolve_curve(today, r, discount_rate, discount_curve, "discount")
    forecast_curve_handle = _resolve_curve(today, r, forecast_rate, forecast_curve, "forecast")
    index = build_index(forecast_curve_handle)

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
        pricingEngine=ql.DiscountingSwapEngine(discount_curve_handle),
    )
    return today, start_date, swap, discount_curve_handle


def build_swaption(fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
                    discount_rate=None, forecast_rate=None,
                    discount_curve=None, forecast_curve=None,
                    evaluation_date=None):
    """Build the ql.Swaption object (unpriced -- no engine attached yet).
    See build_forward_swap for discount_rate/forecast_rate/discount_curve/
    forecast_curve/evaluation_date semantics.
    Returns (today, expiry_date, swap, swaption, discount_curve_handle)."""
    today, expiry_date, swap, discount_curve_handle = build_forward_swap(
        fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
        discount_rate=discount_rate, forecast_rate=forecast_rate,
        discount_curve=discount_curve, forecast_curve=forecast_curve,
        evaluation_date=evaluation_date,
    )
    exercise = ql.EuropeanExercise(expiry_date)
    swaption = ql.Swaption(swap, exercise)
    return today, expiry_date, swap, swaption, discount_curve_handle


def price_swaption_only(fixed_rate, T_expiry, swap_tenor_years, r, sigma, notional, option_type,
                         discount_rate=None, forecast_rate=None,
                         discount_curve=None, forecast_curve=None,
                         evaluation_date=None):
    """Price-only helper (no Greeks) -- used both directly and as the
    bumped re-pricer inside price_swaption's finite-difference Greeks.
    See build_forward_swap for discount_rate/forecast_rate/discount_curve/
    forecast_curve semantics. Self-contained evaluation-date scope: sets
    `evaluation_date` (or today, if None) for the duration of this call
    only and restores whatever was set before on exit -- see
    evaluation_date_scope's docstring."""
    with evaluation_date_scope(evaluation_date) as today:
        _, _, _, swaption, discount_curve_handle = build_swaption(
            fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
            discount_rate=discount_rate, forecast_rate=forecast_rate,
            discount_curve=discount_curve, forecast_curve=forecast_curve,
            evaluation_date=today,
        )
        vol_handle = ql.QuoteHandle(ql.SimpleQuote(sigma))
        swaption.setPricingEngine(ql.BlackSwaptionEngine(discount_curve_handle, vol_handle))
        return swaption.NPV()


def build_cap_floor(notional, strike, T_start, tenor_years, r, freq_months, cap,
                     discount_rate=None, forecast_rate=None,
                     discount_curve=None, forecast_curve=None,
                     evaluation_date=None):
    """Build the ql.Cap or ql.Floor instrument (unpriced). A cap/floor is
    a strip of caplets/floorlets, one per accrual period of the generated
    schedule -- each is effectively a call/put on the forward rate that
    resets for that period.

    See build_forward_swap for discount_rate/forecast_rate/discount_curve/
    forecast_curve/evaluation_date semantics (rates default to `r` for
    both when omitted; curves, when given, are used directly and are
    mutually exclusive with the matching flat rate).

    Returns (today, start_date, end_date, instrument, discount_curve_handle).
    """
    today = evaluation_date if evaluation_date is not None else ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    discount_curve_handle = _resolve_curve(today, r, discount_rate, discount_curve, "discount")
    forecast_curve_handle = _resolve_curve(today, r, forecast_rate, forecast_curve, "forecast")
    index = build_index(forecast_curve_handle, tenor=ql.Period(freq_months, ql.Months))

    start_date = floored_start_date(today, T_start)
    end_date = CALENDAR.advance(start_date, ql.Period(int(round(tenor_years * 12)), ql.Months))
    schedule = ql.Schedule(
        start_date, end_date, ql.Period(freq_months, ql.Months), CALENDAR,
        ql.ModifiedFollowing, ql.ModifiedFollowing, ql.DateGeneration.Forward, False,
    )
    leg = ql.IborLeg([notional], schedule, index)
    instrument = ql.Cap(leg, [strike]) if cap else ql.Floor(leg, [strike])
    return today, start_date, end_date, instrument, discount_curve_handle


def price_cap_floor_only(notional, strike, T_start, tenor_years, r, sigma, freq_months, cap,
                          discount_rate=None, forecast_rate=None,
                          discount_curve=None, forecast_curve=None,
                          evaluation_date=None):
    """Price-only helper (no Greeks) for a cap (cap=True) or floor
    (cap=False) -- used directly and as the bumped re-pricer for
    finite-difference Greeks. See build_forward_swap for
    discount_rate/forecast_rate/discount_curve/forecast_curve semantics.
    Self-contained evaluation-date scope, same as price_swaption_only."""
    with evaluation_date_scope(evaluation_date) as today:
        _, _, _, instrument, discount_curve_handle = build_cap_floor(
            notional, strike, T_start, tenor_years, r, freq_months, cap,
            discount_rate=discount_rate, forecast_rate=forecast_rate,
            discount_curve=discount_curve, forecast_curve=forecast_curve,
            evaluation_date=today,
        )
        vol_handle = ql.QuoteHandle(ql.SimpleQuote(sigma))
        instrument.setPricingEngine(ql.BlackCapFloorEngine(discount_curve_handle, vol_handle))
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


def finite_difference_curve_greeks(price_only_fn, T, discount_curve, forecast_curve, sigma):
    """Curve-input analogue of finite_difference_multi_curve_greeks above,
    used whenever a caller supplies `discount_curve`/`forecast_curve`
    (`ql.YieldTermStructureHandle`) instead of flat discount_rate/
    forecast_rate -- see this module's GREEKS UNDER CURVE INPUTS docstring
    section for why a genuine (possibly non-flat) curve needs a different
    bump mechanism than finite_difference_multi_curve_greeks's "rebuild a
    FlatForward at rate + h".

    price_only_fn(T_, discount_curve_, forecast_curve_, sigma_) -> price,
    with every other parameter already bound by the caller. Both curve
    arguments passed to price_only_fn are `ql.YieldTermStructureHandle`
    (either the original inputs, unbumped, or the output of
    `_bumped_curve`).

    delta/gamma bump `forecast_curve` by a parallel `+-h_r` zero-rate
    spread holding `discount_curve` fixed; rho bumps `discount_curve` the
    same way holding `forecast_curve` fixed -- the curve-input equivalent
    of finite_difference_multi_curve_greeks's independent delta/rho bumps,
    genuinely separable for the same reason (two different curves playing
    two different roles). Same bump-size reasoning as
    finite_difference_multi_curve_greeks (see that docstring).
    """
    h_r = 1e-2
    h_sigma = 1e-2
    h_T = 1 / 365  # one day

    price_mid = price_only_fn(T, discount_curve, forecast_curve, sigma)

    forecast_up = _bumped_curve(forecast_curve, h_r)
    forecast_down = _bumped_curve(forecast_curve, -h_r)
    price_up_f = price_only_fn(T, discount_curve, forecast_up, sigma)
    price_down_f = price_only_fn(T, discount_curve, forecast_down, sigma)
    delta = (price_up_f - price_down_f) / (2 * h_r)
    gamma = (price_up_f - 2 * price_mid + price_down_f) / (h_r ** 2)

    discount_up = _bumped_curve(discount_curve, h_r)
    discount_down = _bumped_curve(discount_curve, -h_r)
    price_up_d = price_only_fn(T, discount_up, forecast_curve, sigma)
    price_down_d = price_only_fn(T, discount_down, forecast_curve, sigma)
    rho = (price_up_d - price_down_d) / (2 * h_r) / 100

    price_less_T = price_only_fn(max(T - h_T, 1e-6), discount_curve, forecast_curve, sigma)
    theta = price_less_T - price_mid  # decay over one day, already "per day"

    price_up_sigma = price_only_fn(T, discount_curve, forecast_curve, sigma + h_sigma)
    price_down_sigma = price_only_fn(T, discount_curve, forecast_curve, sigma - h_sigma)
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


def implied_volatility_swaption(market_price, fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
                                 evaluation_date=None):
    """Back out the lognormal vol implied by a swaption's market price,
    using QuantLib's own Newton solver on the Swaption object
    (Swaption.impliedVolatility), the same closed-form-backed approach
    options_calc/_engine.py uses for European vanillas. `evaluation_date`:
    see evaluation_date_scope -- restored on exit."""
    with evaluation_date_scope(evaluation_date) as today:
        _, expiry_date, swap, swaption, curve = build_swaption(
            fixed_rate, T_expiry, swap_tenor_years, r, notional, option_type,
            evaluation_date=today,
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


def implied_volatility_cap_floor(market_price, notional, strike, T_start, tenor_years, r, freq_months, cap,
                                  evaluation_date=None):
    """Back out the lognormal vol implied by a cap's or floor's market
    price, using QuantLib's CapFloor.impliedVolatility (Newton solver
    against the same flat-vol Black-76 model price_cap_floor_only uses).
    `evaluation_date`: see evaluation_date_scope -- restored on exit."""
    with evaluation_date_scope(evaluation_date) as today:
        _, start_date, end_date, instrument, curve = build_cap_floor(
            notional, strike, T_start, tenor_years, r, freq_months, cap,
            evaluation_date=today,
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
