"""Equity/commodity option pricing glue (Phase 7, options_calc merge).

No live trade feed exists yet for EQ_OPTION / CMDTY_OPTION instruments --
this phase lands the pricing capability so a future trade type prices on
day one; tests seed synthetic trades directly (no data-ingest parser
exists for either asset class). New ``instruments.asset_class`` values
introduced here: ``'EQ_OPTION'``, ``'CMDTY_OPTION'`` (also used as
``trades.product`` for dispatch, mirroring ``'FX_OPTION'``) -- report to
housekeeper for CLAUDE.md's "Tables" section (out of this package's
ownership to edit CLAUDE.md itself).

**Underlying identity.** Unlike FX_OPTION (whose underlying pair is
``base_ccy + quote_ccy`` of the option's OWN instrument row), an equity/
commodity option's underlying is a single instrument with no natural
base/quote split. This module reuses ``instruments.bbg_ticker`` on the
OPTION's own row to hold the underlying's instrument_id (e.g. ``'SPX
Index'``, ``'GC1 Comdty'``) -- a repurposing of that column distinct from
its FX/FUTURE/IRS meaning ("the Bloomberg ticker for this exact
instrument"), flagged here since it is easy to get backwards.

**SPOT, read from `marks_official`, as instructed.** CLAUDE.md's "Official
marks" table only defines an official source for FX's SPOT/FWD_OUTRIGHT
(``BBG_BFXFORWARD``) -- there is no separate official-source rule for an
equity/commodity underlying yet. Under the CURRENT `marks_official` view
(schema.py's `OFFICIAL_MARK_SOURCE`, out of this package's ownership), a
SPOT row only becomes visible there if stamped `source='BBG_BFXFORWARD'`,
regardless of asset class -- so an equity/commodity underlying's SPOT mark
must currently be written under that same source string to be read here,
which is a real quirk (an equity index spot is not actually an "FX
forward"), not a design choice. Flagged for housekeeper/data-ingest to
consider a dedicated equity/commodity SPOT source later.

**Rate**: a single OIS zero rate to the option's expiry, off
``instruments.quote_ccy``'s curve (``engine/options/rates.py::
resolve_ccy_rate``) -- no domestic/foreign split (that's an FX-only
concept; commodity Black-76 and equity Black-Scholes-Merton both take one
discount rate).

**Dividend yield** (equity only): a manual ``equity_dividend_yields``
table, defensive DDL (CREATE IF NOT EXISTS), NO DEFAULT ROW -- every
equity option trade needs an explicit entry, even ``0.0`` for a
non-dividend payer; missing -> skip "no dividend yield". Commodity
options take no dividend-yield input at all (Black-76 ties the
cost-of-carry rate to `r` inside the vendored pricer itself -- see
MODELS.md's commodity section).

**Vol**: two sources, in priority order, never blended:
  (a) ``vol_surface_points`` -- a strike x tenor grid per (as_of,
      underlying), built into an ``options_calc.vol_surface.VolSurface``
      and read via ``get_vol(K, T)``. Only used when strike is known
      (> 0) and a full rectangular grid is staged.
  (b) this package's own flat ``option_vols`` table (``inputs.py``),
      reusing its `pair` column keyed on the underlying's instrument_id
      instead of an FX pair string -- exact-expiry match, then `'*'` flat
      fallback, via the existing `get_manual_vol`.
  (c) else skip "no vol".

**Premium unit, restated (see pricer.py's OptionPriceResult docstring).**
The vendored equity/commodity pricers return an UNSCALED quote-ccy price
per 1 unit of underlying. The PREMIUM mark written here is that price
times ``instruments.multiplier`` (NOT the FX base-notional-fraction
convention) -- the concrete point where that multiplication happens is
`_price_eq_cmdty_row` below.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from . import pricer
from .inputs import get_manual_vol
from .rates import resolve_ccy_rate
from engine.rates.store import snapped_at

_MARK_FIELDS = ("PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO")

_STRIKE_PAYOFFS = {"VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO"}
_BARRIER_PAYOFFS = {"BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH"}
_EQUITY_PAYOFFS = _STRIKE_PAYOFFS | _BARRIER_PAYOFFS
_COMMODITY_PAYOFFS = {"VANILLA", "AMERICAN", "ASIAN"}  # no barrier/digital/one-touch upstream

# --------------------------------------------------------------------------- tables

_EQUITY_DIV_DDL = """
CREATE TABLE IF NOT EXISTS equity_dividend_yields (
  as_of_date      TEXT NOT NULL,
  underlying      TEXT NOT NULL,   -- underlying instrument_id, e.g. 'SPX Index'
  dividend_yield  REAL NOT NULL,   -- continuous, decimal; 0.0 for a non-dividend payer (explicit, not a sentinel)
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, underlying, source)
);
"""

_VOL_SURFACE_POINTS_DDL = """
CREATE TABLE IF NOT EXISTS vol_surface_points (
  as_of_date      TEXT NOT NULL,
  underlying      TEXT NOT NULL,
  tenor_days      INTEGER NOT NULL,
  strike          REAL NOT NULL,
  vol             REAL NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, underlying, tenor_days, strike, source)
);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(_EQUITY_DIV_DDL)
    conn.execute(_VOL_SURFACE_POINTS_DDL)


