"""Book tab: the app's home. "What is my book, and what needs me?" Rebuilt on 2026-09-28 (the
screens tidy, wave 1). Vocabulary (user, 2026-09-28, the research app's Book): a **strategy** is
the spread type, cross exchange / cross product / term structure ("Unassigned" for a plain JSHY10
label), never Jason's PBRoot name; the PBRoot name (COPAR3, CATTLE, `trades.strategy`) is his
**trade**. The code keeps `strategy` as the field name of the PBRoot label (spreads-engine's
kind `strategy`, `trades.strategy`); every label, hover and CSV header says "trade". Earlier:
the approved mock is the brief's Main.dc.html, with Empty.dc.html for a
database with no trades and AfterUpload.dc.html for trades with no marks yet). One screen:

  1. The title line: "Book", the counts ("6 strategies · 5 spreads · 12 outrights · 3 options ·
     2 LME prompts · 8 FX hedges"), the group-by switch (Strategy, the default; Commodity;
     Instrument) and Download CSV. Since 2026-09-28 (Jason's real export: every trade carries his
     strategy name and the broker's trade type, and "a lot of trades are long short on the same
     commodity - the net exposure must be seen"; then, the same day, "he does need to be able to
     sort by strategy"; the definitive design approved later that day: strategy -> pairs ->
     legs): the Strategy view is one group per strategy read from spreads-engine's
     `strategies` entry: a pair row per pair the engine made inside it (its two legs' names,
     its type in words with "inferred" / "fallback" small, the paired size, the engine's gross
     and net on the paired lots, its level at entry and now in the pair's unit, the move, its
     legs' P&L, the earlier leg's next event, a "legs N bd apart" marker, a "residual" marker),
     its two leg rows under it (the pair's lots on each contract; a contract split over two
     pairs lists its trades once, on the first, and the other row says so instead of a figure),
     then the lots left outright under "Outright" and the strategy's USD/CNH futures and FX
     trades under "Hedge"; the group line carrying the strategy's type in words, its legs in
     words ("long COMEX copper 34 lots · short LME copper 350 t · net long 35.6 t", the net in
     one unit only where curve-positions' `by_subsector` conversion is this strategy's alone),
     "hedged 96 %" where it has CNY legs (the engine's `hedge_coverage_net`; an amber marker when
     the hedge runs with the exposure) and its subtotals, with the engine's Daily split ("spread
     +12.3k · FX −1.1k · hedge +0.9k") on the Daily's hover when it is whole and equals the
     Daily shown; the Commodity view nets the book per open contract across strategies, grouped by
     commodity across exchanges (copper = COMEX + LME), each commodity's group line carrying its
     net exposure in words from `by_subsector`; the Instrument view keeps one row per position (a
     strategy named by its own name, its legs on hover and in the detail). The trade type is on
     hover of the position name and on the strategy group line, never a column; a label that
     disagrees with the legs is one line of the Data issues drawer.
  2. The tiles: Strategies (spread types), Trades (PBRoot names), Positions, P&L today and P&L since entry (the header's own Daily
     and LTD, the same value and markers), Gross exposure, Net exposure (the engine's USD
     notional summed over the known positions, "excl. N" for the rest).
  3. The movers strip: the three best and three worst Daily figures among the rows.
  3a. The summary first (2026-09-28, the user on Jason's real blotter: "where is the pnl by
     strat"; the research app's Book shape): the "By strategy" table (`summary_table`), one
     line per strategy with its type, legs, open trades, P&L today / MTD / since entry, gross,
     net, its signed share of the Book's LTD and of the Book's gross (`_share`: part / whole of
     two summed figures, display), hedge coverage and earliest next event, sorted by gross,
     the Book line = the header; then "Break it down" (`breakdown_table`), the same columns by
     Commodity | Type | Instrument (`BREAKDOWN_ID`); then the positions table under a
     "Positions" fold, open by default, with its Strategy | Commodity | Instrument switch.
  4. The positions table, one row per open position: the spread positions of
     `engine.spreads.book_spreads(conn, as_of)["positions"]` (their levels from
     `engine/spreads/levels.py`), the outright futures one row per contract (`["outrights"]`),
     and one row per open trade for the options on futures, LME forwards and FX hedges from the
     shared filled reader (`ui/tabs/blotter_pricing.priced_value_book`). Columns: Position (plain
     names, the instrument ids and the type on hover), Size (words: "long 15 lots"), Gross and
     Net (the engine's USD notional: `book_spreads`' `gross_usd` / `net_usd` on a position or an
     outright, curve-positions' `notional_usd` on a netted contract whose trades are exactly that
     row's; a dash with the reason otherwise, never lots × multiplier × mark × spot here), Entry,
     Now (the unit as a small suffix), Move, Daily, MTD, LTD, Next. The settled and closed-out
     trades are one line of their own so that the Book line equals the header's Daily, MTD and
     LTD to the cent: every trade of the as-of book is in exactly one row, and every row's period
     figure is the P&L tab's own per-trade figure (`ui.tabs.pnl.period_rows`, the header's
     split), the known ones summed (display). A group line sums its rows' known figures and says
     "excl. N" when any is missing (no marker at all while the book has no marks: the Loaded
     line under the tiles says values come with the first pull); a missing cell is an em dash with its reason
     on hover. Nothing is recomputed: a spread's entry, level, move and $ per unit are
     spreads-engine's; an outright's Entry is the lots-weighted fill and its Move the mark less
     the Daily reference close's mark, both read off the value rows; a trade row's Entry is its
     fill and its Now its mark.
  5. A click on a row opens its detail under the table: a spread's entries and legs
     (`detail_payloads`, `members_table`, `detail_legs_table`), an outright's, a leg's or a
     trade row's trades, the settled line's list.
  6. "Needs you" (at most five lines, a chip, one sentence and a tab link): RED / AMBER events
     with real dates, DATES (the contracts on estimated dates), MARKS (the marks missing), GROUP
     (the trade sets the spread rule could not match), LIMITS.
  7. "Last load": one sentence from the persisted `upload_report`.
  8. One Data issues drawer.

Two states of the same screen: with no trades on file, the "No blotter loaded" card (the Upload
button and the sample link) beside "What happens next"; with trades but no official mark for the
book, one quiet "Loaded" line under the tiles, fills in Entry and dashes elsewhere. Since later
the same day the pair rows carry the research app's context (z, %ile, a sparkline; `pair_research`,
read-only, never in a figure) and the sections read the research app's shape (`section_head`).

Display rules: `ui.tabs.formatting` (2026-09-28). The tab has no date picker: it follows the
header's as-of store and re-renders in place on the data revision and its safety interval.
`layout(default_date)` (alias `build_layout`) and `register_callbacks(app, get_db_path)` are the
shell's interface. `contract_label`, `position_size_text` and `_spreads` are read by the P&L tab.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dash_table, dcc, html

from ui import sample_book
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import (
    HAND_KINDS, MINUS, MISSING, SOURCE_INFERRED, SOURCE_MIXED, TRADE_TYPE_TITLES, about, contract_name, date_cell,
    fx_name, issues_drawer, lme_name, missing_cell, money_cell, parse_contract_id, plain_words, price_text, quoted_unit,
    short_date, short_money, short_root_name, sign_class, signed_money, signed_number, size_words, spread_name,
    sum_known, tab_link, trade_type_words, type_disagrees, unit_suffix,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "book-body"
REFRESH_ID = "book-refresh"
TOOLBAR_ID = "book-toolbar"
GROUP_ID = "book-group-by"                 # the Positions fold's Strategy | Commodity | Instrument switch
TILES_ID = "book-tiles"
CSV_BUTTON_ID = "book-csv"
DOWNLOAD_ID = "book-download"
TABLE_ID = "book-table"
DETAIL_ID = "book-detail"
DETAIL_MEMBERS_ID = "book-detail-members"
DETAIL_LEGS_ID = "book-detail-legs"
ALERTS_ID = "book-alerts"
LOAD_ID = "book-load"
ISSUES_ID = "book-issues"
EMPTY_UPLOAD_TYPE = "book-empty-upload"    # the empty state's Upload button (clicks the top bar's input): a
EMPTY_UPLOAD_ID = {"type": EMPTY_UPLOAD_TYPE, "idx": "book"}   # pattern id, since the button exists only while the book is empty
EMPTY_UPLOAD_SINK_ID = "book-empty-upload-sink"
ROW_TYPE = "book-row"                      # the pattern id of a clickable row: {"type": ROW_TYPE, "idx": row id}

NA = MISSING
PERIODS = ("daily", "mtd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "mtd": "MTD", "ltd": "LTD"}
GROUP_COMMODITY, GROUP_STRATEGY, GROUP_INSTRUMENT = "commodity", "strategy", "instrument"
GROUP_OPTIONS = ((GROUP_STRATEGY, "Strategy"), (GROUP_COMMODITY, "Commodity"), (GROUP_INSTRUMENT, "Instrument"))
DEFAULT_GROUP = GROUP_STRATEGY
# the summary first (2026-09-28, the user on Jason's real blotter: "where is the pnl by strat"; the
# research app's Book shape): the By strategy table (one line per spread type), then "Break it down"
# by Trade (PBRoot name) | Commodity | Instrument, then the positions table folded open under it
SUMMARY_ID = "book-summary"
BREAKDOWN_ID = "book-breakdown-by"
BREAKDOWN_TABLE_ID = "book-breakdown"
BREAKDOWN_SECTION_ID = "book-breakdown-section"
POSITIONS_SECTION_ID = "book-positions"
POSITIONS_TABLE_ID = "book-positions-table"
UNDER_ID = "book-under"
SUMMARY_SECTION_ID = "book-summary-section"   # the By strategy title row (static: it holds the Download CSV button)
SUMMARY_TABLE_ID = "book-summary-table"       # the placeholder the By strategy table is rendered into
POSITIONS_SUMMARY_ID = "book-positions-meta"  # the Positions fold's summary meta: "· 23 · strategy → trade → pairs → legs"
RESEARCH_LABEL = "research"                   # the research app's context on a pair row (read-only, never in a figure)
RESEARCH_COLUMNS = ("z", "%ile", "")          # z-score, 5-year percentile, the sparkline
SPARK_W, SPARK_H = 90, 18                     # the sparkline, px
SPARK_DAYS = 365                              # the history the sparkline shows, calendar days to the as-of
SPARK_POINTS = 260                            # at most this many points on the polyline
FOLD_WORDS = {GROUP_STRATEGY: "strategy → trade → pairs → legs", GROUP_COMMODITY: "commodity → contract",
              GROUP_INSTRUMENT: "instrument → position"}
GROUP_TYPE = "type"                        # by strategy: the spread type of each position (the By strategy table)
GROUP_TRADE = "trade"                      # by trade: one line per PBRoot name (Break it down's default)
BREAKDOWN_OPTIONS = ((GROUP_TRADE, "Trade"), (GROUP_COMMODITY, "Commodity"), (GROUP_INSTRUMENT, "Instrument"))
DEFAULT_BREAKDOWN = GROUP_TRADE
NO_TYPE_GROUP = "Unassigned"               # a plain JSHY10 label: no strategy (spread type) on the trade
MIXED_GROUP = "Mixed labels"
STRATEGY_ORDER = ("CROSS_EXCHANGE", "CROSS_PRODUCT", "TERM_STRUCTURE")   # the research app's order
BOOK_LABEL = "Book"
SETTLED_GROUP = "Settled and closed out"
OTHER_GROUP = "Other"
OPTIONS_GROUP, FX_GROUP, LME_GROUP, FUTURES_GROUP = "Options on futures", "FX hedges", "LME forwards", "Futures"
NO_STRATEGY_GROUP = "No trade name"        # the rows whose fills carry no PBRoot name
LEG_PREFIX = "LEG"                          # a Strategy view row: LEG-<strategy>-<instrument>[-<prompt>]
PAIR_PREFIX = "PAIR"                        # a Strategy view pair row: PAIR-<pair_id> (strategy|contract a|contract b)
HEDGE_PREFIX = "HEDGE"                      # a Strategy view hedge row: HEDGE-<strategy>-<instrument>
PART_PAIR, PART_OUTRIGHT, PART_HEDGE = "pair", "outright", "hedge"   # the parts of a strategy, in this order
_PART_RANK = {PART_PAIR: 0, "": 1, PART_OUTRIGHT: 1, PART_HEDGE: 2}
FALLBACK_WORDS = "paired by a fallback rule, not by the trade's own strategy"
PART_LABELS = {
    PART_OUTRIGHT: ("Outright", "The trade's lots left after its pairs: whole lots with no leg to pair with, or "
                                "left over beyond a pair's exact match; an option or an FX product inside the "
                                "trade is listed here too."),
    PART_HEDGE: ("Hedge", "The trade's own USD/CNH futures and FX fills: the desk's hedge of the CNY legs. Gross "
                          "is the hedge's USD notional (lots × the contract's USD size, or the USD leg's amount)."),
}
NO_MARKS_REASON = "no marks on file yet: values appear after the first Bloomberg pull"
FX_SECTOR = "fx"                            # the SGX USD/CNH future's sector in config/contracts.csv
_SECTOR_ORDER = ("energy", "metals", "agriculture", "ferrous", FX_SECTOR)
CONTRACT_PRODUCTS = ("FUTURE", "CMDTY_OPTION", "LME_FWD")   # netted per contract in the Commodity view
LINES_ON_HOVER = 12
NEEDS_MAX = 5
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
_METAL_UNITS = {"XAU": "oz", "XAG": "oz", "XPT": "oz", "XPD": "oz"}
_NEXT_EVENT_WORDS = {"first notice": "first notice", "last trade": "last trade", "option expiry": "expiry",
                     "LME prompt": "prompt"}

TAB_KEYS = {"Book": "book", "Exposure": "curve", "P&L": "pnl", "Risk": "risk", "Trades": "blotter",
            "Data": "market-data"}

TITLE_ABOUT = ("The book at the header's as-of date. A strategy is the spread type (cross exchange, cross product, "
               "term structure), a trade is Jason's PBRoot name (COPAR3, CATTLE). By strategy: one line per strategy with "
               "the trades in it. Break it down: by trade, by commodity (the contracts netted across trades) or by "
               "instrument. Positions: every open position, by strategy then trade (each trade's pairs with their legs, "
               "the lots left outright, its hedges), by commodity or by instrument. Each row: its size, the engine's "
               "gross and net USD notional, its entry, its level now, today's move and its Daily, MTD and LTD P&L in "
               "USD. Every view adds up to the header. Click a row for its fills and legs.")
NEEDS_ABOUT = ("What needs you today, most urgent first, one line per kind: expiries within the alert window, the "
               "contracts still on estimated dates, the marks missing, the trade sets the spread rule could not "
               "match, the limits. A line whose detail is on another tab names it; a position's dates are in "
               "its own Next column below.")
LOAD_ABOUT = "What the last blotter load did, as the upload recorded it."
COLUMN_TIPS = {
    "Position": "The trade, spread, contract, option, LME prompt or FX hedge in plain words, the trade name (PBRoot) "
                "small beside it; the instrument ids and the strategy (cross exchange, cross product, term "
                "structure) on hover. Click a row for its fills and legs.",
    "Size": "The open size in words: a spread in lots of the near month or in its template's quantity unit, a future "
            "in lots, an LME prompt in tonnes, an FX hedge in its base currency.",
    "Gross": "The gross USD notional on the open lots (k / m, the full figure on hover): the engine's figure for a "
             "trade, a spread, an outright or a contract at the day's marks and spots. A dash with its reason "
             "where the engine gives none (an option's lots × price is a value, not a notional).",
    "Net": "The net USD notional, long positive, likewise.",
    "Entry": "A spread's level at entry (the lots-weighted fills of its legs), an outright's lots-weighted fill, a "
             "fill's price. Prices at tick precision.",
    "Now": "The level or mark at the day's official marks, with its unit.",
    "Move": "Now less the level or mark at the Daily's reference close; a spread's $ per unit on hover.",
    "Daily": "Daily P&L in USD (k / m, the full figure on hover), by the header's rule; a group line sums its rows' "
             "known figures and says what it leaves out.",
    "MTD": "MTD P&L in USD, likewise.",
    "LTD": "LTD P&L in USD, likewise.",
    "Next": "The position's next event (first notice, last trade, option expiry, LME prompt, FX value date) and the "
            "business days to it; grey with a leading ≈ while the date is estimated.",
    # the By strategy and Break it down tables (2026-09-28)
    "Strategy": "The strategy is the spread type, in the research app's words: cross exchange, cross product or term "
                "structure; 'Unassigned' for a plain JSHY10 label with no type. On a position it is the engine's: the "
                "broker's label where the legs agree with it, else read from the legs; 'labels disagree' when its "
                "fills carry different labels. Every trade of the book is in exactly one line, so the Book line is "
                "the header's figure.",
    "Trade": "Jason's trade name (the PBRoot label on his fills: COPAR3, CATTLE); 'No trade name' gathers the fills "
             "without one, 'Settled and closed out' the fills no longer open. Every fill of the book is in exactly "
             "one line, so the Book line is the header's figure.",
    "Trades": "The trades (PBRoot names) in this strategy; how many on hover.",
    "Legs": "The trade's open lots per contract, netted, and its net in one unit where the contracts convert.",
    "Open": "How many of the line's fills are open (every fill on file on hover).",
    "P&L today": "Daily P&L in USD by the header's rule (k / m, the full figure on hover); the engine's split "
                 "(spread, FX, hedge) on hover of a trade's figure.",
    "Since entry": "LTD P&L in USD: every fill of the line since the first, the settled ones at their frozen figure.",
    "% of P&L": "The line's LTD as a share of the Book's LTD, signed (a loss against a profitable book is negative); "
                "the shares add to 100. A dash while either figure is missing.",
    "% of exposure": "The line's gross USD notional as a share of the Book's gross.",
    "Hedged": "For a trade with CNY legs: how much of the CNY legs' signed net notional its FX hedge covers "
              "(the engine's figure, 100 % = fully hedged); an amber marker when the hedge runs with the exposure.",
    # the research context on a pair row (2026-09-28, the user's reversal of "no market context on the monitor")
    "z": "The research app's z-score of the pair's spread level against its own history (the app's primary window; "
         "its 1-year z on hover). Context, read-only: never in P&L, delta or a total.",
    "%ile": "The research app's 5-year percentile of the pair's spread level. Context, read-only.",
    "History": "The pair's spread level over the last year in the research app; the entry level a dashed line, the "
               "last point a dot. Context, read-only.",
}
RESEARCH_ABOUT = ("Context from the research app (RV Spreads), read-only: the z-score, the 5-year percentile and a "
                  "year of the spread's level, on a pair the research app tracks (its template id, or a calendar "
                  "of one root). Never a mark, never in P&L, delta or a total.")


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


def members_table(payload: Dict[str, Any]) -> dash_table.DataTable:
    """The position's entries: one row per member spread, its trade dates, accounts, size and
    entry level (a dash with its reason)."""
    unit = payload.get("unit") or ""
    rows, tips = [], []
    for m in payload.get("members") or []:
        size = _num(m.get("size"))
        entry = _num(m.get("level_entry"))
        rows.append({"spread_id": m.get("spread_id") or "", "trade_dates": ", ".join(m.get("trade_dates") or []),
                     "accounts": ", ".join(m.get("accounts") or []),
                     "size": NA if size is None else size_text(size, m.get("size_unit") or ""),
                     "entry": level_text(entry, unit), "trades": ", ".join(m.get("trade_ids") or []),
                     "status": m.get("status") or ""})
        tip: Dict[str, dict] = {}
        if entry is None:
            tip["entry"] = _tip(m.get("level_entry_reason") or "no entry level and no reason")
        tips.append(tip)
    columns = [rk.text("Entry", "spread_id"), rk.text("Trade date(s)", "trade_dates"),
               rk.text("Account(s)", "accounts"), rk.text("Size", "size"),
               rk.text(f"Entry level ({unit or 'no unit'})", "entry"), rk.text("Trades", "trades"),
               rk.text("Status", "status")]
    return dash_table.DataTable(
        id=DETAIL_MEMBERS_ID, columns=columns, data=rows, tooltip_data=tips, **rk.sortable(),
        **_table_styles([], [c["id"] for c in columns if c["id"] != "entry"], na_cols=("entry", "size")),
        page_action="none")


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
    """`build()` memoised on (kind, database path, mtime, as_of); a failure is not memoised."""
    rev = _db_revision(conn)
    if rev is None:
        return build()
    key = (kind, *rev, as_of)
    if key not in _MEMO:
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.clear()
        _MEMO[key] = build()
    return _MEMO[key]


# --------------------------------------------------------------------------- reading the lanes
def _failure(what: str, exc: Exception) -> str:
    return f"{what} ({type(exc).__name__}: {exc})"


def _spreads(conn: sqlite3.Connection, as_of: str) -> dict:
    """`book_spreads` through the screens' shared filled reader, one pricing snapshot, memoised
    on the database revision and the as-of (the P&L tab reads this too)."""
    def build():
        from engine.spreads import book_spreads
        from ui.tabs.blotter_pricing import priced_value_book, pricing_snapshot
        with pricing_snapshot(conn, "Book tab"):
            return book_spreads(conn, as_of, value_fn=priced_value_book)
    return _memo("spreads", conn, as_of, build)


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
        from engine.curve import curve_positions
        data["curve"], data["curve_error"] = curve_positions(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001 -- the commodity group lines then say why
        data["curve"], data["curve_error"] = None, _failure("the net exposure by commodity could not be built", exc)
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
        data["limits"], data["limits_error"] = limit_checks(conn, as_of), ""
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


def _commodity_of(root_ids: Sequence[str], product: str, data: dict) -> Tuple[str, str]:
    """(group label, subsector key) of a row for the Commodity view: an FX product or a root of
    sector 'fx' (the SGX USD/CNH future) under FX hedges; else the root's commodity across
    exchanges in plain words (`engine.curve.subsector_name`: 'Copper', 'Iron ore'); Other when
    the root is not in the universe."""
    if product in FX_PRODUCTS:
        return FX_GROUP, ""
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
        sector_group, instrument_group = FX_GROUP, FX_GROUP
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
        notional_reason = "an FX hedge's notional is its currency exposure, on the Exposure tab's currency card"
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


def strategy_positions(data: dict) -> List[dict]:
    """The open strategy positions (spreads-engine's kind `strategy`: Jason's PBRoot names)."""
    return [p for p in open_positions(data.get("spreads") or {}) if str(p.get("kind") or "") == "strategy"]


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
    """The research app's statistics and a year of level history for every pair of the strategies
    (`engine/risk/research_spreads.py`, read-only; mock data on this PC): {"keys": {pair_id: (sid,
    inst, reason)}, "stats": {(sid, inst): entry}, "history": {(sid, inst): {"values", "first",
    "last", "reason"}}, "reason": '' or why nothing could be read, "source"}."""
    from engine.risk.research_spreads import research_spread_history, research_spread_stats
    keys: Dict[str, Tuple[str, str, str]] = {}
    for entry in (data.get("spreads") or {}).get("strategies") or []:
        for p in entry.get("pairs") or []:
            keys[str(p.get("pair_id") or "")] = _pair_research_key(p)
    wanted = sorted({(sid, inst) for sid, inst, _why in keys.values() if sid})
    out: Dict[str, Any] = {"keys": keys, "stats": {}, "history": {}, "reason": "", "source": ""}
    if not wanted:
        return out
    stats = research_spread_stats(wanted, as_of)
    out["stats"], out["reason"], out["source"] = dict(stats.get("stats") or {}), str(stats.get("reason") or ""), str(stats.get("source") or "")
    out["path"] = str(stats.get("path") or "")
    if not stats.get("available"):
        return out
    start = (dt.date.fromisoformat(as_of) - dt.timedelta(days=SPARK_DAYS)).isoformat()
    for key in wanted:
        series = research_spread_history(key, start, as_of)
        values = [float(v) for v in series.tolist() if v is not None and math.isfinite(float(v))]
        if len(values) > SPARK_POINTS:
            step = len(values) / SPARK_POINTS
            values = [values[int(i * step)] for i in range(SPARK_POINTS)] + [values[-1]]
        out["history"][key] = {"values": values, "reason": str(series.attrs.get("reason") or ""),
                               "first": series.index[0].date().isoformat() if len(series) else "",
                               "last": series.index[-1].date().isoformat() if len(series) else ""}
    return out


def pair_research(data: dict, p: dict) -> dict:
    """The research context of one pair for its row: key, z, pctile, level, unit, name, asof, the
    sparkline values, the entry level, and a reason when there is nothing to show."""
    res = data.get("research") or {}
    pair_id = str(p.get("pair_id") or "")
    sid, inst, why = (res.get("keys") or {}).get(pair_id) or _pair_research_key(p)
    key_text = f"{sid} {inst}".strip()
    out: Dict[str, Any] = {"key": key_text, "sid": sid, "inst": inst, "z": None, "z_kind": "", "z_1y": None, "pctile": None,
                           "level": None, "unit": "", "name": "", "asof": None, "note": "", "reason": "",
                           "values": [], "first": "", "last": "", "history_reason": "", "entry": _num(p.get("level_entry"))}
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
    hist = (res.get("history") or {}).get((sid, inst)) or {}
    out.update(values=list(hist.get("values") or []), first=str(hist.get("first") or ""), last=str(hist.get("last") or ""),
               history_reason=str(hist.get("reason") or (f"no research history for {key_text}" if not hist else "")))
    return out


def research_hover(r: dict) -> str:
    """One hover for the three research cells of a pair row."""
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
    if r.get("values"):
        bits.append(f"history {r['first']} to {r['last']} ({len(r['values'])} points), the dashed line the entry level")
    elif r.get("history_reason"):
        bits.append(r["history_reason"])
    if r.get("note"):
        bits.append(r["note"])
    bits.append("context from the research app, read-only: never in P&L, delta or a total")
    return " · ".join(bits)


def sparkline_src(values: Sequence[float], entry: Optional[float] = None, w: int = SPARK_W, h: int = SPARK_H) -> str:
    """An inline SVG sparkline as a data URI for an `html.Img`: the values as a polyline, the
    entry level a dashed line when it lies near the history's range, the last point a dot. The
    browser draws it; no JavaScript, no plotly figure."""
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or abs(hi) * 0.01 or 1.0
    show_entry = entry is not None and math.isfinite(entry) and lo - span * 0.5 <= entry <= hi + span * 0.5
    if show_entry:
        lo, hi = min(lo, entry), max(hi, entry)
        span = (hi - lo) or 1.0
    pad = 2.0

    def x(i: int) -> float:
        return pad + (w - 2 * pad) * i / max(len(vals) - 1, 1)

    def y(v: float) -> float:
        return h - pad - (h - 2 * pad) * (v - lo) / span

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    if show_entry:
        ye = y(entry)
        parts.append(f'<line x1="0" y1="{ye:.1f}" x2="{w}" y2="{ye:.1f}" stroke="#9ca3af" stroke-width="1" stroke-dasharray="2,2"/>')
    parts.append(f'<polyline points="{points}" fill="none" stroke="#0f1f3d" stroke-width="1" stroke-linejoin="round"/>')
    parts.append(f'<circle cx="{x(len(vals) - 1):.1f}" cy="{y(vals[-1]):.1f}" r="1.8" fill="#0f1f3d"/>')
    parts.append("</svg>")
    return "data:image/svg+xml;utf8," + quote("".join(parts), safe="")


def _research_tds(row: dict) -> List[html.Td]:
    """The three research cells of a row: z, %ile and the sparkline on a pair row, each a dash with
    its reason when the research app has nothing; empty on every other row."""
    r = row.get("research")
    if row.get("kind") != "pair" or not r:
        return [html.Td("", className="book-research") for _ in RESEARCH_COLUMNS]
    if r.get("reason"):
        why = r["reason"]
        return [html.Td(missing_cell(why), className="book-research") for _ in RESEARCH_COLUMNS]
    hover = research_hover(r)
    z = html.Td(signed_number(r["z"], 2) if r.get("z") is not None else missing_cell(f"no z-score for {r['key']}: not enough history in the research app"),
                className="book-research", title=plain_words(hover))
    pct = html.Td(f"{r['pctile']:.0f}" if r.get("pctile") is not None else missing_cell(f"no 5-year percentile for {r['key']} in the research app"),
                  className="book-research", title=plain_words(hover))
    src = sparkline_src(r.get("values") or [], r.get("entry"))
    spark = html.Td(html.Img(src=src, className="book-spark", alt="", width=SPARK_W, height=SPARK_H) if src
                    else missing_cell(r.get("history_reason") or f"no research history for {r['key']}"),
                    className="book-research book-spark-cell", title=plain_words(hover))
    return [z, pct, spark]


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
        order = [FUTURES_GROUP, OPTIONS_GROUP, LME_GROUP, FX_GROUP, OTHER_GROUP, SETTLED_GROUP]
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


def _strategy_position(data: Optional[dict], label: str) -> Optional[dict]:
    for p in strategy_positions(data or {}):
        if str(p.get("strategy") or p.get("name") or "") == label:
            return p
    return None


def group_notional(label: str, rows: Sequence[dict], by: str, data: Optional[dict]
                   ) -> Tuple[Optional[float], Optional[float], int, List[str], str]:
    """(gross, net, excluded, reasons, source) of a group line: in the Strategy view the
    strategy's own engine figure (`book_spreads`' `gross_usd` / `net_usd` on the position, never
    a sum of its legs); otherwise the rows' known figures summed."""
    p = _strategy_position(data, label) if by == GROUP_STRATEGY else None
    if p is not None:
        gross, net = _num(p.get("gross_usd")), _num(p.get("net_usd"))
        why = str(p.get("notional_reason") or "no notional given")
        if gross is None:
            return None, None, 1, [f"{label}: {why}"], ""
        return gross, net, 0, [], "the spread finder's notional on the trade's open lots at the day's marks and spots"
    gross, net, excluded, reasons = notional_total(rows)
    return gross, net, excluded, reasons, "the rows' known figures summed"


