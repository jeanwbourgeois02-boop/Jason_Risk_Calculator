"""P&L tab: "What did I make, and why?" (Phase G, 2026-09-29; the design doc's "Table specs", P&L).

The trade is the unit, as on the Book: the shared filter bar and its switch (`ui.tabs.trade_filter`;
here the switch slices the table by Trade (default) | Type | Commodity, whole trades only, never a
trade split), then the headline of the rows showing, then:
  - the controls: Period Today | 5d | MTD (default) | YTD | All | Custom (two dates), and Total |
    By month;
  - the chart: the period's business days, the daily P&L of the rows showing as bars (one colour per
    slice value when sliced by Type or Commodity with 6 values or fewer) and the cumulative P&L of
    the period from 0 as a line; a day's hover gives its P&L, the cumulative and the top 3 trades;
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
    pct_text, plain_words, sign_class, sum_known, cap, format_cell,
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
CHART_HEIGHT = 300
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
_PALETTE = ("#0f1f3d", "#c9a227", "#2e7d32", "#6a1b9a", "#00838f", "#ef6c00")
LTD_SPLIT_WORDS = "All has no reference close to split against: realised and open only"


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
    backfill = header.backfill_status(conn)
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
    from ui.tabs.blotter_pricing import research_inputs_key
    return _memo("base", conn, as_of, lambda: _base(conn, as_of), extra=research_inputs_key())


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


def slices(b: dict, p: dict, state: Optional[dict], mode: str = MODE_TOTAL) -> List[Tuple[str, List[dict]]]:
    """[(slice value, its trades)] of the rows showing, sorted by the size of the period's P&L (By
    month: of the months' total)."""
    s = tf.normal(state)
    shown = tf.apply(b.get("trades") or [], s)
    by: Dict[str, List[dict]] = {}
    for t in shown:
        by.setdefault(_slice_of(t, s["group"]), []).append(t)

    def size(item):
        ids = fills_of(item[1])
        v = month_total(b, ids) if mode == MODE_MONTH else part_sum(p, ids)[0]
        return (v is None, -abs(v or 0.0), item[0])
    return sorted(by.items(), key=size)


# --------------------------------------------------------------------------- the table
def _money_td(fig: Tuple[Optional[float], int, List[str]], hover: str = "", full: bool = False) -> html.Td:
    v, n, reasons = fig
    if v is None:
        return html.Td(missing_cell(_lines(*reasons[:8]) or "no figure"))
    m = marker(f"excl. {n}", _lines(f"excludes {_plural(n, 'fill')}", *reasons[:8]), "marker--small") if n else None
    if full:
        return html.Td([html.Span(full_signed(v), className=sign_class(v) or None, title=plain_words(hover) or None), m])
    return html.Td([km_cell(v, hover=hover), m])


def _columns(p: dict, group: str, mode: str, months: Sequence[str] = ()) -> List[Tuple[str, str, str]]:
    first = {tf.GROUP_NONE: "Trade", tf.GROUP_BY_TYPE: "Type", tf.GROUP_BY_COMMODITY: "Commodity"}[group]
    cols = [("name", first, "l")]
    if group == tf.GROUP_NONE:
        cols.append(("type", "Type", "l"))
    if mode == MODE_MONTH:
        cols += [(f"m:{m}", _month_title(m, months), "") for m in months] + [("mtotal", "Total", "")]
        return cols
    cols.append(("total", "P&L", ""))
    if p.get("ltd"):
        return cols + [(key, title, "") for key, title, _h in LTD_PARTS] + [("share", "% of total", "")]
    show_other = any(abs(_num(x.get("other")) or 0.0) >= 0.005 for x in (p.get("by_trade") or {}).values())
    for key, title, _h in COMPONENTS:
        if key == "other" and not show_other:
            continue
        cols.append((key, title, ""))
    cols.append(("share", "% of total", ""))
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


COLUMN_TIPS = {"total": "The period's P&L in USD: each fill's change by the header's own rule, summed.",
               "share": "The row's share of the total, shown only when the two have the same sign.",
               "mtotal": "The row's months added up.", "type": "The trade's type by rule, as on the Book."}


def head(cols: Sequence[Tuple[str, str, str]]) -> html.Thead:
    tips = {**COLUMN_TIPS, **{k: h for k, _t, h in COMPONENTS}, **{k: h for k, _t, h in LTD_PARTS}}
    return html.Thead(html.Tr([html.Th(title, className=cls or None, title=tips.get(key)) for key, title, cls in cols]))


