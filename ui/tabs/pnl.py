"""P&L tab: "What did I make, and why?" (Phase G, 2026-09-29; the design doc's "Table specs", P&L).

The trade is the unit, as on the Book: the shared filter bar and its switch (`ui.tabs.trade_filter`;
here the switch slices the table by Spread (the trade, default) | Strategy (its type) | Commodity
family, whole trades only, never a trade split, or by Contract (2026-09-30, the user's decision: the
one slice that splits a trade): one row per contract, `ui.tabs.book_contracts`' grouping, its P&L
and split the per-fill figures of the fills in it summed, a click giving the trades holding it),
then the headline of the rows showing, then:
  - the controls: Total | By month, the slice, a Download CSV link;
  - the table (2026-10-01, user: the chart removed, "the today last 2 days 5d onwards - that should
    be in the table"): one row per slice value, the total row first (unfiltered, each column but 2d =
    the top bar's figure for that period), columns Today | 2d | 5d | MTD | YTD | All (since entry, LTD;
    2d, the user's yes 2026-10-01, from the close 2 business days back, which the top bar lacks), each
    period's split into Spread, FX, Hedge, New, Realised (Other) on hover of its cell (All: Realised
    and Open); sorted by the size of MTD, each period heading sortable; By month: one column per
    month and a Total; a click on a row opens its legs (sliced by Trade) or its trades, each
    clickable to its legs;
  - the track record, folded: best and worst day, days up, largest fall, now against the peak, the
    closed trades' win rate, average win and loss and holding days, and a small table by type.

Every figure is the engine's, as given, summed per trade and slice (display): each period's P&L per
fill and its split are `engine.spreads.period_explain`, the months `engine.pnl.series.monthly_pnl`,
the record `engine.pnl.series.track_record` and `engine.spreads.scorecard`. The trades are the Book's
(`ui.tabs.book.gather`: `engine.spreads.trade_book` plus the fills on no trade), so a trade here is
exactly the Book's. `period_rows` (below) stays: the Book reads each fill's Daily / MTD / YTD / LTD
through it (the header's own split over the screens' shared filled reader).
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dcc, html

from ui.revision import DATA_REVISION_ID
from ui.tabs import header
from ui.tabs import trade_filter as tf
from ui.tabs.formatting import (
    MISSING, about, compact, day_text, full_signed, issues_drawer, km_cell, marker, missing_cell,
    pct_text, plain_words, row_info, sign_class, sum_known, cap,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "pnl-body"
CONTENT_ID = "pnl-content"
HEADLINE_ID = "pnl-headline"
MODE_ID = "pnl-mode"
TABLE_SLOT_ID = "pnl-table-slot"
TABLE_ID = "pnl-table"
TRACK_ID = "pnl-track"
FOOT_ID = "pnl-foot"
CSV_BUTTON_ID = "pnl-csv"
DOWNLOAD_ID = "pnl-download"
OPEN_STORE_ID = "pnl-open-rows"             # session: the rows opened ("g:<slice value>", "t:<trade>")
SORT_STORE_ID = "pnl-sort-store"            # session: {"key": period key, "dir": "desc" | "asc"} or None
ROW_TYPE = "pnl-row"                        # a clickable row: {"type", "idx": row key}
SORT_TYPE = "pnl-sort"                      # a period heading: {"type", "idx": period key}
TAB = "pnl"
NA = MISSING
HIDDEN = {"display": "none"}

# The per-fill split the Book reads (the header's own rule).
PERIODS = ("daily", "d5", "mtd", "ytd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD", "ltd": "LTD"}

# The tab's period columns (2026-10-01: columns of the one table, no switch); MTD orders the rows.
PERIOD_CHOICES = (("today", "Today"), ("d2", "2d"), ("d5", "5d"), ("mtd", "MTD"), ("ytd", "YTD"), ("all", "All"))
PERIOD_KEYS = tuple(k for k, _t in PERIOD_CHOICES)
DEFAULT_PERIOD = "mtd"
ENGINE_KEY = {"today": "daily", "d5": "d5", "mtd": "mtd", "ytd": "ytd", "all": "ltd"}   # 2d: `_two_day`
# the top bar's figure each column's total equals (unfiltered; the top bar has no 2d)
HEADER_WORDS = {"today": "the top bar's Daily", "d5": "the 5d on the top bar's Daily hover",
                "mtd": "the top bar's MTD", "ytd": "the top bar's YTD", "all": "the top bar's LTD"}
PERIOD_TIPS = {"today": "Today's P&L: each fill's change since the previous close, by the header's own Daily rule.",
               "d2": "The last 2 business days: each fill's change since the close 2 business days back.",
               "d5": "The last 5 business days: each fill's change since the close 5 business days back.",
               "mtd": "Month to date: each fill's change since the last close of the previous month.",
               "ytd": "Year to date: each fill's change since the last close of the previous year.",
               "all": "Since entry (LTD): each fill's whole P&L since it was dealt, realised and open."}
MODE_TOTAL, MODE_MONTH = "total", "month"
COMPONENTS = (("spread", "Spread", "The spread's own move: the contracts' change in their currency, at the end's spot."),
              ("fx", "FX", "The currency's move on the P&L already made (0 on a USD trade)."),
              ("hedge", "Hedge", "The currency hedges' P&L (the USD/CNH future, FX trades)."),
              ("new_trades", "New", "Fills dealt within the period: their whole P&L to the end."),
              ("realised", "Realised", "Trades settled within the period: their whole change."),
              ("other", "Other", "A trade that could not be split (no local P&L or conversion on one date)."))
LTD_PARTS = (("realised", "Realised", "Trades settled or closed out: their P&L, frozen."),
             ("open", "Open", "Trades still open: their P&L since they opened (the total less realised)."))


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _lines(*parts: Any) -> str:
    return "\n".join(str(p) for p in parts if p)


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def full_usd(v: float) -> str:
    return f"USD {full_signed(v)}"


# --------------------------------------------------------------------------- the per-fill split (kept)
class PeriodView:
    """One period's figures: `entry` (the header's whole-book entry with its markers), `rows` (one
    per trade on the as-of book: trade_id, instrument_id, product, status, trade_date, value (float
    or None), reason, note), `ref_iso`, `ref_used` and `blocked`."""

    def __init__(self, key: str, entry: dict, rows: pd.DataFrame, ref_iso: str = "", ref_used: str = "",
                 blocked: Sequence[str] = ()):
        self.key, self.entry, self.rows, self.ref_iso, self.ref_used = key, entry, rows, ref_iso, ref_used
        self.blocked = list(blocked)

    @property
    def title(self) -> str:
        return PERIOD_TITLES[self.key]


def _rows_frame(df: pd.DataFrame, values: Dict[str, Optional[float]], reasons: Dict[str, str],
                notes: Dict[str, str]) -> pd.DataFrame:
    out = df[["trade_id", "instrument_id", "product", "status", "trade_date"]].copy()
    out["value"] = [values.get(t) for t in out["trade_id"]]
    out["reason"] = [reasons.get(t, "") for t in out["trade_id"]]
    out["note"] = [notes.get(t, "") for t in out["trade_id"]]
    return out


def period_rows(conn: sqlite3.Connection, as_of: str, key: str, df_today: pd.DataFrame) -> PeriodView:
    """The per-trade figures of `key` (`PERIODS`) on `as_of`, by the header's rule: the whole-book
    `entry` is `header._priced_single` / `_priced_diff` itself, with the fill and step-back markers,
    so the known figures summed are the header's figure."""
    from engine.pnl.calendar import load_holidays
    from engine.pnl.ledger import period_reference_dates
    from engine.pnl.reference import annotate, fill_caption, filled_from, resolve_reference
    from ui.tabs.blotter_pricing import priced_value_book

    try:
        root_reason = header._root_reason(conn, as_of, header._needs_cached(conn, as_of))
    except Exception:  # noqa: BLE001 -- the plain fallback sentence, as the header does
        root_reason = header._root_reason(conn, as_of)
    if df_today.empty:
        return PeriodView(key, dict(header._EMPTY_PRICED), _rows_frame(df_today, {}, {}, {}))

    reasons_today = {t: str(r or "") for t, r in zip(df_today["trade_id"], df_today["reason"])}
    notes_today = {t: str(n or "") for t, n in zip(df_today["trade_id"], df_today.get("note", [""] * len(df_today)))}
    a_pnl = {t: float(v) for t, v, r in zip(df_today["trade_id"], df_today["pnl_usd"], df_today["reason"])
             if not r and v == v}

    if key == "ltd":
        entry = header._priced_single(df_today, root_reason)
        n_filled = header._filled_count(df_today)
        if n_filled and entry.get("available"):
            entry["markers"] = [(f"filled {n_filled}",
                                 header._joined(fill_caption(df_today, as_of), header._fill_notes(df_today)))]
        values = {t: a_pnl.get(t) for t in df_today["trade_id"]}
        notes = {t: n for t, n in notes_today.items() if filled_from(n)}
        return PeriodView(key, entry, _rows_frame(df_today, values, reasons_today, notes))

    holidays = load_holidays()
    ref_iso = period_reference_dates(as_of)[key]
    backfill = None  # the reference-close reason says the P&L fact and points at the Data tab (2026-09-30)
    title = PERIOD_TITLES[key]

    def _book(iso: str) -> pd.DataFrame:
        return priced_value_book(conn, iso)[0]

    choice = resolve_reference(df_today, ref_iso, _book, holidays, frames_filled=True)
    entry = header._priced_diff(df_today, choice.frame, root_reason, choice.ref_date_used,
                                header._reference_reason(conn, ref_iso, title, backfill))
    entry = annotate(entry, choice,
                     lambda s: header._reference_reason(conn, s.date, title, backfill)(s.n_blocked, s.n_open_then))
    if entry.get("available"):
        entry["markers"] = header._reference_markers(entry, choice)
    split = choice.split
    ref_used = choice.ref_date_used
    frame_b = choice.frame
    b_pnl = ({t: float(v) for t, v, r in zip(frame_b["trade_id"], frame_b["pnl_usd"], frame_b["reason"]) if not r and v == v}
             if not frame_b.empty else {})
    b_notes = ({t: str(n or "") for t, n in zip(frame_b["trade_id"], frame_b.get("note", [""] * len(frame_b)))}
               if not frame_b.empty else {})
    values: Dict[str, Optional[float]] = {}
    reasons: Dict[str, str] = {}
    notes: Dict[str, str] = {}
    unusable = not entry.get("available")
    for t in df_today["trade_id"]:
        if unusable:
            values[t] = None
            reasons[t] = str(entry.get("reason") or f"{title} has no usable reference close")
        elif t in split.contributing_b_ids:
            values[t] = a_pnl[t] - b_pnl[t]
            fill_note = b_notes.get(t, "")
            notes[t] = f"reference close {ref_used}: {fill_note}" if filled_from(fill_note) else ""
        elif t in split.contributing_a_ids:
            values[t] = a_pnl[t]
            notes[t] = f"new since the {ref_used} close: its whole LTD counts (trading P&L)"
        elif t in split.blocked_ids:
            values[t] = None
            reasons[t] = f"priced today but not on the {ref_used} close: left out of {title} rather than faked"
        else:
            values[t] = None
            reasons[t] = reasons_today.get(t) or f"no P&L on {as_of}"
        if filled_from(notes_today.get(t, "")) and values[t] is not None:
            notes[t] = header._joined(notes[t], notes_today[t])
    return PeriodView(key, entry, _rows_frame(df_today, values, reasons, notes), ref_iso, ref_used,
                      sorted(split.blocked_ids))


