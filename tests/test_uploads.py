"""ui/uploads.py: the blotter upload control.

An upload asks nothing of Bloomberg (user decision 2026-09-21: Bloomberg is pulled on
request only, by the "Pull Bloomberg now" button); until then a successful import woke
the feed for a pull. `app.bloomberg_feed` is `None` with `create_app(start_feed=False)`,
the default in tests -- that must never raise.
"""
from __future__ import annotations

import base64

import pytest

pytest.importorskip("dash", reason="dash is not installed in this environment")

from ui import app as uiapp  # noqa: E402
from ui import uploads  # noqa: E402


class _FakeFeed:
    def __init__(self):
        self.triggered = 0

    def trigger_now(self):
        self.triggered += 1


def _confirm_callback(app):
    # Multi-output callback keyed by all its outputs joined with '..' plus a hash
    # suffix (same convention as tests/test_ui.py's _report_confirm_callback) --
    # match by prefix rather than the exact key.
    key = next(k for k in app.callback_map
               if k.startswith(f"..{uploads.RESULT_ID}.children...{uploads.SOURCE_LINE_ID}"))
    cb = app.callback_map[key]["callback"]
    return getattr(cb, "__wrapped__", cb)


def _data_url() -> str:
    return "data:application/octet-stream;base64," + base64.b64encode(b"x").decode()


def test_successful_import_asks_nothing_of_bloomberg(tmp_path, monkeypatch):
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fake_feed = _FakeFeed()
    app.bloomberg_feed = fake_feed
    fn = _confirm_callback(app)

    monkeypatch.setattr(uploads, "import_blotter", lambda *a, **k: "Imported blotter.csv: 1 trade.")
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "none", "trades": 1, "positions": 0})

    fn(1, _data_url(), "blotter.csv")

    assert fake_feed.triggered == 0      # 2026-09-21: on request only, an upload pulls nothing


def test_failed_import_does_not_trigger_a_feed_pull(tmp_path, monkeypatch):
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fake_feed = _FakeFeed()
    app.bloomberg_feed = fake_feed
    fn = _confirm_callback(app)

    def _reject(*a, **k):
        raise ValueError("This file is not a trade blotter.")

    monkeypatch.setattr(uploads, "import_blotter", _reject)
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")

    fn(1, _data_url(), "not-a-blotter.csv")

    assert fake_feed.triggered == 0


def test_successful_import_with_no_bloomberg_feed_does_not_raise(tmp_path, monkeypatch):
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    assert app.bloomberg_feed is None  # start_feed=False -> no feed, the common test/no-Bloomberg case
    fn = _confirm_callback(app)

    monkeypatch.setattr(uploads, "import_blotter", lambda *a, **k: "Imported blotter.csv: 1 trade.")
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "none", "trades": 1, "positions": 0})

    result, source_line, stage_style, data_rev, book_rev = fn(1, _data_url(), "blotter.csv")
    assert source_line == "Loaded: 1 trades in database."
    # ui/revision.py (2026-09-18): a successful import publishes both revisions, which is
    # what makes the header and every tab redraw without a browser reload.
    assert isinstance(data_rev, str) and data_rev
    assert isinstance(book_rev, str) and book_rev


def test_uploads_has_no_way_to_pull_bloomberg():
    """2026-09-21: the helper that woke the feed after an import is gone, not just unused."""
    assert not hasattr(uploads, "_trigger_feed_refresh")


# ---------------------------------------------------------------- transient result box
# (2026-09-18, user: "it's always there, it won't go, it needs to go"). A clean import
# dismisses itself; rejects and errors stay until closed; x and a new selection clear it.

CLEAN = ("Imported blotter.csv: 2 trades -- 1 forwards, 0 spot, 1 futures, 0 options, 0 rate swaps; "
         "4 legs. 0 cash rows seen (0 of them spot fills).")
WITH_REJECTS = CLEAN + " 1 row(s) could not be read and were skipped: row 7 EURUSD: no value date."


def _callback_for_input(app, component_id, prop):
    """The raw function of the one callback that listens to `component_id.prop`."""
    matches = [entry for entry in app.callback_map.values()
               if any(i["id"] == component_id and i["property"] == prop for i in entry["inputs"])]
    assert len(matches) == 1, f"{len(matches)} callbacks listen to {component_id}.{prop}"
    cb = matches[0]["callback"]
    return getattr(cb, "__wrapped__", cb)


