"""American vanilla options on equities and equity indexes.

Model: Black-Scholes-Merton dynamics (same underlying process as the
European pricer), but exercise can happen at any time up to expiry, not
only at expiry. This has no closed-form solution in general -- optimal
early-exercise timing turns it into a free-boundary problem -- so it is
priced numerically.

Engine: Cox-Ross-Rubinstein binomial tree, the standard textbook method for
American options. Greeks are obtained by bump-and-reprice (finite
differences), since a tree gives a price, not an analytic sensitivity.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks

_BINOMIAL_STEPS = 800


def _price_only(S, K, T, r, sigma, option_type, dividend_rate):
    today, maturity, process = build_process(S, K, T, r, sigma, dividend_rate)

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(
        ql.BinomialVanillaEngine(process, "crr", _BINOMIAL_STEPS)
    )
    return option.NPV()


def price(S, K, T, r, sigma, option_type="call", dividend_yield=0.0):
    """Price an American equity/index option.

    S: spot price (a single stock or an index level)
    K: strike price
    T: time to expiry, in years
    r: risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    dividend_yield: continuous dividend yield (annual, decimal); 0 for a
        non-dividend-paying stock or index. Note: with zero dividends, an
        American call is never worth exercising early and should price
        identically to its European counterpart -- a useful sanity check.
    """
    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, K, T_, r_, sigma_, option_type, dividend_rate_)

    return finite_difference_greeks(price_only, S, T, r, sigma, dividend_yield)
