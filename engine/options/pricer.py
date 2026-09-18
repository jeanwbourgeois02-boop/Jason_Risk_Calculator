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

- **Cash payoffs (DIGITAL / ONE_TOUCH / NO_TOUCH): payout currency and PREMIUM
  unit (2026-09-18 units audit).** The vendored ``fx/digital.py`` and
  ``fx/one_touch.py`` pay ``cash_payout`` units of the QUOTE (domestic)
  currency. Until this audit the wrappers here called them with
  ``cash_payout=1.0`` and divided by spot like a vanilla, so the PREMIUM mark was
  "the base-ccy value of 1 QUOTE unit paid per 1 base unit of quantity" --
  ``exp(-r_d T) N(d2) / S``: about 1/150 of the right number for USDJPY and 1/11
  for EURSEK. The blotter books a digital differently (the three in
  ``data/raw/new_sample_trades.csv``): ``Quantity`` is the PAYOUT notional, the
  fill is a FRACTION OF THAT PAYOUT, and the premium is invoiced in the pair's
  BASE currency (``Currency`` = EUR for EURSEK112526C, USD for both
  USDJPY111926P; ``NetInvoice = Quantity x Price``). The payout can only be in
  the BASE currency as well: USD 248,000 of premium cannot buy a JPY 2,000,000
  payout, nor EUR 121,000 a SEK 1,000,000 one. CONFIRMED BY THE USER 2026-09-18:
  USD on the USDJPY digitals, EUR on the EURSEK one. So ``payout_ccy='BASE'`` is the
  default of every cash-payoff wrapper below: the option pays ``cash_payout``
  units of BASE currency per 1 unit of ``trades.quantity``, and ``premium`` is
  the base-ccy value of that, i.e. with ``cash_payout=1`` a fraction of the
  payout, between 0 and the BASE currency's discount factor -- the same unit
  as the blotter fill, so ``quantity x (PREMIUM - fill)`` is base-ccy P&L for a
  digital exactly as for a vanilla. ``payout_ccy='QUOTE'`` keeps the old
  vendored meaning (``cash_payout`` QUOTE units per 1 unit of quantity; e.g.
  ``cash_payout=strike`` is "base notional converted at the strike") for a
  ticket the user confirms is booked that way. No pricing model is added here:
    - a BASE-payout digital is the static replication
      ``S_T 1{S_T>K} = (S_T-K)^+ + K 1{S_T>K}`` (call) /
      ``S_T 1{S_T<K} = K 1{S_T<K} - (K-S_T)^+`` (put), i.e. the vendored
      cash digital paying K plus/minus the vendored vanilla, combined
      linearly field by field like ``options_calc.structures.combine``;
    - a BASE-payout touch is the vendored touch priced in the inverted pair
      (spot ``1/S``, level ``1/B``, the two rates swapped, up <-> down), which
      values 1 BASE unit directly, then mapped back to this module's units by
      ``_flip_to_quote_terms``. Tests check the two routes against each other
      and against the closed form ``exp(-r_f T) N(+-d1)``.