# --------------------------------------------------------------------------- gathering
def _memo(kind: str, conn: sqlite3.Connection, as_of: str, build: Callable[[], Any], extra: tuple = ()) -> Any:
    from ui.tabs.blotter_pricing import screen_memo
    return screen_memo("pnl-" + kind, conn, as_of, build, extra=extra)


def base(conn: sqlite3.Connection, as_of: str) -> dict:
    """What no period changes: the daily series, the Book's trades, the months, the record."""
    from ui.tabs.blotter_pricing import config_inputs_key
    return _memo("base", conn, as_of, lambda: _base(conn, as_of), extra=config_inputs_key())


def _base(conn: sqlite3.Connection, as_of: str) -> dict:
    from engine.pnl.series import daily_series, monthly_pnl, track_record
    from ui.tabs import book
    from ui.tabs.blotter_pricing import shared_spreads

    data: Dict[str, Any] = {"as_of": as_of, "errors": [], "n_trades": book.trades_on_file(conn)}
    if not data["n_trades"]:
        return data
    from ui.tabs.blotter_pricing import raw_value_book
    series = daily_series(conn, as_of, value_fn=raw_value_book)   # one valuation per date, shared (S6)
    data["series"] = series
    bk = book.gather(conn, as_of)
    data["trades"] = list(bk.get("trades") or [])
    data["errors"].extend(bk.get("errors") or [])
    data["trade_of"] = {str(i): str(t.get("trade")) for t in data["trades"] for i in t.get("trade_ids") or []}
    try:
        from ui.tabs import book_contracts
        data["contracts"] = book_contracts.rows_of(conn, bk)
    except Exception as exc:  # noqa: BLE001 -- the Contract slice then says why
        log.exception("P&L tab: the contracts could not be grouped for %s", as_of)
        data["contracts"] = []
        data["errors"].append(("Contracts", f"could not be grouped ({type(exc).__name__}: {exc})"))
    try:
        data["spreads"] = shared_spreads(conn, as_of, filled=True)
    except Exception as exc:  # noqa: BLE001 -- period_explain then groups off the series itself
        data["spreads"] = None
        data["errors"].append(("Positions", f"could not be grouped ({type(exc).__name__}: {exc})"))
    for name, title, build in (("track", "Track record", lambda: track_record(series, as_of)),
                               ("monthly", "Months", lambda: monthly_pnl(series, as_of))):
        try:
            data[name] = build()
        except Exception as exc:  # noqa: BLE001 -- its own section says so
            log.exception("P&L tab: the %s failed for %s", name, as_of)
            data[name] = None
            data["errors"].append((title, f"could not be built ({type(exc).__name__}: {exc})"))
    try:
        from engine.spreads import scorecard
        try:
            from ui.tabs.blotter_pricing import shared_trade_book
            tb = shared_trade_book(conn, as_of) if data["spreads"] is not None else None
        except Exception:  # noqa: BLE001 -- the scorecard then reads the rule types itself
            tb = None
        data["scorecard"] = scorecard(conn, as_of, series=series, spreads=data["spreads"], trades=tb)
    except Exception as exc:  # noqa: BLE001
        log.exception("P&L tab: the scorecard failed for %s", as_of)
        data["scorecard"] = None
        data["errors"].append(("Scorecard", f"could not be built ({type(exc).__name__}: {exc})"))
    return data


def period(conn: sqlite3.Connection, as_of: str, choice: str) -> dict:
    """One period's figures per fill, memoised per choice."""
    choice = choice if choice in dict(PERIOD_CHOICES) else DEFAULT_PERIOD
    return _memo("period", conn, as_of, lambda: _period(conn, as_of, choice), extra=(choice,))


