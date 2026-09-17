"""Thin scalar wrappers over the vendored Garman-Kohlhagen FX pricers
(``engine/options/vendor/options_calc/fx/*.py``). No pricing math lives here
-- every function below just shapes plain-scalar inputs/outputs around a call
into the vendored library.

Conventions this module fixes -- restate, don't re-derive, elsewhere in this
package:

- **Domestic vs foreign rate (Garman-Kohlhagen).** This schema quotes a pair
  as ``instruments.quote_ccy`` per 1 unit of ``instruments.base_ccy`` (e.g.
  EURSEK = SEK per 1 EUR). ``domestic_rate`` is therefore the QUOTE
  currency's rate and ``foreign_rate`` is the BASE currency's rate --
  matching both the vendored pricers' own "S = domestic per unit of
  foreign" docstrings and ``options_calc.fx.g10.domestic_and_foreign_currency``
  (domestic = quote, foreign = base). Swap these and every Greek is wrong.

- **DELTA mark convention** (CLAUDE.md's marks table: "DELTA = base-ccy
  delta per 1 unit of trades.quantity"). This module returns the vendored
  pricer's plain, raw spot ``delta`` field unmodified: the sign as computed
  for a LONG position in the given ``option_type`` (a long call is
  positive, a long put is negative). The sign of the actual trade
  (``trades.quantity``, CLAUDE.md: ``> 0`` = long base currency) is applied
  downstream (engine/ladder's delta query: ``t.quantity * m.value``), never
  here -- this module has no notion of which side of the trade it's for.

- **PREMIUM unit conversion** -- the whole reason this module exists rather
  than calling the vendored pricers directly. The vendored FX pricers
  return ``price`` in QUOTE currency per 1 unit of BASE notional (standard
  Garman-Kohlhagen output). The blotter's ``trades.price`` fill, however, is
  a premium expressed as a FRACTION OF BASE-CCY NOTIONAL, paid in the BASE
  currency (see ``data/ingest/blotter.py::_parse_option``; confirmed against
  real rows -- EURSEK 35,000,000 notional @ 0.00579 -> NetInvoice 202,650
  EUR = 35,000,000 * 0.00579; EURUSD 35,000,000 @ 0.00652 -> 228,200 EUR).
  To compare a computed mark against that fill, and so CLAUDE.md's
  ``PnL_USD = (premium_mark - premium_fill) * Size`` works with no further
  conversion, every function here divides the vendored quote-ccy price by
  spot: ``premium = quote_price / spot``. This is exact dimensional
  analysis, not an approximation: quote_price is [quote ccy] per [1 base
  unit]; dividing by spot ([quote ccy] per [1 base unit]) yields a
  dimensionless fraction of base notional -- the same unit trades.price is
  already in.

- **Lazy vendor imports.** Every function below imports its vendored
  ``options_calc.fx.*`` submodule locally, not at module top level.
  Importing ANY name from ``engine.options.vendor.options_calc`` (even one
  submodule) executes that package's ``__init__.py`` chain
  (``options_calc/__init__.py`` -> ``fx/__init__.py`` -> ``fx/european.py``
  -> ``_engine.py``), which imports QuantLib unconditionally. Keeping the
  import inside each function (mirroring ``engine/rates/store.py``'s own
  ``import QuantLib as ql`` placement) means this module itself -- and
  everything that imports it -- stays importable in an environment without
  QuantLib installed; only actually calling a pricer requires it.

- **Time-to-expiry / "today" quirk, worth knowing before it looks like a
  bug.** The vendored ``_engine.py::build_process`` always sets QuantLib's
  evaluation date to ``ql.Date.todaysDate()`` (the REAL system date), not
  to whatever ``as_of`` the caller passed in. This does not make results
  wrong: the vendored engine only ever measures a relative time-to-expiry
  ``T`` from "today", and every date it constructs (the option's maturity)
  is built as ``today + T*365 days`` -- so pricing is entirely a function
  of ``T``, never of the absolute calendar date. This module computes
  ``T = (expiry - as_of).days / 365.0`` (Act/365, matching the vendored
  engine's own ``ql.Actual365Fixed()`` day counter) and passes only ``T``
  through, so a historical ``as_of`` prices identically regardless of the
  machine's real clock date.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Dict


@dataclass
class OptionPriceResult:
    """One pricer call's output, already converted to this app's units.

    premium: base-ccy fraction of notional (see module docstring) -- the
        number directly comparable to ``trades.price`` / marks_official's
        PREMIUM value.
    delta, gamma, theta, vega, rho: the vendored pricer's own fields,
        passed through unmodified (see module docstring on sign / units).
    quote_price: the vendored pricer's raw ``price`` field (quote ccy per
        1 unit of base notional) before the premium conversion above --
        kept for callers (e.g. engine/options/structures.py) that need the
        pre-conversion quote-ccy value.
    """

    premium: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    quote_price: float


def year_fraction(as_of: datetime.date, expiry: datetime.date) -> float:
    """Act/365 year fraction from as_of to expiry, matching the vendored
    engine's own day counter (options_calc/_engine.py: ql.Actual365Fixed()).
    Raises ValueError for an expiry on or before as_of -- nothing to price,
    never silently clamped to some minimum."""
    days = (expiry - as_of).days
    if days <= 0:
        raise ValueError(
            f"expiry {expiry.isoformat()} is not after as_of {as_of.isoformat()}"
        )
    return days / 365.0


def _to_result(raw: Dict[str, float], spot: float) -> OptionPriceResult:
    return OptionPriceResult(
        premium=raw["price"] / spot,
        delta=raw["delta"],
        gamma=raw["gamma"],
        theta=raw["theta"],
        vega=raw["vega"],
        rho=raw["rho"],
        quote_price=raw["price"],
    )


def price_fx_vanilla(
    spot: float,
    strike: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    option_type: str,
) -> OptionPriceResult:
    """European vanilla FX call/put (Garman-Kohlhagen, closed-form)."""
    from .vendor.options_calc.fx import european

    T = year_fraction(as_of, expiry)
    raw = european.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower())
    return _to_result(raw, spot)


def price_fx_digital(
    spot: float,
    strike: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    option_type: str,
    cash_payout: float = 1.0,
) -> OptionPriceResult:
    """European cash-or-nothing digital FX option. cash_payout is in quote
    ccy, same units as the vanilla pricer's raw price -- divided by spot the
    same way to land in the base-notional-fraction premium convention."""
    from .vendor.options_calc.fx import digital

    T = year_fraction(as_of, expiry)
    raw = digital.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower(), cash_payout)
    return _to_result(raw, spot)


def price_fx_american(
    spot: float,
    strike: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    option_type: str,
) -> OptionPriceResult:
    """American-exercise FX vanilla (800-step Cox-Ross-Rubinstein binomial
    tree, bump-and-reprice Greeks -- noticeably slower than the closed-form
    European pricer; expect this call to take on the order of a second)."""
    from .vendor.options_calc.fx import american

    T = year_fraction(as_of, expiry)
    raw = american.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower())
    return _to_result(raw, spot)


def price_fx_asian(
    spot: float,
    strike: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    option_type: str,
    n_fixings: int = 12,
) -> OptionPriceResult:
    """Arithmetic-average Asian FX option (Monte Carlo, 20,000 paths, fixed
    seed -- keep the vendored default path count, per task instruction; this
    call is the slowest of the group, typically several seconds).

    KNOWN LIMITATION: the vendored ``fx/asian.py`` pricer spaces its
    ``n_fixings`` averaging dates evenly across the WHOLE ``T`` from
    ``as_of`` to ``expiry`` -- it has no parameter for a custom averaging
    START date partway through the option's life. ``instrument_options.
    avg_start_date`` is therefore read by ``store.py``'s dispatch (so the
    field is not silently ignored there) but is NOT fed into this function
    or the vendored engine, since no such input exists to feed it into.
    Averaging-window support is out of scope for this phase; flagged here
    rather than approximated."""
    from .vendor.options_calc.fx import asian

    T = year_fraction(as_of, expiry)
    raw = asian.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower(), n_fixings=n_fixings)
    return _to_result(raw, spot)


def price_fx_barrier(
    spot: float,
    strike: float,
    barrier: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    option_type: str,
    barrier_type: str,
    rebate: float = 0.0,
) -> OptionPriceResult:
    """European barrier FX option (KIKO structure). ``barrier_type`` must be
    one of 'up-and-out', 'down-and-out', 'up-and-in', 'down-and-in' -- the
    caller (store.py's dispatch) derives up/down from barrier vs spot; see
    that module for the documented derivation rule."""
    # Aliased on import: `fx/__init__.py` does `from .barrier import ...`-style
    # re-exports for other names, and this function's own `barrier` PARAMETER
    # (the numeric level) would otherwise be shadowed by a same-named module
    # import -- alias to keep the two unambiguous.
    from .vendor.options_calc.fx import barrier as _barrier_mod

    T = year_fraction(as_of, expiry)
    raw = _barrier_mod.price(
        spot, strike, barrier, T, domestic_rate, foreign_rate, vol,
        option_type=option_type.lower(), barrier_type=barrier_type, rebate=rebate,
    )
    return _to_result(raw, spot)


def price_fx_one_touch(
    spot: float,
    barrier: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    direction: str,
    cash_payout: float = 1.0,
) -> OptionPriceResult:
    """One-touch FX option: pays cash_payout (quote ccy) if barrier is ever
    touched before expiry. ``direction`` is 'up' or 'down' -- store.py's
    dispatch derives it from barrier vs spot; see that module."""
    # NOTE: `fx/__init__.py` does `from .one_touch import one_touch, no_touch`,
    # which REBINDS the `fx` package's `one_touch` attribute from the
    # submodule to the FUNCTION of the same name (a real Python gotcha: a
    # later `from .submodule import name` where `name == submodule name`
    # shadows the submodule reference in the parent package's namespace).
    # So `from .vendor.options_calc.fx import one_touch` here imports the
    # FUNCTION directly, not the submodule -- call it, don't attribute-access
    # into it.
    from .vendor.options_calc.fx import one_touch as _one_touch_fn

    T = year_fraction(as_of, expiry)
    raw = _one_touch_fn(spot, barrier, T, domestic_rate, foreign_rate, vol, cash_payout=cash_payout, direction=direction)
    return _to_result(raw, spot)


def price_fx_no_touch(
    spot: float,
    barrier: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    direction: str,
    cash_payout: float = 1.0,
) -> OptionPriceResult:
    """No-touch FX option: pays cash_payout (quote ccy) if barrier is NEVER
    touched before expiry. Arguments: same as price_fx_one_touch."""
    # Same shadowing note as price_fx_one_touch above -- `no_touch` here is
    # the FUNCTION, not a submodule.
    from .vendor.options_calc.fx import no_touch as _no_touch_fn

    T = year_fraction(as_of, expiry)
    raw = _no_touch_fn(spot, barrier, T, domestic_rate, foreign_rate, vol, cash_payout=cash_payout, direction=direction)
    return _to_result(raw, spot)
