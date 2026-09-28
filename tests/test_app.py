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


# The tab list itself is pinned in tests/test_ui_smoke.py; here only its shape is read.
TABS = list(uiapp.VISIBLE_TABS)
KEYS = [uiapp.TAB_KEYS[label] for label in TABS]


def test_tab_ids_are_stable_keys_with_no_ampersand_or_space():
    """A renamed tab keeps its body id: Timing & cash is still tab-body-expiries, Data still
    tab-body-market-data, so nothing keyed on a body id moves."""
    assert uiapp.tab_body_id("Timing & cash") == "tab-body-expiries"
    assert uiapp.tab_body_id("Data") == "tab-body-market-data"
    for label in TABS:
        body_id = uiapp.tab_body_id(label)
        assert "&" not in body_id and " " not in body_id and body_id == body_id.lower()


def test_create_app_registers_the_risk_callback_and_the_show_hide_covers_its_body(tmp_path):
    from ui import revision
    from ui.tabs import header, risk
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    key = f"{risk.BODY_ID}.children"
    assert key in app.callback_map, "risk.register_callbacks was not called from create_app"
    inputs = {(d["id"], d["property"]) for d in app.callback_map[key]["inputs"]}
    assert inputs == {(header.AS_OF_STORE_ID, "data"), (revision.DATA_REVISION_ID, "data"),
                      (risk.REFRESH_ID, "n_intervals")}
    # The one show/hide callback toggles every body, the Risk one included.
    style_key = [k for k in app.callback_map if k.startswith("..tab-body-book.style")]
    assert style_key and "tab-body-risk.style" in style_key[0]
    wrapped = app.callback_map[style_key[0]]["callback"]
    toggle = getattr(wrapped, "__wrapped__", wrapped)    # the raw function, not Dash's context wrapper
    assert toggle("risk") == [{} if label == "Risk" else {"display": "none"} for label in TABS]


def test_create_app_registers_curve_and_expiries_with_no_duplicate_outputs(tmp_path):
    from ui import revision
    from ui.tabs import curve, expiries, header
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    for module, own_inputs in ((curve, {(curve.REFRESH_ID, "n_intervals"), (curve.UNIT_ID, "value")}),
                               (expiries, {(expiries.REFRESH_ID, "n_intervals")})):
        key = f"{module.BODY_ID}.children"
        assert key in app.callback_map, f"{module.__name__}.register_callbacks was not called"
        inputs = {(d["id"], d["property"]) for d in app.callback_map[key]["inputs"]}
        assert inputs == {(header.AS_OF_STORE_ID, "data"), (revision.DATA_REVISION_ID, "data")} | own_inputs
    # Every output is claimed by one callback only (allow_duplicate outputs carry a hash suffix
    # in their key, so a plain output repeated across two callbacks would collapse here).
    outputs = [o for k in app.callback_map for o in k.strip(".").split("...") if "@" not in o]
    assert len(outputs) == len(set(outputs))
    style_key = next(k for k in app.callback_map if k.startswith("..tab-body-book.style"))
    assert "tab-body-curve.style" in style_key and "tab-body-expiries.style" in style_key
    wrapped = app.callback_map[style_key]["callback"]
    toggle = getattr(wrapped, "__wrapped__", wrapped)
    for key in KEYS:
        assert toggle(key) == [{} if other == key else {"display": "none"} for other in KEYS]


# ------------------------------------------------ shared controls: no retired mark source (2026-09-24)

def test_source_options_offer_official_only_never_bnp_or_the_workbook():
    from ui.tabs import controls
    values = [o["value"] for o in controls.SOURCE_OPTIONS]
    labels = " ".join(o["label"] for o in controls.SOURCE_OPTIONS)
    assert values == [controls.SOURCE_OFFICIAL]
    assert "BNP" not in labels and "Workbook" not in labels
    dropdown = controls.build_source_dropdown("x-source").children[1]
    assert dropdown.value == controls.SOURCE_OFFICIAL
    assert controls.source_value_to_param(dropdown.value) is None