def set_dividend_yield(conn: sqlite3.Connection, as_of: str, underlying: str, dividend_yield: float, source: str = "MANUAL") -> None:
    ensure_tables(conn)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO equity_dividend_yields (as_of_date, underlying, dividend_yield, source) "
            "VALUES (?,?,?,?)",
            (as_of, underlying, float(dividend_yield), source),
        )


def get_dividend_yield(conn: sqlite3.Connection, as_of: str, underlying: str, source: str = "MANUAL") -> Optional[float]:
    ensure_tables(conn)
    row = conn.execute(
        "SELECT dividend_yield FROM equity_dividend_yields WHERE as_of_date = ? AND underlying = ? AND source = ?",
        (as_of, underlying, source),
    ).fetchone()
    return row[0] if row is not None else None


def write_vol_surface_points(conn: sqlite3.Connection, as_of: str, underlying: str, points, source: str = "MANUAL") -> None:
    """`points`: iterable of (tenor_days, strike, vol) tuples -- must form a
    full rectangular grid (every strike quoted at every tenor) for
    `_build_vol_surface` to use it; a partial grid is refused there, not
    guessed at."""
    ensure_tables(conn)
    rows = [(as_of, underlying, int(td), float(k), float(v), source) for td, k, v in points]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO vol_surface_points "
            "(as_of_date, underlying, tenor_days, strike, vol, source) VALUES (?,?,?,?,?,?)",
            rows,
        )


def _build_vol_surface(conn: sqlite3.Connection, as_of: str, underlying: str, source: str = "MANUAL"):
    """Build an options_calc.vol_surface.VolSurface from vol_surface_points,
    or None if no points are staged, or if the staged points don't form a
    full rectangular strike x tenor grid (refused rather than guessed)."""
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT DISTINCT tenor_days, strike, vol FROM vol_surface_points "
        "WHERE as_of_date = ? AND underlying = ? AND source = ? ORDER BY tenor_days, strike",
        (as_of, underlying, source),
    ).fetchall()
    if not rows:
        return None
    tenors = sorted({r[0] for r in rows})
    strikes = sorted({r[1] for r in rows})
    grid = {(td, k): v for td, k, v in rows}
    try:
        vols = [[grid[(td, k)] for k in strikes] for td in tenors]
    except KeyError:
        return None
    from .vendor.options_calc.vol_surface import VolSurface

    return VolSurface(strikes=strikes, tenors=[td / 365.0 for td in tenors], vols=vols)


# --------------------------------------------------------------------------- inputs

@dataclass
class EqCmdtyMarketInputs:
    spot: float
    r: float
    vol: float
    vol_source: str  # 'SURFACE' | 'MANUAL'
    dividend_yield: float = 0.0  # equity only; always 0.0 (unused) for commodity


@dataclass
class EqCmdtyInputsResult:
    inputs: Optional[EqCmdtyMarketInputs]
    reason: str = ""


def _resolve_vol(conn, as_of, underlying, expiry_iso, strike, as_of_date, expiry_date):
    surface = _build_vol_surface(conn, as_of, underlying)
    if surface is not None and strike is not None and strike > 0:
        T = (expiry_date - as_of_date).days / 365.0
        return surface.get_vol(strike, T), "SURFACE"
    manual = get_manual_vol(conn, as_of, underlying, expiry_iso)
    if manual is not None:
        return manual, "MANUAL"
    return None, ""


