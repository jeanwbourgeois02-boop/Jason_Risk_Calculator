"""ui/uploads.py: the blotter upload control.

Covers the 2026-09-17 coordinator request: after a successful `import_blotter()`,
wake the Bloomberg feed for an extra pull right away
(`data.bloomberg.live.LiveFeed.trigger_now`, added by bbg-data the same day) instead
of leaving marks for the trades just uploaded to wait out the feed's normal 2-minute
interval. `app.bloomberg_feed` is `None` on a machine with no Bloomberg session
(`ui.app.create_app(start_feed=False)`, the default in tests) -- that must never raise.
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


def test_successful_import_triggers_an_immediate_feed_pull(tmp_path, monkeypatch):
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fake_feed = _FakeFeed()
    app.bloomberg_feed = fake_feed
    fn = _confirm_callback(app)

    monkeypatch.setattr(uploads, "import_blotter", lambda *a, **k: "Imported blotter.csv: 1 trade.")
    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "none", "trades": 1, "positions": 0})

    fn(1, _data_url(), "blotter.csv")

    assert fake_feed.triggered == 1


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


def test_trigger_feed_refresh_helper_guards_missing_attribute():
    class _NoFeedAttr:
        pass

    uploads._trigger_feed_refresh(_NoFeedAttr())  # must not raise


def test_trigger_feed_refresh_helper_calls_trigger_now_when_present():
    fake_feed = _FakeFeed()

    class _WithFeed:
        bloomberg_feed = fake_feed

    uploads._trigger_feed_refresh(_WithFeed())
    assert fake_feed.triggered == 1
