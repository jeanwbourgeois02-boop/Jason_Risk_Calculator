"""P&L tab: "How did the P&L get here, and what drove it?" (rebuilt 2026-09-29, layout approved
by the user that day; the attribution table left: the Book gives the cross-section).

The Book is today's cross-section; this tab is the history and the explanation. Every figure is
the engine's, as given; the tab sums known figures of one unit, ranks and formats, never prices:
  - `engine.pnl.series.daily_series`: one filled valuation per business day (the screens' fill,
    memoised in process on the database revision), its `chart_points()` (the LTD line, the
    header's chart too), `track_record` (the daily P&L bars and the record line) and
    `monthly_pnl` (month by month, the current month = MTD);
  - `engine.spreads.period_explain`: the chosen period's total (the header's figure) split into
    spread, FX, hedge, new trades, realised and other (LTD: realised and open), and the same per
    position of the Book (`by_position`, the Book's own position ids).
Any as-of is valued as the header values it (a weekend's too). One `daily_series` and one `book_spreads` (the Book's memoised `_spreads`) are passed to
everything in a render, so the tab prices nothing twice.

Layout, top to bottom: the title line (the period switch Daily | 5d | MTD | YTD | LTD, MTD by
default, Download CSV); the explain tiles for the period; the chart (cumulative LTD, the daily
P&L as bars in a lower panel, the period shaded; Book | By spread type | By commodity: the
per-trade LTDs of the series summed per group and day, display); the five best and five worst
positions of the period (a click opens the Book tab); month by month by spread type or
commodity; the scorecard of his ideas (`engine.spreads.scorecard`: closed and open lines, a table
by trade name or spread type, every idea in a fold); the carry of the calendar spreads
(`engine.spreads.carry`, research context, never in a P&L figure); the rolls read off the fills
(`engine.spreads.rolls`: the price spread at each roll, not P&L; rolls out in the headline, moves
into an earlier month apart); the track record in one line; one "Data issues (N)" drawer.

`period_rows` (below) stays: the Book reads each trade's Daily / MTD / LTD through it (the
header's own split over the screens' shared filled reader). `layout(default_date)` and
`register_callbacks(app, get_db_path)` are the shell's interface; `render(as_of, db_path, ...)`
builds the whole body for a direct render.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import Input, Output, State, dcc, html

from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import header
from ui.tabs import ranking as rk
from ui.tabs.formatting import (
    MISSING, TRADE_TYPE_TITLES, about, contract_name, full_money, fx_name, issues_drawer,
    lme_name, marker, missing_cell, money_cell, price_decimals, price_text, quoted_unit, short_date, short_root_name,
    sign_class, signed_money, signed_number, spread_name, sum_known, tab_link,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "pnl-body"
REFRESH_ID = "pnl-refresh"
PERIOD_ID = "pnl-period"
CHART_BY_ID = "pnl-chart-by"
MONTH_BY_ID = "pnl-month-by"
CSV_BUTTON_ID = "pnl-csv"
DOWNLOAD_ID = "pnl-download"
NOTE_ID = "pnl-note"
TILES_ID = "pnl-tiles"
SECTIONS_ID = "pnl-sections"
CHART_ID = "pnl-chart"
GRAPH_ID = "pnl-graph"
CONTRIB_ID = "pnl-contributors"
MONTHS_ID = "pnl-months"
MONTHS_TABLE_ID = "pnl-months-table"
TRACK_ID = "pnl-track"
SCORE_ID = "pnl-scorecard"
SCORE_BY_ID = "pnl-score-by"
CARRY_ID = "pnl-carry"
ROLLS_ID = "pnl-rolls"
ISSUES_SLOT_ID = "pnl-issues-slot"
ISSUES_ID = "pnl-issues"
CHART_HEIGHT = 300

NA = MISSING
PERIODS = ("daily", "d5", "mtd", "ytd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD", "ltd": "LTD"}
PERIOD_TIPS = {"daily": "LTD today less LTD at the previous business day's close.",
               "d5": "LTD today less LTD five business days back.",
               "mtd": "LTD today less LTD at the last business day of the previous month.",
               "ytd": "LTD today less LTD at the last business day of the previous year.",
               "ltd": "Life to date: every trade's P&L at today's marks, settled trades frozen."}
DEFAULT_PERIOD = "mtd"
CHART_BOOK, CHART_TYPE, CHART_COMMODITY = "book", "type", "commodity"
CHART_OPTIONS = ((CHART_BOOK, "Book"), (CHART_TYPE, "By spread type"), (CHART_COMMODITY, "By commodity"))
MONTH_OPTIONS = ((CHART_TYPE, "Spread type"), (CHART_COMMODITY, "Commodity"))
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
FX_SECTOR = "fx"
FX_GROUP = "FX hedges"
OTHER_GROUP = "Other"
TYPE_ORDER = ("Cross exchange", "Cross product", "Term structure", "Outrights", "Options on futures", "LME forwards",
              FX_GROUP, OTHER_GROUP)
_PRODUCT_GROUPS = {"FUTURE": "Outrights", "CMDTY_OPTION": "Options on futures", "LME_FWD": "LME forwards"}
N_CONTRIBUTORS = 5
LINES_ON_HOVER = 12
_CENT = 0.005
QUESTION = "how did the P&L get here, and what drove it"

TAB_ABOUT = ("How did the P&L get here, and what drove it? The period chosen on the switch drives everything "
             "below: its total (the header's figure) split by driver, the chart of the LTD and the daily P&L with "
             "the period shaded, the positions that made and lost the most, the months and the book's record. "
             "Each figure is the engine's, summed from the trades priced; what is left out is named.")
TILE_TIPS = {
    "total": "The period's P&L: the header's figure, the trades priced at both ends summed, a trade new since the "
             "reference close at its whole P&L to date.",
    "spread": "Spread: the spreads' and contracts' own prices moved, at today's FX.",
    "fx": "FX: the currency moved on the P&L a non-USD contract had already made.",
    "hedge": "Hedge: the currency hedges (FX spot and forwards, the USD/CNH future), their whole change.",
    "new_trades": "New trades: dealt after the reference close, their whole P&L to date.",
    "realised": "Realised: trades settled within the period, their whole change. A closed-out option group (bought "
                "and sold back before expiry) stays open until its expiry.",
    "other": "Other: trades whose change could not be split between their price and the currency, whole.",
    "open": "Open: the trades not yet settled (a closed-out option group until its expiry), their P&L to date.",
}
LTD_TIPS = {"realised": "Realised: trades settled by the as-of date, frozen at settlement. A closed-out option group "
                        "(bought and sold back before expiry) stays open until its expiry.",
            "open": "Open: the trades not yet settled, at today's marks (a closed-out option group at its closing "
                    "fill until its expiry)."}
TILE_LABELS = {"total": "Total", "spread": "Spread", "fx": "FX", "hedge": "Hedge", "new_trades": "New trades",
               "realised": "Realised", "other": "Other", "open": "Open"}
CHART_ABOUT = ("The LTD of the book (or of each group) on every business day since the first trade, each day the "
               "trades priced that day summed (excl. and filled on hover); under it each day's Daily P&L, the "
               "header's figure for that day (excl. N on hover). The chosen period is shaded.")
CONTRIB_ABOUT = ("The five positions that made the most and the five that lost the most over the period, named "
                 "as on the Book; their split on hover; a click opens the Book tab.")
MONTHS_ABOUT = ("Each calendar month: the month-end LTD less the previous month-end LTD, per trade, summed by "
                "group; the current month is the MTD. A month's Total is the sum of its trades priced at both ends.")
TRACK_ABOUT = ("The book's record from its daily P&L, each day the header's own Daily for that day (the trades "
               "priced at both ends; the rest named, excl. N). A day with no Daily figure is left out and named "
               "on hover; the fall from the peak runs on the LTD.")
# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def full_usd(v: float) -> str:
    return full_money(v)


# --------------------------------------------------------------------------- the period
class PeriodView:
    """One period's figures: `entry` (the header's whole-book entry with its markers), `rows`
    (one per trade on the as-of book: trade_id, instrument_id, product, status, trade_date,
    value (float or None), reason, note), `ref_iso` (the period's own reference date, '' for
    LTD), `ref_used` (the close measured from) and `blocked` (the trades priced today but not
    on the reference close, left out with that reason)."""

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
    """The per-trade figures of `key` (`PERIODS`) on `as_of`, by the header's rule (module
    docstring). The whole-book `entry` is `header._priced_single` / `_priced_diff` itself,
    with the fill and step-back markers `header._build_figures` puts beside the header's
    cards, so the Total line here is the header's figure."""
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
def _instruments(conn: sqlite3.Connection) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    try:
        for inst, base, quote, expiry in conn.execute(
                "SELECT instrument_id, base_ccy, quote_ccy, expiry_date FROM instruments"):
            out[str(inst)] = {"base": str(base or ""), "quote": str(quote or ""), "expiry": str(expiry or ""),
                              "strike": None, "option_type": ""}
        for inst, strike, otype in conn.execute("SELECT instrument_id, strike, option_type FROM instrument_options"):
            if str(inst) in out:
                out[str(inst)]["strike"] = _num(strike) or None
                out[str(inst)]["option_type"] = str(otype or "")
    except sqlite3.Error:
        pass
    return out


def _roots() -> Dict[str, Any]:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- the ids stand for the names
        return {}