def _month_cells(b: dict, ids: Sequence[str], months: Sequence[str], full: bool = False) -> List[html.Td]:
    mp = b.get("monthly")
    frame = mp.by_trade if mp is not None else pd.DataFrame()
    cells, vals = [], []
    idset = set(ids)
    for m in months:
        sub = frame[(frame["month"] == m) & frame["trade_id"].astype(str).isin(idset)] if not frame.empty else frame
        pairs = [(_num(v) if inc else None, f"{t}: {r or 'left out'}")
                 for t, v, inc, r in zip(sub.get("trade_id", []), sub.get("pnl_usd", []), sub.get("included", []),
                                         sub.get("reason", []))]
        fig = sum_known(pairs) if pairs else (None, 0, ["no fill of these on file that month"])
        vals.append((fig[0], ""))
        cells.append(_money_td(fig, full=full))
    total = sum_known(vals)
    cells.append(_money_td((total[0], 0, ["no month with a figure"]), hover="the months added up", full=full))
    return cells


SMALL_TOTAL = 0.01     # a total under 1 % of the rows' gross P&L: no share against it


def _cells(p: dict, b: dict, ids: Sequence[str], cols, grand, mode: str, months: Sequence[str],
           full: bool = False) -> List[html.Td]:
    """`grand`: (the total of the rows showing, their gross P&L: the rows' |P&L| added)."""
    if mode == MODE_MONTH:
        return _month_cells(b, ids, months, full)
    fig = row_figures(p, ids)
    total, gross = grand if isinstance(grand, tuple) else (grand, None)
    out = []
    for key, _t, _c in cols:
        if key in ("name", "type"):
            continue
        if key == "share":
            v = fig["total"][0]
            if full or v is None or not v or not total or (v > 0) != (total > 0):
                out.append(html.Td(""))
            elif gross and abs(total) < SMALL_TOTAL * gross:
                out.append(html.Td(html.Span(MISSING, className="tk-sub", title=(
                    f"Not shown: the total, {full_signed(total)} USD, is under 1 % of the rows' gross P&L "
                    f"({format_cell(gross)} USD), so a share of it says nothing"))))
            else:
                out.append(html.Td(pct_text(v / total)))
            continue
        out.append(_money_td(fig[key], full=full))
    return out


def _row_id(key: str) -> dict:
    return {"type": ROW_TYPE, "idx": key}


def leg_rows(p: dict, b: dict, t: dict, cols, grand, mode, months) -> List[html.Tr]:
    """A trade's legs (hedges last) with the same columns, at full figures."""
    legs = sorted(t.get("legs") or [], key=lambda leg: (bool(leg.get("hedge")), str(leg.get("name") or "")))
    rows = []
    for leg in legs:
        ids = [str(i) for i in leg.get("trade_ids") or []]
        name = str(leg.get("name") or leg.get("contract_id") or "") + (" (hedge)" if leg.get("hedge") else "")
        first = [html.Td(name, className="l tk-indent2" if len(cols) and cols[0][1] != "Trade" else "l tk-indent")]
        if any(k == "type" for k, _t, _c in cols):
            first.append(html.Td(""))
        rows.append(html.Tr(first + _cells(p, b, ids, cols, grand, mode, months, full=True),
                            className="tk-leg" + (" tk-leg--hedge" if leg.get("hedge") else "")))
    return rows