def periods(conn: sqlite3.Connection, as_of: str) -> Dict[str, dict]:
    """{period key: `period`} for the six columns, in order."""
    return {k: period(conn, as_of, k) for k in PERIOD_KEYS}


def _period(conn: sqlite3.Connection, as_of: str, choice: str) -> dict:
    """{available, reason, total, start_ref, ref_used, end, ltd, by_trade {tid: parts}, excluded
    [(tid, why)], other_trades, title}."""
    from engine.spreads.period_explain import COMPONENTS as PARTS, period_explain
    b = base(conn, as_of)
    series = b["series"]
    out: Dict[str, Any] = {"available": False, "reason": "", "total": None, "by_trade": {}, "excluded": [],
                           "other_trades": [], "ltd": choice == "all", "choice": choice,
                           "title": dict(PERIOD_CHOICES)[choice]}
    if not series.days:
        out["reason"] = f"no trade dated on or before {as_of}"
        return out
    if choice == "d2":
        return _two_day(conn, as_of, series, out)
    ex = period_explain(conn, as_of, ENGINE_KEY[choice], series=series, spreads=b.get("spreads"))
    out.update(start_ref=ex.get("start_ref"), ref_used=ex.get("ref_used"), end=as_of if as_of in series.days
               else series.days[-1], total=ex.get("total"), available=bool(ex.get("available")),
               reason=ex.get("reason") or "", excluded=list(ex.get("excluded") or []),
               other_trades=list(ex.get("other_trades") or []), ref_note=ex.get("ref_note") or "")
    out["by_trade"] = {str(r["trade_id"]): {k: r.get(k) for k in ("total", *PARTS)} for r in ex.get("by_trade") or []}
    if choice == "all":
        # the engine's own bucket per fill: all of a fill's LTD is realised or open, never split
        for r in ex.get("by_trade") or []:
            out["by_trade"][str(r["trade_id"])]["open"] = (r.get("total") if r.get("bucket") == "open" else 0.0)
    return out


def _two_day(conn: sqlite3.Connection, as_of: str, series, out: Dict[str, Any]) -> dict:
    """The 2d column (user yes, 2026-10-01; the top bar has none): `engine.pnl.series.period_pnl`
    from the close 2 business days before the as-of (`period_reference_dates`' T-2, the trading
    calendar of `config/holidays.txt`; its own step-back when that close has no value) to the
    as-of, split by `period_explain.classify_trades`, as the Custom range did."""
    from data.contracts import load_roots
    from engine.pnl.ledger import period_reference_dates
    from engine.pnl.series import period_pnl
    from engine.spreads.period_explain import COMPONENTS as PARTS, _currencies, classify_trades
    ref = period_reference_dates(as_of)["previous_day"]
    try:
        pp = period_pnl(series, ref, as_of)
    except Exception as exc:  # noqa: BLE001 -- the column then says why, as period_explain does
        out.update(start_ref=ref, reason=f"{as_of} is not a business day of the daily series ({exc})")
        return out
    out.update(start_ref=ref, ref_used=pp.ref_used, end=as_of, total=pp.total, available=pp.available,
               reason=pp.reason, excluded=list(pp.excluded), ref_note=pp.ref_note or "")
    if pp.available and pp.total is not None:
        ids = [str(i) for i in (pp.frame_end["instrument_id"] if not pp.frame_end.empty else [])]
        parts, _bucket, others = classify_trades(pp, load_roots(), _currencies(conn, ids), False)
        out["by_trade"] = {tid: {**{c: p[c] for c in PARTS}, "total": p["_amount"]} for tid, p in parts.items()}
        out["other_trades"] = others
    return out


# --------------------------------------------------------------------------- slicing
# `ps` below is `periods(...)`: {period key: that period's figures per fill}. A row's figure for a
# period is its fills' figures summed (display), never recomputed.
NOTHING = 0.5          # a split part under half a dollar is nothing: left off the cell's hover
FILTER_KEYS = (*PERIOD_KEYS, "mtotal")


def _slice_of(t: dict, group: str) -> str:
    return str(t.get("trade") or "") if group == tf.GROUP_NONE else tf.group_of(t, group)


def fills_of(trades: Sequence[dict]) -> List[str]:
    return [str(i) for t in trades for i in t.get("trade_ids") or []]


def tab_state(state: Optional[dict]) -> Dict[str, Any]:
    """The shared filter state with this tab's column filters kept only for columns it still has
    (a filter on a retired column, a session's "Spread" or "Hedge", would hide rows with no funnel
    left to clear it)."""
    s = tf.normal(state)
    pre = f"{TAB}:"
    s["cols"] = {k: v for k, v in s["cols"].items() if not k.startswith(pre) or k[len(pre):] in FILTER_KEYS}
    return s


def part_sum(p: dict, ids: Sequence[str], part: str = "total") -> Tuple[Optional[float], int, List[str]]:
    """(known sum, count excluded, reasons) of a component of one period over the fills `ids` (display)."""
    by, why = p.get("by_trade") or {}, dict(p.get("excluded") or [])
    whole = "" if p.get("available") else str(p.get("reason") or "")
    vals = []
    for i in ids:
        if i in by:
            vals.append((by[i].get(part), ""))
        else:
            vals.append((None, f"{i}: {why.get(i) or whole or 'not in the period'}"))
    return sum_known(vals)


def split_lines(p: dict, ids: Sequence[str]) -> List[str]:
    """The period's split of these fills, one line per part that is not nothing (a cell's hover):
    Spread, FX, Hedge, New, Realised, Other; All: Realised and Open."""
    out = []
    for key, title, _h in (LTD_PARTS if p.get("ltd") else COMPONENTS):
        v = part_sum(p, ids, key)[0]
        if v is not None and abs(v) >= NOTHING:
            out.append(f"{title}: {full_signed(v)}")
    return out


def month_total(b: dict, ids: Sequence[str]) -> Optional[float]:
    """The months' figures of these fills added up (display; None when no month has one)."""
    mp = b.get("monthly")
    frame = mp.by_trade if mp is not None else pd.DataFrame()
    if frame.empty:
        return None
    sub = frame[frame["trade_id"].astype(str).isin(set(ids)) & frame["included"]]
    return float(sub["pnl_usd"].sum()) if not sub.empty else None


def _filter_value(b: dict, ps: Dict[str, dict], t: dict, col: str) -> Optional[float]:
    """A trade's figure for a number column's filter: that period's P&L as the row shows it."""
    ids = fills_of([t])
    if col == "mtotal":
        return month_total(b, ids)
    return part_sum(ps[col], ids)[0] if col in ps else None


def shown_trades(b: dict, ps: Dict[str, dict], state: Optional[dict]) -> List[dict]:
    """The trades the search and every column's filter keep (whole trades; a number column tests
    each trade's own figure for that period, whatever the slice)."""
    s = tab_state(state)
    shown = tf.apply(b.get("trades") or [], s)
    cols = tf.tab_filters(s, TAB)
    if cols:
        shown = [t for t in shown if tf.keeps_cols(t, cols, lambda t, col: _filter_value(b, ps, t, col))]
    return shown


def sort_of(sort: Optional[dict]) -> Optional[dict]:
    """The sort chosen on a period heading, or None (the default: by the size of MTD)."""
    return sort if isinstance(sort, dict) and sort.get("key") in PERIOD_KEYS else None


def next_sort(current: Optional[dict], key: str) -> Optional[dict]:
    """A heading's click: largest first, then smallest first, then back to the default order."""
    cur = sort_of(current) or {}
    if cur.get("key") != key:
        return {"key": key, "dir": "desc"}
    return {"key": key, "dir": "asc"} if cur.get("dir") == "desc" else None


