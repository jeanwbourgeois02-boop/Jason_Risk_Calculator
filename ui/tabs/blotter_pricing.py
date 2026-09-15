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
import os
import sqlite3
from functools import lru_cache
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


def _db_cache_key(conn: sqlite3.Connection):
    """(file path, mtime) of the connection's main database, or None for an in-memory /
    unreadable one (then nothing is cached). The mtime invalidates the cache whenever an
    upload or a Bloomberg write touches the file."""
    try:
        for _seq, name, path in conn.execute("PRAGMA database_list"):
            if name == "main":
                if not path:
                    return None
                return (path, os.path.getmtime(path))
    except (sqlite3.Error, OSError):
        return None
    return None


@lru_cache(maxsize=256)
def _priced_value_book_cached(path: str, _mtime: float, as_of: str) -> Tuple[pd.DataFrame, int, int]:
    from ui.app import connect_readonly
    conn = connect_readonly(path)
    try:
        return _priced_value_book_uncached(conn, as_of)
    finally:
        conn.close()


def priced_value_book(conn: sqlite3.Connection, as_of: str) -> Tuple[pd.DataFrame, int, int]:
    """Memoised front for `_priced_value_book_uncached` (perf, 2026-09-15): the header,
    the blotter and its strips together revalue the same book at the same handful of
    dates a dozen times per page load. Keyed on (db path, db mtime, as_of) so a new
    upload or mark write invalidates it; the cached frame is returned as a copy so a
    caller's in-place edits never leak into another caller. Connections with no file
    (tests on ':memory:') bypass the cache."""
    key = _db_cache_key(conn)
    if key is None:
        return _priced_value_book_uncached(conn, as_of)
    df, n_fallback, n_total = _priced_value_book_cached(key[0], key[1], as_of)
    return df.copy(), n_fallback, n_total