def table(b: dict, p: dict, state: Optional[dict], mode: str, opened: Sequence[str]) -> html.Table:
    s = tf.normal(state)
    group = s["group"]
    months = [m["month"] for m in (b["monthly"].months if b.get("monthly") is not None else [])] if mode == MODE_MONTH else []
    cols = _columns(p, group, mode, months)
    items = slices(b, p, s, mode)
    shown = [t for _v, ts in items for t in ts]
    ids_all = fills_of(shown)
    grand = (part_sum(p, ids_all)[0], sum(abs(part_sum(p, fills_of(ts))[0] or 0.0) for _v, ts in items))
    opened = set(opened or [])
    filtered = tf.is_filtered(s)
    named = [t for t in b.get("trades") or [] if not t.get("pseudo")]
    label = (f"Filtered · {len([t for t in shown if not t.get('pseudo')])} of {len(named)}" if filtered
             else f"Book · {_plural(len(named), 'trade')}")
    total_first = [html.Td(html.Span(label, title=("equals the top bar's figure for the period" if not filtered
                                                   and p.get("choice") in ("today", "mtd", "all") else None)),
                           className="l")]
    if group == tf.GROUP_NONE:
        total_first.append(html.Td(""))
    body: List[Any] = [html.Tr(total_first + _cells(p, b, ids_all, cols, grand, mode, months),
                               className="tk-total book-total")]
    for value, trades in items:
        ids = fills_of(trades)
        if group == tf.GROUP_NONE:
            t = trades[0]
            key = f"t:{value}"
            is_open = key in opened
            first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"), html.Span(value, className="tk-name")],
                             className="l"),
                     html.Td(tf.trade_type_label(t, short=True) if not t.get("pseudo") else "", className="l")]
            body.append(html.Tr(first + _cells(p, b, ids, cols, grand, mode, months), id=_row_id(key), n_clicks=0,
                                className="tk-row" + (" tk-row--open" if is_open else "")))
            if is_open:
                body.extend(leg_rows(p, b, t, cols, grand, mode, months))
            continue
        key = f"g:{value}"
        is_open = key in opened
        hover = _lines(*(str(t.get("trade")) for t in trades))
        first = [html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"),
                          html.Span(value, className="tk-name", title=hover),
                          html.Span(f" · {_plural(len(trades), 'trade')}", className="tk-sub")], className="l")]
        body.append(html.Tr(first + _cells(p, b, ids, cols, grand, mode, months), id=_row_id(key), n_clicks=0,
                            className="tk-row" + (" tk-row--open" if is_open else "")))
        if not is_open:
            continue
        for t in sorted(trades, key=lambda t: -abs(part_sum(p, fills_of([t]))[0] or 0.0)):
            tkey = f"t:{t.get('trade')}"
            t_open = tkey in opened
            first = [html.Td([html.Span("▾ " if t_open else "▸ ", className="tk-chev"), str(t.get("trade"))],
                             className="l tk-indent")]
            body.append(html.Tr(first + _cells(p, b, fills_of([t]), cols, grand, mode, months), id=_row_id(tkey),
                                n_clicks=0, className="tk-row tk-row--sub" + (" tk-row--open" if t_open else "")))
            if t_open:
                body.extend(leg_rows(p, b, t, cols, grand, mode, months))
    return html.Table([head(cols), html.Tbody(body)], id=TABLE_ID, className="book-table book-grid tk-table")


# --------------------------------------------------------------------------- the headline
def headline(b: dict, p: dict, state: Optional[dict]) -> html.Div:
    s = tf.normal(state)
    shown = tf.apply(b.get("trades") or [], s)
    named = [t for t in b.get("trades") or [] if not t.get("pseudo")]
    ids = fills_of(shown)
    fig = row_figures(p, ids)
    count = (f"{len([t for t in shown if not t.get('pseudo')])} of {len(named)}" if tf.is_filtered(s)
             else _plural(len(named), "trade"))
    v, n, r = fig["total"]
    total = html.Span([km_cell(v, reason=_lines(p.get("reason"), *r[:6])),
                       marker(f"excl. {n}", _lines(*r[:8]), "marker--small") if n and v is not None else None])
    if p.get("ltd"):
        parts: Any = html.Span(["Realised ", km_cell(fig["realised"][0]), " · open ", km_cell(fig["open"][0])],
                               title=LTD_SPLIT_WORDS)
    else:
        parts = html.Span([x for k, w in (("spread", "spread"), ("fx", "FX"), ("hedge", "hedge"))
                           for x in (f"{w} ", km_cell(fig[k][0]), " ")])
    per_trade = [(t, part_sum(p, fills_of([t]))[0]) for t in shown]
    per_trade = [(t, x) for t, x in per_trade if x is not None]
    best = max(per_trade, key=lambda x: x[1]) if per_trade else None
    worst = min(per_trade, key=lambda x: x[1]) if per_trade else None
    bw: Any = MISSING
    if best:
        bw = html.Span([f"{best[0].get('trade')} ", km_cell(best[1]), " · ", f"{worst[0].get('trade')} ",
                        km_cell(worst[1])])
    ref = p.get("ref_used") or p.get("start_ref")
    return tf.headline([
        ("Trades", count, "the trades showing; the top bar is always the whole book"),
        (f"P&L {p.get('title') or ''}".strip(), total,
         _lines(f"from the {ref} close" if ref else "since each trade opened", p.get("ref_note") or "")),
        ("Of which", parts, "the split of the rows showing (the rest is new trades, realised and other)"),
        ("Best · worst trade", bw, "the rows' single best and worst trade over the period"),
    ])


