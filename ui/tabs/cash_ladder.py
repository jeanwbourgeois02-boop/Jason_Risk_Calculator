"""Cash ladder tab: source/date controls + a DataTable rendering
`engine.ladder.views.ladder_table`.

`engine/ladder/views.py` (owned by cash-ladder) is expected to expose
`ladder_table(conn, as_of_date, source=None) -> DataFrame` with columns `ccy`, one
column per ISO settle-date string (ascending), `total`, `usd`. That module may not exist
yet / may still be in flux while this file is written, so it is imported lazily *inside*
the callback (never at module import time) and wrapped in try/except ImportError so
`import ui.app` and `import ui.tabs.cash_ladder` always succeed regardless of whether
engine/ladder/views.py is present.

Number formatting (`format_cell` / `format_ladder_frame`): every non-`ccy` cell is
rounded to whole units and rendered with thousands separators, negatives in parentheses
(e.g. -1234567.8 -> "(1,234,568)"), NaN/None -> "" (blank). This is a display-only
transform; storage/precision live entirely in engine/ and data/, per CLAUDE.md ("ui/ ...
never recomputes P&L or delta itself").
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, dash_table, dcc, html

SOURCE_OFFICIAL = "OFFICIAL"
SOURCE_OPTIONS = [
    {"label": "Official", "value": SOURCE_OFFICIAL},
    {"label": "BNP_BVAL", "value": "BNP_BVAL"},
]

SOURCE_DROPDOWN_ID = "cash-ladder-source"
DATE_PICKER_ID = "cash-ladder-date"
TABLE_CONTAINER_ID = "cash-ladder-table-container"


def source_value_to_param(value: Optional[str]) -> Optional[str]:
    """Map the dropdown's sentinel 'OFFICIAL' (or an unset value) to source=None, the
    ladder_table convention for "use marks_official". Any other value (e.g. 'BNP_BVAL')
    passes through unchanged."""
    if value in (None, SOURCE_OFFICIAL):
        return None
    return value


def format_cell(value) -> str:
    """Format a single numeric cell: round to whole units, thousands separators,
    negatives in parentheses, blank ("") for NaN/None."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    rounded = round(float(value))
    if rounded < 0:
        return f"({abs(rounded):,})"
    return f"{rounded:,}"


def format_ladder_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Pure helper: apply `format_cell` to every column except `ccy` (which passes
    through unchanged as it is not numeric). Returns a new DataFrame of strings so it
    can be unit-tested without Dash."""
    out = df.copy()
    for col in out.columns:
        if col == "ccy":
            continue
        out[col] = out[col].map(format_cell)
    return out


def table_from_ladder(df: pd.DataFrame) -> dash_table.DataTable:
    """Build the DataTable component from an already-fetched ladder DataFrame (ccy, one
    column per ISO settle-date string ascending, total, usd). Pure function of the
    frame -- does not touch the DB -- so it is unit-testable with a hand-built frame."""
    formatted = format_ladder_frame(df)
    columns = [{"name": col, "id": col} for col in formatted.columns]
    return dash_table.DataTable(
        id="cash-ladder-datatable",
        columns=columns,
        data=formatted.to_dict("records"),
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def message_box(message: str) -> html.P:
    """Grey status text shown in the table container instead of a DataTable (missing
    view module, missing DB, no as_of date, etc)."""
    return html.P(message, style={"color": "gray"})


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Controls + an (initially empty) table container for the Cash ladder tab. The
    table itself is filled in by the callback registered in register_callbacks."""
    return html.Div([
        html.H3("Cash ladder"),
        html.Div(
            [
                html.Label("Source"),
                dcc.Dropdown(
                    id=SOURCE_DROPDOWN_ID,
                    options=SOURCE_OPTIONS,
                    value=SOURCE_OFFICIAL,
                    clearable=False,
                ),
            ],
            style={"width": "200px", "display": "inline-block", "marginRight": "20px"},
        ),
        html.Div(
            [
                html.Label("As of"),
                dcc.DatePickerSingle(id=DATE_PICKER_ID, date=default_date),
            ],
            style={"display": "inline-block"},
        ),
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
        Input(SOURCE_DROPDOWN_ID, "value"),
        Input(DATE_PICKER_ID, "date"),
    )
    def _update_table(source_value, as_of_date):
        if not as_of_date:
            return message_box("No as-of date available.")

        try:
            from engine.ladder.views import ladder_table
        except ImportError as exc:
            return message_box(f"Cash ladder view not available yet ({exc}).")

        # Local import: keeps this module importable even if ui.app changes shape.
        from ui.app import connect_readonly

        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc}).")
        try:
            df = ladder_table(conn, as_of_date, source_value_to_param(source_value))
        finally:
            conn.close()
        return table_from_ladder(df)
