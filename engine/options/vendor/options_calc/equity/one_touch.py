"""One-touch and no-touch options on equities and equity indexes.

Payoff: a FIXED cash amount depending on whether the underlying EVER
touches a specified barrier level DURING the option's life -- not just
where it ends up at expiry (that's digital.py) and not "touch decides
whether a further vanilla payoff exists" (that's barrier.py). This is the
genuinely path-dependent cousin of a digital option:

  - "one-touch": pays the cash amount if the barrier IS touched at any
    point before expiry; pays nothing if it never touches
  - "no-touch": pays the cash amount if the barrier is NEVER touched;
    pays nothing if it is touched at any point

One-touch and no-touch (same barrier, otherwise identical) are
complementary: exactly one of "touched" or "never touched" happens, so
their prices sum to the discounted cash payout -- a no-arbitrage identity,
verified in tests.

PAYOUT TIMING: this uses the "deferred" convention -- if triggered, the
cash is paid at EXPIRY, not immediately at the moment of touch (an
"immediate" one-touch, which pays right away, is a different, related
instrument this does not implement). Deferred is simpler to price
(discounting is unambiguous: always back to today from expiry) and is
common market practice.

Exercise: constructed as American internally (QuantLib requires this for
its binary-barrier engine, since the touch condition must be monitored
continuously through the option's life), with payoffAtExpiry=True to get
the deferred payout convention described above. Model: Black-Scholes-
Merton, closed-form (QuantLib's AnalyticBinaryBarrierEngine). Greeks by
bump-and-reprice, same as barrier.py and digital.py.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks

_BARRIER_DIRECTIONS = {
    "up": (ql.Barrier.UpIn, ql.Barrier.UpOut),
    "down": (ql.Barrier.DownIn, ql.Barrier.DownOut),
}

# A call struck just above zero is in-the-money for any positive spot, so
# once the barrier condition activates the payoff, it always pays out --
# exactly the "does it pay a fixed amount, unconditional on level" shape
# a touch option needs. Not user-configurable; it's an implementation
# detail of how the cash-or-nothing payoff is built, not a real strike.
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


def one_touch(S, barrier, T, r, sigma, cash_payout=1.0, direction="up", dividend_yield=0.0):
    """Price a one-touch option: pays cash_payout if the barrier is EVER
    touched before expiry, nothing otherwise.

    S: spot price
    barrier: the trigger level (must be above S for direction='up',
        below S for direction='down')
    T: time to expiry, in years
    r: risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    cash_payout: the fixed amount paid if touched
    direction: 'up' or 'down'
    dividend_yield: continuous dividend yield (annual, decimal)
    """
    if direction not in _BARRIER_DIRECTIONS:
        raise ValueError(f"direction must be one of {list(_BARRIER_DIRECTIONS)}")

    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, barrier, T_, r_, sigma_, cash_payout, direction, "one-touch", dividend_rate_)

    return finite_difference_greeks(price_only, S, T, r, sigma, dividend_yield)


def no_touch(S, barrier, T, r, sigma, cash_payout=1.0, direction="up", dividend_yield=0.0):
    """Price a no-touch option: pays cash_payout if the barrier is NEVER
    touched before expiry, nothing otherwise.

    Arguments: same as one_touch().
    """
    if direction not in _BARRIER_DIRECTIONS:
        raise ValueError(f"direction must be one of {list(_BARRIER_DIRECTIONS)}")

    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, barrier, T_, r_, sigma_, cash_payout, direction, "no-touch", dividend_rate_)

    return finite_difference_greeks(price_only, S, T, r, sigma, dividend_yield)
