"""BNP report upload control (docs: user decisions 2026-09-15, item B; date-picker
removed by the coordinator's same-day follow-up).

One button, "Upload BNP report" (.xlsx/.xls/.csv). This is the ONLY data input: the
HA-portfolio reference-workbook recognition/preview that used to live in this control
has been removed entirely (moved to nothing -- the workbook stays a read-only file on
the Reconciliation tab's manual-rates grid, `ui/workbook_rates.py`).

Flow: choose file -> file name + snapshot date shown -> "Confirm insert" -> dcc.Loading
("Importing...") -> one small status line under the button. Nothing is written to the
database before Confirm is pressed (`import_report` is only called from the Confirm
callback, never from the file-picked callback).

Snapshot date, no picker: `data.ingest.upload.suggested_date` already derives the date
from the filename (`HA_PNL_YYYYMMDD...` -> that date minus one business day, per
CLAUDE.md "File dated T is the T-1 close snapshot") -- there is nothing for a user to
pick correctly that the filename doesn't already say, so a `dcc.DatePickerSingle` was
one more control than the flow needed. The date now shows as read-only text next to the
file name ("HA_PNL_20260818.csv - snapshot 2026-08-17"); a one-line `dcc.Input`
(`type="date"`) only appears in its place when the filename carries no recognisable
date, so a user is never asked to enter a date the filename already answers.

Both date sources are read as State by the Confirm callback (`report-date` store first,
the manual `dcc.Input` as fallback). There is deliberately NO callback with the manual
input's `value` as an Input: the file-picked callback resets that value to None on every
selection, and Dash fires downstream callbacks on every callback-written prop whether or
not the value changed, so a "manual value -> store" callback ran right after the file
callback and wiped the filename-derived date out of the store (bug seen 2026-09-15 as
"Choose the snapshot date before confirming" on a correctly named HA_PNL file).

The status line on success is a single sentence:
    "Loaded <filename> - snapshot <as_of> - <N> trades, <M> positions"
`trades` = total rows currently in the `trades` table (trades have no as_of_date column
of their own -- CLAUDE.md's `trades` table -- so a per-file count is not recoverable
without added schema); `positions` = rows in `positions` for THIS as_of_date, which is
per-file. Documented here since it is a asymmetry a reader might otherwise assume is a
bug. Errors show in the same line, in red (`.source-result--error`).

History (expandable, read from the DB, no schema added): one row per distinct
`positions.as_of_date`, its position count. Trade counts are not stored per snapshot
(see above) so only the running total is shown once, above the list.
"""
from __future__ import annotations

from dash import Input, Output, State, dcc, html, no_update

from data.ingest.upload import decode, suggested_date, import_report

SOURCE_LINE_ID = "data-source-line"
FILE_UPLOAD_ID = "report-file"
STAGE_ID = "report-stage"
FILENAME_ID = "report-filename"
SNAPSHOT_TEXT_ID = "report-snapshot-text"
MANUAL_DATE_ID = "report-manual-date"
DATE_PICKER_ID = "report-date"  # dcc.Store now (was DatePickerSingle) -- holds the resolved ISO date
CONFIRM_ID = "report-import"
RESULT_ID = "report-result"
HISTORY_ID = "report-history"
HISTORY_DETAILS_ID = "report-history-details"


def describe_source(data: dict) -> str:
    """One line naming the loaded BNP snapshot, from ui.app.summary()."""
    if data.get("as_of_date") in (None, "none"):
        return "No BNP report loaded yet."
    return f"Loaded: BNP report as of {data['as_of_date']} ({data['trades']} trades, {data['positions']} positions)."


def upload_history(db_path) -> list:
    """[(as_of_date, position_count)] descending by date, read straight from
    `positions` (no upload-log table exists, per the user's instruction not to add
    schema). Empty list if the DB is missing or has no positions yet."""
    import sqlite3
    from pathlib import Path

    db_path = Path(db_path)
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT as_of_date, COUNT(*) FROM positions GROUP BY as_of_date ORDER BY as_of_date DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return rows


def history_layout(db_path) -> html.Details:
    rows = upload_history(db_path)
    if not rows:
        body = html.P("No previous uploads.", className="section-kicker")
    else:
        body = html.Ul([html.Li(f"{as_of} - {count} positions") for as_of, count in rows])
    return html.Details(id=HISTORY_DETAILS_ID, className="details details--compact", children=[
        html.Summary("Upload history"),
        html.Div(id=HISTORY_ID, children=body),
    ])


