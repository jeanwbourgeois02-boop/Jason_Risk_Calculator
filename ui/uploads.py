"""Trade file upload control: one button, "Upload trade file" (.csv/.xlsx/.xls),
accepting the trade blotter (`data.ingest.upload.import_blotter`, tolerant of format
variation per that module's docstring). No date picker: each blotter row carries its
own dates.

Flow: choose file -> "Confirm insert" -> dcc.Loading -> one status line. Nothing is
written before Confirm; the file-picked callback only decodes enough to validate
size/shape and show the file name. On success the Confirm row is hidden again and the
top-bar source line (`describe_source`, whole-database totals from `ui.app.load_summary`)
is the permanent record. The loader's summary sentence (new/updated trades, excluded and
rejected rows) renders verbatim as `.source-result--info`; errors render in
`.source-result--error` with the Confirm row left in place for a retry.
"""
from __future__ import annotations

import logging

from dash import Input, Output, State, dcc, html, no_update

from data.ingest.upload import decode, import_blotter, preview_frame, validate_blotter_shape

log = logging.getLogger(__name__)

SOURCE_LINE_ID = "data-source-line"
FILE_UPLOAD_ID = "report-file"
STAGE_ID = "report-stage"
FILENAME_ID = "report-filename"
CONFIRM_ID = "report-import"
RESULT_ID = "report-result"


def describe_source(data: dict) -> str:
    """One line describing what's in the database (whole-database totals from
    ui.app.summary(), not "what was last uploaded")."""
    trades = data.get("trades", 0)
    if trades:
        return f"Loaded: {trades} trades in database."
    return "No data loaded yet."


def layout(data: dict = None):
    data = data or {}
    return html.Div(className="source-strip", children=[
        html.Div(className="source-row", children=[
            dcc.Upload(id=FILE_UPLOAD_ID, className="source-upload",
                       children=html.Button("Upload trade file", className="btn"),
                       accept=".csv,.xlsx,.xls", multiple=False, max_size=25 * 1024 * 1024),
            html.Div(id=SOURCE_LINE_ID, className="source-line", children=describe_source(data)),
        ]),
        html.Div(id=STAGE_ID, className="source-row source-row--stage", style={"display": "none"}, children=[
            html.Span(id=FILENAME_ID, className="source-file"),
            html.Button("Confirm insert", id=CONFIRM_ID, n_clicks=0, className="btn"),
        ]),
        dcc.Loading(type="dot", color="#1f5fbf", children=html.Div(id=RESULT_ID, role="status", className="source-result")),
    ])


def register(app, get_db_path):
    @app.callback(
        Output(STAGE_ID, "style"), Output(FILENAME_ID, "children"),
        Output(RESULT_ID, "children", allow_duplicate=True),
        Input(FILE_UPLOAD_ID, "contents"),
        State(FILE_UPLOAD_ID, "filename"), prevent_initial_call=True,
    )
    def _selected(contents, filename):
        # Nothing is written here -- this only decodes/validates shape enough to show
        # the file name and Confirm. import_blotter() is called exclusively from
        # _confirm().
        hidden = {"display": "none"}
        if not contents:
            return hidden, "", ""
        try:
            payload = decode(contents)
            validate_blotter_shape(preview_frame(payload, filename))
        except Exception as exc:
            return hidden, "", html.Span(str(exc), className="source-result--error")
        return {}, filename, ""

    @app.callback(
        Output(RESULT_ID, "children"), Output(SOURCE_LINE_ID, "children"),
        Output(STAGE_ID, "style", allow_duplicate=True),
        Input(CONFIRM_ID, "n_clicks"),
        State(FILE_UPLOAD_ID, "contents"), State(FILE_UPLOAD_ID, "filename"),
        prevent_initial_call=True,
    )
    def _confirm(clicks, contents, filename):
        keep = (no_update, no_update)  # source line, stage
        if not contents:
            return (no_update, *keep)
        db_path = get_db_path()
        try:
            message = import_blotter(decode(contents), filename, db_path)
        except Exception as exc:
            return (html.Span(f"Import failed; no data saved. {exc}", className="source-result--error"), *keep)
        log.info("%s (blotter): %s", filename, message)
        from ui.app import load_summary
        data = load_summary(db_path)
        result = html.Div(message, className="source-result--info")
        return result, describe_source(data), {"display": "none"}
