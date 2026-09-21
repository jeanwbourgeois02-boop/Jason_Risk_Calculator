"""Pricing helpers for the Blotter tab: a memoised front for
`engine.pnl.valuation.value_book` (`priced_value_book`) plus the row-scoped P&L
strip/headline aggregation every sub-tab uses (`row_scoped_headline`,
`row_scoped_period_pnl`, `asset_class_pnl_rows`'s underlying pieces). This module does
no valuation math of its own -- all P&L arithmetic stays inside `engine/pnl/`; rows
with no official mark (`reason` non-empty) always still render (trade, fill, dates,
status) with blank P&L, per CLAUDE.md's "Missing values stay missing everywhere".

**No BNP_BVAL fallback pricing (removed 2026-09-17, user decision "no bnp fall back",
verbatim).** From 2026-09-15 to 2026-09-17 this module retried a row with no official
mark against `marks_source='BNP_BVAL'` so a Bloomberg-less DB still showed a P&L number,
labelled as coming from the BNP file rather than Bloomberg. The user reversed that
decision: CLAUDE.md's "Official marks" table already says `BNP_BVAL` is reconciliation
-only and never feeds P&L, so the retry was a standing violation of that rule, not a
sanctioned exception to it -- it is gone, not merely optimised (a same-day earlier
change in this session had vectorised the fallback merge for performance; that whole
code path is now deleted rather than kept faster). `priced_value_book` still returns
its original `(df, n_fallback, n_total)` three-tuple so callers outside this lane (e.g.
`ui/tabs/header.py`) do not break -- `n_fallback` is now always `0`; the `df` no longer
carries a `priced_from_bnp` column at all, since nothing reads it any more once the
Blotter/FX badge text that used to display it was also removed (see
`ui/tabs/blotter.py` and `ui/tabs/blotter_fx.py`). `engine/pnl/valuation.py`'s own
`marks_source` parameter on `value_book` has since been removed outright (2026-09-17,
same pass) -- this module already only ever called it with the default (official-only),
so nothing here needed to change when that parameter disappeared.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
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


log = logging.getLogger(__name__)

_SNAPSHOT = threading.local()
# A render slower than this says so in the terminal (see `pricing_snapshot`).
SLOW_RENDER_SECONDS = 3.0


@contextmanager
def pricing_snapshot(conn: sqlite3.Connection, label: str = ""):
    """Pin `priced_value_book`'s cache key for the duration of ONE render (2026-09-18).

    `_db_cache_key` re-reads the file's mtime on every call, so a write landing in the
    middle of a render (the Bloomberg pull, the backfill, the pull an upload triggers)
    changed the key part-way through and every later call missed the cache: measured on
    a 772-trade book, one Total book render went from 6 full `value_book` runs to 76
    (1.5 s to 11.7 s), FX from 6 to 19. Inside this block the key is read once, so a
    render costs one `value_book` per distinct date whatever is being written, and the
    strip and the table of that render are priced off the same view of the marks rather
    than a mix of before and after the write.

    Scoped to the calling thread (a Dash callback runs start to finish on its request's
    thread) and released on exit, so the NEXT render always takes a fresh key: an upload
    is never served from a snapshot taken before it. Re-entrant -- an inner block keeps
    the outer key. Callers that never enter one behave exactly as before.

    With a `label`, a render that takes `SLOW_RENDER_SECONDS` or more logs one WARNING
    naming the view, how long it took and how many full `value_book` runs it cost. A
    view that ERRORS already prints its traceback in the terminal; a view that is merely
    slow printed nothing at all, so "the tab did not load" on the Bloomberg PC left no
    trace to diagnose from (user, 2026-09-18). Logging only: nothing about the render
    itself changes."""
    if getattr(_SNAPSHOT, "key", None) is not None:
        yield
        return
    _SNAPSHOT.key = (_db_cache_key(conn),)  # 1-tuple: a None key (in-memory db) is still "pinned"
    _SNAPSHOT.repricings = 0
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        _SNAPSHOT.key = None
        if label and elapsed >= SLOW_RENDER_SECONDS:
            log.warning("slow render: %s took %.1fs (%d full re-pricings of the book)",
                        label, elapsed, getattr(_SNAPSHOT, "repricings", 0))


def _render_cache_key(conn: sqlite3.Connection):
    pinned = getattr(_SNAPSHOT, "key", None)
    return pinned[0] if pinned is not None else _db_cache_key(conn)


@lru_cache(maxsize=256)
def _priced_value_book_cached(path: str, _mtime: float, as_of: str) -> Tuple[pd.DataFrame, int, int]:
    from ui.app import connect_readonly
    # Only reached on a cache miss, on the caller's own thread: counted for the slow-render log.
    _SNAPSHOT.repricings = getattr(_SNAPSHOT, "repricings", 0) + 1
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
    key = _render_cache_key(conn)  # pinned for the whole render inside `pricing_snapshot`
    if key is None:
        return _priced_value_book_uncached(conn, as_of)
    df, n_fallback, n_total = _priced_value_book_cached(key[0], key[1], as_of)
    return df.copy(), n_fallback, n_total


def _priced_value_book_uncached(conn: sqlite3.Connection, as_of: str) -> Tuple[pd.DataFrame, int, int]:
    """`value_book(as_of)` unchanged -- no second pass, no fallback merge (removed
    2026-09-17, module docstring). Returns `(df, 0, n_total)`: the `0` and the
    3-tuple shape are kept only so callers outside this lane (`ui/tabs/header.py`)
    that still unpack `(df, n_fallback, n_total)` do not break. A row with no
    official mark keeps its `reason` and `pnl_usd = NaN` exactly as `value_book`
    returns it; this function does not touch it further."""
    df = value_book(conn, as_of)
    return df, 0, len(df)


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
    `{value, ref_date, available, reason}`), but computed from `priced_value_book`
    (a thin memoised front for `value_book`, see module docstring) and optionally
    filtered to `products`."""
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
#
# 2026-09-17 (live-Bloomberg-PC follow-up, matches ui/tabs/header.py's own same-day fix
# to its headline cards -- see that module's docstring, "Partial pricing"): spots/most
# forwards/swaps now price on the live PC, but a handful of visible rows never will (an
# option with no strike typed in yet, one same-day forward, one future) -- and the old
# `_priced_sum_for_ids`/`_entry`/`_diff_entry`/`_trading_entry` chain below "poisoned" a
# whole period to NaN the instant ANY ONE visible row was unpriced, so a strip with even
# one bad row went blank even though every other row priced fine. Replaced with
# `_priced_single_scoped`/`_priced_diff_scoped` (this module's own version of
# `header.py`'s `_priced_single`/`_priced_diff` -- duplicated rather than imported,
# since `header.py` is a different lane's file and explicitly says so): a period figure
# now sums PRICED rows only within the CURRENT ROW-SCOPED set (`trade_ids`, i.e. this
# sub-tab's visible rows after filtering -- not the whole book, unlike header.py's
# whole-book cards) and carries a visible `excluded_summary` ("excludes N of M trades
# unpriced") plus a `excluded_detail` breakdown tooltip; a card only goes fully
# unavailable ("n/a") when EVERY row in the set is unpriced. A period DIFFERENCE
# additionally excludes a trade priced on one date but not the other (never credits it
# with a fake one-sided jump); a trade new since the reference date still contributes
# normally. Per-trade arithmetic (`engine/pnl/valuation.py`) is untouched -- this is
# aggregation-only, exactly like header.py's equivalent change.

