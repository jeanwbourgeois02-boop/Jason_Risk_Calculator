"""Fallback pricing for the Blotter tab (user decision 2026-09-15, item 5).

The DB on the analyst's PC has no Bloomberg marks yet, only `BNP_BVAL` marks written
from the BNP file. `engine.pnl.valuation.value_book` reads `marks_official` by default
(`BNP_BVAL` is never official per CLAUDE.md), so every row would come back with
`pnl_usd = NaN` and a `reason` on a Bloomberg-less machine. Per the decision, rows must
always render (trade, fill, dates, status) and P&L should retry a missing mark with
`marks_source='BNP_BVAL'` rather than blank the whole tab; any row priced this way is
labelled so nobody mistakes a BNP file rate for an official Bloomberg mark.

This module does no valuation math of its own -- it only re-runs
`engine.pnl.valuation.value_book` a second time with `marks_source='BNP_BVAL'` for the
rows that came back NaN from the official pass, and merges the result in. All P&L
arithmetic stays inside `engine/pnl/`.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Dict, Optional, Tuple

import pandas as pd

from engine.pnl.aggregate import (
    _last_business_day_of_prev_month,
    _last_business_day_of_prev_year,
    _n_business_days_back,
    _prev_business_day,
    load_holidays,
)
from engine.pnl.valuation import value_book

FALLBACK_SOURCE = "BNP_BVAL"
FALLBACK_LABEL = "BNP file (not Bloomberg)"

_MERGE_COLS = ["mark", "mark_date", "mark_source", "spot", "spot_source", "pnl_local",
               "pnl_usd", "pnl_spot_usd", "pnl_carry_usd", "reason", "note"]


def priced_value_book(conn: sqlite3.Connection, as_of: str) -> Tuple[pd.DataFrame, int, int]:
    """`value_book(as_of)`, with any row still unpriced (non-empty `reason`) retried at
    `marks_source='BNP_BVAL'`. Returns `(df, n_fallback, n_total)`; `df` gets an extra
    boolean column `priced_from_bnp` and, for fallback rows, `mark_source` is relabelled
    `FALLBACK_LABEL` for display (the raw source string from `marks` is not lost -- it
    is folded into `note` instead). Rows are never dropped: a trade with no mark at all
    (official or BNP_BVAL) still renders with its `reason` intact and `pnl_usd = NaN`."""
    df = value_book(conn, as_of)
    n_total = len(df)
    if df.empty:
        df = df.copy()
        df["priced_from_bnp"] = pd.Series(dtype=bool)
        return df, 0, 0

    df = df.copy()
    df["priced_from_bnp"] = False
    missing = df.index[df["reason"] != ""]
    n_fallback = 0
    if len(missing):
        fb = value_book(conn, as_of, marks_source=FALLBACK_SOURCE)
        fb_by_id = fb.set_index("trade_id") if not fb.empty else fb
        for idx in missing:
            trade_id = df.at[idx, "trade_id"]
            if fb.empty or trade_id not in fb_by_id.index:
                continue
            frow = fb_by_id.loc[trade_id]
            if isinstance(frow, pd.DataFrame):  # duplicate trade_id, shouldn't happen; take first
                frow = frow.iloc[0]
            if frow["reason"] != "":
                continue  # still unpriced even from the BNP file; leave the original reason
            for col in _MERGE_COLS:
                df.at[idx, col] = frow[col]
            note = str(df.at[idx, "note"] or "")
            df.at[idx, "note"] = (note + " " if note else "") + f"priced from {FALLBACK_SOURCE} (not Bloomberg)."
            df.at[idx, "mark_source"] = FALLBACK_LABEL
            df.at[idx, "priced_from_bnp"] = True
            n_fallback += 1
    return df, n_fallback, n_total


def _sum_pnl(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    if df["pnl_usd"].isna().any():
        return float("nan")
    return float(df["pnl_usd"].sum())


def _priced_ltd(conn: sqlite3.Connection, as_of: str, products: Optional[tuple]) -> float:
    df, _, _ = priced_value_book(conn, as_of)
    if products is not None and not df.empty:
        df = df[df["product"].isin(products)]
    return _sum_pnl(df)


def scoped_period_pnl(conn: sqlite3.Connection, as_of: str, products: Optional[tuple] = None) -> Dict[str, dict]:
    """`engine.pnl.ledger.period_pnl`'s shape (LTD/Daily/5d/MTD/YTD/trading, each
    `{value, ref_date, available, reason}`), but computed from `priced_value_book` (so
    a BNP_BVAL-only book still prices) and optionally filtered to `products`."""
    holidays = load_holidays()
    d = dt.date.fromisoformat(as_of)
    refs = {
        "daily": _prev_business_day(d, holidays),
        "d5": _n_business_days_back(d, 5, holidays),
        "mtd": _last_business_day_of_prev_month(d, holidays),
        "ytd": _last_business_day_of_prev_year(d, holidays),
    }
    ltd_today = _priced_ltd(conn, as_of, products)
    out: Dict[str, dict] = {
        "ltd": {"value": ltd_today, "ref_date": as_of, "available": not _isnan(ltd_today),
                "reason": "" if not _isnan(ltd_today) else "a priced row is still missing every mark"},
    }
    for key, ref in refs.items():
        entry = {"value": float("nan"), "ref_date": ref.isoformat(), "available": False, "reason": ""}
        if _isnan(ltd_today):
            entry["reason"] = "today's LTD unavailable"
        else:
            ltd_ref = _priced_ltd(conn, ref.isoformat(), products)
            if _isnan(ltd_ref):
                entry["reason"] = f"LTD on {ref.isoformat()} unavailable"
            else:
                entry.update(value=ltd_today - ltd_ref, available=True)
        out[key] = entry

    df, _, _ = priced_value_book(conn, as_of)
    if products is not None and not df.empty:
        df = df[df["product"].isin(products)]
    trading_rows = df[df["trade_date"] == as_of] if not df.empty else df
    if trading_rows.empty:
        out["trading"] = {"value": 0.0, "ref_date": as_of, "available": True, "reason": ""}
    elif trading_rows["pnl_usd"].isna().any():
        out["trading"] = {"value": float("nan"), "ref_date": as_of, "available": False,
                           "reason": "a trade dated today has no mark from any source"}
    else:
        out["trading"] = {"value": float(trading_rows["pnl_usd"].sum()), "ref_date": as_of,
                           "available": True, "reason": ""}
    return out


def _isnan(value: float) -> bool:
    return value != value


# --------------------------------------------------------------------------- row-scoped strip
# 2026-09-15 coordinator addition: the P&L strip on every sub-tab must follow the rows
# currently visible after the DataTable's own header filtering (`derived_virtual_data`),
# not just the sub-tab's product scope -- recomputed for exactly that set of trade_ids,
# at `as_of` and at each reference date from `engine.pnl.ledger.period_reference_dates`.

PERIOD_ORDER = ("ltd", "daily", "previous_day", "d5", "mtd", "ytd", "trading")
PERIOD_TITLES = {"ltd": "LTD", "daily": "Daily", "previous_day": "Previous day",
                  "d5": "5d", "mtd": "MTD", "ytd": "YTD", "trading": "Trading"}


def _priced_sum_for_ids(conn: sqlite3.Connection, date: str, trade_ids) -> Tuple[float, list]:
    """Sum `pnl_usd` (fallback-priced) over `trade_ids` present in `date`'s book
    (a trade_id not yet on the book on `date`, e.g. traded later, is simply excluded,
    not an error). Returns `(value, bad_trade_ids)`; `value` is NaN iff at least one
    present row is still unpriced from every source, and `bad_trade_ids` names them."""
    if not trade_ids:
        return 0.0, []
    df, _, _ = priced_value_book(conn, date)
    if df.empty:
        return 0.0, []
    sel = df[df["trade_id"].isin(trade_ids)]
    if sel.empty:
        return 0.0, []
    bad = sel[sel["pnl_usd"].isna()]["trade_id"].tolist()
    if bad:
        return float("nan"), bad
    return float(sel["pnl_usd"].sum()), []


def _entry(value: float, ref_date: str, bad: list) -> dict:
    if _isnan(value):
        return {"value": value, "ref_date": ref_date, "available": False,
                "reason": f"no mark (any source) for {', '.join(sorted(set(bad)))}"}
    return {"value": value, "ref_date": ref_date, "available": True, "reason": ""}


def row_scoped_period_pnl(conn: sqlite3.Connection, as_of: str, trade_ids) -> Dict[str, dict]:
    """LTD/Daily/Previous day/5d/MTD/YTD/Trading over exactly `trade_ids`, each
    `{value, ref_date, available, reason}`, using the same business-day reference
    dates as `engine.pnl.ledger.period_pnl` (via `period_reference_dates`)."""
    from engine.pnl.ledger import period_reference_dates

    trade_ids = list(trade_ids)
    refs = period_reference_dates(as_of)
    ltd_today, bad_today = _priced_sum_for_ids(conn, as_of, trade_ids)
    out: Dict[str, dict] = {"ltd": _entry(ltd_today, as_of, bad_today)}

    for key in ("daily", "d5", "mtd", "ytd"):
        ref_date = refs[key]
        if _isnan(ltd_today):
            out[key] = {"value": float("nan"), "ref_date": ref_date, "available": False,
                        "reason": f"no mark (any source) for {', '.join(sorted(set(bad_today)))}"}
            continue
        v_ref, bad_ref = _priced_sum_for_ids(conn, ref_date, trade_ids)
        if _isnan(v_ref):
            out[key] = {"value": float("nan"), "ref_date": ref_date, "available": False,
                        "reason": f"no mark (any source) for {', '.join(sorted(set(bad_ref)))}"}
        else:
            out[key] = {"value": ltd_today - v_ref, "ref_date": ref_date, "available": True, "reason": ""}

    v1, bad1 = _priced_sum_for_ids(conn, refs["daily"], trade_ids)
    v2, bad2 = _priced_sum_for_ids(conn, refs["previous_day"], trade_ids)
    if _isnan(v1) or _isnan(v2):
        out["previous_day"] = {"value": float("nan"), "ref_date": refs["previous_day"], "available": False,
                                "reason": f"no mark (any source) for {', '.join(sorted(set(bad1) | set(bad2)))}"}
    else:
        out["previous_day"] = {"value": v1 - v2, "ref_date": refs["previous_day"], "available": True, "reason": ""}

    df, _, _ = priced_value_book(conn, as_of)
    sel = df[df["trade_id"].isin(trade_ids)] if not df.empty else df
    trading_rows = sel[sel["trade_date"] == as_of] if not sel.empty else sel
    if trading_rows.empty:
        out["trading"] = {"value": 0.0, "ref_date": as_of, "available": True, "reason": ""}
    elif trading_rows["pnl_usd"].isna().any():
        out["trading"] = {"value": float("nan"), "ref_date": as_of, "available": False,
                           "reason": "a selected trade dated today has no mark from any source"}
    else:
        out["trading"] = {"value": float(trading_rows["pnl_usd"].sum()), "ref_date": as_of,
                           "available": True, "reason": ""}
    return out
