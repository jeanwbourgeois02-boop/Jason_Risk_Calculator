"""Layer 2 valuation: one row per trade, at one date's marks. docs/BUILD_PLAN.md section 2.

`value_book(conn, as_of, marks_source=None)` is the headline calculation: FX (spot,
forward, swap legs -- all stored as ordinary 2-leg FX trades, see the swap packaging
note below) and futures, each valued fresh from `marks_official` (or from `marks`
filtered to `marks_source` when given, e.g. the Reconciliation tab's manual entries).
Settled trades are never recomputed: their row is read back from `realised_pnl`
(engine/pnl/ledger.realise_settled must have populated it first; a settled trade with
no realised_pnl row is Unavailable, not recomputed from a stale open-trade formula).

Swap packaging: per CLAUDE.md's package_id rule and docs/BUILD_PLAN.md task B, an
FX_SWAP is stored as TWO ordinary `trades` rows (near + far), each with its own 2 legs,
sharing `package_id`. So no special-casing is needed here: both legs value exactly like
any other FX_FWD row; grouping by package_id for display is a UI concern.

USD conversion `S` (quote currency -> USD) at spot: identity when quote is USD,
otherwise the SPOT mark of instrument `USD<quote>` inverted, or of `<quote>USD`
directly -- whichever is on file. No USD leg is ever invented for a cross.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Optional

import pandas as pd

COLUMNS = [
    "trade_id", "instrument_id", "product", "strategy", "theme", "trade_date",
    "settle_date", "status", "quantity", "fill", "mark", "mark_date", "mark_source",
    "spot", "spot_source", "pnl_local", "pnl_usd", "pnl_spot_usd", "pnl_carry_usd",
    "reason", "note",
]

FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP")

_NAN = float("nan")


def _has_theme_column(conn: sqlite3.Connection) -> bool:
    return any(r[1] == "theme" for r in conn.execute("PRAGMA table_info(trades)"))


def _fx_sql(theme: bool) -> str:
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill,
               i.base_ccy, i.quote_ccy, l.settle_date
        FROM trades t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        WHERE t.product IN ({",".join("?" * len(FX_PRODUCTS))}) AND t.trade_date <= ?
          AND l.leg_no = 1
    """


def _fut_sql(theme: bool) -> str:
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill, i.multiplier, l.settle_date
        FROM trades t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        WHERE t.product = 'FUTURE' AND t.trade_date <= :as_of AND l.leg_no = 1
    """


def _mark_table(source: Optional[str]) -> str:
    return "marks_official" if source is None else "marks"


def _mark_at(conn: sqlite3.Connection, instrument_id: str, settle_date: str, mark_type: str,
             as_of: str, source: Optional[str]) -> Optional[tuple]:
    """(value, source) of the mark dated exactly `as_of` for `settle_date`, or None."""
    table = _mark_table(source)
    extra = "" if source is None else " AND source = :source"
    row = conn.execute(
        f"SELECT value, source, snapped_at FROM {table} WHERE instrument_id = :i AND settle_date = :s "
        f"AND mark_type = :m AND as_of_date = :d{extra} ORDER BY snapped_at DESC LIMIT 1",
        {"i": instrument_id, "s": settle_date, "m": mark_type, "d": as_of, "source": source},
    ).fetchone()
    return None if row is None else (row[0], row[1])


def usd_per_quote(conn: sqlite3.Connection, quote_ccy: str, as_of: str,
                   source: Optional[str] = None) -> tuple:
    """(S, pair, mark_source) converting 1 unit of quote_ccy to USD at spot on `as_of`.
    Never invents a USD leg for a cross: tries USD<quote> (inverted) then <quote>USD."""
    if quote_ccy == "USD":
        return 1.0, "USD", "identity"
    inv_pair = f"USD{quote_ccy}"
    hit = _mark_at(conn, inv_pair, as_of, "SPOT", as_of, source)
    if hit is not None and hit[0]:
        return 1.0 / float(hit[0]), inv_pair, hit[1]
    direct_pair = f"{quote_ccy}USD"
    hit = _mark_at(conn, direct_pair, as_of, "SPOT", as_of, source)
    if hit is not None:
        return float(hit[0]), direct_pair, hit[1]
    return _NAN, None, None


def _realised_row(conn: sqlite3.Connection, trade_id: str) -> Optional[pd.Series]:
    df = pd.read_sql_query("SELECT * FROM realised_pnl WHERE trade_id = :t", conn, params={"t": trade_id})
    return None if df.empty else df.iloc[0]


def value_book(conn: sqlite3.Connection, as_of: str, marks_source: Optional[str] = None) -> pd.DataFrame:
    """One row per trade at `as_of`'s marks. See module docstring and BUILD_PLAN section 2."""
    rows = []
    theme = _has_theme_column(conn)
    # sqlite3 params: positional IN(...) placeholders followed by :as_of.
    fx = pd.read_sql_query(_fx_sql(theme), conn, params=(*FX_PRODUCTS, as_of))
    fut = pd.read_sql_query(_fut_sql(theme), conn, params={"as_of": as_of})

    for r in fx.itertuples(index=False):
        status = "SETTLED" if r.settle_date < as_of else "OPEN"
        base = {
            "trade_id": r.trade_id, "instrument_id": r.instrument_id, "product": r.product,
            "strategy": r.strategy, "theme": r.theme, "trade_date": r.trade_date,
            "settle_date": r.settle_date, "status": status, "quantity": r.quantity, "fill": r.fill,
        }
        if status == "SETTLED":
            rows.append({**base, **_settled_fx_row(conn, r.trade_id)})
            continue
        rows.append({**base, **_open_fx_row(conn, r, as_of, marks_source)})

    for r in fut.itertuples(index=False):
        status = "SETTLED" if r.settle_date < as_of else "OPEN"
        base = {
            "trade_id": r.trade_id, "instrument_id": r.instrument_id, "product": r.product,
            "strategy": r.strategy, "theme": r.theme, "trade_date": r.trade_date,
            "settle_date": r.settle_date, "status": status, "quantity": r.quantity, "fill": r.fill,
        }
        if status == "SETTLED":
            rows.append({**base, **_settled_future_row(conn, r.trade_id)})
            continue
        rows.append({**base, **_open_future_row(conn, r, as_of, marks_source)})

    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame(rows, columns=COLUMNS)


