"""European vanilla options on commodity futures (gold, silver, oil, etc.).

Model: Black-76 -- the standard model for options on a futures/forward
price, as opposed to Black-Scholes-Merton for options on a spot price
(equity/european.py) or Garman-Kohlhagen for FX (fx/european.py).

Black-76's formula is mathematically identical to our existing
Black-Scholes-Merton engine with the dividend/cost-of-carry rate set
EQUAL to the discount rate:

    d1 = [ln(F/K) + 0.5*sigma^2*T] / (sigma*sqrt(T))

which is exactly what our engine's d1 = [ln(S/K) + (r-q+0.5*sigma^2)T] /
(sigma*sqrt(T)) reduces to when q = r (the r-q term cancels). This makes
sense economically: a futures price already has no further cost-of-carry
drift under the risk-neutral measure (unlike spot, which drifts up at the
risk-free rate before adjusting for dividends/foreign rates) -- setting
the "dividend rate" equal to the discount rate is exactly what removes
that drift term. So this file is a thin wrapper around the same shared
engine equity/european.py and fx/european.py already use, with F (the
futures price) passed in the S argument's position and dividend_rate=r.

Exercise: European. Many real commodity options are American-style in
practice (see commodity/american.py) -- use this one specifically for
European-style commodity options or futures options where American
exercise doesn't apply.
"""

from .._engine import price_european_vanilla


def price(F, K, T, r, sigma, option_type="call"):
    """Price a European option on a commodity futures/forward price
    (Black-76).

    F: current futures/forward price (e.g. a gold futures price)
    K: strike price
    T: time to expiry, in years
    r: discount rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'

    NOTE on rho: the shared engine underneath this treats "r" and
    "dividend_rate" as two independently-bumpable curve handles (which is
    correct for equity/FX, where they genuinely are two separate rates).
    Here they're set to the SAME value (r), because Black-76 only has one
    real rate playing two roles (discounting, and cancelling the drift
    term). Bumping "r" alone while holding the dividend curve fixed would
    silently break that r = dividend_rate identity and produce a
    meaningless partial derivative. The correct total rate sensitivity is
    the SUM of the engine's two raw partials -- verified against a direct
    finite-difference bump of r (with both curves moved together) -- so
    that's what's returned as 'rho' here; the raw 'rho_dividend' field is
    removed rather than exposed, since it doesn't correspond to a real,
    independent risk in a single-rate model.
    """
    result = price_european_vanilla(F, K, T, r, sigma, option_type, dividend_rate=r)
    result["rho"] = result["rho"] + result.pop("rho_dividend")
    return result
