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

- **Year fraction is always Act/365 calendar days (revised 2026-09-17).**
  Phase 7 briefly routed T through ``engine/options/calendars.py::
  calendar_year_fraction`` (Business252). The same-day audit showed the
  vendored engine quantises T back to whole calendar days
  (``today + round(T*365)``) and prices off ``Actual365Fixed``, so the
  business-day T only mis-rounded the calendar count (a one-year EURUSD
  option lost 6 days). ``_resolve_T`` therefore always uses ``year_fraction``;
  the ``calendar_aware`` argument is kept for signature compatibility and
  ignored. ``pair`` still drives the delta convention below.

- **delta_convention / DELTA_PA (Phase 7).** ``options_calc.fx.g10.
  recommended_delta`` picks, per pair, whether the market-standard delta
  is raw spot delta or premium-adjusted delta (``fx/_conventions.py``'s
  ``delta_premium_adjusted = delta - price/S``, always computed by every
  vendored fx/*.py pricer regardless of pair). ``OptionPriceResult`` now
  carries both the always-computed ``delta_premium_adjusted`` field and a
  ``delta_convention`` string (``'RAW'`` | ``'PREMIUM_ADJUSTED'`` for a
  recognized G10 pair, ``'UNKNOWN'`` when ``pair`` is ``None`` or not a
  recognized G10 pair). This does NOT change what the ``DELTA`` mark
  means -- ``store.py`` still writes ``result.delta`` (raw) there
  unconditionally, per CLAUDE.md's DELTA convention and this module's own
  rule above. ``store.py`` additionally writes a ``DELTA_PA`` mark from
  ``result.delta_premium_adjusted`` only when ``delta_convention ==
  'PREMIUM_ADJUSTED'`` -- see that module.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class OptionPriceResult:
    """One pricer call's output, already converted to this app's units.

    premium: for FX (price_fx_*), base-ccy fraction of notional (see
        module docstring) -- the number directly comparable to
        ``trades.price`` / marks_official's PREMIUM value. For equity/
        commodity (price_equity_option / price_commodity_option), this is
        instead the UNSCALED quote-ccy price per 1 unit of underlying (no
        base-fraction conversion -- there is no "base notional" concept
        for a single-underlying equity/commodity option the way there is
        for an FX pair) -- store.py multiplies by ``instruments.
        multiplier`` when writing the PREMIUM mark for those asset
        classes. See store.py's equity/commodity section for the full
        unit contrast with FX.
    delta, gamma, theta, vega, rho: the vendored pricer's own fields,
        passed through unmodified (see module docstring on sign / units).
    quote_price: the vendored pricer's raw ``price`` field (quote ccy per
        1 unit of base notional/underlying) before the premium conversion
        above -- kept for callers (e.g. engine/options/structures.py) that
        need the pre-conversion quote-ccy value. Equal to `premium` for
        equity/commodity results (no conversion applied there).
    delta_premium_adjusted: FX only -- ``delta - price/S`` (always present
        on an FX result; 0.0 for equity/commodity, which have no such
        concept). See module docstring's "delta_convention / DELTA_PA"
        section.
    delta_convention: FX only -- 'RAW' | 'PREMIUM_ADJUSTED' | 'UNKNOWN'.
        'N/A' for equity/commodity results.
    """

    premium: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    quote_price: float
    delta_premium_adjusted: float = 0.0
    delta_convention: str = "UNKNOWN"


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


def _delta_convention(pair: Optional[str]) -> str:
    """'RAW' | 'PREMIUM_ADJUSTED' for a recognized G10 pair (via
    options_calc.fx.g10.pair_convention's premium_currency), else
    'UNKNOWN' -- see module docstring's "delta_convention / DELTA_PA"
    section."""
    if pair is None:
        return "UNKNOWN"
    from .vendor.options_calc.fx.g10 import pair_convention

    try:
        convention = pair_convention(pair)
    except ValueError:
        return "UNKNOWN"
    return "PREMIUM_ADJUSTED" if convention["premium_currency"] == "base" else "RAW"


def _to_result(raw: Dict[str, float], spot: float, pair: Optional[str] = None) -> OptionPriceResult:
    return OptionPriceResult(
        premium=raw["price"] / spot,
        delta=raw["delta"],
        gamma=raw["gamma"],
        theta=raw["theta"],
        vega=raw["vega"],
        rho=raw["rho"],
        quote_price=raw["price"],
        delta_premium_adjusted=raw.get("delta_premium_adjusted", 0.0),
        delta_convention=_delta_convention(pair),
    )


def _to_result_plain(raw: Dict[str, float]) -> OptionPriceResult:
    """Equity/commodity result builder -- no spot-fraction premium
    conversion, no delta convention (FX-only concept). See
    OptionPriceResult's own docstring for the unit contrast."""
    return OptionPriceResult(
        premium=raw["price"],
        delta=raw["delta"],
        gamma=raw["gamma"],
        theta=raw["theta"],
        vega=raw["vega"],
        rho=raw["rho"],
        quote_price=raw["price"],
        delta_premium_adjusted=0.0,
        delta_convention="N/A",
    )


def _resolve_T(pair: Optional[str], as_of: datetime.date, expiry: datetime.date, calendar_aware: bool) -> float:
    """T for a price_fx_* call: always the plain Act/365 calendar-day count.

    `calendar_aware` is accepted for signature compatibility and ignored (2026-09-17
    audit). The Business252 fraction from calendars.py::calendar_year_fraction was fed
    in here for a few hours, but the vendored engine rebuilds the maturity as
    `today + round(T * 365)` calendar days and reads every Greek off Actual365Fixed, so
    a business-day T did not change the convention -- it only re-rounded the calendar
    day count, shortening a one-year option by ~6 days (premium ~0.9 % low). Market
    practice for FX option expiry time is calendar days / 365, which is what this is.
    `pair` still selects the delta convention (RAW vs premium-adjusted)."""
    return year_fraction(as_of, expiry)


def price_fx_vanilla(
    spot: float,
    strike: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    option_type: str,
    pair: Optional[str] = None,
    calendar_aware: bool = True,
) -> OptionPriceResult:
    """European vanilla FX call/put (Garman-Kohlhagen, closed-form). See
    module docstring for `pair` / `calendar_aware`."""
    from .vendor.options_calc.fx import european

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    raw = european.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower())
    return _to_result(raw, spot, pair)


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
    pair: Optional[str] = None,
    calendar_aware: bool = True,
) -> OptionPriceResult:
    """European cash-or-nothing digital FX option. cash_payout is in quote
    ccy, same units as the vanilla pricer's raw price -- divided by spot the
    same way to land in the base-notional-fraction premium convention.
    See module docstring for `pair` / `calendar_aware`."""
    from .vendor.options_calc.fx import digital

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    raw = digital.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower(), cash_payout)
    return _to_result(raw, spot, pair)


def price_fx_american(
    spot: float,
    strike: float,
    expiry: datetime.date,
    as_of: datetime.date,
    domestic_rate: float,
    foreign_rate: float,
    vol: float,
    option_type: str,
    pair: Optional[str] = None,
    calendar_aware: bool = True,
) -> OptionPriceResult:
    """American-exercise FX vanilla (800-step Cox-Ross-Rubinstein binomial
    tree, bump-and-reprice Greeks -- noticeably slower than the closed-form
    European pricer; expect this call to take on the order of a second).
    See module docstring for `pair` / `calendar_aware`."""
    from .vendor.options_calc.fx import american

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    raw = american.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower())
    return _to_result(raw, spot, pair)


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
    pair: Optional[str] = None,
    calendar_aware: bool = True,
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

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    raw = asian.price(spot, strike, T, domestic_rate, foreign_rate, vol, option_type.lower(), n_fixings=n_fixings)
    return _to_result(raw, spot, pair)


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
    pair: Optional[str] = None,
    calendar_aware: bool = True,
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

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    raw = _barrier_mod.price(
        spot, strike, barrier, T, domestic_rate, foreign_rate, vol,
        option_type=option_type.lower(), barrier_type=barrier_type, rebate=rebate,
    )
    return _to_result(raw, spot, pair)


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
    pair: Optional[str] = None,
    calendar_aware: bool = True,
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

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    raw = _one_touch_fn(spot, barrier, T, domestic_rate, foreign_rate, vol, cash_payout=cash_payout, direction=direction)
    return _to_result(raw, spot, pair)


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
    pair: Optional[str] = None,
    calendar_aware: bool = True,
) -> OptionPriceResult:
    """No-touch FX option: pays cash_payout (quote ccy) if barrier is NEVER
    touched before expiry. Arguments: same as price_fx_one_touch."""
    # Same shadowing note as price_fx_one_touch above -- `no_touch` here is
    # the FUNCTION, not a submodule.
    from .vendor.options_calc.fx import no_touch as _no_touch_fn

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    raw = _no_touch_fn(spot, barrier, T, domestic_rate, foreign_rate, vol, cash_payout=cash_payout, direction=direction)
    return _to_result(raw, spot, pair)


# --------------------------------------------------------------------------- equity (Phase 7)

_EQUITY_STRIKE_PAYOFFS = {"VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO"}
_EQUITY_BARRIER_PAYOFFS = {"BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH"}


def price_equity_option(
    payoff: str,
    spot: float,
    strike: Optional[float],
    expiry: datetime.date,
    as_of: datetime.date,
    r: float,
    vol: float,
    option_type: Optional[str] = None,
    dividend_yield: float = 0.0,
    barrier: Optional[float] = None,
    barrier_type: Optional[str] = None,
    rebate: float = 0.0,
    cash_payout: float = 1.0,
    direction: Optional[str] = None,
    n_fixings: int = 12,
) -> OptionPriceResult:
    """Dispatch to the vendored equity/*.py pricer matching `payoff`
    (same payoff vocabulary as store.py's FX dispatch: VANILLA, DIGITAL,
    AMERICAN, ASIAN, BARRIER_KI, BARRIER_KO, ONE_TOUCH, NO_TOUCH).
    `premium` on the returned result is UNSCALED quote-ccy price per 1
    unit of the underlying (see OptionPriceResult's own docstring) --
    store.py multiplies by `instruments.multiplier` when writing the
    PREMIUM mark."""
    from .vendor.options_calc import equity

    T = year_fraction(as_of, expiry)

    if payoff == "VANILLA":
        raw = equity.price_european(spot, strike, T, r, vol, option_type.lower(), dividend_yield)
    elif payoff == "AMERICAN":
        raw = equity.price_american(spot, strike, T, r, vol, option_type.lower(), dividend_yield)
    elif payoff == "ASIAN":
        raw = equity.price_asian(spot, strike, T, r, vol, option_type.lower(), dividend_yield, n_fixings)
    elif payoff in ("BARRIER_KI", "BARRIER_KO"):
        raw = equity.price_barrier(
            spot, strike, barrier, T, r, vol, option_type.lower(), barrier_type, rebate, dividend_yield,
        )
    elif payoff == "DIGITAL":
        raw = equity.price_digital(spot, strike, T, r, vol, option_type.lower(), cash_payout, dividend_yield)
    elif payoff in ("ONE_TOUCH", "NO_TOUCH"):
        # Same fx/__init__.py-style submodule/function name shadow as
        # price_fx_one_touch/price_fx_no_touch above -- equity/__init__.py
        # also does `from .one_touch import one_touch, no_touch`.
        from .vendor.options_calc.equity import one_touch as _one_touch_fn, no_touch as _no_touch_fn

        fn = _one_touch_fn if payoff == "ONE_TOUCH" else _no_touch_fn
        raw = fn(spot, barrier, T, r, vol, cash_payout=cash_payout, direction=direction, dividend_yield=dividend_yield)
    else:
        raise ValueError(f"unsupported equity payoff {payoff!r}")

    return _to_result_plain(raw)


# --------------------------------------------------------------------------- commodity (Phase 7)

def price_commodity_option(
    payoff: str,
    future_price: float,
    strike: float,
    expiry: datetime.date,
    as_of: datetime.date,
    r: float,
    vol: float,
    option_type: str,
    n_fixings: int = 12,
) -> OptionPriceResult:
    """Dispatch to the vendored commodity/*.py pricer (Black-76: the
    underlying is a futures/forward price, `dividend_rate` is fixed equal
    to `r` inside the vendored pricers -- see MODELS.md's commodity
    section). Only VANILLA / AMERICAN / ASIAN are implemented upstream --
    no barrier/digital/one-touch commodity pricer exists in
    options_calc/commodity/ (MODELS.md's "Planned" section notes this as
    a known gap, not something skipped here). `premium` is UNSCALED
    quote-ccy price per 1 unit of underlying, same as price_equity_option
    -- see OptionPriceResult's docstring."""
    from .vendor.options_calc import commodity

    T = year_fraction(as_of, expiry)

    if payoff == "VANILLA":
        raw = commodity.price_european(future_price, strike, T, r, vol, option_type.lower())
    elif payoff == "AMERICAN":
        raw = commodity.price_american(future_price, strike, T, r, vol, option_type.lower())
    elif payoff == "ASIAN":
        raw = commodity.price_asian(future_price, strike, T, r, vol, option_type.lower(), n_fixings)
    else:
        raise ValueError(f"unsupported commodity payoff {payoff!r} (only VANILLA/AMERICAN/ASIAN exist upstream)")

    return _to_result_plain(raw)