def _rank(b: dict, ps: Dict[str, dict], ids: Sequence[str], mode: str, sort: Optional[dict]) -> tuple:
    """A row's place: By month the size of its months' total; else the size of its MTD, or the
    period chosen on a heading (largest first, or smallest first); a row with no figure last."""
    if mode == MODE_MONTH:
        v = month_total(b, ids)
        return (v is None, -abs(v or 0.0))
    srt = sort_of(sort)
    if srt is None:
        v = part_sum(ps[DEFAULT_PERIOD], ids)[0]
        return (v is None, -abs(v or 0.0))
    v = part_sum(ps[srt["key"]], ids)[0]
    return (v is None, (-1.0 if srt.get("dir") != "asc" else 1.0) * (v or 0.0))


def slices(b: dict, ps: Dict[str, dict], state: Optional[dict], mode: str = MODE_TOTAL,
           sort: Optional[dict] = None) -> List[Tuple[str, List[dict]]]:
    """[(slice value, its trades)] of the rows showing, in the table's order (`_rank`)."""
    s = tab_state(state)
    shown = shown_trades(b, ps, s)
    by: Dict[str, List[dict]] = {}
    for t in shown:
        by.setdefault(_slice_of(t, s["group"]), []).append(t)
    return sorted(by.items(), key=lambda item: (_rank(b, ps, fills_of(item[1]), mode, sort), item[0]))


def contract_items(b: dict, ps: Dict[str, dict], state: Optional[dict], mode: str = MODE_TOTAL,
                   sort: Optional[dict] = None) -> List[Tuple[str, List[str], dict]]:
    """The Contract slice: [(contract name, the fills of the trades showing in it, the contract row)],
    in the table's order. The trades showing are the filters' whole trades, as in every slice; a
    contract row holds only their fills in it, so the rows add up to the same total."""
    shown = set(fills_of(shown_trades(b, ps, state)))
    items = []
    for r in b.get("contracts") or []:
        ids = [i for i in r["trade_ids"] if i in shown]
        if ids:
            items.append((r["name"], ids, r))
    return sorted(items, key=lambda item: (_rank(b, ps, item[1], mode, sort), item[0], item[2]["key"]))


def clearer_words(r: dict, ids: Sequence[str]) -> Tuple[str, str]:
    """(the clearers of these fills of a contract, the accounts on hover)."""
    of = r.get("clearer_of") or {}
    names = sorted({of.get(i, "") for i in ids} - {""})
    return " + ".join(names), ""


# --------------------------------------------------------------------------- the table
# Row kinds: the Book / Filtered total row and a slice's group row keep ONE "Excl. N" marker per
# period cell; a trade or a leg row carries no badge, its reasons on each figure's hover and in the
# row's one "i" after the name (layout wave 2, 2026-09-29).
ROW_TOTAL, ROW_GROUP, ROW_TRADE, ROW_LEG = "total", "group", "trade", "leg"


def _money_td(fig: Tuple[Optional[float], int, List[str]], hover: str = "", full: bool = False,
              badge: bool = False) -> html.Td:
    """A money cell: the em dash with its reasons when there is no figure; `badge`: the one
    "Excl. N" marker of a total or group row, else the fills left out go on the figure's hover."""
    v, n, reasons = fig
    if v is None:
        return html.Td(missing_cell(_lines(*reasons[:8]) or "no figure"))
    left_out = _lines(f"Excludes {_plural(n, 'fill')} with no figure", *reasons[:8]) if n else ""
    m = marker(f"Excl. {n}", left_out, "marker--small") if n and badge else None
    tip = _lines(hover, "" if badge else left_out)
    if full:
        title = cap(plain_words(tip)) or None
        return html.Td([html.Span(full_signed(v), className=sign_class(v) or None, title=title), m])
    return html.Td([km_cell(v, hover=tip or None), m])


def _row_reasons(ps: Dict[str, dict], ids: Sequence[str], mode: str) -> List[str]:
    """The reasons of a trade or leg row, for its one "i": the fills each period leaves out, each
    reason once."""
    if mode == MODE_MONTH:
        return []
    heads: List[str] = []
    reasons: List[str] = []
    for k, title in PERIOD_CHOICES:
        _v, n, why = part_sum(ps[k], ids)
        if n:
            heads.append(f"{title} excludes {_plural(n, 'fill')} with no figure")
            reasons += [r for r in why if r not in reasons]
    return heads + reasons[:8] if heads else []


def _columns(group: str, mode: str, months: Sequence[str] = ()) -> List[Tuple[str, str, str]]:
    cols = [("name", SLICE_TITLES.get(group, SLICE_TITLES[tf.GROUP_NONE]), "l")]
    if group == tf.GROUP_NONE:
        cols.append(("type", "Strategy", "l"))
    elif group == tf.GROUP_BY_CONTRACT:
        cols.append(("clearer", "Clearer", "l"))
    if mode == MODE_MONTH:
        return cols + [(f"m:{m}", _month_title(m, months), "") for m in months] + [("mtotal", "Total", "")]
    return cols + [(k, title, "") for k, title in PERIOD_CHOICES]


def _month_title(m: str, months: Sequence[str]) -> str:
    try:
        d = dt.date.fromisoformat(m + "-01")
    except ValueError:
        return m
    title = f"{d:%b}"
    if months and m == months[-1]:
        title += " (MTD)"
    if months and months[0][:4] != months[-1][:4]:
        title = f"{d:%b} {d.year % 100:02d}" + (" (MTD)" if m == months[-1] else "")
    return title


COLUMN_TIPS = {"mtotal": "The row's months added up.",
               "type": "The trade's strategy: its type by rule from its legs (calendar, cross-exchange, cross-product, "
                       "mixed, outright), the Book's Type.",
               "clearer": "The clearing broker of the contract's fills, from the file's account."}
SLICE_TITLES = {tf.GROUP_NONE: "Spread", tf.GROUP_BY_TYPE: "Strategy", tf.GROUP_BY_COMMODITY: "Commodity family",
                tf.GROUP_BY_CONTRACT: "Contract"}
SLICE_TIPS = {"Spread": "One row per trade (a PBRoot name), whole, every leg and hedge in it; click for its legs.",
              "Strategy": "The trades by their strategy, whole; click for the trades.",
              "Commodity family": "The trades by their commodity family, whole; click for the trades.",
              "Contract": "One row per contract (SHFE, COMEX and LME copper apart), the P&L of every fill in it: the "
                          "one slice that splits a trade. Click for the trades holding it."}


# The first column's funnel holds the lists carried across Book, P&L and Risk, its own first: by
# trade, the trade names and the commodity families (the Type column holds the types); sliced by
# type or commodity, that list then the other two, so every carried filter stays in reach.
NAME_PARTS = {"Spread": ("trade", "commodity"), "Strategy": ("type", "trade", "commodity"),
              "Commodity family": ("commodity", "trade", "type"), "Contract": ("trade", "commodity", "type")}
NUMBER_HINT = "Each trade's own figure for that period, in USD."
SORT_WORDS = "Click to sort by this period: largest first, then smallest first, then back to the size of MTD."


def period_tip(b: dict, p: dict, key: str) -> str:
    """A period heading's hover: what it is, the close it is measured from, and where its split is."""
    ref = p.get("ref_used") or p.get("start_ref")
    measured = f"Measured from the {day_text(ref, b.get('as_of'))} close." if ref and not p.get("ltd") else ""
    split = ("Hover a figure for its split into realised and open." if p.get("ltd")
             else "Hover a figure for its split into spread, FX, hedge, new and realised.")
    return _lines(PERIOD_TIPS[key], measured, p.get("ref_note") or "", split, SORT_WORDS)