def groups_notional(groups: Sequence[Tuple[str, Sequence[dict]]], by: str, data: Optional[dict]
                    ) -> Tuple[Optional[float], Optional[float], int, List[str]]:
    """The gross and net of a set of group lines summed (a trade's is the engine's own), the
    groups with none counted with their reasons."""
    pairs_g, pairs_n = [], []
    excluded, reasons = 0, []
    for label, members in groups:
        g, n, ex, why, _src = group_notional(label, members, by, data)
        if g is None:
            excluded += max(ex, 1)
            reasons += why or [f"{label}: no notional"]
            continue
        excluded += ex
        reasons += why
        pairs_g.append((g, ""))
        pairs_n.append((n, ""))
    return sum_known(pairs_g)[0], sum_known(pairs_n)[0], excluded, reasons


def total_notional(rows: Sequence[dict], by: str, data: Optional[dict]
                   ) -> Tuple[Optional[float], Optional[float], int, List[str]]:
    """The Book line's gross and net: the group lines' figures summed (a trade's is the
    engine's own), the groups with none counted."""
    return groups_notional(grouped_rows(rows, by, data), by, data)


# --------------------------------------------------------------------------- the table
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


def _name_children(row: dict) -> List[Any]:
    """The Position cell: the name, then the strategy name small (the names when a netted contract
    row holds several); nothing after a row that is the strategy itself or sits under its
    strategy's group in the Strategy view."""
    children: List[Any] = [row["name"]]
    if row["kind"] == "pair":
        words = row.get("type_words") or ""
        tag = row.get("type_tag") or ""
        if words:
            children.append(html.Span([words, html.Span(f" · {tag}", className="book-pair-tag") if tag else None],
                                      className="book-pair-type",
                                      title=plain_words(row.get("type_note") or f"the pair's rule: {words}")))
        return children
    if row.get("hand") == "strategy" or row.get("group"):
        return children
    names = row.get("strategies") or ([row["strategy"]] if row.get("strategy") else [])
    if names:
        children.append(html.Span(", ".join(names), className="name-sub",
                                  title="Jason's trade name (the broker's PBRoot label)"))
    return children


