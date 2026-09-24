"""Options on commodity futures (product ``CMDTY_OPTION``): Greeks at the vol Bloomberg's own
price implies.

Owned by the listed-options-pricer lane (CLAUDE.md "P&L conventions -> Options on commodity
futures", user decision 2026-09-24). The equity-index branch left the app on 2026-09-24.

**No model enters the P&L.** A listed option's P&L is ``contracts x multiplier x (m - f)`` on
Bloomberg's own price of it (official FUTURE_PX on the option's instrument, dated at its
expiry), read by `engine/pnl`, never by this module. What is computed here are the Greeks, and
a missing input blanks those with its reason, never the P&L.

**How an option sits in the tables** (ingest-parser writes it): ``instruments`` row keyed on
contract-master's canonical option id (``'CLZ26C 70 Comdty'``), ``base_ccy`` the root id
(``'NYMEX:CL'``), ``quote_ccy`` the contract's currency, ``multiplier`` the underlying
future's, ``expiry_date`` the option's last trade date; ``instrument_options`` holds the
strike (quoted scale), CALL / PUT and the payoff, ``'AMERICAN'`` or ``'VANILLA'`` (European).

**Inputs, each from `as_of` alone, never another day's and never another source:**

- the option's price: its official FUTURE_PX at its own expiry;
- the underlying future: ``data.contracts.option_for(base_ccy, instrument_id)``'s underlying
  contract (``'CLZ26 Comdty'``), its official FUTURE_PX at that future's expiry (its
  ``instruments`` row's, Bloomberg's date once applied; a single row at another date is taken
  as the same contract);
- time to expiry: Act/365 calendar days to the option's last trade date
  (``instruments.expiry_date``);
- the discount rate: one zero rate to the expiry off the contract currency's own curve
  (`engine/options/rates.py::resolve_ccy_rate_with_source`: its OIS curve, else a
  ``manual_rates`` row for it). A currency with neither (CNY, MYR, SGD, ... have no OIS
  convention in ``engine/rates/conventions.py``) discounts on the USD SOFR curve instead, and
  ``rate_source`` says so. Black-76's discount factor only scales the price by e^(-rT), so the
  choice moves the implied vol and Greeks by a few basis points at most.

**Model.** Black-76 on the future's price: VANILLA (European) with the vendored closed form
(``options_calc.commodity``, carry tied to ``r``), AMERICAN with Barone-Adesi-Whaley (the
vendored ``_baw_engine``, QuantLib's ``BaroneAdesiWhaleyApproximationEngine``, carry equal to
``r`` so the drift cancels; the vol solved by Brent's method on that price, to ~1e-12, rather than
QuantLib's own BAW solver, which is only ~1e-3 accurate). The vol is the one Bloomberg's price
implies under that same model,
and the Greeks are taken under that same model at that vol (BAW by bump-and-reprice through the
vendored ``finite_difference_greeks``), so the model reprices Bloomberg's price exactly and
nothing else enters. No other vol (a surface, a manual vol) is ever substituted: with no price,
or a price no vol can explain, the Greeks are blank with the reason.

**Price scale.** Bloomberg's option price, the underlying's price, the strike and the fill are
all in the contract's quoted scale (cents for CBOT corn, USD/bbl for WTI): the canonical id
writes the strike as Bloomberg does, and both prices come from Bloomberg's own quote. Nothing
is converted. Black-76 is homogeneous in (price, future, strike), so the vol and the delta do
not depend on the scale at all; gamma, vega and theta are in the quoted unit. A strike more
than ``_SCALE_RATIO`` times away from the future is taken for a scale mix-up and blanks the
Greeks, never guessed at.

**Marks and units**, per 1 LONG option lot (the reader applies the trade's sign and size):

- ``DELTA``: futures-equivalent lots per option lot = dV/dF, the option's multiplier being its
  underlying future's (a mismatch between the two rows blanks the Greeks). Premium-paid
  (discounted) Black-76 delta, e^(-rT) N(d1) for a European call.
- ``GAMMA``: change in DELTA per 1.0 of the future's price as quoted.
- ``VEGA``: price change as quoted per 1 vol point; ``THETA``: per calendar day; ``RHO``: per 1
  percentage point of the discount rate. All three in the quoted price unit, per unit of the
  underlying: times ``multiplier`` for the quote-currency amount of one lot.
- ``PREMIUM``: Bloomberg's price of the option as quoted (per unit of the underlying, quoted
  scale), the number the model was calibrated to. Not x multiplier (until 2026-09-24 it was the
  model's price x multiplier).
- The implied vol is returned (``implied_vol``), never written: it has no mark type.

``price_listed_commodity_option`` computes all of this for one option and writes nothing;
``write_listed_marks`` writes its marks; ``price_and_store_commodity`` /
``price_all_and_store_commodity`` do both per trade.
"""
from __future__ import annotations