def _resolve_inputs(conn, as_of, underlying, quote_ccy, expiry_iso, strike, curve_cache, need_dividend) -> EqCmdtyInputsResult:
    spot_row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, underlying),
    ).fetchone()
    if spot_row is None:
        return EqCmdtyInputsResult(None, "no underlying SPOT mark")
    spot = spot_row[0]

    dividend_yield = 0.0
    if need_dividend:
        dividend_yield = get_dividend_yield(conn, as_of, underlying)
        if dividend_yield is None:
            return EqCmdtyInputsResult(None, "no dividend yield")

    r, reason = resolve_ccy_rate(conn, as_of, quote_ccy, expiry_iso, curve_cache)
    if r is None:
        return EqCmdtyInputsResult(None, reason)

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry_date = datetime.date.fromisoformat(expiry_iso)
    vol, vol_source = _resolve_vol(conn, as_of, underlying, expiry_iso, strike, as_of_date, expiry_date)
    if vol is None:
        return EqCmdtyInputsResult(None, "no vol")

    return EqCmdtyInputsResult(EqCmdtyMarketInputs(
        spot=spot, r=r, vol=vol, vol_source=vol_source, dividend_yield=dividend_yield,
    ))


def resolve_equity_inputs(conn, as_of, underlying, quote_ccy, expiry_iso, strike, curve_cache=None) -> EqCmdtyInputsResult:
    return _resolve_inputs(conn, as_of, underlying, quote_ccy, expiry_iso, strike, curve_cache, need_dividend=True)


def resolve_commodity_inputs(conn, as_of, underlying, quote_ccy, expiry_iso, strike, curve_cache=None) -> EqCmdtyInputsResult:
    return _resolve_inputs(conn, as_of, underlying, quote_ccy, expiry_iso, strike, curve_cache, need_dividend=False)


# --------------------------------------------------------------------------- store

@dataclass
class EqCmdtyOutcome:
    trade_id: str
    instrument_id: str
    package_id: str
    quantity: float
    priced: bool
    reason: str = ""
    result: Optional[pricer.OptionPriceResult] = None
    vol_source: str = ""


