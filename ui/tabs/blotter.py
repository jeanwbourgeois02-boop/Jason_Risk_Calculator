"""Blotter tab: docs/BUILD_PLAN.md section 5 "Blotter", rebuilt 2026-09-15 into sub-tabs
(user decision): Total book, FX, Futures, Options, Bundles, Manual entry. The Rates
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

Sub-tab layout, each (Total book / FX / Futures / Options):
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
`_register_strip_refresh`. A genuine book change (an upload, a manual trade booked or
deleted), a date change and a sub-tab change still rebuild it; a saved option term does
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

Total book (2026-09-17, user request "there should be P&L by asset type"): under the
strip, `asset_class_pnl_table` shows one row per asset class present (FX, Futures,
Options) plus Total, each with LTD / Daily / Previous day / 5d / MTD / YTD /
Trading computed by `row_scoped_period_pnl` over that class's trade ids -- the same
arithmetic and reference dates as the strip, so the class rows always sum to the strip.
Above it, the Positions table (`positions_table`): the currency rows, the FX net and gross
and the FX options' delta of `engine.ladder.positions.book_positions`.

Futures (2026-09-24, commodity conversion): Jason's commodity futures, each contract in its
own currency. The table shows the contract's exchange (contract master, `data.contracts`),
`value_book`'s `pnl_local` with its currency (the instrument's quote currency) beside the
USD P&L, which the engine converted at spot; nothing is converted here.

FX is `ui.tabs.blotter_fx.build_layout`: the legacy sheet's "All FX trades" column
layout (trade / tenor / fill / t-1-EOD-t-2 mark and P&L), but priced by
`engine.pnl.fx_blotter.fx_blotter_rows` -> `engine.pnl.valuation.value_book` (market-
standard convention: each FX leg at its own settle_date's FWD_OUTRIGHT, quote P&L
converted at spot, futures contracts x multiplier x (mark - fill), settled trades
frozen) -- rebuilt first as a literal xlsx replica on 2026-09-17 and replaced the same
day, by user decision, with this standard-convention version. It has no filter bar or
row-click detail panel -- its rows (trade_id/instrument_id/quantity_usd_notional/tenor/
fill/mark_*/pnl_*) don't have `value_book`'s shape (trade_id/mark/pnl_usd/...) that
scaffolding assumes. It DOES have a P&L strip (added 2026-09-17), built statically
inside `blotter_fx.build_layout` itself rather than through the generic
`_register_strip_callback` loop below: `blotter_fx._fx_strip` calls this same module's
`render_headline_strip`/`priced_value_book`/`row_scoped_headline` scoped to the
FX+FUTURE trade universe, through the identical `priced_value_book` pricing path the
table uses -- so the strip and the table now agree exactly on any priced trade (unlike
the retired replica table, which deliberately differed). The whole sub-tab is a single
always-current block, rebuilt on the same top-level `_update` callback as every other
sub-tab.

Bundles sub-tab (item 4): `ui.tabs.blotter_bundles` renders a list of bundles (from
`data.ingest.themes.list_bundles`) with LTD/Daily/MTD/YTD via
`engine.pnl.ledger.period_pnl_by(conn, as_of, 'theme')`, plus an "Unassigned" line, a
create form and add/remove-pair actions calling `data.ingest.themes.set_theme` (via the
`add_pair_to_bundle` / `remove_pair_from_bundle` helpers).

Manual entry sub-tab (2026-09-18, user request "need a place to manually input OTC
products"): `ui.tabs.manual_entry` -- book an FX option or forward the blotter export
does not carry (`data/ingest/manual.py`, `trades.source = 'MANUAL'`, survives every
re-upload), list/delete the manual trades on file, and the same Option terms editor
the Options sub-tab shows for options the export left without a strike.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs import blotter_bundles as bundles_ui
from ui.tabs import blotter_fx as blotter_fx_ui
from ui.tabs import manual_entry as manual_entry_ui
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
from ui.tabs.controls import build_date_picker
from ui.tabs import ranking as rk
from ui.tabs.formatting import format_cell

DATE_PICKER_ID = "blotter-date"
TOOLBAR_ID = "blotter-toolbar"
SUBTABS_ID = "blotter-subtabs"
CONTENT_ID = "blotter-content"
TITLE_ID = "blotter-title"
TODAY_BUTTON_ID = "blotter-today-button"
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
# Order per user decision 2026-09-15: Total book | FX | Futures | Options | Bundles. "FX"
# scope is FX_SPOT/FX_FWD/FX_SWAP (FUTURE has its own sub-tab). FX_SWAP stays (2026-09-24):
# manual entry books a swap as one 4-leg trade; only the blotter's package rule left. "Manual entry" (2026-09-18)
# closes the row: it is a form, not a view of the book. Rates left on 2026-09-24 (the
# macro trader's products are out of the app, CLAUDE.md "Commodity conversion plan").
SCOPE_ORDER = ("total", "fx", "futures", "options", "bundles", "manual")
SCOPE_LABELS = {"total": "Total book", "fx": "FX", "futures": "Futures",
                "options": "Options", "bundles": "Bundles", "manual": "Manual entry"}
SCOPE_PRODUCTS = {
    "total": None,
    "fx": ("FX_SPOT", "FX_FWD", "FX_SWAP"),
    "futures": ("FUTURE",),
    "options": ("FX_OPTION",),
    "manual": None,
}
# Sub-tabs that are forms/lists of their own, not `priced_value_book`-shaped tables:
# no strip / row-detail / filter callbacks are registered for them.
_NON_TABLE_SCOPES = ("bundles", "fx", "options", "manual")
# Views rebuilt whole when only marks changed (ui/revision.py); see `_update`.
_MARKS_REBUILD_SCOPES = ("bundles", "fx")
# Sub-tabs whose own module refreshes its table IN PLACE from the revision stores
# (`ui.tabs.options._render` / `_headline`), so it left the list above on 2026-09-18: a
# wholesale rebuild on every Bloomberg pull reset the Options terms editor's dropdown and
# could wipe a strike being typed into a cell. A genuine book change (an upload, a manual
# trade booked or deleted -- `_update` compares `revision.trade_set_signature`) and a date
# or sub-tab change still rebuild it; a saved term does NOT, although it is published as a
# book revision too. The one piece of the sub-tab built HERE, the P&L strip above the
# table, follows new marks through `_register_strip_refresh`.
_SELF_REFRESHING_SCOPES = ("options",)
# Kept (empty) so the placeholder path stays available for a future scope.
PLACEHOLDER_SCOPES: dict = {}

# Asset class per product for the Total book's per-class P&L table. A product not listed
# here (CMDTY_OPTION has no ingest path yet) falls to "Other" in `asset_class_pnl_rows`,
# still counted and summed, never dropped.
ASSET_CLASS_OF = {"FX_SPOT": "FX", "FX_FWD": "FX", "FX_SWAP": "FX", "FUTURE": "Futures", "FX_OPTION": "Options"}
ASSET_CLASS_ORDER = ("FX", "Futures", "Options")
ASSET_TABLE_ID = "blotter-asset-class-table"
POSITIONS_TABLE_ID = "blotter-positions-table"
# With no futures trades in the book for the date shown, the Futures strip reads "n/a"
# with this reason rather than a hollow zero.
FUTURES_NO_TRADES_REASON = "no futures trades in the book on this date"

# Columns offered as click-to-filter dropdowns (user decision 2026-09-15, replacing the
# broken native filter row): every categorical column that exists in a scope's own
# display columns. Date/amount/rate columns are not offered -- multi-select-from-values
# only makes sense for the categorical ones; "pick USDJPY" is the request, not a range
# filter.
FILTERABLE_COLS = ["instrument_id", "side", "status", "product", "strategy", "theme"]


# Futures columns (2026-09-15, then 2026-09-24 for the commodity book): Trade date |
# Contract | Exchange | Side | Contracts | Fill | Expiry | Status | Settlement | P&L (local)
# | Ccy | P&L (USD) | Strategy | Bundle | Trade id. A commodity future's P&L is struck in the
# contract's own currency (`value_book`'s `pnl_local`, in the instrument's quote currency)
# and converted to USD at spot by the engine; both are shown, side by side, as computed.
# "Settlement" is `mark_date` (the date the price used for P&L was struck).
_FUTURES_DISPLAY_COLUMNS = [
    "trade_date", "instrument_id", "exchange", "side", "quantity", "fill", "settle_date", "status",
    "mark_date", "pnl_local", "pnl_ccy", "pnl_usd", "strategy", "theme", "trade_id",
]
_FUTURES_COLUMN_LABELS = {
    "trade_date": "Trade date", "instrument_id": "Contract", "exchange": "Exchange", "side": "Side",
    "quantity": "Contracts", "fill": "Fill", "settle_date": "Expiry", "status": "Status",
    "mark_date": "Settlement", "pnl_local": "P&L (local)", "pnl_ccy": "Ccy", "pnl_usd": "P&L (USD)",
    "strategy": "Strategy", "theme": "Bundle", "trade_id": "Trade id",
}
# A settled future is the ledger's frozen row (`realised_pnl`), which holds its P&L in USD
# only: `value_book` leaves `pnl_local` blank on it, and this is said where the number
# would be (engine/pnl/valuation.py::_settled_future_row).
SETTLED_LOCAL_REASON = "settled: the ledger froze this trade's P&L in USD; its local-currency P&L is not stored"

# Display order and headers per the 2026-09-15 rebuild + coordinator's headline note:
# Mark is renamed "Live rate" and grouped with the two new per-pair columns (Notional,
# T-1 rate) right after Mark date, matching the old Excel Portfolio header row.
_DISPLAY_COLUMNS = [
    "trade_date", "instrument_id", "side", "quantity", "fill", "settle_date", "status",
    "mark_date", "notional_usd", "mark", "t1_rate", "pnl_usd", "product", "strategy",
    "theme", "trade_id",
]
_COLUMN_LABELS = {
    "trade_date": "Trade date", "instrument_id": "Pair", "side": "Side",
    "quantity": "Amount", "fill": "Fill", "settle_date": "Value date", "status": "Status",
    "mark_date": "Mark date", "notional_usd": "Notional (USD)", "mark": "Live rate",
    "t1_rate": "T-1 rate", "pnl_usd": "P&L (USD)", "product": "Product",
    "strategy": "Strategy", "theme": "Bundle", "trade_id": "Trade id",
}
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


_PRODUCT_LABELS = {"FX_SPOT": "Spot", "FX_FWD": "Forward", "FX_SWAP": "Swap", "FUTURE": "Future", "FX_OPTION": "Option"}


def _fmt_product(value) -> str:
    return _PRODUCT_LABELS.get(value, value or "")


def _sorted_scope_df(df: pd.DataFrame) -> pd.DataFrame:
    """The order the table opens in (settle date, then pair); a header click re-ranks it
    (ui.tabs.ranking). No-op on an empty frame."""
    if df.empty or "settle_date" not in df.columns:
        return df
    return df.sort_values(["settle_date", "instrument_id"], kind="stable")


_COLUMN_FORMATS = {   # the numeric columns of the trade table (ui.tabs.ranking); every other column is text
    "quantity": rk.count(),                 # unsigned: direction is carried by `side`
    "notional_usd": rk.amount(),
    "pnl_local": rk.amount(nully="n/a"),     # the Futures sub-tab: P&L in the contract's own currency
    "pnl_usd": rk.amount(nully="n/a"),
    "fill": rk.rate(6, nully="n/a"),
    "mark": rk.rate(6, nully="n/a"),
    "t1_rate": rk.rate(6, nully="n/a"),
}


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
    for col in cols:
        if col == "status":
            formatted[col] = formatted[col].map(_fmt_status)
        elif col == "product":
            formatted[col] = formatted[col].map(_fmt_product)
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
        reason = reasons.iloc[i] if i < len(reasons) else ""
        tip = {}
        if reason and rec.get("pnl_usd") is None:
            tip["pnl_usd"] = {"value": reason, "type": "text"}
        if "pnl_local" in rec and rec["pnl_local"] is None:
            # Unpriced: the row's own reason. Priced but settled: the frozen row holds USD only.
            settled = (statuses.iloc[i] if i < len(statuses) else "") == "SETTLED"
            why = reason or (SETTLED_LOCAL_REASON if settled else "no local-currency P&L on this row")
            tip["pnl_local"] = {"value": why, "type": "text"}
        tooltip_data.append(tip)
    pnl_cols = [c for c in ("pnl_local", "pnl_usd") if c in cols] or ["pnl_usd"]
    style_data_conditional = rk.sign_styles(pnl_cols, bold=True,
                                            nil={"color": "var(--muted)", "fontStyle": "italic"})
    return data_records, tooltip_data, style_data_conditional


def detail_table(df: pd.DataFrame, table_id: str = DATATABLE_ID,
                  display_columns: Optional[list] = None,
                  column_labels: Optional[dict] = None) -> dash_table.DataTable:
    """Build the trade table. `display_columns`/`column_labels` default to the FX/Total
    layout; the Futures sub-tab passes its own (Contract/Contracts/Expiry/Settlement
    instead of Pair/Amount/Value date/Live rate). Filtering is the dropdown bar built by
    `_filter_bar` (see module docstring); sorting is native (ui.tabs.ranking): the table
    opens in `_sorted_scope_df`'s order and any header click re-ranks it."""
    display_columns = display_columns if display_columns is not None else _DISPLAY_COLUMNS
    column_labels = column_labels if column_labels is not None else _COLUMN_LABELS
    cols = [c for c in display_columns if c in df.columns]
    data_records, tooltip_data, style_data_conditional = _format_rows(df, display_columns, column_labels)
    return dash_table.DataTable(
        id=table_id,
        columns=[rk.numeric(column_labels.get(c, c.replace("_", " ").title()), c, _COLUMN_FORMATS[c])
                 if c in _COLUMN_FORMATS else rk.text(column_labels.get(c, c.replace("_", " ").title()), c)
                 for c in cols],
        data=data_records,
        tooltip_data=tooltip_data,
        **rk.sortable(table_id),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
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
    label_fn = {"status": _fmt_status, "product": _fmt_product}.get(col, lambda v: v)
    values = sorted({v for v in df[col].tolist() if v not in (None, "")})
    return [{"label": label_fn(v) or "(blank)", "value": v} for v in values]


def _filter_bar(df: pd.DataFrame, table_id: str, display_columns: list, column_labels: dict) -> html.Div:
    """One multi-select dropdown per categorical column present in this scope, e.g.
    click Pair, pick USDJPY, see only USDJPY rows -- and the P&L strip above narrows to
    match (it listens on the same table's `derived_virtual_data`). Replaces the native
    filter row (module docstring). Built from the FULL scope df so every dropdown lists
    every value that scope ever has, not just what a prior selection left visible."""
    cols = [c for c in FILTERABLE_COLS if c in display_columns and c in df.columns]
    if not cols:
        return html.Div()
    children = [html.Div(className="blotter-filter", children=[
        html.Label(column_labels.get(c, c.replace("_", " ").title())),
        dcc.Dropdown(id=f"{table_id}-filter-{c}", options=_filter_options(df, c, column_labels),
                     value=[], multi=True, placeholder="All", className="blotter-filter-dropdown"),
    ]) for c in cols]
    children.append(html.Button("Clear filters", id=f"{table_id}-filter-clear", n_clicks=0,
                                className="btn btn--ghost"))
    return html.Div(className="blotter-filter-bar", children=children)


def render_headline_strip(headline: dict, hidden: tuple = (), caption: str = "") -> html.Div:
    """The Excel Portfolio header's card row (LTD/Daily/Trades/Trading/LTD-1
    daily/LTD-1/LTD-2/Trading T-1/5d/MTD/YTD), bold green/red by sign, reference date
    underneath, "n/a" muted italic with the reason as a tooltip when unavailable.

    `hidden` / `caption` (2026-09-18, used by the Options strip only -- `options_strip`):
    the keys of `HEADLINE_ORDER` to leave out, and one plain line shown under the row in
    their place. Both empty (every other scope): the row is exactly what it always was.

    No longer takes an optional caption (removed 2026-09-17, user decision "no bnp
    fall back"): its only use was the "n of m rows priced from BNP file rates, not
    Bloomberg" fallback badge, which no longer applies now that a row with no official
    mark simply shows "n/a" with its `reason` as a tooltip -- there is no second,
    non-Bloomberg source to badge any more.

    An AVAILABLE entry may also carry `excluded_summary`/`excluded_detail`
    (2026-09-17, live-Bloomberg-PC partial-pricing follow-up, matching
    `ui/tabs/header.py::_pnl_card`'s same-day equivalent -- see
    `ui.tabs.blotter_pricing`'s module docstring): a short, always-visible caption
    ("excludes N of M trades unpriced") under the value, with the per-product/reason
    breakdown as its tooltip -- the value itself is a real sum over the row-scoped
    set's PRICED rows, not a placeholder, so it keeps its normal sign colouring; only
    the caption differs from a fully-priced card."""
    cards = []
    for key in HEADLINE_ORDER:
        if key in hidden:
            continue
        entry = headline.get(key, {})
        available = entry.get("available")
        value = entry.get("value")
        if key == "trades":
            value_div = html.Div(f"{int(value)}" if value == value else "n/a",
                                  className="card-value")
        elif not available:
            reason = entry.get("reason", "")
            value_div = html.Div("n/a", className="card-value card-value--muted",
                                  title=reason or "unavailable")
        else:
            colour = "var(--pos)" if value >= 0 else "var(--neg)"
            value_div = html.Div(format_cell(value), className="card-value",
                                  style={"color": colour})
        card_children = [
            html.Div(HEADLINE_TITLES[key], className="card-label"),
            value_div,
            html.Small(entry.get("ref_date", ""), className="card-note"),
        ]
        ref_note = entry.get("ref_note")
        if ref_note and available:
            # Measured from an earlier close than the period's own reference date
            # (engine.pnl.reference, 2026-09-21): visible, the skipped dates' reasons on hover.
            card_children.append(html.Div(
                ref_note, className="card-note",
                style={"fontStyle": "italic", "color": "var(--muted)"},
                title=entry.get("ref_note_detail", "")))
        excluded_summary = entry.get("excluded_summary")
        if excluded_summary:
            card_children.append(html.Div(
                excluded_summary, className="card-note",
                style={"fontStyle": "italic", "color": "var(--muted)"},
                title=entry.get("excluded_detail", "")))
        cards.append(html.Div(className="card", children=card_children))
    children = [html.Div(cards, className="cards")]
    if caption:
        children.append(html.P(caption, className="section-kicker", style={"fontStyle": "italic"}))
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
    that class's figure unavailable with the reason, never zero."""
    from ui.tabs.blotter_pricing import row_scoped_period_pnl
    if df.empty:
        return []
    classes = df["product"].map(ASSET_CLASS_OF).fillna("Other")
    rows = []
    for cls in [c for c in ASSET_CLASS_ORDER if c in set(classes)] + (["Other"] if "Other" in set(classes) else []):
        ids = df.loc[classes == cls, "trade_id"].tolist()
        rows.append({"asset_class": cls, "trades": len(ids), **row_scoped_period_pnl(conn, as_of, ids)})
    all_ids = df["trade_id"].tolist()
    rows.append({"asset_class": "Total", "trades": len(all_ids), **row_scoped_period_pnl(conn, as_of, all_ids)})
    return rows


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
        for key in _ASSET_PERIODS:
            entry = r.get(key, {})
            if entry.get("available"):
                rec[key] = rk.value(entry["value"])
                summary = entry.get("excluded_summary")
                if summary:
                    detail = entry.get("excluded_detail", "")
                    tip[key] = {"value": f"{summary}. {detail}" if detail else summary, "type": "text"}
                ref_note = entry.get("ref_note")
                if ref_note:  # measured from an earlier close (engine.pnl.reference)
                    rest = tip.get(key, {}).get("value", "")
                    tip[key] = {"value": f"{ref_note}. {rest}" if rest else ref_note, "type": "text"}
            else:
                rec[key] = None
                tip[key] = {"value": entry.get("reason", "") or "unavailable", "type": "text"}
        records.append(rec)
        tooltips.append(tip)
    is_total = [rec["asset_class"] == "Total" for rec in records]
    body = [rec for rec, t in zip(records, is_total) if not t]
    body_tips = [tip for tip, t in zip(tooltips, is_total) if not t]
    footer = [rec for rec, t in zip(records, is_total) if t]
    footer_tips = [tip for tip, t in zip(tooltips, is_total) if t]
    table = dash_table.DataTable(
        id=ASSET_TABLE_ID,
        columns=[rk.text(_ASSET_LABELS["asset_class"], "asset_class"), rk.numeric(_ASSET_LABELS["trades"], "trades", rk.count())]
                + [rk.numeric(_ASSET_LABELS[c], c, rk.amount(nully="n/a")) for c in _ASSET_PERIODS],
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
        html.H4("P&L by asset class"),
        rk.with_footer(table, footer, footer_style=total_style, footer_tooltips=footer_tips)])


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