def type_sentence(trade_type: str, type_source: str = "", type_note: str = "") -> str:
    """The strategy (spread type) in one sentence for a hover: 'Strategy: cross exchange (the
    broker's label)', 'Strategy: term structure (read from the legs)', 'Strategy: the broker's
    labels disagree: ...'; '' for an outright with no type and no note."""
    words = trade_type_words(trade_type)
    source, note = str(type_source or ""), str(type_note or "")
    if not words:
        if source == SOURCE_MIXED:
            return f"Strategy: the broker's labels on the legs disagree{': ' + note if note else ''}"
        return f"Strategy: {note}" if note else ""
    how = "read from the legs, no broker label on these fills" if source == SOURCE_INFERRED else "the broker's label"
    text = f"Strategy: {words} ({how})"
    if type_disagrees(source, note):
        text += f". Check: {note}"
    return text


def position_tr(row: dict, markers: bool = True, marks_on_file: bool = True) -> html.Tr:
    move_cls = sign_class(row["move_sign"]) if row["move"] is not None else "cell-missing"
    now_children: List[Any] = [row["now_text"]]
    if row["now"] is not None and row.get("unit"):
        now_children.append(unit_suffix(row["unit"]))
    if row.get("ratio"):
        now_children.append(unit_suffix("ratio"))
    hover = "\n".join(t for t in (row["name_hover"], type_sentence(row["trade_type"], row["type_source"], row["type_note"])
                                  if row["kind"] != "settled" else "") if t)
    size_children: List[Any] = [row["size"]]
    if row.get("size_marker"):
        text, why = row["size_marker"]
        size_children.append(html.Span(text, className="marker marker--small", title=plain_words(why)))
    cells = [
        html.Td(_name_children(row), className="l book-name", title=plain_words(hover)),
        html.Td(size_children, className="l", title=plain_words(row["size_hover"]) or None),
    ]
    if row["kind"] == "settled":
        cells += [html.Td(""), html.Td("")]
    else:
        cells += _notional_tds(row["gross"], row["net"], 0, [row["notional_reason"]], row["notional_source"],
                               markers, marks_on_file)
    cells += [
        html.Td(row["entry_text"], title=plain_words(row["entry_hover"]) or None,
                className="cell-missing" if row["entry"] is None and row["entry_text"] == NA else None),
        html.Td(now_children, title=plain_words(row["now_hover"]) or None,
                className="cell-missing" if row["now"] is None and row["now_text"] == NA else None),
        html.Td(row["move_text"], title=plain_words(row["move_hover"]) or None,
                className=move_cls if row["move_text"] != "" else None),
    ]
    cells += _research_tds(row)
    for key in PERIODS:
        value, excluded, reasons, note = row["periods"][key]
        cells.append(_money_td(value, excluded, reasons, note, markers=markers and not row.get("subtotal")))
    next_td = _next_td(row)
    if row.get("next_marker"):
        text, why = row["next_marker"]
        next_td.children = [next_td.children, html.Span(text, className="marker marker--amber marker--small",
                                                        title=plain_words(why))]
    cells.append(next_td)
    cls = "book-row book-pair" if row["kind"] == "pair" else (
        "book-row book-row--leg" if row.get("part") and row["kind"] != "settled" else "book-row")
    return html.Tr(cells, id={"type": ROW_TYPE, "idx": row["id"]}, n_clicks=0, className=cls)


def _units_words(value: Optional[float], unit: str) -> str:
    """'long 35.6 t', 'short 7.9m USD', 'flat': a net in physical units, rounded to a tenth."""
    if value is None:
        return NA
    if unit and len(unit) == 3 and unit.isupper() and unit.isalpha():
        return size_words(value, ccy=unit)
    return size_words(round(float(value), 1), unit or "units")


def _split_words(c: dict) -> str:
    """'long COMEX 34 lots', 'short LME 350 t': one root's net on its exchange (an LME root in
    tonnes, a futures root in lots)."""
    exchange = str(c.get("exchange") or c.get("root_id") or "")
    if exchange == "LME":
        v, unit = _num(c.get("net_units")), str(c.get("unit") or "t")
    else:
        v, unit = _num(c.get("net_lots")), "lots"
    words = size_words(round(v, 1), unit) if v is not None else NA
    side, _sp, rest = words.partition(" ")
    return f"{side} {exchange} {rest}".strip() if rest else f"{exchange} {words}".strip()


def _root_words(root_id: str, lots: float, roots: Dict[str, Any], longs: float = 0.0, shorts: float = 0.0) -> str:
    """'long COMEX copper 34 lots', 'short LME zinc 1,900 t', 'HRC flat (long 200, short 200
    lots)': one root's net of a strategy's open legs, an LME root in tonnes (its lots are
    tonnes), a futures root in lots; a root that nets to zero says each way (a term structure)."""
    root = roots.get(root_id)
    if root is None and len(root_id) == 3 and root_id.isalpha() and root_id.isupper():
        return f"FX hedge {size_words(lots, ccy=root_id)}"      # an FX spot / forward leg: its base currency amount
    name = short_root_name(root, root_id)
    unit = "t" if root_id.startswith("LME:") else "lots"
    if abs(lots) < 1e-9:
        return f"{name} flat (long {plain(longs)}, short {plain(abs(shorts))} {unit})"
    words = size_words(round(float(lots), 1), unit)
    side, _sp, rest = words.partition(" ")
    return f"{side} {name} {rest}".strip() if rest else f"{name} {words}".strip()