def _priced_value_book_uncached(conn: sqlite3.Connection, as_of: str) -> Tuple[pd.DataFrame, int, int]:
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
    t1 = _prev_business_day(d, holidays)
    refs = {
        "daily": t1,
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

    # "Previous day" (coordinator addition 2026-09-15, header compaction): LTD at T-1
    # close minus LTD at T-2 close -- the prior day's own daily move, distinct from
    # "daily" above (today vs T-1). Reuses the same `_priced_ltd` fallback chain.
    t2 = _prev_business_day(t1, holidays)
    prev_entry = {"value": float("nan"), "ref_date": t2.isoformat(), "available": False, "reason": ""}
    ltd_t1 = _priced_ltd(conn, t1.isoformat(), products)
    if _isnan(ltd_t1):
        prev_entry["reason"] = f"LTD on {t1.isoformat()} unavailable"
    else:
        ltd_t2 = _priced_ltd(conn, t2.isoformat(), products)
        if _isnan(ltd_t2):
            prev_entry["reason"] = f"LTD on {t2.isoformat()} unavailable"
        else:
            prev_entry.update(value=ltd_t1 - ltd_t2, available=True)
    out["previous_day"] = prev_entry

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


def _trading_entry(rows: pd.DataFrame, ref_date: str) -> dict:
    if rows.empty:
        return {"value": 0.0, "ref_date": ref_date, "available": True, "reason": ""}
    if rows["pnl_usd"].isna().any():
        return {"value": float("nan"), "ref_date": ref_date, "available": False,
                "reason": "a trade dated this date has no mark from any source"}
    return {"value": float(rows["pnl_usd"].sum()), "ref_date": ref_date, "available": True, "reason": ""}


def _diff_entry(a: float, ref_a: str, bad_a: list, b: float, bad_b: list) -> dict:
    if _isnan(a) or _isnan(b):
        bad = sorted(set(bad_a) | set(bad_b))
        return {"value": float("nan"), "ref_date": ref_a, "available": False,
                "reason": f"no mark (any source) for {', '.join(bad)}" if bad
                          else "a reference close is unavailable"}
    return {"value": a - b, "ref_date": ref_a, "available": True, "reason": ""}


# --------------------------------------------------------------------------- headline strip
# 2026-09-15 coordinator addition: the Excel Portfolio header's exact card order, all
# row-scoped to the trade list's currently visible rows (post header-filter).
# Order regrouped 2026-09-15 (user decision): the continuous LTD/period series stays
# together (LTD, Daily, Previous day, 5d, MTD, YTD -- same grouping and "Previous day"
# name as the app header), then the activity group (Trades, Trading, Trading T-1) sits
# together, then the two raw LTD levels the Excel-parity "LTD-1 daily" figure is built
# from (LTD-1, LTD-2) close the row. Was: trading and trading_t1 separated by three
# other cards; "LTD-1 daily" named differently from the header's "Previous day" for the
# same LTD(t-1)-LTD(t-2) figure.
HEADLINE_ORDER = ("ltd", "daily", "ltd1_daily", "d5", "mtd", "ytd",
                   "trades", "trading", "trading_t1", "ltd1", "ltd2")
HEADLINE_TITLES = {
    "ltd": "LTD P&L", "daily": "Daily P&L", "ltd1_daily": "Previous day P&L",
    "d5": "5d", "mtd": "MTD", "ytd": "YTD",
    "trades": "Trades", "trading": "Trading P&L", "trading_t1": "Trading P&L T-1",
    "ltd1": "LTD-1 P&L", "ltd2": "LTD-2 P&L",
}


def row_scoped_headline(conn: sqlite3.Connection, as_of: str, trade_ids) -> Dict[str, dict]:
    """The Excel Portfolio header's card set (LTD, Daily, Trades, Trading, LTD-1 daily,
    LTD-1, LTD-2, Trading T-1, 5d, MTD, YTD), row-scoped to `trade_ids`. T-1/T-2 are
    `period_reference_dates`'s `daily`/`previous_day`; `trades` is a plain count, never
    unavailable."""
    from engine.pnl.ledger import period_reference_dates

    trade_ids = list(trade_ids)
    refs = period_reference_dates(as_of)
    t1, t2 = refs["daily"], refs["previous_day"]

    ltd_today, bad_today = _priced_sum_for_ids(conn, as_of, trade_ids)
    ltd_t1, bad_t1 = _priced_sum_for_ids(conn, t1, trade_ids)
    ltd_t2, bad_t2 = _priced_sum_for_ids(conn, t2, trade_ids)
    ltd_d5, bad_d5 = _priced_sum_for_ids(conn, refs["d5"], trade_ids)
    ltd_mtd, bad_mtd = _priced_sum_for_ids(conn, refs["mtd"], trade_ids)
    ltd_ytd, bad_ytd = _priced_sum_for_ids(conn, refs["ytd"], trade_ids)

    out: Dict[str, dict] = {
        "ltd": _entry(ltd_today, as_of, bad_today),
        "daily": _diff_entry(ltd_today, as_of, bad_today, ltd_t1, bad_t1),
        "trades": {"value": float(len(trade_ids)), "ref_date": as_of, "available": True,
                   "reason": "", "is_count": True},
        "ltd1_daily": _diff_entry(ltd_t1, t1, bad_t1, ltd_t2, bad_t2),
        "ltd1": _entry(ltd_t1, t1, bad_t1),
        "ltd2": _entry(ltd_t2, t2, bad_t2),
        "d5": _diff_entry(ltd_today, as_of, bad_today, ltd_d5, bad_d5),
        "mtd": _diff_entry(ltd_today, as_of, bad_today, ltd_mtd, bad_mtd),
        "ytd": _diff_entry(ltd_today, as_of, bad_today, ltd_ytd, bad_ytd),
    }

    df, _, _ = priced_value_book(conn, as_of)
    sel = df[df["trade_id"].isin(trade_ids)] if not df.empty else df
    trading_rows = sel[sel["trade_date"] == as_of] if not sel.empty else sel
    out["trading"] = _trading_entry(trading_rows, as_of)

    df_t1, _, _ = priced_value_book(conn, t1)
    sel_t1 = df_t1[df_t1["trade_id"].isin(trade_ids)] if not df_t1.empty else df_t1
    trading_t1_rows = sel_t1[sel_t1["trade_date"] == t1] if not sel_t1.empty else sel_t1
    out["trading_t1"] = _trading_entry(trading_t1_rows, t1)
    return out


def _official_mark(conn: sqlite3.Connection, instrument_id: str, settle_date: str,
                    mark_type: str, as_of: str) -> Optional[float]:
    row = conn.execute(
        "SELECT value FROM marks_official WHERE instrument_id = :i AND settle_date = :s "
        "AND mark_type = :m AND as_of_date = :d",
        {"i": instrument_id, "s": settle_date, "m": mark_type, "d": as_of},
    ).fetchone()
    return None if row is None else float(row[0])


def add_row_display_fields(conn: sqlite3.Connection, df: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Add the Excel header row's per-pair columns to a `priced_value_book` frame:
    `side` (Buy/Sell of the base currency), `notional_usd` (signed USD notional, base
    direction -- CLAUDE.md "Display notional"), `t1_rate` (FWD_OUTRIGHT for the row's
    settle date on the T-1 close, NaN if absent or not an FX product)."""
    from engine.pnl.ledger import period_reference_dates
    from engine.pnl.valuation import usd_per_quote

    if df.empty:
        df = df.copy()
        for col in ("side", "notional_usd", "t1_rate"):
            df[col] = pd.Series(dtype=object)
        return df

    df = df.copy()
    df["side"] = df["quantity"].map(lambda q: "Buy" if q >= 0 else "Sell")

    base_ccy = {}
    for instrument_id in df["instrument_id"].unique():
        row = conn.execute("SELECT base_ccy FROM instruments WHERE instrument_id = ?",
                            (instrument_id,)).fetchone()
        base_ccy[instrument_id] = row[0] if row else None

    notional = []
    for r in df.itertuples(index=False):
        ccy = base_ccy.get(r.instrument_id)
        if not ccy:
            notional.append(float("nan"))
        elif ccy == "USD":
            notional.append(float(r.quantity))
        else:
            s, pair, _src = usd_per_quote(conn, ccy, as_of)
            notional.append(float(r.quantity) * s if s == s else float("nan"))
    df["notional_usd"] = notional

    t1 = period_reference_dates(as_of)["daily"]
    t1_rates = []
    for r in df.itertuples(index=False):
        if r.product not in FX_PRODUCTS_FOR_T1_RATE:
            t1_rates.append(float("nan"))
            continue
        val = _official_mark(conn, r.instrument_id, r.settle_date, "FWD_OUTRIGHT", t1)
        t1_rates.append(val if val is not None else float("nan"))
    df["t1_rate"] = t1_rates
    return df


FX_PRODUCTS_FOR_T1_RATE = ("FX_SPOT", "FX_FWD", "FX_SWAP")


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
