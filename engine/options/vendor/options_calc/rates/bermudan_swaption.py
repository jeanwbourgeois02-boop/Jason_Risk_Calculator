"""Bermudan swaptions -- exercisable on a discrete SET of dates (typically
coinciding with the underlying swap's own fixed-leg payment dates), not
only at a single final expiry the way swaption.py's European swaption is.

WHY THIS NEEDS A DIFFERENT MODEL, NOT JUST A DIFFERENT EXERCISE FLAG
-----------------------------------------------------------------------
swaption.py prices European swaptions closed-form via
ql.BlackSwaptionEngine, because a European swaption has a single decision
date and Black-76 (lognormal forward swap rate at that one date) is
enough. A Bermudan swaption's value depends on the OPTIMAL EXERCISE
STRATEGY across multiple dates -- at each exercise date the holder must
decide "exercise now" vs "the (unknown, model-dependent) continuation
value of waiting" -- which requires modeling the evolution of the entire
term structure, not just the distribution of one forward rate at one
date. Black-76 has no notion of continuation value. This needs a genuine
term-structure (short-rate) model plus a lattice/tree or finite-difference
engine that can compute continuation values at every exercise date via
backward induction.

MODEL CHOICE: HULL-WHITE ONE-FACTOR (`ql.HullWhite`), NOT G2/TWO-FACTOR
-----------------------------------------------------------------------
QuantLib offers several short-rate models usable here (one-factor
Hull-White, two-factor G2/Hull-White, Black-Karasinski, etc). This module
uses ONE-FACTOR HULL-WHITE (`ql.HullWhite(discount_curve, a, sigma)`) with
`ql.TreeSwaptionEngine` (a trinomial-tree backward-induction engine),
chosen deliberately as the simplest defensible choice rather than the
most accurate one:
  - It is a normal (Gaussian) short-rate model with a closed-form bond
    price formula, mean-reverting at speed `a` with volatility `sigma` --
    well understood, numerically stable, and the standard "first" model
    taught/used for Bermudan swaptions before reaching for a two-factor
    model.
  - A single-factor model means ALL POINTS ON THE CURVE MOVE PERFECTLY
    CORRELATED with each other (driven by one Brownian motion) -- it
    cannot represent a genuine twist/steepening of the curve independent
    of a parallel-ish shift. A two-factor model (G2) captures much more
    realistic curve dynamics (partial decorrelation between short and
    long rates) and is what a real desk pricing Bermudan swaptions would
    typically use, especially for longer- dated or more curve-shape-
    sensitive structures. That extra realism is explicitly NOT attempted
    here.
  - `a` (mean reversion) and `sigma` (short-rate volatility) are NOT
    calibrated to market swaption/cap volatilities in this module -- the
    caller supplies them directly (with defaults chosen only to be
    "reasonable," not fit to any market). A real desk calibrates these
    two parameters to a relevant basket of co-terminal European swaptions
    (each with the same final maturity as the Bermudan, different
    expiries) so the one-factor model reprices the observable European
    swaptions correctly before trusting it for the Bermudan's early-
    exercise value. That calibration routine is a separate, non-trivial
    piece of work and is explicitly out of scope here -- see "What's
    simplified" below.
  - Discounting for the HW dynamics is tied to the SAME `discount_rate`
    curve used elsewhere in this module (see options_calc/rates/_engine.py's
    MULTI-CURVE SUPPORT). The floating leg still forecasts off
    `forecast_rate` via the underlying VanillaSwap exactly as
    swaption.py/cap_floor.py do.

WHAT'S SIMPLIFIED / OUT OF SCOPE HERE, STATED PLAINLY
--------------------------------------------------------
- NO CALIBRATION of `hw_mean_reversion`/`hw_volatility` to market data --
  supplied directly by the caller (see above).
- ONE-FACTOR, not two-factor -- see model choice discussion above.
- `ql.TreeSwaptionEngine`'s trinomial tree has its own discretization
  error controlled by `tree_steps` -- more steps converge closer to the
  model's true (continuous-time) price at increasing computational cost.
  This module does not auto-select a "good enough" step count; the
  default (100) is a reasonable-but-arbitrary starting point, not a
  convergence-tested value for every possible input combination.
- The Bermudan's exercise dates are generated mechanically (evenly spaced
  every `exercise_frequency` years from the first exercise date to just
  before the underlying swap's maturity) rather than being read off a
  real deal's actual fixed-leg payment schedule. For a real trade the
  exercise dates should exactly match the swap's own coupon dates; this
  module's generated dates are a reasonable approximation given the
  simplified swap construction _engine.py already uses (annual fixed
  leg), not a guarantee of exact alignment in every configuration.
- Inherits every other _engine.py simplification (flat curves --
  possibly two of them now, see MULTI-CURVE SUPPORT -- lognormal-vs-
  normal vol distinction is moot here since HW is a short-rate model with
  its own `sigma` in ABSOLUTE rate terms/year, not the lognormal Black
  vol used elsewhere in `rates/`; Actual365Fixed/NullCalendar day count).

I DID NOT independently re-derive Hull-White's bond-pricing formula or
`ql.TreeSwaptionEngine`'s trinomial-tree construction by hand -- both rely
entirely on QuantLib's own implementation. What I did verify: (1) that a
Bermudan swaption priced under this engine is worth at least as much as
the analogous EUROPEAN swaption priced under the SAME Hull-White model
and the SAME tree engine (the correct, model-consistent form of the
"more exercise opportunities can only add value" identity -- see
`price_european_swaption_hw` below, added specifically so this comparison
is apples-to-apples rather than comparing a tree-based Bermudan price
against a completely different model's -- Black-76's -- European price),
and (2) that the Bermudan price converges as `tree_steps` increases (does
not blow up or move wildly for a reasonable step-count range), in
tests/rates/test_bermudan_swaption.py.
"""

