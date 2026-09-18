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

On a successful import `_confirm` also publishes both `ui.revision` stores (2026-09-18),
which is what makes the header, the Ladder, the Blotter and Market data redraw from the
new book at once: before that, nothing but this strip's own status line heard about an
upload, and the page kept showing the old book until the browser was reloaded. A failed
import publishes neither.

On a successful import, `_trigger_feed_refresh` wakes `app.bloomberg_feed`
(`data.bloomberg.live.LiveFeed.trigger_now`, 2026-09-17) for one extra pull right away
so marks for the trades just uploaded are priced within seconds rather than waiting out
the feed's normal interval; a failed import never triggers it, and a machine with no
Bloomberg feed (`app.bloomberg_feed is None`) is a no-op, never an error.

The result box is transient (2026-09-18, user: "it's always there, it won't go, it needs
to go"); the source line is the durable record and is untouched by any of this:

  * a clean import (`source-result--info`) dismisses itself after `RESULT_DISMISS_MS`.
    The timer (`RESULT_TIMER_ID`) is enabled only while such a message is showing and is
    disabled again the moment the box empties, so it never ticks in the background;
  * an import that skipped unreadable rows (`source-result--warning`) or any error
    (`source-result--error`) never dismisses itself: the user has to be able to read
    which rows failed;
  * every message has a x button (`RESULT_CLOSE_ID`) that clears it at once;
  * choosing a new file clears whatever was showing.

All of that hangs off ONE fact, the box's own children: `_result_changed` opens or
closes the box and arms or disarms the timer from `result_kind`, and the x button and
the timer both simply empty the box. `_selected` and `_confirm` keep their outputs.

The staged file is released as soon as it is consumed or dropped: "Cancel" in the
Confirm row, or a successful import, sets the `dcc.Upload`'s `contents` back to None,
which `_selected` turns into a hidden Confirm row. Without that the browser kept the
whole file after the import and choosing the very same file again did nothing at all
(the Upload's props had not changed, so Dash had nothing to fire).

The "Pull Bloomberg now" button and its status line in the same row live in
`ui/feed_controls.py`; this module only places them and registers them.

Structured import report (2026-09-18, coordinator contract). `run_import` prefers
`data.ingest.upload.import_blotter_report(payload, filename, db_path) -> {"message",
"rejects", "warnings", "notes"}` when the ingest module has it, and otherwise calls
`import_blotter` exactly as before, so either side can land first. With the structure:

  * the box is `--warning` (sticky) when `rejects > 0` OR `warnings > 0` (cells the parser
    had to rebuild, ignore or distrust), else `--info` (dismisses itself). The old
    `REJECTS_PHRASE` rule still applies on top, so a sentence that says rows could not
    be read is never auto-dismissed even if a counter disagrees with it;
  * `notes` render as a short list under the headline instead of one run-on line. The
    ingest sentence carries the notes too, so the headline is that sentence minus
    whatever is reproduced verbatim in the list: nothing is dropped, nothing shown twice.

One rule is easy to miss: the structure is used only while the `import_blotter` bound in
THIS module is still the ingest module's own function. `import_blotter` here is the seam
callers and tests substitute (`tests/test_ui.py` patches exactly that name); if it has
been substituted, the substitute IS the import, and calling the ingest module's report
function instead would silently import something else.
"""
from __future__ import annotations

import logging
import re

from dash import Input, Output, State, dcc, html, no_update

from data.ingest import upload as _ingest
from data.ingest.upload import decode, import_blotter, preview_frame, validate_blotter_shape
from ui import feed_controls, revision

log = logging.getLogger(__name__)

SOURCE_LINE_ID = "data-source-line"
FILE_UPLOAD_ID = "report-file"
STAGE_ID = "report-stage"
FILENAME_ID = "report-filename"
CONFIRM_ID = "report-import"
CANCEL_ID = "report-cancel"
RESULT_ID = "report-result"
RESULT_BOX_ID = "report-result-box"
RESULT_CLOSE_ID = "report-result-close"
RESULT_TIMER_ID = "report-result-timer"

RESULT_DISMISS_MS = 8_000
NOTES_MAX_HEIGHT = "240px"      # see report_result: measured, not guessed
BOX_CLOSED = "source-result-box"
BOX_OPEN = "source-result-box source-result-box--open"

# The phrase the ingest sentence uses for rows it had to skip: the fallback rule when there
# is no structured report, and a safety net when there is. The ingest module owns it since
# 2026-09-18 (`data.ingest.upload.REJECTS_PHRASE`); read from there so the two cannot drift,
# with the literal only for an ingest module that predates it. Pinned by tests on both sides.
REJECTS_PHRASE = str(getattr(_ingest, "REJECTS_PHRASE", "could not be read")).lower()


def describe_source(data: dict) -> str:
    """One line describing what's in the database (whole-database totals from
    ui.app.summary(), not "what was last uploaded")."""
    trades = data.get("trades", 0)
    if trades:
        return f"Loaded: {trades} trades in database."
    return "No data loaded yet."


def _class_name(children) -> str:
    """className of a result, whether it is still a Dash component (a test, or a
    callback's own return value) or already the JSON the browser sends back."""
    if isinstance(children, (list, tuple)):
        return " ".join(_class_name(c) for c in children)
    if isinstance(children, dict):
        return str((children.get("props") or {}).get("className") or "")
    return str(getattr(children, "className", "") or "")


def result_kind(children) -> str:
    """"" when the box is empty, else "info" | "warning" | "error". Anything showing
    that is not recognisably a clean import counts as an error, so an unclassifiable
    message is never dismissed from under the user."""
    if children is None or children == "" or children == [] or children == ():
        return ""
    name = _class_name(children)
    for kind in ("error", "warning", "info"):
        if f"source-result--{kind}" in name:
            return kind
    return "error"


def auto_dismisses(children) -> bool:
    """Only a clean import dismisses itself; rejects and errors stay until closed."""
    return result_kind(children) == "info"


def import_result(message: str):
    """The import's summary sentence, verbatim: `--info` when every row loaded,
    `--warning` (never auto-dismissed) when it names rows that were skipped. The
    fallback path when the ingest side offers no structured report."""
    kind = "warning" if REJECTS_PHRASE in (message or "").lower() else "info"
    return html.Div(message, className=f"source-result--{kind}")


def _count(value):
    """A non-negative int from a report counter; None when it is not usable as one (the
    caller then decides from the sentence instead of guessing zero). A list is counted,
    in case the ingest side hands over the rejects themselves rather than how many."""
    if value is None or isinstance(value, str):
        return None
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def normalise_report(raw) -> dict:
    """`{"message", "rejects", "warnings", "notes", "structured"}` from whatever the import
    returned: the contract's dict, or the plain sentence `import_blotter` returns.

    Tolerant on purpose (the ingest side is being written in parallel): a missing or
    unusable `rejects` falls back to the phrase rule rather than to zero, so skipped
    rows are never auto-dismissed because a key was absent."""
    if not isinstance(raw, dict):
        message = "" if raw is None else str(raw)
        return {"message": message, "rejects": int(REJECTS_PHRASE in message.lower()),
                "warnings": 0, "notes": [], "structured": False}
    message = str(raw.get("message") or "")
    rejects = _count(raw.get("rejects"))
    notes = raw.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    return {"message": message,
            "rejects": int(REJECTS_PHRASE in message.lower()) if rejects is None else rejects,
            "warnings": _count(raw.get("warnings")) or 0,
            "notes": [str(n).strip() for n in notes if str(n).strip()],
            "structured": True}


def is_sticky(report: dict) -> bool:
    """Rejects or parser warnings keep the box open until the user closes it. The phrase
    rule stays on top: a sentence saying rows could not be read wins over a counter."""
    return (report["rejects"] > 0 or report["warnings"] > 0
            or REJECTS_PHRASE in report["message"].lower())


def headline_without_notes(message: str, notes) -> str:
    """The ingest sentence minus each note it reproduces verbatim, for display above the
    notes list. Only text that reappears in the list is removed, so nothing is lost; a
    note the sentence words differently simply stays in both. Falls back to the whole
    sentence if stripping would leave next to nothing."""
    text = message
    for note in sorted(notes, key=len, reverse=True):      # longest first: one note may contain another
        core = note.strip().rstrip(".").strip()
        if core:
            text = re.sub(re.escape(core) + r"\.?", "", text, count=1)
    text = re.sub(r"\s+([.;,:])", r"\1", text)              # " ." left behind by a removed note
    text = re.sub(r"([;,])(\s*[;,.])+", r"\1", text)         # "; ; ." -> ";"
    text = re.sub(r"\s{2,}", " ", text).strip(" ;,:")
    return text if len(text) >= 12 else message


def report_result(report: dict):
    """The box's content for one import. Same single-Div shape as `import_result` when
    there are no notes to list; with notes, a headline and a short list under it."""
    kind = "warning" if is_sticky(report) else "info"
    if not report["structured"]:
        return import_result(report["message"])
    if not report["notes"]:
        return html.Div(report["message"], className=f"source-result--{kind}")
    # Inline styles, not the stylesheet: the list must stay compact and, when the ingest
    # side sends many notes, scroll inside the box rather than grow down the page. The
    # cap is sized from the REAL notes, which run to ~190 characters (three lines each in
    # the 560px box): four of them measured 180px in the browser at 1366px, so 240px shows
    # four to five whole and only a longer list scrolls. (132px, sized from the contract's
    # short examples, hid the end of the fourth note.)
    return html.Div(className=f"source-result--{kind}", children=[
        html.Div(headline_without_notes(report["message"], report["notes"]), className="source-result-headline"),
        html.Ul([html.Li(note, style={"margin": "2px 0"}) for note in report["notes"]],
                className="source-result-notes",
                style={"margin": "6px 0 0", "paddingLeft": "18px", "maxHeight": NOTES_MAX_HEIGHT, "overflowY": "auto"}),
    ])


def run_import(payload, filename, db_path) -> dict:
    """Run the import and return the normalised report. Prefers the ingest module's
    structured `import_blotter_report` when it exists AND this module's `import_blotter`
    has not been substituted (see the module docstring); otherwise the sentence path."""
    report_fn = getattr(_ingest, "import_blotter_report", None)
    if callable(report_fn) and import_blotter is getattr(_ingest, "import_blotter", None):
        return normalise_report(report_fn(payload, filename, db_path))
    return normalise_report(import_blotter(payload, filename, db_path))


def layout(data: dict = None):
    data = data or {}
    return html.Div(className="source-strip", children=[
        html.Div(className="source-row", children=[
            dcc.Upload(id=FILE_UPLOAD_ID, className="source-upload",
                       children=html.Button("Upload trade file", className="btn"),
                       accept=".csv,.xlsx,.xls", multiple=False, max_size=25 * 1024 * 1024),
            html.Div(id=SOURCE_LINE_ID, className="source-line", children=describe_source(data)),
            *feed_controls.controls(),
        ]),
        # Everything that drops below the bar, stacked, so a failed import's message sits
        # under the Confirm row it belongs to instead of on top of it.
        html.Div(className="source-drop", children=[
            html.Div(id=STAGE_ID, className="source-row source-row--stage", style={"display": "none"}, children=[
                html.Span(id=FILENAME_ID, className="source-file"),
                html.Button("Confirm insert", id=CONFIRM_ID, n_clicks=0, className="btn"),
                html.Button("Cancel", id=CANCEL_ID, n_clicks=0, className="btn btn--ghost"),
            ]),
            # Closed = no panel, no x, but the Loading dots still show while an import runs.
            html.Div(id=RESULT_BOX_ID, className=BOX_CLOSED, children=[
                dcc.Loading(type="dot", color="#1f5fbf",
                            children=html.Div(id=RESULT_ID, role="status", className="source-result")),
                html.Button("×", id=RESULT_CLOSE_ID, n_clicks=0, className="source-result-close",
                            title="Dismiss this message", **{"aria-label": "Dismiss this message"}),
            ]),
        ]),
        dcc.Interval(id=RESULT_TIMER_ID, interval=RESULT_DISMISS_MS, n_intervals=0, disabled=True),
        *feed_controls.plumbing(),
    ])


def _trigger_feed_refresh(app) -> None:
    """Wake the Bloomberg feed for one extra pull right away after a successful
    upload, instead of leaving marks for the trades just uploaded to wait out the
    rest of the feed's normal interval (`data.bloomberg.live.LiveFeed.trigger_now`
    names this exact call site in its own docstring, 2026-09-17). `app.bloomberg_feed`
    is `None` whenever no Bloomberg session is available (`ui.app.create_app`'s
    `start_feed=False`, or `start_feed_if_available` found nothing) -- guarded for
    `None` and for the attribute being absent entirely (e.g. a lightweight test
    double standing in for `app`), so a Bloomberg-less machine never raises here."""
    feed = getattr(app, "bloomberg_feed", None)
    if feed is not None:
        feed.trigger_now()


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
            # The staged file was released (`_release_file`: Cancel, or an import that
            # just succeeded). Hide the Confirm row; the message is not this branch's to
            # clear, or a successful import would wipe its own summary.
            return hidden, "", no_update
        try:
            payload = decode(contents)
            validate_blotter_shape(preview_frame(payload, filename))
        except Exception as exc:
            return hidden, "", html.Span(str(exc), className="source-result--error")
        return {}, filename, ""

    @app.callback(
        Output(RESULT_ID, "children"), Output(SOURCE_LINE_ID, "children"),
        Output(STAGE_ID, "style", allow_duplicate=True),
        Output(revision.DATA_REVISION_ID, "data", allow_duplicate=True),
        Output(revision.BOOK_REVISION_ID, "data", allow_duplicate=True),
        Input(CONFIRM_ID, "n_clicks"),
        State(FILE_UPLOAD_ID, "contents"), State(FILE_UPLOAD_ID, "filename"),
        prevent_initial_call=True,
    )
    def _confirm(clicks, contents, filename):
        keep = (no_update, no_update, no_update, no_update)  # source line, stage, data rev, book rev
        if not contents:
            return (no_update, *keep)
        db_path = get_db_path()
        try:
            report = run_import(decode(contents), filename, db_path)
        except Exception as exc:
            return (html.Span(f"Import failed; no data saved. {exc}", className="source-result--error"), *keep)
        # The box is transient; the log keeps the whole sentence, the counters and the notes.
        log.info("%s (blotter): %s [rejects=%s warnings=%s notes=%s]", filename, report["message"],
                 report["rejects"], report["warnings"], report["notes"])
        from ui.app import load_summary
        data = load_summary(db_path)
        _trigger_feed_refresh(app)
        result = report_result(report)
        # Publish both revisions now (ui/revision.py) so the header and every tab redraw
        # from the new book straight away, no browser reload and no wait for the poll.
        return (result, describe_source(data), {"display": "none"},
                revision.file_signature(db_path), revision.book_signature(db_path))

    @app.callback(
        Output(RESULT_BOX_ID, "className"), Output(RESULT_TIMER_ID, "disabled"),
        Input(RESULT_ID, "children"),
    )
    def _result_changed(children):
        # The box's own children are the single fact everything else follows: open the
        # panel (and its x) when there is a message, arm the dismiss timer only for a
        # clean import, and disarm it the moment the box empties. Enabling a disabled
        # dcc.Interval starts a fresh full interval, so each message gets its own 8 s.
        if not result_kind(children):
            return BOX_CLOSED, True
        return BOX_OPEN, not auto_dismisses(children)

    @app.callback(
        Output(RESULT_ID, "children", allow_duplicate=True),
        Input(RESULT_CLOSE_ID, "n_clicks"), Input(RESULT_TIMER_ID, "n_intervals"),
        prevent_initial_call=True,
    )
    def _dismiss(clicks, ticks):
        # x or the timer: both just empty the box; `_result_changed` does the rest.
        if not clicks and not ticks:
            return no_update
        return ""

    @app.callback(
        Output(FILE_UPLOAD_ID, "contents"), Output(FILE_UPLOAD_ID, "filename"),
        Input(CANCEL_ID, "n_clicks"), Input(SOURCE_LINE_ID, "children"),
        prevent_initial_call=True,
    )
    def _release_file(_cancel_clicks, _source_line):
        # Cancel drops the staged file; a successful import (the only thing that rewrites
        # the source line) has consumed it. Either way `_selected` then hides the Confirm
        # row, and the same file can be chosen again later.
        return None, None

    feed_controls.register(app, get_db_path)
