"""G10 currency and pair conventions.

"G10" refers to the ten major, freely-floating currencies of developed
economies: USD, EUR, JPY, GBP, CHF, CAD, AUD, NZD, SEK, NOK. This module
does not add new pricing math -- every fx/*.py pricer already works for
any currency pair, since they just take spot/rates/vol as plain numbers.
What this module encodes is the MARKET CONVENTION knowledge needed to use
those pricers correctly for a specific named pair, which is easy to get
backwards:

1. WHICH CURRENCY IS "DOMESTIC" (our functions' quote currency) VS.
   "FOREIGN" (our functions' base currency) for a given pair -- this is
   NOT consistent across pairs. EUR/USD quotes "USD per 1 EUR" (EUR is
   base/foreign, USD is quote/domestic in our terms), but USD/JPY quotes
   "JPY per 1 USD" (USD is base/foreign, JPY is quote/domestic). Get this
   backwards and domestic_rate/foreign_rate are silently swapped.

2. WHICH DELTA CONVENTION IS MARKET-STANDARD for a given pair. Every FX
   pricer in this package always computes BOTH raw 'delta' and
   'delta_premium_adjusted' (see fx/_conventions.py) -- but only one of
   them is the number a real desk would actually quote/hedge with for a
   given pair, depending on which currency premium is conventionally paid
   in. For EUR, GBP, AUD, NZD pairs (against USD), premium is
   conventionally paid in the BASE currency, so premium-adjusted delta is
   standard. For USD/JPY, USD/CHF, USD/CAD, USD/SEK, USD/NOK, premium is
   conventionally paid in USD (the quote currency here), so raw spot
   delta is standard instead.

Covers all 45 G10 pairs: the 9 standard pairs against USD (individually
verified, well-documented conventions) plus the 36 cross pairs not
involving USD (base/quote determined by standard FX market hierarchy;
premium_currency for crosses is a simplified default -- see the caveat on
_CROSS_BASE_PRIORITY below).

This module does not fetch real interest rates, vol, or spot -- those
still have to be supplied by the caller (or a future live data
connection, see MODELS.md). It only prevents the two conventions above
from being silently misapplied.
"""

G10_CURRENCIES = frozenset({"USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK"})

# Standard market quoting convention for each G10 currency against USD.
# "base" is the currency the pair is quoted IN TERMS OF one unit of; in
# this package's fx/*.py signatures, base = foreign_rate's currency,
# quote = domestic_rate's currency (S is "quote per 1 unit of base").
#
# premium_currency: "base" means premium is conventionally paid in the
# base currency (use delta_premium_adjusted); "quote" means premium is
# conventionally paid in the quote currency (use raw delta). These 9
# pairs are well-documented, standard FX market convention.
_G10_USD_PAIRS = {
    "EURUSD": {"base": "EUR", "quote": "USD", "premium_currency": "base"},
    "GBPUSD": {"base": "GBP", "quote": "USD", "premium_currency": "base"},
    "AUDUSD": {"base": "AUD", "quote": "USD", "premium_currency": "base"},
    "NZDUSD": {"base": "NZD", "quote": "USD", "premium_currency": "base"},
    "USDJPY": {"base": "USD", "quote": "JPY", "premium_currency": "quote"},
    "USDCHF": {"base": "USD", "quote": "CHF", "premium_currency": "quote"},
    "USDCAD": {"base": "USD", "quote": "CAD", "premium_currency": "quote"},
    "USDSEK": {"base": "USD", "quote": "SEK", "premium_currency": "quote"},
    "USDNOK": {"base": "USD", "quote": "NOK", "premium_currency": "quote"},
}

# Base-currency priority for G10 CROSS pairs (not involving USD), highest
# priority first -- e.g. EUR outranks GBP, so EUR/GBP quotes GBP per 1
# EUR (EUR is base). This is standard FX market hierarchy for which
# currency of a pair is conventionally the base.
#
# CAVEAT: unlike the 9 USD pairs above (individually verified, standard,
# widely documented), the premium-currency convention applied to crosses
# below is a SIMPLIFIED DEFAULT ("base" for every cross), not individually
# verified per pair. General FX convention leans this way for most
# non-USD crosses, but a specific desk's actual convention for a specific
# cross should be checked before relying on this for real trading --
# treat cross-pair premium_currency as a reasonable default, not a
# verified fact the way the 9 USD pairs are.
_CROSS_BASE_PRIORITY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "SEK", "NOK", "JPY"]


def _cross_pair_convention(base, quote):
    return {"base": base, "quote": quote, "premium_currency": "base"}


def _build_cross_pairs():
    crosses = {}
    for i, higher in enumerate(_CROSS_BASE_PRIORITY):
        for lower in _CROSS_BASE_PRIORITY[i + 1:]:
            if "USD" in (higher, lower):
                continue  # already covered by the verified _G10_USD_PAIRS table
            crosses[higher + lower] = _cross_pair_convention(base=higher, quote=lower)
    return crosses


_G10_CROSS_PAIRS = _build_cross_pairs()

_ALL_G10_PAIRS = {**_G10_USD_PAIRS, **_G10_CROSS_PAIRS}


def pair_convention(pair):
    """Look up the base/quote/premium-currency convention for a standard
    G10 pair (USD pair or cross), e.g. pair_convention("EURUSD") or
    pair_convention("EURGBP").

    Returns a dict: {"base": ..., "quote": ..., "premium_currency": ...}.
    See the module docstring and _CROSS_BASE_PRIORITY's caveat above for
    why the 9 USD pairs are verified conventions while cross-pair
    premium_currency is a simplified default.
    """
    pair = pair.upper().replace("/", "")
    if pair not in _ALL_G10_PAIRS:
        raise ValueError(
            f"'{pair}' is not a recognized G10 pair. Valid pairs: "
            f"{sorted(_ALL_G10_PAIRS)}"
        )
    return _ALL_G10_PAIRS[pair]


def domestic_and_foreign_currency(pair):
    """Return (domestic_currency, foreign_currency) for a standard G10
    pair, matching this package's fx/*.py convention: domestic = quote
    currency (what domestic_rate should be), foreign = base currency
    (what foreign_rate should be).

    Example: domestic_and_foreign_currency("EURUSD") -> ("USD", "EUR")
             domestic_and_foreign_currency("USDJPY") -> ("JPY", "USD")
    """
    convention = pair_convention(pair)
    return convention["quote"], convention["base"]


def recommended_delta(result, pair):
    """Return whichever of 'delta' or 'delta_premium_adjusted' is the
    market-standard number for the given pair, per its premium currency
    convention. Every fx/*.py pricer computes both fields; this picks the
    correct one rather than requiring the caller to know the convention.
    """
    convention = pair_convention(pair)
    if convention["premium_currency"] == "base":
        return result["delta_premium_adjusted"]
    return result["delta"]
