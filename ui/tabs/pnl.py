"""P&L tab: "What did I make, and why?" (Phase G, 2026-09-29; the design doc's "Table specs", P&L).

The trade is the unit, as on the Book: the shared filter bar and its switch (`ui.tabs.trade_filter`;
here the switch slices the table by Spread (the trade, default) | Strategy (its type) | Commodity
family, whole trades only, never a trade split, or by Contract (2026-09-30, the user's decision: the
one slice that splits a trade): one row per contract, `ui.tabs.book_contracts`' grouping, its P&L
and split the per-fill figures of the fills in it summed, a click giving the trades holding it),
then the headline of the rows showing, then:
  - the controls: Period Today | 5d | MTD (default) | YTD | All | Custom (two dates), and Total |
    By month;
  - the chart: two panels on one business-day axis (only days with a close): on top the period's
    P&L to date of the rows showing, a line filled to zero from 0 at the reference close; below,
    smaller, each day's P&L as green / red bars; a day's hover gives its P&L, the P&L to date and
    the 3 biggest trades that day;
  - the table: one row per slice value, sorted by the size of its P&L, the total row first (sticky,
    = the headline, and unfiltered = the top bar's figure for the period), columns P&L, Spread, FX,
    Hedge, New, Realised (Other only when not zero), % of total; By month: one column per month
    and a Total; a click on a row opens its legs (sliced by Trade) or its trades, each clickable to
    its legs;
  - the track record, folded: best and worst day, days up, largest fall, now against the peak, the
    closed trades' win rate, average win and loss and holding days, and a small table by type.

Every figure is the engine's, as given, summed per trade and slice (display): the period's P&L per
fill and its split are `engine.spreads.period_explain` (a custom range: `engine.pnl.series.
period_pnl` and `period_explain.classify_trades`, the same rule), the daily bars and the line are
`period_pnl` per day, the months `engine.pnl.series.monthly_pnl`, the record
`engine.pnl.series.track_record` and `engine.spreads.scorecard`. The trades are the Book's
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
    MISSING, about, compact, day_text, full_signed, issues_drawer, km_cell, km_text, marker, missing_cell,
    pct_text, plain_words, row_info, sign_class, sum_known, cap,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "pnl-body"
CONTENT_ID = "pnl-content"
HEADLINE_ID = "pnl-headline"
PERIOD_ID = "pnl-period"
CUSTOM_ID = "pnl-custom"
CUSTOM_WRAP_ID = "pnl-custom-wrap"
MODE_ID = "pnl-mode"
CHART_ID = "pnl-chart"
TABLE_SLOT_ID = "pnl-table-slot"
TABLE_ID = "pnl-table"
TRACK_ID = "pnl-track"
FOOT_ID = "pnl-foot"
CSV_BUTTON_ID = "pnl-csv"
DOWNLOAD_ID = "pnl-download"
OPEN_STORE_ID = "pnl-open-rows"             # session: the rows opened ("g:<slice value>", "t:<trade>")
ROW_TYPE = "pnl-row"                        # a clickable row: {"type", "idx": row key}
TAB = "pnl"
CHART_HEIGHT = 380
NA = MISSING
HIDDEN = {"display": "none"}

# The per-fill split the Book reads (the header's own rule).
PERIODS = ("daily", "d5", "mtd", "ytd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD", "ltd": "LTD"}

# The tab's own period switch.
PERIOD_CHOICES = (("today", "Today"), ("d5", "5d"), ("mtd", "MTD"), ("ytd", "YTD"), ("all", "All"),
                  ("custom", "Custom"))
DEFAULT_PERIOD = "mtd"
ENGINE_KEY = {"today": "daily", "d5": "d5", "mtd": "mtd", "ytd": "ytd", "all": "ltd"}
MODE_TOTAL, MODE_MONTH = "total", "month"
COMPONENTS = (("spread", "Spread", "The spread's own move: the contracts' change in their currency, at the end's spot."),
              ("fx", "FX", "The currency's move on the P&L already made (0 on a USD trade)."),
              ("hedge", "Hedge", "The currency hedges' P&L (the USD/CNH future, FX trades)."),
              ("new_trades", "New", "Fills dealt within the period: their whole P&L to the end."),
              ("realised", "Realised", "Trades settled within the period: their whole change."),
              ("other", "Other", "A trade that could not be split (no local P&L or conversion on one date)."))
LTD_PARTS = (("realised", "Realised", "Trades settled or closed out: their P&L, frozen."),
             ("open", "Open", "Trades still open: their P&L since they opened (the total less realised)."))
# the chart's colours: the kit's navy line, green / red bars, hairline grid
_NAVY, _GREEN, _RED = "#0f1f3d", "#1a7f4b", "#c0392b"
_FILL = "rgba(15, 31, 61, 0.07)"
_GRID, _ZERO, _MUTED = "#eef0f4", "#9aa3b2", "#6b7280"


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


def _series_day(series, day: Optional[str]) -> Optional[str]:
    """The last day of the series on or before `day` (None when there is none)."""
    if not day:
        return None
    days = [d for d in series.days if d <= day]
    return days[-1] if days else None


def period(conn: sqlite3.Connection, as_of: str, choice: str, start: Optional[str] = None,
           end: Optional[str] = None) -> dict:
    """The period's figures per fill and per day, memoised per choice."""
    choice = choice if choice in dict(PERIOD_CHOICES) else DEFAULT_PERIOD
    extra = (choice, start or "", end or "") if choice == "custom" else (choice,)
    return _memo("period", conn, as_of, lambda: _period(conn, as_of, choice, start, end), extra=extra)