def head(b: dict, ps: Dict[str, dict], cols: Sequence[Tuple[str, str, str]], state: Optional[dict] = None,
         options: Optional[Dict[str, List[dict]]] = None, sort: Optional[dict] = None) -> html.Thead:
    """The column heads, each with its funnel: the text columns the lists carried across the trade
    tabs, the figures one comparison (this tab's own); a period heading sorts on a click."""
    tips = {**COLUMN_TIPS, "name": SLICE_TIPS.get(cols[0][1], "") if cols else "",
            **{k: period_tip(b, ps[k], k) for k in PERIOD_KEYS if k in ps}}
    srt = sort_of(sort)
    ths = []
    half = len(cols) // 2
    for i, (key, title, cls) in enumerate(cols):
        pop = None
        if key == "name":
            pop = tf.trade_funnel(TAB, state, options or {}, NAME_PARTS.get(title, ("trade",)), "name")
        elif key == "type":
            pop = tf.trade_funnel(TAB, state, options or {}, ("type",), "type")
        elif key in FILTER_KEYS:
            pop = tf.number_funnel(TAB, state, key, hint=f"{NUMBER_HINT} {tf.NUMBER_HINT}")
        sort_id = {"type": SORT_TYPE, "idx": key} if key in PERIOD_KEYS else None
        ths.append(tf.head_th(title, cls, tips.get(key) or "", sort_id=sort_id, arrow=tf.arrow_of(srt, key),
                              pop=pop, right=i > half and i > 0))
    return html.Thead(html.Tr(ths))


def _month_cells(b: dict, ids: Sequence[str], months: Sequence[str], full: bool = False,
                 badge: bool = False) -> List[html.Td]:
    """One cell per month: blank where none of these fills was on file that month (nothing to show),
    the em dash with its reason where every one of them was left out."""
    mp = b.get("monthly")
    frame = mp.by_trade if mp is not None else pd.DataFrame()
    cells, vals = [], []
    idset = set(ids)
    for m in months:
        sub = frame[(frame["month"] == m) & frame["trade_id"].astype(str).isin(idset)] if not frame.empty else frame
        pairs = [(_num(v) if inc else None, f"{t}: {r or 'left out'}")
                 for t, v, inc, r in zip(sub.get("trade_id", []), sub.get("pnl_usd", []), sub.get("included", []),
                                         sub.get("reason", []))]
        if not pairs:
            cells.append(html.Td(""))
            continue
        fig = sum_known(pairs)
        vals.append((fig[0], ""))
        cells.append(_money_td(fig, full=full, badge=badge))
    total = sum_known(vals) if vals else (None, 0, [])
    cells.append(_money_td((total[0], 0, ["No month with a figure"]), hover="The months added up", full=full))
    return cells


def _cells(ps: Dict[str, dict], b: dict, ids: Sequence[str], cols, mode: str, months: Sequence[str],
           kind: str = ROW_TRADE, header_match: bool = False) -> List[html.Td]:
    """The row's figures: one per period column, its split on hover (`header_match`: the unfiltered
    total row, whose every figure is the top bar's); By month one per month and the Total."""
    full = kind == ROW_LEG
    badge = kind in (ROW_TOTAL, ROW_GROUP)
    if mode == MODE_MONTH:
        return _month_cells(b, ids, months, full, badge)
    out = []
    for key, _t, _c in cols:
        if key not in PERIOD_KEYS:
            continue
        p = ps[key]
        hover = _lines(_total_words(b, p, key) if header_match else "", *split_lines(p, ids))
        out.append(_money_td(part_sum(p, ids), hover=hover, full=full, badge=badge))
    return out


def _total_words(b: dict, p: dict, key: str) -> str:
    """The unfiltered total row's first hover line: the top bar's figure it equals, or for 2d (the
    top bar has none) the close it is measured from."""
    if key in HEADER_WORDS:
        return f"Equals {HEADER_WORDS[key]}"
    ref = p.get("ref_used") or p.get("start_ref")
    return f"Measured from the {day_text(ref, b.get('as_of'))} close" if ref else ""


def _row_id(key: str) -> dict:
    return {"type": ROW_TYPE, "idx": key}


def leg_rows(ps: Dict[str, dict], b: dict, t: dict, cols, mode, months) -> List[html.Tr]:
    """A trade's legs (hedges last) with the same columns, at full figures."""
    legs = sorted(t.get("legs") or [], key=lambda leg: (bool(leg.get("hedge")), str(leg.get("name") or "")))
    rows = []
    for leg in legs:
        ids = [str(i) for i in leg.get("trade_ids") or []]
        name = str(leg.get("name") or leg.get("contract_id") or "") + (" (hedge)" if leg.get("hedge") else "")
        first = [html.Td([name, row_info(_row_reasons(ps, ids, mode))],
                         className="l tk-indent2" if len(cols) and cols[0][0] == "name"
                         and cols[0][1] != SLICE_TITLES[tf.GROUP_NONE] else "l tk-indent")]
        if any(k == "type" for k, _t, _c in cols):
            first.append(html.Td(""))
        rows.append(html.Tr(first + _cells(ps, b, ids, cols, mode, months, ROW_LEG),
                            className="tk-leg" + (" tk-leg--hedge" if leg.get("hedge") else "")))
    return rows


TOTAL_TIP = "Each column but 2d equals the top bar's figure for its period (the top bar has no 2d)"


def contract_table(b: dict, ps: Dict[str, dict], s: dict, mode: str, opened: set, cols, months,
                   sort: Optional[dict] = None) -> html.Table:
    """The Contract slice: the total row (the same fills as every slice), one row per contract with
    its clearer, a click giving the trades holding it, each with its part of the contract's P&L."""
    items = contract_items(b, ps, s, mode, sort)
    ids_all = fills_of(shown_trades(b, ps, s))
    filtered = tf.is_filtered(s, TAB)
    n_all = len(b.get("contracts") or [])
    label = (f"Filtered · {len(items)} of {_plural(n_all, 'contract')}" if filtered
             else f"Book · {_plural(n_all, 'contract')}")
    body: List[Any] = [html.Tr([html.Td(html.Span(label, title=None if filtered else TOTAL_TIP), className="l"),
                                html.Td("")]
                               + _cells(ps, b, ids_all, cols, mode, months, ROW_TOTAL, header_match=not filtered),
                               className="tk-total book-total")]
    for name, ids, r in items:
        key = f"c:{r['key']}"
        is_open = key in opened
        clearers, _h = clearer_words(r, ids)
        trades = [str(t.get("trade") or "") for t, leg in r["legs"] if set(leg.get("trade_ids") or []) & set(ids)]
        hover = _lines(f"{_plural(len(ids), 'fill')} in {_plural(len(trades), 'trade')}: {', '.join(trades)}",
                       "Only the fills of the trades showing" if len(ids) < len(r["trade_ids"]) else "")
        first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"),
                          html.Span(name, className="tk-name", title=plain_words(hover)),
                          row_info(_row_reasons(ps, ids, mode))], className="l"),
                 html.Td(clearers or missing_cell("No account on file for these fills"), className="l")]
        body.append(html.Tr(first + _cells(ps, b, ids, cols, mode, months), id=_row_id(key), n_clicks=0,
                            className="tk-row" + (" tk-row--open" if is_open else "")))
        if not is_open:
            continue
        idset = set(ids)
        subs = [(t, [str(i) for i in leg.get("trade_ids") or [] if str(i) in idset]) for t, leg in r["legs"]]
        for t, t_ids in sorted([x for x in subs if x[1]], key=lambda x: _rank(b, ps, x[1], mode, sort)):
            t_clr, _h = clearer_words(r, t_ids)
            body.append(html.Tr([html.Td([str(t.get("trade") or ""), row_info(_row_reasons(ps, t_ids, mode))],
                                         className="l tk-indent"),
                                 html.Td(t_clr, className="l")] + _cells(ps, b, t_ids, cols, mode, months, ROW_LEG),
                                className="tk-leg"))
    return html.Table([head(b, ps, cols, s, tf.options_for(b.get("trades") or []), sort), html.Tbody(body)],
                      id=TABLE_ID, className="book-table book-grid tk-table")


