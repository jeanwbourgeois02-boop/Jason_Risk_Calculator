"""The book's daily series: one filled valuation per business day, per trade, and the period,
monthly and track-record figures read off it (user yes under hard rule 7, 2026-09-29: the
rebuilt P&L tab, "How did the P&L get here, and what drove it?").

Nothing here prices anything new. Each day is `value_book(conn, day)` with the fill applied
(`engine.pnl.reference.fill_book`), exactly what `ui/tabs/blotter_pricing.py::priced_value_book`
gives every screen, so a figure read off the series agrees with the header to the cent. A
period is the header's own rule (`reference.resolve_reference` with the fill already in the
frames, then `reference.diff_split`): both ends priced -> the difference; traded after the
reference close -> its LTD (trading P&L); priced at the end but not on the reference close ->
left out with that reason; unpriced -> the valuation's reason. Never zero, never a silent drop.

Cost. A day is valued once per database revision (path, mtime, size) and kept IN PROCESS: a
second call, or the next as-of on the same revision, costs only its new days. Nothing is ever
written to the database (a write would change the file's mtime and invalidate every
mtime-keyed cache in a loop: CLAUDE.md "Guard rails"). A new revision of a file drops the
entries of its older revisions. Each call logs how many full re-pricings it cost.
"""
from __future__ import annotations

import collections
import datetime as dt
import logging
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple

import pandas as pd

from engine.pnl.calendar import (
    _is_business_day, _last_business_day_of_prev_month, _last_business_day_of_prev_year,
    _n_business_days_back, _prev_business_day, load_holidays,
)
from engine.pnl.reference import (
    MAX_STEP_BACK_BUSINESS_DAYS, NOTHING_PRICED, fill_book, filled_from, resolve_reference,
)
from engine.pnl.valuation import COLUMNS, value_book

log = logging.getLogger(__name__)

SERIES_COLUMNS = tuple(COLUMNS) + ("ltd_usd", "filled", "filled_from")
PERIOD_KEYS = ("daily", "d5", "mtd", "ytd", "ltd")
# Realised = settled (user yes 2026-09-29): a closed-out option group (CLOSED) stays open until its
# expiry, as the P&L tab's Realised line (`ui/tabs/pnl.py::realised_entries`) counts it.
_SETTLED = ("SETTLED",)

# ------------------------------------------------------------------ the in-process memo
_MEMO_MAX = 8192                       # (revision, day) entries per store; a year of days is ~260
_GUARD = threading.RLock()             # one series computed at a time; readers of the stores too
_RAW: "collections.OrderedDict[tuple, pd.DataFrame]" = collections.OrderedDict()
_FILLED: "collections.OrderedDict[tuple, pd.DataFrame]" = collections.OrderedDict()


def db_revision(conn: sqlite3.Connection) -> Optional[tuple]:
    """(path, mtime, size) of the connection's main database file; None for an in-memory or
    unreadable one (then nothing is memoised). Read only: never touches the file."""
    try:
        path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
        if not path:
            return None
        st = os.stat(path)
        return (str(path), st.st_mtime, st.st_size)
    except Exception:  # noqa: BLE001 -- no memo, never a failure
        return None


def clear_memo() -> None:
    with _GUARD:
        _RAW.clear()
        _FILLED.clear()


def _put(store, key: tuple, value) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > _MEMO_MAX:
        store.popitem(last=False)


def _drop_old_revisions(revision: tuple) -> None:
    """A newer revision of the same file makes the older one's days dead weight."""
    for store in (_RAW, _FILLED):
        for key in [k for k in store if k[0] == revision[0] and k[:-1] != revision]:
            del store[key]


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(SERIES_COLUMNS))


def _with_series_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if frame.empty:
        return frame.reindex(columns=list(SERIES_COLUMNS))
    reasons = frame["reason"].fillna("").astype(str)
    values = pd.to_numeric(frame["pnl_usd"], errors="coerce")
    frame["ltd_usd"] = [float(v) if r == "" and not math.isnan(v) else None for r, v in zip(reasons, values)]
    sources = [filled_from(n) for n in frame["note"]]
    frame["filled"] = [bool(s) for s in sources]
    frame["filled_from"] = sources
    return frame


