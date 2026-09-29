"""Book tab: the trade list (Phase G, 2026-09-29; CLAUDE.md "Screens redesign plan -> Phase G", the
design doc's "Table specs").

The trade is the unit: one row per trade name (Jason's PBRoot suffix, `JSHY10_ZNA1` and
`JSHY10.3_ZNA1` one trade), every leg and hedge under it, never split. Columns: Trade | Type |
What it is | Size | Entry | Now | z | Today | Daily | LTD | Next, and the flags at the row's right
edge. Above the table the shared filter bar and Group switch (`ui.tabs.trade_filter`, the same on
P&L and Risk, carried across the three tabs) and the headline of the rows showing. The table's
first row is the total ("Book · 6 trades", "Filtered · 3 of 6"), sticky, equal unfiltered to the
top bar's Daily, MTD and LTD to the cent; the fully flat trades are in the closed fold at the
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
    thread; the z column fills in a moment after the table): z, percentile, z at entry, the Size
    hover's hedge % and correlation. Research figures carry the "mock history" marker while the
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
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dcc, html

from ui import sample_book
from ui.revision import DATA_REVISION_ID
from ui.tabs import trade_filter as tf
from ui.tabs.formatting import (
    MINUS, MISSING, about, compact, contract_name, day_text, format_cell, full_signed, fx_name, is_fx_pair,
    issues_drawer, km_cell, km_text, marker, missing_cell, pct_text, plain_words, price_text, quoted_unit,
    sign_class, sum_known, z_text,
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
                          "cross-product, mixed or outright; an amber dot when the PBRoot says otherwise."),
    ("what", "What it is", "l", "The trade in plain words, with the share of its currency exposure hedged."),
    ("size", "Size", "", "The lots of each side (side A first); the value per side, the balance and the USD per "
                         "1-unit move of the level on hover."),
    ("entry", "Entry", "", "The level at entry: the fills' size-weighted level (a calendar far minus near in its "
                           "unit, a China-against-West pair the converted ratio China over foreign)."),
    ("now", "Now", "", "The same level at the latest official marks."),
    ("z", "z", "", "Where Now sits in one year of this exact level: (Now minus the mean) over the standard "
                   "deviation of the last 252 settlements (research history)."),
    ("today", "Today", "", "The level's move since the previous close, green when it helps the trade."),
    ("daily", "Daily", "", "Today's P&L in USD, every fill of the trade including its hedge."),
    ("ltd", "LTD", "", "The P&L since the trade opened, in USD."),
    ("next", "Next", "l", "The nearest key date of an open leg: first notice, last trade, option expiry or LME "
                          "prompt; red within 3 business days, amber within 10, grey ≈ when estimated."),
    ("flags", "", "", "Flags: unbalanced, hedge oversized, type mismatch, a leg without a price."),
)
NEXT_ABBR = {"first notice": "FN", "last trade": "LT", "option expiry": "Exp", "LME prompt": "Prompt",
             "expiry": "Exp", "prompt": "Prompt"}
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


def unit_words(unit: Optional[str]) -> str:
    """'$/bbl' for 'USD/bbl'; '' for a ratio (it has no unit)."""
    u = str(unit or "")
    return "" if u == "ratio" else u.replace("USD/", "$/")


def level_text(value: Any, level: dict, signed: bool = False) -> str:
    """A level in its own format: a ratio at 4 decimals, a price difference at the unit's tick."""
    v = _num(value)
    if v is None:
        return MISSING
    if level.get("mode") == "ratio" or level.get("unit") == "ratio":
        body = f"{abs(v):.4f}"
        sign = MINUS if v < 0 and float(body) else ("+" if signed and v > 0 and float(body) else "")
        return sign + body
    text = price_text(v, str(level.get("unit") or ""))
    return ("+" + text) if signed and v > 0 and not text.startswith(MINUS) and text.strip("0.,") else text


def _level_cell(value: Any, level: dict, reason: str, hover: str = "", estimated: bool = False,
                className: str = "") -> Any:
    v = _num(value)
    if v is None:
        return missing_cell(_lines(reason, hover) or "no level")
    unit = unit_words(level.get("unit"))
    return html.Span([("≈ " if estimated else "") + level_text(v, level),
                      html.Span(unit, className="cell-unit") if unit else None],
                     className=" ".join(c for c in (className, "cell-estimated" if estimated else "") if c) or None,
                     title=plain_words(hover) or None)


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
    cols = "trade_id, trade_date, quantity, price, instrument_id, product, pb_root, broker_price, broker_symbol"
    for sql in (f"SELECT {cols} FROM trades", "SELECT trade_id, trade_date, quantity, price, instrument_id, product, "
                                              "pb_root, '', '' FROM trades",
                "SELECT trade_id, trade_date, quantity, price, instrument_id, product, '', '', '' FROM trades"):
        try:
            return {str(r[0]): {"trade_date": str(r[1] or ""), "quantity": _num(r[2]), "price": _num(r[3]),
                                "instrument_id": str(r[4] or ""), "product": str(r[5] or ""), "pb_root": str(r[6] or ""),
                                "broker_price": str(r[7] or ""), "broker_symbol": str(r[8] or "")}
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
    data["trades"] = trades
    data["research"] = tf.research_source()
    return data


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
             className: str = "") -> html.Td:
    """A k / m money cell with its 'excl. N' marker (the reasons on hover); the dash with every
    reason when nothing is known."""
    if total is None:
        return html.Td(missing_cell(_lines(*list(dict.fromkeys(reasons))[:8]) or "no figure"), className=className or None)
    return html.Td([km_cell(total, hover=hover),
                    marker(f"excl. {n_excl}", _excl_hover(n_excl, reasons), "marker--small") if n_excl else None],
                   className=className or None)


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
    fills = data.get("fills") or {}
    return max(((fills.get(str(i)) or {}).get("trade_date", "") for i in t.get("trade_ids") or []), default="")