def table(b: dict, ps: Dict[str, dict], state: Optional[dict], mode: str, opened: Sequence[str],
          sort: Optional[dict] = None) -> html.Table:
    s = tab_state(state)
    group = s["group"]
    months = [m["month"] for m in (b["monthly"].months if b.get("monthly") is not None else [])] if mode == MODE_MONTH else []
    cols = _columns(group, mode, months)
    if group == tf.GROUP_BY_CONTRACT:
        return contract_table(b, ps, s, mode, set(opened or []), cols, months, sort)
    items = slices(b, ps, s, mode, sort)
    shown = [t for _v, ts in items for t in ts]
    ids_all = fills_of(shown)
    opened = set(opened or [])
    filtered = tf.is_filtered(s, TAB)
    named = [t for t in b.get("trades") or [] if not t.get("pseudo")]
    label = (f"Filtered · {len([t for t in shown if not t.get('pseudo')])} of {_plural(len(named), 'trade')}"
             if filtered
             else f"Book · {_plural(len(named), 'trade')}")
    total_first = [html.Td(html.Span(label, title=None if filtered else TOTAL_TIP), className="l")]
    if group == tf.GROUP_NONE:
        total_first.append(html.Td(""))
    body: List[Any] = [html.Tr(total_first + _cells(ps, b, ids_all, cols, mode, months, ROW_TOTAL,
                                                    header_match=not filtered),
                               className="tk-total book-total")]
    for value, trades in items:
        ids = fills_of(trades)
        if group == tf.GROUP_NONE:
            t = trades[0]
            key = f"t:{value}"
            is_open = key in opened
            first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"), html.Span(value, className="tk-name"),
                              row_info(_row_reasons(ps, ids, mode))], className="l"),
                     html.Td(tf.trade_type_label(t, short=True) if not t.get("pseudo") else "", className="l")]
            body.append(html.Tr(first + _cells(ps, b, ids, cols, mode, months), id=_row_id(key), n_clicks=0,
                                className="tk-row" + (" tk-row--open" if is_open else "")))
            if is_open:
                body.extend(leg_rows(ps, b, t, cols, mode, months))
            continue
        key = f"g:{value}"
        is_open = key in opened
        hover = _lines(*(str(t.get("trade")) for t in trades))
        first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"),
                          html.Span(value, className="tk-name", title=hover),
                          html.Span(f" · {_plural(len(trades), 'trade')}", className="tk-sub")], className="l")]
        body.append(html.Tr(first + _cells(ps, b, ids, cols, mode, months, ROW_GROUP), id=_row_id(key), n_clicks=0,
                            className="tk-row" + (" tk-row--open" if is_open else "")))
        if not is_open:
            continue
        for t in sorted(trades, key=lambda t: _rank(b, ps, fills_of([t]), mode, sort)):
            tkey = f"t:{t.get('trade')}"
            t_open = tkey in opened
            t_ids = fills_of([t])
            first = [html.Td([html.Span("▾ " if t_open else "▸ ", className="tk-chev"), str(t.get("trade")),
                              row_info(_row_reasons(ps, t_ids, mode))], className="l tk-indent")]
            body.append(html.Tr(first + _cells(ps, b, t_ids, cols, mode, months), id=_row_id(tkey),
                                n_clicks=0, className="tk-row tk-row--sub" + (" tk-row--open" if t_open else "")))
            if t_open:
                body.extend(leg_rows(ps, b, t, cols, mode, months))
    return html.Table([head(b, ps, cols, s, tf.options_for(b.get("trades") or []), sort), html.Tbody(body)],
                      id=TABLE_ID, className="book-table book-grid tk-table")


# --------------------------------------------------------------------------- the headline
def headline(b: dict, ps: Dict[str, dict], state: Optional[dict]) -> Optional[html.Div]:
    """Only what neither the header nor the table shows at a glance: the rows' single best and
    worst trade today. The count and every period's P&L are the total row's; the close each period
    is measured from is on its heading's hover. None when there is nothing to say."""
    shown = shown_trades(b, ps, state)
    p = ps["today"]
    per_trade = [(t, part_sum(p, fills_of([t]))[0]) for t in shown if not t.get("pseudo")]
    per_trade = [(t, x) for t, x in per_trade if x is not None]
    best = max(per_trade, key=lambda x: x[1]) if per_trade else None
    worst = min(per_trade, key=lambda x: x[1]) if per_trade else None
    items: List[Any] = []
    if best and best[1] >= NOTHING:
        items.append(("Best today", html.Span([f"{best[0].get('trade')} ", km_cell(best[1])]),
                      "The rows' single best trade today"))
    if worst and worst[1] <= -NOTHING:
        items.append(("Worst today", html.Span([f"{worst[0].get('trade')} ", km_cell(worst[1])]),
                      "The rows' single worst trade today"))
    return tf.headline(items) if items else None


# --------------------------------------------------------------------------- the track record
def _days(v: Any) -> str:
    n = _num(v)
    return MISSING if n is None else f"{n:.0f}"


def hold_text(closed: dict) -> str:
    """'12 / 30': the engine's median holding days of the closed winners / losers (a dash for a
    side with none)."""
    hw, hl = closed.get("median_hold_days_win"), closed.get("median_hold_days_loss")
    if _num(hw) is None and _num(hl) is None:
        return MISSING
    return f"{_days(hw)} / {_days(hl)}"


def hold_hover(closed: dict) -> str:
    parts = []
    for side, key in (("winners", "win"), ("losers", "loss")):
        med, avg = _num(closed.get(f"median_hold_days_{key}")), _num(closed.get(f"avg_hold_days_{key}"))
        parts.append(f"{side}: median {_days(med)}, average {_days(avg)} days" if med is not None
                     else f"{side}: none closed")
    return _lines(*parts)


BY_TYPE_COLUMNS = (("type", "Type", "l", "The trade's type by rule from its legs, as on the Book."),
                   ("closed", "Closed", "", "Trades of this type that are flat."),
                   ("win", "Win rate", "", "Closed winners over closed trades of the type (a scratch within a few "
                                           "dollars counts as neither)."),
                   ("avg", "Avg win / loss", "", "Average P&L of the closed winners / losers of the type."),
                   ("hold", "Hold days win / loss", "", "Median holding days of the closed winners / losers."),
                   ("closed_pnl", "Closed P&L", "", "The closed trades' P&L, summed (LTD on the as-of)."),
                   ("open", "Open", "", "Trades of this type still open, every one (priced or not)."),
                   ("open_pnl", "Open P&L", "", "The open trades' LTD, summed."))


def _excl(excluded: Sequence) -> Any:
    """The "Excl. N" marker of a by-type figure (the trades it leaves out, with why), or None."""
    if not excluded:
        return None
    return marker(f"Excl. {len(excluded)}", _lines(f"Excludes {_plural(len(excluded), 'trade')} with no figure",
                                                   *(f"{pid}: {w}" for pid, w in list(excluded)[:8])), "marker--small")


