"""Cash ladder tab: source/date controls + a DataTable rendering a transposed view of
`engine.ladder.views.ladder_table`.

`engine/ladder/views.py` (owned by cash-ladder) is expected to expose
`ladder_table(conn, as_of_date, source=None) -> DataFrame` with columns `ccy`, one
column per ISO settle-date string (ascending), `total`, `usd`. That module may not exist
yet / may still be in flux while this file is written, so it is imported lazily *inside*
the callback (never at module import time) and wrapped in try/except ImportError so
`import ui.app` and `import ui.tabs.cash_ladder` always succeed regardless of whether
engine/ladder/views.py is present.

Display is a TRANSPOSE of `ladder_table`'s shape (`ladder_table` itself is untouched --
we do not own it and its contract, "one row per currency", is unchanged):
  - Rows: settle dates ascending, plus a `Total` row at the bottom (from `ladder_table`'s
    `total` / `usd` columns).
  - Columns: one per currency, ordered by |usd| descending first (currencies with a
    defined `usd`), then currencies with `usd` = NaN afterwards sorted alphabetically --
    the same order `ladder_table` already returns its rows in; `transpose_ladder`
    recomputes this order itself so it stays correct even when fed a hand-built frame
    that is not pre-sorted. Plus a `usd_equivalent` column on the right.
  - `usd_equivalent` per date row = sum over currencies of (amount on that date x that
    currency's implied spot), where implied spot for a currency = `usd / total` from the
    ladder frame (this is exact, since `ladder_table` computes `usd` as `total x spot`).
    BLANK RULE (documented here since it is a judgement call, not in the CLAUDE.md
    contract): the `usd_equivalent` cell for a date is blank if either (a) any currency
    with a non-zero, non-NaN amount on that date has no defined spot (its `usd` is NaN),
    or (b) any currency with a non-zero, non-NaN amount on that date has `total == 0`
    (spot cannot be recovered from 0/0 even though `usd` may itself be defined as 0 in
    that degenerate case), or (c) the amount itself is NaN (unknown flow, not "no flow" --
    distinct from the explicit-zero convention `ladder_table` otherwise guarantees).
    Currencies with a zero or NaN amount on that date never block the sum. The `Total`
    row's `usd_equivalent` uses the same rule against the `total` / `usd` columns and
    equals the sum of the `usd` column when defined.

Number formatting (`ui.tabs.formatting.format_cell` / `format_frame`): every non-label
cell is rounded to whole units and rendered with thousands separators, negatives in
parentheses (e.g. -1234567.8 -> "(1,234,568)"), NaN/None -> "" (blank). This is a
display-only transform; storage/precision live entirely in engine/ and data/, per
CLAUDE.md ("ui/ ... never recomputes P&L or delta itself").
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, dash_table, dcc, html

from ui.tabs.controls import (
    SOURCE_OFFICIAL,
    SOURCE_OPTIONS,
    build_date_picker,
    build_source_dropdown,
    source_value_to_param,
)
from ui.tabs.formatting import format_cell, format_frame as format_ladder_frame

SOURCE_DROPDOWN_ID = "cash-ladder-source"
DATE_PICKER_ID = "cash-ladder-date"
TABLE_CONTAINER_ID = "cash-ladder-table-container"
TOOLBAR_ID = "cash-ladder-toolbar"
SORT_ID = "cash-ladder-summary-sort"   # value: 'top' | 'all' (summary scope)
STATUS_ID = "cash-ladder-status"
REFRESH_ID = "cash-ladder-refresh"
PULL_NOW_ID = "cash-ladder-pull-now"
PULL_NOW_STATUS_ID = "cash-ladder-pull-now-status"
PULL_REVISION_ID = "cash-ladder-pull-revision"
REFRESH_MS = 120_000  # matches data.bloomberg.live.INTERVAL_SECONDS
WORKBOOK_SECTION_ID = "cash-ladder-workbook-section"

TRANSPOSED_LABEL_COL = "settle_date"
USD_EQUIVALENT_COL = "usd_equivalent"


def _ccy_order(df: pd.DataFrame) -> list:
    """Currency order: |usd| descending first (usd defined), then alphabetical (usd
    NaN) -- same rule as `ladder_table`'s own row order, recomputed here so this
    function is correct even for a hand-built frame that is not pre-sorted."""
    has_usd = df["usd"].notna()
    with_usd = (
        df[has_usd]
        .assign(_abs_usd=lambda d: d["usd"].abs())
        .sort_values("_abs_usd", ascending=False, kind="mergesort")
        .drop(columns="_abs_usd")
    )
    without_usd = df[~has_usd].sort_values("ccy", kind="mergesort")
    return pd.concat([with_usd, without_usd], ignore_index=True)["ccy"].tolist()


def _implied_spot(total: float, usd: float) -> float:
    """usd / total, i.e. the per-unit rate implied by `ladder_table`'s own `usd = total
    x spot` computation. NaN if usd is undefined or total is 0 (0/0 is not recoverable
    even where usd happens to be defined as 0 in that degenerate case)."""
    if pd.isna(usd):
        return float("nan")
    if total == 0:
        return float("nan")
    return usd / total


def transpose_ladder(df: pd.DataFrame) -> pd.DataFrame:
    """Pure transform: `ladder_table`'s (ccy, dates..., total, usd) frame ->
    (settle_date, currencies..., usd_equivalent), rows = dates ascending + a `Total`
    row. See module docstring for the exact column order and the `usd_equivalent`
    blank rule."""
    if df.empty or "ccy" not in df.columns:
        return pd.DataFrame(columns=[TRANSPOSED_LABEL_COL, USD_EQUIVALENT_COL])

    date_cols = sorted(c for c in df.columns if c not in ("ccy", "total", "usd"))
    ccy_order = _ccy_order(df)

    by_ccy = df.set_index("ccy")
    spot = {
        ccy: _implied_spot(by_ccy.loc[ccy, "total"], by_ccy.loc[ccy, "usd"])
        for ccy in ccy_order
    }

    def usd_equivalent(amounts: dict) -> float:
        total = 0.0
        for ccy, amount in amounts.items():
            if amount is None or pd.isna(amount):
                return float("nan")
            if amount == 0:
                continue
            s = spot[ccy]
            if pd.isna(s):
                return float("nan")
            total += amount * s
        return total

    rows = []
    for d in date_cols:
        amounts = {ccy: by_ccy.loc[ccy, d] for ccy in ccy_order}
        row = {TRANSPOSED_LABEL_COL: d, **amounts}
        row[USD_EQUIVALENT_COL] = usd_equivalent(amounts)
        rows.append(row)

    totals = {ccy: by_ccy.loc[ccy, "total"] for ccy in ccy_order}
    total_row = {TRANSPOSED_LABEL_COL: "Total", **totals}
    total_row[USD_EQUIVALENT_COL] = usd_equivalent(totals)
    rows.append(total_row)

    columns = [TRANSPOSED_LABEL_COL] + ccy_order + [USD_EQUIVALENT_COL]
    return pd.DataFrame(rows, columns=columns)


def table_from_ladder(
    df: pd.DataFrame,
    label_col: str = "ccy",
    table_id: str = "cash-ladder-datatable",
) -> dash_table.DataTable:
    """Build the DataTable component from an already-fetched (or already-transposed)
    ladder DataFrame. Pure function of the frame -- does not touch the DB -- so it is
    unit-testable with a hand-built frame."""
    formatted = format_ladder_frame(df, label_col=label_col)
    columns = [{"name": col, "id": col} for col in formatted.columns]
    return dash_table.DataTable(
        id=table_id,
        columns=columns,
        data=formatted.to_dict("records"),
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def message_box(message: str) -> html.P:
    """Grey status text shown in the table container instead of a DataTable (missing
    view module, missing DB, no as_of date, etc)."""
    return html.P(message, style={"color": "gray"})


def valuation_table(df: pd.DataFrame, table_id: str) -> dash_table.DataTable:
    """Preserve rate precision and text labels while formatting dollar amounts."""
    rates = {"fill", "mark", "spot_usd_per_local", "denominator"}
    labels = {
        "ccy": "Local currency", "settle_date": "Settlement date",
        "local_amount": "Signed local amount", "fill": "Entry FX",
        "mark": "Workbook valuation FX", "valuation_date": "Workbook pricing date",
        "usd_entry": "Signed USD entry", "usd_valuation": "Workbook USD valuation",
        "pnl_usd": "USD P&L", "spot_usd_per_local": "General spot (USD/local; reference)",
        "physical_usd_valuation": "Actual local leg at valuation FX (USD)",
        "valuation_residual": "Workbook less actual leg value (USD)",
        "denominator": "Workbook divisor", "workbook_quantity": "Workbook quantity C",
    }
    formatted = df.copy()
    for col in formatted:
        if col in rates:
            formatted[col] = formatted[col].map(lambda value: "" if pd.isna(value) else f"{value:,.8f}")
        elif pd.api.types.is_numeric_dtype(formatted[col]):
            formatted[col] = formatted[col].map(format_cell)
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": labels.get(col, col.replace("_", " ").title()), "id": col}
                 for col in formatted],
        data=formatted.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "minWidth": "110px"},
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        page_size=25,
    )


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Controls + an (initially empty) table container for the Cash ladder tab. The
    table itself is filled in by the callback registered in register_callbacks."""
    return html.Div(className="cash-ladder", children=[
        html.H3("FX Risk and Settlement Ladder"),
        html.Div(id=TOOLBAR_ID, className="toolbar", children=[
            build_date_picker(DATE_PICKER_ID, default_date=default_date),
            html.Div(className="toolbar-group", children=[
                html.Label("Currency order"),
                dcc.RadioItems(id=SORT_ID, value="usd", inline=True,
                               options=[{"label": "|USD delta|", "value": "usd"},
                                        {"label": "A-Z", "value": "alpha"}]),
            ]),
            html.Div(className="toolbar-group toolbar-group--exposure", children=[
                html.Label("Status"),
                html.Div(id=STATUS_ID, className="toolbar-static",
                         children="Bloomberg: waiting for first refresh"),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Bloomberg"),
                html.Div([html.Button("Pull now", id=PULL_NOW_ID, n_clicks=0, className="btn"),
                          html.Span(id=PULL_NOW_STATUS_ID, className="status-line", style={"marginLeft": "8px"})]),
                dcc.Store(id=PULL_REVISION_ID),
            ]),
            html.Div(className="toolbar-group toolbar-group--workbook", children=[
                build_source_dropdown(SOURCE_DROPDOWN_ID, label="Workbook MTM rates"),
            ]),
        ]),
        dcc.Interval(id=REFRESH_ID, interval=REFRESH_MS, n_intervals=0),
        html.Div(id=TABLE_CONTAINER_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Register the callback that re-queries `ladder_table` whenever the source dropdown
    or date picker changes.

    `get_db_path` is a zero-arg callable returning the resolved DB path (typically
    `ui.app.get_db_path`, or a closure over the path `create_app` resolved for an
    explicit `db_path` override) -- passed in rather than imported at module scope so
    tests can supply a stub without touching the real DB / env / RISK_DB.
    """

    @app.callback(
        Output(TABLE_CONTAINER_ID, "children"),
        Output(STATUS_ID, "children"),
        Input(SOURCE_DROPDOWN_ID, "value"),
        Input(DATE_PICKER_ID, "date"),
        Input("rates-revision", "data"),
        Input(SORT_ID, "value"),
        Input(REFRESH_ID, "n_intervals"),
        Input(PULL_REVISION_ID, "data"),
    )
    def _update_table(source_value, as_of_date, _rates_revision=None, sort="usd", _n_intervals=0, _pull_rev=None):
        """Returns (tab body, toolbar status text). Re-runs every REFRESH_MS so the ladder
        follows the 2-minute Bloomberg feed (data.bloomberg.live)."""
        body, toolbar_status = _render(source_value, as_of_date, sort or "usd")
        return body, toolbar_status

    def _render(source_value, as_of_date, sort):
        toolbar_status = "Bloomberg: status unknown"
        if not as_of_date:
            return message_box("No as-of date available."), toolbar_status

        try:
            from engine.ladder.views import ladder_table
        except ImportError as exc:
            return message_box(f"Cash ladder view not available yet ({exc}).", ), toolbar_status

        # Local import: keeps this module importable even if ui.app changes shape.
        from ui.app import connect_readonly

        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc})."), toolbar_status
        try:
            df = ladder_table(conn, as_of_date, source_value_to_param(source_value))
            from engine.ladder.valuation import ladder_trade_valuation, ladder_valuation_summary
            detail = ladder_trade_valuation(conn, as_of_date, source_value_to_param(source_value))
            summary = ladder_valuation_summary(detail)
            # Exposure ladder (spec): additional, separately labelled section built from
            # engine.ladder.exposure via records_from_db; mock rates, never Bloomberg.
            try:
                from engine.ladder.exposure import build_exposure
                from engine.ladder.exposure_adapter import records_from_db
                from data.bloomberg.live import rates_from_marks, read_status
                from ui.tabs.exposure import BOOK_DISPLAY, exposure_section, rate_status_text
                from ui.tabs.market_data import diagnostics_panel, feed_headline
                records, unresolved = records_from_db(conn, as_of_date, book_mapping=BOOK_DISPLAY)
                # Rates: latest official SPOT marks written by the Bloomberg feed. Never mock.
                rates = rates_from_marks(conn)
                feed_status = read_status(db_path)
                # Workbook MTM period figures for the compact status line: existing engine
                # function, never recomputed here; None -> 'Workbook MTM unavailable'.
                try:
                    from engine.pnl.aggregate import period_pnl
                    period = period_pnl(conn, as_of_date, source=source_value_to_param(source_value))
                except Exception:  # pricing not loaded / engine unavailable
                    period = None
                # P&L ledger (engine/pnl/ledger.py): read-only summary for the picked as-of date.
                try:
                    from engine.pnl.ledger import ledger_summary
                    from ui.tabs.ledger import ledger_block
                    ledger_ui = ledger_block(ledger_summary(conn, as_of_date, rates), as_of_date)
                except Exception as exc:  # never let the ledger take the ladder down
                    ledger_ui = message_box(f"P&L ledger unavailable ({exc!r}).")
                exposure = html.Div([
                    exposure_section(records, unresolved, as_of_date, rates=rates, period=period,
                                     sort=sort, feed_status=feed_status),
                    ledger_ui,
                    diagnostics_panel(feed_status, rates),
                ])
                books = ", ".join(sorted({r["book"] for r in records})) or "none"
                result = build_exposure(records, rates)
                toolbar_status = f"Book {books} · {rate_status_text(result)} · {feed_headline(feed_status)}"
            except ImportError as exc:
                exposure = message_box(f"Exposure ladder not available ({exc}).")
        finally:
            conn.close()
        transposed = transpose_ladder(df)
        missing = int(detail["pnl_usd"].isna().sum())
        status = (f"USD P&L incomplete: {missing} of {len(detail)} open forwards lack workbook pricing."
                  if missing else f"{len(detail)} open forwards priced using workbook formulas.")
        return html.Div([
            exposure,
            html.Details(id=WORKBOOK_SECTION_ID, className="section section--secondary details", open=False, children=[
            html.Summary("Workbook mark-to-market (Excel method)"),
            html.P("The HA-portfolio workbook's own formulas: one shared valuation date and the workbook divisor. "
                   "A different number from the exposure P&L above, on purpose.", className="section-kicker"),
            html.H4("Currency and settlement date: workbook P&L"),
            html.P(status),
            html.P("Signed USD entry + workbook USD valuation = USD P&L. "
                   "Rates are shown separately for each trade below. Cash balances are excluded from P&L. "
                   "NDF rows show notionals, not gross cash settlement amounts."),
            valuation_table(summary, "cash-ladder-valuation-summary"),
            html.H4("Trade calculation detail"),
            html.P("The workbook divisor determines P&L conversion. General spot is displayed for reference. "
                   "Any difference between workbook value and the actual broker local leg is shown explicitly; "
                   "blank pricing cells indicate unavailable inputs."),
            valuation_table(detail, "cash-ladder-valuation-detail"),
            html.H4("Cash settlement amounts and current balances"),
            html.P("This overview includes balances and deliverable cash flows. Its USD equivalent uses spot "
                   "and is a cash value, not P&L."),
            table_from_ladder(transposed, label_col=TRANSPOSED_LABEL_COL),
            ]),
        ]), toolbar_status

    @app.callback(
        Output(PULL_NOW_STATUS_ID, "children"),
        Output(PULL_REVISION_ID, "data"),
        Input(PULL_NOW_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _pull_now(n_clicks):
        """Synchronous single Bloomberg pull (data.bloomberg.live.pull_once); the returned
        revision re-triggers the ladder so new marks show immediately."""
        import time
        from data.bloomberg.live import pull_once
        status = pull_once(get_db_path())
        if status.get("connected"):
            text = f"Pulled {status.get('time', '')}: {status.get('written', 0)} marks written, {status.get('failed', 0)} failed"
        else:
            text = f"Not pulled: {status.get('reason', 'unknown')}"
        return text, str(time.time())