def _open_fx_row(conn, r, as_of, marks_source) -> dict:
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=_NAN, spot_source="",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN, reason="", note="")
    m_hit = _mark_at(conn, r.instrument_id, r.settle_date, "FWD_OUTRIGHT", as_of, marks_source)
    if m_hit is None:
        out["reason"] = f"no FWD_OUTRIGHT mark for {r.instrument_id} settle {r.settle_date} on {as_of}"
        return out
    m, m_src = float(m_hit[0]), m_hit[1]
    out["mark"], out["mark_source"] = m, m_src
    s, s_pair, s_src = usd_per_quote(conn, r.quote_ccy, as_of, marks_source)
    if s != s or s_pair is None:
        out["spot_source"] = ""
        out["reason"] = f"no SPOT for USD conversion of {r.quote_ccy} on {as_of}"
        return out
    out["spot"], out["spot_source"] = s, s_src
    pnl_local = r.quantity * (m - r.fill)
    pnl_usd = pnl_local * s
    out["pnl_local"], out["pnl_usd"] = pnl_local, pnl_usd
    spot_hit = _mark_at(conn, r.instrument_id, as_of, "SPOT", as_of, marks_source)
    if spot_hit is None:
        out["pnl_spot_usd"], out["pnl_carry_usd"] = pnl_usd, 0.0
        out["note"] = f"no SPOT for {r.instrument_id} on {as_of}; carry split unavailable"
        return out
    m_spot = float(spot_hit[0])
    pnl_carry = r.quantity * (m - m_spot) * s
    out["pnl_carry_usd"] = pnl_carry
    out["pnl_spot_usd"] = pnl_usd - pnl_carry
    return out


def _settled_fx_row(conn, trade_id) -> dict:
    row = _realised_row(conn, trade_id)
    if row is None:
        return dict(mark=_NAN, mark_date="", mark_source="", spot=_NAN, spot_source="",
                    pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN,
                    reason=f"settled trade {trade_id} not yet realised (run realise_settled)")
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=row["spot_usd_per_local"], spot_source=row["spot_source"],
                pnl_local=_NAN, pnl_usd=float(row["pnl_usd"]), pnl_spot_usd=float(row["pnl_usd"]),
                pnl_carry_usd=0.0, reason="", note=row["note"])


def _open_future_row(conn, r, as_of, marks_source) -> dict:
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=1.0, spot_source="identity",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="", note="")
    m_hit = _mark_at(conn, r.instrument_id, r.settle_date, "FUTURE_PX", as_of, marks_source)
    if m_hit is None:
        out["reason"] = f"no FUTURE_PX mark for {r.instrument_id} expiry {r.settle_date} on {as_of}"
        return out
    m, m_src = float(m_hit[0]), m_hit[1]
    out["mark"], out["mark_source"] = m, m_src
    pnl = r.quantity * r.multiplier * (m - r.fill)
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl, pnl, pnl
    return out


def _settled_future_row(conn, trade_id) -> dict:
    row = _realised_row(conn, trade_id)
    if row is None:
        return dict(mark=_NAN, mark_date="", mark_source="", spot=1.0, spot_source="identity",
                    pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN,
                    reason=f"settled trade {trade_id} not yet realised (run realise_settled)")
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=1.0, spot_source="identity", pnl_local=_NAN, pnl_usd=float(row["pnl_usd"]),
                pnl_spot_usd=float(row["pnl_usd"]), pnl_carry_usd=0.0, reason="", note=row["note"])