def risk_rows(risk: Optional[dict]) -> Dict[str, dict]:
    return {str(r.get("trade")): r for r in (risk or {}).get("trades") or []}


# --------------------------------------------------------------------------- the cells
def _type_td(t: dict) -> html.Td:
    code = tf.type_code(t)
    mismatch = str(t.get("type_mismatch") or "")
    words = tf.type_label(code, short=True) if not t.get("pseudo") else MISSING
    hover = _lines(t.get("type_note") or "", mismatch and f"Type mismatch: {mismatch}")
    return html.Td([html.Span(words, title=plain_words(hover) or None),
                    html.Span("●", className="tk-flag-dot", title=plain_words(mismatch)) if mismatch else None],
                   className="l")


def coverage_words(hedge: dict) -> str:
    cov = _num((hedge or {}).get("coverage"))
    if cov is None:
        return ""
    return f"{hedge.get('currency') or 'CNH'} {pct_text(cov)} hedged"


def _what_td(t: dict) -> html.Td:
    what = str(t.get("what_it_is") or "")
    cov = coverage_words(t.get("hedge") or {})
    legs = [str(leg.get("name") or "") for leg in t.get("legs") or []]
    hover = _lines(what, *(f"· {n}" for n in legs if n))
    return html.Td([html.Span(what, title=plain_words(hover) or None),
                    html.Span(f" · {cov}", className="tk-sub") if cov else None], className="l tk-what")


def size_text(t: dict) -> Tuple[str, str]:
    """(the Size cell, why when blank)."""
    size = t.get("size") or {}
    sides = size.get("sides") or []
    if len(sides) < 2:
        return "", str(size.get("reason") or "no size")
    a, b = (abs(_num(s.get("lots")) or 0.0) for s in sides[:2])
    if not a and not b:
        return "", str(size.get("reason") or "nothing open")
    if not a or not b:
        n = a or b
        text = f"{n:,.0f} {'lot' if n == 1 else 'lots'}"
    else:
        text = str(size.get("ratio_text") or f"{a:,.0f} : {b:,.0f}")
    subs = t.get("sub_spreads") or []
    if tf.type_code(t) == "MIXED" and len(subs) > 1:
        text += f" +{len(subs) - 1} more"
    return text, ""


def size_hover(t: dict, r: Optional[dict], research: dict) -> str:
    size, level = t.get("size") or {}, t.get("level") or {}
    lines = []
    for leg in t.get("legs") or []:
        if leg.get("status") == "open" and not leg.get("hedge"):
            lines.append(f"{leg.get('name')}: {_num(leg.get('lots')) or 0:+,.0f} lots")
    for s in size.get("sides") or []:
        fill, mark = _num(s.get("value_fill_usd")), _num(s.get("value_mark_usd"))
        metal = _num(s.get("physical"))
        lines.append(f"{s.get('label')}: {format_cell(abs(fill)) + ' USD at the fill' if fill is not None else 'value at the fill not known'}"
                     + (f", {format_cell(abs(mark))} USD at the mark" if mark is not None else "")
                     + (f"; {abs(metal):,.0f} {s.get('physical_unit') or ''}" if metal else ""))
    gap = _num(size.get("value_gap"))
    if gap is not None:
        lines.append(f"Balance: the sides differ by {pct_text(gap)} of their value "
                     f"({'unbalanced' if size.get('unbalanced') else 'balanced'} beyond {pct_text(size.get('tolerance') or 0.1)})")
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


def _size_td(t: dict, r: Optional[dict], research: dict) -> html.Td:
    text, why = size_text(t)
    if not text:
        return html.Td(missing_cell(why))
    return html.Td(html.Span(text, title=plain_words(size_hover(t, r, research))))