# ------------------------------------------------------------------ 1. the shared series
@dataclass(frozen=True, eq=False)
class DailySeries:
    """The filled valuation of every business day from the book's first trade to `as_of`.

    `frames[day]` is that day's `value_book` frame after the fill (the screens' rows), with
    three columns added: `ltd_usd` (the trade's LTD in USD, None when unpriced: `reason`
    says why), `filled` (valued at an earlier close) and `filled_from` (that close, '').
    The frames are copies of the memo: a caller may edit them."""
    as_of: str
    days: Tuple[str, ...]
    frames: Mapping[str, pd.DataFrame]
    holidays: FrozenSet[str]
    revision: Optional[tuple]
    repricings: int                     # full `value_book` runs this call cost
    seconds: float
    first_trade_date: str = ""

    def frame(self, day: str) -> pd.DataFrame:
        """The book on `day`: the series frame; an empty frame for a day before the first
        trade (nothing was open: a legitimate zero reference). A business day after `as_of`
        or a day that is not a business day raises KeyError (the series never guesses)."""
        if day in self.frames:
            return self.frames[day]
        if not self.days or day < self.days[0]:
            return _empty_frame()
        raise KeyError(f"{day} is not a business day of the series ({self.days[0]} to {self.as_of})")

    def ltd(self, day: str) -> Dict[str, Optional[float]]:
        """{trade_id: LTD USD or None} on `day`."""
        f = self.frame(day)
        return dict(zip(f["trade_id"], f["ltd_usd"])) if not f.empty else {}

    def book_day(self, day: str) -> Tuple[Optional[float], int, int, int]:
        """(sum over priced trades or None when nothing is priced, n unpriced, n trades,
        n filled): the LTD chart's point for `day`, as `ui/tabs/header.py::_cached_ltd`
        returns it (an empty book is (0.0, 0, 0, 0))."""
        f = self.frame(day)
        if f.empty:
            return 0.0, 0, 0, 0
        priced = f[f["reason"] == ""]
        n_unpriced = int(len(f) - len(priced))
        n_filled = int(f["filled"].sum())
        if priced.empty:
            return None, n_unpriced, int(len(f)), n_filled
        return float(priced["pnl_usd"].sum()), n_unpriced, int(len(f)), n_filled

    def chart_points(self) -> List[Tuple[str, Optional[float], int, int, int]]:
        """[(day, value, n unpriced, n trades, n filled)] for every day, oldest first."""
        return [(d, *self.book_day(d)) for d in self.days]


def _business_days(start: dt.date, end: dt.date, holidays) -> List[str]:
    out, d = [], start
    while d <= end:
        if _is_business_day(d, holidays):
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def daily_series(conn: sqlite3.Connection, as_of: str, db_key: Optional[tuple] = None) -> DailySeries:
    """The filled per-trade valuation of every business day (config/holidays.txt calendar) from
    the book's first trade date to `as_of`, oldest first.

    When `as_of` is not a business day (a weekend or holiday book date) it is the series' last
    day, valued the same way, so every figure on it is the header's own.

    `db_key` is the memo key of the database: `db_revision(conn)` when None. A caller that pins
    one view of the file for a whole render passes the key it read once (any hashable tuple whose
    first item is the file path). An in-memory database (key None) is valued without memo.
    Each day is `value_book(conn, day)` then `fill_book` (the look-back reads the series' own
    unfilled days, so a filled value is never carried further), priced once per key and day."""
    started = time.perf_counter()
    holidays = load_holidays()
    row = conn.execute("SELECT MIN(trade_date) FROM trades_official").fetchone()
    first = str(row[0])[:10] if row and row[0] else ""
    end = dt.date.fromisoformat(as_of)
    days = _business_days(dt.date.fromisoformat(first), end, holidays) if first else []
    if first and first <= as_of and not _is_business_day(end, holidays):
        # a weekend or holiday as-of (the book date is a Saturday from Friday 17:00 New York):
        # valued itself, as the header values it; reference closes still step on business days
        days.append(as_of)
    revision = db_key if db_key is not None else db_revision(conn)
    repricings = 0
    frames: Dict[str, pd.DataFrame] = {}
    local_raw: Dict[str, pd.DataFrame] = {}      # the in-memory database's own raw days

    def raw(day: str) -> pd.DataFrame:
        nonlocal repricings
        if revision is not None:
            hit = _RAW.get((*revision, day))
            if hit is not None:
                return hit
        elif day in local_raw:
            return local_raw[day]
        df = value_book(conn, day)
        repricings += 1
        if revision is not None:
            _put(_RAW, (*revision, day), df)
        else:
            local_raw[day] = df
        return df

    def rows_for(iso: str, ids) -> pd.DataFrame:
        if not first or iso < first:
            return _empty_frame()           # nothing dealt yet: no earlier price to take
        cached = _RAW.get((*revision, iso)) if revision is not None else local_raw.get(iso)
        if cached is not None:
            return cached[cached["trade_id"].isin(ids)]
        return value_book(conn, iso, trade_ids=ids)

    with _GUARD:
        if revision is not None:
            _drop_old_revisions(revision)
        for day in days:
            key = (*revision, day) if revision is not None else None
            hit = _FILLED.get(key) if key is not None else None
            if hit is None:
                filled, _ = fill_book(raw(day), day, rows_for, holidays)
                hit = _with_series_columns(filled if filled is not None else _empty_frame())
                if key is not None:
                    _put(_FILLED, key, hit)
            frames[day] = hit.copy()
    seconds = time.perf_counter() - started
    log.info("daily series to %s: %d days, %d full re-pricings of the book (%d from memo), %.2fs",
             as_of, len(days), repricings, len(days) - repricings, seconds)
    return DailySeries(as_of, tuple(days), frames, frozenset(holidays), revision, repricings, seconds, first)


