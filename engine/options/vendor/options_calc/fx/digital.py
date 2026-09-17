"""Digital (cash-or-nothing / binary) options on FX pairs.

Same payoff/model as equity/digital.py, with the foreign risk-free rate
substituted for the dividend yield. Common around known FX-moving events
(a central bank decision, an election) where a trader wants a sharp,
capped bet on direction rather than a payoff that scales with magnitude.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks
from ._conventions import add_premium_adjusted_delta, add_forward_delta


def _price_only(S, K, T, r, sigma, option_type, cash_payout, dividend_rate):
    today, maturity, process = build_process(S, K, T, r, sigma, dividend_rate)

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.CashOrNothingPayoff(ql_option_type, K, cash_payout)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
    return option.NPV()


def price(S, K, T, domestic_rate, foreign_rate, sigma, option_type="call", cash_payout=1.0):
    """Price a European cash-or-nothing digital FX option.

    S: spot exchange rate (domestic per unit of foreign, e.g. USD per EUR
        for EUR/USD)
    K: strike (same quoting convention as S)
    T: time to expiry, in years
    domestic_rate: domestic risk-free rate (annual, decimal)
    foreign_rate: foreign risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' (pays out if S > K at expiry) or 'put' (pays out
        if S < K at expiry)
    cash_payout: the fixed amount paid if in-the-money at expiry
    """
    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, K, T_, r_, sigma_, option_type, cash_payout, dividend_rate_)

    result = finite_difference_greeks(price_only, S, T, domestic_rate, sigma, foreign_rate)
    result["rho_foreign"] = result.pop("rho_dividend")
    result = add_premium_adjusted_delta(result, S)
    return add_forward_delta(result, T, foreign_rate)