PERIOD_ORDER = ("ltd", "daily", "previous_day", "d5", "mtd", "ytd", "trading")
PERIOD_TITLES = {"ltd": "LTD", "daily": "Daily", "previous_day": "Previous day",
                  "d5": "5d", "mtd": "MTD", "ytd": "YTD", "trading": "Trading"}

_MISSING_TAG_RE = re.compile(r"no (\S+) mark")

_PRODUCT_LABELS = {
    "FX_SPOT": "spot", "FX_FWD": "forward", "FX_SWAP": "swap",
    "FUTURE": "future", "IRS": "swap (IRS)", "FX_OPTION": "option",
}


BAD_VALUE_TAG = "stored value is not a number"
# How many full `value_book` reasons a breakdown spells out for bad-stored-value rows.
_BAD_VALUE_EXAMPLES = 3


def is_bad_value_reason(reason) -> bool:
    """True for the reasons `engine.pnl.valuation._guarded_row` writes when a trade could
    not be valued because of what is STORED for it (text in a REAL column, a malformed
    date) rather than because a mark is missing -- "trade <id>: trades.price is not a
    number ('24-Jul')" / "trade <id>: could not be valued (...)". These name the trade id,
    the table.column and the offending value, so the views repeat them in full."""
    return isinstance(reason, str) and ("is not a number" in reason or "could not be valued" in reason)


