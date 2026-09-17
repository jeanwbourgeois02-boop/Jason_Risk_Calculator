"""Asian (average-price) vanilla options on equities and equity indexes.

Payoff: max(average(S) - K, 0) for a call, max(K - average(S), 0) for a
put, where "average" is the arithmetic mean of the underlying's price on a
discrete set of fixing dates between today and expiry (not the spot price
at a single point in time, as with a European/American vanilla).

Exercise: European (you cannot early-exercise against an average that is
still being computed).

Model: Black-Scholes-Merton dynamics. There is no closed-form solution for
an arithmetic average under lognormal dynamics (the sum of lognormals is
not itself lognormal), so this is priced by Monte Carlo simulation -- the
standard, industry-normal approach for arithmetic Asian options.

Greeks via bump-and-reprice, since Monte Carlo gives a price estimate, not
an analytic sensitivity.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks

_MC_SAMPLES = 20_000
_MC_SEED = 42


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
    option.setPricingEngine(
        ql.MCDiscreteArithmeticAPEngine(
            process, "PseudoRandom", requiredSamples=_MC_SAMPLES, seed=_MC_SEED
        )
    )
    return option.NPV()


def price(S, K, T, r, sigma, option_type="call", dividend_yield=0.0, n_fixings=12):
    """Price an arithmetic-average Asian equity/index option.

    S: spot price (a single stock or an index level)
    K: strike price
    T: time to expiry, in years
    r: risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    dividend_yield: continuous dividend yield (annual, decimal)
    n_fixings: number of evenly spaced averaging dates between today and
        expiry (e.g. 12 for monthly averaging over a 1-year option)
    """
    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(
            S_, K, T_, r_, sigma_, option_type, dividend_rate_, n_fixings
        )

    return finite_difference_greeks(price_only, S, T, r, sigma, dividend_yield)