def strategy_legs_words(position: dict, data: dict) -> Tuple[List[Any], str]:
    """The legs sentence of a strategy's group line: each root's net open lots or tonnes in
    words ('long COMEX copper 34 lots · short LME copper 350 t'), then 'net long 35.6 t' for a
    commodity whose curve-positions `by_subsector` line is this strategy's alone (every root of
    the commodity in this strategy at the same net, so the engine's conversion to one unit is
    the strategy's own; the note on hover); no net where it is not. (children, hover)."""
    roots = data.get("roots") or {}
    by_root: Dict[str, float] = {}
    longs: Dict[str, float] = {}
    shorts: Dict[str, float] = {}
    for leg in position.get("legs") or []:
        rid = str(leg.get("root_id") or "")
        lots = _num(leg.get("open_lots"))
        if lots is None:
            lots = _num(leg.get("lots")) or 0.0
        by_root[rid] = by_root.get(rid, 0.0) + lots
        (longs if lots > 0 else shorts)[rid] = (longs if lots > 0 else shorts).get(rid, 0.0) + lots
    parts: List[Any] = []
    for rid, lots in by_root.items():
        if abs(lots) < 1e-9 and not longs.get(rid) and not shorts.get(rid):
            continue                       # no open lots at all on this root
        if parts:
            parts.append(" · ")
        parts.append(html.Span(_root_words(rid, lots, roots, longs.get(rid, 0.0), shorts.get(rid, 0.0)),
                               className=sign_class(lots) or None,
                               title=plain_words(f"{rid}: the trade's open lots on its contracts, netted (display)")))
    subs = ((data.get("curve") or {}).get("by_subsector") or {})
    subsectors = data.get("subsectors") or {}
    hover_lines: List[str] = []
    by_sub: Dict[str, List[str]] = {}
    for rid in by_root:
        by_sub.setdefault(subsectors.get(rid, ""), []).append(rid)
    for key, rids in by_sub.items():
        sub = subs.get(key)
        if not sub or len(rids) < 2:
            continue                       # one root: its own words above say it; nothing to convert
        split = {str(c.get("root_id")): c for c in sub.get("split") or []}
        if set(split) != set(rids):
            continue                       # another strategy holds this commodity too
        same = all(abs((_num(split[r].get("net_units" if r.startswith("LME:") else "net_lots")) or 0.0) - by_root[r]) < 1e-6
                   for r in rids)
        net_units, unit = _num(sub.get("net_units")), str(sub.get("unit") or "")
        if not same or net_units is None:
            continue
        name = str(sub.get("name") or key)
        prefix = f"{name.lower()} " if len(by_sub) > 1 else ""
        parts += [" · ", html.Span(f"{prefix}net {_units_words(net_units, unit)}", className=sign_class(net_units) or None,
                                   title=plain_words(str(sub.get("units_note") or f"the roots' units add: {unit}")))]
        if sub.get("units_note"):
            hover_lines.append(f"{name}: {sub['units_note']}")
    return parts, "\n".join(hover_lines)


def net_exposure_children(sub: Optional[dict], label: str = "", markers: bool = True) -> Tuple[List[Any], str]:
    """The net exposure line of a commodity group, from curve-positions' `by_subsector` entry:
    'net long 35.6 t · long COMEX 34 lots · short LME 350 t' (the net in one physical unit, then
    each root's net on its exchange), the units note, the USD net and gross (or why not) on
    hover; a dash with the units note when the units do not add. (children, hover)."""
    if not sub:
        return [], ""
    net_units, unit = _num(sub.get("net_units")), str(sub.get("unit") or "")
    parts: List[Any] = []
    units_note = str(sub.get("units_note") or "")
    if net_units is None:
        parts.append(html.Span(["net ", missing_cell(units_note or "no net in one unit")]))
    else:
        parts.append(html.Span(f"net {_units_words(net_units, unit)}", className=sign_class(net_units) or None,
                               title=units_note or f"the roots' units add: {unit}"))
    split = list(sub.get("split") or [])
    exchanges = [str(c.get("exchange") or "") for c in split]
    for c in split:
        words = _split_words(c)
        if exchanges.count(str(c.get("exchange") or "")) > 1:
            words += f" ({short_root_name(None, str(c.get('root_id') or ''))})"
        parts += [" · ", html.Span(words, title=plain_words(f"{c.get('root_id')}: {c.get('reason') or 'net lots, the trades netted'}"))]
    net_usd, gross_usd = _num(sub.get("net_usd")), _num(sub.get("gross_usd"))
    hover_lines = [units_note] if units_note else []
    if net_usd is not None and gross_usd is not None:
        hover_lines.append(f"USD notional: net {signed_money(net_usd, '$')}, gross {short_money(gross_usd, '$')} "
                           f"({full_usd(net_usd)} / {full_usd(gross_usd)})")
    else:
        hover_lines.append(f"no USD notional: {sub.get('reason') or 'a price is missing'}")
    if sub.get("missing") and markers:
        parts.append(html.Span(f"excl. {len(sub['missing'])}", className="marker",
                               title=plain_words(f"no USD figure for {', '.join(sub['missing'][:LINES_ON_HOVER])}: {sub.get('reason') or ''}")))
    return parts, "\n".join(hover_lines)


def _subsector_for_group(label: str, rows: Sequence[dict], data: Optional[dict]) -> Optional[dict]:
    subs = ((data or {}).get("curve") or {}).get("by_subsector") or {}
    keys = [r.get("subsector") for r in rows if r.get("subsector")]
    for key in dict.fromkeys(keys):
        if key in subs:
            return subs[key]
    return next((sub for sub in subs.values() if str(sub.get("name")) == label), None)


def hedge_coverage_children(entry: Optional[dict]) -> List[Any]:
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
        return ["hedged ", missing_cell(reason)]
    gross_cov, unhedged = _num(entry.get("hedge_coverage")), _num(entry.get("unhedged_cny"))
    hedge_usd, cny_net = _num(entry.get("hedge_usd")), _num(entry.get("cny_net_usd"))
    hover = "; ".join(t for t in (
        f"the hedge ({signed_money(hedge_usd, '$') if hedge_usd is not None else 'n/a'}) against the CNY legs' signed net "
        f"notional ({signed_money(cny_net, '$') if cny_net is not None else 'n/a'}): 100 % = fully hedged",
        f"unhedged CNY {signed_money(unhedged, '$')} ({full_usd(unhedged)})" if unhedged is not None else "",
        f"against the CNY legs' gross notional: {gross_cov * 100:.0f} %" if gross_cov is not None else "",
        reason) if t)
    pct = round(net_cov * 100) + 0.0            # never a "−0 %" for a coverage that rounds to nothing
    out: List[Any] = [html.Span(f"hedged {pct:.0f} %".replace("-", MINUS), title=plain_words(hover))]
    if reason:
        out.append(html.Span("hedge runs with the exposure", className="marker marker--amber", title=plain_words(reason)))
    return out


def daily_split_note(entry: Optional[dict], total: Optional[float]) -> str:
    """The strategy line's Daily hover: 'spread +12.3k · FX −1.1k · hedge +0.9k' when the engine's
    split is whole and equals the Daily shown to the cent; else its reason. Display only."""
    if not entry:
        return ""
    split = entry.get("daily") or {}
    if split.get("reason"):
        return f"not split: {split['reason']}"
    if total is None:
        return ""
    if abs(float(split.get("total") or 0.0) - total) > 0.005:
        return f"not split: the engine's split adds to {full_usd(float(split.get('total') or 0.0))}, not the Daily shown"
    return (f"spread {signed_money(_num(split.get('spread')) or 0.0)} · FX {signed_money(_num(split.get('fx')) or 0.0)} · "
            f"hedge {signed_money(_num(split.get('hedge')) or 0.0)}")


def strategy_line_children(label: str, position: dict, data: dict) -> Tuple[List[Any], str]:
    """A trade's group line: the PBRoot name, its strategy in words (small; the source and the
    engine's note on hover, 'inferred' never on screen), and its legs in words. (children, title)."""
    children: List[Any] = [label]
    words = trade_type_words(position.get("trade_type"))
    sentence = type_sentence(str(position.get("trade_type") or ""), str(position.get("type_source") or ""),
                             str(position.get("type_note") or ""))
    if words:
        children.append(html.Span(words, className="book-strategy-type", title=plain_words(sentence)))
    elif str(position.get("type_source") or "") == SOURCE_MIXED:
        children.append(html.Span("labels disagree", className="book-strategy-type marker--amber",
                                  title=plain_words(sentence)))
    parts, hover = strategy_legs_words(position, data)
    if parts:
        children += [" · ", html.Span(parts, className="book-net-line", title=plain_words(hover) or None)]
    coverage = hedge_coverage_children(_strategy_entry(data, label))
    if coverage:
        children += [" · ", html.Span(coverage, className="book-net-line")]
    n = len(position.get("trade_ids") or [])
    title = f"Trade {label} (Jason's PBRoot name): {_plural(n, 'fill')}; its open lots per contract are the rows under it"
    return children, title


def group_tr(label: str, rows: Sequence[dict], by: str = GROUP_INSTRUMENT, data: Optional[dict] = None,
             markers: bool = True, marks_on_file: bool = True) -> html.Tr:
    children: List[Any] = [label]
    title = f"{_plural(len(rows), 'position')}"
    position = _strategy_position(data, label) if by == GROUP_STRATEGY else None
    if position is not None:
        children, title = strategy_line_children(label, position, data or {})
    elif by == GROUP_COMMODITY and label != SETTLED_GROUP:
        sub = _subsector_for_group(label, rows, data)
        parts, hover = net_exposure_children(sub, label, markers=markers)
        if parts:
            children += [" · ", html.Span(parts, className="book-net-line", title=plain_words(hover) or None)]
            title = f"{_plural(len(rows), 'contract')}; net exposure at delta (the Exposure tab's figure)"
        elif (data or {}).get("curve_error"):
            children += [" · ", html.Span(["net ", missing_cell(data["curve_error"])], className="book-net-line")]
    cells: List[Any] = [html.Td(children, className="l", colSpan=2, title=plain_words(title))]
    if label == SETTLED_GROUP:
        cells += [html.Td(""), html.Td("")]
    else:
        gross, net, excluded, reasons, source = group_notional(label, rows, by, data)
        cells += _notional_tds(gross, net, excluded, reasons, source, markers, marks_on_file)
    cells.append(html.Td("", colSpan=3 + len(RESEARCH_COLUMNS)))
    entry = _strategy_entry(data, label) if position is not None else None
    for key in PERIODS:
        total, excluded, reasons = group_total(rows, key)
        note = daily_split_note(entry, total) if key == "daily" else ""
        cells.append(_money_td(total, excluded, reasons, note, markers=markers))
    cells.append(html.Td(""))
    return html.Tr(cells, className="book-group")


def part_tr(part: str) -> html.Tr:
    """The small 'Outright' / 'Hedge' label over a strategy's rows of that part."""
    label, hover = PART_LABELS[part]
    return html.Tr([html.Td(label, className="l", colSpan=11 + len(RESEARCH_COLUMNS), title=plain_words(hover))],
                   className="book-part")


def total_tr(rows: Sequence[dict], marks_on_file: bool, by: str = GROUP_INSTRUMENT, data: Optional[dict] = None) -> html.Tr:
    note = "= header" if marks_on_file else "no marks on file"
    cells: List[Any] = [html.Td([BOOK_LABEL, html.Span(note, className="book-note")], className="l", colSpan=2,
                                title="Every trade of the as-of book is in one row above, so this line is the "
                                      "header's Daily, MTD and LTD: the known figures summed, what is left out named.")]
    gross, net, excluded, reasons = total_notional(rows, by, data)
    cells += _notional_tds(gross, net, excluded, reasons, "the group lines' figures summed", marks_on_file, marks_on_file,
                           bold=True)
    cells.append(html.Td("", colSpan=3 + len(RESEARCH_COLUMNS)))
    for key in PERIODS:
        total, excluded, reasons = group_total(rows, key)
        cells.append(_money_td(total, excluded, reasons, bold=True, markers=marks_on_file))
    cells.append(html.Td(""))
    return html.Tr(cells, className="book-total")


def _position_type_key(position: dict) -> str:
    """A trade's strategy label from its engine position: the type in words, 'Mixed labels' when
    its fills' labels disagree with no type read, else 'Unassigned'."""
    words = strategy_words(position.get("trade_type"))
    if words:
        return words
    return MIXED_GROUP if str(position.get("type_source") or "") == SOURCE_MIXED else NO_TYPE_GROUP


def _strategy_labels_order(names: Sequence[str]) -> List[str]:
    head = [strategy_words(code) for code in STRATEGY_ORDER]
    tail = [MIXED_GROUP, NO_TYPE_GROUP, SETTLED_GROUP]
    return [g for g in head if g in names] + sorted(g for g in names if g not in head + tail) + [g for g in tail if g in names]


def typed_groups(rows: Sequence[dict], data: Optional[dict]) -> List[Tuple[str, List[Tuple[str, List[dict]]]]]:
    """The Positions fold's Strategy view: [(strategy label, [(trade label, its rows)])]. A trade
    (PBRoot name) sits under the strategy the engine gave its position; the rows with no trade
    name each under their own; the settled line last, under its own label."""
    buckets: Dict[str, List[Tuple[str, List[dict]]]] = {}
    for label, members in grouped_rows(rows, GROUP_STRATEGY, data):
        if label == SETTLED_GROUP:
            buckets.setdefault(SETTLED_GROUP, []).append((label, list(members)))
            continue
        position = _strategy_position(data, label)
        if position is not None:
            buckets.setdefault(_position_type_key(position), []).append((label, list(members)))
            continue
        for key in dict.fromkeys(_type_key(r) for r in members):
            buckets.setdefault(key, []).append((label, [r for r in members if _type_key(r) == key]))
    return [(g, buckets[g]) for g in _strategy_labels_order(list(buckets))]


