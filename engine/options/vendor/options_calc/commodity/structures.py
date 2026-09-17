"""Named multi-leg commodity futures option structures, built from
commodity/european.py + structures.py's combine(). Same structures as
equity/structures.py and fx/structures.py, using the Black-76 commodity
pricer underneath -- see equity/structures.py for the economic
explanation of each structure.
"""

from ..structures import combine
from .european import price


def straddle(F, K, T, r, sigma):
    """Long straddle: long 1 call + long 1 put, same strike/expiry.
    See equity/structures.py:straddle for the explanation."""
    call = price(F, K, T, r, sigma, "call")
    put = price(F, K, T, r, sigma, "put")
    return combine((call, 1.0), (put, 1.0))


def strangle(F, K_put, K_call, T, r, sigma):
    """Long strangle: long put at K_put + long call at K_call (K_put <
    K_call). See equity/structures.py:strangle for the explanation."""
    put = price(F, K_put, T, r, sigma, "put")
    call = price(F, K_call, T, r, sigma, "call")
    return combine((put, 1.0), (call, 1.0))


def risk_reversal(F, K_put, K_call, T, r, sigma, long_call=True):
    """Risk reversal: long one side, short the other, at two strikes.
    See equity/structures.py:risk_reversal for the explanation."""
    call = price(F, K_call, T, r, sigma, "call")
    put = price(F, K_put, T, r, sigma, "put")
    if long_call:
        return combine((call, 1.0), (put, -1.0))
    return combine((put, 1.0), (call, -1.0))


def collar(F, K_put, K_call, T, r, sigma):
    """Collar: long protective put at K_put, short call at K_call to
    finance it. See equity/structures.py:collar for the explanation --
    common for a producer/consumer hedging a commodity within a range."""
    put = price(F, K_put, T, r, sigma, "put")
    call = price(F, K_call, T, r, sigma, "call")
    return combine((put, 1.0), (call, -1.0))


def call_spread(F, K_low, K_high, T, r, sigma):
    """Bull call spread: long call at K_low, short call at K_high.
    See equity/structures.py:call_spread for the explanation."""
    long_call = price(F, K_low, T, r, sigma, "call")
    short_call = price(F, K_high, T, r, sigma, "call")
    return combine((long_call, 1.0), (short_call, -1.0))


def put_spread(F, K_low, K_high, T, r, sigma):
    """Bear put spread: long put at K_high, short put at K_low.
    See equity/structures.py:put_spread for the explanation."""
    long_put = price(F, K_high, T, r, sigma, "put")
    short_put = price(F, K_low, T, r, sigma, "put")
    return combine((long_put, 1.0), (short_put, -1.0))


def butterfly(F, K_low, K_mid, K_high, T, r, sigma, option_type="call"):
    """Long butterfly: long 1 at K_low, short 2 at K_mid, long 1 at K_high.
    See equity/structures.py:butterfly for the full explanation."""
    low = price(F, K_low, T, r, sigma, option_type)
    mid = price(F, K_mid, T, r, sigma, option_type)
    high = price(F, K_high, T, r, sigma, option_type)
    return combine((low, 1.0), (mid, -2.0), (high, 1.0))


def iron_condor(F, K1, K2, K3, K4, T, r, sigma):
    """Iron condor: long put at K1, short put at K2, short call at K3,
    long call at K4 (K1 < K2 < K3 < K4). See equity/structures.py:
    iron_condor for the full explanation."""
    long_put = price(F, K1, T, r, sigma, "put")
    short_put = price(F, K2, T, r, sigma, "put")
    short_call = price(F, K3, T, r, sigma, "call")
    long_call = price(F, K4, T, r, sigma, "call")
    return combine((long_put, 1.0), (short_put, -1.0), (short_call, -1.0), (long_call, 1.0))
