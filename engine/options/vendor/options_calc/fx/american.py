"""American vanilla options on FX pairs.

Model: Garman-Kohlhagen dynamics (foreign rate substituted for dividend
yield), with American exercise. No closed-form solution -- priced with the
same binomial tree approach as equity/american.py.

Engine: Cox-Ross-Rubinstein binomial tree. Greeks via bump-and-reprice.

Output includes 'delta_premium_adjusted', 'delta_forward',
'delta_forward_premium_adjusted', and 'rho_foreign' as separate fields
alongside raw 'delta' and domestic 'rho' -- see fx/european.py and
fx/_conventions.py for the full explanation.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks
from ._conventions import add_premium_adjusted_delta, add_forward_delta

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


def price(S, K, T, domestic_rate, foreign_rate, sigma, option_type="call"):
    """Price an American FX option.

    S: spot exchange rate (domestic per unit of foreign, e.g. USD per EUR
        for EUR/USD)
    K: strike (same quoting convention as S)
    T: time to expiry, in years
    domestic_rate: domestic risk-free rate (annual, decimal)
    foreign_rate: foreign risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    """
    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, K, T_, r_, sigma_, option_type, dividend_rate_)

    result = finite_difference_greeks(price_only, S, T, domestic_rate, sigma, foreign_rate)
    result["rho_foreign"] = result.pop("rho_dividend")
    result = add_premium_adjusted_delta(result, S)
    return add_forward_delta(result, T, foreign_rate)
