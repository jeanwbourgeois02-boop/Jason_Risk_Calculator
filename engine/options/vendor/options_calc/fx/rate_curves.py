"""Illustrative G10 short-term rate reference -- PLACEHOLDER DATA, NOT LIVE.

WHAT THIS IS AND IS NOT
-------------------------
Every fx/*.py pricer needs a domestic_rate and a foreign_rate number. Up
to now, the caller has had to know and supply both by hand every time.
This module provides a convenience lookup of illustrative short-term
rates per G10 currency, structured so pricers can be called with just a
pair name instead of two rate numbers typed from memory.

THESE ARE NOT LIVE, CURRENT, OR ACCURATE RATES. There is no live market
data connection in this environment (see MODELS.md for why -- the same
reason VolSurface has no automatic Bloomberg feed). The numbers below are
plausible-looking illustrative placeholders for each currency's short-term
policy-adjacent rate (e.g. what SOFR, the Fed Funds rate, or €STR broadly
represent), NOT a snapshot of any actual date. Interest rates move
continuously; treat every number here as a stand-in to be overwritten with
a real rate before this is used for anything beyond testing the plumbing.

INTENDED USAGE PATTERN
------------------------
    from options_calc.fx.rate_curves import set_rate, get_domestic_and_foreign_rates

    # Overwrite with real, current rates before using this for real work:
    set_rate("USD", 0.0525)
    set_rate("EUR", 0.0400)

    domestic_rate, foreign_rate = get_domestic_and_foreign_rates("EURUSD")
    price_european_fx_option(S=..., K=..., T=..., domestic_rate=domestic_rate,
                              foreign_rate=foreign_rate, sigma=..., option_type="call")

This is a mutable, in-memory table (not a live feed) specifically so a
real rate source -- read from a file, an API, or eventually Bloomberg --
can populate it via the same set_rate() call, without changing this
module's interface or any pricer.
"""

from .g10 import domestic_and_foreign_currency, G10_CURRENCIES

# Illustrative placeholders only -- see module docstring. Not sourced from
# any live feed or dated snapshot.
_rates = {
    "USD": 0.0500,
    "EUR": 0.0350,
    "JPY": 0.0025,
    "GBP": 0.0475,
    "CHF": 0.0100,
    "CAD": 0.0450,
    "AUD": 0.0425,
    "NZD": 0.0450,
    "SEK": 0.0300,
    "NOK": 0.0400,
}


def get_rate(currency):
    """Return the current in-memory illustrative rate for a G10 currency.
    See module docstring: this is placeholder data, not a live rate,
    until overwritten via set_rate()."""
    if currency not in G10_CURRENCIES:
        raise ValueError(f"'{currency}' is not a recognized G10 currency")
    return _rates[currency]


def set_rate(currency, rate):
    """Overwrite the in-memory rate for a G10 currency -- the intended way
    to feed in a real, current rate (read from a file, API, or future
    live data connection) instead of using the illustrative placeholder.
    """
    if currency not in G10_CURRENCIES:
        raise ValueError(f"'{currency}' is not a recognized G10 currency")
    _rates[currency] = rate


def get_domestic_and_foreign_rates(pair):
    """Return (domestic_rate, foreign_rate) for a G10 pair, ready to pass
    directly into any fx/*.py pricer's matching arguments. Sourced from
    the in-memory table above -- illustrative until set_rate() is used to
    supply real numbers.
    """
    domestic_ccy, foreign_ccy = domestic_and_foreign_currency(pair)
    return get_rate(domestic_ccy), get_rate(foreign_ccy)
