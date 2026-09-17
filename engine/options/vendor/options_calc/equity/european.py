"""European vanilla options on equities and equity indexes.

Model: Black-Scholes-Merton (Black-Scholes with a continuous dividend yield).
Exercise: European only (exercisable at expiry only).
Payoff: vanilla call/put, max(S-K, 0) or max(K-S, 0).
"""

from .._engine import price_european_vanilla


def price(S, K, T, r, sigma, option_type="call", dividend_yield=0.0):
    """Price a European equity/index option.

    S: spot price (a single stock or an index level)
    K: strike price
    T: time to expiry, in years
    r: risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    dividend_yield: continuous dividend yield (annual, decimal); 0 for a
        non-dividend-paying stock or index
    """
    return price_european_vanilla(
        S, K, T, r, sigma, option_type, dividend_rate=dividend_yield
    )
