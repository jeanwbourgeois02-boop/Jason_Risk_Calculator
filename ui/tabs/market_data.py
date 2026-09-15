"""Market data tab: "Can I trust the numbers?" (BUILD_PLAN.md section 5).

Content, per the section-5 table:
  * `mark_inventory` table (data.bloomberg.inventory.mark_inventory) with a status
    colour per row (OFFICIAL / INTERP / MANUAL / MISSING).
  * The pull button and feed status, moved here from `ui/tabs/cash_ladder.py`'s
    toolbar (C1 removes them there); reuses `data.bloomberg.live.pull_once` /
    `read_status` exactly as the ladder toolbar did.
  * `close_completeness` calendar strip over the trailing 20 business days ending on
    the selected as-of date (a fixed, documented default -- nothing in BUILD_PLAN.md
    specifies a window).
  * Manual entry form calling `data.bloomberg.manual.write_manual_mark`.
  * The Bloomberg diagnostics panel (`diagnostics_panel` / `feed_headline`, previously
    imported by `ui/tabs/cash_ladder.py` from this module) stays here unchanged so
    C1 and any other caller keep working without edits.

No calculation happens in this module: every number is read straight from
`data.bloomberg.inventory` / `data.bloomberg.live` / `data.bloomberg.manual`. Engine
and data imports are lazy (inside callbacks / render helpers) so `import ui.app` and
`import ui.tabs.market_data` always succeed even if those modules are mid-edit.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs.controls import build_date_picker

DATE_PICKER_ID = "market-data-date"
TOOLBAR_ID = "market-data-toolbar"
STATUS_ID = "market-data-status"
BODY_ID = "market-data-body"
REFRESH_ID = "market-data-refresh"
PULL_NOW_ID = "market-data-pull-now"
PULL_NOW_STATUS_ID = "market-data-pull-now-status"
PULL_REVISION_ID = "market-data-pull-revision"
REFRESH_MS = 120_000  # matches data.bloomberg.live.INTERVAL_SECONDS

INVENTORY_TABLE_ID = "market-data-inventory-table"
COMPLETENESS_TABLE_ID = "market-data-completeness-table"
COMPLETENESS_DAYS = 20  # trailing business days shown in the calendar strip; not specified in BUILD_PLAN.md

MANUAL_INSTRUMENT_ID = "market-data-manual-instrument"
MANUAL_SETTLE_ID = "market-data-manual-settle"
MANUAL_MARK_TYPE_ID = "market-data-manual-mark-type"
MANUAL_VALUE_ID = "market-data-manual-value"
MANUAL_SUBMIT_ID = "market-data-manual-submit"
MANUAL_STATUS_ID = "market-data-manual-status"

_MONO = {"fontFamily": "Consolas, 'Courier New', monospace", "fontSize": "12px", "padding": "3px 8px",
         "textAlign": "left", "whiteSpace": "nowrap"}
_HEAD = {"fontWeight": "600", "backgroundColor": "#f0f2f5", "borderBottom": "1px solid #d9dee3"}

MANUAL_MARK_TYPE_OPTIONS = [
    {"label": t, "value": t}
    for t in ("SPOT", "FWD_OUTRIGHT", "FUTURE_PX", "PAR_RATE", "PV_USD", "DV01_USD", "DELTA", "PREMIUM")
]


# --------------------------------------------------------------------------- diagnostics panel (unchanged, moved here)
def feed_headline(status: Optional[dict]) -> str:
    """One line for the toolbar / summary: connection, last pull, counts."""
    if not status:
        return "Bloomberg: no pull recorded yet"
    if not status.get("connected"):
        return f"Bloomberg: not connected — {status.get('reason', 'unknown reason')}"
    return (f"Bloomberg: connected · last pull {status.get('time', '')} · "
            f"{status.get('written', 0)} marks written, {status.get('failed', 0)} failed · refresh every 2 min")


def diagnostics_panel(status: Optional[dict], rates: Dict[str, dict], open_by_default: bool = False) -> html.Details:
    items: List[dict] = list((status or {}).get("items", []))
    failed = [i for i in items if i.get("status") == "FAILED"]
    rows = [{"status": i.get("status", ""), "instrument_id": i.get("instrument_id", ""),
             "mark_type": i.get("mark_type", ""), "settle_date": i.get("settle_date", ""),
             "value": "" if i.get("value") is None else f"{float(i['value']):.8f}",
             "source": i.get("source", ""), "detail": i.get("detail", "")} for i in items]
    rate_rows = [{"currency": c, "pair": v.get("pair", ""), "rate": f"{v['rate']:.8f}",
                  "inverted": "1/rate" if v["inverted"] else "direct", "source": v["source"],
                  "timestamp": v["timestamp"], "stale": "STALE" if v["stale"] else "fresh"}
                 for c, v in sorted(rates.items())]
    summary_bits = [feed_headline(status)]
    if status and status.get("as_of_date"):
        summary_bits.append(f"requests built for as-of {status['as_of_date']}; live marks stamped {status.get('as_of_marks', '')}")
    if status and status.get("warnings"):
        summary_bits.append(f"{len(status['warnings'])} warning(s) from the pull")
    children = [
        html.Summary(f"Bloomberg diagnostics · {len(items) - len(failed)} OK / {len(failed)} failed"
                     if items else "Bloomberg diagnostics"),
        html.Div(id="market-data-diag-summary", className="status-line", children=" · ".join(summary_bits)),
    ]
    if status and status.get("traceback"):
        children.append(html.Pre(status["traceback"], className="diag-trace"))
    children += [
        html.H4("Requested marks: pulled vs failed"),
        dash_table.DataTable(
            id="market-data-diag-table",
            columns=[{"name": n, "id": i} for n, i in [("Status", "status"), ("Pair", "instrument_id"),
                                                       ("Mark", "mark_type"), ("Settle date", "settle_date"),
                                                       ("Value", "value"), ("Source", "source"), ("Detail", "detail")]],
            data=rows, page_size=40, sort_action="native", filter_action="native",
            style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
            style_data_conditional=[
                {"if": {"filter_query": "{status} = 'OK'", "column_id": "status"}, "color": "#1a7f4b", "fontWeight": "600"},
                {"if": {"filter_query": "{status} = 'FAILED'", "column_id": "status"}, "color": "#b42318", "fontWeight": "600"},
                {"if": {"filter_query": "{status} = 'FAILED'"}, "backgroundColor": "#fff4f2"},
                {"if": {"filter_query": "{status} = 'SKIPPED'"}, "color": "#616e7c"},
            ]),
        html.H4("Spot rates the ladder is using (latest official SPOT mark per currency)"),
        dash_table.DataTable(
            id="market-data-rates-table",
            columns=[{"name": n, "id": i} for n, i in [("Currency", "currency"), ("Pair", "pair"), ("Rate", "rate"),
                                                       ("USD per local", "inverted"), ("Source", "source"),
                                                       ("Snapped at", "timestamp"), ("Freshness", "stale")]],
            data=rate_rows, style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
            style_data_conditional=[{"if": {"filter_query": "{stale} = 'STALE'"}, "color": "#8a4b00",
                                     "backgroundColor": "#fff4e5"}]),
    ]
    if status and status.get("warnings"):
        children += [html.H4("Pull warnings"), html.Ul([html.Li(w, className="status-line") for w in status["warnings"]])]
    return html.Details(id="market-data-diag", className="details details--diag",
                        open=open_by_default or bool(failed), children=children)


# --------------------------------------------------------------------------- inventory table
_STATUS_STYLE = [
    {"if": {"filter_query": "{status} = 'OFFICIAL'", "column_id": "status"}, "color": "#1a7f4b", "fontWeight": "600"},
    {"if": {"filter_query": "{status} = 'INTERP'", "column_id": "status"}, "color": "#8a4b00"},
    {"if": {"filter_query": "{status} = 'MANUAL'", "column_id": "status"}, "color": "#1a4b8a"},
    {"if": {"filter_query": "{status} = 'MISSING'", "column_id": "status"}, "color": "#b42318", "fontWeight": "600"},
    {"if": {"filter_query": "{status} = 'MISSING'"}, "backgroundColor": "#fff4f2"},
]


def inventory_table(df: pd.DataFrame) -> dash_table.DataTable:
    """Pure formatting of `mark_inventory`'s frame into a DataTable with status
    colours. Blank cells (value/source/snapped_at) for MISSING rows, per the module's
    own MISSING convention -- never a substitute value."""
    formatted = df.copy()
    if "value" in formatted.columns:
        formatted["value"] = formatted["value"].map(lambda v: "" if v is None or pd.isna(v) else f"{float(v):.8f}")
    for col in ("source", "snapped_at"):
        if col in formatted.columns:
            formatted[col] = formatted[col].map(lambda v: "" if v is None else str(v))
    columns = [{"name": n, "id": i} for n, i in [
        ("Instrument", "instrument_id"), ("Settle date", "settle_date"), ("Mark type", "mark_type"),
        ("Value", "value"), ("Source", "source"), ("Snapped at", "snapped_at"), ("Status", "status"),
    ] if i in formatted.columns]
    return dash_table.DataTable(
        id=INVENTORY_TABLE_ID,
        columns=columns,
        data=formatted.to_dict("records"),
        page_size=50, sort_action="native", filter_action="native",
        style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
        style_data_conditional=_STATUS_STYLE,
    )


def completeness_table(df: pd.DataFrame) -> dash_table.DataTable:
    """Calendar strip: one row per business day, needed vs present official SPOT
    marks, complete flag coloured."""
    formatted = df.copy()
    columns = [{"name": n, "id": i} for n, i in [
        ("Date", "as_of_date"), ("Needed", "needed"), ("Present", "present"), ("Complete", "complete"),
    ] if i in formatted.columns]
    if "complete" in formatted.columns:
        formatted["complete"] = formatted["complete"].map(lambda v: "yes" if v else "no")
    return dash_table.DataTable(
        id=COMPLETENESS_TABLE_ID,
        columns=columns,
        data=formatted.to_dict("records"),
        page_size=25,
        style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
        style_data_conditional=[
            {"if": {"filter_query": "{complete} = 'no'"}, "backgroundColor": "#fff4f2", "color": "#b42318"},
        ],
    )


def manual_entry_form() -> html.Div:
    """Manual mark entry calling `data.bloomberg.manual.write_manual_mark`. Free-text
    instrument id / settle date (no dropdown source wired here -- keeping this narrow
    per the C3 scope) and a mark-type dropdown covering every mark_type in the
    contract's 'Official marks' table."""
    return html.Div(className="market-data-manual-entry", children=[
        html.H4("Manual mark entry"),
        html.P("MANUAL is official only for DELTA and PREMIUM; for SPOT / FWD_OUTRIGHT / FUTURE_PX / "
               "PAR_RATE / PV_USD / DV01_USD a manual row is visible here but is never used by valuation "
               "unless explicitly requested (marks_source='MANUAL')."),
        html.Div(className="toolbar", children=[
            html.Div([html.Label("Instrument"), dcc.Input(id=MANUAL_INSTRUMENT_ID, type="text")]),
            html.Div([html.Label("Settle date"), dcc.Input(id=MANUAL_SETTLE_ID, type="text", placeholder="YYYY-MM-DD")]),
            html.Div([html.Label("Mark type"), dcc.Dropdown(id=MANUAL_MARK_TYPE_ID, options=MANUAL_MARK_TYPE_OPTIONS,
                                                             value="SPOT", clearable=False, style={"width": "160px"})]),
            html.Div([html.Label("Value"), dcc.Input(id=MANUAL_VALUE_ID, type="number")]),
            html.Div([html.Button("Save", id=MANUAL_SUBMIT_ID, n_clicks=0, className="btn")]),
        ]),
        html.Div(id=MANUAL_STATUS_ID, className="status-line"),
    ])


