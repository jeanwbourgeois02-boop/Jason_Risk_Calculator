"""Barrier options on equities and equity indexes.

Payoff: the same vanilla call/put payoff as european.py, but conditional
on whether the underlying ever touches a specified barrier level during
the option's life (not just where it ends up at expiry):

  - "up-and-out"   / "down-and-out": alive normally, but dies (becomes
    worthless) the moment the barrier is touched
  - "up-and-in"    / "down-and-in":  starts worthless, only activates if
    the barrier is touched at some point before expiry

"up" means the barrier sits above spot; "down" means it sits below spot.
Barrier options are cheaper than an equivalent vanilla precisely because
you're giving up some scenarios (knock-out) or need to earn the payoff by
hitting a level first (knock-in).

Exercise: European. Model: Black-Scholes-Merton -- a European barrier
option has a closed-form solution (the Reiner-Rubinstein formulas), unlike
American exercise or Asian averaging, so QuantLib's AnalyticBarrierEngine
handles it directly. Greeks here are still computed by bump-and-reprice
for consistency with the rest of this package's numerically-priced
instruments, rather than relying on engine-specific analytic Greek support
that may not be exposed identically across QuantLib versions.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks

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


def price(S, K, barrier, T, r, sigma, option_type="call", barrier_type="up-and-out",
          rebate=0.0, dividend_yield=0.0):
    """Price a European barrier equity/index option.

    S: spot price
    K: strike price
    barrier: the barrier level (must be above S for "up-*", below S for "down-*")
    T: time to expiry, in years
    r: risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    barrier_type: one of 'up-and-out', 'down-and-out', 'up-and-in', 'down-and-in'
    rebate: cash amount paid if the option is knocked out (or, for a
        knock-in that never activates, paid at expiry instead); 0 for none
    dividend_yield: continuous dividend yield (annual, decimal)
    """
    if barrier_type not in _BARRIER_TYPES:
        raise ValueError(f"barrier_type must be one of {list(_BARRIER_TYPES)}")

    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(
            S_, K, barrier, rebate, T_, r_, sigma_, option_type, barrier_type, dividend_rate_
        )

    return finite_difference_greeks(price_only, S, T, r, sigma, dividend_yield)