import QuantLib as ql

from ._engine import build_forward_swap, evaluation_date_scope, CALENDAR

_DEFAULT_NOTIONAL = 1_000_000.0
_DEFAULT_HW_MEAN_REVERSION = 0.03
_DEFAULT_HW_VOLATILITY = 0.01
_DEFAULT_TREE_STEPS = 100


def _generate_exercise_dates(start_date, end_date, exercise_frequency_years):
    """Evenly spaced exercise dates from start_date up to (but strictly
    before) end_date, every exercise_frequency_years years. See module
    docstring's "What's simplified" note on why these are mechanically
    generated rather than read off a real deal's coupon schedule."""
    step_days = max(int(round(exercise_frequency_years * 365)), 1)
    dates = []
    d = start_date
    while d < end_date:
        dates.append(d)
        d = CALENDAR.advance(d, ql.Period(step_days, ql.Days))
    if not dates:
        raise ValueError(
            "No exercise dates generated -- exercise_frequency_years is too "
            "large relative to swap_tenor, or swap_tenor is too short."
        )
    return dates


def _to_ql_date(d):
    """datetime.date -> ql.Date. Accepts a ql.Date unchanged (so a caller
    mixing ql.Date and datetime.date in the same exercise_dates list is
    not silently mishandled -- ql.Date has no .year/.month/.day trio the
    same way, so it is detected via isinstance and passed through)."""
    if isinstance(d, ql.Date):
        return d
    return ql.Date(d.day, d.month, d.year)


def _validate_explicit_exercise_dates(exercise_dates, evaluation_date, maturity_date):
    """Validate and convert a caller-supplied list of Bermudan exercise
    dates (see price_bermudan_swaption's `exercise_dates` parameter):
    non-empty, strictly increasing, all strictly before the underlying
    swap's maturity, and the first on or after the evaluation date (a
    caller cannot stage an exercise opportunity that has already passed).
    Returns the list converted to ql.Date, in the given order (NOT
    re-sorted -- an out-of-order input is a caller error, not silently
    fixed)."""
    if not exercise_dates:
        raise ValueError("exercise_dates must be a non-empty list of dates")
    ql_dates = [_to_ql_date(d) for d in exercise_dates]
    for a, b in zip(ql_dates, ql_dates[1:]):
        if not (b > a):
            raise ValueError(
                f"exercise_dates must be strictly increasing; found {a} followed by {b}"
            )
    if ql_dates[0] < evaluation_date:
        raise ValueError(
            f"first exercise date {ql_dates[0]} is before the evaluation date {evaluation_date}"
        )
    if ql_dates[-1] >= maturity_date:
        raise ValueError(
            f"exercise date {ql_dates[-1]} must be strictly before the underlying "
            f"swap's maturity {maturity_date}"
        )
    return ql_dates