- **Units of every field on ``OptionPriceResult`` (FX), all per 1 unit of
  ``trades.quantity`` and for a LONG position** (``store.py`` writes them to
  ``marks`` unchanged; its docstring restates this list and how to turn each
  into a USD figure):
    - ``premium``  dimensionless: base-ccy value / base notional (vanilla,
      American, Asian, barrier) or / base-ccy payout (digital, touch). Paid in
      BASE ccy. NOT pips, NOT a percentage, NOT quote currency.
    - ``quote_price``  ``premium x spot``: QUOTE ccy per 1 unit of quantity.
    - ``delta``  d(quote_price)/d(spot): BASE-ccy units (spot delta, not
      premium-adjusted; exceeds 1 for a digital near its strike).
    - ``delta_premium_adjusted``  ``delta - premium``, BASE-ccy units.
    - ``gamma``  d(delta)/d(spot): change in ``delta`` per move of 1.0 in the
      spot rate as quoted (1 SEK per EUR, 1 JPY per USD, 1.00 USD per EUR) --
      NOT per 1 % and NOT per pip, so it is not comparable across pairs until
      rescaled (``gamma x spot / 100`` = change in delta per 1 % spot move).
    - ``vega``  QUOTE ccy per 1 vol POINT (vol 10 % -> 11 %).
    - ``theta``  QUOTE ccy per 1 CALENDAR day of decay (negative when time
      decay costs the holder).
    - ``rho``  QUOTE ccy per 1 PERCENTAGE POINT (100 bp) rise in the DOMESTIC
      (= quote currency) rate. The foreign-rate rho is computed by the vendored
      pricers but not carried on the result.

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

    premium: for FX (price_fx_*), base-ccy fraction of notional -- of the
        base-ccy PAYOUT for a digital / touch (see module docstring's "Cash
        payoffs" section) -- the number directly comparable to
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


# Fields every vendored fx/*.py pricer returns in the same units (quote ccy per 1 unit
# of base notional, Greeks w.r.t. spot / vol point / calendar day / 1pp of rate), so a
# static replication can add them field by field.
_LINEAR_FIELDS = ("price", "delta", "gamma", "theta", "vega", "rho", "rho_foreign")

PAYOUT_BASE = "BASE"
PAYOUT_QUOTE = "QUOTE"


def _payout_ccy(payout_ccy: str) -> str:
    value = (payout_ccy or "").upper()
    if value not in (PAYOUT_BASE, PAYOUT_QUOTE):
        raise ValueError(f"payout_ccy must be 'BASE' or 'QUOTE', got {payout_ccy!r}")
    return value


def _flip_to_quote_terms(flipped: Dict[str, float], spot: float) -> Dict[str, float]:
    """Map a vendored result priced in the INVERTED pair back to this module's units.

    `flipped` comes from a vendored fx pricer called with spot ``u = 1/S``, the level(s)
    inverted, and the two rates swapped (its "domestic" is this pair's BASE currency), so
    its ``price`` is V_b(u): BASE-ccy value per 1 BASE unit paid. Every other wrapper here
    returns QUOTE-ccy value per unit of quantity, V_q(S) = S * V_b(1/S), with Greeks
    w.r.t. S and the QUOTE-ccy rate. Chain rule, u = 1/S:

        delta  dV_q/dS   = V_b - u * V_b'(u)
        gamma  d2V_q/dS2 = u**3 * V_b''(u)
        theta, vega      = S * (flipped theta, vega)      (S held fixed)
        rho (quote-ccy rate)  = S * flipped rho_foreign   (that world's foreign rate)
        rho_foreign (base-ccy rate) = S * flipped rho

    A change of numeraire on the vendored output, not a pricing model of its own."""
    u = 1.0 / spot
    price_b = flipped["price"]
    raw = {
        "price": spot * price_b,
        "delta": price_b - u * flipped["delta"],
        "gamma": flipped["gamma"] * u ** 3,
        "theta": spot * flipped["theta"],
        "vega": spot * flipped["vega"],
        "rho": spot * flipped["rho_foreign"],
        "rho_foreign": spot * flipped["rho"],
    }
    raw["delta_premium_adjusted"] = raw["delta"] - raw["price"] / spot
    return raw


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
    payout_ccy: str = PAYOUT_BASE,
) -> OptionPriceResult:
    """European digital FX option paying `cash_payout` per 1 unit of quantity if it
    finishes in the money (call: S_T > K; put: S_T < K).

    `payout_ccy='BASE'` (default, the blotter's convention -- module docstring's "Cash
    payoffs" section): the payout is in the pair's BASE currency, so with
    `cash_payout=1.0` the returned `premium` is a FRACTION OF THE PAYOUT, paid in base
    ccy, between 0 and the base-ccy discount factor: exp(-r_f T) N(d1) for a call,
    exp(-r_f T) N(-d1) for a put. Built from the two vendored closed-form pricers by
    static replication (cash digital paying K, plus the vanilla call / minus the vanilla
    put), so every Greek is the same linear combination in the vanilla's own units.

    `payout_ccy='QUOTE'`: the vendored pricer as is -- `cash_payout` QUOTE-ccy units per 1
    unit of quantity; `premium` is then that value in base ccy (price / spot).
    See module docstring for `pair` / `calendar_aware`."""
    from .vendor.options_calc.fx import digital, european

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    kind = option_type.lower()
    if _payout_ccy(payout_ccy) == PAYOUT_QUOTE:
        raw = digital.price(spot, strike, T, domestic_rate, foreign_rate, vol, kind, cash_payout)
        return _to_result(raw, spot, pair)

    cash_leg = digital.price(spot, strike, T, domestic_rate, foreign_rate, vol, kind, strike)
    vanilla_leg = european.price(spot, strike, T, domestic_rate, foreign_rate, vol, kind)
    sign = 1.0 if kind == "call" else -1.0
    raw = {key: cash_payout * (cash_leg[key] + sign * vanilla_leg[key]) for key in _LINEAR_FIELDS}
    raw["delta_premium_adjusted"] = raw["delta"] - raw["price"] / spot
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
    payout_ccy: str = PAYOUT_BASE,
) -> OptionPriceResult:
    """One-touch FX option: pays `cash_payout` per 1 unit of quantity, at expiry, if
    the barrier is ever touched before expiry. ``direction`` is 'up' or 'down' --
    store.py's dispatch derives it from barrier vs spot; see that module.

    `payout_ccy='BASE'` (default): payout in the pair's BASE currency, `premium` = a
    fraction of the payout (with `cash_payout=1.0`), between 0 and the base-ccy discount
    factor -- same convention as `price_fx_digital`, see module docstring's "Cash
    payoffs" section. `payout_ccy='QUOTE'`: the vendored pricer as is (`cash_payout`
    QUOTE-ccy units per 1 unit of quantity)."""
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
    return _price_touch(_one_touch_fn, spot, barrier, T, domestic_rate, foreign_rate, vol,
                        direction, cash_payout, pair, payout_ccy)