def positions_rows(conn: sqlite3.Connection, as_of: str) -> tuple:
    """(records, tooltips) of the Total book's Positions table (user, 2026-09-22: "I want
    to see the delta by currency ... this is the key table of the blotter"): the figures of
    `engine.ladder.positions.book_positions`. First one row per currency with delta (the
    Ladder's risk table: rate as quoted on its plain pair, local delta, USD delta; a metal
    row says it is not in the FX net), then the FX net and gross, then the FX options' delta
    by pair (already inside the currency rows). The equity-index line and the rates DV01
    left on 2026-09-24 with the macro trader's products: `book_positions` may still return
    its `equity_index` and `rates` blocks until the book-positions lane drops them, and
    they are not read here. Commodity futures get their own lines in Phase 3; until then
    the Futures sub-tab and the Curve tab show them. Columns: Position, Rate, Delta
    (local), Delta (USD), Detail. A figure that could not be computed reads "n/a" with its
    reason in the cell's tooltip. Cells are numbers (ui.tabs.ranking): a missing one is None."""
    from engine.ladder.positions import book_positions
    pos = book_positions(conn, as_of)
    records, tips = [], []

    def add(label, units, usd, detail="", reason="", indent=False, rate="", kind=""):
        rec = {"position": ("    " if indent else "") + label, "rate": rate, "units": units, "usd": usd,
               "detail": detail, "kind": kind}
        tip = {}
        if reason:
            for col in ("units", "usd"):
                if rec[col] is None:
                    tip[col] = {"value": reason, "type": "text"}
        records.append(rec)
        tips.append(tip)

    fx = pos["fx"]
    for c in fx.get("by_ccy", []):
        detail = "metal, not in the FX net" if c["metal"] else ("" if c["ccy"] != "USD" else "USD legs")
        add(f"{c['ccy']}", _pos_num(c["local_delta"]), _pos_num(c["usd_delta"]), detail, c["reason"],
            rate=(f"{_pair_label(c)} {_quoted(c['quoted'])}".strip() if c["ccy"] != "USD" else ""), kind="ccy")
    if not fx.get("by_ccy") and fx.get("reason"):
        add("Delta by currency", "", None, "", fx["reason"], kind="ccy")
    add("FX net USD delta (+ = long USD)", "", _pos_num(fx["net_usd"]), "the header's Net USD; FX options' delta included", fx["reason"], kind="total")
    add("FX gross USD delta", "", _pos_num(fx["gross_usd"]), "sum of |per-pair USD delta|", fx["reason"], kind="total")

    opt = pos["fx_options"]
    for pair, usd in sorted(opt["by_pair"].items()):
        add(f"{pair} options delta", "", _pos_num(usd), "inside the currency rows above", kind="option")
    add("FX options delta (USD)", "", _pos_num(opt["usd_delta"]),
        f"{opt['options']} open option(s), part of the FX net above" + (f"; {len(opt['missing'])} not converted" if opt["missing"] else ""),
        opt["reason"] or (opt["missing"][0] if opt["missing"] else ""), kind="total")
    return records, tips


