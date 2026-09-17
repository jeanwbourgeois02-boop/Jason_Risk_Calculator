"""Named multi-leg equity/index option structures, built from
equity/european.py + structures.py's combine(). No new pricing model --
each structure just prices its individual legs and sums them.
"""

from ..structures import combine
from .european import price


def straddle(S, K, T, r, sigma, dividend_yield=0.0):
    """Long straddle: long 1 call + long 1 put, same strike and expiry.

    A pure bet on the SIZE of a move, not its direction -- profits if
    spot moves far enough either way to cover the combined premium paid.
    Long gamma, long vega, negative theta (you're paying for convexity).
    """
    call = price(S, K, T, r, sigma, "call", dividend_yield)
    put = price(S, K, T, r, sigma, "put", dividend_yield)
    return combine((call, 1.0), (put, 1.0))


def strangle(S, K_put, K_call, T, r, sigma, dividend_yield=0.0):
    """Long strangle: long 1 put at a lower strike + long 1 call at a
    higher strike (K_put < K_call), both typically out-of-the-money.

    Same directional idea as a straddle (bet on a big move either way)
    but cheaper to put on, since both legs start out-of-the-money --
    trades off lower cost for needing a larger move to profit.
    """
    put = price(S, K_put, T, r, sigma, "put", dividend_yield)
    call = price(S, K_call, T, r, sigma, "call", dividend_yield)
    return combine((put, 1.0), (call, 1.0))


def risk_reversal(S, K_put, K_call, T, r, sigma, dividend_yield=0.0, long_call=True):
    """Risk reversal: long one side, short the other, at two different
    strikes (K_put < K_call) -- a directional bet financed by selling the
    opposite side's optionality.

    long_call=True:  long the call, short the put (bullish -- financed by
                      selling downside protection)
    long_call=False: long the put, short the call (bearish -- financed by
                      selling upside)

    The price/skew relationship between the two legs is exactly what the
    FX market's "risk reversal" quote measures (see fx/structures.py) --
    here it's the equity/index equivalent, built the same way.
    """
    call = price(S, K_call, T, r, sigma, "call", dividend_yield)
    put = price(S, K_put, T, r, sigma, "put", dividend_yield)
    if long_call:
        return combine((call, 1.0), (put, -1.0))
    return combine((put, 1.0), (call, -1.0))


def collar(S, K_put, K_call, T, r, sigma, dividend_yield=0.0):
    """Collar: long a protective put at a lower strike, short a call at a
    higher strike (K_put < K_call), to finance the put's premium.

    Standard hedge for someone already holding the underlying: caps the
    downside (the put) by giving up some upside (the short call). This is
    mechanically identical to a bearish risk_reversal() -- named
    separately because "collar" signals the hedging use case (protecting
    an existing position) rather than a standalone directional bet.
    """
    put = price(S, K_put, T, r, sigma, "put", dividend_yield)
    call = price(S, K_call, T, r, sigma, "call", dividend_yield)
    return combine((put, 1.0), (call, -1.0))


def call_spread(S, K_low, K_high, T, r, sigma, dividend_yield=0.0):
    """Bull call spread (vertical spread): long a call at K_low, short a
    call at K_high (K_low < K_high).

    Cheaper, capped-upside way to express a bullish view than an outright
    long call -- the short call finances part of the premium in exchange
    for giving up gains above K_high. Max profit is capped at K_high - K_low
    minus net premium paid; max loss is the net premium.
    """
    long_call = price(S, K_low, T, r, sigma, "call", dividend_yield)
    short_call = price(S, K_high, T, r, sigma, "call", dividend_yield)
    return combine((long_call, 1.0), (short_call, -1.0))


def put_spread(S, K_low, K_high, T, r, sigma, dividend_yield=0.0):
    """Bear put spread (vertical spread): long a put at K_high, short a
    put at K_low (K_low < K_high).

    The bearish mirror of call_spread(): cheaper, capped-downside way to
    express a bearish view than an outright long put.
    """
    long_put = price(S, K_high, T, r, sigma, "put", dividend_yield)
    short_put = price(S, K_low, T, r, sigma, "put", dividend_yield)
    return combine((long_put, 1.0), (short_put, -1.0))


def butterfly(S, K_low, K_mid, K_high, T, r, sigma, dividend_yield=0.0, option_type="call"):
    """Long butterfly: long 1 option at K_low, short 2 at K_mid, long 1 at
    K_high (K_low < K_mid < K_high, typically evenly spaced).

    The opposite bet from a straddle: profits if spot pins NEAR K_mid by
    expiry, loses (up to the net premium paid) if spot moves far in either
    direction. Short gamma, short vega, positive theta near K_mid -- a bet
    on LOW realized volatility / a range-bound market, with strictly
    limited risk (max loss = net premium paid) unlike an outright short
    straddle, which has unlimited risk.
    """
    low = price(S, K_low, T, r, sigma, option_type, dividend_yield)
    mid = price(S, K_mid, T, r, sigma, option_type, dividend_yield)
    high = price(S, K_high, T, r, sigma, option_type, dividend_yield)
    return combine((low, 1.0), (mid, -2.0), (high, 1.0))


def iron_condor(S, K1, K2, K3, K4, T, r, sigma, dividend_yield=0.0):
    """Iron condor: long put at K1, short put at K2, short call at K3,
    long call at K4 (K1 < K2 < K3 < K4, four legs, two option types).

    The wider-plateau cousin of butterfly(): instead of betting spot pins
    at a single strike, this bets spot stays ANYWHERE between K2 and K3 by
    expiry -- both short legs expire worthless and you keep the net
    premium collected. K1 and K4 are cheap, far-OTM protection bought to
    cap the otherwise-unlimited risk of just selling a strangle outright
    (short put at K2 + short call at K3 alone). Like butterfly, this is a
    short-volatility, range-bound bet (short gamma, short vega, positive
    theta) but with a flat maximum-profit region between K2 and K3 rather
    than a single peak -- the trade-off is a lower max profit than a
    butterfly's peak, in exchange for profiting across a wider range of
    outcomes.

    Typically opened for a net CREDIT (the two sold near-the-money legs
    are worth more than the two bought far-OTM legs), so `price` here is
    usually negative -- money received to open the position, not paid.
    """
    long_put = price(S, K1, T, r, sigma, "put", dividend_yield)
    short_put = price(S, K2, T, r, sigma, "put", dividend_yield)
    short_call = price(S, K3, T, r, sigma, "call", dividend_yield)
    long_call = price(S, K4, T, r, sigma, "call", dividend_yield)
    return combine((long_put, 1.0), (short_put, -1.0), (short_call, -1.0), (long_call, 1.0))
