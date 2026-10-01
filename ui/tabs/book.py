"""Book tab: the trade list (Phase G, 2026-09-29; CLAUDE.md "Screens redesign plan -> Phase G", the
design doc's "Table specs").

The trade is the unit: one row per trade name (Jason's PBRoot suffix, `JSHY10_ZNA1` and
`JSHY10.3_ZNA1` one trade), every leg and hedge under it, never split. Columns, one line per row,
each answering one of the four morning questions (user, 2026-09-30: what and how big, where in and
where now, making money, anything coming or broken): Trade | What it is | Quantity | Entry level |
Level now | z | P&L today | Open | Locked in | P&L since entry | Next date | Check (Open and Locked
in are the engine's split of P&L since entry, `pnl_open` / `pnl_locked`). Type, Entry date and Move
are in the opened trade's facts. A column no trade has data for (z, Open / Locked in, Next date,
Check) is hidden with one grey line under the table saying why (`hidden_columns`). The legs' fills
and marks are on the levels' hovers; a row's caveats (a figure
leaving fills out) sit in one small "i" after its name (`formatting.row_info`). Nothing sits between the header and the card (user, 2026-09-30): the card's
title strip holds the view switch, the search, a plain Download CSV link and, only while a filter
is set, the rows' MTD and YTD (no Group switch, no Expand / Collapse all since 2026-09-30). The
table's first row is the total ("Book · 6 trades", "Filtered · 3 of 6"), sticky, with the count of
trades to check in its Check cell, equal unfiltered to
the top bar's Daily, MTD and LTD to the cent; the fully flat trades are in the closed fold at the
bottom. Under every trade row its legs always show (user, 2026-09-30), lighter and indented, hedges
last in italics, a trade of several spreads with a head row per part (`leg_rows`): each leg's size in
words, average fill, mark, P&L today, Open, Locked in and P&L since entry, the legs adding up to
their trade. A click on a trade opens its panel after its legs: the flags, what the leg rows do not
carry (each open leg's value in USD and roll-down, the trade's gross and carry), the facts, the
hedge line, the level since the first fill with its entry dashed and its rolls marked, and links to
its fills (Blotter), its P&L history and its risk, each filtered to it. Above the table, one summary
card (`summary_card`): the whole book broken down by spread type, commodity family or commodity,
each group's trades, P&L today and P&L since entry, the Book row the header's figures.

What is read, never recomputed:
  - `engine.spreads.trade_book` (`blotter_pricing.shared_trade_book`, once per database revision
    and as-of, shared with P&L and Risk): the type, what it is, legs, size and balance, level,
    carry, hedge, next date, flags, sub-spreads, rolls, gross and net;
  - the P&L per fill: `ui.tabs.pnl.period_rows` (the header's own split over the shared filled
    reader `priced_value_book`), summed per trade and per leg (display), so the total row is the
    header's figure;
  - `engine.risk.trades.trade_risk` (`blotter_pricing.shared_trade_risk`, computed on its own
    thread; the z columns fill in a moment after the table): `spread_z`, z at entry, now and exit
    per trade and per spread (2026-10-01), the
    Quantity hover's hedge % and correlation, from the book database's own Bloomberg price history
    (`price_history`); before the first pull the drawer says why once;
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

from ui.revision import DATA_REVISION_ID
from ui.tabs import trade_filter as tf
from ui.tabs.formatting import (
    cap, cap_parts, tidy,
    EN_DASH, MINUS, MISSING, about, compact, contract_name, day_text, format_cell, full_signed, fx_name, is_fx_pair,
    issues_drawer, km_cell, km_text, marker, missing_cell, month_label, parse_contract_id, pct_text,
    plain_words, price_decimals, price_text, quoted_unit, row_info, short_date, short_root_name, sign_class, size_words, strike_text,
    sum_known, title_name, z_text,
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
OPEN_STORE_ID = "book-open-trades"           # session: the trade names whose panel is open
SORT_STORE_ID = "book-sort-store"            # session: {"key", "dir": "desc" | "asc"} or None
FOLD_STORE_ID = "book-folds"                 # session: {"groups": [folded group labels], "closed": bool}
SHOWN_STORE_ID = "book-shown"                # the trade names showing
RISK_READY_ID = "book-risk-ready"
RISK_POLL_ID = "book-risk-poll"
ROW_TYPE = "book-trade-row"                  # a trade row: {"type", "idx": trade name}
LEGS_TYPE = "book-legs-caret"                # a trade's caret (no callback: ui/assets/book_filters.js shows its legs)
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

# The columns: key, title, alignment class ('l' = left), the title's one-sentence definition. The trade
# table of 2026-10-01 (user: "lets make these improvements straight away"): Size, the spread at entry and
# now, z at entry and now, the P&L, and one narrow Action column in place of Next date and Check.
COLUMNS: Tuple[Tuple[str, str, str, str], ...] = (
    ("trade", "Trade", "l", "Jason's trade name, the text after the underscore of the PBRoot. A trade of several "
                            "spreads lists each spread on the row under it; the caret before the name shows the "
                            "legs (hedges last, in italics). Click elsewhere on a trade for its hedge, facts, level "
                            "since entry and links."),
    ("what", "What it is", "l", "The position in plain words, the long side first, the exchange only where it "
                                "tells the sides apart; a trade of several spreads says how many and on what, each "
                                "spread on its own row under it. The whole of it on hover; the currency hedge and "
                                "the type are in the opened trade."),
    ("qty", "Size", "l", "The size of each side in the unit the spread is matched in: lots on one exchange, the "
                         "physical quantity for one commodity across exchanges or a crack, dollar value at the fill "
                         "between two commodities. Equal sides read \"200 lots a side\", sides within 10 % of "
                         "each other \"≈\" their average, sides further apart each one in amber. The exact sides "
                         "and their legs on hover."),
    ("entry", "Spread at entry", "", "The spread at entry: the fills' size-weighted level (a calendar near minus far "
                                     "in its unit, a China-against-West pair the converted ratio China over "
                                     "foreign, an outright its price). How it is built and each leg's average fill "
                                     "on hover."),
    ("now", "Spread now", "", "The same spread at the latest official marks; how it is built and each leg's mark on "
                              "hover."),
    ("z_entry", "Z at entry", "", "Where the spread stood on its first fill against its own daily closes in the "
                                  "calendar year to that day: (level minus their mean) over their standard "
                                  "deviation (Bloomberg price history); bold at 2 or more either way. The "
                                  "percentile and the window on hover."),
    ("z_now", "Z now", "", "The same for the spread today, against the calendar year to today; on a closed trade "
                           "(the Closed fold) the z at exit, the day it went flat."),
    ("daily", "P&L today", "", "Today's P&L in USD, every fill of the trade including its hedge."),
    ("open", "Open", "", "P&L on the lots you still hold, at today's price against their average entry."),
    ("locked", "Locked in", "", "P&L already made or lost on lots unwound, hedges matured or options expired."),
    ("ltd", "P&L since entry", "", "Open plus locked in: the P&L since the trade opened, in USD."),
    ("action", "Action", "l", "Blank unless something needs doing: a key date within 10 business days (first "
                              "notice, last trade, option expiry or LME prompt; red within 3, amber within 10, grey "
                              "≈ when estimated), a contract not recognised (red), a leg with no price, a price the "
                              "marks check flags, fills on no trade, a currency hedge that runs the wrong way, is "
                              "far too big or is left alone, or sides more than 10 % apart. The most pressing one "
                              "shows, the rest on hover."),
)
# The Next date cell's event in words (user, 2026-09-30: "Last trade 30 Nov", never "LT 30 Nov").
NEXT_ABBR = {"first notice": "First notice", "last trade": "Last trade", "option expiry": "Expiry",
             "LME prompt": "Prompt", "expiry": "Expiry", "prompt": "Prompt", "value date": "Value date"}
TITLE_ID = "book-title"                      # the strip's title: "Trades" or "Contracts" (the view)
CARD_ID = "book-main-card"                   # the table card; a class names the view (the CSS hides Group by contract)
CARD_CLASS = "book-card book-main tk-card"
CONTRACT_COL = "c-"                          # the By contract view's own column filters (ui/tabs/book_contracts.py)
# the opened trade's table of what the leg rows under the trade do not carry (2026-09-30)
LEG_COLUMNS = (("leg", "Leg", "l"), ("value", "Value USD", ""), ("roll", "Roll-down / mo", ""))


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


def level_decimals(level: dict, legs: Sequence[dict] = ()) -> Optional[int]:
    """The decimals of a level in its legs' own price unit (a calendar, one contract's price, an
    options trade's net premium): the most any of its legs' price cells shows (`price_decimals` on
    the leg's average fill, else its mark), so the level reads at the legs' precision, never coarser
    (2026-10-01: an SGX iron ore calendar of 96.5 / 96.7 read "0"). None for a ratio, a level in a
    template's own unit, or no priced leg: the unit's tick then."""
    if not level or level.get("mode") == "ratio" or level.get("unit") == "ratio":
        return None
    spec = level.get("spec") or {}
    spec_legs = spec.get("legs") or []
    same_unit = (level.get("source") in ("calendar", "price", "premium")
                 or (spec_legs and len({str(x.get("root_id") or "") for x in spec_legs}) == 1))
    if not same_unit:
        return None
    ids = ({str(x.get("instrument_id") or "") for x in spec_legs}
           | {str(x.get("instrument_id") or "") for x in level.get("price_legs") or []}) - {""}
    unit = str(level.get("unit") or "")
    best: Optional[int] = None
    for leg in legs or ():
        if leg.get("hedge") or leg.get("unrecognised") or (ids and str(leg.get("instrument_id") or "") not in ids):
            continue
        ref = _num(leg.get("avg_fill"))
        ref = ref if ref is not None else _num(leg.get("mark"))
        if ref is not None:
            d = price_decimals(unit, ref)
            best = d if best is None else max(best, d)
    return best


def level_text(value: Any, level: dict, signed: bool = False, legs: Sequence[dict] = ()) -> str:
    """A level in its own format: a ratio at 4 decimals, a price difference at its legs' precision
    (`level_decimals`; the unit's tick without legs), with a real minus. `signed`: a move of the level
    (the Move column), a real sign both ways ('−0.0023', '+20'): brackets are for money only (user,
    2026-09-30; CLAUDE.md "Tabs as views")."""
    v = _num(value)
    if v is None:
        return MISSING
    if level.get("mode") == "ratio" or level.get("unit") == "ratio":
        body = f"{abs(v):.4f}"
    else:
        unit = str(level.get("unit") or "")
        d = level_decimals(level, legs)
        if d is None:
            d = price_decimals(unit)
        body = price_text(abs(v), unit, decimals=d)
        if not signed:
            return price_text(v, unit, decimals=d)
    moved = bool(body.strip("0.,"))
    if signed:
        return ((MINUS if v < 0 else "+") if moved else "") + body
    return (MINUS if v < 0 and moved else "") + body


def _level_cell(value: Any, level: dict, reason: str, hover: str = "", estimated: bool = False,
                className: str = "", with_unit: bool = False, legs: Sequence[dict] = ()) -> Any:
    """A level figure at the row's one precision (`level_text`: the unit's tick, 4 decimals for a
    ratio or a premium with no unit), grey after a "≈" when `estimated`. The unit (`with_unit`, on
    Entry and Now since 2026-09-30; "ratio" for a ratio) sits in a fixed-width slot so the figures
    line up as a column."""
    v = _num(value)
    if v is None:
        return missing_cell(_lines(reason, hover) or "no level")
    unit = ""
    if with_unit:
        unit = "ratio" if level.get("mode") == "ratio" or level.get("unit") == "ratio" else unit_words(level.get("unit"))
    return html.Span([("≈ " if estimated else "") + level_text(v, level, legs=legs),
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
    the Upload button's pattern id differs per tab."""
    return html.Div(className="book-empty", children=[
        html.Div(className="book-card book-empty-main", children=[
            html.Div("No blotter loaded", className="book-empty-title"),
            html.Div("Upload Jason's blotter export, a .csv or .xlsx straight from the prime broker. An upload "
                     "merges by Trade Id; nothing else is ever typed in.", className="book-empty-text"),
            html.Div(className="book-empty-actions", children=[
                html.Button("Upload blotter", id={"type": EMPTY_UPLOAD_TYPE, "idx": idx}, n_clicks=0, className="btn",
                            title="Choose the blotter file (the same upload as the top bar's)"),
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
    not wait for them), once per database revision, as-of and config files. Shared: never edit."""
    from ui.tabs.blotter_pricing import config_inputs_key
    return _memo("gather", conn, as_of, lambda: _gather(conn, as_of), extra=config_inputs_key())


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
    from ui.tabs.blotter_pricing import price_history_summary
    data["history"] = price_history_summary(conn)
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


def _cents(v: float) -> float:
    """A money figure at the cent for its colour: a sum's -2e-10 shows a plain 0, never a red one."""
    return round(float(v), 2) + 0.0


def money_td(total: Optional[float], n_excl: int, reasons: Sequence[str], hover: str = "",
             className: str = "", check: Sequence[str] = (), row: bool = False) -> html.Td:
    """A k / m money cell with its 'excl. N' marker (the reasons on hover); the dash with every
    reason when nothing is known. With `check` (the flagged prices behind it) the figure is grey,
    never green or red, after a grey ≈: a price to check never reads as a real gain or loss. On a
    trade row (`row`) there is no marker: the figure's own hover says what it leaves out and the
    row's one "i" gathers it (`excl_line`, layout wave 2026-09-29); totals keep their marker."""
    if total is None:
        return html.Td(missing_cell(_lines(*list(dict.fromkeys(reasons))[:8]) or "no figure"), className=className or None)
    total = _cents(total)
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


# --------------------------------------------------------------------------- open / locked in
# The engine's split of P&L since entry (spreads-engine, 2026-09-30): `pnl_open` (the lots still held,
# at today's price against their average entry) and `pnl_locked` (the lots already unwound), USD or
# None with `pnl_split_reason`, on each trade row and each leg. Read with .get(): a trade_book without
# the keys shows the dash with "Split not available yet". Never recomputed here.
SPLIT_KEYS = {"open": "pnl_open", "locked": "pnl_locked"}
SPLIT_MISSING = "Split not available yet"
SPLIT_WORDS = {"open": "Open: P&L on the lots still held, at today's price against their average entry",
               "locked": "Locked in: P&L already made or lost on lots unwound, hedges matured or options expired"}
NON_USD_LOCKED = "In USD at today's rate, so it moves with the currency"


def non_usd(item: dict) -> bool:
    """A leg in another currency than USD, or a trade holding one (its Locked in moves with the rate)."""
    legs = item.get("legs") if "legs" in item else [item]
    return any(str(leg.get("currency") or "") not in ("", "USD") for leg in legs or [])


def split_value(item: dict, kind: str) -> Tuple[Optional[float], str]:
    """(figure, reason) of a trade's or a leg's Open / Locked in (`kind` 'open' | 'locked'), as the
    engine gave it; the reason when there is none."""
    key = SPLIT_KEYS[kind]
    if key not in item:
        return None, SPLIT_MISSING
    v = _num(item.get(key))
    if v is None:
        return None, str(item.get("pnl_split_reason") or "no split given")
    return v, ""


def has_split(item: dict) -> bool:
    return any(_num(item.get(k)) is not None for k in SPLIT_KEYS.values())


def trade_split(data: dict, t: dict, kind: str, ltd: Optional[Tuple[Optional[float], int, List[str]]] = None
                ) -> Tuple[Optional[float], str]:
    """A trade row's Open / Locked in: a closed trade's P&L is all locked in (its P&L since entry, the
    fills' own figures summed), Open blank; fills on no trade have no split; else the engine's."""
    if t.get("pseudo"):
        return None, "fills on no trade: no split"
    if t.get("status") == "closed":
        if kind == "open":
            return None, ""
        fig = ltd or fill_sum(data, "ltd", [str(i) for i in t.get("trade_ids") or []])
        return fig[0], _lines(*fig[2][:4]) if fig[0] is None else ""
    return split_value(t, kind)


def split_td(data: dict, t: dict, kind: str, ltd=None, check: Sequence[str] = ()) -> html.Td:
    """The Open / Locked in cell of a trade row: the full figure (a loss in brackets, red), the dash
    with the engine's reason when there is none; blank Open on a closed trade (nothing held)."""
    v, why = trade_split(data, t, kind, ltd)
    if v is None and not why:
        return html.Td(html.Span("", title="Closed: no lots held, all of its P&L is locked in"))
    if v is None:
        return html.Td(missing_cell(why))
    v = _cents(v)
    hover = SPLIT_WORDS[kind]
    if kind == "locked" and t.get("status") == "closed":
        hover = "Closed: all of its P&L is locked in (its P&L since entry)"
    elif kind == "locked" and t.get("unwound") is False:
        hover = _lines(hover, "Nothing unwound yet")
    if kind == "locked" and non_usd(t):
        hover = _lines(hover, NON_USD_LOCKED)
    return html.Td([check_mark(check), km_cell(v, hover=hover, colour=not check,
                                               className="cell-estimated" if check else "")])


def split_total_td(data: dict, shown: Sequence[dict], kind: str) -> html.Td:
    """The total row's Open / Locked in cell, its "excl. N" naming the trades with no figure."""
    total, n, reasons = split_total(data, shown, kind)
    if total is None:
        return html.Td(missing_cell(_lines(*list(dict.fromkeys(reasons))[:8]) or SPLIT_MISSING))
    hover = _lines(f"Excludes {_plural(n, 'trade')} with no figure", *list(dict.fromkeys(reasons))[:8]) if n else ""
    return html.Td([km_cell(_cents(total), hover=SPLIT_WORDS[kind]),
                    marker(f"excl. {n}", hover, "marker--small") if n else None])


def split_total(data: dict, shown: Sequence[dict], kind: str) -> Tuple[Optional[float], int, List[str]]:
    """The total row's Open / Locked in: the rows' own figures summed (display), with the rows that
    have none counted and named (closed trades carry no Open and are not counted)."""
    vals = []
    for t in shown:
        v, why = trade_split(data, t, kind)
        if v is None and not why:
            continue
        vals.append((v, f"{t.get('trade')}: {why}"))
    return sum_known(vals)


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
    """'Copper', 'WTI', 'Brent', 'Heating Oil', 'NBP Gas': the root's short name without its exchange
    word, in Title Case (user, 2026-10-01: "can the spreads be capitalised"; `title_name`)."""
    words = short_root_name(root, root_id).split()
    if exchange and len(words) > 1 and words[0].upper() == exchange.upper():
        words = words[1:]
    return title_name(" ".join(words))


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


# What it is in two lines (user, 2026-09-30, on Jason's real book: the one-sentence form was "unclear").
# Line 1, the position: which side is long and which short, by commodity and exchange, the months only
# where they are the point ("Long COMEX copper / short LME copper", "Long CME HRC Oct–Nov26 / short
# Dec26", "Short SHFE silver Dec26"). Line 2, grey: the sizes ("34 v 14 lots"), the months of a cross
# ("Nov/Dec26"), a side that is flat ("COMEX leg closed"), what else is held ("+ 1 more spread"), then
# the hedge ("CNH 92 % hedged", amber when it runs the wrong way or is far too big). Display only: the
# engine's legs and lots, added per side (long against short); the hedge figures are the engine's.
_NAMING: Dict[int, tuple] = {}


def _naming(roots: Dict[str, Any]) -> Tuple[Dict[str, set], Dict[str, set]]:
    """({commodity words: exchanges}, {subsector: exchanges}) over the contract list, once per list."""
    hit = _NAMING.get(id(roots))
    if hit is not None and hit[0] is roots:
        return hit[1]
    words: Dict[str, set] = {}
    subs: Dict[str, set] = {}
    for rid, root in roots.items():
        ex = str(getattr(root, "exchange", "") or "")
        words.setdefault(_commodity_words(root, rid, ex), set()).add(ex)
        subs.setdefault(str(getattr(root, "subsector", "") or ""), set()).add(ex)
    _NAMING.clear()
    _NAMING[id(roots)] = (roots, (words, subs))
    return words, subs


def _needs_exchange(item: dict, roots: Dict[str, Any], held: Dict[str, set]) -> bool:
    """The exchange is named only where it tells things apart: the trade holds the commodity on two
    exchanges, or the commodity trades on more than one (SGX iron ore, COMEX copper, CME HRC), unless
    its name is already a benchmark of its own (WTI, Brent, feeder cattle)."""
    name, ex = item["commodity"], item["exchange"]
    if not ex:
        return False
    if len(held.get(name, ())) > 1:
        return True
    words, subs = _naming(roots)
    if len(words.get(name, ())) > 1:
        return True
    sub = str(getattr(roots.get(item["root"]), "subsector", "") or "")
    return len(subs.get(sub, ())) > 1 and name.lower() == sub.replace("_", " ").lower()


def _held_items(legs: Sequence[Tuple[dict, float]], roots: Dict[str, Any]) -> List[dict]:
    """One item per (side, contract root, option type) for lots, one per contract for an FX or metal
    forward or FX option; each with its lots or notional added (display), months, strikes, prompts."""
    items: Dict[tuple, dict] = {}
    for leg, q in legs:
        product, rid = str(leg.get("product") or ""), str(leg.get("root_id") or "")
        sign = 1 if q > 0 else -1
        if product in LOT_PRODUCTS:
            p = parse_contract_id(str(leg.get("contract_id") or "")) or {}
            opt = str(p.get("option_type") or "") if product not in ("FUTURE", "LME_FWD") else ""
            exchange = str(leg.get("exchange") or "")
            item = items.setdefault(("lots", sign, rid, opt), {
                "kind": "lots", "sign": sign, "q": 0.0, "root": rid, "exchange": exchange, "opt": opt,
                "commodity": _commodity_words(roots.get(rid), rid, exchange), "months": [], "strikes": [],
                "prompts": [], "unit": "lots"})
            item["q"] += abs(q)
            month = _leg_month(leg)
            if month:
                item["months"].append(month)
            if p.get("strike"):
                item["strikes"].append(str(p.get("strike")))
            if product == "LME_FWD" and leg.get("prompt"):
                item["prompts"].append(str(leg.get("prompt")))
        else:
            unit, _money = _base_unit(leg)
            label = _fx_label(leg)
            item = items.setdefault(("fx", sign, str(leg.get("contract_id") or label)), {
                "kind": "fx", "sign": sign, "q": 0.0, "unit": unit, "pair": label.split(" ")[0], "label": label,
                "root": "", "commodity": label, "months": []})
            item["q"] += abs(q)
    return list(items.values())


def _span_words(months: Sequence[Tuple[int, int]], strip: bool = True) -> str:
    """'Dec26'; a side's run of months 'Oct–Nov26', 'Dec26–Jan27' (`strip`); two months apart or on
    two sides 'Nov/Dec26'; three or more 'Oct–Dec26'."""
    ms = sorted(set(months))
    if not ms:
        return ""
    if len(ms) == 1:
        return month_label(ms[0][1], ms[0][0])
    first, last = month_label(ms[0][1], ms[0][0]), month_label(ms[-1][1], ms[-1][0])
    if len(ms) == 2:
        run = (ms[1][0] * 12 + ms[1][1]) - (ms[0][0] * 12 + ms[0][1]) == 1
        sep = EN_DASH if (strip and run) else "/"
    else:
        sep = EN_DASH
    return f"{first[:3]}{sep}{last}" if ms[0][0] == ms[-1][0] else f"{first}{sep}{last}"


def _when_words(item: dict) -> str:
    """The item's months ('Dec26', 'Feb–Mar27'), an LME ticket's one prompt ('10 Dec26')."""
    prompts = sorted(set(item.get("prompts") or []))
    if len(prompts) == 1 and len(set(item["months"])) <= 1:
        return f"{short_date(prompts[0])}{prompts[0][2:4]}"
    return _span_words(item["months"])


def _names_words(names: List[str]) -> str:
    """'RBOB and heating oil'; names sharing their last word said once ('live and feeder cattle')."""
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) == 2:
        a, b = names[0].split(" "), names[1].split(" ")
        if len(a) > 1 and len(b) > 1 and a[-1] == b[-1]:
            return f"{' '.join(a[:-1])} and {names[1]}"
    return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] if names else ""


def _opt_words(item: dict, strikes_ok: bool) -> str:
    if not item.get("opt"):
        return ""
    strikes = list(dict.fromkeys(item.get("strikes") or []))
    strike = strike_text(strikes[0]) if strikes_ok and len(strikes) == 1 else ""
    return " ".join(x for x in (strike, item["opt"] + ("s" if item["q"] != 1 else "")) if x)


def _position_lines(t: dict, legs: Sequence[Tuple[dict, float]], roots: Dict[str, Any]) -> Tuple[str, List[str]]:
    """(line 1, line 2's parts) of these legs: the long side first ("Long COMEX copper / short LME
    copper"), then the sizes ("34 v 14 lots") and, for a cross, the months ("Nov/Dec26")."""
    items = _held_items(legs, roots)
    if not items:
        return "", []
    lots = [i for i in items if i["kind"] == "lots"]
    held: Dict[str, set] = {}
    for i in lots:
        held.setdefault(i["commodity"], set()).add(i["exchange"])
    sides = [s for s in ([i for i in items if i["sign"] > 0], [i for i in items if i["sign"] < 0]) if s]
    for s in sides:
        s.sort(key=lambda i: (-i["q"], str(i.get("commodity") or i.get("label"))))
    # the exchange said where it tells things apart, and once while it stays the same ("Long CBOT soybean
    # meal and soybean oil / short soybeans", "Long LME copper / short aluminium"); an FX pair once a side
    said = {"ex": "", "pair": ""}
    for side in sides:
        said["pair"] = ""
        for i in side:
            if i["kind"] == "fx":
                i["name"] = i["label"] if i["pair"] != said["pair"] else i["label"][len(i["pair"]):].strip()
                said["pair"] = i["pair"]
            elif _needs_exchange(i, roots, held) and i["exchange"] != said["ex"]:
                i["name"] = f"{i['exchange']} {i['commodity']}"
                said["ex"] = i["exchange"]
            else:
                i["name"] = i["commodity"]
    one_root = len({i["root"] for i in lots}) == 1 and len(lots) == len(items)
    inline = one_root or len(sides) == 1          # the months are the point: a calendar, an outright
    opt_types = [i["opt"] for i in lots if i["opt"]]
    strikes_ok = len(opt_types) == 1 or len(set(opt_types)) < len(opt_types)
    parts: List[str] = []
    first_when = ""
    for n, side in enumerate(sides):
        word = ("long" if side[0]["sign"] > 0 else "short")
        if one_root and n:
            # the other side of a calendar or a structure on one contract: the months (when they
            # differ) and the option type, never the name again ("Long CME HRC Oct–Nov26 / short Dec26")
            when = _span_words([m for i in side for m in i["months"]])
            bits = [when if when != first_when else "", *(_opt_words(i, strikes_ok) for i in side)]
            parts.append(" ".join([word] + [b for b in bits if b]))
            continue
        if all(i["kind"] == "lots" for i in side):
            if inline:
                phrases = [" ".join(x for x in (i["name"], _when_words(i), _opt_words(i, strikes_ok)) if x)
                           for i in side]
                if one_root:
                    first_when = _span_words([m for i in side for m in i["months"]])
                    phrases = [" ".join(x for x in (side[0]["name"], first_when,
                                                    *(_opt_words(i, strikes_ok) for i in side)) if x)]
                text = ", ".join(dict.fromkeys(phrases))
            else:
                text = _names_words([" ".join(x for x in (i["name"], _opt_words(i, strikes_ok)) if x) for i in side])
        else:
            text = ", ".join(i["name"] for i in side)
        parts.append(f"{word} {text}")
    line1 = cap(" / ".join(parts))
    line2: List[str] = []
    # each side's size: lots added per side (display), an FX notional per contract ("5,000,000 +
    # 2,000,000 USD": two options' notionals are not one figure)
    per_side: List[Dict[str, List[float]]] = []
    for side in sides:
        by_unit: Dict[str, List[float]] = {}
        for i in side:
            got = by_unit.setdefault(i["unit"], [])
            if i["kind"] == "lots" and got:
                got[0] += i["q"]
            else:
                got.append(i["q"])
        per_side.append(by_unit)
    units = [u for s in per_side for u in s]

    def figs(qs: List[float]) -> str:
        return " + ".join(_count_words(q) for q in qs)
    if len(set(units)) == 1:
        unit = units[0]
        text2 = " v ".join(figs(qs) for s in per_side for qs in s.values())
        if unit == "lots" and text2 == "1":
            unit = "lot"
        line2.append(f"{text2} {unit}")
    else:
        line2.append(" v ".join(" + ".join(f"{figs(qs)} {u}" for u, qs in s.items()) for s in per_side))
    if not inline:
        months = _span_words([m for i in lots for m in i["months"]], strip=False)
        if months:
            line2.append(months)
    return line1, line2


def _closed_side_words(t: dict, open_legs: Sequence[dict]) -> str:
    """'COMEX leg closed' when a spread's other side is flat (SILARB1: 2 SHFE lots left, COMEX flat)."""
    if tf.type_code(t) not in ("CROSS_EXCHANGE", "CROSS_PRODUCT", "MIXED"):
        return ""
    open_roots = {str(leg.get("root_id") or "") for leg in open_legs}
    open_ex = {str(leg.get("exchange") or "") for leg in open_legs}
    gone: List[str] = []
    for leg in t.get("legs") or []:
        rid = str(leg.get("root_id") or "")
        if leg.get("hedge") or leg.get("unrecognised") or leg.get("status") == "open" or not rid or rid in open_roots:
            continue
        ex = str(leg.get("exchange") or "")
        word = ex if ex and ex not in open_ex else str(leg.get("commodity") or "") or ex
        if word and word not in gone:
            gone.append(word)
    if not gone:
        return ""
    return f"{' and '.join(gone)} {'leg' if len(gone) == 1 else 'legs'} closed"


def _is_unmatched(sub: dict) -> bool:
    return str(sub.get("type") or "") == "UNMATCHED" or bool(sub.get("unmatched"))


def _spread_count_words(subs: Sequence[dict]) -> str:
    """'2 calendars', '2 month pairs', '2 spreads': several spreads on the same contracts, said once."""
    kinds = {str(x.get("type") or "") for x in subs}
    word = "calendars" if kinds == {"CALENDAR"} else "month pairs" if kinds <= {"CROSS_EXCHANGE", "CROSS_PRODUCT"} \
        else "spreads"
    return f"{len(subs)} {word}"


# line 2's parts that are counts, said after a " · " on the one-line What it is (never a cross's months)
_COUNT_PART = re.compile(r"^(\+ .*|\d+ (calendars|month pairs|spreads)|Unmatched legs)$")


def what_parts(t: dict, roots: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str], List[str]]:
    """(line 1, line 2's parts, the other parts in words for the hover) of What it is. The trade's
    open legs (hedges apart). A trade of several spreads (spreads-engine's `sub_spreads`, a part of
    three legs or more cut into its equal-size spreads since 2026-10-01) says the spreads on its
    largest set of contracts, "2 calendars" when there are several there (STEEL's two HRC calendars,
    ZNA1's month pairs), and "+ N more spread(s)" for the spreads on other contracts only (SCO1: the
    iron ore calendars, + 1 more spread, the HRC calendar); legs no rule pairs (an UNMATCHED part) are
    "+ unmatched legs"; an open leg the contract list does not know is counted ("+ 1 not recognised").
    A closed trade, a row of fills on no trade or one with nothing recognised keeps the engine's
    words (`what_text`) and no second line."""
    roots = roots if roots is not None else _roots()
    by_id = {str(leg.get("contract_id")): leg for leg in t.get("legs") or []}
    open_legs = _open_legs(t)
    legs = [(leg, _num(leg.get("lots")) or 0.0) for leg in open_legs]
    unknown = [leg for leg in t.get("legs") or [] if leg.get("unrecognised") and leg.get("status") != "closed"]
    subs = [x for x in t.get("sub_spreads") or [] if x.get("legs")]
    real = [x for x in subs if not _is_unmatched(x)]
    unmatched = [x for x in subs if _is_unmatched(x)]
    others: List[str] = []
    counts: List[str] = []
    n_more, word_more = 0, "spread"

    def roots_of(sub):
        return tuple(sorted(str(r) for r in sub.get("root_ids") or []))

    def sub_legs(sub):
        return [(by_id[str(x.get("contract_id"))], _num(x.get("lots")) or 0.0) for x in sub.get("legs") or []
                if str(x.get("contract_id")) in by_id and (_num(x.get("lots")) or 0.0)
                and by_id[str(x.get("contract_id"))].get("status") == "open"
                and not by_id[str(x.get("contract_id"))].get("hedge")]

    groups: List[Tuple[bool, List[dict]]] = []           # (unmatched, its spreads)
    by_roots: Dict[tuple, List[dict]] = {}
    for x in real:
        by_roots.setdefault(roots_of(x), []).append(x)
    groups += [(False, g) for g in by_roots.values()] + [(True, [x]) for x in unmatched]
    head_unmatched = False
    if not t.get("pseudo") and len(groups) > 1:
        # the headline is the largest set of contracts, legs no rule pairs included (an UNMATCHED part
        # bigger than the spreads beside it leads, said "unmatched legs"); the rest are counted
        ranked = sorted(groups, key=lambda g: -sum(abs(q) for x in g[1] for _l, q in sub_legs(x)))
        head_unmatched, head = ranked[0]
        legs = [lq for x in head for lq in sub_legs(x)]
        if head_unmatched:
            counts.append("Unmatched legs")
        elif len(head) > 1:
            counts.append(_spread_count_words(head))
        in_subs = {str(x.get("contract_id")) for sub in subs for x in sub.get("legs") or []}
        rest_subs = [x for un, g in ranked[1:] if not un for x in g]
        rest = [sub_legs(x) for x in rest_subs]
        loose = [[(leg, _num(leg.get("lots")) or 0.0)] for leg in open_legs if str(leg.get("contract_id")) not in in_subs]
        for part in rest + loose:
            a, b = _position_lines(t, part, roots)
            if a:
                others.append(f"{a} ({' · '.join(b)})" if b else a)
        n_more = len(rest_subs) + len(loose)
        if any(x.get("type") == "OUTRIGHT" for x in rest_subs) or loose:
            word_more = "part"
        others += [part_name(x) for un, g in ranked[1:] if un for x in g]
        more_unmatched = any(un for un, _g in ranked[1:])
    else:
        more_unmatched = False
        if len(real) > 1 and not t.get("pseudo"):
            counts.append(_spread_count_words(real))      # several spreads on the same contracts
        elif unmatched and not real and not t.get("pseudo"):
            counts.append("Unmatched legs")
            others += [part_name(x) for x in unmatched]
    line1, line2 = ("", []) if t.get("pseudo") or t.get("status") == "closed" else _position_lines(t, legs, roots)
    if not line1:
        return what_text(t), [], []
    # line 2 keeps only the sizes, the months, the counts and the hedge coverage (user, 2026-09-30);
    # a flat side ("COMEX leg closed") is said on the cell's hover (`_with_words`: what_closed)
    line2 += counts
    if n_more:
        line2.append(f"+ {n_more} more {word_more}{'s' if n_more > 1 else ''}")
    if more_unmatched:
        line2.append("+ unmatched legs")
    if unknown:
        line2.append(f"+ {len(unknown)} not recognised")
        others += [f"Not recognised: {leg.get('name')} ({signed_count(_num(leg.get('lots')) or 0.0)} in the file)"
                   for leg in unknown]
    return line1, line2, others


def _with_words(t: dict, roots: Dict[str, Any]) -> dict:
    line1, line2, others = what_parts(t, roots)
    sub = " · ".join(line2)
    closed = "" if t.get("pseudo") or t.get("status") == "closed" else _closed_side_words(t, _open_legs(t))
    # the Book's one-line row (user, 2026-09-30): What it is = line 1, a cross's months after a comma and
    # "+ N more spread(s)"; Quantity = the sizes (line 2's first part); "+ N not recognised" is a Check chip
    qty = line2[0] if line2 else ""
    rest = line2[1:]
    months = [x for x in rest if not _COUNT_PART.match(x)]
    counts = [x for x in rest if _COUNT_PART.match(x) and "not recognised" not in x]
    one = line1 + (", " + " ".join(months) if months else "") + "".join(f" · {c}" for c in counts)
    return dict(t, what_words=" · ".join(x for x in (line1, sub, closed) if x), what_title=line1, what_sub=sub,
                what_others=others, what_closed=cap(closed), what_one=one, what_qty=qty,
                what_spreads=spreads_summary(t, roots) if is_multi(t) else "")


# A trade of several spreads (user, 2026-10-01): its row says how many and on what ("3 calendar spreads:
# Iron Ore and HRC", "2 Zinc spreads, SHFE vs LME"), each spread on its own row under it with its size,
# levels and z. Display only: the engine's sub_spreads, counted and named.
SPREAD_KIND_WORDS = {"CALENDAR": "calendar ", "CROSS_PRODUCT": "cross-product ", "CROSS_EXCHANGE": ""}


def spread_subs(t: dict) -> List[dict]:
    """The trade's spreads (spreads-engine's `sub_spreads` that hold legs)."""
    return [x for x in t.get("sub_spreads") or [] if x.get("legs")]


def is_multi(t: dict) -> bool:
    """An open trade of several spreads: its row carries no size, level or z (each spread's row does)."""
    return t.get("status") == "open" and not t.get("pseudo") and len(spread_subs(t)) > 1


def spreads_summary(t: dict, roots: Optional[Dict[str, Any]] = None) -> str:
    """'3 calendar spreads: Iron Ore and HRC', '2 Zinc spreads, SHFE vs LME', '2 HRC calendar spreads';
    '+ unmatched legs' after it when some legs pair with nothing. '' when no spread is matched."""
    roots = roots if roots is not None else _roots()
    subs = spread_subs(t)
    real = [x for x in subs if not _is_unmatched(x)]
    if not real:
        return ""
    weight: Dict[str, float] = {}
    exchanges: Dict[str, List[str]] = {}
    for x in real:
        size = sum(abs(_num(leg.get("lots")) or 0.0) for leg in x.get("legs") or [])
        for rid in x.get("root_ids") or []:
            root = roots.get(str(rid))
            ex = str(getattr(root, "exchange", "") or "")
            name = _commodity_words(root, str(rid), ex)
            weight[name] = weight.get(name, 0.0) + size
            if ex and ex not in exchanges.setdefault(name, []):
                exchanges[name].append(ex)
    names = sorted(weight, key=lambda n: -weight[n])
    kinds = {str(x.get("type") or "") for x in real}
    kind = SPREAD_KIND_WORDS.get(next(iter(kinds)), "") if len(kinds) == 1 else ""
    noun = "parts" if "OUTRIGHT" in kinds else "spreads"
    noun = noun if len(real) > 1 else noun[:-1]
    if len(names) == 1:
        text = f"{len(real)} {names[0]} {kind}{noun}"
        exs = exchanges.get(names[0]) or []
        if kinds == {"CROSS_EXCHANGE"} and len(exs) > 1:
            first = str(real[0].get("what_it_is") or "")
            exs = sorted(exs, key=lambda e: (first.find(e) < 0, first.find(e)))
            text += ", " + " vs ".join(exs)
    else:
        text = f"{len(real)} {kind}{noun}: {_names_words(names)}"
    if len(real) < len(subs):
        text += " + unmatched legs"
    return text


def coverage_words(hedge: dict) -> str:
    """'CNH 92% hedged' while the coverage is a sensible share (0 to 150 %); '' otherwise (the
    percent sign tight on the figure on this grey line, user 2026-09-30)."""
    cov = _num((hedge or {}).get("coverage"))
    if cov is None or cov < 0 or cov > 1.5 or hedge.get("direction_note") or hedge.get("hedge_oversized"):
        return ""
    return f"{hedge.get('currency') or 'CNH'} {pct_text(cov).replace(' %', '%')} hedged"


def _times_words(x: float) -> str:
    return f"{x:.0f}×" if x >= 3 else f"{x:.1f}×"


def hedge_parts(t: dict) -> Tuple[str, bool, str]:
    """(the hedge words, amber, hover) of What it is's second line, from the engine's hedge block:
    'CNH 92 % hedged'; amber 'CNH hedge runs the wrong way' (it runs with the exposure), 'CNH hedge
    6× the exposure' (oversized) or 'CNH hedge left, no CNY leg open', the engine's sentences on
    hover; else the engine's own words ('JPY hedged', 'GBP/USD hedged'). Never a negative or
    absurd percentage: the coverage is the engine's figure, only its size is said (display)."""
    hedge = t.get("hedge") or {}
    ccy = str(hedge.get("currency") or "CNH")
    cov = _num(hedge.get("coverage"))
    notes = _lines(cap(str(hedge.get("direction_note") or "")), cap(str(hedge.get("hedge_oversized") or "")))
    if hedge.get("present") and hedge.get("exposure_basis") == "no CNY leg open":
        return f"{ccy} hedge left, no CNY leg open", True, _lines(notes, cap(str(hedge.get("reason") or "")))
    if cov is not None and (hedge.get("direction_note") or cov < 0):
        size = f", {_times_words(abs(cov))} the exposure" if abs(cov) > 1.5 else ""
        return f"{ccy} hedge runs the wrong way{size}", True, notes
    if cov is not None and (hedge.get("hedge_oversized") or cov > 1.5):
        return f"{ccy} hedge {_times_words(cov)} the exposure", True, notes
    words = coverage_words(hedge)
    if words:
        exp, hed = _num(hedge.get("exposure_usd")), _num(hedge.get("hedge_usd"))
        return words, False, _lines(f"China legs {format_cell(exp)} USD · hedge {format_cell(hed)} USD"
                                    if exp is not None and hed is not None else "")
    m = _HEDGE_WORDS.search(str(t.get("what_it_is") or ""))
    return (m.group(1), False, "") if m else ("", False, "")


def hedge_words(t: dict) -> str:
    return hedge_parts(t)[0]


def hedge_problem(t: dict) -> Optional[Tuple[str, str]]:
    """(the Check chip's short name, its sentence) when the trade's currency hedge needs doing
    (user, 2026-09-30: the warning leaves What it is for the Check column): 'Hedge wrong way',
    'Hedge too big', 'Hedge left alone'; None when the hedge is fine or there is none."""
    words, amber, notes = hedge_parts(t)
    if amber:
        name = ("Hedge left alone" if "left" in words else "Hedge wrong way" if "wrong way" in words
                else "Hedge too big")
        return name, _lines(cap(words), notes)
    for f in t.get("flags") or []:
        if str(f.get("code") or "") == "hedge_oversized":
            return "Hedge too big", flag_sentence(f)
    return None


def what_line(t: dict) -> str:
    """Both lines and the hedge words as one line (the CSV, the sort)."""
    text = str(t.get("what_words") or "")
    if not text:
        a, b, _o = what_parts(t)
        text = " · ".join([a, *b]) if b else a
    tail = hedge_words(t)
    return f"{text} · {tail}" if tail and tail not in text else text


def _what_td(t: dict, r: Optional[dict]) -> html.Td:
    """What it is: line 1 the position in words, line 2 grey and short (lowercase allowed, `tk-what-sub`)
    the sizes, months, what else is held and the hedge coverage (a hedge that needs doing is a Check
    chip instead); on hover both lines, a flat side, the other parts, then each leg's lots, the value
    per side, the balance, the USD per 1-unit move of the level, hedge % and correlation, and the
    legs not listed there (hedges, closed)."""
    if "what_title" in t:
        what, sub, others = str(t.get("what_title") or ""), str(t.get("what_sub") or ""), list(t.get("what_others") or [])
    else:
        what, parts, others = what_parts(t)
        sub = " · ".join(parts)
    tail, amber, tail_hover = hedge_parts(t)
    if tail and tail.split(" ")[-1] == "hedged":
        what = _HEDGED_TAIL.sub("", what)     # the second line says it
    level = t.get("level") or {}
    structure = (f"Level: {level.get('note')}" if level.get("note")
                 and (level.get("mode") == "premium" or level.get("source") == "template") else "")
    listed = {str(leg.get("name")) for leg in t.get("legs") or [] if leg.get("status") == "open" and not leg.get("hedge")}
    rest = [str(leg.get("name") or "") for leg in t.get("legs") or [] if str(leg.get("name") or "") not in listed]
    closed = str(t.get("what_closed") or "")
    if "what_title" not in t and not t.get("pseudo") and t.get("status") != "closed":
        closed = cap(_closed_side_words(t, _open_legs(t)))
    hover = _lines(cap(what), " · ".join(x for x in (sub, tail) if x), closed, tail_hover, structure,
                   _lines("Also held:", *others) if others else "",
                   size_hover(t, r), *(f"· {n}" for n in rest if n))
    second: List[Any] = []
    if sub:
        second.append(sub)
    if tail and not amber:
        if second:
            second.append(" · ")
        second.append(html.Span(tail, className="tk-cov", title=plain_words(tail_hover) or None))
    return html.Td([html.Div(cap(what), className="tk-clip tk-what-main", title=plain_words(hover) or None),
                    html.Div(second, className="tk-sub tk-what-sub tk-clip") if second else None],
                   className="l tk-what")


def _what_one_td(t: dict) -> html.Td:
    """The Book's What it is on one line (user, 2026-09-30): the position, a cross's months, "+ N more
    spread(s)"; clipped to the column, the whole of it, a flat side, the other parts, the value per
    side, the balance and the legs not listed on hover. The hedge is in the opened trade."""
    if "what_one" in t:
        one = str(t.get("what_one") or "")
    else:
        line1, _l2, _o = what_parts(t)
        one = line1
    one = _HEDGED_TAIL.sub("", one) or one
    others = list(t.get("what_others") or [])
    level = t.get("level") or {}
    structure = (f"Level: {level.get('note')}" if level.get("note")
                 and (level.get("mode") == "premium" or level.get("source") == "template") else "")
    listed = {str(leg.get("name")) for leg in t.get("legs") or [] if leg.get("status") == "open" and not leg.get("hedge")}
    rest = [str(leg.get("name") or "") for leg in t.get("legs") or [] if str(leg.get("name") or "") not in listed]
    many = str(t.get("what_spreads") or "") if "what_spreads" in t else (spreads_summary(t) if is_multi(t) else "")
    if many:
        # a trade of several spreads: how many and on what; each spread and its size on hover
        spreads = [f"{part_name(x)}: {sides_text(x)[0]}" if sides_text(x)[0] else part_name(x) for x in spread_subs(t)]
        hover = _lines(cap(many), cap(one), "Spreads:", *spreads, str(t.get("what_closed") or ""),
                       *(f"· {n}" for n in rest if n))
        return html.Td(html.Span(cap(many), className="tk-clip tk-what-main", title=plain_words(hover) or None),
                       className="l tk-what tk-what--one")
    hover = _lines(cap(one), str(t.get("what_closed") or ""), structure,
                   _lines("Also held:", *others) if others else "", *(f"· {n}" for n in rest if n))
    return html.Td(html.Span(cap(one), className="tk-clip tk-what-main", title=plain_words(hover) or None),
                   className="l tk-what tk-what--one")


# Quantity (user, 2026-10-01): the engine's `size_sides` of the trade and of each spread, the size of each
# side in the unit the spread is matched in ('2,521 v 2,521 lots', '3,135 t v 3,135 t', '$15,094,650 v
# $14,928,200', 'Short 2 lots'), money in full; the legs of each side and the basis in words on hover.
SIZE_BASIS_WORDS = {"lots": "Sized in lots", "value": "Sized by dollar value at the fill"}
PHYSICAL_UNIT_WORDS = {"t": "tonnes", "bbl": "barrels", "oz": "ounces", "lb": "pounds", "gal": "gallons",
                       "bu": "bushels", "st": "short tons", "mmbtu": "MMBtu", "mwh": "MWh"}


def basis_words(sides: dict) -> str:
    """'Sized in lots', 'Sized by tonnes', 'Sized by dollar value at the fill'."""
    basis = str(sides.get("basis") or "")
    if basis == "physical":
        unit = str(sides.get("unit") or "")
        return f"Sized by {PHYSICAL_UNIT_WORDS.get(unit.lower(), unit) or 'physical quantity'}"
    return SIZE_BASIS_WORDS.get(basis, "")


def sides_hover(sides: dict) -> str:
    """The legs of each side and the basis: 'Long: CME Feeder Cattle', 'Short: CME Live Cattle', 'Sized
    by dollar value at the fill'."""
    return _lines(f"Long: {title_name(str(sides.get('long_label')))}" if sides.get("long_label") else "",
                  f"Short: {title_name(str(sides.get('short_label')))}" if sides.get("short_label") else "",
                  basis_words(sides))


def sides_text(item: dict) -> Tuple[str, str]:
    """(the Quantity text of a trade or a spread, why when blank): `size_sides.text` as the engine
    wrote it."""
    sides = item.get("size_sides") or {}
    return str(sides.get("text") or ""), str(sides.get("reason") or "")


# Size (user, 2026-10-01): from the engine's `size_sides` (the size of each side in the spread's own unit),
# said once when the sides match: "200 lots a side"; "≈ 8,175 t a side" (their average) when they are within
# 10 % of each other, the exact sides on hover; "Long 386 t / short 350 t" in amber beyond that, the Action
# column then saying "Sides off by 10 %". One side only: the engine's own words ("Short 2 lots").
SIDES_TOLERANCE = 0.10


def _side_amount(x: float, sides: dict) -> str:
    """'200 lots', '1 lot', '8,175 t', '$34,657,881': one side's size in the spread's own unit, money in full."""
    basis, unit = str(sides.get("basis") or ""), str(sides.get("unit") or "")
    if basis == "value":
        return f"${x:,.0f}"
    if basis == "lots" or unit == "lots":
        body = f"{x:,.2f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-6 else f"{x:,.0f}"
        return f"{body} {'lot' if body == '1' else 'lots'}"
    unit = {"mwh": "MWh", "mmbtu": "MMBtu"}.get(unit.lower(), unit)
    return f"{x:,.0f} {unit}".strip()


def sides_gap(sides: Optional[dict]) -> Optional[float]:
    """How far apart the two sides are, as a share of the smaller one (0.10 = 10 %); None with one side
    or no figures. Display only, on the engine's sizes."""
    lo, sh = _num((sides or {}).get("long")), _num((sides or {}).get("short"))
    if lo is None or sh is None or lo <= 1e-9 or sh <= 1e-9:
        return None
    return abs(lo - sh) / min(lo, sh)


def size_display(sides: Optional[dict]) -> Tuple[str, bool, str]:
    """(the Size text, amber, the exact sides for the hover) of a trade or a spread from its `size_sides`."""
    sides = sides or {}
    text = str(sides.get("text") or "")
    gap = sides_gap(sides)
    if gap is None:
        lo, sh = _num(sides.get("long")), _num(sides.get("short"))
        if text and (lo or sh) and not (lo and sh):
            # one side only: "Short 1 lot", "Long 100 t" (the engine's text in the same words, the unit singular at 1)
            return ("Long " if lo else "Short ") + _side_amount(float(lo or sh), sides), False, ""
        return text, False, ""
    lo, sh = float(sides["long"]), float(sides["short"])
    exact = _lines(f"Long {_side_amount(lo, sides)}" + (f": {title_name(str(sides.get('long_label')))}"
                                                         if sides.get("long_label") else ""),
                   f"Short {_side_amount(sh, sides)}" + (f": {title_name(str(sides.get('short_label')))}"
                                                           if sides.get("short_label") else ""))
    if _side_amount(lo, sides) == _side_amount(sh, sides):
        return f"{_side_amount(lo, sides)} a side", False, exact
    if gap <= SIDES_TOLERANCE + 1e-12:
        return f"≈ {_side_amount((lo + sh) / 2.0, sides)} a side", False, _lines(
            exact, f"The sides are {pct_text(gap)} apart: the average shown")
    return (f"Long {_side_amount(lo, sides)} / short {_side_amount(sh, sides)}", True,
            _lines(exact, f"The sides are {pct_text(gap)} apart, more than {pct_text(SIDES_TOLERANCE)}"))


def size_td(item: dict, hover_more: str = "") -> html.Td:
    """A Size cell from `size_sides` (`size_display`), the basis and `hover_more` on hover; an em dash
    with the engine's reason when there is no size."""
    sides = item.get("size_sides")
    text, amber, exact = size_display(sides)
    if not text:
        legs = _open_legs(item) if "legs" in item else []
        if len(legs) == 1:
            # one open leg the engine gives no sides for (an FX forward, an option): that leg's own size
            words = leg_qty_words(legs[0])
            if words:
                return html.Td(html.Span(words, className="tk-clip", title=plain_words(hover_more) or None),
                               className="l tk-qty")
        why = str((sides or {}).get("reason") or "") or "no size read"
        if legs and why == "nothing open":
            why = "No size per side for this kind of trade: each leg's size is on its row under the caret"
        return html.Td(missing_cell(why), className="l tk-qty")
    hover = _lines(exact or sides_hover(sides or {}), basis_words(sides or {}) if exact else "", hover_more)
    return html.Td(html.Span(text, className="tk-clip" + (" cell-amber" if amber else ""),
                             title=plain_words(hover) or None), className="l tk-qty")


def _qty_td(t: dict, r: Optional[dict]) -> html.Td:
    """The trade row's Size (`size_td` on the trade's own `size_sides`); blank on a trade of several spreads
    (each spread's row carries its own), an em dash with its reason when closed or on no trade."""
    if is_multi(t):
        return html.Td("", className="l tk-qty")
    sides = t.get("size_sides")
    if sides is None or t.get("status") == "closed" or t.get("pseudo"):
        qty = "" if sides is None else str(sides.get("text") or "")
        why = ("closed: nothing held" if t.get("status") == "closed"
               else "fills on no trade: each fill's size is in the opened row" if t.get("pseudo")
               else "no open lots read")
        if qty and t.get("status") != "closed" and not t.get("pseudo"):
            return html.Td(html.Span(qty, className="tk-clip"), className="l tk-qty")
        return html.Td(missing_cell(why), className="l")
    return size_td(t, size_hover(t, r))


def sub_qty_td(sub: dict) -> html.Td:
    """A spread's Size on its own row: its own `size_sides`."""
    if not sub.get("size_sides"):
        return html.Td("", className="l tk-qty")
    return size_td(sub)


def sides_off(t: dict) -> Optional[Tuple[float, str]]:
    """(the largest gap between two sides beyond 10 %, which spread) of an open trade: the trade's own
    sides for a trade of one spread, each spread's for a trade of several; None when every one is within."""
    if t.get("status") != "open" or t.get("pseudo"):
        return None
    items = [(x, part_name(x)) for x in spread_subs(t)] if is_multi(t) else [(t, "")]
    worst: Optional[Tuple[float, str]] = None
    for item, name in items:
        gap = sides_gap(item.get("size_sides"))
        if gap is not None and gap > SIDES_TOLERANCE + 1e-12 and (worst is None or gap > worst[0]):
            worst = (gap, name)
    return worst


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
    """(the Quantity as one string, why when blank): the engine's `size_sides.text` ('2,521 v 2,521
    lots', '$15,094,650 v $14,928,200'), else '+30 / −4 lots', '+2 lots', '−100 oz'; the CSV's Size
    column."""
    if t.get("size_sides") is not None and t.get("status") != "closed":
        text, why = sides_text(t)
        if text or why:
            return text, why
    fig, unit, more, why = size_parts(t)
    if not fig:
        return "", why
    return " ".join(x for x in (fig, unit, f"+{more} more" if more else "") if x), ""


def size_hover(t: dict, r: Optional[dict]) -> str:
    size, level = t.get("size") or {}, t.get("level") or {}
    lines = []
    subs = [x for x in t.get("sub_spreads") or [] if x.get("legs")]
    if len(subs) > 1:
        for x in subs:
            text = sides_text(x)[0]
            if not text:
                a, b = sub_sides(t, x)
                text = f"{_pair_figure(a, b)} lots"
            lines.append(f"{part_name(x)}: {text}")
    for leg in t.get("legs") or []:
        if leg.get("status") == "open" and not leg.get("hedge"):
            fig, unit = leg_size(leg)
            lines.append(f"{leg.get('name')}: {fig} {unit}")
    for s in size.get("sides") or []:
        if not _num(s.get("lots")):
            continue                            # an outright's empty side: nothing to say
        fill, mark = _num(s.get("value_fill_usd")), _num(s.get("value_mark_usd"))
        metal = _num(s.get("physical"))
        lines.append(f"{title_name(str(s.get('label') or ''))}: {format_cell(abs(fill)) + ' USD at the fill' if fill is not None else 'value at the fill not known'}"
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
        if hp is not None:
            lines.append(f"Hedge %: {pct_text(hp / 100.0)}" + (f" · correlation {corr:.2f}" if corr is not None else ""))
        elif r.get("hedge_reason"):
            lines.append(f"Hedge %: not given ({r['hedge_reason']})")
    else:
        lines.append("Hedge %: the risk figures are still being computed")
    return _lines(*lines)


# Entry level and Level now on one line (user, 2026-09-30): the level with its unit as a grey suffix on
# both; each leg's own price at its tick on hover, the average fill on Entry level and the mark on Level
# now (hedges left out), and the z-score on Level now. The legs' figures as `trade_book` gives them.
def leg_price_lines(data: Optional[dict], t: dict, key: str) -> List[str]:
    """One hover line per non-hedge leg of the level: its name and its `key` ('avg_fill' or 'mark')
    at its tick; a closed trade's legs, or none, give []."""
    if data is None or t.get("pseudo"):
        return []
    closed = t.get("status") == "closed" or (t.get("level") or {}).get("closed")
    legs = [leg for leg in t.get("legs") or [] if not leg.get("hedge") and not leg.get("unrecognised")
            and (closed or leg.get("status") == "open")]
    lines = []
    for leg in legs:
        v = _num(leg.get(key))
        if v is None and closed:
            continue
        text = (price_text(v, _leg_unit(data, leg), _num(leg.get("avg_fill"))) if v is not None
                else f"{MISSING} ({leg.get(key + '_reason') or 'not given'})")
        lines.append(f"{leg.get('name') or leg.get('contract_id')}: {'avg fill' if key == 'avg_fill' else 'mark'} {text}")
    return lines


# z at entry, now and exit per spread (user, 2026-10-01): risk-metrics' `trade_risk(...)["spread_z"]`,
# {position_id: {trade, status, entry_date, exit_date, trade_level, spreads [one per sub_spread]}}, each
# a `_spread_z` {z_entry / percentile_entry / z_entry_reason, z_now ..., z_exit ..., entry / now / exit
# {date, close_date, level, z, percentile, window {start, end, closes, full_year}, reason, note}}. Shown as
# the engine gives it: nothing is recomputed here.
Z_KEYS = ("z_entry", "z_now")                 # the two z columns
Z_POINT_WORDS = {"entry": "Z at entry", "now": "Z today", "exit": "Z at exit"}


def _z_hover(zd: dict, point: str, z: float, level: Optional[dict] = None, legs: Sequence[dict] = ()) -> str:
    """The hover of a z cell: z and percentile, the level on the day read, the spread's first fill or
    exit, the window of closes, the engine's note. `level` / `legs`: the row's level and legs, so the
    level reads at its legs' precision (`level_text`)."""
    d = zd.get(point) or {}
    win = d.get("window") or {}
    pct = _num(zd.get(f"percentile_{point}"))
    day = d.get("close_date") or d.get("date") or (zd.get("entry_date") if point == "entry" else "")
    ratio = zd.get("level_kind") == "ratio"
    lv = dict(level or {}) if level and level.get("unit") else {
        "unit": "ratio" if ratio else str(zd.get("level_unit") or ""), "mode": "ratio" if ratio else ""}
    unit = "" if ratio else unit_words(lv.get("unit"))
    return _lines(f"{Z_POINT_WORDS[point]}: {z_text(z)}" + (f" · percentile {pct_text(pct / 100.0)}" if pct is not None else ""),
                  f"Level {level_text(d.get('level'), lv, legs=legs)}{' ' + unit if unit else ''} on {day_text(day)}"
                  if _num(d.get("level")) is not None and day else "",
                  f"First fill of the spread: {day_text(zd.get('entry_date'))}" if point == "entry" and zd.get("entry_date")
                  else "",
                  f"Exit: the day it went flat, {day_text(zd.get('exit_date'))}" if point == "exit" and zd.get("exit_date")
                  else "",
                  (f"Window: {win.get('closes')} daily closes from {day_text(win.get('start'))} to {day_text(win.get('end'))}"
                   " (the calendar year to that day, Bloomberg price history)") if win.get("closes") else "",
                  cap(str(d.get("note") or "")))


def z_cell(zd: Optional[dict], point: str, ready: bool, why: str = "", extra: str = "",
           level: Optional[dict] = None, legs: Sequence[dict] = ()) -> html.Td:
    """One z cell (`point` 'entry' | 'now' | 'exit'): the figure, bold at |z| >= 2, its percentile,
    day and window on hover; an em dash with its reason otherwise (`why` when there is no entry)."""
    if not ready:
        return html.Td(missing_cell("the risk figures are still being computed"), className="tk-z")
    if zd is None:
        return html.Td(missing_cell(why or "no z for this trade"), className="tk-z")
    z = _num(zd.get(f"z_{point}"))
    if z is None:
        reason = str(zd.get(f"z_{point}_reason") or zd.get("reason") or why or "no z")
        return html.Td(missing_cell(_lines(reason, extra)), className="tk-z")
    return html.Td(html.Span(z_text(z), className="tk-bold" if abs(z) >= 2 else None,
                             title=plain_words(_lines(_z_hover(zd, point, z, level, legs), extra))), className="tk-z")


def spreads_z_lines(zd: Optional[dict], point: str) -> str:
    """Each spread's z at `point` in a line, for the hover of a trade of several spreads."""
    lines = []
    for s in (zd or {}).get("spreads") or []:
        z = _num(s.get(f"z_{point}"))
        name = title_name(str(s.get("what_it_is") or "")) or f"Spread {int(s.get('spread') or 0) + 1}"
        lines.append(f"{name}: {z_text(z) if z is not None else MISSING}")
    return _lines(*lines)


def trade_z_cells(t: dict, zd: Optional[dict], ready: bool) -> Dict[str, html.Td]:
    """The trade row's z entry and z now cells: the trade's own level's (`trade_level`); on a closed
    trade z now is the z at exit. A trade whose level is one of several spreads says which on hover."""
    closed = t.get("status") == "closed"
    tl = (zd or {}).get("trade_level")
    note = str((t.get("level") or {}).get("note") or "")
    many = len([x for x in t.get("sub_spreads") or [] if x.get("legs")]) > 1
    extra_e = _lines(f"Read on {note}" if many and note else "", spreads_z_lines(zd, "entry") if many else "")
    extra_n = _lines(f"Read on {note}" if many and note else "",
                     spreads_z_lines(zd, "exit" if closed else "now") if many else "")
    why = "no z-score for this trade"
    level, legs = (None if closed else t.get("level")), t.get("legs") or ()
    return {"z_entry": z_cell(tl, "entry", ready, why, extra_e, level, legs),
            "z_now": z_cell(tl, "exit" if closed else "now", ready, why, extra_n, level, legs)}


# How a spread's level is built, in one sentence on the hover of its two level cells (user, 2026-10-01):
# "CME HRC Oct26 minus CME HRC Dec26, in $/st", "SHFE Zinc Nov26 in USD at spot divided by LME Zinc 18
# Nov26, a ratio", "The price of SHFE Silver Dec26, in CNY/kg". Read from the level's own spec (its legs
# and weights) as the engine gave it; nothing is computed.
def _level_leg_names(level: dict, legs: Sequence[dict] = ()) -> Dict[str, str]:
    """{instrument id: the leg's name} from the trade's legs, the level's own legs winning (an LME prompt)."""
    names: Dict[str, str] = {}
    for leg in legs or ():
        iid = str(leg.get("instrument_id") or "")
        if iid and iid not in names and leg.get("name"):
            names[iid] = str(leg.get("name"))
    for leg in level.get("legs") or []:
        iid = str(leg.get("instrument_id") or "")
        if iid and leg.get("name"):
            names[iid] = str(leg.get("name"))
    return names


def level_words(level: dict, legs: Sequence[dict] = ()) -> str:
    """The sentence saying how `level` is built ('' when the spec does not say)."""
    if not level or level.get("closed"):
        return ""
    mode = str(level.get("mode") or "")
    unit = unit_words(level.get("unit"))
    names = _level_leg_names(level, legs)
    if mode == "premium":
        return level_unit_line(level)
    if mode == "price":
        ids = [str(x.get("instrument_id") or "") for x in level.get("price_legs") or []]
        leg = next((x for x in legs or () if not x.get("hedge") and not x.get("unrecognised")
                    and x.get("status") == "open"), None)
        name = (names.get(ids[0]) if ids else "") or str((leg or {}).get("name") or "")
        return (f"The price of {title_name(name)}" + (f", in {unit}" if unit else "")) if name else level_unit_line(level)
    spec = level.get("spec") or {}
    spec_legs = [x for x in spec.get("legs") or [] if _num(x.get("weight"))]
    ratio = mode == "ratio" or level.get("unit") == "ratio"
    if not spec_legs:
        return level_unit_line(level)
    ccy = "USD" if ratio else str(spec.get("currency") or "")

    def name_of(x):
        iid = str(x.get("instrument_id") or "")
        n = title_name(names.get(iid) or contract_name(iid) or iid)
        return n + (" in USD at spot" if ccy and str(x.get("currency") or ccy) != ccy else "")
    pos = [x for x in spec_legs if float(x["weight"]) > 0]
    neg = [x for x in spec_legs if float(x["weight"]) < 0]
    if ratio:
        if len(pos) == 1 and len(neg) == 1:
            return f"{name_of(pos[0])} divided by {name_of(neg[0])}, a ratio"
        return level_unit_line(level)
    words = []
    for n, x in enumerate(spec_legs):
        w = float(x["weight"])
        times = "" if abs(abs(w) - 1.0) < 1e-9 else f"{abs(w):g} × "
        lead = ("" if w > 0 else "Minus ") if n == 0 else (" plus " if w > 0 else " minus ")
        words.append(f"{lead}{times}{name_of(x)}")
    return cap("".join(words) + (f", in {unit}" if unit else ""))


def _entry_td(t: dict, data: Optional[dict] = None) -> html.Td:
    level = t.get("level") or {}
    hover = _lines(level_words(level, t.get("legs") or ()) or level_unit_line(level),
                   f"Entry: first fill {t.get('first_trade_date') or ''}", *leg_price_lines(data, t, "avg_fill"),
                   (level.get("sources") or {}).get("entry", ""), *estimate_words(level, "entry"))
    return html.Td(_level_cell(level.get("entry"), level, str(level.get("entry_reason") or level.get("reason") or ""),
                               hover, estimated=bool(level.get("entry_estimated")), with_unit=True,
                               legs=t.get("legs") or ()),
                   className="tk-level tk-entry")


def _now_td(t: dict, check: Sequence[str] = (), data: Optional[dict] = None, r: Optional[dict] = None,
            ready: bool = True) -> html.Td:
    level = t.get("level") or {}
    carry = ""
    if tf.type_code(t) == "CALENDAR" or level.get("source") == "calendar":
        c = _num(t.get("carry_per_month"))
        carry = (f"Carry per month: {full_signed(c)} USD (the legs' roll-down on one curve)"
                 if c is not None else f"Carry per month: not summed ({t.get('carry_reason') or 'not given'})")
    hover = _lines(level_words(level, t.get("legs") or ()), *leg_price_lines(data, t, "mark"),
                   (level.get("sources") or {}).get("now", ""), level.get("note") or "", carry,
                   *estimate_words(level, "now"), *(f"Price to check: {x}" for x in check))
    estimated = bool(level.get("now_estimated")) or _estimated_level(t) or bool(check)
    return html.Td(_level_cell(level.get("now"), level, str(level.get("now_reason") or level.get("reason") or ""),
                               hover, estimated=estimated, with_unit=True, legs=t.get("legs") or ()),
                   className="tk-level tk-now")


def sigma_words(r: Optional[dict], ready: bool, level: dict, legs: Sequence[dict] = ()) -> str:
    """'Move in σ: +1.4 (a daily σ of 0.21 $/bbl over 251 changes)' from
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
        sd_text = (f" (a daily σ of {level_text(sd, level, legs=legs)}{' ' + unit if unit else ''}"
                   + (f" over {days} daily changes" if days else "") + ")")
    return f"Move in σ: {z_text(ms)}{sd_text}"


def _today_td(t: dict, r: Optional[dict] = None, ready: bool = True, check: Sequence[str] = ()) -> html.Td:
    level = t.get("level") or {}
    legs = t.get("legs") or ()
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
    since = f"Since the {level.get('prev_date') or 'previous'} close ({level_text(level.get('prev'), level, legs=legs)})"
    notes = estimate_words(level, "prev", "now")
    hover = _lines(since, level_unit_line(level),
                   f"Worth {full_signed(effect)} USD to the trade" if effect is not None else "",
                   sigma_words(r, ready, level, legs), *notes, *(f"Price to check: {x}" for x in check))
    text = level_text(ch, level, signed=True, legs=legs)
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


# The flags' short names, most severe first; every flag is a chip of its own (user, 2026-09-30: never
# "Unbalanced +1"), its sentence on hover.
FLAG_NAMES = (("unrecognised", "Not recognised"), ("leg_without_price", "No price"), ("no_trade", "No trade"),
              ("price_check", "Price to check"), ("unbalanced", "Unbalanced"), ("hedge_oversized", "Hedge too big"),
              ("type_mismatch", "Type ≠ PBRoot"))
_FLAG_RANK = {code: i for i, (code, _n) in enumerate(FLAG_NAMES)}


def flag_name(flag: dict) -> str:
    """'Unbalanced', 'Hedge too big', 'Not recognised' ...: the flag's short name."""
    return dict(FLAG_NAMES).get(str(flag.get("code") or "")) or cap(str(flag.get("label") or "Flag"))


def flags_by_severity(flags: Sequence[dict]) -> List[dict]:
    return sorted(flags, key=lambda f: (not is_red(f), _FLAG_RANK.get(str(f.get("code") or ""), 99)))


# The Check column (user, 2026-09-30: "empty unless something needs doing"): only the flags that ask for
# an action are chips; unbalanced and a type differing from the PBRoot stay in the opened trade (its
# flags line), the type's hover and, for the type, the Blotter's Landed in cell.
CHECK_CODES = ("unrecognised", "leg_without_price", "no_trade", "price_check")


def check_items(t: dict) -> List[Tuple[str, bool, str]]:
    """(short name, red, sentence) of each thing to do on the trade, red first: a contract not
    recognised, a leg with no price, a price to check, fills on no trade, a hedge problem."""
    out = [(flag_name(f), is_red(f), flag_sentence(f)) for f in flags_by_severity(t.get("flags") or [])
           if str(f.get("code") or "") in CHECK_CODES]
    hedge = hedge_problem(t)
    if hedge:
        out.append((hedge[0], False, hedge[1]))
    return out


# The Action column (user, 2026-10-01: Next date and Check made one narrow column): blank unless something
# needs doing. A key date at the engine's RED or AMBER level ("Last trade in 4 days", red / amber; an
# estimated one grey after "≈", never coloured: `formatting.date_cell`'s rule), the checks above, and
# sides more than 10 % apart. The most pressing one shows, "+1" after it, all of them on hover.
SEV_RED, SEV_AMBER, SEV_GREY = 0, 1, 2
ACTION_LEVELS = {"RED": SEV_RED, "EXPIRED": SEV_RED, "AMBER": SEV_AMBER}


def date_action(t: dict, as_of: str) -> Optional[Tuple[str, int, str, bool]]:
    """(text, severity, hover, estimated) of the trade's key date when the engine's level is RED or AMBER
    (within 10 business days of its alert; a paper position past first notice keeps its level); None
    further away or with no date."""
    nx = t.get("next") or None
    if t.get("status") != "open" or not nx or not nx.get("date"):
        return None
    level = str(nx.get("level") or "").upper()
    if level not in ACTION_LEVELS:
        return None
    _text, _cls, hover = next_parts(t, as_of)
    event = str(nx.get("event") or "")
    words = NEXT_ABBR.get(event, cap(event))
    if nx.get("estimated"):
        return f"≈ {words} {day_text(nx.get('date'), as_of)}", SEV_GREY, hover, True
    bd = nx.get("business_days")
    try:
        bd = int(bd)
    except (TypeError, ValueError):
        bd = None
    if str(nx.get("date") or "") < str(as_of or "") or (bd is not None and bd < 0):
        text = f"{words} passed"
    elif bd is None:
        text = f"{words} {day_text(nx.get('date'), as_of)}"
    elif bd == 0:
        text = f"{words} today"
    else:
        text = f"{words} in {bd} {'day' if bd == 1 else 'days'}"
    return text, ACTION_LEVELS[level], hover, False


def action_items(t: dict, as_of: str) -> List[Tuple[str, int, str]]:
    """(short text, severity, sentence) of everything to do on the trade, most pressing first: the checks
    (a contract not recognised red, the rest amber), a key date (red, amber or grey when estimated) and
    sides more than 10 % apart (amber)."""
    out: List[Tuple[str, int, str]] = [(name, SEV_RED if red else SEV_AMBER, sentence)
                                       for name, red, sentence in check_items(t)]
    when = date_action(t, as_of)
    if when:
        out.append((when[0], when[1], when[2]))
    off = sides_off(t)
    if off:
        gap, where = off
        out.append((f"Sides off by {pct_text(gap)}", SEV_AMBER,
                    f"The two sides are {pct_text(gap)} apart, more than {pct_text(SIDES_TOLERANCE)}"
                    + (f": {where}" if where else "")))
    return sorted(out, key=lambda x: x[1])


ACTION_CLASS = {SEV_RED: "cell-red", SEV_AMBER: "cell-amber", SEV_GREY: "cell-estimated"}


def _action_td(t: dict, as_of: str) -> html.Td:
    """The most pressing thing to do on the trade in its colour, "+N" after it when there is more, every
    item's sentence on hover; blank when nothing needs doing."""
    items = action_items(t, as_of)
    if not items:
        return html.Td("", className="l tk-action-cell")
    text, sev, _s = items[0]
    hover = _lines(*(cap(sentence or name) for name, _sv, sentence in items))
    more = html.Span(f" +{len(items) - 1}", className="tk-action-more") if len(items) > 1 else None
    return html.Td(html.Span([html.Span(text, className=ACTION_CLASS[sev]), more], title=plain_words(hover) or None),
                   className="l tk-action-cell")


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


def part_level_words(sub: dict, legs: Sequence[dict] = ()) -> str:
    """One part's level as the engine gave it ('SGX iron ore Oct/Nov26 vs Feb/Mar27: entry 12.50,
    now 13.00 $/t'), or its reason; at the precision of `legs` (the trade's legs, `level_decimals`)."""
    lv = sub.get("level") or {}
    unit = unit_words(lv.get("unit"))
    what = cap(str(sub.get("what_it_is") or ""))
    if _num(lv.get("now")) is None and _num(lv.get("entry")) is None:
        return f"{what}: no level ({lv.get('reason') or 'none'})"
    return (f"{what}: entry {level_text(lv.get('entry'), lv, legs=legs)}, now {level_text(lv.get('now'), lv, legs=legs)}"
            + (f" {unit}" if unit else ""))


def has_leg_rows(t: dict) -> bool:
    """The trade has legs to show under its caret (none for the fills on no trade)."""
    return not t.get("pseudo") and any(kind == "leg" for kind, _i, _l in leg_sequence(t))


def legs_caret(t: dict) -> Any:
    """The caret before a trade's name: a click shows or hides its legs (in the browser,
    `ui/assets/book_filters.js`; the row's own click, which opens the panel, never sees it). None
    when there are no legs to show."""
    if not has_leg_rows(t):
        return html.Span("", className="tk-chev tk-chev--none")
    name = str(t.get("trade") or "")
    return html.Span("▸", id={"type": LEGS_TYPE, "idx": name}, className="tk-chev tk-legs-caret",
                     title="Show or hide the legs", **{"data-caret-of": name})


def trade_tr(data: dict, t: dict, r: Optional[dict], ready: bool, opened: bool,
             hidden: Sequence[str] = (), zd: Optional[dict] = None) -> html.Tr:
    """One trade's row, in `columns(hidden)`'s order (a hidden column's cell is left out); `zd` the
    trade's `spread_z` entry (`spread_z_of`). A trade of one spread (or one outright) carries its size,
    levels and z; a trade of several leaves them blank, each spread's row under it carrying its own."""
    ids = [str(i) for i in t.get("trade_ids") or []]
    daily = fill_sum(data, "daily", ids)
    ltd = fill_sum(data, "ltd", ids)
    mtd, ytd = fill_sum(data, "mtd", ids), fill_sum(data, "ytd", ids)
    name = str(t.get("trade") or "")
    pbs = t.get("pb_roots") or []
    tv = dict(t, level=display_level(data, t))
    closed = bool(tv["level"].get("closed"))
    close_day = str((t.get("closed") or {}).get("close_date") or "") if closed else ""
    name_hover = _lines(f"PBRoot: {', '.join(pbs)}" if pbs else "", f"{_plural(len(ids), 'fill')}",
                        f"Closed on {day_text(close_day, data['as_of'])}" if close_day else "")
    check = check_lines(data, ids)
    info = row_info([excl_line("P&L today", daily), excl_line("P&L since entry", ltd), excl_line("MTD", mtd),
                     excl_line("YTD", ytd)])
    multi = is_multi(t)
    z_cells = {} if multi else trade_z_cells(t, zd, ready)
    blank = html.Td("")
    cells: Dict[str, Any] = {
        "trade": html.Td([legs_caret(t), html.Span(name, className="tk-name", title=name_hover or None), info],
                         className="l"),
        "what": _what_one_td(t), "qty": _qty_td(t, r),
        "entry": html.Td("", className="tk-level") if multi else _entry_td(tv, data),
        "now": html.Td("", className="tk-level") if multi else _now_td(tv, check, data, r, ready),
        "z_entry": blank if multi else z_cells["z_entry"],
        "z_now": html.Td("") if multi else z_cells["z_now"],
        "daily": money_td(*daily, hover=_split_hover(t), check=check, row=True),
        "open": split_td(data, t, "open", ltd, check),
        "locked": split_td(data, t, "locked", ltd, check),
        "ltd": money_td(*ltd, hover=_lines("Open plus locked in", f"MTD {km_text(mtd[0])}", f"YTD {km_text(ytd[0])}",
                                           "The final P&L: the trade is flat" if closed else ""), check=check, row=True),
        "action": _action_td(t, data["as_of"]),
    }
    return html.Tr([cells[key] for key, *_ in columns(hidden) if cells.get(key) is not None],
                   id={"type": ROW_TYPE, "idx": name}, n_clicks=0,
                   className="tk-row" + (" tk-row--open" if opened else "") + (" tk-row--pseudo" if t.get("pseudo") else "")
                   + (" tk-row--multi" if multi else ""))


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
    as_of = str(data.get("as_of") or "")
    acts = [(t, action_items(t, as_of)) for t in shown]
    out["to_check"] = sum(1 for _t, items in acts if items)
    out["red_checks"] = sum(1 for _t, items in acts if any(sev == SEV_RED for _n, sev, _s in items))
    out["check_lines"] = [f"{t.get('trade')}: {(sentence.splitlines()[0] if sentence else name)}"
                          for t, items in acts for name, _sev, sentence in items]
    return out


def _total_checks(tot: dict) -> Any:
    """'3 to act on' in the total row's Action cell (the trades with something to do): red when any
    item is red, amber otherwise; each item's trade and sentence on hover."""
    n, red = tot["to_check"], tot["red_checks"]
    if not n:
        return ""
    lines = tot["check_lines"]
    hover = _lines(*lines[:20], f"And {len(lines) - 20} more" if len(lines) > 20 else "")
    return html.Span(f"{n} to act on", className="tk-flags" + (" tk-flags--red" if red else ""),
                     title=plain_words(hover))


def total_tr(data: dict, shown: Sequence[dict], filtered: bool, hidden: Sequence[str] = ()) -> html.Tr:
    tot = totals(data, shown)
    label, hover = _total_label(shown, data.get("trades") or [], filtered)
    if not filtered:
        hover = _lines(hover, "P&L today, MTD and P&L since entry equal the top bar's Daily, MTD and LTD to the cent")
    # nothing under What it is (user, 2026-09-30: Gross and Net left the total row)
    cells = {"trade": html.Td(html.Span(label, title=hover), className="l"),
             "daily": _checked_money(data, shown, tot["daily"]),
             "open": split_total_td(data, shown, "open"), "locked": split_total_td(data, shown, "locked"),
             "ltd": _checked_money(data, shown, tot["ltd"], hover=_lines("Open plus locked in",
                                                                         f"MTD {km_text(tot['mtd'][0])}")),
             "action": html.Td(_total_checks(tot), className="l tk-action-cell")}
    return html.Tr([cells.get(key) or html.Td("") for key, *_ in columns(hidden)], className="tk-total book-total")


def _checked_money(data: dict, shown: Sequence[dict], fig, hover: str = "") -> html.Td:
    """A total's money cell and, when a price to check drives some of the rows, the trades named."""
    names = [str(t.get("trade")) for t in shown if check_lines(data, [str(i) for i in t.get("trade_ids") or []])]
    td = money_td(*fig, hover=hover)
    if names and fig[0] is not None:
        td.children = list(td.children) + [marker(f"≈ {len(names)} to check", _lines(
            "Includes the P&L of trades priced off a price the marks check flags:", *names), "marker--small")]
    return td


# --------------------------------------------------------------------------- sort
SORTABLE = {"trade", "what", "qty", "entry", "now", "z_entry", "z_now", "daily", "open", "locked", "ltd", "action"}


def trade_z(t: dict, zd: Optional[dict], key: str) -> Optional[float]:
    """The z a trade row shows under `key` ('z_entry' | 'z_now'; a closed trade's z now is its z at
    exit), for the sort and the filter: the trade's own level's, None for a trade of several spreads."""
    tl = (zd or {}).get("trade_level") or {}
    point = "entry" if key == "z_entry" else ("exit" if t.get("status") == "closed" else "now")
    return None if is_multi(t) else _num(tl.get(f"z_{point}"))


def sort_value(data: dict, t: dict, key: str, r: Optional[dict], zd: Optional[dict] = None) -> Any:
    level = t.get("level") or {}
    if key == "trade":
        return str(t.get("trade") or "").lower()
    if key == "what":
        return what_line(t).lower()
    if key in ("entry", "now"):
        return None if is_multi(t) else _num(level.get(key))
    if key == "qty":
        lots = [abs(_num(leg.get("lots")) or 0.0) for leg in _open_legs(t)]
        return sum(lots) if lots else None
    if key in Z_KEYS:
        z = trade_z(t, zd, key)
        return abs(z) if z is not None else None
    if key in ("daily", "ltd"):
        return fill_sum(data, key, [str(i) for i in t.get("trade_ids") or []])[0]
    if key in SPLIT_KEYS:
        return trade_split(data, t, key)[0]
    if key == "action":
        items = action_items(t, str(data.get("as_of") or ""))
        return (sum(1 for _n, sev, _s in items if sev == SEV_RED), sum(1 for _n, sev, _s in items if sev == SEV_AMBER),
                len(items)) if items else None
    return None


def sort_rows(data: dict, rows: Sequence[dict], sort: Optional[dict], risk: Dict[str, dict],
              zmap: Optional[Dict[str, dict]] = None) -> List[dict]:
    """The rows in the default order (sector in its fixed order, then commodity, then name; user,
    2026-10-01: `trade_filter.default_order`), or by the column chosen; a missing value always last.
    `zmap`: risk-metrics' `spread_z` (the z columns' sort)."""
    base = tf.default_order(rows)
    key = (sort or {}).get("key")
    if key not in SORTABLE:
        return base
    zmap = zmap or {}
    desc = (sort or {}).get("dir") != "asc"

    def value(t):
        return sort_value(data, t, key, risk.get(str(t.get("trade"))), zmap.get(str(t.get("position_id") or "")))
    have = [t for t in base if value(t) is not None]
    none = [t for t in base if t not in have]
    have.sort(key=value, reverse=desc)
    return have + none


def next_sort(current: Optional[dict], key: str) -> Optional[dict]:
    """desc -> asc -> the default order."""
    cur = current or {}
    if cur.get("key") != key:
        return {"key": key, "dir": "desc"}
    if cur.get("dir") == "desc":
        return {"key": key, "dir": "asc"}
    return None


# The column filters (spreadsheet-style, the funnel in each heading; user, 2026-09-29): only on the text
# columns since 2026-10-01 (user): Trade, What it is (the commodity family and the type) and Action. Every
# heading still sorts on its title; the number columns carry no funnel.
LIST_FUNNELS = {"trade": ("trade",), "what": ("commodity", "type")}     # the type column left (2026-09-30)
OWN_FUNNELS = ("action",)                     # the Book's own column filters (the rest are shared parts)
FLAG_ANY, FLAG_RED = "any", "red"
FLAG_OPTIONS = [{"label": "Something to act on", "value": FLAG_ANY}, {"label": "Red only", "value": FLAG_RED}]


def _action_funnel(state: Optional[dict]) -> html.Details:
    value = tf.tab_filters(state, TAB).get("action")
    ticked = value if isinstance(value, list) else []
    return tf.funnel(f"{TAB}:action", tf.pop_list(tf.col_id(TAB, "action"), "Action", FLAG_OPTIONS, ticked,
                                                 search=False),
                     bool(ticked), tf.option_words(FLAG_OPTIONS, ticked))


def columns(hidden: Sequence[str] = ()) -> Tuple[Tuple[str, str, str, str], ...]:
    """The trade view's columns, less the `hidden` ones (`hidden_columns`: a column no row has data for)."""
    gone = set(hidden or ())
    return tuple(c for c in COLUMNS if c[0] not in gone)


# A column no trade has data for is hidden, with one grey line under the table saying why (user,
# 2026-09-30). Worked out on every trade on file, never on the filtered rows, so a filter never makes a
# column come and go; a hidden column's filter and sort are set aside while it is hidden.
Z_HIDDEN_NO_HISTORY = "z-score hidden: it needs Bloomberg's price history, fetched by Pull Bloomberg now"


def any_z(risk: dict) -> bool:
    """Any z at entry, now or exit on any trade or spread of risk-metrics' `spread_z` (or a trade row)."""
    for zd in (risk.get("spread_z") or {}).values():
        for one in [zd.get("trade_level") or {}, *(zd.get("spreads") or [])]:
            if any(_num(one.get(f"z_{p}")) is not None for p in ("entry", "now", "exit")):
                return True
    return any(_num(r.get("z")) is not None for r in risk.get("trades") or [])


def hidden_columns(data: dict, risk: Optional[dict]) -> Dict[str, str]:
    """{column key: why it is hidden} for z, Open / Locked in and Action."""
    trades = data.get("trades") or []
    named = [t for t in trades if not t.get("pseudo")]
    out: Dict[str, str] = {}
    if not (data.get("history") or {}).get("rows"):
        out["z_entry"] = out["z_now"] = Z_HIDDEN_NO_HISTORY
    elif risk is not None and not any_z(risk):
        why = (risk.get("reason") if not risk.get("available") else "") or next(
            (str(r.get("z_reason")) for r in risk.get("trades") or [] if r.get("z_reason")), "")
        out["z_entry"] = out["z_now"] = "z-score hidden: no trade has one" + (f" ({why})" if why else "")
    open_named = [t for t in named if t.get("status") == "open"]
    if open_named and not any(has_split(t) for t in open_named):
        reasons = [str(t.get("pnl_split_reason") or "") for t in open_named if any(k in t for k in SPLIT_KEYS.values())]
        why = next((r for r in reasons if r), "")
        out["open"] = out["locked"] = ("Open and Locked in hidden: split not available"
                                       + (f" ({why})" if why else " yet"))
    if not any(action_items(t, str(data.get("as_of") or "")) for t in trades):
        out["action"] = "Action hidden: nothing to act on"
    return out


def hidden_row(hidden: Dict[str, str], span: int) -> Optional[html.Tfoot]:
    """The one grey line under the table naming each hidden column and why (a table row, never a loose
    line)."""
    if not hidden:
        return None
    words = list(dict.fromkeys(hidden.values()))
    return html.Tfoot(html.Tr(html.Td(cap_parts(" · ".join(words)), colSpan=span, className="l tk-hidden-note"),
                              className="tk-hidden-row"))


def head(sort: Optional[dict], state: Optional[dict] = None,
         options: Optional[Dict[str, List[dict]]] = None, hidden: Sequence[str] = ()) -> html.Thead:
    """The column heads: each title sorts, each filterable column carries its funnel (`trade_filter.
    funnel`)."""
    ths = []
    cols = columns(hidden)
    half = len(cols) // 2
    for i, (key, title, cls, tip) in enumerate(cols):
        pop = None
        if key in LIST_FUNNELS:
            pop = tf.trade_funnel(TAB, state, options or {}, LIST_FUNNELS[key], key)
        elif key == "action":
            pop = _action_funnel(state)
        ths.append(tf.head_th(title, cls, tip, sort_id={"type": SORT_TYPE, "idx": key} if key in SORTABLE else None,
                              arrow=tf.arrow_of(sort, key), pop=pop, right=i > half))
    return html.Thead(html.Tr(ths))


def filter_lists(t: dict, col: str, as_of: str = "") -> List[str]:
    """A trade's values for a tick-list filter of its own column (Action)."""
    if col == "action":
        items = action_items(t, as_of)
        return ([FLAG_ANY] if items else []) + ([FLAG_RED] if any(sev == SEV_RED for _n, sev, _s in items) else [])
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


def _split_sum_td(legs: Sequence[dict], kind: str) -> html.Td:
    """The Open or Locked in cell of a part row: its legs' own figures summed (display), "excl. N"
    with the reasons when a leg has none; the dash when none has."""
    total, n, reasons = sum_known(split_value(leg, kind) for leg in legs)
    if total is None:
        return html.Td(missing_cell(_lines(*list(dict.fromkeys(reasons))[:4]) or SPLIT_MISSING))
    return html.Td([km_cell(_cents(total), hover=SPLIT_WORDS[kind]),
                    marker(f"excl. {n}", _lines(*list(dict.fromkeys(reasons))[:4]), "marker--small") if n else None])


def _part_level_td(level: Optional[dict], end: str, legs: Sequence[dict] = (), data: Optional[dict] = None,
                   t: Optional[dict] = None) -> html.Td:
    """A spread's own level at `end` ('entry' | 'now') from the engine, with its unit like the trade row's;
    how it is built, each of its legs' average fill (entry) or mark (now) and the move on hover."""
    if level is None:
        return html.Td("")
    ch = _num(level.get("change"))
    prices = leg_price_lines(data, dict(t or {}, legs=list(legs), level=level), "avg_fill" if end == "entry" else "mark")
    hover = _lines(level_words(level, legs) or level_unit_line(level), *prices,
                   f"Move since the previous close: {level_text(ch, level, signed=True, legs=legs)}"
                   if ch is not None and end == "now" else "",
                   level.get("note") or "")
    return html.Td(_level_cell(level.get(end), level, str(level.get(end + "_reason") or level.get("reason") or ""),
                               hover, with_unit=True, legs=legs), className="tk-level")


UNMATCHED = "UNMATCHED"           # spreads-engine's part of legs no equal-size rule pairs (2026-10-01)


def part_name(sub: dict) -> str:
    """A spread's name in words with its type where the name does not say it ('CME HRC Oct/Dec26
    Calendar', 'SHFE Zinc vs LME Zinc, Oct26 · Cross-exchange'); legs no rule pairs read "Unmatched
    legs: SGX Iron Ore Oct26 + SGX Iron Ore Nov26" (the plain words, user 2026-09-28)."""
    what = title_name(str(sub.get("what_it_is") or ""))
    if str(sub.get("type") or "") == UNMATCHED or sub.get("unmatched"):
        rest = re.sub(r"^\s*unmatched( legs)?\s*:\s*", "", what, flags=re.IGNORECASE)
        return f"Unmatched legs: {rest}" if rest else "Unmatched legs"
    kind = tf.type_label(sub.get("type") or "") if sub.get("type") else ""
    return cap(what if not kind or kind.split("-")[0].lower() in what.lower() else f"{what} · {kind}")


def part_z_cells(t: dict, sub: dict, zd: Optional[dict], ready: bool, legs: Sequence[dict] = ()) -> Dict[str, html.Td]:
    """A spread's head row z entry and z now (z exit once the trade is closed): its own `spreads`
    entry of risk-metrics' `spread_z`, matched by its place among the trade's sub_spreads."""
    subs = list(t.get("sub_spreads") or [])
    idx = next((n for n, x in enumerate(subs) if x is sub), None)
    if idx is None and sub in subs:
        idx = subs.index(sub)
    one = next((x for x in (zd or {}).get("spreads") or [] if x.get("spread") == idx), None) if idx is not None else None
    why = (str((sub.get("level") or {}).get("reason") or "") or "no z-score for this spread") if one is None else ""
    closed = t.get("status") == "closed"
    level = sub.get("level") or None
    return {"z_entry": z_cell(one, "entry", ready, why, level=level, legs=legs),
            "z_now": z_cell(one, "exit" if closed else "now", ready, why, level=level, legs=legs)}


def part_tr(data: dict, t: dict, sub: dict, legs: Sequence[dict], hidden: Sequence[str] = (),
            zd: Optional[dict] = None, ready: bool = True) -> html.Tr:
    """A spread's head row under a trade of several spreads (its legs follow it): its name, its size
    per side, its own level and z (user, 2026-10-01: each spread's z on its own row) and its legs'
    P&L summed (display). "Other legs" (legs in no spread) carries the P&L only."""
    ids = [str(i) for leg in legs for i in leg.get("trade_ids") or []]
    other = "level" not in sub and not sub.get("type")
    level = None if other else (sub.get("level") or {})
    z_cells = {} if other else part_z_cells(t, sub, zd, ready, legs)
    cells: Dict[str, Any] = {
        "trade": html.Td(part_name(sub), colSpan=2, className="l tk-part-name"),
        "qty": html.Td("") if other else sub_qty_td(sub),
        "entry": _part_level_td(level, "entry", legs, data, t), "now": _part_level_td(level, "now", legs, data, t),
        "z_entry": z_cells.get("z_entry"), "z_now": z_cells.get("z_now"),
        "daily": money_td(*fill_sum(data, "daily", ids), row=True),
        "open": _split_sum_td(legs, "open"), "locked": _split_sum_td(legs, "locked"),
        "ltd": money_td(*fill_sum(data, "ltd", ids), row=True),
    }
    return html.Tr([cells.get(key) or html.Td("") for key, *_ in columns(hidden) if key != "what"],
                   className="tk-part-row")


def leg_sequence(t: dict) -> List[Tuple[str, dict, List[dict]]]:
    """The rows under a trade, in the order the opened trade listed them: its legs (side A then B, by
    month, flat legs after; under a head row per part for a trade of several spreads, "Other legs"
    for the rest), then its hedges. Each item ("part", sub-spread, its legs) or ("leg", leg, [])."""
    legs = _leg_order(t)
    if legs and all(leg.get("hedge") for leg in legs):
        legs = [dict(leg, hedge=False) for leg in legs]     # a trade of hedges only: no leg is "the hedge" of another
    plain = [leg for leg in legs if not leg.get("hedge")]
    out: List[Tuple[str, dict, List[dict]]] = []
    subs = [x for x in t.get("sub_spreads") or [] if x.get("legs")]
    if len(subs) > 1:
        taken: set = set()
        for sub in subs:
            cids = {x.get("contract_id") for x in sub.get("legs") or []}
            mine = [leg for leg in plain if leg.get("contract_id") not in taken and leg.get("contract_id") in cids]
            if not mine:
                continue
            taken.update(leg.get("contract_id") for leg in mine)
            out.append(("part", sub, mine))
            out.extend(("leg", leg, []) for leg in mine)
        rest = [leg for leg in plain if leg.get("contract_id") not in taken]
        if rest:
            out.append(("part", {"what_it_is": "Other legs"}, rest))
            out.extend(("leg", leg, []) for leg in rest)
    else:
        out.extend(("leg", leg, []) for leg in plain)
    out.extend(("leg", leg, []) for leg in legs if leg.get("hedge"))
    return out


def leg_qty_words(leg: dict) -> str:
    """A leg's size in words, the side first: 'Long 4 lots', 'Short 1,500,000 USD', 'Short 100 oz', 'Flat'."""
    q = _num(leg.get("lots"))
    if q is None:
        return ""
    if str(leg.get("product") or "") in NOTIONAL_PRODUCTS:
        unit, money = _base_unit(leg)
        return size_words(q, ccy=unit) if money else size_words(q, unit)
    return size_words(q, "lots")


def _leg_split_td(data: dict, t: dict, leg: dict, kind: str, check: Sequence[str]) -> html.Td:
    """A leg row's Open / Locked in, the engine's per-leg split; a closed trade's legs carry no Open
    (nothing held), as its own row."""
    if kind == "open" and t.get("status") == "closed":
        return html.Td("")
    v, why = split_value(leg, kind)
    if v is None:
        return html.Td(missing_cell(why))
    hover = _lines(SPLIT_WORDS[kind], NON_USD_LOCKED if kind == "locked" and non_usd(leg) else "")
    return html.Td([check_mark(check), km_cell(_cents(v), hover=hover, colour=not check,
                                               className="cell-estimated" if check else "")])


def leg_row(data: dict, t: dict, leg: dict, hidden: Sequence[str] = (), last: bool = False,
            top: bool = False) -> html.Tr:
    """One leg under its trade, in the trade table's own columns: the leg's name with its exchange, its
    size in words, its average fill and its mark at tick precision, then P&L today and since entry (its
    fills' own figures summed, as the trade row's, so the legs add up to it) and the engine's Open and
    Locked in; z, Next date and Check are the trade's."""
    ids = [str(i) for i in leg.get("trade_ids") or []]
    hedge = bool(leg.get("hedge"))
    unrec = bool(leg.get("unrecognised"))
    flat = not unrec and leg.get("status") != "open"
    check = leg_check_lines(data, leg)
    unit = "" if unrec else _leg_unit(data, leg)
    name = str(leg.get("name") or contract_name(str(leg.get("contract_id") or "")) or leg.get("contract_id") or "")
    lots_hover, fill_hover = _fills_hover(data, leg)
    tag = ("Not recognised" if unrec else "FX hedge" if hedge else "Closed" if flat else "")
    tag_hover = (leg.get("unrecognised_reason") or "Contract not recognised") if unrec else (
        "A currency hedge of the whole trade, never of one spread" if hedge
        else "Nothing held on this contract any more")
    name_td = html.Td([html.Span(name, title=plain_words(_leg_hover(data, leg)) or None),
                       html.Span(tag, className="tk-leg-tag" + (" tk-leg-tag--red" if unrec else ""),
                                 title=plain_words(tag_hover)) if tag else None],
                      colSpan=2, className="l tk-leg-name")
    qty = leg_qty_words(leg)
    qty_td = html.Td(html.Span(qty, title=plain_words(_lines(lots_hover, "As in the file" if unrec else "")) or None)
                     if qty else missing_cell("no size read"), className="l tk-qty")
    fill = _num(leg.get("avg_fill"))
    entry_td = html.Td(html.Span(price_text(fill, unit, fill), title=plain_words(_lines("Average fill", fill_hover)))
                       if fill is not None else missing_cell(leg.get("avg_fill_reason") or "no open lots"),
                       className="tk-level")
    mark = _num(leg.get("mark"))
    est = is_estimated_mark(leg.get("mark_source"))
    note = str((t.get("level") or {}).get("note") or "")
    m_hover = _lines(mark_hover(data, leg, unit, fill, note), *(f"Price to check: {x}" for x in check))
    now_td = html.Td(html.Span(("≈ " if est or check else "") + price_text(mark, unit, fill), title=plain_words(m_hover),
                               className=("cell-estimated" if est else "") + (" cell-amber" if check else "") or None)
                     if mark is not None else missing_cell(_lines(leg.get("mark_reason") or "no mark",
                                                                  "" if unrec else m_hover)),
                     className="tk-level")
    rows = _df_rows(data, ids)
    local = None
    if not rows.empty and leg.get("currency") not in ("", "USD") and "pnl_local" in rows:
        local = sum_known((_num(v), "") for v in rows["pnl_local"])[0]
    realised = (sum_known((_num(v), "") for v, s in zip(rows.get("pnl_usd", []), rows.get("status", []))
                          if s == "SETTLED")[0] if not rows.empty else None)
    ltd_hover = _lines(f"Local: {leg.get('currency')} {full_signed(local)}" if local is not None else "",
                       f"Realised: {full_signed(realised)} USD" if realised is not None else "")
    cells: Dict[str, Any] = {
        "trade": name_td, "qty": qty_td, "entry": entry_td, "now": now_td,
        "daily": money_td(*fill_sum(data, "daily", ids), check=check, row=True),
        "open": _leg_split_td(data, t, leg, "open", check), "locked": _leg_split_td(data, t, leg, "locked", check),
        "ltd": money_td(*fill_sum(data, "ltd", ids), hover=ltd_hover, check=check, row=True),
    }
    return html.Tr([cells.get(key) or html.Td("") for key, *_ in columns(hidden) if key != "what"],
                   className="tk-leg-row" + (" tk-leg-row--hedge" if hedge else "") + (" tk-leg-row--flat" if flat else "")
                   + (" tk-leg-row--unrec" if unrec else "") + (" tk-leg-row--last" if last else "")
                   + (" tk-leg-row--top" if top else ""), **{"data-legs-of": str(t.get("trade") or "")})


def leg_rows(data: dict, t: dict, hidden: Sequence[str] = (), zd: Optional[dict] = None,
             ready: bool = True) -> List[html.Tr]:
    """Every row under a trade (user, 2026-10-01): each spread's row of a trade of several spreads, always
    showing, and the legs, hidden until the trade's caret is clicked (`ui/assets/book_filters.js` shows the
    rows marked with the trade's name); hedges last, at the spreads' level, never under one spread. None
    for the fills on no trade (their opened row lists each fill)."""
    if t.get("pseudo"):
        return []
    seq = leg_sequence(t)
    multi = any(kind == "part" for kind, _i, _l in seq)
    out = []
    for n, (kind, item, legs) in enumerate(seq):
        if kind == "part":
            out.append(part_tr(data, t, item, legs, hidden, zd, ready))
        else:
            out.append(leg_row(data, t, item, hidden, last=n == len(seq) - 1, top=multi and bool(item.get("hedge"))))
    return out


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


def panel_legs(data: dict, t: dict) -> Optional[html.Table]:
    """What the legs' rows under the trade do not carry (2026-09-30: the legs now always show inline, so
    the opened trade no longer repeats their sizes, prices and P&L): each open leg's value in USD (the
    local value and the spot on hover) and its roll-down a month, the trade's gross value and carry in
    the foot. None when nothing is open."""
    legs = [leg for kind, leg, _l in leg_sequence(t) if kind == "leg" and leg.get("status") == "open"
            and not leg.get("unrecognised")]
    if not legs:
        return None
    body = []
    for leg in legs:
        value = _num(leg.get("value_usd"))
        v_hover = value_hover(data, leg, _df_rows(data, [str(i) for i in leg.get("trade_ids") or []]))
        value_cell = (html.Span(full_signed(value), title=plain_words(v_hover) or None)
                      if value is not None else missing_cell(_lines(leg.get("value_reason") or "no value", v_hover)))
        roll = _num(leg.get("roll_down_usd_per_month"))
        roll_hover = _lines(f"{price_text(leg.get('roll_down'))} {leg.get('roll_down_unit') or ''} a month over "
                            f"{leg.get('horizon_months') or ''} months on the Bloomberg history's curve"
                            if _num(leg.get("roll_down")) is not None else "")
        roll_cell = (html.Span(full_signed(roll), title=plain_words(roll_hover) or None) if roll is not None
                     else missing_cell(leg.get("roll_down_reason") or "no roll-down"))
        name = str(leg.get("name") or leg.get("contract_id") or "")
        body.append(html.Tr([html.Td(name + (" (hedge)" if leg.get("hedge") else ""), className="l",
                                     title=plain_words(_leg_hover(data, leg)) or None),
                             html.Td(value_cell), html.Td(roll_cell)],
                            className="tk-leg" + (" tk-leg--hedge" if leg.get("hedge") else "")))
    carry = _num(t.get("carry_per_month"))
    carry_cell = (html.Span(full_signed(carry), title="The legs' roll-down a month on one curve")
                  if carry is not None else html.Span("Not summed", className="tk-sub",
                                                      title=plain_words(str(t.get("carry_reason") or "not given"))))
    gross = _num(t.get("gross_usd"))
    foot = html.Tr([html.Td("Trade total", className="l"),
                    html.Td(format_cell(gross) if gross is not None else missing_cell(t.get("notional_reason") or "no gross"),
                            title="Gross USD value of the open legs"),
                    html.Td(carry_cell)])
    head = html.Thead(html.Tr([html.Th(title, className=cls or None) for _k, title, cls in LEG_COLUMNS]))
    return html.Table([head, html.Tbody(body), html.Tfoot(foot)], className="book-table tk-table tk-legs")


def panel_facts(t: dict, data: Optional[dict] = None, r: Optional[dict] = None, ready: bool = True,
                check: Sequence[str] = ()) -> html.Table:
    """The trade's facts beside its legs, a small key / value table (layout wave 2026-09-29: never a
    loose line): its type (with the PBRoot disagreement when there is one), its entry date (the first
    fill), today's move of the level in its unit, USD per 1-unit move of the level, the balance of its
    sides. All read from the engine's fields; nothing computed. Type, Entry date and Move left the
    row for here on 2026-09-30."""
    level, size = t.get("level") or {}, t.get("size") or {}
    rows: List[Any] = []
    if not t.get("pseudo"):
        mismatch = str(t.get("type_mismatch") or "")
        words = tf.trade_type_label(t)
        note = t.get("type_note") or ""
        type_cell: Any = html.Span(words, title=plain_words(note) or None)
        if mismatch:
            type_cell = html.Div([type_cell, html.Div(cap(plain_words(mismatch)), className="tk-sub cell-amber tk-kv-note")])
        rows.append(("Type", type_cell))
    first = str(t.get("first_trade_date") or "")
    rows.append(("Entry date", html.Span(day_text(first, (data or {}).get("as_of")), title=f"First fill {first}")
                 if first else missing_cell("no first fill date on file")))
    # the next key date, however far (the Action column says it only within 10 business days)
    as_of = str((data or {}).get("as_of") or "")
    if t.get("status") == "closed" and (t.get("closed") or {}).get("close_date"):
        rows.append(("Closed", html.Span(day_text(t["closed"]["close_date"], as_of),
                                         title="Its last fill, or a leg's settlement after it")))
    elif not t.get("pseudo"):
        text, cls, nh = next_parts(t, as_of)
        rows.append(("Next date", html.Span(text, className=cls or None, title=plain_words(nh) or None)
                     if text else missing_cell(nh)))
    if data is not None and not t.get("pseudo"):
        tv = dict(t, level=display_level(data, t))
        unit = unit_words(tv["level"].get("unit"))
        move = _today_td(tv, r, ready, check).children
        rows.append((f"Move today{f' ({unit})' if unit else ''}", move))
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
    from ui.tabs.blotter_pricing import priced_value_book, config_inputs_key

    def compute():
        dates = _closes(str(t.get("first_trade_date") or data["as_of"]), data["as_of"])
        return level_history(conn, t, dates, value_fn=priced_value_book)
    return _memo("level", conn, data["as_of"], compute, extra=(str(t.get("trade")), *config_inputs_key()))


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


def panel_tr(conn: sqlite3.Connection, data: dict, t: dict, span: int = len(COLUMNS), r: Optional[dict] = None,
             ready: bool = True) -> html.Tr:
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
            html.Div([html.Div([x for x in (panel_legs(data, t), hedge_line(t)) if x is not None] or None,
                               className="tk-legs-main"),
                      panel_facts(t, data, r, ready, check_lines(data, [str(i) for i in t.get("trade_ids") or []]))],
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
    return html.Tr(html.Td(parts, colSpan=span, className="l tk-panel-cell"), className="tk-panel")


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
        ("Fill", "Date", "Quantity", "Fill price", "Mark", "P&L today", "P&L since entry"))])), html.Tbody(body)],
        className="book-table tk-table tk-legs")