def _as_browser_json(component):
    """What Dash hands a callback for a component-valued Input: plain JSON, not the object."""
    return component.to_plotly_json() if hasattr(component, "to_plotly_json") else component


def _confirm_with(tmp_path, monkeypatch, message):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    monkeypatch.setattr(uploads, "import_blotter", lambda *a, **k: message)
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "none", "trades": 2})
    return app, _confirm_callback(app)(1, _data_url(), "blotter.csv")


def test_clean_import_opens_the_box_and_arms_the_dismiss_timer(tmp_path, monkeypatch):
    app, (result, _line, stage_style, _d, _b) = _confirm_with(tmp_path, monkeypatch, CLEAN)
    assert result.className == "source-result--info"
    assert stage_style == {"display": "none"}           # the Confirm row hides after a good import

    box_class, timer_disabled = _callback_for_input(app, uploads.RESULT_ID, "children")(_as_browser_json(result))
    assert box_class == uploads.BOX_OPEN
    assert timer_disabled is False                       # armed: it will dismiss itself


def test_dismiss_timer_is_about_eight_seconds_and_starts_disabled():
    layout = uploads.layout({})
    timer = next(c for c in layout.children if getattr(c, "id", None) == uploads.RESULT_TIMER_ID)
    assert timer.disabled is True                        # never ticks while nothing is showing
    assert 6_000 <= timer.interval <= 10_000
    assert timer.interval == uploads.RESULT_DISMISS_MS


def test_timer_tick_empties_the_box_and_the_empty_box_disarms_the_timer(tmp_path):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    assert _callback_for_input(app, uploads.RESULT_TIMER_ID, "n_intervals")(0, 1) == ""
    # ... and the emptied box closes and switches the timer off again, so it cannot tick forever
    assert _callback_for_input(app, uploads.RESULT_ID, "children")("") == (uploads.BOX_CLOSED, True)
    assert _callback_for_input(app, uploads.RESULT_ID, "children")(None) == (uploads.BOX_CLOSED, True)


def test_import_with_rejects_does_not_auto_dismiss(tmp_path, monkeypatch):
    app, (result, _line, _stage, _d, _b) = _confirm_with(tmp_path, monkeypatch, WITH_REJECTS)
    assert result.className == "source-result--warning"
    assert "row 7 EURUSD" in str(result)                 # the failed rows are on the page, verbatim

    box_class, timer_disabled = _callback_for_input(app, uploads.RESULT_ID, "children")(_as_browser_json(result))
    assert box_class == uploads.BOX_OPEN                 # showing, and closable with x ...
    assert timer_disabled is True                        # ... but never dismissed from under the user


def test_failed_import_does_not_auto_dismiss_and_keeps_the_confirm_row(tmp_path, monkeypatch):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)

    def _reject(*a, **k):
        raise ValueError("This file is not a trade blotter.")

    monkeypatch.setattr(uploads, "import_blotter", _reject)
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    import dash
    result, _line, stage_style, _d, _b = _confirm_callback(app)(1, _data_url(), "x.csv")
    assert stage_style is dash.no_update                 # left in place for a retry (or Cancel)
    assert _callback_for_input(app, uploads.RESULT_ID, "children")(_as_browser_json(result)) == \
        (uploads.BOX_OPEN, True)


def test_unclassifiable_message_is_treated_as_sticky():
    assert uploads.result_kind("some bare string") == "error"
    assert uploads.auto_dismisses("some bare string") is False
    assert uploads.auto_dismisses({"props": {"className": "source-result--info", "children": "ok"}}) is True
    assert uploads.auto_dismisses({"props": {"className": "source-result--warning"}}) is False


def test_rejects_phrase_matches_what_the_loader_actually_writes(tmp_path):
    """`import_result` reads the loader's sentence; pin the coupling against the real
    loader so a reworded sentence fails here instead of silently auto-dismissing rejects."""
    import inspect
    from data.ingest import upload as loader
    assert uploads.REJECTS_PHRASE in inspect.getsource(loader.import_blotter)
    assert uploads.import_result(CLEAN).className == "source-result--info"
    assert uploads.import_result(WITH_REJECTS).className == "source-result--warning"


def test_x_button_clears_the_message(tmp_path):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    dismiss = _callback_for_input(app, uploads.RESULT_CLOSE_ID, "n_clicks")
    assert dismiss(1, 0) == ""                           # x, timer never ticked
    import dash
    assert dismiss(0, 0) is dash.no_update               # nothing clicked, nothing ticked

    layout_text = str(uploads.layout({}))
    assert uploads.RESULT_CLOSE_ID in layout_text and "Dismiss this message" in layout_text


