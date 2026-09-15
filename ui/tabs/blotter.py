"""Blotter tab: docs/BUILD_PLAN.md section 5 "Blotter", rebuilt 2026-09-15 into five
sub-tabs (user decision): Total book, FX, Rates, Options, Bundles. Rows come from
`engine.pnl.valuation.value_book(as_of)` (via `ui.tabs.blotter_pricing.priced_value_book`,
which retries a missing official mark with `marks_source='BNP_BVAL'` so a
Bloomberg-less DB still prices -- see that module's docstring), filtered per sub-tab by
`product`. Native header filter/sort on the trade table (2026-09-15, unchanged from the
prior single-table Blotter): `dash_table.DataTable` has no dropdown *filter* widget, so
every column uses the native text filter row (`>=`, `contains`, bare value, ...).

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
    PERIOD_ORDER,
    PERIOD_TITLES,
    priced_value_book,
    row_scoped_period_pnl,
)
from ui.tabs.controls import build_date_picker
from ui.tabs.formatting import format_cell

DATE_PICKER_ID = "blotter-date"
TOOLBAR_ID = "blotter-toolbar"
SUBTABS_ID = "blotter-subtabs"
CONTENT_ID = "blotter-content"
THEME_INPUT_ID = "blotter-theme-input"
THEME_BUTTON_ID = "blotter-theme-button"
THEME_STATUS_ID = "blotter-theme-status"
THEME_REVISION_ID = "blotter-theme-revision"

# Kept for the pre-2026-09-15 tests that still exercise a single detail table by id.
TABLE_CONTAINER_ID = "blotter-table-container"
DATATABLE_ID = "blotter-datatable"
SUBTOTAL_ID = "blotter-subtotal"
GROUP_BY_ID = "blotter-group-by"

GROUP_KEYS = ("instrument_id", "product", "strategy", "theme")
_ALL = "All"

# --------------------------------------------------------------------------- sub-tabs
SCOPE_ORDER = ("total", "fx", "rates", "options", "bundles")
SCOPE_LABELS = {"total": "Total book", "fx": "FX", "rates": "Rates", "options": "Options",
                "bundles": "Bundles"}
SCOPE_PRODUCTS = {
    "total": None,
    "fx": ("FX_SPOT", "FX_FWD", "FX_SWAP", "FUTURE"),
    "rates": ("IRS",),
    "options": ("FX_OPTION",),
}
PLACEHOLDER_SCOPES = {
    "rates": "no IRS trades loaded; view not built yet",
    "options": "no option trades loaded; view not built yet",
}

_DISPLAY_COLUMNS = [
    "trade_id", "instrument_id", "product", "strategy", "theme", "trade_date",
    "settle_date", "status", "quantity", "fill", "mark", "mark_source", "spot", "pnl_local",
    "pnl_usd", "pnl_spot_usd", "pnl_carry_usd", "reason", "note",
]

_CATEGORICAL_FILTER_COLUMNS = {"status", "product", "instrument_id", "strategy", "theme"}


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _filter_options(df: pd.DataFrame, col: str) -> list:
    if df.empty or col not in df.columns:
        return [{"label": _ALL, "value": _ALL}]
    values = sorted(v for v in df[col].dropna().unique().tolist() if v != "")
    return [{"label": _ALL, "value": _ALL}] + [{"label": v, "value": v} for v in values]


def apply_filters(df: pd.DataFrame, status=None, product=None, pair=None, strategy=None,
                   theme=None, date_from=None, date_to=None) -> pd.DataFrame:
    """Pure function of the value_book frame and filter values, kept for callers/tests
    that still exercise it directly (the running app now uses the DataTable's own
    native header filter row instead of a toolbar; see module docstring)."""
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


def detail_table(df: pd.DataFrame, table_id: str = DATATABLE_ID) -> dash_table.DataTable:
    """Format the (already scope-filtered) value_book frame for display."""
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns]
    formatted = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    usd_cols = {"pnl_local", "pnl_usd", "pnl_spot_usd", "pnl_carry_usd"}
    rate_cols = {"fill", "mark", "spot", "quantity"}
    for col in cols:
        if col in usd_cols:
            formatted[col] = formatted[col].map(format_cell)
        elif col in rate_cols:
            formatted[col] = formatted[col].map(_fmt_rate)
    # priced_from_bnp travels with the row (not displayed) so the derived_virtual_data
    # callback can tell which visible rows were fallback-priced.
    data_records = formatted.to_dict("records")
    if "priced_from_bnp" in df.columns:
        for rec, flag in zip(data_records, df["priced_from_bnp"].tolist()):
            rec["priced_from_bnp"] = bool(flag)
    if "trade_id" in df.columns:
        for rec, tid in zip(data_records, df["trade_id"].tolist()):
            rec.setdefault("trade_id", tid)
    style_data_conditional = [
        {"if": {"filter_query": "{reason} != ''"}, "backgroundColor": "#fff3cd"},
    ]
    if "note" in cols:
        style_data_conditional.append(
            {"if": {"column_id": "note"}, "color": "gray", "fontStyle": "italic"}
        )
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": c.replace("_", " ").title(), "id": c} for c in cols],
        data=data_records,
        filter_action="native",
        sort_action="native",
        sort_mode="multi",
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "minWidth": "90px"},
        style_header={"fontWeight": "bold"},
        style_data_conditional=style_data_conditional,
        page_size=25,
        row_selectable=False,
    )


def subtotal_line(rows: list) -> html.P:
    """Kept for the pre-2026-09-15 single-number subtotal (some tests still exercise
    it); the sub-tab pages now use `render_pnl_strip` instead."""
    if not rows:
        return html.P("Subtotal (visible rows): $0", className="section-kicker")
    total = 0.0
    n_unavailable = 0
    for row in rows:
        raw = row.get("pnl_usd", "")
        if raw in ("", "Unavailable", None):
            n_unavailable += 1
            continue
        text = str(raw).replace(",", "").replace("$", "")
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            value = float(text)
        except ValueError:
            n_unavailable += 1
            continue
        total += -value if negative else value
    suffix = f" ({n_unavailable} row(s) unavailable, excluded)" if n_unavailable else ""
    return html.P(f"Subtotal (visible rows): {format_cell(total)}{suffix}",
                  className="section-kicker")


def render_pnl_strip(periods: dict, caption: Optional[str] = None) -> html.Div:
    """LTD/Daily/Previous day/5d/MTD/YTD/Trading cards, each with its reference date
    shown underneath, per the 2026-09-15 coordinator addition."""
    cards = []
    for key in PERIOD_ORDER:
        entry = periods.get(key, {})
        if entry.get("available"):
            value_text = format_cell(entry["value"])
        else:
            reason = entry.get("reason", "")
            value_text = f"Unavailable ({reason})" if reason else "Unavailable"
        cards.append(html.Div(className="pnl-card", children=[
            html.Div(PERIOD_TITLES[key], className="pnl-card-title"),
            html.Div(value_text, className="pnl-card-value"),
            html.Small(entry.get("ref_date", ""), className="pnl-card-refdate"),
        ]))
    children = [html.Div(cards, className="pnl-strip")]
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


def group_summary_table(grouped: dict, key: str) -> dash_table.DataTable:
    """`period_pnl_by`'s {group: {period: {value, available, reason}}} -> a DataTable
    with one row per group and LTD-period columns."""
    _period_order = ("daily", "d5", "mtd", "ytd")
    _period_titles = {"daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD"}
    rows = []
    for group, periods in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        row = {key: group}
        for p in _period_order:
            entry = periods.get(p, {})
            if entry.get("available"):
                row[_period_titles[p]] = format_cell(entry["value"])
            else:
                reason = entry.get("reason", "")
                row[_period_titles[p]] = f"Unavailable ({reason})" if reason else "Unavailable"
        rows.append(row)
    columns = [{"name": key.replace("_", " ").title(), "id": key}] + \
              [{"name": _period_titles[p], "id": _period_titles[p]} for p in _period_order]
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


def _expand_children(conn: sqlite3.Connection, df: pd.DataFrame) -> html.Div:
    packages = _package_ids(conn)
    expand_children = []
    seen_packages = set()
    for _, row in df.iterrows():
        trade_id = row["trade_id"]
        pkg = packages.get(trade_id)
        if pkg and pkg in seen_packages:
            continue
        if pkg:
            seen_packages.add(pkg)
            pkg_rows = df[df["trade_id"].isin([tid for tid, p in packages.items() if p == pkg])]
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
    return html.Div(expand_children)


def scope_layout(scope: str, conn: sqlite3.Connection, as_of: str) -> html.Div:
    """Build one sub-tab's content: P&L strip (initial, whole-scope) + trade table +
    row-expand panels. Rates/Options are placeholders per module docstring."""
    strip_id = f"blotter-strip-{scope}"
    table_id = f"blotter-datatable-{scope}"

    if scope in PLACEHOLDER_SCOPES:
        empty = pd.DataFrame(columns=_DISPLAY_COLUMNS)
        return html.Div([
            html.Div(id=strip_id, children=render_placeholder_strip(PLACEHOLDER_SCOPES[scope])),
            detail_table(empty, table_id=table_id),
        ])

    df, n_fallback, n_total = priced_value_book(conn, as_of)
    products = SCOPE_PRODUCTS[scope]
    if products is not None and not df.empty:
        df = df[df["product"].isin(products)]

    trade_ids = df["trade_id"].tolist() if not df.empty else []
    periods = row_scoped_period_pnl(conn, as_of, trade_ids)
    scope_fallback = int(df["priced_from_bnp"].sum()) if not df.empty else 0
    caption = fallback_caption(scope_fallback, len(df))

    body = [html.Div(id=strip_id, children=render_pnl_strip(periods, caption))]
    if df.empty:
        body.append(message_box("No trades for this as-of date in this scope."))
    else:
        body.append(detail_table(df, table_id=table_id))
        body.append(_expand_children(conn, df))
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


def build_layout(default_date: Optional[str] = None) -> html.Div:
    return html.Div(className="blotter", children=[
        html.H3("Blotter"),
        html.Div(id=TOOLBAR_ID, className="toolbar", children=[
            build_date_picker(DATE_PICKER_ID, default_date=default_date),
            html.Div(className="toolbar-group", children=[
                html.Label("Set theme"),
                dcc.Input(id=THEME_INPUT_ID, type="text", placeholder="trade_id:theme"),
                html.Button("Set", id=THEME_BUTTON_ID, n_clicks=0, className="btn"),
                html.Span(id=THEME_STATUS_ID, className="status-line", style={"marginLeft": "8px"}),
                dcc.Store(id=THEME_REVISION_ID),
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

    @app.callback(
        Output(CONTENT_ID, "children"),
        Input(DATE_PICKER_ID, "date"),
        Input(SUBTABS_ID, "value"),
        Input(THEME_REVISION_ID, "data"),
    )
    def _update(as_of_date, scope, _theme_rev=None):
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
                n_fallback = sum(1 for r in (rows or []) if r.get("priced_from_bnp"))
                caption = fallback_caption(n_fallback, len(rows or []))
                periods = row_scoped_period_pnl(conn, as_of_date, trade_ids)
                return render_pnl_strip(periods, caption)
            finally:
                conn.close()

    for _scope in SCOPE_ORDER:
        if _scope != "bundles":
            _register_strip_callback(_scope)

    @app.callback(
        Output(THEME_STATUS_ID, "children"),
        Output(THEME_REVISION_ID, "data"),
        Input(THEME_BUTTON_ID, "n_clicks"),
        State(THEME_INPUT_ID, "value"),
        prevent_initial_call=True,
    )
    def _set_theme(n_clicks, value):
        import time
        if not value or ":" not in value:
            return "Enter as trade_id:theme", None
        trade_id, _, theme = value.partition(":")
        trade_id, theme = trade_id.strip(), theme.strip()
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
        prevent_initial_call=True,
    )
    def _bundle_detail(selected, _rev, _add_clicks, _remove_clicks, add_pair, remove_pair):
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
            return bundles_ui.bundle_detail_table(pairs), status
        finally:
            conn.close()