def _trade_names(rows: Sequence[dict]) -> List[str]:
    """The trade names (PBRoot) on a set of rows, sorted, the group labels never among them."""
    names: List[str] = []
    for r in rows:
        for s in (r.get("strategies") or ([r["strategy"]] if r.get("strategy") else [])):
            if s and s not in (SETTLED_GROUP, NO_STRATEGY_GROUP) and s not in names:
                names.append(s)
    return sorted(names)


def strategy_tr(label: str, groups: Sequence[Tuple[str, Sequence[dict]]], data: Optional[dict], markers: bool = True,
                marks_on_file: bool = True) -> html.Tr:
    """The Positions fold's strategy line over its trades: the label, the trade names small, the
    trade lines' gross and net summed, and its rows' Daily, MTD and LTD summed (display)."""
    members = [r for _label, ms in groups for r in ms]
    names = _trade_names(members)
    children: List[Any] = [label]
    if names:
        children.append(html.Span(", ".join(names), className="book-strategy-type",
                                  title=f"{_plural(len(names), 'trade')} (PBRoot names) in this strategy"))
    n_open, n_total = _open_count(data or {}, [t for r in summed_rows(members) for t in r["trade_ids"]])
    title = (f"Strategy {label}: {_plural(len(groups), 'trade')}, {n_open} open of {_plural(n_total, 'fill')} on file; "
             f"its trades' lines are under it")
    cells: List[Any] = [html.Td(children, className="l", colSpan=2, title=plain_words(title))]
    gross, net, excluded, reasons = groups_notional(groups, GROUP_STRATEGY, data)
    cells += _notional_tds(gross, net, excluded, reasons, "the trade lines' figures summed", markers, marks_on_file)
    cells.append(html.Td("", colSpan=3 + len(RESEARCH_COLUMNS)))
    for key in PERIODS:
        total, excluded, reasons = group_total(members, key)
        cells.append(_money_td(total, excluded, reasons, markers=markers))
    cells.append(html.Td(""))
    return html.Tr(cells, className="book-strategy")


def book_table(rows: Sequence[dict], by: str, marks_on_file: bool, data: Optional[dict] = None) -> html.Div:
    """The table. The Strategy view nests strategy (spread type) -> trade (PBRoot name) -> pairs
    -> legs -> Outright -> Hedge. While the book has no marks at all (`marks_on_file` False) no
    cell carries an "excl. N" marker: the Loaded banner above says values come with the first pull."""
    head = html.Thead([
        html.Tr([html.Th("", colSpan=7),
                 html.Th(RESEARCH_LABEL, colSpan=len(RESEARCH_COLUMNS), className="book-research-head", title=RESEARCH_ABOUT),
                 html.Th("", colSpan=4)], className="book-head-groups"),
        html.Tr([
            html.Th("Position", className="l", style={"width": "320px"}, title=COLUMN_TIPS["Position"]),
            html.Th("Size", className="l", title=COLUMN_TIPS["Size"]),
            html.Th("Gross", title=COLUMN_TIPS["Gross"]), html.Th("Net", title=COLUMN_TIPS["Net"]),
            html.Th("Entry", title=COLUMN_TIPS["Entry"]), html.Th("Now", title=COLUMN_TIPS["Now"]),
            html.Th("Move", title=COLUMN_TIPS["Move"]),
            html.Th("z", className="book-research", title=COLUMN_TIPS["z"]),
            html.Th("%ile", className="book-research", title=COLUMN_TIPS["%ile"]),
            html.Th("", className="book-research", title=COLUMN_TIPS["History"]),
            html.Th("Daily", title=COLUMN_TIPS["Daily"]),
            html.Th("MTD", title=COLUMN_TIPS["MTD"]), html.Th("LTD", title=COLUMN_TIPS["LTD"]),
            html.Th("Next", className="l", style={"width": "130px"}, title=COLUMN_TIPS["Next"])])])
    body: List[Any] = []
    if by == GROUP_STRATEGY:
        sections = typed_groups(rows, data)
    else:
        sections = [("", grouped_rows(rows, by, data))]
    for strategy, groups in sections:
        if strategy and strategy != SETTLED_GROUP:
            body.append(strategy_tr(strategy, groups, data, markers=marks_on_file, marks_on_file=marks_on_file))
        for label, members in groups:
            body.append(group_tr(label, members, by, data, markers=marks_on_file, marks_on_file=marks_on_file))
            shown = ""
            for r in members:
                part = str(r.get("part") or "")
                if part in PART_LABELS and part != shown and r["kind"] != "settled":
                    body.append(part_tr(part))
                shown = part or shown
                body.append(position_tr(r, markers=marks_on_file, marks_on_file=marks_on_file))
    body.append(total_tr(rows, marks_on_file, by, data))
    return html.Div(className="book-card", children=[html.Table([head, html.Tbody(body)], id=TABLE_ID,
                                                                className="book-table")])


# --------------------------------------------------------------------------- the tiles
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


def open_view_rows(view_rows: Sequence[dict]) -> List[dict]:
    """The open rows of a view as the Positions tile and the fold's summary count them: a pair
    counts once (its two leg rows are inside it), the settled line not at all."""
    return [r for r in view_rows if r["kind"] != "settled" and not (r["kind"] == "leg" and r.get("part") == PART_PAIR)]


def tiles_row(positions: Sequence[dict], view_rows: Sequence[dict], data: dict, marks_on_file: bool) -> html.Div:
    """Seven tiles over the summary: Strategies (the spread types present), Trades (the PBRoot
    names), Positions, P&L today and P&L since entry (the header's own Daily and LTD, the same
    value and markers), Gross exposure, Net exposure (the engine's USD notional summed over the
    known position rows, "excl. N" for the rest; a dash with the reason when nothing is priced)."""
    strategies = strategy_positions(data)
    names = [str(p.get("strategy") or p.get("name") or "") for p in strategies]
    present = {_type_key(r) for r in positions if r["kind"] != "settled"}
    types = [strategy_words(code) for code in STRATEGY_ORDER if strategy_words(code) in present]
    open_rows = open_view_rows(view_rows)
    df = data.get("df")
    n_trades = int(len(df)) if df is not None else 0
    n_open = int((df["status"] == "OPEN").sum()) if df is not None and not df.empty else 0
    gross, net, excluded, reasons = notional_total([r for r in positions if r["kind"] != "settled"])
    why = ("; ".join(reasons[:LINES_ON_HOVER]) or "no notional") if marks_on_file else NO_MARKS_REASON

    def tile(label: str, value: Any, hover: str, kind: str = "") -> html.Div:
        return html.Div(className="book-tile", children=[
            html.Div(label, className="k"), html.Div(value, className=f"v {kind}".strip(), title=plain_words(hover) or None)])

    daily, daily_hover = _header_figure(data, "daily", marks_on_file)
    ltd, ltd_hover = _header_figure(data, "ltd", marks_on_file)
    tiles = [
        tile("Strategies", f"{len(types):,}",
             (", ".join(types) + ("; some positions unassigned" if NO_TYPE_GROUP in present else "")) if types
             else "no strategy (spread type) on the open positions"),
        tile("Trades", f"{len(names):,}", ", ".join(names) if names else "no trade name (PBRoot) on the fills"),
        tile("Positions", f"{len(open_rows):,}",
             f"{counts_text(positions, data)} · {_plural(n_trades, 'fill')} on file, {n_open} open; the rows of the Positions table"),
        tile("P&L today", daily, f"{COLUMN_TIPS['P&L today']}\n{daily_hover}"),
        tile("P&L since entry", ltd, f"{COLUMN_TIPS['Since entry']}\n{ltd_hover}"),
        tile("Gross exposure", _notional_cell(gross, why, "the engine's gross USD notional over the positions, summed",
                                              excluded=excluded, reasons=reasons, markers=marks_on_file,
                                              marks_on_file=marks_on_file),
             COLUMN_TIPS["Gross"] if gross is not None else why),
        tile("Net exposure", _notional_cell(net, why, "the engine's net USD notional over the positions, summed, long positive",
                                            signed=True, excluded=excluded, reasons=reasons, markers=marks_on_file,
                                            marks_on_file=marks_on_file),
             COLUMN_TIPS["Net"] if net is not None else why),
    ]
    return html.Div(tiles, id=TILES_ID, className="book-tiles")


# --------------------------------------------------------------------------- the summary: By strategy, Break it down
def _share(part: Optional[float], whole: Optional[float]) -> Optional[float]:
    """part / whole in percent, of two summed figures (display); None when either is missing or
    the whole is zero."""
    if part is None or whole is None or abs(whole) < 1e-9:
        return None
    return part / whole * 100.0


def _pct_td(part: Optional[float], whole: Optional[float], what: str, signed: bool = False, bold: bool = False,
            marks_on_file: bool = True) -> html.Td:
    """A share cell: '+42.3 %' (signed, coloured) or '42.3 %', the two full figures on hover; a
    dash with the reason when either side is missing."""
    share = _share(part, whole)
    style = {"fontWeight": 700} if bold else None
    if share is None:
        why = (NO_MARKS_REASON if not marks_on_file else
               f"no share: {'the line has no ' + what if part is None else 'the Book has no ' + what if whole is None else 'the Book figure is zero'}")
        return html.Td(missing_cell(why), style=style)
    text = (f"{share:+.1f} %" if signed else f"{share:.1f} %").replace("-", MINUS)
    hover = f"{full_usd(part)} of the Book's {full_usd(whole)} ({what})"
    return html.Td(html.Span(text, className=(sign_class(share) if signed else None) or None, title=plain_words(hover)),
                   style=style)


def _earliest_next(rows: Sequence[dict]) -> Optional[dict]:
    """The earliest next event among a set of rows (real or estimated), by date."""
    events = [r["next"] for r in rows if r.get("next") and r["next"].get("iso")]
    return min(events, key=lambda n: (str(n["iso"]), 0 if not n.get("estimated") else 1)) if events else None


def _open_count(data: dict, trade_ids: Sequence[str]) -> Tuple[int, int]:
    """(open, total) over a set of trade ids."""
    df = data.get("df")
    if df is None or df.empty:
        return 0, len(trade_ids)
    status = dict(zip(df["trade_id"].astype(str), df["status"].astype(str)))
    ids = list(dict.fromkeys(str(t) for t in trade_ids))
    return sum(1 for t in ids if status.get(t) == "OPEN"), len(ids)


def strategy_words(code: Optional[str]) -> str:
    """A strategy (spread type) as a group label, with its capital: 'Cross exchange'; '' for none."""
    words = trade_type_words(code)
    return words[:1].upper() + words[1:] if words else ""


def _type_key(row: dict) -> str:
    if row["kind"] == "settled":
        return SETTLED_GROUP
    if row.get("type_source") == SOURCE_MIXED and not row.get("trade_type"):
        return MIXED_GROUP
    return strategy_words(row.get("trade_type")) or NO_TYPE_GROUP


def breakdown_groups(rows: Sequence[dict], by: str, data: dict) -> List[Tuple[str, List[dict]]]:
    """[(label, rows)] of a summary view: Commodity, Instrument and Trade through `grouped_rows`;
    by strategy (`GROUP_TYPE`) by each row's own type (one type per fill, the type of the position
    it sits in, `trade_types`): cross exchange, cross product, term structure, then the rest."""
    if by != GROUP_TYPE:
        return grouped_rows(rows, by, data)
    names = list(dict.fromkeys(_type_key(r) for r in rows))
    return [(g, sorted((r for r in rows if _type_key(r) == g), key=_daily_abs)) for g in _strategy_labels_order(names)]


def summary_entries(data: dict, rows: Sequence[dict], by: str = GROUP_STRATEGY) -> List[dict]:
    """One entry per group of the view: the label, its rows, the trade's position and engine
    entry (the Trade view, `GROUP_STRATEGY`), the period figures (`group_total`, the same sums
    the group lines use), the gross and net (`group_notional`: a trade's the engine's own), the
    open and total fill counts, the trade names, the earliest next event, the type words. Sorted
    by gross largest first, the unlabelled and settled lines last; the By strategy view keeps the
    research app's order (cross exchange, cross product, term structure, unassigned)."""
    groups = breakdown_groups(rows, by, data) if by == GROUP_TYPE else grouped_rows(rows, by, data)
    out: List[dict] = []
    for label, members in groups:
        position = _strategy_position(data, label) if by == GROUP_STRATEGY else None
        entry = _strategy_entry(data, label) if position is not None else None
        summed = summed_rows(members)
        tids = [t for r in summed for t in r["trade_ids"]]
        n_open, n_total = _open_count(data, tids)
        settled = label == SETTLED_GROUP
        if settled:
            gross, net, excluded, reasons, source = None, None, 0, ["settled and closed-out trades carry no open notional"], ""
        else:
            gross, net, excluded, reasons, source = group_notional(label, members, by, data)
        if position is not None:
            type_code, type_source, type_note = (str(position.get(k) or "") for k in ("trade_type", "type_source", "type_note"))
        elif settled:
            type_code, type_source, type_note = "", "", ""
        else:
            _s, type_code, type_source, type_note = _labels_of(data, tids)
        out.append({"label": label, "rows": members, "position": position, "entry": entry, "settled": settled,
                    "periods": {k: group_total(members, k) for k in PERIODS}, "gross": gross, "net": net,
                    "excluded": excluded, "reasons": reasons, "source": source, "n_open": n_open, "n_total": n_total,
                    "next": _earliest_next(members), "trade_type": type_code, "type_source": type_source,
                    "type_note": type_note, "trades": _trade_names(summed)})
    if by == GROUP_TYPE:
        return out
    tail = {NO_STRATEGY_GROUP: 1, NO_TYPE_GROUP: 1, MIXED_GROUP: 1, OTHER_GROUP: 1, SETTLED_GROUP: 2}
    out.sort(key=lambda e: (tail.get(e["label"], 0), 0 if e["gross"] is not None else 1, -(e["gross"] or 0.0), e["label"]))
    return out


