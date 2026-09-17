"""Implied volatility solvers for equity/index options.

Given a market-observed price, backs out the volatility that would make
the model reproduce it -- the reverse direction of equity/european.py,
equity/barrier.py, equity/digital.py.

Covers European vanilla, barrier, and digital -- all three are closed-form
under Black-Scholes-Merton, so QuantLib's solver (which re-runs the
pricer many times as it iterates, Newton's method) is cheap and stable
for all of them. American exercise is also covered, via
implied_volatility_american() below -- but NOT by re-running
equity/american.py's 800-step binomial tree (too slow to call
repeatedly). Instead it uses the Barone-Adesi-Whaley approximation
(options_calc/_baw_engine.py), a fast quasi-analytic pricer built
specifically to make repeated re-pricing inside a root-finder practical.
See implied_volatility_american()'s docstring for the accuracy trade-off
this implies. Asian is also covered, via implied_volatility_asian() below,
using the same "fast approximation instead of the real pricer" strategy:
the Turnbull-Wakeman approximation (options_calc/_asian_approx_engine.py)
in place of equity/asian.py's Monte Carlo engine, whose simulation noise
would otherwise confuse a root-finder chasing a precise target price.
"""

from .._engine import (
    implied_volatility_european as _iv_european,
    implied_volatility_digital as _iv_digital,
    implied_volatility_barrier as _iv_barrier,
    implied_volatility_one_touch as _iv_one_touch,
)
from .._baw_engine import implied_volatility as _iv_american
from .._asian_approx_engine import implied_volatility as _iv_asian


def implied_volatility(market_price, S, K, T, r, option_type="call", dividend_yield=0.0):
    """Back out the volatility implied by a European equity/index option's
    market price.

    market_price: the option's actual observed trading price
    S, K, T, r, option_type, dividend_yield: same meaning as equity/european.py
    """
    return _iv_european(
        market_price, S, K, T, r, option_type, dividend_rate=dividend_yield
    )


def implied_volatility_digital(market_price, S, K, T, r, option_type="call",
                                cash_payout=1.0, dividend_yield=0.0):
    """Back out the volatility implied by a digital equity/index option's
    market price.

    market_price: the option's actual observed trading price
    S, K, T, r, option_type, cash_payout, dividend_yield: same meaning as
        equity/digital.py
    """
    return _iv_digital(
        market_price, S, K, T, r, option_type, cash_payout, dividend_rate=dividend_yield
    )


def implied_volatility_barrier(market_price, S, K, barrier, T, r, option_type="call",
                                barrier_type="up-and-out", rebate=0.0, dividend_yield=0.0):
    """Back out the volatility implied by a European barrier equity/index
    option's market price.

    market_price: the option's actual observed trading price
    S, K, barrier, T, r, option_type, barrier_type, rebate, dividend_yield:
        same meaning as equity/barrier.py
    """
    return _iv_barrier(
        market_price, S, K, barrier, rebate, T, r, option_type, barrier_type,
        dividend_rate=dividend_yield,
    )


def implied_volatility_american(market_price, S, K, T, r, option_type="call", dividend_yield=0.0):
    """Back out the volatility implied by an American equity/index
    option's market price.

    market_price: the option's actual observed trading price
    S, K, T, r, option_type, dividend_yield: same meaning as
        equity/american.py

    Unlike implied_volatility()/_digital()/_barrier() above, this does NOT
    re-run equity/american.py's binomial tree inside the solver -- an
    800-step CRR tree is too slow to rebuild on every Newton iteration.
    Instead it solves against the Barone-Adesi-Whaley approximation
    (options_calc/_baw_engine.py), QuantLib's built-in fast closed-form-
    style American pricer. BAW is an approximation, not the exact
    American price: it can differ from the tree's price by roughly
    0.1-1% in typical cases, more for long-dated or deep in/out-of-the-
    money options (see MODELS.md and tests/equity/test_implied_vol_american.py
    for measured discrepancies). The vol this returns is implied relative
    to the BAW model, not the tree -- treat it as a fast, good
    approximation rather than an exact match to what equity/american.py
    would imply.
    """
    return _iv_american(
        market_price, S, K, T, r, option_type, dividend_rate=dividend_yield
    )


def implied_volatility_asian(market_price, S, K, T, r, option_type="call",
                              dividend_yield=0.0, n_fixings=12):
    """Back out the volatility implied by an arithmetic-average Asian
    equity/index option's market price.

    market_price: the option's actual observed trading price
    S, K, T, r, option_type, dividend_yield, n_fixings: same meaning as
        equity/asian.py

    Does NOT re-run equity/asian.py's Monte Carlo engine inside the
    solver -- simulation noise in a Monte Carlo price is not a smooth,
    deterministic function of sigma, which can confuse or destabilize a
    root-finder chasing a precise target price. Instead solves against
    the Turnbull-Wakeman approximation (options_calc/_asian_approx_engine.py),
    a fast, standard moment-matching approximation for arithmetic Asian
    prices. Turnbull-Wakeman is an approximation, not the exact arithmetic-
    average price -- measured against this package's own Monte Carlo
    pricer for a representative case, it differs by roughly 2% (see
    _asian_approx_engine.py's docstring and
    tests/equity/test_implied_vol_asian.py). The vol this returns is
    implied relative to the Turnbull-Wakeman model, not the Monte Carlo
    one -- treat it as a fast, reasonable approximation rather than an
    exact match to what equity/asian.py would imply.
    """
    return _iv_asian(
        market_price, S, K, T, r, option_type, dividend_rate=dividend_yield, n_fixings=n_fixings
    )


def implied_volatility_one_touch(market_price, S, barrier, T, r, cash_payout=1.0,
                                  direction="up", dividend_yield=0.0):
    """Back out the volatility implied by a one-touch equity/index
    option's market price.

    market_price: the option's actual observed trading price
    S, barrier, T, r, cash_payout, direction, dividend_yield: same meaning
        as equity/one_touch.py's one_touch()

    Solves via bisection against the EXACT SAME pricer/engine
    one_touch.py uses (not an approximation) -- see
    _engine.implied_volatility_one_touch()'s docstring for why QuantLib's
    own impliedVolatility() can't be used here, and why bisection is
    reliable for this instrument (price is cleanly monotonic in vol).
    """
    return _iv_one_touch(
        market_price, S, barrier, T, r, cash_payout, direction, "one-touch",
        dividend_rate=dividend_yield,
    )


def implied_volatility_no_touch(market_price, S, barrier, T, r, cash_payout=1.0,
                                 direction="up", dividend_yield=0.0):
    """Back out the volatility implied by a no-touch equity/index option's
    market price. Same mechanics as implied_volatility_one_touch() -- see
    its docstring."""
    return _iv_one_touch(
        market_price, S, barrier, T, r, cash_payout, direction, "no-touch",
        dividend_rate=dividend_yield,
    )