def by_type_table(sc: dict) -> Optional[html.Table]:
    """The track record by the trade-book rule type (`scorecard`'s `by_type`: Calendar,
    Cross-exchange, Cross-product, Mixed, Outright), each type's closed and open summary as the
    engine gives it."""
    by = sc.get("by_type") or {}
    if not by:
        return None
    order = {c: n for n, c in enumerate(tf.TYPE_ORDER)}
    rows = []
    for code in sorted(by, key=lambda c: (order.get(c, 99), c)):
        summ = by[code] or {}
        c, o = summ.get("closed") or {}, summ.get("open") or {}
        c_excl, o_excl = list(c.get("excluded") or []), list(o.get("excluded") or [])
        n_open = int(o.get("count") or 0) + len(o_excl)
        avg = (html.Span([km_cell(c.get("avg_win")), " / ", km_cell(c.get("avg_loss"))])
               if c.get("count") else missing_cell("no closed trade of this type"))
        rows.append(html.Tr([
            html.Td(tf.type_label(code) if code else html.Span("Not typed", title="hedges only, or legs the app does "
                                                                                    "not recognise"), className="l"),
            html.Td(str(c.get("count") or 0)),
            html.Td(pct_text(c.get("win_rate")) if _num(c.get("win_rate")) is not None
                    else missing_cell("no closed trade of this type")),
            html.Td(avg),
            html.Td(hold_text(c) if c.get("count") else missing_cell("no closed trade of this type"),
                    title=plain_words(hold_hover(c)) if c.get("count") else None),
            html.Td([km_cell(c.get("total")), _excl(c_excl)] if c.get("count") else missing_cell(
                _lines("no closed trade of this type", *(f"{pid}: {w}" for pid, w in c_excl[:8])))),
            html.Td(str(n_open)),
            html.Td([km_cell(o.get("unrealised_usd")) if o.get("count")
                     else missing_cell(_lines("no open trade of this type has a figure" if n_open
                                              else "no open trade of this type", *(f"{pid}: {w}" for pid, w in o_excl[:8]))),
                     _excl(o_excl) if o.get("count") else None]),
        ]))
    head = html.Thead(html.Tr([html.Th(t, className=c or None, title=h) for _k, t, c, h in BY_TYPE_COLUMNS]))
    return html.Table([head, html.Tbody(rows)], className="book-table tk-table tk-small pnl-by-type")


def _kv_table(rows: Sequence[Tuple[str, Any, str]], head: str) -> html.Table:
    """A small key / value table (the kit's `tk-kv`) under a one-cell heading; the definition of
    each figure on hover of its key."""
    body = [html.Tr([html.Td(k, className="tk-kv-k", title=cap(plain_words(h)) or None), html.Td(v)])
            for k, v, h in rows]
    return html.Table([html.Thead(html.Tr(html.Th(head, colSpan=2, className="l"))), html.Tbody(body)],
                      className="book-table tk-table tk-kv pnl-track-kv")


def track_block(b: dict, as_of: str) -> html.Details:
    tr = b.get("track")
    days_rows: List[Tuple[str, Any, str]] = []
    closed_rows: List[Tuple[str, Any, str]] = []
    items = days_rows

    def item(k: str, v: Any, hover: str = "") -> None:
        items.append((k, v, hover))

    if tr is None:
        item("Days counted", missing_cell("The track record could not be built"))
    elif not tr.get("n_days"):
        item("Days counted", "None yet", "No business day with a Daily figure yet")
    else:
        best, worst = tr.get("best_day") or {}, tr.get("worst_day") or {}
        item("Best day", html.Span([km_cell(best.get("value")), f" on {day_text(best.get('date'), as_of)}"]),
             "The book's best Daily figure since the first trade")
        item("Worst day", html.Span([km_cell(worst.get("value")), f" on {day_text(worst.get('date'), as_of)}"]),
             "The book's worst Daily figure since the first trade")
        share = tr.get("share_positive")
        item("Days up", f"{pct_text(share)} ({tr['n_positive']} of {tr['n_days']})" if share is not None
             else missing_cell("No day with a Daily figure"), "Business days whose Daily figure was above 0")
        dd = tr.get("max_drawdown") or {}
        if (dd.get("value") or 0.0) < -0.005:
            item("Largest fall", html.Span([km_cell(dd["value"]), f" {day_text(dd.get('peak_date'), as_of)} → "
                                                                   f"{day_text(dd.get('trough_date'), as_of)}"]),
                 "The largest fall of the LTD from an earlier peak, peak to trough")
        else:
            item("Largest fall", "None", "The LTD has never been below an earlier peak")
        fp = tr.get("from_peak") or {}
        if fp:
            item("From the peak", html.Span([km_cell(fp.get("value")), f" since {day_text(fp.get('peak_date'), as_of)}"]),
                 "The LTD now against its highest close")
    sc = b.get("scorecard") or {}
    closed = (sc.get("summary") or {}).get("closed") or {}
    items = closed_rows
    if closed.get("count"):
        item("Count", str(closed["count"]), "Trades that are flat")
        item("Win rate", pct_text(closed.get("win_rate")), f"Winners over {_plural(closed['count'], 'closed trade')}")
        item("Average win / loss", html.Span([km_cell(closed.get("avg_win")), " / ", km_cell(closed.get("avg_loss"))]),
             "Average P&L of the closed winners / losers")
        payoff = _num(closed.get("payoff_ratio"))
        item("Payoff", f"{payoff:.2f}" if payoff is not None else missing_cell("Needs a closed winner and loser"),
             "Average win over average loss")
        item("Hold days, win / loss", hold_text(closed),
             _lines("Median holding days of the closed winners / losers (calendar days from the first fill to "
                    "the day the trade went flat)", hold_hover(closed)))
    else:
        item("Closed trades", "None yet", "The win rate needs a closed trade")
    type_table = by_type_table(sc)
    grid = html.Div([_kv_table(days_rows, "Days"), _kv_table(closed_rows, "Closed trades"), type_table],
                    className="pnl-track-grid")
    return html.Details([html.Summary(about("Track record", "The whole book since the first trade: each day by the "
                                                            "header's own Daily rule; win rate over the closed trades.",
                                            level="span")),
                         grid], className="book-fold tk-fold-block", id=TRACK_ID)


# --------------------------------------------------------------------------- issues, CSV
def issue_items(b: dict, ps: Dict[str, dict]) -> List[Any]:
    """The drawer's reasons, per period (the same reason about the same fill under several periods
    is one row, the drawer's own rule)."""
    items: List[Any] = list(b.get("errors") or [])
    trade_of = b.get("trade_of") or {}

    def where(tid: Any) -> str:
        trade = trade_of.get(str(tid), "")
        return f"{trade} · Fill {tid}" if trade else f"Fill {tid}"

    for k, title in PERIOD_CHOICES:
        p = ps.get(k) or {}
        if p.get("reason") and not p.get("available"):
            items.append((title, "Whole book", p["reason"]))
        for tid, why in p.get("excluded") or []:
            items.append((title, where(tid), str(why)))
        for o in p.get("other_trades") or []:
            items.append((title, where(o.get("trade_id")),
                          f"{full_signed(o.get('amount'))} USD not split into spread and FX: {o.get('why')}"))
    return items


def _csv_figures(row: dict, ps: Dict[str, dict], ids: Sequence[str]) -> None:
    """Each period's P&L of these fills, then each period's split, close and fills left out."""
    for k, title in PERIOD_CHOICES:
        row[title] = part_sum(ps[k], ids)[0]
    for k, title in PERIOD_CHOICES:
        p = ps[k]
        for key, part, _h in (LTD_PARTS if p.get("ltd") else COMPONENTS):
            row[f"{title} {part.lower()}"] = part_sum(p, ids, key)[0]
        if not p.get("ltd"):
            row[f"{title} from close"] = p.get("ref_used") or p.get("start_ref")
        row[f"{title} fills excluded"] = part_sum(p, ids)[1]


