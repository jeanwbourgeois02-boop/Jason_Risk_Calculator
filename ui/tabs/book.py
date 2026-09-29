"""Book tab: the app's home. "What is my book, and what needs me?" Rebuilt on 2026-09-29 (the
layout approved by the user against the research app's Book, `../Commodity Dashboard/rvapp/app/book.py`:
tiles, one summary table with a break-down switch, a flat Open trades list). Vocabulary (user,
2026-09-28): a **spread type** is cross exchange / cross product / term structure; Jason's PBRoot
name (COPAR3, CATTLE, `trades.strategy`) is his **trade**, never called a strategy on screen (the
code keeps `strategy` as the field name of the PBRoot label). Since 2026-09-29 (user: "i want
everything to be in a table"), top to bottom, at most two cards under the title:

  1. Needs you (`needs_card`, only when non-empty): a small table, Level | What | When | Go, one row
     per first notice, last trade, expiry or prompt at EXPIRED / RED / AMBER with a real date and
     per position RED / AMBER on liquidity (research data, → Risk).
  2. The book card (`book_card`): the strip (`grid_strip`: Group by Spread type | Commodity |
     Instrument | Sector | Trade, the search, Download CSV; the four multi-selects, "+ figures" for
     the comparison boxes, Expand all / Collapse all, Clear filters, the meta), then one grouped
     table (group -> position -> legs -> fills), its first body row the Book line ("Book · N open",
     = the header's Daily, MTD, YTD and LTD to the cent, sticky under the heads), the settled group
     last; a click on a name opens its chart, fills and legs under it. The footer: the line before
     the first pull, the last load (`upload_report`) and the Data issues drawer, whose first lines
     are the marks missing (→ Data) and the legs that could not be paired (→ Blotter).

The tiles row left on 2026-09-29: its figures are on the Book line and in the header; a trade's CNH
hedge coverage sits on its own row ("CNH 92 % hedged").

Every row's period figure is the P&L tab's own per-trade figure (`ui.tabs.pnl.period_rows`, the
header's split), the known ones summed (display); nothing is priced or recomputed here. The empty
state (no trades) is the "No blotter loaded" card. Display rules: `ui.tabs.formatting`. The tab has
no date picker: it follows the header's as-of store and re-renders in place on the data revision
and its safety interval. `layout(default_date)` (alias `build_layout`) and
`register_callbacks(app, get_db_path)` are the shell's interface; `gather`, `book_rows`,
`grouped_rows`, `group_total`, `trade_types`, `_period_of`, `_spreads`, `_labels`, `empty_state` and
`trades_on_file` are read by the P&L, Trades, Exposure and Risk tabs and the smoke test.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os
import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dash_table, dcc, html

from ui import sample_book
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import compact
from ui.tabs.formatting import (
    HAND_KINDS, MINUS, MISSING, SOURCE_MIXED, TRADE_TYPE_TITLES, about, contract_name, date_cell,
    fx_name, issues_drawer, lme_name, missing_cell, money_cell, parse_contract_id, plain_words, price_text, quoted_unit,
    short_date, short_money, short_root_name, sign_class, signed_money, signed_number, size_words, spread_name,
    sum_known, tab_link, trade_type_words, type_cell, type_disagrees, unit_suffix,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "book-body"
REFRESH_ID = "book-refresh"
TOOLBAR_ID = "book-toolbar"
CSV_BUTTON_ID = "book-csv"
DOWNLOAD_ID = "book-download"
TABLE_ID = "book-table"
DETAIL_LEGS_ID = "book-detail-legs"
ALERTS_ID = "book-alerts"
LOAD_ID = "book-load"
ISSUES_ID = "book-issues"
EMPTY_UPLOAD_TYPE = "book-empty-upload"    # the empty state's Upload button (clicks the top bar's input), a pattern id
EMPTY_UPLOAD_SINK_ID = "book-empty-upload-sink"
ROW_TYPE = "book-row"                      # the pattern id of a clickable row: {"type": ROW_TYPE, "idx": row id}

NA = MISSING
PERIODS = ("daily", "mtd", "ytd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "mtd": "MTD", "ytd": "YTD", "ltd": "LTD"}
GROUP_COMMODITY, GROUP_STRATEGY, GROUP_INSTRUMENT = "commodity", "strategy", "instrument"
UNDER_ID = "book-under"
RESEARCH_LABEL = "research"                   # the research app's context on a pair row (read-only, never in a figure)
GROUP_TYPE = "type"                        # by spread type (the summary's default)
GROUP_TRADE = "trade"                      # by trade: one line per PBRoot name
BOOK_LABEL = "Book"
SETTLED_GROUP = "Settled and closed out"
OTHER_GROUP = "Other"
OPTIONS_GROUP, FX_GROUP, LME_GROUP, FUTURES_GROUP = "Options on futures", "FX hedges", "LME forwards", "Futures"
# A precious-metal pair (an XAUUSD forward, an XAGUSD option) is a position of its own, not a
# currency hedge (user, 2026-09-29; the engine's test, `engine.spreads.is_hedge`): an outright
# in the Spread type view, under Metals by sector, its own line by commodity and instrument.
PRECIOUS_GROUP = "Metals (FX-quoted)"
NO_STRATEGY_GROUP = "No trade name"        # the rows whose fills carry no PBRoot name
LEG_PREFIX = "LEG"                          # a Strategy view row: LEG-<strategy>-<instrument>[-<prompt>]
PAIR_PREFIX = "PAIR"                        # a Strategy view pair row: PAIR-<pair_id> (strategy|contract a|contract b)
HEDGE_PREFIX = "HEDGE"                      # a Strategy view hedge row: HEDGE-<strategy>-<instrument>
PART_PAIR, PART_OUTRIGHT, PART_HEDGE = "pair", "outright", "hedge"   # the parts of a strategy, in this order
_PART_RANK = {PART_PAIR: 0, "": 1, PART_OUTRIGHT: 1, PART_HEDGE: 2}
FALLBACK_WORDS = "paired by a fallback rule, not by the trade's own strategy"
NO_MARKS_REASON = "no marks on file yet: values appear after the first Bloomberg pull"
FX_SECTOR = "fx"                            # the SGX USD/CNH future's sector in config/contracts.csv
_SECTOR_ORDER = ("energy", "metals", "agriculture", "ferrous", FX_SECTOR)
CONTRACT_PRODUCTS = ("FUTURE", "CMDTY_OPTION", "LME_FWD")   # netted per contract in the Commodity view
LINES_ON_HOVER = 12
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
_METAL_UNITS = {"XAU": "oz", "XAG": "oz", "XPT": "oz", "XPD": "oz"}
_NEXT_EVENT_WORDS = {"first notice": "first notice", "last trade": "last trade", "option expiry": "expiry",
                     "LME prompt": "prompt"}

TAB_KEYS = {"Book": "book", "Exposure": "curve", "P&L": "pnl", "Risk": "risk", "Blotter": "blotter",
            "Data": "market-data"}

TITLE_ABOUT = ("The book at the header's as-of date. A spread type is cross exchange, cross product or term "
               "structure; a trade is Jason's PBRoot name (COPAR3, CATTLE). P&L by: the whole book by spread type, "
               "sector, commodity, instrument or trade, the Book line = the header. Open trades: one row per pair, "
               "leg left over, option, LME prompt and FX trade; click a line above to filter it, a column head to "
               "sort, a row for its chart, fills and legs.")
NEEDS_ABOUT = ("What needs you today, most urgent first, one line per kind: an expiry or first notice within the "
               "alert window, the marks missing, the legs that could not be paired.")
LOAD_ABOUT = "What the last blotter load did, as the upload recorded it."
COLUMN_TIPS = {
    "Spread": "The pair, leg, option, LME prompt or FX trade in plain words, Jason's trade name (PBRoot) small and "
              "grey after it; the legs and instrument ids on hover.",
    "Type": "Cross exchange, cross product or term structure for a pair (the engine's rule); outright, option, LME "
            "forward or FX hedge otherwise.",
    "Sector": "The contract's sector; FX hedges on their own.",
    "Size": "The open size in words: a pair in its paired quantity (the smaller side), a future in lots, an LME "
            "prompt in tonnes, an FX hedge in its base currency.",
    "Entry": "A pair's level at entry (the lots-weighted fills of its legs), an outright's lots-weighted fill, a "
             "fill's price. Prices at tick precision.",
    "Level": "A pair's level at the day's official marks in its unit; the mark otherwise.",
    "Move": "Level less the Daily's reference close: green when it moved the position's way, red when against; the "
            "$ per unit and the move in σ (research) on hover.",
    "z": "The research app's z-score of the pair's spread level against its own history (the app's primary window; "
         "its 1-year z on hover). Context, read-only: never in P&L, delta or a total.",
    "%ile": "The research app's 5-year percentile of the pair's spread level. Context, read-only.",
    "P&L today": "Daily P&L in USD by the header's rule (k / m, the full figure on hover); on a row of a trade, the "
                 "engine's split of that whole trade's Daily (spread, FX, hedge) on hover.",
    "MTD": "MTD P&L in USD, likewise.",
    "LTD": "LTD P&L in USD, likewise: every fill since the first, the settled ones at their frozen figure.",
    "Gross": "The gross USD notional on the open lots (k / m, the full figure on hover): the engine's figure for a "
             "pair, a leg, an outright or a trade at the day's marks and spots. A dash with its reason where the "
             "engine gives none (an option's lots × price is a value, not a notional).",
    "Net": "The net USD notional, long positive, likewise; an amber marker when a pair's legs do not balance or a "
           "leg is left over from its trade's pairs.",
    "Next": "The position's next event (first notice, last trade, option expiry, LME prompt, FX value date) and the "
            "business days to it; red within 3 business days and amber within 10 by the engine's level, grey with "
            "a leading ≈ while the date is estimated.",
    "Trades": "How many open positions the line holds (the rows of Open trades); the fills on hover.",
    "% of gross": "The line's gross USD notional as a share of the Book's gross.",
}


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def full_usd(v: float) -> str:
    """The full figure behind a k / m cell: 'USD −6,928' (a true minus sign)."""
    return f"USD {v:,.0f}".replace("-", MINUS)


def contract_label(instrument_id: Optional[str]) -> str:
    """'CLZ26 Comdty' -> 'CLZ26': the instrument id without its Bloomberg yellow key."""
    s = str(instrument_id or "")
    for key in (" Comdty", " Curncy", " Index"):
        if s.endswith(key):
            return s[: -len(key)]
    return s


def pointer(tab: str, idx: str, label: Optional[str] = None):
    """A tab's name as a link that opens it (`formatting.tab_link`); nothing for this tab or for
    a line with no tab to point at (`tab` empty: the detail is on the Book itself)."""
    if tab in ("", "Book") or tab not in TAB_KEYS:
        return html.Span()
    return tab_link(label or tab, TAB_KEYS[tab], idx, title=f"Open the {tab} tab")


def _sector_label(sector: str) -> str:
    return str(sector or "").replace("_", " ").capitalize() or OTHER_GROUP


# --------------------------------------------------------------------------- spread helpers
# Kept from the retired Spreads tab: the size, level and drill-down builders this tab and the
# P&L tab (`position_size_text`) read.
_DETAIL_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                "padding": "3px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)"}
_ONE_LINE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}


def signed(v: Optional[float]) -> str:
    """+10 / −10 (a true minus sign) / 0, as few decimals as the number needs."""
    if v is None:
        return NA
    if v == 0:
        return "0"
    return f"{v:+,g}".replace("-", MINUS)


def plain(v: float) -> str:
    """5,000 / 96.45 / −2.5 (a true minus sign): thousands separators, at most 2 decimals, trimmed."""
    text = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
    return (MINUS + text) if v < 0 and text != "0" else text


def size_text(size: float, unit: str) -> str:
    """'10 lots', '5,000 bbl', '96.45 oz': the engine's size with its unit in one cell."""
    return f"{plain(size)} {unit}".strip()


def level_decimals(unit: Optional[str]) -> int:
    """How many decimals a level in `unit` is shown with: the exchange's tick, roughly."""
    ccy, _sep, qty = str(unit or "").partition("/")
    ccy, qty = ccy.strip().upper(), qty.strip().lower()
    if ccy == "RATIO" or str(unit or "").lower() == "ratio":
        return 4                       # a China-against-West ratio (user, 2026-09-28: four decimals)
    if qty in ("bu", "lb", "gal"):
        return 4
    if qty in ("mmbtu", "cwt"):
        return 3
    if ccy in ("JPY", "KRW"):
        return 0
    if ccy == "CNY":
        return 2 if qty == "g" else 1
    return 2


def level_text(v: Optional[float], unit: Optional[str], sign: bool = False) -> str:
    """A level (or, with `sign`, a change) in its unit's decimals, a true minus sign."""
    if v is None:
        return NA
    d = level_decimals(unit)
    text = f"{abs(v):,.{d}f}"
    if float(text.replace(",", "")) == 0:
        return text
    if v < 0:
        return MINUS + text
    return ("+" + text) if sign else text


def position_size_text(p: Dict[str, Any]) -> Tuple[str, str]:
    """('long 15 lots', hover) or ('—', why): the summed size with its direction and unit."""
    size = _num(p.get("size"))
    if size is None:
        why = ("no calendar or template fits all of its futures legs, so it has no size"
               if p.get("kind") in HAND_KINDS else "no size given")
        return NA, why
    unit = p.get("size_unit") or "lots"
    hover = (f"{size_words(size, unit)}: a calendar in lots of the near month (long = long the near month), a "
             "template in its quantity unit (long = long the spread as the template writes it)")
    return size_words(size, unit), hover


def open_positions(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [p for p in (result.get("positions") or []) if p.get("status") != "closed"]


def detail_payloads(result: Dict[str, Any], _research: Optional[dict] = None) -> Dict[str, dict]:
    """{position_id: what its drill-down shows}: the position's levels, its member spreads'
    entries (from `result["spreads"]`) and its legs (`level_legs` beside the summed `legs`)."""
    by_id = {s.get("spread_id"): s for s in result.get("spreads") or []}
    out: Dict[str, dict] = {}
    for p in open_positions(result):
        lots = {leg.get("instrument_id"): leg for leg in p.get("legs") or []}
        members = []
        for sid in p.get("spread_ids") or []:
            s = by_id.get(sid) or {}
            members.append({k: s.get(k) for k in ("spread_id", "trade_dates", "accounts", "trade_ids", "size",
                                                   "size_unit", "status", "level_entry", "level_entry_reason")}
                           | {"spread_id": sid})
        legs = []
        # A strategy or bundle with no level has no `level_legs`: its summed legs stand in (lots,
        # open lots, currency; the prices are dashes with the level's reason).
        for leg in (p.get("level_legs") or p.get("legs") or []):
            held = lots.get(leg.get("instrument_id")) or {}
            legs.append({k: leg.get(k) for k in ("instrument_id", "root_id", "weight", "qty_factor", "conversion",
                                                 "currency", "price_scale", "entry_price", "prev_price",
                                                 "now_price")}
                        | {k: held.get(k) for k in ("lots", "open_lots", "pnl_usd", "status", "reason")})
        out[p.get("position_id") or str(p.get("name"))] = {
            "position_id": p.get("position_id"), "name": p.get("name"), "unit": p.get("level_unit") or "",
            "size_text": position_size_text(p)[0],
            **{k: p.get(k) for k in ("level_entry", "level_entry_reason", "level_now", "level_now_reason",
                                     "level_prev", "level_prev_reason", "level_prev_date")},
            "sources": dict(p.get("level_sources") or {}),
            "members": members, "legs": legs,
        }
    return out


def _table_styles(numeric_cols: Sequence[str], text_cols: Sequence[str], na_cols: Sequence[str] = (),
                  extra_na: Sequence[str] = ()) -> dict:
    return dict(
        style_table={"overflowX": "auto"},
        style_cell=_DETAIL_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in text_cols],
        style_header={"fontWeight": "600", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(numeric_cols)
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in (*numeric_cols, *na_cols, *extra_na)],
        tooltip_delay=0, tooltip_duration=None,
    )


def _tip(text: str) -> dict:
    return {"value": plain_words(text), "type": "text"}


