"""FX settlement calendars and spot-date conventions.

THE GAP THIS FILLS
-------------------
Every pricer in this package currently treats "today" as the trade date
with no holiday calendar at all (`_engine.py` uses `ql.NullCalendar()`,
which has no holidays -- every day is a business day). Real FX spot
trades settle T+2 BUSINESS DAYS after the trade date, not "today," and
which days count as business days depends on BOTH currencies' holiday
calendars jointly -- a EUR/USD trade needs a day that's a business day in
both the Eurozone (TARGET) and the US, not just one of them.

SCOPE OF THIS MODULE
---------------------
This module provides the calendar/spot-date REFERENCE functions
(`joint_calendar()`, `spot_date()`) correctly, using QuantLib's real
calendars for each G10 market. It does NOT yet change how any pricer in
equity/ or fx/ computes dates internally -- they still take a plain
T-in-years and measure it from "today" via `_engine.year_fraction_to_date()`
with `NullCalendar()`. Wiring spot-date-aware, holiday-aware dates into
the actual pricers would mean changing their interface (taking a trade
date and an expiry date rather than a plain T), which is a real design
decision beyond just adding a calendar lookup -- flagged in MODELS.md as
a deliberate follow-up rather than done here.
"""

import QuantLib as ql

from .g10 import pair_convention

_CURRENCY_CALENDARS = {
    "USD": lambda: ql.UnitedStates(ql.UnitedStates.Settlement),
    "EUR": ql.TARGET,
    "JPY": ql.Japan,
    "GBP": ql.UnitedKingdom,
    "CHF": ql.Switzerland,
    "CAD": ql.Canada,
    "AUD": ql.Australia,
    "NZD": ql.NewZealand,
    "SEK": ql.Sweden,
    "NOK": ql.Norway,
}

# USD/CAD is a well-known, standard exception to the general T+2 FX spot
# rule -- it conventionally settles T+1, because North American payment
# systems for these two currencies allow next-day settlement. Every other
# G10 pair uses the general T+2 convention.
_SPOT_LAG_EXCEPTIONS = {
    frozenset({"USD", "CAD"}): 1,
}
_DEFAULT_SPOT_LAG_DAYS = 2


def currency_calendar(currency):
    """Return the QuantLib calendar for a single G10 currency's home market."""
    if currency not in _CURRENCY_CALENDARS:
        raise ValueError(f"'{currency}' is not a recognized G10 currency")
    return _CURRENCY_CALENDARS[currency]()


def joint_calendar(pair):
    """Return the QuantLib joint calendar for a G10 pair -- a day only
    counts as a business day if it's a business day in BOTH currencies'
    home markets, which is the correct standard for FX settlement."""
    convention = pair_convention(pair)
    return ql.JointCalendar(
        currency_calendar(convention["base"]), currency_calendar(convention["quote"])
    )


def spot_lag_days(pair):
    """Return the standard spot settlement lag, in business days, for a
    G10 pair. 2 for every pair except USD/CAD (1, a well-known standard
    exception)."""
    convention = pair_convention(pair)
    key = frozenset({convention["base"], convention["quote"]})
    return _SPOT_LAG_EXCEPTIONS.get(key, _DEFAULT_SPOT_LAG_DAYS)


def spot_date(pair, trade_date):
    """Return the spot settlement date for a trade in this pair done on
    trade_date: trade_date advanced by the pair's spot lag (2 business
    days, or 1 for USD/CAD), skipping days that are holidays in EITHER
    currency's home market.

    trade_date must be a ql.Date.
    """
    calendar = joint_calendar(pair)
    lag = spot_lag_days(pair)
    return calendar.advance(trade_date, ql.Period(lag, ql.Days))
