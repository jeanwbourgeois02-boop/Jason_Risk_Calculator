"""Blotter tab (key "blotter"; the Trades tab of 2026-09-28, the Blotter again since 2026-09-29).

2026-09-29 (user): "What did I load, and did it load right?" The tab is the audit trail of the
uploaded file and shows NO P&L, mark, Greek or period figure (the Book is the one place for
them). One view, `blotter_view`: the last upload (one line, then the rows that did not become
trades and the file-level warnings, `data.ingest.upload.last_upload_report` /
`last_upload_issues`; this block left the Data tab that day), every fill as uploaded (one table,
one row per trade on file: Trade Id, trade date, side, quantity with its unit, the broker's
description as written, our contract, the broker's price and ours with a grey x100 where the
broker's price units were converted, the trade name, the type and the Book position the trade
landed in, the status and its uploads, `trade_upload_trail`; filters, a search, a date range and
a CSV), the upload history (`upload_history`, a closed fold) and the Data issues drawer. The
Options sub-tab sits beside it only while the book holds an open option (`has_open_option`),
kept as it was; the Bundles sub-tab left. The notes below are the tab's history: the priced
trade table they describe (`scope_df`, `detail_table`, the strips) is no longer rendered on this
tab; `add_instrument_fields` and `add_trade_labels` stay, the P&L tab reads them.

Before 2026-09-29 -- Trades tab (the Blotter until 2026-09-28): docs/BUILD_PLAN.md section 5 "Blotter", rebuilt
2026-09-15 into sub-tabs (user decision): All trades (the Total book), Options, Bundles. The FX
and Futures & LME sub-tabs left on 2026-09-28 (UI redesign wave 3: their rows are all in All
trades, their summaries on the P&L and Exposure tabs) and their code with them (`ui.tabs.blotter_fx`
deleted, the futures grouping cut). The Manual entry sub-tab and the manual booking path
(`ui.tabs.manual_entry`, `data/ingest/manual.py`) left the same day (user: the blotter upload
is the only way a trade enters the app). The Rates
sub-tab left on 2026-09-24 with the macro trader's products (CLAUDE.md "Commodity
conversion plan", Phase 2: rates, NDFs, the FX-swap package rule and the equity index are
out of the app). Rows come from
`engine.pnl.valuation.value_book(as_of)` (via `ui.tabs.blotter_pricing.priced_value_book`,
a thin memoisation layer -- see that module's docstring), filtered per sub-tab by
`product`. A row with no official mark shows its `reason` and blank P&L; it is never
retried against `BNP_BVAL` (removed 2026-09-17, user decision "no bnp fall back" --
CLAUDE.md already says BNP_BVAL is reconciliation-only and never feeds P&L, so the
retry this docstring used to describe was a violation of that rule, not a feature).

Filtering (rebuilt 2026-09-15, user decision -- the prior native text filter row on
`dash_table.DataTable` did not work in this Dash version: verified with a bare
three-row reproduction table outside this app, so the fix is our own filter bar, not a
config tweak): one multi-select `dcc.Dropdown` per categorical column (Pair/Contract,
Side, Status, Product, Strategy, Bundle), listing only the values present in that
sub-tab's own trades. Picking values there re-queries the same priced book, keeps only
the matching rows, and replaces the table's `data` -- which is also what drives the
P&L strip below (`Input(table_id, "derived_virtual_data")`), so picking USDJPY narrows
both the rows and every LTD/Daily/.../Trading figure to just those trades. Sorting is
native (`ui.tabs.ranking`, user 2026-09-22: every table ranks on a header click, numbers
stored as numbers); the settle-date / pair order is only the order a table opens in.

Screens redesign, Phase A (user, 2026-09-25; CLAUDE.md "Screens redesign plan"): the
Total book has NO P&L strip any more -- the header above every tab is the total book. Its
trade table reads in commodity terms (`_DISPLAY_COLUMNS`: instrument, commodity or pair,
exchange, plain product name, side, quantity with its unit, fill, mark, previous close, P&L
in the contract's own currency with the currency, P&L USD, status, expiry / value date,
bundle, trade id), every figure as `value_book` gives it (`add_instrument_fields`,
`add_prev_close`). The other sub-tabs keep their scoped strip, compact
(`render_headline_strip`: k / m, a marker such as "excl. 2" with its sentence on hover,
the full figure on hover of the value). Section definitions sit on hover of their title
(`ui.tabs.formatting.about`), never as a paragraph; summary money is k / m
(`rk.amount_short` + `rk.whole_units`), trade rows keep full figures.

Sub-tab layout (Options; the Total book since 2026-09-25 has no strip):
  (a) a P&L strip: LTD, Daily, Previous day, 5d, MTD, YTD, Trading -- recomputed for
      exactly the rows currently visible in that sub-tab's table AFTER native header
      filtering (`derived_virtual_data`), per the 2026-09-15 coordinator addition, via
      `ui.tabs.blotter_pricing.row_scoped_period_pnl`. Each figure shows its reference
      date underneath.
  (b) the trade table: `status` (OPEN/SETTLED) and `instrument_id` are ordinary native-
      filterable columns; row expand (an `html.Details` per trade) shows legs and the
      marks used, exactly as before.

Options (2026-09-17): a real view through the generic path -- `value_book` now builds
FX_OPTION rows (premium mark minus fill, in base currency, converted at spot), so the
Options sub-tab is the same strip + filter bar + table as Total book, scoped to
`FX_OPTION`. `PLACEHOLDER_SCOPES` is kept (empty) for the placeholder rendering path.

Options wiring (2026-09-18). `ui.tabs.options` brings its own callbacks, registered from
`register_callbacks` here. It refreshes its own table in place from the revision stores,
so `_update` does not rebuild that sub-tab on a marks-only revision
(`_SELF_REFRESHING_SCOPES`); the P&L strip above the table, built here, follows through
`_register_strip_refresh`. A genuine book change (an upload), a date change and a sub-tab
change still rebuild it; a saved option term does
not, although it is published as a book revision: `_update` compares
`ui.revision.trade_set_signature` with the one the content on screen was built from
(`BUILT_TRADE_SET_ID`), so typing three strikes in a row is never interrupted by a
rebuild. The banners (missing option terms, stored values
that are not numbers) sit in `NOTICES_ID`, always in the page and refreshed in place on
every revision (`_refresh_notices`), and a saved Options cell publishes the data revision
at once (`_publish_option_cell_edit`), so the banner drops an option the moment its strike
is saved. The Options strip (`options_strip`) shows only the cards that have a value:
a card waiting for an EARLIER close's option marks -- which exist only for the days the
app ran with Bloomberg -- is left out and named in one caption line under the row
(`options_hidden_cards`); a card that is "n/a" for any other reason stays, with its
reason. Every other scope's strip always shows every card.

Total book (2026-09-17, user request "there should be P&L by asset type"):
`asset_class_pnl_table` shows one row per asset class present (Futures, LME forwards,
Options, then FX since 2026-09-25) plus Total, each with LTD / Daily / Previous day / 5d /
MTD / YTD / Trading computed by `row_scoped_period_pnl` over that class's trade ids -- the
header's arithmetic and reference dates, so the class rows sum to the Total, in k / m.
Above them a collapsed "Data issues (N)" drawer (`total_book_issues`) lists every trade
with no P&L and every trade valued from an earlier close, with its reason.
Above it, the Positions block (`positions_table`), from one `engine.ladder.positions.
book_positions` call: first the Commodities (Phase 3; `commodity_positions_section`: one
line per sector, the sector's commodities under it, the Commodities total, then the P&L the
non-USD futures hold in each currency), then the FX lines as before (the currency rows, the
FX net and gross, the FX options' delta). Commodities are not in the FX net.

Futures, LME forwards and FX trades are rows of All trades (commodity terms: each trade's
exchange, sector and name from the contract master, `data.contracts.load_roots`, `value_book`'s
`pnl_local` with its currency beside the USD P&L the engine converted at spot; an LME ticket's
local P&L is its USD P&L, USD-quoted; nothing is converted here). Their sub-tabs and the
grouped futures table left on 2026-09-28. Options on commodity futures (CMDTY_OPTION) are on
the Options sub-tab.

Bundles sub-tab (item 4): `ui.tabs.blotter_bundles` renders a list of bundles (from
`data.ingest.themes.list_bundles`) with LTD/Daily/MTD/YTD via
`engine.pnl.ledger.period_pnl_by(conn, as_of, 'theme')`, plus an "Unassigned" line, a
create form and add/remove-pair actions calling `data.ingest.themes.set_theme` (via the
`add_pair_to_bundle` / `remove_pair_from_bundle` helpers).

The Option terms editor (`ui.tabs.options.terms_editor`, a strike, type or payoff the export
left blank, saved into `instrument_options`) stays on the Options sub-tab.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs import blotter_fills as fills_ui
from ui.tabs import options as options_ui
from ui.tabs.blotter_pricing import (
    HEADLINE_ORDER,
    HEADLINE_TITLES,
    add_row_display_fields,
    describe_bad_stored_values,
    priced_value_book,
    pricing_snapshot,
    row_scoped_headline,
)
from ui.revision import (
    BOOK_REVISION_ID,
    DATA_REVISION_ID,
    file_signature,
    publish_if_changed,
    trade_set_signature,
)
from ui.tabs.controls import today_ny
from ui.tabs.header import AS_OF_STORE_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import compact
from ui.tabs.formatting import (
    cap, tidy,
    MISSING, about, format_cell, is_fx_pair, issues_drawer, marker,
    plain_words, price_text, quoted_unit, short_money, short_root_name, trade_type_words,
)

# The tab's own date picker and title row left on 2026-09-28 (the screens tidy): the header's
# picker is the one place the as-of changes, and every callback here reads `header.AS_OF_STORE_ID`.
SUBTABS_ID = "blotter-subtabs"
CONTENT_ID = "blotter-content"
# Always in the page (`build_layout`), never inside the rebuilt content (2026-09-18):
# the banners, so they refresh in place on every revision instead of only when a sub-tab
# is rebuilt, and the trade-set signature the content on screen was built from, which
# `_update` reads back to tell a genuine book change from a saved option term.
NOTICES_ID = "blotter-notices"
BUILT_TRADE_SET_ID = "blotter-built-trade-set"

# Kept for the pre-2026-09-15 tests that still exercise a single detail table by id.
TABLE_CONTAINER_ID = "blotter-table-container"
DATATABLE_ID = "blotter-datatable"
DETAIL_PANEL_ID = "blotter-datatable"  # "-{scope}-detail" suffix keeps the id under the
# test_ui.py "blotter-datatable-" dynamic-id allow-list (owned by another agent)

_ALL = "All"

# --------------------------------------------------------------------------- sub-tabs
# Order per user decision 2026-09-15: Total book | FX | Futures | Options | Bundles; the FX and
# Futures & LME sub-tabs and the Manual entry form left on 2026-09-28 (every trade is a row
# of All trades; the blotter upload is the only way a trade enters the app). Rates left on
# 2026-09-24 (the macro trader's products are out of the app, CLAUDE.md "Commodity
# conversion plan").
# The Blotter (2026-09-29): one view, "total" (every fill as uploaded, `blotter_view`), and the
# Options sub-tab beside it only while the book holds an open option (`has_open_option`); the
# sub-tab bar is hidden otherwise. The Bundles sub-tab left that day (`ui.tabs.blotter_bundles`
# stays on disk, rendered nowhere; the bundle data is untouched).
SCOPE_ORDER = ("total", "options")
SCOPE_LABELS = {"total": "All fills", "options": "Options"}
SCOPE_PRODUCTS = {
    "total": None,
    # An option on a commodity future (CMDTY_OPTION) sits with the FX options: ui.tabs.options
    # lists it in its own grid.
    "options": ("FX_OPTION", "CMDTY_OPTION"),
}
# Sub-tabs that are forms/lists of their own, not `priced_value_book`-shaped tables:
# no strip / row-detail / filter callbacks are registered for them.
_NON_TABLE_SCOPES = ("total", "options")
# Views rebuilt whole when only marks changed (ui/revision.py); see `_update`. None since the
# Blotter shows no mark (2026-09-29).
_MARKS_REBUILD_SCOPES: tuple = ()
# Sub-tabs whose own module refreshes its table IN PLACE from the revision stores
# (`ui.tabs.options._render` / `_headline`), so it left the list above on 2026-09-18: a
# wholesale rebuild on every Bloomberg pull reset the Options terms editor's dropdown and
# could wipe a strike being typed into a cell. A genuine book change (an upload --
# `_update` compares `revision.trade_set_signature`) and a date
# or sub-tab change still rebuild it; a saved term does NOT, although it is published as a
# book revision too.
_SELF_REFRESHING_SCOPES = ("options",)
# Table scopes with no P&L strip of their own (Screens redesign Phase A, 2026-09-25): the
# header above every tab IS the total book, so the Total book shows no cards; no strip
# callback is registered for it. Options left the strip too (layout wave 2, 2026-09-29: its eleven
# P&L tiles repeated the header; its Greeks are the totals row of "By pair or underlying").
_STRIPLESS_SCOPES = ("total", "options")
# Kept (empty) so the placeholder path stays available for a future scope.
PLACEHOLDER_SCOPES: dict = {}

# Asset class per product for the Total book's per-class P&L table. An option on a
# commodity future (CMDTY_OPTION) is an option; an LME prompt-date forward (LME_FWD, Phase 5)
# is its own class, "LME forwards". A product not listed here (an IRS left on an
# old database: `value_book` gives it blank P&L and its reason) falls to "Other" in
# `asset_class_pnl_rows`, still counted, its reason on hover, never dropped.
ASSET_CLASS_OF = {"FX_SPOT": "FX", "FX_FWD": "FX", "FX_SWAP": "FX", "FUTURE": "Futures", "FX_OPTION": "Options",
                  "CMDTY_OPTION": "Options", "LME_FWD": "LME forwards"}
# The commodity classes first (Screens redesign Phase A, 2026-09-25: Jason's book is
# commodities, with FX as its hedges), FX last.
ASSET_CLASS_ORDER = ("Futures", "LME forwards", "Options", "FX")
ASSET_TABLE_ID = "blotter-asset-class-table"
POSITIONS_TABLE_ID = "blotter-positions-table"
# The unit of the Quantity column, per product: a future is booked in contracts, an LME
# forward in tonnes (data/ingest/blotter.py::_parse_lme_forward).
QUANTITY_UNIT_OF = {"FUTURE": "lots", "LME_FWD": "t", "CMDTY_OPTION": "lots"}
# FX trades (the Total book's rows): the quantity is the base-currency amount (an FX
# option's notional), so its unit is the pair's base currency; the pair takes the
# Commodity column's place and the exchange reads OTC.
FX_ROW_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
FX_SECTOR = "FX"
FX_EXCHANGE = "OTC"

# Columns offered as click-to-filter dropdowns (user decision 2026-09-15, replacing the
# broken native filter row): every categorical column that exists in a scope's own
# display columns. Date/amount/rate columns are not offered -- multi-select-from-values
# only makes sense for the categorical ones; "pick USDJPY" is the request, not a range
# filter.
FILTERABLE_COLS = ["sector", "commodity", "instrument_id", "side", "status", "product", "strategy", "trade_type", "theme"]
# All trades (the Total book until 2026-09-28, UI redesign wave 1): the dropdowns commodity,
# product, strategy, type (user, 2026-09-28: "it must also be possible to sort the trades" by the
# broker's trade type and by strategy) and status, a free-text search over the instrument and the
# trade id, and a CSV of the rows shown at full figures.
TOTAL_FILTER_COLS = ["commodity", "product", "strategy", "trade_type", "status"]
SEARCH_COLS = ("instrument_id", "trade_id")


def filter_cols_for(scope: str, display_columns: list) -> list:
    """The columns a sub-tab offers as dropdown filters."""
    wanted = TOTAL_FILTER_COLS if scope == "total" else FILTERABLE_COLS
    return [c for c in wanted if c in display_columns]


def search_rows(df: pd.DataFrame, text: Optional[str]) -> pd.DataFrame:
    """The rows whose instrument or trade id contains `text` (case-insensitive); all with none."""
    needle = str(text or "").strip().lower()
    if not needle or df.empty:
        return df
    cols = [c for c in SEARCH_COLS if c in df.columns]
    if not cols:
        return df
    hit = pd.Series(False, index=df.index)
    for c in cols:
        hit |= df[c].astype(str).str.lower().str.contains(needle, regex=False)
    return df[hit]


# A futures contract whose root the contract master does not know: its sector reads this,
# and its commodity is the root id on file (a label, never a guess at the contract).
UNCLASSIFIED_SECTOR = "Unclassified"
# A settled future is the ledger's frozen row (`realised_pnl`), which holds its P&L in USD
# only: `value_book` leaves `pnl_local` blank on it, and this is said where the number
# would be (engine/pnl/valuation.py::_settled_future_row).
SETTLED_LOCAL_REASON = "settled: this trade's P&L is frozen in USD; its local-currency P&L is not stored"

# The Total book's trade table in commodity terms (Screens redesign Phase A, 2026-09-25,
# replacing the macro book's FX layout: "Pair" holding a futures contract, "Amount" meaning
# lots, a blank "Notional (USD)" on every future). Instrument = the contract, the LME metal
# or the pair; Commodity = the contract master's name, or the pair for an FX trade; Quantity
# is unsigned (Side carries the direction) with its Unit (lots, t, or the base currency);
# Mark and Prev close are `value_book`'s own mark on the as-of and on the previous business
# day's close (`add_prev_close`), their source and date on hover; P&L (local) is in the Ccy
# beside it (`add_instrument_fields`), P&L (USD) as the engine converted it. Nothing here is
# computed: every figure is a `value_book` column.
# The Greeks of an option on a future (UI redesign wave 1, 2026-09-28): the official marks per
# option lot, read as they are (`add_option_greeks`): DELTA in futures lots per lot, GAMMA /
# THETA / VEGA in the contract's price unit per lot; blank on every other product. The Options
# sub-tab scales them to the position through the engine; nothing is scaled or priced here.
GREEK_COLS = ("delta", "gamma", "theta", "vega")
_GREEK_MARK_TYPES = {"delta": "DELTA", "gamma": "GAMMA", "theta": "THETA", "vega": "VEGA"}
GREEK_TIP = ("An option on a future's official Greek per option lot, as the mark on file (the app's option pricer): "
             "Delta in futures lots per lot; Gamma, Theta, Vega in the contract's price unit per lot. Blank on "
             "every other product; the Options sub-tab shows them scaled to the position.")
_DISPLAY_COLUMNS = [
    "instrument_id", "commodity", "exchange", "product", "strategy", "trade_type", "trade_date", "side", "quantity", "qty_unit",
    "fill", "mark", "prev_close", "pnl_local", "pnl_ccy", "pnl_usd", "delta", "gamma", "theta", "vega",
    "status", "settle_date", "theme", "trade_id",
]
_COLUMN_LABELS = {
    "instrument_id": "Instrument", "commodity": "Commodity / pair", "exchange": "Exchange",
    "product": "Product", "strategy": "Strategy", "trade_type": "Type",
    "trade_date": "Trade date", "side": "Side", "quantity": "Quantity",
    "qty_unit": "Unit", "fill": "Fill", "mark": "Mark", "prev_close": "Prev close",
    "pnl_local": "P&L (local)", "pnl_ccy": "Ccy", "pnl_usd": "P&L (USD)",
    "delta": "Delta /lot", "gamma": "Gamma /lot", "theta": "Theta /lot", "vega": "Vega /lot", "status": "Status",
    "settle_date": "Expiry / value date", "theme": "Bundle", "trade_id": "Trade id",
}
# Text columns of the trade tables that read left-aligned (names, not figures).
_LEFT_COLS = ("instrument_id", "commodity", "exchange", "product", "sector", "qty_unit", "pnl_ccy", "status",
              "theme", "strategy", "trade_type")
# Columns that keep their raw (pre-display-formatting) value for the row-click detail
# panel and for the visible-rows -> headline callback.
_PASSTHROUGH_COLS = ("reason",)


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _fmt_rate(value) -> str:
    if value is None or value != value:
        return ""
    return f"{float(value):,.6f}"


def _fmt_amount(value) -> str:
    """Unsigned, thousands-separated (direction is already carried by `side`)."""
    if value is None or value != value:
        return ""
    return f"{abs(round(float(value))):,}"


def _fmt_status(value) -> str:
    return {"OPEN": "Open", "SETTLED": "Settled", "CLOSED": "Closed out"}.get(value, value or "")


# Plain product names (Screens redesign Phase A, 2026-09-25): never a product code on screen.
_PRODUCT_LABELS = {"FX_SPOT": "FX spot", "FX_FWD": "FX forward", "FX_SWAP": "FX swap", "FUTURE": "Future",
                   "FX_OPTION": "FX option", "CMDTY_OPTION": "Option on future", "LME_FWD": "LME forward",
                   "EQ_OPTION": "Listed option"}


def _fmt_product(value) -> str:
    return _PRODUCT_LABELS.get(value, value or "")


def _fmt_trade_type(value) -> str:
    """The broker's trade type in words ('cross exchange'); '' for none."""
    return trade_type_words(value)