# --------------------------------------------------------------------------- the table
def own_cols(state: Optional[dict], hidden: Sequence[str] = ()) -> Dict[str, Any]:
    """The trade view's own column filters: the Book's columns with a funnel (Action), never the By contract
    view's ('c-' keys), a number column's (their funnels left on 2026-10-01: a session's old comparison is
    set aside) nor a column the table hides today (`hidden`: the filter is kept in the store and applies
    again once the column is back)."""
    keys = {c[0] for c in columns(hidden)} & set(OWN_FUNNELS)
    return {k: v for k, v in tf.tab_filters(state, TAB).items() if k in keys}


def view_filtered(state: Optional[dict], hidden: Sequence[str] = ()) -> bool:
    """A filter the trade view applies is set: the search, a shared list, or one of its own columns."""
    s = tf.normal(state)
    return bool(s["search"].strip() or s["type"] or s["commodity"] or s["trade"] or own_cols(s, hidden))


def visible(data: dict, state: Optional[dict], risk: Optional[dict] = None) -> List[dict]:
    """The trades the search and every column's filter keep (whole trades, never a leg)."""
    cols = own_cols(state, hidden_columns(data, risk))
    shown = tf.apply(data.get("trades") or [], state)
    if not cols:
        return shown
    as_of = str(data.get("as_of") or "")
    return [t for t in shown
            if tf.keeps_cols(t, cols, lambda t, col: None, lambda t, col: filter_lists(t, col, as_of))]


