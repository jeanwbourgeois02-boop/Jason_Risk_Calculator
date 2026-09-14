"""Per-pair and book-level P&L aggregation, plus daily/5d/MTD/YTD period P&L.

Built on top of ``engine.pnl.pnl.ltd_per_trade``; this module never recomputes a P&L
figure itself, it only sums the per-trade LTD figures that module already produces (per
CLAUDE.md "Must not replicate": no division by the mark or fill, no forward-outright USD
conversion -- both are handled once, in pnl.py).

See CLAUDE.md "P&L conventions":
  - "Display notional": USD notional per pair, signed by the direction of the base
    currency (the xlsx convention) -- ``aggregate_by_pair``.
  - "Net USD" / "Gross USD" -- ``book_totals``.
  - "Daily / Trading / 5d / MTD / YTD P&L" -- ``period_pnl``. The business-day calendar
    used here is a plain Mon-Fri weekday calendar; a real holiday calendar is a pending
    open item (not yet available to this module) and will change reference dates around
    holidays until it lands.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

from engine.pnl.pnl import ltd_per_trade

BY_PAIR_COLUMNS = ["instrument_id", "usd_notional", "ltd_usd", "n_trades"]


def aggregate_by_pair(per_trade: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    """Collapse a ``ltd_per_trade`` output to one row per instrument_id.

    usd_notional: signed USD notional in the xlsx display convention -- per trade, sign
    = sign of trades.quantity (direction of the base currency), magnitude = |USD leg
    amount| taken from trade_legs (the leg whose ccy = 'USD'), then summed over the
    trades present in ``per_trade`` for that instrument_id. This is independent of
    pnl_usd's own USD conversion (which uses spot, not the leg amount).
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
            usd_notional=("signed_usd", "sum"),
            ltd_usd=("pnl_usd", "sum"),
            n_trades=("trade_id", "count"),
        )
        .reset_index()
    )
    return out[BY_PAIR_COLUMNS]


def book_totals(by_pair: pd.DataFrame, conn: sqlite3.Connection) -> dict:
    """Net / gross USD across FX pairs (excluding gold), plus gold and futures totals.

    Net USD = sum over pairs of sign x usd_notional, sign +1 for USDXXX pairs (base_ccy
    == 'USD', long base = long USD), -1 otherwise. Gross USD = sum of |usd_notional| per
    pair. Both restricted to instruments.asset_class == 'FX' and base_ccy != 'XAU' (gold
    is reported separately, per CLAUDE.md: "Gold and equity futures are reported
    separately"). ``gold_usd`` and ``futures_usd`` are the usd_notional sums of the
    excluded groups (base_ccy == 'XAU', asset_class == 'FUTURE' respectively); 0.0 when
    that group is absent from ``by_pair``.
    """
    empty = {"net_usd": 0.0, "gross_usd": 0.0, "gold_usd": 0.0, "futures_usd": 0.0}
    if by_pair.empty:
        return empty

    ids = by_pair["instrument_id"].tolist()
    placeholders = ",".join("?" for _ in ids)
    instr = pd.read_sql_query(
        f"SELECT instrument_id, asset_class, base_ccy FROM instruments "
        f"WHERE instrument_id IN ({placeholders})",
        conn, params=ids,
    )
    df = by_pair.merge(instr, on="instrument_id", how="left")

    gold_mask = df["base_ccy"] == "XAU"
    futures_mask = (df["asset_class"] == "FUTURE") & ~gold_mask
    fx_mask = (df["asset_class"] == "FX") & ~gold_mask

    fx = df.loc[fx_mask]
    sign = np.where(fx["base_ccy"] == "USD", 1.0, -1.0)
    net_usd = float((sign * fx["usd_notional"]).sum())
    gross_usd = float(fx["usd_notional"].abs().sum())
    gold_usd = float(df.loc[gold_mask, "usd_notional"].sum())
    futures_usd = float(df.loc[futures_mask, "usd_notional"].sum())

    return {"net_usd": net_usd, "gross_usd": gross_usd, "gold_usd": gold_usd, "futures_usd": futures_usd}


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
    """Daily / 5d / MTD / YTD P&L (USD) as of as_of_date, per CLAUDE.md "P&L conventions".

    Each period figure = LTD(as_of_date) - LTD(reference date), where LTD is the sum of
    ltd_per_trade's pnl_usd column (strict=False, so missing marks surface as NaN rather
    than raising). Reference dates:
      daily -> previous business day
      d5    -> 5 business days back
      mtd   -> last business day of the previous month
      ytd   -> last business day of the previous year
    using a plain Monday-Friday calendar -- a real holiday calendar is a pending open
    item and is not applied here.

    Returns a dict with keys ltd, daily, d5, mtd, ytd (USD figures) plus the reference
    date used for each (as_of_date, daily_ref_date, d5_ref_date, mtd_ref_date,
    ytd_ref_date). A period is NaN if LTD(as_of_date) is NaN, or if LTD(reference date)
    is NaN (no marks / no open trades at all on that reference date).
    """
    as_of = dt.date.fromisoformat(as_of_date)
    ref_dates = {
        "daily": _prev_business_day(as_of),
        "d5": _n_business_days_back(as_of, 5),
        "mtd": _last_business_day_of_prev_month(as_of),
        "ytd": _last_business_day_of_prev_year(as_of),
    }

    ltd_asof = _ltd_total(conn, as_of_date, source)
    result: dict = {"as_of_date": as_of_date, "ltd": ltd_asof}

    for key, ref_date in ref_dates.items():
        ref_str = ref_date.isoformat()
        result[f"{key}_ref_date"] = ref_str
        if pd.isna(ltd_asof):
            result[key] = float("nan")
            continue
        ltd_ref = _ltd_total(conn, ref_str, source)
        result[key] = float("nan") if pd.isna(ltd_ref) else ltd_asof - ltd_ref

    return result
