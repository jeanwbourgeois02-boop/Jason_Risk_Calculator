"""Barone-Adesi-Whaley approximation for American vanilla options.

Internal module -- not part of the public API (leading underscore), same
convention as options_calc/_engine.py. This exists purely to make implied
volatility solving for American options practical: equity/american.py's
Cox-Ross-Rubinstein tree (800 steps) is deterministic and could in
principle be re-run inside a root-finder, but rebuilding an 800-step tree
on every iteration of Newton's method is too slow to use at scale.
Barone-Adesi-Whaley (1987) is the standard industry fix -- a fast,
quasi-analytic approximation to the American option price that is cheap
enough to re-evaluate hundreds of times per implied-vol solve.

QuantLib ships this approximation directly as
ql.BaroneAdesiWhaleyApproximationEngine, so it is used here rather than
reimplementing the (quite fiddly, Newton-inside-Newton) BAW formula by
hand -- consistent with this project's general pattern of leaning on
QuantLib's own engines instead of re-deriving Black-Scholes-family math.

BAW is an APPROXIMATION, not the exact American price (only a binomial/
finite-difference/PDE method converges to the true price as steps -> inf).
It is known to be less accurate for long-dated options and deep in/out of
the money. See equity/implied_vol.py's implied_volatility_american() and
MODELS.md for the measured discrepancy against the tree pricer.
"""

import QuantLib as ql

from ._engine import build_process


def _price_only(S, K, T, r, sigma, option_type, dividend_rate):
    today, maturity, process = build_process(S, K, T, r, sigma, dividend_rate)

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(ql.BaroneAdesiWhaleyApproximationEngine(process))
    return option.NPV()


def price(S, K, T, r, sigma, option_type="call", dividend_rate=0.0):
    """Price an American vanilla option via Barone-Adesi-Whaley.

    Same arguments as equity/american.py's price(), but returns only the
    NPV (no Greeks) -- this is meant to be called many times in a tight
    loop by implied_volatility_american(), not used as the general-purpose
    American pricer (use equity/american.py's tree for that; it's the
    more accurate reference implementation).
    """
    return _price_only(S, K, T, r, sigma, option_type, dividend_rate)


_IV_MAX_EVALUATIONS = 1000
_IV_MIN_VOL = 1e-4
_IV_MAX_VOL = 5.0
_IV_ACCURACY = 1e-8
_IV_INITIAL_GUESS = 0.2


def implied_volatility(market_price, S, K, T, r, option_type, dividend_rate):
    """Back out the volatility implied by an American option's market
    price, using the Barone-Adesi-Whaley approximation as the pricer
    inside QuantLib's Newton solver. Shared by equity/implied_vol.py and
    fx/implied_vol.py -- dividend_rate plays the same dual role (real
    dividend yield, or FX foreign rate) as everywhere else in this
    package.
    """
    today, maturity, process = build_process(
        S, K, T, r, _IV_INITIAL_GUESS, dividend_rate
    )

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(ql.BaroneAdesiWhaleyApproximationEngine(process))

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
            f"S={S}, K={K}, T={T}): {exc}. Note: this solve uses the "
            "Barone-Adesi-Whaley approximation, not the tree pricer in "
            "american.py -- a price that the tree could match may still "
            "fail here if it falls outside what BAW's approximation can "
            "reproduce (e.g. very long-dated or deep ITM/OTM options)."
        ) from exc
