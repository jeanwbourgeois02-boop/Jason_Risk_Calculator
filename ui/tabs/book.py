"""Book tab: the trade list (Phase G, 2026-09-29; CLAUDE.md "Screens redesign plan -> Phase G", the
design doc's "Table specs").

The trade is the unit: one row per trade name (Jason's PBRoot suffix, `JSHY10_ZNA1` and
`JSHY10.3_ZNA1` one trade), every leg and hedge under it, never split. Columns: Trade | Type |
What it is | Entry | Now | z | Move | Daily | LTD | Next | Flags; a row's caveats (a figure
leaving fills out) sit in one small "i" after its name (`formatting.row_info`), the research caveat
once on the z heading. Nothing sits between the header and the card (user, 2026-09-30): the card's
title strip holds the search, the Group switch and, only while a filter is set, the rows' MTD and
YTD. The table's first row is the total ("Book · 6 trades", "Filtered · 3 of 6"), sticky, with
Gross and Net USD in its What-it-is cell and the flag count in its Flags cell, equal unfiltered to
the top bar's Daily, MTD and LTD to the cent; the fully flat trades are in the closed fold at the
bottom. A click on a trade opens its panel under it: the flags, the legs (hedges last, in italics;
a Mixed trade's legs under its sub-spreads), the trade's footer (totals, USD per 1-unit move of the
level, the hedge line), the level since the first fill with its entry dashed and its rolls
marked, and links to its fills (Blotter), its P&L history and its risk, each filtered to it.

What is read, never recomputed:
  - `engine.spreads.trade_book` (`blotter_pricing.shared_trade_book`, once per database revision
    and as-of, shared with P&L and Risk): the type, what it is, legs, size and balance, level,
    carry, hedge, next date, flags, sub-spreads, rolls, gross and net;
  - the P&L per fill: `ui.tabs.pnl.period_rows` (the header's own split over the shared filled
    reader `priced_value_book`), summed per trade and per leg (display), so the total row is the
    header's figure;
  - `engine.risk.trades.trade_risk` (`blotter_pricing.shared_trade_risk`, computed on its own
    thread; the z column fills in a moment after the table): z, percentile, z at entry, the What it
    is hover's hedge % and correlation. Research figures carry the "mock history" marker while the
    research data is not real (`trade_filter.research_mark`);
  - `engine.spreads.trades.level_history` for the panel's chart (at most 60 closes).

Display rules: `ui.tabs.formatting` (k / m with one decimal and the sign on trade rows, full
figures on legs, an em dash with its reason for anything missing). `trade_types`, `_labels`,
`_spreads`, `empty_state` and `trades_on_file` stay for the Blotter, P&L, Exposure and Risk tabs.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dcc, html

from ui import sample_book
from ui.revision import DATA_REVISION_ID
from ui.tabs import trade_filter as tf
from ui.tabs.formatting import (
    cap, cap_parts, tidy,
    EN_DASH, MINUS, MISSING, about, compact, contract_name, day_text, format_cell, full_signed, fx_name, is_fx_pair,
    issues_drawer, km_cell, km_text, marker, missing_cell, month_label, paren, parse_contract_id, pct_text,
    plain_words, price_text, quoted_unit, row_info, short_date, short_root_name, sign_class, strike_text, sum_known,
    z_text,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "book-body"                        # a message or the empty state, above the table
CONTENT_ID = "book-content"                  # the bar, the headline, the table card (hidden with no book)
HEADLINE_ID = "book-headline"
TABLE_SLOT_ID = "book-table-slot"
TABLE_ID = "book-table"
FOOT_ID = "book-foot"
CSV_BUTTON_ID = "book-csv"
DOWNLOAD_ID = "book-download"
EXPAND_ALL_ID = "book-expand-all"
COLLAPSE_ALL_ID = "book-collapse-all"
OPEN_STORE_ID = "book-open-trades"           # session: the trade names whose panel is open
SORT_STORE_ID = "book-sort-store"            # session: {"key", "dir": "desc" | "asc"} or None
FOLD_STORE_ID = "book-folds"                 # session: {"groups": [folded group labels], "closed": bool}
SHOWN_STORE_ID = "book-shown"                # the trade names showing (Expand all)
RISK_READY_ID = "book-risk-ready"
RISK_POLL_ID = "book-risk-poll"
ROW_TYPE = "book-trade-row"                  # a trade row: {"type", "idx": trade name}
SORT_TYPE = "book-sort"                      # a column title: {"type", "idx": column key}
GROUP_ROW_TYPE = "book-group-row"            # a group header: {"type", "idx": group label}
CLOSED_FOLD_ID = "book-closed-fold"
EMPTY_UPLOAD_TYPE = "book-empty-upload"      # the empty state's Upload button (clicks the top bar's input)
EMPTY_UPLOAD_SINK_ID = "book-empty-upload-sink"   # in the app's layout (ui/app.py), so every tab's card works
TAB = "book"
HISTORY_POINTS = 60
NA = MISSING
HIDDEN = {"display": "none"}
PERIODS = ("daily", "mtd", "ytd", "ltd")
PREPULL_TEXT = "Levels and P&L fill in after the first Bloomberg pull; fills and sizes are already right"
TAB_KEYS = {"Book": "book", "P&L": "pnl", "Risk": "risk", "Blotter": "blotter", "Data": "market-data"}

# The columns: key, title, alignment class ('l' = left), the title's one-sentence definition.
COLUMNS: Tuple[Tuple[str, str, str, str], ...] = (
    ("trade", "Trade", "l", "Jason's trade name, the text after the underscore of the PBRoot; click a row for its legs."),
    ("type", "Type", "l", "The trade's type by rule from its legs (hedges apart): calendar, cross-exchange, "
                          "cross-product, mixed or outright; a type mismatch with the PBRoot is in Flags."),
    ("what", "What it is", "l", "The trade in plain words: each side's lots, long or short, the first side first "
                                "(an FX or metal forward or FX option its notional), then the share of its currency "
                                "exposure hedged; the value per side, the balance, the USD per 1-unit move of the "
                                "level and the hedge % on hover."),
    ("entry", "Entry", "", "The level at entry: the fills' size-weighted level (a calendar near minus far in its "
                           "unit, a China-against-West pair the converted ratio China over foreign)."),
    ("now", "Now", "", "The same level at the latest official marks."),
    ("z", "z", "", "Where Now sits in one year of this exact level: (Now minus the mean) over the standard "
                   "deviation of the last 252 settlements (research history)."),
    ("today", "Move", "", "The level's move since the previous close, in the level's own unit, green when it helps "
                          "the trade; a dash when it did not move."),
    ("daily", "Daily", "", "Today's P&L in USD, every fill of the trade including its hedge."),
    ("ltd", "LTD", "", "The P&L since the trade opened, in USD."),
    ("next", "Next", "l", "The nearest key date of an open leg: first notice, last trade, option expiry or LME "
                          "prompt; red within 3 business days, amber within 10, grey ≈ when estimated."),
    ("flags", "Flags", "l", "The trade's most severe flag in words, and how many more: unbalanced, hedge too big, "
                            "type mismatch, no price, price to check; red when a contract is not recognised. Each "
                            "flag's sentence on hover."),
)
NEXT_ABBR = {"first notice": "FN", "last trade": "LT", "option expiry": "Exp", "LME prompt": "Prompt",
             "expiry": "Exp", "prompt": "Prompt", "value date": "Value"}
TITLE_ID = "book-title"                      # the strip's title: "Trades" or "Contracts" (the view)
CARD_ID = "book-main-card"                   # the table card; a class names the view (the CSS hides Group by contract)
CARD_CLASS = "book-card book-main tk-card"
CONTRACT_COL = "c-"                          # the By contract view's own column filters (ui/tabs/book_contracts.py)
LEG_COLUMNS = (("leg", "Leg", "l"), ("side", "Side", "l"), ("lots", "Lots", ""), ("value", "Value USD", ""),
               ("fill", "Avg fill", ""), ("mark", "Mark", ""), ("daily", "Daily", ""), ("ltd", "LTD", ""),
               ("roll", "Roll-down / mo", ""))


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def _lines(*parts: Any) -> str:
    return "\n".join(str(p) for p in parts if p)


METAL_PAIRS = ("XAU", "XAG", "XPT", "XPD")
NBSP = "\u00a0"        # holds an empty unit slot open (a ratio's Now), so the Now figures line up


def unit_words(unit: Optional[str]) -> str:
    """'$/bbl' for 'USD/bbl'; '$/oz' for a precious-metal pair ('XAUUSD'); '' for a ratio or another
    currency pair's rate (neither has a unit)."""
    u = str(unit or "")
    if u == "ratio":
        return ""
    if is_fx_pair(u[:6]) and len(u) == 6:
        return "$/oz" if u[:3] in METAL_PAIRS and u[3:] == "USD" else ""
    return u.replace("USD/", "$/").replace("/mwh", "/MWh").replace("/mmbtu", "/MMBtu")


def level_text(value: Any, level: dict, signed: bool = False) -> str:
    """A level in its own format: a ratio at 4 decimals, a price difference at the unit's tick,
    with a real minus. `signed`: a move of the level (the Move column), a fall in brackets and a
    rise plain (the money rule of 2026-09-30, which the user asked for on the Move column too)."""
    v = _num(value)
    if v is None:
        return MISSING
    if level.get("mode") == "ratio" or level.get("unit") == "ratio":
        body = f"{abs(v):.4f}"
        if signed:
            return paren(body, v < 0 and bool(float(body)))
        return (MINUS if v < 0 and float(body) else "") + body
    if signed:
        body = price_text(abs(v), str(level.get("unit") or ""))
        return paren(body, v < 0 and bool(body.strip("0.,")))
    return price_text(v, str(level.get("unit") or ""))


def _level_cell(value: Any, level: dict, reason: str, hover: str = "", estimated: bool = False,
                className: str = "", with_unit: bool = False) -> Any:
    """A level figure at the row's one precision (`level_text`: the unit's tick, 4 decimals for a
    ratio or a premium with no unit), grey after a "≈" when `estimated`. The unit is said once per
    row, on Now (`with_unit`), in a fixed-width slot so the Now figures line up as a column."""
    v = _num(value)
    if v is None:
        return missing_cell(_lines(reason, hover) or "no level")
    unit = unit_words(level.get("unit")) if with_unit else ""
    return html.Span([("≈ " if estimated else "") + level_text(v, level),
                      html.Span(unit or NBSP, className="cell-unit tk-unit-slot") if with_unit else None],
                     className=" ".join(c for c in (className, "cell-estimated" if estimated else "") if c) or None,
                     title=plain_words(hover) or None)


def level_unit_line(level: dict) -> str:
    """The row's level unit in words for a hover ('In $/bbl', 'A ratio, no unit', 'Net premium per
    unit, in $/bbl', 'Premium as a fraction of notional'): the unit is shown once, on Now."""
    unit = unit_words(level.get("unit"))
    if level.get("mode") == "premium":
        return f"Net premium per unit, in {unit}" if unit else "Premium as a fraction of notional"
    if level.get("mode") == "ratio" or level.get("unit") == "ratio":
        return "A ratio, no unit"
    return f"In {unit}" if unit else ""


def estimate_words(level: dict, *ends: str) -> List[str]:
    """The engine's estimate notes of `ends` ('entry', 'prev', 'now'), one hover line each; a note
    the level's own source line for that end already carries is not repeated."""
    names = {"entry": "Entry", "prev": "Previous close", "now": "Now"}
    said = " ".join(str(v) for v in (level.get("sources") or {}).values())
    return [f"{names[e]} estimated: {level.get(e + '_estimate_note')}" for e in ends
            if level.get(e + "_estimated") and level.get(e + "_estimate_note")
            and str(level.get(e + "_estimate_note")) not in said]


def _usd_per_move(level: dict) -> Tuple[Optional[float], str]:
    """(USD of a move, 'USD per 0.01 of the ratio' / 'USD per 1 $/bbl of the level')."""
    usd = _num(level.get("usd_per_unit"))
    if usd is None:
        return None, str(level.get("usd_per_unit_reason") or "not given")
    if level.get("mode") == "ratio" or level.get("unit") == "ratio":
        return usd * 0.01, "USD per 0.01 of the ratio"
    return usd, f"USD per 1 {unit_words(level.get('unit')) or 'unit'} of the level"


def _roots() -> Dict[str, Any]:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- the ids stand for the names
        return {}


def _memo(kind: str, conn: sqlite3.Connection, as_of: str, build: Callable[[], Any], extra: tuple = ()) -> Any:
    from ui.tabs.blotter_pricing import screen_memo
    return screen_memo("book-" + kind, conn, as_of, build, extra=extra)


# --------------------------------------------------------------------------- read by other tabs (kept)
def _spreads(conn: sqlite3.Connection, as_of: str) -> dict:
    """`book_spreads` through the screens' shared filled reader, memoised (the Blotter reads it)."""
    from ui.tabs.blotter_pricing import shared_spreads
    return shared_spreads(conn, as_of, filled=True)