def test_new_selection_clears_the_previous_message(tmp_path, monkeypatch):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    monkeypatch.setattr(uploads, "preview_frame", lambda payload, filename: object())
    monkeypatch.setattr(uploads, "validate_blotter_shape", lambda frame: None)

    stage_style, filename, result = _callback_for_input(app, uploads.FILE_UPLOAD_ID, "contents")(
        _data_url(), "next.csv")
    assert (stage_style, filename, result) == ({}, "next.csv", "")
    assert _callback_for_input(app, uploads.RESULT_ID, "children")(result) == (uploads.BOX_CLOSED, True)


def test_cancel_and_a_good_import_release_the_staged_file_and_hide_the_confirm_row(tmp_path):
    import dash
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    # Cancel (or the source line rewritten by a good import) drops the file from the Upload ...
    assert _callback_for_input(app, uploads.CANCEL_ID, "n_clicks")(1, "Loaded: 2 trades in database.") == (None, None)
    # ... which hides the Confirm row without wiping the import's own summary.
    stage_style, filename, result = _callback_for_input(app, uploads.FILE_UPLOAD_ID, "contents")(None, None)
    assert stage_style == {"display": "none"} and filename == ""
    assert result is dash.no_update
    assert "Cancel" in str(uploads.layout({}))


# ---------------------------------------------------------------- structured import report
# (2026-09-18 contract: data.ingest.upload.import_blotter_report -> {"message", "rejects",
# "warnings", "notes"}. It may or may not exist yet on the ingest side, so every test here
# installs or removes it explicitly, with raising=False.)

NOTES = ["10 swaps have no pay/receive in the file: assumed pay fixed",
         "3 options have no strike on file",
         "2 prices looked like dates ('24-Jul') and were rebuilt from NetInvoice / Quantity",
         "1 forward has no value date in the Description: taken from Settle Date"]


def _confirm_with_report(tmp_path, monkeypatch, report):
    """`_confirm` with a fake `import_blotter_report` on the ingest module; this module's
    own `import_blotter` is left alone, which is what selects the structured path."""
    from data.ingest import upload as ingest
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    calls = []
    monkeypatch.setattr(ingest, "import_blotter_report",
                        lambda payload, filename, db_path: calls.append(filename) or report, raising=False)
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "none", "trades": 2})
    outputs = _confirm_callback(app)(1, _data_url(), "blotter.csv")
    assert calls == ["blotter.csv"]                       # the structured function was the one called
    box = _callback_for_input(app, uploads.RESULT_ID, "children")(_as_browser_json(outputs[0]))
    return outputs, box


def test_report_with_parser_warnings_and_no_rejects_is_sticky(tmp_path, monkeypatch):
    (result, line, stage, data_rev, book_rev), (box_class, timer_disabled) = _confirm_with_report(
        tmp_path, monkeypatch, {"message": CLEAN, "rejects": 0, "warnings": 2, "notes": []})
    assert result.className == "source-result--warning"
    assert (box_class, timer_disabled) == (uploads.BOX_OPEN, True)      # never dismissed from under the user
    assert line == "Loaded: 2 trades in database." and stage == {"display": "none"}
    assert data_rev and book_rev                                          # still a successful import


def test_report_with_no_rejects_and_no_warnings_dismisses_itself(tmp_path, monkeypatch):
    (result, *_rest), (box_class, timer_disabled) = _confirm_with_report(
        tmp_path, monkeypatch, {"message": CLEAN, "rejects": 0, "warnings": 0, "notes": []})
    assert result.className == "source-result--info" and CLEAN in str(result)
    assert (box_class, timer_disabled) == (uploads.BOX_OPEN, False)     # timer armed


def test_report_with_rejects_is_sticky(tmp_path, monkeypatch):
    (result, *_rest), (box_class, timer_disabled) = _confirm_with_report(
        tmp_path, monkeypatch, {"message": WITH_REJECTS, "rejects": 1, "warnings": 0, "notes": []})
    assert result.className == "source-result--warning" and "row 7 EURUSD" in str(result)
    assert (box_class, timer_disabled) == (uploads.BOX_OPEN, True)


