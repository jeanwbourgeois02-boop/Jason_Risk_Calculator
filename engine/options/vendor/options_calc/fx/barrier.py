"""Barrier options on FX pairs.

Same payoff/model as equity/barrier.py, with the foreign risk-free rate
substituted for the dividend yield (Garman-Kohlhagen-style). This is
exactly the "KIKO" (knock-in-knock-out) structure referenced earlier as a
standard, cheap way macro FX desks and corporates express a view or hedge
-- cheaper premium than a vanilla, in exchange for giving up (or requiring)
a barrier touch.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks
from ._conventions import add_premium_adjusted_delta, add_forward_delta

_BARRIER_TYPES = {
    "up-and-out": ql.Barrier.UpOut,
    "down-and-out": ql.Barrier.DownOut,
    "up-and-in": ql.Barrier.UpIn,
    "down-and-in": ql.Barrier.DownIn,
}


def _price_only(S, K, barrier, rebate, T, r, sigma, option_type, barrier_type, dividend_rate):
    today, maturity, process = build_process(S, K, T, r, sigma, dividend_rate)

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    ql_barrier_type = _BARRIER_TYPES[barrier_type]
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.BarrierOption(ql_barrier_type, barrier, rebate, payoff, exercise)
    option.setPricingEngine(ql.AnalyticBarrierEngine(process))
    return option.NPV()


def price(S, K, barrier, T, domestic_rate, foreign_rate, sigma, option_type="call",
          barrier_type="up-and-out", rebate=0.0):
    """Price a European barrier FX option.

    S: spot exchange rate (domestic per unit of foreign, e.g. USD per EUR
        for EUR/USD)
    K: strike (same quoting convention as S)
    barrier: the barrier level (must be above S for "up-*", below S for "down-*")
    T: time to expiry, in years
    domestic_rate: domestic risk-free rate (annual, decimal)
    foreign_rate: foreign risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    barrier_type: one of 'up-and-out', 'down-and-out', 'up-and-in', 'down-and-in'
    rebate: cash amount paid if the option is knocked out; 0 for none
    """
    if barrier_type not in _BARRIER_TYPES:
        raise ValueError(f"barrier_type must be one of {list(_BARRIER_TYPES)}")

    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(
            S_, K, barrier, rebate, T_, r_, sigma_, option_type, barrier_type, dividend_rate_
        )

    result = finite_difference_greeks(price_only, S, T, domestic_rate, sigma, foreign_rate)
    result["rho_foreign"] = result.pop("rho_dividend")
    result = add_premium_adjusted_delta(result, S)
    return add_forward_delta(result, T, foreign_rate)