def csv_frame(b: dict, ps: Dict[str, dict], state: Optional[dict], mode: str,
              sort: Optional[dict] = None) -> pd.DataFrame:
    s = tab_state(state)
    rows = []
    months = [m["month"] for m in (b["monthly"].months if b.get("monthly") is not None else [])]
    mp = b.get("monthly")

    def add_months(row: dict, ids: Sequence[str]) -> None:
        if mode == MODE_MONTH and mp is not None:
            fr = mp.by_trade
            for m in months:
                sub = fr[(fr["month"] == m) & fr["trade_id"].astype(str).isin(ids) & fr["included"]]
                row[m] = float(sub["pnl_usd"].sum()) if not sub.empty else None

    if s["group"] == tf.GROUP_BY_CONTRACT:
        for name, ids, r in contract_items(b, ps, s, mode, sort):
            idset = set(ids)
            for t, leg in r["legs"]:
                t_ids = [str(i) for i in leg.get("trade_ids") or [] if str(i) in idset]
                if not t_ids:
                    continue
                row = {"Slice": name, "Contract key": r["key"], "Clearer": clearer_words(r, t_ids)[0],
                       "Trade": t.get("trade"), "Strategy": tf.trade_type_label(t), "Commodity": r["family"]}
                _csv_figures(row, ps, t_ids)
                add_months(row, t_ids)
                rows.append(row)
        return pd.DataFrame(rows)
    for value, trades in slices(b, ps, s, mode, sort):
        for t in trades:
            for leg in t.get("legs") or []:
                ids = [str(i) for i in leg.get("trade_ids") or []]
                row = {"Slice": value, "Trade": t.get("trade"), "Strategy": tf.trade_type_label(t),
                       "Commodity": tf.family_label(t), "Leg": leg.get("name"), "Hedge": bool(leg.get("hedge"))}
                _csv_figures(row, ps, ids)
                add_months(row, ids)
                rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- render
def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render_parts(as_of: Optional[str], db_path, state: Optional[dict] = None, mode: str = MODE_TOTAL,
                 opened: Sequence[str] = (), sort: Optional[dict] = None) -> dict:
    out = {"body": None, "shown": False, "headline": None, "table": None, "track": None, "foot": None}
    if not as_of:
        out["body"] = message_box("No as-of date available.")
        return out
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        out["body"] = message_box(f"Database not available ({exc}).")
        return out
    try:
        from ui.tabs import book
        if book.trades_on_file(conn) == 0:
            out["body"] = book.empty_state(idx=TAB)
            return out
        b = base(conn, as_of)
        ps = periods(conn, as_of)
        mode = mode if mode in (MODE_TOTAL, MODE_MONTH) else MODE_TOTAL
        out.update(shown=True, headline=headline(b, ps, state),
                   table=table(b, ps, state, mode, opened, sort), track=track_block(b, as_of),
                   foot=issues_drawer(issue_items(b, ps)))
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("P&L tab could not be built for %s", as_of)
        out["body"] = html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The P&L could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    from ui.tabs.book import _tidy_parts
    return _tidy_parts(out)


def render(as_of: Optional[str], db_path, state: Optional[dict] = None, mode: str = MODE_TOTAL) -> html.Div:
    """The whole body for a direct render."""
    p = render_parts(as_of, db_path, state, mode=mode)
    if not p["shown"]:
        return html.Div([p["body"]])
    return html.Div([p["headline"], html.Div(p["table"], className="book-card book-main tk-card"),
                     p["track"], p["foot"]])


def options_of(as_of: str, db_path) -> Dict[str, List[dict]]:
    from ui.tabs import book
    return book.options_of(as_of, db_path)


def _switch(id_: str, options: Sequence[Tuple[str, str]], default: str) -> dcc.RadioItems:
    return dcc.RadioItems(id=id_, className="book-switch", options=[{"label": lb, "value": v} for v, lb in options],
                          value=default, inline=True, persistence=True, persistence_type="session")


def layout(default_date: Optional[str] = None) -> html.Div:
    return html.Div(className="pnl-tab", children=[
        html.Div(html.H2("P&L", className="tab-title"), className="tab-header"),
        html.Div(id=BODY_ID, children=[message_box("Loading the P&L...")]),
        html.Div(id=CONTENT_ID, style=HIDDEN, children=[
            html.Div(className="tk-strip tk-strip--controls", children=[
                html.Span("Table", className="tk-k"),
                _switch(MODE_ID, ((MODE_TOTAL, "Total"), (MODE_MONTH, "By month")), MODE_TOTAL),
                tf.bar_slot(TAB),
                # a plain link at the strip's right end, as on the Book, so the strip stays one line at
                # 1680 px (user, 2026-09-30: the gold button wrapped onto a second line)
                html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0,
                            className="book-link-button book-csv-link pnl-csv-link",
                            title="Every leg of the rows showing with each period's P&L and split (and months), "
                                  "at full figures"),
                dcc.Download(id=DOWNLOAD_ID)]),
            html.Div(id=HEADLINE_ID),
            html.Div(html.Div(id=TABLE_SLOT_ID, className="tk-table-slot"), className="book-card book-main tk-card"),
            html.Div(id=f"{TRACK_ID}-slot"),
            html.Div(id=FOOT_ID),
        ]),
        dcc.Store(id=OPEN_STORE_ID, storage_type="session"),
        dcc.Store(id=SORT_STORE_ID, storage_type="session"),
    ])


build_layout = layout


def _clicked() -> bool:
    hit = dash.ctx.triggered or []
    return bool(hit and hit[0].get("value"))


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    tf.register_bar(app, TAB, get_db_path, options_of)

    @app.callback(
        Output(BODY_ID, "children"), Output(CONTENT_ID, "style"), Output(HEADLINE_ID, "children"),
        Output(TABLE_SLOT_ID, "children"), Output(f"{TRACK_ID}-slot", "children"), Output(FOOT_ID, "children"),
        Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(tf.STORE_ID, "data"),
        Input(MODE_ID, "value"), Input(OPEN_STORE_ID, "data"), Input(SORT_STORE_ID, "data"),
    )
    def _render(as_of, _rev, state, mode, opened, sort):
        p = render_parts(as_of, get_db_path(), state, mode or MODE_TOTAL, opened or [], sort)
        if not p["shown"]:
            return p["body"], HIDDEN, None, None, None, None
        return (None, {}, compact(p["headline"]), compact(p["table"]), compact(p["track"]), compact(p["foot"]))

    @app.callback(Output(OPEN_STORE_ID, "data"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  State(OPEN_STORE_ID, "data"), prevent_initial_call=True)
    def _toggle(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        key = str(trig.get("idx") or "")
        cur = list(current or [])
        return [x for x in cur if x != key] if key in cur else cur + [key]

    @app.callback(Output(SORT_STORE_ID, "data"), Input({"type": SORT_TYPE, "idx": ALL}, "n_clicks"),
                  State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _sort(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        return next_sort(current, str(trig.get("idx") or ""))

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(tf.STORE_ID, "data"), State(MODE_ID, "value"), State(SORT_STORE_ID, "data"),
                  prevent_initial_call=True)
    def _csv(n_clicks, as_of, state, mode, sort):
        if not n_clicks or not as_of:
            return dash.no_update
        conn = _open(get_db_path())
        try:
            b = base(conn, as_of)
            frame = csv_frame(b, periods(conn, as_of), state, mode or MODE_TOTAL, sort)
        finally:
            conn.close()
        return dcc.send_data_frame(frame.to_csv, f"pnl_{as_of}.csv", index=False)