def table(conn: sqlite3.Connection, data: dict, state: Optional[dict], sort: Optional[dict], opened: Sequence[str],
          folds: Optional[dict], risk: Optional[dict]) -> Tuple[html.Table, List[str]]:
    s = tf.normal(state)
    shown = visible(data, s, risk)
    ready = risk is not None
    rrows = risk_rows(risk)
    zmap = (risk or {}).get("spread_z") or {}
    opened_set = set(opened or [])
    folds = folds or {}
    hidden = hidden_columns(data, risk)
    cols = columns(hidden)
    keys = [c[0] for c in cols]
    lead = keys.index("daily")      # the columns before P&L today (the closed fold's label)
    if (sort or {}).get("key") in hidden:
        sort = None                 # a hidden column's sort is set aside, the store keeps it
    body: List[Any] = [total_tr(data, shown, view_filtered(s, hidden), hidden)]
    open_rows = [t for t in shown if t.get("status") == "open" or (t.get("pseudo") and t.get("status") != "closed")]
    closed_rows = [t for t in shown if t not in open_rows]

    def add(t):
        name = str(t.get("trade") or "")
        is_open = name in opened_set
        zd = zmap.get(str(t.get("position_id") or ""))
        body.append(trade_tr(data, t, rrows.get(name), ready, is_open, hidden, zd))
        body.extend(leg_rows(data, t, hidden, zd, ready))   # spreads showing, legs behind the caret (2026-10-01)
        if is_open:
            try:
                body.append(panel_tr(conn, data, t, len(cols), rrows.get(name), ready))
            except Exception as exc:  # noqa: BLE001 -- one panel's reason, never the table
                log.exception("Book: the panel of %s failed", name)
                body.append(html.Tr(html.Td(missing_cell(f"the panel could not be built ({type(exc).__name__}: {exc})"),
                                            colSpan=len(cols), className="l"), className="tk-panel"))

    # one flat list (user, 2026-09-30: the Group switch left the Book's strip; the shared group, the P&L
    # tab's slice, is not read here)
    for t in sort_rows(data, open_rows, sort, rrows, zmap):
        add(t)
    if closed_rows:
        year = str(data["as_of"])[:4]
        this_year = sum(1 for t in closed_rows if closed_on(data, t)[:4] == year)
        title = (f"Closed this year ({len(closed_rows)})" if this_year == len(closed_rows)
                 else f"Closed ({len(closed_rows)}, {this_year} this year)")
        is_open = bool(folds.get("closed"))
        ids = [str(i) for t in closed_rows for i in t.get("trade_ids") or []]
        ltd_closed = fill_sum(data, "ltd", ids)
        fold_cells = {"daily": money_td(*fill_sum(data, "daily", ids)), "locked": money_td(*ltd_closed),
                      "ltd": money_td(*ltd_closed)}
        # the z columns read Z at entry | Z at exit in the fold (user, 2026-10-01): said on the fold's own row
        z_heads = [html.Td(html.Span(word, className="tk-sub", title=tip), className="tk-z")
                   for k, word, tip in (("z_entry", "Z at entry", "The z-score of the spread on its first fill"),
                                        ("z_now", "Z at exit", "The z-score of the spread on the day the trade went flat"))
                   if k in keys]
        body.append(html.Tr([
            html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"), title], className="l",
                    colSpan=lead - len(z_heads),
                    title="The fully flat trades: Spread at entry and Spread now are the entry and exit levels, Z at "
                          "entry and Z at exit the z-scores on those days, P&L since entry the final P&L, all of it "
                          "locked in")]
            + z_heads + [fold_cells.get(k) or html.Td("") for k in keys[lead:]],
            id=CLOSED_FOLD_ID, n_clicks=0, className="tk-fold"))
        if is_open:
            for t in sorted(closed_rows, key=lambda t: closed_on(data, t), reverse=True):
                add(t)
    return (html.Table([head(sort, s, tf.options_for(data.get("trades") or []), hidden),
                        html.Tbody(body), hidden_row(hidden, len(cols))], id=TABLE_ID,
                       className="book-table book-grid tk-table tk-trades"),
            [str(t.get("trade")) for t in shown])


