"""FX market delta conventions.

Internal module -- not part of the public API. Every fx/*.py pricer's
`delta` field is raw Black-Scholes-style delta (dPrice/dSpot). Real FX
desks often quote and hedge with a DIFFERENT number: premium-adjusted
delta, used whenever the option's premium is paid in the base/foreign
currency (the market convention for many pairs, e.g. EUR, GBP, AUD as the
base currency against USD).

WHY THIS IS A SEPARATE NUMBER FROM RAW DELTA
----------------------------------------------
If you receive (or pay) the option's premium in the foreign currency, you
already hold (or owe) some amount of that foreign currency the moment the
trade is done -- separate from the option's own foreign-currency exposure.
Raw Black-Scholes delta ignores this; premium-adjusted delta nets it out,
which is why it's the number FX desks actually use for hedge sizing and
for quoting strikes by delta (e.g. "the 25-delta put") when premium is
paid in the base currency.

FORMULA
-------
delta_premium_adjusted = delta - price / S

This holds for both calls and puts -- the correction term is the same
additive adjustment either way, because it comes from netting out the
foreign-currency premium itself, not from the option's payoff shape.

FORWARD DELTA -- A SEPARATE, FURTHER CONVENTION
-------------------------------------------------
Everything above is "spot delta": sensitivity of the option's value to a
move in SPOT. FX desks also commonly quote "forward delta": sensitivity
to a move in the FORWARD rate instead. The two are related by a simple
scaling, because F = S * exp((r_d - r_f) * T), so a move in F corresponds
to a proportionally scaled move in S:

    delta_forward = delta * exp(foreign_rate * T)

This removes the foreign-currency discount factor exp(-foreign_rate * T)
baked into spot delta (see fx/european.py -- spot delta is
exp(-r_f*T)*N(d1); forward delta is the undiscounted N(d1)). Verified
against the standard reference formula for FX delta conventions
(Reiswich & Wystup, "FX Volatility Smile Construction"). The same scaling
is applied uniformly to get the premium-adjusted forward delta from the
premium-adjusted spot delta -- this follows the same pattern the
reference literature uses, though it was not independently re-derived
from first principles here to the same level of scrutiny as the base
formula above.
"""

import math


def add_premium_adjusted_delta(result, S):
    """Add 'delta_premium_adjusted' to a pricer's result dict, computed
    from its own 'delta' and 'price' fields plus the spot S. Mutates and
    returns the same dict for convenient chaining at the end of a pricer.
    """
    result["delta_premium_adjusted"] = result["delta"] - result["price"] / S
    return result


def add_forward_delta(result, T, foreign_rate):
    """Add 'delta_forward' and 'delta_forward_premium_adjusted' to a
    pricer's result dict -- see module docstring's FORWARD DELTA section.
    Requires 'delta' and 'delta_premium_adjusted' to already be present
    (call add_premium_adjusted_delta first). Mutates and returns the same
    dict for convenient chaining.
    """
    scale = math.exp(foreign_rate * T)
    result["delta_forward"] = result["delta"] * scale
    result["delta_forward_premium_adjusted"] = result["delta_premium_adjusted"] * scale
    return result