def _labels(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{trade_id: {strategy, trade_type, pb_root, broker_symbol}}: the broker's labels as on file."""
    try:
        return {str(t): {"strategy": str(st or ""), "trade_type": str(tt or ""), "pb_root": str(pb or ""),
                         "broker_symbol": str(sym or "")}
                for t, st, tt, pb, sym in conn.execute(
                    "SELECT trade_id, strategy, trade_type, pb_root, broker_symbol FROM trades")}
    except sqlite3.Error:
        pass
    try:
        return {str(t): {"strategy": str(st or ""), "trade_type": str(tt or ""), "pb_root": str(pb or ""),
                         "broker_symbol": ""}
                for t, st, tt, pb in conn.execute("SELECT trade_id, strategy, trade_type, pb_root FROM trades")}
    except sqlite3.Error:
        return {}


def trade_types(data: dict) -> Dict[str, dict]:
    """{trade_id: {trade_type, type_source, type_note, position}}: the type spreads-engine gave the
    position the trade is in, else the trade's own label (the Blotter reads it)."""
    cached = data.get("_trade_types")
    if cached is not None:
        return cached
    out: Dict[str, dict] = {}
    for tid, lab in (data.get("labels") or {}).items():
        out[str(tid)] = {"trade_type": lab.get("trade_type", ""), "type_source": "label" if lab.get("trade_type") else "",
                         "type_note": "the broker's label on the trade" if lab.get("trade_type") else "", "position": ""}
    result = data.get("spreads") or {}
    for p in result.get("positions") or []:
        entry = {k: str(p.get(k) or "") for k in ("trade_type", "type_source", "type_note")}
        entry["position"] = str(p.get("strategy") or p.get("name") or "")
        for tid in p.get("trade_ids") or []:
            out[str(tid)] = dict(entry)
    for o in result.get("outrights") or []:
        if o.get("trade_type"):
            out[str(o.get("trade_id"))] = {**{k: str(o.get(k) or "") for k in ("trade_type", "type_source", "type_note")},
                                          "position": ""}
    data["_trade_types"] = out
    return out


def trades_on_file(conn: sqlite3.Connection) -> int:
    """How many trades the database holds: 0 = no blotter loaded (every tab's empty-state test)."""
    try:
        return int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
    except sqlite3.Error:
        return 0


def empty_state(data: Optional[dict] = None, idx: str = "book") -> html.Div:
    """No trades on file: the upload card and the three steps. `idx` names the tab rendering it, so
    the Upload button's and the sample link's pattern ids differ per tab."""
    return html.Div(className="book-empty", children=[
        html.Div(className="book-card book-empty-main", children=[
            html.Div("No blotter loaded", className="book-empty-title"),
            html.Div("Upload Jason's blotter export, a .csv or .xlsx straight from the prime broker. An upload "
                     "merges by Trade Id; nothing else is ever typed in.", className="book-empty-text"),
            html.Div(className="book-empty-actions", children=[
                html.Button("Upload blotter", id={"type": EMPTY_UPLOAD_TYPE, "idx": idx}, n_clicks=0, className="btn",
                            title="Choose the blotter file (the same upload as the top bar's)"),
                *([sample_book.view_link(idx, small=False)] if not sample_book.is_sample_active() else []),
            ])]),
        html.Div(className="book-card book-empty-next", children=[
            html.Div("What happens next", className="book-h"),
            html.Div(className="book-step", children=[html.Span("1", className="book-step-num"), html.Span([
                html.B("Upload."), " The Book fills with one row per trade; the Blotter says what became a "
                                   "trade and what did not."])]),
            html.Div(className="book-step", children=[html.Span("2", className="book-step-num"), html.Span([
                html.B("Pull Bloomberg"), " on the Bloomberg PC, or import the marks snapshot here. Until then every "
                                          "trade shows its fills and sizes and a dash for its value."])]),
            html.Div(className="book-step", children=[html.Span("3", className="book-step-num"), html.Span([
                html.B("Read."), " Book for what you hold and what is due, P&L for where it came from, Risk for "
                                 "what it can lose, Data for whether to trust it."])]),
        ])])


_OPEN_UPLOAD_JS = (
    "function(ns) {\n"
    "    var n = (ns || []).filter(function(x) { return x; }).length;\n"
    "    if (!n) { return window.dash_clientside.no_update; }\n"
    "    var box = document.getElementById('report-file');\n"
    "    var input = box ? box.querySelector('input[type=file]') : null;\n"
    "    if (input) { input.click(); }\n"
    "    return n;\n"
    "}"
)


# --------------------------------------------------------------------------- gathering
def _fill_info(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{trade_id: {trade_date, quantity, price, broker_price, broker_symbol, pb_root, instrument_id,
    product}} as on file (display: the fills behind a leg's average, the raw PBRoot cells)."""
    cols = "trade_id, trade_date, quantity, price, instrument_id, product, pb_root, broker_price, broker_symbol, strategy"
    for sql in (f"SELECT {cols} FROM trades", "SELECT trade_id, trade_date, quantity, price, instrument_id, product, "
                                              "pb_root, '', '', strategy FROM trades",
                "SELECT trade_id, trade_date, quantity, price, instrument_id, product, '', '', '', '' FROM trades"):
        try:
            return {str(r[0]): {"trade_date": str(r[1] or ""), "quantity": _num(r[2]), "price": _num(r[3]),
                                "instrument_id": str(r[4] or ""), "product": str(r[5] or ""), "pb_root": str(r[6] or ""),
                                "broker_price": str(r[7] or ""), "broker_symbol": str(r[8] or ""),
                                "strategy": str(r[9] or "")}
                    for r in conn.execute(sql)}
        except sqlite3.Error:
            continue
    return {}


def _has_marks(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT 1 FROM marks_official LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def _unassigned_trade(ids: Sequence[str], df: pd.DataFrame, fills: Dict[str, dict], roots: Dict[str, Any],
                      why: str) -> dict:
    """The pseudo-trade of the fills on no trade (no PBRoot name, or a trade the engine could not
    read): its own row so every fill is in exactly one row and the total stays the header's."""
    rows = df[df["trade_id"].isin(ids)] if not df.empty else df
    open_ = bool(not rows.empty and (rows["status"] == "OPEN").any())
    legs = []
    for tid in ids:
        f = fills.get(tid) or {}
        inst = f.get("instrument_id", "")
        legs.append({"name": _fill_name(inst, f.get("product", ""), roots), "contract_id": inst,
                     "broker_symbols": [f.get("broker_symbol", "")], "trade_ids": [tid]})
    return {"trade": tf.UNASSIGNED, "pseudo": True, "trade_ids": list(ids), "status": "open" if open_ else "closed",
            "type": "", "commodity_family": "", "what_it_is": f"{_plural(len(ids), 'fill')} on no trade", "legs": legs,
            "flags": [{"code": "no_trade", "label": "no trade name", "sentence": why}], "level": {}, "size": {},
            "hedge": {}, "next": None, "next_reason": "fills on no trade: no key date read", "pnl": {},
            "gross_usd": None, "net_usd": None, "notional_reason": "fills on no trade", "sub_spreads": [],
            "pb_roots": sorted({(fills.get(t) or {}).get("pb_root", "") for t in ids} - {""}),
            "first_trade_date": min(((fills.get(t) or {}).get("trade_date", "") for t in ids), default="")}


def _fill_name(inst: str, product: str, roots: Dict[str, Any]) -> str:
    if product.startswith("FX"):
        return fx_name(inst[:6], product)
    return contract_name(inst, None, "") or inst.split(" ")[0]


def gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """Everything the Book shows for `as_of` but the risk figures (computed apart, so the table does
    not wait for them), once per database revision, as-of and research inputs. Shared: never edit."""
    from ui.tabs.blotter_pricing import research_inputs_key
    return _memo("gather", conn, as_of, lambda: _gather(conn, as_of), extra=research_inputs_key())


def _gather(conn: sqlite3.Connection, as_of: str) -> dict:
    from ui.tabs.blotter_pricing import pricing_snapshot, priced_value_book, shared_trade_book
    from ui.tabs.pnl import period_rows

    data: Dict[str, Any] = {"as_of": as_of, "errors": [], "n_trades": trades_on_file(conn)}
    if not data["n_trades"]:
        return data
    roots = _roots()
    data["roots"] = roots
    data["fills"] = fills = _fill_info(conn)
    data["has_marks"] = _has_marks(conn)
    with pricing_snapshot(conn, "Book"):
        df = priced_value_book(conn, as_of)[0]
        data["df"] = df
        per: Dict[str, Dict[str, tuple]] = {}
        views = {}
        for key in PERIODS:
            try:
                view = period_rows(conn, as_of, key, df)
                views[key] = view
                per[key] = {str(t): (_num(v), str(r or ""), str(n or ""))
                            for t, v, r, n in zip(view.rows["trade_id"], view.rows["value"], view.rows["reason"],
                                                  view.rows["note"])}
            except Exception as exc:  # noqa: BLE001 -- that period's column says why
                log.exception("Book: the %s figures failed for %s", key, as_of)
                per[key] = {}
                data["errors"].append(("P&L", f"the {key.upper()} figures could not be split per trade "
                                              f"({type(exc).__name__}: {exc})"))
        data["per_fill"], data["views"] = per, views
        try:
            tb = shared_trade_book(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- every fill then sits on the no-trade row, said so
            log.exception("Book: the trade book failed for %s", as_of)
            tb = {"trades": [], "unassigned": [], "notes": []}
            data["errors"].append(("Trades", f"the trades could not be read ({type(exc).__name__}: {exc})"))
    data["notes"] = list(tb.get("notes") or [])
    trades = list(tb.get("trades") or [])
    covered = {str(t) for tr in trades for t in tr.get("trade_ids") or []}
    on_file = [str(t) for t in df["trade_id"]] if not df.empty else []
    left = [t for t in on_file if t not in covered]
    if left:
        unassigned = set(str(t) for t in tb.get("unassigned") or [])
        why = ("these fills carry no trade name (no PBRoot suffix): they are on no trade"
               if set(left) <= unassigned else
               "these fills are on no trade the engine could read (the Data issues say why)")
        trades.append(_unassigned_trade(left, df, fills, roots, why))
    # What it is in words (the lots, long or short, first side first), once per gather: the shared
    # trade dicts are copied, never edited; the search reads `what_words` (trade_filter._search_blob)
    data["trades"] = [_with_words(t, roots) for t in trades]
    data["research"] = tf.research_source()
    return data


# --------------------------------------------------------------------------- prices to check (the marks check)
CHECK_LABEL = "price to check"


def price_checks(conn: sqlite3.Connection, as_of: str) -> dict:
    """{"fills": {trade_id: [sentence]}, "legs": {(instrument_id, trade_id): [sentence]}}: every official
    mark the Data tab's marks check flags (`data.bloomberg.inventory.mark_checks`, status CHECK, read
    through `data_checks.check_frame`, the Data tab's own memo) against the fills it prices (`blocks`).
    A flagged price stays the official mark the P&L uses; the Book only says so (an amber flag and a
    grey ≈ on the figures it drives). Memoised per revision, as-of and book day."""
    from data.bloomberg.live import book_today
    today = book_today().isoformat()

    def compute():
        from ui.tabs import data_checks as dc
        fills: Dict[str, List[str]] = {}
        legs: Dict[tuple, List[str]] = {}
        for row in dc.mark_records(conn, as_of):
            if row.get("status") != "CHECK":
                continue
            words = dc.problem_words(row, as_of, today)[0]
            sentence = f"{row['name']}: {words[:1].lower() + words[1:]}"
            for tid in row.get("blocks") or []:
                fills.setdefault(str(tid), []).append(sentence)
                legs.setdefault((str(row.get("instrument_id") or ""), str(tid)), []).append(sentence)
        return {"fills": fills, "legs": legs}
    return _memo("price-checks", conn, as_of, compute, extra=(today,))


def check_lines(data: dict, ids: Sequence[str]) -> List[str]:
    """The flagged prices behind these fills, one sentence each (none: [])."""
    by = (data.get("checks") or {}).get("fills") or {}
    return list(dict.fromkeys(x for i in ids for x in by.get(str(i), [])))


def leg_check_lines(data: dict, leg: dict) -> List[str]:
    by = (data.get("checks") or {}).get("legs") or {}
    inst = str(leg.get("instrument_id") or leg.get("contract_id") or "")
    return list(dict.fromkeys(x for i in leg.get("trade_ids") or [] for x in by.get((inst, str(i)), [])))


def with_checks(data: dict, checks: dict) -> dict:
    """The gathered book with the price checks on it: a shallow copy (the shared gather is never
    edited) whose trades carry one amber "price to check" flag per flagged price behind their fills."""
    out = dict(data, checks=checks)
    if not (checks or {}).get("fills"):
        return out
    trades = []
    for t in data.get("trades") or []:
        lines = check_lines(out, [str(i) for i in t.get("trade_ids") or []])
        if lines:
            t = dict(t, flags=list(t.get("flags") or []) + [
                {"code": "price_check", "label": CHECK_LABEL, "sentence": x, "severity": "amber"} for x in lines])
        trades.append(t)
    out["trades"] = trades
    return out


def check_mark(lines: Sequence[str]):
    """The grey ≈ before a figure a flagged price drives (its sentences on hover), or None."""
    if not lines:
        return None
    return html.Span("≈ ", className="cell-estimated",
                     title=plain_words(_lines("Price to check: this figure uses a price the Data tab's marks check "
                                              "flags", *lines)))


def gathered(conn: sqlite3.Connection, as_of: str) -> dict:
    """`gather` with the price checks on it (`with_checks`): what the Book renders."""
    data = gather(conn, as_of)
    if not data.get("n_trades"):
        return data
    try:
        checks = price_checks(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the table still shows; the drawer says why
        log.exception("Book: the marks check could not be read for %s", as_of)
        return dict(data, errors=list(data.get("errors") or []) + [
            ("Marks check", f"the prices to check could not be read ({type(exc).__name__}: {exc})")])
    return with_checks(data, checks)


# --------------------------------------------------------------------------- figures per trade
def fill_sum(data: dict, key: str, ids: Sequence[str]) -> Tuple[Optional[float], int, List[str]]:
    """(known sum, count excluded, reasons) of the period `key` over the fills `ids`: each fill's
    `period_rows` figure (the header's split), summed (display)."""
    per = (data.get("per_fill") or {}).get(key) or {}
    vals = []
    for t in ids:
        v, r, _n = per.get(str(t), (None, f"{t}: no {key.upper()} figure", ""))
        vals.append((v, f"{t}: {r}" if r else f"{t}: no figure"))
    return sum_known(vals)


def _excl_hover(n: int, reasons: Sequence[str]) -> str:
    if not n:
        return ""
    head = f"excludes {_plural(n, 'fill')} with no figure"
    return _lines(head, *list(dict.fromkeys(reasons))[:8])


def money_td(total: Optional[float], n_excl: int, reasons: Sequence[str], hover: str = "",
             className: str = "", check: Sequence[str] = (), row: bool = False) -> html.Td:
    """A k / m money cell with its 'excl. N' marker (the reasons on hover); the dash with every
    reason when nothing is known. With `check` (the flagged prices behind it) the figure is grey,
    never green or red, after a grey ≈: a price to check never reads as a real gain or loss. On a
    trade row (`row`) there is no marker: the figure's own hover says what it leaves out and the
    row's one "i" gathers it (`excl_line`, layout wave 2026-09-29); totals keep their marker."""
    if total is None:
        return html.Td(missing_cell(_lines(*list(dict.fromkeys(reasons))[:8]) or "no figure"), className=className or None)
    if row:
        return html.Td([check_mark(check), km_cell(total, hover=_lines(hover, _excl_hover(n_excl, reasons)),
                                                   colour=not check, className="cell-estimated" if check else "")],
                       className=className or None)
    return html.Td([check_mark(check), km_cell(total, hover=hover, colour=not check,
                                               className="cell-estimated" if check else ""),
                    marker(f"excl. {n_excl}", _excl_hover(n_excl, reasons), "marker--small") if n_excl else None],
                   className=className or None)


def excl_line(label: str, fig: Tuple[Optional[float], int, List[str]]) -> str:
    """The row "i"'s line for a figure that leaves fills out ('' when it leaves none out, or when
    nothing is known: the cell's own dash then says why): 'Daily excludes 1 fill with no figure:
    910000032: contract not recognised ...'."""
    total, n, reasons = fig
    if not n or total is None:
        return ""
    return f"{label} excludes {_plural(n, 'fill')} with no figure: " + "; ".join(list(dict.fromkeys(reasons))[:4])


def _split_hover(t: dict) -> str:
    split = (t.get("pnl") or {}).get("split")
    if not split:
        why = (t.get("pnl") or {}).get("split_reason")
        return f"No split of today: {why}" if why else ""
    words = (("spread", "spread"), ("fx", "FX"), ("hedge", "hedge"), ("new_trades", "new trades"),
             ("realised", "realised"), ("other", "other"))
    parts = [f"{w} {km_text(split.get(k))}" for k, w in words if _num(split.get(k))]
    return "Split: " + (" · ".join(parts) if parts else "no change")


def is_estimated_mark(source: Any) -> bool:
    return str(source or "").startswith("INTERP")


def _estimated_level(t: dict) -> bool:
    return any(is_estimated_mark(leg.get("mark_source")) for leg in t.get("legs") or []
               if leg.get("status") == "open" and not leg.get("hedge"))


def closed_on(data: dict, t: dict) -> str:
    """The day a closed trade went flat: the engine's close_date, else its last fill's date."""
    got = str((t.get("closed") or {}).get("close_date") or "")
    if got:
        return got
    fills = data.get("fills") or {}
    return max(((fills.get(str(i)) or {}).get("trade_date", "") for i in t.get("trade_ids") or []), default="")


def risk_rows(risk: Optional[dict]) -> Dict[str, dict]:
    return {str(r.get("trade")): r for r in (risk or {}).get("trades") or []}


# --------------------------------------------------------------------------- the cells
def _type_td(t: dict) -> html.Td:
    """The type in words; a mismatch with the PBRoot is on hover here and counted in the Flags
    column (layout wave 2026-09-29: no stray dot after the type)."""
    mismatch = str(t.get("type_mismatch") or "")
    words = tf.trade_type_label(t, short=True) if not t.get("pseudo") else MISSING
    hover = _lines(t.get("type_note") or "", mismatch and f"Type mismatch (see Flags): {mismatch}")
    return html.Td(html.Span(words, title=plain_words(hover) or None), className="l")


def coverage_words(hedge: dict) -> str:
    cov = _num((hedge or {}).get("coverage"))
    if cov is None:
        return ""
    return f"{hedge.get('currency') or 'CNH'} {pct_text(cov)} hedged"


_HEDGED_TAIL = re.compile(r",?\s*\b[A-Z]{3} hedged$")


def what_text(t: dict) -> str:
    """What the trade is, in words: the engine's sentence; for a closed trade (the engine says
    "nothing open") the contracts it held."""
    what = str(t.get("what_it_is") or "")
    if t.get("status") == "closed" and what.strip().lower() in ("", "nothing open"):
        legs = [leg for leg in t.get("legs") or [] if not leg.get("hedge")] or list(t.get("legs") or [])
        names = list(dict.fromkeys(str(leg.get("name") or "") for leg in legs if leg.get("name")))
        if names:
            return "Held " + " / ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
    return what


# What it is in words (user, 2026-09-30, the Size column gone: "this shitty, clunky lot"): each
# side's lots long or short, the first side first, in one plain sentence ("Short 30 SHFE copper vs
# long 4 COMEX copper, Nov/Dec26"). Display only: the legs' own lots summed per side and contract.
_HEDGE_WORDS = re.compile(r",\s*([A-Z]{3}(?:/[A-Z]{3})* hedged)\s*$")
_FX_DAY = re.compile(r"\b(\d{1,2}) ([A-Z][a-z]{2}) (\d{2})\b")
_FX_OPTION_TAIL = re.compile(r"\b(call|put) ([\d.,]+)$")


def _count_words(v: float) -> str:
    """'30', '1,500', '2.5': a count in full with its thousands separator, no sign."""
    return f"{abs(v):,.2f}".rstrip("0").rstrip(".")


def _commodity_words(root: Any, root_id: str, exchange: str) -> str:
    """'copper', 'WTI', 'Brent', 'heating oil', 'NBP gas': the root's short name without its exchange
    word, in lower case where the contract list itself writes it so (never 'wti' or 'brent')."""
    words = short_root_name(root, root_id).split()
    if exchange and len(words) > 1 and words[0].upper() == exchange.upper():
        words = words[1:]
    text = " ".join(words)
    first = words[0] if words else ""
    csv_name = str(getattr(root, "name", "") or "")
    if first[:1].isupper() and first[1:].islower() and re.search(rf"\b{re.escape(first.lower())}\b", csv_name):
        text = text[:1].lower() + text[1:]
    return text


def _leg_month(leg: dict) -> Optional[Tuple[int, int]]:
    m = str(leg.get("month") or "")
    if len(m) >= 7 and m[:4].isdigit() and m[5:7].isdigit():
        return int(m[:4]), int(m[5:7])
    p = parse_contract_id(str(leg.get("contract_id") or leg.get("instrument_id") or ""))
    return (p["year"], p["month"]) if p else None


def _months_words(months: Sequence[Tuple[int, int]]) -> str:
    """'Dec26', 'Nov/Dec26', 'Dec26/Jan27', 'Oct26–Jan27'."""
    ms = sorted(set(months))
    if not ms:
        return ""
    if len(ms) == 1:
        return month_label(ms[0][1], ms[0][0])
    first, last = month_label(ms[0][1], ms[0][0]), month_label(ms[-1][1], ms[-1][0])
    if len(ms) == 2:
        return f"{first[:3]}/{last}" if ms[0][0] == ms[-1][0] else f"{first}/{last}"
    return f"{first}{EN_DASH}{last}"


def _fx_label(leg: dict) -> str:
    """'XAUUSD 16 Dec26 forward', 'EURUSD 18 Nov26 1.1800 call' from the leg's own name."""
    name = _FX_DAY.sub(r"\1 \2\3", str(leg.get("name") or ""))
    return _FX_OPTION_TAIL.sub(r"\2 \1", name)


def _word_items(legs: Sequence[Tuple[dict, float]], roots: Dict[str, Any]) -> List[dict]:
    """One item per thing held: futures and options per contract (per root and option type when the
    trade holds several commodities, their months then said once at the end), an FX or metal
    forward or FX option per leg. Each with its sign and its lots or notional, summed (display)."""
    lot = [(leg, q) for leg, q in legs if str(leg.get("product") or "") in LOT_PRODUCTS]
    multi = len({str(leg.get("root_id") or "") for leg, _q in lot}) > 1
    items: Dict[tuple, dict] = {}
    for leg, q in legs:
        product, rid = str(leg.get("product") or ""), str(leg.get("root_id") or "")
        sign = 1 if q > 0 else -1
        if product in LOT_PRODUCTS:
            p = parse_contract_id(str(leg.get("contract_id") or "")) or {}
            opt = str(p.get("option_type") or "") if product != "FUTURE" and product != "LME_FWD" else ""
            month = _leg_month(leg)
            when = (f"{short_date(leg.get('prompt'))}{str(month[0])[2:]}" if product == "LME_FWD" and leg.get("prompt")
                    and month else (month_label(month[1], month[0]) if month else ""))
            key = (sign, rid, opt) if multi else (sign, rid, when, opt, str(p.get("strike") or ""))
            exchange = str(leg.get("exchange") or "")
            item = items.setdefault(key, {"kind": "lots", "sign": sign, "q": 0.0, "root": rid, "exchange": exchange,
                                          "commodity": _commodity_words(roots.get(rid), rid, exchange),
                                          "when": when, "opt": opt, "strike": str(p.get("strike") or ""),
                                          "months": []})
            item["q"] += abs(q)
            if month:
                item["months"].append(month)
        else:
            unit, money = _base_unit(leg)
            label = _fx_label(leg)
            item = items.setdefault((sign, str(leg.get("contract_id") or label)),
                                    {"kind": "fx", "sign": sign, "q": 0.0, "unit": unit, "money": money,
                                     "pair": label.split(" ")[0], "label": label})
            item["q"] += abs(q)
    return list(items.values())


def _sides_of(t: dict, items: List[dict]) -> List[List[dict]]:
    """The items in two sides, the trade's first side first (`size.sides`: its roots, or its sign
    when both sides hold one root, a calendar); a side of several commodities against one (a
    crack, a crush) goes second, so the input reads first. Inside a side the largest first."""
    eng = [s for s in (t.get("size") or {}).get("sides") or [] if _num(s.get("lots"))]
    roots = {str(i.get("root")) for i in items if i["kind"] == "lots"}
    a_roots = {str(r) for r in (eng[0].get("root_ids") or [])} if eng else set()
    if a_roots and (roots - a_roots) and (roots & a_roots):
        a = [i for i in items if i["kind"] == "lots" and i["root"] in a_roots]
    else:
        sign = (1 if (_num(eng[0].get("lots")) or 0) > 0 else -1) if eng else (
            1 if any(i["sign"] > 0 for i in items) else -1)
        a = [i for i in items if i["sign"] == sign]
    b = [i for i in items if i not in a]
    if not a:
        a, b = b, []
    n_roots = (lambda side: len({i.get("root") or i.get("pair") for i in side}))
    if b and n_roots(a) > 1 and n_roots(b) == 1:
        a, b = b, a

    def order(side):
        if not side:
            return side
        lead = max(side, key=lambda i: i["q"])["sign"]
        return sorted(side, key=lambda i: (i["sign"] != lead, -i["q"], str(i.get("commodity") or i.get("label"))))
    return [s for s in (order(a), order(b)) if s]


def _sentence(t: dict, legs: Sequence[Tuple[dict, float]], roots: Dict[str, Any]) -> str:
    items = _word_items(legs, roots)
    if not items:
        return ""
    sides = _sides_of(t, items)
    lots = [i for i in items if i["kind"] == "lots"]
    multi = len({i["root"] for i in lots}) > 1
    whens = {i["when"] for i in lots}
    tail = multi or (len(whens) == 1 and not any(i["opt"] for i in lots))
    opt_types = [i["opt"] for i in lots if i["opt"]]
    parts: List[str] = []
    prev: Dict[str, Any] = {"sign": None, "root": None, "exchange": None, "when": None, "pair": None}
    for n, side in enumerate(sides):
        words: List[str] = []
        for i in side:
            bits: List[str] = []
            if i["sign"] != prev["sign"]:
                bits.append("long" if i["sign"] > 0 else "short")
            if i["kind"] == "fx":
                bits.append(f"{_count_words(i['q'])} {i['unit']}".strip())
                bits.append(i["label"] if i["pair"] != prev["pair"] else i["label"][len(i["pair"]):].strip())
                prev.update(pair=i["pair"], root=None)
            else:
                bits.append(_count_words(i["q"]))
                if i["root"] != prev["root"]:
                    bits.append(i["commodity"] if i["exchange"] and i["exchange"] == prev["exchange"]
                                else " ".join(x for x in (i["exchange"], i["commodity"]) if x))
                if not tail and i["when"] and i["when"] != prev["when"]:
                    bits.append(i["when"])
                if i["opt"]:
                    if opt_types.count(i["opt"]) > 1 or len(opt_types) == 1:
                        bits.append(strike_text(i["strike"]))
                    bits.append(i["opt"] + ("s" if i["q"] != 1 else ""))
                prev.update(root=i["root"], exchange=i["exchange"] or prev["exchange"], when=i["when"], pair=None)
            prev["sign"] = i["sign"]
            words.append(" ".join(b for b in bits if b))
        parts.append((" vs " if n else "") + ", ".join(words))
    text = "".join(parts)
    if tail:
        months = _months_words([m for i in lots for m in i["months"]])
        if months:
            text += f", {months}"
    return cap(text)


def what_parts(t: dict, roots: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """(the What it is sentence, the other parts' sentences for its hover). The trade's open legs
    (hedges apart) in words; a trade of several parts says its largest and "+ N more"; an open
    leg the contract list does not know is counted ("+ 1 not recognised"). A closed trade, a row of
    fills on no trade or one with nothing recognised keeps the engine's words (`what_text`)."""
    roots = roots if roots is not None else _roots()
    by_id = {str(leg.get("contract_id")): leg for leg in t.get("legs") or []}
    legs = [(leg, _num(leg.get("lots")) or 0.0) for leg in _open_legs(t)]
    unknown = [leg for leg in t.get("legs") or [] if leg.get("unrecognised") and leg.get("status") != "closed"]
    subs = [x for x in t.get("sub_spreads") or [] if x.get("legs")]
    others: List[str] = []
    if len(subs) > 1 and not t.get("pseudo"):
        def sub_legs(sub):
            return [(by_id[str(x.get("contract_id"))], _num(x.get("lots")) or 0.0) for x in sub.get("legs") or []
                    if str(x.get("contract_id")) in by_id and (_num(x.get("lots")) or 0.0)
                    and by_id[str(x.get("contract_id"))].get("status") == "open"
                    and not by_id[str(x.get("contract_id"))].get("hedge")]
        ranked = sorted(subs, key=lambda x: -sum(abs(q) for _l, q in sub_legs(x)))
        in_subs = {str(x.get("contract_id")) for sub in subs for x in sub.get("legs") or []}
        legs, rest = sub_legs(ranked[0]), ranked[1:]
        others = [s for s in (_sentence(t, sub_legs(x), roots) for x in rest) if s]
        others += [_sentence(t, [(leg, _num(leg.get("lots")) or 0.0)], roots) for leg in _open_legs(t)
                   if str(leg.get("contract_id")) not in in_subs]
    text = "" if t.get("pseudo") or t.get("status") == "closed" else _sentence(t, legs, roots)
    if not text:
        return what_text(t), []
    if others:
        text += f" + {len(others)} more"
    if unknown:
        text += f" + {len(unknown)} not recognised"
        others += [f"Not recognised: {leg.get('name')} ({signed_count(_num(leg.get('lots')) or 0.0)} in the file)"
                   for leg in unknown]
    return text, others


def _with_words(t: dict, roots: Dict[str, Any]) -> dict:
    text, others = what_parts(t, roots)
    return dict(t, what_words=text, what_others=others)


def hedge_words(t: dict) -> str:
    """The grey words after the sentence: the hedge coverage with its figure ('CNH 92 % hedged'),
    else the engine's own ('JPY hedged', 'GBP/USD hedged')."""
    cov = coverage_words(t.get("hedge") or {})
    if cov:
        return cov
    m = _HEDGE_WORDS.search(str(t.get("what_it_is") or ""))
    return m.group(1) if m else ""


def what_line(t: dict) -> str:
    """The sentence and its hedge words as one line (the CSV, the sort)."""
    text = str(t.get("what_words") or "") or what_parts(t)[0]
    tail = hedge_words(t)
    return f"{text} · {tail}" if tail and tail not in text else text


def _what_td(t: dict, r: Optional[dict], research: dict) -> html.Td:
    """What it is: the sentence (clipped with an ellipsis on a narrow screen, whole on hover), the
    hedge words grey after it; on hover the sentence, the other parts, then what the Size column
    carried until 2026-09-30 (each leg's lots, the value per side, the balance, the USD per 1-unit
    move of the level, hedge % and correlation) and the legs not listed there (hedges, closed)."""
    if "what_words" in t:
        what, others = str(t.get("what_words") or ""), list(t.get("what_others") or [])
    else:
        what, others = what_parts(t)
    tail = hedge_words(t)
    if tail and tail.split(" ")[-1] == "hedged":
        what = _HEDGED_TAIL.sub("", what)     # the grey words say it
    level = t.get("level") or {}
    structure = (f"Level: {level.get('note')}" if level.get("note")
                 and (level.get("mode") == "premium" or level.get("source") == "template") else "")
    listed = {str(leg.get("name")) for leg in t.get("legs") or [] if leg.get("status") == "open" and not leg.get("hedge")}
    rest = [str(leg.get("name") or "") for leg in t.get("legs") or [] if str(leg.get("name") or "") not in listed]
    hover = _lines(f"{cap(what)} · {tail}" if tail else cap(what), structure,
                   _lines("Also held:", *others) if others else "",
                   size_hover(t, r, research), *(f"· {n}" for n in rest if n))
    return html.Td([html.Span(cap(what), className="tk-clip", title=plain_words(hover) or None),
                    html.Span(f" · {tail}", className="tk-sub tk-cov") if tail else None],
                   className="l tk-what" + (" tk-what--cov" if tail else ""))


LOT_PRODUCTS = {"FUTURE", "CMDTY_OPTION", "EQ_OPTION", "LME_FWD"}      # sized in lots
NOTIONAL_PRODUCTS = {"FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION"}      # sized in the pair's base unit


def signed_count(n: float, money: bool = False) -> str:
    """'+30', '−4', '+1,000'; with `money` a currency amount in full ('+2,000,000', '−250,000'):
    a size keeps its real minus, and no k / m since 2026-09-30."""
    f = float(n)
    v = abs(f)
    if money and v >= 1000:
        body = f"{v:,.0f}"
    else:
        body = f"{abs(f):,.2f}".rstrip("0").rstrip(".")
    if body == "0":
        return "0"
    return (MINUS if f < 0 else "+") + body


def _lots_word(*counts: float) -> str:
    return "lot" if len(counts) == 1 and abs(counts[0]) == 1 else "lots"


def _base_unit(leg: dict) -> Tuple[str, bool]:
    """(unit, is money) of an FX leg's notional: 'oz' for a precious metal pair, else its base currency."""
    base = str(leg.get("root_id") or "")[:3]
    if base in METAL_PAIRS:
        return "oz", False
    return base, bool(base)


def leg_size(leg: dict) -> Tuple[str, str]:
    """(signed figure, unit) of one leg: '+4' 'lots', '−100' 'oz', '+10.0m' 'EUR'."""
    q = _num(leg.get("lots")) or 0.0
    if str(leg.get("product") or "") in NOTIONAL_PRODUCTS:
        unit, money = _base_unit(leg)
        return signed_count(q, money), unit
    return signed_count(q), _lots_word(q)


def _pair_figure(a: float, b: float, money: bool = False) -> str:
    """'+30 / −4' for two sides, '+2' for one."""
    if a and b:
        return f"{signed_count(a, money)} / {signed_count(b, money)}"
    return signed_count(a or b, money)


def sub_sides(t: dict, sub: dict) -> Tuple[float, float]:
    """(side A lots, side B lots), signed (+ long), of one part of a trade (display: its legs' lots
    added per side). Side A is the trade's first side (`size.sides[0]`, its roots); a part on one
    root (a calendar) is long against short."""
    sides = (t.get("size") or {}).get("sides") or []
    a_roots = {str(r) for r in (sides[0].get("root_ids") or [])} if sides else set()
    root_of = {str(leg.get("contract_id")): str(leg.get("root_id") or "") for leg in t.get("legs") or []}
    legs = [(root_of.get(str(x.get("contract_id")), ""), _num(x.get("lots")) or 0.0) for x in sub.get("legs") or []]
    roots = {r for r, _l in legs}
    if len(roots) <= 1 or not (roots & a_roots) or roots <= a_roots:
        return sum(q for _r, q in legs if q > 0), sum(q for _r, q in legs if q < 0)
    return sum(q for r, q in legs if r in a_roots), sum(q for r, q in legs if r not in a_roots)


def _open_legs(t: dict) -> List[dict]:
    return [leg for leg in t.get("legs") or [] if leg.get("status") == "open" and not leg.get("hedge")
            and not leg.get("unrecognised") and (_num(leg.get("lots")) or 0.0)]


def size_parts(t: dict) -> Tuple[str, str, int, str]:
    """(figure, unit, other parts, why when blank) of the Size cell, one form on every open row:
    a spread's signed lots per side, side A first ('+30 / −4' lots); an outright future or option
    its signed lots ('+2' lots); an FX or precious-metal forward or FX option its signed notional
    in its unit ('−100' oz, '+10.0m' EUR). A trade of several parts shows its largest and counts
    the rest (on hover). Display only: the engine's sides, or the legs' lots added."""
    size = t.get("size") or {}
    sides = size.get("sides") or []
    subs = [x for x in t.get("sub_spreads") or [] if x.get("legs")]
    if len(subs) > 1:
        best = max(subs, key=lambda x: sum(abs(_num(leg.get("lots")) or 0.0) for leg in x.get("legs") or []))
        a, b = sub_sides(t, best)
        if a or b:
            return _pair_figure(a, b), _lots_word(*(x for x in (a, b) if x)), len(subs) - 1, ""
    if len(sides) >= 2:
        a, b = (_num(s.get("lots")) or 0.0 for s in sides[:2])
        if a or b:
            return _pair_figure(a, b), _lots_word(*(x for x in (a, b) if x)), 0, ""
    legs = _open_legs(t)
    if legs and all(str(leg.get("product") or "") in LOT_PRODUCTS for leg in legs):
        qs = [_num(leg.get("lots")) or 0.0 for leg in legs]
        a, b = sum(q for q in qs if q > 0), sum(q for q in qs if q < 0)
        return _pair_figure(a, b), _lots_word(*(x for x in (a, b) if x)), 0, ""
    if legs and all(str(leg.get("product") or "") in NOTIONAL_PRODUCTS for leg in legs):
        by: Dict[Tuple[str, bool], List[float]] = {}
        for leg in legs:
            by.setdefault(_base_unit(leg), []).append(_num(leg.get("lots")) or 0.0)
        (unit, money), qs = max(by.items(), key=lambda kv: sum(abs(q) for q in kv[1]))
        a, b = sum(q for q in qs if q > 0), sum(q for q in qs if q < 0)
        others = sum(1 for leg in legs if _base_unit(leg) != (unit, money))
        return _pair_figure(a, b, money), unit, others, ""
    if legs:
        leg = max(legs, key=lambda x: abs(_num(x.get("lots")) or 0.0))
        fig, unit = leg_size(leg)
        return fig, unit, len(legs) - 1, ""
    unknown = [leg for leg in t.get("legs") or [] if leg.get("unrecognised")]
    if unknown and t.get("status") != "closed":
        return "", "", 0, ("contract not recognised: no size until it is mapped ("
                           + "; ".join(f"{leg.get('name')}: {signed_count(_num(leg.get('lots')) or 0.0)} in the file"
                                       for leg in unknown) + ")")
    return "", "", 0, str(size.get("reason") or "nothing open")


def size_text(t: dict) -> Tuple[str, str]:
    """(the Size cell as one string, why when blank): '+30 / −4 lots', '+2 lots', '−100 oz'; the
    CSV's Size column."""
    fig, unit, more, why = size_parts(t)
    if not fig:
        return "", why
    return " ".join(x for x in (fig, unit, f"+{more} more" if more else "") if x), ""


def size_hover(t: dict, r: Optional[dict], research: dict) -> str:
    size, level = t.get("size") or {}, t.get("level") or {}
    lines = []
    subs = [x for x in t.get("sub_spreads") or [] if x.get("legs")]
    if len(subs) > 1:
        for x in subs:
            a, b = sub_sides(t, x)
            lines.append(f"{cap(str(x.get('what_it_is') or ''))}: {_pair_figure(a, b)} lots")
    for leg in t.get("legs") or []:
        if leg.get("status") == "open" and not leg.get("hedge"):
            fig, unit = leg_size(leg)
            lines.append(f"{leg.get('name')}: {fig} {unit}")
    for s in size.get("sides") or []:
        if not _num(s.get("lots")):
            continue                            # an outright's empty side: nothing to say
        fill, mark = _num(s.get("value_fill_usd")), _num(s.get("value_mark_usd"))
        metal = _num(s.get("physical"))
        lines.append(f"{s.get('label')}: {format_cell(abs(fill)) + ' USD at the fill' if fill is not None else 'value at the fill not known'}"
                     + (f", {format_cell(abs(mark))} USD at the mark" if mark is not None else "")
                     + (f"; {abs(metal):,.0f} {s.get('physical_unit') or ''}" if metal else ""))
    gap = _num(size.get("value_gap"))
    if gap is not None:
        tol = pct_text(size.get("tolerance") or 0.1)
        lines.append(f"Balance: the sides differ by {pct_text(gap)} of their value "
                     + (f"(unbalanced: beyond {tol})" if size.get("unbalanced") else f"(balanced: within {tol})"))
    elif size.get("reason"):
        lines.append(str(size["reason"]))
    usd, words = _usd_per_move(level)
    lines.append(f"{words}: {format_cell(usd)}" if usd is not None else f"USD per move of the level: {words}")
    if r is not None:
        hp, corr = _num(r.get("hedge_pct")), _num(r.get("leg_correlation"))
        mark = tf.research_words(research)
        if hp is not None:
            lines.append(f"Hedge %: {pct_text(hp / 100.0)}" + (f" · correlation {corr:.2f}" if corr is not None else "")
                         + (f" {mark}" if mark else ""))
        elif r.get("hedge_reason"):
            lines.append(f"Hedge %: not given ({r['hedge_reason']})")
    else:
        lines.append("Hedge %: the risk figures are still being computed")
    return _lines(*lines)


def _entry_td(t: dict) -> html.Td:
    level = t.get("level") or {}
    fills = [f"{leg.get('name')}: avg fill {price_text(leg.get('avg_fill'))}" for leg in t.get("legs") or []
             if not leg.get("hedge") and _num(leg.get("avg_fill")) is not None]
    hover = _lines(f"Entry: first fill {t.get('first_trade_date') or ''}", *fills,
                   (level.get("sources") or {}).get("entry", ""), *estimate_words(level, "entry"),
                   level_unit_line(level))
    return html.Td(_level_cell(level.get("entry"), level, str(level.get("entry_reason") or level.get("reason") or ""),
                               hover, estimated=bool(level.get("entry_estimated"))), className="tk-level")


def _now_td(t: dict, check: Sequence[str] = ()) -> html.Td:
    level = t.get("level") or {}
    carry = ""
    if tf.type_code(t) == "CALENDAR" or level.get("source") == "calendar":
        c = _num(t.get("carry_per_month"))
        carry = (f"Carry per month: {full_signed(c)} USD (the legs' roll-down on one curve, research)"
                 if c is not None else f"Carry per month: not summed ({t.get('carry_reason') or 'not given'})")
    hover = _lines(level_unit_line(level) if level.get("mode") == "premium" else "",
                   (level.get("sources") or {}).get("now", ""), level.get("note") or "", carry,
                   *estimate_words(level, "now"), *(f"Price to check: {x}" for x in check))
    estimated = bool(level.get("now_estimated")) or _estimated_level(t) or bool(check)
    return html.Td(_level_cell(level.get("now"), level, str(level.get("now_reason") or level.get("reason") or ""),
                               hover, estimated=estimated, with_unit=True), className="tk-level tk-now")


def _z_td(r: Optional[dict], ready: bool, research: dict) -> html.Td:
    if not ready:
        return html.Td(missing_cell("the risk figures are still being computed"))
    if r is None:
        return html.Td(missing_cell("no risk row for this trade"))
    z = _num(r.get("z"))
    if z is None:
        return html.Td(missing_cell(r.get("z_reason") or "no z"))
    pct, ze = _num(r.get("percentile")), _num(r.get("z_entry"))
    hover = _lines(f"Percentile: {pct_text(pct / 100.0) if pct is not None else MISSING}",
                   f"z at entry: {z_text(ze)}" if ze is not None else f"z at entry: {r.get('z_entry_reason') or 'not given'}",
                   f"Window: {r.get('level_days') or ''} settlements to {r.get('level_date') or ''}, research history",
                   tf.research_words(research))
    # the research caveat sits once, on the column's heading (`head`), never on every cell
    return html.Td(html.Span(z_text(z), className="tk-bold" if abs(z) >= 2 else None, title=plain_words(hover)))


def sigma_words(r: Optional[dict], ready: bool, level: dict) -> str:
    """'Move in σ: +1.4 (a daily σ of 0.21 $/bbl over 251 changes, research history)' from
    `trade_risk`'s move_sigma / level_sd, or the engine's reason."""
    if not ready:
        return "Move in σ: the risk figures are still being computed"
    if r is None:
        return "Move in σ: no risk row for this trade"
    ms, sd = _num(r.get("move_sigma")), _num(r.get("level_sd"))
    if ms is None:
        return f"Move in σ: not given ({r.get('move_sigma_reason') or r.get('level_sd_reason') or 'no figure'})"
    sd_text = ""
    if sd is not None:
        unit = unit_words(r.get("level_unit") or level.get("unit"))
        days = r.get("level_sd_days")
        sd_text = (f" (a daily σ of {level_text(sd, level)}{' ' + unit if unit else ''}"
                   + (f" over {days} daily changes" if days else "") + ", research history)")
    return f"Move in σ: {z_text(ms)}{sd_text}"


def _today_td(t: dict, r: Optional[dict] = None, ready: bool = True, check: Sequence[str] = ()) -> html.Td:
    level = t.get("level") or {}
    ch = _num(level.get("change"))
    if ch is None:
        return html.Td(missing_cell(level.get("change_reason") or level.get("reason") or "no move"))
    usd = _num(level.get("usd_per_unit"))
    effect = ch * usd if usd is not None else None
    cls = sign_class(effect) if effect is not None else ""
    if effect is None and level.get("mode") == "price":
        leg = _price_leg(t)
        held = _num((leg or {}).get("lots")) or 0.0
        cls = sign_class(ch * held) if held else ""      # a rise helps a long, hurts a short (display only)
    since = f"Since the {level.get('prev_date') or 'previous'} close ({level_text(level.get('prev'), level)})"
    notes = estimate_words(level, "prev", "now")
    hover = _lines(since, level_unit_line(level),
                   f"Worth {full_signed(effect)} USD to the trade" if effect is not None else "",
                   sigma_words(r, ready, level), *notes, *(f"Price to check: {x}" for x in check))
    text = level_text(ch, level, signed=True)
    if not text.strip("+()" + MINUS + "0.,"):
        # nothing moved at the level's precision: one dash for the whole column, never 0.0000 / 0.00 / 0.0
        return html.Td(missing_cell(_lines(f"No move {since[0].lower()}{since[1:]}", *notes,
                                           *(f"Price to check: {x}" for x in check))), className="tk-level")
    # the unit is said once per row, on Now; an estimated end (either close) greys the move
    if check or level.get("prev_estimated") or level.get("now_estimated") or _estimated_level(t):
        return html.Td(html.Span("≈ " + text, className="cell-estimated", title=plain_words(hover)), className="tk-level")
    return html.Td(html.Span(text, className=cls or None, title=plain_words(hover)), className="tk-level")


def next_parts(t: dict, as_of: str) -> Tuple[str, str, str]:
    """(text, class, hover) of the Next cell."""
    nx = t.get("next") or None
    if not nx or not nx.get("date"):
        return "", "", str(t.get("next_reason") or "no key date")
    event = str(nx.get("event") or "")
    abbr = NEXT_ABBR.get(event, event[:1].upper() + event[1:])
    text = f"{abbr} {day_text(nx.get('date'), as_of)}"
    bd = nx.get("business_days")
    level = str(nx.get("level") or "").upper()
    est = bool(nx.get("estimated"))
    what = str(nx.get("leg") or "") or contract_name(str(nx.get("contract_id") or "")) or str(nx.get("contract_id") or "")
    alert = str(nx.get("alert_date") or "")
    to_go = f", {bd} business {'day' if bd == 1 else 'days'} to go" if bd is not None else ""
    if est:
        first = (f"Estimated {event} of {what}: {day_text(nx.get('date'), as_of)}; the alert is held early, "
                 f"to {day_text(alert or nx.get('date'), as_of)}{to_go}")
    elif alert and alert != str(nx.get("date") or ""):
        first = f"{cap(event)} of {what} on {day_text(nx.get('date'), as_of)}; alert on {day_text(alert, as_of)}{to_go}"
    else:
        first = f"{cap(event)} of {what} on {day_text(nx.get('date'), as_of)}{to_go}"
    hover = _lines(first, f"Alert level: {level.lower()}" if level else "", nx.get("reason") or "")
    if est:
        return f"≈ {text}", "cell-estimated", hover
    return text, {"RED": "cell-red", "EXPIRED": "cell-red", "AMBER": "cell-amber"}.get(level, ""), hover


def _next_td(t: dict, as_of: str) -> html.Td:
    text, cls, hover = next_parts(t, as_of)
    if not text:
        return html.Td(missing_cell(hover), className="l")
    return html.Td(html.Span(text, className=cls or None, title=plain_words(hover)), className="l")


def is_red(flag: dict) -> bool:
    """A red flag (the engine's `severity`: a contract not recognised); amber otherwise."""
    return str(flag.get("severity") or "") == "red"


def flag_sentence(flag: dict) -> str:
    """The flag in one sentence: the engine's sentence, which names the flag itself when it is a
    red one ('contract not recognised: XYZ6-USAA: P&L can't be computed until it is mapped')."""
    label, sentence = str(flag.get("label") or ""), str(flag.get("sentence") or "")
    if not label or sentence.lower().startswith(label.lower()):
        return cap_parts(sentence or label)
    return cap_parts(f"{label}: {sentence}")


def _closed_td(t: dict, as_of: str) -> html.Td:
    """A closed trade's Next cell: the day it went flat (the engine's close_date), grey."""
    c = t.get("closed") or {}
    if not c.get("close_date"):
        return html.Td(missing_cell(c.get("reason") or "no close date"), className="l")
    hover = _lines(f"Closed on {c['close_date']}: its last fill, or a leg's settlement after it",
                   f"Last open close: {c['open_date']}" if c.get("open_date") else "")
    return html.Td(html.Span(f"Closed {day_text(c['close_date'], as_of)}", className="tk-sub", title=hover), className="l")


# The flags' short names, most severe first (the Flags cell names the first and counts the rest).
FLAG_NAMES = (("unrecognised", "Not recognised"), ("leg_without_price", "No price"), ("no_trade", "No trade"),
              ("price_check", "Price to check"), ("unbalanced", "Unbalanced"), ("hedge_oversized", "Hedge too big"),
              ("type_mismatch", "Type mismatch"))
_FLAG_RANK = {code: i for i, (code, _n) in enumerate(FLAG_NAMES)}


def flag_name(flag: dict) -> str:
    """'Unbalanced', 'Hedge too big', 'Not recognised' ...: the flag's short name."""
    return dict(FLAG_NAMES).get(str(flag.get("code") or "")) or cap(str(flag.get("label") or "Flag"))


def flags_by_severity(flags: Sequence[dict]) -> List[dict]:
    return sorted(flags, key=lambda f: (not is_red(f), _FLAG_RANK.get(str(f.get("code") or ""), 99)))


def _flags_td(t: dict) -> html.Td:
    flags = flags_by_severity(t.get("flags") or [])
    if not flags:
        return html.Td("", className="tk-flags-cell")    # no flag: blank, the column's one rule
    red = is_red(flags[0])
    hover = _lines(*(flag_sentence(f) for f in flags))
    return html.Td(html.Span([flag_name(flags[0]), html.Span(f" +{len(flags) - 1}", className="tk-flags-more")
                              if len(flags) > 1 else None],
                             className="tk-flags" + (" tk-flags--red" if red else ""), title=plain_words(hover)),
                   className="tk-flags-cell")


# --------------------------------------------------------------------------- rows
def _price_leg(t: dict) -> Optional[dict]:
    """The one non-hedge contract of an outright read at its price (`level.mode` 'price'): the
    open leg, or for a closed trade the leg it held."""
    legs = [leg for leg in t.get("legs") or [] if not leg.get("hedge") and not leg.get("unrecognised")]
    open_ = [leg for leg in legs if leg.get("status") == "open"]
    return (open_ or legs or [None])[0]


def display_level(data: dict, t: dict) -> dict:
    """The level the row's Entry / Now / Today read, as the engine gave it: `level` for an open
    trade, the closed block's entry and exit for a closed one (Now = the exit level, no move), and
    for a price level (one contract, `mode` 'price') the unit of that contract's quote, so the
    price shows at its tick. Display only: no figure is changed."""
    closed = t.get("closed") if t.get("status") == "closed" else None
    if closed:
        why = str(closed.get("reason") or "")
        close = day_text(closed.get("close_date"), data.get("as_of")) if closed.get("close_date") else "its close"
        lv = {"unit": closed.get("unit") or "", "mode": closed.get("mode") or "", "entry": closed.get("entry"),
              "entry_reason": closed.get("entry_reason") or why or "no entry level",
              "now": closed.get("exit"), "now_reason": why or "no exit level", "change": None,
              "change_reason": f"closed on {close}: no move today", "prev": None, "closed": True,
              "sources": {"entry": (f"read as the trade stood on {day_text(closed.get('open_date'), data.get('as_of'))}, "
                                    "the last close it was open" if closed.get("open_date") else ""),
                          "now": _lines(f"Exit level: {closed.get('exit_basis')}" if closed.get("exit_basis") else "",
                                        f"Closed {close} (its last fill or a leg's settlement)")}}
    else:
        lv = dict(t.get("level") or {})
    if lv.get("mode") == "price":
        leg = _price_leg(t)
        if leg is not None:
            lv["unit"] = _leg_unit(data, leg)
    return lv


def trade_tr(data: dict, t: dict, r: Optional[dict], ready: bool, opened: bool) -> html.Tr:
    ids = [str(i) for i in t.get("trade_ids") or []]
    daily = fill_sum(data, "daily", ids)
    ltd = fill_sum(data, "ltd", ids)
    mtd, ytd = fill_sum(data, "mtd", ids), fill_sum(data, "ytd", ids)
    name = str(t.get("trade") or "")
    pbs = t.get("pb_roots") or []
    name_hover = _lines(f"PBRoot: {', '.join(pbs)}" if pbs else "", f"{_plural(len(ids), 'fill')}")
    tv = dict(t, level=display_level(data, t))
    closed = bool(tv["level"].get("closed"))
    check = check_lines(data, ids)
    info = row_info([excl_line("Daily", daily), excl_line("LTD", ltd), excl_line("MTD", mtd), excl_line("YTD", ytd)])
    cells = [
        html.Td([html.Span("▾ " if opened else "▸ ", className="tk-chev"),
                 html.Span(name, className="tk-name", title=name_hover or None), info], className="l"),
        _type_td(t), _what_td(t, r, data.get("research") or {}), _entry_td(tv), _now_td(tv, check),
        html.Td(missing_cell("closed: no z")) if closed else _z_td(r, ready, data.get("research") or {}),
        _today_td(tv, r, ready, check),
        money_td(*daily, hover=_split_hover(t), check=check, row=True),
        money_td(*ltd, hover=_lines(f"MTD {km_text(mtd[0])}", f"YTD {km_text(ytd[0])}",
                                    "the final P&L: the trade is flat" if closed else ""), check=check, row=True),
        _closed_td(t, data["as_of"]) if closed else _next_td(t, data["as_of"]), _flags_td(t),
    ]
    return html.Tr(cells, id={"type": ROW_TYPE, "idx": name}, n_clicks=0,
                   className="tk-row" + (" tk-row--open" if opened else "") + (" tk-row--pseudo" if t.get("pseudo") else ""))


def _total_label(shown: Sequence[dict], all_trades: Sequence[dict], filtered: bool) -> Tuple[str, str]:
    named = [t for t in all_trades if not t.get("pseudo")]
    shown_named = [t for t in shown if not t.get("pseudo")]
    n_open = sum(1 for t in named if t.get("status") == "open")
    loose = sum(len(t.get("trade_ids") or []) for t in shown if t.get("pseudo"))
    hover = _lines(f"{len(named)} trades: {n_open} open, {len(named) - n_open} closed",
                   f"with {_plural(loose, 'fill')} on no trade" if loose else "")
    if filtered:
        return f"Filtered · {len(shown_named)} of {_plural(len(named), 'trade')}", hover
    return f"Book · {_plural(len(named), 'trade')}", hover


def nearest_next(trades: Sequence[dict]) -> Optional[dict]:
    best = None
    for t in trades:
        nx = t.get("next") or None
        if nx and nx.get("date") and (best is None or str(nx["date"]) < str(best[1]["date"])):
            best = (t, nx)
    return {"trade": best[0], "next": best[1]} if best else None


def totals(data: dict, shown: Sequence[dict]) -> dict:
    ids = [str(i) for t in shown for i in t.get("trade_ids") or []]
    out = {k: fill_sum(data, k, ids) for k in PERIODS}
    out["gross"] = sum_known((t.get("gross_usd"), f"{t.get('trade')}: {t.get('notional_reason') or 'no gross'}")
                             for t in shown if not t.get("pseudo"))
    out["net"] = sum_known((t.get("net_usd"), f"{t.get('trade')}: {t.get('notional_reason') or 'no net'}")
                           for t in shown if not t.get("pseudo"))
    out["next"] = nearest_next(shown)
    out["flags"] = sum(len(t.get("flags") or []) for t in shown)
    out["red_flags"] = [flag_sentence(f) for t in shown for f in t.get("flags") or [] if is_red(f)]
    out["flag_lines"] = [f"{t.get('trade')}: {flag_sentence(f)}" for t in shown
                         for f in flags_by_severity(t.get("flags") or [])]
    return out


def _total_money(label: str, fig: Tuple[Optional[float], int, List[str]], hover: str, signed: bool) -> html.Span:
    """'Gross 14.2m' / 'Net −34.4k' in the total row, its 'excl. N' marker and reasons on hover."""
    v, n, r = fig
    value = (km_cell(v, hover=hover, signed=signed, colour=False) if v is not None
             else missing_cell(_lines(*list(dict.fromkeys(r))[:8]) or f"no {label.lower()}"))
    return html.Span([html.Span(label + " ", className="tk-total-k"), value,
                      marker(f"excl. {n}", _lines(f"excludes {_plural(n, 'trade')} with no {label.lower()} USD",
                                                  *list(dict.fromkeys(r))[:8]), "marker--small")
                      if n and v is not None else None], className="tk-total-fig")


def _total_flags(tot: dict) -> Any:
    """'9 flags · 2 red' in the total row's Flags cell: red when any is red, amber otherwise; each
    flag's trade and sentence on hover."""
    n, red = tot["flags"], len(tot["red_flags"])
    if not n:
        return ""
    lines = tot["flag_lines"]
    hover = _lines(*lines[:20], f"And {len(lines) - 20} more" if len(lines) > 20 else "")
    text = _plural(n, "flag") + (f" · {red} red" if red else "")
    return html.Span(text, className="tk-flags" + (" tk-flags--red" if red else ""), title=plain_words(hover))


def total_tr(data: dict, shown: Sequence[dict], filtered: bool) -> html.Tr:
    tot = totals(data, shown)
    label, hover = _total_label(shown, data.get("trades") or [], filtered)
    nx = tot["next"]
    nx_cell: Any = ""
    if nx:
        text, cls, nh = next_parts(nx["trade"], data["as_of"])
        nx_cell = html.Span(f"{nx['trade'].get('trade')} {text}", className=cls or None, title=plain_words(nh))
    if not filtered:
        hover = _lines(hover, "Daily, MTD and LTD equal the top bar's to the cent")
    return html.Tr([
        html.Td(html.Span(label, title=hover), className="l"), html.Td(""),
        html.Td([_total_money("Gross", tot["gross"], "Gross USD notional of the rows showing, summed", False),
                 html.Span(" · ", className="tk-total-sep"),
                 _total_money("Net", tot["net"], "Net USD notional of the rows showing, summed", True)],
                className="l tk-total-what"),
        html.Td(""), html.Td(""), html.Td(""), html.Td(""),
        _checked_money(data, shown, tot["daily"]),
        _checked_money(data, shown, tot["ltd"], hover=f"MTD {km_text(tot['mtd'][0])}"),
        html.Td(nx_cell, className="l"), html.Td(_total_flags(tot), className="tk-flags-cell"),
    ], className="tk-total book-total")


def _checked_money(data: dict, shown: Sequence[dict], fig, hover: str = "") -> html.Td:
    """A total's money cell and, when a price to check drives some of the rows, the trades named."""
    names = [str(t.get("trade")) for t in shown if check_lines(data, [str(i) for i in t.get("trade_ids") or []])]
    td = money_td(*fig, hover=hover)
    if names and fig[0] is not None:
        td.children = list(td.children) + [marker(f"≈ {len(names)} to check", _lines(
            "Includes the P&L of trades priced off a price the marks check flags:", *names), "marker--small")]
    return td


def group_tr(data: dict, label: str, rows: Sequence[dict], folded: bool) -> html.Tr:
    ids = [str(i) for t in rows for i in t.get("trade_ids") or []]
    return html.Tr([
        html.Td([html.Span("▸ " if folded else "▾ ", className="tk-chev"), html.Span(label),
                 html.Span(f" · {_plural(len(rows), 'trade')}", className="tk-sub")], className="l", colSpan=7),
        money_td(*fill_sum(data, "daily", ids)), money_td(*fill_sum(data, "ltd", ids)), html.Td(""), html.Td(""),
    ], id={"type": GROUP_ROW_TYPE, "idx": label}, n_clicks=0, className="tk-group")


# --------------------------------------------------------------------------- sort
SORTABLE = {"trade", "type", "what", "entry", "now", "z", "today", "daily", "ltd", "next", "flags"}


def sort_value(data: dict, t: dict, key: str, r: Optional[dict]) -> Any:
    level = t.get("level") or {}
    if key == "trade":
        return str(t.get("trade") or "").lower()
    if key == "type":
        code = tf.type_code(t)
        return tf.TYPE_ORDER.index(code) if code in tf.TYPE_ORDER else 99
    if key == "what":
        return what_line(t).lower()
    if key in ("entry", "now"):
        return _num(level.get(key))
    if key == "today":
        return _num(level.get("change"))
    if key == "z":
        z = _num((r or {}).get("z"))
        return abs(z) if z is not None else None
    if key in ("daily", "ltd"):
        return fill_sum(data, key, [str(i) for i in t.get("trade_ids") or []])[0]
    if key == "next":
        return str((t.get("next") or {}).get("date") or "") or None
    if key == "flags":
        flags = t.get("flags") or []
        return (sum(1 for f in flags if is_red(f)), len(flags)) if flags else None
    return None


def sort_rows(data: dict, rows: Sequence[dict], sort: Optional[dict], risk: Dict[str, dict]) -> List[dict]:
    """The rows in the default order (commodity family, then name), or by the column chosen;
    a missing value always last."""
    base = tf.default_order(rows)
    key = (sort or {}).get("key")
    if key not in SORTABLE:
        return base
    desc = (sort or {}).get("dir") != "asc"
    have = [t for t in base if sort_value(data, t, key, risk.get(str(t.get("trade")))) is not None]
    none = [t for t in base if t not in have]
    have.sort(key=lambda t: sort_value(data, t, key, risk.get(str(t.get("trade")))), reverse=desc)
    return have + none


def next_sort(current: Optional[dict], key: str) -> Optional[dict]:
    """desc -> asc -> the default order."""
    cur = current or {}
    if cur.get("key") != key:
        return {"key": key, "dir": "desc"}
    if cur.get("dir") == "desc":
        return {"key": key, "dir": "asc"}
    return None


# The column filters (spreadsheet-style, the funnel in each heading; user, 2026-09-29): Trade, Type
# and What it is (the commodity family) carry across Book, P&L and Risk; a number column holds one
# comparison, the Book's own; Flags a tick list. Nothing above the table repeats a column's name.
LIST_FUNNELS = {"trade": ("trade",), "type": ("type",), "what": ("commodity",)}
NUMBER_FUNNELS = {"entry": "The level at entry, in the level's own unit.",
                  "now": "The level now, in the level's own unit.",
                  "z": "The z-score (research history); a trade whose z is still being computed does not show.",
                  "today": "The level's move since the previous close, in the level's own unit.",
                  "daily": "Today's P&L in USD.", "ltd": "The P&L since the trade opened, in USD."}
FLAG_ANY, FLAG_RED = "any", "red"
FLAG_OPTIONS = [{"label": "Has flags", "value": FLAG_ANY}, {"label": "Red flags", "value": FLAG_RED}]


def _flags_funnel(state: Optional[dict]) -> html.Details:
    value = tf.tab_filters(state, TAB).get("flags")
    ticked = value if isinstance(value, list) else []
    return tf.funnel(f"{TAB}:flags", tf.pop_list(tf.col_id(TAB, "flags"), "Flags", FLAG_OPTIONS, ticked, search=False),
                     bool(ticked), tf.option_words(FLAG_OPTIONS, ticked))


def head(sort: Optional[dict], research: Optional[dict] = None, state: Optional[dict] = None,
         options: Optional[Dict[str, List[dict]]] = None) -> html.Thead:
    """The column heads: each title sorts, each filterable column carries its funnel (`trade_filter.
    funnel`); z (every value research) carries the one research "i" while the research history is
    not real (`trade_filter.research_head`)."""
    ths = []
    half = len(COLUMNS) // 2
    for i, (key, title, cls, tip) in enumerate(COLUMNS):
        note = tf.research_head(research) if key == "z" and research is not None else None
        pop = None
        if key in LIST_FUNNELS:
            pop = tf.trade_funnel(TAB, state, options or {}, LIST_FUNNELS[key], key)
        elif key in NUMBER_FUNNELS:
            pop = tf.number_funnel(TAB, state, key, hint=f"{NUMBER_FUNNELS[key]} {tf.NUMBER_HINT}")
        elif key == "flags":
            pop = _flags_funnel(state)
        ths.append(tf.head_th(title, cls, tip, sort_id={"type": SORT_TYPE, "idx": key} if key in SORTABLE else None,
                              arrow=tf.arrow_of(sort, key), pop=pop, right=i > half, note=note))
    return html.Thead(html.Tr(ths))


def filter_value(data: dict, t: dict, col: str, rrows: Dict[str, dict]) -> Optional[float]:
    """A trade's figure for a number column's filter, as the row shows it (never recomputed)."""
    if col in ("daily", "ltd"):
        return fill_sum(data, col, [str(i) for i in t.get("trade_ids") or []])[0]
    if col == "z":
        return _num((rrows.get(str(t.get("trade"))) or {}).get("z"))
    level = display_level(data, t)
    return _num(level.get({"entry": "entry", "now": "now", "today": "change"}.get(col, col)))


def filter_lists(t: dict, col: str) -> List[str]:
    """A trade's values for a tick-list filter of its own column (Flags)."""
    if col == "flags":
        flags = t.get("flags") or []
        return ([FLAG_ANY] if flags else []) + ([FLAG_RED] if any(is_red(f) for f in flags) else [])
    return []


# --------------------------------------------------------------------------- the panel
def _root_of(data: dict, leg: dict):
    return (data.get("roots") or {}).get(str(leg.get("root_id") or ""))


def _leg_unit(data: dict, leg: dict) -> str:
    inst = str(leg.get("instrument_id") or "")
    if str(leg.get("product") or "").startswith("FX") and is_fx_pair(inst[:6]):
        return inst[:6]
    return quoted_unit(_root_of(data, leg))


def _df_rows(data: dict, ids: Sequence[str]) -> pd.DataFrame:
    df = data.get("df")
    if df is None or df.empty:
        return pd.DataFrame()
    return df[df["trade_id"].isin([str(i) for i in ids])]


def _leg_hover(data: dict, leg: dict) -> str:
    root = _root_of(data, leg)
    parts = [f"Broker symbol: {', '.join(s for s in leg.get('broker_symbols') or [] if s) or 'not recorded'}",
             f"Bloomberg: {leg.get('contract_id') or leg.get('instrument_id')}"]
    if root is not None:
        parts.append(f"{float(root.contract_size):g} {root.size_unit} a lot · {root.currency} · priced in "
                     f"{quoted_unit(root)}")
    return _lines(*parts)


def _fills_hover(data: dict, leg: dict) -> Tuple[str, str]:
    """(the Lots hover, the Avg fill hover) from the fills on file."""
    fills = data.get("fills") or {}
    root = _root_of(data, leg)
    unit = "t" if leg.get("product") == "LME_FWD" else ("lots" if not str(leg.get("product") or "").startswith("FX") else "")
    bought = sold = 0.0
    lines = []
    scale = float(getattr(root, "broker_price_scale", 1) or 1) if root is not None else 1.0
    for tid in leg.get("trade_ids") or []:
        f = fills.get(str(tid)) or {}
        q = f.get("quantity") or 0.0
        bought += q if q > 0 else 0.0
        sold += -q if q < 0 else 0.0
        broker = f.get("broker_price") or ""
        note = f" (the broker's {broker} ×{scale:g}: its $/lb converted to cents)" if scale != 1 and broker else (
            f" (as written {broker})" if broker else "")
        lines.append(f"{day_text(f.get('trade_date'), data['as_of'])}: {q:+,.0f} at {price_text(f.get('price'))}{note}")
    lots_hover = _lines(f"{_plural(len(leg.get('trade_ids') or []), 'fill')}",
                        f"bought {bought:,.0f} {unit}, sold {sold:,.0f} {unit}".strip())
    return lots_hover, _lines(*lines[:12], f"... and {len(lines) - 12} more" if len(lines) > 12 else "")


def _stamp_words(iso: Any) -> str:
    """'Fri 18 Sep 17:00 NY' from an official mark's stamp ('' when there is none)."""
    if not iso:
        return ""
    from ui.tabs.blotter_fills import ny_time
    return ny_time(iso)


def mark_hover(data: dict, leg: dict, unit: str, fill: Optional[float], level_note: str) -> str:
    """The Mark cell's hover from the leg's own fields (`trade_book`): the source and time of the
    mark, the close it is from when the filled reader carried an earlier one, the previous close
    (its price, date and source, or why there is none) and the legs' closing hours."""
    src = str(leg.get("mark_source") or "")
    stamp = _stamp_words(leg.get("mark_snapped_at"))
    as_of_mark = str(leg.get("mark_as_of") or "")
    lines = [f"Source: {plain_words(src)}" + (f", {stamp}" if stamp else
                                              " (an estimate or a frozen figure: no official stamp)")] if src else []
    if as_of_mark and as_of_mark != data.get("as_of"):
        lines.append(f"Carried from the {day_text(as_of_mark, data.get('as_of'))} close: no price of its own on "
                     f"{day_text(data.get('as_of'), data.get('as_of'))}")
    prev = _num(leg.get("prev_mark"))
    pdate = day_text(leg.get("prev_mark_date"), data.get("as_of")) if leg.get("prev_mark_date") else "the previous"
    if prev is not None:
        psrc = plain_words(leg.get("prev_mark_source") or "")
        lines.append(f"Previous close ({pdate}): {price_text(prev, unit, fill)}" + (f", {psrc}" if psrc else ""))
    else:
        lines.append(f"Previous close ({pdate}): not on file ({leg.get('prev_mark_reason') or 'no reason given'})")
    if level_note and not leg.get("hedge"):
        lines.append(level_note)
    return _lines(*lines)


def value_hover(data: dict, leg: dict, rows: pd.DataFrame) -> str:
    """The Value USD cell's hover: the local value (`value_local`, open quantity x multiplier x
    mark in the leg's currency) and the spot the engine converted at."""
    ccy = str(leg.get("currency") or "")
    local = _num(leg.get("value_local"))
    lines = []
    if local is not None:
        lines.append(" ".join(x for x in ("Local:", ccy, full_signed(local)) if x))
    elif leg.get("value_local_reason"):
        lines.append(f"Local value: {leg.get('value_local_reason')}")
    if ccy not in ("", "USD") and not rows.empty and "spot" in rows:
        spot = next((_num(s) for s in rows["spot"] if _num(s) is not None), None)
        if spot is not None:
            lines.append(f"Spot {spot:g} USD per {ccy}")
    return _lines(*lines)


def leg_tr(data: dict, leg: dict, level_note: str) -> html.Tr:
    ids = [str(i) for i in leg.get("trade_ids") or []]
    hedge = bool(leg.get("hedge"))
    check = leg_check_lines(data, leg)
    unrec = bool(leg.get("unrecognised"))
    unit = "" if unrec else _leg_unit(data, leg)
    lots = _num(leg.get("lots"))
    product = str(leg.get("product") or "")
    if lots is None:
        lots_text = MISSING
    elif product.startswith("FX"):
        lots_text = f"{signed_count(lots, money=True)} {leg.get('root_id') or ''}".strip()
    else:
        lots_text = (MINUS if lots < 0 else "+") + f"{abs(lots):,.0f}" if lots else "0"
    lots_hover, fill_hover = _fills_hover(data, leg)
    rows = _df_rows(data, ids)
    value = _num(leg.get("value_usd"))
    v_hover = value_hover(data, leg, rows)
    value_cell = (html.Span(full_signed(value), title=plain_words(v_hover) or None)
                  if value is not None else missing_cell(_lines(leg.get("value_reason") or "no value",
                                                                "" if unrec else v_hover)))
    fill = _num(leg.get("avg_fill"))
    fill_cell = (html.Span(price_text(fill, unit, fill), title=plain_words(fill_hover) or None) if fill is not None
                 else missing_cell(leg.get("avg_fill_reason") or "no open lots"))
    mark = _num(leg.get("mark"))
    est = is_estimated_mark(leg.get("mark_source"))
    m_hover = mark_hover(data, leg, unit, fill, level_note)
    if check:
        m_hover = _lines(m_hover, *(f"Price to check: {x}" for x in check))
    mark_cell = (html.Span(("≈ " if est or check else "") + price_text(mark, unit, fill), title=plain_words(m_hover),
                           className=("cell-estimated" if est else "") + (" cell-amber" if check else "") or None)
                 if mark is not None else missing_cell(_lines(leg.get("mark_reason") or "no mark",
                                                              "" if unrec else m_hover)))
    daily = fill_sum(data, "daily", ids)
    ltd = fill_sum(data, "ltd", ids)
    local = None
    if not rows.empty and leg.get("currency") not in ("", "USD") and "pnl_local" in rows:
        loc = [(_num(v), "") for v in rows["pnl_local"]]
        local = sum_known(loc)[0]
    realised = sum_known((_num(v), "") for v, s in zip(rows.get("pnl_usd", []), rows.get("status", [])) if s == "SETTLED")[0] \
        if not rows.empty else None
    ltd_hover = _lines(f"Local: {leg.get('currency')} {full_signed(local)}" if local is not None else "",
                       f"Realised: {full_signed(realised)} USD" if realised is not None else "")

    def full_td(tot, hover=""):
        v, n, reasons = tot
        if v is None:
            return html.Td(missing_cell(_lines(*reasons[:6]) or "no figure"))
        return html.Td([html.Span(full_signed(v), className=sign_class(v) or None, title=plain_words(hover) or None),
                        marker(f"excl. {n}", _lines(*reasons[:6]), "marker--small") if n else None])

    roll = _num(leg.get("roll_down_usd_per_month"))
    roll_hover = _lines(f"{price_text(leg.get('roll_down'))} {leg.get('roll_down_unit') or ''} a month over "
                        f"{leg.get('horizon_months') or ''} months on the research curve"
                        if _num(leg.get("roll_down")) is not None else "",
                        tf.research_words(data.get("research") or {}))
    roll_cell = (html.Span(full_signed(roll), title=plain_words(roll_hover) or None) if roll is not None
                 else missing_cell(leg.get("roll_down_reason") or "no roll-down"))
    side = str(leg.get("side") or "")
    name = str(leg.get("name") or leg.get("contract_id") or "")
    if unrec:
        name_cell = html.Span([name, html.Span(" not recognised", className="tk-red")],
                              title=plain_words(_lines(leg.get("unrecognised_reason") or "contract not recognised",
                                                       _leg_hover(data, leg))))
    else:
        name_cell = html.Span(name + (" (hedge)" if hedge else ""), title=plain_words(_leg_hover(data, leg)))
    return html.Tr([
        html.Td(name_cell, className="l"),
        html.Td(cap(side), className="l"),
        html.Td(html.Span(lots_text, title=plain_words(lots_hover))),
        html.Td(value_cell), html.Td(fill_cell), html.Td(mark_cell),
        full_td(daily), full_td(ltd, ltd_hover), html.Td(roll_cell),
    ], className="tk-leg" + (" tk-leg--hedge" if hedge else "") + (" tk-leg--flat" if side == "flat" else "")
       + (" tk-leg--unrec" if unrec else ""))


def _leg_head(research: Optional[dict] = None) -> html.Thead:
    """The legs' heads; Roll-down (research) carries the one research "i" while it is not real."""
    return html.Thead(html.Tr([html.Th([title, tf.research_head(research) if k == "roll" and research is not None
                                        else None], className=cls or None) for k, title, cls in LEG_COLUMNS]))


def _sub_tr(text: str, ids: Sequence[str], data: dict) -> html.Tr:
    return html.Tr([html.Td(cap(text), className="l tk-subhead", colSpan=6),
                    html.Td(full_signed(fill_sum(data, "daily", ids)[0])),
                    html.Td(full_signed(fill_sum(data, "ltd", ids)[0])), html.Td("")], className="tk-legsub")


def _leg_order(t: dict) -> List[dict]:
    """Non-hedge legs first (side A then B, each by month, flat legs after), hedges last."""
    sides = (t.get("size") or {}).get("sides") or []
    side_of = {}
    for n, s in enumerate(sides[:2]):
        for rid in s.get("root_ids") or []:
            side_of.setdefault(str(rid), n)

    def key(leg):
        lots = _num(leg.get("lots")) or 0.0
        side = side_of.get(str(leg.get("root_id")), 0 if lots >= 0 else 1)
        return (bool(leg.get("hedge")), leg.get("status") != "open", side, str(leg.get("month") or ""),
                str(leg.get("name") or ""))
    return sorted(t.get("legs") or [], key=key)


def panel_legs(data: dict, t: dict) -> html.Table:
    level = t.get("level") or {}
    note = str(level.get("note") or "")
    legs = _leg_order(t)
    if legs and all(leg.get("hedge") for leg in legs):
        legs = [dict(leg, hedge=False) for leg in legs]     # a trade of hedges only: no leg is "the hedge" of another
    body: List[Any] = []
    subs = [x for x in t.get("sub_spreads") or [] if x.get("legs")]
    if len(subs) > 1:
        taken: set = set()
        for sub in subs:
            mine = [leg for leg in legs if not leg.get("hedge") and leg.get("contract_id") not in taken
                    and leg.get("contract_id") in {x.get("contract_id") for x in sub.get("legs") or []}]
            if not mine:
                continue
            taken.update(leg.get("contract_id") for leg in mine)
            ids = [str(i) for leg in mine for i in leg.get("trade_ids") or []]
            what = str(sub.get("what_it_is") or "")
            kind = tf.type_label(sub.get("type") or "").lower()
            body.append(_sub_tr(what if kind.split("-")[0] in what.lower() else f"{what} · {kind}", ids, data))
            body.extend(leg_tr(data, leg, note) for leg in mine)
        rest = [leg for leg in legs if not leg.get("hedge") and leg.get("contract_id") not in taken]
        if rest:
            body.append(_sub_tr("Other legs", [str(i) for leg in rest for i in leg.get("trade_ids") or []], data))
            body.extend(leg_tr(data, leg, note) for leg in rest)
    else:
        body.extend(leg_tr(data, leg, note) for leg in legs if not leg.get("hedge"))
    hedges = [leg for leg in legs if leg.get("hedge")]
    if hedges:
        body.append(_sub_tr("Hedge", [str(i) for leg in hedges for i in leg.get("trade_ids") or []], data))
        body.extend(leg_tr(data, leg, note) for leg in hedges)
    ids = [str(i) for i in t.get("trade_ids") or []]
    carry = _num(t.get("carry_per_month"))
    carry_cell = (html.Span(full_signed(carry), title=plain_words(tf.research_words(data.get("research") or {})) or None)
                  if carry is not None else html.Span("Not summed", className="tk-sub",
                                                      title=plain_words(str(t.get("carry_reason") or "not given"))))
    gross = _num(t.get("gross_usd"))
    foot = [html.Tr([html.Td("Trade total", className="l", colSpan=3),
                     html.Td(format_cell(gross) if gross is not None else missing_cell(t.get("notional_reason") or "no gross"),
                             title="gross USD value of the open legs"),
                     html.Td(""), html.Td(""),
                     html.Td(full_signed(fill_sum(data, "daily", ids)[0])),
                     html.Td(full_signed(fill_sum(data, "ltd", ids)[0])), html.Td(carry_cell)])]
    return html.Table([_leg_head(data.get("research")), html.Tbody(body), html.Tfoot(foot)],
                      className="book-table tk-table tk-legs")


def panel_facts(t: dict) -> html.Table:
    """The trade's facts beside its legs, a small key / value table (layout wave 2026-09-29: never a
    loose line): USD per 1-unit move of the level, the balance of its sides, its first fill. All
    read from the engine's fields; nothing computed."""
    level, size = t.get("level") or {}, t.get("size") or {}
    rows: List[Any] = []
    usd, words = _usd_per_move(level)
    move_key = words[:1].upper() + words[1:] if usd is not None else "USD per move of the level"
    rows.append((move_key, format_cell(usd) if usd is not None else missing_cell(words)))
    gap = _num(size.get("value_gap"))
    if gap is not None:
        tol = pct_text(size.get("tolerance") or 0.1)
        rows.append(("Balance", html.Span(f"Sides {pct_text(gap)} apart",
                                          className="cell-amber" if size.get("unbalanced") else None,
                                          title=f"{'Unbalanced' if size.get('unbalanced') else 'Balanced'}: the sides' "
                                                f"values differ by {pct_text(gap)} (the limit is {tol})")))
    if t.get("first_trade_date"):
        rows.append(("First fill", day_text(t.get("first_trade_date"), None)))
    return html.Table(html.Tbody([html.Tr([html.Td(k, className="tk-kv-k"), html.Td(v)]) for k, v in rows]),
                      className="tk-table tk-kv")


def hedge_line(t: dict) -> Optional[html.Div]:
    """The hedge line under the legs (a designed line, `tk-hedge-line`): the currency, the China legs'
    USD, the hedge's USD and the coverage (amber below 80 % or above 150 %); None with no hedge."""
    hedge = t.get("hedge") or {}
    if not hedge.get("present"):
        return None
    cov = _num(hedge.get("coverage"))
    exp, hed = _num(hedge.get("exposure_usd")), _num(hedge.get("hedge_usd"))
    if cov is not None:
        amber = cov < 0.8 or cov > 1.5
        body: List[Any] = [f"{hedge.get('currency')}: China legs {format_cell(exp)} USD · hedge {format_cell(hed)} "
                           "USD · coverage ", html.Span(pct_text(cov), className="cell-amber" if amber else None)]
        title = plain_words(hedge.get("direction_note") or "") or None
    else:
        size = f" {format_cell(hed)} USD" if hed is not None else ""
        body = [f"{hedge.get('currency') or ''}{size}: {cap(str(hedge.get('reason') or 'coverage not read'))}".strip()]
        title = None
    return html.Div([html.Span("Hedge", className="tk-k")] + body, className="tk-hedge-line", title=title)


def _closes(first: str, as_of: str, n: int = HISTORY_POINTS) -> List[str]:
    """The business days from `first` to `as_of` (the as-of itself last), thinned to at most `n`."""
    from engine.pnl.calendar import _is_business_day, load_holidays
    hol = load_holidays()
    try:
        d, end = dt.date.fromisoformat(first[:10]), dt.date.fromisoformat(as_of)
    except ValueError:
        return [as_of]
    days = []
    while d <= end:
        if _is_business_day(d, hol):
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    if not days or days[-1] != as_of:
        days.append(as_of)
    if len(days) > n:
        step = len(days) / float(n - 1)
        picked = sorted({days[min(len(days) - 1, int(round(i * step)))] for i in range(n - 1)} | {days[-1]})
        days = picked
    return days


def history(conn: sqlite3.Connection, data: dict, t: dict) -> dict:
    """`level_history` of the trade over its closes (at most 60), memoised per trade."""
    from engine.spreads.trades import level_history
    from ui.tabs.blotter_pricing import priced_value_book, research_inputs_key

    def compute():
        dates = _closes(str(t.get("first_trade_date") or data["as_of"]), data["as_of"])
        return level_history(conn, t, dates, value_fn=priced_value_book)
    return _memo("level", conn, data["as_of"], compute, extra=(str(t.get("trade")), *research_inputs_key()))


def level_figure(hist: dict, t: dict) -> dict:
    pts = hist.get("points") or []
    level = t.get("level") or {}
    xs = [p["date"] for p in pts]
    have_level = any(_num(p.get("level")) is not None for p in pts)
    traces: List[dict] = []
    shapes: List[dict] = []
    annotations: List[dict] = []
    if have_level:
        ys = [_num(p.get("level")) for p in pts]
        unit = unit_words(hist.get("level_unit") or level.get("unit"))
        traces.append({"x": xs, "y": ys, "type": "scatter", "mode": "lines", "name": "level",
                       "line": {"color": "#0f1f3d", "width": 2}, "connectgaps": False,
                       "hovertemplate": "%{x|%d %b %Y}: %{y}" + (f" {unit}" if unit else "") + "<extra></extra>"})
        entry = _num(hist.get("level_entry"))
        if entry is not None and xs:
            shapes.append({"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": entry, "y1": entry,
                           "line": {"color": "#c9a227", "width": 1.5, "dash": "dash"}})
            annotations.append({"xref": "paper", "x": 0, "y": entry, "text": "Entry", "showarrow": False,
                                "xanchor": "left", "yanchor": "bottom", "font": {"size": 10, "color": "#8a6d0b"}})
        last = next(((x, y) for x, y in zip(reversed(xs), reversed(ys)) if y is not None), None)
        if last:
            annotations.append({"x": last[0], "y": last[1], "text": "Now", "showarrow": True, "arrowhead": 0,
                                "ax": 18, "ay": -14, "font": {"size": 10}})
        rolls = [(p["date"], _num(p.get("level")), "; ".join(p.get("rolls") or [])) for p in pts if p.get("rolls")]
        if rolls:
            traces.append({"x": [r[0] for r in rolls], "y": [r[1] for r in rolls], "type": "scatter", "mode": "markers",
                           "name": "roll", "text": [r[2] for r in rolls],
                           "marker": {"symbol": "diamond", "size": 9, "color": "#c9a227"},
                           "hovertemplate": "Roll %{x|%d %b}: %{text}<extra></extra>"})
        ytitle = unit or "ratio"
    else:
        ys = [_num(p.get("ltd_usd")) for p in pts]
        traces.append({"x": xs, "y": ys, "type": "scatter", "mode": "lines", "name": "LTD",
                       "line": {"color": "#0f1f3d", "width": 2},
                       "hovertemplate": "%{x|%d %b %Y}: LTD %{y:$,.0f}<extra></extra>"})
        rolls = [(p["date"], _num(p.get("ltd_usd")), "; ".join(p.get("rolls") or [])) for p in pts if p.get("rolls")]
        if rolls:
            traces.append({"x": [r[0] for r in rolls], "y": [r[1] for r in rolls], "type": "scatter", "mode": "markers",
                           "name": "roll", "text": [r[2] for r in rolls],
                           "marker": {"symbol": "diamond", "size": 9, "color": "#c9a227"},
                           "hovertemplate": "Roll %{x|%d %b}: %{text}<extra></extra>"})
        ytitle = "LTD USD"
    return {"data": traces, "layout": {
        "height": 220, "margin": {"l": 56, "r": 24, "t": 10, "b": 28}, "showlegend": False,
        "xaxis": {"type": "date", "tickformat": "%d %b"}, "yaxis": {"title": {"text": ytitle, "font": {"size": 10}},
                                                                    "zeroline": False},
        "shapes": shapes, "annotations": annotations, "plot_bgcolor": "#fff", "paper_bgcolor": "#fff"}}


def panel_chart(conn: sqlite3.Connection, data: dict, t: dict) -> Any:
    try:
        hist = history(conn, data, t)
    except Exception as exc:  # noqa: BLE001 -- the panel still shows the legs
        log.exception("Book: level history failed for %s", t.get("trade"))
        return html.Div(missing_cell(f"the level history could not be read ({type(exc).__name__}: {exc})"),
                        className="tk-chart")
    level = t.get("level") or {}
    have = any(_num(p.get("level")) is not None for p in hist.get("points") or [])
    title = ("Level since the first fill" if have else
             f"LTD since the first fill (no single level: {level.get('reason') or level.get('now_reason') or 'none'})")
    left = hist.get("dates_left_out") or []
    return html.Div(className="tk-chart", children=[
        html.Div([html.Span(title, className="book-h"),
                  marker(f"{len(left)} closes left out", _lines(*[str(x) for x in left][:10]), "marker--small")
                  if left else None]),
        dcc.Graph(figure=level_figure(hist, t), config={"displayModeBar": False}, style={"height": "220px"})])


def panel_tr(conn: sqlite3.Connection, data: dict, t: dict) -> html.Tr:
    name = str(t.get("trade") or "")
    flags = t.get("flags") or []
    parts: List[Any] = []
    if flags:
        parts.append(html.Div([html.Div(flag_sentence(f), className="tk-flag-line" + (" tk-flag-line--red" if is_red(f) else ""))
                               for f in sorted(flags, key=lambda f: not is_red(f))], className="tk-flag-lines"))
    if t.get("pseudo"):
        parts.append(pseudo_fills(data, t))
    else:
        parts.append(html.Div(className="tk-panel-body", children=[
            html.Div([html.Div([panel_legs(data, t), hedge_line(t)], className="tk-legs-main"), panel_facts(t)],
                     className="tk-panel-legs"),
            panel_chart(conn, data, t)]))
    n = len(t.get("trade_ids") or [])
    later = sum(1 for f in (data.get("fills") or {}).values()
                if f.get("strategy") == name and f.get("trade_date", "") > str(data.get("as_of") or ""))
    fills_label = (f"See fills ({n} + {later} after {day_text(data.get('as_of'), data.get('as_of'))})" if later
                   else f"See fills ({n})")
    parts.append(html.Div(className="tk-links", children=[
        tf.link(fills_label, "blotter", name, "book-fills",
                "Open the Blotter filtered to this trade's fills" + (
                    f": it also lists the {later} dealt after the as-of date" if later else "")),
        tf.link("P&L history", "pnl", name, "book-pnl", "Open the P&L tab filtered to this trade"),
        tf.link("Risk", "risk", name, "book-risk", "Open the Risk tab filtered to this trade"),
    ]))
    return html.Tr(html.Td(parts, colSpan=len(COLUMNS), className="l tk-panel-cell"), className="tk-panel")


def pseudo_fills(data: dict, t: dict) -> html.Table:
    """The fills on no trade, one row each."""
    fills = data.get("fills") or {}
    df = _df_rows(data, t.get("trade_ids") or [])
    by_id = {str(r["trade_id"]): r for r in df.to_dict("records")} if not df.empty else {}
    body = []
    for tid in t.get("trade_ids") or []:
        f, r = fills.get(str(tid)) or {}, by_id.get(str(tid)) or {}
        daily, ltd = fill_sum(data, "daily", [tid]), fill_sum(data, "ltd", [tid])
        body.append(html.Tr([html.Td(f"{tid} · {f.get('broker_symbol') or f.get('instrument_id')}", className="l"),
                             html.Td(day_text(f.get("trade_date"), data["as_of"]), className="l"),
                             html.Td(f"{(f.get('quantity') or 0):+,.0f}"), html.Td(price_text(f.get("price"))),
                             html.Td(price_text(r.get("mark")) if _num(r.get("mark")) is not None
                                     else missing_cell(r.get("reason") or "no mark")),
                             html.Td(full_signed(daily[0])), html.Td(full_signed(ltd[0]))], className="tk-leg"))
    return html.Table([html.Thead(html.Tr([html.Th(x, className="l" if i < 2 else None) for i, x in enumerate(
        ("Fill", "Date", "Quantity", "Fill price", "Mark", "Daily", "LTD"))])), html.Tbody(body)],
        className="book-table tk-table tk-legs")


# --------------------------------------------------------------------------- the table
def own_cols(state: Optional[dict]) -> Dict[str, Any]:
    """The trade view's own column filters: the Book's, less the By contract view's ('c-' keys)."""
    return {k: v for k, v in tf.tab_filters(state, TAB).items() if not k.startswith(CONTRACT_COL)}


def view_filtered(state: Optional[dict]) -> bool:
    """A filter the trade view applies is set: the search, a shared list, or one of its own columns."""
    s = tf.normal(state)
    return bool(s["search"].strip() or s["type"] or s["commodity"] or s["trade"] or own_cols(s))


def visible(data: dict, state: Optional[dict], risk: Optional[dict] = None) -> List[dict]:
    """The trades the search and every column's filter keep (whole trades, never a leg)."""
    cols = own_cols(state)
    shown = tf.apply(data.get("trades") or [], state)
    if not cols:
        return shown
    rrows = risk_rows(risk)
    return [t for t in shown
            if tf.keeps_cols(t, cols, lambda t, col: filter_value(data, t, col, rrows), filter_lists)]


def table(conn: sqlite3.Connection, data: dict, state: Optional[dict], sort: Optional[dict], opened: Sequence[str],
          folds: Optional[dict], risk: Optional[dict]) -> Tuple[html.Table, List[str]]:
    s = tf.normal(state)
    shown = visible(data, s, risk)
    ready = risk is not None
    rrows = risk_rows(risk)
    opened_set = set(opened or [])
    folds = folds or {}
    folded = set(folds.get("groups") or [])
    body: List[Any] = [total_tr(data, shown, view_filtered(s))]
    open_rows = [t for t in shown if t.get("status") == "open" or (t.get("pseudo") and t.get("status") != "closed")]
    closed_rows = [t for t in shown if t not in open_rows]

    def add(t):
        name = str(t.get("trade") or "")
        is_open = name in opened_set
        body.append(trade_tr(data, t, rrows.get(name), ready, is_open))
        if is_open:
            try:
                body.append(panel_tr(conn, data, t))
            except Exception as exc:  # noqa: BLE001 -- one panel's reason, never the table
                log.exception("Book: the panel of %s failed", name)
                body.append(html.Tr(html.Td(missing_cell(f"the panel could not be built ({type(exc).__name__}: {exc})"),
                                            colSpan=len(COLUMNS), className="l"), className="tk-panel"))

    group = tf.trade_group(s)
    if group == tf.GROUP_NONE:
        for t in sort_rows(data, open_rows, sort, rrows):
            add(t)
    else:
        by: Dict[str, List[dict]] = {}
        for t in open_rows:
            by.setdefault(tf.group_of(t, group), []).append(t)
        for label in tf.group_order(by, group):
            rows = by[label]
            body.append(group_tr(data, label, rows, label in folded))
            if label not in folded:
                for t in sort_rows(data, rows, sort, rrows):
                    add(t)
    if closed_rows:
        year = str(data["as_of"])[:4]
        this_year = sum(1 for t in closed_rows if closed_on(data, t)[:4] == year)
        title = (f"Closed this year ({len(closed_rows)})" if this_year == len(closed_rows)
                 else f"Closed ({len(closed_rows)}, {this_year} this year)")
        is_open = bool(folds.get("closed"))
        ids = [str(i) for t in closed_rows for i in t.get("trade_ids") or []]
        body.append(html.Tr([
            html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"), title], className="l", colSpan=7,
                    title="The fully flat trades: Entry and Now are the entry and exit levels, LTD the final P&L"),
            money_td(*fill_sum(data, "daily", ids)), money_td(*fill_sum(data, "ltd", ids)), html.Td(""), html.Td("")],
            id=CLOSED_FOLD_ID, n_clicks=0, className="tk-fold"))
        if is_open:
            for t in sorted(closed_rows, key=lambda t: closed_on(data, t), reverse=True):
                add(t)
    return (html.Table([head(sort, data.get("research") or {}, s, tf.options_for(data.get("trades") or [])),
                        html.Tbody(body)], id=TABLE_ID,
                       className="book-table book-grid tk-table"),
            [str(t.get("trade")) for t in shown])


def headline(data: dict, state: Optional[dict], risk: Optional[dict] = None) -> Optional[html.Span]:
    """The rows' MTD and YTD, inline in the card's title strip after "Trades", only while a filter is
    set (the header's are the whole book's); None otherwise. Nothing else sits between the header and
    the card (user, 2026-09-30: "net usd and flags ... alone in this row its clunky"): Gross, Net and
    the flags are on the table's total row, Daily and LTD there too (one place per number)."""
    s = tf.normal(state)
    if not view_filtered(s):
        return None
    tot = totals(data, visible(data, s, risk))

    def money(label, k):
        v, n, r = tot[k]
        return html.Span(title=f"The {label} P&L of the rows showing (the header's is the whole book's)",
                         className="tk-strip-fig", children=[
            html.Span(label, className="tk-k"), " ", km_cell(v, reason=_lines(*r[:6])),
            marker(f"excl. {n}", _excl_hover(n, r), "marker--small") if n and v is not None else None])

    return html.Span([money("MTD", "mtd"), money("YTD", "ytd")], className="tk-strip-figs")


ISSUE_KIND_WORDS = {"daily": "Daily", "ltd": "LTD"}


def issue_items(data: dict, risk: Optional[dict]) -> List[Any]:
    """The drawer's rows as (kind, where, reason): a fill with no figure under its trade ("Daily" and
    "LTD" of the same reason made one row by the drawer), the engine's notes, the risk and research
    caveats."""
    items: List[Any] = [(kind, "", why) for kind, why in data.get("errors") or []]
    for n in data.get("notes") or []:
        items.append(("Trades", "", str(n)))
    trade_of = {str(i): str(t.get("trade") or "") for t in data.get("trades") or [] for i in t.get("trade_ids") or []}
    for key in ("daily", "ltd"):
        view = (data.get("views") or {}).get(key)
        if view is None:
            continue
        bad = view.rows[view.rows["value"].isna()] if not view.rows.empty else view.rows
        for tid, why in zip(bad.get("trade_id", []), bad.get("reason", [])):
            where = " · ".join(x for x in (trade_of.get(str(tid), ""), f"Fill {tid}") if x)
            items.append((ISSUE_KIND_WORDS[key], where, str(why or "no figure")))
    if risk is not None and not risk.get("available"):
        items.append(("Risk", "", f"z and hedge % unavailable: {risk.get('reason') or 'no risk history'}"))
    src = data.get("research") or {}
    if src.get("kind") != "real":
        items.append(("Research", "z, carry, roll-down, hedge %",
                      src.get("note") or "the research history is not verified as real"))
    return items


# --------------------------------------------------------------------------- CSV
def csv_frame(data: dict, state: Optional[dict], sort: Optional[dict], risk: Optional[dict]) -> pd.DataFrame:
    rrows = risk_rows(risk)
    rows = []
    for t in sort_rows(data, visible(data, state, risk), sort, rrows):
        ids = [str(i) for i in t.get("trade_ids") or []]
        level = display_level(data, t)
        r = rrows.get(str(t.get("trade"))) or {}
        nx = t.get("next") or {}
        closed = t.get("closed") if t.get("status") == "closed" else None
        rows.append({
            "Trade": t.get("trade"), "Status": t.get("status"), "Type": tf.trade_type_label(t),
            "Commodity": tf.family_label(t), "What it is": what_line(t), "Size": size_text(t)[0],
            "Gross USD": t.get("gross_usd"), "Net USD": t.get("net_usd"),
            "Value gap": (t.get("size") or {}).get("value_gap"),
            "Entry": level.get("entry"), "Now": level.get("now"), "Previous close level": level.get("prev"),
            "Today": level.get("change"), "Level unit": level.get("unit"), "Level mode": level.get("mode"),
            "Move in sigma": r.get("move_sigma"), "Level daily sd": r.get("level_sd"),
            "Closed on": (closed or {}).get("close_date"), "Exit basis": (closed or {}).get("exit_basis"),
            "USD per 1 unit of the level": level.get("usd_per_unit"),
            "z": r.get("z"), "Percentile": r.get("percentile"), "z at entry": r.get("z_entry"),
            "Hedge %": r.get("hedge_pct"), "Leg correlation": r.get("leg_correlation"),
            "Daily": fill_sum(data, "daily", ids)[0], "MTD": fill_sum(data, "mtd", ids)[0],
            "YTD": fill_sum(data, "ytd", ids)[0], "LTD": fill_sum(data, "ltd", ids)[0],
            "Carry per month": t.get("carry_per_month"), "Hedge coverage": (t.get("hedge") or {}).get("coverage"),
            "Next event": nx.get("event"), "Next date": nx.get("date"), "Next estimated": nx.get("estimated"),
            "Flags": "; ".join(("RED " if is_red(f) else "") + flag_sentence(f) for f in t.get("flags") or []),
            "Unrecognised fills": " ".join(str(i) for leg in t.get("legs") or [] if leg.get("unrecognised")
                                           for i in leg.get("trade_ids") or []),
            "PBRoot": ", ".join(t.get("pb_roots") or []), "Fills": len(ids), "Trade ids": " ".join(ids),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- render
def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render_parts(as_of: Optional[str], db_path, state: Optional[dict] = None, sort: Optional[dict] = None,
                 opened: Sequence[str] = (), folds: Optional[dict] = None, wait_risk: bool = False,
                 view: Optional[str] = None, contract: Optional[dict] = None) -> dict:
    """{body, shown (bool), headline, table, foot, names, risk_ready, title} for `as_of`. `view`
    'contract' draws the By contract view (`ui.tabs.book_contracts`) from `contract` = {sort,
    opened, folds} (its own stores); anything else the trade view."""
    from ui.tabs import book_contracts as bc
    by_contract = view == bc.VIEW_CONTRACT
    out = {"body": None, "shown": False, "headline": None, "table": None, "foot": None, "names": [], "risk_ready": True,
           "title": title(by_contract)}
    if not as_of:
        out["body"] = message_box("No as-of date available.")
        return out
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        out["body"] = message_box(f"Database not available ({exc}).")
        return out
    try:
        if trades_on_file(conn) == 0:
            out["body"] = empty_state(idx=TAB)
            return out
        data = gathered(conn, as_of)
        from ui.tabs.blotter_pricing import shared_trade_risk
        try:
            risk = shared_trade_risk(conn, as_of, wait=wait_risk and not by_contract)
        except Exception as exc:  # noqa: BLE001 -- the z column says why
            log.exception("Book: trade risk failed for %s", as_of)
            risk = {"available": False, "reason": f"{type(exc).__name__}: {exc}", "trades": []}
        if by_contract:
            c = contract or {}
            tbl, head_figs, names = bc.render(conn, data, state, c.get("sort"), c.get("opened") or [], c.get("folds"))
            out.update(shown=True, headline=head_figs, table=tbl, names=names)
        else:
            tbl, names = table(conn, data, state, sort, opened, folds, risk)
            out.update(shown=True, headline=headline(data, state, risk), table=tbl, names=names,
                       risk_ready=risk is not None)
        prepull = None if data.get("has_marks") else html.Div(PREPULL_TEXT, className="book-prepull")
        out["foot"] = html.Div([prepull, issues_drawer(issue_items(data, risk))], className="tk-foot")
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("Book could not be built for %s", as_of)
        out["body"] = html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The book could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    return _tidy_parts(out)


def _tidy_parts(out: dict) -> dict:
    """Every hover in the parts starting with a capital (`formatting.tidy`)."""
    for key in ("body", "headline", "table", "chart", "track", "foot", "title"):
        if out.get(key) is not None:
            tidy(out[key])
    return out


def render(as_of: Optional[str], db_path, state: Optional[dict] = None) -> html.Div:
    """The whole body for a direct render (the risk figures waited for)."""
    p = render_parts(as_of, db_path, state, wait_risk=True)
    if not p["shown"]:
        return html.Div([p["body"]])
    return html.Div([html.Div([html.Div(className="tk-strip", children=[p["title"], p["headline"]]),
                               p["table"]], className=CARD_CLASS), p["foot"]])


def options_of(as_of: str, db_path) -> Dict[str, List[dict]]:
    """The filter bar's choices on the Book (the trades present)."""
    conn = _open(db_path)
    try:
        if trades_on_file(conn) == 0:
            return {}
        return tf.options_for(gathered(conn, as_of).get("trades") or [])
    finally:
        conn.close()


# --------------------------------------------------------------------------- layout and callbacks
TRADES_ABOUT = ("One row per trade (a PBRoot name). Click a row for its legs, its level since entry and its links; "
                "the first row is the total of the rows showing, with Gross, Net and the flags. Each column filters "
                "from the funnel in its heading.")
CONTRACTS_ABOUT = ("One row per contract held, netted across every trade that holds it, with its clearer: what the "
                   "broker statements show. Click a row for the trades holding it; the first row is the total of the "
                   "rows showing. Each column filters from the funnel in its heading.")


def title(by_contract: bool = False):
    """The table strip's title of the view: "Trades" or "Contracts", its definition on hover."""
    return about("Contracts" if by_contract else "Trades", CONTRACTS_ABOUT if by_contract else TRADES_ABOUT,
                 level="span", className="tk-title")


def card_class(by_contract: bool = False) -> str:
    return CARD_CLASS + (" book-view--contract" if by_contract else "")


def layout(default_date: Optional[str] = None) -> html.Div:
    """The shell: the message slot, then (hidden with no book) the filter bar, the headline, the
    table card with its strip (Expand all, Collapse all, Download CSV) and the footer; the session
    stores and the risk poll."""
    from ui.tabs import book_contracts as bc
    return html.Div(className="book-tab", children=[
        html.Div(html.H2("Book", className="tab-title"), className="tab-header"),
        html.Div(id=BODY_ID, children=[message_box("Loading the book...")]),
        html.Div(id=CONTENT_ID, style=HIDDEN, children=[
            html.Div(id=CARD_ID, className=CARD_CLASS, children=[
                html.Div(className="tk-strip", children=[
                    html.Div(className="tk-strip-lead", children=[
                        html.Span(title(), id=TITLE_ID),
                        bc.switch(),
                        html.Span(id=HEADLINE_ID, className="tk-strip-slot")]),
                    tf.bar_slot(TAB),
                    html.Button("Expand all", id=EXPAND_ALL_ID, n_clicks=0, className="btn btn--ghost"),
                    html.Button("Collapse all", id=COLLAPSE_ALL_ID, n_clicks=0, className="btn btn--ghost"),
                    html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                                title="The rows showing at full figures, every column and every hover figure"),
                    dcc.Download(id=DOWNLOAD_ID)]),
                html.Div(id=TABLE_SLOT_ID, className="tk-table-slot"),
            ]),
            html.Div(id=FOOT_ID),
        ]),
        dcc.Store(id=OPEN_STORE_ID, storage_type="session"),
        dcc.Store(id=SORT_STORE_ID, storage_type="session"),
        dcc.Store(id=FOLD_STORE_ID, storage_type="session"),
        dcc.Store(id=bc.OPEN_STORE_ID, storage_type="session"),
        dcc.Store(id=bc.SORT_STORE_ID, storage_type="session"),
        dcc.Store(id=bc.FOLD_STORE_ID, storage_type="session"),
        dcc.Store(id=SHOWN_STORE_ID),
        dcc.Store(id=RISK_READY_ID),
        dcc.Interval(id=RISK_POLL_ID, interval=1500, n_intervals=0, disabled=True),
    ])


build_layout = layout


def _clicked() -> bool:
    trig = dash.ctx.triggered or []
    return bool(trig and trig[0].get("value"))


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    from ui.tabs import book_contracts as bc
    tf.register_bar(app, TAB, get_db_path, options_of)
    bc.register_callbacks(app, get_db_path)

    @app.callback(
        Output(BODY_ID, "children"), Output(CONTENT_ID, "style"), Output(HEADLINE_ID, "children"),
        Output(TABLE_SLOT_ID, "children"), Output(FOOT_ID, "children"), Output(SHOWN_STORE_ID, "data"),
        Output(RISK_POLL_ID, "disabled"), Output(TITLE_ID, "children"), Output(CARD_ID, "className"),
        Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(tf.STORE_ID, "data"),
        Input(SORT_STORE_ID, "data"), Input(OPEN_STORE_ID, "data"), Input(FOLD_STORE_ID, "data"),
        Input(RISK_READY_ID, "data"), Input(bc.VIEW_ID, "value"), Input(bc.SORT_STORE_ID, "data"),
        Input(bc.OPEN_STORE_ID, "data"), Input(bc.FOLD_STORE_ID, "data"),
    )
    def _render(as_of, _rev, state, sort, opened, folds, _ready, view, c_sort, c_opened, c_folds):
        p = render_parts(as_of, get_db_path(), state, sort, opened or [], folds, view=view,
                         contract={"sort": c_sort, "opened": c_opened or [], "folds": c_folds})
        by_contract = view == bc.VIEW_CONTRACT
        if not p["shown"]:
            return p["body"], HIDDEN, None, None, None, [], True, p["title"], card_class(by_contract)
        return (None, {}, compact(p["headline"]), compact(p["table"]), compact(p["foot"]), p["names"],
                p["risk_ready"], p["title"], card_class(by_contract))

    @app.callback(Output(RISK_READY_ID, "data"), Input(RISK_POLL_ID, "n_intervals"), State(AS_OF_STORE_ID, "data"),
                  prevent_initial_call=True)
    def _risk_poll(_n, as_of):
        if not as_of:
            return dash.no_update
        from ui.tabs.blotter_pricing import shared_trade_risk
        conn = _open(get_db_path())
        try:
            ready = shared_trade_risk(conn, as_of, wait=False) is not None
        finally:
            conn.close()
        return f"{as_of}-{dt.datetime.now().timestamp()}" if ready else dash.no_update

    @app.callback(Output(OPEN_STORE_ID, "data"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  Input(EXPAND_ALL_ID, "n_clicks"), Input(COLLAPSE_ALL_ID, "n_clicks"),
                  State(OPEN_STORE_ID, "data"), State(SHOWN_STORE_ID, "data"), State(bc.VIEW_ID, "value"),
                  prevent_initial_call=True)
    def _toggle(_rows, _all, _none, current, shown, view):
        if not _clicked():
            return dash.no_update
        trig = dash.ctx.triggered_id
        if trig in (EXPAND_ALL_ID, COLLAPSE_ALL_ID) and view == bc.VIEW_CONTRACT:
            return dash.no_update                   # the By contract view's own panels (book_contracts)
        if trig == EXPAND_ALL_ID:
            return list(shown or [])
        if trig == COLLAPSE_ALL_ID:
            return []
        if isinstance(trig, dict):
            name = str(trig.get("idx") or "")
            cur = list(current or [])
            return [x for x in cur if x != name] if name in cur else cur + [name]
        return dash.no_update

    @app.callback(Output(SORT_STORE_ID, "data"), Input({"type": SORT_TYPE, "idx": ALL}, "n_clicks"),
                  State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _sort(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        return next_sort(current, str(trig.get("idx") or ""))

    @app.callback(Output(FOLD_STORE_ID, "data"), Input({"type": GROUP_ROW_TYPE, "idx": ALL}, "n_clicks"),
                  Input(CLOSED_FOLD_ID, "n_clicks"), State(FOLD_STORE_ID, "data"), prevent_initial_call=True)
    def _fold(_groups, _closed, current):
        if not _clicked():
            return dash.no_update
        cur = dict(current or {})
        trig = dash.ctx.triggered_id
        if trig == CLOSED_FOLD_ID:
            cur["closed"] = not cur.get("closed")
            return cur
        if isinstance(trig, dict):
            label = str(trig.get("idx") or "")
            groups = list(cur.get("groups") or [])
            cur["groups"] = [g for g in groups if g != label] if label in groups else groups + [label]
            return cur
        return dash.no_update

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(tf.STORE_ID, "data"), State(SORT_STORE_ID, "data"), State(bc.VIEW_ID, "value"),
                  State(bc.SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, state, sort, view, c_sort):
        if not n_clicks or not as_of:
            return dash.no_update
        conn = _open(get_db_path())
        try:
            from ui.tabs.blotter_pricing import shared_trade_risk
            data = gathered(conn, as_of)
            if view == bc.VIEW_CONTRACT:
                frame, name = bc.csv_of(conn, data, state, c_sort), f"book_contracts_{as_of}.csv"
            else:
                frame = csv_frame(data, state, sort, shared_trade_risk(conn, as_of, wait=False))
                name = f"book_{as_of}.csv"
        finally:
            conn.close()
        return dcc.send_data_frame(frame.to_csv, name, index=False)

    app.clientside_callback(_OPEN_UPLOAD_JS, Output(EMPTY_UPLOAD_SINK_ID, "data"),
                            Input({"type": EMPTY_UPLOAD_TYPE, "idx": ALL}, "n_clicks"), prevent_initial_call=True)
