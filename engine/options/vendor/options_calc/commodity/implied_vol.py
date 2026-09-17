"""Implied volatility solver for European commodity futures options.

Given a market-observed price, backs out the volatility that would make
the model reproduce it -- the reverse direction of commodity/european.py
(Black-76).
"""

from .._engine import implied_volatility_european


def implied_volatility(market_price, F, K, T, r, option_type="call"):
    """Back out the volatility implied by a European commodity futures
    option's market price.

    market_price: the option's actual observed trading price
    F, K, T, r, option_type: same meaning as commodity/european.py
    """
    return implied_volatility_european(
        market_price, F, K, T, r, option_type, dividend_rate=r
    )
