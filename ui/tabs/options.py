"""Options view inside the Blotter tab (options_calc merge Phase 8, 2026-09-17):
a grouped, collapsible MARS-style risk grid -- Portfolio Totals -> asset class (FX;
Commodity present-but-empty until options on commodity futures land, CLAUDE.md
"Commodity conversion plan" Phase 5) -> structure/package (one row per
`trades.package_id`) -> leg. The equity index (SPX listed options) left the app on
2026-09-24; the listed-option path it used is kept, generic, for Phase 5
(`LISTED_OPTION_PRODUCTS`).

**Rendering only reads.** Every number in the grid comes off `marks_official`
(`PREMIUM`/`DELTA`/`GAMMA`/`THETA`/`VEGA`/`RHO`, `source='QL_OPTIONS_PRICER'`, official
per `data/ingest/schema.py::OFFICIAL_MARK_SOURCE`), `trades_official` and
`priced_value_book` -- matching every other Blotter sub-tab and the "ui/ ... never
recomputes P&L or delta itself" rule in CLAUDE.md. The ONE write path is a user typing
option terms (2026-09-18, below), and it goes through the engine, never SQL of its own.

**Terms typed in the table (2026-09-18, user: "I would like to directly input the
strike price in the cell in the table").** Strike is an editable numeric cell, Type
(Call/Put) and Payoff dropdown cells, on an option's OWN row only -- a LEG row, or the
PACKAGE row of a single-leg package, which IS that leg (`is_leg` = 1). Those cells carry
a gold OUTLINE as their "you can type here" cue, solid and filled where a strike is still
missing, dashed and quiet where a value is on file (`table_styles`; an outline, because
the stylesheet forces every cell's border colour to grey). `DataTable` has
no per-row `editable`, so group rows are fenced twice: their three cells take no pointer
events, and `apply_edit` refuses a group row server-side. A committed edit is validated
(`parse_strike`: a positive number, nothing else), saved with
`engine.options.store.set_option_terms` (exactly what the Option-terms editor's Save
does, so it survives a re-upload the same way), re-priced at once with
`engine.options.store.price_and_store`, and the Bloomberg feed is woken for any market
input the new strike needs. The callback always answers with rows rebuilt from the
database, so an invalid entry is reverted and text never stays in a numeric column; the
reason is said in one line under the headline. A pricing SKIP is written on the row
(`note`). A digital whose strike is not on file yet can have its Payoff chosen FIRST:
`set_option_terms` cannot store a strike payoff without a strike, so the choice is held
(`_PENDING_TERMS`, shown on the row) and saved together with the strike in one call --
the option is then never priced as a vanilla in between.

**Why the old Option-terms editor "could not input the strike" (root cause, 2026-09-18).**
Its Save callback listed `options-collapsed-packages` as an Output and a State. That
store exists only in THIS sub-tab's layout, while the red "cannot be priced" banner sends
the user to Manual entry > Option terms, which embeds the same editor without it: Dash's
renderer drops a callback with a missing Output before it reaches the server
(dash_renderer `executeCallback`, `outputErrors`), silently, since the app runs with
`suppress_callback_exceptions` and no dev tools. There, Save did nothing for any option.
Under Options it did work, but the whole sub-tab is rebuilt on every data revision
(`ui/tabs/blotter.py::_MARKS_REBUILD_SCOPES`), which reset the editor's dropdown to the
FIRST option with no strike and re-ran the prefill over whatever was being typed, so only
the first-listed option was reliably enterable. Now: Save depends only on components the
editor itself owns plus the always-present revision stores, and the dropdown keeps the
user's selection across a rebuild (`persistence`).

**Cost, value and P&L per leg (2026-09-18).** `Premium paid` is the fill
(`trades.price`, a fraction of base notional paid in the BASE currency -- the sample's
35,000,000 x 0.00579 = 202,650 = NetInvoice); `Start value` = quantity x fill and
`Current value` = quantity x PREMIUM, both signed by side and in the premium currency;
`P&L (ccy)` is their difference. **`P&L USD` is never computed here**: it is the book's
own per-trade figure, `ui.tabs.blotter_pricing.priced_value_book`, so this tab cannot
disagree with the headline (asserted in tests/test_ui_options.py). `Start value USD` and
`MktVal` convert at the same spot the book used for that trade, so
MktVal - Start value USD = P&L USD to the cent. Sums in the premium currency are shown
only where every leg of the group shares it; the USD columns always sum.

**Greeks in USD come from the engine.** Delta/Gamma/Vega/Theta/Rho are converted by
`engine.options.portfolio.build_positions`, fed `PricingOutcome`-shaped objects built
from the marks on file (never re-pricing), so the grid and its headline follow whatever
that module defines (2026-09-18 units audit there: delta = USD delta notional, gamma =
USD delta change per 1 % spot move, vega/theta/rho through the quote currency) instead of
a copy of the formula kept here, which is how the two drifted apart. MktVal = PREMIUM x
quantity x USD per BASE ccy, the engine's own "market value" line.

**Aggregation rule.** Every group level (PACKAGE / ASSET_CLASS / TOTAL) is a plain
sum-skip-missing over its own legs (`_agg`), applied uniformly bottom-up, so TOTAL always
equals the sum of its asset-class rows, which equals the sum of their package rows, which
equals the sum of their legs. A group with an unpriced leg therefore shows the sum of its
PRICED legs and says "2 of 3 priced" in `note` -- the same "priced only, and say how
many" convention as the header cards and the Blotter strips; it is blank only when no leg
is priced. The leg-identity columns (MktPx, Premium paid, Expiry, Underlying, Strike,
UndFwdPx, Side, Type, Payoff) are not aggregatable across instruments; a group shows them
only when it has exactly one contributing leg (`_single`) -- which is also how a
single-leg package renders "flat": its PACKAGE row IS that one leg's row, with no
separate LEG row beneath it. A multi-leg package's PACKAGE row is a pure summary with its
LEG rows nested beneath, collapsed by default -- unless one of its legs still needs its
strike, which must not be hidden under a row that takes no typing
(`default_collapsed_packages`).

**Filters and sorting (2026-09-18, user: "the column heads aren't good filters").**
Native `DataTable` filtering, case-insensitive, with a hint in every filter box. Numeric
columns carry RAW numbers (`type: numeric` + a `Format` for display), so `> 1000000` and
`< 0` work and sorting is numeric; Expiry is ISO text, so it sorts and `>= 2026-11`
works; text columns match on "contains". Missing is `None` (a blank cell), never 0 and
never the text "n/a" -- the reason is in `note`. While any filter or sort is active the
rows are the matching options FLAT, one row per trade (`option_rows(flat=True)`), so a
match can never hide inside a collapsed package and group rows never pollute a numeric
filter; the headline then totals the rows on screen (`derived_virtual_data`).

**Headline (2026-09-18, user: "the headlines here also don't work, would like the
Greeks").** Root cause of the blank strip above this view: it is the Blotter's generic
P&L strip (`row_scoped_headline`), which has no Greeks at all, and six of its eleven
cards (Daily, Previous day, 5d, MTD, LTD-1, LTD-2) difference against a PAST close --
but option marks are only ever written for the live date (`data/bloomberg/live.py::
_options_step` prices `today`; the backfill writes FWD_OUTRIGHT/FUTURE_PX, never option
marks), so on a database whose option marks are all dated today those cards are "n/a"
by construction. It also never followed this table (`options` is in blotter.py's
`_NON_TABLE_SCOPES`). `headline_totals`/`headline_strip` below are this view's own
strip, built inside `build_layout`: Delta, Gamma, Vega, Theta, Rho, Start value, Current
value, P&L USD -- each the plain sum of the rows shown -- and "n of m options priced"
with the reasons for the rest.

**Collapse mechanism.** No native tree in `dash_table.DataTable`. A `dcc.Store` of
collapsed package_ids plus the render callback drops LEG rows whose `parent_key` is in
that set, re-running `option_rows` against the current as-of date. The store lives in the
browser session so a rebuild of the sub-tab keeps what the user expanded.
`date_picker_id` defaults to the Blotter tab's own `"blotter-date"` id (a string literal,
not an import of `ui.tabs.blotter`, which would be circular since `blotter.py` imports
this module).

**No page reload, and never under a cell being typed into (`ui/revision.py`; root cause
2026-09-21, user: "make it so you can input strike in the table", three days after the
cells above shipped).** dash-table (4.4.1, read in its bundle) renders the ACTIVE editable
cell as a plain label for as long as any callback with `Output(table, "data")` is in
flight -- its `loading_state`, which the renderer sets when the callback is DISPATCHED,
whatever it goes on to return -- and that unmounts the cell's input together with the text
typed so far; a re-sent `data` also resets the input's text to the stored value. The
render callback used to listen to both revision stores, so every revision did that: every
few seconds while the Bloomberg feed writes, and once right after each saved strike
(`ui.tabs.blotter` publishes the data revision on a saved cell), just as the next strike
was being typed. Returning `no_update` never helped, since the damage is done at dispatch,
and no test could see it, since none runs the browser component. Now the revisions reach
the render callback only through `_gate_refresh` -> `REFRESH_ID` (`refresh_gate`): while
the table's selection sits on a Strike / Type / Payoff cell the revision is HELD, nothing
that outputs to the table is dispatched, and a line under the toolbar says the refresh is
paused; it is let through the moment the selection moves to any other cell. The signal is
`active_cell` because dash-table exposes nothing finer: its `is_focused` is set by a
double-click only, not by clicking a cell and typing. A committed edit is still answered
at once with rows rebuilt from the database, so a saved strike never waits for the gate.
The editor's Save publishes both revisions itself, so nothing waits for the poll.
Two holes left the user reloading the page after typing terms (2026-09-21, "make it so i
dont have to refresh"): Enter leaves the selection on the Strike cell of the row below, so
the gate stayed shut for every later revision until another cell was clicked -- the
selection is now dropped once a cell edit has been answered (`_release_selection`) -- and
the four tables above the grid were built once per sub-tab and followed nothing; they are
now redrawn on every revision (`_refresh_breakdowns`).

**Component ids** reuse prefixes that `tests/test_ui.py::
test_every_static_callback_id_exists_in_layout` already lists as rendered-by-callback
(`blotter-strip-`, `blotter-datatable-`, `options-terms-`).
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from types import SimpleNamespace
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html
from dash.dash_table.Format import Format, Group, Scheme, Sign, Trim

from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import format_cell

TABLE_ID = "options-datatable"
COLLAPSED_STORE_ID = "options-collapsed-packages"
DEFAULT_DATE_PICKER_ID = "blotter-date"
HEADLINE_ID = "blotter-strip-options-greeks"
CLEAR_FILTERS_ID = "blotter-datatable-options-filter-clear"
EDIT_STATUS_ID = "options-terms-edit-status"
VIEW_NOTE_ID = "options-terms-view-note"
# The revision last let through to the table, and the "refresh paused" line (module
# docstring, "never under a cell being typed into").
REFRESH_ID = "options-terms-refresh"
REFRESH_NOTE_ID = "options-terms-refresh-note"

# The user's MARS reference order is kept for its columns (CLAUDE.md "Options"): Position,
# Notional, MktVal, MktPx, Delta, Theta, Gamma, Vega, Expiry, Underlying, UndFwdPx, Rho.
# Around them (2026-09-18): the option's terms and the reason column up front, the cost
# block (Ccy, Premium paid, Start value) before MktVal, and Current value / P&L after
# MktPx, which IS the current premium. Strike is the one MARS column moved (2026-09-21):
# in its MARS slot after Underlying it was the 23rd column, about 2,000 px to the right and
# off most screens (the table's scrollbar sits under the last row), while the row's own
# note says "type it in the Strike cell". It now sits with the other two typed terms,
# right after Payoff, in the order they are filled in.
# 2026-09-21 (user: "theres too many columns - need premium paid - how much you paid in
# dollars, current value and then pnl - after the details"): the option's details, then the
# three dollar figures, then the four main Greeks. Everything else stays in each row's data,
# hidden (LESS_USED_COLUMNS), so filters, the callbacks and the downloads still find it.
DISPLAY_COLUMNS = [
    "label", "side", "option_type", "payoff", "strike", "expiry", "note",
    "position", "notional", "start_value_usd", "mktval", "pnl_usd",
    "delta", "gamma", "vega", "theta",
]
LESS_USED_COLUMNS = ["premium_ccy", "premium_paid", "start_value", "mktpx", "current_value", "pnl_ccy",
                     "underlying", "undfwdpx", "rho", "instrument"]
# Carried in every row's data so filter_query / the callbacks can key off them, but not
# shown -- `hidden_columns`, not omitted from `columns`, so filter_query can still
# reference them (Dash evaluates filter_query against defined columns). `is_leg` = 1 on a
# row that is one trade (its terms are editable), `trade_id` names it, `priced_count` is
# how many of the row's `leg_count` legs have an official PREMIUM. `start_priced_usd` is
# Start value USD of the legs the book has priced, for the headline: set beside Current
# value and P&L it must cover the same options, or an unpriced ticket's premium reads as
# a loss (Start of 8 options against the Current value of 7).
HIDDEN_COLUMNS = ["level", "group_key", "parent_key", "leg_count", "priced_count", "is_leg", "trade_id",
                  "start_priced_usd"] + LESS_USED_COLUMNS
PAYOFF_WORDS = {"VANILLA": "Vanilla", "DIGITAL": "Digital", "AMERICAN": "American", "ASIAN": "Asian",
                "BARRIER_KI": "Knock-in", "BARRIER_KO": "Knock-out", "ONE_TOUCH": "One-touch",
                "NO_TOUCH": "No-touch"}
PAYOFF_CODES = {word: code for code, word in PAYOFF_WORDS.items()}
TYPE_WORDS = {"CALL": "Call", "PUT": "Put"}
TYPE_CODES = {word: code for code, word in TYPE_WORDS.items()}
# Payoffs priced off a strike (engine/options/store.py::_STRIKE_PAYOFFS); the touches are
# priced off their barrier level alone.
STRIKE_PAYOFFS = ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO")
_LEG_WORDS = {2: "Two", 3: "Three", 4: "Four"}
ALL_COLUMNS = DISPLAY_COLUMNS + HIDDEN_COLUMNS

COLUMN_LABELS = {
    "label": "Structure", "side": "Side", "option_type": "Type", "payoff": "Payoff", "note": "Note",
    "position": "Position", "notional": "Notional USD", "premium_ccy": "Ccy", "premium_paid": "Premium paid",
    "start_value": "Start value", "start_value_usd": "Premium paid USD", "mktval": "Current value USD",
    "mktpx": "MktPx (current premium)", "current_value": "Current value", "pnl_ccy": "P&L (ccy)",
    "pnl_usd": "P&L USD", "delta": "Delta", "theta": "Theta", "gamma": "Gamma", "vega": "Vega",
    "expiry": "Expiry", "underlying": "Underlying", "strike": "Strike", "undfwdpx": "UndFwdPx", "rho": "Rho",
    "instrument": "Instrument",
}

# Summed at every group level.
SUM_FIELDS = ("position", "notional", "start_value_usd", "start_priced_usd", "mktval", "pnl_usd",
              "delta", "theta", "gamma", "vega", "rho")
# In the premium currency: summed only where the group's legs all share that currency.
CCY_SUM_FIELDS = ("start_value", "current_value", "pnl_ccy")
NUMERIC_FIELDS = SUM_FIELDS + CCY_SUM_FIELDS
PASSTHROUGH_FIELDS = ("mktpx", "premium_paid", "expiry", "underlying", "strike", "undfwdpx",
                      "side", "option_type", "payoff", "instrument")
# The three cells a user can type into, on an option's own row only.
EDITABLE_COLUMNS = ("strike", "option_type", "payoff")
# Coloured by sign in the grid and in the headline.
SIGNED_FIELDS = ("pnl_ccy", "pnl_usd", "mktval", "delta", "theta", "gamma", "vega", "rho")

# trades.product -> the grid's asset-class group. No ingest path books a CMDTY_OPTION yet,
# so the Commodity group renders present-but-empty today -- by design ("rows must always
# render"). The equity index group left with the SPX options (2026-09-24).
ASSET_CLASS_BY_PRODUCT = {"FX_OPTION": "FX", "CMDTY_OPTION": "Commodity"}
ASSET_CLASS_ORDER = ("FX", "Commodity")
# The listed-option path (`_leg_row`): an option the book values at Bloomberg's own price of
# it -- the official FUTURE_PX on the option's own instrument, in the underlying's points, x
# the contract multiplier per contract -- with no model in its value, and the Greeks the
# pricing step adds at the vol that price implies ("Greeks not calculated: <why>" when it did
# not). Empty since the SPX options, its only user, left the app (2026-09-24); kept for
# Phase 5's options on commodity futures, whose product code goes here (grouped under
# Commodity) once the listed-options-pricer and pnl-valuation lanes name it.
LISTED_OPTION_PRODUCTS: Tuple[str, ...] = ()


def option_products() -> Tuple[str, ...]:
    """Every trades.product this sub-tab lists. Read at call time, so a product added to
    `LISTED_OPTION_PRODUCTS` reaches every query here."""
    return tuple(dict.fromkeys((*ASSET_CLASS_BY_PRODUCT, *LISTED_OPTION_PRODUCTS)))


def _products_in(column: str = "t.product") -> Tuple[str, Tuple[str, ...]]:
    """(`<column> IN (?, ...)`, its parameters) over `option_products()`."""
    products = option_products()
    return f"{column} IN ({','.join('?' * len(products))})", products


def _asset_class_of(product: str) -> str:
    if product in ASSET_CLASS_BY_PRODUCT:
        return ASSET_CLASS_BY_PRODUCT[product]
    return "Commodity" if product in LISTED_OPTION_PRODUCTS else "FX"

_GREEK_MARK_TYPES = (("delta", "DELTA"), ("theta", "THETA"), ("gamma", "GAMMA"),
                     ("vega", "VEGA"), ("rho", "RHO"))


# --------------------------------------------------------------------------- reads

def _is_missing(value) -> bool:
    return value is None or value != value  # NaN != NaN


def _spot_to_usd(conn: sqlite3.Connection, as_of: str, ccy: str) -> Optional[float]:
    """USD per 1 unit of `ccy` from the official SPOT, mirroring
    `engine/options/portfolio.py::_quote_ccy_to_usd` (same rule, generalised to
    either leg of a pair: CLAUDE.md -- quote-ccy P&L converts to USD at spot, never
    the forward outright). None if neither `ccy+USD` nor `USD+ccy` has one."""
    if ccy == "USD":
        return 1.0
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, ccy + "USD"),
    ).fetchone()
    if row is not None:
        return row[0]
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, "USD" + ccy),
    ).fetchone()
    if row is not None and row[0]:
        return 1.0 / row[0]
    return None


def _instrument_options_columns(conn: sqlite3.Connection) -> set:
    """Columns actually present on this DB's `instrument_options` table (2026-09-17 dev-DB
    fix): `CREATE TABLE IF NOT EXISTS` in `data/ingest/schema.py` never adds a column to an
    already-existing table, so a DB created before the `payoff` column landed (this app's
    dev DB, `data/raw/risk.db`) still has the 4-column version and a bare `SELECT
    o.payoff` throws `OperationalError: no such column`, which was never caught -- the
    whole Options sub-tab returned an HTTP 500 and the tab looked like it "did not load"
    (no partial render, no message, just a dead callback). Queried live (not memoised):
    this is one cheap PRAGMA per render, and the alternative -- caching across a schema
    change -- risks silently hiding a real migration gap. Real fix belongs in
    `data/ingest/schema.py` (an `ALTER TABLE instrument_options ADD COLUMN ...` migration
    for pre-existing DBs); reported, not made here (outside this agent's owned files)."""
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(instrument_options)").fetchall()}
    except sqlite3.Error:
        return set()


def _official_marks(conn: sqlite3.Connection, as_of: str, instrument_id: str,
                     settle_date: str, mark_types: Iterable[str]) -> dict:
    mark_types = tuple(mark_types)
    placeholders = ",".join("?" * len(mark_types))
    rows = conn.execute(
        f"SELECT mark_type, value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? "
        f"AND settle_date = ? AND mark_type IN ({placeholders})",
        (as_of, instrument_id, settle_date, *mark_types),
    ).fetchall()
    return dict(rows)


# --------------------------------------------------------------------------- session state
# Two small in-process records (the app is one process: the Bloomberg feed thread lives in
# it too). Both are keyed by the database file, so tests on separate files never meet.
#   _PENDING_TERMS  (db, instrument_id) -> {"payoff": ..., "option_type": ...}: a Payoff /
#                   Type chosen in the grid for an option whose strike is not on file yet.
#                   `set_option_terms` cannot store a strike payoff without a strike, so
#                   the choice waits here and is saved WITH the strike in one call.
#   _LAST_SKIP      (db, as_of, trade_id) -> the `reason` of the last `price_and_store`
#                   skip after an edit, shown on the row until a PREMIUM is on file.
#   _LAST_SAVE      db -> (when, message) of the editor's last Save. Under Options the
#                   Save's own revision rebuilds the sub-tab at once, editor included;
#                   the rebuilt editor shows the message for `_SAVE_MESSAGE_SECONDS`.
_PENDING_TERMS: Dict[Tuple[str, str], dict] = {}
_LAST_SKIP: Dict[Tuple[str, str, str], str] = {}
_LAST_SAVE: Dict[str, Tuple[float, str]] = {}
_SAVE_MESSAGE_SECONDS = 30.0


def _norm_path(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _db_key(conn: sqlite3.Connection) -> Optional[str]:
    """Normalised path of the connection's main database file; None for ':memory:'."""
    try:
        for _seq, name, path in conn.execute("PRAGMA database_list"):
            if name == "main":
                return _norm_path(path) if path else None
    except sqlite3.Error:
        return None
    return None


def _book_rows(conn: sqlite3.Connection, as_of: str) -> Dict[str, dict]:
    """trade_id -> the book's own row (`priced_value_book`, i.e. `value_book`), FX
    options only. `P&L USD` on this tab is read from here and never recomputed."""
    from ui.tabs.blotter_pricing import priced_value_book

    df, _n_fallback, _n_total = priced_value_book(conn, as_of)
    if df.empty:
        return {}
    df = df[df["product"].isin(option_products())]
    return {rec["trade_id"]: rec for rec in df.to_dict("records")}


def _feed_options_step(conn: sqlite3.Connection, as_of: str) -> dict:
    """The `options` block of the feed's status file (`data.bloomberg.live._options_step`)
    when the last Bloomberg cycle priced `as_of`; {} otherwise. Read-only and
    best-effort: no file, another date or an unreadable file is {}."""
    key = _db_key(conn)
    if key is None:
        return {}
    try:
        from data.bloomberg.live import read_status
        step = (read_status(key) or {}).get("options") or {}
        if not isinstance(step, dict) or step.get("as_of_date") != as_of:
            return {}
        return step
    except Exception:  # noqa: BLE001 -- a diagnostic nicety must never blank the tab
        return {}


def _feed_skip_reasons(step: dict) -> Dict[str, str]:
    """trade_id -> why the last Bloomberg cycle could not price it (its `skipped` list)."""
    skipped = step.get("skipped")
    if not isinstance(skipped, list):
        return {}
    return {str(s.get("trade_id")): str(s.get("reason") or "") for s in skipped if isinstance(s, dict)}


def _feed_step_error(step: dict) -> str:
    """Why the last Bloomberg cycle's options step failed as a whole (its `error`), in
    plain words, or "" when it did not. Seen 2026-09-22: a QuantLib OIS bootstrap failure
    stopped the step before any trade was priced, so `skipped` was empty and every leg
    read "priced the next time you press Pull Bloomberg now" after the pull had run."""
    error = step.get("error")
    if not error:
        return ""
    return f"the last Pull Bloomberg now ({step.get('as_of_date')}) failed in its options step: {error}"


def _usd_greeks(conn: sqlite3.Connection, as_of: str, rec: dict, marks: dict) -> Tuple[dict, str]:
    """({delta, theta, gamma, vega, rho} in USD, reason-when-blank). The conversion is
    `engine.options.portfolio.build_positions`'s, fed an outcome-shaped object built from
    the marks on file -- nothing is re-priced and no formula is kept here (module
    docstring). A Greek with no mark stays None; when the engine cannot convert (no SPOT
    for the quote currency or for the pair) all five are None and its reason is returned."""
    raw = {col: marks.get(mark_type) for col, mark_type in _GREEK_MARK_TYPES}
    blank = {col: None for col in raw}
    if all(v is None for v in raw.values()):
        return blank, ""
    try:
        from engine.options.portfolio import build_positions

        result = SimpleNamespace(quote_price=0.0, **{col: (0.0 if v is None else v) for col, v in raw.items()})
        outcome = SimpleNamespace(instrument_id=rec["instrument_id"], package_id=rec["package_id"],
                                  quantity=rec["quantity"], priced=True, result=result)
        legs, skipped = build_positions(conn, as_of, [outcome])
        if not legs:
            reason = skipped[0].get("reason", "") if skipped else ""
            return blank, reason or "Greeks could not be converted to USD"
        scaled = legs[0].position.scaled()
    except Exception as exc:  # noqa: BLE001 -- engine/options is another lane; say so, never 500
        return blank, f"Greeks unavailable ({exc})"
    return {col: (scaled.get(col) if raw[col] is not None else None) for col in raw}, ""


def _leg_note(rec: dict, as_of: str, premium, book: Optional[dict], greeks_reason: str,
              pending: Optional[dict], skip_reason: str, step_error: str = "") -> str:
    """The stated reason for whatever is blank on this row ("" when nothing is). A
    trade's own skip reason wins over the pull's step-level error, which wins over the
    generic "priced the next time you press Pull Bloomberg now"."""
    payoff = rec.get("payoff") or "VANILLA"
    if pending:
        chosen = " ".join(w for w in (PAYOFF_WORDS.get(pending.get("payoff") or payoff, ""),
                                      TYPE_WORDS.get(pending.get("option_type") or rec.get("option_type") or "", ""))
                          if w)
        return f"no strike on file: {chosen} chosen but not saved yet -- type the strike to save both"
    if payoff in STRIKE_PAYOFFS and not rec.get("strike"):
        return "no strike on file: type it in the Strike cell (choose Payoff first if it is a digital)"
    if payoff in ("BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH") and not rec.get("barrier_level"):
        return "no barrier / touch level on file: enter it under Option terms below"
    book_reason = (book or {}).get("reason") or ""
    if (book or {}).get("status") == "CLOSED":
        # Bought and sold back in full (CLAUDE.md "A closed-out option is not live"): valued at
        # the closing fill, so a missing PREMIUM or Greek is nothing to explain.
        return book_reason or (book or {}).get("note") or "closed out: bought and sold back in full"
    if premium is None:
        if (book or {}).get("status") == "SETTLED":
            return book_reason or f"expired {rec['expiry_date']}"
        if skip_reason:
            return f"not priced: {skip_reason}"
        if step_error:
            return f"not priced: {step_error}"
        return book_reason or f"no PREMIUM mark on {as_of}: priced the next time you press Pull Bloomberg now"
    return book_reason or greeks_reason


def _leg_row(conn: sqlite3.Connection, as_of: str, rec: dict, book: Optional[dict] = None,
             pending: Optional[dict] = None, skip_reason: str = "", step_error: str = "") -> dict:
    """One priced (or partially/un-priced -- 'rows must always render') leg, built
    straight from `trades_official`/`instruments`/`instrument_options`/`marks_official`
    plus the book's own row for the P&L, no re-pricing. See the module docstring."""
    asset_class = _asset_class_of(rec["product"])
    listed = rec["product"] in LISTED_OPTION_PRODUCTS
    quantity = rec["quantity"]
    fill = rec.get("fill")

    marks = _official_marks(conn, as_of, rec["instrument_id"], rec["expiry_date"],
                             ("PREMIUM", "FUTURE_PX") + tuple(mt for _, mt in _GREEK_MARK_TYPES))
    premium = marks.get("PREMIUM")
    # Closed out (the book's status, never a mark or a value: user, 2026-09-22, "I only want
    # to see live options"): the trade's price is its closing fill, the book's own mark, so
    # MktVal - Start value is the book's P&L; the pricer writes it no marks, and any left on
    # file from before are not its risk.
    closed = (book or {}).get("status") == "CLOSED"
    if closed and asset_class == "FX" and not _is_missing((book or {}).get("mark")):
        premium = float(book["mark"])
    if listed:
        # A listed option (user decision 2026-09-21, "Bloomberg's option price") is marked at
        # Bloomberg's own price of it, the FUTURE_PX the book's P&L reads, in the underlying's
        # points: x multiplier = per contract. The pricing step only adds the Greeks, so its
        # skip reason is about them, never the value.
        if marks.get("FUTURE_PX") is not None:
            premium = marks["FUTURE_PX"] * float(rec.get("multiplier") or 1.0)
        greeks_missing = "Greeks not calculated: " + (
            skip_reason or step_error or "they are the next time you press Pull Bloomberg now")
        skip_reason = ""

    # USD per 1 unit of the premium (base) currency: the spot the BOOK used for this trade
    # when it priced it, so MktVal - Start value USD equals the book's P&L USD exactly;
    # the official SPOT otherwise (an unpriced trade still gets its cost in USD).
    book_spot = (book or {}).get("spot")
    if (book or {}).get("status") in ("OPEN", "CLOSED") and not _is_missing(book_spot) and book_spot:
        usd_per_base = float(book_spot)
    else:
        usd_per_base = _spot_to_usd(conn, as_of, rec["base_ccy"])

    start_value = quantity * fill if not _is_missing(fill) else None
    current_value = quantity * premium if premium is not None else None
    mktval = current_value * usd_per_base if current_value is not None and usd_per_base is not None else None
    from engine.pnl.valuation import option_fill_is_per_ounce
    per_ounce = not _is_missing(fill) and option_fill_is_per_ounce(rec["base_ccy"], float(fill))
    if per_ounce:
        # A gold option is dealt in USD per ounce (engine.pnl.valuation.option_fill_is_per_ounce):
        # its start value is ounces x fill in the QUOTE currency, never ounces read as money.
        usd_per_quote_ccy = _spot_to_usd(conn, as_of, rec["quote_ccy"])
        start_value_usd = start_value * usd_per_quote_ccy if usd_per_quote_ccy is not None else None
        # the current value in the same (quote) currency, so Current value - Start value reads across
        current_value = mktval / usd_per_quote_ccy if mktval is not None and usd_per_quote_ccy else None
    elif asset_class != "FX":
        # A non-FX (commodity or listed) option: contracts x premium in the underlying's points
        # x the contract multiplier, in the QUOTE currency (15 x 121.5 x 100 = $182,250).
        # Its PREMIUM mark is already per contract (engine/options writes it x multiplier).
        multiplier = float(rec.get("multiplier") or 1.0)
        usd_per_quote_ccy = _spot_to_usd(conn, as_of, rec["quote_ccy"])
        start_value = quantity * fill * multiplier if not _is_missing(fill) else None
        start_value_usd = (start_value * usd_per_quote_ccy
                           if start_value is not None and usd_per_quote_ccy is not None else None)
        mktval = current_value * usd_per_quote_ccy if current_value is not None and usd_per_quote_ccy is not None else None
        usd_per_base = (float(rec.get("strike") or 0.0) * multiplier * usd_per_quote_ccy
                        if usd_per_quote_ccy is not None and rec.get("strike") else None)   # notional = contracts x multiplier x strike
    else:
        start_value_usd = start_value * usd_per_base if start_value is not None and usd_per_base is not None else None
    pnl_ccy = current_value - start_value if current_value is not None and start_value is not None else None

    # The book's own per-trade P&L (CLAUDE.md FX option line), taken as is.
    pnl_usd = None
    if book is not None and not book.get("reason") and not _is_missing(book.get("pnl_usd")):
        pnl_usd = float(book["pnl_usd"])

    if closed:   # no position left: no Greeks, whatever marks are on file
        greeks, greeks_reason = {col: None for col, _mt in _GREEK_MARK_TYPES}, ""
    else:
        greeks, greeks_reason = _usd_greeks(conn, as_of, rec, marks)
    if listed and premium is not None and marks.get("DELTA") is None:
        greeks_reason = greeks_reason or greeks_missing

    if asset_class == "FX":
        underlying = rec["base_ccy"] + rec["quote_ccy"]
        fwd = _official_marks(conn, as_of, underlying, rec["expiry_date"], ("FWD_OUTRIGHT",))
        undfwdpx = fwd.get("FWD_OUTRIGHT")
    else:
        # Commodity / listed: the bbg_ticker's first word -- no live trades exercise this
        # branch today (module docstring); a forward outright has no meaning here.
        ticker = rec["bbg_ticker"] or ""
        underlying = ticker.split()[0] if ticker else ""
        undfwdpx = None

    strike = rec["strike"] if rec["strike"] else None  # 0 = not known (schema.py sentinel) -> blank
    # What the row SHOWS: terms on file, overlaid with a choice waiting for its strike.
    payoff = (pending or {}).get("payoff") or rec.get("payoff") or "VANILLA"
    option_type = (pending or {}).get("option_type") or rec.get("option_type") or ""
    payoff_word = PAYOFF_WORDS.get(payoff, payoff.title())

    return {
        "trade_id": rec["trade_id"], "package_id": rec["package_id"], "asset_class": asset_class,
        "label": f"{underlying} - {payoff_word}" if underlying else payoff_word,
        "instrument": rec["instrument_id"], "payoff_word": payoff_word,
        "side": "Buy" if quantity >= 0 else "Sell",
        "option_type": TYPE_WORDS.get(option_type, option_type.title()), "payoff": payoff_word,
        "note": _leg_note(rec, as_of, premium, book, greeks_reason, pending, skip_reason, step_error),
        "priced": premium is not None,
        "closed_out": closed,
        # Notional in DOLLARS (user, 2026-09-21: "everything in dollars"; 5,000 ounces of gold is
        # not $5,000): |quantity| x USD per base unit at spot; Position keeps the base units.
        "position": quantity, "notional": abs(quantity) * usd_per_base if usd_per_base is not None else None,
        "premium_ccy": rec["quote_ccy"] if (per_ounce or asset_class != "FX") else rec["base_ccy"],
        "premium_paid": None if _is_missing(fill) else fill,
        "start_value": start_value, "start_value_usd": start_value_usd,
        "start_priced_usd": start_value_usd if pnl_usd is not None else None,
        "mktval": mktval, "mktpx": premium, "current_value": current_value,
        "pnl_ccy": pnl_ccy, "pnl_usd": pnl_usd,
        "delta": greeks["delta"], "theta": greeks["theta"], "gamma": greeks["gamma"],
        "vega": greeks["vega"], "rho": greeks["rho"],
        "expiry": rec["expiry_date"], "underlying": underlying, "strike": strike,
        "undfwdpx": undfwdpx,
    }


def _leg_rows(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    opt_cols = _instrument_options_columns(conn)
    strike_expr = "COALESCE(o.strike, 0)" if "strike" in opt_cols else "0"
    option_type_expr = "COALESCE(o.option_type, '')" if "option_type" in opt_cols else "''"
    payoff_expr = "COALESCE(o.payoff, 'VANILLA')" if "payoff" in opt_cols else "'VANILLA'"
    barrier_expr = "COALESCE(o.barrier_level, 0)" if "barrier_level" in opt_cols else "0"
    products_sql, products = _products_in()
    trades = pd.read_sql_query(
        "SELECT t.trade_id, t.package_id, t.instrument_id, t.quantity, t.price AS fill, t.product, "
        "i.base_ccy, i.quote_ccy, i.multiplier, i.expiry_date, i.bbg_ticker, "
        f"{strike_expr} AS strike, {option_type_expr} AS option_type, "
        f"{payoff_expr} AS payoff, {barrier_expr} AS barrier_level "
        "FROM trades_official t JOIN instruments i ON i.instrument_id = t.instrument_id "
        "LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id "
        f"WHERE {products_sql} "
        "ORDER BY t.package_id, t.trade_id",
        conn, params=products,
    )
    if trades.empty:
        return []
    book = _book_rows(conn, as_of)
    db = _db_key(conn)
    step = _feed_options_step(conn, as_of)
    feed_reasons, step_error = _feed_skip_reasons(step), _feed_step_error(step)
    legs = []
    for rec in trades.to_dict("records"):
        trade_id = rec["trade_id"]
        pending = _PENDING_TERMS.get((db, rec["instrument_id"])) if db else None
        if pending and rec["strike"]:
            pending = None  # a strike reached the file some other way: the terms on file win
        skip_reason = (_LAST_SKIP.get((db, as_of, trade_id)) if db else None) or feed_reasons.get(trade_id, "")
        legs.append(_leg_row(conn, as_of, rec, book.get(trade_id), pending, skip_reason, step_error))
    return legs


# --------------------------------------------------------------------------- aggregation

def _agg(values: Iterable[Optional[float]]) -> Optional[float]:
    """Sum, skipping missing legs; None (never 0) when every leg is missing or there
    are no legs at all -- the "rows must always render, missing stays missing" rule,
    extended to group sums."""
    vals = [v for v in values if not _is_missing(v)]
    return sum(vals) if vals else None


def _single(values: Iterable) -> Optional[object]:
    """The one value present when a group has exactly one contributing leg, else
    None -- how a single-leg package "is its own one-row package" (its identity
    columns pass straight through) while a multi-leg group leaves them blank
    (not meaningful to combine a strike/expiry/underlying across legs)."""
    vals = list(values)
    return vals[0] if len(vals) == 1 else None


def _common_ccy(group_legs: List[dict]) -> str:
    """The premium currency every leg of the group shares, "mixed" when they differ,
    "" for an empty group."""
    ccys = {l["premium_ccy"] for l in group_legs}
    if not ccys:
        return ""
    return ccys.pop() if len(ccys) == 1 else "mixed"


def _group_numeric(group_legs: List[dict]) -> dict:
    out = {f: _agg(l[f] for l in group_legs) for f in SUM_FIELDS}
    # Position is in the pair's own units (EUR, USD, ounces of gold): it only adds up inside
    # one underlying. Across pairs it is blank; Notional USD is the figure that adds up.
    if len({l.get("underlying") for l in group_legs}) > 1:
        out["position"] = None
    same_ccy = _common_ccy(group_legs) not in ("", "mixed")
    for f in CCY_SUM_FIELDS:  # EUR and USD premiums do not add up: blank unless one currency
        out[f] = _agg(l[f] for l in group_legs) if same_ccy else None
    return out


def _group_note(group_legs: List[dict], with_reasons: bool = False) -> str:
    """"2 of 3 priced" when a leg of the group has no PREMIUM (its sums then cover the
    priced legs only); a package also names the legs left out and why, since they may be
    collapsed out of sight."""
    unpriced = [l for l in group_legs if not l["priced"]]
    if not unpriced:
        return ""
    text = f"{len(group_legs) - len(unpriced)} of {len(group_legs)} priced"
    if with_reasons:
        text += " -- " + "; ".join(f"{l['instrument']}: {l['note'] or 'no PREMIUM mark'}" for l in unpriced)
    return text


def _row(level: str, group_key: str, parent_key: str, label: str, group_legs: List[dict],
         numeric: dict, passthrough: dict, note: str, trade_id: str = "") -> dict:
    row = {"level": level, "group_key": group_key, "parent_key": parent_key, "label": label,
           "leg_count": len(group_legs), "priced_count": sum(1 for l in group_legs if l["priced"]),
           "is_leg": 1 if trade_id else 0, "trade_id": trade_id,
           "closed_count": sum(1 for l in group_legs if l.get("closed_out")),
           "premium_ccy": _common_ccy(group_legs), "note": note}
    row.update(numeric)
    row.update(passthrough)
    return row


def _own_row(level: str, group_key: str, parent_key: str, label: str, leg: dict) -> dict:
    """The row of ONE trade: a LEG row, or the PACKAGE row of a single-leg package."""
    return _row(level, group_key, parent_key, label, [leg],
                {f: leg[f] for f in NUMERIC_FIELDS}, {f: leg[f] for f in PASSTHROUGH_FIELDS},
                leg["note"], trade_id=leg["trade_id"])


def option_rows(conn: sqlite3.Connection, as_of: str, flat: bool = False) -> pd.DataFrame:
    """TOTAL -> ASSET_CLASS (FX always; Commodity present-but-empty) ->
    PACKAGE (one per `trades.package_id`, flat when it has exactly one leg) -> LEG
    (only emitted for a package with >1 leg).

    `flat=True` is the view under an active filter or sort: one LEG row per trade and
    nothing else, built from the same `_leg_rows`, so the numbers are identical and no
    match can hide inside a collapsed package (module docstring)."""
    legs = _leg_rows(conn, as_of)
    if flat:
        return pd.DataFrame([_own_row("LEG", l["trade_id"], "", l["label"], l) for l in legs],
                            columns=None if legs else _FRAME_COLUMNS)
    rows: List[dict] = []

    def _passthrough(group_legs: List[dict]) -> dict:
        return {f: _single(l[f] for l in group_legs) for f in PASSTHROUGH_FIELDS}

    rows.append(_row("TOTAL", "TOTAL", "", "Portfolio Totals", legs,
                      _group_numeric(legs), _passthrough(legs), _group_note(legs)))

    for cls in ASSET_CLASS_ORDER:
        cls_legs = [l for l in legs if l["asset_class"] == cls]
        rows.append(_row("ASSET_CLASS", cls, "TOTAL", cls, cls_legs,
                          _group_numeric(cls_legs), _passthrough(cls_legs), _group_note(cls_legs)))

        seen_pkgs: List[str] = []
        for l in cls_legs:
            if l["package_id"] not in seen_pkgs:
                seen_pkgs.append(l["package_id"])
        for pkg in seen_pkgs:
            pkg_legs = [l for l in cls_legs if l["package_id"] == pkg]
            if len(pkg_legs) == 1:
                rows.append(_own_row("PACKAGE", pkg, cls, pkg_legs[0]["label"], pkg_legs[0]))
                continue
            # MARS-style structure name: "USDCHF - Two Leg" when every leg shares the
            # underlying, else the package id.
            unders = {l["underlying"] for l in pkg_legs}
            word = _LEG_WORDS.get(len(pkg_legs), str(len(pkg_legs)))
            pkg_label = f"{unders.pop()} - {word} Leg" if len(unders) == 1 else pkg
            pkg_pass = _passthrough(pkg_legs)
            pkg_pass["instrument"] = pkg
            rows.append(_row("PACKAGE", pkg, cls, pkg_label, pkg_legs,
                              _group_numeric(pkg_legs), pkg_pass, _group_note(pkg_legs, with_reasons=True)))
            for l in pkg_legs:
                rows.append(_own_row("LEG", l["trade_id"], pkg, l["payoff_word"], l))

    return pd.DataFrame(rows)


_FRAME_COLUMNS = ["level", "group_key", "parent_key", "label", "leg_count", "priced_count", "is_leg",
                  "trade_id", "closed_count", "premium_ccy", "note", *NUMERIC_FIELDS, *PASSTHROUGH_FIELDS]


# --------------------------------------------------------------------------- formatting

# Display formats for the RAW numbers the grid carries (d3-format, applied in the
# browser): whole units with thousands separators and negatives in parentheses -- the
# app's `format_cell` look -- for amounts; up to six decimals, trailing zeros trimmed, for
# premiums and rates, so a fill reads as the blotter wrote it (0.00579, 0.121, 152).
AMOUNT_FORMAT = Format(precision=0, scheme=Scheme.fixed, group=Group.yes, sign=Sign.parantheses)
RATE_FORMAT = Format(precision=6, scheme=Scheme.fixed, group=Group.yes, trim=Trim.yes)
RATE_FIELDS = ("premium_paid", "mktpx", "strike", "undfwdpx")
AMOUNT_FIELDS = NUMERIC_FIELDS
NUMERIC_COLUMNS = AMOUNT_FIELDS + RATE_FIELDS
TEXT_COLUMNS = ("label", "side", "option_type", "payoff", "note", "premium_ccy", "expiry",
                "underlying", "instrument")

# The hint shown in each column's filter box.
_FILTER_HINTS = {
    "label": "contains, e.g. eursek", "side": "buy / sell", "option_type": "call / put",
    "payoff": "e.g. digital", "note": "e.g. no strike", "premium_ccy": "e.g. eur",
    "expiry": ">= 2026-11", "underlying": "e.g. usdjpy", "instrument": "contains",
    "position": "< 0 = sold", "notional": "> 1000000", "pnl_usd": "< 0", "pnl_ccy": "< 0",
    "strike": "> 11", "premium_paid": "> 0.01", "mktpx": "> 0.01",
}


def _num(value) -> Optional[float]:
    """A real number or None -- what a numeric column is allowed to carry. Text, NaN,
    infinities and booleans are all None, so nothing but a number ever reaches one."""
    if isinstance(value, bool) or _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value) -> str:
    return "" if _is_missing(value) else str(value)


CLOSED_OUT_TAG = "(closed out)"


def _fmt_label(rec: dict, collapsed: set) -> str:
    label = rec["label"]
    leg_count = _num(rec.get("leg_count")) or 0
    if leg_count and (_num(rec.get("closed_count")) or 0) >= leg_count:
        label = f"{label} {CLOSED_OUT_TAG}"   # every trade of the row is bought and sold back
    if rec["level"] == "PACKAGE" and rec.get("leg_count") and rec["leg_count"] > 1:
        arrow = "▸" if rec["group_key"] in collapsed else "▾"  # collapsed / expanded
        return f"{arrow} {label}"
    return label


def default_collapsed_packages(df: pd.DataFrame) -> List[str]:
    """Packages with >1 leg start collapsed; a single-leg package has no toggle at
    all (task instruction: "single-leg packages shown flat"). A package with a leg whose
    strike is still missing starts EXPANDED: collapsed, the user would see a flagged
    package row saying "type it in the Strike cell" whose own Strike cell takes no input,
    and the leg that does take it hidden underneath."""
    if df.empty:
        return []
    mask = (df["level"] == "PACKAGE") & (df["leg_count"] > 1)
    legs = df[df["level"] == "LEG"]
    needs_strike = set(legs.loc[legs["note"].astype(str).str.contains("no strike"), "parent_key"])
    return sorted(k for k in df.loc[mask, "group_key"].tolist() if k not in needs_strike)


def format_rows(df: pd.DataFrame, collapsed: Optional[Iterable[str]] = None) -> tuple:
    """`(data_records, style_data_conditional)`, unit-testable without Dash -- same
    convention as `ui.tabs.rates.format_rows` / `ui.tabs.blotter._format_rows`. LEG
    rows whose `parent_key` is in `collapsed` are dropped from the output."""
    collapsed_set = set(collapsed or [])
    records = []
    for rec in df.to_dict("records"):
        if rec.get("level") == "LEG" and rec.get("parent_key") in collapsed_set:
            continue
        # RAW values: numbers (or None) in numeric columns, plain text elsewhere. Missing
        # is None -- a blank cell -- never 0 and never text; `note` carries the reason.
        out = {
            "level": _text(rec.get("level")), "group_key": _text(rec.get("group_key")),
            "parent_key": _text(rec.get("parent_key")),
            "leg_count": int(_num(rec.get("leg_count")) or 0),
            "priced_count": int(_num(rec.get("priced_count")) or 0),
            "is_leg": int(_num(rec.get("is_leg")) or 0),
            "trade_id": _text(rec.get("trade_id")),
            "label": _fmt_label(rec, collapsed_set),
        }
        for col in TEXT_COLUMNS:
            if col != "label":
                out[col] = _text(rec.get(col))
        for col in NUMERIC_COLUMNS:
            out[col] = _num(rec.get(col))
        records.append({col: out[col] for col in HIDDEN_COLUMNS[:4] + DISPLAY_COLUMNS + HIDDEN_COLUMNS[4:]})
    return records, table_styles()


# The native filter row, made legible (seen in a real browser, 2026-09-18: this is the
# app's first table with one). style.css paints every `th` navy -- filter cells are `th` --
# while dash-table keeps the typed text dark grey and the placeholder TRANSPARENT until
# hover, so the hints were invisible and a typed filter nearly so. Scoped to this table
# through its own `css` prop (the stylesheet is not this module's): a light filter row
# under the navy header, dark text, hints always shown.
_FILTER_CELL = ".dash-spreadsheet-container .dash-spreadsheet-inner th.dash-filter"
FILTER_ROW_CSS = [
    {"selector": _FILTER_CELL,
     "rule": "background: var(--card) !important; color: var(--text) !important; "
             "box-shadow: inset 0 -2px 0 var(--navy);"},
    {"selector": f"{_FILTER_CELL} input", "rule": "color: var(--text) !important;"},
    {"selector": f"{_FILTER_CELL} input::placeholder",
     "rule": "color: var(--text) !important; opacity: 0.55 !important; font-style: italic;"},
]

# Rows whose Strike / Type / Payoff cells take input, and the ones still waiting for a strike.
OWN_ROW_QUERY = "{is_leg} = 1"
NEEDS_STRIKE_QUERY = "{is_leg} = 1 && {note} contains 'no strike'"


def table_styles() -> List[dict]:
    """`style_data_conditional`; independent of the data, so the render callback never
    has to resend it."""
    # No cue here is a BORDER colour: ui/assets/style.css forces
    # `border-color: var(--line) !important` on every table cell, so a coloured border
    # renders grey (seen in a real browser, 2026-09-18; `ui/tabs/rates.py::table_styles`
    # hit the same thing). Rules and cues are an `outline` or an inset `box-shadow`.
    # Nor `var(--muted)` / `var(--accent)`: dash-table defines its OWN `--muted`
    # (#c8c8c8) and `--accent` (hotpink) on the table, shadowing the app's, so text in
    # "muted" came out near-white -- the Note column's reasons were barely legible. Only
    # variables the table does not redefine are used: --gold, --navy, --warn, --pos, --neg.
    styles = [
        # Bold only: the filter row right above it already closes with a navy rule.
        {"if": {"filter_query": "{level} = 'TOTAL'"}, "fontWeight": "700"},
        {"if": {"filter_query": "{level} = 'ASSET_CLASS'"}, "fontWeight": "600"},
        {"if": {"filter_query": "{level} = 'ASSET_CLASS'", "column_id": "label"}, "paddingLeft": "8px"},
        {"if": {"filter_query": "{level} = 'PACKAGE'", "column_id": "label"}, "paddingLeft": "24px"},
        # A leg nested under its package (the flat view's rows have no parent: not indented).
        {"if": {"filter_query": "{level} = 'LEG' && {parent_key} != ''", "column_id": "label"},
         "paddingLeft": "44px"},
        # The stated reason for a blank must be readable: the app's warning tone, italic.
        {"if": {"column_id": "note"}, "color": "var(--warn)", "fontStyle": "italic"},
    ]
    # An option with no strike on file cannot be priced: the whole row is flagged so it
    # is impossible to miss (user request 2026-09-18). The strike is typed in its cell.
    styles.append(
        {"if": {"filter_query": "{note} contains 'no strike'"},
         "backgroundColor": "rgba(178, 59, 59, 0.14)", "color": "var(--neg)", "fontWeight": "700"})
    for key in SIGNED_FIELDS:
        styles += [
            {"if": {"filter_query": f"{{{key}}} < 0", "column_id": key}, "color": "var(--neg)", "fontWeight": "700"},
            {"if": {"filter_query": f"{{{key}}} > 0", "column_id": key}, "color": "var(--pos)", "fontWeight": "700"},
        ]
    for col in EDITABLE_COLUMNS:
        styles += [
            # An option's own row: the cell reads as an input -- a quiet dashed gold
            # outline where it already holds a value. A group row: no cue, no typing.
            {"if": {"filter_query": OWN_ROW_QUERY, "column_id": col},
             "outline": "1px dashed var(--gold)", "outlineOffset": "-3px",
             "backgroundColor": "rgba(201, 162, 39, 0.06)", "cursor": "text" if col == "strike" else "pointer"},
            {"if": {"filter_query": "{is_leg} = 0", "column_id": col}, "pointerEvents": "none"},
        ]
    # Strongest where the user has to act (later rules win): the empty Strike cell of an
    # option that cannot be priced without it is a solid gold box on a gold ground, and
    # its Payoff -- to be set first if it is a digital -- a heavier dash. Keyed off the
    # row's own reason, not off a blank strike: a touch option has no strike by design.
    styles += [
        {"if": {"filter_query": NEEDS_STRIKE_QUERY, "column_id": "payoff"},
         "outline": "2px dashed var(--gold)", "outlineOffset": "-3px"},
        {"if": {"filter_query": NEEDS_STRIKE_QUERY, "column_id": "strike"},
         "outline": "2px solid var(--gold)", "outlineOffset": "-3px",
         "backgroundColor": "rgba(201, 162, 39, 0.30)"},
    ]
    return styles


def table_columns() -> List[dict]:
    """Column definitions: typed (`numeric` with a display `Format` over raw numbers,
    `text` otherwise) so the native filter compares numbers as numbers and text by
    "contains", case-insensitively, each with its own hint; Strike editable, Type and
    Payoff dropdowns."""
    columns = []
    for col in ALL_COLUMNS:
        spec = {"name": COLUMN_LABELS.get(col, col.replace("_", " ").title()), "id": col}
        if col in NUMERIC_COLUMNS or col in ("leg_count", "priced_count", "is_leg"):
            spec["type"] = "numeric"
            if col in NUMERIC_COLUMNS:
                spec["format"] = (RATE_FORMAT if col in RATE_FIELDS else AMOUNT_FORMAT).to_plotly_json()
            hint = _FILTER_HINTS.get(col, "> 0, < 0")
        else:
            spec["type"] = "text"
            hint = _FILTER_HINTS.get(col, "contains")
        spec["filter_options"] = {"case": "insensitive", "placeholder_text": hint}
        if col in EDITABLE_COLUMNS:
            spec["editable"] = True
        if col == "strike":
            # A number typed in arrives as a number; anything else is let through AS TYPED
            # so the callback can say what was wrong, and is then reverted from the
            # database -- it never stays in the column (`apply_edit`, `parse_strike`).
            spec["on_change"] = {"action": "coerce", "failure": "accept"}
        if col in ("option_type", "payoff"):
            spec["presentation"] = "dropdown"
        columns.append(spec)
    return columns


def _dropdowns() -> List[dict]:
    """Type / Payoff choices, offered on an option's own row only; a group row has no
    dropdown and shows its value as plain text."""
    return [
        {"if": {"column_id": "option_type", "filter_query": OWN_ROW_QUERY}, "clearable": False,
         "options": [{"label": w, "value": w} for w in TYPE_WORDS.values()]},
        {"if": {"column_id": "payoff", "filter_query": OWN_ROW_QUERY}, "clearable": False,
         "options": [{"label": w, "value": w} for w in PAYOFF_WORDS.values()]},
    ]


def options_table(df: pd.DataFrame, collapsed: Optional[Iterable[str]] = None,
                   table_id: str = TABLE_ID) -> dash_table.DataTable:
    records, style_data_conditional = format_rows(df, collapsed)
    return dash_table.DataTable(
        id=table_id,
        columns=table_columns(),
        hidden_columns=list(HIDDEN_COLUMNS),
        data=records,
        editable=False,  # per column: Strike / Type / Payoff only (`table_columns`)
        dropdown_conditional=_dropdowns(),
        filter_action="native",
        filter_options={"case": "insensitive", "placeholder_text": "filter"},
        sort_action="native",
        sort_mode="multi",
        # The Blotter rebuilds this sub-tab on a data revision; what the user filtered or
        # sorted by must survive that.
        persistence=True, persistence_type="session", persisted_props=["filter_query", "sort_by"],
        css=[{"selector": ".show-hide", "rule": "display: none"},  # no "Toggle Columns" for bookkeeping fields
             {"selector": ".Select-menu-outer", "rule": "display: block !important"},
             *FILTER_ROW_CSS],
        # Room under the last row for a cell dropdown's menu (the scroll box clips it otherwise).
        style_table={"overflowX": "auto", "paddingBottom": "120px"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_cell_conditional=[{"if": {"column_id": "label"}, "textAlign": "left", "minWidth": "200px"},
                                {"if": {"column_id": "note"}, "textAlign": "left", "minWidth": "160px",
                                 "maxWidth": "420px", "whiteSpace": "normal"},
                                *[{"if": {"column_id": c}, "textAlign": "left"}
                                  for c in ("side", "option_type", "payoff", "premium_ccy", "expiry", "underlying")],
                                # No `var(--muted)` here: inside the table it is dash-table's
                                # own near-white (`table_styles`), and this id is what ties a
                                # row back to the trade file.
                                {"if": {"column_id": "instrument"}, "textAlign": "left", "minWidth": "200px"}],
        style_header={"fontWeight": "bold"},
        style_filter={"fontStyle": "italic"},
        style_data_conditional=style_data_conditional,
        page_size=100,
        page_action="native",
        cell_selectable=True,
    )


# --------------------------------------------------------------------------- view + headline

FLAT_VIEW_NOTE = ("Filter or sort active: every matching option is listed on its own row (grouping is off, so a "
                  "match cannot hide inside a collapsed package) and the headline totals the rows shown. "
                  "Clear filters to return to the grouped view.")


def view_is_flat(filter_query: Optional[str], sort_by: Optional[list]) -> bool:
    """True while the table has any native filter or sort on: the grid then lists the
    options flat (module docstring, "Filters and sorting")."""
    return bool((filter_query or "").strip()) or bool(sort_by)


def table_records(conn: sqlite3.Connection, as_of: str, collapsed: Optional[Iterable[str]] = None,
                  filter_query: Optional[str] = "", sort_by: Optional[list] = None) -> List[dict]:
    """The table's `data` for the current view: grouped (collapse applied), or flat under
    a filter / sort."""
    flat = view_is_flat(filter_query, sort_by)
    records, _styles = format_rows(option_rows(conn, as_of, flat=flat), [] if flat else collapsed)
    return records


REFRESH_PAUSED_NOTE = ("Table refresh is paused while a Strike / Type / Payoff cell is selected, so new marks "
                       "never wipe what you are typing. A term you save still shows at once; click any other "
                       "cell to let new marks in again.")


def cell_takes_typing(active_cell) -> bool:
    """True while the table's selection sits on a Strike / Type / Payoff cell -- the only
    sign dash-table gives that something may be being typed (module docstring)."""
    return isinstance(active_cell, dict) and active_cell.get("column_id") in EDITABLE_COLUMNS


def refresh_gate(active_cell, data_rev, book_rev, released) -> Tuple[Optional[str], str]:
    """`(revision to let through to the table or None, note)` for `_gate_refresh`.

    Held (None) for as long as the selection sits on a cell that takes typing: anything
    that then reached the render callback would turn that cell into a label and discard
    its text (module docstring). Nothing is lost by holding: the revision stores keep their
    latest value, and the first selection change away from those cells lets it through.
    None as well when the table has already had this revision."""
    if cell_takes_typing(active_cell):
        return None, REFRESH_PAUSED_NOTE
    token = f"{data_rev or ''}|{book_rev or ''}"
    return (None if token == released else token), ""


# (field, card title, what the figure is) -- units per engine/options/store.py's list.
HEADLINE_FIELDS = (
    ("delta", "Delta", "USD delta notional"),
    ("gamma", "Gamma", "USD delta change per 1% spot move"),
    ("vega", "Vega", "USD per vol point"),
    ("theta", "Theta", "USD per calendar day"),
    ("rho", "Rho", "USD per 1% of the quote-ccy rate"),
    ("start_priced_usd", "Start value", "premium paid on the priced options, USD at today's spot"),
    ("mktval", "Current value", "MktVal of the priced options, USD at today's spot"),
    ("pnl_usd", "P&L USD", "the book's own per-trade P&L"),
)


def headline_totals(rows: Optional[Iterable[dict]]) -> dict:
    """Totals of the rows on screen (`derived_virtual_data`), no database read.

    Grouped view: the PACKAGE rows (each already the sum of its legs, a single-leg package
    being its own leg), so a collapsed package still counts and an expanded one is not
    counted twice. Flat view: the LEG rows that passed the filter. Each figure is the
    `_agg` sum -- priced rows only, None when none is -- and `priced`/`options` say how
    many options that covers."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    basis = [r for r in rows if r.get("level") == "PACKAGE"] or [r for r in rows if r.get("level") == "LEG"]
    out = {field: _agg(_num(r.get(field)) for r in basis) for field, _title, _unit in HEADLINE_FIELDS}
    out["options"] = sum(int(_num(r.get("leg_count")) or 0) for r in basis)
    out["priced"] = sum(int(_num(r.get("priced_count")) or 0) for r in basis)
    out["unpriced"] = [f"{r.get('instrument') or r.get('label')}: {r.get('note') or 'no PREMIUM mark'}"
                       for r in basis
                       if int(_num(r.get("priced_count")) or 0) < int(_num(r.get("leg_count")) or 0)]
    return out


def headline_strip(totals: dict, filtered: bool = False) -> html.Div:
    """The Greeks / value / P&L cards. A figure nothing contributes to reads "n/a" with
    the reason underneath -- never 0."""
    n, m = totals.get("priced", 0), totals.get("options", 0)
    if m == 0:
        why = "no option matches the filter" if filtered else "no option trades on file"
    elif n == 0:
        why = "none of these options has an official PREMIUM mark on this date"
    else:
        why = ""
    cards = []
    for field, title, unit in HEADLINE_FIELDS:
        value = totals.get(field)
        if value is None:
            value_div = html.Div("n/a", className="card-value card-value--muted",
                                 title=why or "no priced option carries this figure")
        else:
            style = {"color": "var(--pos)" if value >= 0 else "var(--neg)"} if field in SIGNED_FIELDS else {}
            value_div = html.Div(format_cell(value), className="card-value", style=style)
        cards.append(html.Div(className="card", children=[
            html.Div(title, className="card-label"), value_div, html.Small(unit, className="card-note")]))
    unpriced = totals.get("unpriced") or []
    cards.append(html.Div(className="card", children=[
        html.Div("Options priced", className="card-label"),
        html.Div(f"{n} of {m}", className="card-value",
                 style={"color": "var(--neg)"} if n < m else {}),
        html.Small("rows shown by the filter" if filtered else "whole option book", className="card-note"),
    ]))
    children = [html.Div(cards, className="cards")]
    if why:
        children.append(html.P(why[0].upper() + why[1:] + ".", className="section-kicker",
                               style={"fontStyle": "italic"}))
    if unpriced:
        shown = "; ".join(unpriced[:6]) + (f"; and {len(unpriced) - 6} more" if len(unpriced) > 6 else "")
        children.append(html.P(f"Not priced, so not in the totals -- {shown}", className="section-kicker",
                               style={"fontStyle": "italic"}))
    return html.Div(children)


# --------------------------------------------------------------------------- terms typed in the grid

class EditResult(NamedTuple):
    """Outcome of one cell edit or one Save: `ok` False = refused, nothing written."""
    ok: bool
    message: str


_STRIKE_TEXT = re.compile(r"^\d+(\.\d+)?$|^\.\d+$")


def parse_strike(value) -> float:
    """A strike as a positive finite number, or ValueError saying what is wrong in plain
    words. Numbers and plain decimal text ("152", "11.4") only: a decimal comma or a
    thousands separator is ambiguous ("1,000" vs "11,4") and is refused, never guessed."""
    shown = "blank" if value is None or (isinstance(value, str) and not value.strip()) else repr(value)
    problem = ValueError(f"Strike must be a positive number such as 152 or 11.4 (got {shown}); nothing was saved.")
    if isinstance(value, bool) or value is None:
        raise problem
    if isinstance(value, str):
        text = value.strip().replace(" ", "")
        if not _STRIKE_TEXT.match(text):
            raise problem
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise problem from None
    if not math.isfinite(number) or number <= 0:
        raise problem
    return number


def _same_cell(a, b) -> bool:
    a_missing = _is_missing(a) or a == ""
    b_missing = _is_missing(b) or b == ""
    if a_missing or b_missing:
        return a_missing and b_missing
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None and not isinstance(a, str) and not isinstance(b, str):
        return na == nb
    return a == b


def detect_edits(rows: Optional[List[dict]], previous: Optional[List[dict]]) -> List[dict]:
    """[{row, column, value}] for every editable cell that differs between the table's
    `data` and its `data_previous`, rows matched by (level, group_key) so a filter or a
    sort in between changes nothing."""
    before = {(r.get("level"), r.get("group_key")): r for r in (previous or []) if isinstance(r, dict)}
    edits = []
    for row in rows or []:
        old = before.get((row.get("level"), row.get("group_key")))
        if old is None:
            continue
        for col in EDITABLE_COLUMNS:
            if not _same_cell(row.get(col), old.get(col)):
                edits.append({"row": row, "column": col, "value": row.get(col)})
    return edits


def _terms_on_file(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    """The trade's instrument and ALL its terms on file. `set_option_terms` writes every
    term it is given (a payoff left out is stored as VANILLA, a barrier as 0), so an edit
    of one cell must hand the other terms back unchanged: a strike typed onto a DIGITAL
    must leave it a DIGITAL. Read with the engine's own `on_file_terms`; the guarded query
    is only for a database whose `instrument_options` predates a column."""
    products_sql, products = _products_in("product")
    hit = conn.execute(
        f"SELECT instrument_id FROM trades_official WHERE trade_id = ? AND {products_sql}",
        (trade_id, *products)).fetchone()
    if hit is None:
        return None
    try:
        from engine.options.store import on_file_terms
        terms = on_file_terms(conn, hit[0])
    except (ImportError, sqlite3.Error):
        opt_cols = _instrument_options_columns(conn)
        exprs = [("strike", "0"), ("option_type", "''"), ("payoff", "'VANILLA'"), ("barrier_level", "0")]
        select = ", ".join(name if name in opt_cols else f"{default} AS {name}" for name, default in exprs)
        row = conn.execute(f"SELECT {select} FROM instrument_options WHERE instrument_id = ?", (hit[0],)).fetchone()
        row = row or (0.0, "", "VANILLA", 0.0)
        terms = {"strike": row[0], "option_type": row[1], "payoff": row[2], "barrier_level": row[3]}
    return {"instrument_id": hit[0], "strike": float(terms.get("strike") or 0.0),
            "option_type": (terms.get("option_type") or "").upper(),
            "payoff": (terms.get("payoff") or "VANILLA").upper(),
            "barrier_level": float(terms.get("barrier_level") or 0.0)}


def _describe(terms: dict) -> str:
    words = [PAYOFF_WORDS.get(terms["payoff"], terms["payoff"]), TYPE_WORDS.get(terms["option_type"], "")]
    if terms.get("strike"):
        words.append(f"strike {terms['strike']:g}")
    return " ".join(w for w in words if w)


def save_and_price(db_path, as_of: Optional[str], trade_id: Optional[str], instrument_id: str, terms: dict,
                   nudge: Optional[Callable[[], None]] = None) -> EditResult:
    """Save an option's terms and price it at once -- the ONE write path of this module,
    shared by the grid's cells and the Option-terms editor's Save.

    `engine.options.store.set_option_terms` validates and persists (in
    `instrument_options`, which a blotter re-upload never overwrites), then
    `engine.options.store.price_and_store` prices `trade_id` as of `as_of` and writes its
    official marks; a skip's `reason` is kept for the row (`_LAST_SKIP`). `nudge`, when a
    caller passes one, is called after a save; the app passes none (2026-09-21: Bloomberg
    is pulled on request only, and a strike changes nothing about what is asked of it).
    Nothing is priced or computed here."""
    from data.ingest.schema import connect
    from engine.options.store import price_and_store, set_option_terms

    db = _norm_path(db_path)
    outcome_text = ""
    try:
        conn = connect(db_path)
        try:
            set_option_terms(conn, instrument_id, terms["strike"], terms["option_type"], terms["payoff"],
                             terms.get("barrier_level") or 0.0)
            _PENDING_TERMS.pop((db, instrument_id), None)
            if as_of and trade_id is None:
                hit = conn.execute("SELECT trade_id FROM trades_official WHERE instrument_id = ? ORDER BY trade_id",
                                   (instrument_id,)).fetchone()
                trade_id = hit[0] if hit else None
            if as_of and trade_id:
                try:
                    outcome = price_and_store(conn, as_of, trade_id)
                    priced, reason = bool(outcome.priced), outcome.reason
                    premium = getattr(outcome.result, "premium", None) if priced else None
                except Exception as exc:  # noqa: BLE001 -- one pricer blow-up must not lose the save
                    priced, reason, premium = False, f"pricer error: {exc!r}", None
                if priced:
                    _LAST_SKIP.pop((db, as_of, trade_id), None)
                    outcome_text = (f" Priced as of {as_of}" + (f": premium {premium:.6g}." if premium is not None else "."))
                else:
                    _LAST_SKIP[(db, as_of, trade_id)] = reason
                    outcome_text = f" Not priced yet: {reason}."
        finally:
            conn.close()
    except ValueError as exc:
        hint = " Enter the level under Option terms below the table." if "barrier" in str(exc) else ""
        return EditResult(False, f"Not saved: {exc}.{hint}")
    except sqlite3.Error as exc:
        return EditResult(False, f"Not saved: database error ({exc}).")
    if nudge is not None:
        nudge()
    return EditResult(True, f"Saved {instrument_id}: {_describe(terms)}.{outcome_text}")


def apply_edit(db_path, as_of: Optional[str], edit: dict,
               nudge: Optional[Callable[[], None]] = None) -> EditResult:
    """Validate and save ONE edited cell (`detect_edits`' shape). Refuses a group row,
    refuses anything but a positive number as a strike, and holds a Payoff / Type chosen
    before the strike exists until the strike is typed (module docstring). Never raises
    for bad input: the message says what was wrong, and the caller re-renders the rows
    from the database, which is what reverts the cell."""
    row, column, value = edit["row"], edit["column"], edit["value"]
    trade_id = row.get("trade_id")
    if not row.get("is_leg") or not trade_id:
        return EditResult(False, "Strike, Type and Payoff are typed on an option's own row, not on a totals or "
                                 "package row; nothing was saved.")
    try:
        from ui.app import connect_readonly
        conn = connect_readonly(db_path)
    except sqlite3.Error as exc:
        return EditResult(False, f"Not saved: database not available ({exc}).")
    try:
        on_file = _terms_on_file(conn, str(trade_id))
    finally:
        conn.close()
    if on_file is None:
        return EditResult(False, f"Not saved: trade {trade_id} is not an option on file.")

    key = (_norm_path(db_path), on_file["instrument_id"])
    if on_file["strike"]:
        _PENDING_TERMS.pop(key, None)  # a strike is on file: nothing is waiting for one any more
    terms = {**on_file, **_PENDING_TERMS.get(key, {})}
    if column == "strike":
        try:
            terms["strike"] = parse_strike(value)
        except ValueError as exc:
            return EditResult(False, str(exc))
    elif column == "option_type":
        code = TYPE_CODES.get(str(value).strip().title()) if value else None
        if code is None:
            return EditResult(False, f"Type must be Call or Put (got {value!r}); nothing was saved.")
        terms["option_type"] = code
    elif column == "payoff":
        code = PAYOFF_CODES.get(str(value).strip()) if value else None
        if code is None:
            return EditResult(False, f"Payoff must be one of {', '.join(PAYOFF_WORDS.values())} "
                                     f"(got {value!r}); nothing was saved.")
        terms["payoff"] = code
    else:
        return EditResult(False, f"{column} cannot be edited here; nothing was saved.")

    if all(terms[k] == on_file[k] for k in ("strike", "option_type", "payoff")):
        return EditResult(True, "")  # what is on file already: nothing to write

    if column != "strike" and terms["payoff"] in STRIKE_PAYOFFS and not terms["strike"]:
        # No strike on file yet: `set_option_terms` cannot store this payoff without one.
        # Hold the choice; it is saved together with the strike, in one call.
        _PENDING_TERMS[key] = {"payoff": terms["payoff"], "option_type": terms["option_type"]}
        return EditResult(True, f"{_describe(terms)} noted for {on_file['instrument_id']}: type its strike in "
                                "the Strike cell to save both (nothing is saved or priced until then).")
    return save_and_price(db_path, as_of, str(trade_id), on_file["instrument_id"], terms, nudge)


def handle_table_event(db_path, as_of: Optional[str], collapsed, filter_query, sort_by,
                       rows: Optional[List[dict]], previous: Optional[List[dict]], edited: bool,
                       nudge: Optional[Callable[[], None]] = None) -> Tuple[List[dict], Optional[html.Span]]:
    """(`data`, status line or None). `edited` = the trigger was a user edit of a cell:
    every changed cell is applied first. The rows are ALWAYS rebuilt from the database
    afterwards, so a refused entry is reverted and no text survives in a numeric column."""
    status = None
    if edited:
        results = [apply_edit(db_path, as_of, e, nudge) for e in detect_edits(rows, previous)]
        failures = [r.message for r in results if not r.ok]
        messages = failures or [r.message for r in results if r.message]
        if messages:
            status = html.Span(" ".join(messages),
                               className="source-result--error" if failures else "source-result--info")
    from ui.app import connect_readonly
    conn = connect_readonly(db_path)
    try:
        records = table_records(conn, as_of, collapsed, filter_query, sort_by)
    finally:
        conn.close()
    return records, status


TERMS_INSTRUMENT_ID = "options-terms-instrument"
TERMS_STRIKE_ID = "options-terms-strike"
TERMS_TYPE_ID = "options-terms-type"
TERMS_PAYOFF_ID = "options-terms-payoff"
TERMS_BARRIER_ID = "options-terms-barrier"
TERMS_SAVE_ID = "options-terms-save"
TERMS_STATUS_ID = "options-terms-status"


def option_instruments(conn: sqlite3.Connection) -> List[dict]:
    """Every option instrument with a trade on file, with its current terms; options
    with no strike first so the ones that block pricing are at the top of the list.
    Same missing-column defence as `_leg_rows` (`_instrument_options_columns`) -- this
    query hits the same pre-`payoff`-column dev DB via `terms_editor`."""
    opt_cols = _instrument_options_columns(conn)
    strike_expr = "COALESCE(o.strike, 0)" if "strike" in opt_cols else "0"
    option_type_expr = "COALESCE(o.option_type, '')" if "option_type" in opt_cols else "''"
    payoff_expr = "COALESCE(o.payoff, 'VANILLA')" if "payoff" in opt_cols else "'VANILLA'"
    barrier_expr = "COALESCE(o.barrier_level, 0)" if "barrier_level" in opt_cols else "0"
    products_sql, products = _products_in()
    rows = conn.execute(
        f"SELECT DISTINCT i.instrument_id, i.expiry_date, {strike_expr}, {option_type_expr}, "
        f"{payoff_expr}, {barrier_expr} "
        "FROM trades_official t JOIN instruments i USING (instrument_id) "
        "LEFT JOIN instrument_options o USING (instrument_id) "
        f"WHERE {products_sql} "
        f"ORDER BY ({strike_expr} = 0) DESC, i.expiry_date, i.instrument_id", products).fetchall()
    return [{"instrument_id": r[0], "expiry": r[1], "strike": r[2], "option_type": r[3],
             "payoff": r[4], "barrier_level": r[5]} for r in rows]


def _terms_dropdown_options(insts: List[dict]) -> List[dict]:
    return [{"label": (f"{i['instrument_id']}  (exp {i['expiry']}" + (", NO STRIKE" if not i["strike"] else "") + ")"),
             "value": i["instrument_id"]} for i in insts]


def terms_editor(conn: sqlite3.Connection) -> html.Details:
    """Option terms the blotter export cannot supply (2026-09-17): a digital's strike,
    a barrier level, or a payoff the free text did not name. Saved into
    `instrument_options` via `engine.options.store.set_option_terms`; a re-upload of the
    blotter never overwrites a value typed here. Saving prices the option at once
    (`save_and_price`). Options with no strike on file are listed first and marked.
    Embedded both under Options and under Manual entry (`ui.tabs.manual_entry`), so it
    must not depend on any component outside itself."""
    insts = option_instruments(conn)
    missing = [i for i in insts if not i["strike"]]
    summary_text = "Option terms" + (f" -- {len(missing)} option(s) cannot be priced until their strike is entered"
                                      if missing else "")
    # The last Save's message, for an editor rebuilt by that very Save (`_LAST_SAVE`).
    when, last_message = _LAST_SAVE.get(_db_key(conn) or "", (0.0, ""))
    flash = (html.Span(last_message, className="source-result--info")
             if last_message and time.time() - when <= _SAVE_MESSAGE_SECONDS else None)
    return html.Details(className="section options-terms", open=bool(missing) or flash is not None, children=[
        html.Summary(summary_text),
        html.P("The blotter export carries no strike, barrier or payoff type for some options "
               "(typically digitals). Strike, Type and Payoff can be typed straight into the Options "
               "table; a barrier / touch level is entered here. Terms survive re-uploads and the "
               "option is priced as soon as they are saved.", className="section-kicker"),
        html.Div(className="toolbar", children=[
            html.Div(className="toolbar-group", children=[
                html.Label("Option"),
                # `persistence`: the Blotter rebuilds the Options sub-tab on every data
                # revision, which used to throw the selection back to the first option
                # with no strike while the user was filling in another one (module
                # docstring, root cause). The selection now survives the rebuild.
                dcc.Dropdown(id=TERMS_INSTRUMENT_ID, options=_terms_dropdown_options(insts),
                             value=(missing[0]["instrument_id"] if missing else (insts[0]["instrument_id"] if insts else None)),
                             clearable=False, style={"width": "360px"},
                             persistence=True, persistence_type="session"),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Payoff"),
                dcc.Dropdown(id=TERMS_PAYOFF_ID, clearable=False, style={"width": "150px"},
                             options=[{"label": PAYOFF_WORDS[k], "value": k} for k in PAYOFF_WORDS]),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Call / Put"),
                dcc.Dropdown(id=TERMS_TYPE_ID, clearable=False, style={"width": "110px"},
                             options=[{"label": "Call", "value": "CALL"}, {"label": "Put", "value": "PUT"}]),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Strike"),
                dcc.Input(id=TERMS_STRIKE_ID, type="number", step="any", style={"width": "120px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Barrier / touch level"),
                dcc.Input(id=TERMS_BARRIER_ID, type="number", step="any", style={"width": "120px"}),
            ]),
            html.Button("Save terms", id=TERMS_SAVE_ID, n_clicks=0, className="btn"),
            html.Span(id=TERMS_STATUS_ID, className="status-line", role="status", children=flash),
        ]),
    ])


# ------------------------------------------------------------------ aggregates above the table (2026-09-21)
# Four views of the options book above the table (user, 2026-09-21: the first set, by payoff
# type / strike / expiry / put-call, said little): where the risk is (by pair), what decays
# when (expiry ladder), which ideas are working (by structure), what is in play (spot against
# strike). Grouping and adding the legs' own figures only; sums cover the priced options and
# the unpriced ones are counted beside them, never added as zero.
# Refreshed in place on every revision (`_refresh_breakdowns`), so the id carries one of the
# rendered-by-callback prefixes (module docstring, "Component ids").
BREAKDOWNS_ID = "options-terms-breakdowns"
EXPIRY_BUCKETS = ((7, "This week"), (31, "Within 1 month"), (92, "1 to 3 months"), (10 ** 6, "Beyond 3 months"))


def _live(legs: pd.DataFrame) -> pd.Series:
    """True on a leg that is still a position: not closed out (the book's status)."""
    if "closed_count" not in legs.columns:
        return pd.Series(True, index=legs.index)
    return legs["closed_count"].map(lambda v: not (_num(v) or 0)).astype(bool)


def _sum_group(name: str, g: pd.DataFrame) -> dict:
    priced = g[g["pnl_usd"].map(lambda v: not _is_missing(v))]
    out = {"group": name, "options": len(g), "unpriced": len(g) - len(priced),
           "closed": int((~_live(g)).sum()),
           "paid": _agg(priced["start_priced_usd"]), "value": _agg(priced["mktval"]), "pnl": _agg(priced["pnl_usd"])}
    out.update({k: _agg(g[k]) for k in ("delta", "gamma", "vega", "theta")})
    out["pnl_pct"] = (out["pnl"] / abs(out["paid"]) * 100.0) if out["pnl"] is not None and out["paid"] else None
    return out


def grouped_rows(legs: pd.DataFrame, keys: pd.Series, order: Optional[List[str]] = None) -> List[dict]:
    """One `_sum_group` row per value of `keys` (in `order` when given) over the LIVE legs
    only, then a Total over every leg. A closed-out option (bought and sold back in full,
    status CLOSED in the book) is no risk and decays nothing, so it is left out of the rows
    before grouping (user, 2026-09-22: "I only want to see live options, no need show closed
    out options"; until then a group was dropped only when its value summed to about zero,
    which kept a pair that also had live options and folded the closed legs into it). The
    Total still carries its P&L and says how many closed-out options are in it."""
    if legs is None or legs.empty:
        return []
    live = _live(legs)
    live_legs, live_keys = legs[live], keys[live]
    names = [n for n in (order or sorted(set(live_keys))) if (live_keys == n).any()]
    rows = [_sum_group(n, live_legs[live_keys == n]) for n in names]
    return rows + [_sum_group("Total", legs)]


def days_to_expiry(expiry, as_of: str) -> Optional[int]:
    try:
        return int((pd.Timestamp(str(expiry)[:10]) - pd.Timestamp(as_of)).days)
    except (TypeError, ValueError):
        return None


def expiry_bucket(expiry, as_of: str) -> str:
    days = days_to_expiry(expiry, as_of)
    if days is None:
        return "No expiry on file"
    if days < 0:
        return "Expired"
    return next(label for limit, label in EXPIRY_BUCKETS if days <= limit)


def in_play_rows(conn: sqlite3.Connection, as_of: str, legs: pd.DataFrame) -> List[dict]:
    """Per open option: spot against strike (how far, in per cent of spot), days left, delta and
    value, nearest to the strike first. Spot is the pair's official SPOT on `as_of`."""
    rows = []
    # A closed-out option (the book's status) is no position: not in play.
    for leg in legs[_live(legs)].to_dict("records"):
        days = days_to_expiry(leg.get("expiry"), as_of)
        if days is None or days < 0:
            continue
        spot = _official_marks(conn, as_of, leg.get("underlying") or "", as_of, ("SPOT",)).get("SPOT")
        strike = leg.get("strike")
        away = ((strike - spot) / spot * 100.0) if spot and not _is_missing(strike) and strike else None
        name = " ".join(str(leg.get(k) or "") for k in ("underlying", "side", "option_type", "payoff")).strip()
        rows.append({"group": name, "strike": strike, "spot": spot, "away_pct": away, "days": days,
                     "delta": leg.get("delta"), "value": leg.get("mktval")})
    return sorted(rows, key=lambda r: (abs(r["away_pct"]) if r["away_pct"] is not None else 1e9, r["days"]))


def _cell(value, kind: str = "money"):
    if _is_missing(value):
        return html.Td("n/a", className="cell--unavailable")
    if kind == "count":
        return html.Td(str(value))
    if kind == "rate":
        return html.Td(f"{float(value):,.4f}".rstrip("0").rstrip("."))
    if kind == "pct":
        return html.Td(f"{float(value):+.1f}%", className="fx-ccy-num--neg" if value < 0 else "fx-ccy-num--pos")
    cls = ("fx-ccy-num--neg" if value < 0 else "fx-ccy-num--pos") if kind == "signed" else ""
    return html.Td(format_cell(value), className=cls)


_KIND_FORMATS = {"count": rk.count(nully="n/a"), "money": rk.amount(nully="n/a"), "signed": rk.amount(nully="n/a"),
                 "rate": rk.rate(4, nully="n/a", trim=True), "pct": rk.percent(1)}


def _agg_table(title: str, kicker: str, first: str, columns: List[Tuple[str, str, str]], rows: List[dict]) -> html.Div:
    """One compact ranked table (ui.tabs.ranking): `columns` = (heading, row key, kind), the
    numbers stored as numbers and formatted by kind; a row named 'Total' is the pinned
    footer, never ranked with the rest."""
    def record(r: dict) -> dict:
        rec = {"group": r["group"] + (f" ({r['unpriced']} unpriced)" if r.get("unpriced") else "")
               + (f" (incl. {r['closed']} closed out)" if r.get("closed") else "")}
        for _heading, key, _kind in columns:
            v = r.get(key)
            rec[key] = None if _is_missing(v) else float(v)
        return rec

    body = [record(r) for r in rows if r["group"] != "Total"]
    total = [record(r) for r in rows if r["group"] == "Total"]
    if not rows:
        inner = html.P("No options on file.", className="section-kicker")
    else:
        table_id = f"{BREAKDOWNS_ID}-{re.sub(r'[^a-z0-9]+', '-', first.lower())}"
        table = dash_table.DataTable(
            id=table_id,
            columns=[rk.text(first, "group")] + [rk.numeric(h, key, _KIND_FORMATS[kind]) for h, key, kind in columns],
            data=body,
            **rk.sortable(table_id),
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                        "padding": "4px 8px"},
            style_cell_conditional=[{"if": {"column_id": "group"}, "textAlign": "left", "fontWeight": "600"}],
            style_header={"fontWeight": "bold"},
            style_data_conditional=rk.sign_styles([key for _h, key, kind in columns if kind in ("signed", "pct")],
                                                  nil={"color": "var(--muted)", "fontStyle": "italic"}),
        )
        total_style = [{"if": {"filter_query": "{group} = 'Total'"}, "fontWeight": "700", "borderTop": "2px solid var(--muted)"}]
        inner = rk.with_footer(table, total, footer_style=total_style)
    return html.Div(className="section fx-ccy-panel", children=[
        html.H4(title, className="fx-ccy-title"), html.P(kicker, className="section-kicker"),
        html.Div(className="fx-ccy-scroll", children=inner)])


def structure_names(conn: sqlite3.Connection, as_of: str, legs: pd.DataFrame) -> pd.Series:
    """Each leg's structure: its package's own label in the grouped view (a straddle, a
    spread), the leg's own label for a single-leg package."""
    grouped = option_rows(conn, as_of)
    label_of = {r["group_key"]: r["label"] for r in grouped.to_dict("records") if r.get("level") == "PACKAGE"}
    parent_of = {r["trade_id"]: r.get("parent_key") for r in grouped.to_dict("records") if r.get("level") == "LEG"}
    return legs.apply(lambda r: label_of.get(parent_of.get(r["trade_id"]) or r["group_key"]) or r["label"], axis=1)


def breakdown_tables(conn: sqlite3.Connection, as_of: str) -> html.Div:
    return html.Div(id=BREAKDOWNS_ID, className="fx-ccy-tables", children=breakdown_children(conn, as_of))


def breakdown_children(conn: sqlite3.Connection, as_of: str) -> List[html.Div]:
    """The four tables themselves: what `build_layout` puts in `BREAKDOWNS_ID` and what
    `_refresh_breakdowns` replaces there on a revision."""
    legs = option_rows(conn, as_of, flat=True)
    empty = legs is None or legs.empty
    money = [("Options", "options", "count"), ("Premium paid USD", "paid", "money"),
             ("Current value USD", "value", "money"), ("P&L USD", "pnl", "signed")]
    greeks = [("Delta USD", "delta", "signed"), ("Gamma", "gamma", "signed"), ("Vega", "vega", "signed"),
              ("Theta / day", "theta", "signed")]
    by_pair = [] if empty else grouped_rows(legs, legs["underlying"].fillna(""))
    ladder = [] if empty else grouped_rows(legs, legs["expiry"].map(lambda e: expiry_bucket(e, as_of)),
                                           ["Expired"] + [label for _l, label in EXPIRY_BUCKETS] + ["No expiry on file"])
    structures = [] if empty else grouped_rows(legs, structure_names(conn, as_of, legs))
    return [
        _agg_table("By pair: where the risk is", "Dollars paid, worth and made, with the net Greeks, per underlying.",
                   "Pair", money + greeks, by_pair),
        _agg_table("Expiry ladder: what decays when", "Current value is what is lost if these expire worthless.",
                   "Expires", [money[0], money[2], greeks[3], greeks[1], greeks[2], money[3]], ladder),
        _agg_table("By structure: which ideas are working", "P&L as a per cent of the premium paid.",
                   "Structure", money + [("P&L % of premium", "pnl_pct", "pct")], structures),
        _agg_table("Spot against strike: what is in play", "Open options, nearest to their strike first.",
                   "Option", [("Strike", "strike", "rate"), ("Spot", "spot", "rate"), ("Strike vs spot", "away_pct", "pct"),
                              ("Days left", "days", "count"), ("Delta USD", "delta", "signed"),
                              ("Current value USD", "value", "money")],
                   [] if empty else in_play_rows(conn, as_of, legs)),
    ]


def build_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole Options sub-tab body: the Greeks / value / P&L headline, then Portfolio
    Totals -> asset class -> package -> leg (multi-leg packages collapsed by default),
    then the option-terms editor. The headline is built here, not in `blotter.py`, from
    the same records the table is given."""
    df = option_rows(conn, as_of)
    collapsed = default_collapsed_packages(df)
    table = options_table(df, collapsed)
    return html.Div(className="section", children=[
        # Session storage: a rebuild of the sub-tab keeps what the user expanded.
        dcc.Store(id=COLLAPSED_STORE_ID, data=collapsed, storage_type="session"),
        # The revision last let through to the table (`refresh_gate`).
        dcc.Store(id=REFRESH_ID),
        html.Div(id=HEADLINE_ID, children=headline_strip(headline_totals(table.data))),
        html.Div(className="toolbar", children=[
            html.Span("Click a Strike cell, type the strike and press Enter; pick Type / Payoff in theirs "
                      "(the gold-outlined cells right after the structure name, on an option's own row; a "
                      "solid gold box is a strike still missing). Filter boxes take text, or a comparison "
                      "such as > 1000000, < 0, >= 2026-11.", className="section-kicker"),
            html.Button("Clear filters", id=CLEAR_FILTERS_ID, n_clicks=0, className="btn btn--ghost"),
        ]),
        html.Div(id=EDIT_STATUS_ID, className="status-line", role="status"),
        html.Div(id=REFRESH_NOTE_ID, className="section-kicker", style={"fontStyle": "italic"}),
        html.Div(id=VIEW_NOTE_ID, className="section-kicker", style={"fontStyle": "italic"}),
        breakdown_tables(conn, as_of),
        table,
        terms_editor(conn),
    ])


def register_callbacks(app, get_db_path: Callable[[], object],
                        date_picker_id: str = DEFAULT_DATE_PICKER_ID) -> None:
    """Six callbacks on the table -- expand/collapse, the refresh gate (revisions, held
    while a term cell is selected), render (collapse, filter/sort view, cell edits, the
    revisions the gate lets through), the selection released after a cell edit, headline,
    clear filters -- one on the four tables above it (redrawn on every revision) and two on
    the Option-terms editor (prefill, save). See the module docstring for each mechanism."""
    from dash import ctx, no_update
    from dash.exceptions import MissingCallbackContextException, PreventUpdate

    def _triggered_props() -> set:
        try:
            return {t["prop_id"] for t in (ctx.triggered or [])}
        except MissingCallbackContextException:  # called directly (a test), not by Dash
            return set()

    @app.callback(
        Output(COLLAPSED_STORE_ID, "data"),
        Input(TABLE_ID, "active_cell"),
        State(TABLE_ID, "derived_viewport_data"),
        State(TABLE_ID, "data"),
        State(COLLAPSED_STORE_ID, "data"),
        prevent_initial_call=True,
    )
    def _toggle_package(active_cell, viewport_rows, data_rows, collapsed):
        # `active_cell.row` indexes the rows ON SCREEN (after filter / sort / page), which
        # is `derived_viewport_data`; `data` only before the table has derived anything.
        rows = viewport_rows if viewport_rows is not None else data_rows
        if not active_cell or not rows:
            return no_update
        idx = active_cell.get("row")
        if idx is None or idx >= len(rows):
            return no_update
        row = rows[idx]
        if row.get("level") != "PACKAGE" or not row.get("leg_count") or row["leg_count"] <= 1:
            return no_update
        pkg = row.get("group_key")
        current = set(collapsed or [])
        if pkg in current:
            current.discard(pkg)
        else:
            current.add(pkg)
        return sorted(current)

    @app.callback(
        Output(REFRESH_ID, "data"),
        Output(REFRESH_NOTE_ID, "children"),
        Input(DATA_REVISION_ID, "data"),
        Input(BOOK_REVISION_ID, "data"),
        Input(TABLE_ID, "active_cell"),
        State(REFRESH_ID, "data"),
    )
    def _gate_refresh(data_rev, book_rev, active_cell, released):
        """The ONLY listener of the revision stores on this table's behalf, and it does not
        output to the table: dash-table turns the cell being typed into into a label, text
        lost, for as long as a callback that does is in flight (module docstring). So a
        revision is passed on to `_render` -- through `REFRESH_ID` -- only while the
        selection is on no Strike / Type / Payoff cell; `active_cell` is an Input so that
        moving off such a cell lets a held revision through at once. No database access."""
        token, note = refresh_gate(active_cell, data_rev, book_rev, released)
        return (no_update if token is None else token), note

    @app.callback(
        Output(TABLE_ID, "data"),
        Output(EDIT_STATUS_ID, "children"),
        Output(VIEW_NOTE_ID, "children"),
        Input(COLLAPSED_STORE_ID, "data"),
        Input(TABLE_ID, "filter_query"),
        Input(TABLE_ID, "sort_by"),
        Input(TABLE_ID, "data_timestamp"),
        Input(REFRESH_ID, "data"),   # never the revision stores themselves: `_gate_refresh`
        State(TABLE_ID, "data"),
        State(TABLE_ID, "data_previous"),
        State(date_picker_id, "date"),
    )
    def _render(collapsed, filter_query, sort_by, _edited_at, _refresh, rows, previous, as_of_date):
        # Not `prevent_initial_call`: a rebuilt sub-tab comes back with the session's
        # filter / sort / collapse state, and the rows must match it from the start.
        if not as_of_date:
            raise PreventUpdate
        triggered = _triggered_props()
        edited = f"{TABLE_ID}.data_timestamp" in triggered
        try:
            records, status = handle_table_event(get_db_path(), as_of_date, collapsed, filter_query, sort_by,
                                                 rows, previous, edited)
        except sqlite3.OperationalError:
            raise PreventUpdate
        note = FLAT_VIEW_NOTE if view_is_flat(filter_query, sort_by) else ""
        revision_only = bool(triggered) and triggered <= {f"{REFRESH_ID}.data"}
        if revision_only and records == rows:
            return no_update, no_update, note  # nothing on screen changed: leave the table alone
        return records, (status if edited else no_update), note

    @app.callback(
        Output(TABLE_ID, "active_cell", allow_duplicate=True),
        Output(TABLE_ID, "selected_cells", allow_duplicate=True),
        Input(EDIT_STATUS_ID, "children"),
        prevent_initial_call=True,
    )
    def _release_selection(_status):
        """A cell edit has just been answered (`_render` writes the status line on an edit
        and on nothing else): drop the table's selection. Enter leaves it on the Strike cell
        of the row below, and for as long as it sat there `_gate_refresh` held every later
        revision -- the next "Pull Bloomberg now", an upload -- so the table stayed as it was
        until the user clicked elsewhere or reloaded the page (2026-09-21, user: "make it so
        i dont have to refresh"). `allow_duplicate`, so these two props are no second route
        from `_render` back to the gate in Dash's callback graph."""
        return None, []

    @app.callback(
        Output(BREAKDOWNS_ID, "children"),
        Input(DATA_REVISION_ID, "data"),
        Input(BOOK_REVISION_ID, "data"),
        State(date_picker_id, "date"),
        prevent_initial_call=True,
    )
    def _refresh_breakdowns(_data_rev, _book_rev, as_of_date):
        """The four tables above the grid, redrawn in place on every revision: a saved
        strike, new marks, a re-upload. They were built once per sub-tab and kept the
        figures of that moment beside a table that had moved on. Not behind the gate:
        nothing here outputs to the table, so no typing is at risk. A database that cannot
        be read right now leaves them as they are; the next revision tries again."""
        if not as_of_date:
            raise PreventUpdate
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(get_db_path())
        except sqlite3.Error:
            raise PreventUpdate
        try:
            return breakdown_children(conn, as_of_date)
        except sqlite3.Error:
            raise PreventUpdate
        finally:
            conn.close()

    @app.callback(
        Output(HEADLINE_ID, "children"),
        Input(TABLE_ID, "derived_virtual_data"),
        State(TABLE_ID, "filter_query"),
        State(TABLE_ID, "sort_by"),
    )
    def _headline(rows, filter_query, sort_by):
        """Totals of the rows shown. Under a filter the table first filters the grouped
        rows it already holds and only then receives the flat ones: that in-between state
        is skipped, not summed."""
        if rows is None:
            raise PreventUpdate
        if view_is_flat(filter_query, sort_by) and any(r.get("level") != "LEG" for r in rows):
            raise PreventUpdate
        return headline_strip(headline_totals(rows), filtered=bool((filter_query or "").strip()))

    @app.callback(
        Output(TABLE_ID, "filter_query"),
        Output(TABLE_ID, "sort_by"),
        Input(CLEAR_FILTERS_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_filters(_n_clicks):
        return "", []

    @app.callback(
        Output(TERMS_PAYOFF_ID, "value"),
        Output(TERMS_TYPE_ID, "value"),
        Output(TERMS_STRIKE_ID, "value"),
        Output(TERMS_BARRIER_ID, "value"),
        Input(TERMS_INSTRUMENT_ID, "value"),
    )
    def _prefill_terms(instrument_id):
        """Show the terms currently on file for the chosen option."""
        if not instrument_id:
            raise PreventUpdate
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(get_db_path())
        except sqlite3.OperationalError:
            raise PreventUpdate
        try:
            row = next((i for i in option_instruments(conn) if i["instrument_id"] == instrument_id), None)
        finally:
            conn.close()
        if row is None:
            raise PreventUpdate
        return (row["payoff"] or "VANILLA", row["option_type"] or None,
                row["strike"] or None, row["barrier_level"] or None)

    @app.callback(
        Output(TERMS_STATUS_ID, "children"),
        Output(TERMS_INSTRUMENT_ID, "options"),
        Output(DATA_REVISION_ID, "data", allow_duplicate=True),
        Output(BOOK_REVISION_ID, "data", allow_duplicate=True),
        Input(TERMS_SAVE_ID, "n_clicks"),
        State(TERMS_INSTRUMENT_ID, "value"),
        State(TERMS_PAYOFF_ID, "value"),
        State(TERMS_TYPE_ID, "value"),
        State(TERMS_STRIKE_ID, "value"),
        State(TERMS_BARRIER_ID, "value"),
        State(date_picker_id, "date"),
        prevent_initial_call=True,
    )
    def _save_terms(n_clicks, instrument_id, payoff, option_type, strike, barrier, as_of_date=None):
        """Save the editor's terms, price the option, wake the feed and publish both
        revisions (`ui/revision.py`) so the grid, the strips and the header redraw at once.

        Every Output and State here is either inside the editor itself or always in the
        page. It used to output to (and read) `options-collapsed-packages`, which exists
        only under Options: from Manual entry > Option terms -- where the "cannot be
        priced" banner sends the user -- Dash dropped the callback in the browser and Save
        did nothing (module docstring, root cause)."""
        if not n_clicks or not instrument_id:
            return no_update, no_update, no_update, no_update
        from ui import revision
        db_path = get_db_path()
        terms = {"strike": strike or 0.0, "option_type": (option_type or "").upper(),
                 "payoff": (payoff or "VANILLA").upper(), "barrier_level": barrier or 0.0}
        result = save_and_price(db_path, as_of_date, None, instrument_id, terms)
        if not result.ok:
            return html.Span(result.message, className="source-result--error"), no_update, no_update, no_update
        _LAST_SAVE[_norm_path(db_path)] = (time.time(), result.message)
        try:
            from ui.app import connect_readonly
            conn = connect_readonly(db_path)
            try:
                dropdown = _terms_dropdown_options(option_instruments(conn))
            finally:
                conn.close()
        except sqlite3.Error:
            dropdown = no_update
        return (html.Span(result.message, className="source-result--info"), dropdown,
                revision.file_signature(db_path), revision.book_signature(db_path))