def _spreads(conn: sqlite3.Connection, as_of: str, series) -> Tuple[Optional[dict], str]:
    """The Book's own `book_spreads` (its memoised reader, so the positions and their ids are the
    Book's); off the series' frames when that cannot be read."""
    try:
        from ui.tabs import book
        return book._spreads(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001 -- the engine's own grouping off the series
        log.exception("P&L tab: the Book's spreads could not be read for %s", as_of)
        try:
            from engine.spreads import book_spreads
            from engine.pnl.valuation import value_book

            def read(c, day):
                try:
                    return series.frame(day)
                except KeyError:
                    return value_book(c, day)
            return book_spreads(conn, as_of, value_fn=read), ""
        except Exception as exc2:  # noqa: BLE001
            return None, f"the positions could not be grouped ({type(exc2).__name__}: {exc2}; {exc})"


def _trade_types(conn: sqlite3.Connection, spreads: Optional[dict]) -> Dict[str, dict]:
    """{trade_id: {trade_type, ...}}: the Book's one type per trade (`book.trade_types`)."""
    try:
        from ui.tabs import book
        return book.trade_types({"labels": book._labels(conn), "spreads": spreads or {}})
    except Exception:  # noqa: BLE001 -- the broker's own label then
        try:
            return {str(t): {"trade_type": str(tt or "")}
                    for t, tt in conn.execute("SELECT trade_id, trade_type FROM trades")}
        except sqlite3.Error:
            return {}


def group_maps(conn: sqlite3.Connection, frame: pd.DataFrame, spreads: Optional[dict], roots: Dict[str, Any],
               instruments: Dict[str, dict]) -> Dict[str, Dict[str, str]]:
    """{'type': {trade_id: group}, 'commodity': {trade_id: group}}: each trade grouped as the Book
    groups it: its position's spread type (else outrights, options on futures, LME forwards, FX
    hedges), and its commodity across exchanges (`engine.curve.subsector_name`; an FX product or
    the USD/CNH future under FX hedges)."""
    try:
        from engine.curve import subsector_name
    except Exception:  # noqa: BLE001
        def subsector_name(key):  # type: ignore[misc]
            return str(key or "").replace("_", " ").capitalize()
    types = _trade_types(conn, spreads)
    by_type: Dict[str, str] = {}
    by_commodity: Dict[str, str] = {}
    if frame is None or frame.empty:
        return {CHART_TYPE: by_type, CHART_COMMODITY: by_commodity}
    for tid, inst, product in zip(frame["trade_id"], frame["instrument_id"], frame["product"]):
        tid, product = str(tid), str(product)
        base = (instruments.get(str(inst)) or {}).get("base", "")
        root = roots.get(base)
        fx = product in FX_PRODUCTS or str(getattr(root, "sector", "") or "") == FX_SECTOR
        code = str((types.get(tid) or {}).get("trade_type") or "")
        by_type[tid] = TRADE_TYPE_TITLES.get(code) or (FX_GROUP if fx else _PRODUCT_GROUPS.get(product, OTHER_GROUP))
        if fx:
            by_commodity[tid] = FX_GROUP
        elif root is None:
            by_commodity[tid] = OTHER_GROUP
        else:
            by_commodity[tid] = subsector_name(str(getattr(root, "subsector", "") or "")) or OTHER_GROUP
    return {CHART_TYPE: by_type, CHART_COMMODITY: by_commodity}


def gather(conn: sqlite3.Connection, as_of: str, key: str = DEFAULT_PERIOD) -> dict:
    """Everything the tab shows for `as_of` and the period `key`, each part in its own try: one
    that fails costs its own section, with the reason. One daily series and one set of positions
    for the whole render."""
    from engine.pnl.series import daily_series, monthly_pnl, period_pnl, period_start, track_record
    from engine.spreads import period_explain
    from ui.tabs import book

    key = key if key in PERIODS else DEFAULT_PERIOD
    data: Dict[str, Any] = {"as_of": as_of, "key": key, "errors": []}
    data["n_trades"] = book.trades_on_file(conn)
    if not data["n_trades"]:
        return data
    eff = as_of                  # the series takes any as-of, a weekend's too, as the header does
    data["eff"], data["note"] = eff, ""
    series = daily_series(conn, eff)
    data["series"] = series
    spreads, err = _spreads(conn, eff, series)
    data["spreads"] = spreads
    if err:
        data["errors"].append(("Positions", err))
    try:
        data["explain"] = period_explain(conn, eff, key, series=series, spreads=spreads)
        data["period"] = period_pnl(series, period_start(eff, key, series.holidays), eff)
    except Exception as exc:  # noqa: BLE001
        log.exception("P&L tab: the %s explain failed for %s", key, eff)
        data["explain"], data["period"] = None, None
        data["errors"].append((PERIOD_TITLES[key], f"the period could not be explained ({type(exc).__name__}: {exc})"))
    try:
        data["track"] = track_record(series, eff)
    except Exception as exc:  # noqa: BLE001
        data["track"] = None
        data["errors"].append(("Track record", f"could not be built ({type(exc).__name__}: {exc})"))
    try:
        data["monthly"] = monthly_pnl(series, eff)
    except Exception as exc:  # noqa: BLE001
        data["monthly"] = None
        data["errors"].append(("Months", f"could not be built ({type(exc).__name__}: {exc})"))
    from engine.spreads import carry, rolls, scorecard
    for name, title, build in (("scorecard", "Scorecard", lambda: scorecard(conn, eff, series=series, spreads=spreads)),
                               ("carry", "Carry", lambda: carry(conn, eff, spreads=spreads)),
                               ("rolls", "Rolls", lambda: rolls(conn, eff, spreads=spreads))):
        try:
            data[name] = build()
        except Exception as exc:  # noqa: BLE001 -- its own section says so, the reason in the drawer
            log.exception("P&L tab: the %s failed for %s", name, eff)
            data[name] = None
            data["errors"].append((title, f"could not be built ({type(exc).__name__}: {exc})"))
    data["roots"], data["instruments"] = _roots(), _instruments(conn)
    frame = series.frame(eff) if series.days else pd.DataFrame()
    data["frame"] = frame
    data["groups"] = group_maps(conn, frame, spreads, data["roots"], data["instruments"])
    return data


# --------------------------------------------------------------------------- names
def position_name(data: dict, position_id: str, fallback: str = "") -> str:
    """The Book's plain name of a position id: a spread by `spread_name`, an outright contract by
    `contract_name`, a single trade by its product's name."""
    roots, instruments = data.get("roots") or {}, data.get("instruments") or {}
    if position_id.startswith("OUTRIGHT-"):
        inst = position_id[len("OUTRIGHT-"):]
        base = (instruments.get(inst) or {}).get("base", "")
        return contract_name(inst, roots.get(base), base)
    if position_id.startswith("TRADE-"):
        tid = position_id[len("TRADE-"):]
        frame = data.get("frame")
        row = None
        if frame is not None and not frame.empty:
            hit = frame[frame["trade_id"] == tid]
            row = hit.iloc[0] if not hit.empty else None
        if row is None:
            return fallback or tid
        inst, product, settle = str(row["instrument_id"]), str(row["product"]), str(row.get("settle_date") or "")
        info = instruments.get(inst) or {}
        base = info.get("base", "")
        if product == "LME_FWD":
            return lme_name(roots.get(base), base, settle)
        if product == "FX_OPTION":
            return fx_name(base + info.get("quote", ""), product, info.get("expiry") or settle,
                           info.get("option_type", ""), info.get("strike"))
        if product in FX_PRODUCTS:
            return fx_name(inst, product, settle)
        return contract_name(inst, roots.get(base), base)
    for p in (data.get("spreads") or {}).get("positions") or []:
        if str(p.get("position_id") or p.get("name")) == position_id:
            return spread_name(p, roots)
    return fallback or position_id


# --------------------------------------------------------------------------- 1. the tiles
def _lines_hover(head: str, lines: Sequence[str]) -> str:
    more = [f"and {len(lines) - LINES_ON_HOVER} more"] if len(lines) > LINES_ON_HOVER else []
    return "\n".join([head, *lines[:LINES_ON_HOVER], *more])


def total_markers(data: dict) -> List[Any]:
    """The header's markers for the period, read off the series' period: "excl. N" (the trades
    left out, each with its reason), "filled N" (valued at an earlier close), "ref <date>" (the
    reference close stepped back)."""
    ex, pp = data.get("explain") or {}, data.get("period")
    out: List[Any] = []
    excluded = list(ex.get("excluded") or [])
    if excluded:
        n_total = len(pp.by_trade) if pp is not None else 0
        out.append(marker(f"excl. {len(excluded)}",
                          _lines_hover(f"excludes {len(excluded)} of {n_total} trades",
                                       [f"{t}: {why}" for t, why in excluded])))
    if pp is not None:
        n_filled = pp.n_filled_end if pp.start_ref is None else pp.n_filled_ref
        if n_filled:
            where = pp.end if pp.start_ref is None else pp.ref_used
            out.append(marker(f"filled {n_filled}", f"{n_filled} trade(s) with no price on {where} valued at "
                                                     "their last earlier close"))
        if pp.start_ref and pp.ref_used and pp.ref_used != pp.start_ref:
            out.append(marker(f"ref {short_date(pp.ref_used)}", pp.ref_note or
                              f"measured from the {pp.ref_used} close: {pp.start_ref} has no usable close"))
    return [m for m in out if m is not None]


def _tile(label: str, tip: str, value: Optional[float], reason: str = "", hover: str = "",
          extra: Sequence[Any] = (), sub: str = "") -> html.Div:
    v: List[Any] = [money_cell(value, reason=reason or "no figure", hover=hover)]
    v.extend(extra)
    children: List[Any] = [html.Div(label, className="k", title=tip), html.Div(v, className="v")]
    if sub:
        children.append(html.Div(sub, className="pnl-tile-sub"))
    return html.Div(children, className="book-tile")


def tiles(data: dict) -> html.Div:
    key, ex = data["key"], data.get("explain")
    if ex is None:
        return html.Div(className="book-tiles", children=[_tile("Total", TILE_TIPS["total"], None,
                                                                  "the period could not be explained")])
    title = PERIOD_TITLES[key]
    reason = str(ex.get("reason") or "")
    days = data["series"].days
    if key == "ltd":
        sub = f"since the first trade, {short_date(days[0])}" if days else ""
    elif ex.get("ref_used"):
        sub = f"vs {short_date(ex['ref_used'])} close"
    else:
        sub = ""
    out = [_tile(f"{title} total", TILE_TIPS["total"] + " " + PERIOD_TIPS[key], ex.get("total"), reason,
                 extra=total_markers(data) if ex.get("total") is not None else (), sub=sub)]
    split_reason = str(ex.get("split_reason") or reason or "")
    names = ("realised", "open") if key == "ltd" else ("spread", "fx", "hedge", "new_trades", "realised")
    for name in names:
        tip = LTD_TIPS.get(name, TILE_TIPS[name]) if key == "ltd" else TILE_TIPS[name]
        out.append(_tile(TILE_LABELS[name], tip, ex.get(name), split_reason))
    other = ex.get("other")
    if key != "ltd" and other is not None and abs(other) >= _CENT:
        lines = [f"{o['trade_id']}: {full_money(o['amount'])} ({o['why']})" for o in ex.get("other_trades") or []]
        out.append(_tile(TILE_LABELS["other"], TILE_TIPS["other"], other,
                         hover=_lines_hover("the trades not split:", lines) if lines else ""))
    return html.Div(out, className="book-tiles pnl-tiles")


# --------------------------------------------------------------------------- 2. the chart
def _point_text(n_unpriced: int, n_total: int, n_filled: int) -> str:
    parts_ = []
    if n_unpriced:
        parts_.append(f"excl. {n_unpriced} of {n_total} trades unpriced")
    if n_filled:
        parts_.append(f"filled {n_filled}: valued at an earlier close")
    return "; ".join(parts_)


def _day_text(r: dict) -> str:
    """A day's markers as the header shows them: "excl. N" (and the first trades left out)."""
    n = int(r.get("n_excluded") or 0)
    if not n:
        return ""
    names = ", ".join(str(t) for t, _w in (r.get("excluded") or [])[:3])
    return f"excl. {n} ({names}{', ...' if n > 3 else ''})"


def _group_order(names: Sequence[str], by: str, weight: Dict[str, float]) -> List[str]:
    if by == CHART_TYPE:
        rank = {n: i for i, n in enumerate(TYPE_ORDER)}
        return sorted(names, key=lambda n: (rank.get(n, len(TYPE_ORDER)), n))
    tail = (FX_GROUP, OTHER_GROUP)
    return sorted(names, key=lambda n: (n in tail, tail.index(n) if n in tail else 0, -abs(weight.get(n, 0.0)), n))


def group_lines(data: dict, by: str) -> List[Tuple[str, List[Optional[float]], List[str]]]:
    """[(group, LTD per day, hover per day)]: each day the per-trade LTDs of the series summed
    by group (display: known figures of one unit); a group with trades but none priced that
    day is a gap, a group with no trade yet is 0 (nothing was held)."""
    series, mapping = data["series"], (data.get("groups") or {}).get(by) or {}
    names = sorted(set(mapping.values()))
    values: Dict[str, List[Optional[float]]] = {n: [] for n in names}
    texts: Dict[str, List[str]] = {n: [] for n in names}
    for day in series.days:
        f = series.frames[day]
        per: Dict[str, List[Tuple[Any, str]]] = {n: [] for n in names}
        for tid, v in zip(f["trade_id"], f["ltd_usd"]):
            per.setdefault(mapping.get(str(tid), OTHER_GROUP), []).append((v, str(tid)))
        for n in names:
            pairs = per.get(n) or []
            if not pairs:
                values[n].append(0.0)
                texts[n].append("")
                continue
            total, excluded, _r = sum_known(pairs)
            values[n].append(total)
            texts[n].append(f"excl. {excluded} of {len(pairs)} trades unpriced" if excluded else "")
    last = {n: next((v for v in reversed(values[n]) if v is not None), 0.0) for n in names}
    return [(n, values[n], texts[n]) for n in _group_order(names, by, last)]


_PALETTE = ("#0f1f3d", "#b8860b", "#2e7d32", "#c0392b", "#6a1b9a", "#00838f", "#ef6c00", "#5d4037", "#546e7a",
            "#ad1457")


def chart_figure(data: dict, by: str = CHART_BOOK) -> dict:
    """The LTD line(s) over every business day of the series, the daily P&L bars in a lower
    panel (a grey x where the day has no figure, its reason on hover), the period shaded."""
    series, key, ex = data["series"], data["key"], data.get("explain") or {}
    points = series.chart_points()
    xs = [p[0] for p in points]
    traces: List[dict] = []
    book_line = {"x": xs, "y": [p[1] for p in points], "type": "scatter", "mode": "lines", "name": "Book",
                 "text": [_point_text(p[2], p[3], p[4]) for p in points], "yaxis": "y",
                 "hovertemplate": "Book LTD %{y:$,.0f} %{text}<extra></extra>",
                 "line": {"color": "#0f1f3d", "width": 2}}
    if by == CHART_BOOK:
        traces.append(book_line)
    else:
        book_line["line"] = {"color": "#9ca3af", "width": 1.5, "dash": "dot"}
        traces.append(book_line)
        for i, (name, ys, texts) in enumerate(group_lines(data, by)):
            traces.append({"x": xs, "y": ys, "type": "scatter", "mode": "lines", "name": name, "text": texts,
                           "yaxis": "y", "line": {"color": _PALETTE[(i + 1) % len(_PALETTE)], "width": 1.6},
                           "hovertemplate": f"{name} " + "%{y:$,.0f} %{text}<extra></extra>"})
    track = data.get("track") or {}
    counted = [r for r in track.get("daily") or [] if r.get("value") is not None]
    bys = [r["value"] for r in counted]
    traces.append({"x": [r["date"] for r in counted], "y": bys, "type": "bar", "name": "Daily P&L", "yaxis": "y2",
                   "showlegend": False, "text": [_day_text(r) for r in counted],
                   "marker": {"color": ["#1a7f4b" if v >= 0 else "#c0392b" for v in bys]},
                   "hovertemplate": "Daily %{y:$,.0f} %{text}<extra></extra>"})
    left_out = dict(track.get("excluded_days") or [])
    if left_out:
        lx = sorted(left_out)
        traces.append({"x": lx, "y": [0] * len(lx), "type": "scatter", "mode": "markers", "yaxis": "y2",
                       "name": "no daily figure", "showlegend": False, "text": [left_out[d] for d in lx],
                       "marker": {"symbol": "x-thin", "size": 6, "color": "#9ca3af", "line": {"width": 1}},
                       "hovertemplate": "Daily: no figure, %{text}<extra></extra>"})
    shapes = []
    start = ex.get("ref_used") or ex.get("start_ref")
    if key != "ltd" and start and xs:
        shapes.append({"type": "rect", "xref": "x", "yref": "paper", "x0": start, "x1": data["eff"], "y0": 0,
                       "y1": 1, "fillcolor": "rgba(184,134,11,0.10)", "line": {"width": 0}, "layer": "below"})
    return {"data": traces, "layout": {
        "height": CHART_HEIGHT, "margin": {"l": 56, "r": 16, "t": 8 if by == CHART_BOOK else 28, "b": 28},
        "hovermode": "x unified", "showlegend": by != CHART_BOOK,
        "legend": {"orientation": "h", "y": 1.0, "yanchor": "bottom", "x": 0, "font": {"size": 11}},
        "xaxis": {"type": "date", "tickformat": "%d %b", "anchor": "y2", "hoverformat": "%a %d %b %Y"},
        "yaxis": {"domain": [0.34, 1], "title": {"text": "LTD", "font": {"size": 11}}, "tickformat": "~s",
                  "zeroline": True, "zerolinecolor": "#c9ced8"},
        "yaxis2": {"domain": [0, 0.26], "title": {"text": "Daily", "font": {"size": 11}}, "tickformat": "~s",
                   "zeroline": True, "zerolinecolor": "#c9ced8"},
        "bargap": 0.25, "shapes": shapes, "plot_bgcolor": "#fff", "paper_bgcolor": "#fff"}}


def chart(data: dict, by: str) -> Any:
    if not data["series"].days:
        return html.P(f"No trades dated on or before {data['eff']}: nothing to chart.", className="book-quiet")
    return dcc.Graph(id=GRAPH_ID, figure=chart_figure(data, by), config={"displayModeBar": False})


# --------------------------------------------------------------------------- 3. contributors
_SPLIT_WORDS = (("spread", "spread"), ("fx", "FX"), ("hedge", "hedge"), ("new_trades", "new trades"),
                ("realised", "realised"), ("other", "other"), ("open", "open"))


def split_hover(p: dict, key: str) -> str:
    """The full figure, 'spread +12.3k · FX −1.2k' (the non-zero parts), the trades, what is left out."""
    names = ("realised", "open") if key == "ltd" else ("spread", "fx", "hedge", "new_trades", "realised", "other")
    split = [f"{w} {signed_money(p.get(k))}" for k, w in _SPLIT_WORDS
             if k in names and p.get(k) is not None and abs(p[k]) >= _CENT]
    tids = list(p.get("trade_ids") or [])
    lines = [full_money(p["total"]), " · ".join(split),
             f"{len(tids)} trade{'' if len(tids) == 1 else 's'}: " + ", ".join(tids[:LINES_ON_HOVER])
             + (" ..." if len(tids) > LINES_ON_HOVER else "")]
    if p.get("n_excluded"):
        lines.append(f"excl. {p['n_excluded']}: left out of the period (see Data issues)")
    lines.append("Click to open the Book tab.")
    return "\n".join(x for x in lines if x)


def contributor_rows(data: dict) -> Tuple[List[dict], List[dict]]:
    """(the five best, the five worst) positions of the period by their known total."""
    ex = data.get("explain") or {}
    known = [p for p in ex.get("by_position") or [] if p.get("total") is not None]
    best = sorted([p for p in known if p["total"] >= _CENT], key=lambda p: -p["total"])[:N_CONTRIBUTORS]
    worst = sorted([p for p in known if p["total"] <= -_CENT], key=lambda p: p["total"])[:N_CONTRIBUTORS]
    return best, worst


def _contrib_list(data: dict, rows: Sequence[dict], side: str, scale: float) -> html.Div:
    items: List[Any] = []
    for n, p in enumerate(rows):
        name = position_name(data, str(p["position_id"]), str(p.get("name") or ""))
        width = f"{max(2.0, 100.0 * abs(p['total']) / scale):.1f}%" if scale else "2%"
        label = [html.Span(name, className="pnl-contrib-name"),
                 html.Span(html.Span(className=f"pnl-bar pnl-bar--{side}", style={"width": width}),
                           className="pnl-bar-track"),
                 html.Span(signed_money(p["total"]),
                           className=f"pnl-contrib-value {'cell-pos' if side == 'best' else 'cell-neg'}")]
        items.append(tab_link(label, "book", f"pnl-{side}-{n}", title=split_hover(p, data["key"]),
                              className="pnl-contrib-row"))
    if not items:
        items.append(html.Div("No position " + ("made money" if side == "best" else "lost money") + " over the period.",
                              className="book-quiet"))
    return html.Div(items, className="pnl-contrib-list")


def contributors(data: dict) -> Any:
    ex = data.get("explain")
    if ex is None or ex.get("total") is None:
        return html.P(missing_cell(str((ex or {}).get("reason") or "the period has no figure")), className="book-quiet")
    best, worst = contributor_rows(data)
    scale = max([abs(p["total"]) for p in best + worst] or [0.0])
    return html.Div(className="cards-row pnl-contrib", children=[
        html.Div(className="book-card card-pad", children=[html.Div("Made the most", className="book-h"),
                                                           _contrib_list(data, best, "best", scale)]),
        html.Div(className="book-card card-pad", children=[html.Div("Lost the most", className="book-h"),
                                                           _contrib_list(data, worst, "worst", scale)]),
    ])


# --------------------------------------------------------------------------- 4. month by month
def _month_words(month: str) -> str:
    try:
        d = dt.date.fromisoformat(month + "-01")
    except ValueError:
        return month
    return f"{d:%b} {d.year}"


def month_records(data: dict, by: str) -> Tuple[List[str], List[dict]]:
    """(groups, rows newest first): each row {month, label, hover, current, total, reason,
    total_excluded, total_reasons, cells {group: (value, excluded, reasons, n trades)}}; a group's
    cell is its trades' monthly P&L summed (display), the Total the engine's month total."""
    monthly = data.get("monthly")
    if monthly is None:
        return [], []
    mapping = (data.get("groups") or {}).get(by) or {}
    bt = monthly.by_trade
    weight: Dict[str, float] = {}
    rows = []
    for m in monthly.months:
        sub = bt[bt["month"] == m["month"]] if not bt.empty else bt
        per: Dict[str, List[Tuple[Any, str]]] = {}
        left_out = []
        for tid, v, inc, why in zip(sub["trade_id"], sub["pnl_usd"], sub["included"], sub["reason"]):
            g = mapping.get(str(tid), OTHER_GROUP)
            per.setdefault(g, []).append((v if inc else None, f"{tid}: {why}" if why else str(tid)))
            if not inc:
                left_out.append(f"{tid}: {why}")
        cells = {}
        for g, pairs in per.items():
            total, excluded, reasons = sum_known(pairs)
            cells[g] = (total, excluded, reasons, len(pairs))
            weight[g] = weight.get(g, 0.0) + abs(total or 0.0)
        hover = f"from the {m.get('ref_used') or m.get('start_ref')} close to {m['end']}"
        if m.get("ref_note"):
            hover += f"\n{m['ref_note']}"
        rows.append({"month": m["month"], "label": _month_words(m["month"]), "current": m["month"] == data["eff"][:7],
                     "hover": hover, "total": m["total"] if m["available"] else None,
                     "reason": str(m.get("reason") or ""), "total_excluded": int(m.get("n_excluded") or 0),
                     "total_reasons": left_out, "cells": cells})
    return _group_order(list(weight), by, weight), list(reversed(rows))


def _sum_td(value: Optional[float], excluded: int, reasons: Sequence[str], reason: str = "",
            bold: bool = False) -> html.Td:
    if value is None:
        return html.Td(missing_cell(reason or "; ".join(reasons[:LINES_ON_HOVER]) or "no figure"))
    children: List[Any] = [money_cell(value)]
    if excluded:
        children.append(marker(f"excl. {excluded}", _lines_hover(f"excludes {excluded} trade(s)", list(reasons))))
    return html.Td(children, style={"fontWeight": 700} if bold else None)


def months_table(data: dict, by: str) -> Any:
    groups, rows = month_records(data, by)
    if not rows:
        return html.P("No month to show.", className="book-quiet")
    head = html.Thead(html.Tr([html.Th("Month", className="l")] + [html.Th(g) for g in groups]
                              + [html.Th("Total", title="The month's P&L: the trades priced at both month ends "
                                                        "summed, a trade new in the month at its P&L to date")]))
    body = []
    for r in rows:
        label: List[Any] = [r["label"]]
        if r["current"]:
            label.append(html.Span("MTD", className="book-note"))
        cells: List[Any] = [html.Td(label, className="l", title=r["hover"])]
        for g in groups:
            hit = r["cells"].get(g)
            if hit is None:
                cells.append(html.Td("", title=f"no {g.lower()} trade held in {r['label']}"))
            else:
                cells.append(_sum_td(hit[0], hit[1], hit[2]))
        cells.append(_sum_td(r["total"], r["total_excluded"], r["total_reasons"], r["reason"], bold=True))
        body.append(html.Tr(cells))
    return html.Div(className="book-card", children=[
        html.Table([head, html.Tbody(body)], id=MONTHS_TABLE_ID, className="book-table pnl-months-table")])


# --------------------------------------------------------------------------- 5. the scorecard, carry and rolls
# (wave 2, 2026-09-29). Every figure is the engine's (`engine.spreads.scorecard`, `carry`,
# `rolls`); the tab ranks, formats and sums known figures of one unit (a display subtotal
# with `excl. N`), never prices.
SCORE_BY_TRADE, SCORE_BY_TYPE = "trade", "type"
SCORE_OPTIONS = ((SCORE_BY_TRADE, "By trade"), (SCORE_BY_TYPE, "By spread type"))
NO_TRADE_NAME, NO_TYPE = "No trade name", "No type"
SCORE_ABOUT = ("How do his trade ideas do? An idea is a position of the Book (a trade name, a spread, an outright "
               "contract, any other trade on its own). It is closed when it went flat (bought and sold back, "
               "settled) and stays flat to the as-of date. Every idea's P&L is its LTD today, so closed and open "
               "add up to the book's LTD; a closed idea's figure at its close and the currency move since are on "
               "hover of its P&L. Win rate: winners over closed ideas (a result within half "
               "a cent of zero is neither). Payoff: the average win over the average loss. Expectancy: the average "
               "P&L per closed idea. Held: business days from the first trade to the close. An idea with a trade "
               "unpriced on its P&L day is left out of every figure and named, never counted as zero.")
CARRY_ABOUT = ("Research context, from the research app's settlement curve: what each calendar spread's level would "
               "do over the next contract step (a month on a monthly contract) if the curve kept its shape, in USD "
               "on the position's open lots (+ = the curve's shape pays the position). Calendar spreads only. Never a "
               "mark, never in a P&L figure or total.")
ROLLS_ABOUT = ("A roll: on one day, a trade that cuts a position in one contract month and a trade the position's "
               "way in another month of the same contract, same trade name, lots within 5 %, read off the fills. "
               "The roll figure is the price spread paid or received at the roll (the month sold less the month "
               "bought, x lots x the contract's size, in USD at that day's spot): it is NOT P&L. Both fills are "
               "traded at market and their P&L is already in every figure on this tab. + = the roll earned carry "
               "(a long rolled out in backwardation, a short in contango), - = it cost carry. The headline counts "
               "rolls out into a later month only; a move into an earlier month is listed apart, never in it.")


def _pct(v: Optional[float], reason: str = "no closed idea yet") -> Any:
    return missing_cell(reason) if v is None else f"{v * 100:.0f}%"


def _days(v: Optional[float]) -> str:
    return MISSING if v is None else f"{v:.0f} d"


def _track_item(items: List[Any], k: str, v: Any, hover: str = "") -> None:
    items.append(html.Span([html.Span(k, className="pnl-track-k"), v], className="pnl-track-item",
                           title=hover or None))


def _markers(*ms: Any) -> List[Any]:
    return [m for m in ms if m is not None]


def _group_words(key: str, by: str) -> str:
    if by == SCORE_BY_TYPE:
        return TRADE_TYPE_TITLES.get(key) or (key.replace("_", " ").capitalize() if key else NO_TYPE)
    return key or NO_TRADE_NAME


def _idea_name(data: dict, idea: dict) -> str:
    pid, name = str(idea.get("position_id") or ""), str(idea.get("name") or "")
    if pid.startswith("POSITION-STRATEGY-"):
        return name or pid[len("POSITION-STRATEGY-"):] or "no trade name"
    return position_name(data, pid, name)


def _excl_marker(excluded: Sequence[Tuple[str, str]], what: str, data: Optional[dict] = None) -> Any:
    if not excluded:
        return None
    names = {str(i.get("position_id")): _idea_name(data, i)
             for i in ((data or {}).get("scorecard") or {}).get("ideas") or []} if data else {}
    excluded = [(names.get(str(p), str(p)), w) for p, w in excluded]
    return marker(f"excl. {len(excluded)}", _lines_hover(f"{_plural(len(excluded), what)} left out (a trade unpriced "
                                                         "on its P&L day):", [f"{p}: {w}" for p, w in excluded]))


def score_lines(data: dict) -> List[Any]:
    """Two lines: the closed ideas' record, the open ideas' state (the engine's summary)."""
    sc = data.get("scorecard") or {}
    summ = sc.get("summary") or {}
    c, o = summ.get("closed") or {}, summ.get("open") or {}
    closed: List[Any] = []
    n = int(c.get("count") or 0)
    _track_item(closed, "Closed", html.Span([f"{n}", *_markers(_excl_marker(c.get("excluded") or [], "closed idea", data))]),
                f"{c.get('wins', 0)} won, {c.get('losses', 0)} lost, {c.get('scratches', 0)} flat; "
                f"together {full_money(c.get('total'))}")
    if n:
        best, worst = c.get("best") or {}, c.get("worst") or {}
        _track_item(closed, "Win rate", html.Span(_pct(c.get("win_rate"))),
                    f"{c.get('wins', 0)} of {n} closed ideas made money")
        _track_item(closed, "Avg win", money_cell(c.get("avg_win"), "no closed idea made money"))
        _track_item(closed, "Avg loss", money_cell(c.get("avg_loss"), "no closed idea lost money"))
        _track_item(closed, "Payoff", html.Span(missing_cell("needs a win and a loss") if c.get("payoff_ratio") is None
                                                else f"{c['payoff_ratio']:.2f}"),
                    "the average win over the average loss (needs a win and a loss)")
        _track_item(closed, "Expectancy", html.Span([money_cell(c.get("expectancy")), " per idea"]),
                    "the average P&L per closed idea")
        _track_item(closed, "Held", html.Span(f"winners {_days(c.get('avg_hold_days_win'))} · "
                                              f"losers {_days(c.get('avg_hold_days_loss'))}"),
                    "the average business days from the first trade to the close")
        if best or worst:
            _track_item(closed, "Best / worst", html.Span([money_cell(best.get("pnl_usd")), " / ",
                                                           money_cell(worst.get("pnl_usd"))]),
                        f"best: {_idea_name(data, best)}\nworst: {_idea_name(data, worst)}")
    opened: List[Any] = []
    _track_item(opened, "Open", html.Span([f"{int(o.get('count') or 0)}",
                                           *_markers(_excl_marker(o.get("excluded") or [], "open idea", data))]))
    if o.get("count"):
        _track_item(opened, "Unrealised", money_cell(o.get("unrealised_usd")), "the open ideas' LTD today, summed")
        _track_item(opened, "In profit", html.Span(f"{int(o.get('in_profit') or 0)} of {int(o['count'])}"))
    return [html.Div(closed, className="pnl-track pnl-score-line"),
            html.Div(opened, className="pnl-track pnl-score-line")]


def score_table(data: dict, by: str) -> Any:
    """One row per trade name (or spread type), the engine's summary for that group, all the
    ideas last."""
    sc = data.get("scorecard") or {}
    groups = sc.get("by_trade_name" if by == SCORE_BY_TRADE else "by_spread_type") or {}
    if not groups:
        return None
    word = "trade name" if by == SCORE_BY_TRADE else "spread type"
    head = html.Thead(html.Tr([
        html.Th("Trade" if by == SCORE_BY_TRADE else "Spread type", className="l",
                title=f"The ideas grouped by their {word}"),
        html.Th("Ideas", title="Closed · open ideas"),
        html.Th("Win rate", title="Winners over closed ideas"),
        html.Th("Closed P&L", title="The closed ideas' P&L, each at its LTD today (its figure at the close on hover in "
                                     "Every idea)"),
        html.Th("Avg win"), html.Th("Avg loss"),
        html.Th("Open P&L", title="The open ideas' LTD today (unrealised)")]))

    def row(label: str, s: dict, bold: bool = False) -> html.Tr:
        c, o = s.get("closed") or {}, s.get("open") or {}
        excluded = list(c.get("excluded") or []) + list(o.get("excluded") or [])
        ideas: List[Any] = [f"{int(c.get('count') or 0)} · {int(o.get('count') or 0)}",
                            *_markers(_excl_marker(excluded, "idea", data))]
        return html.Tr([
            html.Td(label, className="l book-name"), html.Td(ideas),
            html.Td(_pct(c.get("win_rate"))),
            html.Td(money_cell(c.get("total")) if c.get("count") else missing_cell("no closed idea")),
            html.Td(money_cell(c.get("avg_win"), "no closed idea made money")),
            html.Td(money_cell(c.get("avg_loss"), "no closed idea lost money")),
            html.Td(money_cell(o.get("unrealised_usd")) if o.get("count") else missing_cell("no open idea")),
        ], style={"fontWeight": 700} if bold else None)

    body = [row(_group_words(k, by), s) for k, s in groups.items() if k]
    if "" in groups:
        body.append(row(_group_words("", by), groups[""]))
    if len(groups) > 1:
        body.append(row("All ideas", sc.get("summary") or {}, bold=True))
    return html.Div(className="book-card", children=[html.Table([head, html.Tbody(body)],
                                                                className="book-table pnl-score-table")])


def _idea_pnl_hover(i: dict) -> str:
    """'on <date>', and for a closed idea 'at close on <date>: $x; since then FX ±$y' (the engine's
    context figures, never recomputed)."""
    lines = [f"LTD on {i.get('pnl_date')}"]
    if i.get("status") == "closed":
        at = _num(i.get("pnl_at_close"))
        if at is not None:
            since = _num(i.get("fx_since_close"))
            lines.append(f"at close on {i.get('pnl_at_close_date') or i.get('close_date')}: {full_money(at)}"
                         + (f"; since then FX {signed_money(since, '$')} (the currency re-conversion)"
                            if since is not None and abs(since) >= _CENT else "; no move since"))
    return "\n".join(lines)


def ideas_fold(data: dict) -> Any:
    """Every idea, closed first (the engine's order): name, trade, status, held, P&L, best, worst."""
    ideas = (data.get("scorecard") or {}).get("ideas") or []
    if not ideas:
        return None
    head = html.Thead(html.Tr([html.Th("Idea", className="l"), html.Th("Trade", className="l"),
                               html.Th("Status", className="l"), html.Th("Held", title="Business days"),
                               html.Th("P&L", title="Its LTD today; a closed idea's figure at its close and the currency move since on "
                                                  "hover"),
                               html.Th("Best", title="The highest LTD it showed while held"),
                               html.Th("Worst", title="The lowest LTD it showed while held")]))
    body = []
    for i in ideas:
        status = "closed" if i.get("status") == "closed" else "open"
        status_hover = (f"dealt {i.get('first_trade_date')}, flat from {i.get('close_date')}" if status == "closed"
                        else f"dealt {i.get('first_trade_date')}, held today")
        unpriced = (f"; {i['days_unpriced']} day(s) with a trade unpriced left out of best / worst"
                    if i.get("days_unpriced") else "")
        body.append(html.Tr([
            html.Td(_idea_name(data, i), className="l book-name",
                    title=f"{i.get('position_id')}: trades {', '.join(i.get('trade_ids') or [])}"),
            html.Td("" if i.get("kind") == "strategy" else (i.get("trade_name") or ""), className="l"),
            html.Td(status, className="l", title=status_hover),
            html.Td(f"{int(i.get('holding_days') or 0)}"),
            html.Td(money_cell(i.get("pnl_usd"), i.get("reason") or "not valued", hover=_idea_pnl_hover(i))),
            html.Td(money_cell(i.get("best_ltd"), "no day with every trade priced",
                               hover=f"on {i.get('best_date')}{unpriced}")),
            html.Td(money_cell(i.get("worst_ltd"), "no day with every trade priced",
                               hover=f"on {i.get('worst_date')}{unpriced}")),
        ]))
    return html.Details(className="book-fold", children=[
        html.Summary(f"Every idea ({len(ideas)})", className="pnl-fold-summary"),
        html.Div(className="book-card", children=[html.Table([head, html.Tbody(body)],
                                                              className="book-table pnl-ideas-table")])])


def scorecard_block(data: dict, by: str = SCORE_BY_TRADE) -> Any:
    sc = data.get("scorecard")
    if sc is None:
        return html.P(missing_cell("the scorecard could not be built (see Data issues)"), className="book-quiet")
    if sc.get("reason"):
        return html.P(f"No scorecard: {str(sc['reason']).rstrip('.')}.", className="book-quiet")
    if not sc.get("ideas"):
        return html.P("No idea on file yet.", className="book-quiet")
    by = by if by in dict(SCORE_OPTIONS) else SCORE_BY_TRADE
    return html.Div([c for c in (*score_lines(data), score_table(data, by), ideas_fold(data)) if c is not None])


# --- carry (research)
def _leg_words(data: dict, inst: str) -> str:
    """A carry leg's plain name: 'WTI Dec26', 'LME zinc 21 Oct'."""
    roots = data.get("roots") or {}
    if " " in inst and inst.split(" ")[0].startswith("LME:"):
        rid, prompt = inst.split(" ", 1)
        return lme_name(roots.get(rid), rid, prompt)
    base = ((data.get("instruments") or {}).get(inst) or {}).get("base", "")
    return contract_name(inst, roots.get(base), base)


def _carry_legs(r: dict) -> Tuple[str, str]:
    """(near, far) of a carry row: a pair's from its id ('<trade>|<near>|<far>', an LME prompt
    'LME:ZS 2026-10-21' kept whole), a position's from the engine's fields."""
    parts_ = str(r.get("id") or "").split("|")
    if r.get("source") != "position" and len(parts_) >= 3:
        return parts_[1], parts_[2]
    return str(r.get("near") or ""), str(r.get("far") or "")


def _no_path(text: str) -> str:
    """A reason without a file path in brackets, each clause once (no path on a screen)."""
    clauses = [re.sub(r"\s*\([A-Za-z]:\\[^)]*\)", "", c).strip() for c in str(text or "").split(";")]
    return "; ".join(dict.fromkeys(c for c in clauses if c))


def carry_rows(data: dict) -> List[dict]:
    """The calendar rows of the engine's carry as the Book shows them: its positions, then a
    trade's pair only when no position holds the same two contracts (nothing counts twice)."""
    rows = [r for r in ((data.get("carry") or {}).get("rows") or []) if r.get("calendar")]
    positions = [r for r in rows if r.get("source") == "position"]
    held = {_carry_legs(r) for r in positions}
    return positions + [r for r in rows if r.get("source") != "position" and _carry_legs(r) not in held]


def _carry_name(data: dict, r: dict) -> str:
    near, far = _carry_legs(r)
    words = f"{_leg_words(data, near)} / {_leg_words(data, far)}"
    if r.get("source") != "position":
        trade = str(r.get("id") or "").split("|")[0]
        words += f" ({trade})" if trade else ""
    return words


def carry_block(data: dict) -> Any:
    ca = data.get("carry")
    if ca is None:
        return html.P(missing_cell("the carry could not be read (see Data issues)"), className="book-quiet")
    rows = carry_rows(data)
    tag = html.Span("research", className="pnl-research-tag", title=CARRY_ABOUT)
    if not rows:
        why = str(ca.get("reason") or "no calendar spread held")
        return html.P([f"Curve shape over the next month: no calendar position ({why}) ", tag], className="book-quiet")
    lines, pairs = [], []
    for r in rows:
        name = _carry_name(data, r)
        v = _num(r.get("roll_down_usd"))
        pairs.append((v, f"{name}: {_no_path(r.get('reason')) or 'no figure'}"))
        step = f"{r['roll_down']:+.2f} {r.get('unit') or ''}".strip() if r.get("roll_down") is not None else ""
        extra = "; ".join(x for x in (
            f"read {r['research_date']}" if r.get("research_date") else "",
            f"steps {r['horizon_months']} months on its contract cycle" if (r.get("horizon_months") or 1) != 1 else "",
            str(r.get("note") or "")) if x)
        lines.append(f"{name}: {signed_money(v, '$')}"
                     + (f" ({'; '.join(x for x in (step, extra) if x)})" if step or extra else "")
                     + ("" if v is not None else f", {_no_path(r.get('reason')) or 'no figure'}"))
    total, excluded, reasons = sum_known(pairs)
    hover = _lines_hover("Per calendar position (research):", lines)
    value = (money_cell(total, hover=hover) if total is not None
             else missing_cell("; ".join(reasons[:LINES_ON_HOVER]) or "no figure"))
    kids: List[Any] = ["Curve shape over the next month: ", value,
                       html.Span(f" ({_plural(len(rows), 'calendar position')})", title=hover)]
    if excluded:
        kids.append(marker(f"excl. {excluded}", _lines_hover(f"{excluded} calendar position(s) with no figure:",
                                                             reasons)))
    kids += [" ", tag]
    return html.P(kids, className="pnl-carry-line")


# --- rolls
def _full_signed(v: Optional[float], reason: str = "") -> Any:
    """A roll's USD at the full figure, signed and coloured; the dash with its reason."""
    if v is None:
        return missing_cell(reason or "no USD figure")
    text = f"{abs(v):,.0f}"
    sign = "" if text == "0" else ("+" if v > 0 else "−")
    return html.Span(sign + text, className=sign_class(v) or None, title=full_money(v))


def _roll_tr(data: dict, r: dict) -> html.Tr:
    root = (data.get("roots") or {}).get(str(r.get("root_id") or ""))
    unit = quoted_unit(root) or str(r.get("unit") or "")
    spread = _num(r.get("roll_spread_unit"))
    d = max(2, price_decimals(str(r.get("unit") or "")))
    far_near = _num(r.get("far_minus_near"))
    held = _num(r.get("held_before"))
    traded = _num(r.get("lots_traded"))
    usd = _num(r.get("roll_yield_usd"))
    ids = list(r.get("from_trade_ids") or []) + list(r.get("to_trade_ids") or [])
    local_hover = (f"{full_money(r.get('roll_yield_local'), str(r.get('currency') or ''))} at "
                   f"{r.get('spot_note') or 'spot'}" if usd is not None and str(r.get("currency") or "USD") != "USD"
                   else None)
    return html.Tr([
        html.Td(short_date(r.get("date")), className="l", title=str(r.get("date") or "")),
        html.Td(r.get("trade_name") or "", className="l"),
        html.Td(short_root_name(root, str(r.get("root_id") or "")), className="l book-name",
                title=f"{r.get('root_name')}: {r.get('from_contract')} to {r.get('to_contract')}; trades "
                      f"{', '.join(ids)}"),
        html.Td(str(r.get("label") or "").replace("->", "→"), className="l"),
        html.Td(str(r.get("direction") or ""), className="l",
                title=f"{held:+g} lots held before the roll" if held is not None else None),
        html.Td(f"{_num(r.get('lots')) or 0:g}",
                title=f"{traded:g} lots traded on the smaller side" if traded is not None else None),
        html.Td(f"{price_text(r.get('sold_price'), unit)} / {price_text(r.get('bought_price'), unit)}",
                title="the month sold / the month bought, lots-weighted fills"),
        html.Td([signed_number(spread, d) if spread is not None else MISSING,
                 html.Span(f" {r.get('unit') or ''}", className="unit-suffix")],
                title="the month sold less the month bought, in the contract's quoted unit"),
        html.Td(str(r.get("curve") or ""), className="l",
                title=f"far month less near month at the fills: {far_near:+g}" if far_near is not None else None),
        html.Td(_full_signed(usd, str(r.get("reason") or "")), title=local_hover),
    ])


def _roll_sum(rows: Sequence[dict]) -> Tuple[Optional[float], int, List[str]]:
    return sum_known((_num(r.get("roll_yield_usd")), f"{r.get('date')} {r.get('label')}: {r.get('reason')}")
                     for r in rows)


def rolls_block(data: dict) -> Any:
    ro = data.get("rolls")
    if ro is None:
        return html.P(missing_cell("the rolls could not be read (see Data issues)"), className="book-quiet")
    found = list(ro.get("rolls") or [])
    excluded = [e for e in ro.get("excluded") or [] if e.get("kind") != "no_spot"]
    if not found and not excluded:
        return html.P("No roll in the book's fills" + (f" ({ro['reason']})" if ro.get("reason") else "") + ".",
                      className="book-quiet")
    outs = [r for r in found if r.get("roll_kind") == "out"]
    ins = [r for r in found if r.get("roll_kind") == "in"]
    line: List[Any] = []
    _track_item(line, "Rolls out", html.Span(f"{len(outs)}"), "rolls into a later month")
    if outs:
        t_out, x_out, why_out = _roll_sum(outs)
        word = "earned" if (t_out or 0.0) >= 0 else "paid"
        _track_item(line, f"Carry {word}", html.Span([
            money_cell(t_out, "; ".join(why_out[:LINES_ON_HOVER]) or "no roll converted to USD",
                       hover="the price spread at the rolls, not P&L (the fills' P&L is already in every figure)"),
            *_markers(marker(f"excl. {x_out}", _lines_hover(f"{x_out} roll(s) with no USD figure:", why_out))
                      if x_out else None)]))
    if ins:
        t_in, x_in, why_in = _roll_sum(ins)
        _track_item(line, f"Moved into an earlier month ({len(ins)})", html.Span([
            money_cell(t_in, "; ".join(why_in[:LINES_ON_HOVER]) or "no move converted to USD"),
            *_markers(marker(f"excl. {x_in}", _lines_hover(f"{x_in} move(s) with no USD figure:", why_in))
                      if x_in else None)]),
            "a position moved into a nearer month: not a roll out, never in the headline")
    kids: List[Any] = [html.Div(line, className="pnl-track")]
    if found:
        head = html.Thead(html.Tr([
            html.Th("Date", className="l"), html.Th("Trade", className="l"), html.Th("Contract", className="l"),
            html.Th("Move", className="l"), html.Th("Position", className="l", title="The position rolled"),
            html.Th("Lots"), html.Th("Sold / bought"), html.Th("Roll spread", title="Sold less bought"),
            html.Th("Curve", className="l", title="Contango: the far month above the near; backwardation: below"),
            html.Th("Roll USD", title="The price spread paid or received at the roll, in USD at that day's spot: "
                                      "not P&L. + = earned carry, − = paid carry.")]))
        body = [_roll_tr(data, r) for r in outs]
        if ins:
            body.append(html.Tr(html.Td(f"Moved into an earlier month ({len(ins)}): not in the headline",
                                        colSpan=10, className="l pnl-rolls-sub")))
            body += [_roll_tr(data, r) for r in ins]
        kids.append(html.Div(className="book-card", children=[
            html.Table([head, html.Tbody(body)], className="book-table pnl-rolls-table")]))
    if excluded:
        kids.append(html.P(f"{_plural(len(excluded), 'possible roll')} not counted.", className="book-quiet",
                           title=_lines_hover("Not counted, never guessed:",
                                              [f"{e.get('date')} {e.get('trade_name') or ''} {e.get('root_id')}: "
                                               f"{e.get('reason')}" for e in excluded])))
    return html.Div(kids)


# --------------------------------------------------------------------------- 6. track record
def track_line(data: dict) -> Any:
    """One line: best day, worst day, days up (n of N), the largest fall (peak -> trough), now
    against the peak; the days left out named on hover."""
    tr = data.get("track")
    if tr is None:
        return html.P("The track record could not be built.", className="book-quiet")
    left = tr.get("excluded_days") or []
    left_hover = (_lines_hover(f"{len(left)} day(s) with no Daily figure (the header would show a dash)",
                               [f"{d}: {why}" for d, why in left]) if left else "")
    items: List[Any] = []

    def item(k: str, v: Any, hover: str = "") -> None:
        items.append(html.Span([html.Span(k, className="pnl-track-k"), v], className="pnl-track-item",
                               title=hover or None))

    if not tr.get("n_days"):
        item("Days counted", html.Span("none yet"), left_hover or "no business day with every trade priced yet")
        return html.Div(items, className="pnl-track")
    best, worst = tr.get("best_day") or {}, tr.get("worst_day") or {}
    item("Best day", html.Span([money_cell(best.get("value")), f" {short_date(best.get('date'))}"]))
    item("Worst day", html.Span([money_cell(worst.get("value")), f" {short_date(worst.get('date'))}"]))
    share = tr.get("share_positive")
    partial = [r for r in tr.get("daily") or [] if r.get("value") is not None and r.get("n_excluded")]
    partial_hover = (_lines_hover(f"{len(partial)} counted day(s) leave trades out (the header's excl. N)",
                                  [f"{r['date']}: excl. {r['n_excluded']}" for r in partial]) if partial else "")
    extra = [m for m in (marker(f"{len(left)} days left out", left_hover) if left else None,
                         marker(f"excl. on {len(partial)}", partial_hover) if partial else None) if m is not None]
    item("Days up", html.Span([f"{tr['n_positive']} of {tr['n_days']}"
                               + (f" ({share * 100:.0f}%)" if share is not None else ""), *extra]),
         "\n".join(x for x in (left_hover, partial_hover) if x))
    dd = tr.get("max_drawdown") or {}
    if (dd.get("value") or 0.0) < -_CENT:
        item("Largest fall", html.Span([money_cell(dd["value"]),
                                        f" {short_date(dd['peak_date'])} → {short_date(dd['trough_date'])}"]),
             f"from the LTD peak {full_money(dd['peak_value'])} on {dd['peak_date']} to "
             f"{full_money(dd['trough_value'])} on {dd['trough_date']}")
    else:
        item("Largest fall", html.Span("none"), "the LTD has never been below an earlier peak")
    fp = tr.get("from_peak") or {}
    if fp:
        item("Now vs peak", html.Span([money_cell(fp.get("value")), f" peak {short_date(fp.get('peak_date'))}"]),
             f"LTD {full_money(fp.get('ltd'))} on {fp.get('date')} against the peak "
             f"{full_money(fp.get('peak_value'))} on {fp.get('peak_date')}")
    if tr.get("days_with_fill"):
        items.append(marker(f"filled {tr['days_with_fill']}", f"{tr['days_with_fill']} counted day(s) include a trade "
                                                              "valued at an earlier close"))
    return html.Div(items, className="pnl-track")


# --------------------------------------------------------------------------- 7. data issues
def issue_items(data: dict) -> List[Any]:
    items: List[Any] = list(data.get("errors") or [])
    ex, key = data.get("explain") or {}, data.get("key", DEFAULT_PERIOD)
    title = PERIOD_TITLES.get(key, key)
    if data.get("note"):
        items.append(("As of", data["note"]))
    if ex:
        if not ex.get("available") and ex.get("reason"):
            items.append((title, str(ex["reason"])))
        if ex.get("ref_note"):
            items.append((title, str(ex["ref_note"])))
        for tid, why in ex.get("excluded") or []:
            items.append((str(tid), f"left out of {title}: {why}"))
        for o in ex.get("other_trades") or []:
            items.append((str(o["trade_id"]), f"{title}: {full_money(o['amount'])} under Other, not split between "
                                              f"price and currency ({o['why']})"))
    pp = data.get("period")
    if pp is not None and not pp.by_trade.empty:
        for tid, note in zip(pp.by_trade["trade_id"], pp.by_trade["note"]):
            if note:
                items.append((str(tid), str(note)))
    monthly = data.get("monthly")
    if monthly is not None:
        for m in monthly.months:
            if not m["available"]:
                items.append((_month_words(m["month"]), str(m.get("reason") or "no figure")))
            elif m.get("n_excluded"):
                items.append((_month_words(m["month"]), f"{m['n_excluded']} trade(s) left out of the month's total"))
    left = (data.get("track") or {}).get("excluded_days") or []
    if left:
        items.append(("Track record", f"{len(left)} day(s) with no Daily figure, left out: "
                                      + "; ".join(f"{d} ({why})" for d, why in left[:5])
                                      + (f"; and {len(left) - 5} more" if len(left) > 5 else "")))
    sc = data.get("scorecard") or {}
    if sc.get("excluded"):
        names = {str(i.get("position_id")): _idea_name(data, i) for i in sc.get("ideas") or []}
        items.append(("Scorecard", f"{len(sc['excluded'])} idea(s) left out of every statistic, a trade unpriced on "
                                   "the P&L day: " + ", ".join(names.get(str(p), str(p)) for p, _w in sc["excluded"][:5])
                                   + (f"; and {len(sc['excluded']) - 5} more" if len(sc["excluded"]) > 5 else "")))
    for r in carry_rows(data):
        if r.get("roll_down_usd") is None and r.get("reason"):
            items.append((f"Carry, {_carry_name(data, r)}", _no_path(r["reason"])))
    for e in (data.get("rolls") or {}).get("excluded") or []:
        items.append((f"Roll, {e.get('date')} {e.get('root_id')}", str(e.get("reason") or "")))
    review = (data.get("spreads") or {}).get("review") or []
    if review:
        items.append(("Positions", f"{len(review)} set(s) of trades could not be grouped into a spread, shown as "
                                   "outrights (the Book tab names them)."))
    return items


# --------------------------------------------------------------------------- the CSV
def csv_frame(data: dict, month_by: str = CHART_TYPE) -> pd.DataFrame:
    """Every position of the period with its split, and the month table (per month and group, and
    the month's total), at full figures."""
    records = []
    ex, key = data.get("explain") or {}, data.get("key", DEFAULT_PERIOD)
    for p in ex.get("by_position") or []:
        rec = {"table": f"positions {PERIOD_TITLES.get(key, key)}", "month": "",
               "line": position_name(data, str(p["position_id"]), str(p.get("name") or "")),
               "position_id": p["position_id"], "total_usd": p.get("total")}
        for k, _w in _SPLIT_WORDS:
            rec[f"{k}_usd"] = p.get(k)
        rec["excluded"] = p.get("n_excluded")
        rec["trade_ids"] = " ".join(p.get("trade_ids") or [])
        records.append(rec)
    groups, rows = month_records(data, month_by)
    words = dict(MONTH_OPTIONS).get(month_by, month_by).lower()
    for r in rows:
        for g in groups:
            hit = r["cells"].get(g)
            if hit is not None:
                records.append({"table": f"months by {words}", "month": r["month"], "line": g,
                                "total_usd": hit[0], "excluded": hit[1]})
        records.append({"table": "months", "month": r["month"], "line": "Total", "total_usd": r["total"],
                        "excluded": r["total_excluded"]})
    for i in (data.get("scorecard") or {}).get("ideas") or []:
        records.append({"table": "scorecard ideas", "line": _idea_name(data, i), "position_id": i.get("position_id"),
                        "trade_name": i.get("trade_name"), "spread_type": i.get("spread_type"),
                        "status": i.get("status"), "first_trade_date": i.get("first_trade_date"),
                        "close_date": i.get("close_date"), "holding_days": i.get("holding_days"),
                        "total_usd": i.get("pnl_usd"), "pnl_at_close_usd": i.get("pnl_at_close"),
                        "fx_since_close_usd": i.get("fx_since_close"), "best_ltd_usd": i.get("best_ltd"),
                        "worst_ltd_usd": i.get("worst_ltd"), "reason": i.get("reason"),
                        "trade_ids": " ".join(i.get("trade_ids") or [])})
    for r in carry_rows(data):
        records.append({"table": "carry (research, not P&L)", "line": _carry_name(data, r), "position_id": r.get("id"),
                        "total_usd": r.get("roll_down_usd"), "roll_down": r.get("roll_down"), "unit": r.get("unit"),
                        "research_date": r.get("research_date"), "reason": r.get("reason")})
    for r in (data.get("rolls") or {}).get("rolls") or []:
        records.append({"table": f"rolls {r.get('roll_kind')} (price spread at the roll, not P&L)",
                        "month": r.get("date"), "line": f"{r.get('root_name')} {r.get('label')}",
                        "trade_name": r.get("trade_name"), "direction": r.get("direction"), "lots": r.get("lots"),
                        "sold_price": r.get("sold_price"), "bought_price": r.get("bought_price"),
                        "roll_spread": r.get("roll_spread_unit"), "unit": r.get("unit"), "curve": r.get("curve"),
                        "total_usd": r.get("roll_yield_usd"), "reason": r.get("reason"),
                        "trade_ids": " ".join((r.get("from_trade_ids") or []) + (r.get("to_trade_ids") or []))})
    return pd.DataFrame(records)


# --------------------------------------------------------------------------- body and shell
_HIDDEN = {"display": "none"}


def parts(data: dict, chart_by: str = CHART_BOOK, month_by: str = CHART_TYPE, score_by: str = SCORE_BY_TRADE) -> dict:
    """{note, tiles, sections_style, chart, contributors, months, scorecard, carry, rolls, track,
    issues}: the callback's outputs, each section built in its own try."""
    chart_by = chart_by if chart_by in dict(CHART_OPTIONS) else CHART_BOOK
    month_by = month_by if month_by in dict(MONTH_OPTIONS) else CHART_TYPE
    if not data.get("n_trades"):                  # no blotter loaded: the Book's card (user, 2026-09-28)
        from ui.tabs.book import empty_state
        return {"note": "", "tiles": empty_state(idx="pnl"), "sections_style": _HIDDEN, "chart": None,
                "contributors": None, "months": None, "scorecard": None, "carry": None, "rolls": None, "track": None,
                "issues": None}
    out: Dict[str, Any] = {"note": data.get("note") or "", "sections_style": {}}
    for name, build in (("tiles", lambda: tiles(data)), ("chart", lambda: chart(data, chart_by)),
                        ("contributors", lambda: contributors(data)),
                        ("months", lambda: months_table(data, month_by)),
                        ("scorecard", lambda: scorecard_block(data, score_by)), ("carry", lambda: carry_block(data)),
                        ("rolls", lambda: rolls_block(data)), ("track", lambda: track_line(data))):
        try:
            out[name] = build()
        except Exception as exc:  # noqa: BLE001 -- the reason in its own section, never a blank tab
            log.exception("P&L tab: %s failed for %s", name, data.get("as_of"))
            out[name] = html.P(f"This section could not be built ({type(exc).__name__}: {exc}).",
                               className="status-line status-line--bad")
    out["issues"] = issues_drawer(issue_items(data), id=ISSUES_ID)
    return out


def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render_parts(as_of: Optional[str], db_path, period: str = DEFAULT_PERIOD, chart_by: str = CHART_BOOK,
                 month_by: str = CHART_TYPE, score_by: str = SCORE_BY_TRADE) -> dict:
    blank = {"note": "", "sections_style": _HIDDEN, "chart": None, "contributors": None, "months": None,
             "scorecard": None, "carry": None, "rolls": None, "track": None, "issues": None}
    if not as_of:
        return {**blank, "tiles": message_box("No as-of date available.")}
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return {**blank, "tiles": message_box(f"Database not available ({exc}).")}
    try:
        return parts(gather(conn, as_of, period or DEFAULT_PERIOD), chart_by or CHART_BOOK, month_by or CHART_TYPE,
                     score_by or SCORE_BY_TRADE)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("P&L tab failed for as_of=%s", as_of)
        return {**blank, "tiles": html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The P&L tab could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])}
    finally:
        conn.close()