def positions_table(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The Total book's Positions block (`positions_rows`), above the P&L by asset class.
    Ranked (ui.tabs.ranking): a click on Delta (USD) puts the largest risk first; the
    section totals (FX net and gross, FX options) are the pinned footer, in their own
    order, never ranked with the lines."""
    records, tips = positions_rows(conn, as_of)
    body = [(r, t) for r, t in zip(records, tips) if r["kind"] != "total"]
    footer = [(r, t) for r, t in zip(records, tips) if r["kind"] == "total"]
    table = dash_table.DataTable(
        id=POSITIONS_TABLE_ID,
        columns=[rk.text("Position", "position"), rk.text("Rate", "rate"),
                 rk.numeric("Delta (local)", "units", rk.amount(2, nully="n/a", trim=True)),
                 rk.numeric("Delta (USD)", "usd", rk.amount(nully="n/a")), rk.text("", "detail")],
        data=[r for r, _ in body], tooltip_data=[t for _, t in body],
        **rk.sortable(POSITIONS_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "padding": "4px 8px", "whiteSpace": "pre"},
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("position", "rate", "detail")],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["units"], pos="inherit")
                               + rk.sign_styles(["usd"], bold=True, nil={"color": "var(--muted)", "fontStyle": "italic"}),
    )
    total_style = [{"if": {"filter_query": "{kind} = 'total'"}, "fontWeight": "700", "borderTop": "1px solid var(--muted)"}]
    return html.Div(className="section", children=[
        html.H4("Positions"),
        html.P("Delta by currency at the day's official spot, FX options included. Futures positions are on the "
               "Futures sub-tab and the Curve tab. A figure with no mark reads n/a with the reason on hover.",
               className="section-kicker"),
        rk.with_footer(table, [r for r, _ in footer], footer_style=total_style, footer_tooltips=[t for _, t in footer])])


def render_placeholder_strip(message: str) -> html.Div:
    return html.Div(html.P(f"Unavailable ({message})", className="section-kicker",
                            style={"fontStyle": "italic"}))


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


def row_expand_panel(conn: sqlite3.Connection, trade_id: str, row: pd.Series) -> html.Div:
    """Legs (from `trade_legs`) + the marks-used columns already on the value_book row."""
    legs = pd.read_sql_query(
        "SELECT leg_no, leg_type, ccy, amount, start_date, settle_date, rate, settles_cash "
        "FROM trade_legs WHERE trade_id = ? ORDER BY leg_no", conn, params=(trade_id,))
    marks_used = html.P(
        f"Mark: {row.get('mark')} ({row.get('mark_source')}, dated {row.get('mark_date')}) · "
        f"Spot: {row.get('spot')} ({row.get('spot_source')})",
        className="section-kicker",
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


def scope_columns(scope: str) -> tuple:
    """(display_columns, column_labels) for a sub-tab -- Futures has its own layout,
    every other scope shares the FX/Total one. Factored out so both `scope_layout` and
    the filter-dropdown callback build the identical column set."""
    if scope == "futures":
        return _FUTURES_DISPLAY_COLUMNS, _FUTURES_COLUMN_LABELS
    return _DISPLAY_COLUMNS, _COLUMN_LABELS


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
    if scope == "futures":
        df = add_future_fields(conn, df)
    return _sorted_scope_df(df)


def add_future_fields(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    """The Futures table's two descriptive columns, looked up, never computed: `pnl_ccy`, the
    currency `value_book`'s `pnl_local` is in (the instrument's quote currency, CLAUDE.md
    "P&L conventions -> Futures"), and `exchange`, the contract root's exchange in the
    contract master (`data.contracts.get_root` on the instrument's `base_ccy`, which is the
    root id, 'SHFE:CU'). A contract the master does not know shows a blank exchange; the
    currency is always the instrument's own."""
    from data.contracts import get_root

    df = df.copy()
    info = {}
    for instrument_id in df["instrument_id"].unique():
        row = conn.execute("SELECT base_ccy, quote_ccy FROM instruments WHERE instrument_id = ?",
                           (instrument_id,)).fetchone()
        base, quote = (row[0], row[1]) if row else ("", "")
        try:
            exchange = get_root(base).exchange if base else ""
        except KeyError:
            exchange = ""
        info[instrument_id] = (exchange, quote or "")
    df["exchange"] = df["instrument_id"].map(lambda i: info.get(i, ("", ""))[0])
    df["pnl_ccy"] = df["instrument_id"].map(lambda i: info.get(i, ("", ""))[1])
    return df


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
                    style={"border": "1px solid var(--neg)", "borderRadius": "6px", "padding": "8px 12px",
                           "margin": "0 0 10px", "background": "rgba(178, 59, 59, 0.08)"},
                    children=[html.B("Stored values that are not numbers. "), html.Span(found + ".")])