def _sorted_scope_df(df: pd.DataFrame) -> pd.DataFrame:
    """The order the table opens in (settle date, then pair); a header click re-ranks it
    (ui.tabs.ranking). No-op on an empty frame."""
    if df.empty or "settle_date" not in df.columns:
        return df
    return df.sort_values(["settle_date", "instrument_id"], kind="stable")


_COLUMN_FORMATS = {   # the numeric columns of the trade table (ui.tabs.ranking); every other column is text
    "quantity": rk.count(),                 # unsigned: direction is carried by `side`
    "notional_usd": rk.amount(),
    "pnl_local": rk.amount(nully=MISSING),     # the Futures sub-tab: P&L in the contract's own currency
    "pnl_usd": rk.amount(nully=MISSING),
    "t1_rate": rk.rate(6, nully=MISSING),
    "delta": rk.rate(4, nully="", trim=True),     # an option on a future's Greeks per lot; blank elsewhere
    "gamma": rk.rate(4, nully="", trim=True),
    "theta": rk.rate(4, nully="", trim=True),
    "vega": rk.rate(4, nully="", trim=True),
}


# Fill, mark and previous close are text at tick precision (`formatting.price_text`, 2026-09-28),
# so the table never prints eight decimals; the unit is the row's `price_unit`.
_PRICE_COLS = ("fill", "mark", "prev_close")

SETTLED_MARK_REASON = "settled: this trade's P&L is frozen; it is no longer marked"


def _text(value) -> str:
    """A text cell of a value_book row: '' for None / NaN."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value)


def _mark_tip(mark, reason, settled: bool, source, note) -> str:
    """The Mark cell's hover: the mark's source ("Bloomberg history", or "estimated from ..."
    naming the near marks it was estimated from) and the row's note (a fill from an earlier
    close says so); with no mark, why (the row's own reason, or that a settled trade is
    frozen). Every part is in plain words (`plain_words`: no mark type or source code). The
    row's `mark_date` is the date the mark is keyed on (a future's expiry, a leg's value
    date), not the close it was taken on, so it is not offered as "dated" here."""
    note = plain_words(_text(note))
    if mark is None:
        why = plain_words(_text(reason)) or (SETTLED_MARK_REASON if settled else "no mark on this row")
        return f"{why}. {note}" if note and note not in why else why
    return "; ".join(t for t in (plain_words(_text(source)), note) if t) or "the mark the valuation used"


def _format_rows(df: pd.DataFrame, display_columns: list, column_labels: dict):
    """Records for the trade table from the (already scope/dropdown-filtered,
    pricing-enriched) value_book frame: numbers as numbers, which the table formats
    (`_COLUMN_FORMATS`: Amount unsigned with commas, rates to 6 dp, P&L with a negative in
    parentheses and "n/a" when unpriced, its reason as the cell's tooltip), dates left as
    ISO strings. Returns `(data_records, tooltip_data, style_data_conditional)` -- split
    out from `detail_table` (2026-09-15) so the filter-dropdown callback can refresh a
    table's `data`/`tooltip_data` props without rebuilding the whole DataTable."""
    cols = [c for c in display_columns if c in df.columns]
    formatted = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    reasons = df["reason"] if "reason" in df.columns else pd.Series([""] * len(df))
    statuses = df["status"] if "status" in df.columns else pd.Series([""] * len(df))

    def _column(name: str) -> list:
        return df[name].tolist() if name in df.columns else [""] * len(df)

    mark_sources, notes = _column("mark_source"), _column("note")
    prev_tips = _column("prev_close_tip")
    type_sources, type_notes = _column("type_source"), _column("type_note")
    long_names = _column("commodity_long")
    price_units, flags = _column("price_unit"), _column("flag")
    fills = _column("fill")
    for col in cols:
        if col == "status":
            formatted[col] = formatted[col].map(_fmt_status)
        elif col == "product":
            formatted[col] = formatted[col].map(_fmt_product)
        elif col == "trade_type":
            formatted[col] = formatted[col].map(_fmt_trade_type)
    data_records = formatted.to_dict("records")
    tooltip_data = []
    # trade_id/reason travel with the row (not all displayed) so the
    # derived_virtual_data callback and the row-click detail panel can use them.
    for i, rec in enumerate(data_records):
        for col in _COLUMN_FORMATS:
            if col in rec:
                value = rk.value(rec[col])
                rec[col] = abs(round(value)) if (col == "quantity" and isinstance(value, float)) else value
        if "trade_id" in df.columns:
            rec.setdefault("trade_id", df["trade_id"].iloc[i])
        reason = plain_words(reasons.iloc[i] if i < len(reasons) else "")
        tip = {}
        if reason and rec.get("pnl_usd") is None:
            tip["pnl_usd"] = {"value": reason, "type": "text"}
        if "pnl_local" in rec and rec["pnl_local"] is None:
            # Unpriced: the row's own reason. Priced but settled: the frozen row holds USD only.
            settled = (statuses.iloc[i] if i < len(statuses) else "") == "SETTLED"
            why = reason or (SETTLED_LOCAL_REASON if settled else "no local-currency P&L on this row")
            tip["pnl_local"] = {"value": why, "type": "text"}
        if "mark" in rec:
            settled = (statuses.iloc[i] if i < len(statuses) else "") == "SETTLED"
            # The price columns are text (`_PRICE_COLS`), so the raw cell is still a float or NaN here:
            # `rk.value` reads NaN as None, which is what selects the "why there is no mark" hover.
            tip["mark"] = {"value": _mark_tip(rk.value(rec["mark"]), reason, settled, mark_sources[i], notes[i]),
                           "type": "text"}
        if "prev_close" in rec and prev_tips[i]:
            tip["prev_close"] = {"value": plain_words(str(prev_tips[i])), "type": "text"}
        if "commodity" in rec and long_names[i] and str(long_names[i]) != str(rec["commodity"]):
            tip["commodity"] = {"value": f"{long_names[i]} ({rec.get('exchange') or ''})".replace(" ()", ""), "type": "text"}
        if "trade_type" in rec:
            src, note = str(type_sources[i] or ""), str(type_notes[i] or "")
            words = (f"{src}: " if src and src != "label" else "") + (note or ("the broker's label" if rec["trade_type"] else "no type: outright"))
            tip["trade_type"] = {"value": words, "type": "text"}
        # Prices at tick precision (2026-09-28): text cells, never eight decimals.
        unit = str(price_units[i] or "") if i < len(price_units) else ""
        fill = rk.value(fills[i]) if i < len(fills) else None
        for col in _PRICE_COLS:
            if col in rec:
                v = rk.value(rec[col])
                rec[col] = price_text(v, unit, fill) if isinstance(v, (int, float)) else MISSING
        if i < len(flags) and flags[i] and "instrument_id" in rec:
            rec["instrument_id"] = f"{rec['instrument_id']} \u00b7 {flags[i]}"
            tip["instrument_id"] = {"value": NO_STRIKE_HOVER, "type": "text"}
        tooltip_data.append(tip)
    pnl_cols = [c for c in ("pnl_local", "pnl_usd") if c in cols] or ["pnl_usd"]
    style_data_conditional = rk.sign_styles(pnl_cols, bold=True,
                                            nil={"color": "var(--muted)", "fontStyle": "italic"})
    return data_records, tooltip_data, style_data_conditional


def detail_table(df: pd.DataFrame, table_id: str = DATATABLE_ID,
                  display_columns: Optional[list] = None,
                  column_labels: Optional[dict] = None, scope: str = "") -> dash_table.DataTable:
    """Build the trade table. `display_columns`/`column_labels` default to the Total book's
    commodity-terms layout (`_DISPLAY_COLUMNS`; a column the frame lacks is left out);
    `scope_header_tips` go on its headers. Filtering is the dropdown bar built by
    `_filter_bar` (see module docstring); sorting is native (ui.tabs.ranking): the table
    opens in `_sorted_scope_df`'s order and any header click re-ranks it."""
    display_columns = display_columns if display_columns is not None else _DISPLAY_COLUMNS
    column_labels = column_labels if column_labels is not None else _COLUMN_LABELS
    cols = [c for c in display_columns if c in df.columns]
    data_records, tooltip_data, style_data_conditional = _table_rows(scope, df, display_columns, column_labels)
    return dash_table.DataTable(
        id=table_id,
        columns=[rk.numeric(column_labels.get(c, c.replace("_", " ").title()), c, _COLUMN_FORMATS[c])
                 if c in _COLUMN_FORMATS else rk.text(column_labels.get(c, c.replace("_", " ").title()), c)
                 for c in cols],
        data=data_records,
        tooltip_data=tooltip_data,
        tooltip_header={c: t for c, t in scope_header_tips(scope).items() if c in cols},
        **rk.sortable(table_id),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in cols if c in _LEFT_COLS],
        style_header={"fontWeight": "bold"},
        style_data_conditional=style_data_conditional,
        page_size=25,
        page_action="native",
        row_selectable=False,
        cell_selectable=True,
    )