def _reason_tag(reason: str) -> str:
    """Short tag extracted from one of `value_book`'s own `reason` strings, for the
    unpriced-trade breakdown tooltip -- e.g. "no PREMIUM" from "no PREMIUM mark for ...
    expiry ... on ...". Never invents a reason, only summarises the one
    `engine.pnl.valuation` already gave. Mirrors `ui/tabs/header.py::_reason_tag`
    exactly (duplicated, not imported -- see the section comment above)."""
    if not reason:
        return "unpriced"
    if "cannot be frozen" in reason:
        return "no historical mark at settlement"
    if is_bad_value_reason(reason):
        return BAD_VALUE_TAG
    if "SPOT for USD conversion" in reason:
        return "no SPOT (USD conversion)"
    m = _MISSING_TAG_RE.search(reason)
    if m:
        return f"no {m.group(1)}"
    return "unpriced"


def _product_label(product: str, count: int) -> str:
    label = _PRODUCT_LABELS.get(product, str(product).lower() or "trade")
    return label if count == 1 else f"{label}s"


def _unpriced_breakdown(unpriced: pd.DataFrame) -> str:
    """"5 options: no PREMIUM; 1 forward: no FWD_OUTRIGHT" -- grouped by (product, a
    short reason tag), most-affected group first. "" for no unpriced rows. Mirrors
    `ui/tabs/header.py::_unpriced_breakdown` exactly."""
    if unpriced.empty:
        return ""
    tags = unpriced["reason"].map(_reason_tag)
    groups = unpriced.groupby([unpriced["product"], tags]).size().sort_values(ascending=False)
    out = "; ".join(f"{count} {_product_label(product, count)}: {tag}"
                     for (product, tag), count in groups.items())
    return out + bad_value_detail(unpriced)


def bad_value_detail(unpriced: pd.DataFrame) -> str:
    """" -- trade 928825333: trades.price is not a number ('24-Jul')" for the rows of
    `unpriced` that are unpriced because of a bad STORED value (`is_bad_value_reason`):
    `value_book`'s own reason in full (trade id, table.column, offending value), at most
    `_BAD_VALUE_EXAMPLES` of them. "" when there are none. A missing mark is fixed by a
    Bloomberg pull; this is fixed in the data, so the breakdown says exactly where."""
    if unpriced.empty:
        return ""
    reasons = [r for r in unpriced["reason"].tolist() if is_bad_value_reason(r)]
    if not reasons:
        return ""
    more = f" (+{len(reasons) - _BAD_VALUE_EXAMPLES} more)" if len(reasons) > _BAD_VALUE_EXAMPLES else ""
    return " -- " + "; ".join(reasons[:_BAD_VALUE_EXAMPLES]) + more


def bad_value_note(unpriced: pd.DataFrame) -> str:
    """" (1 with a stored value that is not a number)" -- appended to a card's VISIBLE
    "excludes N of M trades unpriced" caption when some of those N are unpriced because
    of a bad stored value; "" otherwise, so every other caption reads exactly as before.
    Visible, not hover-only: a data error the user must fix should not need a mouse-over."""
    if unpriced.empty:
        return ""
    n = sum(1 for r in unpriced["reason"].tolist() if is_bad_value_reason(r))
    return f" ({n} with a {BAD_VALUE_TAG})" if n else ""


# Every REAL column the P&L, the ladder or a Blotter table multiplies, with the key that
# names a row to the user. `bad_stored_values` scans these for anything that is not a number.
_NUMERIC_COLUMNS = (
    ("trades", ("trade_id",), ("quantity", "price")),
    ("trade_legs", ("trade_id", "leg_no"), ("amount", "rate")),
    ("instruments", ("instrument_id",), ("multiplier",)),
    ("instrument_options", ("instrument_id",), ("strike", "barrier_level")),
    ("realised_pnl", ("trade_id",), ("local_amount", "usd_entry_amount", "spot_usd_per_local", "pnl_usd")),
    ("marks", ("as_of_date", "instrument_id", "settle_date", "mark_type", "source"), ("value",)),
)
_BAD_VALUE_FIX = {
    "trades": "re-upload the blotter (or delete and re-book the manual trade)",
    "trade_legs": "re-upload the blotter (or delete and re-book the manual trade)",
    "instruments": "re-upload the blotter",
    "instrument_options": "re-enter the option's terms under Manual entry > Option terms",
    "realised_pnl": "rebuilt automatically by the next Bloomberg pull; the trade is valued at its last "
                    "official mark meanwhile",
    "marks": "re-run the Bloomberg pull or backfill for that date (or re-enter the manual mark)",
}


