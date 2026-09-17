"""One-touch and no-touch options on FX pairs.

Same payoff/model as equity/one_touch.py, with the foreign risk-free rate
substituted for the dividend yield. Very standard in FX specifically --
"will EUR/USD touch 1.20 before expiry" is a classic, liquidly-traded
macro FX structure, distinct from a barrier option (which needs a touch
to activate/deactivate a further vanilla payoff) and from a digital
(which only checks the level at expiry, ignoring the path).

Output includes 'delta_premium_adjusted' and 'rho_foreign' as separate
fields alongside raw 'delta' and domestic 'rho' -- see fx/european.py and
fx/_conventions.py for the full explanation.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks
from ._conventions import add_premium_adjusted_delta, add_forward_delta

_BARRIER_DIRECTIONS = {
    "up": (ql.Barrier.UpIn, ql.Barrier.UpOut),
    "down": (ql.Barrier.DownIn, ql.Barrier.DownOut),
}

_NOMINAL_STRIKE = 1e-6


def _price_only(S, barrier, T, r, sigma, cash_payout, direction, touch_type, dividend_rate):
    today, maturity, process = build_process(S, barrier, T, r, sigma, dividend_rate)

    knock_in_type, knock_out_type = _BARRIER_DIRECTIONS[direction]
    ql_barrier_type = knock_in_type if touch_type == "one-touch" else knock_out_type

    payoff = ql.CashOrNothingPayoff(ql.Option.Call, _NOMINAL_STRIKE, cash_payout)
    exercise = ql.AmericanExercise(today, maturity, True)  # payoffAtExpiry=True (deferred)
    option = ql.BarrierOption(ql_barrier_type, barrier, 0.0, payoff, exercise)
    option.setPricingEngine(ql.AnalyticBinaryBarrierEngine(process))
    return option.NPV()


def one_touch(S, barrier, T, domestic_rate, foreign_rate, sigma, cash_payout=1.0, direction="up"):
    """Price a one-touch FX option: pays cash_payout if the barrier is
    EVER touched before expiry, nothing otherwise.

    S: spot exchange rate (domestic per unit of foreign, e.g. USD per EUR
        for EUR/USD)
    barrier: the trigger level (must be above S for direction='up',
        below S for direction='down')
    T: time to expiry, in years
    domestic_rate: domestic risk-free rate (annual, decimal)
    foreign_rate: foreign risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    cash_payout: the fixed amount paid if touched
    direction: 'up' or 'down'
    """
    if direction not in _BARRIER_DIRECTIONS:
        raise ValueError(f"direction must be one of {list(_BARRIER_DIRECTIONS)}")

    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, barrier, T_, r_, sigma_, cash_payout, direction, "one-touch", dividend_rate_)

    result = finite_difference_greeks(price_only, S, T, domestic_rate, sigma, foreign_rate)
    result["rho_foreign"] = result.pop("rho_dividend")
    result = add_premium_adjusted_delta(result, S)
    return add_forward_delta(result, T, foreign_rate)


def no_touch(S, barrier, T, domestic_rate, foreign_rate, sigma, cash_payout=1.0, direction="up"):
    """Price a no-touch FX option: pays cash_payout if the barrier is
    NEVER touched before expiry, nothing otherwise.

    Arguments: same as one_touch().
    """
    if direction not in _BARRIER_DIRECTIONS:
        raise ValueError(f"direction must be one of {list(_BARRIER_DIRECTIONS)}")

    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, barrier, T_, r_, sigma_, cash_payout, direction, "no-touch", dividend_rate_)

    result = finite_difference_greeks(price_only, S, T, domestic_rate, sigma, foreign_rate)
    result["rho_foreign"] = result.pop("rho_dividend")
    result = add_premium_adjusted_delta(result, S)
    return add_forward_delta(result, T, foreign_rate)