def missing_terms_notice(conn: sqlite3.Connection) -> Optional[html.Div]:
    """A red banner naming every option that has no strike on file (so cannot be
    priced) and where to enter it -- shown at the top of every Blotter sub-tab, not
    only under Options, because a blank P&L anywhere else is otherwise unexplained
    (user request 2026-09-18). None when every option has its terms."""
    from ui.tabs import options as options_ui
    try:
        missing = [i["instrument_id"] for i in options_ui.option_instruments(conn) if not i["strike"]]
    except Exception:  # never let the notice itself blank a tab
        logging.getLogger(__name__).exception("missing_terms_notice failed")
        return None
    if not missing:
        return None
    return html.Div(className="notice notice--terms", role="alert",
                    style={"border": "1px solid var(--neg)", "borderRadius": "6px", "padding": "8px 12px",
                           "margin": "0 0 10px", "background": "rgba(178, 59, 59, 0.08)"},
                    children=[
                        html.B(f"{len(missing)} option{'s' if len(missing) != 1 else ''} cannot be priced: no strike on file. "),
                        html.Span(", ".join(missing) + ". "),
                        # The Options table's Strike, Type and Payoff cells are editable
                        # (`ui.tabs.options.EDITABLE_COLUMNS`), so that comes first; the form
                        # and a re-upload are the alternatives. One plain sentence each.
                        html.Span("Type the strike straight into the Strike cell under Options, "
                                  "and set Payoff to Digital where it is one. "),
                        html.Span("You can also enter it under Manual entry ▸ Option terms. "),
                        html.Span("Or re-upload a blotter export that includes a Strike column."),
                    ])