def _period(conn: sqlite3.Connection, as_of: str, choice: str, start: Optional[str], end: Optional[str]) -> dict:
    """{available, reason, total, start_ref, ref_used, end, ltd, by_trade {tid: parts}, excluded
    [(tid, why)], other_trades, days [{date, by_trade {tid: pnl}, cum {tid: pnl}, n_excluded,
    filled}], title}."""
    from engine.pnl.series import period_pnl
    from engine.spreads.period_explain import COMPONENTS as PARTS, classify_trades, period_explain
    b = base(conn, as_of)
    series = b["series"]
    out: Dict[str, Any] = {"available": False, "reason": "", "total": None, "by_trade": {}, "excluded": [],
                           "other_trades": [], "days": [], "ltd": choice == "all", "choice": choice}
    if not series.days:
        out["reason"] = f"no trade dated on or before {as_of}"
        return out
    if choice == "custom":
        s_day, e_day = _series_day(series, start), _series_day(series, min(end or as_of, as_of))
        if not start or not end:
            out["reason"] = "choose the two dates of the custom period"
            return out
        if e_day is None or (s_day is not None and s_day >= e_day):
            out["reason"] = f"no business day of the book between {start} and {end}"
            return out
        pp = period_pnl(series, s_day, e_day)
        out.update(start_ref=s_day, ref_used=pp.ref_used, end=e_day, total=pp.total, available=pp.available,
                   reason=pp.reason, excluded=list(pp.excluded),
                   title=f"{day_text(s_day, as_of)} to {day_text(e_day, as_of)}")
        if pp.available and pp.total is not None:
            from data.contracts import load_roots
            from engine.spreads.period_explain import _currencies
            ids = [str(i) for i in (pp.frame_end["instrument_id"] if not pp.frame_end.empty else [])]
            parts, _bucket, others = classify_trades(pp, load_roots(), _currencies(conn, ids), False)
            out["by_trade"] = {tid: {**{c: p[c] for c in PARTS}, "total": p["_amount"]} for tid, p in parts.items()}
            out["other_trades"] = others
    else:
        ex = period_explain(conn, as_of, ENGINE_KEY[choice], series=series, spreads=b.get("spreads"))
        out.update(start_ref=ex.get("start_ref"), ref_used=ex.get("ref_used"), end=as_of if as_of in series.days
                   else series.days[-1], total=ex.get("total"), available=bool(ex.get("available")),
                   reason=ex.get("reason") or "", excluded=list(ex.get("excluded") or []),
                   other_trades=list(ex.get("other_trades") or []), ref_note=ex.get("ref_note") or "",
                   title=dict(PERIOD_CHOICES)[choice])
        out["by_trade"] = {str(r["trade_id"]): {k: r.get(k) for k in ("total", *PARTS)} for r in ex.get("by_trade") or []}
        if choice == "all":
            # the engine's own bucket per fill: all of a fill's LTD is realised or open, never split
            for r in ex.get("by_trade") or []:
                out["by_trade"][str(r["trade_id"])]["open"] = (r.get("total") if r.get("bucket") == "open" else 0.0)
    # the chart's days: every business day of the period, its daily P&L per fill and the period to date
    days = [d for d in series.days if (out.get("start_ref") is None or d > out["start_ref"]) and d <= out.get("end", as_of)]
    for d in days:
        i = series.days.index(d)
        prev = series.days[i - 1] if i > 0 else None
        try:
            one = period_pnl(series, prev, d)
            cum = period_pnl(series, out.get("start_ref"), d)
        except Exception as exc:  # noqa: BLE001 -- that day is left out, said so
            out.setdefault("days_left", []).append((d, f"{type(exc).__name__}: {exc}"))
            continue
        out["days"].append({"date": d, "n_excluded": one.n_excluded, "filled": one.n_filled_end,
                            "by_trade": _included(one), "cum": _included(cum)})
    return out


def _included(pp) -> Dict[str, float]:
    bt = pp.by_trade
    if bt is None or bt.empty:
        return {}
    inc = bt[bt["included"]]
    return {str(t): float(v) for t, v in zip(inc["trade_id"], inc["pnl_usd"])}


# --------------------------------------------------------------------------- slicing
def _slice_of(t: dict, group: str) -> str:
    return str(t.get("trade") or "") if group == tf.GROUP_NONE else tf.group_of(t, group)


def fills_of(trades: Sequence[dict]) -> List[str]:
    return [str(i) for t in trades for i in t.get("trade_ids") or []]


def part_sum(p: dict, ids: Sequence[str], part: str = "total") -> Tuple[Optional[float], int, List[str]]:
    """(known sum, count excluded, reasons) of a component over the fills `ids` (display)."""
    by, why = p.get("by_trade") or {}, dict(p.get("excluded") or [])
    vals = []
    for i in ids:
        if i in by:
            vals.append((by[i].get(part), ""))
        else:
            vals.append((None, f"{i}: {why.get(i) or 'not in the period'}"))
    return sum_known(vals)


def row_figures(p: dict, ids: Sequence[str]) -> dict:
    out = {"total": part_sum(p, ids, "total")}
    for key, _t, _h in COMPONENTS:
        out[key] = part_sum(p, ids, key)
    out["open"] = part_sum(p, ids, "open")
    return out


def month_total(b: dict, ids: Sequence[str]) -> Optional[float]:
    """The months' figures of these fills added up (display; None when no month has one)."""
    mp = b.get("monthly")
    frame = mp.by_trade if mp is not None else pd.DataFrame()
    if frame.empty:
        return None
    sub = frame[frame["trade_id"].astype(str).isin(set(ids)) & frame["included"]]
    return float(sub["pnl_usd"].sum()) if not sub.empty else None


def _filter_value(b: dict, p: dict, t: dict, col: str) -> Optional[float]:
    """A trade's figure for a number column's filter: the period's part as the row shows it."""
    ids = fills_of([t])
    if col == "mtotal":
        return month_total(b, ids)
    return part_sum(p, ids, col)[0]


def shown_trades(b: dict, p: dict, state: Optional[dict]) -> List[dict]:
    """The trades the search and every column's filter keep (whole trades; a number column tests
    each trade's own figure for the period, whatever the slice)."""
    s = tf.normal(state)
    shown = tf.apply(b.get("trades") or [], s)
    cols = tf.tab_filters(s, TAB)
    if cols:
        shown = [t for t in shown if tf.keeps_cols(t, cols, lambda t, col: _filter_value(b, p, t, col))]
    return shown