_HW_VOLATILITY_PLAUSIBLE_MAX = 0.05  # real-world HW short-rate vol calibrations are ~0.5%-2%/yr


def _hull_white_tree_engine(discount_curve, hw_mean_reversion, hw_volatility, tree_steps):
    if not (0 < hw_volatility <= _HW_VOLATILITY_PLAUSIBLE_MAX):
        raise ValueError(
            f"hw_volatility={hw_volatility!r} is outside the plausible range "
            f"(0, {_HW_VOLATILITY_PLAUSIBLE_MAX}] for a Hull-White short-rate vol "
            f"(real calibrations are typically 0.005-0.02). This module does NOT "
            f"calibrate hw_volatility -- it's supplied directly (see module "
            f"docstring) -- so a typo'd value (e.g. 1.0 instead of 0.01) would "
            f"otherwise silently produce a structurally normal-looking but "
            f"economically nonsensical price with no error. If you deliberately "
            f"need a value outside this range, that suggests this module's "
            f"assumptions don't fit your use case."
        )
    model = ql.HullWhite(discount_curve, hw_mean_reversion, hw_volatility)
    return ql.TreeSwaptionEngine(model, tree_steps)


def price_bermudan_swaption(fixed_rate, first_exercise, swap_tenor, exercise_frequency, r,
                             notional=_DEFAULT_NOTIONAL, option_type="payer",
                             discount_rate=None, forecast_rate=None,
                             discount_curve=None, forecast_curve=None,
                             evaluation_date=None,
                             exercise_dates=None,
                             hw_mean_reversion=_DEFAULT_HW_MEAN_REVERSION,
                             hw_volatility=_DEFAULT_HW_VOLATILITY,
                             tree_steps=_DEFAULT_TREE_STEPS):
    """Price a Bermudan swaption under a one-factor Hull-White short-rate
    model with a trinomial-tree engine (see module docstring for why this
    model/engine combination and what it simplifies).

    fixed_rate: the swaption's strike (annual, decimal) -- same underlying
        swap's fixed rate as swaption.py.
    first_exercise: time (years from today) of the FIRST exercise
        opportunity -- also when the underlying swap starts. Ignored for
        purposes of building the exercise schedule when `exercise_dates`
        is given (see below); still used to build the underlying forward
        swap itself (its start date).
    swap_tenor: length of the underlying swap in years, starting at
        `first_exercise`.
    exercise_frequency: spacing (years) between exercise opportunities,
        e.g. 1.0 for annual Bermudan exercise. The last generated date is
        strictly before the swap's maturity (see _generate_exercise_dates).
        Ignored when `exercise_dates` is given.
    r: flat rate used for both discounting and forecasting when none of
        discount_rate/forecast_rate/discount_curve/forecast_curve are
        given (same convention as swaption.py/cap_floor.py).
    notional, option_type: same meaning as swaption.py.
    discount_rate, forecast_rate: optional multi-curve overrides, same
        semantics as swaption.py/_engine.py's MULTI-CURVE SUPPORT. Each is
        mutually exclusive with its curve counterpart below.
    discount_curve, forecast_curve: optional `ql.YieldTermStructureHandle`
        overrides -- used directly instead of a flat FlatForward, exactly
        as swaption.py/cap_floor.py (see _engine.py's CURVE-INPUT SUPPORT
        docstring section). The Hull-White model itself is built directly
        off whichever discount curve results (flat or genuine).
    evaluation_date: optional ql.Date; None reproduces "evaluate as of
        today". Always restored to its prior value on exit -- see
        _engine.py's evaluation_date_scope.
    exercise_dates: optional explicit list of exercise opportunities
        (datetime.date, or ql.Date), bypassing the mechanically-generated,
        evenly-spaced schedule entirely -- use this to match a real deal's
        actual coupon/exercise dates instead of the
        `exercise_frequency`-generated approximation (see module
        docstring's "What's simplified" note, and
        `_validate_explicit_exercise_dates`). Validated: non-empty,
        strictly increasing, all strictly before the underlying swap's
        maturity, first on or after the evaluation date -- ValueError
        otherwise. When given, `exercise_frequency` is ignored for
        schedule generation (still a required positional argument, for
        backward compatibility with the mechanically-generated path).
    hw_mean_reversion, hw_volatility: Hull-White model parameters (`a`,
        `sigma`) -- NOT calibrated to market data, supplied directly (see
        module docstring).
    tree_steps: number of time steps in the trinomial tree -- more steps
        means a more accurate (and slower) price.

    Returns the swaption price only (a single number, not a Greeks dict --
    bump-and-reprice Greeks under a Hull-White tree are meaningfully more
    expensive per bump than the closed-form Black-76 Greeks elsewhere in
    this package, and this module does not attempt them; a caller wanting
    Greeks here can bump inputs and call this function again).
    """
    with evaluation_date_scope(evaluation_date) as today:
        _, start_date, swap, discount_curve_handle = build_forward_swap(
            fixed_rate, first_exercise, swap_tenor, r, notional, option_type,
            discount_rate=discount_rate, forecast_rate=forecast_rate,
            discount_curve=discount_curve, forecast_curve=forecast_curve,
            evaluation_date=today,
        )
        end_date = swap.fixedSchedule().endDate()
        if exercise_dates is not None:
            ql_exercise_dates = _validate_explicit_exercise_dates(exercise_dates, today, end_date)
        else:
            ql_exercise_dates = _generate_exercise_dates(start_date, end_date, exercise_frequency)

        swaption = ql.Swaption(swap, ql.BermudanExercise(ql_exercise_dates))
        swaption.setPricingEngine(
            _hull_white_tree_engine(discount_curve_handle, hw_mean_reversion, hw_volatility, tree_steps)
        )
        return swaption.NPV()