# ------------------------------------------------------------ tab links (user, 2026-09-25)
# A tab's name on another screen is a link (ui/tabs/formatting.py::tab_link); one callback
# here sets main-tabs to the clicked link's key.
def _link_prop(key, idx="book-x"):
    import json
    return json.dumps({"idx": idx, "tab": key, "type": "tab-link"}, separators=(",", ":")) + ".n_clicks"


def test_tab_from_link_click_takes_a_real_click_to_a_known_key():
    assert uiapp.tab_from_link_click([{"prop_id": _link_prop("curve"), "value": 1}]) == "curve"
    assert uiapp.tab_from_link_click([{"prop_id": _link_prop("market-data"), "value": 4}]) == "market-data"


def test_tab_from_link_click_ignores_first_render_unknown_keys_and_other_ids():
    assert uiapp.tab_from_link_click([{"prop_id": _link_prop("curve"), "value": None}]) is None
    assert uiapp.tab_from_link_click([{"prop_id": _link_prop("curve"), "value": 0}]) is None
    assert uiapp.tab_from_link_click([{"prop_id": _link_prop("Curve"), "value": 1}]) is None   # a label, not a key
    assert uiapp.tab_from_link_click([{"prop_id": _link_prop("nowhere"), "value": 1}]) is None
    assert uiapp.tab_from_link_click([{"prop_id": "main-tabs.value", "value": "risk"}]) is None
    assert uiapp.tab_from_link_click([{"prop_id": ".", "value": None}]) is None
    assert uiapp.tab_from_link_click([{"prop_id": '{"type":"other","tab":"risk"}.n_clicks', "value": 1}]) is None
    assert uiapp.tab_from_link_click([]) is None and uiapp.tab_from_link_click(None) is None
    assert uiapp.tab_from_link_click(["junk"]) is None
    # A fresh link rendered with 0 beside a real click: the click wins.
    assert uiapp.tab_from_link_click([{"prop_id": _link_prop("risk", "a"), "value": 0},
                                      {"prop_id": _link_prop("pnl", "b"), "value": 2}]) == "pnl"
    # a hidden tab's key (Spreads, FX & cash left the bar in wave 3) is not a destination
    for hidden in uiapp.HIDDEN_TAB_KEYS.values():
        assert uiapp.tab_from_link_click([{"prop_id": _link_prop(hidden), "value": 1}]) is None


def test_every_tab_key_is_reachable_by_a_link():
    for key in KEYS:
        assert uiapp.tab_from_link_click([{"prop_id": _link_prop(key), "value": 1}]) == key


def test_create_app_registers_the_tab_link_callback(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ui.tabs import formatting as fmt
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    key = f"{uiapp.MAIN_TABS_ID}.value"
    assert key in app.callback_map, "the tab-link callback is not registered"
    cb = app.callback_map[key]
    listed = [c for c in app._callback_list if c["output"] == key]
    assert len(listed) == 1 and listed[0]["prevent_initial_call"] is True    # never on page load
    (inp,) = cb["inputs"]
    assert inp["property"] == "n_clicks"
    import json
    assert json.loads(inp["id"]) == {"type": fmt.TAB_LINK_TYPE, "tab": ["ALL"], "idx": ["ALL"]}
    wrapped = cb["callback"]
    follow = getattr(wrapped, "__wrapped__", wrapped)
    link = fmt.tab_link("\u2192 Risk", "risk", "book-var")
    prop = json.dumps(link.id, separators=(",", ":")) + ".n_clicks"
    monkeypatch.setattr(dash, "ctx", SimpleNamespace(triggered=[{"prop_id": prop, "value": 1}]))
    assert follow([1]) == "risk"
    monkeypatch.setattr(dash, "ctx", SimpleNamespace(triggered=[{"prop_id": prop, "value": None}]))
    assert follow([None]) is dash.no_update
    bad = json.dumps(fmt.tab_link_id("nowhere", "x")) + ".n_clicks"
    monkeypatch.setattr(dash, "ctx", SimpleNamespace(triggered=[{"prop_id": bad, "value": 1}]))
    assert follow([1]) is dash.no_update