def slices(b: dict, p: dict, state: Optional[dict], mode: str = MODE_TOTAL) -> List[Tuple[str, List[dict]]]:
    """[(slice value, its trades)] of the rows showing, sorted by the size of the period's P&L (By
    month: of the months' total)."""
    s = tf.normal(state)
    shown = shown_trades(b, p, s)
    by: Dict[str, List[dict]] = {}
    for t in shown:
        by.setdefault(_slice_of(t, s["group"]), []).append(t)

    def size(item):
        ids = fills_of(item[1])
        v = month_total(b, ids) if mode == MODE_MONTH else part_sum(p, ids)[0]
        return (v is None, -abs(v or 0.0), item[0])
    return sorted(by.items(), key=size)


def contract_items(b: dict, p: dict, state: Optional[dict], mode: str = MODE_TOTAL) -> List[Tuple[str, List[str], dict]]:
    """The Contract slice: [(contract name, the fills of the trades showing in it, the contract row)],
    sorted by the size of the period's P&L. The trades showing are the filters' whole trades, as in
    every slice; a contract row holds only their fills in it, so the rows add up to the same total."""
    shown = set(fills_of(shown_trades(b, p, state)))
    items = []
    for r in b.get("contracts") or []:
        ids = [i for i in r["trade_ids"] if i in shown]
        if ids:
            items.append((r["name"], ids, r))

    def size(item):
        v = month_total(b, item[1]) if mode == MODE_MONTH else part_sum(p, item[1])[0]
        return (v is None, -abs(v or 0.0), item[0], item[2]["key"])
    return sorted(items, key=size)


def chart_groups(b: dict, p: dict, state: Optional[dict]) -> List[Tuple[str, set]]:
    """[(slice value, its fills)] of the rows showing, in the table's order: the chart's colours."""
    s = tf.normal(state)
    if s["group"] == tf.GROUP_BY_CONTRACT:
        return [(name, set(ids)) for name, ids, _r in contract_items(b, p, s)]
    return [(value, set(fills_of(trades))) for value, trades in slices(b, p, s)]


def clearer_words(r: dict, ids: Sequence[str]) -> Tuple[str, str]:
    """(the clearers of these fills of a contract, the accounts on hover)."""
    of = r.get("clearer_of") or {}
    names = sorted({of.get(i, "") for i in ids} - {""})
    return " + ".join(names), ""


# --------------------------------------------------------------------------- the table
# Row kinds: the Book / Filtered total row and a slice's group row keep ONE "Excl. N" marker (on the
# P&L cell); a trade or a leg row carries no badge, its reasons on each figure's hover and in the
# row's one "i" after the name (layout wave 2, 2026-09-29).
ROW_TOTAL, ROW_GROUP, ROW_TRADE, ROW_LEG = "total", "group", "trade", "leg"
NOTHING = 0.5          # a split part under half a dollar shows blank: nothing in that part


def _money_td(fig: Tuple[Optional[float], int, List[str]], hover: str = "", full: bool = False,
              badge: bool = False, blank_zero: bool = False) -> html.Td:
    """A money cell: the em dash with its reasons when there is no figure; blank when `blank_zero` and
    the figure is nothing at display precision; `badge`: the one "Excl. N" marker of a total or group
    row, else the fills left out go on the figure's hover only."""
    v, n, reasons = fig
    if v is None:
        return html.Td(missing_cell(_lines(*reasons[:8]) or "no figure"))
    left_out = _lines(f"Excludes {_plural(n, 'fill')} with no figure", *reasons[:8]) if n else ""
    if blank_zero and abs(v) < NOTHING:
        return html.Td("")
    m = marker(f"Excl. {n}", left_out, "marker--small") if n and badge else None
    tip = _lines(hover, "" if badge else left_out)
    if full:
        title = cap(plain_words(tip)) or None
        return html.Td([html.Span(full_signed(v), className=sign_class(v) or None, title=title), m])
    return html.Td([km_cell(v, hover=tip or None), m])


def _row_reasons(p: dict, ids: Sequence[str], mode: str) -> List[str]:
    """The reasons of a trade or leg row, for its one "i": the fills the period leaves out."""
    if mode == MODE_MONTH:
        return []
    _v, n, reasons = part_sum(p, ids)
    return [f"{p.get('title') or 'The period'} excludes {_plural(n, 'fill')} with no figure", *reasons[:8]] if n else []


def _columns(p: dict, group: str, mode: str, months: Sequence[str] = ()) -> List[Tuple[str, str, str]]:
    cols = [("name", SLICE_TITLES.get(group, SLICE_TITLES[tf.GROUP_NONE]), "l")]
    if group == tf.GROUP_NONE:
        cols.append(("type", "Strategy", "l"))
    elif group == tf.GROUP_BY_CONTRACT:
        cols.append(("clearer", "Clearer", "l"))
    if mode == MODE_MONTH:
        cols += [(f"m:{m}", _month_title(m, months), "") for m in months] + [("mtotal", "Total", "")]
        return cols
    cols.append(("total", "P&L", ""))
    if p.get("ltd"):
        return cols + [(key, title, "") for key, title, _h in LTD_PARTS]
    show_other = any(abs(_num(x.get("other")) or 0.0) >= 0.005 for x in (p.get("by_trade") or {}).values())
    for key, title, _h in COMPONENTS:
        if key == "other" and not show_other:
            continue
        cols.append((key, title, ""))
    return cols


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