def _filter_options(df: pd.DataFrame, col: str, column_labels: dict) -> list:
    """Sorted distinct raw values of `col` across the WHOLE scope (before any dropdown
    selection), as dropdown options. Formatted labels for the columns that get one
    (Status/Product) so the dropdown reads the same words as the table."""
    if df.empty or col not in df.columns:
        return []
    label_fn = {"status": _fmt_status, "product": _fmt_product, "trade_type": _fmt_trade_type}.get(col, lambda v: v)
    values = sorted({v for v in df[col].tolist() if v not in (None, "")})
    return [{"label": label_fn(v) or "(blank)", "value": v} for v in values]


def _filter_bar(df: pd.DataFrame, table_id: str, display_columns: list, column_labels: dict) -> html.Div:
    """One multi-select dropdown per categorical column present in this scope, e.g.
    click Pair, pick USDJPY, see only USDJPY rows -- and the P&L strip above narrows to
    match (it listens on the same table's `derived_virtual_data`). Replaces the native
    filter row (module docstring). Built from the FULL scope df so every dropdown lists
    every value that scope ever has, not just what a prior selection left visible."""
    scope = "total" if table_id.endswith("-total") else ""
    cols = [c for c in filter_cols_for(scope, display_columns) if c in df.columns]
    if not cols:
        return html.Div()
    children = [html.Div(className="blotter-filter", children=[
        html.Label(column_labels.get(c, c.replace("_", " ").title())),
        dcc.Dropdown(id=f"{table_id}-filter-{c}", options=_filter_options(df, c, column_labels),
                     value=[], multi=True, placeholder="All", className="blotter-filter-dropdown"),
    ]) for c in cols]
    if scope == "total":
        children.append(html.Div(className="blotter-filter", children=[
            html.Label("Search"),
            dcc.Input(id=f"{table_id}-search", type="text", value="", debounce=True,
                      placeholder="instrument or trade id", className="blotter-filter-search"),
        ]))
    children.append(html.Button("Clear filters", id=f"{table_id}-filter-clear", n_clicks=0,
                                className="btn btn--ghost"))
    if scope == "total":
        children.append(html.Button("Download CSV", id=f"{table_id}-csv", n_clicks=0, className="btn btn--ghost",
                                    title="The rows shown, every column, at full figures"))
        children.append(dcc.Download(id=f"{table_id}-download"))
    return html.Div(className="blotter-filter-bar", children=children)


_EXCL_RE = re.compile(r"excludes (\d+)")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _excluded_marker(summary: str) -> str:
    """"excludes 3 of 12 trades unpriced" -> "excl. 3" (the sentence goes on hover)."""
    found = _EXCL_RE.search(summary or "")
    return f"excl. {found.group(1)}" if found else "excl."


def _ref_marker(ref_note: str) -> str:
    """"from the 2026-06-19 close ..." -> "from 06-19"; "stepped back" when no date is named."""
    found = _DATE_RE.search(ref_note or "")
    return f"from {found.group(2)}-{found.group(3)}" if found else "stepped back"


def render_headline_strip(headline: dict, hidden: tuple = (), caption: str = "") -> html.Div:
    """A sub-tab's compact P&L strip (Screens redesign Phase A, 2026-09-25): one small card
    per figure of `HEADLINE_ORDER`, the value in k / m (`short_money`), bold green / red by
    sign, its full figure and reference date on hover; "n/a" muted with the reason on hover
    when unavailable. What used to be caption sentences under a value are short markers with
    the sentence on hover: "excl. N" (the header's display rule: a real sum over the PRICED
    rows, `excluded_summary` / `excluded_detail`) and "from MM-DD" (measured from an earlier
    close than the period's own, `ref_note` / `ref_note_detail`). The Total book has no strip
    (the header is the total book); Options does.

    `hidden` / `caption` (used by the Options strip only, `options_strip`): the keys to leave
    out, and the sentence naming them, shown as one marker ("3 waiting") after the cards.
    Never a 0 in place of "n/a"."""
    cards = []
    for key in HEADLINE_ORDER:
        if key in hidden:
            continue
        entry = headline.get(key, {})
        available = entry.get("available")
        value = entry.get("value")
        ref_date = entry.get("ref_date", "")
        if key == "trades":
            value_div = html.Div(f"{int(value)}" if value == value and value is not None else MISSING,
                                 className="card-value", title=ref_date)
        elif not available:
            reason = entry.get("reason", "")
            value_div = html.Div(MISSING, className="card-value card-value--muted",
                                 title=cap(reason) or "Unavailable")
        else:
            colour = "var(--pos)" if value >= 0 else "var(--neg)"
            value_div = html.Div(short_money(value), className="card-value", style={"color": colour},
                                 title=f"USD, {ref_date}" if ref_date else "USD")
        markers = []
        if available:
            ref_note = entry.get("ref_note")
            if ref_note:
                detail = entry.get("ref_note_detail", "")
                markers.append(marker(_ref_marker(ref_note), f"{ref_note}. {detail}" if detail else ref_note))
            summary = entry.get("excluded_summary")
            if summary:
                detail = entry.get("excluded_detail", "")
                markers.append(marker(_excluded_marker(summary), f"{summary}. {detail}" if detail else summary))
        card_children = [html.Div(HEADLINE_TITLES[key], className="card-label", title=ref_date), value_div]
        if markers:
            card_children.append(html.Div([x for m in markers for x in (m, " ")][:-1], className="card-note"))
        cards.append(html.Div(className="card", children=card_children))
    children = [html.Div(cards, className="cards")]
    if caption:
        children.append(marker(f"{len(hidden)} waiting" if hidden else "note", caption))
    return html.Div(children)


# The strip's cards that compare with, or stand on, an EARLIER close, and the earlier
# dates each one needs (keys of `engine.pnl.ledger.period_reference_dates`). `ltd`,
# `trades` and `trading` are about the as-of itself and are never hidden.
_EARLIER_CLOSE_CARDS = {
    "daily": ("daily",), "ltd1_daily": ("daily", "previous_day"), "d5": ("d5",), "mtd": ("mtd",),
    "ytd": ("ytd",), "trading_t1": ("daily",), "ltd1": ("daily",), "ltd2": ("previous_day",),
}
_NO_PREMIUM_PREFIX = "no PREMIUM mark"   # `engine.pnl.valuation._open_option_row`'s own reason text


def options_hidden_cards(conn: sqlite3.Connection, as_of: str, trade_ids, headline: dict) -> tuple:
    """The Options strip's cards to leave out: the ones that are unavailable ONLY because
    the earlier close they need has no option marks.

    Why (user, 2026-09-18): option PREMIUM marks are written by the options pricer on the
    days the app runs with Bloomberg -- the historical backfill writes spots, forwards and
    futures, never option premiums -- so on the first days Daily / Previous day / 5d / MTD /
    LTD-1 / LTD-2 are all "n/a", and a row of six "n/a" reads as "the headlines don't
    work" although today's figures are right beside them.

    A card is hidden only when ALL of this holds, so nothing else is ever swept under it:
      - it is one of `_EARLIER_CLOSE_CARDS` and it is unavailable (a card with a value,
        even a partial one with an "excludes" caption, always shows);
      - a period DIFFERENCE from today (Daily, 5d, MTD, YTD) is hidden only while today's
        LTD itself has a value -- when nothing prices TODAY, that is today's problem and
        every card keeps its "n/a" and its reason;
      - on each earlier date the card needs, every unpriced option is unpriced for want of
        a PREMIUM mark (`value_book`'s own reason). A stored value that is not a number, a
        missing conversion spot, an expired option that cannot be frozen: any other reason
        keeps the card on screen, "n/a", with that reason as its tooltip.
    Never a 0 in place of "n/a": a hidden card is absent and named in the caption."""
    from engine.pnl.ledger import period_reference_dates

    trade_ids = set(trade_ids)
    if not trade_ids:
        return ()
    refs = period_reference_dates(as_of)
    today_priced = bool(headline.get("ltd", {}).get("available"))

    def unpriced_reasons(day: str) -> list:
        df, _, _ = priced_value_book(conn, day)   # memoised: the same frames the strip was built from
        if df.empty:
            return []
        rows = df[df["trade_id"].isin(trade_ids)]
        return [str(r) for r in rows["reason"].tolist() if r]

    hidden = []
    for key in HEADLINE_ORDER:
        needs = _EARLIER_CLOSE_CARDS.get(key)
        if needs is None or headline.get(key, {}).get("available"):
            continue
        if key in ("daily", "d5", "mtd", "ytd") and not today_priced:
            continue
        reasons = [r for name in needs for r in unpriced_reasons(refs[name])]
        if reasons and all(r.startswith(_NO_PREMIUM_PREFIX) for r in reasons):
            hidden.append(key)
    return tuple(hidden)


def options_hidden_caption(hidden: tuple) -> str:
    """"Daily P&L, 5d and MTD appear once option marks exist for the earlier close; they
    are written each day the app runs with Bloomberg." -- "" when nothing is hidden."""
    names = [HEADLINE_TITLES[key] for key in hidden]
    if not names:
        return ""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    verb = "appears" if len(names) == 1 else "appear"
    return (f"{listed} {verb} once option marks exist for the earlier close; "
            f"they are written each day the app runs with Bloomberg.")


