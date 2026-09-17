"""Shared QuantLib plumbing used by every pricer in this package.

Internal module -- not part of the public API (leading underscore). Every
public pricer file calls into this so the QuantLib setup (dates, day
counters, term structures, process construction) is written once instead of
duplicated per exercise style / asset class.
"""

import QuantLib as ql

DAY_COUNTER = ql.Actual365Fixed()
CALENDAR = ql.NullCalendar()


def year_fraction_to_date(today, T):
    """Convert a time-to-expiry in years into a QuantLib Date, rounding
    half-up (not Python's banker's rounding) to the nearest calendar day."""
    return today + int(T * 365 + 0.5)


def build_process(S, K, T, r, sigma, dividend_rate):
    """Build a BlackScholesMertonProcess plus the dates needed by an engine.

    dividend_rate plays the role of a real dividend yield for equity/index
    underlyings, or the foreign risk-free rate for FX underlyings
    (Garman-Kohlhagen is this same model with that substitution).

    Returns (today, maturity_date, process).
    """
    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    maturity = year_fraction_to_date(today, T)

    spot_handle = ql.QuoteHandle(ql.SimpleQuote(S))
    rate_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, DAY_COUNTER))
    dividend_ts = ql.YieldTermStructureHandle(
        ql.FlatForward(today, dividend_rate, DAY_COUNTER)
    )
    vol_ts = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, CALENDAR, sigma, DAY_COUNTER)
    )
    process = ql.BlackScholesMertonProcess(spot_handle, dividend_ts, rate_ts, vol_ts)
    return today, maturity, process


def price_european_vanilla(S, K, T, r, sigma, option_type, dividend_rate):
    """Price a European vanilla call/put via Black-Scholes-Merton.

    Closed-form solution, so Greeks come directly from the analytic engine
    rather than finite differences.
    """
    today, maturity, process = build_process(S, K, T, r, sigma, dividend_rate)

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(ql.AnalyticEuropeanEngine(process))

    return {
        "price": option.NPV(),
        "delta": option.delta(),
        "gamma": option.gamma(),
        "theta": option.thetaPerDay(),
        "vega": option.vega() / 100,   # per 1 vol point (1.00 = 100%)
        "rho": option.rho() / 100,     # per 1 percentage point of the r argument
        "rho_dividend": option.dividendRho() / 100,  # per 1pp of dividend_rate
    }


_IV_MAX_EVALUATIONS = 1000
_IV_MIN_VOL = 1e-4
_IV_MAX_VOL = 5.0
_IV_ACCURACY = 1e-8
_IV_INITIAL_GUESS = 0.2


def implied_volatility_european(market_price, S, K, T, r, option_type, dividend_rate):
    """Back out the volatility implied by a European vanilla option's market
    price (Newton's method via QuantLib). Shared by equity/implied_vol.py and
    fx/implied_vol.py -- dividend_rate plays the same dual role (real
    dividend yield, or FX foreign rate) as everywhere else in this package.
    """
    # sigma is a placeholder here -- it gets solved for, so its starting
    # value only matters as Newton's method's initial guess.
    today, maturity, process = build_process(
        S, K, T, r, _IV_INITIAL_GUESS, dividend_rate
    )

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.VanillaOption(payoff, exercise)

    try:
        return option.impliedVolatility(
            market_price,
            process,
            _IV_ACCURACY,
            _IV_MAX_EVALUATIONS,
            _IV_MIN_VOL,
            _IV_MAX_VOL,
        )
    except RuntimeError as exc:
        raise ValueError(
            f"Could not solve for implied volatility (price={market_price}, "
            f"S={S}, K={K}, T={T}): {exc}"
        ) from exc


def implied_volatility_digital(market_price, S, K, T, r, option_type, cash_payout, dividend_rate):
    """Back out the volatility implied by a digital (cash-or-nothing)
    option's market price. Same closed-form-backed solver as
    implied_volatility_european -- a digital's price is also analytic
    under Black-Scholes-Merton (essentially the N(d2) term scaled by
    cash_payout), so this is just as cheap and stable to invert.
    """
    today, maturity, process = build_process(
        S, K, T, r, _IV_INITIAL_GUESS, dividend_rate
    )

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.CashOrNothingPayoff(ql_option_type, K, cash_payout)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.VanillaOption(payoff, exercise)

    try:
        return option.impliedVolatility(
            market_price,
            process,
            _IV_ACCURACY,
            _IV_MAX_EVALUATIONS,
            _IV_MIN_VOL,
            _IV_MAX_VOL,
        )
    except RuntimeError as exc:
        raise ValueError(
            f"Could not solve for implied volatility (price={market_price}, "
            f"S={S}, K={K}, T={T}): {exc}"
        ) from exc