def _entry_td(t: dict) -> html.Td:
    level = t.get("level") or {}
    fills = [f"{leg.get('name')}: avg fill {price_text(leg.get('avg_fill'))}" for leg in t.get("legs") or []
             if not leg.get("hedge") and _num(leg.get("avg_fill")) is not None]
    hover = _lines(f"Entry: first fill {t.get('first_trade_date') or ''}", *fills,
                   (level.get("sources") or {}).get("entry", ""))
    return html.Td(_level_cell(level.get("entry"), level, str(level.get("entry_reason") or level.get("reason") or ""),
                               hover))


def _now_td(t: dict) -> html.Td:
    level = t.get("level") or {}
    carry = ""
    if tf.type_code(t) == "CALENDAR" or level.get("source") == "calendar":
        c = _num(t.get("carry_per_month"))
        carry = (f"Carry per month: {full_signed(c)} USD (the legs' roll-down on one curve, research)"
                 if c is not None else f"Carry per month: not summed ({t.get('carry_reason') or 'not given'})")
    hover = _lines((level.get("sources") or {}).get("now", ""), level.get("note") or "", carry)
    return html.Td(_level_cell(level.get("now"), level, str(level.get("now_reason") or level.get("reason") or ""),
                               hover, estimated=_estimated_level(t)))


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
    return html.Td([html.Span(z_text(z), className="tk-bold" if abs(z) >= 2 else None, title=plain_words(hover)),
                    tf.research_mark(research)])


def _today_td(t: dict) -> html.Td:
    level = t.get("level") or {}
    ch = _num(level.get("change"))
    if ch is None:
        return html.Td(missing_cell(level.get("change_reason") or level.get("reason") or "no move"))
    usd = _num(level.get("usd_per_unit"))
    effect = ch * usd if usd is not None else None
    cls = sign_class(effect) if effect is not None else ""
    hover = _lines(f"Since the {level.get('prev_date') or 'previous'} close ({level_text(level.get('prev'), level)})",
                   f"Worth {full_signed(effect)} USD to the trade" if effect is not None else "",
                   "Move in σ: not given by the engine yet")
    return html.Td(html.Span(level_text(ch, level, signed=True), className=cls or None, title=plain_words(hover)))


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
    hover = _lines(f"{event} of {nx.get('contract_id') or ''} on {nx.get('date')}"
                   + (f", {bd} business days to go" if bd is not None else ""),
                   f"engine level {level}" if level else "", nx.get("reason") or "")
    if est:
        return f"≈ {text}", "cell-estimated", hover
    return text, {"RED": "cell-red", "EXPIRED": "cell-red", "AMBER": "cell-amber"}.get(level, ""), hover


def _next_td(t: dict, as_of: str) -> html.Td:
    text, cls, hover = next_parts(t, as_of)
    if not text:
        return html.Td(missing_cell(hover), className="l")
    return html.Td(html.Span(text, className=cls or None, title=plain_words(hover)), className="l")


def _flags_td(t: dict) -> html.Td:
    flags = t.get("flags") or []
    if not flags:
        return html.Td("")
    hover = _lines(*(f"{f.get('label')}: {f.get('sentence')}" for f in flags))
    return html.Td(html.Span(f"● {len(flags)}", className="tk-flags", title=plain_words(hover)))