def _connect_readonly(path) -> sqlite3.Connection:
    """Open the database read-only without importing `ui.app` (which pulls in the
    other tabs and can be mid-edit while this module is developed/tested)."""
    from pathlib import Path
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Controls + an (initially empty) body container. The body is filled in by the
    callback registered in register_callbacks, matching the cash-ladder tab's pattern."""
    return html.Div(className="market-data", children=[
        html.H3("Market data"),
        html.Div(id=TOOLBAR_ID, className="toolbar", children=[
            build_date_picker(DATE_PICKER_ID, default_date=default_date),
            html.Div(className="toolbar-group", children=[
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
        ]),
        dcc.Interval(id=REFRESH_ID, interval=REFRESH_MS, n_intervals=0),
        html.Div(id=BODY_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Register the callbacks: main body refresh (inventory + completeness +
    diagnostics), the "Pull now" button, and the manual-entry submit button.

    `get_db_path` is a zero-arg callable returning the resolved DB path, same
    convention as `ui/tabs/cash_ladder.py::register_callbacks`.
    """

    @app.callback(
        Output(BODY_ID, "children"),
        Output(STATUS_ID, "children"),
        Input(DATE_PICKER_ID, "date"),
        Input(REFRESH_ID, "n_intervals"),
        Input(PULL_REVISION_ID, "data"),
        Input(MANUAL_STATUS_ID, "children"),
    )
    def _update_body(as_of_date, _n_intervals=0, _pull_rev=None, _manual_status=None):
        return _render(as_of_date)

    def _render(as_of_date):
        if not as_of_date:
            return message_box("No as-of date available."), "Bloomberg: status unknown"

        db_path = get_db_path()
        try:
            conn = _connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc})."), "Bloomberg: status unknown"

        try:
            from data.bloomberg.inventory import mark_inventory, close_completeness
            inv = mark_inventory(conn, as_of_date)
            end = date.fromisoformat(as_of_date)
            start = end - timedelta(days=int(COMPLETENESS_DAYS * 1.6) + 5)  # generous calendar padding for bd count
            completeness = close_completeness(conn, start.isoformat(), end.isoformat())
            completeness = completeness.tail(COMPLETENESS_DAYS)
        except ImportError as exc:
            conn.close()
            return message_box(f"Market data inventory not available yet ({exc})."), "Bloomberg: status unknown"

        try:
            from data.bloomberg.live import read_status, rates_from_marks
            feed_status = read_status(db_path)
            rates = rates_from_marks(conn)
        except ImportError:
            feed_status, rates = None, {}
        finally:
            conn.close()

        missing = int((inv["status"] == "MISSING").sum()) if not inv.empty else 0
        toolbar_status = f"{feed_headline(feed_status)} · {missing} of {len(inv)} needed marks missing"

        body = html.Div([
            html.H4("Marks needed today"),
            inventory_table(inv) if not inv.empty else message_box("No open legs or futures need a mark on this date."),
            html.H4(f"Close completeness (trailing {COMPLETENESS_DAYS} business days)"),
            completeness_table(completeness) if not completeness.empty else message_box("No completeness data."),
            manual_entry_form(),
            diagnostics_panel(feed_status, rates),
        ])
        return body, toolbar_status

    @app.callback(
        Output(PULL_NOW_STATUS_ID, "children"),
        Output(PULL_REVISION_ID, "data"),
        Input(PULL_NOW_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _pull_now(n_clicks):
        """Synchronous single Bloomberg pull (data.bloomberg.live.pull_once); moved from
        `ui/tabs/cash_ladder.py`'s toolbar, same behaviour."""
        import time
        from data.bloomberg.live import pull_once
        status = pull_once(get_db_path())
        if status.get("connected"):
            text = f"Pulled {status.get('time', '')}: {status.get('written', 0)} marks written, {status.get('failed', 0)} failed"
        else:
            text = f"Not pulled: {status.get('reason', 'unknown')}"
        return text, str(time.time())

    @app.callback(
        Output(MANUAL_STATUS_ID, "children"),
        Input(MANUAL_SUBMIT_ID, "n_clicks"),
        State(DATE_PICKER_ID, "date"),
        State(MANUAL_INSTRUMENT_ID, "value"),
        State(MANUAL_SETTLE_ID, "value"),
        State(MANUAL_MARK_TYPE_ID, "value"),
        State(MANUAL_VALUE_ID, "value"),
        prevent_initial_call=True,
    )
    def _submit_manual(n_clicks, as_of_date, instrument_id, settle_date, mark_type, value):
        if not as_of_date or not instrument_id or not settle_date or not mark_type or value is None:
            return "Fill in every field before saving."
        try:
            from data.bloomberg.manual import write_manual_mark
            from data.ingest.schema import connect
        except ImportError as exc:
            return f"Manual entry not available yet ({exc})."
        db_path = get_db_path()
        conn = connect(db_path)
        try:
            write_manual_mark(conn, as_of_date, instrument_id, settle_date, mark_type, float(value))
        except Exception as exc:
            return f"Save failed: {exc!r}"
        finally:
            conn.close()
        return f"Saved MANUAL {mark_type} {instrument_id} {settle_date} = {float(value)!r}."
