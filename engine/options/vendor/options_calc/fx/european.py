"""European vanilla options on FX pairs.

Model: Garman-Kohlhagen -- Black-Scholes-Merton with the foreign risk-free
rate substituted for the dividend yield (holding foreign currency earns
interest, the same way holding a dividend-paying stock does).
Exercise: European only (exercisable at expiry only).
Payoff: vanilla call/put, max(S-K, 0) or max(K-S, 0).

Output includes both 'delta' (raw Black-Scholes delta) and
'delta_premium_adjusted' (the market convention used when premium is paid
in the base/foreign currency -- see _conventions.py). It also includes
'rho' (sensitivity to domestic_rate) and 'rho_foreign' (sensitivity to
foreign_rate) as two separate fields, since they are genuinely different
risks, not one aggregate number.

Output also includes 'delta_forward' and 'delta_forward_premium_adjusted'
(sensitivity to the FORWARD rate instead of spot -- a further FX
convention, distinct from premium-adjustment; see _conventions.py).
"""

from .._engine import price_european_vanilla
from ._conventions import add_premium_adjusted_delta, add_forward_delta


def price(S, K, T, domestic_rate, foreign_rate, sigma, option_type="call"):
    """Price a European FX option (Garman-Kohlhagen).

    S: spot exchange rate (domestic per unit of foreign, e.g. USD per EUR
        for EUR/USD)
    K: strike (same quoting convention as S)
    T: time to expiry, in years
    domestic_rate: domestic risk-free rate (annual, decimal)
    foreign_rate: foreign risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    """
    result = price_european_vanilla(
        S, K, T, domestic_rate, sigma, option_type, dividend_rate=foreign_rate
    )
    result["rho_foreign"] = result.pop("rho_dividend")
    result = add_premium_adjusted_delta(result, S)
    return add_forward_delta(result, T, foreign_rate)