def bad_stored_values(conn: sqlite3.Connection, examples: int = 3) -> list:
    """Stored figures that are not numbers, one entry per table.column that has any:
    `{table, column, count, examples: [(key text, value), ...], fix}`. SQLite keeps text it
    cannot convert as TEXT even in a REAL column, and `float()` on it is what produced the
    bare "could not convert string to float: '...'" error cards (2026-09-18) -- which said
    neither which trade nor which column. This names both, straight from the database, so
    it also covers the views that never go through `value_book` (the ladder's leg amounts,
    the Options table's strikes). Read-only; [] when everything is a number, when a table
    does not exist on this database, or when the scan itself fails (it never raises)."""
    out = []
    for table, keys, columns in _NUMERIC_COLUMNS:
        for column in columns:
            where = f'typeof("{column}") NOT IN (\'real\', \'integer\')'
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}').fetchone()[0]
                if not count:
                    continue
                rows = conn.execute(f'SELECT {", ".join(keys)}, "{column}" FROM "{table}" WHERE {where} LIMIT ?',
                                     (examples,)).fetchall()
            except sqlite3.Error:
                continue
            shown = [(" ".join(f"{k} {v}" for k, v in zip(keys, row[:-1])), row[-1]) for row in rows]
            out.append({"table": table, "column": column, "count": count, "examples": shown,
                        "fix": _BAD_VALUE_FIX.get(table, "")})
    return out


def describe_bad_stored_values(conn: sqlite3.Connection) -> str:
    """One sentence per entry of `bad_stored_values`, "" when there are none:
    "trades.price: 1 value that is not a number -- '24-Jul' (trade_id 928825333); to fix:
    re-upload the blotter ..."."""
    parts = []
    for item in bad_stored_values(conn):
        n = item["count"]
        shown = "; ".join(f"{value!r} ({key})" for key, value in item["examples"])
        more = f", +{n - len(item['examples'])} more" if n > len(item["examples"]) else ""
        fix = f"; to fix: {item['fix']}" if item["fix"] else ""
        parts.append(f"{item['table']}.{item['column']}: "
                     f"{f'{n} values that are not numbers' if n != 1 else '1 value that is not a number'}"
                     f" -- {shown}{more}{fix}")
    return ". ".join(parts)


def _nothing_priced_reason(unpriced: pd.DataFrame, day: str) -> str:
    """"nothing priced on 2026-08-31: 742 forwards: no FWD_OUTRIGHT; 11 futures: no
    FUTURE_PX (e.g. 896328363, 896328364, ...)" -- the per-product breakdown first, then
    at most five trade ids. Replaces a plain dump of every unpriced trade id (2026-09-18:
    768 ids in one tooltip on the reference data, which said nothing about WHY)."""
    ids = sorted(set(unpriced["trade_id"])) if not unpriced.empty else []
    if not ids:
        return f"nothing priced on {day}"
    sample = ", ".join(ids[:5]) + (", ..." if len(ids) > 5 else "")
    return f"nothing priced on {day}: {_unpriced_breakdown(unpriced)} (e.g. {sample})"


def _reference_missing_reason(b_unpriced: pd.DataFrame, ref_day: str, n_blocked: int, n_open: int,
                              conn: Optional[sqlite3.Connection] = None) -> str:
    """Why a period difference cannot be formed: most trades open on `ref_day` are
    unpriced there (mirrors `ui/tabs/header.py::_reference_reason`, row-scoped). Ends with
    what the backfill itself reports about that date (the header's
    `past_close_explanation`, read from the status file beside `conn`'s database), since a
    past day's close only ever arrives that way and no screen has a control that runs it."""
    from ui.tabs.header import backfill_status, past_close_explanation
    breakdown = _unpriced_breakdown(b_unpriced)
    return (f"needs the {ref_day} close: {n_blocked} of {n_open} trades open that day have no official mark there"
            + (f" ({breakdown})" if breakdown else "") + " -- " + past_close_explanation(backfill_status(conn), ref_day))