def test_counter_is_trusted_over_wording_but_the_phrase_rule_still_catches_a_disagreement():
    # rejects counted, sentence worded differently from REJECTS_PHRASE: the counter decides
    reworded = uploads.normalise_report({"message": "Imported x.csv. 4 rows skipped.", "rejects": 4, "warnings": 0})
    assert uploads.is_sticky(reworded)
    # counter says 0 but the sentence says rows could not be read: sticky, never hidden
    assert uploads.is_sticky(uploads.normalise_report({"message": WITH_REJECTS, "rejects": 0, "warnings": 0}))
    # a missing counter falls back to the sentence, not to zero
    assert uploads.normalise_report({"message": WITH_REJECTS})["rejects"] == 1
    assert uploads.normalise_report({"message": CLEAN})["rejects"] == 0
    # a list of rejects instead of a count is counted, not crashed on
    assert uploads.normalise_report({"message": CLEAN, "rejects": ["row 7"], "warnings": None})["rejects"] == 1


def test_without_the_report_function_confirm_falls_back_to_the_phrase_rule(tmp_path, monkeypatch):
    from data.ingest import upload as ingest
    monkeypatch.delattr(ingest, "import_blotter_report", raising=False)   # the ingest side has not landed it
    for message, expected in ((CLEAN, "source-result--info"), (WITH_REJECTS, "source-result--warning")):
        app, (result, *_rest) = _confirm_with(tmp_path, monkeypatch, message)
        assert result.className == expected
        assert result.children == message                                  # the old single-sentence shape
        assert _callback_for_input(app, uploads.RESULT_ID, "children")(_as_browser_json(result)) == \
            (uploads.BOX_OPEN, expected.endswith("warning"))


def test_a_substituted_import_blotter_wins_even_once_the_report_function_exists(tmp_path, monkeypatch):
    """The ordering trap: tests/test_ui.py patches only `ui.uploads.import_blotter`. When the
    ingest side lands `import_blotter_report`, a bare getattr preference would bypass that
    patch and run the REAL import on the test's junk payload. The substitute is the import."""
    from data.ingest import upload as ingest

    def _must_not_run(*a, **k):
        raise AssertionError("the ingest report function was called although import_blotter was substituted")

    monkeypatch.setattr(ingest, "import_blotter_report", _must_not_run, raising=False)
    _app, (result, line, *_rest) = _confirm_with(tmp_path, monkeypatch, CLEAN)   # patches uploads.import_blotter
    assert result.className == "source-result--info" and result.children == CLEAN
    assert line == "Loaded: 2 trades in database."


def test_notes_render_as_a_list_under_the_headline_with_nothing_lost_or_doubled(tmp_path, monkeypatch):
    message = CLEAN + " " + " ".join(n + "." for n in NOTES)               # the sentence carries the notes too
    (result, *_rest), (box_class, timer_disabled) = _confirm_with_report(
        tmp_path, monkeypatch, {"message": message, "rejects": 0, "warnings": 2, "notes": NOTES})
    headline, notes_list = result.children
    assert headline.children == CLEAN                                       # run-on notes stripped from the headline
    assert [li.children for li in notes_list.children] == NOTES             # each note once, in the ingest order
    rendered = str(result)
    assert all(rendered.count(note) == 1 for note in NOTES)                 # not shown twice
    assert (box_class, timer_disabled) == (uploads.BOX_OPEN, True)          # className is still on the outer Div


def test_headline_keeps_whatever_the_list_does_not_reproduce():
    # a note the sentence words differently stays in the sentence: nothing is ever dropped
    message = CLEAN + " Ten swaps were assumed pay fixed. 3 options have no strike on file."
    headline = uploads.headline_without_notes(message, NOTES[:2])
    assert headline == CLEAN + " Ten swaps were assumed pay fixed."
    # a sentence that is nothing BUT its notes is shown whole rather than as an empty headline
    assert uploads.headline_without_notes(NOTES[1] + ".", [NOTES[1]]) == NOTES[1] + "."
    # notes joined with semicolons leave no "; ; ." debris behind
    joined = CLEAN + " Notes: " + "; ".join(NOTES) + "."
    assert uploads.headline_without_notes(joined, NOTES) == CLEAN + " Notes"


SAMPLE_BLOTTER = __import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "sample" / "blotter_sample.csv"