# --------------------------------------------------------------------------- the chart
def chart_figure(b: dict, p: dict, state: Optional[dict]) -> dict:
    s = tf.normal(state)
    items = slices(b, p, s)
    trade_of = b.get("trade_of") or {}
    show = set(fills_of([t for _v, ts in items for t in ts]))
    days = p.get("days") or []
    xs = [d["date"] for d in days]
    traces: List[dict] = []
    daily_tot, cum_tot, texts = [], [], []
    for d in days:
        vals = {t: v for t, v in d["by_trade"].items() if t in show}
        tot = sum(vals.values()) if vals else None
        cum = [v for t, v in d["cum"].items() if t in show]
        daily_tot.append(tot)
        cum_tot.append(sum(cum) if cum else None)
        per: Dict[str, float] = {}
        for t, v in vals.items():
            per[trade_of.get(t, tf.UNASSIGNED)] = per.get(trade_of.get(t, tf.UNASSIGNED), 0.0) + v
        top = sorted(per.items(), key=lambda x: -abs(x[1]))[:3]
        texts.append("<br>".join([f"P&L {km_text(tot)} · cumulative {km_text(cum_tot[-1])}"]
                                 + [f"{n} {km_text(v)}" for n, v in top]
                                 + ([f"filled {d['filled']}"] if d.get("filled") else [])
                                 + ([f"excl. {d['n_excluded']}"] if d.get("n_excluded") else [])))
    group = s["group"]
    if group != tf.GROUP_NONE and 1 < len(items) <= 6:
        for n, (value, trades) in enumerate(items):
            ids = set(fills_of(trades))
            ys = [sum(v for t, v in d["by_trade"].items() if t in ids) for d in days]
            traces.append({"x": xs, "y": ys, "type": "bar", "name": value, "marker": {"color": _PALETTE[n % 6]},
                           "hovertemplate": f"{value} " + "%{y:$,.0f}<extra></extra>"})
    else:
        traces.append({"x": xs, "y": daily_tot, "type": "bar", "name": "Daily P&L",
                       "marker": {"color": ["#1a7f4b" if (v or 0) >= 0 else "#c0392b" for v in daily_tot]},
                       "hovertemplate": "Daily %{y:$,.0f}<extra></extra>"})
    traces.append({"x": xs, "y": cum_tot, "type": "scatter", "mode": "lines+markers" if len(xs) < 3 else "lines",
                   "name": "Cumulative", "line": {"color": "#0f1f3d", "width": 2}, "text": texts, "yaxis": "y",
                   "hovertemplate": "%{x|%a %d %b %Y}<br>%{text}<extra></extra>"})
    return {"data": traces, "layout": {
        "height": CHART_HEIGHT, "barmode": "relative", "margin": {"l": 56, "r": 16, "t": 24, "b": 28},
        "hovermode": "x unified", "showlegend": group != tf.GROUP_NONE and 1 < len(items) <= 6,
        "legend": {"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0, "font": {"size": 11}},
        "xaxis": {"type": "date", "tickformat": "%d %b"},
        "yaxis": {"tickformat": "~s", "zeroline": True, "zerolinecolor": "#c9ced8"},
        "plot_bgcolor": "#fff", "paper_bgcolor": "#fff"}}


def chart(b: dict, p: dict, state: Optional[dict]) -> Any:
    if not p.get("days"):
        return html.P(cap(p.get("reason") or "No business day in the period: nothing to chart."), className="book-quiet")
    fig = chart_figure(b, p, state)
    if not any(v is not None for trace in fig["data"] for v in trace.get("y") or []):
        why = [str(w) for _t, w in (p.get("excluded") or [])][:1]
        return html.P(cap(_lines(
            "No day of the period has a P&L figure for the rows showing, so there is nothing to chart"
            + (": no trade has a price on these days yet (the figures fill in after a Bloomberg pull)"
               if not p.get("available") or not p.get("by_trade") else ""),
            *(f"For example: {w}" for w in why))), className="book-quiet", title=plain_words(p.get("reason") or "") or None)
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


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


def track_block(b: dict, as_of: str) -> html.Details:
    tr = b.get("track")
    items: List[Any] = []

    def item(k: str, v: Any, hover: str = "") -> None:
        items.append(html.Span([html.Span(k, className="pnl-track-k"), v], className="pnl-track-item",
                               title=plain_words(hover) or None))

    if tr is None:
        items.append(missing_cell("the track record could not be built"))
    elif not tr.get("n_days"):
        item("Days counted", "none yet", "no business day with a Daily figure yet")
    else:
        best, worst = tr.get("best_day") or {}, tr.get("worst_day") or {}
        item("Best day", html.Span([km_cell(best.get("value")), f" {day_text(best.get('date'), as_of)}"]))
        item("Worst day", html.Span([km_cell(worst.get("value")), f" {day_text(worst.get('date'), as_of)}"]))
        share = tr.get("share_positive")
        item("Days up", f"{pct_text(share)} ({tr['n_positive']} of {tr['n_days']})" if share is not None else MISSING)
        dd = tr.get("max_drawdown") or {}
        if (dd.get("value") or 0.0) < -0.005:
            item("Largest fall", html.Span([km_cell(dd["value"]), f" {day_text(dd.get('peak_date'), as_of)} → "
                                                                   f"{day_text(dd.get('trough_date'), as_of)}"]))
        else:
            item("Largest fall", "none", "the LTD has never been below an earlier peak")
        fp = tr.get("from_peak") or {}
        if fp:
            item("From the peak", html.Span([km_cell(fp.get("value")), f" peak {day_text(fp.get('peak_date'), as_of)}"]))
    sc = b.get("scorecard") or {}
    closed = (sc.get("summary") or {}).get("closed") or {}
    if closed.get("count"):
        item("Win rate", pct_text(closed.get("win_rate")), f"over {_plural(closed['count'], 'closed trade')}")
        item("Average win / loss", html.Span([km_cell(closed.get("avg_win")), " / ", km_cell(closed.get("avg_loss"))]))
        payoff = _num(closed.get("payoff_ratio"))
        item("Payoff", f"{payoff:.2f}" if payoff is not None else MISSING, "average win over average loss")
        item("Holding days, winners / losers", hold_text(closed),
             _lines("median holding days of the closed winners / losers (calendar days from the first fill to "
                    "the day the trade went flat)", hold_hover(closed)))
    else:
        item("Closed trades", "none yet", "the win rate needs a closed trade")
    type_table = by_type_table(sc)
    return html.Details([html.Summary(about("Track record", "The whole book since the first trade: each day by the "
                                                            "header's own Daily rule; win rate over the closed trades.",
                                            level="span")),
                         html.Div(items, className="pnl-track"), type_table], className="book-fold tk-fold-block",
                        id=TRACK_ID)


# --------------------------------------------------------------------------- issues, CSV
def issue_items(b: dict, p: dict) -> List[Any]:
    items: List[Any] = list(b.get("errors") or [])
    trade_of = b.get("trade_of") or {}
    if p.get("reason") and not p.get("available"):
        items.append(("Period", p["reason"]))
    for tid, why in p.get("excluded") or []:
        items.append((f"{trade_of.get(str(tid), '')} {tid}".strip(), str(why)))
    for o in p.get("other_trades") or []:
        items.append((f"Other {o.get('trade_id')}", f"{full_signed(o.get('amount'))} USD not split: {o.get('why')}"))
    for d, why in p.get("days_left") or []:
        items.append((f"Chart {d}", why))
    return items


def csv_frame(b: dict, p: dict, state: Optional[dict], mode: str) -> pd.DataFrame:
    s = tf.normal(state)
    rows = []
    months = [m["month"] for m in (b["monthly"].months if b.get("monthly") is not None else [])]
    mp = b.get("monthly")
    for value, trades in slices(b, p, s, mode):
        for t in trades:
            for leg in t.get("legs") or []:
                ids = [str(i) for i in leg.get("trade_ids") or []]
                row = {"Slice": value, "Trade": t.get("trade"), "Type": tf.trade_type_label(t),
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
        html.Div(id=BODY_ID, children=[message_box("Loading the P&L...")]),
        html.Div(id=CONTENT_ID, style=HIDDEN, children=[
            tf.bar_slot(TAB),
            html.Div(className="tk-strip tk-strip--controls", children=[
                html.Span("Period", className="tk-k"),
                _switch(PERIOD_ID, PERIOD_CHOICES, DEFAULT_PERIOD),
                html.Span(id=CUSTOM_WRAP_ID, style=HIDDEN, children=[
                    dcc.DatePickerRange(id=CUSTOM_ID, display_format="D MMM YYYY", persistence=True,
                                        persistence_type="session", clearable=True, className="tk-range")]),
                html.Span("Table", className="tk-k"),
                _switch(MODE_ID, ((MODE_TOTAL, "Total"), (MODE_MONTH, "By month")), MODE_TOTAL),
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