def blotter_notices(conn: sqlite3.Connection) -> list:
    """The Blotter's banners, in display order, each only if it has something to say:
    stored values that are not numbers, then options with no strike on file. [] is the
    normal case. One builder for the always-present container the page refreshes in place
    (`NOTICES_ID`, `_refresh_notices`) and for `scope_layout`'s direct callers."""
    return [n for n in (
        _safe_section("Stored values notice", lambda: bad_values_notice(conn)),
        _safe_section("Option terms notice", lambda: missing_terms_notice(conn)),
    ) if n is not None]


def scope_layout(scope: str, conn: sqlite3.Connection, as_of: str, with_notices: bool = True) -> html.Div:
    """One sub-tab's content, with the missing-option-terms banner and the
    stored-values-that-are-not-numbers banner (each only if it has something to say) on
    top of the scope body built by `_scope_layout_body`. With neither, the body is
    returned as it is -- no extra nesting.

    `with_notices=False` is how the page itself calls it (`_update`, 2026-09-18): there
    the banners live in `NOTICES_ID`, above the content and outside it, so they follow
    every revision in place -- a saved strike leaves the banner at once -- instead of
    being frozen into the sub-tab until its next rebuild (and never, under Manual entry,
    which is not rebuilt on a revision at all)."""
    body = _scope_layout_body(scope, conn, as_of)
    notices = blotter_notices(conn) if with_notices else []
    if not notices:
        return body
    return html.Div([*notices, body])


