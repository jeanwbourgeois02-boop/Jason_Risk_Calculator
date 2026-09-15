"""Glue between this package's ported QuantLib pricing core and the repository's
SQLite schema (``data/ingest/schema.py``): reads ``curve_quotes`` / ``trades`` /
``trade_legs``, writes ``curves`` / ``marks``.

Two entry points (task spec):
  - ``bootstrap_and_store(conn, as_of, ccy, index=None)``: bootstraps the OIS curve for
    (ccy, index) from ``curve_quotes`` and writes one ``curves`` row per pillar,
    ``source='QL_PRICER'``. Returns the in-memory ``CurveSet`` (used by
    ``price_and_store`` so DV01 bucket bumping has live ``ql.SimpleQuote`` objects to
    perturb, not just the frozen discount factors written to ``curves``).
  - ``price_and_store(conn, as_of, trade_id)``: prices one IRS trade and writes
    ``PV_USD`` / ``DV01_USD`` / ``PAR_RATE`` marks, ``source='QL_PRICER'``.

Sign / value conventions (see also ``valuation.py`` and ``instruments.py``
docstrings, and CLAUDE.md "P&L conventions"):
  - ``trades.quantity > 0`` -> pay fixed (CLAUDE.md, matches ``data/ingest/irs.py``'s
    documented convention: BNP ``Position > 0`` -> pay fixed). ``notional`` passed to
    the pricer is ``abs(quantity)``.
  - ``PV_USD`` = ``SwapResult.npv`` (QuantLib's own Payer/Receiver-signed NPV, positive
    = asset to the fund). **Caveat**: Phase 1 scope has no FX-spot source wired in, so
    for a non-USD-notional IRS this is actually PV in the swap's own notional currency,
    not converted to USD -- every IRS in the current reference data
    (``data/raw/HA_PNL_20260818.csv``) is USD notional (`IRSOIS-USD-...`), so this does
    not bite yet, but it is a known gap flagged in agent memory
    (``rates-fx-conversion-gap.md``), not silently assumed away.
  - ``DV01_USD`` = ``SwapResult.dv01_parallel`` (NPV change for a +1bp parallel curve
    bump), same non-USD-notional caveat as PV_USD.
  - ``PAR_RATE`` = ``SwapResult.par_rate`` (the swap's fair fixed rate, decimal, e.g.
    0.0398 for 3.98%).
  - ``PnL_USD = PV_USD(t) - PV_USD(trade date)`` per CLAUDE.md is computed by whichever
    P&L engine layer differences two `PV_USD` marks; this module only writes marks.
"""
from __future__ import annotations

import datetime
import sqlite3
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from .conventions import CCY_RFR
from .curves import CurveSet, build_curve_set
from .valuation import SwapResult, price_swap

_NY_ZONE: Optional[ZoneInfo] = None


def _ny() -> ZoneInfo:
    """Lazily resolve America/New_York (see data/bloomberg/pull_marks.py::_ny for why
    this must not be a module-level constant: ZoneInfo("America/New_York") raises
    immediately on a stock Windows Python without the `tzdata` package)."""
    global _NY_ZONE
    if _NY_ZONE is None:
        _NY_ZONE = ZoneInfo("America/New_York")
    return _NY_ZONE


def snapped_at(as_of: datetime.date) -> str:
    """17:00 America/New_York on as_of, ISO with the resolved offset for that date --
    same close convention as CLAUDE.md "Mark time" / data/bloomberg/pull_marks.py."""
    return datetime.datetime(as_of.year, as_of.month, as_of.day, 17, 0, 0, tzinfo=_ny()).isoformat()


def _curve_id(ccy: str, index: str) -> str:
    return f"{ccy}-{index}-OIS"


def _read_curve_quotes(conn: sqlite3.Connection, as_of: str, ccy: str, index: str) -> List[Tuple[str, str, float]]:
    """Rows for (as_of, ccy, index) from curve_quotes, one source only. Multiple
    sources are not blended: prefer 'BBG_BDP' (the live pull's default source, see
    data/bloomberg/rates_marketdata.py::write_curve_quotes) if present, else whichever
    source sorts first alphabetically, for determinism."""
    rows = conn.execute(
        'SELECT tenor, value, source FROM curve_quotes '
        'WHERE as_of_date = ? AND ccy = ? AND "index" = ? AND quote_type = ?',
        (as_of, ccy, index, "OIS"),
    ).fetchall()
    if not rows:
        return []
    sources = sorted({r[2] for r in rows})
    source = "BBG_BDP" if "BBG_BDP" in sources else sources[0]
    picked = [(tenor, value) for tenor, value, src in rows if src == source]
    return picked