import datetime
import math
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import pricer
from .rates import resolve_ccy_rate_with_source
from engine.rates.store import snapped_at

PRODUCT = "CMDTY_OPTION"
PRICER_SOURCE = "QL_OPTIONS_PRICER"

MARK_TYPES = ("PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO")

EUROPEAN_PAYOFF, AMERICAN_PAYOFF = "VANILLA", "AMERICAN"
_PAYOFFS = (EUROPEAN_PAYOFF, AMERICAN_PAYOFF)
MODEL_BY_PAYOFF = {EUROPEAN_PAYOFF: "BLACK76", AMERICAN_PAYOFF: "BAW"}

NO_STRIKE_REASON = "no strike on file: enter the strike under Option terms"   # store.py's wording
_SCALE_RATIO = 20.0
_IV_BRACKET = (1e-4, 5.0)          # the vendored solvers' own vol bounds
_PERPETUAL = "9999-12-31"


# --------------------------------------------------------------------------- reads

def _number(value) -> Optional[float]:
    """`value` as a finite float, or None when it is not a number (a data error)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _read_option(conn: sqlite3.Connection, trade_or_instrument: str) -> Optional[dict]:
    """The option's instrument and terms, by instrument id or by a trade's id."""
    sql = ("SELECT i.instrument_id, i.asset_class, i.base_ccy, i.quote_ccy, i.multiplier, i.expiry_date, "
           "o.strike, o.option_type, o.payoff "
           "FROM instruments i LEFT JOIN instrument_options o ON o.instrument_id = i.instrument_id "
           "WHERE i.instrument_id = ?")
    row = conn.execute(sql, (trade_or_instrument,)).fetchone()
    if row is None:
        hit = conn.execute("SELECT instrument_id FROM trades_official WHERE trade_id = ?",
                           (trade_or_instrument,)).fetchone()
        if hit is None:
            return None
        row = conn.execute(sql, (hit[0],)).fetchone()
        if row is None:
            return None
    keys = ("instrument_id", "asset_class", "root_id", "quote_ccy", "multiplier", "expiry_date",
            "strike", "option_type", "payoff")
    return dict(zip(keys, row))


def _underlying(conn: sqlite3.Connection, root_id: str, instrument_id: str) -> Tuple[Optional[str], str]:
    """(the underlying future's canonical id, '') from contract-master, or (None, reason)."""
    from data.contracts import option_for   # lazy: engine.options stays importable on its own

    try:
        return option_for(root_id, instrument_id, conn=conn).underlying.contract_id, ""
    except (KeyError, ValueError) as exc:        # UnknownContract is a ValueError
        return None, f"underlying future of {instrument_id} not known to the contract master ({exc})"


def _future_price(conn: sqlite3.Connection, as_of: str, underlying: str):
    """(price, multiplier, '') of the underlying future on `as_of`, Bloomberg's official
    FUTURE_PX, or (None, None, reason). The row at the future's own expiry wins; a single row
    at another date is the same contract after its expiry was moved to Bloomberg's own date."""
    inst = conn.execute("SELECT expiry_date, multiplier FROM instruments WHERE instrument_id = ?",
                        (underlying,)).fetchone()
    if inst is None:
        return None, None, f"the underlying future {underlying} has no instruments row"
    rows = conn.execute(
        "SELECT settle_date, value FROM marks_official "
        "WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'FUTURE_PX'",
        (as_of, underlying),
    ).fetchall()
    exact = [r for r in rows if r[0] == inst[0]]
    picked = exact[0] if exact else (rows[0] if len(rows) == 1 else None)
    if picked is None:
        if rows:
            return None, None, (f"{len(rows)} Bloomberg prices of the underlying future {underlying} on {as_of}, "
                                "none at its expiry")
        return None, None, f"no Bloomberg price of the underlying future {underlying} on {as_of}"
    price = _number(picked[1])
    if price is None or price <= 0:
        return None, None, (f"Bloomberg's price of the underlying future {underlying} on {as_of} "
                            f"is not usable ({picked[1]!r})")
    return price, _number(inst[1]), ""


def _listed_price(conn: sqlite3.Connection, as_of: str, instrument_id: str, expiry_iso: str):
    """(price, '') Bloomberg's own price of the option on `as_of` (official FUTURE_PX on the
    option's instrument at its expiry, as quoted), or (None, reason)."""
    hit = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND settle_date = ? "
        "AND mark_type = 'FUTURE_PX'", (as_of, instrument_id, expiry_iso),
    ).fetchone()
    if hit is None:
        return None, f"no Bloomberg price of the option {instrument_id} on {as_of}"
    price = _number(hit[0])
    if price is None:
        return None, f"Bloomberg's price of the option {instrument_id} on {as_of} is not a number ({hit[0]!r})"
    return price, ""