def render(as_of: Optional[str], db_path, period: str = DEFAULT_PERIOD, chart_by: str = CHART_BOOK,
           month_by: str = CHART_TYPE, score_by: str = SCORE_BY_TRADE) -> html.Div:
    """The whole body for a direct render (the smoke test, a proof): the tiles, the chart, the
    contributors, the months, the scorecard, the carry, the rolls, the track record and the
    drawer, in the tab's order."""
    p = render_parts(as_of, db_path, period, chart_by, month_by, score_by)
    children = [html.Div(p["note"], className="book-quiet") if p["note"] else None, p["tiles"], p["chart"],
                p["contributors"], p["months"], p["scorecard"], p["carry"], p["rolls"], p["track"], p["issues"]]
    return html.Div(className="pnl-body", children=[c for c in children if c is not None])


def render_csv(as_of: Optional[str], db_path, period: str, month_by: str):
    if not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        data = gather(conn, as_of, period or DEFAULT_PERIOD)
        frame = csv_frame(data, month_by or CHART_TYPE) if data.get("n_trades") else pd.DataFrame()
        return dcc.send_data_frame(frame.to_csv, f"pnl-{period or DEFAULT_PERIOD}-{as_of}.csv", index=False)
    except Exception:  # noqa: BLE001
        log.exception("P&L csv failed for %s", as_of)
        return None
    finally:
        conn.close()


