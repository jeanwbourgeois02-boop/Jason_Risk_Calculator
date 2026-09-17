"""Implied volatility solvers for FX options.

Given a market-observed price, backs out the volatility that would make
the model reproduce it -- the reverse direction of fx/european.py
(Garman-Kohlhagen), fx/barrier.py, fx/digital.py.

Covers European vanilla, barrier, and digital -- all three are closed-form,
so QuantLib's solver is cheap and stable for all of them. American
exercise is also covered, via implied_volatility_american() below, using
the Barone-Adesi-Whaley approximation rather than re-running fx/american.py's
binomial tree -- see equity/implied_vol.py for the full explanation
(shared with the equity/index case; dividend_rate there plays the role of
the FX foreign rate here, same as everywhere else in this package). Asian
is also covered, via implied_volatility_asian() below, using the
Turnbull-Wakeman approximation -- see equity/implied_vol.py for the full
explanation.
"""

from .._engine import (
    implied_volatility_european as _iv_european,
    implied_volatility_digital as _iv_digital,
    implied_volatility_barrier as _iv_barrier,
    implied_volatility_one_touch as _iv_one_touch,
)
from .._baw_engine import implied_volatility as _iv_american
from .._asian_approx_engine import implied_volatility as _iv_asian


def implied_volatility(market_price, S, K, T, domestic_rate, foreign_rate, option_type="call"):
    """Back out the volatility implied by a European FX option's market
    price.

    market_price: the option's actual observed trading price
    S, K, T, domestic_rate, foreign_rate, option_type: same meaning as
        fx/european.py
    """
    return _iv_european(
        market_price, S, K, T, domestic_rate, option_type, dividend_rate=foreign_rate
    )


def implied_volatility_digital(market_price, S, K, T, domestic_rate, foreign_rate,
                                option_type="call", cash_payout=1.0):
    """Back out the volatility implied by a digital FX option's market
    price.

    market_price: the option's actual observed trading price
    S, K, T, domestic_rate, foreign_rate, option_type, cash_payout: same
        meaning as fx/digital.py
    """
    return _iv_digital(
        market_price, S, K, T, domestic_rate, option_type, cash_payout, dividend_rate=foreign_rate
    )


def implied_volatility_barrier(market_price, S, K, barrier, T, domestic_rate, foreign_rate,
                                option_type="call", barrier_type="up-and-out", rebate=0.0):
    """Back out the volatility implied by a European barrier FX option's
    market price.

    market_price: the option's actual observed trading price
    S, K, barrier, T, domestic_rate, foreign_rate, option_type,
        barrier_type, rebate: same meaning as fx/barrier.py
    """
    return _iv_barrier(
        market_price, S, K, barrier, rebate, T, domestic_rate, option_type, barrier_type,
        dividend_rate=foreign_rate,
    )


def implied_volatility_american(market_price, S, K, T, domestic_rate, foreign_rate,
                                 option_type="call"):
    """Back out the volatility implied by an American FX option's market
    price.

    market_price: the option's actual observed trading price
    S, K, T, domestic_rate, foreign_rate, option_type: same meaning as
        fx/american.py

    Solves against the Barone-Adesi-Whaley approximation rather than
    fx/american.py's binomial tree, for the same speed reasons described
    in equity.implied_vol.implied_volatility_american() -- see that
    docstring (and MODELS.md) for the accuracy trade-off versus the tree.
    """
    return _iv_american(
        market_price, S, K, T, domestic_rate, option_type, dividend_rate=foreign_rate
    )


def implied_volatility_asian(market_price, S, K, T, domestic_rate, foreign_rate,
                              option_type="call", n_fixings=12):
    """Back out the volatility implied by an arithmetic-average Asian FX
    option's market price.

    market_price: the option's actual observed trading price
    S, K, T, domestic_rate, foreign_rate, option_type, n_fixings: same
        meaning as fx/asian.py

    Solves against the Turnbull-Wakeman approximation rather than
    fx/asian.py's Monte Carlo engine, for the same reasons described in
    equity.implied_vol.implied_volatility_asian() -- see that docstring
    (and MODELS.md) for the accuracy trade-off versus Monte Carlo.
    """
    return _iv_asian(
        market_price, S, K, T, domestic_rate, option_type,
        dividend_rate=foreign_rate, n_fixings=n_fixings,
    )


def implied_volatility_one_touch(market_price, S, barrier, T, domestic_rate, foreign_rate,
                                  cash_payout=1.0, direction="up"):
    """Back out the volatility implied by a one-touch FX option's market
    price. Same mechanics as equity.implied_vol.implied_volatility_one_touch()
    -- see its docstring and _engine.implied_volatility_one_touch()'s for
    the full explanation."""
    return _iv_one_touch(
        market_price, S, barrier, T, domestic_rate, cash_payout, direction, "one-touch",
        dividend_rate=foreign_rate,
    )


def implied_volatility_no_touch(market_price, S, barrier, T, domestic_rate, foreign_rate,
                                 cash_payout=1.0, direction="up"):
    """Back out the volatility implied by a no-touch FX option's market
    price. Same mechanics as implied_volatility_one_touch() -- see its
    docstring."""
    return _iv_one_touch(
        market_price, S, barrier, T, domestic_rate, cash_payout, direction, "no-touch",
        dividend_rate=foreign_rate,
    )