def _type_td(e: dict) -> html.Td:
    """The strategy in words; on a trade line whose fills carry different labels an amber
    'labels disagree'; on a commodity or instrument group of several types a plain 'mixed'."""
    words = trade_type_words(e["trade_type"])
    sentence = type_sentence(e["trade_type"], e["type_source"], e["type_note"])
    if words:
        return html.Td(words, className="l book-type", title=plain_words(sentence))
    if e["type_source"] == SOURCE_MIXED:
        if e["position"] is not None:
            return html.Td(html.Span("labels disagree", className="marker--amber book-type"), className="l",
                           title=plain_words(sentence))
        return html.Td("mixed", className="l book-type", title=plain_words(e["type_note"] or "positions of several types"))
    return html.Td("", className="l")


def _open_td(e: dict, bold: bool = False) -> html.Td:
    """The Open column: how many of the line's fills are open (every fill on file on hover)."""
    hover = (f"{_plural(e['n_total'], 'fill')} settled or closed out" if e["settled"] else
             f"{e['n_open']} open of {_plural(e['n_total'], 'fill')} on file")
    return html.Td(f"{e['n_open']:,}" if not e["settled"] else f"{e['n_total']:,}", title=hover,
                   style={"fontWeight": 700} if bold else None)


def _names_td(e: dict) -> html.Td:
    """The By strategy table's Trades column: the PBRoot names in the strategy, grey, the counts
    on hover; blank on the settled line."""
    if e["settled"]:
        return html.Td("", className="l book-trades", title=f"{_plural(e['n_total'], 'fill')} settled or closed out")
    names = e.get("trades") or []
    if not names:
        return html.Td("no trade name", className="l book-trades",
                       title=f"no PBRoot name on these fills; {e['n_open']} open of {_plural(e['n_total'], 'fill')} on file")
    return html.Td(", ".join(names), className="l book-trades",
                   title=f"{_plural(len(names), 'trade')} (PBRoot names); {e['n_open']} open of {_plural(e['n_total'], 'fill')} on file")


def _label_td(e: dict, by: str) -> html.Td:
    if e["position"] is not None:
        n = len(e["position"].get("trade_ids") or [])
        title = f"Trade {e['label']} (Jason's PBRoot name): {_plural(n, 'fill')}; its pairs and legs are in the Positions table"
    elif by == GROUP_TYPE and not e["settled"]:
        title = f"Strategy {e['label']}: {_plural(len(summed_rows(e['rows'])), 'position')}, {_plural(len(e.get('trades') or []), 'trade')}"
    else:
        title = f"{_plural(len(summed_rows(e['rows'])), 'position')}"
    return html.Td(e["label"], className="l book-name", title=plain_words(title))


def _summary_pnl_tds(e: dict, marks_on_file: bool, bold: bool = False) -> List[html.Td]:
    cells = []
    for key in PERIODS:
        total, excluded, reasons = e["periods"][key]
        note = daily_split_note(e["entry"], total) if key == "daily" else ""
        cells.append(_money_td(total, excluded, reasons, note, bold=bold, markers=marks_on_file))
    return cells


def _book_entry(rows: Sequence[dict], by: str, data: dict) -> dict:
    """The Book line of a summary table: the header's figures, the group lines' notional summed."""
    gross, net, excluded, reasons = total_notional(rows, GROUP_INSTRUMENT if by == GROUP_TYPE else by, data)
    tids = [t for r in summed_rows(rows) for t in r["trade_ids"]]
    n_open, n_total = _open_count(data, tids)
    return {"label": BOOK_LABEL, "rows": list(rows), "position": None, "entry": None, "settled": False,
            "periods": {k: group_total(rows, k) for k in PERIODS}, "gross": gross, "net": net, "excluded": excluded,
            "reasons": reasons, "source": "the group lines' figures summed", "n_open": n_open, "n_total": n_total,
            "next": _earliest_next(rows), "trade_type": "", "type_source": "", "type_note": ""}


def _summary_tr(e: dict, book: dict, by: str, marks_on_file: bool, legs: bool, data: dict, total: bool = False) -> html.Tr:
    """One line of a summary table. By strategy (`GROUP_TYPE`): the label, the trade names, then
    the figures. By trade (`GROUP_STRATEGY`, `legs`): the name, its strategy, its legs, the open
    count, the figures, hedged. By commodity or instrument: the name, its strategy, the open
    count, the figures."""
    bold = total
    if total:
        note = "= header" if marks_on_file else "no marks on file"
        cells: List[Any] = [html.Td([BOOK_LABEL, html.Span(note, className="book-note")], className="l book-name",
                                    title="Every fill of the as-of book is in one line above, so this line is the "
                                          "header's Daily, MTD and LTD: the known figures summed, what is left out named."),
                            html.Td("", className="l")]
        if legs:
            cells.append(html.Td("", className="l"))
    else:
        cells = [_label_td(e, by), _names_td(e) if by == GROUP_TYPE else _type_td(e)]
        if legs:
            parts, hover = strategy_legs_words(e["position"], data) if e["position"] is not None else ([], "")
            cells.append(html.Td(html.Span(parts, className="book-net-line", title=plain_words(hover) or None) if parts else "",
                                 className="l book-legs"))
    if by != GROUP_TYPE:
        cells.append(_open_td(e, bold=bold))
    cells += _summary_pnl_tds(e, marks_on_file, bold=bold)
    if e["settled"]:
        cells += [html.Td(""), html.Td("")]
    else:
        cells += _notional_tds(e["gross"], e["net"], e["excluded"], e["reasons"], e["source"], marks_on_file, marks_on_file,
                               bold=bold)
    cells.append(_pct_td(e["periods"]["ltd"][0], book["periods"]["ltd"][0], "LTD P&L", signed=True, bold=bold,
                         marks_on_file=marks_on_file))
    if e["settled"]:
        cells.append(html.Td(""))
    else:
        cells.append(_pct_td(e["gross"], book["gross"], "gross notional", bold=bold, marks_on_file=marks_on_file))
    if legs:
        coverage = hedge_coverage_children(e["entry"]) if not total else []
        cells.append(html.Td(html.Span(coverage, className="book-net-line") if coverage else "", className="l"))
    cells.append(_next_td({"next": e["next"], "kind": "settled" if e["settled"] or total else ""}))
    return html.Tr(cells, className="book-total" if total else "book-summary-row")


def _summary_head(first: str, legs: bool, by: str = GROUP_STRATEGY) -> html.Thead:
    tip = COLUMN_TIPS["Strategy" if by == GROUP_TYPE else "Trade" if by == GROUP_STRATEGY else "Position"]
    cols: List[Any] = [html.Th(first, className="l", title=tip)]
    if by == GROUP_TYPE:
        cols.append(html.Th("Trades", className="l", title=COLUMN_TIPS["Trades"]))
    else:
        cols.append(html.Th("Strategy", className="l", title=COLUMN_TIPS["Strategy"]))
    if legs:
        cols.append(html.Th("Legs", className="l", title=COLUMN_TIPS["Legs"]))
    if by != GROUP_TYPE:
        cols.append(html.Th("Open", title=COLUMN_TIPS["Open"]))
    cols += [html.Th("P&L today", title=COLUMN_TIPS["P&L today"]),
             html.Th("MTD", title=COLUMN_TIPS["MTD"]), html.Th("Since entry", title=COLUMN_TIPS["Since entry"]),
             html.Th("Gross", title=COLUMN_TIPS["Gross"]), html.Th("Net", title=COLUMN_TIPS["Net"]),
             html.Th("% of P&L", title=COLUMN_TIPS["% of P&L"]), html.Th("% of exposure", title=COLUMN_TIPS["% of exposure"])]
    if legs:
        cols.append(html.Th("Hedged", className="l", title=COLUMN_TIPS["Hedged"]))
    cols.append(html.Th("Next", className="l", style={"width": "130px"}, title=COLUMN_TIPS["Next"]))
    return html.Thead(html.Tr(cols))


def summary_table(data: dict, rows: Sequence[dict], marks_on_file: bool) -> html.Div:
    """The By strategy table (`SUMMARY_ID`), the hero: one line per strategy (spread type) present
    among the position rows, in the research app's order (cross exchange, cross product, term
    structure, then mixed labels, unassigned, the settled line), the trades (PBRoot names) in it,
    P&L today, MTD, since entry, gross, net, its signed share of the Book's LTD, its share of the
    Book's gross and the earliest next event; the Book line = the header. Nothing computed but the
    sums and the two shares."""
    entries = summary_entries(data, rows, GROUP_TYPE)
    book = _book_entry(rows, GROUP_TYPE, data)
    body = [_summary_tr(e, book, GROUP_TYPE, marks_on_file, False, data) for e in entries]
    body.append(_summary_tr(book, book, GROUP_TYPE, marks_on_file, False, data, total=True))
    return html.Div(className="book-card", children=[html.Table([_summary_head("Strategy", False, GROUP_TYPE), html.Tbody(body)],
                                                                id=SUMMARY_ID, className="book-table book-summary")])


def trade_table(data: dict, rows: Sequence[dict], marks_on_file: bool) -> html.Div:
    """Break it down by trade: one line per trade (Jason's PBRoot name; 'No trade name' and the
    settled line when they exist), its strategy, legs, open fills, P&L today, MTD, since entry,
    gross, net, the two shares, the hedge coverage and the earliest next event; the Book line =
    the header."""
    entries = summary_entries(data, rows, GROUP_STRATEGY)
    book = _book_entry(rows, GROUP_STRATEGY, data)
    body = [_summary_tr(e, book, GROUP_STRATEGY, marks_on_file, True, data) for e in entries]
    body.append(_summary_tr(book, book, GROUP_STRATEGY, marks_on_file, True, data, total=True))
    return html.Div(className="book-card", children=[html.Table([_summary_head("Trade", True, GROUP_STRATEGY), html.Tbody(body)],
                                                                id=BREAKDOWN_TABLE_ID + "-table",
                                                                className="book-table book-summary")])


def breakdown_table(data: dict, rows: Sequence[dict], by: str, marks_on_file: bool) -> html.Div:
    """The Break it down table (`BREAKDOWN_TABLE_ID`): by trade (`trade_table`), else the same
    columns without Legs and Hedged, grouped by Commodity or Instrument over the existing rows;
    the Book line = the header."""
    if by == GROUP_TRADE:
        return trade_table(data, rows, marks_on_file)
    first = dict(BREAKDOWN_OPTIONS).get(by, "Group")
    entries = summary_entries(data, rows, by)
    book = _book_entry(rows, by, data)
    body = [_summary_tr(e, book, by, marks_on_file, False, data) for e in entries]
    body.append(_summary_tr(book, book, by, marks_on_file, False, data, total=True))
    return html.Div(className="book-card", children=[html.Table([_summary_head(first, False, by), html.Tbody(body)],
                                                                id=BREAKDOWN_TABLE_ID + "-table",
                                                                className="book-table book-summary")])


def breakdown_rows(data: dict, by: str) -> List[dict]:
    """The rows the Break it down view groups: by trade the Strategy view's rows (each trade's
    pairs, legs and hedges), by commodity the netted contract rows, else the position rows (one
    per trade, spread, outright, fill)."""
    if by == GROUP_TRADE:
        return book_rows(data, GROUP_STRATEGY)
    return book_rows(data, GROUP_COMMODITY) if by == GROUP_COMMODITY else book_rows(data)