def _switch(id_: str, options: Sequence[Tuple[str, str]], default: str) -> dcc.RadioItems:
    return dcc.RadioItems(id=id_, className="book-switch", options=[{"label": lb, "value": v} for v, lb in options],
                          value=default, inline=True, persistence=True, persistence_type="session")


def _head(title: str, tip: str, *extra: Any) -> html.Div:
    return html.Div(className="book-section-head", children=[
        about(title, tip, level="h4", className="book-section-title"), *extra])


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: the title line (the period switch, the as-of note, Download CSV), then the
    sections' heads with their switches (always in the layout: a static callback input must be)
    and the slots the one callback fills. No date picker: the tab follows the header's as-of."""
    return html.Div(className="pnl-tab", children=[
        html.Div(className="book-title-row", children=[
            about("P&L", TAB_ABOUT, level="h3"),
            html.Span(QUESTION, className="book-counts"),
            _switch(PERIOD_ID, [(k, PERIOD_TITLES[k]) for k in PERIODS], DEFAULT_PERIOD),
            html.Span(id=NOTE_ID, className="book-counts pnl-note"),
            html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                        title="Every position of the period with its split, and the month table, at full figures"),
            dcc.Download(id=DOWNLOAD_ID),
        ]),
        html.Div(id=BODY_ID, className="pnl-body", children=[
            html.Div(id=TILES_ID, children=[message_box("Loading the P&L...")]),
            html.Div(id=SECTIONS_ID, children=[
                _head("LTD and daily P&L", CHART_ABOUT, _switch(CHART_BY_ID, CHART_OPTIONS, CHART_BOOK)),
                html.Div(id=CHART_ID, className="book-card pnl-chart-card"),
                _head("What drove it", CONTRIB_ABOUT),
                html.Div(id=CONTRIB_ID),
                _head("Month by month", MONTHS_ABOUT, _switch(MONTH_BY_ID, MONTH_OPTIONS, CHART_TYPE)),
                html.Div(id=MONTHS_ID),
                _head("Scorecard", SCORE_ABOUT, _switch(SCORE_BY_ID, SCORE_OPTIONS, SCORE_BY_TRADE)),
                html.Div(id=SCORE_ID),
                _head("Carry", CARRY_ABOUT),
                html.Div(id=CARRY_ID),
                _head("Rolls", ROLLS_ABOUT),
                html.Div(id=ROLLS_ID),
                _head("Track record", TRACK_ABOUT),
                html.Div(id=TRACK_ID),
                html.Div(id=ISSUES_SLOT_ID),
            ]),
        ]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """One callback fills every slot on the header's as-of, the three switches, every data
    revision and the safety interval (the engine memoises the series on the database revision,
    the Book memoises the positions); the CSV on its button."""

    @app.callback(
        Output(NOTE_ID, "children"), Output(TILES_ID, "children"), Output(SECTIONS_ID, "style"),
        Output(CHART_ID, "children"), Output(CONTRIB_ID, "children"), Output(MONTHS_ID, "children"),
        Output(SCORE_ID, "children"), Output(CARRY_ID, "children"), Output(ROLLS_ID, "children"),
        Output(TRACK_ID, "children"), Output(ISSUES_SLOT_ID, "children"),
        Input(AS_OF_STORE_ID, "data"), Input(PERIOD_ID, "value"), Input(CHART_BY_ID, "value"),
        Input(MONTH_BY_ID, "value"), Input(SCORE_BY_ID, "value"), Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, period=DEFAULT_PERIOD, chart_by=CHART_BOOK, month_by=CHART_TYPE, score_by=SCORE_BY_TRADE,
                _rev=None, _n=0):
        p = render_parts(as_of, get_db_path(), period, chart_by, month_by, score_by)
        return (p["note"], p["tiles"], p["sections_style"], p["chart"], p["contributors"], p["months"],
                p["scorecard"], p["carry"], p["rolls"], p["track"], p["issues"])

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(PERIOD_ID, "value"), State(MONTH_BY_ID, "value"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, period, month_by):
        if not n_clicks:
            return dash.no_update
        return render_csv(as_of, get_db_path(), period, month_by)