# ------------------------------------------------------------------ 2. periods and months
def period_start(end: str, key: str, holidays: Optional[FrozenSet[str]] = None) -> Optional[str]:
    """The period's own reference close for `end` (None for LTD): Daily t-1bd, 5d t-5bd, MTD the
    last business day of the previous month, YTD of the previous year, on the trading calendar."""
    holidays = load_holidays() if holidays is None else holidays
    d = dt.date.fromisoformat(end)
    if key == "ltd":
        return None
    ref = {"daily": lambda: _prev_business_day(d, holidays),
           "d5": lambda: _n_business_days_back(d, 5, holidays),
           "mtd": lambda: _last_business_day_of_prev_month(d, holidays),
           "ytd": lambda: _last_business_day_of_prev_year(d, holidays)}[key]()
    return ref.isoformat()


@dataclass(frozen=True, eq=False)
class PeriodPnl:
    """One period's P&L per trade, by the header's rule.

    `by_trade` has one row per trade on `end`: `trade_id`, `pnl_usd` (NaN when not included),
    `included`, `kind` ('change' | 'new trade' | ''), `realised` (settled within the period; a
    closed-out option is not realised until it settles at expiry), `reason` (why left out, '' when included), `note` (the fill, '' otherwise).
    `total` is the sum of the included trades (None when the period has no figure, `reason`
    says why); it is the header's figure for the same period to the cent."""
    start_ref: Optional[str]            # the period's own reference close; None for LTD
    ref_used: Optional[str]             # the close measured from (after the step-back)
    end: str
    available: bool
    total: Optional[float]
    reason: str
    by_trade: pd.DataFrame
    excluded: Tuple[Tuple[str, str], ...] = ()
    ref_note: str = ""                  # "from the <d> close: <ref> has no usable close", '' otherwise
    skipped: Tuple[str, ...] = ()       # reference closes tried without value, newest first
    n_filled_end: int = 0               # included trades valued at an earlier close on `end`
    n_filled_ref: int = 0               # included trades valued at an earlier close on `ref_used`
    frame_end: pd.DataFrame = field(default_factory=_empty_frame)
    frame_ref: pd.DataFrame = field(default_factory=_empty_frame)   # for engine/spreads/daily_split.py

    @property
    def n_excluded(self) -> int:
        return len(self.excluded)


def _no_usd_as_unpriced(frame: pd.DataFrame) -> pd.DataFrame:
    """`frame` with a row that has no reason but no USD P&L either given a reason, so it counts
    as unpriced in `diff_split` exactly as in `engine/spreads/daily_split.py::_priced` (never a
    figure missing from a sum that the split counted as priced). A copy only when one exists."""
    if frame.empty:
        return frame
    bad = (frame["reason"].fillna("") == "") & pd.to_numeric(frame["pnl_usd"], errors="coerce").isna()
    if not bad.any():
        return frame
    frame = frame.copy()
    frame.loc[bad, "reason"] = "no USD P&L on its row"
    return frame


