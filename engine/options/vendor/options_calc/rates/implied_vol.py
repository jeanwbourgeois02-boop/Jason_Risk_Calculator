"""Implied volatility solvers for interest rate derivatives.

Given a market-observed price, backs out the flat LOGNORMAL vol that would
make this package's Black-76 model reproduce it -- the reverse direction of
rates/swaption.py and rates/cap_floor.py. Mirrors the pattern in
equity/implied_vol.py and fx/implied_vol.py: closed-form pricers (Black-76
here, Black-Scholes-Merton there) support a cheap, stable Newton solve via
QuantLib's own impliedVolatility methods, rather than a hand-rolled
bisection.

Every value returned/consumed here is LOGNORMAL vol as a decimal (e.g.
0.20 = 20%), matching rates/swaption.py and rates/cap_floor.py -- NOT
normal/bp vol. See rates/_engine.py's module docstring for why this
matters.
"""

from ._engine import implied_volatility_swaption as _iv_swaption
from ._engine import implied_volatility_cap_floor as _iv_cap_floor

_DEFAULT_NOTIONAL = 1_000_000.0
_DEFAULT_FREQ_MONTHS = 6


def implied_volatility_swaption(market_price, fixed_rate, expiry, swap_tenor, r,
                                 notional=_DEFAULT_NOTIONAL, option_type="payer",
                                 evaluation_date=None):
    """Back out the lognormal vol implied by a swaption's market price.

    market_price: the swaption's actual observed price.
    fixed_rate, expiry, swap_tenor, r, notional, option_type: same meaning
        as rates/swaption.py's price().
    evaluation_date: optional ql.Date; None reproduces "evaluate as of
        today". Always restored to its prior value on exit -- see
        _engine.py's evaluation_date_scope.
    """
    return _iv_swaption(market_price, fixed_rate, expiry, swap_tenor, r, notional, option_type,
                         evaluation_date=evaluation_date)


def implied_volatility_cap(market_price, strike, start, tenor, r,
                            notional=_DEFAULT_NOTIONAL, freq_months=_DEFAULT_FREQ_MONTHS,
                            evaluation_date=None):
    """Back out the flat lognormal vol implied by a cap's market price
    (the single vol that, applied to every caplet, reproduces the given
    price -- see rates/cap_floor.py's "FLAT VOL ACROSS THE STRIP" caveat).

    market_price: the cap's actual observed price.
    strike, start, tenor, r, notional, freq_months: same meaning as
        rates/cap_floor.py's price_cap(). evaluation_date: see
        implied_volatility_swaption.
    """
    return _iv_cap_floor(market_price, notional, strike, start, tenor, r, freq_months, cap=True,
                          evaluation_date=evaluation_date)


def implied_volatility_floor(market_price, strike, start, tenor, r,
                              notional=_DEFAULT_NOTIONAL, freq_months=_DEFAULT_FREQ_MONTHS,
                              evaluation_date=None):
    """Back out the flat lognormal vol implied by a floor's market price.
    Same mechanics/caveats as implied_volatility_cap -- see rates/cap_floor.py."""
    return _iv_cap_floor(market_price, notional, strike, start, tenor, r, freq_months, cap=False,
                          evaluation_date=evaluation_date)