def _discount_rate(conn, as_of: str, ccy: str, expiry_iso: str, curve_cache: Optional[dict]):
    """(r, source, '') the zero rate to expiry off `ccy`'s own curve (OIS, else manual rate),
    else off the USD curve with the fallback named; or (None, '', reason)."""
    rate, reason = resolve_ccy_rate_with_source(conn, as_of, ccy, expiry_iso, curve_cache)
    if rate is not None:
        return rate.rate, rate.detail, ""
    if ccy == "USD":
        return None, "", reason
    usd, usd_reason = resolve_ccy_rate_with_source(conn, as_of, "USD", expiry_iso, curve_cache)
    if usd is None:
        return None, "", f"{reason}, and {usd_reason} to discount on instead"
    return usd.rate, f"{usd.detail} (no {ccy} curve or manual rate on file)", ""


# --------------------------------------------------------------------------- model

def _intrinsic(future: float, strike: float, option_type: str) -> float:
    return max(future - strike, 0.0) if option_type == "CALL" else max(strike - future, 0.0)


def _baw_price_fn(strike: float, option_type: str):
    from .vendor.options_calc import _baw_engine

    kind = option_type.lower()
    return lambda F_, T_, r_, sigma_, q_: _baw_engine.price(F_, strike, T_, r_, sigma_, kind, q_)


