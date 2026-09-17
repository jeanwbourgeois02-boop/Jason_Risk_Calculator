"""Named multi-leg FX option structures, built from fx/european.py +
structures.py's combine(). Same structures as equity/structures.py, with
the two-rate FX pricer underneath instead -- see that file for the
economic explanation of each structure; only the pricer call signature
differs here.
"""

from ..structures import combine
from .european import price


def straddle(S, K, T, domestic_rate, foreign_rate, sigma):
    """Long straddle: long 1 call + long 1 put, same strike and expiry.
    See equity/structures.py:straddle for the economic explanation."""
    call = price(S, K, T, domestic_rate, foreign_rate, sigma, "call")
    put = price(S, K, T, domestic_rate, foreign_rate, sigma, "put")
    return combine((call, 1.0), (put, 1.0))


def strangle(S, K_put, K_call, T, domestic_rate, foreign_rate, sigma):
    """Long strangle: long put at K_put + long call at K_call (K_put <
    K_call). See equity/structures.py:strangle for the explanation."""
    put = price(S, K_put, T, domestic_rate, foreign_rate, sigma, "put")
    call = price(S, K_call, T, domestic_rate, foreign_rate, sigma, "call")
    return combine((put, 1.0), (call, 1.0))


def risk_reversal(S, K_put, K_call, T, domestic_rate, foreign_rate, sigma, long_call=True):
    """Risk reversal: long one side, short the other, at two strikes.

    This is the FX structure whose PRICE difference between the two legs
    is literally what the market quotes as "the risk reversal" (e.g.
    Bloomberg's EURUSD25R1M Curncy) -- the standard way FX desks quote
    and trade skew directly, rather than pricing two separate vanillas.
    See equity/structures.py:risk_reversal for the full explanation.
    """
    call = price(S, K_call, T, domestic_rate, foreign_rate, sigma, "call")
    put = price(S, K_put, T, domestic_rate, foreign_rate, sigma, "put")
    if long_call:
        return combine((call, 1.0), (put, -1.0))
    return combine((put, 1.0), (call, -1.0))


def collar(S, K_put, K_call, T, domestic_rate, foreign_rate, sigma):
    """Collar: long protective put at K_put, short call at K_call to
    finance it. See equity/structures.py:collar for the explanation --
    common for a corporate hedging FX exposure within a defined range."""
    put = price(S, K_put, T, domestic_rate, foreign_rate, sigma, "put")
    call = price(S, K_call, T, domestic_rate, foreign_rate, sigma, "call")
    return combine((put, 1.0), (call, -1.0))


def call_spread(S, K_low, K_high, T, domestic_rate, foreign_rate, sigma):
    """Bull call spread: long call at K_low, short call at K_high.
    See equity/structures.py:call_spread for the explanation."""
    long_call = price(S, K_low, T, domestic_rate, foreign_rate, sigma, "call")
    short_call = price(S, K_high, T, domestic_rate, foreign_rate, sigma, "call")
    return combine((long_call, 1.0), (short_call, -1.0))


def put_spread(S, K_low, K_high, T, domestic_rate, foreign_rate, sigma):
    """Bear put spread: long put at K_high, short put at K_low.
    See equity/structures.py:put_spread for the explanation."""
    long_put = price(S, K_high, T, domestic_rate, foreign_rate, sigma, "put")
    short_put = price(S, K_low, T, domestic_rate, foreign_rate, sigma, "put")
    return combine((long_put, 1.0), (short_put, -1.0))


def butterfly(S, K_low, K_mid, K_high, T, domestic_rate, foreign_rate, sigma, option_type="call"):
    """Long butterfly: long 1 at K_low, short 2 at K_mid, long 1 at K_high.
    See equity/structures.py:butterfly for the full explanation."""
    low = price(S, K_low, T, domestic_rate, foreign_rate, sigma, option_type)
    mid = price(S, K_mid, T, domestic_rate, foreign_rate, sigma, option_type)
    high = price(S, K_high, T, domestic_rate, foreign_rate, sigma, option_type)
    return combine((low, 1.0), (mid, -2.0), (high, 1.0))


def iron_condor(S, K1, K2, K3, K4, T, domestic_rate, foreign_rate, sigma):
    """Iron condor: long put at K1, short put at K2, short call at K3,
    long call at K4 (K1 < K2 < K3 < K4). See equity/structures.py:
    iron_condor for the full explanation."""
    long_put = price(S, K1, T, domestic_rate, foreign_rate, sigma, "put")
    short_put = price(S, K2, T, domestic_rate, foreign_rate, sigma, "put")
    short_call = price(S, K3, T, domestic_rate, foreign_rate, sigma, "call")
    long_call = price(S, K4, T, domestic_rate, foreign_rate, sigma, "call")
    return combine((long_put, 1.0), (short_put, -1.0), (short_call, -1.0), (long_call, 1.0))