def _price_touch(touch_fn, spot, barrier, T, domestic_rate, foreign_rate, vol, direction,
                 cash_payout, pair, payout_ccy) -> OptionPriceResult:
    """Shared body of price_fx_one_touch / price_fx_no_touch. BASE payout = the vendored
    touch priced in the inverted pair (spot 1/S, level 1/B, rates swapped, up <-> down),
    which values 1 BASE unit directly, mapped back by `_flip_to_quote_terms`."""
    if _payout_ccy(payout_ccy) == PAYOUT_QUOTE:
        raw = touch_fn(spot, barrier, T, domestic_rate, foreign_rate, vol,
                       cash_payout=cash_payout, direction=direction)
        return _to_result(raw, spot, pair)
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    flipped = touch_fn(1.0 / spot, 1.0 / barrier, T, foreign_rate, domestic_rate, vol,
                       cash_payout=cash_payout, direction="down" if direction == "up" else "up")
    return _to_result(_flip_to_quote_terms(flipped, spot), spot, pair)


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
    payout_ccy: str = PAYOUT_BASE,
) -> OptionPriceResult:
    """No-touch FX option: pays `cash_payout` per 1 unit of quantity, at expiry, if the
    barrier is NEVER touched before expiry. Arguments and payout-currency convention:
    same as price_fx_one_touch (BASE-ccy payout by default, `premium` = fraction of it)."""
    # Same shadowing note as price_fx_one_touch above -- `no_touch` here is
    # the FUNCTION, not a submodule.
    from .vendor.options_calc.fx import no_touch as _no_touch_fn

    T = _resolve_T(pair, as_of, expiry, calendar_aware)
    return _price_touch(_no_touch_fn, spot, barrier, T, domestic_rate, foreign_rate, vol,
                        direction, cash_payout, pair, payout_ccy)