_BY_TRADE_COLUMNS = ["trade_id", "pnl_usd", "included", "kind", "realised", "reason", "note"]


def _by_trade(records: List[dict]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=_BY_TRADE_COLUMNS)


def period_pnl(series: DailySeries, start_ref: Optional[str], end: str) -> PeriodPnl:
    """Per trade, the P&L from the close `start_ref` to `end` (`start_ref` None: the LTD on `end`).

    The reference close steps back one business day at a time, at most 5, while the trades
    priced on `end` but unpriced on it outnumber those priced at both ends
    (`reference.resolve_reference`); the frames already carry the fill, so no trade is walked
    back twice. Both ends priced -> LTD(end) - LTD(ref); absent on the reference close (dealt
    after it) -> its LTD at `end`, kind 'new trade'; priced at `end` but not on the reference
    close -> left out with that reason; unpriced at `end` -> left out with the valuation's reason."""
    df_a = _no_usd_as_unpriced(series.frame(end))
    status_a = dict(zip(df_a["trade_id"], df_a["status"])) if not df_a.empty else {}
    note_a = {t: (str(n) if filled_from(n) else "") for t, n in zip(df_a["trade_id"], df_a["note"])} \
        if not df_a.empty else {}
    reason_a = {t: str(r or "") for t, r in zip(df_a["trade_id"], df_a["reason"])} if not df_a.empty else {}
    pnl_a = {t: float(v) for t, v, r in zip(df_a["trade_id"], df_a["pnl_usd"], df_a["reason"])
             if not r and v == v} if not df_a.empty else {}

    if df_a.empty:
        return PeriodPnl(start_ref, start_ref, end, True, 0.0, "", _by_trade([]), frame_end=df_a)

    if start_ref is None:                                   # LTD: the header's `_priced_single`
        records, excluded = [], []
        for t in df_a["trade_id"]:
            if t in pnl_a:
                records.append(dict(trade_id=t, pnl_usd=pnl_a[t], included=True, kind="",
                                    realised=status_a.get(t) in _SETTLED, reason="", note=note_a.get(t, "")))
            else:
                why = reason_a.get(t) or f"no USD P&L on {end}"
                excluded.append((t, why))
                records.append(dict(trade_id=t, pnl_usd=math.nan, included=False, kind="",
                                    realised=False, reason=why, note=""))
        if not pnl_a:
            return PeriodPnl(None, None, end, False, None, f"no trade on file is priced on {end}",
                             _by_trade(records), tuple(excluded), frame_end=df_a)
        return PeriodPnl(None, None, end, True, sum(pnl_a.values()), "", _by_trade(records), tuple(excluded),
                         n_filled_end=sum(1 for t in pnl_a if note_a.get(t)), frame_end=df_a)

    choice = resolve_reference(df_a, start_ref, lambda day: _no_usd_as_unpriced(series.frame(day)),
                               series.holidays, MAX_STEP_BACK_BUSINESS_DAYS, frames_filled=True)
    split, ref_used, df_b = choice.split, choice.ref_date_used, choice.frame
    skipped = tuple(s.date for s in choice.skipped)
    pnl_b = {t: float(v) for t, v, r in zip(df_b["trade_id"], df_b["pnl_usd"], df_b["reason"])
             if not r and v == v} if not df_b.empty else {}
    status_b = dict(zip(df_b["trade_id"], df_b["status"])) if not df_b.empty else {}
    note_b = {t: (str(n) if filled_from(n) else "") for t, n in zip(df_b["trade_id"], df_b["note"])} \
        if not df_b.empty else {}

    period_reason = ""
    if not choice.found:
        n_open = split.n_open_then
        period_reason = (f"{split.n_blocked} of {n_open} trades open on {start_ref} have no price there; "
                         f"{choice.exhausted_sentence}")
    elif split.status == NOTHING_PRICED:
        period_reason = f"no trade on file is priced on {end}"

    records, excluded = [], []
    for t in df_a["trade_id"]:
        rec = dict(trade_id=t, pnl_usd=math.nan, included=False, kind="", realised=False, reason="", note="")
        if period_reason:
            rec["reason"] = period_reason
        elif t in split.contributing_b_ids:
            rec.update(pnl_usd=pnl_a[t] - pnl_b[t], included=True, kind="change",
                       realised=status_a.get(t) in _SETTLED and status_b.get(t) not in _SETTLED)
            fill_notes = [n for n in (f"reference close {ref_used}: {note_b[t]}" if note_b.get(t) else "",
                                      note_a.get(t, "")) if n]
            rec["note"] = "; ".join(fill_notes)
        elif t in split.contributing_a_ids:
            rec.update(pnl_usd=pnl_a[t], included=True, kind="new trade", realised=status_a.get(t) in _SETTLED,
                       note=note_a.get(t, ""))
        elif t in split.blocked_ids:
            rec["reason"] = f"priced on {end} but not on the {ref_used} close: left out rather than faked"
        else:
            rec["reason"] = reason_a.get(t) or f"no USD P&L on {end}"
        if not rec["included"]:
            excluded.append((t, rec["reason"]))
        records.append(rec)

    by_trade = _by_trade(records)
    if period_reason:
        return PeriodPnl(start_ref, ref_used, end, False, None, period_reason, by_trade, tuple(excluded),
                         choice.note, skipped, frame_end=df_a, frame_ref=df_b)
    included = by_trade[by_trade["included"]]
    total = (sum(pnl_a[t] for t in split.contributing_a_ids) - sum(pnl_b[t] for t in split.contributing_b_ids))
    return PeriodPnl(start_ref, ref_used, end, True, total, "", by_trade, tuple(excluded), choice.note, skipped,
                     n_filled_end=sum(1 for t in included["trade_id"] if note_a.get(t)),
                     n_filled_ref=sum(1 for t in included["trade_id"] if note_b.get(t)),
                     frame_end=df_a, frame_ref=df_b)