def _scope_layout_body(scope: str, conn: sqlite3.Connection, as_of: str) -> html.Div:
    """Build one sub-tab's content: headline strip (initial, whole-scope) + filter
    dropdown bar + trade table + an (empty until a row is clicked) detail container
    below it. FX / Options / Manual entry delegate to their own modules.

    Every sub-section (P&L strip / delegated FX-Options table / the Total book's
    asset-class breakdown / the filter bar + trade table) is wrapped in `_safe_section`
    so a failure in one doesn't blank the others -- see that helper's docstring."""
    strip_id = f"blotter-strip-{scope}"
    table_id = f"blotter-datatable-{scope}"
    detail_id = f"{DETAIL_PANEL_ID}-{scope}-detail"
    display_columns, column_labels = scope_columns(scope)

    if scope == "options":
        def _strip():
            # Only the cards that have a value, and one caption for the ones that are
            # waiting for an earlier close's option marks (`options_strip`).
            return html.Div(id=strip_id, children=scope_strip(scope, conn, as_of))
        return html.Div([
            _safe_section("P&L strip", _strip, conn),
            _safe_section("Options", lambda: options_ui.build_layout(conn, as_of), conn),
        ])

    if scope == "fx":
        return _safe_section("FX", lambda: blotter_fx_ui.build_layout(conn, as_of), conn)

    if scope == "manual":
        return _safe_section("Manual entry", lambda: manual_entry_ui.build_layout(conn))

    if scope in PLACEHOLDER_SCOPES:
        def _placeholder():
            empty = pd.DataFrame(columns=_DISPLAY_COLUMNS)
            return html.Div([
                html.Div(id=strip_id, children=render_placeholder_strip(PLACEHOLDER_SCOPES[scope])),
                detail_table(empty, table_id=table_id),
                html.Div(id=detail_id),
            ])
        return _safe_section(SCOPE_LABELS.get(scope, scope), _placeholder)

    # "total"/"futures": the shared priced_value_book pipeline (module docstring calls
    # this "pricing"). A failure pulling `df` itself blocks everything downstream (the
    # strip, the asset-class table and the trade table all need it), so that one step
    # is not wrapped by `_safe_section` -- there is nothing partial to preserve -- but
    # still degrades to one inline card rather than propagating past `_update`.
    try:
        df = scope_df(conn, scope, as_of)
    except Exception as exc:  # noqa: BLE001 -- see _safe_section's docstring
        import logging
        logging.getLogger(__name__).exception("Blotter section %r failed to render", "Pricing")
        return html.Div([_error_card("Pricing", exc, conn), html.Div(id=detail_id)])

    if scope == "futures" and df.empty:
        empty = pd.DataFrame(columns=display_columns)
        return html.Div([
            html.Div(id=strip_id, children=render_placeholder_strip(FUTURES_NO_TRADES_REASON)),
            detail_table(empty, table_id=table_id, display_columns=display_columns,
                         column_labels=column_labels),
            html.Div(id=detail_id),
        ])

    def _strip():
        trade_ids = df["trade_id"].tolist() if not df.empty else []
        headline = row_scoped_headline(conn, as_of, trade_ids)
        return html.Div(id=strip_id, children=render_headline_strip(headline))

    body = [_safe_section("P&L strip", _strip, conn)]
    if scope == "total" and not df.empty:
        body.append(_safe_section("Positions", lambda: positions_table(conn, as_of), conn))
        body.append(_safe_section("P&L by asset class", lambda: asset_class_pnl_table(conn, as_of, df), conn))
    if df.empty:
        body.append(message_box("No trades for this as-of date in this scope."))
        body.append(html.Div(id=detail_id))
    else:
        body.append(_safe_section(
            "Filter bar", lambda: _filter_bar(df, table_id, display_columns, column_labels)))
        body.append(_safe_section(
            "Trade table",
            lambda: detail_table(df, table_id=table_id, display_columns=display_columns,
                                  column_labels=column_labels), conn))
        body.append(html.Div(id=detail_id))
    return html.Div(body)


def bundles_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    from data.ingest.themes import list_bundles
    try:
        from engine.pnl.ledger import period_pnl_by
        grouped = period_pnl_by(conn, as_of, "theme")
    except Exception:
        grouped = {}
    bundles = list_bundles(conn)
    return html.Div([
        bundles_ui.bundle_list_table(bundles, grouped),
        bundles_ui.create_form(),
        bundles_ui.add_remove_form(),
        dcc.Store(id=bundles_ui.BUNDLE_SELECTED_ID),
        dcc.Store(id=bundles_ui.BUNDLE_REVISION_ID),
        html.Div(id="blotter-bundle-detail-container"),
    ])


