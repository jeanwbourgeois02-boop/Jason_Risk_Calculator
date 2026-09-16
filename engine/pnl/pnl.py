"""Excel-compatible trade P&L (All FX trades columns H, I and K).

The workbook uses signed USD notional C, a shared WORKDAY(M1,5) FX mark
maturity, and C*(valuation-fill)/IF(RIGHT(name,3)="USD",fill,denominator_mark).
K deliberately uses the previous day's F mark as denominator, not J.
Missing inputs remain NaN. PB settlement marks are never substituted.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

OUTPUT_COLUMNS = [
    "trade_id", "instrument_id", "settle_date", "quantity", "fill", "mark", "spot",
    "pnl_quote", "pnl_usd", "source", "valuation_date", "workbook_quantity",
    "denominator", "usd_entry", "formula", "trade_date",
]


def workbook_valuation_date(as_of_date: str) -> str:
    """Literal WORKDAY(as_of,5), without an Excel holidays argument."""
    day = dt.date.fromisoformat(as_of_date)
    for _ in range(5):
        day += dt.timedelta(days=1)
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
    return day.isoformat()


def workbook_fx_pnl(instrument_id: str, usd_quantity: float, fill: float,
                    mark: float, denominator_mark: Optional[float] = None) -> float:
    """Literal row formula. Pass F as denominator_mark when evaluating K (T-2).

    Despite the historical name, Excel applies this same formula to futures.
    usd_quantity is workbook column C, NOT the PB's base-currency quantity.
    """
    denominator = fill if instrument_id.endswith("USD") else (
        mark if denominator_mark is None else denominator_mark)
    try:
        if not all(math.isfinite(float(v)) for v in (usd_quantity, fill, mark, denominator)):
            return float("nan")
        if fill <= 0 or mark <= 0 or denominator <= 0:
            return float("nan")
        return usd_quantity * (mark - fill) / denominator
    except (TypeError, ValueError):
        return float("nan")


def _marks_df(conn: sqlite3.Connection, as_of_date: str, mark_type: str,
              source: Optional[str]) -> pd.DataFrame:
    table = "marks_official" if source is None else "marks"
    extra = "" if source is None else " AND source = :source"
    return pd.read_sql_query(
        f"SELECT instrument_id, settle_date, value FROM {table} "
        "WHERE mark_type = :mark_type AND as_of_date = :as_of" + extra,
        conn, params={"mark_type": mark_type, "as_of": as_of_date, "source": source})


def ltd_per_trade(conn: sqlite3.Connection, as_of_date: str,
                  source: Optional[str] = None, strict: bool = True, *,
                  valuation_date: Optional[str] = None,
                  denominator_as_of: Optional[str] = None,
                  include_settled: bool = True) -> pd.DataFrame:
    """Workbook row arithmetic over recorded FX forwards and futures.

    All recorded trades dated through as_of are included, even after settlement,
    matching Excel's lack of a maturity filter. Historical columns must be evaluated
    using the CURRENT workbook's common valuation_date. denominator_as_of implements
    the K-column F-denominator quirk. Sources are explicit; None is marks_official.
    The legacy spot column is the formula conversion factor 1/denominator, NOT spot.
    """
    trades = pd.read_sql_query("""
        SELECT t.trade_id,t.instrument_id,t.trade_date,l.settle_date,t.quantity,
               t.price AS fill,t.product,i.base_ccy,i.quote_ccy,i.multiplier,
               (SELECT SUM(u.amount) FROM trade_legs u
                WHERE u.trade_id=t.trade_id AND u.ccy='USD') AS usd_entry
        FROM trades_official t JOIN instruments i ON i.instrument_id=t.instrument_id
        JOIN trade_legs l ON l.trade_id=t.trade_id AND l.leg_no=1
        WHERE t.product IN ('FX_FWD','FUTURE') AND t.trade_date<=:as_of
        """, conn, params={"as_of": as_of_date})
    if not include_settled:
        trades = trades[trades["settle_date"] > as_of_date]
    if trades.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    common_date = valuation_date or workbook_valuation_date(as_of_date)
    trades["valuation_date"] = np.where(trades["product"] == "FUTURE",
                                         trades["settle_date"], common_date)
    trades["workbook_quantity"] = np.sign(trades["quantity"]) * trades["usd_entry"].abs()
    cross = (trades['base_ccy'] != 'USD') & (trades['quote_ccy'] != 'USD')
    trades.loc[cross, 'workbook_quantity'] = trades.loc[cross, 'quantity']
    future = trades["product"] == "FUTURE"
    trades.loc[future, "workbook_quantity"] = (
        trades.loc[future, "quantity"] * trades.loc[future, "multiplier"] * trades.loc[future, "fill"])

    def lookup(observation_date):
        frames = [_marks_df(conn, observation_date, kind, source).assign(product=product)
                  for kind, product in [("FWD_OUTRIGHT", "FX_FWD"), ("FUTURE_PX", "FUTURE")]]
        frame = pd.concat(frames, ignore_index=True)
        return {(r.instrument_id, r.settle_date, r.product): r.value
                for r in frame.itertuples()}

    current = lookup(as_of_date)
    denominator_marks = current if denominator_as_of is None else lookup(denominator_as_of)
    keys = list(zip(trades["instrument_id"], trades["valuation_date"], trades["product"]))
    trades["mark"] = [current.get(k, float("nan")) for k in keys]
    trades["denominator"] = [denominator_marks.get(k, float("nan")) for k in keys]
    ends_usd = trades["instrument_id"].str.endswith("USD")
    trades.loc[ends_usd, "denominator"] = trades.loc[ends_usd, "fill"]
    trades.loc[trades["mark"] <= 0, "mark"] = float("nan")
    trades.loc[trades["denominator"] <= 0, "denominator"] = float("nan")
    trades["spot"] = 1.0 / trades["denominator"]
    trades["pnl_quote"] = trades["workbook_quantity"] * (trades["mark"] - trades["fill"])
    trades["pnl_usd"] = [workbook_fx_pnl(r.instrument_id, r.workbook_quantity,
        r.fill, r.mark, r.denominator) for r in trades.itertuples()]
    trades["source"] = "OFFICIAL" if source is None else source
    trades["formula"] = np.where(ends_usd, "C * (mark - fill) / fill",
                                 "C * (mark - fill) / denominator")
    out = trades[OUTPUT_COLUMNS].reset_index(drop=True)
    if strict:
        bad = out.loc[out["pnl_usd"].isna(), "trade_id"].tolist()
        if bad:
            raise ValueError(f"ltd_per_trade: missing workbook quantity or valuation rate for trade_id(s): {sorted(bad)}")
    return out