def bootstrap_and_store(
    conn: sqlite3.Connection, as_of: str, ccy: str, index: Optional[str] = None
) -> CurveSet:
    """Bootstrap the OIS curve for (ccy, index) as of `as_of` (ISO date string) from
    `curve_quotes`, and write one `curves` row per quoted tenor (`node_date` = the
    OIS helper's pillar date implied by that tenor, `discount_factor` from the
    bootstrapped curve, `par_rate` = the raw quoted rate, `source='QL_PRICER'`).
    Returns the in-memory CurveSet (see module docstring for why).
    """
    index = index or CCY_RFR.get(ccy)
    if index is None:
        raise ValueError(f"No canonical OIS index for currency {ccy!r}; supported: {sorted(CCY_RFR)}")

    quotes = _read_curve_quotes(conn, as_of, ccy, index)
    if not quotes:
        raise ValueError(f"No curve_quotes rows for ({as_of}, {ccy}, {index})")

    as_of_date = datetime.date.fromisoformat(as_of)
    curve_set = build_curve_set(quotes, as_of_date, ccy, index)

    import QuantLib as ql

    from . import qlmap
    from .conventions import CONVENTIONS

    conv = CONVENTIONS.get(ccy, index)
    cal = qlmap.calendar(conv.calendar)
    as_of_ql = qlmap.ql_date(as_of_date)
    spot_ql = cal.advance(as_of_ql, int(conv.spot_lag), ql.Days)

    curve_id = _curve_id(ccy, index)
    rows = []
    for tenor, value in quotes:
        node_ql = cal.advance(spot_ql, qlmap.period(tenor), qlmap.bdc(conv.business_day_convention))
        node_date = qlmap.py_date(node_ql).isoformat()
        df = curve_set.discount_curve.discount(node_ql)
        rows.append((curve_id, as_of, node_date, df, float(value), "QL_PRICER"))

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO curves (curve_id, as_of_date, node_date, discount_factor, par_rate, source) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
    return curve_set


def _read_irs_trade(conn: sqlite3.Connection, trade_id: str):
    trade = conn.execute(
        "SELECT trade_id, instrument_id, product, trade_date, quantity, price "
        "FROM trades WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    if trade is None:
        raise ValueError(f"No trade {trade_id!r}")
    _tid, instrument_id, product, trade_date, quantity, price = trade
    if product != "IRS":
        raise ValueError(f"Trade {trade_id!r} has product {product!r}, expected 'IRS'")

    instrument = conn.execute(
        "SELECT base_ccy, quote_ccy FROM instruments WHERE instrument_id = ?", (instrument_id,)
    ).fetchone()
    if instrument is None:
        raise ValueError(f"No instrument {instrument_id!r}")
    ccy = instrument[0]

    legs = conn.execute(
        "SELECT leg_type, start_date, settle_date FROM trade_legs WHERE trade_id = ? ORDER BY leg_no",
        (trade_id,),
    ).fetchall()
    fixed_leg = next((l for l in legs if l[0] == "FIXED"), None)
    if fixed_leg is None:
        raise ValueError(f"Trade {trade_id!r} has no FIXED leg")
    effective_date = datetime.date.fromisoformat(fixed_leg[1])
    maturity_date = datetime.date.fromisoformat(fixed_leg[2])

    return instrument_id, ccy, trade_date, quantity, price, effective_date, maturity_date


def price_and_store(conn: sqlite3.Connection, as_of: str, trade_id: str) -> SwapResult:
    """Price one IRS trade as of `as_of` and write PV_USD / DV01_USD / PAR_RATE marks
    (source='QL_PRICER'). Bootstraps (and stores) the OIS curve for the trade's
    currency internally via `bootstrap_and_store`, so `curve_quotes` for
    (as_of, ccy, canonical RFR index) must already be populated.
    """
    instrument_id, ccy, _trade_date, quantity, fixed_rate, effective_date, maturity_date = _read_irs_trade(
        conn, trade_id
    )
    curve_set = bootstrap_and_store(conn, as_of, ccy)

    pay_fixed = quantity > 0
    notional = abs(quantity)

    result = price_swap(curve_set, effective_date, maturity_date, fixed_rate, notional, pay_fixed)

    settle_date = maturity_date.isoformat()
    snapped = snapped_at(datetime.date.fromisoformat(as_of))
    rows = [
        (as_of, instrument_id, settle_date, "PV_USD", result.npv, "QL_PRICER", snapped),
        (as_of, instrument_id, settle_date, "DV01_USD", result.dv01_parallel, "QL_PRICER", snapped),
    ]
    if result.par_rate is not None:
        rows.append((as_of, instrument_id, settle_date, "PAR_RATE", result.par_rate, "QL_PRICER", snapped))

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO marks "
            "(as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return result