def headline(data: dict, state: Optional[dict], risk: Optional[dict] = None) -> Optional[html.Span]:
    """The rows' MTD and YTD, inline in the card's title strip after "Trades", only while a filter is
    set (the header's are the whole book's); None otherwise. Nothing else sits between the header and
    the card (user, 2026-09-30: "net usd and flags ... alone in this row its clunky"): Gross, Net and
    the flags are on the table's total row, Daily and LTD there too (one place per number)."""
    s = tf.normal(state)
    if not view_filtered(s, hidden_columns(data, risk)):
        return None
    tot = totals(data, visible(data, s, risk))

    def money(label, k):
        v, n, r = tot[k]
        return html.Span(title=f"The {label} P&L of the rows showing (the header's is the whole book's)",
                         className="tk-strip-fig", children=[
            html.Span(label, className="tk-k"), " ", km_cell(v, reason=_lines(*r[:6])),
            marker(f"excl. {n}", _excl_hover(n, r), "marker--small") if n and v is not None else None])

    return html.Span([money("MTD", "mtd"), money("YTD", "ytd")], className="tk-strip-figs")


# --------------------------------------------------------------------------- the summary
# One card above the table (user, 2026-09-30, after the research app's Book: "a mix of this and what we
# already have ... and then the summary"): the whole book broken down by spread type, commodity family or
# commodity, each group's trades with their P&L today and since entry, the Book row equal to the top
# bar's Daily and LTD. Sums of the trade rows' own figures (display), never filtered (like the top bar);
# no gross or net and no figure the top bar already shows on its own.
SUMMARY_CARD_ID = "book-summary-card"
SUMMARY_SLOT_ID = "book-summary-slot"
SUMMARY_COUNT_ID = "book-summary-count"
SUMMARY_BY_ID = "book-summary-by"
# Sector replaced Commodity family on 2026-10-01 (user): its value stays "family" so a session that chose
# it keeps its choice.
BY_TYPE, BY_SECTOR, BY_COMMODITY = "type", "family", "commodity"
SUMMARY_OPTIONS = [{"label": "Spread type", "value": BY_TYPE}, {"label": "Sector", "value": BY_SECTOR},
                   {"label": "Commodity", "value": BY_COMMODITY}]