def _real_report_function_or_skip():
    from data.ingest import upload as ingest
    if not callable(getattr(ingest, "import_blotter_report", None)):
        pytest.skip("data.ingest.upload.import_blotter_report has not landed yet")
    if not SAMPLE_BLOTTER.exists():
        pytest.skip(f"sample blotter not found: {SAMPLE_BLOTTER}")
    return ingest


def test_real_ingest_report_on_the_clean_sample_is_structured_clean_and_lists_its_notes(tmp_path):
    """Against the REAL ingest function, not a fake: the contract as it actually landed."""
    ingest = _real_report_function_or_skip()
    assert uploads.REJECTS_PHRASE == ingest.REJECTS_PHRASE.lower()          # one phrase, owned by ingest
    report = uploads.run_import(SAMPLE_BLOTTER.read_bytes(), SAMPLE_BLOTTER.name, tmp_path / "risk.db")
    assert report["structured"] is True
    assert (report["rejects"], report["warnings"]) == (0, 0) and not uploads.is_sticky(report)
    result = uploads.report_result(report)
    assert result.className == "source-result--info"                         # a clean import: dismisses itself
    if report["notes"]:                                                      # information-only notes, per ingest
        headline, notes_list = result.children
        assert headline.children.startswith(f"Imported {SAMPLE_BLOTTER.name}:")
        assert [li.children for li in notes_list.children] == report["notes"]
        assert not any(note in headline.children for note in report["notes"])   # stripped from the run-on line
        assert all(str(result).count(note[:60]) == 1 for note in report["notes"])


def test_real_ingest_report_with_one_unreadable_row_is_sticky(tmp_path):
    ingest = _real_report_function_or_skip()
    from data.ingest import blotter
    frame = blotter.read_table(SAMPLE_BLOTTER)
    first_forward = frame[frame["Fin Type"] == "FORWARD"].index[0]
    frame.loc[first_forward, "Buy Currency"] = "EUR.C-EUAA"                  # contradicts the row's own description
    report = uploads.run_import(frame.to_csv(index=False).encode(), "x.csv", tmp_path / "risk.db")
    assert report["rejects"] == 1 and uploads.is_sticky(report)
    result = uploads.report_result(report)
    assert result.className == "source-result--warning"
    assert uploads.REJECTS_PHRASE in str(result).lower()                     # which row failed is on the page


def test_report_that_is_not_a_dict_is_treated_as_the_plain_sentence():
    report = uploads.normalise_report(WITH_REJECTS)
    assert report["structured"] is False and report["rejects"] == 1
    assert uploads.report_result(report).className == "source-result--warning"
    assert uploads.report_result(uploads.normalise_report(None)).className == "source-result--info"


# ---------------------------------------------------------------- "Pull Bloomberg now"
from datetime import datetime, timedelta, timezone  # noqa: E402

from ui import feed_controls, revision  # noqa: E402

T0 = datetime(2026, 9, 18, 14, 32, 5, tzinfo=timezone.utc)


def _status(at, **over):
    base = {"time": at.isoformat(), "connected": True, "reason": "", "written": 212, "failed": 3}
    return {**base, **over}


def test_pull_button_sits_in_the_upload_row_with_its_status_line():
    layout = uploads.layout({})
    row = layout.children[0]
    ids = [getattr(c, "id", None) for c in row.children]
    assert uploads.FILE_UPLOAD_ID in ids and feed_controls.PULL_BUTTON_ID in ids
    assert ids.index(feed_controls.PULL_STATUS_ID) == ids.index(feed_controls.PULL_BUTTON_ID) + 1
    button = row.children[ids.index(feed_controls.PULL_BUTTON_ID)]
    assert button.children == "Pull Bloomberg now"
    # 2026-09-21 (user: "put on the top top headline ... clicked from wherever in the app"):
    # the bar draws the row right to left, so the FIRST child is the far right corner --
    # the pull button, its status line beside it, a divider, then the upload control.
    assert ids[:2] == [feed_controls.PULL_BUTTON_ID, feed_controls.PULL_STATUS_ID]
    assert row.children[2].className == "top-bar-divider" and ids.index(uploads.FILE_UPLOAD_ID) == 3
    css = (__import__("pathlib").Path(uploads.__file__).parent / "assets" / "style.css").read_text(encoding="utf-8")
    assert ".top-bar { position: sticky; top: 0;" in css     # the bar stays in reach at any scroll position
    # beside the button itself the line does not repeat "pulls only when you press ..."
    beside = feed_controls.poll_outcome(_status(T0), None, feed=object())[0]
    assert "212 marks written" in beside and feed_controls.ON_REQUEST_WORDS not in beside
    poll = next(c for c in layout.children if getattr(c, "id", None) == feed_controls.PULL_POLL_ID)
    assert poll.disabled is True                          # runs only while a pull is outstanding