_BARRIER_TYPES = {
    "up-and-out": ql.Barrier.UpOut,
    "down-and-out": ql.Barrier.DownOut,
    "up-and-in": ql.Barrier.UpIn,
    "down-and-in": ql.Barrier.DownIn,
}


def implied_volatility_barrier(
    market_price, S, K, barrier, rebate, T, r, option_type, barrier_type, dividend_rate
):
    """Back out the volatility implied by a European barrier option's
    market price. Same closed-form-backed solver as
    implied_volatility_european -- a European barrier option has a
    closed-form solution (Reiner-Rubinstein), so this is just as cheap
    and stable to invert as the vanilla case.
    """
    if barrier_type not in _BARRIER_TYPES:
        raise ValueError(f"barrier_type must be one of {list(_BARRIER_TYPES)}")

    today, maturity, process = build_process(
        S, K, T, r, _IV_INITIAL_GUESS, dividend_rate
    )

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    ql_barrier_type = _BARRIER_TYPES[barrier_type]
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.BarrierOption(ql_barrier_type, barrier, rebate, payoff, exercise)

    try:
        return option.impliedVolatility(
            market_price,
            process,
            _IV_ACCURACY,
            _IV_MAX_EVALUATIONS,
            _IV_MIN_VOL,
            _IV_MAX_VOL,
        )
    except RuntimeError as exc:
        is_knock_out = barrier_type in ("up-and-out", "down-and-out")
        hint = (
            " Knock-out barrier prices are NOT monotonic in volatility "
            "(vega changes sign -- see MODELS.md), so a target price can "
            "sometimes correspond to two different vols, or fall outside "
            "what a simple bracketing search finds over [0.0001, 5]. "
            "Knock-in barriers do not have this problem (price is "
            "monotonic in vol)."
            if is_knock_out else ""
        )
        raise ValueError(
            f"Could not solve for implied volatility (price={market_price}, "
            f"S={S}, K={K}, T={T}, barrier_type={barrier_type}): {exc}.{hint}"
        ) from exc


_TOUCH_DIRECTIONS = {
    "up": (ql.Barrier.UpIn, ql.Barrier.UpOut),
    "down": (ql.Barrier.DownIn, ql.Barrier.DownOut),
}
_TOUCH_NOMINAL_STRIKE = 1e-6
_TOUCH_MIN_VOL = 0.01  # see implied_volatility_one_touch's docstring


def _one_touch_price_only(S, barrier, T, r, sigma, cash_payout, direction, touch_type, dividend_rate):
    today, maturity, process = build_process(S, barrier, T, r, sigma, dividend_rate)
    knock_in_type, knock_out_type = _TOUCH_DIRECTIONS[direction]
    ql_barrier_type = knock_in_type if touch_type == "one-touch" else knock_out_type
    payoff = ql.CashOrNothingPayoff(ql.Option.Call, _TOUCH_NOMINAL_STRIKE, cash_payout)
    exercise = ql.AmericanExercise(today, maturity, True)
    option = ql.BarrierOption(ql_barrier_type, barrier, 0.0, payoff, exercise)
    option.setPricingEngine(ql.AnalyticBinaryBarrierEngine(process))
    return option.NPV()


