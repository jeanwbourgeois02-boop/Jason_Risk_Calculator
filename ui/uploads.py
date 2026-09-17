"""Trade file upload control (docs: user decisions 2026-09-15, item B; date-picker
removed by the coordinator's same-day follow-up; blotter-only pivot 2026-09-16, briefly
restored to dual-format the same day, then reverted to blotter-only for good 2026-09-17
-- user's own words: "fix the excel import - new file and only new file - make it as
flexible as possible", after confirming directly that he doesn't need the BNP-sourced
cash-balance data (he wants delta exposure and cashflow timing on the Ladder tab, which
the blotter alone already provides in full). ``data/ingest/upload.py`` no longer even
contains BNP-upload code (not just unreachable -- removed); `data/ingest/bnp.py` itself
is untouched and still used as a library by `data/load.py`'s CLI, just no longer
reachable from this control.

One button, "Upload trade file" (.xlsx/.xls/.csv), accepting only the trade blotter
shape (`data/ingest/blotter.py` / `data.ingest.upload.import_blotter`, which is now
deliberately tolerant -- see that module's own docstring -- of BOM-prefixed files,
mixed-case headers, extra/reordered/unknown columns, and individual malformed rows
that would otherwise block an entire good file).

There is no date picker anywhere in this control. A blotter file has no single EOD
snapshot date -- each row carries its own `TradeDate` / settle date, so there is
nothing to pick, resolve, or gate the Confirm button on.

Flow: choose file -> "Confirm insert" -> dcc.Loading ("Importing...") -> one small status
line under the button. Nothing is written to the database before Confirm is pressed
(`import_blotter` is only called from the Confirm callback, never from the file-picked
callback). The file-picked callback only decodes enough to validate size/shape
(`decode()`, `data.ingest.upload.validate_blotter_shape`) and show the file name and
Confirm button; an unrecognized file shows a clean error and no Confirm button.

On success the file-name / Confirm row is hidden again (user direction 2026-09-15: no
need to keep that around) and the top-bar source line becomes the permanent record. See
`describe_source` for its wording, which stays honest about what's actually in the
database (from `ui.app.load_summary`), not which control last wrote to it, since a dev
database seeded outside this control (e.g. via `data/load.py`'s CLI) can still hold BNP
`positions` rows.

The loader's own summary (new-trade / leg / excluded / rejected-row counts from
`import_blotter`) is real information about what happened to the user's data and must
be seen, not just logged. It renders once, right after Confirm, as a single
`.source-result--info` line holding the loader's sentence verbatim. Errors show in the
result line, in red (`.source-result--error`), with the Confirm row left in place so
the user can retry.

History (expandable, read from the DB, no schema added): one row per distinct
`positions.as_of_date`, its position count. A blotter upload never writes `positions`
(CURRENCY rows there are settlement-level cash movements, not an EOD balance; see
`import_blotter`'s docstring), so on a database that has only ever received blotter
uploads this list stays empty -- expected, not a bug, since the whole point of this
change is that BNP-sourced `positions` are no longer part of the app's own data flow.
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
HISTORY_ID = "report-history"
HISTORY_DETAILS_ID = "report-history-details"


def describe_source(data: dict) -> str:
    """One line describing what's actually in the database, from ui.app.summary().

    Not "what was last uploaded" -- the summary dict is whole-database totals, so this
    stays honest about the *state* of the DB:
      - positions > 0: a BNP snapshot exists (from outside this control, e.g. a dev DB
        seeded via `data/load.py`'s CLI) -- keep the "as of <date>" framing since it's
        genuinely informative.
      - positions == 0 but trades > 0: only blotter-style trades are loaded, the normal
        case for a database that only ever went through this control.
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
    schema). Empty list if the DB is missing or has no positions yet -- the normal
    case now, since this control never writes `positions` itself."""
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
            html.Button("Confirm insert", id=CONFIRM_ID, n_clicks=0, className="btn"),
        ]),
        dcc.Loading(type="dot", color="#1f5fbf", children=html.Div(id=RESULT_ID, role="status", className="source-result")),
        html.Div(id="report-history-wrap", children=initial_history),
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
        Output("report-history-wrap", "children"),
        Output(STAGE_ID, "style", allow_duplicate=True),
        Input(CONFIRM_ID, "n_clicks"),
        State(FILE_UPLOAD_ID, "contents"), State(FILE_UPLOAD_ID, "filename"),
        prevent_initial_call=True,
    )
    def _confirm(clicks, contents, filename):
        keep = (no_update, no_update, no_update)  # source line, history, stage
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
        return result, describe_source(data), history_layout(db_path), {"display": "none"}