SUMMARY_ABOUT = ("The whole book, never filtered (like the top bar), broken down by spread type, sector or commodity: "
                 "each group's trades, open and closed, with their P&L today and since entry added up from the trades' "
                 "own rows. The Book row equals the top bar's Daily and LTD to the cent. A trade is never split across "
                 "groups: one holding two sectors is under its larger leg's, one holding two commodities is a group "
                 "of its own.")
SUMMARY_GROUP_TIPS = {
    BY_TYPE: "The trade's spread type, from its legs: calendar, cross-exchange, cross-product, mixed or outright.",
    BY_SECTOR: "The trade's sector, in a fixed order: base metals, precious metals, ferrous, energy, grains & softs, "
               "livestock, FX; a trade holding two is under its larger leg's.",
    BY_COMMODITY: "The commodities the trade holds, its currency hedges left out; a trade of two commodities is its "
                  "own group.",
}
METAL_NAMES = {"XAU": "Gold", "XAG": "Silver", "XPT": "Platinum", "XPD": "Palladium"}


def commodity_label(t: dict) -> str:
    """The commodities a trade holds, hedges left out ('Copper', 'Soybean / soymeal / soyoil'); a precious
    metal pair its metal, another currency pair 'FX'; 'Not recognised' when it holds nothing else."""
    legs = [leg for leg in t.get("legs") or [] if not leg.get("hedge")] or list(t.get("legs") or [])
    names: List[str] = []
    unknown = False
    for leg in legs:
        if leg.get("unrecognised"):
            unknown = True
            continue
        name = str(leg.get("commodity") or "")
        if not name and str(leg.get("product") or "") in NOTIONAL_PRODUCTS:
            base = str(leg.get("root_id") or leg.get("instrument_id") or "")[:3]
            name = METAL_NAMES.get(base, "FX")
        if name:
            names.append(name if name == "FX" or name.isupper() else name.lower())
    names = sorted(set(names))
    if not names:
        return "Not recognised" if unknown else "Other"
    return title_name(" / ".join(names))