BLANK_WORDS = "A blank cell: nothing in this part over the period."
COLUMN_TIPS = {"total": "The period's P&L in USD: each fill's change by the header's own rule, summed.",
               "mtotal": "The row's months added up.",
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
NUMBER_KEYS = {"total", "open", "mtotal", *(k for k, _t, _h in COMPONENTS)}
NUMBER_HINT = "Each trade's own figure for the period, in USD."


def head(cols: Sequence[Tuple[str, str, str]], state: Optional[dict] = None,
         options: Optional[Dict[str, List[dict]]] = None) -> html.Thead:
    """The column heads, each with its funnel: the text columns the lists carried across the trade
    tabs, the figures one comparison (this tab's own)."""
    tips = {**COLUMN_TIPS, **{k: f"{h} {BLANK_WORDS}" for k, _t, h in COMPONENTS},
            "name": SLICE_TIPS.get(cols[0][1], "") if cols else "",
            **{k: f"{h} {BLANK_WORDS}" for k, _t, h in LTD_PARTS}}
    ths = []
    half = len(cols) // 2
    for i, (key, title, cls) in enumerate(cols):
        pop = None
        if key == "name":
            pop = tf.trade_funnel(TAB, state, options or {}, NAME_PARTS.get(title, ("trade",)), "name")
        elif key == "type":
            pop = tf.trade_funnel(TAB, state, options or {}, ("type",), "type")
        elif key in NUMBER_KEYS:
            pop = tf.number_funnel(TAB, state, key, hint=f"{NUMBER_HINT} {tf.NUMBER_HINT}")
        ths.append(tf.head_th(title, cls, tips.get(key) or "", pop=pop, right=i > half and i > 0))
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


def _cells(p: dict, b: dict, ids: Sequence[str], cols, mode: str, months: Sequence[str],
           kind: str = ROW_TRADE) -> List[html.Td]:
    full = kind == ROW_LEG
    badge = kind in (ROW_TOTAL, ROW_GROUP)
    if mode == MODE_MONTH:
        return _month_cells(b, ids, months, full, badge)
    fig = row_figures(p, ids)
    out = []
    for key, _t, _c in cols:
        if key in ("name", "type", "clearer"):
            continue
        if key == "total":
            out.append(_money_td(fig[key], full=full, badge=badge))
        else:   # a split part: the total row keeps its figure; elsewhere nothing in it is blank
            out.append(_money_td(fig[key], full=full, blank_zero=kind != ROW_TOTAL))
    return out


def _row_id(key: str) -> dict:
    return {"type": ROW_TYPE, "idx": key}


def leg_rows(p: dict, b: dict, t: dict, cols, mode, months) -> List[html.Tr]:
    """A trade's legs (hedges last) with the same columns, at full figures."""
    legs = sorted(t.get("legs") or [], key=lambda leg: (bool(leg.get("hedge")), str(leg.get("name") or "")))
    rows = []
    for leg in legs:
        ids = [str(i) for i in leg.get("trade_ids") or []]
        name = str(leg.get("name") or leg.get("contract_id") or "") + (" (hedge)" if leg.get("hedge") else "")
        first = [html.Td([name, row_info(_row_reasons(p, ids, mode))],
                         className="l tk-indent2" if len(cols) and cols[0][0] == "name"
                         and cols[0][1] != SLICE_TITLES[tf.GROUP_NONE] else "l tk-indent")]
        if any(k == "type" for k, _t, _c in cols):
            first.append(html.Td(""))
        rows.append(html.Tr(first + _cells(p, b, ids, cols, mode, months, ROW_LEG),
                            className="tk-leg" + (" tk-leg--hedge" if leg.get("hedge") else "")))
    return rows


def contract_table(b: dict, p: dict, s: dict, mode: str, opened: set, cols, months) -> html.Table:
    """The Contract slice: the total row (the same fills as every slice), one row per contract with
    its clearer, a click giving the trades holding it, each with its part of the contract's P&L."""
    items = contract_items(b, p, s, mode)
    ids_all = fills_of(shown_trades(b, p, s))
    filtered = tf.is_filtered(s, TAB)
    n_all = len(b.get("contracts") or [])
    label = (f"Filtered · {len(items)} of {_plural(n_all, 'contract')}" if filtered
             else f"Book · {_plural(n_all, 'contract')}")
    tip = ("equals the top bar's figure for the period" if not filtered and p.get("choice") in ("today", "mtd", "all")
           else None)
    body: List[Any] = [html.Tr([html.Td(html.Span(label, title=tip), className="l"), html.Td("")]
                               + _cells(p, b, ids_all, cols, mode, months, ROW_TOTAL), className="tk-total book-total")]
    for name, ids, r in items:
        key = f"c:{r['key']}"
        is_open = key in opened
        clearers, _h = clearer_words(r, ids)
        trades = [str(t.get("trade") or "") for t, leg in r["legs"] if set(leg.get("trade_ids") or []) & set(ids)]
        hover = _lines(f"{_plural(len(ids), 'fill')} in {_plural(len(trades), 'trade')}: {', '.join(trades)}",
                       "Only the fills of the trades showing" if len(ids) < len(r["trade_ids"]) else "")
        first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"),
                          html.Span(name, className="tk-name", title=plain_words(hover)),
                          row_info(_row_reasons(p, ids, mode))], className="l"),
                 html.Td(clearers or missing_cell("No account on file for these fills"), className="l")]
        body.append(html.Tr(first + _cells(p, b, ids, cols, mode, months), id=_row_id(key), n_clicks=0,
                            className="tk-row" + (" tk-row--open" if is_open else "")))
        if not is_open:
            continue
        idset = set(ids)
        subs = [(t, [str(i) for i in leg.get("trade_ids") or [] if str(i) in idset]) for t, leg in r["legs"]]
        for t, t_ids in sorted([x for x in subs if x[1]], key=lambda x: -abs(part_sum(p, x[1])[0] or 0.0)):
            t_clr, _h = clearer_words(r, t_ids)
            body.append(html.Tr([html.Td([str(t.get("trade") or ""), row_info(_row_reasons(p, t_ids, mode))],
                                         className="l tk-indent"),
                                 html.Td(t_clr, className="l")] + _cells(p, b, t_ids, cols, mode, months, ROW_LEG),
                                className="tk-leg"))
    return html.Table([head(cols, s, tf.options_for(b.get("trades") or [])), html.Tbody(body)], id=TABLE_ID,
                      className="book-table book-grid tk-table")