def detail_legs_table(payload: Dict[str, Any]) -> dash_table.DataTable:
    """The legs in the shape's order: weight, lots, currency, the prices as quoted at entry, at
    the previous close and now, and the conversion to the spread's unit."""
    rows, tips = [], []
    reasons = {"entry_price": payload.get("level_entry_reason"), "prev_price": payload.get("level_prev_reason"),
               "now_price": payload.get("level_now_reason")}
    for leg in payload.get("legs") or []:
        rec: Dict[str, Any] = {"contract": contract_label(leg.get("instrument_id")),
                               "weight": _num(leg.get("weight")), "lots": _num(leg.get("lots")),
                               "open_lots": _num(leg.get("open_lots")), "currency": leg.get("currency") or ""}
        tip: Dict[str, dict] = {}
        for col in ("entry_price", "prev_price", "now_price"):
            v = _num(leg.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(reasons[col] or "no price and no reason")
        conv, scale = _num(leg.get("conversion")), _num(leg.get("price_scale"))
        rec["conversion"] = NA if conv is None else f"× {conv:g}"
        qf = _num(leg.get("qty_factor"))
        tip["conversion"] = _tip(
            (f"price per quote quantity to per spread quantity: × {conv:g}" if conv is not None
             else "no conversion given")
            + (f"; price scale {scale:g}" if scale is not None else "")
            + (f"; the template's quantity factor {qf:g}" if qf is not None else ""))
        for col in ("weight", "lots", "open_lots"):
            if rec[col] is None:
                rec[col] = NA
                tip[col] = _tip(leg.get("reason") or "not given")
        if leg.get("reason"):
            tip["contract"] = _tip(leg["reason"])
        rows.append(rec)
        tips.append(tip)
    px = rk.rate(4, nully="", trim=True)
    prev_date = payload.get("level_prev_date") or "previous"
    columns = [rk.text("Contract", "contract"), rk.numeric("Weight", "weight", rk.rate(6, trim=True)),
               rk.numeric("Lots", "lots", rk.amount(2, nully="", trim=True)),
               rk.numeric("Open lots", "open_lots", rk.amount(2, nully="", trim=True)),
               rk.text("Ccy", "currency"), rk.numeric("Entry px", "entry_price", px),
               rk.numeric(f"{prev_date} close px", "prev_price", px), rk.numeric("Now px", "now_price", px),
               rk.text("To spread unit", "conversion")]
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    return dash_table.DataTable(
        id=DETAIL_LEGS_ID, columns=columns, data=rows, tooltip_data=tips, **rk.sortable(),
        **_table_styles([], [c["id"] for c in columns if c["type"] == "text"], extra_na=numeric),
        page_action="none")


# --------------------------------------------------------------------------- memo
_MEMO: Dict[Any, Any] = {}
_MEMO_MAX = 64


def _db_revision(conn: sqlite3.Connection) -> Optional[tuple]:
    try:
        path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
        return (str(path), os.path.getmtime(path)) if path else None
    except Exception:  # noqa: BLE001 -- no memo, never a failure
        return None


def _memo(kind: str, conn: sqlite3.Connection, as_of: str, build: Callable[[], Any]) -> Any:
    """`build()` memoised on (kind, database path, mtime, as_of), computed once however many
    callbacks ask at the same time (2026-09-29: the top and the grid callbacks both fire on a page
    load and each ran the whole gather; `blotter_pricing.screen_memo`, single-flight); a failure
    is not memoised."""
    from ui.tabs.blotter_pricing import screen_memo
    return screen_memo("book-" + kind, conn, as_of, build)


# --------------------------------------------------------------------------- reading the lanes
def _failure(what: str, exc: Exception) -> str:
    return f"{what} ({type(exc).__name__}: {exc})"


def _spreads(conn: sqlite3.Connection, as_of: str) -> dict:
    """`book_spreads` through the screens' shared filled reader, one pricing snapshot, memoised
    on the database revision and the as-of (the P&L tab reads this too)."""
    from ui.tabs.blotter_pricing import shared_spreads   # the header reads the same one
    return shared_spreads(conn, as_of, filled=True)


def _needs(conn: sqlite3.Connection, as_of: str) -> tuple:
    from ui.tabs.header import needed_marks
    return _memo("needs", conn, as_of, lambda: needed_marks(conn, as_of))


def _roots() -> Dict[str, Any]:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- the root id stands for its name and sector
        return {}


def _instruments(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{instrument_id: {base_ccy, quote_ccy, asset_class, expiry_date}} plus the option terms on
    file ({strike, option_type}), read once per render."""
    out: Dict[str, dict] = {}
    try:
        for inst, base, quote, cls, expiry in conn.execute(
                "SELECT instrument_id, base_ccy, quote_ccy, asset_class, expiry_date FROM instruments"):
            out[str(inst)] = {"base_ccy": str(base or ""), "quote_ccy": str(quote or ""), "asset_class": str(cls or ""),
                              "expiry_date": str(expiry or ""), "strike": None, "option_type": ""}
        for inst, strike, otype in conn.execute("SELECT instrument_id, strike, option_type FROM instrument_options"):
            if str(inst) in out:
                out[str(inst)]["strike"] = _num(strike) or None
                out[str(inst)]["option_type"] = str(otype or "")
    except sqlite3.Error:
        pass
    return out


def _labels(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{trade_id: {strategy, trade_type, pb_root}}: the broker's labels on every trade on file
    (2026-09-28: Jason's strategy name and the trade type from the PBRoot), read as they are; {}
    on a database from before the columns."""
    try:
        return {str(t): {"strategy": str(st or ""), "trade_type": str(tt or ""), "pb_root": str(pb or "")}
                for t, st, tt, pb in conn.execute("SELECT trade_id, strategy, trade_type, pb_root FROM trades")}
    except sqlite3.Error:
        return {}


def _subsectors(roots: Dict[str, Any]) -> Dict[str, str]:
    """{root id: subsector key} from the contract universe ('copper' for COMEX:HG and LME:CA)."""
    return {rid: str(getattr(r, "subsector", "") or "") for rid, r in roots.items()}


def _load_report(conn: sqlite3.Connection) -> dict:
    """The persisted upload report and the rows that did not become trades."""
    try:
        found = conn.execute("SELECT row_no, symbol, kind, reason, filename, uploaded_at FROM upload_issues "
                             "ORDER BY row_no").fetchall()
        issues = [{"row_no": n, "symbol": sym, "kind": kind, "reason": why, "filename": name, "uploaded_at": at}
                  for n, sym, kind, why, name, at in found]
    except sqlite3.Error:
        issues = []
    try:
        from data.ingest.upload import last_upload_report
        report, report_error = last_upload_report(conn), ""
    except Exception as exc:  # noqa: BLE001
        report, report_error = None, _failure("the upload report could not be read", exc)
    return {"issues": issues, "report": report, "report_error": report_error}


def _periods(conn: sqlite3.Connection, as_of: str, df_today: pd.DataFrame) -> Dict[str, Any]:
    """The P&L tab's per-trade figures for Daily, MTD and LTD (`pnl.period_rows`, the header's own
    split), so the Book line is the header's figure."""
    from ui.tabs.pnl import period_rows
    return {key: period_rows(conn, as_of, key, df_today) for key in PERIODS}


def gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """Every lane's output the tab reads, each in its own try: one that fails costs its own
    section only, with the reason."""
    from ui.tabs.blotter_pricing import priced_value_book, pricing_snapshot
    from ui.tabs.header import latest_mark_time
    data: Dict[str, Any] = {"as_of": as_of}
    with pricing_snapshot(conn, "Book tab"):
        df_today, n_filled, n_total = priced_value_book(conn, as_of)
        data["df"], data["n_filled"], data["n_total"] = df_today, int(n_filled), int(n_total)
        try:
            data["periods"], data["periods_error"] = _periods(conn, as_of, df_today), ""
        except Exception as exc:  # noqa: BLE001
            log.exception("book tab: period figures failed for %s", as_of)
            data["periods"], data["periods_error"] = {}, _failure("the period figures could not be built", exc)
        try:
            data["spreads"], data["spreads_error"] = _spreads(conn, as_of), ""
        except Exception as exc:  # noqa: BLE001
            log.exception("book tab: book_spreads failed for %s", as_of)
            data["spreads"], data["spreads_error"] = None, _failure("the spreads could not be built", exc)
        daily = data["periods"].get("daily")
        ref_used = getattr(daily, "ref_used", "") or ""
        data["ref_used"] = ref_used
        prev: Dict[str, float] = {}
        if ref_used:
            try:
                ref_df = priced_value_book(conn, ref_used)[0]
                if not ref_df.empty:
                    for tid, mark, reason in zip(ref_df["trade_id"], ref_df["mark"], ref_df["reason"]):
                        m = _num(mark)
                        if m is not None and not reason:
                            prev[str(tid)] = m
            except Exception:  # noqa: BLE001 -- the Move is then a dash with its reason
                prev = {}
        data["prev_marks"] = prev
    data["roots"] = _roots()
    data["subsectors"] = _subsectors(data["roots"])
    data["instruments"] = _instruments(conn)
    data["labels"] = _labels(conn)
    data["marks_on_file"] = latest_mark_time(conn, as_of) is not None
    try:
        data["research"], data["research_error"] = _research(data, as_of), ""
    except Exception as exc:  # noqa: BLE001 -- the research cells then say why
        log.exception("book tab: research context failed for %s", as_of)
        data["research"], data["research_error"] = None, _failure("the research context could not be read", exc)
    try:
        from ui.tabs.blotter_pricing import shared_curve    # the Exposure and Risk tabs read the same one
        data["curve"], data["curve_error"] = shared_curve(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001 -- the commodity group lines then say why
        data["curve"], data["curve_error"] = None, _failure("the net exposure by commodity could not be built", exc)
    try:
        from engine.spreads import carry
        data["carry"], data["carry_error"] = carry(conn, as_of, spreads=data.get("spreads")), ""
    except Exception as exc:  # noqa: BLE001 -- research context: the column is then blank, the reason in the drawer
        log.exception("book tab: carry failed for %s", as_of)
        data["carry"], data["carry_error"] = None, _failure("the carry (roll-down) could not be read", exc)
    try:
        from engine.limits import liquidity
        data["liquidity"] = liquidity(conn, as_of, spreads=data.get("spreads"), curve=data.get("curve"))
    except Exception:  # noqa: BLE001 -- research context for one Needs you line: a failure hides the line
        log.exception("book tab: the liquidity check failed for %s", as_of)
        data["liquidity"] = None
    try:
        from engine.expiry import expiry_schedule
        data["schedule"], data["schedule_error"] = expiry_schedule(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        data["schedule"], data["schedule_error"] = None, _failure("the roll calendar could not be built", exc)
    try:
        data["needs"], data["needs_error"] = _needs(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        data["needs"], data["needs_error"] = None, _failure(f"the marks the book needs on {as_of} could not be listed",
                                                            exc)
    try:
        from engine.limits import limit_checks
        data["limits"], data["limits_error"] = limit_checks(conn, as_of, curve=data.get("curve")), ""
    except Exception as exc:  # noqa: BLE001
        data["limits"], data["limits_error"] = None, _failure("the limits could not be checked", exc)
    try:
        data["load"], data["load_error"] = _load_report(conn), ""
    except Exception as exc:  # noqa: BLE001
        data["load"], data["load_error"] = None, _failure("the load report could not be read", exc)
    try:
        from engine.pnl.calendar import load_holidays
        data["holidays"] = load_holidays()
    except Exception:  # noqa: BLE001
        data["holidays"] = set()
    return data


# --------------------------------------------------------------------------- the rows
def _period_of(data: dict, key: str, trade_ids: Sequence[str]) -> Tuple[Optional[float], int, List[str], str]:
    """(value, excluded, reasons, note) of a set of trades for a period: the P&L tab's per-trade
    figures, the known ones summed (display); the fill notes gathered."""
    view = (data.get("periods") or {}).get(key)
    if view is None:
        return None, len(trade_ids), [data.get("periods_error") or "the period figures could not be built"], ""
    rows = view.rows
    if rows.empty:
        return None, len(trade_ids), ["no trade on the book"], ""
    sub = rows[rows["trade_id"].isin(list(trade_ids))]
    pairs = [(v, f"{t}: {r}" if r else "") for t, v, r in zip(sub["trade_id"], sub["value"], sub["reason"])]
    total, excluded, reasons = sum_known(pairs)
    notes = "; ".join(dict.fromkeys(str(n) for n in sub["note"].tolist() if n))
    return total, excluded, reasons, notes


def _periods_of(data: dict, trade_ids: Sequence[str]) -> dict:
    return {key: _period_of(data, key, trade_ids) for key in PERIODS}


def _sector_of(root_ids: Sequence[str], roots: Dict[str, Any]) -> str:
    for r in root_ids:
        sector = str(getattr(roots.get(r), "sector", "") or "")
        if sector:
            return sector
    return ""


def fx_is_hedge(product: str, base: str = "", quote: str = "", instrument_id: str = "", root=None) -> bool:
    """The engine's currency-hedge test (`engine.spreads.is_hedge`), never copied here: an FX
    product on a currency pair or the USD/CNH future is a hedge, a precious-metal pair is not."""
    from engine.spreads import is_hedge
    return bool(is_hedge(root, product, base, quote, instrument_id))


def _commodity_of(root_ids: Sequence[str], product: str, data: dict) -> Tuple[str, str]:
    """(group label, subsector key) of a row for the Commodity view: an FX hedge (an FX product
    on a currency pair) or a root of sector 'fx' (the SGX USD/CNH future) under FX hedges, a
    precious-metal pair under its own line; else the root's commodity across exchanges in plain
    words (`engine.curve.subsector_name`: 'Copper', 'Iron ore'); Other when the root is not in
    the universe."""
    if product in FX_PRODUCTS:
        base = str(root_ids[0]) if root_ids else ""
        return (FX_GROUP if fx_is_hedge(product, base) else PRECIOUS_GROUP), ""
    for rid in root_ids:
        root = data["roots"].get(rid)
        if root is None:
            continue
        key = data["subsectors"].get(rid, "")
        if str(getattr(root, "sector", "") or "") == FX_SECTOR:
            return FX_GROUP, key
        try:
            from engine.curve import subsector_name
            return subsector_name(key) or OTHER_GROUP, key
        except Exception:  # noqa: BLE001 -- the key itself, capitalised
            return (key.replace("_", " ").capitalize() or OTHER_GROUP), key
    return OTHER_GROUP, ""


def trade_types(data: dict) -> Dict[str, dict]:
    """{trade_id: {trade_type, type_source, type_note, position}}: one type per trade, the same on
    every screen: the type spreads-engine gave the position the trade is in (a strategy, a spread,
    an outright: the broker's label where the legs agree with it, else read from the legs,
    `type_source` saying which), and for a trade in no position its own label. Built once per
    render and kept on `data`."""
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


def _labels_of(data: dict, trade_ids: Sequence[str]) -> Tuple[List[str], str, str, str]:
    """(strategies, trade_type, type_source, type_note) of a set of trades: the strategies from the
    broker's labels (`trades.strategy`); the type from `trade_types` (one per trade, the
    position's): one type among the trades -> that type, its source 'label' when every trade's
    is, else 'inferred'; several -> no type, 'mixed labels', the note naming them; none -> ''."""
    labels = data.get("labels") or {}
    mine = [labels.get(str(t)) or {} for t in trade_ids]
    strategies = sorted({m.get("strategy", "") for m in mine if m.get("strategy")})
    types_of = trade_types(data)
    typed = [types_of.get(str(t)) or {} for t in trade_ids]
    types = sorted({x.get("trade_type", "") for x in typed if x.get("trade_type")})
    if not types:
        notes = "; ".join(dict.fromkeys(x.get("type_note", "") for x in typed if x.get("type_note")))
        return strategies, "", SOURCE_MIXED if any(x.get("type_source") == SOURCE_MIXED for x in typed) else "", notes
    if len(types) == 1:
        sources = {x.get("type_source", "") for x in typed if x.get("trade_type") == types[0]}
        source = "label" if sources == {"label"} else "inferred"
        notes = "; ".join(dict.fromkeys(
            (f"{x['position']}: " if x.get("position") else "") + x.get("type_note", "") for x in typed if x.get("type_note")))
        return strategies, types[0], source, notes
    words = "; ".join(dict.fromkeys(f"{x.get('position') or 'trade'}: {TRADE_TYPE_TITLES.get(x.get('trade_type', ''), 'no type').lower()}"
                                    for x in typed))
    return strategies, "", SOURCE_MIXED, f"the trades' positions are of different types: {words}"


def _bd_until(as_of: str, iso: Optional[str], holidays) -> Optional[int]:
    """Business days from `as_of` to `iso` (weekdays not in `holidays`), negative when past; None
    when a date is not a date. Display only, for an FX value date or an option expiry that is on
    no exchange calendar."""
    try:
        a, b = dt.date.fromisoformat(str(as_of)), dt.date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    sign = 1 if b >= a else -1
    lo, hi = min(a, b), max(a, b)
    n, d = 0, lo
    while d < hi:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d not in (holidays or ()):
            n += 1
    return sign * n


def _schedule_rows(data: dict, contract_ids: Sequence[str]) -> List[dict]:
    wanted = set(contract_ids)
    return [r for r in (data.get("schedule") or {}).get("rows") or [] if r.get("contract_id") in wanted]


_LEVEL_RANK = {"EXPIRED": 0, "RED": 1, "AMBER": 2, "GREEN": 3}


def _next_from_schedule(data: dict, contract_ids: Sequence[str]) -> Optional[dict]:
    """The earliest event among the contracts on the roll calendar, as `date_cell` reads it."""
    found = _schedule_rows(data, contract_ids)
    if not found:
        return None
    found.sort(key=lambda r: (_LEVEL_RANK.get(str(r.get("level")), 9),
                              r.get("business_days") if r.get("business_days") is not None else 10 ** 6))
    r = found[0]
    event = _NEXT_EVENT_WORDS.get(str(r.get("next_event") or ""), str(r.get("next_event") or "event"))
    hover = "; ".join(x for x in (
        f"{contract_label(r.get('contract_id'))}: {r.get('next_event') or 'event'} {r.get('next_event_date') or 'date unknown'}"
        f"{' (estimated)' if r.get('estimated') else ''}, {r.get('level')}",
        f"counted to the alert date {r.get('alert_date')} ({r.get('alert_basis') or ''})".strip()
        if r.get("alert_date") and r.get("alert_date") != r.get("next_event_date") else "",
        str(r.get("reason") or ""),
        f"{len(found) - 1} more contract(s) of this position on the calendar" if len(found) > 1 else "") if x)
    return {"iso": r.get("next_event_date"), "bd": r.get("business_days"), "estimated": bool(r.get("estimated")),
            "level": str(r.get("level") or ""), "hover": hover, "event": event, "row": r}


def _next_plain(data: dict, iso: Optional[str], what: str) -> Optional[dict]:
    if not iso or str(iso).startswith("9999"):
        return None
    return {"iso": iso, "bd": _bd_until(data["as_of"], iso, data.get("holidays")), "estimated": False, "level": "",
            "hover": f"{what} {iso}", "event": what, "row": None}


def _row(**kw) -> dict:
    base = {"id": "", "kind": "", "name": "", "name_hover": "", "size": NA, "size_hover": "", "entry": None,
            "entry_text": NA, "entry_hover": "", "now": None, "now_text": NA, "now_hover": "", "unit": "",
            "move": None, "move_text": NA, "move_hover": "", "move_sign": None, "periods": {}, "next": None,
            "trade_ids": [], "sector": "", "instrument_group": OTHER_GROUP, "sector_group": OTHER_GROUP,
            "ratio": False, "hand": "", "order": ("", ""), "strategy": "", "strategies": [], "trade_type": "", "type_source": "",
            "type_note": "", "commodity_group": OTHER_GROUP, "subsector": "",
            # the engine's USD notional on the open lots (None with its reason), the root and the
            # signed lots (tonnes for an LME prompt), and the group a Strategy view row sits in
            "gross": None, "net": None, "notional_reason": "", "notional_source": "", "root_id": "", "lots": None,
            "group": "",
            # the Strategy view's structure (2026-09-28: strategy -> pairs -> legs -> outright -> hedge): the
            # part a row sits in, its pair and its place under it; a pair row is a sub-header whose figures are
            # its legs' (`subtotal`: never added into the group or Book line); a leg row whose trades' P&L sits
            # on another row of the same contract says so (`no_pnl`, the reason) and is never summed either
            "part": "", "pair_id": "", "pair_n": 0, "leg_n": 0, "subtotal": False, "no_pnl": "",
            "type_words": "", "type_tag": "", "size_marker": None, "next_marker": None, "detail_trade_ids": [],
            # the research app's context on a pair row (`pair_research`), None on every other row
            "research": None}
    base.update(kw)
    return base


def _curve_row(data: dict, contract_id: str) -> Optional[dict]:
    """curve-positions' row of an open netted contract ('CLZ26 Comdty', 'LME:CA 2026-12-10')."""
    for r in ((data.get("curve") or {}).get("rows") or []):
        if str(r.get("contract_id")) == contract_id:
            return r
    return None


def contract_notional(data: dict, contract_id: str, trade_ids: Sequence[str], lots: Optional[float],
                      product: str) -> Tuple[Optional[float], Optional[float], str, str]:
    """(gross, net, reason, source) of one contract's open lots: curve-positions' `notional_usd`
    when its row nets exactly these trades (the Exposure tab's figure, at the exact official
    marks), 0 for lots that net to zero, else None with the reason. Nothing is priced here."""
    if product == "CMDTY_OPTION":
        return None, None, "an option's lots × price is its value, not a notional", ""
    if lots is not None and abs(lots) < 1e-9:
        return 0.0, 0.0, "", "flat: the lots net to zero"
    row = _curve_row(data, contract_id)
    if row is None:
        if data.get("curve_error"):
            return None, None, str(data["curve_error"]), ""
        return None, None, f"{contract_label(contract_id)}: no open position on the Exposure tab's list", ""
    theirs = {str(t) for t in row.get("trade_ids") or []}
    if theirs != {str(t) for t in trade_ids}:
        others = len(theirs - {str(t) for t in trade_ids})
        return None, None, (f"{contract_label(contract_id)} is held in another trade too ({_plural(others, 'other fill')}): "
                            "the engine gives the notional per trade, on the trade line"), ""
    v = _num(row.get("notional_usd"))
    if v is None:
        return None, None, str(row.get("reason") or "no USD notional"), ""
    return abs(v), v, "", "the Exposure tab's figure: the open lots at the day's exact official mark and spot"


def _outright_notional(data: dict, trade_ids: Sequence[str]) -> Tuple[Optional[float], Optional[float], str, str]:
    """(gross, net, reason, source) of the outright trades of one contract: spreads-engine's
    `net_usd` per outright summed (display), the gross |net| of the one contract; None with the
    reasons when any is missing."""
    by_id = {str(o.get("trade_id")): o for o in (data.get("spreads") or {}).get("outrights") or []}
    pairs = []
    for t in trade_ids:
        o = by_id.get(str(t)) or {}
        pairs.append((_num(o.get("net_usd")), f"{t}: {o.get('notional_reason') or 'no notional from the spread finder'}"))
    net, excluded, reasons = sum_known(pairs)
    if excluded or net is None:
        return None, None, "; ".join(reasons) or "no notional", ""
    return abs(net), net, "", "the spread finder's notional on the open lots at the day's marks"


def _fill_and_mark(data: dict, trade_ids: Sequence[str]) -> Tuple[Optional[float], Optional[float], str, str]:
    """(lots-weighted fill, mark, mark reason, mark source) of a set of trades of one contract,
    off the value rows (nothing priced here: the fill is the trades' own, the mark the rows')."""
    df = data["df"]
    sub = df[df["trade_id"].isin(list(trade_ids))] if not df.empty else df
    if sub.empty:
        return None, None, "no value row", "", None
    q = pd.to_numeric(sub["quantity"], errors="coerce")
    f = pd.to_numeric(sub["fill"], errors="coerce")
    ok = q.notna() & f.notna() & (q != 0)
    fill = float((q[ok] * f[ok]).sum() / q[ok].sum()) if ok.any() and q[ok].sum() != 0 else (
        float(f[f.notna()].iloc[0]) if f.notna().any() else None)
    # The raw fill with the most decimals decides the precision (never the weighted average's).
    raw = max((float(x) for x in f[f.notna()].tolist()), key=lambda x: len(f"{x:.6f}".rstrip("0").split(".")[1]), default=None)
    marks = pd.to_numeric(sub["mark"], errors="coerce")
    priced = sub[marks.notna() & (sub["reason"] == "")]
    if priced.empty:
        why = "; ".join(dict.fromkeys(str(r) for r in sub["reason"].tolist() if r)) or "no mark on the value rows"
        return fill, None, why, "", raw
    first = priced.iloc[0]
    return fill, float(first["mark"]), "", str(first.get("mark_source") or ""), raw


def _move(now: Optional[float], prev: Optional[float], ref_used: str, what: str) -> Tuple[Optional[float], str]:
    if now is None:
        return None, f"no {what} today"
    if prev is None:
        return None, f"no {what} on the Daily's reference close ({ref_used or 'no reference date'})"
    return now - prev, f"{what} now less the {ref_used} close's"


def _prev_mark(data: dict, trade_ids: Sequence[str]) -> Optional[float]:
    prev = data.get("prev_marks") or {}
    for t in trade_ids:
        if str(t) in prev:
            return prev[str(t)]
    return None


def spread_row(position: dict, data: dict) -> dict:
    """One row of a spread position, the engine's figures as they are."""
    roots = data["roots"]
    legs = position.get("legs") or []
    unit = str(position.get("level_unit") or "")
    ratio = unit.lower() == "ratio"
    root_ids = [str(leg.get("root_id") or "") for leg in legs]
    size, size_hover = position_size_text(position)
    entry, now, move = _num(position.get("level_entry")), _num(position.get("level_now")), _num(position.get("level_change"))
    upu = _num(position.get("usd_per_unit"))
    sources = position.get("level_sources") or {}
    trade_ids = [str(t) for t in position.get("trade_ids") or []]
    legs_words = "; ".join(f"{contract_label(leg.get('instrument_id'))}: {signed(_num(leg.get('lots')))} lot(s), "
                           f"{signed(_num(leg.get('open_lots')))} open" for leg in legs) or "no legs"
    move_hover = (f"{signed_number(move, level_decimals(unit))} × {short_money(upu, '$')} per {unit or 'unit'}"
                  f" (the USD P&L of a 1.0 move on the open lots)"
                  if move is not None and upu is not None else
                  str(position.get("level_change_reason") or position.get("level_prev_reason")
                      or "no level change given"))
    kind = str(position.get("kind") or "")
    products = {str(leg.get("product") or "FUTURE") for leg in legs}
    commodity_group, subsector = _commodity_of(root_ids, "FUTURE" if products - set(FX_PRODUCTS) else next(iter(products), ""), data)
    gross, net = _num(position.get("gross_usd")), _num(position.get("net_usd"))
    return _row(
        id=str(position.get("position_id") or position.get("name")), kind="spread", hand=kind if kind in HAND_KINDS else "",
        strategy=str(position.get("strategy") or ""), strategies=[str(position.get("strategy") or "")] if position.get("strategy") else [],
        trade_type=str(position.get("trade_type") or ""), type_source=str(position.get("type_source") or ""),
        type_note=str(position.get("type_note") or ""), commodity_group=commodity_group, subsector=subsector,
        gross=gross, net=net, notional_reason=str(position.get("notional_reason") or ("" if gross is not None else "no notional given")),
        notional_source="the spread finder's notional on the open lots at the day's marks and spots",
        name=spread_name(position, roots),
        name_hover=f"{position.get('name') or ''}: {legs_words}. Trades {', '.join(trade_ids)}. Click for its "
                   "entries and legs.",
        size=size, size_hover=size_hover, unit="" if ratio else unit, ratio=ratio,
        entry=entry, entry_text=level_text(entry, unit),
        entry_hover=(str(position.get("level_entry_reason") or "no entry level given") if entry is None
                     else f"{level_text(entry, unit)} {unit}: the lots-weighted fills; {sources.get('entry') or ''}".strip()),
        now=now, now_text=level_text(now, unit),
        now_hover=(str(position.get("level_now_reason") or "no level given") if now is None
                   else f"{level_text(now, unit)} {unit} at the day's official marks; {sources.get('now') or ''}".strip()),
        move=move, move_text=level_text(move, unit, sign=True), move_hover=move_hover, move_sign=move,
        periods=_periods_of(data, trade_ids), next=_next_from_schedule(data, [str(leg.get("instrument_id")) for leg in legs]),
        trade_ids=trade_ids, sector=_sector_of(root_ids, roots),
        sector_group=_sector_label(_sector_of(root_ids, roots)), instrument_group=FUTURES_GROUP,
    )


def _contract_key(r: pd.Series) -> Tuple[str, str]:
    """The contract a value row is a position in: its instrument, plus the prompt for an LME
    ticket (one LME instrument, many prompts)."""
    inst = str(r["instrument_id"])
    return (inst, str(r.get("settle_date") or "")) if str(r["product"]) == "LME_FWD" else (inst, "")


def contract_rows(data: dict, trade_ids: Sequence[str], kind: str = "contract", strategy: str = "") -> List[dict]:
    """One row per open contract among `trade_ids` (a future or an option on one per instrument,
    an LME prompt per instrument and prompt), the trades' signed lots netted (display), their
    lots-weighted fill as Entry, the mark as Now, the mark less the reference close's as Move,
    the strategies of its trades small and their labels as its type (on hover). `kind`
    "outright" for the futures the spread rule left alone (their `why_outright` on hover),
    "contract" for the Commodity view's netted rows, "leg" with `strategy` for the Strategy
    view's rows (that strategy's own lots on the contract, grouped under its name)."""
    df = data["df"]
    roots, instruments = data["roots"], data["instruments"]
    wanted = set(str(t) for t in trade_ids)
    sub = df[df["trade_id"].astype(str).isin(wanted) & df["product"].isin(CONTRACT_PRODUCTS)] if not df.empty else df
    by_contract: Dict[Tuple[str, str], List[pd.Series]] = {}
    for _i, r in sub.iterrows():
        by_contract.setdefault(_contract_key(r), []).append(r)
    why_outright = {}
    if kind == "outright":
        for o in (data.get("spreads") or {}).get("outrights") or []:
            if o.get("why_outright"):
                why_outright[str(o.get("trade_id"))] = str(o["why_outright"])
    rows = []
    for (inst, prompt), trades in by_contract.items():
        tids = [str(t["trade_id"]) for t in trades]
        product = str(trades[0]["product"])
        info = instruments.get(inst) or {}
        root_id = str(info.get("base_ccy") or "")
        root = roots.get(root_id)
        lots = sum(_num(t.get("quantity")) or 0.0 for t in trades)
        fill, mark, why, source, raw = _fill_and_mark(data, tids)
        prev = _prev_mark(data, tids)
        move, move_why = _move(mark, prev, data.get("ref_used", ""), "mark")
        strategies, trade_type, type_source, type_note = _labels_of(data, tids)
        contract_id = f"{root_id} {prompt}" if product == "LME_FWD" else inst
        if kind == "outright":
            gross, net, notional_reason, notional_source = _outright_notional(data, tids)
        else:
            gross, net, notional_reason, notional_source = contract_notional(data, contract_id, tids, lots, product)
        if product == "LME_FWD":
            unit = quoted_unit(root) or "USD/t"
            name = lme_name(root, root_id, prompt)
            size = size_words(lots, "t")
            nxt = _next_from_schedule(data, [f"{root_id} {prompt}"]) or _next_plain(data, prompt, "prompt")
            instrument_group = LME_GROUP
        else:
            unit = quoted_unit(root)
            name = contract_name(inst, root, root_id)
            size = size_words(lots, "lots")
            nxt = _next_from_schedule(data, [inst]) or (
                _next_plain(data, info.get("expiry_date") or str(trades[0].get("settle_date") or ""), "expiry")
                if product == "CMDTY_OPTION" else None)
            instrument_group = OPTIONS_GROUP if product == "CMDTY_OPTION" else FUTURES_GROUP
        commodity_group, subsector = _commodity_of([root_id], product, data)
        parsed = parse_contract_id(inst)
        when = prompt if product == "LME_FWD" else (f"{parsed['year']:04d}-{parsed['month']:02d}" if parsed
                                                    else str(info.get("expiry_date") or ""))
        order = (str(getattr(root, "exchange", "") or ("LME" if product == "LME_FWD" else "")), when)
        why_out = "; ".join(dict.fromkeys(why_outright[t] for t in tids if t in why_outright))
        what = ("in no spread" if kind == "outright" else f"of trade {strategy}" if kind == "leg" else
                f"netted across {', '.join(strategies) if strategies else 'the fills'}")
        prefix = "OUTRIGHT" if kind == "outright" else (f"{LEG_PREFIX}-{strategy}" if kind == "leg" else "CONTRACT")
        rows.append(_row(
            id=f"{prefix}-{inst}" + (f"-{prompt}" if prompt else ""), kind=kind, group=strategy if kind == "leg" else "",
            root_id=root_id, lots=lots, gross=gross, net=net, notional_reason=notional_reason, notional_source=notional_source,
            name=name, name_hover=f"{inst}{f' {prompt}' if prompt else ''}: {_plural(len(tids), 'trade')} {what}"
                                  f"{f' ({why_out})' if why_out else ''}. Trades {', '.join(tids)}. Click for its trades.",
            size=size, size_hover=("the trade's open size on the contract, its fills' signed quantities added up (display)"
                                   if kind == "leg" else
                                   "the contract's open size, the trades' signed quantities added up (display)"),
            unit=unit, entry=fill, entry_text=price_text(fill, unit, raw),
            entry_hover="the lots-weighted fill of the open trades" if fill is not None else "no fill on the value rows",
            now=mark, now_text=price_text(mark, unit, raw),
            now_hover=(why if mark is None else f"the official mark on {data['as_of']} ({source or 'source not named'})"),
            move=move, move_text=(NA if move is None else signed_number(move, max(2, price_decimals_for(unit, raw)))),
            move_hover=move_why, move_sign=move,
            periods=_periods_of(data, tids), next=nxt, trade_ids=tids,
            sector=_sector_of([root_id], roots) or ("metals" if product == "LME_FWD" else ""),
            sector_group=_sector_label(_sector_of([root_id], roots)), instrument_group=instrument_group,
            strategy=strategy or (strategies[0] if len(strategies) == 1 else ""), strategies=strategies,
            trade_type=trade_type, type_source=type_source, type_note=type_note,
            commodity_group=commodity_group, subsector=subsector, order=order,
        ))
    return rows


def outright_rows(data: dict, open_ids: set) -> List[dict]:
    """One row per contract of the open outright futures (a future in no spread): `contract_rows`
    over the spread rule's `outrights`, kind "outright"."""
    result = data.get("spreads") or {}
    tids = [str(o.get("trade_id")) for o in result.get("outrights") or [] if str(o.get("trade_id")) in open_ids]
    return contract_rows(data, tids, kind="outright")


def price_decimals_for(unit: str, fill) -> int:
    from ui.tabs.formatting import price_decimals
    return price_decimals(unit, fill)


def trade_row(r: pd.Series, data: dict) -> dict:
    """One row per open trade of the products the spread rule does not cover: an option on a
    future, an LME prompt, an FX hedge (and a leftover product under Other)."""
    roots, instruments = data["roots"], data["instruments"]
    tid, inst, product = str(r["trade_id"]), str(r["instrument_id"]), str(r["product"])
    info = instruments.get(inst) or {}
    base = info.get("base_ccy", "")
    root = roots.get(base)
    qty, fill, mark = _num(r.get("quantity")), _num(r.get("fill")), _num(r.get("mark"))
    reason = str(r.get("reason") or "")
    mark = mark if not reason else None
    settle = str(r.get("settle_date") or "")
    prev = _prev_mark(data, [tid])
    unit, size, name, nxt, sector_group, instrument_group, sector = "", NA, inst, None, OTHER_GROUP, OTHER_GROUP, ""
    if product == "CMDTY_OPTION":
        unit = quoted_unit(root)
        name = contract_name(inst, root, base)
        size = size_words(qty, "lots")
        nxt = _next_from_schedule(data, [inst]) or _next_plain(data, info.get("expiry_date") or settle, "expiry")
        sector_group, instrument_group, sector = OPTIONS_GROUP, OPTIONS_GROUP, str(getattr(root, "sector", "") or "")
    elif product == "LME_FWD":
        unit = quoted_unit(root) or "USD/t"
        name = lme_name(root, base, settle)
        size = size_words(qty, "t")
        nxt = _next_from_schedule(data, [f"{base} {settle}"]) or _next_plain(data, settle, "prompt")
        sector = str(getattr(root, "sector", "") or "metals")
        sector_group, instrument_group = _sector_label(sector), LME_GROUP
    elif product in FX_PRODUCTS:
        pair = inst if product != "FX_OPTION" else (base + info.get("quote_ccy", ""))
        name = fx_name(pair, product, settle if product != "FX_OPTION" else (info.get("expiry_date") or settle),
                       info.get("option_type", ""), info.get("strike"))
        size = size_words(qty, _METAL_UNITS[base]) if base in _METAL_UNITS else size_words(qty, ccy=base or "")
        nxt = _next_plain(data, info.get("expiry_date") if product == "FX_OPTION" else settle,
                          "expiry" if product == "FX_OPTION" else "value date")
        if fx_is_hedge(product, base, info.get("quote_ccy", ""), inst, root):
            sector_group, instrument_group = FX_GROUP, FX_GROUP
        else:                       # a precious-metal pair: a metals position, not a hedge
            sector_group, instrument_group, sector = _sector_label("metals"), PRECIOUS_GROUP, "metals"
        if product == "FX_OPTION":
            unit = f"of {base} notional" if base else ""
    else:
        name = f"{contract_label(inst)} ({product.replace('_', ' ').lower()})"
        size = size_words(qty, "units")
        nxt = _next_plain(data, settle, "settle date")
    move, move_why = _move(mark, prev, data.get("ref_used", ""), "mark")
    # The unit the price is read in: the contract's; an FX spot / forward its pair (a JPY cross 3
    # decimals, gold 2, else 4); an FX option premium none (its own decimals).
    price_unit = unit if product not in FX_PRODUCTS else (inst if product in ("FX_SPOT", "FX_FWD") else "")
    d = price_decimals_for(price_unit, fill)
    strategies, trade_type, type_source, type_note = _labels_of(data, [tid])
    commodity_group, subsector = _commodity_of([base], product, data)
    if product in ("LME_FWD", "CMDTY_OPTION"):
        gross, net, notional_reason, notional_source = contract_notional(
            data, f"{base} {settle}" if product == "LME_FWD" else inst, [tid], qty, product)
    elif product in FX_PRODUCTS:
        gross, net, notional_source = None, None, ""
        notional_reason = ("an FX hedge's notional is its currency exposure, on the Exposure tab's currency card"
                           if instrument_group == FX_GROUP else
                           "a precious-metal forward's USD exposure is on the Exposure tab's currency card (metals)")
    else:
        gross, net, notional_source = None, None, ""
        notional_reason = f"no USD notional from the engine for a {product.replace('_', ' ').lower()}"
    return _row(
        id=f"TRADE-{tid}", kind="trade", name=name, root_id=base, lots=qty,
        gross=gross, net=net, notional_reason=notional_reason, notional_source=notional_source,
        strategy=strategies[0] if strategies else "", strategies=strategies,
        trade_type=trade_type, type_source=type_source, type_note=type_note,
        commodity_group=commodity_group, subsector=subsector,
        order=(str(getattr(root, "exchange", "") or "OTC"), str(info.get("expiry_date") if product == "FX_OPTION" else settle)),
        name_hover=f"{inst}, trade {tid}, dealt {r.get('trade_date') or ''}. Click for the trade.",
        size=size, size_hover=f"the trade's quantity as booked ({qty:g})" if qty is not None else "no quantity",
        unit=unit, entry=fill, entry_text=price_text(fill, price_unit, fill),
        entry_hover="the fill" if fill is not None else "no fill", now=mark,
        now_text=price_text(mark, price_unit, fill),
        now_hover=(reason or "no mark") if mark is None else
                  f"the official mark on {data['as_of']} ({r.get('mark_source') or 'source not named'})",
        move=move, move_text=NA if move is None else signed_number(move, d), move_hover=move_why, move_sign=move,
        periods=_periods_of(data, [tid]), next=nxt, trade_ids=[tid], sector=sector,
        sector_group=sector_group, instrument_group=instrument_group,
    )


def settled_row(data: dict, trade_ids: Sequence[str], strategy: str = "") -> dict:
    """The one line of the settled and closed-out trades (their P&L is in the header's LTD); with
    `strategy`, that strategy's own settled line under its group in the Strategy view."""
    n = len(trade_ids)
    return _row(id="SETTLED" + (f"-{strategy}" if strategy else ""), kind="settled", group=strategy,
                name=f"{_plural(n, 'settled or closed-out trade')}",
                name_hover="Settled trades and closed-out options: their P&L stays in the book's LTD. "
                           "Click for the list.",
                size="", size_hover="", entry_text="", now_text="", move_text="", gross=0.0, net=0.0,
                notional_source="settled: no open lots",
                periods=_periods_of(data, trade_ids), trade_ids=[str(t) for t in trade_ids],
                sector_group=SETTLED_GROUP, instrument_group=SETTLED_GROUP, commodity_group=SETTLED_GROUP,
                strategy=strategy or SETTLED_GROUP, trade_type=SETTLED_GROUP)


def _strategy_entry(data: Optional[dict], name: str) -> Optional[dict]:
    """spreads-engine's `strategies` entry of one strategy name (its pairs, residuals, hedges,
    hedge coverage and Daily split); None when the engine gave none."""
    for s in ((data or {}).get("spreads") or {}).get("strategies") or []:
        if str(s.get("name") or "") == name:
            return s
    return None


def _leg_name(data: dict, inst: str, product: str, root_id: str, prompt: str) -> str:
    root = data["roots"].get(root_id)
    if product == "LME_FWD":
        return lme_name(root, root_id, prompt)
    return contract_name(inst, root, root_id)


def _next_from_engine(ev: Optional[dict]) -> Optional[dict]:
    """A pair's `next_event` (the earlier leg's row of the roll calendar) as `date_cell` reads it."""
    if not ev or not ev.get("date"):
        return None
    event = _NEXT_EVENT_WORDS.get(str(ev.get("event") or ""), str(ev.get("event") or "event"))
    hover = "; ".join(x for x in (
        f"{contract_label(ev.get('contract_id'))}: {ev.get('event') or 'event'} {ev.get('date')}"
        f"{' (estimated)' if ev.get('estimated') else ''}, {ev.get('level') or 'no level'}",
        str(ev.get("reason") or "")) if x)
    return {"iso": ev.get("date"), "bd": ev.get("business_days"), "estimated": bool(ev.get("estimated")),
            "level": str(ev.get("level") or ""), "hover": hover, "event": event,
            "row": {"alert_date": ev.get("alert_date")}}


def _leg_qty(leg: dict, data: dict) -> Optional[float]:
    """A leg's size as the Book counts it: tonnes for an LME prompt (its lots × the lot's tonnes),
    lots otherwise."""
    lots = _num(leg.get("lots"))
    if lots is None:
        return None
    if str(leg.get("product") or "") == "LME_FWD":
        root = data["roots"].get(str(leg.get("root_id") or ""))
        size = _num(getattr(root, "contract_size", None)) if root is not None else None
        return lots * size if size else lots
    return lots


def _position_leg(position: Optional[dict], inst: str) -> Optional[dict]:
    for leg in (position or {}).get("legs") or []:
        if str(leg.get("instrument_id") or "") == inst:
            return leg
    return None


def pair_row(data: dict, strategy: str, n: int, p: dict) -> dict:
    """The Strategy view's pair row: a sub-header between the strategy line and its two leg rows.
    Its name is the two legs', its type in words (the engine's rule; "inferred" or "fallback"
    small, the note on hover), its size in words, the engine's gross and net on the paired lots,
    its levels in the pair's unit (the other form and the $ per unit on hover), its period
    figures its legs' trades' (`subtotal`: never added into the group line again), Next from the
    earlier leg. Nothing is priced here."""
    legs = p.get("legs") or []
    names = [_leg_name(data, str(l.get("instrument_id") or ""), str(l.get("product") or ""), str(l.get("root_id") or ""),
                       str(l.get("prompt") or "")) for l in legs]
    unit = str(p.get("unit") or "")
    ratio = unit.lower() == "ratio"
    entry, now, move = _num(p.get("level_entry")), _num(p.get("level_now")), _num(p.get("level_change"))
    upu = _num(p.get("usd_per_unit"))
    alt_unit, alt = str(p.get("unit_alt") or ""), p.get("level_alt") or None
    d = level_decimals(unit)
    hover_alt = ""
    if alt_unit and alt:
        hover_alt = (f"in {alt_unit}: entry {level_text(_num(alt.get('entry')), alt_unit)}, now "
                     f"{level_text(_num(alt.get('now')), alt_unit)}, move {level_text(_num(alt.get('change')), alt_unit, sign=True)}")
    move_hover = "\n".join(t for t in (
        (f"{signed_number(move, d)} × {short_money(upu, '$')} per {unit or 'unit'} (the USD P&L of a 1.0 move on the paired lots)"
         if move is not None and upu is not None else
         str(p.get("level_change_reason") or p.get("level_prev_reason") or p.get("usd_per_unit_reason") or "no level change given")),
        hover_alt) if t)
    trade_ids = [str(t) for t in p.get("trade_ids") or []]
    all_ids = list(dict.fromkeys(str(t) for l in legs for t in (l.get("contract_trade_ids") or l.get("trade_ids") or [])))
    size = _num(p.get("size"))
    size_unit = str(p.get("size_unit") or "")
    direction = str(p.get("direction") or "long")
    size_words_text = size_words(-size if direction == "short" and size is not None else size, size_unit or "lots") if size is not None else NA
    legs_words = "; ".join(f"{nm}: {size_words(_leg_qty(l, data), 't' if str(l.get('product')) == 'LME_FWD' else 'lots')}"
                           for nm, l in zip(names, legs))
    type_code = str(p.get("type") or "")
    source = str(p.get("type_source") or "")
    tag = source if source in ("inferred", "fallback") else ""
    note = str(p.get("note") or "")
    how = {"label": "the trade's own label", "inferred": "read from the legs"}.get(source, FALLBACK_WORDS)
    type_hover = f"Strategy: {trade_type_words(type_code) or 'pair'} ({how})" + (f". {note}" if note else "")
    residual = _num(p.get("residual_units")) or 0.0
    size_marker = None
    if abs(residual) > 1e-9:
        r_unit = str(p.get("residual_unit") or size_unit)
        r_usd = _num(p.get("residual_usd"))
        text = f"residual {signed_number(residual, 1)} {r_unit}"
        size_marker = (text, f"the pair holds {signed_number(residual, 2)} {r_unit} beyond an exact match: the larger side pairs "
                             f"the whole lots nearest to the smaller side"
                             + (f"; the pair's net USD notional (the two legs' notionals netted, the residual and the price "
                                f"basis between the legs together) is its Net column: {signed_money(r_usd, '$')}" if r_usd is not None else ""))
    next_marker = None
    if p.get("legs_apart_flag"):
        apart = _num(p.get("legs_apart_bd"))
        dates = []
        for nm, l in zip(names, legs):
            cid = str(l.get("contract_id") or l.get("instrument_id") or "")
            ev = _next_from_schedule(data, [cid])
            dates.append(f"{nm}: {ev['event']} {ev['iso']}{' (estimated)' if ev['estimated'] else ''}" if ev else f"{nm}: no event on the roll calendar")
        next_marker = (f"legs {int(abs(apart)) if apart is not None else '?'} bd apart",
                       "the two legs' events are not on the same day: " + "; ".join(dates))
    root_ids = [str(l.get("root_id") or "") for l in legs]
    products = {str(l.get("product") or "FUTURE") for l in legs}
    commodity_group, subsector = _commodity_of(root_ids, "FUTURE" if products - set(FX_PRODUCTS) else next(iter(products), ""), data)
    gross, net = _num(p.get("gross_usd")), _num(p.get("net_usd"))
    template = str(p.get("template_name") or "")
    return _row(
        id=f"{PAIR_PREFIX}-{p.get('pair_id') or f'{strategy}|{n}'}", kind="pair", part=PART_PAIR, pair_id=str(p.get("pair_id") or ""),
        pair_n=n, leg_n=-1, subtotal=True, group=strategy, strategy=strategy, strategies=[strategy] if strategy else [],
        trade_type=type_code, type_source=source, type_note=note, type_words=trade_type_words(type_code) or "pair", type_tag=tag,
        commodity_group=commodity_group, subsector=subsector, root_id=root_ids[0] if root_ids else "",
        gross=gross, net=net, notional_reason=str(p.get("notional_reason") or ("" if gross is not None else "no notional given")),
        notional_source="the engine's notional on the paired lots at the day's marks and spots (both legs, gross; net long positive)",
        name=" / ".join(names) or str(p.get("pair_id") or "pair"),
        name_hover=(f"{'Template ' + template + '. ' if template else ''}{legs_words}. {type_hover}. "
                    f"Its P&L below is its legs' trades' ({_plural(len(trade_ids), 'trade')}). Click for its legs and trades."),
        size=size_words_text, size_hover=f"the paired size: the smaller side in full ({size:g} {size_unit}), {direction}" if size is not None else "no size",
        size_marker=size_marker, unit="" if ratio else unit, ratio=ratio,
        entry=entry, entry_text=level_text(entry, unit),
        entry_hover=(str(p.get("level_entry_reason") or "no entry level given") if entry is None
                     else f"{level_text(entry, unit)} {unit}: the lots-weighted fills of the two legs" + (f"; {hover_alt}" if hover_alt else "")),
        now=now, now_text=level_text(now, unit),
        now_hover=(str(p.get("level_now_reason") or "no level given") if now is None
                   else f"{level_text(now, unit)} {unit} at the day's official marks" + (f"; {hover_alt}" if hover_alt else "")),
        move=move, move_text=level_text(move, unit, sign=True), move_hover=move_hover, move_sign=move,
        periods=_periods_of(data, trade_ids), next=_next_from_engine(p.get("next_event")), next_marker=next_marker,
        trade_ids=trade_ids, detail_trade_ids=all_ids, sector=_sector_of(root_ids, data["roots"]),
        sector_group=_sector_label(_sector_of(root_ids, data["roots"])), instrument_group=FUTURES_GROUP,
        order=(str(p.get("pair_id") or ""), ""),
        research=pair_research(data, p),
    )


# --------------------------------------------------------------------------- research context (read-only)
def _pair_research_key(p: dict) -> Tuple[str, str, str]:
    """(spread_id, instance, reason) of a pair in the research app's key convention: its template
    id, or a calendar's `cal.<exchange>_<code>.<near>_<far>` with the near year, from spreads-engine's
    own `research_key` on the pair's level spec (never rebuilt here); ('', '', why) otherwise."""
    template = str(p.get("template") or "")
    if template:
        return template, "", ""
    spec = p.get("level_spec")
    if not spec:
        return "", "", "no calendar or template fits this pair, so the research app has no spread for it"
    try:
        from engine.spreads.grouping import CALENDAR
        from engine.spreads.levels import research_key, spec_from_dict
        if str(spec.get("kind") or "") != CALENDAR:
            return "", "", ("the pair is neither a calendar nor a template of config/spreads/, so the research app "
                            "has no spread for it")
        return research_key(spec_from_dict(spec))
    except Exception as exc:  # noqa: BLE001 -- a reason on the cell, never a crash
        return "", "", f"the research key could not be built ({type(exc).__name__}: {exc})"


def _research(data: dict, as_of: str) -> dict:
    """The research app's statistics for every pair of the trades (`engine/risk/research_spreads.py`,
    read-only; mock data on this PC): {"keys": {pair_id: (sid, inst, reason)}, "stats": {(sid,
    inst): entry}, "reason": '' or why nothing could be read, "source"}. (Its year of history fed
    the sparkline, which left the Book on 2026-09-29.)"""
    from engine.risk.research_spreads import research_spread_stats
    keys: Dict[str, Tuple[str, str, str]] = {}
    for entry in (data.get("spreads") or {}).get("strategies") or []:
        for p in entry.get("pairs") or []:
            keys[str(p.get("pair_id") or "")] = _pair_research_key(p)
    wanted = sorted({(sid, inst) for sid, inst, _why in keys.values() if sid})
    out: Dict[str, Any] = {"keys": keys, "stats": {}, "reason": "", "source": ""}
    if not wanted:
        return out
    stats = research_spread_stats(wanted, as_of)
    out["stats"], out["reason"], out["source"] = dict(stats.get("stats") or {}), str(stats.get("reason") or ""), str(stats.get("source") or "")
    out["path"] = str(stats.get("path") or "")
    return out


def pair_research(data: dict, p: dict) -> dict:
    """The research context of one pair for its row: key, z, pctile, level, unit, name, asof, the
    entry level, and a reason when there is nothing to show."""
    res = data.get("research") or {}
    pair_id = str(p.get("pair_id") or "")
    sid, inst, why = (res.get("keys") or {}).get(pair_id) or _pair_research_key(p)
    key_text = f"{sid} {inst}".strip()
    out: Dict[str, Any] = {"key": key_text, "sid": sid, "inst": inst, "z": None, "z_kind": "", "z_1y": None, "pctile": None,
                           "level": None, "unit": "", "name": "", "asof": None, "note": "", "reason": "",
                           "entry": _num(p.get("level_entry"))}
    if data.get("research_error"):
        out["reason"] = str(data["research_error"])
        return out
    if not sid:
        out["reason"] = why or "no research key for this pair"
        return out
    entry = (res.get("stats") or {}).get((sid, inst))
    if entry is None:
        out["reason"] = f"no research row for {key_text}: {res.get('reason') or 'the research database was not read'}"
        return out
    if not entry.get("found"):
        # the reader names the database file in its reason; a hover carries no file path (the kit)
        why = str(entry.get("reason") or "not in the research universe").replace(f" ({res.get('path')})", "")
        out["reason"] = f"no research row for {key_text}: {why}"
        return out
    out.update(z=_num(entry.get("z_primary")), z_kind=str(entry.get("z_primary_kind") or ""), z_1y=_num(entry.get("z_1y")),
               pctile=_num(entry.get("pctile_5y")), level=_num(entry.get("level")), unit=str(entry.get("unit") or ""),
               name=str(entry.get("name") or ""), asof=entry.get("asof"), note=str(entry.get("note") or ""))
    return out


def research_hover(r: dict) -> str:
    """One hover for the research cells (z, %ile) of a pair row."""
    bits = [f"{RESEARCH_LABEL}: {r['name'] or r['key']} ({r['key']})" if r.get("name") else f"{RESEARCH_LABEL}: {r['key']}"]
    if r.get("z") is not None:
        bits.append(f"z {signed_number(r['z'], 2)}" + (f" ({r['z_kind']})" if r.get("z_kind") else "")
                    + (f", 1-year z {signed_number(r['z_1y'], 2)}" if r.get("z_1y") is not None else ""))
    else:
        bits.append("no z-score (not enough history in the research app)")
    bits.append(f"5-year percentile {r['pctile']:.0f}" if r.get("pctile") is not None else "no 5-year percentile")
    if r.get("level") is not None:
        bits.append(f"level {level_text(r['level'], r.get('unit'))} {r.get('unit') or ''}".rstrip())
    if r.get("asof"):
        bits.append(f"run of {r['asof']}")
    if r.get("note"):
        bits.append(r["note"])
    bits.append("context from the research app, read-only: never in P&L, delta or a total")
    return " · ".join(bits)


def engine_leg_row(data: dict, strategy: str, leg: dict, part: str, position: Optional[dict] = None,
                   pair: Optional[dict] = None, pair_n: int = 0, leg_n: int = 0, taken: Optional[set] = None) -> dict:
    """A leg row of the Strategy view from the engine's contract-shaped entry: a pair's leg (its
    lots on the contract in this pair) or a residual (the lots left outright). Entry, Now and
    Move are the contract's (its fill, its mark, off the value rows). The row's P&L is the trades
    the engine allocated to it: a contract split over two pairs lists its trades once, on the
    first, and the other row says so instead of a figure (never counted twice, never zero)."""
    inst, product = str(leg.get("instrument_id") or ""), str(leg.get("product") or "FUTURE")
    prompt = str(leg.get("prompt") or "") if product == "LME_FWD" else ""
    root_id = str(leg.get("root_id") or "")
    root = data["roots"].get(root_id)
    info = data["instruments"].get(inst) or {}
    all_ids = [str(t) for t in leg.get("contract_trade_ids") or leg.get("trade_ids") or []]
    mine = [str(t) for t in leg.get("trade_ids") or [] if taken is None or str(t) not in taken]
    qty = _leg_qty(leg, data)
    fill, mark, why, source, raw = _fill_and_mark(data, all_ids)
    prev = _prev_mark(data, all_ids)
    move, move_why = _move(mark, prev, data.get("ref_used", ""), "mark")
    strategies, trade_type, type_source, type_note = _labels_of(data, all_ids)
    contract_id = f"{root_id} {prompt}" if product == "LME_FWD" else inst
    name = _leg_name(data, inst, product, root_id, prompt)
    if product == "LME_FWD":
        unit = quoted_unit(root) or "USD/t"
        size = size_words(qty, "t")
        nxt = _next_from_schedule(data, [contract_id]) or _next_plain(data, prompt, "prompt")
        instrument_group = LME_GROUP
    else:
        unit = quoted_unit(root)
        size = size_words(qty, "lots")
        nxt = _next_from_schedule(data, [inst]) or (
            _next_plain(data, info.get("expiry_date") or "", "expiry") if product == "CMDTY_OPTION" else None)
        instrument_group = OPTIONS_GROUP if product == "CMDTY_OPTION" else FUTURES_GROUP
    pleg = _position_leg(position, inst)
    held = _num((pleg or {}).get("open_lots"))
    whole = pleg is not None and held is not None and qty is not None and abs(held - qty) < 1e-6
    if part == PART_OUTRIGHT:
        gross, net = _num(leg.get("gross_usd")), _num(leg.get("net_usd"))
        notional_reason = str(leg.get("notional_reason") or ("" if gross is not None else "no notional given"))
        notional_source = "the engine's notional on the lots left outright, at the day's marks and spots"
    elif product == "CMDTY_OPTION":
        gross, net, notional_reason, notional_source = None, None, "an option's lots × price is its value, not a notional", ""
    elif whole:
        gross, net = _num(pleg.get("gross_usd")), _num(pleg.get("net_usd"))
        notional_reason = str(pleg.get("notional_reason") or ("" if gross is not None else "no notional given"))
        notional_source = "the engine's notional on the trade's open lots of the contract, at the day's marks and spots"
    else:
        gross, net, notional_source = None, None, ""
        notional_reason = (f"{name} is split between pairs (or a pair and an outright): the engine gives its notional per "
                           f"trade (the trade line) and per pair (the pair line), not per share")
    if mine:
        periods, no_pnl = _periods_of(data, mine), ""
        pnl_words = (f"its P&L is the contract's {_plural(len(mine), 'trade')}" +
                     (" (a fill is not split between pairs: the contract's other row shows a dash)" if len(mine) != len(all_ids) or not whole else ""))
    else:
        no_pnl = (f"{name}: its fills' P&L is on the first row of this contract in the trade (a fill is not split "
                  f"between pairs)")
        periods, pnl_words = {k: (None, 0, [no_pnl], "") for k in PERIODS}, "its P&L is on the contract's first row in this trade"
    held_words = size_words(held, "t" if product == "LME_FWD" else "lots") if held is not None else ""
    what = (("this pair's lots on the contract" + (f" (the trade holds {held_words} in all)" if held_words and not whole else ""))
            if part == PART_PAIR else str(leg.get("why") or "left outright"))
    commodity_group, subsector = _commodity_of([root_id], product, data)
    parsed = parse_contract_id(inst)
    when = prompt if product == "LME_FWD" else (f"{parsed['year']:04d}-{parsed['month']:02d}" if parsed
                                                else str(info.get("expiry_date") or ""))
    suffix = f"-{pair_n}" if part == PART_PAIR else "-out"
    return _row(
        id=f"{LEG_PREFIX}-{strategy}{suffix}-{inst}" + (f"-{prompt}" if prompt else ""), kind="leg", part=part,
        pair_id=str((pair or {}).get("pair_id") or ""), pair_n=pair_n, leg_n=leg_n, no_pnl=no_pnl,
        group=strategy, root_id=root_id, lots=qty, gross=gross, net=net, notional_reason=notional_reason, notional_source=notional_source,
        name=name, name_hover=f"{inst}{f' {prompt}' if prompt else ''}: {what}; {pnl_words}. Trades {', '.join(all_ids)}. Click for its trades.",
        size=size, size_hover=("this pair's share of the contract, from the engine's pairing (the smaller side in full, the "
                               "larger side's nearest whole lots)" if part == PART_PAIR else
                               "the trade's lots on the contract left after its pairs"),
        unit=unit, entry=fill, entry_text=price_text(fill, unit, raw),
        entry_hover="the lots-weighted fill of the contract's open trades" if fill is not None else "no fill on the value rows",
        now=mark, now_text=price_text(mark, unit, raw),
        now_hover=(why if mark is None else f"the official mark on {data['as_of']} ({source or 'source not named'})"),
        move=move, move_text=(NA if move is None else signed_number(move, max(2, price_decimals_for(unit, raw)))),
        move_hover=move_why, move_sign=move,
        periods=periods, next=nxt, trade_ids=mine, detail_trade_ids=all_ids,
        sector=_sector_of([root_id], data["roots"]) or ("metals" if product == "LME_FWD" else ""),
        sector_group=_sector_label(_sector_of([root_id], data["roots"])), instrument_group=instrument_group,
        strategy=strategy, strategies=strategies, trade_type=trade_type, type_source=type_source, type_note=type_note,
        commodity_group=commodity_group, subsector=subsector,
        order=(str(getattr(root, "exchange", "") or ("LME" if product == "LME_FWD" else "")), when),
    )


def hedge_row(data: dict, strategy: str, h: dict, n: int, taken: Optional[set] = None) -> dict:
    """A hedge row of the Strategy view: the strategy's own USD/CNH future per contract, or its
    FX spot / forward / swap trades per pair, net over the open trades; Gross the engine's
    `usd_notional` (a dash with its reason when None)."""
    inst, product = str(h.get("instrument_id") or ""), str(h.get("product") or "FUTURE")
    root_id = str(h.get("root_id") or "")
    root = data["roots"].get(root_id)
    info = data["instruments"].get(inst) or {}
    all_ids = [str(t) for t in h.get("trade_ids") or []]
    mine = [t for t in all_ids if taken is None or t not in taken]
    lots = _num(h.get("lots"))
    fill, mark, why, source, raw = _fill_and_mark(data, h.get("open_trade_ids") or all_ids)
    prev = _prev_mark(data, h.get("open_trade_ids") or all_ids)
    move, move_why = _move(mark, prev, data.get("ref_used", ""), "mark")
    usd = _num(h.get("usd_notional"))
    df = data["df"]
    first = df[df["trade_id"].astype(str).isin(h.get("open_trade_ids") or all_ids)] if not df.empty else df
    settle = str(first.iloc[0].get("settle_date") or "") if not first.empty else ""
    if product in FX_PRODUCTS:
        base = str(info.get("base_ccy") or inst[:3])
        name = fx_name(inst, product, settle, info.get("option_type", ""), info.get("strike"))
        size, price_unit, unit = size_words(lots, ccy=base), inst, ""
        nxt = _next_plain(data, settle, "value date")
        exchange = "OTC"
    else:
        name = contract_name(inst, root, root_id)
        size, price_unit, unit = size_words(lots, "lots"), quoted_unit(root), quoted_unit(root)
        nxt = _next_from_schedule(data, [inst]) or _next_plain(data, info.get("expiry_date") or settle, "expiry")
        exchange = str(getattr(root, "exchange", "") or "")
    strategies, trade_type, type_source, type_note = _labels_of(data, all_ids)
    commodity_group, subsector = _commodity_of([root_id], product, data)
    reason = str(h.get("notional_reason") or ("" if usd is not None else "no USD notional given"))
    return _row(
        id=f"{HEDGE_PREFIX}-{strategy}-{inst}" + (f"-{settle}" if product in FX_PRODUCTS and settle else ""), kind="leg",
        part=PART_HEDGE, leg_n=n, group=strategy, root_id=root_id, lots=lots,
        gross=abs(usd) if usd is not None else None, net=usd, notional_reason=reason,
        notional_source="the hedge's USD notional: lots × the contract's USD size, or the USD leg's signed amount",
        name=name, name_hover=(f"{inst}: the trade's hedge, net over its open fills ({_plural(len(all_ids), 'fill')}"
                               f"{', settled ones listed for their P&L' if len(h.get('open_trade_ids') or []) != len(all_ids) else ''}). "
                               f"Trades {', '.join(all_ids)}. Click for its trades."),
        size=size, size_hover="the hedge's open size, its trades' signed quantities added up (display)",
        unit=unit, entry=fill, entry_text=price_text(fill, price_unit, raw),
        entry_hover="the lots-weighted fill of the open trades" if fill is not None else "no fill on the value rows",
        now=mark, now_text=price_text(mark, price_unit, raw),
        now_hover=(why if mark is None else f"the official mark on {data['as_of']} ({source or 'source not named'})"),
        move=move, move_text=(NA if move is None else signed_number(move, max(2, price_decimals_for(price_unit, raw)))),
        move_hover=move_why, move_sign=move,
        periods=_periods_of(data, mine), next=nxt, trade_ids=mine, detail_trade_ids=all_ids,
        sector=str(getattr(root, "sector", "") or FX_SECTOR), sector_group=FX_GROUP, instrument_group=FX_GROUP,
        strategy=strategy, strategies=strategies, trade_type=trade_type, type_source=type_source, type_note=type_note,
        commodity_group=commodity_group, subsector=subsector, order=(exchange, settle or str(info.get("expiry_date") or "")),
    )


def strategy_leg_rows(data: dict, position: dict, open_ids: set, assigned: set) -> List[dict]:
    """The Strategy view's rows of one strategy, read from spreads-engine's `strategies` entry
    (2026-09-28, the approved design: strategy -> pairs -> legs): a pair row per pair with its
    two leg rows under it, the residuals as leg rows under "Outright", the hedges under "Hedge",
    an option or FX residual as its trade row, and the strategy's own settled line for the trades
    no longer open, so the group's subtotal is the strategy's whole P&L. Every trade of the
    strategy is in exactly one row that carries P&L (a pair row's figures are its legs', never
    added again). Without an engine entry, the earlier form: one leg row per contract."""
    name = str(position.get("strategy") or position.get("name") or "")
    tids = [str(t) for t in position.get("trade_ids") or [] if str(t) not in assigned]
    mine_open = [t for t in tids if t in open_ids]
    entry = _strategy_entry(data, name)
    rows: List[dict] = []
    taken: set = set()
    allowed = set(tids)
    if entry is not None:
        for n, p in enumerate(entry.get("pairs") or []):
            prow = pair_row(data, name, n, p)
            prow["trade_ids"] = [t for t in prow["trade_ids"] if t in allowed]
            rows.append(prow)
            for k, leg in enumerate(p.get("legs") or []):
                row = engine_leg_row(data, name, leg, PART_PAIR, position, p, n, k, taken)
                row["trade_ids"] = [t for t in row["trade_ids"] if t in allowed and t not in taken]
                if not row["trade_ids"] and not row["no_pnl"]:
                    row["no_pnl"] = f"{row['name']}: its fills' P&L is on another row of this contract in the trade"
                    row["periods"] = {key: (None, 0, [row["no_pnl"]], "") for key in PERIODS}
                taken.update(row["trade_ids"])
                rows.append(row)
        df = data["df"]
        for k, r in enumerate(entry.get("residuals") or []):
            if r.get("contract_id"):
                row = engine_leg_row(data, name, r, PART_OUTRIGHT, position, None, 0, k, taken)
            else:
                tid = str(r.get("trade_id") or "")
                hit = df[df["trade_id"].astype(str) == tid] if not df.empty else df
                if hit.empty or tid in taken or tid not in allowed:
                    continue
                row = trade_row(hit.iloc[0], data)
                row["id"] = f"{LEG_PREFIX}-{name}-out-{row['id']}"
                row.update(group=name, strategy=name, part=PART_OUTRIGHT, leg_n=k,
                           name_hover=f"{row['name_hover']} {plain_words(str(r.get('why') or ''))}".strip())
            row["trade_ids"] = [t for t in row["trade_ids"] if t in allowed and t not in taken]
            if not row["trade_ids"] and not row["no_pnl"]:
                row["no_pnl"] = f"{row['name']}: its fills' P&L is on another row of this contract in the trade"
                row["periods"] = {key: (None, 0, [row["no_pnl"]], "") for key in PERIODS}
            taken.update(row["trade_ids"])
            rows.append(row)
        for k, h in enumerate(entry.get("hedges") or []):
            row = hedge_row(data, name, h, k, taken)
            row["trade_ids"] = [t for t in row["trade_ids"] if t in allowed]
            taken.update(row["trade_ids"])
            rows.append(row)
    # anything open the engine did not place (or, with no entry, every open trade): one row per contract
    left_open = [t for t in mine_open if t not in taken]
    if left_open:
        extra = contract_rows(data, left_open, kind="leg", strategy=name)
        for row in extra:
            row["part"] = PART_OUTRIGHT if entry is not None else ""
        rows += extra
        taken.update(t for r in extra for t in r["trade_ids"])
        df = data["df"]
        for _i, r in df[df["trade_id"].astype(str).isin([t for t in mine_open if t not in taken])].iterrows():
            row = trade_row(r, data)
            row["id"] = f"{LEG_PREFIX}-{name}-{row['id']}"
            row["group"], row["strategy"] = name, name
            row["part"] = PART_OUTRIGHT if entry is not None else ""
            rows.append(row)
            taken.add(str(r["trade_id"]))
    left = [t for t in tids if t not in taken]
    if left:
        rows.append(settled_row(data, left, strategy=name))
    return rows


def book_rows(data: dict, by: str = GROUP_INSTRUMENT) -> List[dict]:
    """Every row of the table, for the view `by`. The Strategy view: each strategy's legs
    (`strategy_leg_rows`), then the other open positions, outrights and trades under No
    strategy. The Commodity view: one row per open contract (the trades netted across
    strategies, `contract_rows`), one row per other open trade, the settled line. The
    Instrument view: the open positions (a strategy, a spread across its trade dates), the open
    outright contracts, one row per other open trade, and the settled line. In every view every
    trade of the as-of book is in exactly one row (the Book line is the header's)."""
    df = data.get("df")
    if df is None or df.empty:
        return []
    open_ids = {str(t) for t, s in zip(df["trade_id"], df["status"]) if str(s) == "OPEN"}
    all_ids = [str(t) for t in df["trade_id"]]
    assigned: set = set()
    rows: List[dict] = []
    result = data.get("spreads") or {}
    positions = [] if by == GROUP_COMMODITY else open_positions(result)
    for p in positions:
        tids = [str(t) for t in p.get("trade_ids") or []]
        if not any(t in open_ids for t in tids):
            continue
        if by == GROUP_STRATEGY and str(p.get("kind") or "") == "strategy":
            legs = strategy_leg_rows(data, p, open_ids, assigned)
            for row in legs:
                assigned.update(row["trade_ids"])
            rows.extend(legs)
            continue
        row = spread_row(p, data)
        row["trade_ids"] = [t for t in tids if t not in assigned]
        row["periods"] = _periods_of(data, row["trade_ids"])
        assigned.update(row["trade_ids"])
        rows.append(row)
    contracts = (contract_rows(data, sorted(open_ids - assigned)) if by == GROUP_COMMODITY
                 else outright_rows(data, open_ids - assigned))
    for row in contracts:
        row["trade_ids"] = [t for t in row["trade_ids"] if t not in assigned]
        if not row["trade_ids"]:
            continue
        assigned.update(row["trade_ids"])
        rows.append(row)
    for _i, r in df.iterrows():
        tid = str(r["trade_id"])
        if tid in assigned or tid not in open_ids:
            continue
        rows.append(trade_row(r, data))
        assigned.add(tid)
    left = [t for t in all_ids if t not in assigned]
    if left:
        rows.append(settled_row(data, left))
    return rows


# --------------------------------------------------------------------------- grouping
def _group_key(row: dict, by: str) -> str:
    if by == GROUP_STRATEGY and row.get("group"):
        return row["group"]
    if row["kind"] == "settled":
        return SETTLED_GROUP
    if by == GROUP_COMMODITY:
        return row["commodity_group"]
    if by == GROUP_STRATEGY:
        return row["strategy"] or NO_STRATEGY_GROUP
    return row["instrument_group"]


def _commodity_group_order(data: Optional[dict]) -> List[str]:
    """The Commodity view's group labels in order: curve-positions' subsectors in the fixed sector
    order (energy, metals, agriculture, ferrous), then FX hedges, Other, Settled."""
    subs = ((data or {}).get("curve") or {}).get("by_subsector") or {}
    by_sector: Dict[str, List[str]] = {}
    for key, sub in subs.items():
        by_sector.setdefault(str(sub.get("sector") or ""), []).append(str(sub.get("name") or key))
    out: List[str] = []
    for sector in _SECTOR_ORDER:
        if sector == FX_SECTOR:
            continue
        out += by_sector.pop(sector, [])
    for sector in sorted(by_sector):
        if sector != FX_SECTOR:
            out += by_sector[sector]
    return out


def _group_order(by: str, names: Sequence[str], data: Optional[dict] = None) -> List[str]:
    tail = [FX_GROUP, OTHER_GROUP, SETTLED_GROUP]
    if by == GROUP_INSTRUMENT:
        order = [FUTURES_GROUP, OPTIONS_GROUP, LME_GROUP, PRECIOUS_GROUP, FX_GROUP, OTHER_GROUP, SETTLED_GROUP]
        return [g for g in order if g in names] + sorted(g for g in names if g not in order)
    if by == GROUP_STRATEGY:
        fixed = [NO_STRATEGY_GROUP, SETTLED_GROUP]
        return sorted(g for g in names if g not in fixed) + [g for g in fixed if g in names]
    head = _commodity_group_order(data)
    known = head + tail
    return ([g for g in head if g in names] + sorted(g for g in names if g not in known)
            + [g for g in tail if g in names])


def _daily_abs(row: dict) -> Tuple[int, float]:
    v = row["periods"]["daily"][0]
    return (1, 0.0) if v is None else (0, -abs(v))


def _curve_order(row: dict) -> Tuple[int, str, str, str]:
    """The Commodity view's order inside a group: exchange, then contract month (an LME prompt by
    its date, an FX hedge by its value date), nearest first; the settled line last."""
    exchange, when = row.get("order") or ("", "")
    return (1 if row["kind"] == "settled" else 0, exchange, when, row["name"])


def grouped_rows(rows: Sequence[dict], by: str, data: Optional[dict] = None) -> List[Tuple[str, List[dict]]]:
    """[(group label, its rows)] in the group order of `by` (`data` for the Commodity view's sector
    order; without it the groups come alphabetically). Inside a group the position views rank by
    |Daily| largest first; the Commodity view reads by exchange then contract month (user,
    2026-09-28)."""
    names = list(dict.fromkeys(_group_key(r, by) for r in rows))
    out = []
    for g in _group_order(by, names, data):
        members = sorted((r for r in rows if _group_key(r, by) == g),
                         key=_strategy_order if by == GROUP_STRATEGY else _curve_order if by == GROUP_COMMODITY else _daily_abs)
        out.append((g, members))
    return out


def _strategy_order(row: dict) -> Tuple[int, int, int, int, str, str, str]:
    """The Strategy view's order inside a group: the pairs (each pair row then its two legs), the
    outright lots, the hedges, the settled line; the rows of "No strategy" as the Commodity view
    reads them (exchange, then month)."""
    settled, exchange, when, name = _curve_order(row)
    return (_PART_RANK.get(str(row.get("part") or ""), 1) if not settled else 3, settled,
            int(row.get("pair_n") or 0), int(row.get("leg_n") or 0), exchange, when, name)


def summed_rows(rows: Sequence[dict]) -> List[dict]:
    """The rows a group or Book line adds up: never a pair row (its figures are its legs', which
    are rows of their own) and never a leg row whose trades' P&L sits on another row."""
    return [r for r in rows if not r.get("subtotal") and not r.get("no_pnl")]


def group_total(rows: Sequence[dict], key: str) -> Tuple[Optional[float], int, List[str]]:
    """A group or Book line's figure: the rows' known figures summed, the count left out (rows
    with no figure plus the trades left out inside the rows) and the reasons."""
    rows = summed_rows(rows)
    pairs = [(r["periods"][key][0], f"{r['name']}: {'; '.join(r['periods'][key][2]) or 'no figure'}")
             for r in rows]
    total, excluded, reasons = sum_known(pairs)
    inner = sum(r["periods"][key][1] for r in rows if r["periods"][key][0] is not None)
    for r in rows:
        if r["periods"][key][0] is not None and r["periods"][key][1]:
            reasons.append(f"{r['name']}: {'; '.join(r['periods'][key][2])}")
    return total, excluded + inner, reasons


def notional_total(rows: Sequence[dict]) -> Tuple[Optional[float], Optional[float], int, List[str]]:
    """(gross, net, excluded, reasons): the rows' known engine notionals summed (display), the
    rows with none counted with their reasons."""
    rows = [r for r in rows if not r.get("subtotal")]
    pairs_g = [(r["gross"], f"{r['name']}: {r['notional_reason'] or 'no notional'}") for r in rows]
    gross, excluded, reasons = sum_known(pairs_g)
    net = sum_known([(r["net"], "") for r in rows])[0]
    return gross, net, excluded, reasons


# --------------------------------------------------------------------------- cells, tiles, detail and load helpers kept from the earlier tables
def _excl_hover(excluded: int, reasons: Sequence[str]) -> str:
    return (f"excludes {excluded}: " + "; ".join(reasons[:LINES_ON_HOVER])
            + (f"; and {len(reasons) - LINES_ON_HOVER} more" if len(reasons) > LINES_ON_HOVER else ""))


def _money_td(value, excluded: int, reasons: Sequence[str], note: str = "", bold: bool = False,
              markers: bool = True) -> html.Td:
    """A k / m money cell with its "excl. N" marker; `markers` False (the book has no marks at
    all) keeps the reasons on hover and shows no marker."""
    hover_parts = []
    if excluded:
        hover_parts.append(_excl_hover(excluded, reasons))
    if note:
        hover_parts.append(note)
    if value is None:
        return html.Td(missing_cell("; ".join(reasons[:LINES_ON_HOVER]) or "no figure"))
    children: List[Any] = [money_cell(value, hover="\n".join(hover_parts))]
    if excluded and markers:
        children.append(html.Span(f"excl. {excluded}", className="marker", title=plain_words("\n".join(hover_parts))))
    return html.Td(children, style={"fontWeight": 700} if bold else None)


def _notional_cell(value: Optional[float], reason: str, source: str = "", signed: bool = False, excluded: int = 0,
                   reasons: Sequence[str] = (), markers: bool = True, marks_on_file: bool = True) -> List[Any]:
    """A Gross or Net cell's children: k / m (a net with its sign and colour), the full figure
    and where it came from on hover; the dash with the reason when the engine gave none."""
    if value is None or not marks_on_file:
        # with no marks at all a figure could only be a flat row's zero, or a partial sum of them: a
        # dash instead, like every other cell before the first pull (a zero only once marks are on file)
        return [missing_cell(reason if marks_on_file else NO_MARKS_REASON)]
    hover = "\n".join(t for t in (full_usd(value), source, _excl_hover(excluded, reasons) if excluded else "") if t)
    if signed:
        cell = html.Span(signed_money(value, "$"), className=sign_class(value) or None, title=plain_words(hover))
    else:
        cell = html.Span(short_money(value, "$"), title=plain_words(hover))
    children: List[Any] = [cell]
    if excluded and markers:
        children.append(html.Span(f"excl. {excluded}", className="marker", title=plain_words(_excl_hover(excluded, reasons))))
    return children


def _notional_tds(gross, net, excluded: int, reasons: Sequence[str], source: str, markers: bool, marks_on_file: bool,
                  bold: bool = False) -> List[html.Td]:
    style = {"fontWeight": 700} if bold else None
    why = "; ".join(reasons[:LINES_ON_HOVER]) or "no notional"
    return [html.Td(_notional_cell(gross, why, source, excluded=excluded, reasons=reasons, markers=markers,
                                   marks_on_file=marks_on_file), style=style),
            html.Td(_notional_cell(net, why, source, signed=True, excluded=excluded, reasons=reasons, markers=markers,
                                   marks_on_file=marks_on_file), style=style)]


def _next_td(row: dict) -> html.Td:
    nxt = row.get("next")
    if not nxt:
        if row["kind"] == "settled":
            return html.Td("", className="l")
        return html.Td(missing_cell("no event on the roll calendar for this position"), className="l")
    alert = (nxt.get("row") or {}).get("alert_date") if nxt.get("estimated") else None
    return html.Td(date_cell(nxt["iso"], nxt["bd"], nxt["estimated"], nxt["level"],
                             f"{nxt['event']}: {nxt['hover']}", alert_date=alert), className="l")


def hedge_coverage_children(entry: Optional[dict], words: str = "hedged") -> List[Any]:
    """'hedged 96 %' on a strategy line with CNY legs: the engine's `hedge_coverage_net` (the
    hedge against the signed CNY net, 1.0 = fully hedged), the unhedged CNY notional and the
    gross-based coverage on hover; an amber marker with the engine's sentence when the hedge
    runs with the exposure; a dash with `hedge_reason` when the figure is not given; nothing at
    all for a strategy with no CNY leg."""
    if not entry:
        return []
    reason = str(entry.get("hedge_reason") or "")
    net_cov = _num(entry.get("hedge_coverage_net"))
    if net_cov is None:
        if not reason or "no CNY legs" in reason:
            return []
        return [f"{words} " if words else "", missing_cell(reason)]
    gross_cov, unhedged = _num(entry.get("hedge_coverage")), _num(entry.get("unhedged_cny"))
    hedge_usd, cny_net = _num(entry.get("hedge_usd")), _num(entry.get("cny_net_usd"))
    hover = "; ".join(t for t in (
        f"the hedge ({signed_money(hedge_usd, '$') if hedge_usd is not None else 'n/a'}) against the CNY legs' signed net "
        f"notional ({signed_money(cny_net, '$') if cny_net is not None else 'n/a'}): 100 % = fully hedged",
        f"unhedged CNY {signed_money(unhedged, '$')} ({full_usd(unhedged)})" if unhedged is not None else "",
        f"against the CNY legs' gross notional: {gross_cov * 100:.0f} %" if gross_cov is not None else "",
        reason) if t)
    pct = round(net_cov * 100) + 0.0            # never a "−0 %" for a coverage that rounds to nothing
    out: List[Any] = [html.Span(f"{words} {pct:.0f} %".strip().replace("-", MINUS), title=plain_words(hover))]
    if reason:
        out.append(html.Span("hedge runs with the exposure", className="marker marker--amber", title=plain_words(reason)))
    return out


def _header_figure(data: dict, key: str, marks_on_file: bool) -> Tuple[List[Any], str]:
    """The header's own figure of a period (`pnl.period_rows`' entry: the same value the header
    card shows, with its 'excl. N' / 'filled N' / 'ref <date>' markers) as tile children, and
    its hover. A dash with the reason when it is not available (no marker before the first pull)."""
    view = (data.get("periods") or {}).get(key)
    entry = getattr(view, "entry", None) or {}
    value = _num(entry.get("value")) if entry.get("available") else None
    if value is None:
        why = (data.get("periods_error") or str(entry.get("reason") or "no figure")) if marks_on_file else NO_MARKS_REASON
        return [missing_cell(why)], why
    markers = list(entry.get("markers") or []) if marks_on_file else []
    hover = "\n".join(t for t in (full_usd(value), *(f"{text}: {why}" for text, why in markers)) if t)
    children: List[Any] = [money_cell(value, hover=hover)]
    for text, why in markers:
        children.append(html.Span(text, className="marker", title=plain_words(why)))
    return children, hover


def strategy_words(code: Optional[str]) -> str:
    """A strategy (spread type) as a group label, with its capital: 'Cross exchange'; '' for none."""
    words = trade_type_words(code)
    return words[:1].upper() + words[1:] if words else ""


def _trade_lines(data: dict, trade_ids: Sequence[str]) -> html.Table:
    """A small table of trades: id, date, product, size, fill, mark, LTD (full figures)."""
    df = data["df"]
    sub = df[df["trade_id"].isin(list(trade_ids))] if not df.empty else df
    ltd = (data.get("periods") or {}).get("ltd")
    ltd_by = dict(zip(ltd.rows["trade_id"], ltd.rows["value"])) if ltd is not None and not ltd.rows.empty else {}
    head = html.Tr([html.Th(h, className="l" if i < 4 else None) for i, h in
                    enumerate(("Trade", "Date", "Product", "Status", "Quantity", "Fill", "Mark", "LTD USD"))])
    body = []
    for _i, r in sub.iterrows():
        tid = str(r["trade_id"])
        mark = _num(r.get("mark")) if not r.get("reason") else None
        v = _num(ltd_by.get(tid))
        body.append(html.Tr([
            html.Td(tid, className="l"), html.Td(str(r.get("trade_date") or ""), className="l"),
            html.Td(str(r.get("product") or "").replace("_", " ").lower(), className="l"),
            html.Td(str(r.get("status") or "").lower(), className="l"),
            html.Td(plain(_num(r.get("quantity")) or 0.0)),
            html.Td(price_text(_num(r.get("fill")), "", _num(r.get("fill")))),
            html.Td(price_text(mark, "", _num(r.get("fill"))) if mark is not None else missing_cell(str(r.get("reason") or "no mark"))),
            html.Td(f"{v:,.0f}".replace("-", MINUS) if v is not None else missing_cell(str(r.get("reason") or "no P&L"))),
        ]))
    return html.Table([html.Thead(head), html.Tbody(body)], className="book-table")


_REPORT_KINDS = (("futures", "future", "futures"), ("options_on_futures", "option", "options"),
                 ("lme_forwards", "LME forward", "LME forwards"), ("fx_forwards", "FX forward", "FX forwards"),
                 ("fx_spot", "FX spot", "FX spot"), ("fx_options", "FX option", "FX options"))


def _loaded_at_words(iso: Optional[str]) -> str:
    """'Mon 28 Sep 09:12' in the local time zone from the report's ISO UTC stamp."""
    try:
        t = dt.datetime.fromisoformat(str(iso or ""))
    except ValueError:
        return f"{iso or 'time unknown'}"
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    t = t.astimezone()
    return f"{t:%a} {t.day} {t:%b %H:%M}"


def load_sentence(load: Optional[dict]) -> Tuple[str, str, bool]:
    """(sentence, hover, clean): 'file · time · counts · every row became a trade' or '... · N rows
    did not become trades'; 'No upload recorded on this database' without a report."""
    report = (load or {}).get("report")
    if not report:
        return "No upload recorded on this database", str((load or {}).get("report_error") or ""), True
    kinds = [f"{int(report.get(col) or 0)} {one if int(report.get(col) or 0) == 1 else many}"
             for col, one, many in _REPORT_KINDS if int(report.get(col) or 0)]
    issues = (load or {}).get("issues") or []
    n_bad = len(issues) + int(report.get("excluded_rows") or 0)
    tail = "every row became a trade" if not n_bad else f"{_plural(n_bad, 'row')} did not become trades"
    merge = merge_words(report)
    text = " · ".join(x for x in (str(report.get("filename") or "the last file"),
                                        _loaded_at_words(report.get("uploaded_at")),
                                        ", ".join(kinds) if kinds else "no trade loaded", merge, tail) if x)
    hover = str(report.get("summary") or "").strip() or "The upload recorded no message."
    if issues:
        hover += "\n" + "\n".join(f"- row {i.get('row_no')} {i.get('symbol') or ''}: {i.get('kind')}: {i.get('reason')}"
                                  for i in issues[:LINES_ON_HOVER])
    if report.get("excluded_text"):
        hover += f"\n{report['excluded_text']}"
    return text, hover, not n_bad


def merge_words(report: dict) -> str:
    """'89 trades in the file: 12 added, 77 replaced, 0 removed; 89 on file' from the upload's
    merge by Trade Id (2026-09-28); '' on a report from before the merge counts."""
    added, replaced, removed = (int(report.get(k) or 0) for k in ("added", "replaced", "removed"))
    on_file = int(report.get("on_file_after") or 0)
    if not (added or replaced or removed or on_file):
        return ""
    return (f"{_plural(added + replaced + removed, 'trade')} in the file: {added} added, {replaced} replaced, "
            f"{removed} removed; {on_file} on file")


def issue_items(data: dict, rows: Sequence[dict]) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for key, label in (("periods_error", "P&L"), ("spreads_error", "Spreads"), ("carry_error", "Carry"),
                       ("schedule_error", "Roll calendar"),
                       ("needs_error", "Marks"), ("limits_error", "Limits"), ("load_error", "Load")):
        if data.get(key):
            items.append((label, data[key]))
    items += [("Grouping", str(r)) for r in (data.get("spreads") or {}).get("reasons") or [] if r]
    # a broker label that disagrees with the legs, or labels that disagree among themselves: one
    # line each (the amber "check" chip left the table on 2026-09-28)
    for p in open_positions(data.get("spreads") or {}):
        if type_disagrees(str(p.get("type_source") or ""), str(p.get("type_note") or "")):
            items.append(("Strategy check", f"{p.get('strategy') or p.get('name')}: {plain_words(p.get('type_note')) or 'the labels disagree'}"))
    # the pairing inside each trade: a pair made by a fallback rule (not the trade's own strategy)
    # and the engine's notes, one line each in plain words
    for s in (data.get("spreads") or {}).get("strategies") or []:
        who = str(s.get("name") or "")
        if not who:
            continue                       # the unlabelled trades are position rows under No strategy, not pairs
        for p in s.get("pairs") or []:
            if str(p.get("type_source") or "") == "fallback":
                legs = p.get("legs") or []
                names = " / ".join(_leg_name(data, str(l.get("instrument_id") or ""), str(l.get("product") or ""),
                                             str(l.get("root_id") or ""), str(l.get("prompt") or "")) for l in legs)
                items.append(("Pairing", f"{who}: {names}: {plain_words(p.get('note')) or FALLBACK_WORDS}"))
        for note in s.get("notes") or []:
            if note:
                items.append(("Pairing", f"{who}: {plain_words(note)}"))
    marks = bool(data.get("marks_on_file"))
    for r in rows:
        lines = []
        if r.get("no_pnl") or r.get("subtotal"):
            continue                       # its figures are on another row (a pair's on its legs')
        if marks and r["kind"] != "settled" and r["gross"] is None and r["notional_reason"]:
            lines.append(f"gross / net: {r['notional_reason']}")
        for p in PERIODS:
            value, excluded, reasons, _note = r["periods"][p]
            if value is None:
                lines.append(f"{PERIOD_TITLES[p]}: {'; '.join(reasons) or 'no figure and no reason'}")
            elif excluded:
                lines.append(f"{PERIOD_TITLES[p]} excludes {excluded}: {'; '.join(reasons)}")
        if r["kind"] != "settled":
            for key in ("entry", "now", "move"):
                if r[key] is None and r[f"{key}_text"] == NA:
                    lines.append(f"{key}: {r[f'{key}_hover']}")
        if lines:
            items.append((r["name"], "; ".join(dict.fromkeys(lines))))
    df = data.get("df")
    if df is not None and not df.empty:
        for tid, v, why in zip(df["trade_id"], df["pnl_usd"], df["reason"]):
            if _num(v) is None:
                items.append((str(tid), f"no P&L on {data.get('as_of')}: {why or 'no reason given'}"))
    if data.get("n_filled"):
        items.append(("Fill", f"{_plural(int(data['n_filled']), 'trade')} valued from an earlier close (no price on "
                              f"{data.get('as_of')}); each trade's note is on the Blotter tab."))
    return items


def trades_on_file(conn: sqlite3.Connection) -> int:
    """How many trades the database holds, whatever their date: 0 = no blotter loaded, the
    one test every tab's empty state runs (Exposure, P&L and Risk show the Book's card too,
    user 2026-09-28)."""
    try:
        return int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
    except sqlite3.Error:
        return 0


def empty_state(data: Optional[dict] = None, idx: str = "book") -> html.Div:
    """No trades on file (Empty.dc.html): the upload card and the three steps. `idx` names
    the tab rendering it ("book", "curve", "pnl", "risk"): every tab body is always in the
    layout, so the Upload button's and the sample link's pattern ids carry it and no id
    appears twice on one page; the one clientside callback (`register_callbacks`) and the
    sample-book switch (`ui/uploads.py`) match any idx."""
    return html.Div(className="book-empty", children=[
        html.Div(className="book-card book-empty-main", children=[
            html.Div("No blotter loaded", className="book-empty-title"),
            html.Div("Upload Jason's blotter export, a .csv or .xlsx straight from the prime broker. Every upload "
                     "replaces the whole book; nothing else is ever typed in.", className="book-empty-text"),
            html.Div(className="book-empty-actions", children=[
                html.Button("Upload blotter", id={"type": EMPTY_UPLOAD_TYPE, "idx": idx}, n_clicks=0, className="btn",
                            title="Choose the blotter file (the same upload as the top bar's)"),
                *([sample_book.view_link(idx, small=False)] if not sample_book.is_sample_active() else []),
            ])]),
        html.Div(className="book-card book-empty-next", children=[
            html.Div("What happens next", className="book-h"),
            html.Div(className="book-step", children=[html.Span("1", className="book-step-num"), html.Span([
                html.B("Upload."), " The Book fills with positions and a one-line load report says what became a "
                                   "trade and what did not."])]),
            html.Div(className="book-step", children=[html.Span("2", className="book-step-num"), html.Span([
                html.B("Pull Bloomberg"), " on the Bloomberg PC, or import the marks snapshot here. Until then every "
                                          "position shows its fill and a dash for its value."])]),
            html.Div(className="book-step", children=[html.Span("3", className="book-step-num"), html.Span([
                html.B("Read."), " Book for what you hold and what is due, Exposure for the months, P&L for where "
                                 "it came from, Risk for what it can lose, Data for whether to trust it."])]),
        ])])


HIDDEN = {"display": "none"}


def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def _gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """`gather` memoised on the database revision and the as-of (2026-09-28: the tab's callback
    re-renders on either switch, so one gather serves every view of the same book)."""
    return _memo("gather", conn, as_of, lambda: gather(conn, as_of))


def _problem(as_of: str, exc: Exception) -> html.Div:
    return html.Div(className="status-panel status-panel--down", children=[
        html.P(f"The book could not be built for {as_of} ({type(exc).__name__}: {exc}).",
               className="status-line status-line--bad")])


def _switch(id_: str, options: Sequence[Tuple[str, str]], default: str) -> dcc.RadioItems:
    return dcc.RadioItems(id=id_, className="book-switch",
                          options=[{"label": label, "value": value} for value, label in options],
                          value=default, inline=True, persistence=True, persistence_type="session")


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


# --------------------------------------------------------------------------- the open trades (2026-09-29)
# The Book's layout approved on 2026-09-29 against the research app's Book: tiles, Needs you,
# one summary table with a break-down switch, a flat Open trades list, the row detail, the
# closed fold, the last load and the drawer. The rows are the Strategy rows of `book_rows`
# flattened: a pair row carries its two legs' trades (its leg rows go), everything else is a row
# of its own, and the settled lines go to the closed fold. Every trade is in exactly one row that
# carries P&L, so the summary's Book line is the header.
GROUP_SECTOR = "sector"
VIEW_CONTRACT = "contract"                  # the Instrument view: one group per contract
VIEW_ID = "book-view"
VIEW_OPTIONS = ((GROUP_TYPE, "Spread type"), (GROUP_COMMODITY, "Commodity"), (VIEW_CONTRACT, "Instrument"),
                (GROUP_SECTOR, "Sector"), (GROUP_TRADE, "Trade"))
DEFAULT_VIEW = GROUP_TYPE
OUTRIGHTS_GROUP = "Outrights"
OTHER_SPREADS_GROUP = "Other spreads"
TYPE_GROUP_ORDER = ("Cross exchange", "Cross product", "Term structure", OTHER_SPREADS_GROUP, OUTRIGHTS_GROUP,
                    OPTIONS_GROUP, LME_GROUP, FX_GROUP, OTHER_GROUP, SETTLED_GROUP)
_TYPE_WORDS = {OUTRIGHTS_GROUP: "outright", OPTIONS_GROUP: "option", LME_GROUP: "LME forward", FX_GROUP: "FX hedge",
               OTHER_GROUP: "other"}
GRID_SECTION_ID = "book-grid-section"
TBODY_ID = "book-grid-body"
GRID_META_ID = "book-grid-meta"
PREPULL_ID = "book-prepull"
EXPAND_ALL_ID = "book-expand-all"
COLLAPSE_ALL_ID = "book-collapse-all"
CLEAR_FILTERS_ID = "book-clear-filters"
EXPAND_STORE_ID = "book-open-rows"          # {"base": default | open | closed, "flip": [node ids]}, session
SORT_STORE_ID = "book-sort-store"           # {"key", "desc"} or None (the order as built), session
DETAIL_STORE_ID = "book-detail-open"        # the node whose detail is open under its row, or None, session
TOGGLE_TYPE = "book-toggle"                 # a row's chevron: {"type": ..., "idx": node id}
SORT_TYPE = "book-sort"                     # a column head: {"type": ..., "idx": column key}
SORT_ARROW_TYPE = "book-sort-arrow"         # the arrow inside a column head
PREPULL_TEXT = "Levels and P&L fill in after the first Bloomberg pull; fills and sizes are already right"
HISTORY_POINTS = 60                         # at most this many closes on a detail chart, plus the as-of
_CHINA_CCY = ("CNY", "CNH")
SPLIT_WORDS = (("spread", "spread"), ("fx", "FX"), ("hedge", "hedge"), ("new_trades", "new trades"),
               ("realised", "realised"), ("other", "other"))   # the engine's Daily split, in this order
AS_HELD = "as held: an option counts as its option lots, not at delta; delta is on the Exposure tab"
GRID_TIPS = {
    "name": "The group, the position (a pair, a leg left over, an option, an LME prompt, an FX trade), its legs, its "
            "fills. Click the chevron to open a row, the name for its chart, fills and legs. Jason's trade name "
            "(PBRoot) small and grey; the legs and ids on hover.",
    "type": "Cross exchange, cross product or term structure for a pair (the engine's rule); outright, option, LME "
            "forward or FX hedge otherwise.",
    "sector": "The contract's sector; FX hedges on their own.",
    "net": f"Net lots, {AS_HELD}. A fill its quantity; a contract its fills netted; a group the contracts' nets added "
           "where the units agree (lots, tonnes, one currency), 'mixed' otherwise; a commodity in one physical unit "
           "(curve-positions' figure).",
    "gross": f"Gross lots, {AS_HELD}: each contract's net, without its sign, added up where the units agree.",
    "fill": "A fill's price; a leg's or contract's lots-weighted average fill; a pair's level at entry.",
    "mark": "The official mark on the day (its source and date on hover); a pair's level at the day's marks.",
    "prev": "The mark, or a pair's level, on the Daily's reference close.",
    "move": "Mark less the reference close: green when it moved the position's way, red when against; a pair's $ per "
            "unit and its move in σ (research) on hover.",
    "carry": "Research: the calendar's roll-down per month in USD on the position (+ = the curve's shape pays the "
             "position), from the research app's curve; the roll-down in the spread's unit and the note on hover. "
             "Calendar spreads only. Context, never in P&L or a total.",
    "z": "The research app's z-score of the pair's level (context, read-only: never in P&L, delta or a total).",
    "pctile": "The research app's 5-year percentile of the pair's level (context, read-only).",
    "daily": "Daily P&L in USD by the header's rule (k / m, a fill at the full figure); every level sums its fills; on "
             "a position of a trade, the engine's split of that whole trade's Daily on hover.",
    "mtd": "MTD P&L in USD, likewise.", "ytd": "YTD P&L in USD, likewise.",
    "ltd": "LTD P&L in USD, likewise: every fill since the first, the settled ones at their frozen figure.",
    "local": "P&L in the contract's own currency, on a fill, a leg or a one-contract position; never summed across "
             "currencies.",
    "gross_usd": "The engine's gross USD notional on the open lots (a pair's on its paired lots); a group sums its rows' "
                 "known figures, 'excl. N' for the rest.",
    "net_usd": "The engine's net USD notional, long positive, likewise; an amber marker when a pair's legs do not "
               "balance or a leg is left over from its trade's pairs.",
    "next": "The next event (first notice, last trade, expiry, prompt, value date) and the business days to it; red "
            "within 3 and amber within 10 by the engine's level, grey with ≈ while the date is estimated.",
}


def _units_words(value: Optional[float], unit: str) -> str:
    """'long 35.6 t', 'short 7.9m USD', 'flat': a net in physical units, rounded to a tenth."""
    if value is None:
        return ""
    if unit and len(unit) == 3 and unit.isupper() and unit.isalpha():
        return size_words(value, ccy=unit)
    return size_words(round(float(value), 1), unit or "units")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _engine_pair(data: dict, pair_id: str) -> Optional[dict]:
    """spreads-engine's pair entry by its id, from any strategy."""
    for s in ((data.get("spreads") or {}).get("strategies") or []):
        for p in s.get("pairs") or []:
            if str(p.get("pair_id") or "") == pair_id:
                return p
    return None


def _engine_position(data: dict, position_id: str) -> Optional[dict]:
    for p in open_positions(data.get("spreads") or {}):
        if str(p.get("position_id") or p.get("name")) == position_id:
            return p
    return None


def _type_group(row: dict) -> str:
    """The row's line in the Spread type view."""
    if row["kind"] == "settled":
        return SETTLED_GROUP
    if row["kind"] in ("pair", "spread"):
        return strategy_words(row.get("trade_type")) or OTHER_SPREADS_GROUP
    group = row.get("instrument_group")
    if group in (FUTURES_GROUP, PRECIOUS_GROUP):     # a precious-metal pair is an outright, not a hedge
        return OUTRIGHTS_GROUP
    return group if group in (OPTIONS_GROUP, LME_GROUP, FX_GROUP) else OTHER_GROUP


def _sector_group(row: dict) -> str:
    if row["kind"] == "settled":
        return SETTLED_GROUP
    if row.get("instrument_group") == FX_GROUP or row.get("sector") == FX_SECTOR:
        return FX_GROUP
    return _sector_label(row.get("sector")) if row.get("sector") else OTHER_GROUP


def _direction(row: dict, data: dict, pair: Optional[dict]) -> Tuple[Optional[float], Optional[float]]:
    """(+1 long / −1 short / None, the signed size) of a row: a pair's direction is its first
    leg's (the level is written first leg first), a spread's its size's sign, else its lots'."""
    if row["kind"] == "pair":
        if pair is None:
            return None, None
        sign = -1.0 if str(pair.get("direction") or "long") == "short" else 1.0
        size = _num(pair.get("size"))
        return sign, (sign * size if size is not None else None)
    if row["kind"] == "spread":
        position = _engine_position(data, row["id"])
        size = _num((position or {}).get("size"))
    else:
        size = _num(row.get("lots"))
    if not size:
        return None, size
    return (1.0 if size > 0 else -1.0), size


def _broken(row: dict, pair: Optional[dict]) -> Optional[Tuple[str, str]]:
    """The amber marker of a pair whose legs do not balance, or of a leg left over from a trade's
    pairs, with its sentence in plain words; None otherwise."""
    if row["kind"] == "pair" and pair is not None:
        residual = _num(pair.get("residual_units")) or 0.0
        if abs(residual) > 1e-9:
            unit = str(pair.get("residual_unit") or pair.get("size_unit") or "")
            return (f"unbalanced {signed_number(residual, 1)} {unit}".strip(),
                    f"The two legs do not balance: the pair holds {signed_number(residual, 2)} {unit} more on one "
                    f"side than an exact match (the larger leg pairs the whole lots nearest to the smaller one). "
                    f"Net is the two legs' USD notional netted, so it carries that difference and the price gap "
                    f"between the legs.")
    if row["kind"] == "leg" and row.get("part") == PART_OUTRIGHT:
        who = row.get("group") or ""
        return ("left over", f"A leg of trade {who} left over after its pairs: lots with no opposite leg to pair "
                             f"with, or beyond a pair's exact match. It is an outright until it is paired.")
    return None


def _china(row: dict, data: dict, pair: Optional[dict]) -> bool:
    if pair is not None:
        return any(str(leg.get("currency") or "") in _CHINA_CCY for leg in pair.get("legs") or [])
    root = (data.get("roots") or {}).get(str(row.get("root_id") or ""))
    return str(getattr(root, "currency", "") or "") in _CHINA_CCY


def _split_hover(data: dict, row: dict) -> str:
    """The engine's Daily split of the trade (PBRoot name) the row sits in: it is given per trade,
    not per pair, so the hover says it is the whole trade's."""
    name = str(row.get("group") or "")
    entry = _strategy_entry(data, name) if name else None
    split = (entry or {}).get("daily") or {}
    if not split:
        return ""
    if split.get("reason"):
        return f"trade {name} today, not split: {split['reason']}"
    parts = [f"{word} {signed_money(_num(split.get(key)))}" for key, word in SPLIT_WORDS
             if _num(split.get(key))]
    return f"trade {name} today (the whole trade, not this row): " + " · ".join(parts) if parts else ""


def _sigma_hover(data: dict, row: dict) -> str:
    """Our move against the research app's 20-day daily vol (`sigma_move`), labelled research."""
    r = row.get("research") or {}
    if row["kind"] != "pair" or not r.get("sid") or row.get("move") is None:
        return ""
    stats = ((data.get("research") or {}).get("stats") or {}).get((r.get("sid"), r.get("inst")))
    if stats is None:
        return ""
    try:
        from engine.risk.research_spreads import sigma_move
        sigma, why = sigma_move(row["move"], stats, unit="ratio" if row.get("ratio") else (row.get("unit") or None))
    except Exception as exc:  # noqa: BLE001 -- context only: a reason, never a failure
        sigma, why = None, f"{type(exc).__name__}: {exc}"
    if sigma is None:
        return f"move in σ (research): not given, {why}"
    return f"move {signed_number(sigma, 1)}σ against the research app's 20-day daily vol (context, read-only)"


def _decorate(row: dict, data: dict) -> None:
    pair = _engine_pair(data, row.get("pair_id") or "") if row["kind"] == "pair" else None
    row["type_group"] = _type_group(row)
    row["type_text"] = ((trade_type_words(row.get("trade_type")) or "spread") if row["kind"] in ("pair", "spread")
                        else _TYPE_WORDS.get(row["type_group"], "other"))
    row["sector_key"] = _sector_group(row)
    row["direction"], row["size_num"] = _direction(row, data, pair)
    row["broken"] = _broken(row, pair)
    row["trade_name"] = str(row.get("group") or row.get("strategy") or "")
    if row["trade_name"] == SETTLED_GROUP:
        row["trade_name"] = ""
    entry = _strategy_entry(data, str(row.get("group") or "")) if row.get("group") else None
    row["coverage"] = entry if entry is not None and _china(row, data, pair) else None
    row["split_hover"] = _split_hover(data, row)
    row["sigma_hover"] = _sigma_hover(data, row)


def open_rows(data: dict) -> Tuple[List[dict], List[dict]]:
    """(the open positions, the settled lines), built once per `gather` and kept on it.
    From `book_rows(data, GROUP_STRATEGY)`: a pair row takes its two leg rows' trades and keeps the
    leg rows under it (`leg_rows`, the grid's legs level); a pair whose contracts' fills sit on an earlier pair of the same
    contracts says so and is not summed; every other open row stays as it is."""
    cached = data.get("_open_rows")
    if cached is not None:
        return cached
    rows: List[dict] = []
    settled: List[dict] = []
    pairs: Dict[Tuple[str, int], dict] = {}
    for r in book_rows(data, GROUP_STRATEGY):
        if r["kind"] == "settled":
            settled.append(r)
            continue
        if r["kind"] == "pair":
            r = {**r, "subtotal": False, "trade_ids": [], "leg_rows": []}
            pairs[(r["group"], r["pair_n"])] = r
            rows.append(r)
            continue
        key = (r.get("group"), r.get("pair_n"))
        if r["kind"] == "leg" and r.get("part") == PART_PAIR and key in pairs:
            p = pairs[key]
            p["trade_ids"] += [t for t in r["trade_ids"] if t not in p["trade_ids"]]
            p["leg_rows"].append(r)
            continue
        rows.append(r)
    for p in pairs.values():
        if p["trade_ids"]:
            p["periods"] = _periods_of(data, p["trade_ids"])
        else:
            p["no_pnl"] = (f"{p['name']}: its fills' P&L is on an earlier pair of the same contracts in this trade "
                           "(a fill is not split between pairs)")
            p["periods"] = {k: (None, 0, [p["no_pnl"]], "") for k in PERIODS}
    for r in rows:
        _decorate(r, data)
    for r in settled:
        r.update(type_group=SETTLED_GROUP, sector_key=SETTLED_GROUP, type_text="", direction=None, size_num=None,
                 broken=None, trade_name=str(r.get("group") or ""), coverage=None, split_hover="", sigma_hover="")
    data["_open_rows"] = (rows, settled)
    return rows, settled


# --------------------------------------------------------------------------- the grid: the Book's one table (2026-09-29)
# The user's boss, 2026-09-29: one big grouped table, as granular as possible in a logical way,
# instead of a summary table over a flat list. Every level has the same columns (a cell that does
# not apply is blank): the group rows are the summary. A node's P&L is its trades' per-trade
# figures (`pnl.period_rows`, the header's split) summed, display; its notional is the engine's
# figure on the row, a group's the known figures of its rows summed. Nothing is priced here.
def _unit_of(product: str, base: str) -> str:
    if product in ("FUTURE", "CMDTY_OPTION", "EQ_OPTION"):
        return "lots"
    if product == "LME_FWD":
        return "t"
    if product in FX_PRODUCTS:
        return _METAL_UNITS.get(base, base or "units")
    return "units"


def trade_info(data: dict) -> Dict[str, dict]:
    """{trade_id: what a fill's row shows}, off the shared filled reader's rows and the instruments
    on file, built once per `gather` and kept on it."""
    cached = data.get("_trade_info")
    if cached is not None:
        return cached
    out: Dict[str, dict] = {}
    df = data.get("df")
    prev = data.get("prev_marks") or {}
    roots, instruments = data.get("roots") or {}, data.get("instruments") or {}
    if df is not None and not df.empty:
        for r in df.to_dict("records"):
            tid, inst, product = str(r["trade_id"]), str(r["instrument_id"]), str(r["product"])
            info = instruments.get(inst) or {}
            base = str(info.get("base_ccy") or "")
            root = roots.get(base)
            settle = str(r.get("settle_date") or "")
            reason = str(r.get("reason") or "")
            if product == "LME_FWD":
                name = lme_name(root, base, settle)
            elif product in FX_PRODUCTS:
                pair = inst if product != "FX_OPTION" else base + str(info.get("quote_ccy") or "")
                name = fx_name(pair, product, settle if product != "FX_OPTION" else (info.get("expiry_date") or settle),
                               info.get("option_type", ""), info.get("strike"))
            else:
                name = contract_name(inst, root, base)
            if product in FX_PRODUCTS and fx_is_hedge(product, base, str(info.get("quote_ccy") or ""), inst, root):
                commodity, sector = FX_GROUP, FX_GROUP
            elif product in FX_PRODUCTS:            # a precious-metal pair: a metals position
                commodity, sector = PRECIOUS_GROUP, _sector_label("metals")
            else:
                commodity = _commodity_of([base], product, data)[0]
                sector = (FX_GROUP if str(getattr(root, "sector", "") or "") == FX_SECTOR else
                          _sector_label(str(getattr(root, "sector", "") or ("metals" if product == "LME_FWD" else ""))))
            mark = _num(r.get("mark")) if not reason else None
            unit = quoted_unit(root) if product not in FX_PRODUCTS else (inst if product in ("FX_SPOT", "FX_FWD") else "")
            out[tid] = {
                "trade_id": tid, "instrument_id": inst, "product": product, "trade_date": str(r.get("trade_date") or ""),
                "status": str(r.get("status") or ""), "qty": _num(r.get("quantity")), "fill": _num(r.get("fill")),
                "mark": mark, "mark_source": str(r.get("mark_source") or ""), "mark_date": str(r.get("mark_date") or ""),
                "reason": reason, "note": str(r.get("note") or ""), "prev": prev.get(tid),
                "local": _num(r.get("pnl_local")), "currency": str(info.get("quote_ccy") or ""),
                "unit": _unit_of(product, base), "price_unit": unit if product != "LME_FWD" else (unit or "USD/t"),
                "contract": (inst, settle if product in ("LME_FWD", *FX_PRODUCTS) else ""), "root_id": base,
                "name": name, "commodity": commodity, "sector": sector or OTHER_GROUP,
                "strategy": str(((data.get("labels") or {}).get(tid) or {}).get("strategy") or ""),
            }
    data["_trade_info"] = out
    return out


def _per_trade(data: dict) -> Dict[str, Optional[Dict[str, Tuple[Any, str, str]]]]:
    """{period: {trade_id: (value, reason, note)}} off `pnl.period_rows`' rows (None for a period
    that could not be built), kept on `data`."""
    cached = data.get("_per_trade")
    if cached is not None:
        return cached
    out: Dict[str, Any] = {}
    for key in PERIODS:
        view = (data.get("periods") or {}).get(key)
        if view is None:
            out[key] = None
            continue
        rows = view.rows
        out[key] = {str(t): (v, str(r or ""), str(n or "")) for t, v, r, n in
                    zip(rows["trade_id"], rows["value"], rows["reason"], rows["note"])} if not rows.empty else {}
    data["_per_trade"] = out
    return out


def period_sum(data: dict, key: str, ids: Sequence[str]) -> Tuple[Optional[float], int, List[str], str]:
    """`_period_of` over a lookup: the trades' figures of the period, the known ones summed; a
    trade the period's rows do not list is not counted (as `_period_of`)."""
    lookup = _per_trade(data).get(key)
    if lookup is None:
        return None, len(ids), [data.get("periods_error") or "the period figures could not be built"], ""
    pairs, notes = [], []
    for t in ids:
        hit = lookup.get(str(t))
        if hit is None:
            continue
        v, r, n = hit
        pairs.append((v, f"{t}: {r}" if r else ""))
        if n:
            notes.append(n)
    total, excluded, reasons = sum_known(pairs)
    return total, excluded, reasons, "; ".join(dict.fromkeys(notes))


def position_map(data: dict) -> Dict[str, dict]:
    """{trade_id: the Book row (pair, leg left over, outright, option, LME prompt, FX trade, settled
    line) that carries its P&L in the Spread type view}."""
    cached = data.get("_position_map")
    if cached is not None:
        return cached
    rows, settled = open_rows(data)
    out: Dict[str, dict] = {}
    for r in rows + settled:
        for t in r["trade_ids"]:
            out.setdefault(str(t), r)
    data["_position_map"] = out
    return out


def explain_ids(data: dict) -> Dict[str, str]:
    """{trade_id: the position id `engine.spreads.period_explain` gives its trade}: a position of
    `book_spreads` (its own `position_id`: a trade (PBRoot name) is one position there, `STRATEGY-...`;
    a spread of the unlabelled trades its `POSITION-...` id), else `OUTRIGHT-<instrument_id>` for a
    future left outright, else `TRADE-<trade_id>`. The same rule as `period_explain._positions_of`,
    read here so the P&L tab, the scorecard and the Risk tab can point at a Book row by that id."""
    cached = data.get("_explain_ids")
    if cached is not None:
        return cached
    out: Dict[str, str] = {}
    spreads = data.get("spreads") or {}
    for p in spreads.get("positions") or []:
        for t in p.get("trade_ids") or []:
            out.setdefault(str(t), str(p.get("position_id") or p.get("name")))
    for o in spreads.get("outrights") or []:
        out.setdefault(str(o.get("trade_id")), f"OUTRIGHT-{o.get('instrument_id') or ''}")
    df = data.get("df")
    if df is not None and not df.empty:
        for t in df["trade_id"]:
            out.setdefault(str(t), f"TRADE-{t}")
    data["_explain_ids"] = out
    return out


def row_link_attrs(data: dict, node: dict) -> Dict[str, str]:
    """The ids another tab links to a Book row by (`period_explain`'s position ids): `data-position`
    on the row that IS that unit (a Trade-view group line for a trade name, `POSITION-STRATEGY-...`;
    a spread of the unlabelled trades, `POSITION-...`; an outright contract, `OUTRIGHT-<instrument>`;
    an option, LME ticket or FX trade, `TRADE-<trade_id>`), and on every row below a unit
    `data-member-of`, the unit's id(s) its fills belong to."""
    ids = explain_ids(data)
    linked = sorted({ids[t] for t in node["tids"] if t in ids})
    if not linked:
        return {}
    strategies = {str(p.get("position_id") or p.get("name")) for p in (data.get("spreads") or {}).get("positions") or []
                  if str(p.get("kind") or "") == "strategy"}
    out: Dict[str, str] = {}
    if node["level"] == "group":
        if node["id"].startswith(f"{GROUP_TRADE}:g:") and len(linked) == 1 and linked[0] in strategies:
            out["data-position"] = linked[0]
        return out
    if len(linked) == 1 and linked[0] not in strategies and node["level"] in ("position", "instrument"):
        out["data-position"] = linked[0]
    out["data-member-of"] = " ".join(linked)
    return out


def _node(nid: str, level: str, depth: int, label: str, *, row: Optional[dict] = None, tids: Sequence[str] = (),
          children: Sequence[dict] = (), sub: str = "", hover: str = "", default_open: bool = False,
          leaf: Optional[str] = None, detail: bool = False) -> dict:
    return {"id": nid, "level": level, "depth": depth, "label": label, "row": row, "tids": [str(t) for t in tids],
            "children": list(children), "sub": sub, "hover": hover, "default_open": default_open, "leaf": leaf,
            "detail": detail}


def _trade_node(data: dict, tid: str, parent: str, depth: int, sub: str = "", with_name: bool = False) -> dict:
    t = trade_info(data).get(str(tid)) or {"trade_date": "", "status": "", "instrument_id": "", "product": "", "name": tid}
    status = str(t.get("status") or "").upper()
    word = "Fill" if status in ("OPEN", "") else status.capitalize()
    label = f"{t['name']} · {word.lower()} {short_date(t['trade_date'])}" if with_name else f"{word} {short_date(t['trade_date'])}"
    hover = (f"Trade {tid}: {str(t.get('product') or '').replace('_', ' ').lower()} {t.get('instrument_id')}, dealt "
             f"{t.get('trade_date')}, {status.lower() or 'status not given'}")
    return _node(f"{parent}/t:{tid}", "trade", depth, label, tids=[tid], sub=sub or str(tid), hover=hover, leaf=str(tid))


def _trade_nodes(data: dict, tids: Sequence[str], parent: str, depth: int, subs: Optional[Callable[[str], str]] = None,
                 with_name: bool = False) -> List[dict]:
    info = trade_info(data)
    ordered = sorted((str(t) for t in tids), key=lambda t: ((info.get(t) or {}).get("trade_date", ""), t))
    return [_trade_node(data, t, parent, depth, subs(t) if subs else "", with_name) for t in ordered]


def _spread_legs(data: dict, row: dict, nid: str, depth: int) -> List[dict]:
    """The legs of a spread of the unlabelled trades: one row per contract of its trades
    (`contract_rows`), the engine's notional of the position's leg on it, its trades under it."""
    position = _engine_position(data, row["id"])
    legs = []
    covered: set = set()
    for leg in contract_rows(data, row["trade_ids"], kind="contract"):
        inst = leg["id"].split("-", 1)[1] if "-" in leg["id"] else leg["id"]
        pleg = _position_leg(position, str(inst).split("-")[0] if leg.get("instrument_group") == LME_GROUP else str(inst))
        if pleg is not None:
            leg["gross"], leg["net"] = _num(pleg.get("gross_usd")), _num(pleg.get("net_usd"))
            leg["notional_reason"] = str(pleg.get("notional_reason") or ("" if leg["gross"] is not None else "no notional given"))
            leg["notional_source"] = "the engine's notional of the spread's leg, at the day's marks and spots"
        lid = f"{nid}/{leg['id']}"
        covered.update(leg["trade_ids"])
        legs.append(_node(lid, "leg", depth, leg["name"], row=leg, tids=leg["trade_ids"],
                          children=_trade_nodes(data, leg["trade_ids"], lid, depth + 1), detail=True,
                          hover=leg["name_hover"]))
    rest = [t for t in row["trade_ids"] if t not in covered]
    return legs + _trade_nodes(data, rest, nid, depth)


def _position_node(data: dict, row: dict, parent: str, depth: int) -> dict:
    """A position of the Spread type, Sector or Trade view: a pair with its two legs and their
    fills, a spread with its legs, a one-contract position with its fills, a one-fill position."""
    nid = f"{parent}/{row['id']}"
    kind = row["kind"]
    if kind == "trade":
        return _node(nid, "position", depth, row["name"], row=row, tids=row["trade_ids"],
                     leaf=row["trade_ids"][0] if row["trade_ids"] else None, detail=True, hover=row["name_hover"],
                     sub=row.get("trade_name") or "")
    if kind == "pair":
        legs = []
        for leg in row.get("leg_rows") or []:
            lid = f"{nid}/{leg['id']}"
            legs.append(_node(lid, "leg", depth + 1, leg["name"], row=leg, tids=leg["trade_ids"],
                              children=_trade_nodes(data, leg["trade_ids"], lid, depth + 2), detail=True,
                              hover=leg["name_hover"]))
        children = legs
    elif kind == "spread":
        children = _spread_legs(data, row, nid, depth + 1)
    else:
        children = _trade_nodes(data, row["trade_ids"], nid, depth + 1)
    return _node(nid, "position", depth, row["name"], row=row, tids=row["trade_ids"], children=children, detail=True,
                 hover=row["name_hover"], sub=row.get("trade_name") or "")


def _instrument_node(data: dict, row: dict, parent: str, depth: int) -> dict:
    """A contract of the Commodity or Instrument view (its trades netted across trades), its fills
    under it, each with the spread it sits in small and grey."""
    nid = f"{parent}/{row['id']}"
    pos = position_map(data)

    def spread_of(t: str) -> str:
        r = pos.get(t) or {}
        if r.get("kind") in ("pair", "spread"):
            return r["name"] + (f" · {r['trade_name']}" if r.get("trade_name") else "")
        return str(r.get("trade_name") or "")

    if row["kind"] == "trade":
        tid = row["trade_ids"][0] if row["trade_ids"] else None
        return _node(nid, "instrument", depth, row["name"], row=row, tids=row["trade_ids"], leaf=tid, detail=True,
                     hover=row["name_hover"], sub=spread_of(tid) if tid else "")
    return _node(nid, "instrument", depth, row["name"], row=row, tids=row["trade_ids"],
                 children=_trade_nodes(data, row["trade_ids"], nid, depth + 1, subs=spread_of), detail=True,
                 hover=row["name_hover"])


def _settled_node(data: dict, view: str, tids: Sequence[str]) -> dict:
    gid = f"{view}:g:{SETTLED_GROUP}"
    pos = position_map(data)
    return _node(gid, "group", 0, SETTLED_GROUP, tids=tids,
                 children=_trade_nodes(data, tids, gid, 1, subs=lambda t: str((pos.get(t) or {}).get("trade_name") or t),
                                       with_name=True),
                 hover="Settled trades and closed-out options: their P&L stays in the book's LTD.")


def _view_order(view: str, names: Sequence[str], data: dict) -> List[str]:
    if view == GROUP_TYPE:
        head, tail = list(TYPE_GROUP_ORDER[:-2]), [OTHER_GROUP]
    elif view == GROUP_SECTOR:
        head, tail = [_sector_label(s) for s in _SECTOR_ORDER if s != FX_SECTOR], [FX_GROUP, OTHER_GROUP]
    elif view == GROUP_TRADE:
        head, tail = [], [NO_STRATEGY_GROUP]
    else:
        return [g for g in _group_order(GROUP_COMMODITY, names, data) if g != SETTLED_GROUP]
    return [g for g in head if g in names] + sorted(g for g in names if g not in head + tail) + [g for g in tail if g in names]


def grid_tree(data: dict, view: str) -> List[dict]:
    """The top nodes of the view, the settled group last; built once per view and `gather`."""
    cache = data.setdefault("_grid", {})
    if view in cache:
        return cache[view]
    nodes: List[dict] = []
    if view in (GROUP_TYPE, GROUP_SECTOR, GROUP_TRADE):
        rows, settled = open_rows(data)
        key = {GROUP_TYPE: lambda r: r["type_group"], GROUP_SECTOR: lambda r: r["sector_key"],
               GROUP_TRADE: lambda r: r["trade_name"] or NO_STRATEGY_GROUP}[view]
        names = list(dict.fromkeys(key(r) for r in rows))
        for label in _view_order(view, names, data):
            gid = f"{view}:g:{label}"
            members = sorted((r for r in rows if key(r) == label), key=_daily_abs)
            children = [_position_node(data, r, gid, 1) for r in members]
            nodes.append(_node(gid, "group", 0, label, tids=[t for c in children for t in c["tids"]], children=children,
                               default_open=True))
            if view == GROUP_TRADE:
                # the trade's CNH hedge coverage on its own line (the tile's figure until 2026-09-29)
                nodes[-1]["coverage"] = _strategy_entry(data, label)
        left = [t for r in settled for t in r["trade_ids"]]
    else:
        crows = book_rows(data, GROUP_COMMODITY)
        inst_rows = [r for r in crows if r["kind"] != "settled"]
        left = [t for r in crows if r["kind"] == "settled" for t in r["trade_ids"]]
        names = list(dict.fromkeys(r["commodity_group"] for r in inst_rows))
        order = _view_order(view, names, data)
        subs = ((data.get("curve") or {}).get("by_subsector") or {})
        if view == GROUP_COMMODITY:
            for label in order:
                gid = f"{view}:g:{label}"
                members = sorted((r for r in inst_rows if r["commodity_group"] == label), key=_curve_order)
                children = [_instrument_node(data, r, gid, 1) for r in members]
                group = _node(gid, "group", 0, label, tids=[t for c in children for t in c["tids"]],
                              children=children, default_open=True)
                group["commodity_sub"] = next((sub for sub in subs.values() if str(sub.get("name")) == label), None)
                nodes.append(group)
        else:
            rank = {g: i for i, g in enumerate(order)}
            for r in sorted(inst_rows, key=lambda r: (rank.get(r["commodity_group"], 99), _curve_order(r))):
                nodes.append(_instrument_node(data, r, f"{view}:g", 0))
    if left:
        nodes.append(_settled_node(data, view, left))
    cache[view] = nodes
    return nodes


# --- the values of a node
def _sizes(data: dict, ids: Sequence[str], one_root: bool = False) -> dict:
    """Net and gross lots as held over a set of fills: per contract netted, then summed when every
    contract is in the same unit (lots, tonnes, one currency); 'mixed' with the split on hover
    otherwise; with `one_root` (a group line) only when every contract is of one root, since lots of
    different contracts do not add up to a position. Open fills only. Display: nothing converted."""
    info = trade_info(data)
    nets: Dict[Tuple[str, str], float] = {}
    units: Dict[Tuple[str, str], str] = {}
    names: Dict[Tuple[str, str], str] = {}
    roots: set = set()
    for t in ids:
        ti = info.get(str(t))
        if not ti or ti["status"] != "OPEN" or ti["qty"] is None:
            continue
        k = ti["contract"]
        nets[k] = nets.get(k, 0.0) + ti["qty"]
        units[k], names[k] = ti["unit"], ti["name"]
        roots.add(ti["root_id"])
    if not nets:
        return {"net": None, "gross": None, "unit": "", "mixed": "", "fx": False}
    used = set(units.values())
    if len(used) == 1 and (not one_root or len(roots) == 1):
        unit = used.pop()
        fx = len(unit) == 3 and unit.isupper()
        return {"net": sum(nets.values()), "gross": sum(abs(v) for v in nets.values()), "unit": unit, "mixed": "", "fx": fx}
    items = [f"{names[k]} {_size_text(v, units[k])}" for k, v in nets.items()]
    split = "; ".join(items[:LINES_ON_HOVER]) + (f"; and {len(items) - LINES_ON_HOVER} more" if len(items) > LINES_ON_HOVER else "")
    why = "mixed units" if len(used) > 1 else "several contracts"
    return {"net": None, "gross": None, "unit": "", "mixed": f"{why}, not added: {split}", "fx": False}


def _size_text(value: Optional[float], unit: str) -> str:
    if value is None:
        return ""
    if unit and len(unit) == 3 and unit.isupper() and unit.isalpha():
        return size_words(value, ccy=unit)
    return size_words(value, unit or "lots")


def _local(data: dict, ids: Sequence[str]) -> Tuple[Optional[float], str, str]:
    """(P&L in the contract's currency summed, the currency, why none) over fills of ONE currency;
    none across currencies (never summed)."""
    info = trade_info(data)
    got = [info[t] for t in ids if t in info]
    ccys = {g["currency"] for g in got}
    if not got or len(ccys) != 1:
        return None, "", "" if not got else "several currencies: never summed"
    total, _excl, reasons = sum_known([(g["local"], f"{g['trade_id']}: {g['reason'] or 'no local P&L'}") for g in got])
    return total, ccys.pop(), "; ".join(reasons)


_ONE_CONTRACT = ("outright", "contract", "leg", "trade")


def node_values(data: dict, node: dict, shown: Optional[set] = None) -> dict:
    """Every column's value of a node over the fills shown (all when `shown` is None)."""
    ids = node["tids"] if shown is None else [t for t in node["tids"] if t in shown]
    row = node.get("row")
    info = trade_info(data)
    level = node["level"]
    leaf = node.get("leaf")
    v: Dict[str, Any] = {"ids": ids}
    no_pnl = (row or {}).get("no_pnl") if row else ""
    for key in PERIODS:
        if ids:
            v[key] = period_sum(data, key, ids)
        elif no_pnl:
            v[key] = (None, 0, [no_pnl], "")
        else:
            v[key] = None
    # sizes (as held)
    kind = (row or {}).get("kind", "")
    sizes = _sizes(data, ids, one_root=level in ("group", "book"))
    if leaf and level == "trade":
        ti = info.get(leaf) or {}
        q = ti.get("qty")
        v.update(net=q, net_text=_cap(_size_text(q, ti.get("unit", ""))), gross=None, gross_text="",
                 size_hover="the fill's quantity as booked")
    elif kind in ("pair", "spread") and shown is None:
        g = sizes if sizes["net"] is not None else _sizes(data, node["tids"])
        v.update(net=row.get("size_num"), net_text=_cap(str(row.get("size") or "")),
                 gross=g["gross"], gross_text=_cap(_size_text(g["gross"], g["unit"]).replace("long ", "")) if g["gross"] is not None else "",
                 size_hover=f"the spread's size as held (the smaller side paired in full); {row.get('size_hover') or ''}".strip("; "),
                 gross_hover=g["mixed"] or "the legs' lots added up, each contract netted first")
    elif kind == "leg" and row.get("part") == PART_PAIR and shown is None:
        lots = _num(row.get("lots"))
        unit = "t" if row.get("instrument_group") == LME_GROUP else "lots"
        v.update(net=lots, net_text=_cap(_size_text(lots, unit)), gross=abs(lots) if lots is not None else None,
                 gross_text=_size_text(abs(lots), unit).replace("long ", "") if lots else "",
                 size_hover=row.get("size_hover") or "this pair's lots on the contract")
    elif level == "group" and node.get("commodity_sub") and shown is None:
        sub = node["commodity_sub"]
        net_units, unit = _num(sub.get("net_units")), str(sub.get("unit") or "")
        v.update(net=net_units if sizes["net"] is None else sizes["net"],
                 net_text=(_cap(_units_words(net_units, unit)) if net_units is not None else
                           _cap(_size_text(sizes["net"], sizes["unit"])) if sizes["net"] is not None else ""),
                 gross=sizes["gross"], gross_text=_size_text(sizes["gross"], sizes["unit"]).replace("long ", "") if sizes["gross"] else "",
                 size_hover=str(sub.get("units_note") or f"the commodity's net as held, in {unit} (curve-positions' figure)"),
                 mixed=sizes["mixed"])
    else:
        v.update(net=sizes["net"], net_text=_cap(_size_text(sizes["net"], sizes["unit"])) if sizes["net"] is not None else "",
                 gross=sizes["gross"],
                 gross_text=_size_text(sizes["gross"], sizes["unit"]).replace("long ", "") if sizes["gross"] else "",
                 size_hover="the open fills' quantities, each contract netted first", mixed=sizes["mixed"])
    # fill / mark / prev / move
    if leaf and (level == "trade" or row is None or kind == "trade"):
        ti = info.get(leaf) or {}
        unit = ti.get("price_unit", "")
        fill, mark, prev = ti.get("fill"), ti.get("mark"), ti.get("prev")
        move = (mark - prev) if mark is not None and prev is not None else None
        d = price_decimals_for(unit, fill)
        v.update(fill=fill, fill_text=price_text(fill, unit, fill) if fill is not None else "",
                 mark=mark, mark_text=price_text(mark, unit, fill) if mark is not None else NA,
                 mark_hover=(f"the official mark on {data['as_of']}: {ti.get('mark_source') or 'source not named'}"
                             f"{', dated ' + ti['mark_date'] if ti.get('mark_date') else ''}" if mark is not None
                             else ti.get("reason") or "no mark"),
                 prev=prev, prev_text=price_text(prev, unit, fill) if prev is not None else NA,
                 prev_hover=(f"the mark on the Daily's reference close ({data.get('ref_used') or 'no reference date'})"
                             if prev is not None else f"no mark on the Daily's reference close ({data.get('ref_used') or 'none'})"),
                 move=move, move_text=signed_number(move, d) if move is not None else NA,
                 move_hover=f"mark less the {data.get('ref_used')} close's" if move is not None else "no mark on one of the two closes",
                 unit=unit if level != "trade" else "", direction=(1.0 if (ti.get("qty") or 0) > 0 else -1.0) if ti.get("qty") else None)
        if ti.get("status") not in ("OPEN", ""):
            v.update(mark_text="", prev_text="", move_text="", mark_hover="", prev_hover="", move_hover="", move=None,
                     mark=None, prev=None)
    elif row is not None and kind != "settled":
        prev, prev_text, prev_hover = None, "", ""
        if kind in ("pair", "spread"):
            src = _engine_pair(data, row.get("pair_id") or "") if kind == "pair" else _engine_position(data, row["id"])
            prev = _num((src or {}).get("level_prev"))
            unit = str((src or {}).get("unit") or (src or {}).get("level_unit") or "")
            prev_text = level_text(prev, unit) if prev is not None else NA
            prev_hover = (f"the level on the {(src or {}).get('level_prev_date') or 'reference'} close" if prev is not None
                          else str((src or {}).get("level_prev_reason") or "no level on the reference close"))
        else:
            prev = _prev_mark(data, row.get("detail_trade_ids") or row["trade_ids"])
            unit = row.get("unit") or ""
            prev_text = price_text(prev, unit, row.get("entry")) if prev is not None else NA
            prev_hover = (f"the mark on the Daily's reference close ({data.get('ref_used')})" if prev is not None
                          else f"no mark on the Daily's reference close ({data.get('ref_used') or 'none'})")
        v.update(fill=row.get("entry"), fill_text=row.get("entry_text") or "", fill_hover=row.get("entry_hover") or "",
                 mark=row.get("now"), mark_text=row.get("now_text") or "", mark_hover=row.get("now_hover") or "",
                 prev=prev, prev_text=prev_text, prev_hover=prev_hover,
                 move=row.get("move"), move_text=row.get("move_text") or "",
                 move_hover="\n".join(t for t in (row.get("move_hover"), row.get("sigma_hover")) if t),
                 unit=row.get("unit") or "", ratio=bool(row.get("ratio")),
                 direction=row.get("direction") if row.get("direction") is not None else
                 ((1.0 if (_num(row.get("lots")) or 0) > 0 else -1.0) if _num(row.get("lots")) else None))
    # carry (research): a calendar position or pair, matched by the engine's id
    v["carry"], v["carry_row"] = None, None
    if level == "position" and kind in ("pair", "spread"):
        cid = str(row.get("pair_id") or "") if kind == "pair" else str(row.get("id") or "")
        crow = ((data.get("carry") or {}).get("by_id") or {}).get(cid)
        if crow is not None and crow.get("calendar"):
            v["carry"], v["carry_row"] = _num(crow.get("roll_down_usd")), crow
    # research
    r = (row or {}).get("research") if kind == "pair" and level == "position" else None
    v.update(z=(r or {}).get("z") if r and not r.get("reason") else None,
             pctile=(r or {}).get("pctile") if r and not r.get("reason") else None, research=r)
    # P&L in the contract's currency: a fill, a leg, a one-contract position
    if level == "trade" or level == "leg" or (level in ("position", "instrument") and kind in _ONE_CONTRACT and kind != "settled"):
        v["local"], v["local_ccy"], v["local_why"] = _local(data, ids)
    else:
        v["local"], v["local_ccy"], v["local_why"] = None, "", ""
    # notional (the engine's), next
    if row is not None and level != "trade" and kind != "settled":
        v.update(gross_usd=row.get("gross"), net_usd=row.get("net"), n_excl=0, n_reasons=[row.get("notional_reason") or ""],
                 n_source=row.get("notional_source") or "", has_notional=True, next=row.get("next"))
    elif level in ("group", "book"):
        g_pairs, n_pairs, nexts = [], [], []
        excl, reasons = 0, []
        for c in node["children"]:
            if (c["level"] == "trade" or (c["level"] == "group" and c["label"] == SETTLED_GROUP)
                    or (shown is not None and not any(t in shown for t in c["tids"]))):
                continue
            cv = node_values(data, c, shown) if c["level"] == "group" else None
            crow = c.get("row") or {}
            if cv is not None:
                g, n, ex, why = cv["gross_usd"], cv["net_usd"], cv["n_excl"], cv["n_reasons"]
            else:
                g, n, ex, why = crow.get("gross"), crow.get("net"), 0, [f"{c['label']}: {crow.get('notional_reason') or 'no notional'}"]
            if g is None:
                excl += max(ex, 1)
                reasons += why
            else:
                excl += ex
                reasons += why if ex else []
                g_pairs.append((g, ""))
                n_pairs.append((n, ""))
            nxt = (cv or {}).get("next") if cv is not None else crow.get("next")
            if nxt and nxt.get("iso"):
                nexts.append(nxt)
        gross = sum_known(g_pairs)[0]
        v.update(gross_usd=gross, net_usd=sum_known(n_pairs)[0] if gross is not None else None, n_excl=excl,
                 n_reasons=reasons, n_source="the rows' engine figures summed", has_notional=bool(g_pairs or excl),
                 next=min(nexts, key=lambda n: (str(n["iso"]), 1 if n.get("estimated") else 0)) if nexts else None)
    else:
        v.update(gross_usd=None, net_usd=None, n_excl=0, n_reasons=[], n_source="", has_notional=False, next=None)
    nxt = v.get("next") or {}
    v["bd"] = nxt.get("bd") if nxt else None
    return v


# --- filters and sort
GRID_COLUMNS = (
    ("name", "Name", "l"), ("type", "Type", "l"), ("sector", "Sector", "l"), ("net", "Net lots", "l"),
    ("gross", "Gross lots", "l"), ("fill", "Fill / Entry", ""), ("mark", "Mark / Level", ""), ("prev", "Prev close", ""),
    ("move", "Move", ""), ("carry", "Carry / mo", "book-research"), ("z", "z", "book-research"), ("pctile", "%ile", "book-research"), ("daily", "Daily", ""),
    ("mtd", "MTD", ""), ("ytd", "YTD", ""), ("ltd", "LTD", ""), ("local", "P&L local", ""), ("gross_usd", "Gross USD", ""),
    ("net_usd", "Net USD", ""), ("next", "Next", "l"))
MULTI_FILTERS = ("type", "sector", "commodity", "local")     # the multi-select dropdowns (local = the currency)
TEXT_FILTERS = ("name",)
NUMERIC_FILTERS = tuple(k for k, _l, _c in GRID_COLUMNS if k not in ("name", "type", "sector", "local"))
FILTER_KEYS = TEXT_FILTERS + MULTI_FILTERS + NUMERIC_FILTERS
_CMP = re.compile(r"^\s*(>=|<=|!=|==|=|>|<)?\s*([-+−]?\s*\d[\d,]*(?:\.\d*)?|[-+−]?\s*\.\d+)\s*([kmb])?\s*$", re.I)
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}


def parse_compare(text: Optional[str]) -> Optional[Tuple[str, float]]:
    """'> 0', '< -10000', '>= 1.5m', '= 3', '10k' (equal) -> (op, value); None for an empty or
    unreadable box (then it filters nothing)."""
    m = _CMP.match(str(text or "")) if text else None
    if not m:
        return None
    op = {"==": "=", None: "="}.get(m.group(1), m.group(1))
    num = float(m.group(2).replace(",", "").replace("−", "-").replace(" ", ""))
    return op, num * _SCALE.get((m.group(3) or "").lower(), 1.0)


def _passes(value: Optional[float], test: Tuple[str, float]) -> bool:
    if value is None:
        return False
    op, x = test
    return {"=": value == x, "!=": value != x, ">": value > x, "<": value < x, ">=": value >= x, "<=": value <= x}[op]


def _numeric(v: dict, key: str) -> Optional[float]:
    if key in PERIODS:
        return (v.get(key) or (None,))[0]
    if key == "next":
        return v.get("bd")
    return _num(v.get(key))


def active_filters(filters: Optional[dict]) -> dict:
    """The filters that filter something: {key: text | [values] | (op, x)}."""
    out: Dict[str, Any] = {}
    for key, value in (filters or {}).items():
        if key in TEXT_FILTERS and str(value or "").strip():
            out[key] = str(value).strip().lower()
        elif key in MULTI_FILTERS and value:
            out[key] = list(value)
        elif key in NUMERIC_FILTERS:
            test = parse_compare(value)
            if test is not None:
                out[key] = test
    return out


def shown_trades(data: dict, nodes: Sequence[dict], filters: dict) -> Optional[set]:
    """The fills that pass every filter (None when none is set). A fill is tested on its own value
    of a column, else on the nearest row above it that has one (a z-score is its pair's)."""
    if not filters:
        return None
    info, pos = trade_info(data), position_map(data)
    memo: Dict[str, dict] = {}
    keep: set = set()

    def values(n: dict) -> dict:
        if n["id"] not in memo:
            memo[n["id"]] = node_values(data, n)
        return memo[n["id"]]

    def walk(n: dict, chain: List[dict]) -> None:
        chain = chain + [n]
        if n.get("leaf"):
            tid = n["leaf"]
            ti = info.get(tid) or {}
            prow = pos.get(tid) or {}
            ok = True
            for key, test in filters.items():
                if key == "name":
                    words = " ".join([c["label"] + " " + c.get("sub", "") for c in chain]
                                     + [tid, ti.get("instrument_id", ""), ti.get("strategy", "")]).lower()
                    ok = test in words
                elif key == "type":
                    ok = (prow.get("type_group") or SETTLED_GROUP) in test
                elif key == "sector":
                    ok = (prow.get("sector_key") if prow.get("kind") != "settled" else ti.get("sector")) in test
                elif key == "commodity":
                    ok = ti.get("commodity") in test
                elif key == "local":
                    ok = ti.get("currency") in test
                else:
                    value = None
                    for c in reversed(chain):
                        value = _numeric(values(c), key)
                        if value is not None:
                            break
                    if value is None and key in ("z", "pctile") and prow.get("kind") == "pair":
                        research = prow.get("research") or {}
                        value = _num(research.get(key)) if not research.get("reason") else None
                    ok = _passes(value, test)
                if not ok:
                    break
            if ok:
                keep.add(tid)
            return
        for c in n["children"]:
            walk(c, chain)

    for n in nodes:
        walk(n, [])
    return keep


def filter_options(data: dict) -> Dict[str, List[dict]]:
    """The choices of the four dropdowns, from the book on file."""
    rows, _settled = open_rows(data)
    info = trade_info(data)
    types = [g for g in TYPE_GROUP_ORDER if any(r["type_group"] == g for r in rows)] + [SETTLED_GROUP]
    sectors = sorted({r["sector_key"] for r in rows} | {t["sector"] for t in info.values()})
    commodities = sorted({t["commodity"] for t in info.values()})
    ccys = sorted({t["currency"] for t in info.values() if t["currency"]})
    return {k: [{"label": x, "value": x} for x in vals]
            for k, vals in (("type", types), ("sector", sectors), ("commodity", commodities), ("local", ccys))}


def _sort_key(v: dict, key: str, node: dict) -> Any:
    if key == "name":
        return str(node["label"]).lower()
    if key == "type":
        row = node.get("row") or {}
        return str(row.get("type_text") or "")
    if key == "sector":
        row = node.get("row") or {}
        return str(row.get("sector_key") or "")
    if key == "local":
        return v.get("local")
    return _numeric(v, key)


def _sorted(nodes: Sequence[dict], values: Dict[str, dict], sort: Optional[dict]) -> List[dict]:
    """Siblings in the order asked (missing figures last, the settled group last), else as built."""
    key = (sort or {}).get("key")
    if key not in {k for k, _l, _c in GRID_COLUMNS}:
        return list(nodes)
    fixed = [n for n in nodes if n["label"] == SETTLED_GROUP and n["level"] == "group"]
    rest = [n for n in nodes if n not in fixed]
    known = [n for n in rest if _sort_key(values[n["id"]], key, n) is not None]
    missing = [n for n in rest if _sort_key(values[n["id"]], key, n) is None]
    known.sort(key=lambda n: _sort_key(values[n["id"]], key, n), reverse=bool(sort.get("desc")))
    return known + missing + fixed


def next_sort(current: Optional[dict], key: str) -> dict:
    """The sort after a click on `key`: the same column flips, a new one starts descending for a
    figure and ascending for words."""
    if (current or {}).get("key") == key:
        return {"key": key, "desc": not bool(current.get("desc"))}
    return {"key": key, "desc": key not in ("name", "type", "sector", "next")}


def is_open(node: dict, state: Optional[dict]) -> bool:
    state = state or {}
    base = state.get("base") or "default"
    opened = node["default_open"] if base == "default" else base == "open"
    return (not opened) if node["id"] in set(state.get("flip") or []) else opened


# --- the rows
def _blank() -> html.Td:
    return html.Td("")


def _full_td(value: Optional[float], reasons: Sequence[str] = (), note: str = "") -> html.Td:
    """A fill's money cell at the full figure (the kit: trade rows at full figures)."""
    if value is None:
        return html.Td(missing_cell("; ".join(reasons) or "no figure"))
    text = f"{value:,.0f}".replace("-", MINUS)
    text = ("+" + text) if value > 0 and text not in ("0",) else text
    return html.Td(html.Span(text, className=sign_class(value) or None,
                             title="\n".join(t for t in (full_usd(value), note) if t)))


def _name_td(node: dict, v: dict, has_children: bool, opened: bool, detail_open: bool) -> html.Td:
    children: List[Any] = []
    if has_children:
        children.append(html.Span("▾" if opened else "▸", id={"type": TOGGLE_TYPE, "idx": node["id"]}, n_clicks=0,
                                  className="book-chevron", title="Collapse" if opened else "Expand"))
    else:
        children.append(html.Span("", className="book-chevron book-chevron--none"))
    if node.get("detail"):
        children.append(html.Span(node["label"], id={"type": ROW_TYPE, "idx": node["id"]}, n_clicks=0,
                                  className="book-name-link" + (" book-name-link--open" if detail_open else ""),
                                  title=plain_words(node.get("hover") or "") + "\nClick for its chart, fills and legs."))
    else:
        children.append(html.Span(node["label"], title=plain_words(node.get("hover") or "") or None))
    if node.get("sub"):
        children.append(html.Span(node["sub"], className="name-sub"))
    row = node.get("row") or {}
    entry = row.get("coverage") if node["level"] == "position" else node.get("coverage")
    coverage = coverage_words(entry)
    if coverage:
        children.append(html.Span(coverage, className="book-coverage"))
    return html.Td(children, className=f"l book-name book-d{node['depth']}")


def coverage_words(entry: Optional[dict]) -> List[Any]:
    """'CNH 92 % hedged', small and muted after a name (the engine's `hedge_coverage_net`, its
    hover as `hedge_coverage_children` gives it); [] when the entry has no CNY legs."""
    parts = hedge_coverage_children(entry, words="")
    if not parts:
        return []
    if isinstance(parts[0], str):                  # no figure: the dash with the engine's reason
        return ["CNH hedged ", *parts[1:]]
    return ["CNH ", *parts[:1], " hedged", *parts[1:]]


def _carry_td(crow: Optional[dict]) -> html.Td:
    """Carry / mo (research): the roll-down in USD per month, k / m, the roll-down in the spread's
    unit and the engine's note on hover; blank off a calendar; a dash with the reason when the
    research curve could not give it."""
    if not crow:
        return html.Td("", className="book-research")
    usd = _num(crow.get("roll_down_usd"))
    months = _num(crow.get("horizon_months")) or 1.0
    unit = str(crow.get("unit") or "")
    roll = _num(crow.get("roll_down"))
    hover = "\n".join(t for t in (
        f"roll-down {signed_number(roll, level_decimals(unit))} {unit} over {months:g} month(s)" if roll is not None else "",
        f"{full_usd(usd)} a month on the position" if usd is not None else "",
        str(crow.get("note") or ""), str(crow.get("reason") or ""),
        "research: the research app's curve, context only, never in P&L or a total") if t)
    if usd is None:
        return html.Td(missing_cell(hover or "no roll-down"), className="book-research")
    return html.Td(html.Span(signed_money(usd), className=sign_class(usd) or None, title=plain_words(hover)),
                   className="book-research")


def grid_tr(data: dict, node: dict, v: dict, marks: bool, opened: bool, detail_open: bool) -> html.Tr:
    """One row of the grid: the same columns at every level, blank where a column does not apply."""
    level, row = node["level"], node.get("row") or {}
    kind = row.get("kind", "")
    trade_level = level == "trade" or (node.get("leaf") and kind == "trade")
    cells: List[Any] = [_name_td(node, v, bool(node["children"]), opened, detail_open)]
    # type, sector
    if level == "position":
        if kind in ("pair", "spread") and row.get("trade_type"):
            cells.append(html.Td(type_cell(row["trade_type"], row.get("type_source", ""), row.get("type_note", "")),
                                 className="l book-type"))
        else:
            cells.append(html.Td(row.get("type_text") or "", className="l book-type"))
    else:
        cells.append(_blank())
    cells.append(html.Td(row.get("sector_key") or (_sector_group(row) if row and level == "instrument" else ""),
                         className="l book-type") if level in ("position", "instrument") else _blank())
    # net and gross lots, as held
    hover = ("as held: an option counts as its option lots, not at delta; delta is on the Exposure tab\n"
             + str(v.get("size_hover") or ""))
    if v.get("net_text"):
        cells.append(html.Td(v["net_text"], className="l", title=plain_words(hover)))
    elif v.get("mixed"):
        cells.append(html.Td(html.Span("mixed", className="cell-unit"), className="l", title=plain_words(v["mixed"])))
    else:
        cells.append(_blank())
    cells.append(html.Td(v.get("gross_text") or "", className="l",
                         title=plain_words(hover + "\n" + str(v.get("gross_hover") or "")) if v.get("gross_text") else None))
    # fill, mark, prev, move
    if "fill_text" in v:
        cells.append(html.Td(v["fill_text"], title=plain_words(v.get("fill_hover") or "") or None))
        mark: List[Any] = [v["mark_text"]]
        if v.get("mark") is not None and v.get("unit") and level != "trade":
            mark.append(unit_suffix(v["unit"]))
        if v.get("ratio"):
            mark.append(unit_suffix("ratio"))
        cells.append(html.Td(mark, title=plain_words(v.get("mark_hover") or "") or None,
                             className="cell-missing" if v["mark_text"] == NA else None))
        cells.append(html.Td(v.get("prev_text") or "", title=plain_words(v.get("prev_hover") or "") or None,
                             className="cell-missing" if v.get("prev_text") == NA else None))
        effect = v["move"] * v["direction"] if v.get("move") is not None and v.get("direction") else None
        move_hover = "\n".join(t for t in (v.get("move_hover"), "in the position's favour" if effect and effect > 0 else
                                           "against the position" if effect and effect < 0 else "") if t)
        cells.append(html.Td(v.get("move_text") or "", title=plain_words(move_hover) or None,
                             className=("cell-missing" if v.get("move_text") == NA else sign_class(effect) or None)))
    else:
        cells += [_blank(), _blank(), _blank(), _blank()]
    cells.append(_carry_td(v.get("carry_row")))
    # research
    r = v.get("research")
    if r:
        if r.get("reason"):
            cells += [html.Td(missing_cell(r["reason"]), className="book-research") for _ in range(2)]
        else:
            hover_r = plain_words(research_hover(r))
            cells.append(html.Td(signed_number(r["z"], 2) if r.get("z") is not None else
                                 missing_cell(f"no z-score for {r['key']}: not enough history in the research app"),
                                 className="book-research", title=hover_r))
            cells.append(html.Td(f"{r['pctile']:.0f}" if r.get("pctile") is not None else
                                 missing_cell(f"no 5-year percentile for {r['key']} in the research app"),
                                 className="book-research", title=hover_r))
    else:
        cells += [html.Td("", className="book-research"), html.Td("", className="book-research")]
    # P&L
    bold = level == "group"
    for key in PERIODS:
        p = v.get(key)
        if p is None:
            cells.append(_blank())
            continue
        value, excluded, reasons, note = p
        extra = "\n".join(t for t in (note, row.get("split_hover", "") if key == "daily" and level == "position" else "") if t)
        if trade_level and level == "trade":
            cells.append(_full_td(value, reasons, extra))
        else:
            cells.append(_money_td(value, excluded, reasons, extra, bold=bold, markers=marks))
    # P&L in the contract's currency
    if v.get("local_ccy") or v.get("local_why"):
        if v.get("local") is None:
            cells.append(html.Td(missing_cell(v.get("local_why") or "no local P&L")))
        else:
            loc = v["local"]
            text = f"{loc:,.0f}".replace("-", MINUS)
            cells.append(html.Td([html.Span(("+" + text) if loc > 0 else text, className=sign_class(loc) or None),
                                  unit_suffix(v["local_ccy"])],
                                 title=f"{v['local_ccy']} {loc:,.2f}: the P&L in the contract's currency, never summed "
                                       "across currencies"))
    else:
        cells.append(_blank())
    # notional
    if v.get("has_notional") and kind != "settled":
        why = "; ".join(str(x) for x in (v.get("n_reasons") or [])[:LINES_ON_HOVER] if x) or "no notional"
        cells += _notional_tds(v.get("gross_usd"), v.get("net_usd"), v.get("n_excl", 0), v.get("n_reasons") or [],
                               v.get("n_source") or "", marks, marks, bold=bold)
        if level != "group" and v.get("gross_usd") is None:
            cells[-2] = html.Td(missing_cell(why if marks else NO_MARKS_REASON))
            cells[-1] = html.Td(missing_cell(why if marks else NO_MARKS_REASON))
    else:
        cells += [_blank(), _blank()]
    if row.get("broken") and level == "position":
        text, why = row["broken"]
        net_td = cells[-1]
        net_td.children = list(net_td.children or []) + [
            html.Span(text, className="marker marker--amber marker--small", title=plain_words(why))]
    # next
    if v.get("next") is not None or (level in ("position", "leg", "instrument") and row):
        next_td = _next_td({"next": v.get("next"), "kind": kind})
        if row.get("next_marker") and level == "position":
            text, why = row["next_marker"]
            next_td.children = [next_td.children, html.Span(text, className="marker marker--amber marker--small",
                                                            title=plain_words(why))]
        cells.append(next_td)
    else:
        cells.append(_blank())
    cls = f"book-grid-row book-grid-{level}" + (" book-grid-muted" if row.get("no_pnl") else "")
    return html.Tr(cells, className=cls, **row_link_attrs(data, node))


def book_tr(data: dict, nodes: Sequence[dict], shown: Optional[set], marks: bool, n_open: Optional[int] = None,
            what: str = "positions") -> html.Tr:
    """The Book line, the grid's first body row since 2026-09-29 (it sticks under the heads):
    "Book · N open", every fill shown, the groups' notionals summed; '= header' when nothing is
    filtered, 'filtered' (and why it no longer equals the header) otherwise."""
    every = _node("book", "book", 0, BOOK_LABEL, tids=[t for n in nodes for t in n["tids"]], children=nodes)
    v = node_values(data, every, shown)
    if shown is None:
        note = "= header" if marks else "no marks on file"
        title = ("Every trade of the as-of book is in one row below, so this line is the header's Daily, MTD, YTD and "
                 "LTD: the known figures summed, what is left out named.")
    else:
        note = "filtered"
        title = "The fills the filters leave, summed: this is not the header's figure while a filter is set."
    label = f"{BOOK_LABEL} · {n_open:,} open" if n_open is not None else BOOK_LABEL
    if n_open is not None:
        title += f"\n{n_open:,} open {what} shown, the settled ones apart."
    cells: List[Any] = [html.Td([label, html.Span(note, className="book-note")], className="l book-name", title=title)]
    cells += [_blank() for _ in range(11)]
    for key in PERIODS:
        value, excluded, reasons, _note = v[key] if v.get(key) else (None, 0, [], "")
        cells.append(_money_td(value, excluded, reasons, bold=True, markers=marks))
    cells.append(_blank())
    cells += _notional_tds(v.get("gross_usd"), v.get("net_usd"), v.get("n_excl", 0), v.get("n_reasons") or [],
                           "the groups' figures summed", marks, marks, bold=True)
    cells.append(_next_td({"next": v.get("next"), "kind": "settled"}))
    return html.Tr(cells, className="book-total")


def grid_body(data: dict, view: str, state: Optional[dict] = None, sort: Optional[dict] = None,
              filters: Optional[dict] = None, detail: Optional[str] = None,
              conn: Optional[sqlite3.Connection] = None) -> Tuple[List[Any], str]:
    """(the grid's rows, its meta line): the Book line first, then the visible nodes of the view in
    the open state asked, siblings sorted, the fills filtered, the clicked row's detail under it."""
    marks = bool(data.get("marks_on_file"))
    nodes = grid_tree(data, view)
    flt = active_filters(filters)
    shown = shown_trades(data, nodes, flt)
    out: List[Any] = []
    counts = {"position": 0, "trade": 0}

    def emit(siblings: Sequence[dict]) -> None:
        visible = [n for n in siblings if shown is None or any(t in shown for t in n["tids"])]
        values = {n["id"]: node_values(data, n, shown) for n in visible}
        for n in _sorted(visible, values, sort):
            opened = is_open(n, state)
            out.append(grid_tr(data, n, values[n["id"]], marks, opened, detail == n["id"]))
            if detail == n["id"] and conn is not None and n.get("row") is not None:
                out.append(html.Tr(html.Td(detail_for(conn, data, n["row"], key=n["id"]), colSpan=len(GRID_COLUMNS)),
                                   className="book-detail-row"))
            if opened and n["children"]:
                emit(n["children"])

    emit(nodes)
    for n in nodes:
        for c in n["children"]:
            if c["level"] in ("position", "instrument") and (shown is None or any(t in shown for t in c["tids"])):
                counts["position"] += 1
        if n["level"] == "instrument" and (shown is None or any(t in shown for t in n["tids"])):
            counts["position"] += 1
    n_fills = len(shown) if shown is not None else len(trade_info(data))
    what = "positions" if view in (GROUP_TYPE, GROUP_SECTOR, GROUP_TRADE) else "contracts"
    out.insert(0, book_tr(data, nodes, shown, marks, counts["position"], what))
    meta = f"{counts['position']:,} {what} · {n_fills:,} fills" + (" shown (filtered)" if shown is not None else "")
    return out, meta


def grid_head(sort: Optional[dict] = None) -> html.Thead:
    """The head row: the column titles (a click sorts). The filters sit in the book card's strip above the
    table since the look pass of 2026-09-29."""
    titles = []
    for key, label, cls in GRID_COLUMNS:
        active = (sort or {}).get("key") == key
        arrow = ("▼" if sort.get("desc") else "▲") if active else ""
        titles.append(html.Th([label, html.Span(arrow, id={"type": SORT_ARROW_TYPE, "idx": key}, className="book-sort-arrow")],
                              id={"type": SORT_TYPE, "idx": key}, n_clicks=0,
                              className=" ".join(c for c in (cls, "book-sortable") if c),
                              title=plain_words(f"{GRID_TIPS.get(key, '')}\nClick to sort (within each group)."),
                              style={"minWidth": "280px"} if key == "name" else None))
    return html.Thead([html.Tr(titles)])


FILTER_LABELS = {"name": "Search", "commodity": "Commodity", "type": "Type", "sector": "Sector", "local": "Currency"}
NUMERIC_FILTERS_ABOUT = ("A comparison on the column's figure: > 0, < -10k, >= 1.5m, = 3 (Next: business days). "
                         "A fill is tested on its own figure, else on the nearest row above it that has one.")
FIGURES_TOGGLE_ID = "book-figures-toggle"   # "+ figures": shows / hides the comparison boxes
FIGURES_ROW_ID = "book-filter-nums"         # the comparison boxes' line, hidden unless asked for or one is set
_FIGURES_JS = (
    "function(n) {\n"
    "    var vals = Array.prototype.slice.call(arguments, 1);\n"
    "    var set = vals.filter(function(v) { return v !== null && v !== undefined && String(v).trim() !== ''; }).length;\n"
    "    var open = set > 0 || ((n || 0) % 2 === 1);\n"
    "    return [open ? {} : {display: 'none'}, (open ? '− figures' : '+ figures') + (set ? ' (' + set + ')' : '')];\n"
    "}"
)


def _strip_field(key: str, label: str, control: Any, cls: str = "", tip: Optional[str] = None) -> html.Div:
    return html.Div(className=" ".join(c for c in ("book-strip-field", cls) if c), title=tip,
                    children=[html.Label(label, htmlFor=filter_id(key)), control])


def grid_strip() -> html.Div:
    """The book card's top strip (2026-09-29, the look of the reference app's filter bar folded into
    the card): line 1 the view switch, the search and Download CSV; line 2 the four multi-selects
    ("All"), "+ figures", Expand all / Collapse all, Clear filters and the grid's meta; then the
    comparison boxes, hidden until "+ figures" or a value in one of them. The ids of the filter row
    this replaces; static, so a box keeps its focus while the rows re-render."""
    labels = {k: label for k, label, _cls in GRID_COLUMNS}
    line1 = html.Div(className="book-strip-line", children=[
        html.Span("Group by", className="book-strip-label"),
        _switch(VIEW_ID, VIEW_OPTIONS, DEFAULT_VIEW),
        dcc.Input(id=filter_id("name"), type="text", debounce=True, placeholder="Search name, contract, trade id",
                  className="blotter-filter-search book-strip-search", persistence=True, persistence_type="session"),
        html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                    title="The grid as shown (filters and sort applied, every level), at full figures"),
        dcc.Download(id=DOWNLOAD_ID)])
    line2 = html.Div(className="book-strip-line", children=[
        *[_strip_field(k, FILTER_LABELS[k], dcc.Dropdown(
            id=filter_id(k), multi=True, options=[], placeholder="All", className="blotter-filter-dropdown",
            persistence=True, persistence_type="session")) for k in ("commodity", "type", "sector", "local")],
        html.Button("+ figures", id=FIGURES_TOGGLE_ID, n_clicks=0, className="book-download book-tool",
                    title=NUMERIC_FILTERS_ABOUT),
        html.Button("Expand all", id=EXPAND_ALL_ID, n_clicks=0, className="book-download book-tool"),
        html.Button("Collapse all", id=COLLAPSE_ALL_ID, n_clicks=0, className="book-download book-tool"),
        html.Button("Clear filters", id=CLEAR_FILTERS_ID, n_clicks=0, className="book-download book-tool"),
        html.Span(id=GRID_META_ID, className="book-section-meta book-strip-meta")])
    nums = [_strip_field(k, labels[k], dcc.Input(
        id=filter_id(k), type="text", debounce=True, placeholder="any", className="book-filter-box book-filter-num",
        persistence=True, persistence_type="session"), "book-strip-field--num",
        "A comparison: > 0, < -10k, >= 1.5m, = 3" + (" (business days)" if k == "next" else ""))
        for k in NUMERIC_FILTERS]
    return html.Div(className="book-strip", children=[
        line1, line2, html.Div(nums, id=FIGURES_ROW_ID, className="book-strip-line book-filter-nums", style=HIDDEN)])


def book_card(table: html.Table, prepull: Any = None, under: Any = None, style: Optional[dict] = None) -> html.Div:
    """The one book card: the strip, the grid, and the footer (the line before the first pull, the
    last load on the left, "Data issues (N)" on the right, the drawer opening below it)."""
    return html.Div(id=GRID_SECTION_ID, className="book-card book-main", style=style, children=[
        grid_strip(),
        table,
        html.Div(className="book-foot", children=[
            html.Div(prepull, id=PREPULL_ID, className="book-foot-prepull"),
            html.Div(under, id=UNDER_ID, className="book-foot-under")])])


def filter_id(key: str) -> str:
    return f"book-f-{key}"


# --------------------------------------------------------------------------- Needs you, last load
_LIQ_LEVELS = ("RED", "AMBER")


def _event_hover(r: dict) -> str:
    return "; ".join(x for x in (
        f"{contract_label(r.get('contract_id'))}: {r.get('next_event') or 'event'} {r.get('next_event_date') or 'date unknown'}, "
        f"{r.get('level')}",
        f"counted to the alert date {r.get('alert_date')} ({r.get('alert_basis') or ''})".strip()
        if r.get("alert_date") and r.get("alert_date") != r.get("next_event_date") else "",
        str(r.get("reason") or "")) if x)


def needs(data: dict) -> List[dict]:
    """The Needs you card's rows, most urgent first: every first notice, last trade, expiry or
    prompt at EXPIRED / RED / AMBER with a real date (the roll calendar, `engine.expiry`), then
    every position RED / AMBER on liquidity (`engine.limits.liquidity`, research data with
    placeholder thresholds, → Risk). Each is {level, what, iso, bd, estimated, alert, hover, tab,
    idx}. The marks missing and the legs that could not be paired are Data issues lines
    (`need_notes`)."""
    out: List[dict] = []
    sched = (data.get("schedule") or {}).get("rows") or []
    urgent = [r for r in sched if r.get("level") in ("EXPIRED", "RED", "AMBER") and not r.get("estimated")]
    urgent.sort(key=lambda r: (_LEVEL_RANK.get(str(r.get("level")), 9),
                               r.get("business_days") if r.get("business_days") is not None else 10 ** 6))
    for r in urgent:
        inst = str(r.get("contract_id") or "")
        root_id = str(r.get("root_id") or "")
        root = data["roots"].get(root_id)
        name = (lme_name(root, root_id, str(inst.split(" ")[-1]) if " " in inst else None)
                if str(r.get("product")) == "LME_FWD" else contract_name(inst, root, root_id))
        event = _NEXT_EVENT_WORDS.get(str(r.get("next_event") or ""), str(r.get("next_event") or "event"))
        out.append({"level": str(r.get("level")), "what": f"{name} {event}", "iso": r.get("next_event_date"),
                    "bd": r.get("business_days"), "estimated": bool(r.get("estimated")), "alert": r.get("alert_date"),
                    "hover": _event_hover(r), "tab": "", "idx": ""})
    liq = data.get("liquidity") or {}
    note = str(liq.get("placeholder_note") or "")
    flagged = [p for p in liq.get("positions") or [] if str(p.get("level")) in _LIQ_LEVELS]
    for i, p in enumerate(flagged):
        days, oi = _num(p.get("days_to_exit")), _num(p.get("pct_of_oi"))
        bits = [b for b in (f"{days:.1f} days to exit" if days is not None else "",
                            f"{oi:.1%} of open interest" if oi is not None else "") if b]
        hover = "\n".join(t for t in (plain_words(p.get("reason")),
                                      f"weakest leg {p.get('weakest')}" if p.get("weakest") else "",
                                      "Research data (the research app's open interest and volume), read-only; the "
                                      "thresholds are placeholders.", note) if t)
        out.append({"level": str(p.get("level")),
                    "what": f"{p.get('name') or p.get('position_id')}: liquidity" + (f", {', '.join(bits)}" if bits else ""),
                    "iso": None, "bd": None, "estimated": False, "alert": None, "hover": hover,
                    "tab": "Risk", "idx": f"book-need-liquidity-{i}"})
    out.sort(key=lambda n: _LEVEL_RANK.get(n["level"], 9))
    return out


def needs_card(data: dict) -> Optional[html.Div]:
    """The Needs you card: a small table, Level | What | When | Go; None when nothing needs Jason."""
    items = needs(data)
    if not items:
        return None
    head = html.Tr([html.Th("Level", className="l"), html.Th("What", className="l"), html.Th("When", className="l"),
                    html.Th("Go", className="l")])
    body = []
    for n in items:
        lvl = n["level"]
        chip = html.Span(lvl, className=f"level-chip level-chip--{lvl.lower()}", title=plain_words(n["hover"]) or None)
        if n["iso"]:
            when: Any = date_cell(n["iso"], None if lvl == "EXPIRED" else n["bd"], n["estimated"], lvl, n["hover"],
                                  prefix="expired" if lvl == "EXPIRED" else "", alert_date=n["alert"])
        else:
            when = html.Span("now", className="cell-unit", title="today's position against the research app's latest "
                                                                  "open interest and volume")
        body.append(html.Tr([html.Td(chip, className="l"),
                             html.Td(n["what"], className="l book-needs-what", title=plain_words(n["hover"]) or None),
                             html.Td(when, className="l"),
                             html.Td(pointer(n["tab"], n["idx"], f"→ {n['tab']}") if n["tab"] else "", className="l")]))
    return html.Div(id=ALERTS_ID, className="book-card book-needs-card", children=[
        about("Needs you", NEEDS_ABOUT, level="div", className="book-section-title book-needs-title"),
        html.Table([html.Thead(head), html.Tbody(body)], className="book-table book-needs-table")])


def need_notes(data: dict, rows: Sequence[dict]) -> List[Any]:
    """The marks missing (→ Data) and the legs that could not be paired (→ Blotter): the first
    lines of the Data issues drawer, each with its link (the Needs you strip's until 2026-09-29).
    A marks error is its own drawer line (`issue_items`)."""
    out: List[Any] = []

    def line(label: str, text: str, hover: str, tab: str, idx: str) -> html.Span:
        return html.Span([html.Span(label, className="issue-label"), " ",
                          html.Span(text, title=plain_words(hover) or None), " ", pointer(tab, idx, f"→ {tab}")])

    needed, missing = data.get("needs") or (0, [])
    if missing and not data.get("needs_error"):
        names: List[str] = []
        for m in missing:
            inst = str(m.get("instrument_id") or "")
            info = data["instruments"].get(inst) or {}
            root_id = info.get("base_ccy", "")
            label = (contract_name(inst, data["roots"].get(root_id), root_id) if parse_contract_id(inst)
                     else f"{short_root_name(data['roots'].get(root_id), root_id)} {short_date(m.get('settle_date'))}".strip()
                     if root_id in data["roots"] else inst)
            if label not in names:
                names.append(label)
        out.append(line("Marks", f"{len(missing)} of {needed} marks missing: {', '.join(names[:3])}"
                                 + (f" +{len(names) - 3}" if len(names) > 3 else ""),
                        "\n".join(f"{m['instrument_id']} {plain_words(m['mark_type']).lower()} {m['settle_date']}"
                                  for m in missing[:LINES_ON_HOVER]), "Data", "book-need-1"))
    left = [r for r in rows if r["kind"] == "leg" and r.get("part") == PART_OUTRIGHT]
    review = (data.get("spreads") or {}).get("review") or []
    if left or review:
        bits = []
        if left:
            bits.append(f"{_plural(len(left), 'leg')} could not be paired: " + ", ".join(r["name"] for r in left[:3])
                        + (f" +{len(left) - 3}" if len(left) > 3 else ""))
        if review:
            bits.append(f"{_plural(len(review), 'set')} of trades could not be grouped")
        hover = "\n".join([f"{r['name']} (trade {r.get('group')})" for r in left[:LINES_ON_HOVER]]
                          + [plain_words(str(x.get("reason") or x.get("review_id") or "")) for x in review[:LINES_ON_HOVER]])
        out.append(line("Pairs", " · ".join(bits), hover, "Blotter", "book-need-2"))
    return out


def last_load_line(data: dict) -> html.Div:
    """One quiet line: what the last blotter load did (`upload_report`)."""
    if data.get("load_error"):
        return html.Div(id=LOAD_ID, className="book-last-load", children=[
            html.Span("Last load", className="book-last-load-k"), missing_cell(data["load_error"])])
    text, hover, clean = load_sentence(data.get("load"))
    children: List[Any] = [html.Span("Last load", className="book-last-load-k", title=LOAD_ABOUT),
                           html.Span(text, title=plain_words(hover) or None)]
    if not clean:
        children += [" · ", pointer("Data", "book-load-data", "what was skipped and why")]
    return html.Div(id=LOAD_ID, className="book-last-load", children=children)


# --------------------------------------------------------------------------- the row detail
def _thin(dates: Sequence[str], as_of: str, n: int = HISTORY_POINTS) -> List[str]:
    """At most `n` of the dates, evenly spread, plus the as-of."""
    dates = list(dates)
    if len(dates) > n:
        step = math.ceil(len(dates) / n)
        dates = dates[::step]
    if as_of not in dates:
        dates.append(as_of)
    return sorted(set(dates))


def level_history(conn: sqlite3.Connection, data: dict, row: dict, key: str = "") -> dict:
    """{dates, values, reasons, unit, entry, what, reason} of the row's chart, memoised on the
    database revision, the as-of and the row: a pair's or a spread's level from
    `engine.spreads.history.position_history` (the one level rule, read off the screens' filled
    reader), an outright's, option's, LME prompt's or FX trade's mark read off the same reader.
    Nothing is priced here beyond what the reader values on each date."""
    as_of = str(data["as_of"])

    def build() -> dict:
        from engine.spreads.history import history_dates, position_history
        from ui.tabs.blotter_pricing import priced_value_book, pricing_snapshot
        out: Dict[str, Any] = {"dates": [], "values": [], "reasons": [], "unit": row.get("unit") or "",
                               "entry": row.get("entry"), "what": "level", "reason": "", "note": ""}
        position = None
        if row["kind"] == "pair":
            p = _engine_pair(data, row.get("pair_id") or "") or {}
            spec = p.get("level_spec")
            if not spec:
                out["reason"] = str(p.get("level_now_reason") or "the pair has no level formula")
                return out
            ids = sorted({str(t) for leg in spec.get("legs") or [] for t in leg.get("trade_ids") or []})
            position = {"position_id": row["id"], "trade_ids": ids, "level_spec": spec,
                        "level_now_reason": p.get("level_now_reason")}
            out["unit"] = str(spec.get("unit") or "")
            if str(p.get("unit") or "").lower() == "ratio":
                out["entry"] = _num((p.get("level_alt") or {}).get("entry"))
                out["note"] = f"in {out['unit']}: the ratio on the row is China over foreign of the same prices"
            else:
                out["entry"] = _num(p.get("level_entry"))
        elif row["kind"] == "spread":
            position = _engine_position(data, row["id"])
            if not position or not position.get("level_spec"):
                out["reason"] = str((position or {}).get("level_now_reason") or "the spread has no level formula")
                return out
            out["unit"], out["entry"] = str(position.get("level_unit") or ""), _num(position.get("level_entry"))
        with pricing_snapshot(conn, "Book tab"):
            if position is not None:
                dates = _thin(history_dates(conn, position, as_of), as_of)
                hist = position_history(conn, position, dates, value_fn=priced_value_book)
                for pt in hist.get("points") or []:
                    out["dates"].append(pt["date"])
                    out["values"].append(_num(pt.get("level")))
                    out["reasons"].append(str(pt.get("level_reason") or ""))
                return out
            out["what"] = "mark"
            ids = [str(t) for t in (row.get("detail_trade_ids") or row["trade_ids"])]
            if not ids:
                out["reason"] = "no trade on this row"
                return out
            for day in _thin(history_dates(conn, ids, as_of), as_of):
                frame = priced_value_book(conn, day)[0]
                sub = frame[frame["trade_id"].astype(str).isin(ids)] if not frame.empty else frame
                value, why = None, f"no trade of this row valued on {day}"
                for _i, r in sub.iterrows():
                    m = _num(r.get("mark"))
                    if m is not None and not r.get("reason"):
                        value, why = m, ""
                        break
                    why = str(r.get("reason") or why)
                out["dates"].append(day)
                out["values"].append(value)
                out["reasons"].append(why)
        return out

    return _memo(f"history|{key or row['id']}", conn, as_of, build)


def level_chart(hist: dict) -> Any:
    """A small plotly line of the level (or mark) with a dashed line at the entry; the reason
    when there is nothing to draw."""
    known = [v for v in hist["values"] if v is not None]
    if not known:
        why = hist.get("reason") or "; ".join(dict.fromkeys(r for r in hist["reasons"] if r)) or "no level on file"
        return html.Div(["No chart: ", html.Span(plain_words(why))], className="book-quiet")
    import plotly.graph_objects as go
    fig = go.Figure(go.Scatter(x=hist["dates"], y=hist["values"], mode="lines+markers", connectgaps=False,
                               line={"color": "#0f1f3d", "width": 1.5}, marker={"size": 3},
                               hovertemplate="%{x}<br>%{y}<extra></extra>"))
    entry = hist.get("entry")
    if entry is not None:
        fig.add_hline(y=entry, line_dash="dash", line_color="#9ca3af", line_width=1,
                      annotation_text="entry", annotation_position="top left",
                      annotation_font={"size": 10, "color": "#6b7280"})
    fig.update_layout(height=200, margin={"l": 50, "r": 10, "t": 8, "b": 24}, plot_bgcolor="#fff",
                      paper_bgcolor="#fff", showlegend=False, font={"size": 11},
                      xaxis={"showgrid": False}, yaxis={"gridcolor": "#eef1f4", "title": hist.get("unit") or None})
    gaps = [r for v, r in zip(hist["values"], hist["reasons"]) if v is None]
    notes = []
    if gaps:
        notes.append(f"{_plural(len(gaps), 'close')} with no {hist['what']}: "
                     + "; ".join(dict.fromkeys(plain_words(r) for r in gaps if r))[:400])
    if hist.get("note"):
        notes.append(hist["note"])
    return html.Div([dcc.Graph(figure=fig, config={"displayModeBar": False}, style={"height": "200px"}),
                     *[html.Div(n, className="book-quiet") for n in notes]])


def _pair_payload(data: dict, row: dict) -> Optional[dict]:
    """A pair's legs for `detail_legs_table`, read off the engine's pair: its level formula's legs
    with the prices it read at entry, on the previous close and now, and the pair's lots."""
    p = _engine_pair(data, row.get("pair_id") or "")
    if p is None:
        return None
    spec = p.get("level_spec") or {}
    prices = p.get("level_prices") or {}
    if isinstance(prices, dict) and "alt" in prices:
        prices = prices.get("alt") or {}
    held = {str(leg.get("instrument_id")): leg for leg in p.get("legs") or []}
    legs = []
    for n, leg in enumerate(spec.get("legs") or p.get("legs") or []):
        mine = held.get(str(leg.get("instrument_id"))) or {}
        px = {}
        for key in ("entry", "prev", "now"):
            seq = prices.get(key) if isinstance(prices, dict) else None
            px[key] = _num(seq[n]) if isinstance(seq, (list, tuple)) and len(seq) > n else None
        legs.append({"instrument_id": leg.get("instrument_id"), "root_id": leg.get("root_id"),
                     "weight": leg.get("weight", mine.get("weight")), "qty_factor": leg.get("qty_factor"),
                     "conversion": leg.get("qty_conv"), "currency": leg.get("currency") or mine.get("currency"),
                     "price_scale": leg.get("price_scale"), "entry_price": px["entry"], "prev_price": px["prev"],
                     "now_price": px["now"], "lots": mine.get("lots"), "open_lots": mine.get("lots"),
                     "reason": "" if spec else str(p.get("level_now_reason") or "")})
    return {"legs": legs, "level_prev_date": p.get("level_prev_date"),
            **{k: p.get(k) for k in ("level_entry_reason", "level_prev_reason", "level_now_reason")}}


def detail_for(conn: sqlite3.Connection, data: dict, row: dict, key: str = "") -> Any:
    """The panel under Open trades for a clicked row: the chart of its level (or mark) since its
    first trade with the entry dashed, its fills (entries averaged), its legs."""
    ids = row.get("detail_trade_ids") or row["trade_ids"]
    head_children: List[Any] = []
    if row["kind"] == "settled":
        head_children = [about(f"{row['name']}" + (f" · {row['trade_name']}" if row.get("trade_name") else ""),
                               row["name_hover"], level="h5", style={"margin": "0 0 6px"}),
                         pointer("Blotter", "book-detail-trades", "→ Blotter")]
        return html.Div(className="section section--secondary book-detail", children=[
            html.Div(className="book-detail-head", children=head_children), _trade_lines(data, ids)])
    unit = row.get("unit") or ("ratio" if row.get("ratio") else "")
    summary = f"{row['name']} · {_cap(str(row['size']))} · entry {row['entry_text']} · level {row['now_text']} {unit}".strip()
    hist = level_history(conn, data, row, key)
    chart_title = "Level since the first trade" if hist["what"] == "level" else "Mark since the first trade"
    children: List[Any] = [
        html.Div(className="book-detail-head", children=[
            about(summary, row["name_hover"], level="h5", style={"margin": "0 0 6px"}),
            pointer("Blotter", "book-detail-trades", "→ Blotter")]),
        about(chart_title, f"From our own official marks through the screens' filled reader, at most {HISTORY_POINTS} "
                           "closes from the first trade date to the as-of; the dashed line is the entry.",
              level="div", className="section-kicker"),
        level_chart(hist),
        about(f"Fills · entry {row['entry_text']}", "Every fill of the row, open or closed, at full figures; the entry is "
                                                   "the lots-weighted average of the open fills (a pair's: its legs' "
                                                   "fills in its level formula).",
              level="div", className="section-kicker"),
        _trade_lines(data, ids),
    ]
    payload = None
    if row["kind"] == "pair":
        payload = _pair_payload(data, row)
    elif row["kind"] == "spread":
        payload = detail_payloads(data.get("spreads") or {}).get(row["id"])
    if payload and payload.get("legs"):
        children += [about("Legs", "Each leg's prices as quoted (entry = its lots-weighted average fill), and the factor "
                                   "that turns its price into the spread's unit.", level="div", className="section-kicker"),
                     detail_legs_table(payload)]
    return html.Div(className="section section--secondary book-detail", children=children)


# --------------------------------------------------------------------------- assembling the tab
def parts(data: dict, view: str = DEFAULT_VIEW, state: Optional[dict] = None, sort: Optional[dict] = None,
          filters: Optional[dict] = None, detail: Optional[str] = None, conn: Optional[sqlite3.Connection] = None,
          what: str = "all") -> dict:
    """The tab's pieces from `gather`'s output, each for its own placeholder of the static layout:
    `top` (the Needs you card, or nothing), `prepull` (the line before the first pull), `options`
    (the dropdowns' choices), `under` (the book card's footer: the last load and the Data issues
    drawer) when `what` is "all" or "top"; `rows` (the grid's rows, the Book line first) and
    `meta` when it is "all" or "grid"; `shown` (False for the empty state)."""
    view = view if view in dict(VIEW_OPTIONS) else DEFAULT_VIEW
    out = dict(_EMPTY_PARTS)
    if not data.get("n_total"):
        return {**out, "top": empty_state(data)}
    out["shown"] = True
    rows, settled = open_rows(data)
    marks = bool(data.get("marks_on_file"))
    if what in ("all", "top"):
        top: List[Any] = []
        if data.get("spreads_error"):
            top.append(message_box(data["spreads_error"]))
        card = needs_card(data)
        if card is not None:
            top.append(card)
        drawer = issues_drawer(need_notes(data, rows) + issue_items(data, rows + settled), id=ISSUES_ID)
        out.update(top=html.Div(top), options=filter_options(data),
                   prepull=None if marks else html.Div(PREPULL_TEXT, className="book-prepull", title=NO_MARKS_REASON),
                   under=[last_load_line(data), drawer or html.Div()])
    if what in ("all", "grid"):
        out["rows"], out["meta"] = grid_body(data, view, state, sort, filters, detail, conn)
    return out


def body(data: dict, view: str = DEFAULT_VIEW, state: Optional[dict] = None, sort: Optional[dict] = None,
         filters: Optional[dict] = None) -> Tuple[html.Div, str, dict]:
    """(the whole tab as one Div, the grid's meta, the toolbar's style): `parts` in the layout's
    order, for a direct render (the smoke test, a script)."""
    p = parts(data, view, state, sort, filters)
    if not p["shown"]:
        return html.Div([p["top"]]), "", HIDDEN
    table = html.Table([grid_head(sort), html.Tbody(p["rows"], id=TBODY_ID)], id=TABLE_ID, className="book-table book-grid")
    return html.Div([p["top"], book_card(table, p["prepull"], p["under"])]), p["meta"], {}


# --------------------------------------------------------------------------- CSV
def csv_frame(data: dict, view: str, sort: Optional[dict] = None, filters: Optional[dict] = None) -> pd.DataFrame:
    """The grid as shown (the filters and the sort applied, every level whatever is open), at full
    figures, one line per row with its level and its path."""
    nodes = grid_tree(data, view)
    shown = shown_trades(data, nodes, active_filters(filters))
    records: List[dict] = []

    def walk(siblings: Sequence[dict], path: List[str]) -> None:
        visible = [n for n in siblings if shown is None or any(t in shown for t in n["tids"])]
        values = {n["id"]: node_values(data, n, shown) for n in visible}
        for n in _sorted(visible, values, sort):
            v, row = values[n["id"]], n.get("row") or {}
            nxt = v.get("next") or {}
            rec = {"level": n["level"], "path": " > ".join(path), "name": n["label"], "note": n.get("sub") or "",
                   "type": row.get("type_text", "") if n["level"] == "position" else "",
                   "sector": row.get("sector_key", "") if n["level"] in ("position", "instrument") else "",
                   "net_lots": v.get("net"), "net_lots_words": v.get("net_text", ""), "gross_lots": v.get("gross"),
                   "fill_or_entry": v.get("fill"), "mark_or_level": v.get("mark"), "prev_close": v.get("prev"),
                   "move": v.get("move"), "unit": v.get("unit", ""), "research_carry_usd_per_month": v.get("carry"), "research_z": v.get("z"), "research_pctile": v.get("pctile")}
            for key in PERIODS:
                p = v.get(key)
                rec[f"{key}_usd"] = p[0] if p else None
                rec[f"{key}_excluded"] = p[1] if p else None
            rec.update(pnl_local=v.get("local"), pnl_local_ccy=v.get("local_ccy", ""), gross_usd=v.get("gross_usd"),
                       net_usd=v.get("net_usd"), flag=(row.get("broken") or ("", ""))[0] if n["level"] == "position" else "",
                       next_event=nxt.get("event", ""), next_date=nxt.get("iso", ""), next_estimated=nxt.get("estimated", ""),
                       trade_ids=" ".join(v["ids"]))
            records.append(rec)
            if n["children"]:
                walk(n["children"], path + [n["label"]])

    walk(nodes, [])
    return pd.DataFrame(records)


# --------------------------------------------------------------------------- shell
_EMPTY_PARTS = {"top": None, "prepull": None, "rows": [], "meta": "", "options": {}, "under": None, "shown": False}


def render_parts(as_of: Optional[str], db_path, view: str = DEFAULT_VIEW, state: Optional[dict] = None,
                 sort: Optional[dict] = None, filters: Optional[dict] = None, detail: Optional[str] = None,
                 what: str = "all") -> dict:
    """`parts` for `as_of`, from one read-only connection closed straight after. A problem is a
    message where the top would be, the other sections hidden."""
    if not as_of:
        return {**_EMPTY_PARTS, "top": message_box("No as-of date available.")}
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return {**_EMPTY_PARTS, "top": message_box(f"Database not available ({exc}).")}
    try:
        return parts(_gather(conn, as_of), view or DEFAULT_VIEW, state, sort, filters, detail, conn, what)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("book tab failed for %s", as_of)
        return {**_EMPTY_PARTS, "top": _problem(as_of, exc)}
    finally:
        conn.close()


def render(as_of: Optional[str], db_path, view: str = DEFAULT_VIEW) -> Tuple[Any, str, dict]:
    """(the whole tab as one Div, the grid's meta, the toolbar style) for `as_of`: `body` on one
    read-only connection closed straight after. A problem is a message where the body would be."""
    if not as_of:
        return message_box("No as-of date available."), "", {}
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc})."), "", {}
    try:
        return body(_gather(conn, as_of), view or DEFAULT_VIEW)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("book tab failed for %s", as_of)
        return _problem(as_of, exc), "", {}
    finally:
        conn.close()


def render_detail(node_id: Optional[str], as_of: Optional[str], db_path, view: str = DEFAULT_VIEW) -> Any:
    """The detail of one node of the view (a script's or a test's direct call; the app renders it
    inline under its row)."""
    if not node_id or not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        data = _gather(conn, as_of)
        found: List[dict] = []

        def walk(nodes: Sequence[dict]) -> None:
            for n in nodes:
                if n["id"] == node_id:
                    found.append(n)
                walk(n["children"])
        walk(grid_tree(data, view))
        if not found or found[0].get("row") is None:
            return None
        return detail_for(conn, data, found[0]["row"], key=found[0]["id"])
    except Exception as exc:  # noqa: BLE001 -- the reason under the row, never a 500
        log.exception("book tab detail failed for %s", node_id)
        return message_box(f"The position's detail could not be built ({type(exc).__name__}: {exc}).")
    finally:
        conn.close()


def render_csv(as_of: Optional[str], db_path, view: str, sort: Optional[dict] = None, filters: Optional[dict] = None):
    if not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        frame = csv_frame(_gather(conn, as_of), view if view in dict(VIEW_OPTIONS) else DEFAULT_VIEW, sort, filters)
        return dcc.send_data_frame(frame.to_csv, f"book-{as_of}.csv", index=False)
    except Exception:  # noqa: BLE001
        log.exception("book tab csv failed for %s", as_of)
        return None
    finally:
        conn.close()


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell (2026-09-29, one card): the title, the top placeholder (the Needs you card
    or the empty state), the book card (`book_card`: its strip with the view switch and the
    filters, static so a box keeps its focus; the grid, its head static and its body a
    placeholder; the footer with the line before the first pull, the last load and the drawer),
    the session stores (open rows, sort, the row whose detail is open) and the safety interval.
    No date picker."""
    return html.Div(className="book-tab", children=[
        html.Div(id=TOOLBAR_ID, className="book-title-row", children=[about("Book", TITLE_ABOUT, level="h3")]),
        html.Div(id=BODY_ID, children=[message_box("Loading the book...")]),
        book_card(html.Table([grid_head(), html.Tbody(id=TBODY_ID)], id=TABLE_ID, className="book-table book-grid"),
                  style=HIDDEN),
        dcc.Store(id=EXPAND_STORE_ID, storage_type="session"),
        dcc.Store(id=SORT_STORE_ID, storage_type="session"),
        dcc.Store(id=DETAIL_STORE_ID, storage_type="session"),
        dcc.Store(id=EMPTY_UPLOAD_SINK_ID),
    ])


build_layout = layout


def _clicked() -> bool:
    """True when the input that fired the callback was really clicked (a re-rendered chevron or
    name comes back with n_clicks 0, which must not count as a click)."""
    trig = dash.ctx.triggered or []
    return bool(trig and trig[0].get("value"))


def _filters_of(values: Sequence[Any]) -> dict:
    return dict(zip(FILTER_KEYS, values))


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The top (Needs you, the last load, the drawer, the dropdowns' choices) on the as-of,
    the data revision and the safety interval; the grid on those and the view, the open rows, the
    sort, the detail and the filters; the stores from the chevrons, the buttons, the column heads
    and the names; Clear filters; the CSV; the empty state's Upload button."""
    filter_inputs = [Input(filter_id(k), "value") for k in FILTER_KEYS]

    @app.callback(
        Output(BODY_ID, "children"),
        Output(TOOLBAR_ID, "style"),
        Output(GRID_SECTION_ID, "style"),
        Output(PREPULL_ID, "children"),
        Output(UNDER_ID, "children"),
        Output(UNDER_ID, "style"),
        *[Output(filter_id(k), "options") for k in MULTI_FILTERS],
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
    )
    def _update(as_of, _data_rev=None):
        p = render_parts(as_of, get_db_path(), what="top")
        shown = {} if p["shown"] else HIDDEN
        options = p.get("options") or {}
        return (compact(p["top"]), shown, shown, compact(p["prepull"]), compact(p["under"]), shown,
                *[options.get(k, []) for k in MULTI_FILTERS])

    @app.callback(
        Output(TBODY_ID, "children"),
        Output(GRID_META_ID, "children"),
        Output({"type": SORT_ARROW_TYPE, "idx": ALL}, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(VIEW_ID, "value"),
        Input(EXPAND_STORE_ID, "data"),
        Input(SORT_STORE_ID, "data"),
        Input(DETAIL_STORE_ID, "data"),
        *filter_inputs,
    )
    def _grid(as_of, _data_rev, view, state, sort, detail, *filter_values):
        p = render_parts(as_of, get_db_path(), view or DEFAULT_VIEW, state, sort, _filters_of(filter_values), detail,
                         what="grid")
        arrows = [(("▼" if (sort or {}).get("desc") else "▲") if (sort or {}).get("key") == k else "")
                  for k, _label, _cls in GRID_COLUMNS]
        return compact(p["rows"]), p["meta"], arrows

    @app.callback(Output(EXPAND_STORE_ID, "data"),
                  Input({"type": TOGGLE_TYPE, "idx": ALL}, "n_clicks"), Input(EXPAND_ALL_ID, "n_clicks"),
                  Input(COLLAPSE_ALL_ID, "n_clicks"), State(EXPAND_STORE_ID, "data"), prevent_initial_call=True)
    def _expand(_toggles, _all, _none, state):
        trig = dash.ctx.triggered_id
        if not _clicked():
            return dash.no_update
        if trig == EXPAND_ALL_ID:
            return {"base": "open", "flip": []}
        if trig == COLLAPSE_ALL_ID:
            return {"base": "closed", "flip": []}
        if isinstance(trig, dict):
            state = dict(state or {"base": "default", "flip": []})
            flip = list(state.get("flip") or [])
            nid = str(trig.get("idx") or "")
            state["flip"] = [f for f in flip if f != nid] if nid in flip else flip + [nid]
            return state
        return dash.no_update

    @app.callback(Output(SORT_STORE_ID, "data"), Input({"type": SORT_TYPE, "idx": ALL}, "n_clicks"),
                  State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _sort(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        return next_sort(current, str(trig.get("idx") or ""))

    @app.callback(Output(DETAIL_STORE_ID, "data"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  State(DETAIL_STORE_ID, "data"), prevent_initial_call=True)
    def _detail(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        nid = str(trig.get("idx") or "")
        return None if current == nid else nid

    app.clientside_callback(_FIGURES_JS, Output(FIGURES_ROW_ID, "style"), Output(FIGURES_TOGGLE_ID, "children"),
                            Input(FIGURES_TOGGLE_ID, "n_clicks"), *[Input(filter_id(k), "value") for k in NUMERIC_FILTERS])

    @app.callback(*[Output(filter_id(k), "value") for k in FILTER_KEYS], Input(CLEAR_FILTERS_ID, "n_clicks"),
                  prevent_initial_call=True)
    def _clear(n_clicks):
        if not n_clicks:
            return [dash.no_update] * len(FILTER_KEYS)
        return [[] if k in MULTI_FILTERS else "" for k in FILTER_KEYS]

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(VIEW_ID, "value"), State(SORT_STORE_ID, "data"), *[State(filter_id(k), "value") for k in FILTER_KEYS],
                  prevent_initial_call=True)
    def _csv(n_clicks, as_of, view, sort, *filter_values):
        if not n_clicks:
            return dash.no_update
        return render_csv(as_of, get_db_path(), view, sort, _filters_of(filter_values))

    app.clientside_callback(_OPEN_UPLOAD_JS, Output(EMPTY_UPLOAD_SINK_ID, "data"),
                            Input({"type": EMPTY_UPLOAD_TYPE, "idx": ALL}, "n_clicks"), prevent_initial_call=True)