# --------------------------------------------------------------------------- rows
def trade_tr(data: dict, t: dict, r: Optional[dict], ready: bool, opened: bool) -> html.Tr:
    ids = [str(i) for i in t.get("trade_ids") or []]
    daily = fill_sum(data, "daily", ids)
    ltd = fill_sum(data, "ltd", ids)
    mtd, ytd = fill_sum(data, "mtd", ids), fill_sum(data, "ytd", ids)
    name = str(t.get("trade") or "")
    pbs = t.get("pb_roots") or []
    name_hover = _lines(f"PBRoot: {', '.join(pbs)}" if pbs else "", f"{_plural(len(ids), 'fill')}")
    cells = [
        html.Td([html.Span("▾ " if opened else "▸ ", className="tk-chev"),
                 html.Span(name, className="tk-name", title=name_hover or None)], className="l"),
        _type_td(t), _what_td(t), _size_td(t, r, data.get("research") or {}), _entry_td(t), _now_td(t),
        _z_td(r, ready, data.get("research") or {}), _today_td(t),
        money_td(*daily, hover=_split_hover(t)),
        money_td(*ltd, hover=_lines(f"MTD {km_text(mtd[0])}", f"YTD {km_text(ytd[0])}")),
        _next_td(t, data["as_of"]), _flags_td(t),
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
        return f"Filtered · {len(shown_named)} of {len(named)}", hover
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
    return out


def total_tr(data: dict, shown: Sequence[dict], filtered: bool) -> html.Tr:
    tot = totals(data, shown)
    label, hover = _total_label(shown, data.get("trades") or [], filtered)
    g, g_n, g_r = tot["gross"]
    nx = tot["next"]
    nx_cell: Any = ""
    if nx:
        text, cls, nh = next_parts(nx["trade"], data["as_of"])
        nx_cell = html.Span(f"{nx['trade'].get('trade')} {text}", className=cls or None, title=plain_words(nh))
    if not filtered:
        hover = _lines(hover, "Daily, MTD and LTD equal the top bar's to the cent")
    return html.Tr([
        html.Td(html.Span(label, title=hover), className="l"), html.Td(""), html.Td(""),
        html.Td([km_cell(g, hover="gross USD of the rows showing", signed=False, colour=False) if g is not None
                 else missing_cell("no gross"), html.Span(" gross", className="cell-unit"),
                 marker(f"excl. {g_n}", _lines(*g_r), "marker--small") if g_n else None]),
        html.Td(""), html.Td(""), html.Td(""), html.Td(""),
        money_td(*tot["daily"]), money_td(*tot["ltd"], hover=f"MTD {km_text(tot['mtd'][0])}"),
        html.Td(nx_cell, className="l"), html.Td(""),
    ], className="tk-total book-total")


def group_tr(data: dict, label: str, rows: Sequence[dict], folded: bool) -> html.Tr:
    ids = [str(i) for t in rows for i in t.get("trade_ids") or []]
    return html.Tr([
        html.Td([html.Span("▸ " if folded else "▾ ", className="tk-chev"), html.Span(label),
                 html.Span(f" · {_plural(len(rows), 'trade')}", className="tk-sub")], className="l", colSpan=8),
        money_td(*fill_sum(data, "daily", ids)), money_td(*fill_sum(data, "ltd", ids)), html.Td(""), html.Td(""),
    ], id={"type": GROUP_ROW_TYPE, "idx": label}, n_clicks=0, className="tk-group")


# --------------------------------------------------------------------------- sort
SORTABLE = {"trade", "type", "what", "size", "entry", "now", "z", "today", "daily", "ltd", "next"}


def sort_value(data: dict, t: dict, key: str, r: Optional[dict]) -> Any:
    level = t.get("level") or {}
    if key == "trade":
        return str(t.get("trade") or "").lower()
    if key == "type":
        code = tf.type_code(t)
        return tf.TYPE_ORDER.index(code) if code in tf.TYPE_ORDER else 99
    if key == "what":
        return str(t.get("what_it_is") or "").lower()
    if key == "size":
        return _num(t.get("gross_usd"))
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


def head(sort: Optional[dict]) -> html.Thead:
    ths = []
    for key, title, cls, tip in COLUMNS:
        arrow = ""
        if (sort or {}).get("key") == key:
            arrow = " ▼" if (sort or {}).get("dir") != "asc" else " ▲"
        if key in SORTABLE:
            inner = html.Span([title, html.Span(arrow, className="book-sort-arrow")],
                              id={"type": SORT_TYPE, "idx": key}, n_clicks=0, className="tk-sort")
        else:
            inner = title
        ths.append(html.Th(inner, className=" ".join(c for c in (cls, "tk-sortable" if key in SORTABLE else "") if c)
                           or None, title=tip))
    return html.Thead(html.Tr(ths))


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


def leg_tr(data: dict, leg: dict, level_note: str, prev_rows: Dict[str, dict]) -> html.Tr:
    ids = [str(i) for i in leg.get("trade_ids") or []]
    hedge = bool(leg.get("hedge"))
    unit = _leg_unit(data, leg)
    lots = _num(leg.get("lots"))
    product = str(leg.get("product") or "")
    if lots is None:
        lots_text = MISSING
    elif product.startswith("FX"):
        lots_text = f"{km_text(lots)} {leg.get('root_id') or ''}".strip()
    else:
        lots_text = (MINUS if lots < 0 else "+") + f"{abs(lots):,.0f}" if lots else "0"
    lots_hover, fill_hover = _fills_hover(data, leg)
    rows = _df_rows(data, ids)
    spot = next((_num(s) for s in rows.get("spot", []) if _num(s) is not None), None) if not rows.empty else None
    value = _num(leg.get("value_usd"))
    value_cell = (html.Span(full_signed(value), title=f"spot {spot:g} USD per {leg.get('currency') or 'unit'}"
                            if spot is not None and leg.get("currency") not in ("", "USD") else None)
                  if value is not None else missing_cell(leg.get("value_reason") or "no value"))
    fill = _num(leg.get("avg_fill"))
    fill_cell = (html.Span(price_text(fill, unit, fill), title=plain_words(fill_hover) or None) if fill is not None
                 else missing_cell(leg.get("avg_fill_reason") or "no open lots"))
    mark = _num(leg.get("mark"))
    prev = None
    for t in leg.get("open_trade_ids") or ids:
        pr = prev_rows.get(str(t))
        if pr is not None and _num(pr.get("mark")) is not None:
            prev = _num(pr.get("mark"))
            break
    est = is_estimated_mark(leg.get("mark_source"))
    mark_hover = _lines(f"Source: {plain_words(leg.get('mark_source') or '')}",
                        f"Previous close: {price_text(prev, unit, fill)}" if prev is not None else "Previous close: not on file",
                        level_note if not hedge else "", "Mark time: not given by the engine yet")
    mark_cell = (html.Span(("≈ " if est else "") + price_text(mark, unit, fill), title=plain_words(mark_hover),
                           className="cell-estimated" if est else None)
                 if mark is not None else missing_cell(leg.get("mark_reason") or "no mark"))
    daily = fill_sum(data, "daily", ids)
    ltd = fill_sum(data, "ltd", ids)
    local = None
    if not rows.empty and leg.get("currency") not in ("", "USD"):
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
    roll_cell = ([html.Span(full_signed(roll), title=plain_words(roll_hover) or None),
                  tf.research_mark(data.get("research") or {})] if roll is not None
                 else missing_cell(leg.get("roll_down_reason") or "no roll-down"))
    side = str(leg.get("side") or "")
    return html.Tr([
        html.Td(html.Span(str(leg.get("name") or leg.get("contract_id") or "") + (" (hedge)" if hedge else ""),
                          title=plain_words(_leg_hover(data, leg))), className="l"),
        html.Td(side, className="l"),
        html.Td(html.Span(lots_text, title=plain_words(lots_hover))),
        html.Td(value_cell), html.Td(fill_cell), html.Td(mark_cell),
        full_td(daily), full_td(ltd, ltd_hover), html.Td(roll_cell),
    ], className="tk-leg" + (" tk-leg--hedge" if hedge else "") + (" tk-leg--flat" if side == "flat" else ""))


def _leg_head() -> html.Thead:
    return html.Thead(html.Tr([html.Th(title, className=cls or None) for _k, title, cls in LEG_COLUMNS]))


def _sub_tr(text: str, ids: Sequence[str], data: dict) -> html.Tr:
    return html.Tr([html.Td(text, className="l tk-subhead", colSpan=6),
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


def panel_legs(data: dict, t: dict, prev_rows: Dict[str, dict]) -> html.Table:
    level = t.get("level") or {}
    note = str(level.get("note") or "")
    legs = _leg_order(t)
    body: List[Any] = []
    subs = t.get("sub_spreads") or []
    if tf.type_code(t) == "MIXED" and len(subs) > 1:
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
            body.extend(leg_tr(data, leg, note, prev_rows) for leg in mine)
        rest = [leg for leg in legs if not leg.get("hedge") and leg.get("contract_id") not in taken]
        if rest:
            body.append(_sub_tr("Other legs", [str(i) for leg in rest for i in leg.get("trade_ids") or []], data))
            body.extend(leg_tr(data, leg, note, prev_rows) for leg in rest)
    else:
        body.extend(leg_tr(data, leg, note, prev_rows) for leg in legs if not leg.get("hedge"))
    hedges = [leg for leg in legs if leg.get("hedge")]
    if hedges:
        body.append(_sub_tr("Hedge", [str(i) for leg in hedges for i in leg.get("trade_ids") or []], data))
        body.extend(leg_tr(data, leg, note, prev_rows) for leg in hedges)
    ids = [str(i) for i in t.get("trade_ids") or []]
    carry = _num(t.get("carry_per_month"))
    carry_cell = (html.Span(full_signed(carry), title=plain_words(tf.research_words(data.get("research") or {})) or None)
                  if carry is not None else html.Span("not summed", className="tk-sub",
                                                      title=plain_words(str(t.get("carry_reason") or "not given"))))
    gross = _num(t.get("gross_usd"))
    foot = [html.Tr([html.Td("Trade total", className="l", colSpan=3),
                     html.Td(format_cell(gross) if gross is not None else missing_cell(t.get("notional_reason") or "no gross"),
                             title="gross USD value of the open legs"),
                     html.Td(""), html.Td(""),
                     html.Td(full_signed(fill_sum(data, "daily", ids)[0])),
                     html.Td(full_signed(fill_sum(data, "ltd", ids)[0])), html.Td(carry_cell)])]
    return html.Table([_leg_head(), html.Tbody(body), html.Tfoot(foot)], className="book-table tk-table tk-legs")


def panel_facts(t: dict) -> html.Div:
    """The lines under the legs: USD per 1-unit move of the level, the hedge line."""
    level, hedge = t.get("level") or {}, t.get("hedge") or {}
    items: List[Any] = []
    usd, words = _usd_per_move(level)
    items.append(html.Span([html.Span("Level ", className="tk-k"),
                            f"{words}: {format_cell(usd)}" if usd is not None else f"no USD per move ({words})"]))
    if hedge.get("present"):
        cov = _num(hedge.get("coverage"))
        exp, hed = _num(hedge.get("exposure_usd")), _num(hedge.get("hedge_usd"))
        if cov is not None:
            amber = cov < 0.8 or cov > 1.5
            items.append(html.Span([html.Span("Hedge ", className="tk-k"),
                                    f"{hedge.get('currency')}: China legs {format_cell(exp)} USD · hedge {format_cell(hed)} "
                                    "USD · coverage ", html.Span(pct_text(cov), className="cell-amber" if amber else None)],
                                   title=plain_words(hedge.get("direction_note") or "") or None))
        else:
            size = f" {format_cell(hed)} USD" if hed is not None else ""
            items.append(html.Span([html.Span("Hedge ", className="tk-k"),
                                    f"{hedge.get('currency') or ''}{size}: {hedge.get('reason') or 'coverage not read'}"]))
    return html.Div(items, className="tk-facts")


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
            annotations.append({"xref": "paper", "x": 0, "y": entry, "text": "entry", "showarrow": False,
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
                           "hovertemplate": "roll %{x|%d %b}: %{text}<extra></extra>"})
        ytitle = unit or "ratio"
    else:
        ys = [_num(p.get("ltd_usd")) for p in pts]
        traces.append({"x": xs, "y": ys, "type": "scatter", "mode": "lines", "name": "LTD",
                       "line": {"color": "#0f1f3d", "width": 2},
                       "hovertemplate": "%{x|%d %b %Y}: LTD %{y:$,.0f}<extra></extra>"})
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
        parts.append(html.Div([html.Div(f"{f.get('label')}: {f.get('sentence')}", className="tk-flag-line")
                               for f in flags], className="tk-flag-lines"))
    if t.get("pseudo"):
        parts.append(pseudo_fills(data, t))
    else:
        prev_day = str((t.get("level") or {}).get("prev_date") or ((t.get("pnl") or {}).get("ref_dates") or {}).get("daily") or "")
        prev_rows: Dict[str, dict] = {}
        if prev_day:
            try:
                from ui.tabs.blotter_pricing import priced_value_book
                prev_rows = {str(r["trade_id"]): r for r in priced_value_book(conn, prev_day)[0].to_dict("records")}
            except Exception:  # noqa: BLE001 -- the hover says the previous close is not on file
                prev_rows = {}
        parts.append(html.Div(className="tk-panel-body", children=[
            html.Div([panel_legs(data, t, prev_rows), panel_facts(t)], className="tk-panel-legs"),
            panel_chart(conn, data, t)]))
    n = len(t.get("trade_ids") or [])
    parts.append(html.Div(className="tk-links", children=[
        tf.link(f"See fills ({n})", "blotter", name, "book-fills", "Open the Blotter filtered to this trade's fills"),
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
def visible(data: dict, state: Optional[dict]) -> List[dict]:
    return tf.apply(data.get("trades") or [], state)


def table(conn: sqlite3.Connection, data: dict, state: Optional[dict], sort: Optional[dict], opened: Sequence[str],
          folds: Optional[dict], risk: Optional[dict]) -> Tuple[html.Table, List[str]]:
    s = tf.normal(state)
    shown = visible(data, s)
    ready = risk is not None
    rrows = risk_rows(risk)
    opened_set = set(opened or [])
    folds = folds or {}
    folded = set(folds.get("groups") or [])
    body: List[Any] = [total_tr(data, shown, tf.is_filtered(s))]
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

    group = s["group"]
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
            html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"), title], className="l", colSpan=8,
                    title="The fully flat trades: Entry and Now are the entry and exit levels, LTD the final P&L"),
            money_td(*fill_sum(data, "daily", ids)), money_td(*fill_sum(data, "ltd", ids)), html.Td(""), html.Td("")],
            id=CLOSED_FOLD_ID, n_clicks=0, className="tk-fold"))
        if is_open:
            for t in sorted(closed_rows, key=lambda t: closed_on(data, t), reverse=True):
                add(t)
    return (html.Table([head(sort), html.Tbody(body)], id=TABLE_ID, className="book-table book-grid tk-table"),
            [str(t.get("trade")) for t in shown])


def headline(data: dict, state: Optional[dict]) -> html.Div:
    s = tf.normal(state)
    shown = visible(data, s)
    tot = totals(data, shown)
    named = [t for t in data.get("trades") or [] if not t.get("pseudo")]
    shown_named = [t for t in shown if not t.get("pseudo")]
    n_open = sum(1 for t in named if t.get("status") == "open")
    count = (f"{len(shown_named)} of {len(named)}" if tf.is_filtered(s) else f"{len(named)} · {n_open} open")

    def money(k):
        v, n, r = tot[k]
        return html.Span([km_cell(v, reason=_lines(*r[:6])),
                          marker(f"excl. {n}", _excl_hover(n, r), "marker--small") if n and v is not None else None])

    def notional(k):
        v, n, r = tot[k]
        return html.Span([km_cell(v, reason=_lines(*r[:6]), colour=False, signed=(k == "net")),
                          marker(f"excl. {n}", _lines(*r[:8]), "marker--small") if n and v is not None else None])

    nx = tot["next"]
    nx_value: Any = MISSING
    nx_hover = "no key date on the rows showing"
    if nx:
        n = nx["next"]
        bd = n.get("business_days")
        event = str(n.get("event") or "")
        est = "≈ " if n.get("estimated") else ""
        nx_value = html.Span(f"{est}{nx['trade'].get('trade')} {event} " +
                             (f"in {bd} {'day' if bd == 1 else 'days'}" if bd is not None else f"on {day_text(n.get('date'))}"),
                             className=next_parts(nx["trade"], data["as_of"])[1] or None)
        nx_hover = next_parts(nx["trade"], data["as_of"])[2]
    flags = tot["flags"]
    return tf.headline([
        ("Trades", count, "the trades showing (a trade is a PBRoot name); the top bar is always the whole book"),
        ("Daily", money("daily"), "today's P&L of the rows showing"),
        ("MTD", money("mtd"), "the month's P&L of the rows showing"),
        ("LTD", money("ltd"), "the P&L since each trade opened, rows showing"),
        ("Gross USD", notional("gross"), "the rows' gross USD notional, summed"),
        ("Net USD", notional("net"), "the rows' net USD notional, summed"),
        ("Next", nx_value, nx_hover),
        ("Flags", html.Span(_plural(flags, "flag"), className="cell-amber" if flags else None),
         "flags on the rows showing: unbalanced, hedge oversized, type mismatch, a leg without a price"),
    ])


def issue_items(data: dict, risk: Optional[dict]) -> List[Any]:
    items: List[Any] = list(data.get("errors") or [])
    for n in data.get("notes") or []:
        items.append(("Trades", str(n)))
    for key in ("daily", "ltd"):
        view = (data.get("views") or {}).get(key)
        if view is None:
            continue
        bad = view.rows[view.rows["value"].isna()] if not view.rows.empty else view.rows
        for tid, why in zip(bad.get("trade_id", []), bad.get("reason", [])):
            items.append((f"{key.upper()} {tid}", str(why or "no figure")))
    if risk is not None and not risk.get("available"):
        items.append(("Risk", f"z and hedge % unavailable: {risk.get('reason') or 'no risk history'}"))
    src = data.get("research") or {}
    if src.get("kind") != "real":
        items.append(("Research", src.get("note") or "the research history is not verified as real"))
    return items


# --------------------------------------------------------------------------- CSV
def csv_frame(data: dict, state: Optional[dict], sort: Optional[dict], risk: Optional[dict]) -> pd.DataFrame:
    rrows = risk_rows(risk)
    rows = []
    for t in sort_rows(data, visible(data, state), sort, rrows):
        ids = [str(i) for i in t.get("trade_ids") or []]
        level = t.get("level") or {}
        r = rrows.get(str(t.get("trade"))) or {}
        nx = t.get("next") or {}
        rows.append({
            "Trade": t.get("trade"), "Status": t.get("status"), "Type": tf.type_label(tf.type_code(t)),
            "Commodity": tf.family_label(t), "What it is": t.get("what_it_is"), "Size": size_text(t)[0],
            "Gross USD": t.get("gross_usd"), "Net USD": t.get("net_usd"),
            "Value gap": (t.get("size") or {}).get("value_gap"),
            "Entry": level.get("entry"), "Now": level.get("now"), "Previous close level": level.get("prev"),
            "Today": level.get("change"), "Level unit": level.get("unit"),
            "USD per 1 unit of the level": level.get("usd_per_unit"),
            "z": r.get("z"), "Percentile": r.get("percentile"), "z at entry": r.get("z_entry"),
            "Hedge %": r.get("hedge_pct"), "Leg correlation": r.get("leg_correlation"),
            "Daily": fill_sum(data, "daily", ids)[0], "MTD": fill_sum(data, "mtd", ids)[0],
            "YTD": fill_sum(data, "ytd", ids)[0], "LTD": fill_sum(data, "ltd", ids)[0],
            "Carry per month": t.get("carry_per_month"), "Hedge coverage": (t.get("hedge") or {}).get("coverage"),
            "Next event": nx.get("event"), "Next date": nx.get("date"), "Next estimated": nx.get("estimated"),
            "Flags": "; ".join(f"{f.get('label')}: {f.get('sentence')}" for f in t.get("flags") or []),
            "PBRoot": ", ".join(t.get("pb_roots") or []), "Fills": len(ids), "Trade ids": " ".join(ids),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- render
def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render_parts(as_of: Optional[str], db_path, state: Optional[dict] = None, sort: Optional[dict] = None,
                 opened: Sequence[str] = (), folds: Optional[dict] = None, wait_risk: bool = False) -> dict:
    """{body, shown (bool), headline, table, foot, names, risk_ready} for `as_of`."""
    out = {"body": None, "shown": False, "headline": None, "table": None, "foot": None, "names": [], "risk_ready": True}
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
        data = gather(conn, as_of)
        from ui.tabs.blotter_pricing import shared_trade_risk
        try:
            risk = shared_trade_risk(conn, as_of, wait=wait_risk)
        except Exception as exc:  # noqa: BLE001 -- the z column says why
            log.exception("Book: trade risk failed for %s", as_of)
            risk = {"available": False, "reason": f"{type(exc).__name__}: {exc}", "trades": []}
        tbl, names = table(conn, data, state, sort, opened, folds, risk)
        out.update(shown=True, headline=headline(data, state), table=tbl, names=names, risk_ready=risk is not None)
        prepull = None if data.get("has_marks") else html.Div(PREPULL_TEXT, className="book-prepull")
        out["foot"] = html.Div([prepull, issues_drawer(issue_items(data, risk))], className="tk-foot")
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("Book could not be built for %s", as_of)
        out["body"] = html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The book could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    return out


def render(as_of: Optional[str], db_path, state: Optional[dict] = None) -> html.Div:
    """The whole body for a direct render (the risk figures waited for)."""
    p = render_parts(as_of, db_path, state, wait_risk=True)
    if not p["shown"]:
        return html.Div([p["body"]])
    return html.Div([p["headline"], html.Div(p["table"], className="book-card book-main tk-card"), p["foot"]])


def options_of(as_of: str, db_path) -> Dict[str, List[dict]]:
    """The filter bar's choices on the Book (the trades present)."""
    conn = _open(db_path)
    try:
        if trades_on_file(conn) == 0:
            return {}
        return tf.options_for(gather(conn, as_of).get("trades") or [])
    finally:
        conn.close()


# --------------------------------------------------------------------------- layout and callbacks
def layout(default_date: Optional[str] = None) -> html.Div:
    """The shell: the message slot, then (hidden with no book) the filter bar, the headline, the
    table card with its strip (Expand all, Collapse all, Download CSV) and the footer; the session
    stores and the risk poll."""
    return html.Div(className="book-tab", children=[
        html.Div(id=BODY_ID, children=[message_box("Loading the book...")]),
        html.Div(id=CONTENT_ID, style=HIDDEN, children=[
            tf.bar_slot(TAB),
            html.Div(id=HEADLINE_ID),
            html.Div(className="book-card book-main tk-card", children=[
                html.Div(className="tk-strip", children=[
                    about("Trades", "One row per trade (a PBRoot name). Click a row for its legs, its level since entry "
                                    "and its links; the first row is the total of the rows showing.",
                          level="span", className="tk-title"),
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
        dcc.Store(id=SHOWN_STORE_ID),
        dcc.Store(id=RISK_READY_ID),
        dcc.Interval(id=RISK_POLL_ID, interval=1500, n_intervals=0, disabled=True),
    ])


build_layout = layout


def _clicked() -> bool:
    trig = dash.ctx.triggered or []
    return bool(trig and trig[0].get("value"))


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    tf.register_bar(app, TAB, get_db_path, options_of)

    @app.callback(
        Output(BODY_ID, "children"), Output(CONTENT_ID, "style"), Output(HEADLINE_ID, "children"),
        Output(TABLE_SLOT_ID, "children"), Output(FOOT_ID, "children"), Output(SHOWN_STORE_ID, "data"),
        Output(RISK_POLL_ID, "disabled"),
        Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(tf.STORE_ID, "data"),
        Input(SORT_STORE_ID, "data"), Input(OPEN_STORE_ID, "data"), Input(FOLD_STORE_ID, "data"),
        Input(RISK_READY_ID, "data"),
    )
    def _render(as_of, _rev, state, sort, opened, folds, _ready):
        p = render_parts(as_of, get_db_path(), state, sort, opened or [], folds)
        if not p["shown"]:
            return p["body"], HIDDEN, None, None, None, [], True
        return (None, {}, compact(p["headline"]), compact(p["table"]), compact(p["foot"]), p["names"],
                p["risk_ready"])

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
                  State(OPEN_STORE_ID, "data"), State(SHOWN_STORE_ID, "data"), prevent_initial_call=True)
    def _toggle(_rows, _all, _none, current, shown):
        if not _clicked():
            return dash.no_update
        trig = dash.ctx.triggered_id
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
                  State(tf.STORE_ID, "data"), State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, state, sort):
        if not n_clicks or not as_of:
            return dash.no_update
        conn = _open(get_db_path())
        try:
            from ui.tabs.blotter_pricing import shared_trade_risk
            data = gather(conn, as_of)
            frame = csv_frame(data, state, sort, shared_trade_risk(conn, as_of, wait=False))
        finally:
            conn.close()
        return dcc.send_data_frame(frame.to_csv, f"book_{as_of}.csv", index=False)

    app.clientside_callback(_OPEN_UPLOAD_JS, Output(EMPTY_UPLOAD_SINK_ID, "data"),
                            Input({"type": EMPTY_UPLOAD_TYPE, "idx": ALL}, "n_clicks"), prevent_initial_call=True)