def _implied_vol(model: str, price: float, future: float, strike: float, T: float, r: float,
                 option_type: str) -> Tuple[Optional[float], str]:
    """(vol, '') implied by Bloomberg's `price` under `model` with the carry equal to r, or
    (None, reason)."""
    lead = f"no vol is implied by Bloomberg's price {price:g} with the future at {future:g}"
    if price <= 0:
        return None, f"{lead}: a price of zero or less"
    intrinsic = _intrinsic(future, strike, option_type)
    floor = intrinsic if model == "BAW" else intrinsic * math.exp(-r * T)
    if price < floor - 1e-12:
        return None, f"{lead}: it is below the option's intrinsic value {floor:g} at strike {strike:g}"
    cap = (future if option_type == "CALL" else strike) * (1.0 if model == "BAW" else math.exp(-r * T))
    if price >= cap:
        return None, f"{lead}: it is at or above the most the option can be worth ({cap:g})"
    try:
        if model == "BAW":
            # Brent's method on the vendored BAW price itself (monotonic in vol), not QuantLib's
            # solver, whose bumped BAW vega leaves the round trip ~1e-3 off: the Greeks are then
            # taken at a vol that reprices Bloomberg's price to ~1e-10.
            from scipy.optimize import brentq

            fn = _baw_price_fn(strike, option_type)
            lo, hi = _IV_BRACKET
            f_lo, f_hi = fn(future, T, r, lo, r) - price, fn(future, T, r, hi, r) - price
            if f_lo > 0 or f_hi < 0:
                raise ValueError(f"no vol between {lo:g} and {hi:g} reprices it")
            vol = float(brentq(lambda s: fn(future, T, r, s, r) - price, lo, hi, xtol=1e-12, maxiter=200))
        else:
            from .vendor.options_calc.commodity.implied_vol import implied_volatility

            vol = float(implied_volatility(price, future, strike, T, r, option_type.lower()))
    except Exception as exc:  # noqa: BLE001 -- a price the solver cannot reach has no vol
        return None, f"{lead} ({exc})"
    if not (math.isfinite(vol) and vol > 0):
        return None, lead
    return vol, ""


def _greeks(model: str, future: float, strike: float, T: float, r: float, vol: float,
            option_type: str) -> Dict[str, float]:
    """The vendored model's own price and Greeks at `vol` (carry = r, rho summed over both
    legs of the tied rate, as the vendored commodity pricers do)."""
    if model == "BAW":
        from .vendor.options_calc._engine import finite_difference_greeks

        raw = finite_difference_greeks(_baw_price_fn(strike, option_type), future, T, r, vol, r)
        raw["rho"] = raw["rho"] + raw.pop("rho_dividend")
        return raw
    from .vendor.options_calc import commodity

    return commodity.price_european(future, strike, T, r, vol, option_type.lower())


# --------------------------------------------------------------------------- the one call