def periods(series: DailySeries, end: Optional[str] = None) -> Dict[str, PeriodPnl]:
    """{'daily', 'd5', 'mtd', 'ytd', 'ltd'}: `period_pnl` of each on `end` (the series' as-of)."""
    end = end or series.as_of
    return {k: period_pnl(series, period_start(end, k, series.holidays), end) for k in PERIOD_KEYS}


@dataclass(frozen=True, eq=False)
class MonthlyPnl:
    """`months`: one dict per calendar month, oldest first: `month` ('2026-07'), `end` (its last
    business day, or the as-of for the current month), `start_ref`, `ref_used`, `available`,
    `total` (None when n/a), `reason`, `n_included`, `n_excluded`, `ref_note`.
    `by_trade`: long frame `month`, `trade_id`, `pnl_usd` (NaN when left out), `included`,
    `kind`, `realised`, `reason`, `note`. `periods`: the `PeriodPnl` of each month."""
    as_of: str
    months: Tuple[dict, ...]
    by_trade: pd.DataFrame
    periods: Mapping[str, PeriodPnl]

    def table(self) -> pd.DataFrame:
        """Trades by month: rows trade_id, columns month, NaN where a trade has no figure."""
        if self.by_trade.empty:
            return pd.DataFrame()
        return self.by_trade.pivot(index="trade_id", columns="month", values="pnl_usd")


def monthly_pnl(series: DailySeries, as_of: Optional[str] = None) -> MonthlyPnl:
    """Per trade and calendar month, from the first trade's month to `as_of`'s: the month-end LTD
    less the previous month-end LTD (the current month to `as_of`), by `period_pnl`'s rule, so the
    current month is the MTD. Month ends are the last business day of each month."""
    as_of = as_of or series.as_of
    days = [d for d in series.days if d <= as_of]
    ends: Dict[str, str] = {}
    for d in days:
        ends[d[:7]] = d                                    # the last business day of each month seen
    months, frames, per = [], [], {}
    for month, end in ends.items():
        ref = _last_business_day_of_prev_month(dt.date.fromisoformat(end), series.holidays).isoformat()
        p = period_pnl(series, ref, end)
        per[month] = p
        months.append(dict(month=month, end=end, start_ref=ref, ref_used=p.ref_used, available=p.available,
                           total=p.total, reason=p.reason, n_included=int(p.by_trade["included"].sum()),
                           n_excluded=p.n_excluded, ref_note=p.ref_note))
        if not p.by_trade.empty:
            frames.append(p.by_trade.assign(month=month))
    by_trade = (pd.concat(frames, ignore_index=True)[["month"] + _BY_TRADE_COLUMNS] if frames
                else pd.DataFrame(columns=["month"] + _BY_TRADE_COLUMNS))
    return MonthlyPnl(as_of, tuple(months), by_trade, per)


