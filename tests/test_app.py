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
    started = []
    # 2026-09-21: nothing is asked of Bloomberg at start-up, the past-close backfill included
    monkeypatch.setattr(backfill, "start_auto_backfill", lambda *a, **k: started.append(1))

    why = "no Bloomberg API service on localhost:8194 (refused)"
    monkeypatch.setattr(live, "start_feed_if_available", lambda *a, **k: (None, why))
    assert uiapp.start_bloomberg_feed_with_reason(tmp_path / "risk.db") == (None, why)
    assert f"not started: {why}" in capsys.readouterr().out

    class _Feed:
        interval = 900

    feed = _Feed()
    monkeypatch.setattr(live, "start_feed_if_available", lambda *a, **k: (feed, ""))
    assert uiapp.start_bloomberg_feed_with_reason(tmp_path / "risk.db") == (feed, "")
    assert "on request only" in capsys.readouterr().out
    assert started == []


# ---------------------------------------------------------------- the Risk tab (2026-09-22)
# ui/tabs/risk.py is ui-risk's; the shell only places it third, gives it the Ladder's
# default date (it has no picker of its own and follows the header's as-of store) and
# registers its one callback next to the other tabs'.

def _ids(component):
    """Every id in the subtree, leaves included: `_walk` above skips a component with
    neither `children` nor `className` (a `dcc.Interval`), which the Risk body carries."""
    from dash.development.base_component import Component
    found = set()
    stack = [component]
    while stack:
        c = stack.pop()
        found.add(getattr(c, "id", None))
        children = getattr(c, "children", None)
        kids = children if isinstance(children, (list, tuple)) else [children]
        stack.extend(k for k in kids if isinstance(k, Component))
    return found


def test_risk_tab_is_third_and_the_app_still_opens_on_the_blotter():
    from dash import dcc
    assert uiapp.VISIBLE_TABS == ["Blotter", "Ladder", "Risk", "Market data"]
    assert uiapp.VISIBLE_TABS.index("Risk") == 2
    layout = uiapp.build_layout(uiapp.empty_summary("database not found: missing"))
    tabs = layout.children[0].children[0]
    assert isinstance(tabs, dcc.Tabs)
    assert [t.label for t in tabs.children] == ["Blotter", "Ladder", "Risk", "Market data"]
    assert tabs.value == "Blotter"


def test_layout_carries_the_risk_body_and_its_container():
    from ui.tabs import risk
    layout = uiapp.build_layout(uiapp.empty_summary("database not found: missing"))
    bodies = next(c for c in layout.children if getattr(c, "id", None) == "tab-bodies")
    body_ids = [getattr(b, "id", None) for b in bodies.children]
    assert body_ids == ["tab-body-blotter", "tab-body-ladder", "tab-body-risk", "tab-body-market-data"]
    risk_body = bodies.children[2]
    assert risk_body.className == "tab-body"
    inside = _ids(risk_body)
    assert risk.BODY_ID in inside                # "risk-body": the container the callback fills
    assert risk.REFRESH_ID in inside             # "risk-refresh": its own safety interval


def test_risk_tab_title_names_the_ladders_default_date(monkeypatch):
    """Same default date as the Ladder (today in New York): the Risk title says which day
    the header's as-of store starts on."""
    from ui.tabs import cash_ladder
    monkeypatch.setattr(cash_ladder, "today_ny", lambda: "2026-09-22")
    layout = uiapp.build_layout(uiapp.empty_summary("database not found: missing"))
    bodies = next(c for c in layout.children if getattr(c, "id", None) == "tab-bodies")
    texts = [c.children for c in _walk(bodies.children[2]) if isinstance(getattr(c, "children", None), str)]
    assert any("2026-09-22" in t and "as-of" in t for t in texts)


def test_create_app_registers_the_risk_callback_and_the_show_hide_covers_its_body(tmp_path):
    from ui import revision
    from ui.tabs import header, risk
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    key = f"{risk.BODY_ID}.children"
    assert key in app.callback_map, "risk.register_callbacks was not called from create_app"
    inputs = {(d["id"], d["property"]) for d in app.callback_map[key]["inputs"]}
    assert inputs == {(header.AS_OF_STORE_ID, "data"), (revision.DATA_REVISION_ID, "data"),
                      (risk.REFRESH_ID, "n_intervals")}
    # The one show/hide callback now toggles four bodies, the Risk one included.
    style_key = [k for k in app.callback_map if k.startswith("..tab-body-blotter.style")]
    assert style_key and "tab-body-risk.style" in style_key[0]
    wrapped = app.callback_map[style_key[0]]["callback"]
    toggle = getattr(wrapped, "__wrapped__", wrapped)    # the raw function, not Dash's context wrapper
    assert toggle("Risk") == [{"display": "none"}, {"display": "none"}, {}, {"display": "none"}]