# --------------------------------------------------------------------------- expiry day (2026-09-18)

# Payoffs whose value at expiry is a function of the closing spot alone. The others
# (ASIAN, BARRIER_KI, BARRIER_KO, ONE_TOUCH, NO_TOUCH) depend on the path spot took.
EXPIRY_PAYOFFS = ("VANILLA", "AMERICAN", "DIGITAL")
PATH_DEPENDENT_PAYOFFS = ("ASIAN", "BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH")


def price_fx_at_expiry(
    payoff: str,
    spot: float,
    strike: float,
    option_type: str,
    pair: Optional[str] = None,
    payout_ccy: str = PAYOUT_BASE,
) -> OptionPriceResult:
    """The option's PAYOFF at `spot`, in this module's units -- what `store.py` writes on
    the expiry date itself, when there is no time value left and a model price (which
    needs T > 0, a vol and two rates) would be both unnecessary and wrong.

    premium (fraction of base notional / of the base-ccy payout, paid in BASE ccy):
      VANILLA, AMERICAN   call max(S-K, 0) / S        put max(K-S, 0) / S
      DIGITAL             1.0 in the money, else 0.0   (BASE payout; QUOTE payout: 1/S)
    "In the money" is STRICT, the vendored pricers' own convention (their docstrings:
    a digital call "pays out if S > K", a put "if S < K"; QuantLib's CashOrNothingPayoff
    and PlainVanillaPayoff both return 0 at S == K): at S == K every payoff here is 0.
    `quote_price` = premium x S as always, so quantity x premium x S is (S-K) x notional
    in QUOTE ccy for an in-the-money call.

    Greeks, same units as every other result: delta = d(quote_price)/dS of the payoff
    itself -- +1 for a call in the money, -1 for a put in the money, 0 otherwise (the
    holder is long / short one unit of BASE ccy against K of QUOTE until the cut; out
    of the money there is nothing left). A BASE-payout digital in the money is worth S
    in quote ccy (one BASE unit is about to be received), so ITS delta is +1 for call
    and put alike and its premium-adjusted delta 0; a QUOTE-payout digital has delta 0.
    gamma = theta = vega = rho = 0.0: no time value, no vol or rate sensitivity left.
    delta_premium_adjusted = delta - premium, as everywhere (an in-the-money call: K/S).

    Raises ValueError for a path-dependent payoff (PATH_DEPENDENT_PAYOFFS): the closing
    spot cannot say whether a barrier was touched or what an average was. No QuantLib
    pricing happens here; `pair` only selects the delta convention, as elsewhere."""
    payoff = (payoff or "").upper()
    if payoff not in EXPIRY_PAYOFFS:
        raise ValueError(f"payoff {payoff!r} cannot be valued from the closing spot alone")
    kind = (option_type or "").lower()
    if kind not in ("call", "put"):
        raise ValueError(f"option_type must be call or put, got {option_type!r}")
    if not (spot and spot > 0 and strike and strike > 0):
        raise ValueError(f"spot and strike must be positive, got spot={spot!r} strike={strike!r}")

    in_the_money = spot > strike if kind == "call" else spot < strike
    if payoff == "DIGITAL":
        base_payout = _payout_ccy(payout_ccy) == PAYOUT_BASE
        quote_value = (spot if base_payout else 1.0) if in_the_money else 0.0
        delta = 1.0 if (in_the_money and base_payout) else 0.0
    else:
        quote_value = abs(spot - strike) if in_the_money else 0.0
        delta = (1.0 if kind == "call" else -1.0) if in_the_money else 0.0
    raw = {"price": quote_value, "delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    raw["delta_premium_adjusted"] = delta - quote_value / spot
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
