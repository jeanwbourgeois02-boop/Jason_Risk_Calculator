"""Asian (average-price) vanilla options on commodity futures.

Payoff: based on the arithmetic average of the futures price over a set
of fixing dates, not the price at a single point in time -- see
equity/asian.py for the full payoff explanation. GENUINELY common in
commodities specifically: many real-world commodity hedges (e.g. an
airline hedging average jet fuel cost, a producer hedging average oil
revenue over a quarter) are Asian by nature, since the exposure being
hedged is itself an average cost/revenue over a period, not a single
date's price.

Model: Black-76 dynamics (F-as-underlying, dividend_rate=r substitution --
see european.py's docstring). No closed-form solution for an arithmetic
average, so priced by Monte Carlo, same as equity/asian.py and fx/asian.py.

NOTE on rho: same fix as european.py and american.py -- see european.py's
docstring for the full reasoning on why the two raw partials are summed.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks

_MC_SAMPLES = 20_000
_MC_SEED = 42


def _price_only(F, K, T, r, sigma, option_type, dividend_rate, n_fixings):
    today, maturity, process = build_process(F, K, T, r, sigma, dividend_rate)

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


def price(F, K, T, r, sigma, option_type="call", n_fixings=12):
    """Price an arithmetic-average Asian option on a commodity
    futures/forward price.

    F: current futures/forward price
    K: strike price
    T: time to expiry, in years
    r: discount rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    n_fixings: number of evenly spaced averaging dates between today and
        expiry (e.g. 12 for monthly averaging over a 1-year option)
    """
    def price_only(F_, T_, r_, sigma_, dividend_rate_):
        return _price_only(
            F_, K, T_, r_, sigma_, option_type, dividend_rate_, n_fixings
        )

    result = finite_difference_greeks(price_only, F, T, r, sigma, r)
    result["rho"] = result["rho"] + result.pop("rho_dividend")
    return result
