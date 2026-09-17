"""Literal replica of the old xlsx workbook's "All FX trades" sheet -- but sourced from
our own live `trades` / `trade_legs` tables, not the xlsx file itself.

User-authorised override of CLAUDE.md's "Must not replicate" list, confirmed 2026-09-17,
for this one table only. This module deliberately reproduces:

  1. the futures P&L understatement bug (dividing by fill instead of mark for pairs/
     instruments whose id does not end in "USD" -- for a futures ticker this is always
     the mark divisor branch, so contracts * multiplier * fill notional is divided by
     the live mark, understating P&L by fill/mark) -- must-not-replicate item 1;
  2. the LTD-2 column's divisor bug: it reuses the t-1 mark as denominator instead of
     the t-2 mark -- must-not-replicate item 2;
  3. converting every row off ONE shared current outright per pair/instrument, not each
     trade's own settle_date -- must-not-replicate item 4 (FX rows only; futures already
     only have one live mark per contract so this collapses to the same thing there).

`engine/pnl/pnl.py` (workbook_valuation_date, workbook_fx_pnl) already implements the
exact formula and quirks needed; this module reuses them rather than re-deriving, and
adds the three-mark-column (t-1 / EOD / t-2) shape the old sheet displayed, sourced from
marks_official on the corresponding business dates. Missing marks stay missing (None /
NaN): no default, no silent zero, per CLAUDE.md's "missing values stay missing" rule.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

from engine.pnl.aggregate import _n_business_days_back
from engine.pnl.pnl import workbook_fx_pnl, workbook_valuation_date

OUTPUT_COLUMNS = [
    "trade_id", "trade_date", "instrument_id", "quantity_usd_notional", "tenor", "fill",
    "mark_t1", "mark_eod", "mark_t2", "pnl_t1", "pnl_eod", "pnl_t2",
]

FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP")


def _trades_df(conn: sqlite3.Connection, as_of_date: str) -> pd.DataFrame:
    placeholders = ",".join("?" * len(FX_PRODUCTS))
    return pd.read_sql_query(
        f"""
        SELECT t.trade_id, t.trade_date, t.instrument_id, t.product, t.quantity,
               t.price AS fill, i.base_ccy, i.quote_ccy, i.multiplier, l.settle_date AS tenor,
               (SELECT SUM(u.amount) FROM trade_legs u
                WHERE u.trade_id = t.trade_id AND u.ccy = 'USD') AS usd_entry
        FROM trades_official t JOIN instruments i ON i.instrument_id = t.instrument_id
        JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
        WHERE t.product IN ({placeholders}, 'FUTURE') AND t.trade_date <= ?
        """,
        conn, params=(*FX_PRODUCTS, as_of_date),
    )


def _quantity_usd_notional(trades: pd.DataFrame) -> pd.Series:
    """Same convention as pnl.ltd_per_trade's workbook_quantity: sign(quantity) * |USD
    leg| for pairs with a USD leg, raw quantity for crosses, contracts*multiplier*fill
    for futures."""
    out = np.sign(trades["quantity"]) * trades["usd_entry"].abs()
    cross = (trades["base_ccy"] != "USD") & (trades["quote_ccy"] != "USD")
    out.loc[cross] = trades.loc[cross, "quantity"]
    future = trades["product"] == "FUTURE"
    out.loc[future] = trades.loc[future, "quantity"] * trades.loc[future, "multiplier"] * trades.loc[future, "fill"]
    return out


def _marks_at(conn: sqlite3.Connection, observation_date: str) -> dict:
    """(instrument_id, settle_date, mark_type) -> value for FWD_OUTRIGHT and FUTURE_PX
    marks_official rows on observation_date."""
    df = pd.read_sql_query(
        "SELECT instrument_id, settle_date, mark_type, value FROM marks_official "
        "WHERE as_of_date = :d AND mark_type IN ('FWD_OUTRIGHT','FUTURE_PX')",
        conn, params={"d": observation_date},
    )
    return {(r.instrument_id, r.settle_date, r.mark_type): r.value for r in df.itertuples()}


def fx_replica(conn: sqlite3.Connection, as_of_date: str) -> pd.DataFrame:
    """One row per trade (FX_SPOT/FX_FWD/FX_SWAP legs and FUTURE), replicating the old
    xlsx "All FX trades" sheet's row-per-fill layout and its known quirks (see module
    docstring). `as_of_date` plays the role of the sheet's live "today"."""
    trades = _trades_df(conn, as_of_date)
    if trades.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    trades["quantity_usd_notional"] = _quantity_usd_notional(trades)

    maturity = workbook_valuation_date(as_of_date)
    future = trades["product"] == "FUTURE"
    # Shared single valuation date per pair for FX (must-not-replicate item 4); futures
    # are always marked at their own expiry (the only date a FUTURE_PX mark exists for).
    trades["mark_settle_date"] = np.where(future, trades["tenor"], maturity)
    trades["mark_type"] = np.where(future, "FUTURE_PX", "FWD_OUTRIGHT")

    t1_date = _n_business_days_back_str(as_of_date, 1)
    t2_date = _n_business_days_back_str(as_of_date, 2)
    eod_marks = _marks_at(conn, as_of_date)
    t1_marks = _marks_at(conn, t1_date)
    t2_marks = _marks_at(conn, t2_date)

    def lookup(marks_dict, row):
        return marks_dict.get((row.instrument_id, row.mark_settle_date, row.mark_type))

    trades["mark_eod"] = [lookup(eod_marks, r) for r in trades.itertuples()]
    trades["mark_t1"] = [lookup(t1_marks, r) for r in trades.itertuples()]
    trades["mark_t2"] = [lookup(t2_marks, r) for r in trades.itertuples()]

    def pnl_eod(r):
        if r.mark_eod is None:
            return None
        return workbook_fx_pnl(r.instrument_id, r.quantity_usd_notional, r.fill, r.mark_eod)

    def pnl_t1(r):
        if r.mark_t1 is None:
            return None
        return workbook_fx_pnl(r.instrument_id, r.quantity_usd_notional, r.fill, r.mark_t1)

    def pnl_t2(r):
        # Deliberate bug (must-not-replicate item 2): denominator uses mark_t1, not
        # mark_t2, even though the numerator uses mark_t2.
        if r.mark_t2 is None or r.mark_t1 is None:
            return None
        return workbook_fx_pnl(r.instrument_id, r.quantity_usd_notional, r.fill, r.mark_t2, r.mark_t1)

    trades["pnl_eod"] = [pnl_eod(r) for r in trades.itertuples()]
    trades["pnl_t1"] = [pnl_t1(r) for r in trades.itertuples()]
    trades["pnl_t2"] = [pnl_t2(r) for r in trades.itertuples()]

    out = trades.rename(columns={"tenor": "tenor"})[OUTPUT_COLUMNS].reset_index(drop=True)
    return out


def _n_business_days_back_str(as_of_date: str, n: int) -> str:
    import datetime as dt
    return _n_business_days_back(dt.date.fromisoformat(as_of_date), n).isoformat()