def summary_group(t: dict, by: str) -> str:
    if t.get("pseudo"):
        return tf.UNASSIGNED
    if by == BY_SECTOR:
        return tf.trade_sector(t)[0]
    if by == BY_COMMODITY:
        return commodity_label(t)
    return tf.trade_type_label(t)


def summary_table(data: dict, by: str) -> html.Table:
    """Group | Trades | P&L today | P&L since entry, the largest P&L since entry first, the fills on
    no trade last; the Book row first, its figures the header's (the fills' own figures summed over
    every trade on file). No share of the book's P&L (user, 2026-09-30: with the book down overall a
    winning group showed a negative %)."""
    by = by if by in SUMMARY_GROUP_TIPS else BY_TYPE
    trades = data.get("trades") or []
    groups: Dict[str, List[dict]] = {}
    for t in trades:
        groups.setdefault(summary_group(t, by), []).append(t)
    all_ids = [str(i) for t in trades for i in t.get("trade_ids") or []]
    book_ltd = fill_sum(data, "ltd", all_ids)

    def ids_of(ts):
        return [str(i) for t in ts for i in t.get("trade_ids") or []]

    def count_td(ts):
        named = [t for t in ts if not t.get("pseudo")]
        if not named:
            n = len(ids_of(ts))
            return html.Td(html.Span(f"{n}", title=f"{_plural(n, 'fill')} on no trade"))
        n_open = sum(1 for t in named if t.get("status") == "open")
        return html.Td(html.Span(f"{len(named)}", title=_lines(f"{n_open} open, {len(named) - n_open} closed",
                                                                *(str(t.get("trade")) for t in named[:20]))))

    rows = []
    for label, ts in groups.items():
        ids = ids_of(ts)
        daily, ltd = fill_sum(data, "daily", ids), fill_sum(data, "ltd", ids)
        rows.append((label, ts, daily, ltd))
    if by == BY_TYPE:
        rows.sort(key=lambda r: (r[0] == tf.UNASSIGNED, r[3][0] is None, -abs(r[3][0] or 0.0), r[0]))
    else:
        # sectors in their fixed order, commodities by their sector then A to Z (user, 2026-10-01)
        def rank(r):
            ts = [t for t in r[1] if not t.get("pseudo")] or r[1]
            sector = r[0] if by == BY_SECTOR else min((tf.trade_sector(t)[0] for t in ts), key=tf.sector_rank)
            return (r[0] == tf.UNASSIGNED, tf.sector_rank(sector), r[0].lower())
        rows.sort(key=rank)
    body = [html.Tr([html.Td(html.Span("Book", title="Every trade on file; P&L today and since entry equal the top "
                                                     "bar's Daily and LTD to the cent"), className="l"),
                     count_td([t for t in trades if not t.get("pseudo")] or trades),
                     money_td(*fill_sum(data, "daily", all_ids)), money_td(*book_ltd)],
                    className="tk-total book-total")]
    for label, ts, daily, ltd in rows:
        body.append(html.Tr([html.Td(label, className="l"), count_td(ts), money_td(*daily, row=True),
                             money_td(*ltd, row=True)]))
    group_title = next(o["label"] for o in SUMMARY_OPTIONS if o["value"] == by)
    heads = ((group_title, "l", SUMMARY_GROUP_TIPS[by]), ("Trades", "", "The trades in the group, open and closed."),
             ("P&L today", "", "Today's P&L in USD of the group's trades, their hedges included."),
             ("P&L since entry", "", "The P&L since each trade opened, in USD, open plus locked in."))
    head = html.Thead(html.Tr([html.Th(about(t, tip, level="span"), className=cls or None) for t, cls, tip in heads]))
    return html.Table([head, html.Tbody(body)], className="book-table tk-table book-summary-table")


