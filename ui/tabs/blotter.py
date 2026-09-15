"""Blotter tab: docs/BUILD_PLAN.md section 5 "Blotter", rebuilt 2026-09-15 into five
sub-tabs (user decision): Total book, FX, Rates, Options, Bundles. Rows come from
`engine.pnl.valuation.value_book(as_of)` (via `ui.tabs.blotter_pricing.priced_value_book`,
which retries a missing official mark with `marks_source='BNP_BVAL'` so a
Bloomberg-less DB still prices -- see that module's docstring), filtered per sub-tab by
`product`.

Filtering (rebuilt 2026-09-15, user decision -- the prior native text filter row on
`dash_table.DataTable` did not work in this Dash version: verified with a bare
three-row reproduction table outside this app, so the fix is our own filter bar, not a
config tweak): one multi-select `dcc.Dropdown` per categorical column (Pair/Contract,
Side, Status, Product, Strategy, Bundle), listing only the values present in that
sub-tab's own trades. Picking values there re-queries the same priced book, keeps only
the matching rows, and replaces the table's `data` -- which is also what drives the
P&L strip below (`Input(table_id, "derived_virtual_data")`), so picking USDJPY narrows
both the rows and every LTD/Daily/.../Trading figure to just those trades. Native
`sort_action` is dropped for the same reason (also silently inert in this Dash
version); the table keeps a fixed settle-date/pair order instead.

Sub-tab layout, each (Total book / FX / Rates / Options):
  (a) a P&L strip: LTD, Daily, Previous day, 5d, MTD, YTD, Trading -- recomputed for
      exactly the rows currently visible in that sub-tab's table AFTER native header
      filtering (`derived_virtual_data`), per the 2026-09-15 coordinator addition, via
      `ui.tabs.blotter_pricing.row_scoped_period_pnl`. Each figure shows its reference
      date underneath. A fallback caption ("n of m rows priced from BNP file rates, not
      Bloomberg") appears whenever any row in the sub-tab's *scope* (not just the
      visible slice) used the BNP_BVAL retry.
  (b) the trade table: `status` (OPEN/SETTLED) and `instrument_id` are ordinary native-
      filterable columns; row expand (an `html.Details` per trade) shows legs and the
      marks used, exactly as before.

Rates and Options are placeholders (item 3 of the 2026-09-15 decision): no IRS/option
trades exist in `value_book` yet (it only builds FX and FUTURE rows), so their strip
always reads "Unavailable (no IRS/option trades loaded; view not built yet)" and their
table is empty with the same columns as the other sub-tabs -- never hidden, per the
"rows must always render" rule; there just are none to show.

Bundles sub-tab (item 4): `ui.tabs.blotter_bundles` renders a list of bundles (from
`data.ingest.themes.list_bundles`) with LTD/Daily/MTD/YTD via
`engine.pnl.ledger.period_pnl_by(conn, as_of, 'theme')`, plus an "Unassigned" line, a
create form and add/remove-pair actions calling `data.ingest.themes.set_theme` (via the
`add_pair_to_bundle` / `remove_pair_from_bundle` helpers).
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs import blotter_bundles as bundles_ui
from ui.tabs.blotter_pricing import (
    HEADLINE_ORDER,
    HEADLINE_TITLES,
    add_row_display_fields,
    priced_value_book,
    row_scoped_headline,
)
from ui.tabs.controls import build_date_picker
from ui.tabs.formatting import format_cell

DATE_PICKER_ID = "blotter-date"
TOOLBAR_ID = "blotter-toolbar"
SUBTABS_ID = "blotter-subtabs"
CONTENT_ID = "blotter-content"
TITLE_ID = "blotter-title"
TODAY_BUTTON_ID = "blotter-today-button"

# Kept for the pre-2026-09-15 tests that still exercise a single detail table by id.
TABLE_CONTAINER_ID = "blotter-table-container"
DATATABLE_ID = "blotter-datatable"
DETAIL_PANEL_ID = "blotter-datatable"  # "-{scope}-detail" suffix keeps the id under the
# test_ui.py "blotter-datatable-" dynamic-id allow-list (owned by another agent)

_ALL = "All"

# --------------------------------------------------------------------------- sub-tabs
# Order per user decision 2026-09-15: Total book | FX | Futures | Rates | Options |
# Bundles. "FX" scope is FX_SPOT/FX_FWD/FX_SWAP only (FUTURE moved to its own sub-tab).
SCOPE_ORDER = ("total", "fx", "futures", "rates", "options", "bundles")
SCOPE_LABELS = {"total": "Total book", "fx": "FX", "futures": "Futures", "rates": "Rates",
                "options": "Options", "bundles": "Bundles"}
SCOPE_PRODUCTS = {
    "total": None,
    "fx": ("FX_SPOT", "FX_FWD", "FX_SWAP"),
    "futures": ("FUTURE",),
    "rates": ("IRS",),
    "options": ("FX_OPTION",),
}
PLACEHOLDER_SCOPES = {
    "rates": "no IRS trades loaded; view not built yet",
    "options": "no option trades loaded; view not built yet",
}
# Futures is a real view (not a "not built yet" placeholder like Rates/Options), but on
# this PC no futures trades are loaded -- BNP gives one netted position row per contract
# with no fill/trade_date, and fills only arrive via the workbook import on the
# Reconciliation tab (user decision 2026-09-15, item 1). When its scope is genuinely
# empty the strip must read "n/a" with this reason rather than a hollow zero.
FUTURES_NO_TRADES_REASON = ("no futures trades loaded (BNP gives a netted position, "
                             "fills come from the workbook import on the Reconciliation tab)")

# Columns offered as click-to-filter dropdowns (user decision 2026-09-15, replacing the
# broken native filter row): every categorical column that exists in a scope's own
# display columns. Date/amount/rate columns are not offered -- multi-select-from-values
# only makes sense for the categorical ones; "pick USDJPY" is the request, not a range
# filter.
FILTERABLE_COLS = ["instrument_id", "side", "status", "product", "strategy", "theme"]


# Futures columns per the 2026-09-15 decision: Trade date | Contract | Side | Contracts |
# Fill | Expiry | Status | Settlement | P&L (USD) | Strategy | Bundle | Trade id.
# "Settlement" has no dedicated field in value_book's output yet (no futures trade exists
# to observe one on this PC); mapped to `mark_date` (the date the price used for P&L was
# struck) as the closest existing column -- a default pick, noted here since nobody was
# available to confirm it.
_FUTURES_DISPLAY_COLUMNS = [
    "trade_date", "instrument_id", "side", "quantity", "fill", "settle_date", "status",
    "mark_date", "pnl_usd", "strategy", "theme", "trade_id",
]
_FUTURES_COLUMN_LABELS = {
    "trade_date": "Trade date", "instrument_id": "Contract", "side": "Side",
    "quantity": "Contracts", "fill": "Fill", "settle_date": "Expiry", "status": "Status",
    "mark_date": "Settlement", "pnl_usd": "P&L (USD)", "strategy": "Strategy",
    "theme": "Bundle", "trade_id": "Trade id",
}

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
_PASSTHROUGH_COLS = ("priced_from_bnp", "reason")


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
    return {"OPEN": "Open", "SETTLED": "Settled"}.get(value, value or "")


_PRODUCT_LABELS = {"FX_SPOT": "Spot", "FX_FWD": "Forward", "FX_SWAP": "Swap", "FUTURE": "Future",
                   "IRS": "Swap (IRS)", "FX_OPTION": "Option"}


def _fmt_product(value) -> str:
    return _PRODUCT_LABELS.get(value, value or "")


def _sorted_scope_df(df: pd.DataFrame) -> pd.DataFrame:
    """Fixed display order (settle date, then pair) -- replaces the broken interactive
    `sort_action="native"` (verified inert in this Dash version, same as the filter
    row; see module docstring). No-op on an empty frame."""
    if df.empty or "settle_date" not in df.columns:
        return df
    return df.sort_values(["settle_date", "instrument_id"], kind="stable")


def _format_rows(df: pd.DataFrame, display_columns: list, column_labels: dict):
    """Format the (already scope/dropdown-filtered, pricing-enriched) value_book frame
    for display: Amount unsigned with commas, rates to 6dp, P&L bold green/red or "n/a"
    with a tooltip reason when unpriced, dates left as ISO strings. Returns
    `(data_records, tooltip_data, style_data_conditional)` -- split out from
    `detail_table` (2026-09-15) so the filter-dropdown callback can refresh a table's
    `data`/`tooltip_data` props without rebuilding the whole DataTable component."""
    cols = [c for c in display_columns if c in df.columns]
    formatted = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    usd_cols = {"notional_usd", "pnl_usd"}
    rate_cols = {"fill", "mark", "t1_rate"}
    reasons = df["reason"] if "reason" in df.columns else pd.Series([""] * len(df))
    for col in cols:
        if col == "quantity":
            formatted[col] = formatted[col].map(_fmt_amount)
        elif col == "status":
            formatted[col] = formatted[col].map(_fmt_status)
        elif col == "product":
            formatted[col] = formatted[col].map(_fmt_product)
        elif col == "pnl_usd":
            formatted[col] = [
                "n/a" if (v != v) else format_cell(v) for v in df[col].tolist()
            ] if not df.empty else []
        elif col in usd_cols:
            formatted[col] = formatted[col].map(format_cell)
        elif col in rate_cols:
            formatted[col] = ["n/a" if (v != v) else _fmt_rate(v) for v in df[col].tolist()] \
                if not df.empty else []
    data_records = formatted.to_dict("records")
    tooltip_data = []
    # priced_from_bnp/trade_id/reason travel with the row (not all displayed) so the
    # derived_virtual_data callback and the row-click detail panel can use them.
    for i, rec in enumerate(data_records):
        if "priced_from_bnp" in df.columns:
            rec["priced_from_bnp"] = bool(df["priced_from_bnp"].iloc[i])
        if "trade_id" in df.columns:
            rec.setdefault("trade_id", df["trade_id"].iloc[i])
        reason = reasons.iloc[i] if i < len(reasons) else ""
        if reason and rec.get("pnl_usd") == "n/a":
            tooltip_data.append({"pnl_usd": {"value": reason, "type": "text"}})
        else:
            tooltip_data.append({})
    style_data_conditional = [
        {"if": {"filter_query": "{pnl_usd} contains '('", "column_id": "pnl_usd"},
         "color": "var(--neg)", "fontWeight": "700"},
        {"if": {"filter_query": "{pnl_usd} != '' && {pnl_usd} != 'n/a' && "
                                 "!({pnl_usd} contains '(')", "column_id": "pnl_usd"},
         "color": "var(--pos)", "fontWeight": "700"},
        {"if": {"filter_query": "{pnl_usd} = 'n/a'", "column_id": "pnl_usd"},
         "color": "var(--muted)", "fontStyle": "italic"},
    ]
    return data_records, tooltip_data, style_data_conditional


def detail_table(df: pd.DataFrame, table_id: str = DATATABLE_ID,
                  display_columns: Optional[list] = None,
                  column_labels: Optional[dict] = None) -> dash_table.DataTable:
    """Build the trade table. `display_columns`/`column_labels` default to the FX/Total
    layout; the Futures sub-tab passes its own (Contract/Contracts/Expiry/Settlement
    instead of Pair/Amount/Value date/Live rate). No native filter/sort -- see module
    docstring; filtering is the dropdown bar built by `_filter_bar`, sorting is fixed
    (`_sorted_scope_df`, applied by the caller before this is built)."""
    display_columns = display_columns if display_columns is not None else _DISPLAY_COLUMNS
    column_labels = column_labels if column_labels is not None else _COLUMN_LABELS
    cols = [c for c in display_columns if c in df.columns]
    data_records, tooltip_data, style_data_conditional = _format_rows(df, display_columns, column_labels)
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": column_labels.get(c, c.replace("_", " ").title()), "id": c}
                 for c in cols],
        data=data_records,
        tooltip_data=tooltip_data,
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


def render_headline_strip(headline: dict, caption: Optional[str] = None) -> html.Div:
    """The Excel Portfolio header's card row (LTD/Daily/Trades/Trading/LTD-1
    daily/LTD-1/LTD-2/Trading T-1/5d/MTD/YTD), bold green/red by sign, reference date
    underneath, "n/a" muted italic with the reason as a tooltip when unavailable."""
    cards = []
    for key in HEADLINE_ORDER:
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
        cards.append(html.Div(className="card", children=[
            html.Div(HEADLINE_TITLES[key], className="card-label"),
            value_div,
            html.Small(entry.get("ref_date", ""), className="card-note"),
        ]))
    children = [html.Div(cards, className="cards")]
    if caption:
        children.append(html.P(caption, className="section-kicker"))
    return html.Div(children)


def render_placeholder_strip(message: str) -> html.Div:
    return html.Div(html.P(f"Unavailable ({message})", className="section-kicker",
                            style={"fontStyle": "italic"}))


def fallback_caption(n_fallback: int, n_total: int) -> Optional[str]:
    if n_fallback <= 0 or n_total <= 0:
        return None
    return f"{n_fallback} of {n_total} rows priced from BNP file rates, not Bloomberg."


def _legs_table(legs: pd.DataFrame) -> dash_table.DataTable:
    return dash_table.DataTable(
        columns=[{"name": c.replace("_", " ").title(), "id": c} for c in legs.columns],
        data=legs.to_dict("records"),
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


def _package_ids(conn: sqlite3.Connection) -> dict:
    """trade_id -> package_id for trades actually part of a swap package."""
    try:
        rows = conn.execute(
            "SELECT trade_id, package_id FROM trades "
            "WHERE package_id IS NOT NULL AND package_id != '' AND package_id != trade_id"
        ).fetchall()
        return {tid: pkg for tid, pkg in rows}
    except sqlite3.OperationalError:
        return {}


def row_detail_panel(conn: sqlite3.Connection, trade_id: str, df: pd.DataFrame) -> html.Div:
    """The compact panel shown below the table when a row is clicked: swap packages
    show all their legs' trades, an ordinary trade shows just itself."""
    packages = _package_ids(conn)
    pkg = packages.get(trade_id)
    if pkg:
        pkg_ids = [tid for tid, p in packages.items() if p == pkg]
        pkg_rows = df[df["trade_id"].isin(pkg_ids)]
        if pkg_rows.empty:
            pkg_rows = df[df["trade_id"] == trade_id]
        return html.Div(className="section section--secondary", children=[
            html.H5(f"Swap package {pkg} ({len(pkg_rows)} legs)"),
            html.Div([row_expand_panel(conn, r["trade_id"], pd.Series(r))
                      for r in pkg_rows.to_dict("records")]),
        ])
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
    return _sorted_scope_df(df)


