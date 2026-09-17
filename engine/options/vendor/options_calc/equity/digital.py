"""Digital (cash-or-nothing / binary) options on equities and equity indexes.

Payoff: a FIXED cash amount if the option finishes in-the-money at expiry,
zero otherwise -- unlike a vanilla, the payout doesn't scale with how far
in-the-money you end up. A sharp, all-or-nothing bet, commonly used to
express a view on a binary event (an election, a rate decision) where the
outcome matters more than the magnitude.

Exercise: European. Model: Black-Scholes-Merton -- closed-form solution
exists (it's essentially the N(d2) term from the vanilla formula, scaled
by the cash payout instead of by the strike/spot), so QuantLib's
AnalyticEuropeanEngine handles it directly. Greeks here are computed by
bump-and-reprice for consistency with barrier.py and the rest of this
package's non-vanilla instruments -- notably, a digital's gamma near
expiry, right at the strike, is extremely large (the payoff jumps from 0
to the full cash amount over an infinitesimal move), so Greeks here should
be read with that in mind rather than assumed smooth.
"""

import QuantLib as ql

from .._engine import build_process, finite_difference_greeks


def _price_only(S, K, T, r, sigma, option_type, cash_payout, dividend_rate):
    today, maturity, process = build_process(S, K, T, r, sigma, dividend_rate)

    ql_option_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.CashOrNothingPayoff(ql_option_type, K, cash_payout)
    exercise = ql.EuropeanExercise(maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
    return option.NPV()


def price(S, K, T, r, sigma, option_type="call", cash_payout=1.0, dividend_yield=0.0):
    """Price a European cash-or-nothing digital equity/index option.

    S: spot price
    K: strike price
    T: time to expiry, in years
    r: risk-free rate (annual, decimal)
    sigma: volatility (annual, decimal)
    option_type: 'call' (pays out if S > K at expiry) or 'put' (pays out
        if S < K at expiry)
    cash_payout: the fixed amount paid if in-the-money at expiry
    dividend_yield: continuous dividend yield (annual, decimal)
    """
    def price_only(S_, T_, r_, sigma_, dividend_rate_):
        return _price_only(S_, K, T_, r_, sigma_, option_type, cash_payout, dividend_rate_)

    return finite_difference_greeks(price_only, S, T, r, sigma, dividend_yield)