def _read_eq_cmdty_trade(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT t.trade_id, t.instrument_id, t.product, t.package_id, t.quantity,
               i.quote_ccy, i.multiplier, i.bbg_ticker, i.expiry_date,
               o.strike, o.option_type, o.barrier_level, o.payoff
        FROM trades_official t
        JOIN instruments i ON i.instrument_id = t.instrument_id
        LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id
        WHERE t.trade_id = ?
        """,
        (trade_id,),
    ).fetchone()
    if row is None:
        return None
    (trade_id, instrument_id, product, package_id, quantity,
     quote_ccy, multiplier, underlying, expiry_date,
     strike, option_type, barrier_level, payoff) = row
    return dict(
        trade_id=trade_id, instrument_id=instrument_id, product=product, package_id=package_id,
        quantity=quantity, quote_ccy=quote_ccy, multiplier=multiplier, underlying=underlying,
        expiry_date=expiry_date, strike=strike, option_type=option_type,
        barrier_level=barrier_level, payoff=payoff,
    )


def _skip(row: dict, reason: str) -> EqCmdtyOutcome:
    return EqCmdtyOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=False, reason=reason,
    )


def _dispatch_equity(row: dict, as_of_date, expiry, inputs: EqCmdtyMarketInputs) -> pricer.OptionPriceResult:
    S, K, option_type, payoff = inputs.spot, row["strike"], row["option_type"], row["payoff"]
    if payoff in _BARRIER_PAYOFFS:
        barrier_level = row["barrier_level"]
        direction = "up" if barrier_level > S else "down"
        if payoff in ("BARRIER_KI", "BARRIER_KO"):
            suffix = "-and-in" if payoff == "BARRIER_KI" else "-and-out"
            return pricer.price_equity_option(
                payoff, S, K, expiry, as_of_date, inputs.r, inputs.vol, option_type, inputs.dividend_yield,
                barrier=barrier_level, barrier_type=f"{direction}{suffix}",
            )
        return pricer.price_equity_option(
            payoff, S, K, expiry, as_of_date, inputs.r, inputs.vol, dividend_yield=inputs.dividend_yield,
            barrier=barrier_level, direction=direction,
        )
    return pricer.price_equity_option(payoff, S, K, expiry, as_of_date, inputs.r, inputs.vol, option_type, inputs.dividend_yield)


def _dispatch_commodity(row: dict, as_of_date, expiry, inputs: EqCmdtyMarketInputs) -> pricer.OptionPriceResult:
    return pricer.price_commodity_option(
        row["payoff"], inputs.spot, row["strike"], expiry, as_of_date, inputs.r, inputs.vol, row["option_type"],
    )


def _price_eq_cmdty_row(conn: sqlite3.Connection, as_of: str, row: dict, asset_kind: str, curve_cache: Optional[dict] = None) -> EqCmdtyOutcome:
    if row["product"] != asset_kind:
        return _skip(row, f"product {row['product']!r} is not {asset_kind}")
    if row["payoff"] is None:
        return _skip(row, "no instrument_options row")

    payoff = row["payoff"]
    supported = _EQUITY_PAYOFFS if asset_kind == "EQ_OPTION" else _COMMODITY_PAYOFFS
    if payoff not in supported:
        return _skip(row, f"payoff {payoff!r} not supported for {asset_kind}")

    # Same wording as the FX path (store.py::NO_STRIKE_REASON / NO_BARRIER_REASON; not
    # imported, store.py is the FX glue and this module stays independent of it).
    if payoff in _STRIKE_PAYOFFS and not (row["strike"] and row["strike"] > 0):
        return _skip(row, "no strike on file: enter the strike under Option terms")
    if payoff in _BARRIER_PAYOFFS and not (row["barrier_level"] and row["barrier_level"] > 0):
        return _skip(row, "no barrier / touch level on file: enter it under Option terms")
    if payoff not in ("ONE_TOUCH", "NO_TOUCH") and row["option_type"] not in ("CALL", "PUT"):
        return _skip(row, f"unrecognized option_type {row['option_type']!r}")

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry = datetime.date.fromisoformat(row["expiry_date"])
    if expiry <= as_of_date:
        return _skip(row, f"expiry {row['expiry_date']} is not after as_of {as_of}")

    resolver = resolve_equity_inputs if asset_kind == "EQ_OPTION" else resolve_commodity_inputs
    inputs_result = resolver(conn, as_of, row["underlying"], row["quote_ccy"], row["expiry_date"], row["strike"], curve_cache)
    if inputs_result.inputs is None:
        return _skip(row, inputs_result.reason)
    inputs = inputs_result.inputs

    dispatch = _dispatch_equity if asset_kind == "EQ_OPTION" else _dispatch_commodity
    result = dispatch(row, as_of_date, expiry, inputs)

    snapped = snapped_at(as_of_date)
    settle_date = row["expiry_date"]
    multiplier = row["multiplier"]
    # Premium unit, restated (see pricer.py's OptionPriceResult docstring
    # and this module's own docstring): quote-ccy price per unit x
    # multiplier -- NOT the FX base-notional-fraction conversion.
    values = {
        "PREMIUM": result.premium * multiplier,
        "DELTA": result.delta,
        "GAMMA": result.gamma,
        "THETA": result.theta,
        "VEGA": result.vega,
        "RHO": result.rho,
    }
    mark_rows = [
        (as_of, row["instrument_id"], settle_date, mt, values[mt], "QL_OPTIONS_PRICER", snapped)
        for mt in _MARK_FIELDS
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO marks "
            "(as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?)",
            mark_rows,
        )

    return EqCmdtyOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=True, result=result, vol_source=inputs.vol_source,
    )


def price_and_store_equity(conn: sqlite3.Connection, as_of: str, trade_id: str) -> EqCmdtyOutcome:
    row = _read_eq_cmdty_trade(conn, trade_id)
    if row is None:
        raise ValueError(f"No trade {trade_id!r} in trades_official")
    return _price_eq_cmdty_row(conn, as_of, row, "EQ_OPTION", curve_cache={})


def price_and_store_commodity(conn: sqlite3.Connection, as_of: str, trade_id: str) -> EqCmdtyOutcome:
    row = _read_eq_cmdty_trade(conn, trade_id)
    if row is None:
        raise ValueError(f"No trade {trade_id!r} in trades_official")
    return _price_eq_cmdty_row(conn, as_of, row, "CMDTY_OPTION", curve_cache={})


def price_all_and_store_equity(conn: sqlite3.Connection, as_of: str) -> List[EqCmdtyOutcome]:
    trade_ids = [r[0] for r in conn.execute(
        "SELECT trade_id FROM trades_official WHERE product = 'EQ_OPTION' ORDER BY trade_id"
    ).fetchall()]
    curve_cache: dict = {}
    return [_price_eq_cmdty_row(conn, as_of, _read_eq_cmdty_trade(conn, tid), "EQ_OPTION", curve_cache) for tid in trade_ids]


def price_all_and_store_commodity(conn: sqlite3.Connection, as_of: str) -> List[EqCmdtyOutcome]:
    trade_ids = [r[0] for r in conn.execute(
        "SELECT trade_id FROM trades_official WHERE product = 'CMDTY_OPTION' ORDER BY trade_id"
    ).fetchall()]
    curve_cache: dict = {}
    return [_price_eq_cmdty_row(conn, as_of, _read_eq_cmdty_trade(conn, tid), "CMDTY_OPTION", curve_cache) for tid in trade_ids]
