"""P&L tab: source/date controls + per-pair P&L, book totals, and period P&L.

Wired into the "Overall book" tab (see ui/app.py), not "FX", per CLAUDE.md "Six tabs as
views": "Overall book | positions (BNP vs CALC), P&L rollups by strategy / account, LTD
series". Two of this tab's three blocks -- book totals (net/gross/gold/futures USD) and
the daily/5d/MTD/YTD period series -- are exactly the book-wide "P&L rollups ... LTD
series" that row describes; the FX tab's own row in that table is scoped to "FX trades x
marks_official", i.e. forward-outright-level detail, not aggregate/period rollups. The
per-pair block (usd_notional / ltd_usd / n_trades from `aggregate_by_pair`) is included
alongside the totals it feeds rather than duplicated on both tabs.

`engine/pnl/aggregate.py` and `engine/pnl/pnl.py` (owned by pnl-engine) are imported
lazily *inside* the callback (never at module import time), same pattern as
`ui/tabs/cash_ladder.py`, so this module -- and `ui.app` -- always import successfully
even if the engine package is absent or mid-change.

Number formatting: `ui.tabs.formatting.format_cell` (round to whole units, thousands
separators, negatives in parentheses, blank for NaN/None) applied to every numeric cell,
including `n_trades` (a small non-negative integer, so the convention is a no-op there
beyond digit grouping). Source dropdown / date picker reuse `ui.tabs.controls` (the same
helpers `ui/tabs/cash_ladder.py` uses) with this tab's own component ids, so the two
tabs' callbacks never collide.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, dash_table, html

from ui.tabs.controls import build_date_picker, build_source_dropdown, source_value_to_param
from ui.tabs.formatting import format_cell

SOURCE_DROPDOWN_ID = "pnl-source"
DATE_PICKER_ID = "pnl-date"
CONTENT_CONTAINER_ID = "pnl-content-container"

PAIR_TABLE_ID = "pnl-pair-datatable"
TOTALS_TABLE_ID = "pnl-totals-datatable"
PERIOD_TABLE_ID = "pnl-period-datatable"

PAIR_COLUMNS = ["instrument_id", "usd_notional", "ltd_usd", "n_trades"]
TOTALS_KEYS = ["net_usd", "gross_usd", "gold_usd", "futures_usd"]
PERIOD_KEYS = ["ltd", "daily", "d5", "mtd", "ytd"]
PERIOD_LABELS = {"ltd": "LTD", "daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD"}


def message_box(message: str) -> html.P:
    """Grey status text shown in place of the tables (missing engine module, missing
    DB, no as_of date, etc)."""
    return html.P(message, style={"color": "gray"})


def pairs_table_from_by_pair(by_pair: pd.DataFrame) -> dash_table.DataTable:
    """DataTable from an `aggregate_by_pair`-shaped frame (instrument_id, usd_notional,
    ltd_usd, n_trades). Pure function of the frame -- does not touch the DB."""
    if by_pair.empty:
        data = []
    else:
        formatted = by_pair.copy()
        for col in ("usd_notional", "ltd_usd", "n_trades"):
            formatted[col] = formatted[col].map(format_cell)
        data = formatted[PAIR_COLUMNS].to_dict("records")
    columns = [{"name": col, "id": col} for col in PAIR_COLUMNS]
    return dash_table.DataTable(
        id=PAIR_TABLE_ID,
        columns=columns,
        data=data,
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def totals_table_from_book_totals(totals: dict) -> dash_table.DataTable:
    """DataTable from a `book_totals`-shaped dict (net_usd, gross_usd, gold_usd,
    futures_usd). Pure function of the dict."""
    data = [
        {"metric": key, "usd": format_cell(totals.get(key))}
        for key in TOTALS_KEYS
    ]
    return dash_table.DataTable(
        id=TOTALS_TABLE_ID,
        columns=[{"name": "metric", "id": "metric"}, {"name": "usd", "id": "usd"}],
        data=data,
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def period_table_from_period_pnl(period: dict) -> dash_table.DataTable:
    """DataTable from a `period_pnl`-shaped dict (ltd, daily, d5, mtd, ytd, as_of_date,
    plus a `<key>_ref_date` per period). One row per period; `ref_date` is blank for
    `ltd` (cumulative since inception, no single reference date). Pure function of the
    dict."""
    data = []
    for key in PERIOD_KEYS:
        ref_date = period.get(f"{key}_ref_date", "") if key != "ltd" else ""
        data.append({
            "period": PERIOD_LABELS[key],
            "usd": format_cell(period.get(key)),
            "ref_date": ref_date,
        })
    return dash_table.DataTable(
        id=PERIOD_TABLE_ID,
        columns=[
            {"name": "period", "id": "period"},
            {"name": "usd", "id": "usd"},
            {"name": "ref_date", "id": "ref_date"},
        ],
        data=data,
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Controls + an (initially empty) content container for the P&L tab. The tables
    themselves are filled in by the callback registered in register_callbacks."""
    return html.Div([
        html.H3("P&L"),
        build_source_dropdown(SOURCE_DROPDOWN_ID),
        build_date_picker(DATE_PICKER_ID, default_date=default_date),
        html.Div(id=CONTENT_CONTAINER_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Register the callback that re-queries `aggregate_by_pair` / `book_totals` /
    `period_pnl` whenever the source dropdown or date picker changes.

    `get_db_path` is a zero-arg callable returning the resolved DB path, same pattern as
    `ui.tabs.cash_ladder.register_callbacks` -- passed in rather than imported at module
    scope so tests can supply a stub without touching the real DB / env / RISK_DB.
    """

    @app.callback(
        Output(CONTENT_CONTAINER_ID, "children"),
        Input(SOURCE_DROPDOWN_ID, "value"),
        Input(DATE_PICKER_ID, "date"),
        Input('rates-revision', 'data'),
    )
    def _update_content(source_value, as_of_date, rates_revision=None):
        if not as_of_date:
            return message_box("No as-of date available.")

        try:
            from engine.pnl.pnl import ltd_per_trade
            from engine.pnl.aggregate import aggregate_by_pair, book_totals, period_pnl
        except ImportError as exc:
            return message_box(f"P&L engine not available yet ({exc}).")

        # Local import: keeps this module importable even if ui.app changes shape.
        from ui.app import connect_readonly

        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc}).")

        param_source = source_value_to_param(source_value)
        try:
            per_trade = ltd_per_trade(conn, as_of_date, source=param_source, strict=False)
            by_pair = aggregate_by_pair(per_trade, conn)
            totals = book_totals(by_pair, conn)
            period = period_pnl(conn, as_of_date, source=param_source)
        finally:
            conn.close()

        return html.Div([
            html.P('HA-portfolio vJean formulas, applied to recorded trades. Missing inputs are blank, not zero. '
                   'This is the All FX trades calculation; the Portfolio sheet has additional product inputs and manual adjustments.'),
            html.P(f"Shared FX valuation date: {period.get('valuation_date', '')}"),
            html.H4("P&L by pair"),
            pairs_table_from_by_pair(by_pair),
            html.H4("Book totals"),
            message_box(totals.get('status', '')),
            totals_table_from_book_totals(totals),
            html.H4("Period P&L"),
            html.P('Daily follows All FX trades M4. 5d, MTD and YTD remain unavailable because this workbook does not define those calculations.'),
            period_table_from_period_pnl(period),
        ])
