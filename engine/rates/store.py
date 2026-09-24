"""Glue between this package's QuantLib OIS curve bootstrap and the repository's SQLite
schema (``data/ingest/schema.py``): reads ``curve_quotes``, writes ``curves``.

Since 2026-09-24 (commodity conversion Phase 2, user approval that rates / IRS leave the
app) this package prices no swap. What is left is the OIS discount curve the option
pricers need, and two entry points:

  - ``bootstrap_and_store(conn, as_of, ccy, index=None)``: bootstraps the OIS curve for
    (ccy, index) from ``curve_quotes`` and writes one ``curves`` row per pillar,
    ``source='QL_PRICER'``. Returns the in-memory ``CurveSet``. Called by the live
    pull's curves step (``data/bloomberg/live.py::_curves_step``). The option pricers
    build their own curve in memory from the same quotes (``engine/options/rates.py``
    calls ``curves.build_curve_set`` directly) and do not read the ``curves`` rows.
  - ``snapped_at(as_of)``: the official close stamp, 15:00 New York on `as_of`, shared
    with ``engine/options/store.py``, ``engine/options/equity_commodity.py`` and
    ``data/bloomberg/vol_marketdata.py``.

Retired 2026-09-24 with the swaps: ``price_and_store``, ``price_all_and_store``,
``recalc_on_file`` (swaps), ``reverse_direction_marks``, ``load_fixings`` (the
``index_fixings`` loader) and every ``PV_USD`` / ``DV01_USD`` / ``PAR_RATE`` /
``CASHFLOW_USD`` write. Git history keeps them.

Curve convention (unchanged): flat forwards, log-linear in the discount factor
(``curves.DEFAULT_INTERPOLATION``); a bootstrap that does not converge raises
``CurveBuildError`` with its reason, never a silent curve and never a fallback to
another interpolation (see ``curves.py``).
"""
from __future__ import annotations

import datetime
import sqlite3
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from .conventions import CCY_RFR
from .curves import CurveSet, build_curve_set

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
    """15:00 America/New_York (the official close, user decision 2026-09-21) on as_of, ISO with the resolved offset for that date --
    same close convention as CLAUDE.md "Mark time" / data/bloomberg/pull_marks.py."""
    return datetime.datetime(as_of.year, as_of.month, as_of.day, 15, 0, 0, tzinfo=_ny()).isoformat()


def _curve_id(ccy: str, index: str) -> str:
    return f"{ccy}-{index}-OIS"


def _read_curve_quotes(conn: sqlite3.Connection, as_of: str, ccy: str, index: str) -> List[Tuple[str, float]]:
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
    return [(tenor, value) for tenor, value, src in rows if src == source]


def bootstrap_and_store(
    conn: sqlite3.Connection, as_of: str, ccy: str, index: Optional[str] = None
) -> CurveSet:
    """Bootstrap the OIS curve for (ccy, index) as of `as_of` (ISO date string) from
    `curve_quotes`, and write one `curves` row per quoted tenor (`node_date` = the
    OIS helper's pillar date implied by that tenor, `discount_factor` from the
    bootstrapped curve, `par_rate` = the raw quoted rate, `source='QL_PRICER'`),
    INSERT OR REPLACE, so a rerun of the same day replaces its rows. Returns the
    in-memory CurveSet.

    Raises ValueError when the currency has no OIS index in `CCY_RFR` or no quotes are
    on file for the day, and `CurveBuildError` (from `build_curve_set`) when the
    bootstrap does not converge; nothing is written in either case.
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