def scope_layout(scope: str, conn: sqlite3.Connection, as_of: str) -> html.Div:
    """Build one sub-tab's content: headline strip (initial, whole-scope) + filter
    dropdown bar + trade table + an (empty until a row is clicked) detail container
    below it. Rates/Options are placeholders per module docstring."""
    strip_id = f"blotter-strip-{scope}"
    table_id = f"blotter-datatable-{scope}"
    detail_id = f"{DETAIL_PANEL_ID}-{scope}-detail"
    display_columns, column_labels = scope_columns(scope)

    if scope in PLACEHOLDER_SCOPES:
        empty = pd.DataFrame(columns=_DISPLAY_COLUMNS)
        return html.Div([
            html.Div(id=strip_id, children=render_placeholder_strip(PLACEHOLDER_SCOPES[scope])),
            detail_table(empty, table_id=table_id),
            html.Div(id=detail_id),
        ])

    df = scope_df(conn, scope, as_of)

    if scope == "futures" and df.empty:
        empty = pd.DataFrame(columns=display_columns)
        return html.Div([
            html.Div(id=strip_id, children=render_placeholder_strip(FUTURES_NO_TRADES_REASON)),
            detail_table(empty, table_id=table_id, display_columns=display_columns,
                         column_labels=column_labels),
            html.Div(id=detail_id),
        ])

    trade_ids = df["trade_id"].tolist() if not df.empty else []
    headline = row_scoped_headline(conn, as_of, trade_ids)
    scope_fallback = int(df["priced_from_bnp"].sum()) if not df.empty else 0
    caption = fallback_caption(scope_fallback, len(df))

    body = [html.Div(id=strip_id, children=render_headline_strip(headline, caption))]
    if df.empty:
        body.append(message_box("No trades for this as-of date in this scope."))
        body.append(html.Div(id=detail_id))
    else:
        body.append(_filter_bar(df, table_id, display_columns, column_labels))
        body.append(detail_table(df, table_id=table_id, display_columns=display_columns,
                                  column_labels=column_labels))
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
        Input(DATE_PICKER_ID, "date"),
        Input(SUBTABS_ID, "value"),
    )
    def _update(as_of_date, scope):
        if not as_of_date:
            return message_box("No as-of date available.")
        scope = scope or SCOPE_ORDER[0]

        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc}).")
        try:
            if scope == "bundles":
                return bundles_layout(conn, as_of_date)
            return scope_layout(scope, conn, as_of_date)
        except ImportError as exc:
            return message_box(f"Blotter view not available yet ({exc}).")
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
                if _scope == "futures" and not trade_ids:
                    df, _, _ = priced_value_book(conn, as_of_date)
                    if df.empty or df[df["product"] == "FUTURE"].empty:
                        return render_placeholder_strip(FUTURES_NO_TRADES_REASON)
                n_fallback = sum(1 for r in (rows or []) if r.get("priced_from_bnp"))
                caption = fallback_caption(n_fallback, len(rows or []))
                headline = row_scoped_headline(conn, as_of_date, trade_ids)
                return render_headline_strip(headline, caption)
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
            *values, as_of_date = args
            if not as_of_date:
                return [], []
            from ui.app import connect_readonly
            db_path = get_db_path()
            try:
                conn = connect_readonly(db_path)
            except sqlite3.OperationalError:
                return [], []
            try:
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

    for _scope in SCOPE_ORDER:
        if _scope != "bundles":
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