def _today_default(default_date: Optional[str]) -> Optional[str]:
    """As-of defaults to today (New York), like the Ladder tab, so the strip and the
    list show the book as it stands now rather than at the last upload."""
    try:
        from ui.tabs.cash_ladder import today_ny
        return today_ny()
    except Exception:
        return default_date


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Title row matches the Ladder tab's (coordinator instruction 2026-09-15): a single
    row, "Blotter" on the left, the full-text date heading + date picker + "Today"
    button on the right, no card, no kicker -- same class names
    (`ladder-title-row(-heading|-right)`) as `ui.tabs.cash_ladder.build_layout` so one
    stylesheet rule styles both tabs' title rows."""
    from ui.tabs.cash_ladder import heading_date_text

    resolved_date = _today_default(default_date)
    return html.Div(className="blotter", children=[
        html.Div(id=TOOLBAR_ID, className="ladder-title-row", children=[
            html.H3("Blotter", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                html.H4(heading_date_text(resolved_date), id=TITLE_ID, className="section-title"),
                build_date_picker(DATE_PICKER_ID, default_date=resolved_date),
                html.Button("Today", id=TODAY_BUTTON_ID, n_clicks=0, className="btn"),
            ]),
        ]),
        dcc.Tabs(id=SUBTABS_ID, value=SCOPE_ORDER[0], className="subtabs", children=[
            dcc.Tab(label=SCOPE_LABELS[s], value=s, className="subtab",
                    selected_className="subtab--selected")
            for s in SCOPE_ORDER
        ]),
        # Banners (missing option terms, stored values that are not numbers): always in
        # the page and refreshed in place on every revision (`_refresh_notices`), not
        # frozen into a sub-tab's content until its next rebuild.
        html.Div(id=NOTICES_ID),
        # The trade-set signature the content below was built from (`_update`).
        dcc.Store(id=BUILT_TRADE_SET_ID),
        html.Div(id=CONTENT_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Same convention as `ui.tabs.cash_ladder.register_callbacks`."""

    @app.callback(Output(TITLE_ID, "children"), Input(DATE_PICKER_ID, "date"))
    def _update_title(as_of_date):
        from ui.tabs.cash_ladder import heading_date_text
        return heading_date_text(as_of_date)

    @app.callback(
        Output(DATE_PICKER_ID, "date", allow_duplicate=True),
        Input(TODAY_BUTTON_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _jump_to_today(_n_clicks):
        from ui.tabs.cash_ladder import today_ny
        return today_ny()

    @app.callback(
        Output(CONTENT_ID, "children"),
        Output(BUILT_TRADE_SET_ID, "data"),
        Input(DATE_PICKER_ID, "date"),
        Input(SUBTABS_ID, "value"),
        Input(BOOK_REVISION_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        State(BUILT_TRADE_SET_ID, "data"),
    )
    def _update(as_of_date, scope, _book_rev=None, _data_rev=None, built_trade_set=None):
        """`(content, trade-set signature it was built from)`; `(no_update, no_update)`
        when what is on screen stays.

        No browser reload (ui/revision.py, 2026-09-18). A changed TRADE SET (an upload, a
        manual trade booked or deleted) rebuilds whatever sub-tab is showing. A marks-only
        change rebuilds the views that are one static block (FX, Bundles); the Total book
        and Futures tables instead refresh their rows in place through `_apply_filters`,
        so a Bloomberg pull never resets the user's filters, sort or page, and Options
        refreshes its own table and its strip in place (`_SELF_REFRESHING_SCOPES`). The
        Manual entry form is never rebuilt under the user's hands.

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
        if not as_of_date:
            return message_box("No as-of date available."), no_update
        scope = scope or SCOPE_ORDER[0]
        try:
            triggered = {t["prop_id"].split(".")[0] for t in (ctx.triggered or [])}
        except MissingCallbackContextException:  # called directly, not by Dash: just render
            triggered = set()
        db_path = get_db_path()
        trade_set = None
        if triggered and triggered <= {DATA_REVISION_ID, BOOK_REVISION_ID}:
            if scope == "manual":
                return no_update, no_update
            book_moved = False
            if BOOK_REVISION_ID in triggered:
                trade_set = trade_set_signature(db_path)
                book_moved = not trade_set or trade_set != built_trade_set
            if not book_moved and scope not in _MARKS_REBUILD_SCOPES:
                return no_update, no_update
        content = _render_content(db_path, as_of_date, scope)
        if trade_set is None:
            trade_set = trade_set_signature(db_path)
        return content, (trade_set or no_update)

    def _render_content(db_path, as_of_date, scope):
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc}).")
        try:
            with pricing_snapshot(conn, f"Blotter {scope}"):  # one view of the marks per render
                if scope == "bundles":
                    return _safe_section("Bundles", lambda: bundles_layout(conn, as_of_date), conn)
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
            State(DATE_PICKER_ID, "date"),
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
                    if _scope == "futures" and not trade_ids:
                        df, _, _ = priced_value_book(conn, as_of_date)
                        if df.empty or df[df["product"] == "FUTURE"].empty:
                            return render_placeholder_strip(FUTURES_NO_TRADES_REASON)
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
            State(DATE_PICKER_ID, "date"),
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
        filter_cols = [c for c in FILTERABLE_COLS if c in display_columns]
        if not filter_cols:
            return
        filter_ids = [f"{table_id}-filter-{c}" for c in filter_cols]

        def _apply_filters(*args, _scope=scope, _filter_cols=filter_cols,
                            _display_columns=display_columns, _column_labels=column_labels):
            *values, _data_rev, as_of_date = args
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
            data_records, tooltip_data, _ = _format_rows(df, _display_columns, _column_labels)
            return data_records, tooltip_data

        app.callback(
            Output(table_id, "data"),
            Output(table_id, "tooltip_data"),
            *[Input(fid, "value") for fid in filter_ids],
            Input(DATA_REVISION_ID, "data"),  # new marks: refresh the rows, keep the filters
            State(DATE_PICKER_ID, "date"),
            prevent_initial_call=True,
        )(_apply_filters)

        def _clear_filters(_n_clicks):
            return [[] for _ in filter_ids]

        app.callback(
            *[Output(fid, "value") for fid in filter_ids],
            Input(f"{table_id}-filter-clear", "n_clicks"),
            prevent_initial_call=True,
        )(_clear_filters)

    options_ui.register_callbacks(app, get_db_path)
    manual_entry_ui.register_callbacks(app, get_db_path)
    # FX (2026-09-21): "P&L by currency, rows shown" follows the FX trade table's native
    # column filter; that one callback is `ui.tabs.blotter_fx`' own.
    blotter_fx_ui.register_callbacks(app, get_db_path)

    def _register_strip_refresh(scope: str) -> None:
        @app.callback(
            Output(f"blotter-strip-{scope}", "children"),
            Input(DATA_REVISION_ID, "data"),
            State(DATE_PICKER_ID, "date"),
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
        is showing, Manual entry included, without rebuilding anything. A database that
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

    # "fx" (ui.tabs.blotter_fx, rebuilt 2026-09-17),
    # "options" (ui.tabs.options, Phase 8) and "manual" (ui.tabs.manual_entry) have no
    # strip/detail/filter of their own (module docstring above) -- none is a
    # priced_value_book-shaped table, so the generic callbacks below (which assume
    # trade_id/mark/pnl_usd rows) don't apply.
    for _scope in SCOPE_ORDER:
        if _scope not in _NON_TABLE_SCOPES:
            _register_strip_callback(_scope)
            _register_detail_callback(_scope)
            _register_filter_callback(_scope)

    @app.callback(
        Output(bundles_ui.BUNDLE_STATUS_ID, "children"),
        Output(bundles_ui.BUNDLE_REVISION_ID, "data"),
        Input(bundles_ui.BUNDLE_CREATE_BUTTON_ID, "n_clicks"),
        State(bundles_ui.BUNDLE_NAME_INPUT_ID, "value"),
        State(bundles_ui.BUNDLE_DESC_INPUT_ID, "value"),
        State(bundles_ui.BUNDLE_PAIRS_INPUT_ID, "value"),
        prevent_initial_call=True,
    )
    def _create_bundle(n_clicks, name, description, pairs_csv):
        import time
        from data.ingest.themes import add_pair_to_bundle, create_bundle
        if not name or not name.strip():
            return "Enter a bundle name", None
        db_path = get_db_path()
        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.OperationalError as exc:
            return f"Database not available ({exc}).", None
        try:
            create_bundle(conn, name.strip(), description or "")
            pairs = [p.strip() for p in (pairs_csv or "").split(",") if p.strip()]
            failed = []
            for pair in pairs:
                try:
                    add_pair_to_bundle(conn, name.strip(), pair)
                except ValueError:
                    failed.append(pair)
            msg = f"Created bundle {name!r}"
            if failed:
                msg += f"; unknown pairs skipped: {', '.join(failed)}"
            return msg, str(time.time())
        finally:
            conn.close()

    @app.callback(
        Output(bundles_ui.BUNDLE_SELECTED_ID, "data"),
        Input(bundles_ui.BUNDLE_LIST_ID, "derived_virtual_selected_rows"),
        State(bundles_ui.BUNDLE_LIST_ID, "derived_virtual_data"),
        prevent_initial_call=True,
    )
    def _select_bundle(selected_rows, rows):
        if not selected_rows or not rows:
            return None
        return rows[selected_rows[0]].get("name")

    @app.callback(
        Output("blotter-bundle-detail-container", "children"),
        Output(bundles_ui.BUNDLE_STATUS_ID, "children", allow_duplicate=True),
        Input(bundles_ui.BUNDLE_SELECTED_ID, "data"),
        Input(bundles_ui.BUNDLE_REVISION_ID, "data"),
        Input(bundles_ui.BUNDLE_ADD_PAIR_BUTTON_ID, "n_clicks"),
        Input(bundles_ui.BUNDLE_REMOVE_PAIR_BUTTON_ID, "n_clicks"),
        State(bundles_ui.BUNDLE_ADD_PAIR_INPUT_ID, "value"),
        State(bundles_ui.BUNDLE_REMOVE_PAIR_INPUT_ID, "value"),
        State(DATE_PICKER_ID, "date"),
        prevent_initial_call=True,
    )
    def _bundle_detail(selected, _rev, _add_clicks, _remove_clicks, add_pair, remove_pair, as_of_date):
        from dash import ctx

        from data.ingest.themes import add_pair_to_bundle, bundle_pairs, remove_pair_from_bundle
        if not selected:
            return message_box("Select a bundle above to see its pairs."), ""
        db_path = get_db_path()
        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc})."), f"Database not available ({exc})."
        status = ""
        try:
            trigger = ctx.triggered_id if hasattr(ctx, "triggered_id") else None
            if trigger == bundles_ui.BUNDLE_ADD_PAIR_BUTTON_ID and add_pair:
                try:
                    add_pair_to_bundle(conn, selected, add_pair.strip())
                    status = f"Added {add_pair.strip()} to {selected}"
                except ValueError as exc:
                    status = str(exc)
            elif trigger == bundles_ui.BUNDLE_REMOVE_PAIR_BUTTON_ID and remove_pair:
                remove_pair_from_bundle(conn, selected, remove_pair.strip())
                status = f"Removed {remove_pair.strip()} from {selected}"
            pairs = bundle_pairs(conn, selected)
            children = [bundles_ui.bundle_detail_table(pairs)]
            if as_of_date:
                df, _, _ = priced_value_book(conn, as_of_date)
                if not df.empty:
                    df = df[df["theme"] == selected]
                    df = add_row_display_fields(conn, df, as_of_date)
                children.append(html.H5(f"Trades in {selected}"))
                children.append(detail_table(df, table_id="blotter-bundle-trades"))
            return html.Div(children), status
        finally:
            conn.close()