# --------------------------------------------------------------------------- title line, movers
def counts_text(rows: Sequence[dict], data: dict) -> str:
    """'6 trades · 5 spreads · 12 outrights · 3 options · 2 LME prompts · 8 FX hedges' (a zero
    left out), over the position rows; a trade is a PBRoot name."""
    n_strat = sum(1 for r in rows if r["kind"] == "spread" and r.get("hand") == "strategy")
    n_spreads = sum(1 for r in rows if r["kind"] == "spread" and r.get("hand") != "strategy")
    n_out = sum(1 for r in rows if r["kind"] == "outright")
    # the rows of the other products (a trade inside a strategy is counted in its strategy)
    n_opt = sum(1 for r in rows if r["kind"] == "trade" and r["instrument_group"] == OPTIONS_GROUP)
    n_lme = sum(1 for r in rows if r["kind"] == "trade" and r["instrument_group"] == LME_GROUP)
    n_fx = sum(1 for r in rows if r["kind"] == "trade" and r["instrument_group"] == FX_GROUP)
    parts = [(n_strat, "trade", "trades"), (n_spreads, "spread", "spreads"), (n_out, "outright", "outrights"),
             (n_opt, "option", "options"),
             (n_lme, "LME prompt", "LME prompts"), (n_fx, "FX hedge", "FX hedges")]
    words = [f"{n} {one if n == 1 else many}" for n, one, many in parts if n]
    return " · ".join(words) if words else "no open position"


def movers_strip(rows: Sequence[dict]) -> Optional[html.Div]:
    """The three best and three worst Daily figures among the position rows."""
    scored = [(r["periods"]["daily"][0], r) for r in rows if r["kind"] != "settled" and r["periods"]["daily"][0] is not None]
    if not scored:
        return None
    best = [x for x in sorted(scored, key=lambda x: -x[0]) if x[0] > 0][:3]
    worst = [x for x in sorted(scored, key=lambda x: x[0]) if x[0] < 0][:3]
    if not best and not worst:
        return None                        # every Daily is zero: no strip, not a bare label

    def card(v, r):
        return html.Div(className="book-mover", children=[
            html.Span(r["name"], className="book-mover-name", title=plain_words(r["name_hover"])),
            html.B(signed_money(v), className=f"book-mover-value {sign_class(v)}", title=full_usd(v))])
    children: List[Any] = [card(v, r) for v, r in best]
    if best and worst:
        children.append(html.Div(className="book-movers-divider"))
    children += [card(v, r) for v, r in worst]
    children.append(html.Span("today's movers", className="book-movers-label"))
    return html.Div(className="book-movers", children=children)


# --------------------------------------------------------------------------- the row detail
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


def detail_for(data: dict, rows: Sequence[dict], row_id: str) -> Any:
    """The detail of the clicked row: a spread's entries and legs, otherwise its trades."""
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None:
        return None
    if row["kind"] == "spread":
        payload = detail_payloads(data.get("spreads") or {}).get(row_id)
        if payload is None:
            return None
        unit = payload.get("unit") or ""
        summary = (f"{row['name']}: {payload.get('size_text') or ''}, entry {level_text(_num(payload.get('level_entry')), unit)}, "
                   f"now {level_text(_num(payload.get('level_now')), unit)} {unit}").strip()
        return html.Div(className="section section--secondary book-detail", children=[
            about(summary, "The position's entries and legs.", level="h5", style={"margin": "0 0 6px"}),
            about("Entries", "The spreads found on each trade date that make this position, with their own entry "
                             "level.", level="div", className="section-kicker"),
            members_table(payload),
            about("Legs", "Each leg's prices as quoted (entry = its lots-weighted average fill), and the factor that "
                          "turns its price into the spread's unit.", level="div", className="section-kicker"),
            detail_legs_table(payload),
            about("Trades", "Every trade of the position, open or closed, at full figures.", level="div",
                  className="section-kicker"),
            _trade_lines(data, row["trade_ids"])])
    title = row["name"] if row["kind"] != "settled" else SETTLED_GROUP
    if row["kind"] == "pair":
        unit = row.get("unit") or ("ratio" if row.get("ratio") else "")
        summary = f"{row['name']}: {row['size']}, entry {row['entry_text']}, now {row['now_text']} {unit}".strip()
        return html.Div(className="section section--secondary book-detail", children=[
            about(summary, row["name_hover"], level="h5", style={"margin": "0 0 6px"}),
            about("Fills of both legs", "Every fill of the two contracts in this trade, open or closed, at "
                                         "full figures (a contract split between pairs lists its trades on each).",
                  level="div", className="section-kicker"),
            _trade_lines(data, row.get("detail_trade_ids") or row["trade_ids"])])
    return html.Div(className="section section--secondary book-detail", children=[
        html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "10px"}, children=[
            about(title, row["name_hover"], level="h5", style={"margin": "0 0 6px"}),
            pointer("Trades", "book-detail-trades", "→ Trades")]),
        _trade_lines(data, row.get("detail_trade_ids") or row["trade_ids"])])


# --------------------------------------------------------------------------- needs you
def _need(chip: str, kind: str, text: str, hover: str, tab: str, rank: int) -> dict:
    return {"chip": chip, "kind": kind, "text": text, "hover": hover, "tab": tab, "rank": rank}


def _event_words(data: dict, r: dict) -> str:
    inst = str(r.get("contract_id") or "")
    root_id = str(r.get("root_id") or "")
    root = data["roots"].get(root_id)
    if str(r.get("product")) == "LME_FWD":
        name = lme_name(root, root_id, str(inst.split(" ")[-1]) if " " in inst else None)
    else:
        name = contract_name(inst, root, root_id)
    event = _NEXT_EVENT_WORDS.get(str(r.get("next_event") or ""), str(r.get("next_event") or "event"))
    bd = r.get("business_days")
    when = ("expired" if r.get("level") == "EXPIRED" else "today" if bd == 0 else
            f"in {bd} bd" if bd is not None else "date unknown")
    return f"{name} {event} {when}"


def needs(data: dict) -> List[dict]:
    """At most five lines, one per kind, most urgent first."""
    out: List[dict] = []
    rows = (data.get("schedule") or {}).get("rows") or []
    urgent = [r for r in rows if r.get("level") in ("EXPIRED", "RED", "AMBER") and not r.get("estimated")]
    if urgent:
        first = urgent[0]
        more = f" (+{len(urgent) - 1} more)" if len(urgent) > 1 else ""
        out.append(_need(str(first.get("level")), "red" if first.get("level") != "AMBER" else "warn",
                         _event_words(data, first) + more,
                         "\n".join(_event_words(data, r) for r in urgent[:LINES_ON_HOVER]), "", 0))
    estimated = sorted({str(r.get("contract_id")) for r in rows if r.get("estimated") and r.get("level") != "EXPIRED"})
    if estimated:
        out.append(_need("DATES", "warn", f"{_plural(len(estimated), 'contract')} run on estimated dates until the "
                                          "first Bloomberg pull",
                         "\n".join(contract_label(c) for c in estimated[:LINES_ON_HOVER]), "", 1))
    elif data.get("schedule_error"):
        out.append(_need("DATES", "warn", "roll calendar not available", data["schedule_error"], "", 1))
    needed, missing = data.get("needs") or (0, [])
    if data.get("needs_error"):
        out.append(_need("MARKS", "warn", "the marks the book needs could not be listed", data["needs_error"], "Data", 2))
    elif missing:
        names = []
        for m in missing:
            inst = str(m.get("instrument_id") or "")
            info = data["instruments"].get(inst) or {}
            root_id = info.get("base_ccy", "")
            label = (contract_name(inst, data["roots"].get(root_id), root_id) if parse_contract_id(inst)
                     else f"{short_root_name(data['roots'].get(root_id), root_id)} {short_date(m.get('settle_date'))}".strip()
                     if root_id in data["roots"] else inst)
            if label not in names:
                names.append(label)
        out.append(_need("MARKS", "warn", f"{len(missing)} of {needed} marks missing: {', '.join(names[:2])}"
                                          + (f" +{len(names) - 2}" if len(names) > 2 else ""),
                         "\n".join(f"{m['instrument_id']} {plain_words(m['mark_type']).lower()} {m['settle_date']}" for m in missing[:LINES_ON_HOVER]),
                         "Data", 2))
    review = (data.get("spreads") or {}).get("review") or []
    if review:
        out.append(_need("GROUP", "info", f"{_plural(len(review), 'set')} of trades could not be matched to a spread",
                         "\n".join(str(r.get("reason") or r.get("review_id") or "") for r in review[:LINES_ON_HOVER]),
                         "Trades", 3))
    checks = data.get("limits") or []
    if data.get("limits_error"):
        out.append(_need("LIMITS", "quiet", "limits could not be checked", data["limits_error"], "Risk", 4))
    else:
        breached = [c for c in checks if str(c.get("level")) in ("BREACH", "WARN")]
        set_rows = [c for c in checks if c.get("level") not in ("NOT_SET", "config")]
        if breached:
            c = breached[0]
            out.append(_need(str(c.get("level")), "red", f"{c.get('source')} {str(c.get('limit') or '').replace('_', ' ')} "
                                                          f"{c.get('scope')}: {c.get('reason') or 'over its limit'}",
                             "\n".join(str(x.get("reason") or "") for x in breached[:LINES_ON_HOVER]), "Risk", 0))
        elif not set_rows:
            out.append(_need("LIMITS", "quiet", "position limits not set",
                             "No desk or exchange limit is set yet.", "Risk", 4))
    out.sort(key=lambda n: n["rank"])
    return out[:NEEDS_MAX]


def needs_section(data: dict) -> html.Div:
    lines = []
    for n, need in enumerate(needs(data), start=1):
        lines.append(html.Div(className="book-need", children=[
            html.Span(need["chip"], className=f"book-need-chip book-need-chip--{need['kind']}"),
            html.Span(need["text"], title=plain_words(need["hover"]) or None),
            pointer(need["tab"], f"book-need-{n}")]))
    if not lines:
        lines.append(html.Div("Nothing needs you today.", className="book-need", style={"color": "var(--muted)"}))
    return html.Div(className="book-card book-needs", children=[
        about("Needs you", NEEDS_ABOUT, level="div", className="book-h"), html.Div(id=ALERTS_ID, children=lines)])


# --------------------------------------------------------------------------- last load
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


def load_section(data: dict) -> html.Div:
    if data.get("load_error"):
        return html.Div(id=LOAD_ID, className="book-card book-load", children=[
            about("Last load", LOAD_ABOUT, level="div", className="book-h"), message_box(data["load_error"])])
    text, hover, clean = load_sentence(data.get("load"))
    children: List[Any] = [html.Span(text, title=plain_words(hover) or None)]
    if not clean:
        children += [" ", pointer("Data", "book-load-data", "→ Data")]
    return html.Div(id=LOAD_ID, className="book-card book-load", children=[
        about("Last load", LOAD_ABOUT, level="div", className="book-h"), html.Div(children)])


def loaded_line(data: dict) -> html.Div:
    """One quiet line under the tiles of a book with trades and no marks yet: the load sentence and
    that values come with the first pull (the gold-edged banner of the first Phase E build)."""
    text, hover, clean = load_sentence(data.get("load"))
    children: List[Any] = [html.Span("Loaded", className="book-loaded-k"), html.Span(text, title=plain_words(hover) or None),
                           html.Span(" · values appear after the first pull; fills and sizes are already right",
                                     title=NO_MARKS_REASON)]
    if not clean:
        children += [" · ", pointer("Data", "book-banner-data", "what was skipped and why")]
    return html.Div(className="book-loaded-line", children=children)


loaded_banner = loaded_line


# --------------------------------------------------------------------------- issues, body
def issue_items(data: dict, rows: Sequence[dict]) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for key, label in (("periods_error", "P&L"), ("spreads_error", "Spreads"), ("schedule_error", "Roll calendar"),
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
                              f"{data.get('as_of')}); each trade's note is on the Trades tab."))
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
SUMMARY_ABOUT = ("One line per strategy, the spread type (cross exchange, cross product, term structure; unassigned "
                 "for a plain JSHY10 label), with the trades (PBRoot names) in it, P&L today, MTD and since entry "
                 "(the header's rule, the known figures summed), the engine's gross and net USD notional, its signed "
                 "share of the Book's LTD and of the Book's gross, and the earliest next event. Each position's "
                 "strategy is the engine's: the broker's label first, read from the legs only where there is none. "
                 "The Book line is the header.")
BREAKDOWN_ABOUT = ("The same figures by trade (one line per PBRoot name: its strategy, legs, open fills, hedge "
                   "coverage), by commodity (the contracts netted across trades) or by instrument; shares are of "
                   "the whole book. The Book line is the header.")
POSITIONS_ABOUT = ("Every open position: by Strategy, each spread type, then each trade (PBRoot name) with its pairs "
                   "and their legs, the lots left outright and its hedges; by Commodity, one row per open contract "
                   "netted across trades; by Instrument, one row per position. Click a row for its fills. The Book "
                   "line is the header.")


SUMMARY_META = "P&L by spread type; the Book line is the header"
BREAKDOWN_META = "the same figures by trade, commodity or instrument"


def section_head(title: str, text: str, meta: str = "", *extra: Any) -> html.Div:
    """A section's title row, the research app's shape: the small-caps title (its definitions on
    hover), the one-line meta beside it, then anything else (a switch, the CSV button)."""
    children: List[Any] = [about(title, text, level="h4", className="book-section-title")]
    if meta:
        children.append(html.Span(meta, className="book-section-meta"))
    children += [x for x in extra if x is not None]
    return html.Div(className="book-section-head", children=children)