def _scoped_frame(conn: sqlite3.Connection, date: str, trade_ids) -> pd.DataFrame:
    """`priced_value_book(conn, date)` filtered to `trade_ids` present on that date's
    book (a trade_id not yet on the book on `date`, e.g. traded later, is simply
    excluded, not an error)."""
    if not trade_ids:
        df, _, _ = priced_value_book(conn, date)
        return df.iloc[0:0]
    df, _, _ = priced_value_book(conn, date)
    if df.empty:
        return df
    return df[df["trade_id"].isin(trade_ids)]


def _priced_single_from_df(df: pd.DataFrame, ref_date: str) -> dict:
    """{value, ref_date, available, reason, excluded_summary, excluded_detail} for ONE
    already-scoped date's rows: the sum over PRICED rows only -- CLAUDE.md "Missing
    values stay missing": an unpriced row contributes nothing, it is never zeroed or
    invented. All rows unpriced (non-empty set) is a single "n/a" + reason card; an
    empty set is 0.0/available (a book with nothing due yet is 0, not Unavailable).
    Mirrors `ui/tabs/header.py::_priced_single`'s logic, row-scoped instead of
    whole-book."""
    total = len(df)
    if total == 0:
        return {"value": 0.0, "ref_date": ref_date, "available": True, "reason": "",
                "excluded_summary": "", "excluded_detail": ""}
    priced = df[df["reason"] == ""]
    unpriced = df[df["reason"] != ""]
    if priced.empty:
        return {"value": float("nan"), "ref_date": ref_date, "available": False,
                "reason": _nothing_priced_reason(unpriced, ref_date),
                "excluded_summary": "", "excluded_detail": ""}
    value = float(priced["pnl_usd"].sum())
    if unpriced.empty:
        return {"value": value, "ref_date": ref_date, "available": True, "reason": "",
                "excluded_summary": "", "excluded_detail": ""}
    n = len(unpriced)
    return {"value": value, "ref_date": ref_date, "available": True, "reason": "",
            "excluded_summary": f"excludes {n} of {total} trades unpriced" + bad_value_note(unpriced),
            "excluded_detail": _unpriced_breakdown(unpriced)}


def _priced_single_scoped(conn: sqlite3.Connection, date: str, trade_ids, ref_date: str) -> dict:
    return _priced_single_from_df(_scoped_frame(conn, date, trade_ids), ref_date)


def _priced_diff_scoped(conn: sqlite3.Connection, date_a: str, date_b: str, trade_ids,
                         ref_date: str, note_label: str) -> dict:
    """{value, ref_date, available, reason, excluded_summary, excluded_detail} for
    LTD(date_a) - LTD(date_b), both row-scoped to `trade_ids`. Mirrors
    `ui/tabs/header.py::_priced_diff`'s "nothing invented, nothing faked" rule exactly:
      - a trade only present in `date_a`'s scoped book (traded after `date_b`)
        contributes its full value when priced -- a new trade entering the set, not a
        pricing artefact, so it is not "excluded".
      - a trade present in both scoped books contributes normally only when priced in
        BOTH.
      - a trade present in both but priced in only one of the two is EXCLUDED from the
        diff outright (contributes nothing) rather than credited with its full
        one-sided value, which would fake a jump the size of its whole LTD on whichever
        single day a mark happened to appear or vanish.
      - (2026-09-18) when the blocked trades outnumber the trades priced at both ends --
        most of what was open on `date_b` is unpriced there -- the difference is anchored
        by a minority of the set: the figure is unavailable, with a reason naming
        `date_b`, the count and what it is missing (`_reference_missing_reason`), never a
        "$0, excludes 771 of 772" that is really nothing. Same rule as
        `header.py::_priced_diff`.
    `ref_date` is this entry's own displayed reference date (callers use two different
    conventions -- see `row_scoped_headline`/`row_scoped_period_pnl`'s own docstrings);
    `note_label` names `date_b` only inside the "N priced now but unpriced on
    <note_label>" detail note.

    (2026-09-21, user decision "use previous date until has value") a `date_b` with no
    usable close steps back to the previous business day that has one
    (`engine.pnl.reference.resolve_reference`, the header's rule): the entry gains
    `ref_date_used` / `ref_note` / `ref_note_detail` / `ref_dates_skipped`, every
    existing key keeps its meaning, and no mark is copied for any trade."""
    from engine.pnl.reference import annotate, resolve_reference

    df_a = _scoped_frame(conn, date_a, trade_ids)
    choice = resolve_reference(df_a, date_b, lambda iso: _scoped_frame(conn, iso, trade_ids))
    label = choice.ref_date_used if choice.stepped_back else note_label
    entry = _priced_diff_frames(conn, df_a, choice.frame, date_a, ref_date, label)
    return annotate(entry, choice,
                    lambda s: _reference_missing_reason(s.unpriced, s.date, s.n_blocked, s.n_open_then, conn))


