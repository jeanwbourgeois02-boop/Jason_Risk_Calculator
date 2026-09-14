"""Aggregate Excel row P&L, preserving missing values and its daily formulas."""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

from engine.pnl.pnl import ltd_per_trade, workbook_valuation_date

BY_PAIR_COLUMNS = ["instrument_id", "usd_notional", "ltd_usd", "n_trades"]


def aggregate_by_pair(per_trade: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    """Collapse a ``ltd_per_trade`` output to one row per instrument_id.

    usd_notional: signed USD notional in the xlsx display convention -- per trade, sign
    = sign of trades.quantity (direction of the base currency), magnitude = |USD leg
    amount| taken from trade_legs (the leg whose ccy = 'USD'), then summed over the
    trades present in ``per_trade`` for that instrument_id. This is independent of
    pnl_usd's workbook denominator.
    ltd_usd: sum of pnl_usd. n_trades: row count. Both per instrument_id.
    """
    if per_trade.empty:
        return pd.DataFrame(columns=BY_PAIR_COLUMNS)

    trade_ids = per_trade["trade_id"].tolist()
    placeholders = ",".join("?" for _ in trade_ids)
    legs = pd.read_sql_query(
        f"SELECT trade_id, amount FROM trade_legs "
        f"WHERE ccy = 'USD' AND trade_id IN ({placeholders})",
        conn, params=trade_ids,
    )
    usd_leg_abs = legs.groupby("trade_id")["amount"].sum().abs().rename("usd_leg_abs")

    df = per_trade.merge(usd_leg_abs, on="trade_id", how="left")
    df["signed_usd"] = np.sign(df["quantity"]) * df["usd_leg_abs"]

    out = (
        df.groupby("instrument_id")
        .agg(
            usd_notional=("signed_usd", lambda values: values.sum(skipna=False)),
            ltd_usd=("pnl_usd", lambda values: values.sum(skipna=False)),
            n_trades=("trade_id", "count"),
        )
        .reset_index()
    )
    return out[BY_PAIR_COLUMNS]


def book_totals(by_pair: pd.DataFrame, conn: sqlite3.Connection) -> dict:
    """Portfolio B3/B4 require its manual option adjustments and row-specific inputs.

    Do not substitute generic FX-only sums for the workbook's Net/Gross formulas.
    """
    return {"net_usd": float("nan"), "gross_usd": float("nan"),
            "gold_usd": float("nan"), "futures_usd": float("nan"),
            "status": "Portfolio totals unavailable: the workbook's manual option-delta adjustments, IRS/options inputs and complete futures fills are not loaded. No generic total is substituted."}


# --------------------------------------------------------------------- business calendar
# Plain Monday-Friday weekday calendar. No holiday calendar is wired up yet (open item);
# reference dates below will be wrong around holidays until one lands.

def _prev_business_day(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _n_business_days_back(d: dt.date, n: int) -> dt.date:
    for _ in range(n):
        d = _prev_business_day(d)
    return d


def _last_business_day_of_prev_month(d: dt.date) -> dt.date:
    last_day_prev_month = d.replace(day=1) - dt.timedelta(days=1)
    while last_day_prev_month.weekday() >= 5:
        last_day_prev_month -= dt.timedelta(days=1)
    return last_day_prev_month


def _last_business_day_of_prev_year(d: dt.date) -> dt.date:
    last_day = dt.date(d.year - 1, 12, 31)
    while last_day.weekday() >= 5:
        last_day -= dt.timedelta(days=1)
    return last_day


def _ltd_total(conn: sqlite3.Connection, date_str: str, source: Optional[str]) -> float:
    """Sum of pnl_usd from ltd_per_trade(strict=False) on date_str; NaN if there are no
    open trades on that date, or if any trade's pnl_usd is NaN (a missing outright or
    a missing quote-to-USD conversion)."""
    out = ltd_per_trade(conn, date_str, source=source, strict=False)
    if out.empty:
        return float("nan")
    return float(out["pnl_usd"].sum(skipna=False))


def period_pnl(conn: sqlite3.Connection, as_of_date: str, source: Optional[str] = None) -> dict:
    """All FX trades M2:M8, including the K-column historical denominator.

    5d/MTD/YTD are unavailable: this workbook has no equivalent formulas for them.
    Every historical mark uses today's shared forward maturity, as the sheet does.
    """
    as_of = dt.date.fromisoformat(as_of_date)
    previous = _prev_business_day(as_of).isoformat()
    previous2 = _prev_business_day(dt.date.fromisoformat(previous)).isoformat()
    maturity = workbook_valuation_date(as_of_date)
    today = ltd_per_trade(conn, as_of_date, source, strict=False, valuation_date=maturity)
    yesterday = ltd_per_trade(conn, previous, source, strict=False, valuation_date=maturity)
    before = ltd_per_trade(conn, previous2, source, strict=False, valuation_date=maturity,
                           denominator_as_of=previous)
    # N17=O17: the workbook reuses today's BRL rate in yesterday's column.
    brl_current = today[today["instrument_id"] == "USDBRL"].set_index("trade_id")
    from engine.pnl.pnl import workbook_fx_pnl
    for frame, is_before in [(yesterday, False), (before, True)]:
        for idx, row in frame[frame["instrument_id"] == "USDBRL"].iterrows():
            rate = brl_current.loc[row["trade_id"], "mark"] if row["trade_id"] in brl_current.index else float("nan")
            frame.at[idx, "pnl_usd"] = workbook_fx_pnl(row["instrument_id"], row["workbook_quantity"],
                row["fill"], row["mark"] if is_before else rate, rate)

    def total(frame):
        return float(frame["pnl_usd"].sum(skipna=False))

    def trading(frame, date):
        return float(frame.loc[frame["trade_date"] == date, "pnl_usd"].sum(skipna=False))

    ltd, ltd1, ltd2 = total(today), total(yesterday), total(before)
    if today.empty:
        ltd = float('nan')
    return {"as_of_date": as_of_date, "valuation_date": maturity, "ltd": ltd,
            "daily_ref_date": previous, "daily": ltd - ltd1,
            "previous_daily": ltd1 - ltd2, "previous2_ref_date": previous2,
            "trading": trading(today, as_of_date), "trading_previous": trading(yesterday, previous),
            "trading_previous2": trading(before, previous2),
            "d5": float("nan"), "mtd": float("nan"), "ytd": float("nan"),
            "d5_ref_date": _n_business_days_back(as_of, 5).isoformat(),
            "mtd_ref_date": _last_business_day_of_prev_month(as_of).isoformat(),
            "ytd_ref_date": _last_business_day_of_prev_year(as_of).isoformat()}