def table(b: dict, p: dict, state: Optional[dict], mode: str, opened: Sequence[str]) -> html.Table:
    s = tf.normal(state)
    group = s["group"]
    months = [m["month"] for m in (b["monthly"].months if b.get("monthly") is not None else [])] if mode == MODE_MONTH else []
    cols = _columns(p, group, mode, months)
    if group == tf.GROUP_BY_CONTRACT:
        return contract_table(b, p, s, mode, set(opened or []), cols, months)
    items = slices(b, p, s, mode)
    shown = [t for _v, ts in items for t in ts]
    ids_all = fills_of(shown)
    opened = set(opened or [])
    filtered = tf.is_filtered(s, TAB)
    named = [t for t in b.get("trades") or [] if not t.get("pseudo")]
    label = (f"Filtered · {len([t for t in shown if not t.get('pseudo')])} of {_plural(len(named), 'trade')}"
             if filtered
             else f"Book · {_plural(len(named), 'trade')}")
    total_first = [html.Td(html.Span(label, title=("equals the top bar's figure for the period" if not filtered
                                                   and p.get("choice") in ("today", "mtd", "all") else None)),
                           className="l")]
    if group == tf.GROUP_NONE:
        total_first.append(html.Td(""))
    body: List[Any] = [html.Tr(total_first + _cells(p, b, ids_all, cols, mode, months, ROW_TOTAL),
                               className="tk-total book-total")]
    for value, trades in items:
        ids = fills_of(trades)
        if group == tf.GROUP_NONE:
            t = trades[0]
            key = f"t:{value}"
            is_open = key in opened
            first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"), html.Span(value, className="tk-name"),
                              row_info(_row_reasons(p, ids, mode))], className="l"),
                     html.Td(tf.trade_type_label(t, short=True) if not t.get("pseudo") else "", className="l")]
            body.append(html.Tr(first + _cells(p, b, ids, cols, mode, months), id=_row_id(key), n_clicks=0,
                                className="tk-row" + (" tk-row--open" if is_open else "")))
            if is_open:
                body.extend(leg_rows(p, b, t, cols, mode, months))
            continue
        key = f"g:{value}"
        is_open = key in opened
        hover = _lines(*(str(t.get("trade")) for t in trades))
        first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"),
                          html.Span(value, className="tk-name", title=hover),
                          html.Span(f" · {_plural(len(trades), 'trade')}", className="tk-sub")], className="l")]
        body.append(html.Tr(first + _cells(p, b, ids, cols, mode, months, ROW_GROUP), id=_row_id(key), n_clicks=0,
                            className="tk-row" + (" tk-row--open" if is_open else "")))
        if not is_open:
            continue
        for t in sorted(trades, key=lambda t: -abs(part_sum(p, fills_of([t]))[0] or 0.0)):
            tkey = f"t:{t.get('trade')}"
            t_open = tkey in opened
            t_ids = fills_of([t])
            first = [html.Td([html.Span("▾ " if t_open else "▸ ", className="tk-chev"), str(t.get("trade")),
                              row_info(_row_reasons(p, t_ids, mode))], className="l tk-indent")]
            body.append(html.Tr(first + _cells(p, b, t_ids, cols, mode, months), id=_row_id(tkey),
                                n_clicks=0, className="tk-row tk-row--sub" + (" tk-row--open" if t_open else "")))
            if t_open:
                body.extend(leg_rows(p, b, t, cols, mode, months))
    return html.Table([head(cols, s, tf.options_for(b.get("trades") or [])), html.Tbody(body)], id=TABLE_ID,
                      className="book-table book-grid tk-table")


# --------------------------------------------------------------------------- the headline
def headline(b: dict, p: dict, state: Optional[dict]) -> Optional[html.Div]:
    """Only what neither the header nor the table's total row shows (layout wave 2, 2026-09-29): the
    close the period is measured from, and the rows' single best and worst trade. The count, the
    period's P&L and its split are the total row's; None when there is nothing to say."""
    s = tf.normal(state)
    shown = shown_trades(b, p, s)
    as_of = b.get("as_of")
    ref = p.get("ref_used") or p.get("start_ref")
    items: List[Any] = []
    if ref and not p.get("ltd"):
        items.append(("Vs close", day_text(ref, as_of),
                      _lines(f"The period's P&L is measured from the {ref} close", p.get("ref_note") or "")))
    end = p.get("end")
    if p.get("choice") == "custom" and end:
        items.append(("To", day_text(end, as_of), f"The period ends on the {end} close"))
    per_trade = [(t, part_sum(p, fills_of([t]))[0]) for t in shown if not t.get("pseudo")]
    per_trade = [(t, x) for t, x in per_trade if x is not None]
    best = max(per_trade, key=lambda x: x[1]) if per_trade else None
    worst = min(per_trade, key=lambda x: x[1]) if per_trade else None
    if best and best[1] >= NOTHING:
        items.append(("Best trade", html.Span([f"{best[0].get('trade')} ", km_cell(best[1])]),
                      "The rows' single best trade over the period"))
    if worst and worst[1] <= -NOTHING:
        items.append(("Worst trade", html.Span([f"{worst[0].get('trade')} ", km_cell(worst[1])]),
                      "The rows' single worst trade over the period"))
    return tf.headline(items) if items else None


# --------------------------------------------------------------------------- the chart
# Two panels on one business-day axis (2026-09-30, user: "this graph is complete shit"): on top the
# period's P&L to date as a line filled to zero, from 0 at the close it is measured from; below,
# smaller, each day's P&L as green / red bars. Only days with a close are on the axis (a category
# axis), so a weekend or a holiday leaves no gap and the line never slopes across one. Every figure
# is the engine's (`_period`'s days, `period_pnl` per day), summed over the rows showing (display).
def chart_points(b: dict, p: dict, state: Optional[dict]) -> dict:
    """{x, daily, cum, text, ref}: the chart's days (the reference close first, at 0, when the
    period is measured from one), each day's P&L and the period's P&L to date of the rows showing
    (None where no fill of theirs has a figure), and each day's hover."""
    s = tf.normal(state)
    items = chart_groups(b, p, s)
    trade_of = b.get("trade_of") or {}
    show = set().union(*(ids for _v, ids in items)) if items else set()
    days = p.get("days") or []
    xs: List[str] = []
    daily: List[Optional[float]] = []
    cum: List[Optional[float]] = []
    texts: List[str] = []
    ref = p.get("ref_used") or p.get("start_ref")
    if days and ref and not p.get("ltd") and ref < days[0]["date"]:
        xs.append(ref)
        daily.append(None)
        cum.append(0.0)
        texts.append("The close the period is measured from: 0")
    for d in days:
        vals = {t: v for t, v in d["by_trade"].items() if t in show}
        tot = sum(vals.values()) if vals else None
        so_far = [v for t, v in d["cum"].items() if t in show]
        xs.append(d["date"])
        daily.append(tot)
        cum.append(sum(so_far) if so_far else None)
        per: Dict[str, float] = {}
        for t, v in vals.items():
            name = trade_of.get(t, tf.UNASSIGNED)
            per[name] = per.get(name, 0.0) + v
        top = [x for x in sorted(per.items(), key=lambda x: -abs(x[1])) if abs(x[1]) >= NOTHING][:3]
        lines = [f"Day's P&L: {km_text(tot)}",
                 f"P&L to date: {km_text(cum[-1])}"]
        if top:
            lines += ["", "Biggest trades that day"] + [f"{n}: {km_text(v)}" for n, v in top]
        if d.get("filled"):
            lines.append(f"Filled {d['filled']}: valued at an earlier close (no price that day)")
        if d.get("n_excluded"):
            lines.append(f"Excl. {d['n_excluded']}: not priced on both closes, left out")
        texts.append("<br>".join(lines))
    return {"x": xs, "daily": daily, "cum": cum, "text": texts, "ref": 1 if len(xs) > len(days) else 0}


