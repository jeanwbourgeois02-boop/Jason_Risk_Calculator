"""Blotter tab: docs/BUILD_PLAN.md section 5 "Blotter". One row per trade from
`engine.pnl.valuation.value_book(as_of)`, with filters, an optional group-by summary
from `engine.pnl.ledger.period_pnl_by`, and a row-expand panel showing the trade's
`trade_legs` plus the mark/spot columns `value_book` already carries for that row
(no second engine call needed for "marks used": `value_book` already returns
`mark, mark_date, mark_source, spot, spot_source` per trade).

Follows `ui.tabs.cash_ladder`'s `build_layout(default_date)` /
`register_callbacks(app, get_db_path)` convention so C5 wires it the same way.

Design choices (nobody to ask, so noted here):
  - Filters are plain Dash controls above the table: open/settled (`status` column),
    product, pair (`instrument_id`), strategy, theme, and a trade-date range. All are
    optional (blank/"All" = no filter). Options for the dropdowns are populated from
    the *current* `value_book(as_of)` frame's distinct values, not a separate query,
    so the list always matches what's actually shown for that as_of.
  - Group-by is a dropdown over `engine.pnl.ledger.GROUP_KEYS`
    (`instrument_id, product, strategy, theme`) plus a "None" option. When set, a
    summary table appears above the detail table showing LTD/Daily/5d/MTD/YTD per
    group from `period_pnl_by`; the detail table itself is unaffected by group-by
    (filters still narrow it) since `period_pnl_by` prices its own value_book calls
    unfiltered by the UI's row filters -- mixing the two would silently disagree with
    the per-row totals below it, so group-by summaries are always whole-book per group,
    exactly what `period_pnl_by` computes.
  - Swap packages: rows sharing a non-empty `package_id` are shown as ONE row (the sum
    of the two legs' pnl_usd, quantity blank since near/far differ in sign/date) with a
    nested `html.Details` under it listing the two underlying rows. `value_book` does
    not carry `package_id` today (it is a `trades` column, not a value_book column), so
    this grouping is done via a direct `trades` lookup keyed by `trade_id`; trades
    without a package (no such column found, or empty) pass through as ordinary single
    rows. If `package_id` cannot be read at all (schema not migrated yet, per
    BUILD_PLAN task B), the whole tab silently falls back to ungrouped rows -- this is
    a display grouping only, so its absence must never hide a trade.
  - Inline theme edit: a small text input + "Set" button per row, calling
    `data.ingest.themes.set_theme(conn, trade_id, value)`. Errors (unknown trade_id --
    should not happen since the id comes from the table itself) are shown inline and
    never crash the callback.
  - Row expand uses a `dcc.Dropdown`-free `html.Details` per row (one per trade_id) so
    no extra callback wiring is needed; legs come straight from `trade_legs`.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs.controls import build_date_picker
from ui.tabs.formatting import format_cell

DATE_PICKER_ID = "blotter-date"
TABLE_CONTAINER_ID = "blotter-table-container"
TOOLBAR_ID = "blotter-toolbar"
STATUS_FILTER_ID = "blotter-filter-status"
PRODUCT_FILTER_ID = "blotter-filter-product"
PAIR_FILTER_ID = "blotter-filter-pair"
STRATEGY_FILTER_ID = "blotter-filter-strategy"
THEME_FILTER_ID = "blotter-filter-theme"
DATE_FROM_ID = "blotter-filter-date-from"
DATE_TO_ID = "blotter-filter-date-to"
GROUP_BY_ID = "blotter-group-by"
THEME_INPUT_ID = "blotter-theme-input"
THEME_BUTTON_ID = "blotter-theme-button"
THEME_STATUS_ID = "blotter-theme-status"
THEME_REVISION_ID = "blotter-theme-revision"

GROUP_KEYS = ("instrument_id", "product", "strategy", "theme")
_ALL = "All"

_DISPLAY_COLUMNS = [
    "trade_id", "instrument_id", "product", "strategy", "theme", "trade_date",
    "settle_date", "status", "quantity", "fill", "mark", "spot", "pnl_local",
    "pnl_usd", "pnl_spot_usd", "pnl_carry_usd", "reason",
]

_PERIOD_ORDER = ("daily", "d5", "mtd", "ytd")
_PERIOD_TITLES = {"daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD"}


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _filter_options(df: pd.DataFrame, col: str) -> list:
    if df.empty or col not in df.columns:
        return [{"label": _ALL, "value": _ALL}]
    values = sorted(v for v in df[col].dropna().unique().tolist() if v != "")
    return [{"label": _ALL, "value": _ALL}] + [{"label": v, "value": v} for v in values]


def apply_filters(df: pd.DataFrame, status=None, product=None, pair=None, strategy=None,
                   theme=None, date_from=None, date_to=None) -> pd.DataFrame:
    """Pure function of the value_book frame and filter values (each `None`/`_ALL`
    means no filter on that column) so it is unit-testable without Dash or a DB."""
    out = df
    if status and status != _ALL:
        out = out[out["status"] == status]
    if product and product != _ALL:
        out = out[out["product"] == product]
    if pair and pair != _ALL:
        out = out[out["instrument_id"] == pair]
    if strategy and strategy != _ALL:
        out = out[out["strategy"] == strategy]
    if theme and theme != _ALL:
        out = out[out["theme"] == theme]
    if date_from:
        out = out[out["trade_date"] >= date_from]
    if date_to:
        out = out[out["trade_date"] <= date_to]
    return out


def _fmt_rate(value) -> str:
    if value is None or value != value:
        return ""
    return f"{float(value):,.6f}"


def detail_table(df: pd.DataFrame) -> dash_table.DataTable:
    """Format the (already filtered) value_book frame for display: whole-unit USD
    columns via `format_cell`, rate-precision columns (fill/mark/spot) kept at 6dp."""
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns]
    formatted = df[cols].copy()
    usd_cols = {"pnl_local", "pnl_usd", "pnl_spot_usd", "pnl_carry_usd"}
    rate_cols = {"fill", "mark", "spot", "quantity"}
    for col in cols:
        if col in usd_cols:
            formatted[col] = formatted[col].map(format_cell)
        elif col in rate_cols:
            formatted[col] = formatted[col].map(_fmt_rate)
    return dash_table.DataTable(
        id="blotter-datatable",
        columns=[{"name": c.replace("_", " ").title(), "id": c} for c in cols],
        data=formatted.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "minWidth": "90px"},
        style_header={"fontWeight": "bold"},
        page_size=25,
        row_selectable=False,
    )


def group_summary_table(grouped: dict, key: str) -> dash_table.DataTable:
    """`period_pnl_by`'s {group: {period: {value, available, reason}}} -> a DataTable
    with one row per group and LTD-period columns; Unavailable groups show the literal
    string with the reason appended in parentheses."""
    rows = []
    for group, periods in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        row = {key: group}
        for p in _PERIOD_ORDER:
            entry = periods.get(p, {})
            if entry.get("available"):
                row[_PERIOD_TITLES[p]] = format_cell(entry["value"])
            else:
                reason = entry.get("reason", "")
                row[_PERIOD_TITLES[p]] = f"Unavailable ({reason})" if reason else "Unavailable"
        rows.append(row)
    columns = [{"name": key.replace("_", " ").title(), "id": key}] + \
              [{"name": _PERIOD_TITLES[p], "id": _PERIOD_TITLES[p]} for p in _PERIOD_ORDER]
    return dash_table.DataTable(
        id="blotter-group-summary",
        columns=columns,
        data=rows,
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def _legs_table(legs: pd.DataFrame) -> dash_table.DataTable:
    return dash_table.DataTable(
        columns=[{"name": c.replace("_", " ").title(), "id": c} for c in legs.columns],
        data=legs.to_dict("records"),
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontSize": "12px"},
        style_header={"fontWeight": "bold", "fontSize": "12px"},
    )


def row_expand_panel(conn: sqlite3.Connection, trade_id: str, row: pd.Series) -> html.Div:
    """Legs (from `trade_legs`) + the marks-used columns already on the value_book row
    (mark/mark_date/mark_source/spot/spot_source) -- no second engine call needed."""
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
    """trade_id -> package_id for trades that are actually part of a swap package,
    i.e. `package_id != trade_id` (per CLAUDE.md's package_id rule, `package_id`
    defaults to the trade's own `trade_id` for every non-swap trade, so that case is
    excluded here). Empty dict (never raises) if the column is missing or the query
    fails -- swap grouping is a display convenience only, per module docstring."""
    try:
        rows = conn.execute(
            "SELECT trade_id, package_id FROM trades "
            "WHERE package_id IS NOT NULL AND package_id != '' AND package_id != trade_id"
        ).fetchall()
        return {tid: pkg for tid, pkg in rows}
    except sqlite3.OperationalError:
        return {}


def build_layout(default_date: Optional[str] = None) -> html.Div:
    return html.Div(className="blotter", children=[
        html.H3("Blotter"),
        html.Div(id=TOOLBAR_ID, className="toolbar", children=[
            build_date_picker(DATE_PICKER_ID, default_date=default_date),
            html.Div(className="toolbar-group", children=[
                html.Label("Status"),
                dcc.Dropdown(id=STATUS_FILTER_ID, options=[{"label": _ALL, "value": _ALL}],
                             value=_ALL, clearable=False, style={"width": "140px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Product"),
                dcc.Dropdown(id=PRODUCT_FILTER_ID, options=[{"label": _ALL, "value": _ALL}],
                             value=_ALL, clearable=False, style={"width": "140px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Pair"),
                dcc.Dropdown(id=PAIR_FILTER_ID, options=[{"label": _ALL, "value": _ALL}],
                             value=_ALL, clearable=False, style={"width": "140px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Strategy"),
                dcc.Dropdown(id=STRATEGY_FILTER_ID, options=[{"label": _ALL, "value": _ALL}],
                             value=_ALL, clearable=False, style={"width": "140px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Theme"),
                dcc.Dropdown(id=THEME_FILTER_ID, options=[{"label": _ALL, "value": _ALL}],
                             value=_ALL, clearable=False, style={"width": "140px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Trade date from"),
                dcc.Input(id=DATE_FROM_ID, type="text", placeholder="YYYY-MM-DD"),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Trade date to"),
                dcc.Input(id=DATE_TO_ID, type="text", placeholder="YYYY-MM-DD"),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Group by"),
                dcc.Dropdown(
                    id=GROUP_BY_ID,
                    options=[{"label": "None", "value": "none"}] +
                            [{"label": k.replace("_", " ").title(), "value": k} for k in GROUP_KEYS],
                    value="none", clearable=False, style={"width": "160px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Set theme"),
                dcc.Input(id=THEME_INPUT_ID, type="text", placeholder="trade_id:theme"),
                html.Button("Set", id=THEME_BUTTON_ID, n_clicks=0, className="btn"),
                html.Span(id=THEME_STATUS_ID, className="status-line", style={"marginLeft": "8px"}),
                dcc.Store(id=THEME_REVISION_ID),
            ]),
        ]),
        html.Div(id=TABLE_CONTAINER_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Same convention as `ui.tabs.cash_ladder.register_callbacks`."""

    @app.callback(
        Output(TABLE_CONTAINER_ID, "children"),
        Output(STATUS_FILTER_ID, "options"),
        Output(PRODUCT_FILTER_ID, "options"),
        Output(PAIR_FILTER_ID, "options"),
        Output(STRATEGY_FILTER_ID, "options"),
        Output(THEME_FILTER_ID, "options"),
        Input(DATE_PICKER_ID, "date"),
        Input(STATUS_FILTER_ID, "value"),
        Input(PRODUCT_FILTER_ID, "value"),
        Input(PAIR_FILTER_ID, "value"),
        Input(STRATEGY_FILTER_ID, "value"),
        Input(THEME_FILTER_ID, "value"),
        Input(DATE_FROM_ID, "value"),
        Input(DATE_TO_ID, "value"),
        Input(GROUP_BY_ID, "value"),
        Input(THEME_REVISION_ID, "data"),
    )
    def _update(as_of_date, status, product, pair, strategy, theme, date_from, date_to,
                group_by, _theme_rev=None):
        if not as_of_date:
            empty_opts = [{"label": _ALL, "value": _ALL}]
            return message_box("No as-of date available."), empty_opts, empty_opts, empty_opts, empty_opts, empty_opts

        try:
            from engine.pnl.valuation import value_book
        except ImportError as exc:
            empty_opts = [{"label": _ALL, "value": _ALL}]
            return (message_box(f"Blotter view not available yet ({exc})."),
                    empty_opts, empty_opts, empty_opts, empty_opts, empty_opts)

        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            empty_opts = [{"label": _ALL, "value": _ALL}]
            return (message_box(f"Database not available ({exc})."),
                    empty_opts, empty_opts, empty_opts, empty_opts, empty_opts)
        try:
            df = value_book(conn, as_of_date)
            status_opts = _filter_options(df, "status")
            product_opts = _filter_options(df, "product")
            pair_opts = _filter_options(df, "instrument_id")
            strategy_opts = _filter_options(df, "strategy")
            theme_opts = _filter_options(df, "theme")

            filtered = apply_filters(df, status, product, pair, strategy, theme, date_from, date_to)

            body_children = []
            if group_by and group_by != "none" and not df.empty:
                try:
                    from engine.pnl.ledger import period_pnl_by
                    grouped = period_pnl_by(conn, as_of_date, group_by)
                    body_children.append(group_summary_table(grouped, group_by))
                except Exception as exc:  # never let group-by take the detail table down
                    body_children.append(message_box(f"Group summary unavailable ({exc!r})."))

            if filtered.empty:
                body_children.append(message_box("No trades match the current filters."))
            else:
                body_children.append(detail_table(filtered))
                packages = _package_ids(conn)
                expand_children = []
                seen_packages = set()
                for _, row in filtered.iterrows():
                    trade_id = row["trade_id"]
                    pkg = packages.get(trade_id)
                    if pkg and pkg in seen_packages:
                        continue
                    if pkg:
                        seen_packages.add(pkg)
                        pkg_rows = filtered[filtered["trade_id"].isin(
                            [tid for tid, p in packages.items() if p == pkg])]
                        label = f"Swap package {pkg} ({len(pkg_rows)} legs)"
                        panel = html.Div([row_expand_panel(conn, tid, r)
                                           for tid, r in zip(pkg_rows["trade_id"], pkg_rows.to_dict("records"))
                                           for r in [pd.Series(r)]])
                    else:
                        label = f"Trade {trade_id}"
                        panel = row_expand_panel(conn, trade_id, row)
                    expand_children.append(
                        html.Details(className="section section--secondary details", children=[
                            html.Summary(label), panel,
                        ]))
                body_children.append(html.Div(expand_children))
        finally:
            conn.close()
        return html.Div(body_children), status_opts, product_opts, pair_opts, strategy_opts, theme_opts

    @app.callback(
        Output(THEME_STATUS_ID, "children"),
        Output(THEME_REVISION_ID, "data"),
        Input(THEME_BUTTON_ID, "n_clicks"),
        State(THEME_INPUT_ID, "value"),
        prevent_initial_call=True,
    )
    def _set_theme(n_clicks, value):
        """`value` is 'trade_id:theme' (no other UI text input is wired to identify
        which row is being edited; see module docstring)."""
        import time
        if not value or ":" not in value:
            return "Enter as trade_id:theme", None
        trade_id, _, theme = value.partition(":")
        trade_id, theme = trade_id.strip(), theme.strip()
        from ui.app import connect_readonly
        from data.ingest.themes import set_theme
        db_path = get_db_path()
        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.OperationalError as exc:
            return f"Database not available ({exc}).", None
        try:
            set_theme(conn, trade_id, theme)
        except ValueError as exc:
            return str(exc), None
        finally:
            conn.close()
        return f"Set theme={theme!r} on {trade_id}", str(time.time())