def price_european_swaption_hw(fixed_rate, expiry, swap_tenor, r,
                                notional=_DEFAULT_NOTIONAL, option_type="payer",
                                discount_rate=None, forecast_rate=None,
                                discount_curve=None, forecast_curve=None,
                                evaluation_date=None,
                                hw_mean_reversion=_DEFAULT_HW_MEAN_REVERSION,
                                hw_volatility=_DEFAULT_HW_VOLATILITY,
                                tree_steps=_DEFAULT_TREE_STEPS):
    """Price a EUROPEAN swaption (single exercise date, at `expiry`) under
    the SAME Hull-White model and TreeSwaptionEngine as
    price_bermudan_swaption -- provided specifically so a Bermudan price
    can be compared against a European price on an apples-to-apples basis
    (same model, same engine, same discretization), rather than comparing
    against swaption.py's Black-76 European price, which is a different
    model entirely and would make "Bermudan >= European" a much weaker,
    model-inconsistent check. See tests/rates/test_bermudan_swaption.py.

    All arguments have the same meaning as price_bermudan_swaption's,
    minus exercise_frequency/exercise_dates (a European swaption has only
    one exercise date, at `expiry`, so there is no schedule to generate or
    override).
    """
    with evaluation_date_scope(evaluation_date) as today:
        _, start_date, swap, discount_curve_handle = build_forward_swap(
            fixed_rate, expiry, swap_tenor, r, notional, option_type,
            discount_rate=discount_rate, forecast_rate=forecast_rate,
            discount_curve=discount_curve, forecast_curve=forecast_curve,
            evaluation_date=today,
        )
        swaption = ql.Swaption(swap, ql.EuropeanExercise(start_date))
        swaption.setPricingEngine(
            _hull_white_tree_engine(discount_curve_handle, hw_mean_reversion, hw_volatility, tree_steps)
        )
        return swaption.NPV()
