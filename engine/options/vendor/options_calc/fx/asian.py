"""Asian (average-price) vanilla options on FX pairs.

Same payoff/model as equity/asian.py (arithmetic average, Monte Carlo
pricing) with the foreign risk-free rate substituted for the dividend yield
(Garman-Kohlhagen-style substitution). Common in commodity-linked FX and
corporate FX hedging, where a company wants protection on its *average*
conversion rate over a period rather than a single date.

Output includes 'delta_premium_adjusted', 'delta_forward',
'delta_forward_premium_adjusted', and 'rho_foreign' as separate fields --
see fx/european.py and fx/_conventions.py for the explanation.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks
from ._conventions import add_premium_adjusted_delta, add_forward_delta

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


def price(
    S, K, T, domestic_rate, foreign_rate, sigma, option_type="call", n_fixings=12
):
    """Price an arithmetic-average Asian FX option.

    S: spot exchange rate (domestic per unit of foreign, e.g. USD per EUR
        for EUR/USD)
    K: strike (same quoting convention as S)
    T: time to expiry, in years
    domestic_rate: domestic risk-free rate (annual, decimal)
    foreign_rate: foreign risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    n_fixings: number of evenly spaced averaging dates between today and
        expiry
    """
    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(
            S_, K, T_, r_, sigma_, option_type, dividend_rate_, n_fixings
        )

    result = finite_difference_greeks(price_only, S, T, domestic_rate, sigma, foreign_rate)
    result["rho_foreign"] = result.pop("rho_dividend")
    result = add_premium_adjusted_delta(result, S)
    return add_forward_delta(result, T, foreign_rate)