def fold_meta(view_rows: Sequence[dict], by: str) -> str:
    """The Positions fold's summary meta: '· 23 · strategy → trade → pairs → legs'."""
    return f"· {len(open_view_rows(view_rows)):,} · {FOLD_WORDS.get(by, FOLD_WORDS[DEFAULT_GROUP])}"


def parts(data: dict, by: str = DEFAULT_GROUP, breakdown_by: str = DEFAULT_BREAKDOWN) -> dict:
    """The tab's pieces from `gather`'s output, each for its own placeholder of the static
    layout: `top` (the tiles, the loaded line, the movers), `summary` (the By strategy table),
    `breakdown` (the Break it down table), `positions` (the positions table), `under` (Needs you,
    Last load, the drawer), `counts` (the Positions fold's meta) and `shown` (False for the empty
    state: the sections other than `top` are hidden)."""
    by = by if by in dict(GROUP_OPTIONS) else DEFAULT_GROUP
    breakdown_by = breakdown_by if breakdown_by in dict(BREAKDOWN_OPTIONS) else DEFAULT_BREAKDOWN
    if not data.get("n_total"):
        return {"top": empty_state(data), "summary": None, "breakdown": None, "positions": None, "under": None,
                "counts": "", "shown": False}
    positions = book_rows(data)
    strat_rows = book_rows(data, GROUP_STRATEGY)
    rows = strat_rows if by == GROUP_STRATEGY else book_rows(data, by) if by == GROUP_COMMODITY else positions
    brk_rows = strat_rows if breakdown_by == GROUP_TRADE else breakdown_rows(data, breakdown_by)
    marks = bool(data.get("marks_on_file"))
    top: List[Any] = []
    if data.get("spreads_error"):
        top.append(message_box(data["spreads_error"]))
    top.append(tiles_row(positions, strat_rows, data, marks))
    if not marks:
        top.append(loaded_line(data))
    if marks:
        strip = movers_strip(positions)
        if strip is not None:
            top.append(strip)
    summary = summary_table(data, positions, marks) if positions else message_box(f"No open position on {data.get('as_of')}.")
    breakdown = breakdown_table(data, brk_rows, breakdown_by, marks) if brk_rows else None
    table = book_table(rows, by, marks, data) if rows else None
    extra = [r for r in strat_rows if r not in positions] + [r for r in rows if r not in positions and r not in strat_rows]
    under = [needs_section(data),
             html.Div(className="book-side", children=[load_section(data),
                                                       issues_drawer(issue_items(data, positions + extra), id=ISSUES_ID)
                                                       or html.Div()])]
    return {"top": html.Div(top), "summary": summary, "breakdown": breakdown, "positions": table, "under": under,
            "counts": fold_meta(rows, by), "shown": True}


def body(data: dict, by: str = DEFAULT_GROUP, breakdown_by: str = DEFAULT_BREAKDOWN) -> Tuple[html.Div, str, dict]:
    """(the whole tab as one Div, the fold's meta text, the toolbar's style): `parts` assembled in
    the layout's order, for a direct render (the smoke test, a script). The callback fills the
    placeholders one by one."""
    p = parts(data, by, breakdown_by)
    if not p["shown"]:
        return html.Div([p["top"], html.Div(id=DETAIL_ID)]), "", HIDDEN
    return html.Div([p["top"],
                     html.Div([section_head("By strategy", SUMMARY_ABOUT, SUMMARY_META), p["summary"] or html.Div()]),
                     html.Div([section_head("Break it down", BREAKDOWN_ABOUT, BREAKDOWN_META), p["breakdown"] or html.Div()]),
                     html.Div([section_head("Positions", POSITIONS_ABOUT, p["counts"]),
                               p["positions"] or message_box(f"No open position on {data.get('as_of')}."),
                               html.Div(id=DETAIL_ID)]),
                     html.Div(className="book-under", children=p["under"])]), p["counts"], {}


# --------------------------------------------------------------------------- CSV
def csv_frame(rows: Sequence[dict], by: str, data: Optional[dict] = None) -> pd.DataFrame:
    """The table at full figures for the download: one line per row with its group."""
    records = []
    for label, members in grouped_rows(rows, by, data):
        for r in members:
            rec = {"group": label, "part": r.get("part") or "", "row": r["kind"], "pair": r.get("pair_id") or "",
                   "position": r["name"], "trade": ", ".join(r.get("strategies") or []) or r.get("strategy", ""),
                   "strategy": trade_type_words(r.get("trade_type", "")) or "", "strategy_source": r.get("type_source", ""),
                   "size": r["size"], "gross_usd": r["gross"], "net_usd": r["net"],
                   "notional_reason": r["notional_reason"] if r["gross"] is None else "", "entry": r["entry"],
                   "now": r["now"], "unit": r.get("unit") or ("ratio" if r.get("ratio") else ""), "move": r["move"]}
            for key in PERIODS:
                value, excluded, _reasons, _note = r["periods"][key]
                rec[f"{key}_usd"] = value
                rec[f"{key}_excluded"] = excluded
            nxt = r.get("next") or {}
            rec["next_event"], rec["next_date"], rec["next_estimated"] = nxt.get("event", ""), nxt.get("iso", ""), nxt.get("estimated", "")
            research = r.get("research") or {}
            rec["research_key"], rec["research_z"], rec["research_pctile"] = research.get("key", ""), research.get("z"), research.get("pctile")
            rec["trade_ids"] = " ".join(r["trade_ids"])
            records.append(rec)
    return pd.DataFrame(records)


# --------------------------------------------------------------------------- shell
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


def render_parts(as_of: Optional[str], db_path, by: str = DEFAULT_GROUP, breakdown_by: str = DEFAULT_BREAKDOWN) -> dict:
    """`parts` for `as_of`, from one read-only connection closed straight after. A problem is a
    message where the top would be, the other sections hidden."""
    empty = {"top": None, "summary": None, "breakdown": None, "positions": None, "under": None, "counts": "", "shown": False}
    if not as_of:
        return {**empty, "top": message_box("No as-of date available.")}
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return {**empty, "top": message_box(f"Database not available ({exc}).")}
    try:
        return parts(_gather(conn, as_of), by or DEFAULT_GROUP, breakdown_by or DEFAULT_BREAKDOWN)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("book tab failed for %s", as_of)
        return {**empty, "top": _problem(as_of, exc)}
    finally:
        conn.close()


def render(as_of: Optional[str], db_path, by: str = DEFAULT_GROUP) -> Tuple[Any, str, dict]:
    """(the whole tab as one Div, counts text, toolbar style) for `as_of`: `body` on one
    read-only connection closed straight after. A problem is a message where the body would be."""
    if not as_of:
        return message_box("No as-of date available."), "", {}
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc})."), "", {}
    try:
        return body(_gather(conn, as_of), by or DEFAULT_GROUP)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("book tab failed for %s", as_of)
        return _problem(as_of, exc), "", {}
    finally:
        conn.close()


def render_detail(row_id: Optional[str], as_of: Optional[str], db_path) -> Any:
    if not row_id or not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        data = _gather(conn, as_of)
        rid = str(row_id)
        rows = book_rows(data)
        if rid.startswith("CONTRACT-"):
            rows += book_rows(data, GROUP_COMMODITY)
        elif rid.startswith((f"{LEG_PREFIX}-", f"{PAIR_PREFIX}-", f"{HEDGE_PREFIX}-", "SETTLED-")):
            rows += book_rows(data, GROUP_STRATEGY)
        return detail_for(data, rows, rid)
    except Exception as exc:  # noqa: BLE001 -- the reason under the table, never a 500
        log.exception("book tab detail failed for %s", row_id)
        return message_box(f"The position's detail could not be built ({type(exc).__name__}: {exc}).")
    finally:
        conn.close()


def render_csv(as_of: Optional[str], db_path, by: str):
    if not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        data = _gather(conn, as_of)
        by = by if by in dict(GROUP_OPTIONS) else DEFAULT_GROUP
        frame = csv_frame(book_rows(data, by), by, data)
        return dcc.send_data_frame(frame.to_csv, f"book-{as_of}.csv", index=False)
    except Exception:  # noqa: BLE001
        log.exception("book tab csv failed for %s", as_of)
        return None
    finally:
        conn.close()


def _switch(id_: str, options: Sequence[Tuple[str, str]], default: str) -> dcc.RadioItems:
    return dcc.RadioItems(id=id_, className="book-switch",
                          options=[{"label": label, "value": value} for value, label in options],
                          value=default, inline=True, persistence=True, persistence_type="session")


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell (2026-09-28): the title, the top placeholder (the tiles, the loaded line,
    the movers), the By strategy section (its title row static, so the Download CSV button it
    holds is never re-inserted), the Break it down section with its Trade | Commodity | Instrument
    switch, the Positions fold (open; its summary meta a placeholder) with its Strategy | Commodity
    | Instrument switch, the row detail, the under-section and the safety interval. The switches
    are static so the callback can read them (a switch inside a re-rendered body would fire the
    callback that renders it). No date picker."""
    return html.Div(className="book-tab", children=[
        html.Div(id=TOOLBAR_ID, className="book-title-row", children=[about("Book", TITLE_ABOUT, level="h3")]),
        html.Div(id=BODY_ID, children=[message_box("Loading the book...")]),
        html.Div(id=SUMMARY_SECTION_ID, className="book-section", style=HIDDEN, children=[
            section_head("By strategy", SUMMARY_ABOUT, SUMMARY_META,
                         html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                                     title="The Positions table as shown, at full figures, one line per row"),
                         dcc.Download(id=DOWNLOAD_ID)),
            html.Div(id=SUMMARY_TABLE_ID),
        ]),
        html.Div(id=BREAKDOWN_SECTION_ID, className="book-section", style=HIDDEN, children=[
            section_head("Break it down", BREAKDOWN_ABOUT, BREAKDOWN_META, _switch(BREAKDOWN_ID, BREAKDOWN_OPTIONS, DEFAULT_BREAKDOWN)),
            html.Div(id=BREAKDOWN_TABLE_ID),
        ]),
        html.Details(id=POSITIONS_SECTION_ID, className="details book-fold", open=True, style=HIDDEN, children=[
            html.Summary(className="book-section-head", title=POSITIONS_ABOUT, children=[
                html.Span("Positions", className="book-section-title"),
                html.Span(id=POSITIONS_SUMMARY_ID, className="book-section-meta"),
                _switch(GROUP_ID, GROUP_OPTIONS, DEFAULT_GROUP)]),
            html.Div(id=POSITIONS_TABLE_ID),
            html.Div(id=DETAIL_ID),
        ]),
        html.Div(id=UNDER_ID, className="book-under", style=HIDDEN),
        dcc.Store(id=EMPTY_UPLOAD_SINK_ID),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout

# The empty state's Upload button opens the top bar's own file chooser (the one upload of the
# app, `ui.uploads.FILE_UPLOAD_ID`): a click on that hidden input inside the user's click.
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


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The body on the header's as-of, every data revision, the safety interval and the group
    switch; the row detail on a click; the CSV; the empty state's Upload button."""

    @app.callback(
        Output(BODY_ID, "children"),
        Output(SUMMARY_TABLE_ID, "children"),
        Output(BREAKDOWN_TABLE_ID, "children"),
        Output(POSITIONS_TABLE_ID, "children"),
        Output(UNDER_ID, "children"),
        Output(POSITIONS_SUMMARY_ID, "children"),
        Output(TOOLBAR_ID, "style"),
        Output(SUMMARY_SECTION_ID, "style"),
        Output(BREAKDOWN_SECTION_ID, "style"),
        Output(POSITIONS_SECTION_ID, "style"),
        Output(UNDER_ID, "style"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
        Input(GROUP_ID, "value"),
        Input(BREAKDOWN_ID, "value"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0, by=DEFAULT_GROUP, breakdown_by=DEFAULT_BREAKDOWN):
        p = render_parts(as_of, get_db_path(), by or DEFAULT_GROUP, breakdown_by or DEFAULT_BREAKDOWN)
        shown = {} if p["shown"] else HIDDEN
        return (p["top"], p["summary"], p["breakdown"], p["positions"] or message_box(f"No open position on {as_of}."),
                p["under"], p["counts"], shown, shown, shown, shown, shown)

    @app.callback(Output(DETAIL_ID, "children"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  State(AS_OF_STORE_ID, "data"), prevent_initial_call=True)
    def _detail(_clicks, as_of):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not any(_clicks or []):
            return dash.no_update
        return render_detail(trig.get("idx"), as_of, get_db_path())

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(GROUP_ID, "value"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, by):
        if not n_clicks:
            return dash.no_update
        return render_csv(as_of, get_db_path(), by)

    app.clientside_callback(_OPEN_UPLOAD_JS, Output(EMPTY_UPLOAD_SINK_ID, "data"),
                            Input({"type": EMPTY_UPLOAD_TYPE, "idx": ALL}, "n_clicks"), prevent_initial_call=True)