def _priced_diff_frames(conn: sqlite3.Connection, df_a: pd.DataFrame, df_b: pd.DataFrame,
                        date_a: str, ref_date: str, note_label: str) -> dict:
    """`_priced_diff_scoped`'s figure for two already-scoped books, `df_b` the reference
    close's."""
    total = len(df_a)
    if total == 0:
        return {"value": 0.0, "ref_date": ref_date, "available": True, "reason": "",
                "excluded_summary": "", "excluded_detail": ""}

    a_priced = df_a[df_a["reason"] == ""]
    a_unpriced = df_a[df_a["reason"] != ""]
    a_priced_ids = set(a_priced["trade_id"])

    if df_b.empty:
        b_priced_ids, b_unpriced_ids, b_pnl = set(), set(), {}
    else:
        b_priced = df_b[df_b["reason"] == ""]
        b_priced_ids = set(b_priced["trade_id"])
        b_unpriced_ids = set(df_b[df_b["reason"] != ""]["trade_id"])
        b_pnl = dict(zip(b_priced["trade_id"], b_priced["pnl_usd"]))

    blocked_ids = a_priced_ids & b_unpriced_ids  # priced now, unpriced back then -- excluded
    contributing_a_ids = a_priced_ids - blocked_ids
    contributing_b_ids = a_priced_ids & b_priced_ids  # priced at both ends

    if blocked_ids and len(blocked_ids) > len(contributing_b_ids):
        b_unpriced = df_b[df_b["reason"] != ""] if not df_b.empty else df_b
        n_blocked, n_open = len(blocked_ids), len(blocked_ids) + len(contributing_b_ids)
        return {"value": float("nan"), "ref_date": ref_date, "available": False,
                "reason": _reference_missing_reason(b_unpriced, note_label, n_blocked, n_open, conn),
                "excluded_summary": "", "excluded_detail": ""}

    if not contributing_a_ids:
        return {"value": float("nan"), "ref_date": ref_date, "available": False,
                "reason": _nothing_priced_reason(a_unpriced, date_a),
                "excluded_summary": "", "excluded_detail": ""}

    a_sum = float(a_priced[a_priced["trade_id"].isin(contributing_a_ids)]["pnl_usd"].sum())
    b_sum = sum(b_pnl[t] for t in contributing_b_ids)
    value = a_sum - b_sum

    a_unpriced_ids = set(a_unpriced["trade_id"])
    n_excluded = len(a_unpriced_ids) + len(blocked_ids)
    if n_excluded == 0:
        return {"value": value, "ref_date": ref_date, "available": True, "reason": "",
                "excluded_summary": "", "excluded_detail": ""}

    detail = _unpriced_breakdown(a_unpriced)
    if blocked_ids:
        note = f"{len(blocked_ids)} priced now but unpriced on {note_label}"
        detail = f"{detail}; {note}" if detail else note
    return {"value": value, "ref_date": ref_date, "available": True, "reason": "",
            "excluded_summary": f"excludes {n_excluded} of {total} trades unpriced" + bad_value_note(a_unpriced),
            "excluded_detail": detail}


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
    unavailable. `ref_date` convention (unchanged from before the 2026-09-17 partial-
    pricing fix, preserved exactly): every "as of today" card (ltd/daily/d5/mtd/ytd/
    trading) shows `as_of`; only the two raw levels (ltd1/ltd2/ltd1_daily/trading_t1)
    show their own T-1/T-2 date -- this mirrors the Excel header layout, not
    `row_scoped_period_pnl`'s different convention below."""
    from engine.pnl.ledger import period_reference_dates

    trade_ids = list(trade_ids)
    refs = period_reference_dates(as_of)
    t1, t2 = refs["daily"], refs["previous_day"]

    out: Dict[str, dict] = {
        "ltd": _priced_single_scoped(conn, as_of, trade_ids, as_of),
        "daily": _priced_diff_scoped(conn, as_of, t1, trade_ids, as_of, t1),
        "trades": {"value": float(len(trade_ids)), "ref_date": as_of, "available": True,
                   "reason": "", "is_count": True},
        "ltd1_daily": _priced_diff_scoped(conn, t1, t2, trade_ids, t1, t2),
        "ltd1": _priced_single_scoped(conn, t1, trade_ids, t1),
        "ltd2": _priced_single_scoped(conn, t2, trade_ids, t2),
        "d5": _priced_diff_scoped(conn, as_of, refs["d5"], trade_ids, as_of, refs["d5"]),
        "mtd": _priced_diff_scoped(conn, as_of, refs["mtd"], trade_ids, as_of, refs["mtd"]),
        "ytd": _priced_diff_scoped(conn, as_of, refs["ytd"], trade_ids, as_of, refs["ytd"]),
    }

    today_rows = _scoped_frame(conn, as_of, trade_ids)
    today_rows = today_rows[today_rows["trade_date"] == as_of] if not today_rows.empty else today_rows
    out["trading"] = _priced_single_from_df(today_rows, as_of)

    t1_rows = _scoped_frame(conn, t1, trade_ids)
    t1_rows = t1_rows[t1_rows["trade_date"] == t1] if not t1_rows.empty else t1_rows
    out["trading_t1"] = _priced_single_from_df(t1_rows, t1)
    return out