def test_pull_button_with_a_feed_calls_trigger_now_once_even_when_clicked_rapidly(tmp_path):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    app.bloomberg_feed = _FakeFeed()
    clicked = _callback_for_input(app, feed_controls.PULL_BUTTON_ID, "n_clicks")

    line, tooltip, pending, poll_disabled, button_disabled = clicked(1)
    assert "pull requested..." in line
    assert tooltip == line                                       # the clamped line's full text, on hover
    assert pending and pending["requested_at"]
    assert poll_disabled is False and button_disabled is True   # poll on, button off while waiting
    for n in (2, 3, 4):                                          # impatient clicks
        again, _tip, pending_again, _p, _b = clicked(n)
        assert "pull requested..." in again
        assert pending_again["requested_at"] == pending["requested_at"]
    assert app.bloomberg_feed.triggered == 1


def test_guard_allows_a_new_pull_once_the_previous_one_has_landed_or_timed_out():
    guard, feed = feed_controls.PullGuard(), _FakeFeed()
    before = _status(T0 - timedelta(minutes=1))
    _pending, triggered = guard.request(feed, before, now=T0)
    assert triggered and feed.triggered == 1
    assert guard.request(feed, before, now=T0 + timedelta(seconds=5))[1] is False
    assert guard.request(feed, _status(T0 + timedelta(seconds=1)), now=T0 + timedelta(seconds=30))[1] is True
    late = T0 + timedelta(seconds=31 + feed_controls.PULL_TIMEOUT_SECONDS)
    assert guard.request(feed, _status(T0 + timedelta(seconds=1)), now=late)[1] is True
    assert feed.triggered == 3


def test_pull_button_with_no_feed_says_so_plainly_and_asks_for_nothing(tmp_path):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    assert app.bloomberg_feed is None
    line, tooltip, pending, poll_disabled, button_disabled = _callback_for_input(
        app, feed_controls.PULL_BUTTON_ID, "n_clicks")(1)
    assert line.startswith("Bloomberg is not connected on this machine: ")
    assert tooltip == line
    assert uiapp.FEED_NOT_REQUESTED in line
    assert pending is None and poll_disabled is True and button_disabled is False

    app.bloomberg_feed_reason = "no Bloomberg API service on localhost:8194"
    assert feed_controls.not_connected_message(app, None) == \
        "Bloomberg is not connected on this machine: no Bloomberg API service on localhost:8194"

    class _Bare:                                            # no feed, no recorded reason: the status file's
        pass

    assert "blpapi is not installed" in feed_controls.not_connected_message(
        _Bare(), {"connected": False, "reason": "blpapi is not installed on this computer"})
    assert feed_controls.NO_FEED_REASON in feed_controls.not_connected_message(_Bare(), None)


def test_status_line_is_built_from_the_status_dict(monkeypatch):
    now = T0 + timedelta(minutes=1)
    line = feed_controls.feed_headline(_status(T0), 120, feed_running=True, now=now)
    assert "connected" in line and "not connected" not in line
    assert f"last pull {T0.astimezone().strftime('%H:%M:%S')}" in line
    assert "212 marks written, 3 failed" in line
    assert line.endswith(feed_controls.ON_REQUEST_WORDS) and "automatically" not in line
    assert "took" not in line                                # no timings in the status file: nothing said

    down = feed_controls.feed_headline(
        _status(T0, connected=False, reason="no Bloomberg API service on localhost:8194"), 120,
        feed_running=False, now=now)
    assert "not connected — no Bloomberg API service on localhost:8194" in down
    assert "status as of" in down and "last pull" not in down   # nothing was pulled, so it does not say so
    assert down.endswith(feed_controls.NO_FEED_WORDS) and "every 2 min" not in down
    # the startup placeholder's timestamp is not an attempt at anything, and is not called one
    placeholder = feed_controls.feed_headline(
        _status(T0, connected=False, reason="no pull has run yet", written=0, failed=0), 120, feed_running=False, now=now)
    assert "attempt" not in placeholder
    assert feed_controls.feed_headline(None) == f"Bloomberg: no pull recorded yet · {feed_controls.ON_REQUEST_WORDS}"