def price_listed_commodity_option(conn: sqlite3.Connection, trade_or_instrument: str, as_of: str,
                                  curve_cache: Optional[dict] = None) -> dict:
    """The Greeks of one option on a commodity future on `as_of`, written nowhere.

    `trade_or_instrument`: the option's instrument id ('CLZ26C 70 Comdty') or a trade's id.
    Returns a dict, always (never raises on missing data):

    - ``instrument_id``, ``as_of``, ``settle_date`` (the option's expiry, the marks' key);
    - ``marks``: ``{PREMIUM, DELTA, GAMMA, THETA, VEGA, RHO}`` (module docstring for units),
      or ``{}`` when anything is missing;
    - ``reason``: '' when priced, else why the Greeks are blank, in plain words;
    - ``implied_vol``, ``model`` ('BLACK76' | 'BAW'), ``underlying_id``, ``underlying_price``,
      ``option_price``, ``rate``, ``rate_source``: whatever was resolved (None / '' if not);
    - ``result``: a `pricer.OptionPriceResult` of the same numbers (premium = Bloomberg's
      price as quoted) for `engine/options/portfolio.py`, or None.

    `curve_cache`: share one dict across calls of the same day so a currency's OIS curve is
    bootstrapped once."""
    out = {"instrument_id": trade_or_instrument, "as_of": as_of, "settle_date": "", "marks": {}, "reason": "",
           "implied_vol": None, "model": "", "underlying_id": "", "underlying_price": None,
           "option_price": None, "rate": None, "rate_source": "", "result": None}

    def blank(reason: str) -> dict:
        out["reason"] = reason
        return out

    row = _read_option(conn, trade_or_instrument)
    if row is None:
        return blank(f"no option {trade_or_instrument!r} on file")
    inst = row["instrument_id"]
    out["instrument_id"] = inst
    if row["asset_class"] != PRODUCT:
        return blank(f"{inst} is a {row['asset_class']}, not a {PRODUCT}")
    payoff = row["payoff"]
    if payoff is None:
        return blank(f"no option terms on file for {inst}")
    if payoff not in _PAYOFFS:
        return blank(f"payoff {payoff!r} not supported for {PRODUCT} (European or American only)")
    model = MODEL_BY_PAYOFF[payoff]
    out["model"] = model
    strike = _number(row["strike"])
    if not (strike and strike > 0):
        return blank(NO_STRIKE_REASON)
    option_type = row["option_type"]
    if option_type not in ("CALL", "PUT"):
        return blank(f"unrecognized option_type {option_type!r}")

    expiry_iso = row["expiry_date"] or ""
    out["settle_date"] = expiry_iso
    try:
        expiry = datetime.date.fromisoformat(expiry_iso)
    except (TypeError, ValueError):
        expiry = None
    if expiry is None or expiry_iso == _PERPETUAL:
        return blank(f"no expiry on file for {inst} ({expiry_iso!r})")
    as_of_date = datetime.date.fromisoformat(as_of)
    if expiry <= as_of_date:
        return blank(f"expiry {expiry_iso} is not after as_of {as_of}")
    T = pricer.year_fraction(as_of_date, expiry)

    price, reason = _listed_price(conn, as_of, inst, expiry_iso)
    if price is None:
        return blank(reason)
    out["option_price"] = price

    underlying, reason = _underlying(conn, row["root_id"], inst)
    if underlying is None:
        return blank(reason)
    out["underlying_id"] = underlying
    future, fut_multiplier, reason = _future_price(conn, as_of, underlying)
    if future is None:
        return blank(reason)
    out["underlying_price"] = future

    multiplier = _number(row["multiplier"])
    if multiplier is None or fut_multiplier is None or abs(multiplier - fut_multiplier) > 1e-9 * max(
            abs(multiplier), abs(fut_multiplier), 1.0):
        return blank(f"the option's multiplier {row['multiplier']!r} is not its underlying {underlying}'s "
                     f"({fut_multiplier!r}): its delta in futures lots cannot be read off the model")
    if max(strike / future, future / strike) > _SCALE_RATIO:
        return blank(f"strike {strike:g} and the future at {future:g} are more than {_SCALE_RATIO:g}x apart: "
                     "their price scales look different, nothing is converted")

    r, rate_source, reason = _discount_rate(conn, as_of, row["quote_ccy"], expiry_iso, curve_cache)
    if r is None:
        return blank(reason)
    out["rate"], out["rate_source"] = r, rate_source

    vol, reason = _implied_vol(model, price, future, strike, T, r, option_type)
    if vol is None:
        return blank(reason)
    out["implied_vol"] = vol

    raw = _greeks(model, future, strike, T, r, vol, option_type)
    out["marks"] = {"PREMIUM": price, "DELTA": raw["delta"], "GAMMA": raw["gamma"], "THETA": raw["theta"],
                    "VEGA": raw["vega"], "RHO": raw["rho"]}
    out["result"] = pricer.OptionPriceResult(
        premium=price, delta=raw["delta"], gamma=raw["gamma"], theta=raw["theta"], vega=raw["vega"],
        rho=raw["rho"], quote_price=price, delta_premium_adjusted=0.0, delta_convention="N/A")
    return out


def write_listed_marks(conn: sqlite3.Connection, priced: dict, snapped: Optional[str] = None) -> int:
    """Write `priced`'s marks (from `price_listed_commodity_option`) under QL_OPTIONS_PRICER at
    the option's expiry, stamped `snapped` (default: the 15:00 New York close of its as_of).
    Nothing is written for a blank result; returns the number of rows written."""
    marks = priced.get("marks") or {}
    if not marks:
        return 0
    stamp = snapped or snapped_at(datetime.date.fromisoformat(priced["as_of"]))
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO marks "
            "(as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [(priced["as_of"], priced["instrument_id"], priced["settle_date"], mt, marks[mt], PRICER_SOURCE, stamp)
             for mt in MARK_TYPES if mt in marks],
        )
    return len(marks)