def _categories(days: Sequence[str]) -> List[str]:
    """Each day's value on the chart's axis, 'Tue 8 Sep' (the year added to a day of an earlier year
    than the last, 'Wed 31 Dec 2025'): the tick labels and the hover's date are this text itself,
    so the two always agree."""
    out = []
    try:
        end_year = dt.date.fromisoformat(days[-1]).year if days else 0
    except ValueError:
        end_year = 0
    for iso in days:
        try:
            d = dt.date.fromisoformat(iso)
        except ValueError:
            out.append(iso)
            continue
        out.append(f"{d:%a} {d.day} {d:%b}" + (f" {d.year}" if d.year != end_year else ""))
    return out


def _tick_days(days: Sequence[str]) -> List[int]:
    """The labelled days of a business-day axis (their positions): every day up to 16, else the
    first day of each week (up to 70 business days), else the first of each month; at most about
    12 labels either way."""
    try:
        dates = [dt.date.fromisoformat(x) for x in days]
    except ValueError:
        return list(range(len(days)))
    n = len(dates)
    if n <= 16:
        return list(range(n))
    if n <= 70:     # business days, not the calendar: a reference close months before still counts once
        idx = [i for i in range(n) if i == 0 or dates[i].isocalendar()[:2] != dates[i - 1].isocalendar()[:2]]
        idx = idx[::max(1, math.ceil(len(idx) / 10))]
    else:
        idx = [0] + [i for i in range(1, n) if (dates[i].year, dates[i].month) != (dates[i - 1].year, dates[i - 1].month)]
        idx = idx[::max(1, math.ceil(len(idx) / 12))]
    kept: List[int] = []
    for i in idx:               # never two labels side by side (a reference close weeks before the next day)
        if not kept or i - kept[-1] >= max(3, n // 20):
            kept.append(i)
    return kept


def chart_figure(b: dict, p: dict, state: Optional[dict], pts: Optional[dict] = None) -> dict:
    pts = pts or chart_points(b, p, state)
    days, daily, cum, texts = pts["x"], pts["daily"], pts["cum"], pts["text"]
    xs = _categories(days)
    n = len(xs)
    marks = n <= 31
    line = {"x": xs, "y": cum, "type": "scatter", "mode": "lines+markers" if marks else "lines",
            "name": "P&L to date", "xaxis": "x", "yaxis": "y", "fill": "tozeroy", "fillcolor": _FILL,
            "line": {"color": _NAVY, "width": 2}, "marker": {"size": 5 if n > 3 else 8, "color": _NAVY},
            "connectgaps": False, "text": texts, "hovertemplate": "%{text}<extra></extra>"}
    bars = {"x": xs, "y": daily, "type": "bar", "name": "Day's P&L", "xaxis": "x2", "yaxis": "y2",
            "marker": {"color": [_GREEN if (v or 0) >= 0 else _RED for v in daily], "line": {"width": 0}},
            "text": texts, "textposition": "none", "hovertemplate": "%{text}<extra></extra>"}
    tickvals = [xs[i] for i in _tick_days(days)]
    x_common = {"type": "category", "categoryorder": "array", "categoryarray": xs, "showgrid": False,
                "showline": False, "zeroline": False, "fixedrange": True,
                "showspikes": True, "spikemode": "across", "spikethickness": 1, "spikecolor": _ZERO,
                "spikedash": "dot", "spikesnap": "data"}
    y_common = {"tickformat": "(,.0f", "automargin": True, "gridcolor": _GRID, "gridwidth": 1,
                "zeroline": True, "zerolinecolor": _ZERO, "zerolinewidth": 1, "fixedrange": True,
                "tickfont": {"size": 11, "color": _MUTED}, "nticks": 5}
    def padded(values: Sequence[Optional[float]]) -> Optional[List[float]]:
        """The panel's range with zero in it and a tenth of room on each side: plotly snaps a bar or a
        fill to zero at the edge, where the zero line and the panel's label then collide."""
        known = [v for v in values if v is not None]
        if not known:
            return None
        lo, hi = min(0.0, *known), max(0.0, *known)
        pad = (hi - lo) * 0.1 or 1.0
        return [lo - pad, hi + pad]
    annotations = [
        {"text": "P&L to date", "xref": "paper", "yref": "paper", "x": 0, "y": 1.0, "xanchor": "left",
         "yanchor": "bottom", "showarrow": False, "font": {"size": 11, "color": _MUTED}},
        {"text": "Day's P&L", "xref": "paper", "yref": "paper", "x": 0, "y": 0.31, "xanchor": "left",
         "yanchor": "bottom", "showarrow": False, "font": {"size": 11, "color": _MUTED}},
    ]
    last = next((i for i in range(n - 1, -1, -1) if cum[i] is not None), None)
    if last is not None and last >= pts.get("ref", 0):
        v = cum[last]
        annotations.append({"text": f"<b>{km_text(v)}</b>", "x": xs[last], "y": v, "xref": "x", "yref": "y",
                            "xanchor": "right", "yanchor": "bottom" if v >= 0 else "top", "yshift": 4 if v >= 0 else -4,
                            "showarrow": False, "font": {"size": 12, "color": _GREEN if v >= 0 else _RED}})
    return {"data": [line, bars], "layout": {
        "height": CHART_HEIGHT, "margin": {"l": 12, "r": 16, "t": 22, "b": 30},
        "hovermode": "x", "showlegend": False, "bargap": 0.3 if n > 3 else 0.8,
        "hoverlabel": {"bgcolor": "#fff", "bordercolor": "#c9ced8", "align": "left",
                       "font": {"size": 12, "color": "#1f2937"}},
        "xaxis": {**x_common, "anchor": "y", "matches": "x2", "showticklabels": False},
        "yaxis": {**y_common, "domain": [0.38, 1.0], **({"range": padded(cum)} if padded(cum) else {})},
        "xaxis2": {**x_common, "anchor": "y2", "tickmode": "array", "tickvals": tickvals,
                   "tickangle": 0, "tickfont": {"size": 11, "color": _MUTED}},
        "yaxis2": {**y_common, "domain": [0.0, 0.28], "nticks": 4, **({"range": padded(daily)} if padded(daily) else {})},
        "annotations": annotations,
        "font": {"family": "Inter, system-ui, sans-serif", "size": 12, "color": "#1f2937"},
        "plot_bgcolor": "#fff", "paper_bgcolor": "#fff"}}


def _day_label(iso: str) -> str:
    try:
        return f"{dt.date.fromisoformat(iso):%a %d %b}"
    except ValueError:
        return iso


CHART_TITLE = "P&L over the period"
CHART_WORDS = ("Top: the period's P&L to date of the rows showing, from 0 at the close it is measured from; it "
               "ends at the table's total. Bottom: each business day's P&L, by the header's own Daily rule. Only "
               "days with a close are on the axis. Hover a day for its figures and its biggest trades.")


def _chart_note(text: str) -> dict:
    """An empty chart frame holding one centred sentence: the chart's own card and title, never a
    loose line."""
    return {"data": [], "layout": {
        "height": 120, "margin": {"l": 16, "r": 16, "t": 8, "b": 8}, "xaxis": {"visible": False},
        "yaxis": {"visible": False}, "plot_bgcolor": "#fff", "paper_bgcolor": "#fff",
        "annotations": [{"text": text, "showarrow": False, "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5,
                         "font": {"size": 13, "color": "#6b7280"}}]}}


def chart(b: dict, p: dict, state: Optional[dict]) -> Any:
    head = html.Div(about(CHART_TITLE, CHART_WORDS, level="span", className="book-section-title"),
                    className="card-head")
    note = None
    pts = None
    if not p.get("days"):
        note = cap(p.get("reason") or "No business day in the period: nothing to chart")
    else:
        pts = chart_points(b, p, state)
        k = pts["ref"]      # the reference close's 0 is not a figure of the period
        ys = [v for v in pts["daily"][k:] + pts["cum"][k:] if v is not None]
        if not ys:
            note = ("No day of the period has a P&L figure for the rows showing"
                    + (": the figures fill in after a Bloomberg pull"
                       if not p.get("available") or not p.get("by_trade") else ""))
        elif all(abs(v) < NOTHING for v in ys):
            days = p.get("days") or []
            span = (_day_label(days[0]["date"]) if len(days) == 1
                    else f"{_day_label(days[0]['date'])} to {_day_label(days[-1]['date'])}")
            note = f"Nothing moved: the rows showing made 0 on {span}"
    if note is not None:
        graph = dcc.Graph(figure=_chart_note(cap(plain_words(note))), config={"displayModeBar": False, "staticPlot": True})
        return html.Div([head, graph])
    fig = chart_figure(b, p, state, pts)
    return html.Div([head, dcc.Graph(figure=fig, config={"displayModeBar": False}, className="pnl-chart-graph")])


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
def issue_items(b: dict, p: dict) -> List[Any]:
    items: List[Any] = list(b.get("errors") or [])
    trade_of = b.get("trade_of") or {}
    title = p.get("title") or "Period"

    def where(tid: Any) -> str:
        trade = trade_of.get(str(tid), "")
        return f"{trade} · Fill {tid}" if trade else f"Fill {tid}"

    if p.get("reason") and not p.get("available"):
        items.append((title, "Whole book", p["reason"]))
    for tid, why in p.get("excluded") or []:
        items.append((title, where(tid), str(why)))
    for o in p.get("other_trades") or []:
        items.append(("Not split", where(o.get("trade_id")),
                      f"{full_signed(o.get('amount'))} USD not split: {o.get('why')}"))
    for d, why in p.get("days_left") or []:
        items.append(("Chart", day_text(d), why))
    return items


def csv_frame(b: dict, p: dict, state: Optional[dict], mode: str) -> pd.DataFrame:
    s = tf.normal(state)
    rows = []
    months = [m["month"] for m in (b["monthly"].months if b.get("monthly") is not None else [])]
    mp = b.get("monthly")
    if s["group"] == tf.GROUP_BY_CONTRACT:
        for name, ids, r in contract_items(b, p, s, mode):
            idset = set(ids)
            for t, leg in r["legs"]:
                t_ids = [str(i) for i in leg.get("trade_ids") or [] if str(i) in idset]
                if not t_ids:
                    continue
                row = {"Slice": name, "Contract key": r["key"], "Clearer": clearer_words(r, t_ids)[0],
                       "Trade": t.get("trade"), "Strategy": tf.trade_type_label(t), "Commodity": r["family"],
                       "Period": p.get("title"), "From close": p.get("ref_used") or p.get("start_ref"), "To": p.get("end")}
                fig = row_figures(p, t_ids)
                row["P&L"] = fig["total"][0]
                for key, title, _h in COMPONENTS:
                    row[title] = fig[key][0]
                if p.get("ltd"):
                    row["Open"] = fig["open"][0]
                row["Fills excluded"] = fig["total"][1]
                if mode == MODE_MONTH and mp is not None:
                    fr = mp.by_trade
                    for m in months:
                        sub = fr[(fr["month"] == m) & fr["trade_id"].astype(str).isin(t_ids) & fr["included"]]
                        row[m] = float(sub["pnl_usd"].sum()) if not sub.empty else None
                rows.append(row)
        return pd.DataFrame(rows)
    for value, trades in slices(b, p, s, mode):
        for t in trades:
            for leg in t.get("legs") or []:
                ids = [str(i) for i in leg.get("trade_ids") or []]
                row = {"Slice": value, "Trade": t.get("trade"), "Strategy": tf.trade_type_label(t),
                       "Commodity": tf.family_label(t), "Leg": leg.get("name"), "Hedge": bool(leg.get("hedge")),
                       "Period": p.get("title"), "From close": p.get("ref_used") or p.get("start_ref"),
                       "To": p.get("end")}
                fig = row_figures(p, ids)
                row["P&L"] = fig["total"][0]
                for key, title, _h in COMPONENTS:
                    row[title] = fig[key][0]
                if p.get("ltd"):
                    row["Open"] = fig["open"][0]
                row["Fills excluded"] = fig["total"][1]
                if mode == MODE_MONTH and mp is not None:
                    fr = mp.by_trade
                    for m in months:
                        sub = fr[(fr["month"] == m) & fr["trade_id"].astype(str).isin(ids) & fr["included"]]
                        row[m] = float(sub["pnl_usd"].sum()) if not sub.empty else None
                rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- render
def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render_parts(as_of: Optional[str], db_path, state: Optional[dict] = None, choice: str = DEFAULT_PERIOD,
                 start: Optional[str] = None, end: Optional[str] = None, mode: str = MODE_TOTAL,
                 opened: Sequence[str] = ()) -> dict:
    out = {"body": None, "shown": False, "headline": None, "chart": None, "table": None, "track": None, "foot": None}
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
        p = period(conn, as_of, choice or DEFAULT_PERIOD, start, end)
        mode = mode if mode in (MODE_TOTAL, MODE_MONTH) else MODE_TOTAL
        out.update(shown=True, headline=headline(b, p, state), chart=chart(b, p, state),
                   table=table(b, p, state, mode, opened), track=track_block(b, as_of),
                   foot=issues_drawer(issue_items(b, p)))
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("P&L tab could not be built for %s", as_of)
        out["body"] = html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The P&L could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    from ui.tabs.book import _tidy_parts
    return _tidy_parts(out)


def render(as_of: Optional[str], db_path, state: Optional[dict] = None, choice: str = DEFAULT_PERIOD,
           mode: str = MODE_TOTAL) -> html.Div:
    """The whole body for a direct render."""
    p = render_parts(as_of, db_path, state, choice, mode=mode)
    if not p["shown"]:
        return html.Div([p["body"]])
    return html.Div([p["headline"], p["chart"], html.Div(p["table"], className="book-card book-main tk-card"),
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
                html.Span("Period", className="tk-k"),
                _switch(PERIOD_ID, PERIOD_CHOICES, DEFAULT_PERIOD),
                html.Span(id=CUSTOM_WRAP_ID, style=HIDDEN, children=[
                    dcc.DatePickerRange(id=CUSTOM_ID, display_format="D MMM YYYY", persistence=True,
                                        persistence_type="session", clearable=True, className="tk-range")]),
                html.Span("Table", className="tk-k"),
                _switch(MODE_ID, ((MODE_TOTAL, "Total"), (MODE_MONTH, "By month")), MODE_TOTAL),
                tf.bar_slot(TAB),
                html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                            title="Every leg of the rows showing with its split (and months), at full figures"),
                dcc.Download(id=DOWNLOAD_ID)]),
            html.Div(id=HEADLINE_ID),
            html.Div(id=CHART_ID, className="book-card card-pad pnl-chart-card"),
            html.Div(html.Div(id=TABLE_SLOT_ID, className="tk-table-slot"), className="book-card book-main tk-card"),
            html.Div(id=f"{TRACK_ID}-slot"),
            html.Div(id=FOOT_ID),
        ]),
        dcc.Store(id=OPEN_STORE_ID, storage_type="session"),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    tf.register_bar(app, TAB, get_db_path, options_of)

    @app.callback(Output(CUSTOM_WRAP_ID, "style"), Input(PERIOD_ID, "value"))
    def _custom_shown(choice):
        return {} if choice == "custom" else HIDDEN

    @app.callback(
        Output(BODY_ID, "children"), Output(CONTENT_ID, "style"), Output(HEADLINE_ID, "children"),
        Output(CHART_ID, "children"), Output(TABLE_SLOT_ID, "children"), Output(f"{TRACK_ID}-slot", "children"),
        Output(FOOT_ID, "children"),
        Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(tf.STORE_ID, "data"),
        Input(PERIOD_ID, "value"), Input(CUSTOM_ID, "start_date"), Input(CUSTOM_ID, "end_date"),
        Input(MODE_ID, "value"), Input(OPEN_STORE_ID, "data"),
    )
    def _render(as_of, _rev, state, choice, start, end, mode, opened):
        p = render_parts(as_of, get_db_path(), state, choice or DEFAULT_PERIOD, start, end, mode or MODE_TOTAL,
                         opened or [])
        if not p["shown"]:
            return p["body"], HIDDEN, None, None, None, None, None
        return (None, {}, compact(p["headline"]), p["chart"], compact(p["table"]), compact(p["track"]),
                compact(p["foot"]))

    @app.callback(Output(OPEN_STORE_ID, "data"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  State(OPEN_STORE_ID, "data"), prevent_initial_call=True)
    def _toggle(_clicks, current):
        trig = dash.ctx.triggered_id
        hit = dash.ctx.triggered or []
        if not isinstance(trig, dict) or not (hit and hit[0].get("value")):
            return dash.no_update
        key = str(trig.get("idx") or "")
        cur = list(current or [])
        return [x for x in cur if x != key] if key in cur else cur + [key]

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(tf.STORE_ID, "data"), State(PERIOD_ID, "value"), State(CUSTOM_ID, "start_date"),
                  State(CUSTOM_ID, "end_date"), State(MODE_ID, "value"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, state, choice, start, end, mode):
        if not n_clicks or not as_of:
            return dash.no_update
        conn = _open(get_db_path())
        try:
            b = base(conn, as_of)
            p = period(conn, as_of, choice or DEFAULT_PERIOD, start, end)
            frame = csv_frame(b, p, state, mode or MODE_TOTAL)
        finally:
            conn.close()
        return dcc.send_data_frame(frame.to_csv, f"pnl_{choice}_{as_of}.csv", index=False)