def test_headline_never_claims_a_cadence_and_the_safety_net_interval_is_still_read(monkeypatch):
    """2026-09-21: no pull is scheduled, so no headline says "every N minutes", whatever
    interval is passed or on file. The interval is only the screens' own re-read timer."""
    from data.bloomberg import live
    for seconds in (None, 120, 900):
        line = feed_controls.feed_headline(_status(T0), seconds, feed_running=True)
        assert line.endswith("pulls only when you press Pull Bloomberg now") and "every" not in line
    monkeypatch.setattr(live, "INTERVAL_SECONDS", 300)
    assert feed_controls.feed_interval_seconds() == 300

    class _Feed:
        interval = 45

    assert feed_controls.feed_interval_seconds(_Feed()) == 45   # a running feed's own interval wins
    assert (feed_controls.cadence_words(45), feed_controls.cadence_words(90), feed_controls.cadence_words(3600)) == \
        ("every 45 s", "every 1 min 30 s", "every 60 minutes")
    assert (feed_controls.cadence_words(900), feed_controls.cadence_words(60)) == ("every 15 minutes", "every minute")


def test_last_pull_duration_is_in_the_headline_only_when_the_status_file_has_it():
    now = T0 + timedelta(minutes=1)
    timed = _status(T0, timings={"session": 1.2, "spot": 3.1, "forwards": 21.4, "total": 48.3})
    line = feed_controls.feed_headline(timed, 900, feed_running=True, now=now)
    assert f"212 marks written, 3 failed · last pull took 48 s · {feed_controls.ON_REQUEST_WORDS}" in line
    assert "last pull took 3.4 s" in feed_controls.feed_headline(_status(T0, timings={"total": 3.42}), 900, now=now)
    for absent in ({}, {"timings": None}, {"timings": {}}, {"timings": {"spot": 2.0}},
                   {"timings": {"total": "n/a"}}, {"timings": "12"}):
        assert "took" not in feed_controls.feed_headline(_status(T0, **absent), 900, feed_running=True, now=now)
    # a pull that did not connect took no pull's worth of time
    down = _status(T0, connected=False, reason="no port", timings={"total": 30.0})
    assert "took" not in feed_controls.feed_headline(down, 900, feed_running=True, now=now)


def test_safety_net_timers_follow_the_feed_interval_and_the_pull_poll_does_not(monkeypatch):
    from data.bloomberg import live
    from ui.tabs import cash_ladder, market_data
    # the Ladder's and the Market data tab's own timers are one feed cycle, not a typed-in number
    assert cash_ladder.REFRESH_MS == market_data.REFRESH_MS == live.INTERVAL_SECONDS * 1000
    monkeypatch.setattr(live, "INTERVAL_SECONDS", 900)
    assert feed_controls.safety_refresh_ms() == 900_000
    # the status line under the button re-reads the status file every minute at most ...
    assert feed_controls.status_refresh_ms() == 60_000
    monkeypatch.setattr(live, "INTERVAL_SECONDS", 20)
    assert feed_controls.status_refresh_ms() == 20_000
    # ... and the wait after a "Pull Bloomberg now" click stays a 2-second poll, whatever the cadence
    assert feed_controls.PULL_POLL_MS == 2_000
    poll = next(c for c in feed_controls.plumbing() if getattr(c, "id", None) == feed_controls.PULL_POLL_ID)
    assert poll.interval == 2_000
    # the feed module cannot be imported: 15 minutes, not a crash
    monkeypatch.setattr(feed_controls, "feed_interval_seconds", lambda feed=None: None)
    assert feed_controls.safety_refresh_ms() == 900_000
    assert feed_controls.status_refresh_ms() == 60_000


def test_report_already_on_file_at_the_click_is_never_the_requested_pull():
    """Found by the rapid-click test: the status file carries whole seconds, so the report
    already on file (here the placeholder written at app start) can share the click's
    second. Judged on its timestamp alone it looked like the requested pull landing, and
    the guard let the next click through."""
    on_file = _status(T0, connected=False, reason="no pull has run yet", written=0, failed=0)
    pending = {"requested_at": T0.isoformat(), "baseline": feed_controls.status_fingerprint(on_file)}
    assert feed_controls.pull_landed(on_file, pending) is False
    # the backfill patching its own key into that same report changes nothing
    assert feed_controls.pull_landed({**on_file, "backfill": {"running": True, "remaining": 4}}, pending) is False
    # a real report from the same second does count
    assert feed_controls.pull_landed(_status(T0), pending) is True