def _official_mark(conn: sqlite3.Connection, instrument_id: str, settle_date: str,
                    mark_type: str, as_of: str) -> Optional[float]:
    row = conn.execute(
        "SELECT value FROM marks_official WHERE instrument_id = :i AND settle_date = :s "
        "AND mark_type = :m AND as_of_date = :d",
        {"i": instrument_id, "s": settle_date, "m": mark_type, "d": as_of},
    ).fetchone()
    if row is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        # A mark that is not a number is no mark for this display-only column (the T-1
        # rate): blank here, and reported where it matters -- `value_book(t-1)` names it on
        # the trade's row, `bad_stored_values` names it on the Blotter's notice. One such
        # cell used to raise out of `scope_df` and blank the whole sub-tab.
        return None


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
    # A quantity `value_book` could not read is NaN on the row (its `reason` names the
    # stored text): no side can be said for it, and "Sell" would be an invention.
    df["side"] = df["quantity"].map(lambda q: "" if q != q else ("Buy" if q >= 0 else "Sell"))

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
            try:
                s, pair, _src = usd_per_quote(conn, ccy, as_of)
            except ValueError:  # the conversion SPOT on file is not a number: no notional, not an error card
                s = float("nan")
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
    `{value, ref_date, available, reason, excluded_summary, excluded_detail}`, using
    the same business-day reference dates as `engine.pnl.ledger.period_pnl` (via
    `period_reference_dates`). `ref_date` convention (own, different from
    `row_scoped_headline`'s -- preserved exactly): a period DIFFERENCE card
    (daily/d5/mtd/ytd/previous_day) shows its own COMPARISON date underneath, not
    `as_of`; only `ltd`/`trading` show `as_of`."""
    from engine.pnl.ledger import period_reference_dates

    trade_ids = list(trade_ids)
    refs = period_reference_dates(as_of)
    out: Dict[str, dict] = {"ltd": _priced_single_scoped(conn, as_of, trade_ids, as_of)}

    for key in ("daily", "d5", "mtd", "ytd"):
        ref_date = refs[key]
        out[key] = _priced_diff_scoped(conn, as_of, ref_date, trade_ids, ref_date, ref_date)

    out["previous_day"] = _priced_diff_scoped(
        conn, refs["daily"], refs["previous_day"], trade_ids, refs["previous_day"], refs["previous_day"])

    today_rows = _scoped_frame(conn, as_of, trade_ids)
    today_rows = today_rows[today_rows["trade_date"] == as_of] if not today_rows.empty else today_rows
    out["trading"] = _priced_single_from_df(today_rows, as_of)
    return out