def summary_count(data: dict) -> html.Span:
    """'Open trades 17 · 3 to check' for the summary's title row (the checks as on the total row)."""
    named = [t for t in data.get("trades") or [] if not t.get("pseudo")]
    n_open = sum(1 for t in named if t.get("status") == "open")
    checks = _total_checks(totals(data, data.get("trades") or []))
    return html.Span([html.Span("Open trades", className="tk-k"), f" {n_open}",
                      html.Span(" · ", className="tk-total-sep") if checks else None, checks or None],
                     className="tk-headline book-summary-count",
                     title=f"{len(named)} trades on file: {n_open} open, {len(named) - n_open} closed")


def summary_parts(as_of: Optional[str], db_path, by: Optional[str] = None) -> Tuple[Any, Any]:
    """(the summary's table, its title-row count) for `as_of`, or (None, None) with no book."""
    if not as_of:
        return None, None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return None, None
    try:
        if trades_on_file(conn) == 0:
            return None, None
        data = gathered(conn, as_of)
        tbl, count = summary_table(data, by or BY_TYPE), summary_count(data)
    except Exception as exc:  # noqa: BLE001 -- the reason in the card, never a blank tab
        log.exception("Book: the summary could not be built for %s", as_of)
        tbl = html.Table(html.Tbody(html.Tr(html.Td(missing_cell(f"the summary could not be built "
                                                                 f"({type(exc).__name__}: {exc})"), className="l"))),
                         className="book-table tk-table")
        count = None
    finally:
        conn.close()
    return tidy(tbl), tidy(count) if count is not None else None


