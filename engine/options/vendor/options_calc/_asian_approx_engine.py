"""Turnbull-Wakeman approximation for arithmetic-average Asian options.

Internal module -- not part of the public API (leading underscore), same
convention as _engine.py and _baw_engine.py. Exists purely to make implied
volatility solving for Asian options practical: equity/asian.py's Monte
Carlo pricer (20,000 samples) has genuine simulation noise, which can
confuse a Newton-style root-finder chasing a precise target price -- the
"price" the solver sees isn't a smooth, deterministic function of sigma
the way a closed-form or quasi-analytic pricer's is.

Turnbull & Wakeman (1991) is a standard, well-known FAST closed-form-style
approximation for arithmetic Asian option prices (it works by matching the
first two moments of the arithmetic average to an equivalent lognormal
distribution, then applying a Black-Scholes-style formula to that moment-
matched distribution). QuantLib ships this directly as
ql.TurnbullWakemanAsianEngine, so it is used here rather than re-deriving
the moment-matching formula by hand -- consistent with this project's
general pattern of leaning on QuantLib's own engines.

Turnbull-Wakeman is an APPROXIMATION, not the exact arithmetic-Asian price
(only Monte Carlo converges to the true price as samples -> inf). Measured
against this package's own Monte Carlo pricer (equity/asian.py) for a
representative case (S=100, K=105, T=0.5, r=5%, sigma=20%, 12 monthly
fixings): Turnbull-Wakeman gives 2.007, Monte Carlo gives 1.970 -- roughly
1.9% apart, a reasonable level of agreement for two genuinely different
methods (one a moment-matching approximation, one a sampling method with
its own statistical noise), but not identical. DiscreteAveragingAsianOption
does not expose QuantLib's own impliedVolatility() the way VanillaOption
and BarrierOption do (unlike the American/barrier/digital cases in
_baw_engine.py and _engine.py), so this module implements its own
bisection-based root-finder (scipy.optimize.brentq) instead.
"""

import QuantLib as ql
from scipy.optimize import brentq

from ._engine import build_process

_MIN_VOL = 1e-4
_MAX_VOL = 5.0
_ACCURACY = 1e-8
_MAX_ITERATIONS = 200


def _price_only(S, K, T, r, sigma, option_type, dividend_rate, n_fixings):
    today, maturity, process = build_process(S, K, T, r, sigma, dividend_rate)

    fixing_dates = [
        today + int(round((i / n_fixings) * (maturity - today)))
        for i in range(1, n_fixings + 1)
    ]

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.DiscreteAveragingAsianOption(
        ql.Average().Arithmetic, 0.0, 0, fixing_dates, payoff, exercise
    )
    option.setPricingEngine(ql.TurnbullWakemanAsianEngine(process))
    return option.NPV()


def price(S, K, T, r, sigma, option_type="call", dividend_rate=0.0, n_fixings=12):
    """Price an arithmetic-average Asian option via Turnbull-Wakeman.

    Same arguments as equity/asian.py's price(), but returns only the NPV
    (no Greeks) -- meant to be called many times in a tight loop by
    implied_volatility(), not used as the general-purpose Asian pricer
    (use equity/asian.py's Monte Carlo engine for that; it converges to
    the true arithmetic-average price, Turnbull-Wakeman only approximates
    it).
    """
    return _price_only(S, K, T, r, sigma, option_type, dividend_rate, n_fixings)


def implied_volatility(market_price, S, K, T, r, option_type, dividend_rate, n_fixings):
    """Back out the volatility implied by an Asian option's market price,
    using Turnbull-Wakeman as the pricer inside a bisection root-finder
    (scipy.optimize.brentq). Shared by equity/implied_vol.py and
    fx/implied_vol.py.
    """
    def objective(sigma):
        return price(S, K, T, r, sigma, option_type, dividend_rate, n_fixings) - market_price

    try:
        low, high = objective(_MIN_VOL), objective(_MAX_VOL)
        if low * high > 0:
            raise ValueError(
                f"Could not solve for implied volatility (price={market_price}, "
                f"S={S}, K={K}, T={T}): no sign change in [{_MIN_VOL}, {_MAX_VOL}] "
                f"(price at min vol: {low + market_price}, at max vol: {high + market_price}). "
                "Note: this solve uses the Turnbull-Wakeman approximation, not the "
                "Monte Carlo pricer in asian.py -- a price achievable by Monte Carlo "
                "may still fail here if it falls outside what Turnbull-Wakeman's "
                "approximation can reproduce."
            )
        return brentq(objective, _MIN_VOL, _MAX_VOL, xtol=_ACCURACY, maxiter=_MAX_ITERATIONS)
    except RuntimeError as exc:
        raise ValueError(
            f"Could not solve for implied volatility (price={market_price}, "
            f"S={S}, K={K}, T={T}): {exc}"
        ) from exc