# ------------------------------------------------------------------ 3. the track record
def track_record(series: DailySeries, as_of: Optional[str] = None) -> dict:
    """The book's own record from its daily P&L on every business day of the series up to `as_of`
    (a weekend or holiday as-of is left out), each day by the header's Daily rule (user yes,
    2026-09-29): `period_pnl(series, <the business day before>, day)`, so the trades priced at
    both ends count, a trade dealt that day counts its LTD, the reference close steps back as the
    header's does, and the trades left out are listed with their reasons ("excl. N"), never taken
    as zero. A day is dropped only when the header itself would show n/a for it. The fall from the
    peak runs on the book's LTD by the header's LTD rule (the priced trades summed; n/a only when
    nothing is priced), from 0 on the business day before the first trade.

    Returns {as_of, first_day, n_days, n_positive, share_positive (None with no day), best_day
    {value, date}, worst_day {value, date} (None with no day), max_drawdown {value (<= 0),
    peak_date, peak_value, trough_date, trough_value} (value 0 with no fall), from_peak {value
    (<= 0), peak_date, peak_value, date, ltd} (date = the last day with an LTD),
    daily [{date, value (None when n/a), ref_used, n_excluded, excluded [(trade_id, why)],
    ltd, ltd_n_excluded}], excluded_days [(date, reason)] (the days dropped),
    days_with_exclusions (counted days with excl. > 0), days_with_fill (counted days with a
    trade valued at an earlier close at either end)}."""
    as_of = as_of or series.as_of
    days = [d for d in series.days if d <= as_of and _is_business_day(dt.date.fromisoformat(d), series.holidays)]
    daily: List[dict] = []
    excluded_days: List[Tuple[str, str]] = []
    ltd_path: List[Tuple[str, float]] = []
    days_with_fill = days_with_excl = 0
    for d in days:
        prev = _prev_business_day(dt.date.fromisoformat(d), series.holidays).isoformat()
        p = period_pnl(series, prev, d)
        lt = period_pnl(series, None, d)
        if lt.available:
            ltd_path.append((d, lt.total))
        daily.append(dict(date=d, value=p.total if p.available else None, ref_used=p.ref_used,
                          n_excluded=p.n_excluded, excluded=list(p.excluded),
                          ltd=lt.total if lt.available else None, ltd_n_excluded=lt.n_excluded))
        if not p.available:
            excluded_days.append((d, p.reason))
            continue
        days_with_excl += 1 if p.n_excluded else 0
        days_with_fill += 1 if (p.n_filled_end or p.n_filled_ref) else 0

    counted = [(r["date"], r["value"]) for r in daily if r["value"] is not None]
    n_pos = sum(1 for _d, v in counted if v > 0)
    best = max(counted, key=lambda x: x[1]) if counted else None
    worst = min(counted, key=lambda x: x[1]) if counted else None

    start = (_prev_business_day(dt.date.fromisoformat(days[0]), series.holidays).isoformat() if days else as_of)
    peak_date, peak = start, 0.0
    dd = dict(value=0.0, peak_date=start, peak_value=0.0, trough_date=start, trough_value=0.0)
    for d, v in ltd_path:
        if v > peak:
            peak_date, peak = d, v
        if v - peak < dd["value"]:
            dd = dict(value=v - peak, peak_date=peak_date, peak_value=peak, trough_date=d, trough_value=v)
    last = ltd_path[-1] if ltd_path else (start, 0.0)
    from_peak = dict(value=last[1] - peak, peak_date=peak_date, peak_value=peak, date=last[0], ltd=last[1])

    return dict(as_of=as_of, first_day=days[0] if days else "", n_days=len(counted), n_positive=n_pos,
                share_positive=(n_pos / len(counted)) if counted else None,
                best_day=dict(value=best[1], date=best[0]) if best else None,
                worst_day=dict(value=worst[1], date=worst[0]) if worst else None,
                max_drawdown=dd, from_peak=from_peak, daily=daily, excluded_days=excluded_days,
                days_with_exclusions=days_with_excl, days_with_fill=days_with_fill)