def layout(data: dict = None, db_path=None):
    data = data or {}
    initial_history = history_layout(db_path) if db_path is not None else html.Div()
    return html.Div(className="source-strip", children=[
        html.Div(className="source-row", children=[
            dcc.Upload(id=FILE_UPLOAD_ID, className="source-upload",
                       children=html.Button("Upload BNP report", className="btn"),
                       accept=".csv,.xlsx,.xls", multiple=False, max_size=25 * 1024 * 1024),
            html.Div(id=SOURCE_LINE_ID, className="source-line", children=describe_source(data)),
        ]),
        html.Div(id=STAGE_ID, className="source-row source-row--stage", style={"display": "none"}, children=[
            html.Span(id=FILENAME_ID, className="source-file"),
            html.Span(id=SNAPSHOT_TEXT_ID, className="source-snapshot", style={"display": "none"}),
            html.Div(id=f"{MANUAL_DATE_ID}-wrap", style={"display": "none"}, children=[
                html.Label("Snapshot date"),
                dcc.Input(id=MANUAL_DATE_ID, type="date"),
            ]),
            dcc.Store(id=DATE_PICKER_ID, data=None),
            html.Button("Confirm insert", id=CONFIRM_ID, n_clicks=0, className="btn"),
        ]),
        dcc.Loading(type="dot", color="#1f5fbf", children=html.Div(id=RESULT_ID, role="status", className="source-result")),
        html.Div(id="report-history-wrap", children=initial_history),
    ])


def register(app, get_db_path):
    @app.callback(
        Output(STAGE_ID, "style"), Output(FILENAME_ID, "children"),
        Output(SNAPSHOT_TEXT_ID, "children"), Output(SNAPSHOT_TEXT_ID, "style"),
        Output(f"{MANUAL_DATE_ID}-wrap", "style"), Output(MANUAL_DATE_ID, "value"),
        Output(DATE_PICKER_ID, "data"),
        Output(RESULT_ID, "children", allow_duplicate=True),
        Input(FILE_UPLOAD_ID, "contents"),
        State(FILE_UPLOAD_ID, "filename"), prevent_initial_call=True,
    )
    def _selected(contents, filename):
        # Nothing is written here -- this only decodes enough to show the file name
        # and a suggested date. import_report() is called exclusively from _confirm().
        hidden = {"display": "none"}
        if not contents:
            return hidden, "", "", hidden, hidden, None, None, ""
        try:
            decode(contents)  # validates size/shape before showing Confirm
        except Exception as exc:
            return hidden, "", "", hidden, hidden, None, None, \
                html.Span(str(exc), className="source-result--error")
        date = suggested_date(filename)
        if date:
            return {}, filename, f"snapshot {date}", {}, hidden, None, date, ""
        note = "This file name carries no recognisable date; enter the snapshot date, then press Confirm insert."
        return {}, filename, "", hidden, {}, None, None, note

    @app.callback(
        Output(RESULT_ID, "children"), Output(SOURCE_LINE_ID, "children"),
        Output("report-history-wrap", "children"),
        Output("cash-ladder-date", "date", allow_duplicate=True),
        Input(CONFIRM_ID, "n_clicks"),
        State(FILE_UPLOAD_ID, "contents"), State(FILE_UPLOAD_ID, "filename"),
        State(DATE_PICKER_ID, "data"), State(MANUAL_DATE_ID, "value"), prevent_initial_call=True,
    )
    def _confirm(clicks, contents, filename, as_of, manual_date):
        if not contents:
            return no_update, no_update, no_update, no_update
        # Filename-derived date wins; the manual box is only shown when there is none.
        as_of = as_of or manual_date
        if not as_of:
            return html.Span("Choose the snapshot date before confirming.", className="source-result--error"), \
                no_update, no_update, no_update
        db_path = get_db_path()
        try:
            message = import_report(decode(contents), filename, as_of, db_path)
        except Exception as exc:
            return html.Span(f"Import failed; no data saved. {exc}", className="source-result--error"), \
                no_update, no_update, no_update
        from ui.app import load_summary
        data = load_summary(db_path)
        short = (f"Loaded {filename} - snapshot {as_of} - {data['trades']} trades, {data['positions']} positions")
        return short, describe_source(data), history_layout(db_path), as_of
