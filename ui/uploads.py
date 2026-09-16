"""Trade file upload control (docs: user decisions 2026-09-15, item B; date-picker
removed by the coordinator's same-day follow-up; blotter-only pivot 2026-09-16, then
restored to dual-format 2026-09-16 same day -- user's own words: "both valid - both
formats taken - the new thing as the primary source as its real time and the bnp for
things the new one lacks - and then check they reconcile". The blotter (real-time
trade blotter, `data/ingest/blotter.py`) is the primary trade source; the BNP PB
report (`data/ingest/bnp.py`) is a once-daily EOD-Hong-Kong snapshot, still needed for
the cash-ladder balance column (`positions` rows) that the blotter format has no grain
for. Both are accepted through this one control, auto-detected by column shape
(`data.ingest.upload.sniff_format`/`detect_format`) -- never by filename, since neither
format is reliably named.

Flow: choose file -> the file-picked callback decodes + sniffs its format (no DB write
yet) -> shows Confirm, plus a date picker ONLY if the sniffed format is BNP (a blotter
file has no single EOD snapshot date -- each row carries its own `TradeDate` / settle
date, so there is nothing to pick for that format). An unrecognized file (neither
shape matches) shows an error and no Confirm button.

Nothing is written to the database before Confirm is pressed (`import_report`/
`import_blotter` are only called from the Confirm callback). The Confirm callback
re-sniffs the format itself rather than trusting a stored value from the file-picked
callback, since re-parsing here is cheap and it keeps the two callbacks independently
correct (no dcc.Store to keep in sync, no risk of a stale format after a file is
swapped without re-triggering the picker callback).

On success the file-name / Confirm row is hidden again and the top-bar source line
becomes the permanent record; see `describe_source`, which stays honest about what's
actually in the database (not "which control last wrote to it") since either format,
or a dev DB seeded outside this control entirely, can be the current state.

History (expandable, read from the DB, no schema added): one row per distinct
`positions.as_of_date`, its position count -- BNP-sourced only, since a blotter
upload never writes `positions` (CURRENCY rows there are settlement-level cash
movements, not an EOD balance; see `import_blotter`'s docstring).
"""
from __future__ import annotations

import logging

from dash import Input, Output, State, dcc, html, no_update

from data.ingest.upload import decode, import_blotter, import_report, sniff_format, suggested_date

log = logging.getLogger(__name__)

SOURCE_LINE_ID = "data-source-line"
FILE_UPLOAD_ID = "report-file"
STAGE_ID = "report-stage"
FILENAME_ID = "report-filename"
MANUAL_DATE_ID = "report-manual-date"
DATE_PICKER_ID = "report-date"  # dcc.Store -- holds the resolved ISO date for a BNP upload
CONFIRM_ID = "report-import"
RESULT_ID = "report-result"
HISTORY_ID = "report-history"
HISTORY_DETAILS_ID = "report-history-details"


