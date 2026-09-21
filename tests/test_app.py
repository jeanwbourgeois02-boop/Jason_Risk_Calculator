"""ui/app.py: the assembled top-bar layout. Narrow coverage for the 2026-09-17 change
(coordinator request mid-task): the "build <commit>" tag that used to sit in the
top-bar next to the tab strip is removed from the headline entirely -- it was sourced
from `ui.launch.build_label()` (still printed in the launcher's console banner,
ui/launch.py's `main()`, so the information is not lost, just no longer in the UI)."""
from __future__ import annotations

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from ui import app as uiapp  # noqa: E402


def _walk(component):
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for child in children:
            if hasattr(child, "children") or hasattr(child, "className"):
                yield from _walk(child)
    elif hasattr(children, "children") or hasattr(children, "className"):
        yield from _walk(children)


def test_build_tag_is_not_rendered_anywhere_in_the_layout(tmp_path):
    data = uiapp.empty_summary("database not found: missing")
    layout = uiapp.build_layout(data)
    class_names = [getattr(c, "className", None) or "" for c in _walk(layout)]
    assert not any("build-tag" in cls for cls in class_names)


def test_top_bar_still_has_the_tab_strip_and_upload_control(tmp_path):
    data = uiapp.empty_summary("database not found: missing")
    layout = uiapp.build_layout(data)
    top_bar = layout.children[0]
    assert top_bar.className == "top-bar"
    # Tabs + the upload strip -- no third "build" span between them any more.
    assert len(top_bar.children) == 2


# ---------------------------------------------------------------- why there is no feed
# (2026-09-18: the top bar's "Pull Bloomberg now" button must say why it cannot pull on a
# machine without Bloomberg, so create_app keeps the reason next to `bloomberg_feed`.)

def test_create_app_without_a_feed_records_why(tmp_path):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    assert app.bloomberg_feed is None
    assert app.bloomberg_feed_reason == uiapp.FEED_NOT_REQUESTED


def test_feed_switched_off_by_environment_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_LIVE", "0")
    assert uiapp.start_bloomberg_feed_with_reason(tmp_path / "risk.db") == (None, uiapp.FEED_SWITCHED_OFF)
    assert uiapp.start_bloomberg_feed(tmp_path / "risk.db") is None


def test_unreachable_bloomberg_reason_is_kept_and_a_started_feed_has_none(tmp_path, monkeypatch, capsys):
    from data.bloomberg import backfill, live
    monkeypatch.delenv("RISK_LIVE", raising=False)
    monkeypatch.setattr(backfill, "start_auto_backfill", lambda *a, **k: None)

    why = "no Bloomberg API service on localhost:8194 (refused)"
    monkeypatch.setattr(live, "start_feed_if_available", lambda *a, **k: (None, why))
    assert uiapp.start_bloomberg_feed_with_reason(tmp_path / "risk.db") == (None, why)
    assert f"not started: {why}" in capsys.readouterr().out

    class _Feed:
        interval = 900

    feed = _Feed()
    monkeypatch.setattr(live, "start_feed_if_available", lambda *a, **k: (feed, ""))
    assert uiapp.start_bloomberg_feed_with_reason(tmp_path / "risk.db") == (feed, "")
    # the console line's cadence is read from the feed, not typed in
    assert "started (every 15 minutes)" in capsys.readouterr().out
