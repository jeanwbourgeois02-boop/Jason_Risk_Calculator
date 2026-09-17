"""American vanilla options on commodity futures (gold, silver, oil, etc.).

Model: Black-76 dynamics (same F-as-underlying, dividend_rate=r
substitution as european.py -- see that file's docstring for the full
explanation), with early exercise allowed at any time. No closed-form
solution exists in general for American exercise, so priced numerically.

Many real commodity futures options ARE American-style in practice (e.g.
NYMEX crude oil options) -- this is often the more realistic choice
versus european.py for real commodity contracts.

Engine: Cox-Ross-Rubinstein binomial tree, same as equity/american.py and
fx/american.py. Greeks via bump-and-reprice.

NOTE on rho: same fix as european.py -- r and dividend_rate are tied
together (both equal r) in this single-rate model, so the correct total
rate sensitivity is the SUM of the two independently-bumped partials, not
either one alone. See european.py's docstring for the full reasoning.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks

_BINOMIAL_STEPS = 800


def _price_only(F, K, T, r, sigma, option_type, dividend_rate):
    today, maturity, process = build_process(F, K, T, r, sigma, dividend_rate)

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_option_type, K)
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(
        ql.BinomialVanillaEngine(process, "crr", _BINOMIAL_STEPS)
    )
    return option.NPV()


def price(F, K, T, r, sigma, option_type="call"):
    """Price an American option on a commodity futures/forward price.

    F: current futures/forward price
    K: strike price
    T: time to expiry, in years
    r: discount rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' or 'put'
    """
    def price_only(F_, T_, r_, sigma_, dividend_rate_):
        return _price_only(F_, K, T_, r_, sigma_, option_type, dividend_rate_)

    result = finite_difference_greeks(price_only, F, T, r, sigma, r)
    result["rho"] = result["rho"] + result.pop("rho_dividend")
    return result