def implied_volatility_one_touch(
    market_price, S, barrier, T, r, cash_payout, direction, touch_type, dividend_rate
):
    """Back out the volatility implied by a one-touch or no-touch option's
    market price.

    Unlike implied_volatility_european/_barrier/_digital above, this does
    NOT use QuantLib's own impliedVolatility() -- QuantLib's automatic
    engine selection for that method only supports EUROPEAN-exercise
    barrier options, and one-touch/no-touch are built internally on
    AmericanExercise (see equity/one_touch.py's docstring for why).
    Calling it raises "engine not available for non-European barrier
    option". So this solves via bisection (scipy.optimize.brentq) instead
    -- against the EXACT SAME pricer/engine equity/one_touch.py and
    fx/one_touch.py use (AnalyticBinaryBarrierEngine), not an approximation
    the way _baw_engine.py/_asian_approx_engine.py are for American/Asian.
    This is possible specifically because one-touch and no-touch prices
    are cleanly MONOTONIC in volatility (touch probability strictly
    increases with vol, so one-touch price strictly increases and
    no-touch strictly decreases) -- verified by scanning price across a
    wide vol range in tests, unlike knock-out VANILLA barriers (see
    implied_volatility_barrier above), which are NOT monotonic.

    Uses a higher minimum vol bound (_TOUCH_MIN_VOL, 1%) than the other
    solvers in this module (_IV_MIN_VOL, 0.01%): AnalyticBinaryBarrierEngine
    was found (empirically, while building this) to return NaN below
    roughly 0.8% vol for typical inputs -- a QuantLib numerical edge case
    at near-zero vol, not something this package can fix. 1% is
    comfortably above that boundary and still far below any vol level
    that occurs in practice.
    """
    from scipy.optimize import brentq

    def objective(sigma):
        return _one_touch_price_only(
            S, barrier, T, r, sigma, cash_payout, direction, touch_type, dividend_rate
        ) - market_price

    low, high = objective(_TOUCH_MIN_VOL), objective(_IV_MAX_VOL)
    if low * high > 0:
        raise ValueError(
            f"Could not solve for implied volatility (price={market_price}, "
            f"S={S}, barrier={barrier}, T={T}): no sign change in "
            f"[{_TOUCH_MIN_VOL}, {_IV_MAX_VOL}]."
        )
    return brentq(objective, _TOUCH_MIN_VOL, _IV_MAX_VOL, xtol=_IV_ACCURACY, maxiter=_IV_MAX_EVALUATIONS)


def finite_difference_greeks(price_only_fn, S, T, r, sigma, dividend_rate):
    """Bump-and-reprice Greeks for pricers with no closed-form sensitivities
    (American exercise, Asian payoffs, barriers, digitals -- anything
    priced by a tree, Monte Carlo, or other numerical engine rather than a
    closed-form formula).

    price_only_fn(S, T, r, sigma, dividend_rate) -> price, with every other
    parameter (K, option_type, ...) already bound by the caller.
    dividend_rate plays its usual dual role: real dividend yield for
    equity, or the foreign risk-free rate for FX.

    This is the standard, "textbook" way to get Greeks out of a numerical
    engine: nudge one input at a time, hold the rest fixed, and measure the
    resulting price change. It is an approximation (the bump size trades off
    truncation error against numerical noise) but it is the industry-normal
    approach when no closed form exists.

    Bump sizes are relatively large (1% of spot, 1 vol point, 100bp of
    rate) rather than the infinitesimally small steps you'd use against a
    smooth closed-form price. Tree- and Monte-Carlo-based prices have small
    numerical "kinks" from their discretization -- an extremely small bump
    measures that noise instead of the option's real curvature. This is a
    standard, deliberate trade-off, not an oversight.

    Returns "rho" (sensitivity to r) and "rho_dividend" (sensitivity to
    dividend_rate) as two separate fields -- for FX pricers, that second
    one is the foreign-rate rho that a single flat "rho" would otherwise
    silently omit.
    """
    h_S = S * 1e-2
    h_sigma = 1e-2
    h_r = 1e-2
    h_div = 1e-2
    h_T = 1 / 365  # one day

    price_mid = price_only_fn(S, T, r, sigma, dividend_rate)

    price_up_S = price_only_fn(S + h_S, T, r, sigma, dividend_rate)
    price_down_S = price_only_fn(S - h_S, T, r, sigma, dividend_rate)
    delta = (price_up_S - price_down_S) / (2 * h_S)
    gamma = (price_up_S - 2 * price_mid + price_down_S) / (h_S ** 2)

    price_less_T = price_only_fn(S, max(T - h_T, 1e-6), r, sigma, dividend_rate)
    theta = price_less_T - price_mid  # decay over one day, already "per day"

    price_up_sigma = price_only_fn(S, T, r, sigma + h_sigma, dividend_rate)
    price_down_sigma = price_only_fn(S, T, r, sigma - h_sigma, dividend_rate)
    vega = (price_up_sigma - price_down_sigma) / (2 * h_sigma) / 100

    price_up_r = price_only_fn(S, T, r + h_r, sigma, dividend_rate)
    price_down_r = price_only_fn(S, T, r - h_r, sigma, dividend_rate)
    rho = (price_up_r - price_down_r) / (2 * h_r) / 100

    price_up_div = price_only_fn(S, T, r, sigma, dividend_rate + h_div)
    price_down_div = price_only_fn(S, T, r, sigma, dividend_rate - h_div)
    rho_dividend = (price_up_div - price_down_div) / (2 * h_div) / 100

    return {
        "price": price_mid,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
        "rho_dividend": rho_dividend,
    }