def test_poll_waits_then_lands_and_only_a_pull_started_after_the_click_counts():
    pending = {"requested_at": T0.isoformat(),
               "baseline": feed_controls.status_fingerprint(_status(T0 - timedelta(minutes=2)))}
    in_flight = _status(T0 - timedelta(seconds=20))          # started BEFORE the click: not ours
    line, finished, landed = feed_controls.poll_outcome(in_flight, pending, _FakeFeed(), now=T0 + timedelta(seconds=6))
    assert (finished, landed) == (False, False) and "pull requested... 6 s" in line

    ours = _status(T0 + timedelta(seconds=9), written=40, failed=0)
    line, finished, landed = feed_controls.poll_outcome(ours, pending, _FakeFeed(), now=T0 + timedelta(seconds=30))
    assert (finished, landed) == (True, True) and "40 marks written, 0 failed" in line


def test_poll_gives_up_after_the_timeout_instead_of_spinning_forever():
    pending = {"requested_at": T0.isoformat(), "baseline": None}
    late = T0 + timedelta(seconds=feed_controls.PULL_TIMEOUT_SECONDS + 1)
    line, finished, landed = feed_controls.poll_outcome(_status(T0 - timedelta(minutes=5)), pending, _FakeFeed(), now=late)
    assert (finished, landed) == (True, False)
    assert "has not reported" in line
    # nothing outstanding (e.g. the page was reloaded mid-wait): the poll switches itself off
    assert feed_controls.poll_outcome(None, None)[1] is True


def test_landed_pull_publishes_the_data_revision_and_switches_the_poll_off(tmp_path, monkeypatch):
    import dash
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    app.bloomberg_feed = _FakeFeed()
    poll = _callback_for_input(app, feed_controls.PULL_POLL_ID, "n_intervals")
    asked = datetime.now(timezone.utc).replace(microsecond=0)
    pending = {"requested_at": asked.isoformat(), "baseline": None}

    monkeypatch.setattr(feed_controls, "read_feed_status", lambda p: _status(asked - timedelta(seconds=30)))
    line, _tip, new_pending, poll_disabled, button_disabled, data_rev = poll(1, pending)
    assert "pull requested..." in line
    assert all(v is dash.no_update for v in (new_pending, poll_disabled, button_disabled, data_rev))

    monkeypatch.setattr(feed_controls, "read_feed_status", lambda p: _status(asked + timedelta(seconds=2)))
    line, tooltip, new_pending, poll_disabled, button_disabled, data_rev = poll(2, pending)
    assert "connected" in line and tooltip == line
    assert new_pending is None and poll_disabled is True and button_disabled is False
    assert data_rev == revision.file_signature(db_path) and data_rev   # open views redraw, no reload


def test_passive_refresh_leaves_the_line_alone_while_a_pull_is_outstanding(tmp_path, monkeypatch):
    import dash
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    refresh = _callback_for_input(app, feed_controls.STATUS_REFRESH_ID, "n_intervals")
    monkeypatch.setattr(feed_controls, "read_feed_status", lambda p: _status(T0))
    waiting = {"requested_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "baseline": None}
    assert refresh(1, "rev", waiting) == (dash.no_update, dash.no_update)
    idle, tooltip = refresh(1, "rev", None)
    assert "212 marks written, 3 failed" in idle and feed_controls.NO_FEED_WORDS in idle
    assert tooltip == idle


def test_strip_width_and_tab_margin_are_one_number_so_the_strip_cannot_cover_the_tabs():
    """The tabs stretch to fill everything left of a fixed reserve and the strip lives
    inside it. Widening the strip alone drew the Bloomberg status line over the
    "Market data" tab at every window width (seen on a screenshot, 2026-09-18); both
    now read one CSS variable. The LAST rule for each selector is the one that applies."""
    import re
    from pathlib import Path
    css = (Path(uploads.__file__).parent / "assets" / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    tab_margin = re.findall(r"(?m)^\.tabs-bar\s*\{[^}]*margin-right:\s*([^;}]+)", css)[-1].strip()
    strip_width = re.findall(r"(?m)^\.top-bar \.source-strip\s*\{[^}]*max-width:\s*([^;}]+)", css)[-1].strip()
    assert tab_margin == "var(--strip-reserve)"
    assert "var(--strip-reserve)" in strip_width and "100%" not in strip_width