def options_strip(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The P&L strip above the Options table: the generic row scoped to the option trades,
    minus the cards `options_hidden_cards` names, plus one caption line in their place.
    Shared by the sub-tab's build (`_scope_layout_body`) and its in-place refresh on new
    marks (`_refresh_options_strip`), so the two can never disagree."""
    df = scope_df(conn, "options", as_of)
    trade_ids = df["trade_id"].tolist() if not df.empty else []
    headline = row_scoped_headline(conn, as_of, trade_ids)
    hidden = options_hidden_cards(conn, as_of, trade_ids, headline)
    return render_headline_strip(headline, hidden=hidden, caption=options_hidden_caption(hidden))


def scope_strip(scope: str, conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole-scope P&L strip of a sub-tab whose table belongs to another module
    (`_SELF_REFRESHING_SCOPES`): Options gets `options_strip`; any other scope the full
    generic row over its trades, every card shown. One builder for the sub-tab's first
    render and for its in-place refresh on new marks."""
    if scope == "options":
        return options_strip(conn, as_of)
    df = scope_df(conn, scope, as_of)
    trade_ids = df["trade_id"].tolist() if not df.empty else []
    return render_headline_strip(row_scoped_headline(conn, as_of, trade_ids))


def asset_class_pnl_rows(conn: sqlite3.Connection, as_of: str, df: pd.DataFrame) -> list:
    """One dict per asset class present in `df` (ASSET_CLASS_ORDER) plus 'Total', with
    `trades` (count) and the seven period figures from
    `ui.tabs.blotter_pricing.row_scoped_period_pnl` over that class's trade ids --
    each `{value, available, reason, ref_date}` as the strip uses. Missing marks make
    that class's figure unavailable with the reason, never zero. `unpriced` (Phase 3): the
    class's trades `df` shows unpriced on `as_of`, as "<trade id>: <value_book's own reason>"
    (a leftover IRS's "rate swaps left the app ..."), which the table puts on hover."""
    from ui.tabs.blotter_pricing import row_scoped_period_pnl
    if df.empty:
        return []
    classes = df["product"].map(ASSET_CLASS_OF).fillna("Other")

    def unpriced(mask) -> list:
        if "pnl_usd" not in df.columns:
            return []
        rows_ = df[mask & pd.to_numeric(df["pnl_usd"], errors="coerce").isna()]
        reasons = rows_["reason"] if "reason" in rows_.columns else pd.Series([""] * len(rows_), index=rows_.index)
        return [f"{t}: {reasons.loc[i] or 'no P&L'}" for i, t in zip(rows_.index, rows_["trade_id"])]

    rows = []
    for cls in [c for c in ASSET_CLASS_ORDER if c in set(classes)] + (["Other"] if "Other" in set(classes) else []):
        ids = df.loc[classes == cls, "trade_id"].tolist()
        rows.append({"asset_class": cls, "trades": len(ids), **row_scoped_period_pnl(conn, as_of, ids),
                     "unpriced": unpriced(classes == cls)})
    all_ids = df["trade_id"].tolist()
    rows.append({"asset_class": "Total", "trades": len(all_ids), **row_scoped_period_pnl(conn, as_of, all_ids),
                 "unpriced": unpriced(classes == classes)})
    return rows


_UNPRICED_EXAMPLES = 3   # trades named on an asset-class row's hover; the rest counted


def _unpriced_note(row: dict, as_of: str) -> str:
    """"Unpriced on <as_of>: S1: rate swaps left the app ...; (+2 more)" -- '' when none."""
    items = row.get("unpriced") or []
    if not items:
        return ""
    more = f"; (+{len(items) - _UNPRICED_EXAMPLES} more)" if len(items) > _UNPRICED_EXAMPLES else ""
    return f"Unpriced on {as_of}: " + "; ".join(items[:_UNPRICED_EXAMPLES]) + more


_ASSET_PERIODS = ("ltd", "daily", "previous_day", "d5", "mtd", "ytd", "trading")
_ASSET_LABELS = {"asset_class": "Asset class", "trades": "Trades", "ltd": "LTD", "daily": "Daily",
                 "previous_day": "Previous day", "d5": "5d", "mtd": "MTD", "ytd": "YTD", "trading": "Trading"}


def asset_class_pnl_table(conn: sqlite3.Connection, as_of: str, df: pd.DataFrame) -> html.Div:
    """The Total book's P&L-by-asset-class block (module docstring). A class figure
    computed over a mix of priced/unpriced rows (2026-09-17 partial-pricing follow-up,
    `row_scoped_period_pnl`) still shows its real value with the `excluded_summary`/
    `excluded_detail` text as the cell's tooltip -- only a class with NOTHING priced
    at all shows "n/a". Ranked (ui.tabs.ranking): the Total row is the pinned footer."""
    rows = asset_class_pnl_rows(conn, as_of, df)
    records, tooltips = [], []
    for r in rows:
        rec = {"asset_class": r["asset_class"], "trades": int(r["trades"])}
        tip = {}
        why = _unpriced_note(r, as_of)   # each unpriced trade's own reason, not only its short tag
        for key in _ASSET_PERIODS:
            entry = r.get(key, {})
            if entry.get("available"):
                rec[key] = rk.value(entry["value"])
                summary = entry.get("excluded_summary")
                if summary:
                    detail = entry.get("excluded_detail", "")
                    text = f"{summary}. {detail}" if detail else summary
                    tip[key] = {"value": f"{text}. {why}" if why else text, "type": "text"}
                ref_note = entry.get("ref_note")
                if ref_note:  # measured from an earlier close (engine.pnl.reference)
                    rest = tip.get(key, {}).get("value", "")
                    tip[key] = {"value": f"{ref_note}. {rest}" if rest else ref_note, "type": "text"}
            else:
                rec[key] = None
                reason = entry.get("reason", "") or "unavailable"
                tip[key] = {"value": f"{reason}. {why}" if why else reason, "type": "text"}
        records.append(rec)
        tooltips.append(tip)
    for rec, tip in zip(records, tooltips):   # the full figure on hover of every k / m cell
        for key in _ASSET_PERIODS:
            if rec.get(key) is not None:
                full = f"{format_cell(rec[key])} USD"
                rest = tip.get(key, {}).get("value", "")
                tip[key] = {"value": f"{full}. {rest}" if rest else full, "type": "text"}
    # Money in k / m (a summary table, Screens redesign Phase A); the cells stay numbers.
    records = rk.whole_units(records, _ASSET_PERIODS)
    is_total = [rec["asset_class"] == "Total" for rec in records]
    body = [rec for rec, t in zip(records, is_total) if not t]
    body_tips = [tip for tip, t in zip(tooltips, is_total) if not t]
    footer = [rec for rec, t in zip(records, is_total) if t]
    footer_tips = [tip for tip, t in zip(tooltips, is_total) if t]
    table = dash_table.DataTable(
        id=ASSET_TABLE_ID,
        columns=[rk.text(_ASSET_LABELS["asset_class"], "asset_class"), rk.numeric(_ASSET_LABELS["trades"], "trades", rk.count())]
                + [rk.numeric(_ASSET_LABELS[c], c, rk.amount_short(nully=MISSING)) for c in _ASSET_PERIODS],
        data=body, tooltip_data=body_tips,
        **rk.sortable(ASSET_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_cell_conditional=[{"if": {"column_id": "asset_class"}, "textAlign": "left"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(_ASSET_PERIODS, bold=True,
                                              nil={"color": "var(--muted)", "fontStyle": "italic"}),
    )
    total_style = [{"if": {"filter_query": "{asset_class} = 'Total'"}, "fontWeight": "700",
                    "borderTop": "2px solid var(--muted)"}]
    return html.Div(className="section section--secondary", children=[
        about("P&L by asset class", ASSET_CLASS_ABOUT),
        rk.with_footer(table, footer, footer_style=total_style, footer_tooltips=footer_tips)])


ASSET_CLASS_ABOUT = ("Each asset class's LTD and period P&L in USD, over its trades, by the header's rule: "
                     "priced trades only, a period difference over the trades priced at both ends. The classes "
                     "sum to the Total, which is the header's figure for the whole book. A figure that leaves "
                     "trades out, or is n/a, says why on hover; the full figure is on hover too.")


def _pos_num(value, _digits: int = 0):
    """A Positions cell: the number itself, None (printed "n/a") when there is none; the
    table formats it (`_digits` is the Detail sentence's concern, `_pos_text`)."""
    return rk.value(value)


def _quoted(value) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return f"{value:,.4f}" if abs(value) < 100 else f"{value:,.2f}"


def _pair_label(c: dict) -> str:
    """The plain pair a currency row's rate is quoted on ('USDJPY', 'EURUSD', 'XAUUSD').
    `book_positions` labels a row with its rate's own label, which for a currency the old
    NDF rule priced was a 1M NDF ticker ('KWN+1M'); NDFs left the app on 2026-09-24, so a
    label that is not a pair is shown as the plain USD pair of the currency instead (every
    NDF currency is quoted USDXXX). No label (no rate on file) stays blank: the row's USD
    delta is then n/a with its reason."""
    label = str(c.get("label") or "")
    if not label or (len(label) == 6 and label.isalpha()):
        return label
    return f"USD{c['ccy']}"


def positions_rows(conn: sqlite3.Connection, as_of: str, pos: Optional[dict] = None) -> tuple:
    """(records, tooltips) of the Total book's FX positions table (user, 2026-09-22: "I want
    to see the delta by currency ... this is the key table of the blotter"): the figures of
    `engine.ladder.positions.book_positions`. First one row per currency with delta (the
    Ladder's risk table: rate as quoted on its plain pair, local delta, USD delta; a metal
    row says it is not in the FX net), then the FX net and gross, then the FX options' delta
    by pair (already inside the currency rows). The equity-index line and the rates DV01
    left on 2026-09-24 with the macro trader's products. The commodity futures are the
    Positions block's own table above this one (`commodity_positions_rows`, Phase 3).
    Columns: Position, Rate, Delta (local), Delta (USD), Detail. A figure that could not be
    computed reads "n/a" with its reason in the cell's tooltip. Cells are numbers
    (ui.tabs.ranking): a missing one is None. `pos`: `book_positions`' result when the
    caller already has it (`positions_table` reads it once for both tables)."""
    if pos is None:
        pos = _book_positions(conn, as_of)
    records, tips = [], []

    def add(label, units, usd, detail="", reason="", indent=False, rate="", kind="", said=""):
        """`detail` is the short marker shown; `said` its sentence, on hover (Phase A)."""
        rec = {"position": ("    " if indent else "") + label, "rate": rate, "units": units, "usd": usd,
               "detail": detail, "kind": kind}
        tip = {}
        if reason:
            for col in ("units", "usd"):
                if rec[col] is None:
                    tip[col] = {"value": reason, "type": "text"}
        if detail and said:
            tip["detail"] = {"value": said, "type": "text"}
        records.append(rec)
        tips.append(tip)

    fx = pos["fx"]
    for c in fx.get("by_ccy", []):
        detail = "metal, not in the FX net" if c["metal"] else ("" if c["ccy"] != "USD" else "USD legs")
        said = ("A metal's delta is reported apart: it is not in the FX net or gross." if c["metal"]
                else "The USD delta of the FX trades' USD legs." if c["ccy"] == "USD"
                else "")
        add(f"{c['ccy']}", _pos_num(c["local_delta"]), _pos_num(c["usd_delta"]), detail, c["reason"],
            rate=(f"{_pair_label(c)} {_quoted(c['quoted'])}".strip() if c["ccy"] != "USD" else ""), kind="ccy",
            said=said)
    if not fx.get("by_ccy") and fx.get("reason"):
        add("Delta by currency", "", None, "", fx["reason"], kind="ccy")
    add("FX net USD delta (+ = long USD)", "", _pos_num(fx["net_usd"]), "incl. options", fx["reason"], kind="total",
        said="The book's FX net USD delta, + = long USD, FX options' delta included (the FX & cash tab's Net USD).")
    add("FX gross USD delta", "", _pos_num(fx["gross_usd"]), "Σ |pair|", fx["reason"], kind="total",
        said="The sum of |per-pair USD delta|.")

    opt = pos["fx_options"]
    for pair, usd in sorted(opt["by_pair"].items()):
        add(f"{pair} options delta", "", _pos_num(usd), "in rows above", kind="option",
            said="Already inside the currency rows above.")
    missing = len(opt["missing"])
    add("FX options delta (USD)", "", _pos_num(opt["usd_delta"]),
        f"{opt['options']} open" + (f", excl. {missing}" if missing else ""),
        opt["reason"] or (opt["missing"][0] if opt["missing"] else ""), kind="total",
        said=f"{opt['options']} open option(s), part of the FX net above"
             + (f"; {missing} not converted: " + "; ".join(opt["missing"]) if missing else "."))
    return records, tips


def _sector_label(sector) -> str:
    """'Energy' for contract-master's 'energy'; `UNCLASSIFIED_SECTOR` for none."""
    return str(sector).capitalize() if sector else UNCLASSIFIED_SECTOR


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# The commodity block when `book_positions` did not return one at all.
_NO_COMMODITY_BLOCK = {"available": False, "note": "", "sectors": [], "net_usd": None, "gross_usd": None,
                       "missing": [], "currency_exposure": {},
                       "reason": "commodity positions were not returned by the positions engine"}
COMMODITY_POSITIONS_TABLE_ID = "blotter-commodity-positions-table"
COMMODITY_CCY_TABLE_ID = "blotter-commodity-ccy-table"
COMMODITY_TOTAL_LABEL = "Commodities total"
COMMODITY_TOTAL_DETAIL = "not in FX net"
COMMODITY_TOTAL_ABOUT = ("The commodity positions summed in USD at the day's official prices and spot. They are "
                         "positions, not currency delta, so they are not in the FX net below.")


def commodity_positions_rows(block: Optional[dict]) -> dict:
    """The Positions block's Commodities section (commodity conversion Phase 3), from
    `book_positions(...)["commodities"]` as the engine gives it, nothing recomputed:

    {'records', 'tooltips' (the table's lines: one per sector, then one per commodity under
    it), 'footer', 'footer_tooltips' (the Commodities total), 'ccy_records', 'ccy_tooltips'
    (the P&L held in foreign currency, one line per currency), 'caption' (the block's own
    `reason`, "excludes N of M ..." or why there is nothing to show; '' when every figure is
    there)}.

    A sector line carries its net and gross USD (lots and units are per commodity: a sector
    mixes units, so its lot cells are left empty, and say so on hover); a commodity line
    carries name, exchange, net and gross lots, net units with the unit, net and gross USD.
    A None (curve-positions' n/a) is None here, printed "n/a" with the line's reason on
    hover, never 0. A sector summed over only some of its commodities says so in its Detail
    ("excludes N of M commodities with no USD figure", the engine's reason on hover)."""
    block = block if block is not None else _NO_COMMODITY_BLOCK
    records, tips = [], []

    def tip_for(rec: dict, reason: str, cols=("net_lots", "gross_lots", "net_units", "net_usd", "gross_usd")) -> dict:
        return {c: {"value": reason or "not computed", "type": "text"} for c in cols if rec.get(c) is None}

    for s in block.get("sectors") or []:
        comms = s.get("commodities") or []
        missing = s.get("missing") or []
        sector = _sector_label(s.get("sector"))
        # The Detail cell is a short marker, its sentence on hover (Screens redesign Phase A).
        said = _plural(len(comms), "commodity", "commodities")
        if missing:
            said += "; " + (s.get("reason") or f"excludes {len(missing)} of {len(comms)} with no USD figure")
        rec = {"kind": "sector", "position": sector, "sector": sector, "exchange": "", "net_lots": "",
               "gross_lots": "", "net_units": "", "unit": "", "net_usd": _pos_num(s.get("net_usd")),
               "gross_usd": _pos_num(s.get("gross_usd")), "detail": f"excl. {len(missing)}" if missing else ""}
        tip = tip_for(rec, s.get("reason", ""), ("net_usd", "gross_usd"))
        if missing and s.get("reason"):
            for col in ("net_usd", "gross_usd"):
                tip.setdefault(col, {"value": s["reason"], "type": "text"})
        tip["net_lots"] = {"value": "lots and units are per commodity: a sector mixes units", "type": "text"}
        tip["detail"] = {"value": said, "type": "text"}
        tip["position"] = {"value": said, "type": "text"}
        records.append(rec)
        tips.append(tip)
        for c in comms:
            currency = c.get("currency") or ""
            foreign = currency not in ("", "USD")
            rec = {"kind": "commodity", "position": "    " + str(c.get("name") or c.get("root_id") or ""),
                   "sector": sector, "exchange": c.get("exchange") or "", "net_lots": _pos_num(c.get("net_lots")),
                   "gross_lots": _pos_num(c.get("gross_lots")), "net_units": _pos_num(c.get("net_units")),
                   "unit": c.get("unit") or "", "net_usd": _pos_num(c.get("net_usd")),
                   "gross_usd": _pos_num(c.get("gross_usd")),
                   "detail": currency if foreign else ""}
            tip = tip_for(rec, c.get("reason", ""))
            if foreign:
                tip["detail"] = {"value": f"{currency} contract, in USD at the day's spot", "type": "text"}
            records.append(rec)
            tips.append(tip)

    footer, footer_tips = [], []
    if records:
        total = {"kind": "total", "position": COMMODITY_TOTAL_LABEL, "sector": "", "exchange": "", "net_lots": "",
                 "gross_lots": "", "net_units": "", "unit": "", "net_usd": _pos_num(block.get("net_usd")),
                 "gross_usd": _pos_num(block.get("gross_usd")), "detail": COMMODITY_TOTAL_DETAIL}
        total_tip = tip_for(total, block.get("reason", ""), ("net_usd", "gross_usd"))
        total_tip["detail"] = {"value": COMMODITY_TOTAL_ABOUT, "type": "text"}
        if block.get("missing") and block.get("reason"):
            for col in ("net_usd", "gross_usd"):
                total_tip.setdefault(col, {"value": block["reason"], "type": "text"})
        footer, footer_tips = [total], [total_tip]

    ccy_records, ccy_tips = [], []
    for ccy, e in sorted((block.get("currency_exposure") or {}).items()):
        rec = {"currency": ccy, "position": f"P&L held in {ccy}", "pnl_local": _pos_num(e.get("pnl_local")),
               "pnl_usd": _pos_num(e.get("pnl_usd"))}
        ccy_records.append(rec)
        ccy_tips.append({c: {"value": e.get("reason") or "not computed", "type": "text"}
                         for c in ("pnl_local", "pnl_usd") if rec[c] is None})

    caption = block.get("reason") or ""
    if not records and not caption:
        caption = block.get("note") or "no open commodity futures"
    n_missing = len(block.get("missing") or [])
    # The short visible form of `caption` beside the section title (Phase A): "excl. N" when
    # commodities were left out of the sums, else "n/a" (nothing to show, and why on hover).
    caption_marker = (f"Excl. {n_missing}" if n_missing else "None held" if not records else "Note") if caption else ""
    return {"records": records, "tooltips": tips, "footer": footer, "footer_tooltips": footer_tips,
            "ccy_records": ccy_records, "ccy_tooltips": ccy_tips, "caption": caption,
            "caption_marker": caption_marker}


_POSITIONS_CELL = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                   "padding": "4px 8px", "whiteSpace": "pre"}


def commodity_positions_section(block: Optional[dict]) -> html.Div:
    """The Commodities part of the Positions block (`commodity_positions_rows`): its
    caption, the sector / commodity table with the Commodities total pinned under it, then
    the P&L held in foreign currency. Ranked (ui.tabs.ranking): Sector is on every line, so
    a click on it keeps each sector's commodities together; the total never moves."""
    rows = commodity_positions_rows(block)
    title = about("Commodities", COMMODITIES_ABOUT, level="h5")
    if rows["caption"] and rows["records"]:
        # A reason beside the title as a short marker ("excl. 1"), its sentence on hover.
        title.children = list(title.children) + [" ", marker(rows["caption_marker"], rows["caption"])]
    children = [title]
    if rows["caption"] and not rows["records"]:
        # No table at all: the reason is the section's content, in sight.
        children.append(marker(rows["caption"], rows["caption"]))
    if rows["records"]:
        records = rk.whole_units(rows["records"], _SUMMARY_USD_COLS)
        footer = rk.whole_units(rows["footer"], _SUMMARY_USD_COLS)
        lots = rk.amount(2, nully=MISSING, trim=True)
        table = dash_table.DataTable(
            id=COMMODITY_POSITIONS_TABLE_ID,
            columns=[rk.text("Position", "position"), rk.text("Sector", "sector"), rk.text("Exchange", "exchange"),
                     rk.numeric("Net lots", "net_lots", lots), rk.numeric("Gross lots", "gross_lots", lots),
                     rk.numeric("Net units", "net_units", rk.amount(nully=MISSING)), rk.text("Unit", "unit"),
                     rk.numeric("Net USD", "net_usd", rk.amount_short(nully=MISSING)),
                     rk.numeric("Gross USD", "gross_usd", rk.amount_short(nully=MISSING)), rk.text("", "detail")],
            data=records, tooltip_data=_with_full_figures(rows["records"], rows["tooltips"], _SUMMARY_USD_COLS),
            **rk.sortable(COMMODITY_POSITIONS_TABLE_ID),
            style_table={"overflowX": "auto"},
            style_cell=_POSITIONS_CELL,
            style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                    for c in ("position", "sector", "exchange", "unit", "detail")],
            style_header={"fontWeight": "bold"},
            style_data_conditional=[{"if": {"filter_query": "{kind} = 'sector'"}, "fontWeight": "700",
                                     "backgroundColor": "#e8edf7"}]
                                   + rk.sign_styles(["net_lots", "net_units"], pos="inherit")
                                   + rk.sign_styles(["net_usd"], bold=True,
                                                    nil={"color": "var(--muted)", "fontStyle": "italic"}),
        )
        total_style = [{"if": {"filter_query": "{kind} = 'total'"}, "fontWeight": "700",
                        "borderTop": "1px solid var(--muted)"}]
        children.append(rk.with_footer(
            table, footer, footer_style=total_style,
            footer_tooltips=_with_full_figures(rows["footer"], rows["footer_tooltips"], _SUMMARY_USD_COLS)))
    if rows["ccy_records"]:
        children.append(about("P&L held in foreign currency", CCY_PNL_ABOUT, level="h5"))
        ccy_tips = _with_full_figures(rows["ccy_records"], rows["ccy_tooltips"], ("pnl_usd",))
        ccy_tips = _with_full_figures(rows["ccy_records"], ccy_tips, ("pnl_local",),
                                      unit=lambda rec: rec.get("currency") or "")
        children.append(dash_table.DataTable(
            id=COMMODITY_CCY_TABLE_ID,
            columns=[rk.text("Position", "position"), rk.text("Currency", "currency"),
                     rk.numeric("P&L (local)", "pnl_local", rk.amount_short(nully=MISSING)),
                     rk.numeric("P&L (USD)", "pnl_usd", rk.amount_short(nully=MISSING))],
            data=rk.whole_units(rows["ccy_records"], ("pnl_local", "pnl_usd")), tooltip_data=ccy_tips,
            **rk.sortable(COMMODITY_CCY_TABLE_ID),
            style_table={"overflowX": "auto"},
            style_cell=_POSITIONS_CELL,
            style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("position", "currency")],
            style_header={"fontWeight": "bold"},
            style_data_conditional=rk.sign_styles(["pnl_local", "pnl_usd"], bold=True,
                                                  nil={"color": "var(--muted)", "fontStyle": "italic"}),
        ))
    return html.Div(className="positions-commodities", children=children)


def _book_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """`engine.ladder.positions.book_positions` on the screens' shared valuation, curve and spreads."""
    from engine.ladder.positions import book_positions
    from ui.tabs.blotter_pricing import raw_value_book, shared_curve, shared_spreads
    return book_positions(conn, as_of, value_fn=raw_value_book, curve=shared_curve(conn, as_of),
                          spreads=shared_spreads(conn, as_of))


def positions_table(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The Positions block as it was on the Total book: the Commodities first (Jason's book is
    mostly commodities; `commodity_positions_section`), then the FX lines (`fx_positions_table`),
    both from one `book_positions` call. Since the UI redesign wave 2 (2026-09-28) no screen
    renders this whole block: the Exposure tab shows the commodity positions from
    curve-positions itself and takes only the FX part (`fx_positions_table`, "Currency and FX
    exposure"); kept as one function for the tests and older notes that read it."""
    pos = _book_positions(conn, as_of)
    commodities = _safe_section("Commodity positions", lambda: commodity_positions_section(pos.get("commodities")))
    return html.Div(className="section", children=[
        about("Positions", POSITIONS_ABOUT),
        commodities,
        fx_positions_table(conn, as_of, pos=pos, level="h5")])


def fx_positions_table(conn: sqlite3.Connection, as_of: str, pos: Optional[dict] = None,
                       level: str = "h4", title: str = "FX") -> html.Div:
    """The FX part of the Positions block (`positions_rows`): delta by currency at the day's
    official spot, FX options' delta included, from `book_positions` (passed in as `pos` when
    the caller has it). The table is ranked (ui.tabs.ranking): a click on Delta (USD) puts
    the largest risk first; the section totals (FX net and gross, FX options) are the pinned
    footer, in their own order, never ranked with the lines. Commodities are not in the FX
    net (CLAUDE.md "Net USD"). Rendered by the Exposure tab since 2026-09-28 (wave 2)."""
    records, tips = positions_rows(conn, as_of, pos=pos)
    body = [(r, t) for r, t in zip(records, tips) if r["kind"] != "total"]
    footer = [(r, t) for r, t in zip(records, tips) if r["kind"] == "total"]
    table = dash_table.DataTable(
        id=POSITIONS_TABLE_ID,
        columns=[rk.text("Position", "position"), rk.text("Rate", "rate"),
                 rk.numeric("Delta (local)", "units", rk.amount(2, nully=MISSING, trim=True)),
                 rk.numeric("Delta (USD)", "usd", rk.amount_short(nully=MISSING)), rk.text("", "detail")],
        data=rk.whole_units([r for r, _ in body], ("usd",)),
        tooltip_data=_with_full_figures([r for r, _ in body], [t for _, t in body], ("usd",)),
        **rk.sortable(POSITIONS_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_POSITIONS_CELL,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("position", "rate", "detail")],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["units"], pos="inherit")
                               + rk.sign_styles(["usd"], bold=True, nil={"color": "var(--muted)", "fontStyle": "italic"}),
    )
    total_style = [{"if": {"filter_query": "{kind} = 'total'"}, "fontWeight": "700", "borderTop": "1px solid var(--muted)"}]
    footer_records = [r for r, _ in footer]
    return html.Div(className="positions-fx", children=[
        about(title, FX_POSITIONS_ABOUT, level=level),
        rk.with_footer(table, rk.whole_units(footer_records, ("usd",)), footer_style=total_style,
                       footer_tooltips=_with_full_figures(footer_records, [t for _, t in footer], ("usd",)))])


POSITIONS_ABOUT = ("The book's positions, the key table of the Blotter. First the commodity futures, LME forwards "
                   "and options on futures by sector, then commodity, in lots, physical units and USD at the day's "
                   "official prices and spot (contract months on the Exposure tab); they are not in the FX net. Then "
                   "the delta by currency at the day's official spot, FX options included. Money is in k / m, the "
                   "full figure on hover; a figure with no mark reads n/a with the reason on hover, never 0.")
COMMODITIES_ABOUT = ("One line per sector (net and gross USD), its commodities under it: exchange, net and gross "
                     "lots, net physical units, net and gross USD. A non-USD contract is converted at the day's "
                     "spot. The Commodities total is not in the FX net.")
CCY_PNL_ABOUT = ("The P&L the non-USD futures have made, still held in the contract's currency, and its USD value "
                 "at the day's spot, as the engine converted it.")
FX_POSITIONS_ABOUT = ("Delta by currency, the FX & cash tab's risk table: the rate as quoted at the day's official "
                      "spot, the local delta and the USD delta, FX options' delta included, largest |USD delta| "
                      "first, USD last; a metal is shown apart and is not in the FX net. Then the FX net and gross "
                      "USD delta, and the FX options' delta by pair (already inside the currency rows).")
# The summary USD columns of the commodity Positions table: k / m, the full figure on hover.
_SUMMARY_USD_COLS = ("net_usd", "gross_usd")


def _with_full_figures(records: list, tips: list, cols, unit=None) -> list:
    """Copies of `tips` with the full figure ("1,650,590 USD") on hover of every number in
    `cols` that has no hover of its own (a k / m cell's exact value; a cell with a reason
    keeps its reason). `unit(rec)` names the unit when it is not USD."""
    out = []
    for rec, tip in zip(records, tips):
        tip = dict(tip)
        for c in cols:
            v = rec.get(c)
            if c in tip or isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
                continue
            label = unit(rec) if unit else "USD"
            tip[c] = {"value": f"{format_cell(v)} {label}".strip(), "type": "text"}
        out.append(tip)
    return out


def render_placeholder_strip(message: str) -> html.Div:
    """In place of a strip with nothing to sum: the reason, short and in sight."""
    return html.Div(html.Span(f"Unavailable ({message})", className="marker", title=message))


def _legs_table(legs: pd.DataFrame) -> dash_table.DataTable:
    formats = {"leg_no": rk.count(), "amount": rk.amount(), "rate": rk.rate(6, trim=True), "settles_cash": rk.count()}
    records = [{k: (rk.value(v) if k in formats else v) for k, v in rec.items()} for rec in legs.to_dict("records")]
    return dash_table.DataTable(
        columns=[rk.numeric(c.replace("_", " ").title(), c, formats[c]) if c in formats
                 else rk.text(c.replace("_", " ").title(), c) for c in legs.columns],
        data=records,
        **rk.sortable(),
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontSize": "12px"},
        style_header={"fontWeight": "bold", "fontSize": "12px"},
    )


# What a row's key date is, per product, for the row-click panel's "keyed on the <...>".
_KEY_DATE_NAMES = {"FUTURE": "expiry", "LME_FWD": "prompt", "CMDTY_OPTION": "expiry", "FX_OPTION": "expiry",
                   "EQ_OPTION": "expiry"}


def mark_used_text(row) -> str:
    """The row-click panel's mark, worded for what `value_book`'s `mark_date` is. On an open row
    it is the date the mark is keyed on (a future's expiry, an LME ticket's prompt, an FX leg's
    value date), not the close the price came from, so it reads "keyed on the expiry <d>". On a
    settled row it is the date of the price the ledger froze the trade at; on a closed-out
    option, the close-out date. With no mark, an em dash and the row's reason, never a bare
    "nan". The source and the reason are in plain words (`plain_words`: "Bloomberg history",
    "no official futures price"), never a mark type or source code."""
    mark, source = row.get("mark"), plain_words(_text(row.get("mark_source")))
    day, status = _text(row.get("mark_date")), _text(row.get("status"))
    if mark is None or (isinstance(mark, float) and mark != mark):
        why = plain_words(_text(row.get("reason"))) or (SETTLED_MARK_REASON if status == "SETTLED" else "no mark on this row")
        if status == "SETTLED" and day and not _text(row.get("reason")):
            why += f"; frozen at the official price of {day}"
        return f"{MISSING} ({why})"
    if status == "SETTLED":
        when = f"frozen at the official price of {day}" if day else "frozen"
    elif status == "CLOSED":
        when = f"closed out on {day}" if day else "closed out"
    else:
        name = _KEY_DATE_NAMES.get(_text(row.get("product")), "value date")
        when = f"keyed on the {name} {day}" if day else ""
    return f"{mark} ({'; '.join(t for t in (source, when) if t)})" if (source or when) else f"{mark}"


def row_expand_panel(conn: sqlite3.Connection, trade_id: str, row: pd.Series) -> html.Div:
    """Legs (from `trade_legs`) + the marks-used columns already on the value_book row."""
    legs = pd.read_sql_query(
        "SELECT leg_no, leg_type, ccy, amount, start_date, settle_date, rate, settles_cash "
        "FROM trade_legs WHERE trade_id = ? ORDER BY leg_no", conn, params=(trade_id,))
    marks_used = html.P(
        f"Mark: {mark_used_text(row)} · "
        f"Spot: {row.get('spot')} ({plain_words(_text(row.get('spot_source'))) or 'no spot source'})",
        className="blotter-row-marks", style={"fontSize": "12px", "margin": "2px 0"},
    )
    return html.Div(className="blotter-row-expand", children=[
        html.H5(f"Trade {trade_id}"),
        marks_used,
        _legs_table(legs) if not legs.empty else message_box("No legs on file."),
    ])


def row_detail_panel(conn: sqlite3.Connection, trade_id: str, df: pd.DataFrame) -> html.Div:
    """The compact panel shown below the table when a row is clicked: the trade, its legs
    and the marks used. (The FX-swap package view left on 2026-09-24 with the package rule.)"""
    row = df[df["trade_id"] == trade_id]
    if row.empty:
        return message_box("Trade not found in the visible rows.")
    return html.Div(className="section section--secondary",
                     children=[row_expand_panel(conn, trade_id, row.iloc[0])])


def csv_download(rows, display_columns: list, column_labels: dict, scope: str = "total"):
    """`dcc.send_data_frame` of the rows shown (the table's `derived_virtual_data`: every
    filter and sort applied), every display column at full figures under its screen label,
    plus the trade id; None with no rows (nothing to download)."""
    if not rows:
        return None
    frame = pd.DataFrame(list(rows))
    cols = [c for c in display_columns if c in frame.columns]
    if "trade_id" in frame.columns and "trade_id" not in cols:
        cols.append("trade_id")
    frame = frame[cols].rename(columns={c: column_labels.get(c, c) for c in cols})
    return dcc.send_data_frame(frame.to_csv, f"trades-{scope}.csv", index=False)


def scope_columns(scope: str, df: Optional[pd.DataFrame] = None) -> tuple:
    """(display_columns, column_labels) for a sub-tab: every table scope shares the Total
    book's commodity-terms layout (the Futures & LME layout left on 2026-09-28). Factored out
    so both `scope_layout` and the filter-dropdown callback build the identical column set;
    `scope` and `df` are kept for those callers."""
    return _DISPLAY_COLUMNS, _COLUMN_LABELS


def scope_header_tips(scope: str) -> dict:
    """Header hovers of a sub-tab's trade table: the Greek columns say what they are."""
    return {c: GREEK_TIP for c in GREEK_COLS}


def scope_df(conn: sqlite3.Connection, scope: str, as_of: str) -> pd.DataFrame:
    """The full (unfiltered-by-dropdown) priced, display-enriched, sorted frame for one
    sub-tab: `priced_value_book` -> scope's product filter -> `add_row_display_fields`
    -> fixed settle-date/pair order. Shared by `scope_layout` (initial render) and the
    filter-dropdown callback (re-run on every selection change), so the two can never
    drift apart."""
    df, _n_fallback, _n_total = priced_value_book(conn, as_of)
    products = SCOPE_PRODUCTS[scope]
    if products is not None and not df.empty:
        df = df[df["product"].isin(products)]
    if df.empty:
        return df
    df = add_row_display_fields(conn, df, as_of)
    if scope == "total":
        df = add_instrument_fields(conn, df)
        df = add_trade_labels(conn, df, as_of)
        df = add_prev_close(conn, df, as_of)
        df = add_option_greeks(conn, df, as_of)
        df = add_price_units_and_flags(conn, df)
    return _sorted_scope_df(df)


NO_STRIKE_FLAG = "no strike"
NO_STRIKE_HOVER = ("This option has no strike on file, so it cannot be priced: type the strike in its Strike cell "
                   "under Options (Payoff Digital where it is one), or re-upload an export with a Strike column.")


def add_price_units_and_flags(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    """Two display columns (2026-09-28): `price_unit`, the unit each row's fill and mark are quoted
    in (`formatting.quoted_unit` of the contract root, '' for FX), so the table prints prices at
    tick precision; and `flag`, 'no strike' on an FX option with no strike on file (the red banner
    of old, now a marker on the row and a line in the Data issues drawer)."""
    out = df.copy()
    out["price_unit"], out["flag"] = "", ""
    if out.empty:
        return out
    roots: dict = {}
    try:
        from data.contracts import load_roots
        roots = dict(load_roots())
    except Exception:  # noqa: BLE001 -- a price without its unit still prints
        roots = {}
    bases: dict = {}
    try:
        for inst, base in conn.execute("SELECT instrument_id, base_ccy FROM instruments"):
            bases[str(inst)] = str(base or "")
    except sqlite3.Error:
        bases = {}
    products = out["product"].astype(str).tolist() if "product" in out.columns else [""] * len(out)
    # An FX spot / forward reads its price in its pair (a JPY cross 3 decimals, gold 2, else 4:
    # `formatting.fx_pair_decimals`); an FX option premium keeps its own decimals ('').
    out["price_unit"] = [str(i) if p in ("FX_SPOT", "FX_FWD") and is_fx_pair(str(i))
                         else quoted_unit(roots.get(bases.get(str(i), "")))
                         for i, p in zip(out["instrument_id"], products)]
    try:
        from ui.tabs import options as options_ui
        missing = {i["instrument_id"] for i in options_ui.option_instruments(conn) if not i["strike"]}
    except Exception:  # noqa: BLE001 -- no flag rather than a broken table
        missing = set()
    if missing:
        out["flag"] = [NO_STRIKE_FLAG if str(i) in missing else "" for i in out["instrument_id"]]
    return out


def add_trade_labels(conn: sqlite3.Connection, df: pd.DataFrame, as_of: Optional[str] = None) -> pd.DataFrame:
    """`strategy`, `trade_type`, `type_source`, `type_note` and `pb_root` on the frame: the
    broker's labels on each trade (`trades.strategy`, Jason's strategy name; `trades.trade_type`,
    CROSS_EXCHANGE | CROSS_PRODUCT | TERM_STRUCTURE | ''; the raw PBRoot), read as they are, ''
    on a database from before the columns; and, with `as_of`, the type of the position the trade
    is in as spreads-engine gave it (`ui.tabs.book.trade_types`: the label where the legs agree
    with it, else read from the legs, `type_source` 'label' | 'inferred' | 'mixed labels'), so
    the Trades tab shows the one type the Book and P&L show. A `strategy` column the reader
    already carries is kept where it is non-empty."""
    out = df.copy()
    if out.empty:
        for col in ("strategy", "trade_type", "type_source", "type_note", "pb_root"):
            if col not in out.columns:
                out[col] = pd.Series(dtype=object)
        return out
    try:
        labels = {str(t): (str(st or ""), str(tt or ""), str(pb or "")) for t, st, tt, pb in
                  conn.execute("SELECT trade_id, strategy, trade_type, pb_root FROM trades")}
    except sqlite3.Error:
        labels = {}
    tids = [str(t) for t in out["trade_id"]]
    have = out["strategy"].tolist() if "strategy" in out.columns else [""] * len(out)
    out["strategy"] = [str(h or "") or labels.get(t, ("", "", ""))[0] for t, h in zip(tids, have)]
    out["trade_type"] = [labels.get(t, ("", "", ""))[1] for t in tids]
    out["type_source"] = ["label" if labels.get(t, ("", "", ""))[1] else "" for t in tids]
    out["type_note"] = ["the broker's label on the trade" if labels.get(t, ("", "", ""))[1] else "" for t in tids]
    out["pb_root"] = [labels.get(t, ("", "", ""))[2] for t in tids]
    if as_of:
        try:
            from ui.tabs.book import _labels, _spreads, trade_types
            types = trade_types({"labels": _labels(conn), "spreads": _spreads(conn, as_of)})
        except Exception:  # noqa: BLE001 -- the broker's own labels stand
            logging.getLogger(__name__).exception("trade types unavailable for the Trades tab on %s", as_of)
            types = {}
        if types:
            out["trade_type"] = [(types.get(t) or {}).get("trade_type") or tt for t, tt in zip(tids, out["trade_type"])]
            out["type_source"] = [(types.get(t) or {}).get("type_source") or src for t, src in zip(tids, out["type_source"])]
            out["type_note"] = [(types.get(t) or {}).get("type_note") or note for t, note in zip(tids, out["type_note"])]
    return out


def add_option_greeks(conn: sqlite3.Connection, df: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """`GREEK_COLS` on the frame: an open option on a future's official DELTA / GAMMA / THETA /
    VEGA marks on `as_of`, keyed on its own instrument and expiry, read as they are; None on
    every other row (and on an option with no such mark). Nothing is priced or scaled."""
    out = df.copy()
    for col in GREEK_COLS:
        out[col] = None
    if out.empty or "product" not in out.columns:
        return out
    options = out[out["product"] == "CMDTY_OPTION"]
    if options.empty:
        return out
    instruments = sorted({str(i) for i in options["instrument_id"]})
    try:
        placeholders = ",".join("?" * len(instruments))
        rows = conn.execute(
            f"SELECT instrument_id, settle_date, mark_type, value FROM marks_official WHERE as_of_date = ? "
            f"AND mark_type IN ('DELTA','GAMMA','THETA','VEGA') AND instrument_id IN ({placeholders})",
            (as_of, *instruments)).fetchall()
    except sqlite3.Error:
        return out
    marks = {(str(i), str(d), str(mt)): v for i, d, mt, v in rows}
    for idx, r in options.iterrows():
        for col, mt in _GREEK_MARK_TYPES.items():
            v = marks.get((str(r["instrument_id"]), str(r.get("settle_date", "")), mt))
            if v is None:   # keyed on another date (a moved expiry): the option's own mark still
                v = next((val for (i, _d, m), val in marks.items()
                          if i == str(r["instrument_id"]) and m == mt), None)
            out.at[idx, col] = v
    return out


def add_instrument_fields(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    """The trade tables' descriptive columns, looked up, never computed.

    `pnl_ccy`, the currency `value_book`'s `pnl_local` is in: the instrument's quote
    currency (CLAUDE.md "P&L conventions -> Futures"; an FX forward's quote P&L), except an
    FX option's, which is in the pair's base currency (`value_book`: premium in base-ccy
    fraction). From the contract master (`data.contracts.load_roots`, keyed on the
    instrument's `base_ccy`, which is the root id, 'SHFE:CU', for a future, an option on one
    and an LME metal): `exchange`, `sector` ('Energy') and `commodity` (the root's name). An
    FX trade (`FX_ROW_PRODUCTS`) has no root: its commodity is the pair ('USDCNH'), its
    exchange `FX_EXCHANGE` (OTC) and its sector `FX_SECTOR`. A contract the master does not
    know shows a blank exchange, sector `UNCLASSIFIED_SECTOR` and its root id on file as the
    commodity. An LME forward's instrument is its metal's root id, so it takes that root's
    sector and name and sits with the metal.

    `qty_unit`: lots for a future or an option on one, t for an LME forward
    (`QUANTITY_UNIT_OF`), the base currency for an FX trade (its quantity is the base
    amount, an FX option's its notional). An LME forward's blank `pnl_local` shows its
    `pnl_usd` (a USD figure in a USD column)."""
    from data.contracts import load_roots

    try:
        roots = load_roots()
    except (OSError, ValueError):   # an unreadable universe: labels blank, figures untouched
        logging.getLogger(__name__).exception("contract universe unreadable for the Blotter")
        roots = {}
    df = df.copy()
    if df.empty:
        for col in ("exchange", "pnl_ccy", "sector", "commodity", "qty_unit"):
            df[col] = pd.Series(dtype=object)
        return df
    instruments = {}
    for instrument_id in df["instrument_id"].unique():
        row = conn.execute("SELECT base_ccy, quote_ccy FROM instruments WHERE instrument_id = ?",
                           (instrument_id,)).fetchone()
        instruments[instrument_id] = (row[0] or "", row[1] or "") if row else ("", "")

    def describe(instrument_id, product) -> tuple:
        """(exchange, pnl_ccy, sector, commodity, qty_unit) of one row."""
        base, quote = instruments.get(instrument_id, ("", ""))
        root = roots.get(base) if base else None
        if root is not None:
            # the Book's short plain name ("USD/CNH", "HRC", "COMEX copper"); the universe's long name on hover
            return (root.exchange, quote, _sector_label(root.sector), short_root_name(root, base), QUANTITY_UNIT_OF.get(product, ""))
        if product in FX_ROW_PRODUCTS:
            pair = f"{base}{quote}" if base and quote else instrument_id
            return (FX_EXCHANGE, base if product == "FX_OPTION" else quote, FX_SECTOR, pair, base)
        return ("", quote, UNCLASSIFIED_SECTOR, base or instrument_id, QUANTITY_UNIT_OF.get(product, ""))

    described = [describe(i, p) for i, p in zip(df["instrument_id"], df["product"])]
    for n, col in enumerate(("exchange", "pnl_ccy", "sector", "commodity", "qty_unit")):
        df[col] = [d[n] for d in described]
    df["commodity_long"] = [str(getattr(roots.get(instruments.get(i, ("", ""))[0]), "name", "") or "")
                            for i in df["instrument_id"]]
    # An LME forward is USD-quoted (S = 1): its local P&L IS its USD P&L. `value_book` leaves
    # `pnl_local` blank on a settled one (the ledger's frozen row holds USD only), so the
    # frozen USD figure is shown there too, as it is, never recomputed.
    if "pnl_local" in df.columns and "pnl_usd" in df.columns:
        lme_usd = (df["product"] == "LME_FWD") & (df["pnl_ccy"] == "USD")
        df.loc[lme_usd, "pnl_local"] = df.loc[lme_usd, "pnl_local"].fillna(df.loc[lme_usd, "pnl_usd"])
    return df


# The Futures & LME sub-tab's name for the same lookup (kept: tests and older notes use it).
add_future_fields = add_instrument_fields


def add_prev_close(conn: sqlite3.Connection, df: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """`prev_close`: each trade's mark on the previous business day's close (the Daily
    period's reference date, `engine.pnl.ledger.period_reference_dates(as_of)["daily"]`),
    exactly as the shared reader values that day (`priced_value_book`, the fill included),
    never looked up or estimated here; and `prev_close_tip`, the cell's hover: that close's
    date and mark source (and the row's note when the fill used an earlier close), or, with
    no mark, why (the day's own reason, the trade not yet dealt, or already settled)."""
    from engine.pnl.ledger import period_reference_dates

    df = df.copy()
    if df.empty:
        df["prev_close"], df["prev_close_tip"] = pd.Series(dtype=float), pd.Series(dtype=object)
        return df
    t1 = period_reference_dates(as_of)["daily"]
    try:
        prev, _, _ = priced_value_book(conn, t1)
    except Exception as exc:  # noqa: BLE001 -- the column says why; the table still renders
        logging.getLogger(__name__).exception("previous close valuation failed for %s", t1)
        df["prev_close"], df["prev_close_tip"] = float("nan"), f"the {t1} close could not be valued ({exc})"
        return df
    rows = {r["trade_id"]: r for r in prev.to_dict("records")} if not prev.empty else {}
    values, tips = [], []
    for trade_id, trade_date in zip(df["trade_id"], df["trade_date"]):
        r = rows.get(trade_id)
        if r is None:
            values.append(float("nan"))
            tips.append(f"not in the book on the {t1} close" + (" (dealt after it)" if str(trade_date) > t1 else ""))
            continue
        mark = rk.value(r.get("mark"))
        values.append(float("nan") if mark is None else mark)
        if mark is None:
            why = plain_words(_text(r.get("reason"))) or (f"settled by the {t1} close: frozen at settlement, no longer marked"
                                                          if r.get("status") == "SETTLED" else f"no mark on the {t1} close")
            tips.append(why)
        else:
            source = plain_words(_text(r.get("mark_source")))
            said = f"{t1} close" + (f", {source}" if source else "")
            note = plain_words(_text(r.get("note")))
            tips.append(f"{said}; {note}" if note else said)
    df["prev_close"], df["prev_close_tip"] = values, tips
    return df


def _table_rows(scope: str, df: pd.DataFrame, display_columns: list, column_labels: dict):
    """A scope's trade-table records, flat (`_format_rows`; the Futures & LME grouping by sector
    and commodity left on 2026-09-28). One builder for the first render and the filter
    callback's refresh; `scope` is kept for those callers."""
    return _format_rows(df, display_columns, column_labels)


def _bad_values_text(conn: Optional[sqlite3.Connection]) -> str:
    """`describe_bad_stored_values(conn)`, "" when there is no connection or nothing bad.
    Never raises: it only ever decorates an error card or a notice."""
    if conn is None:
        return ""
    try:
        return describe_bad_stored_values(conn)
    except Exception:  # noqa: BLE001 -- diagnosis only, must never be the thing that fails
        return ""


def _error_card(label: str, exc: Exception, conn: Optional[sqlite3.Connection] = None) -> html.Div:
    """Inline failure card for one Blotter sub-section (2026-09-17 coordinator
    instruction, live incident: `ui.tabs.options`'s stale-dev-DB `payoff` column threw
    an uncaught `OperationalError` from `_leg_rows`, which propagated all the way past
    `_update`'s old `except ImportError`-only handler as an HTTP 500 -- Dash's
    `Output(CONTENT_ID, "children")` never fired, so the tab just stayed on whatever it
    showed before, which is what "the blotter sub tabs do not load" looked like from
    the browser). Shows the exception's one-line message so the cause is visible on
    the page itself, not only in the server log.

    With `conn` (2026-09-18): the card also names every stored figure that is not a
    number -- table.column, the trade/instrument/mark it belongs to, the offending text
    and how to fix it (`blotter_pricing.bad_stored_values`). The bare message the user
    got on the Bloomberg PC, "could not convert string to float: '<a date>'", said
    neither which trade nor which column, so nobody could act on it."""
    children = [html.P(f"{label} could not be rendered ({exc}).", className="section-kicker",
                       style={"color": "var(--neg)"})]
    found = _bad_values_text(conn)
    if found:
        children.append(html.P(f"Stored values that are not numbers: {found}.", className="section-kicker",
                               style={"color": "var(--neg)"}))
    return html.Div(className="section section--error", children=children)


def _safe_section(label: str, builder: Callable[[], html.Div],
                  conn: Optional[sqlite3.Connection] = None) -> html.Div:
    """Run one sub-section builder (a P&L strip, a delegated sub-tab's table, the
    asset-class breakdown, ...) and turn any exception into an inline `_error_card`
    instead of letting it propagate. A pure pass-through on success -- returns exactly
    `builder()`'s own component, no extra nesting -- so every existing test asserting on
    `scope_layout`'s returned structure keeps working unchanged; only the failure path
    is new. This means one broken piece of a scope's layout (e.g. the asset-class
    rollup) no longer blanks the rest of that same sub-tab (e.g. its P&L strip and
    trade table), and one sub-tab's delegated build (FX/Options/Bundles)
    failing no longer prevents switching to a different sub-tab, since `_update`
    (this module's top-level callback) still returns successfully either way. Logged
    server-side (`logging.exception`) so the full traceback is still available even
    though the page only shows the one-line message."""
    try:
        return builder()
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
        logging.getLogger(__name__).exception("Blotter section %r failed to render", label)
        return _error_card(label, exc, conn)


def bad_values_notice(conn: sqlite3.Connection) -> Optional[html.Div]:
    """A red banner, on top of every Blotter sub-tab, naming every stored figure that is
    not a number: table.column, the trade / instrument / mark it belongs to, the offending
    text, and what fixes it (`blotter_pricing.bad_stored_values`). None when every stored
    figure is a number, which is the normal case.

    2026-09-18: `value_book` now leaves a trade with such a value unpriced instead of
    failing, so the views load -- which also means the only trace of the bad cell would
    otherwise be one "n/a" row and an "excludes 1 of N trades unpriced" caption. A data
    error the user has to fix is said outright, like the missing-option-terms banner."""
    found = _bad_values_text(conn)
    if not found:
        return None
    return html.Div(className="notice notice--bad-values", role="alert",
                    style={"border": "1px solid var(--neg)", "borderRadius": "4px", "padding": "3px 8px",
                           "margin": "0 0 6px", "fontSize": "12px", "background": "rgba(178, 59, 59, 0.08)"},
                    children=[html.B("Stored values that are not numbers. "), html.Span(found + ".")])


def blotter_notices(conn: sqlite3.Connection) -> list:
    """The Blotter's banners, in display order, each only if it has something to say:
    stored values that are not numbers, then options with no strike on file. [] is the
    normal case. One builder for the always-present container the page refreshes in place
    (`NOTICES_ID`, `_refresh_notices`) and for `scope_layout`'s direct callers."""
    return [n for n in (
        _safe_section("Stored values notice", lambda: bad_values_notice(conn)),
    ) if n is not None]


def scope_layout(scope: str, conn: sqlite3.Connection, as_of: str, with_notices: bool = True) -> html.Div:
    """One sub-tab's content, with the missing-option-terms banner and the
    stored-values-that-are-not-numbers banner (each only if it has something to say) on
    top of the scope body built by `_scope_layout_body`. With neither, the body is
    returned as it is -- no extra nesting.

    `with_notices=False` is how the page itself calls it (`_update`, 2026-09-18): there
    the banners live in `NOTICES_ID`, above the content and outside it, so they follow
    every revision in place -- a saved strike leaves the banner at once -- instead of
    being frozen into the sub-tab until its next rebuild."""
    body = _scope_layout_body(scope, conn, as_of)
    notices = blotter_notices(conn) if with_notices else []
    if not notices:
        return body
    return html.Div([*notices, body])


TOTAL_ISSUES_ID = "blotter-issues-total"
_FILL_NOTE_PREFIX = "no price on "   # engine.pnl.reference.fill_book's note on a filled row


def total_book_issues(df: pd.DataFrame, as_of: str):
    """The Total book's one collapsed "Data issues (N)" drawer (Screens redesign Phase A):
    every trade with no P&L on `as_of`, with `value_book`'s own reason, then every trade
    valued from an earlier close by the fill, with its note. The same reasons are on hover
    of each cell; this lists them in one place. None when there is nothing to say."""
    if df.empty:
        return None
    items = []
    pnl = pd.to_numeric(df["pnl_usd"], errors="coerce") if "pnl_usd" in df.columns else pd.Series(dtype=float)
    reasons = df["reason"].tolist() if "reason" in df.columns else [""] * len(df)
    notes = df["note"].tolist() if "note" in df.columns else [""] * len(df)
    for trade_id, value, reason in zip(df["trade_id"], pnl.tolist(), reasons):
        if value != value:
            items.append((trade_id, f"no P&L on {as_of}: {_text(reason) or 'no reason given'}"))
    for trade_id, note in zip(df["trade_id"], notes):
        if _text(note).startswith(_FILL_NOTE_PREFIX):
            items.append((trade_id, _text(note)))
    if "flag" in df.columns:
        for trade_id, inst, flag in zip(df["trade_id"], df["instrument_id"], df["flag"].tolist()):
            if flag:
                items.append((trade_id, f"{inst}: {NO_STRIKE_HOVER}"))
    return issues_drawer(items, id=TOTAL_ISSUES_ID)


def _scope_layout_body(scope: str, conn: sqlite3.Connection, as_of: str) -> html.Div:
    """Build one sub-tab's content: headline strip (initial, whole-scope) + filter
    dropdown bar + trade table + an (empty until a row is clicked) detail container
    below it. Options delegates to its own module.

    Every sub-section (P&L strip / the delegated Options table / the Total book's
    asset-class breakdown / the filter bar + trade table) is wrapped in `_safe_section`
    so a failure in one doesn't blank the others -- see that helper's docstring."""
    strip_id = f"blotter-strip-{scope}"
    table_id = f"blotter-datatable-{scope}"
    detail_id = f"{DETAIL_PANEL_ID}-{scope}-detail"
    display_columns, column_labels = scope_columns(scope)

    if scope == "options":
        # No P&L strip (layout wave 2, 2026-09-29): the header holds the P&L, the option book's
        # totals are the "By pair or underlying" table's totals row.
        return _safe_section("Options", lambda: options_ui.build_layout(conn, as_of), conn)

    if scope in PLACEHOLDER_SCOPES:
        def _placeholder():
            empty = pd.DataFrame(columns=_DISPLAY_COLUMNS)
            return html.Div([
                html.Div(id=strip_id, children=render_placeholder_strip(PLACEHOLDER_SCOPES[scope])),
                detail_table(empty, table_id=table_id),
                html.Div(id=detail_id),
            ])
        return _safe_section(SCOPE_LABELS.get(scope, scope), _placeholder)

    # "total": the shared priced_value_book pipeline (module docstring calls this
    # "pricing"). A failure pulling `df` itself blocks everything downstream (the
    # strip, the asset-class table and the trade table all need it), so that one step
    # is not wrapped by `_safe_section` -- there is nothing partial to preserve -- but
    # still degrades to one inline card rather than propagating past `_update`.
    try:
        df = scope_df(conn, scope, as_of)
    except Exception as exc:  # noqa: BLE001 -- see _safe_section's docstring
        import logging
        logging.getLogger(__name__).exception("Blotter section %r failed to render", "Pricing")
        return html.Div([_error_card("Pricing", exc, conn), html.Div(id=detail_id)])
    display_columns, column_labels = scope_columns(scope, df)

    def _strip():
        trade_ids = df["trade_id"].tolist() if not df.empty else []
        headline = row_scoped_headline(conn, as_of, trade_ids)
        return html.Div(id=strip_id, children=render_headline_strip(headline))

    # The Total book has no P&L strip (Screens redesign Phase A, 2026-09-25): the header
    # above every tab is the total book. Its reasons sit in one collapsed drawer on top.
    body = [] if scope in _STRIPLESS_SCOPES else [_safe_section("P&L strip", _strip, conn)]
    if scope == "total" and not df.empty:
        drawer = total_book_issues(df, as_of)
        if drawer is not None:
            body.append(drawer)
        # The Positions table and the P&L by asset class left the Trades tab on 2026-09-28 (UI
        # redesign wave 2): the FX positions are on the Exposure tab ("Currency and FX
        # exposure", `fx_positions_table`), the commodity positions there too from
        # curve-positions itself, and the P&L by asset class on the P&L tab ("By product",
        # `asset_class_pnl_table`).
    if df.empty:
        body.append(message_box("No trades for this as-of date in this scope."))
        body.append(html.Div(id=detail_id))
    else:
        body.append(_safe_section(
            "Filter bar", lambda: _filter_bar(df, table_id, display_columns, column_labels)))
        body.append(_safe_section(
            "Trade table",
            lambda: detail_table(df, table_id=table_id, display_columns=display_columns,
                                  column_labels=column_labels, scope=scope), conn))
        body.append(html.Div(id=detail_id))
    return html.Div(body)


# =========================================================================== the Blotter (2026-09-29)
# User, 2026-09-29: the Trades tab is the Blotter again, "What did I load, and did it load right?":
# the audit trail of Jason's uploaded file, with NO P&L (the Book is the one place for P&L, marks
# and period figures). Phase G (round 2, 2026-09-29): top to bottom, the last upload (its one line,
# the rows not loaded, the file-level warnings), every fill in eight columns (a static card whose
# bar and table are drawn by `ui.tabs.blotter_fills`' own callbacks from a session store, so the
# filter survives a refresh and a tab switch), the upload history (a closed fold), the one Data
# issues drawer. Read as the upload lane recorded it and as `engine.spreads.trade_book` lands each
# fill in a trade; nothing is priced or recomputed here.
BLOTTER_QUESTION = "What did I load, and did it load right?"
OPTION_PRODUCTS = ("FX_OPTION", "CMDTY_OPTION")
HISTORY_SLOT_ID = "blotter-history-slot"
BLOTTER_ISSUES_SLOT_ID = "blotter-issues-slot"
BLOTTER_ISSUES_ID = "blotter-issues"


def blotter_view(conn: sqlite3.Connection, as_of: str) -> tuple:
    """(the last upload, the upload history fold or None, the Data issues drawer or None): the
    three blocks of the Blotter around its fills card (`blotter_fills.section`, static in the
    layout). A block that fails says so where it would be."""
    try:
        top = fills_ui.last_upload_block(conn)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("Blotter: the last upload could not be read")
        top = _error_card("Last upload", exc, conn)
    try:
        fold = fills_ui.history_fold(conn)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("Blotter: the upload history could not be read")
        fold = _error_card("Upload history", exc, conn)
    try:
        _df, issues, _with = fills_ui.fills_frame(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the table's own callback says why too
        logging.getLogger(__name__).exception("Blotter: the fills could not be read on %s", as_of)
        issues = [("Fills", f"the fills could not be read on {as_of} ({type(exc).__name__}: {exc})")]
    return top, fold, issues_drawer(issues, id=BLOTTER_ISSUES_ID)


def has_open_option(conn: sqlite3.Connection, as_of: str) -> bool:
    """Whether the book holds an open option (an FX option or an option on a future) on `as_of`,
    by the shared reader's status; with the reader unavailable, an option whose expiry is not
    past. The Options sub-tab shows only then."""
    try:
        df, _, _ = priced_value_book(conn, as_of)
        if df.empty:
            return False
        return bool((df["product"].isin(OPTION_PRODUCTS) & (df["status"] == "OPEN")).any())
    except Exception:  # noqa: BLE001 -- the instruments' own expiries
        try:
            row = conn.execute("SELECT COUNT(*) FROM trades t JOIN instruments i USING (instrument_id) "
                               "WHERE t.product IN ('FX_OPTION','CMDTY_OPTION') AND i.expiry_date >= ?",
                               (as_of,)).fetchone()
            return bool(row and row[0])
        except sqlite3.Error:
            return False


def _today_default(default_date: Optional[str]) -> Optional[str]:
    """As-of defaults to today (New York, `ui.tabs.controls.today_ny`), so the strip and the
    list show the book as it stands now rather than at the last upload."""
    try:
        return today_ny()
    except Exception:
        return default_date


SUBTABS_HIDDEN = {"display": "none"}


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """The Blotter (Phase G, 2026-09-29): the sub-tab bar (All fills | Options), always in the page
    but hidden unless the book holds an open option (`_update` shows it), the banners, the
    trade-set store, the content (the last upload; the Options sub-tab when chosen), the fills
    card (static: its bar and table are drawn by `blotter_fills`' callbacks), the upload history
    and the Data issues drawer. The tab's name as its title (look pass 2026-09-30), no date picker:
    the as-of is the header's store."""
    return html.Div(className="blotter", children=[
        html.Div(html.H2("Blotter", className="tab-title"), className="tab-header"),
        dcc.Tabs(id=SUBTABS_ID, value=SCOPE_ORDER[0], className="subtabs", style=SUBTABS_HIDDEN, children=[
            dcc.Tab(label=SCOPE_LABELS[s], value=s, className="subtab",
                    selected_className="subtab--selected")
            for s in SCOPE_ORDER
        ]),
        # Banners (stored values that are not numbers): always in the page and refreshed in place
        # on every revision (`_refresh_notices`), not frozen into the content until its next rebuild.
        html.Div(id=NOTICES_ID),
        # The trade-set signature the content below was built from (`_update`).
        dcc.Store(id=BUILT_TRADE_SET_ID),
        html.Div(id=CONTENT_ID),
        fills_ui.section(),
        html.Div(id=HISTORY_SLOT_ID),
        html.Div(id=BLOTTER_ISSUES_SLOT_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The shell's convention: `register_callbacks(app, get_db_path)`, every tab alike."""

    @app.callback(
        Output(CONTENT_ID, "children"),
        Output(BUILT_TRADE_SET_ID, "data"),
        Output(SUBTABS_ID, "style"),
        Output(fills_ui.SECTION_ID, "style"),
        Output(HISTORY_SLOT_ID, "children"),
        Output(BLOTTER_ISSUES_SLOT_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(SUBTABS_ID, "value"),
        Input(BOOK_REVISION_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        State(BUILT_TRADE_SET_ID, "data"),
    )
    def _update(as_of_date, scope, _book_rev=None, _data_rev=None, built_trade_set=None):
        """`(content, trade-set signature it was built from, the sub-tab bar's style)`;
        `no_update` three times when what is on screen stays. The sub-tab bar shows only while the
        book holds an open option (2026-09-29); the Options scope falls back to the fills when the
        last one goes.

        No browser reload (ui/revision.py, 2026-09-18). A changed TRADE SET (an upload)
        rebuilds whatever sub-tab is showing. A marks-only change rebuilds the views that
        are one static block (Bundles); the Total book table instead refreshes its rows in
        place through `_apply_filters`, so a Bloomberg pull never resets the user's
        filters, sort or page, and Options refreshes its own table and its strip in place
        (`_SELF_REFRESHING_SCOPES`).

        A BOOK revision is not taken at its word. It also fires when a strike, type or
        payoff is saved (`revision.book_signature` sums strikes and signed quantities, and
        that publisher sends it at once) -- the edit the user makes several of in a row,
        each of which used to tear
        down the sub-tab he was typing the next one into. So on a revision the trade set
        is compared with the one this content was built from (`BUILT_TRADE_SET_ID`, held
        in the page, so it is right per browser tab): unchanged means the revision is, for
        this view, a data revision. An unreadable database counts as changed."""
        from dash import ctx, no_update
        from dash.exceptions import MissingCallbackContextException
        unchanged = (no_update,) * 6
        if not as_of_date:
            return (message_box("No as-of date available."), no_update, no_update, no_update, None, None)
        scope = scope or SCOPE_ORDER[0]
        try:
            triggered = {t["prop_id"].split(".")[0] for t in (ctx.triggered or [])}
        except MissingCallbackContextException:  # called directly, not by Dash: just render
            triggered = set()
        db_path = get_db_path()
        trade_set = None
        if triggered and triggered <= {DATA_REVISION_ID, BOOK_REVISION_ID}:
            book_moved = False
            if BOOK_REVISION_ID in triggered:
                trade_set = trade_set_signature(db_path)
                book_moved = not trade_set or trade_set != built_trade_set
            if not book_moved and scope not in _MARKS_REBUILD_SCOPES:
                return unchanged
        with_options = _options_shown(db_path, as_of_date)
        if scope == "options" and not with_options:
            scope = SCOPE_ORDER[0]   # the last option gone: the sub-tab bar hides, the fills show
        content = _render_content(db_path, as_of_date, scope)
        if trade_set is None:
            trade_set = trade_set_signature(db_path)
        subtabs = {} if with_options else SUBTABS_HIDDEN
        if isinstance(content, tuple):          # the fills scope: last upload, history, drawer
            top, fold, drawer = content
            return tidy(compact(top)), (trade_set or no_update), subtabs, {}, tidy(compact(fold)), tidy(drawer)
        return tidy(compact(content)), (trade_set or no_update), subtabs, SUBTABS_HIDDEN, None, None

    def _options_shown(db_path, as_of_date) -> bool:
        """Whether the Options sub-tab shows: the book holds an open option (`has_open_option`)."""
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError:
            return False
        try:
            with pricing_snapshot(conn, "Blotter options check"):
                return has_open_option(conn, as_of_date)
        except Exception:  # noqa: BLE001 -- no sub-tab rather than a broken tab
            logging.getLogger(__name__).exception("Blotter: the open-option check failed for %s", as_of_date)
            return False
        finally:
            conn.close()

    def _render_content(db_path, as_of_date, scope):
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc}).")
        try:
            with pricing_snapshot(conn, f"Blotter {scope}"):  # one view of the marks per render
                if scope == "total":
                    return blotter_view(conn, as_of_date)
                # The banners are in `NOTICES_ID`, outside this content (`_refresh_notices`).
                return scope_layout(scope, conn, as_of_date, with_notices=False)
        except ImportError as exc:
            return message_box(f"Blotter view not available yet ({exc}).")
        except Exception as exc:  # noqa: BLE001 -- last-resort guard, 2026-09-17
            # Any other exception here used to propagate past Dash's callback wrapper
            # as an uncaught HTTP 500: the sub-tab's Output("blotter-content","children")
            # never fired, so the tab silently stayed on its previous (or blank) content
            # -- exactly the user complaint "the blotter sub tabs do not load", with no
            # error visible anywhere in the browser. A DB/query bug (e.g. a stale dev DB
            # missing a column another sub-tab's query expects) should degrade to a
            # message for THAT sub-tab, not take the whole tab down. Logged so the real
            # cause is still visible server-side rather than swallowed silently.
            import logging
            logging.getLogger(__name__).exception(
                "Blotter sub-tab %r failed to render for as_of=%s", scope, as_of_date)
            found = _bad_values_text(conn)  # names the trade/column of any stored value that is not a number
            return message_box(f"This view could not be rendered ({exc})."
                               + (f" Stored values that are not numbers: {found}." if found else ""))
        finally:
            conn.close()

    def _register_strip_callback(scope: str) -> None:
        table_id = f"blotter-datatable-{scope}"
        strip_id = f"blotter-strip-{scope}"

        @app.callback(
            Output(strip_id, "children"),
            Input(table_id, "derived_virtual_data"),
            State(AS_OF_STORE_ID, "data"),
            prevent_initial_call=True,
        )
        def _update_strip(rows, as_of_date, _scope=scope):
            if not as_of_date:
                return render_placeholder_strip("no as-of date")
            if _scope in PLACEHOLDER_SCOPES:
                return render_placeholder_strip(PLACEHOLDER_SCOPES[_scope])
            trade_ids = [r["trade_id"] for r in (rows or []) if r.get("trade_id")]
            from ui.app import connect_readonly
            db_path = get_db_path()
            try:
                conn = connect_readonly(db_path)
            except sqlite3.OperationalError as exc:
                return message_box(f"Database not available ({exc}).")
            try:
                with pricing_snapshot(conn, f"Blotter {_scope} P&L strip"):
                    headline = row_scoped_headline(conn, as_of_date, trade_ids)
                    return render_headline_strip(headline)
            except Exception as exc:  # noqa: BLE001 -- an HTTP 500 here left the strip on stale figures, silently
                logging.getLogger(__name__).exception("Blotter %r P&L strip failed for as_of=%s", _scope, as_of_date)
                return _error_card("P&L strip", exc, conn)
            finally:
                conn.close()

    def _register_detail_callback(scope: str) -> None:
        table_id = f"blotter-datatable-{scope}"
        detail_id = f"{DETAIL_PANEL_ID}-{scope}-detail"

        @app.callback(
            Output(detail_id, "children"),
            Input(table_id, "active_cell"),
            State(table_id, "derived_virtual_data"),
            State(AS_OF_STORE_ID, "data"),
            prevent_initial_call=True,
        )
        def _update_detail(active_cell, rows, as_of_date, _scope=scope):
            if not active_cell or not rows or not as_of_date:
                return None
            row = rows[active_cell["row"]] if active_cell["row"] < len(rows) else None
            trade_id = row.get("trade_id") if row else None
            if not trade_id:
                return None
            from ui.app import connect_readonly
            db_path = get_db_path()
            try:
                conn = connect_readonly(db_path)
            except sqlite3.OperationalError as exc:
                return message_box(f"Database not available ({exc}).")
            try:
                df, _, _ = priced_value_book(conn, as_of_date)
                products = SCOPE_PRODUCTS.get(_scope)
                if products is not None and not df.empty:
                    df = df[df["product"].isin(products)]
                return row_detail_panel(conn, trade_id, df)
            finally:
                conn.close()

    def _register_filter_callback(scope: str) -> None:
        table_id = f"blotter-datatable-{scope}"
        display_columns, column_labels = scope_columns(scope)
        filter_cols = filter_cols_for(scope, display_columns)
        if not filter_cols:
            return
        filter_ids = [f"{table_id}-filter-{c}" for c in filter_cols]
        with_search = scope == "total"

        def _apply_filters(*args, _scope=scope, _filter_cols=filter_cols,
                            _display_columns=display_columns, _column_labels=column_labels):
            if with_search:
                *values, search, _data_rev, as_of_date = args
            else:
                *values, _data_rev, as_of_date = args
                search = ""
            if not as_of_date:
                return [], []
            from ui.app import connect_readonly
            db_path = get_db_path()
            try:
                conn = connect_readonly(db_path)
            except sqlite3.OperationalError:
                return [], []
            try:
                with pricing_snapshot(conn, f"Blotter {_scope} rows"):
                    df = scope_df(conn, _scope, as_of_date)
            finally:
                conn.close()
            for col, picked in zip(_filter_cols, values):
                if picked:
                    df = df[df[col].isin(picked)]
            df = search_rows(df, search)
            data_records, tooltip_data, _ = _table_rows(_scope, df, _display_columns, _column_labels)
            return data_records, tooltip_data

        app.callback(
            Output(table_id, "data"),
            Output(table_id, "tooltip_data"),
            *[Input(fid, "value") for fid in filter_ids],
            *([Input(f"{table_id}-search", "value")] if with_search else []),
            Input(DATA_REVISION_ID, "data"),  # new marks: refresh the rows, keep the filters
            State(AS_OF_STORE_ID, "data"),
            prevent_initial_call=True,
        )(_apply_filters)

        def _clear_filters(_n_clicks):
            return [[] for _ in filter_ids] + ([""] if with_search else [])

        app.callback(
            *[Output(fid, "value") for fid in filter_ids],
            *([Output(f"{table_id}-search", "value")] if with_search else []),
            Input(f"{table_id}-filter-clear", "n_clicks"),
            prevent_initial_call=True,
        )(_clear_filters)

        if with_search:
            def _download_csv(_n_clicks, rows, _scope=scope, _display_columns=display_columns,
                              _column_labels=column_labels):
                return csv_download(rows, _display_columns, _column_labels, _scope)

            app.callback(
                Output(f"{table_id}-download", "data"),
                Input(f"{table_id}-csv", "n_clicks"),
                State(table_id, "derived_virtual_data"),
                prevent_initial_call=True,
            )(_download_csv)

    options_ui.register_callbacks(app, get_db_path)

    fills_ui.register(app, get_db_path)

    def _register_strip_refresh(scope: str) -> None:
        @app.callback(
            Output(f"blotter-strip-{scope}", "children"),
            Input(DATA_REVISION_ID, "data"),
            State(AS_OF_STORE_ID, "data"),
            prevent_initial_call=True,
        )
        def _refresh_strip(_data_rev, as_of_date, _scope=scope):
            """New marks: redraw the P&L strip above the Options table in place. That
            sub-tab is not rebuilt whole on a data revision (`_SELF_REFRESHING_SCOPES`) and
            its module refreshes only its OWN table, so
            without this the strip would keep the figures of the last rebuild next to a
            table that has moved on. The strip exists only while its sub-tab is showing;
            Dash does not call this otherwise. A failure leaves the strip as it is rather
            than blanking it -- the next revision tries again."""
            from dash import no_update
            if not as_of_date:
                return no_update
            from ui.app import connect_readonly
            try:
                conn = connect_readonly(get_db_path())
            except sqlite3.OperationalError:
                return no_update
            try:
                with pricing_snapshot(conn, f"Blotter {_scope} P&L strip"):
                    return scope_strip(_scope, conn, as_of_date)
            except Exception:  # noqa: BLE001 -- keep what is on screen
                logging.getLogger(__name__).exception(
                    "Blotter %r P&L strip refresh failed for as_of=%s", _scope, as_of_date)
                return no_update
            finally:
                conn.close()

    for _scope in _SELF_REFRESHING_SCOPES:
        if _scope not in _STRIPLESS_SCOPES:   # Options has no strip since 2026-09-29
            _register_strip_refresh(_scope)

    @app.callback(
        Output(NOTICES_ID, "children"),
        Input(DATA_REVISION_ID, "data"),
        Input(BOOK_REVISION_ID, "data"),
    )
    def _refresh_notices(_data_rev=None, _book_rev=None):
        """The banners, read from the database on page load and on EVERY revision, so the
        count is never stale: an option leaves the missing-terms banner the moment its
        strike is saved (the save publishes a revision at once -- the terms editor's Save
        does it itself, a cell edit through `_publish_option_cell_edit`), whichever sub-tab
        is showing, without rebuilding anything. A database that
        cannot be read right now leaves the banners as they are; the next revision retries."""
        from dash import no_update
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(get_db_path())
        except sqlite3.OperationalError:
            return no_update
        try:
            return blotter_notices(conn)
        except sqlite3.Error:
            return no_update
        finally:
            conn.close()

    @app.callback(
        Output(DATA_REVISION_ID, "data", allow_duplicate=True),
        Input(options_ui.EDIT_STATUS_ID, "children"),
        State(DATA_REVISION_ID, "data"),
        prevent_initial_call=True,
    )
    def _publish_option_cell_edit(_status, current_data_rev):
        """A Strike / Type / Payoff CELL has just been saved: publish the data revision
        now. `ui.tabs.options._render` writes the terms and refreshes its own table but
        publishes nothing, so everything else that depends on the new terms -- the
        missing-terms banner, the header cards, the strips -- waited 5-10 seconds for the
        poll to notice the file had changed, with the banner still counting an option the
        user had just fixed. The edit status line is an OUTPUT of `_render`, so this runs
        after the save has landed, never before it. Nothing is published when the file did
        not move (a payoff merely noted until its strike is typed, a refused entry, the
        sub-tab being opened): Dash would fire every listener even for an unchanged value."""
        return publish_if_changed(file_signature(get_db_path()), current_data_rev)

    # "options" (ui.tabs.options, Phase 8) and "bundles" have no strip/detail/filter of
    # their own (module docstring above) -- neither is a priced_value_book-shaped table, so
    # the generic callbacks below (which assume trade_id/mark/pnl_usd rows) don't apply.
    for _scope in SCOPE_ORDER:
        if _scope not in _NON_TABLE_SCOPES:
            if _scope not in _STRIPLESS_SCOPES:   # the Total book has no strip (the header is the total)
                _register_strip_callback(_scope)
            _register_detail_callback(_scope)
            _register_filter_callback(_scope)