def describe_source(data: dict) -> str:
    """One line describing what's actually in the database, from ui.app.summary().

    Not "what was last uploaded" -- the summary dict is whole-database totals, so this
    stays honest about the *state* of the DB. Three cases:
      - positions > 0: a BNP snapshot exists -- keep the original "as of <date>" framing.
      - positions == 0 but trades > 0: only blotter-style trades are loaded, there is no
        EOD snapshot to name a date for.
      - trades == 0 too: nothing loaded yet.
    """
    trades = data.get("trades", 0)
    positions = data.get("positions", 0)
    if positions:
        return f"Loaded: BNP report as of {data['as_of_date']} ({trades} trades, {positions} positions)."
    if trades:
        return f"Loaded: {trades} trades in database (no BNP position snapshot)."
    return "No data loaded yet."


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
        body = html.P("No previous BNP snapshots.", className="section-kicker")
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
                       children=html.Button("Upload trade file", className="btn"),
                       accept=".csv,.xlsx,.xls", multiple=False, max_size=25 * 1024 * 1024),
            html.Div(id=SOURCE_LINE_ID, className="source-line", children=describe_source(data)),
        ]),
        html.Div(id=STAGE_ID, className="source-row source-row--stage", style={"display": "none"}, children=[
            html.Span(id=FILENAME_ID, className="source-file"),
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
        Output(f"{MANUAL_DATE_ID}-wrap", "style"), Output(MANUAL_DATE_ID, "value"),
        Output(DATE_PICKER_ID, "data"),
        Output(RESULT_ID, "children", allow_duplicate=True),
        Input(FILE_UPLOAD_ID, "contents"),
        State(FILE_UPLOAD_ID, "filename"), prevent_initial_call=True,
    )
    def _selected(contents, filename):
        # Nothing is written here -- this only decodes/sniffs enough to show the file
        # name, the date picker (BNP only), and validate shape. import_report()/
        # import_blotter() are called exclusively from _confirm().
        hidden = {"display": "none"}
        if not contents:
            return hidden, "", hidden, None, None, ""
        try:
            payload = decode(contents)
            fmt, _frame = sniff_format(payload, filename)
        except Exception as exc:
            return hidden, "", hidden, None, None, html.Span(str(exc), className="source-result--error")
        if fmt is None:
            msg = ("This file doesn't match either recognized format (BNP position "
                   "report or trade blotter).")
            return hidden, "", hidden, None, None, html.Span(msg, className="source-result--error")
        if fmt == "blotter":
            return {}, filename, hidden, None, None, ""
        # fmt == "bnp": resolve a snapshot date, same silent-when-possible behaviour
        # as before (2026-09-15 follow-up) -- only surface the manual date input when
        # the filename carries no recognisable date at all.
        date = suggested_date(filename)
        if date:
            return {}, filename, hidden, None, date, ""
        note = "This file name carries no recognisable date; enter the snapshot date, then press Confirm insert."
        return {}, filename, {}, None, None, note

    @app.callback(
        Output(RESULT_ID, "children"), Output(SOURCE_LINE_ID, "children"),
        Output("report-history-wrap", "children"),
        Output("cash-ladder-date", "date", allow_duplicate=True),
        Output(STAGE_ID, "style", allow_duplicate=True),
        Input(CONFIRM_ID, "n_clicks"),
        State(FILE_UPLOAD_ID, "contents"), State(FILE_UPLOAD_ID, "filename"),
        State(DATE_PICKER_ID, "data"), State(MANUAL_DATE_ID, "value"), prevent_initial_call=True,
    )
    def _confirm(clicks, contents, filename, as_of, manual_date):
        keep = (no_update, no_update, no_update, no_update)  # source line, history, ladder date, stage
        if not contents:
            return (no_update, *keep)
        db_path = get_db_path()
        try:
            payload = decode(contents)
            fmt, _frame = sniff_format(payload, filename)
        except Exception as exc:
            return (html.Span(f"Import failed; no data saved. {exc}", className="source-result--error"), *keep)
        if fmt == "blotter":
            try:
                message = import_blotter(payload, filename, db_path)
            except Exception as exc:
                return (html.Span(f"Import failed; no data saved. {exc}", className="source-result--error"), *keep)
            log.info("%s (blotter): %s", filename, message)
            from ui.app import load_summary
            data = load_summary(db_path)
            result = html.Div(message, className="source-result--info")
            return result, describe_source(data), history_layout(db_path), no_update, {"display": "none"}
        if fmt == "bnp":
            as_of = as_of or manual_date
            if not as_of:
                return (html.Span("Choose the snapshot date before confirming.", className="source-result--error"), *keep)
            try:
                message = import_report(payload, filename, as_of, db_path)
            except Exception as exc:
                return (html.Span(f"Import failed; no data saved. {exc}", className="source-result--error"), *keep)
            log.info("%s (BNP, snapshot %s): %s", filename, as_of, message)
            from ui.app import load_summary
            data = load_summary(db_path)
            result = html.Div(message, className="source-result--info")
            return result, describe_source(data), history_layout(db_path), as_of, {"display": "none"}
        return (html.Span("This file doesn't match either recognized format.", className="source-result--error"), *keep)
