"""Blotter FX/futures sub-tab rows: the old xlsx "All FX trades" sheet's column layout
(trade / tenor / fill / t-1-EOD-t-2 mark and P&L columns), but priced under CLAUDE.md's
market-standard "P&L conventions" -- NOT the workbook arithmetic.

User reversal, 2026-09-17: `engine/pnl/xlsx_fx_replica.py` (built earlier the same day)
deliberately reproduced the workbook's "Must not replicate" bugs (futures P&L divided by
mark, LTD-2's wrong divisor, one shared maturity per pair). The user withdrew that
authorisation and asked for the sheet's column *shape* only, priced correctly. This
module replaces the replica: it never re-derives P&L, it calls
`engine.pnl.valuation.value_book` -- the one place the market-standard formulas live
(each FX leg at its own settle_date's FWD_OUTRIGHT, quote P&L converted at spot, futures
contracts x multiplier x (mark - fill), settled trades frozen from `realised_pnl`) -- once
per observation date (as_of, t-1bd, t-2bd) and reshapes the three results into one row per
trade. A missing value_book cell (no official mark, no spot, an unrealised settled trade)
stays missing here too: never zeroed, never defaulted.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from engine.pnl.calendar import _n_business_days_back, load_holidays
from engine.pnl.valuation import value_book

OUTPUT_COLUMNS = [
    "trade_id", "trade_date", "instrument_id", "status", "quantity_usd_notional", "tenor", "fill",
    "mark_t1", "mark_eod", "mark_t2", "pnl_t1", "pnl_eod", "pnl_t2", "reason",
]

ValueFn = Callable[[sqlite3.Connection, str], pd.DataFrame]


def _usd_leg_amounts(conn: sqlite3.Connection, trade_ids: List[str]) -> dict:
    """trade_id -> signed sum of trade_legs.amount where ccy = 'USD' (the USD leg of an
    FX trade, or the single USD NOTIONAL leg of a future). Empty dict for no trade_ids."""
    if not trade_ids:
        return {}
    placeholders = ",".join("?" * len(trade_ids))
    df = pd.read_sql_query(
        f"SELECT trade_id, SUM(amount) AS usd_amount FROM trade_legs "
        f"WHERE ccy = 'USD' AND trade_id IN ({placeholders}) GROUP BY trade_id",
        conn, params=trade_ids,
    )
    return dict(zip(df["trade_id"], df["usd_amount"]))


def _instrument_info(conn: sqlite3.Connection, instrument_ids: List[str]) -> pd.DataFrame:
    if not instrument_ids:
        return pd.DataFrame(columns=["instrument_id", "base_ccy", "quote_ccy", "multiplier"])
    placeholders = ",".join("?" * len(instrument_ids))
    return pd.read_sql_query(
        f"SELECT instrument_id, base_ccy, quote_ccy, multiplier FROM instruments "
        f"WHERE instrument_id IN ({placeholders})",
        conn, params=instrument_ids,
    )


def _quantity_usd_notional(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.Series:
    """CLAUDE.md "Display notional": USD notional per trade, sign = direction of the base
    currency. sign(quantity) * |USD leg amount| for pairs with a USD leg; raw base
    quantity for crosses (no USD leg exists to invent); contracts * multiplier * fill for
    futures (moved here unchanged from the retired xlsx_fx_replica module)."""
    if df.empty:
        return pd.Series(dtype=float)

    trade_ids = df["trade_id"].tolist()
    instrument_ids = df["instrument_id"].unique().tolist()
    usd_leg = _usd_leg_amounts(conn, trade_ids)
    instr = _instrument_info(conn, instrument_ids).set_index("instrument_id")

    base_ccy = df["instrument_id"].map(instr["base_ccy"])
    quote_ccy = df["instrument_id"].map(instr["quote_ccy"])
    multiplier = df["instrument_id"].map(instr["multiplier"])
    usd_leg_abs = df["trade_id"].map(usd_leg).abs()

    notional = np.sign(df["quantity"]) * usd_leg_abs
    cross = (base_ccy != "USD") & (quote_ccy != "USD")
    notional = notional.where(~cross, df["quantity"])
    future = df["product"] == "FUTURE"
    notional = notional.where(~future, df["quantity"] * multiplier * df["fill"])
    return notional


def _slim(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """trade_id, mark_<suffix>, pnl_<suffix> from one value_book result, for merging."""
    cols = {"mark": f"mark_{suffix}", "pnl_usd": f"pnl_{suffix}"}
    if df.empty:
        return pd.DataFrame(columns=["trade_id", *cols.values()])
    return df[["trade_id", "mark", "pnl_usd"]].rename(columns=cols)


FX_AND_FUTURE_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FUTURE")


def fx_blotter_rows(conn: sqlite3.Connection, as_of: str, value_fn: ValueFn = value_book,
                    products: tuple = FX_AND_FUTURE_PRODUCTS) -> pd.DataFrame:
    """One row per trade whose product is in `products` (default FX_SPOT/FX_FWD/FX_SWAP/
    FUTURE; the Blotter's FX sub-tab passes the three FX products so futures appear only
    on their own sub-tab), ordered by trade_date, instrument_id, trade_id. Prices the trade book three times --
    at `as_of`, one business day back and two business days back -- via `value_fn`
    (default `value_book`; the UI injects a wrapper that retries missing official marks
    against BNP_BVAL so a Bloomberg-less DB still prices) and lays the results out as
    t-1 / EOD / t-2 mark and P&L columns. A trade done today has no t-1/t-2 row -- those
    cells are None, not an error. All maths is value_book's; nothing here re-derives a
    formula.
    """
    d0 = dt.date.fromisoformat(as_of)
    # Same trading calendar as ledger._period_refs (config/holidays.txt): without it the
    # t-1 / t-2 columns landed on US holidays and disagreed with the header's Daily.
    holidays = load_holidays()
    t1_date = _n_business_days_back(d0, 1, holidays).isoformat()
    t2_date = _n_business_days_back(d0, 2, holidays).isoformat()

    eod = value_fn(conn, as_of)
    t1_df = value_fn(conn, t1_date)
    t2_df = value_fn(conn, t2_date)
    if not eod.empty:
        eod = eod[eod["product"].isin(products)]

    if eod.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    eod = eod.copy()
    eod["quantity_usd_notional"] = _quantity_usd_notional(conn, eod)
    out = eod.rename(columns={"mark": "mark_eod", "pnl_usd": "pnl_eod", "settle_date": "tenor"})

    out = out.merge(_slim(t1_df, "t1"), on="trade_id", how="left")
    out = out.merge(_slim(t2_df, "t2"), on="trade_id", how="left")

    out = out.sort_values(["trade_date", "instrument_id", "trade_id"]).reset_index(drop=True)

    for col in ("mark_eod", "mark_t1", "mark_t2", "pnl_eod", "pnl_t1", "pnl_t2"):
        # .where(..., None) on a float64 Series silently converts None back to NaN
        # (pandas dtype coercion), so cast to object first to keep a real Python None
        # for missing cells rather than a float NaN.
        out[col] = out[col].astype(object).where(pd.notna(out[col]), None)

    return out[OUTPUT_COLUMNS]