# --------------------------------------------------------------------------- per trade

@dataclass
class CommodityOutcome:
    trade_id: str
    instrument_id: str
    package_id: str
    quantity: float
    priced: bool
    reason: str = ""
    result: Optional[pricer.OptionPriceResult] = None
    vol_source: str = ""        # 'IMPLIED' when priced: the only vol ever used
    implied_vol: Optional[float] = None


def _read_trade(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT trade_id, instrument_id, product, package_id, quantity FROM trades_official WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("trade_id", "instrument_id", "product", "package_id", "quantity"), row))


def _outcome(row: dict, priced: dict) -> CommodityOutcome:
    ok = bool(priced["marks"])
    return CommodityOutcome(trade_id=row["trade_id"], instrument_id=row["instrument_id"],
                            package_id=row["package_id"], quantity=row["quantity"], priced=ok,
                            reason=priced["reason"], result=priced["result"],
                            vol_source="IMPLIED" if ok else "", implied_vol=priced["implied_vol"])


def _skip(row: dict, reason: str) -> CommodityOutcome:
    return CommodityOutcome(trade_id=row["trade_id"], instrument_id=row["instrument_id"],
                            package_id=row["package_id"], quantity=row["quantity"], priced=False, reason=reason)


def _price_row(conn: sqlite3.Connection, as_of: str, row: dict, curve_cache: Optional[dict] = None,
               done: Optional[dict] = None) -> CommodityOutcome:
    """Price and store one trade's option; `done` shares one pricing per instrument."""
    if row["product"] != PRODUCT:
        return _skip(row, f"product {row['product']!r} is not {PRODUCT}")
    done = {} if done is None else done
    inst = row["instrument_id"]
    if inst not in done:
        priced = price_listed_commodity_option(conn, inst, as_of, curve_cache)
        write_listed_marks(conn, priced)
        done[inst] = priced
    return _outcome(row, done[inst])


def price_and_store_commodity(conn: sqlite3.Connection, as_of: str, trade_id: str) -> CommodityOutcome:
    row = _read_trade(conn, trade_id)
    if row is None:
        raise ValueError(f"No trade {trade_id!r} in trades_official")
    return _price_row(conn, as_of, row, curve_cache={})


def price_all_and_store_commodity(conn: sqlite3.Connection, as_of: str) -> List[CommodityOutcome]:
    """Price every CMDTY_OPTION trade on file, one outcome per trade, never an exception.

    Each option instrument is priced once (its trades share its marks). One trade's pricer
    blowing up is that trade's own skip, reason ``"pricer error: <exc!r>"``, and every other
    outcome is still returned (2026-09-22: an unguarded loop let one OIS bootstrap failure
    escape and throw away every option's outcome with it). A trade that cannot be read gets an
    outcome too. One `curve_cache` is shared across the run."""
    trade_ids = [r[0] for r in conn.execute(
        "SELECT trade_id FROM trades_official WHERE product = ? ORDER BY trade_id", (PRODUCT,)
    ).fetchall()]
    curve_cache: dict = {}
    done: dict = {}
    outcomes: List[CommodityOutcome] = []
    for trade_id in trade_ids:
        row = None
        try:
            row = _read_trade(conn, trade_id)
            if row is None:
                outcomes.append(CommodityOutcome(trade_id=trade_id, instrument_id="", package_id=trade_id,
                                                 quantity=0.0, priced=False,
                                                 reason="trade could not be read from trades_official"))
                continue
            outcomes.append(_price_row(conn, as_of, row, curve_cache, done))
        except Exception as exc:  # noqa: BLE001 -- one trade's blow-up is its own skip, as in the FX loop
            reason = f"pricer error: {exc!r}"
            outcomes.append(_skip(row, reason) if row else
                            CommodityOutcome(trade_id=trade_id, instrument_id="", package_id=trade_id,
                                             quantity=0.0, priced=False, reason=reason))
    return outcomes