def summary_card(table_: Any = None, count: Any = None) -> html.Div:
    """The summary card: its title, the Break down by switch and the open-trades count in one row, the
    table under it (filled by `_summary`)."""
    return html.Div(id=SUMMARY_CARD_ID, className="book-card tk-card book-summary", children=[
        html.Div(className="tk-strip", children=[
            html.Div(className="tk-strip-lead", children=[
                about("Summary", SUMMARY_ABOUT, level="span", className="tk-title"),
                html.Span(className="book-view-switch", children=[
                    html.Label("Break down by", className="tk-k"),
                    dcc.RadioItems(id=SUMMARY_BY_ID, className="book-switch", options=SUMMARY_OPTIONS, value=BY_TYPE,
                                   inline=True, persistence=True, persistence_type="session")])]),
            html.Span(count, id=SUMMARY_COUNT_ID, className="tk-strip-figs")]),
        html.Div(table_, id=SUMMARY_SLOT_ID, className="tk-table-slot")])


ISSUE_KIND_WORDS = {"daily": "Daily", "ltd": "LTD"}


def issue_items(data: dict, risk: Optional[dict]) -> List[Any]:
    """The drawer's rows as (kind, where, reason): a fill with no figure under its trade ("Daily" and
    "LTD" of the same reason made one row by the drawer), the engine's notes, the risk caveats.
    Before the first Bloomberg price history is on file that is said once (`trade_filter.
    NO_HISTORY_TEXT`), never once per engine note that repeats it."""
    no_history = not (data.get("history") or {}).get("rows")
    items: List[Any] = [(kind, "", why) for kind, why in data.get("errors") or []]
    for n in data.get("notes") or []:
        if not (no_history and tf.is_no_history_reason(n)):
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
    if no_history:
        items.append(("Price history", "z, hedge %, carry", tf.NO_HISTORY_TEXT))
    elif risk is not None and not risk.get("available"):
        items.append(("Risk", "", f"z and hedge % unavailable: {risk.get('reason') or 'no price history'}"))
    return items


# --------------------------------------------------------------------------- CSV
def csv_frame(data: dict, state: Optional[dict], sort: Optional[dict], risk: Optional[dict]) -> pd.DataFrame:
    rrows = risk_rows(risk)
    zmap = (risk or {}).get("spread_z") or {}
    rows = []
    for t in sort_rows(data, visible(data, state, risk), sort, rrows, zmap):
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
            "z entry": trade_z(t, zmap.get(str(t.get("position_id") or "")), "z_entry"),
            "z now": trade_z(t, zmap.get(str(t.get("position_id") or "")), "z_now") if t.get("status") != "closed"
            else None,
            "z exit": trade_z(t, zmap.get(str(t.get("position_id") or "")), "z_now") if t.get("status") == "closed"
            else None,
            "Percentile": r.get("percentile"),
            "Hedge %": r.get("hedge_pct"), "Leg correlation": r.get("leg_correlation"),
            "Daily": fill_sum(data, "daily", ids)[0], "MTD": fill_sum(data, "mtd", ids)[0],
            "YTD": fill_sum(data, "ytd", ids)[0], "LTD": fill_sum(data, "ltd", ids)[0],
            "Open": trade_split(data, t, "open")[0], "Locked in": trade_split(data, t, "locked")[0],
            "Split reason": t.get("pnl_split_reason") or "", "Entry date": t.get("first_trade_date") or "",
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
    return html.Div([summary_card(*summary_parts(as_of, db_path)),
                     html.Div([html.Div(className="tk-strip", children=[p["title"], p["headline"]]),
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
TRADES_ABOUT = ("One row per trade (a PBRoot name); a trade of several spreads has a row per spread under it. "
                "The caret before a trade's name shows its legs, hedges last; the legs add up to their trade. Click "
                "elsewhere on a trade for its hedge, its facts, its level since entry and its links; the first row "
                "is the total of the rows showing, with what needs doing. Each heading sorts on its title; Trade, "
                "What it is and Action filter from the funnel in their heading, keeping or dropping whole trades.")
CONTRACTS_ABOUT = ("One row per contract held, netted across every trade that holds it, with its clearer: what the "
                   "broker statements show. Click a row for the trades holding it; the first row is the total of the "
                   "rows showing. Each heading sorts on its title; Contract, Clearer and Trades filter from the "
                   "funnel in their heading.")


def title(by_contract: bool = False):
    """The table strip's title of the view: "Trades" or "Contracts", its definition on hover."""
    return about("Contracts" if by_contract else "Trades", CONTRACTS_ABOUT if by_contract else TRADES_ABOUT,
                 level="span", className="tk-title")


def card_class(by_contract: bool = False) -> str:
    return CARD_CLASS + (" book-view--contract" if by_contract else "")


def layout(default_date: Optional[str] = None) -> html.Div:
    """The shell: the message slot, then (hidden with no book) the table card with its strip (the
    title, the view switch, the headline, the search, a Download CSV link) and the footer; the
    session stores and the risk poll."""
    from ui.tabs import book_contracts as bc
    return html.Div(className="book-tab", children=[
        html.Div(html.H2("Book", className="tab-title"), className="tab-header"),
        html.Div(id=BODY_ID, children=[message_box("Loading the book...")]),
        html.Div(id=CONTENT_ID, style=HIDDEN, children=[
            summary_card(),
            html.Div(id=CARD_ID, className=CARD_CLASS, children=[
                html.Div(className="tk-strip", children=[
                    html.Div(className="tk-strip-lead", children=[
                        html.Span(title(), id=TITLE_ID),
                        bc.switch(),
                        html.Span(id=HEADLINE_ID, className="tk-strip-slot")]),
                    tf.bar_slot(TAB),
                    # a small plain link, not a gold button (user, 2026-09-30; P&L's too since the same day, Risk keeps its button)
                    html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-link-button book-csv-link",
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

    @app.callback(Output(SUMMARY_SLOT_ID, "children"), Output(SUMMARY_COUNT_ID, "children"),
                  Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(SUMMARY_BY_ID, "value"))
    def _summary(as_of, _rev, by):
        tbl, count = summary_parts(as_of, get_db_path(), by)
        return compact(tbl) if tbl is not None else None, count

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
                  State(OPEN_STORE_ID, "data"), prevent_initial_call=True)
    def _toggle(_rows, current):
        if not _clicked():
            return dash.no_update
        trig = dash.ctx.triggered_id
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
