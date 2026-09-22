"""Smoke tests for the assembled Dash app (ui/app.py), per docs/BUILD_PLAN.md section 5
("Tabs (layer 3)") and the Task C5 wiring prompt.

These are smoke tests only: each tab module (ui/tabs/cash_ladder.py, blotter.py,
market_data.py, header.py) owns its own detailed tests (tests/test_ui_ladder.py
etc.). This file only checks that ui/app.py assembles them correctly -- layout
builds with and without a database, the three tabs are present in the right order,
the header is present, no component id collides across modules, and each module's
register_callbacks is invoked exactly once by create_app().
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd
import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui.tabs import blotter, blotter_fx, cash_ladder, header, market_data  # noqa: E402
from ui.tabs.blotter_pricing import priced_value_book  # noqa: E402
from ui.tabs.formatting import format_cell  # noqa: E402
from ui import revision, uploads  # noqa: E402


def _seed(conn):
    conn.execute(
        "INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES "
        "('t1','XLSX','USDJPY','FX_SPOT','t1','2026-08-17',1000000,147.10,"
        "'BNPP-IPBFX-NMMF','CPTY','HAHY7','trader','desc','')"
    )
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("t1", 1, "FX_NEAR", "USD", 1000000, "2026-08-17", "2026-08-17", 147.10, 1),
            ("t1", 2, "FX_NEAR", "JPY", -147100000, "2026-08-17", "2026-08-17", 147.10, 1),
        ],
    )
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-08-17','USDJPY','2026-08-17','SPOT',147.12,'BBG_BFXFORWARD','2026-08-17T15:00:00-04:00')"
    )
    conn.commit()


def _seeded_db(path):
    conn = sqlite3.connect(path)
    schema.create_schema(conn)
    _seed(conn)
    conn.close()


def _all_ids(component) -> list:
    """Walk a Dash component tree and collect every `.id`, including inside
    dcc.Tabs/dcc.Tab children, dash_table columns' own ids are not components so are
    skipped naturally (they have no .id attribute of this kind)."""
    ids = []
    comp_id = getattr(component, "id", None)
    if comp_id is not None:
        ids.append(comp_id)
    children = getattr(component, "children", None)
    if children is None:
        return ids
    if isinstance(children, (list, tuple)):
        for child in children:
            if hasattr(child, "id") or hasattr(child, "children"):
                ids.extend(_all_ids(child))
    elif hasattr(children, "id") or hasattr(children, "children"):
        ids.extend(_all_ids(children))
    return ids


# ---------------------------------------------------------------- ensure_schema


def test_ensure_schema_creates_empty_db(tmp_path):
    db_path = tmp_path / "risk.db"
    assert not db_path.exists()
    uiapp.ensure_schema(db_path)
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        # pnl_snapshots is retired (docs/BUILD_PLAN.md section 3); it must not exist.
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "pnl_snapshots" not in tables
        assert "trades" in tables
    finally:
        conn.close()


def test_ensure_schema_idempotent_on_existing_db(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    # Re-running must not raise and must not touch existing rows.
    uiapp.ensure_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    finally:
        conn.close()


def test_ensure_schema_purges_legacy_bnp_data(tmp_path, capsys):
    """2026-09-17 ("no bnp fall back"): ensure_schema also calls
    data.ingest.schema.purge_retired_sources once per startup -- a legacy source='BNP'
    trade and a BNP_BVAL mark left over from before this change must be gone after the
    app's normal startup path runs, with no separate manual step."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO trades VALUES ('bnp-1','BNP','USDJPY','FX_FWD','bnp-1','2026-08-01',"
        "1000000,147.0,'acc','cp','HAHY7','t','d','')"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-08-17','USDJPY','2026-08-17','SPOT',147.10,'BNP_BVAL','t')"
    )
    conn.commit()
    conn.close()

    uiapp.ensure_schema(db_path)
    out = capsys.readouterr().out
    assert "purged retired BNP/workbook data" in out

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM trades WHERE source='BNP'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM marks WHERE source='BNP_BVAL'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1  # the seeded 'XLSX' trade survives
    finally:
        conn.close()


# ---------------------------------------------------------------- layout assembly


def test_build_layout_missing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_DB", str(tmp_path / "missing.db"))
    data = uiapp.empty_summary("database not found: missing")
    layout = uiapp.build_layout(data)
    assert isinstance(layout, dash.html.Div)


def test_build_layout_with_database(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    data = uiapp.load_summary(db_path)
    assert data["as_of_date"] == "2026-08-17"
    layout = uiapp.build_layout(data)
    assert isinstance(layout, dash.html.Div)


def _find_tabs(node):
    """Recursively find the first dcc.Tabs in the tree (it now lives inside the
    top-bar Div alongside the upload control, per user decision 2026-09-15 item A)."""
    if isinstance(node, dash.dcc.Tabs):
        return node
    for child in getattr(node, "children", None) or []:
        if isinstance(child, (list, tuple)):
            for c in child:
                found = _find_tabs(c)
                if found is not None:
                    return found
        elif hasattr(child, "children") or isinstance(child, dash.dcc.Tabs):
            found = _find_tabs(child)
            if found is not None:
                return found
    return None


def _tab_labels(layout):
    tabs_bar = _find_tabs(layout)
    return [tab.label for tab in tabs_bar.children]


def _find_tab_bodies(layout):
    """The always-present `html.Div(id="tab-bodies")` sibling of the top-bar/header."""
    for child in layout.children:
        if getattr(child, "id", None) == "tab-bodies":
            return child
    return None


def test_four_tabs_present_in_order(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    assert _tab_labels(layout) == ["Blotter", "Ladder", "Market data"]    # user, 2026-09-22: Blotter first
    assert uiapp.VISIBLE_TABS == ["Blotter", "Ladder", "Market data"]


def test_no_overall_book_or_placeholder_tabs(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    labels = _tab_labels(layout)
    assert "Overall book" not in labels
    assert "FX" not in labels
    assert "Rates" not in labels
    assert "Options" not in labels
    assert "Delta" not in labels


def test_no_page_title_and_top_bar_has_tabs_and_upload(tmp_path):
    """User decision 2026-09-15, item A: no H1 'Risk monitor' or other strip above the
    tabs; the tab bar and the upload control share one top-bar row."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    text = []

    def walk(node):
        if isinstance(node, str):
            text.append(node)
            return
        for child in getattr(node, "children", None) or []:
            if isinstance(child, (list, tuple)):
                for c in child:
                    walk(c)
            else:
                walk(child)
    walk(layout)
    assert not any(isinstance(c, dash.html.H1) for c in layout.children)
    top_bar = layout.children[0]
    assert isinstance(top_bar, dash.html.Div)
    kinds = [type(c).__name__ for c in top_bar.children]
    assert "Tabs" in kinds
    assert "Div" in kinds  # the upload strip


def test_header_directly_under_top_bar(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    # top_bar is children[0]; the header block is the very next sibling.
    assert getattr(layout.children[1], "id", None) == header.HEADER_ID


def test_header_present_above_tabs(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    ids = _all_ids(layout)
    assert header.HEADER_ID in ids
    assert header.AS_OF_STORE_ID in ids


def test_tab_bodies_always_present_and_tabs_have_no_children(tmp_path):
    """2026-09-15 structure fix: dcc.Tab objects carry no `children` of their own (that
    nesting pushed the navy top-bar around the whole page); all four bodies live in one
    always-present `html.Div(id="tab-bodies")` sibling of the top-bar/header."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    tabs_bar = _find_tabs(layout)
    for tab in tabs_bar.children:
        assert not getattr(tab, "children", None)
    bodies = _find_tab_bodies(layout)
    assert bodies is not None
    slugs = {getattr(b, "id", None) for b in bodies.children}
    assert slugs == {"tab-body-ladder", "tab-body-blotter", "tab-body-market-data"}


def test_tab_show_hide_callback_toggles_bodies(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    key = [k for k in app.callback_map if k.startswith(f"..tab-body-{uiapp._slug(uiapp.VISIBLE_TABS[0])}.style")]
    assert key, "expected a callback outputting tab-body-*.style keyed on main-tabs value"
    cb = app.callback_map[key[0]]
    assert any(d["id"] == uiapp.MAIN_TABS_ID and d["property"] == "value" for d in cb["inputs"])


def test_ladder_date_picker_defaults_to_today_ny(tmp_path):
    """Coordinator addition 2026-09-15: the Ladder tab (and the header store it feeds)
    default to today in America/New_York, not the last BNP snapshot date."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)  # snapshot date 2026-08-17, deliberately in the past
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    today = cash_ladder.today_ny()

    def find(node, comp_id):
        if getattr(node, "id", None) == comp_id:
            return node
        children = getattr(node, "children", None)
        if children is None:
            return None
        if isinstance(children, (list, tuple)):
            for child in children:
                if hasattr(child, "id") or hasattr(child, "children"):
                    found = find(child, comp_id)
                    if found is not None:
                        return found
        elif hasattr(children, "id") or hasattr(children, "children"):
            return find(children, comp_id)
        return None

    picker = find(layout, cash_ladder.DATE_PICKER_ID)
    assert picker is not None and picker.date == today

    store = find(layout, header.AS_OF_STORE_ID)
    assert store is not None and store.data == today


def test_header_as_of_defaults_to_today_follows_a_pick_and_rolls_over_at_midnight(tmp_path):
    """User, 2026-09-22: "by default, always price pnl as of today, so that the top bar
    numbers all reflect todays numbers, unless changed specifically otherwise"."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    assert callable(app.layout)                                   # built on every page load: today is fresh
    ids = _all_ids(app.layout())
    assert header.AS_OF_STORE_ID in ids and header.AS_OF_PICKED_ID in ids
    # both pickers feed the header; the poll rolls the header and both pickers to the new day
    keys = list(app.callback_map)
    follow = next(k for k in keys if k.startswith(f"..{header.AS_OF_STORE_ID}.data...{header.AS_OF_PICKED_ID}.data.."))
    inputs = {d["id"] for d in app.callback_map[follow]["inputs"]}
    assert inputs == {cash_ladder.DATE_PICKER_ID, blotter.DATE_PICKER_ID}
    roll = next(k for k in keys if f"{blotter.DATE_PICKER_ID}.date@" in k and header.AS_OF_STORE_ID in k)
    assert {d["id"] for d in app.callback_map[roll]["inputs"]} == {revision.POLL_ID}
    # the rules themselves
    assert header.as_of_after_pick("2026-09-15", "2026-09-22") == ("2026-09-15", True)
    assert header.as_of_after_pick("2026-09-22", "2026-09-22") == ("2026-09-22", False)   # the Today button
    assert header.as_of_after_pick(None, "2026-09-22") == ("2026-09-22", False)
    assert header.as_of_after_tick("2026-09-21", False, "2026-09-22") == "2026-09-22"    # the day rolled
    assert header.as_of_after_tick("2026-09-22", False, "2026-09-22") is None
    assert header.as_of_after_tick("2026-09-15", True, "2026-09-22") is None             # picked: stays


def test_the_books_today_rolls_at_five_pm_new_york():
    """User, 2026-09-22: "only roll to new day after new york 5pm"."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    assert cash_ladder.ROLLOVER_HOUR_NY == 17
    assert cash_ladder.today_ny(datetime(2026, 9, 22, 16, 59, tzinfo=ny)) == "2026-09-22"
    assert cash_ladder.today_ny(datetime(2026, 9, 22, 17, 0, tzinfo=ny)) == "2026-09-23"
    assert cash_ladder.today_ny(datetime(2026, 9, 22, 23, 30, tzinfo=ny)) == "2026-09-23"
    assert cash_ladder.today_ny(datetime(2026, 9, 23, 0, 5, tzinfo=ny)) == "2026-09-23"


def test_ladder_heading_text_and_today_button(tmp_path):
    """Coordinator addition 2026-09-15: the bare date-picker card is replaced by a
    heading naming the as-of date, the picker, and a Today button."""
    assert cash_ladder.heading_date_text("2026-09-15") == "Tuesday 15 September 2026"
    assert cash_ladder.heading_date_text(None) == "As of - no date selected"

    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    ids = _all_ids(app.layout())          # a callable layout since 2026-09-22: built per page load
    assert cash_ladder.TITLE_ID in ids
    assert cash_ladder.TODAY_BUTTON_ID in ids
    # Today button writes to the same date-picker property as the upload confirm
    # callback, so both Outputs must declare allow_duplicate.
    today_key = [k for k in app.callback_map if cash_ladder.TODAY_BUTTON_ID in k or "today" in k.lower()]
    assert any(cash_ladder.DATE_PICKER_ID in k for k in app.callback_map)


def test_header_figures_and_chart_are_separate_callbacks(tmp_path):
    """Coordinator perf finding 2026-09-15: figures must update without waiting on the
    LTD chart, which now only computes when the collapsible is open."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    figure_keys = [k for k in app.callback_map if k.startswith(f"{header.HEADER_ID}-figures")]
    chart_keys = [k for k in app.callback_map if header.CHART_CONTAINER_ID in k]
    assert figure_keys and chart_keys
    assert figure_keys[0] != chart_keys[0]
    chart_cb = app.callback_map[chart_keys[0]]
    assert any(d["id"] == header.DETAILS_ID and d["property"] == "open" for d in chart_cb["inputs"])


def test_no_duplicate_component_ids(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    ids = _all_ids(layout)
    assert len(ids) == len(set(ids)), (
        f"duplicate component ids in assembled layout: "
        f"{sorted({i for i in ids if ids.count(i) > 1})}"
    )


def test_no_duplicate_component_ids_missing_db(tmp_path):
    data = uiapp.empty_summary("database not found")
    layout = uiapp.build_layout(data)
    ids = _all_ids(layout)
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------- create_app wiring


def test_create_app_registers_each_module_once(tmp_path, monkeypatch):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)

    calls = {"header": 0, "cash_ladder": 0, "blotter": 0, "market_data": 0}

    def _counted(name, original):
        def wrapper(app, get_db_path):
            calls[name] += 1
            return original(app, get_db_path)
        return wrapper

    monkeypatch.setattr(header, "register_callbacks", _counted("header", header.register_callbacks))
    monkeypatch.setattr(cash_ladder, "register_callbacks", _counted("cash_ladder", cash_ladder.register_callbacks))
    monkeypatch.setattr(blotter, "register_callbacks", _counted("blotter", blotter.register_callbacks))
    monkeypatch.setattr(market_data, "register_callbacks", _counted("market_data", market_data.register_callbacks))

    uiapp.create_app(db_path=db_path, start_feed=False)

    assert calls == {"header": 1, "cash_ladder": 1, "blotter": 1, "market_data": 1}


def test_create_app_builds_with_missing_database(tmp_path):
    db_path = tmp_path / "does_not_exist" / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    assert isinstance(app, dash.Dash)
    assert db_path.exists()  # ensure_schema created it


def test_create_app_no_feed_by_default(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    assert app.bloomberg_feed is None


def test_get_db_path_default(monkeypatch):
    monkeypatch.delenv("RISK_DB", raising=False)
    assert uiapp.get_db_path() == uiapp.DEFAULT_DB_PATH


# ---------------------------------------------------------------- callback exception config
# 2026-09-16: the Blotter sub-tabs' table/filter-dropdown/detail-panel ids only ever
# exist inside a callback's own Output (ui.tabs.blotter.CONTENT_ID's children), never in
# the static app.layout tree. Without suppress_callback_exceptions=True, Dash silently
# rejects every callback wired to those ids client-side -- the filter dropdowns looked
# present but did nothing. This is a config regression test, not a logic test: it
# guards the one-line fix directly rather than re-deriving it from browser behaviour.


def test_create_app_suppresses_callback_exceptions(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    assert app.config.suppress_callback_exceptions is True


def test_blotter_filter_dropdown_end_to_end_narrows_table(tmp_path):
    """Full HTTP round trip through the Flask test client (not just calling the Python
    callback function directly) -- this is the level at which the bug actually showed
    up: Dash's callback-id validation runs at this layer, so a plain function-level
    call of the filter callback would have looked fine even while it 500'd in the
    browser."""
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    _seed(conn)
    conn.execute(
        "INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES "
        "('t2','BNP','EURUSD','FX_SPOT','t2','2026-08-17',500000,1.10,"
        "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc','')"
    )
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("t2", 1, "FX_NEAR", "EUR", 500000, "2026-08-17", "2026-08-17", 1.10, 1),
            ("t2", 2, "FX_NEAR", "USD", -550000, "2026-08-17", "2026-08-17", 1.10, 1),
        ],
    )
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-08-17','EURUSD','2026-08-17','SPOT',1.11,'BBG_BFXFORWARD','2026-08-17T15:00:00-04:00')"
    )
    # 'XLSX' (2026-09-16, trades_official double-count fix): the Blotter tab's priced
    # value book now reads trades_official, which excludes source='BNP' by design.
    # This test is about the filter dropdown UI mechanics, not source filtering.
    conn.execute("UPDATE trades SET source = 'XLSX'")
    conn.commit()
    conn.close()

    app = uiapp.create_app(db_path=db_path, start_feed=False)
    client = app.server.test_client()
    assert client.get("/").status_code == 200

    import json as _json

    payload = {
        "output": "..blotter-datatable-total.data...blotter-datatable-total.tooltip_data..",
        "outputs": [
            {"id": "blotter-datatable-total", "property": "data"},
            {"id": "blotter-datatable-total", "property": "tooltip_data"},
        ],
        "inputs": [
            {"id": "blotter-datatable-total-filter-instrument_id", "property": "value", "value": ["USDJPY"]},
            {"id": "blotter-datatable-total-filter-side", "property": "value", "value": []},
            {"id": "blotter-datatable-total-filter-status", "property": "value", "value": []},
            {"id": "blotter-datatable-total-filter-product", "property": "value", "value": []},
            {"id": "blotter-datatable-total-filter-strategy", "property": "value", "value": []},
            {"id": "blotter-datatable-total-filter-theme", "property": "value", "value": []},
            # ui/revision.py (2026-09-18): new marks refresh the rows in place, filters kept
            {"id": "data-revision", "property": "data", "value": "rev-1"},
        ],
        "state": [{"id": "blotter-date", "property": "date", "value": "2026-08-17"}],
        "changedPropIds": ["blotter-datatable-total-filter-instrument_id.value"],
    }
    resp = client.post("/_dash-update-component", data=_json.dumps(payload), content_type="application/json")
    assert resp.status_code == 200
    body = _json.loads(resp.get_data(as_text=True))
    rows = body["response"]["blotter-datatable-total"]["data"]
    assert rows, "filter returned no rows"
    assert {r["instrument_id"] for r in rows} == {"USDJPY"}


def test_get_db_path_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("RISK_DB", str(custom))
    assert uiapp.get_db_path() == custom


def test_every_static_callback_id_exists_in_layout(tmp_path):
    """A callback Input/State/Output whose component is not in the initial layout
    fires with a missing argument ('Inputs do not match callback definition', HTTP 500)
    or never fires. Components created inside another callback's output are exempt
    only if listed here with a reason.

    Built on a tmp_path database (2026-09-18): a bare `create_app()` resolves to
    data/raw/risk.db, the user's live database, and runs `ensure_schema` on it -- DDL, the
    column migration and the retired-source purge -- every time anyone runs the suite."""
    import ui.app as a
    app = a.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    ids = set()

    def walk(c):
        i = getattr(c, "id", None)
        if isinstance(i, str):
            ids.add(i)
        ch = getattr(c, "children", None)
        if isinstance(ch, list):
            for x in ch:
                walk(x)
        elif ch is not None and (hasattr(ch, "children") or hasattr(ch, "id")):
            walk(ch)
    walk(app.layout())
    # rendered inside blotter-table-container; options-datatable/-collapsed-packages
    # (ui.tabs.options, Phase 8 options_calc merge) only exist once the "options"
    # sub-tab is selected, same as the other blotter-* dynamic ids below.
    dynamic_ok = {"blotter-datatable", "blotter-subtotal",
                  "options-datatable", "options-collapsed-packages"}
    dynamic_prefixes = ("blotter-datatable-", "blotter-strip-", "blotter-bundle-",
                        "blotter-row-detail-", "options-terms-",
                        "blotter-fx-",  # the FX sub-tab's trade table and its "rows shown" currency table (2026-09-21)
                        "manual-",  # per-sub-tab, rendered by callback (manual-*: Manual entry sub-tab, 2026-09-18)
                        "rates-")   # rendered inside the Blotter's Rates sub-tab (ui.tabs.rates.build_layout), like the Options ids
    missing = []
    for key, cb in app.callback_map.items():
        for kind in ("inputs", "state"):
            for d in cb.get(kind, []):
                if d["id"] not in ids and d["id"] not in dynamic_ok and not d["id"].startswith(dynamic_prefixes):
                    missing.append((kind, d["id"]))
        for out in key.strip(".").split("..."):
            oid = out.split(".")[0]
            if oid and oid not in ids and oid not in dynamic_ok and not oid.startswith(dynamic_prefixes):
                missing.append(("output", oid))
    assert not missing, missing


# ---------------------------------------------------------------- header compaction (2026-09-15)
def test_header_figures_include_previous_day_and_net_gross(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cards = header._build_figures(conn, "2026-08-17")
    finally:
        conn.close()
    titles = [c.children[0].children for c in cards if getattr(c, "className", "") != "header-divider"]
    assert "Previous day" in titles
    assert "Net USD delta" in titles
    assert "Gross USD delta" in titles


def test_header_pnl_card_colours_by_sign():
    pos = header._pnl_card("X", {"value": 100.0, "available": True})
    neg = header._pnl_card("X", {"value": -100.0, "available": True})
    zero = header._pnl_card("X", {"value": 0.0, "available": True})
    assert "header-figure-value--pos" in pos.children[1].className
    assert "header-figure-value--neg" in neg.children[1].className
    assert "header-figure-value--zero" in zero.children[1].className


def test_header_pnl_card_unavailable_shows_reason_as_tooltip():
    card = header._pnl_card("X", {"available": False, "reason": "no mark"})
    value_span = card.children[1]
    assert value_span.children == "n/a"
    assert value_span.title == "no mark"


def test_header_gross_card_is_never_colour_coded():
    card = header._pnl_card("Gross USD", {"value": -5.0, "available": True}, colour=False)
    assert "header-figure-value--neutral" in card.children[1].className


def test_net_gross_usd_matches_ladder_portfolio_totals(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        # net_gross_usd is exposure/delta math (settle_date > as_of): the seeded trade
        # (settle_date == trade_date == as_of) settles on as_of itself, so it must NOT
        # contribute -- add a second trade settling after as_of so the pair still has
        # open delta to assert on (see
        # test_leg_settling_on_as_of_excluded_from_net_gross_but_still_in_grid for the
        # same-day exclusion itself).
        conn.execute(
            "INSERT INTO trades VALUES "
            "('t2','BNP','USDJPY','FX_SPOT','t2','2026-08-17',2000000,147.10,"
            "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc','')"
        )
        conn.executemany(
            "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("t2", 1, "FX_NEAR", "USD", 2000000, "2026-08-17", "2026-08-20", 147.10, 1),
                ("t2", 2, "FX_NEAR", "JPY", -294200000, "2026-08-17", "2026-08-20", 147.10, 1),
            ],
        )
        conn.commit()
        result = cash_ladder.net_gross_usd(conn, "2026-08-17")
    finally:
        conn.close()
    assert result["available"] is True
    assert abs(result["net"]) == pytest.approx(result["gross"])  # single currency, single pair


def test_leg_settling_on_as_of_excluded_from_net_gross_but_still_in_grid(tmp_path):
    """CLAUDE.md 'Six tabs as views': the ladder grid uses settle_date >= as_of (a leg
    settling today is still cash that moves today, on its own date row). Since the
    settled-cash row (user decision 2026-09-18, "expired tickets must settle not
    disappear") a DELIVERABLE leg settling on as_of is cash by close and still carries
    its currency's delta: engine/ladder/exposure_adapter.exposure_records_from_db
    returns it as a settled record (settlement_date = SETTLED) rather than dropping it.
    t1 (seeded, deliverable, settles 2026-08-17 = as_of) must appear on the grid's date
    row AND, as settled cash, in the exposure record set and net_gross_usd; t2 (settles
    2026-08-20, still open) appears in both as an open leg."""
    from engine.ladder.exposure_adapter import SETTLED, records_from_db, exposure_records_from_db

    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        # 'XLSX' (2026-09-16, trades_official double-count fix): records_from_db /
        # exposure_records_from_db now read trades_official, which excludes
        # source='BNP' by design. This test is about the settle_date >= vs > boundary,
        # not source filtering, so relabel the seeded t1 too.
        conn.execute("UPDATE trades SET source = 'XLSX'")
        conn.execute(
            "INSERT INTO trades VALUES "
            "('t2','XLSX','USDJPY','FX_SPOT','t2','2026-08-17',2000000,147.10,"
            "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc','')"
        )
        conn.executemany(
            "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("t2", 1, "FX_NEAR", "USD", 2000000, "2026-08-17", "2026-08-20", 147.10, 1),
                ("t2", 2, "FX_NEAR", "JPY", -294200000, "2026-08-17", "2026-08-20", 147.10, 1),
            ],
        )
        conn.commit()

        grid_records, _ = records_from_db(conn, "2026-08-17")
        exposure_records, _ = exposure_records_from_db(conn, "2026-08-17")

        assert {r["trade_id"] for r in grid_records} == {"t1", "t2"}
        assert {r["settlement_date"] for r in grid_records if r["trade_id"] == "t1"} == {"2026-08-17"}
        assert {r["trade_id"] for r in exposure_records} == {"t1", "t2"}
        assert {r["settlement_date"] for r in exposure_records if r["trade_id"] == "t1"} == {SETTLED}
        assert {r["settlement_date"] for r in exposure_records if r["trade_id"] == "t2"} == {"2026-08-20"}

        result = cash_ladder.net_gross_usd(conn, "2026-08-17")
    finally:
        conn.close()
    assert result["available"] is True
    # t2's 2,000,000 open notional plus t1's 1,000,000 now held as settled JPY cash
    # (still JPY delta) drive Net/Gross: 441,300,000 JPY at the 147.12 spot.
    assert result["gross"] == pytest.approx(3_000_000, rel=1e-3)


def test_scoped_period_pnl_previous_day_is_ltd_t1_minus_ltd_t2(tmp_path):
    from ui.tabs.blotter_pricing import scoped_period_pnl

    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        periods = scoped_period_pnl(conn, "2026-08-17")
    finally:
        conn.close()
    assert "previous_day" in periods
    assert set(periods["previous_day"]) >= {"value", "ref_date", "available", "reason"}


# ---------------------------------------------------------------- Bloomberg diagnostics button
# Moved 2026-09-16 from ui/tabs/header.py (shown above every tab) to
# ui/tabs/market_data.py (Market Data tab only) per user decision -- see
# ui.tabs.market_data.BBG_CHECK_BUTTON_ID / BBG_RESULTS_ID.
def _find_id(node, target_id):
    if getattr(node, "id", None) == target_id:
        return node
    for child in getattr(node, "children", None) or []:
        if not hasattr(child, "id") and not hasattr(child, "children"):
            continue
        found = _find_id(child, target_id)
        if found is not None:
            return found
    return None


def test_header_layout_no_longer_includes_bbg_check_button_or_results():
    layout = header.layout()
    assert not hasattr(header, "BBG_CHECK_BUTTON_ID")
    assert not hasattr(header, "BBG_RESULTS_ID")
    assert _find_id(layout, "market-data-bbg-check-button") is None
    assert _find_id(layout, "header-bbg-check-button") is None


def test_market_data_layout_includes_bbg_check_button_and_results_container():
    layout = market_data.build_layout(default_date="2026-08-17")

    assert _find_id(layout, market_data.BBG_CHECK_BUTTON_ID) is not None
    assert _find_id(layout, market_data.BBG_RESULTS_ID) is not None


def test_render_bbg_results_shows_status_name_and_message():
    checks = [
        {"name": "Session connectivity", "status": "pass", "message": "Connected fine."},
        {"name": "Marks coverage", "status": "fail", "message": "Some marks missing."},
        {"name": "Fallback usage", "status": "warning", "message": "Using BNP_BVAL reconciliation rates."},
    ]
    panel = market_data._render_bbg_results(checks)
    rows = panel.children
    assert len(rows) == 3
    statuses = [row.children[0].children for row in rows]
    names = [row.children[1].children for row in rows]
    messages = [row.children[2].children for row in rows]
    assert statuses == ["PASS", "FAIL", "WARNING"]
    assert names == ["Session connectivity", "Marks coverage", "Fallback usage"]
    assert messages == ["Connected fine.", "Some marks missing.", "Using BNP_BVAL reconciliation rates."]


def test_render_bbg_results_handles_empty_list():
    panel = market_data._render_bbg_results([])
    assert "No diagnostic checks" in panel.children


def test_run_bloomberg_diagnostics_safe_never_raises_and_returns_list(monkeypatch):
    def _boom():
        raise RuntimeError("blpapi not installed")

    monkeypatch.setattr(market_data, "_bbg_diagnostics_entry_point", lambda: _boom)
    result = market_data.run_bloomberg_diagnostics_safe()
    assert isinstance(result, list)
    assert result[0]["status"] == "fail"
    assert "Could not reach Bloomberg" in result[0]["message"]
    # No raw exception text/traceback leaks into the message shown to the user.
    assert "RuntimeError" not in result[0]["message"]
    assert "Traceback" not in result[0]["message"]


def test_run_bloomberg_diagnostics_safe_rejects_non_list_result(monkeypatch):
    monkeypatch.setattr(market_data, "_bbg_diagnostics_entry_point", lambda: (lambda: {"not": "a list"}))
    result = market_data.run_bloomberg_diagnostics_safe()
    assert isinstance(result, list)
    assert result[0]["status"] == "fail"


def test_placeholder_diagnostics_returns_plain_english_checks_without_blpapi():
    # On a dev machine with no Bloomberg terminal/blpapi, the placeholder must not
    # raise and must describe the failure in plain English (no traceback).
    checks = market_data._run_bloomberg_diagnostics_placeholder()
    assert isinstance(checks, list)
    assert len(checks) >= 1
    for c in checks:
        assert c["status"] in {"pass", "fail", "warning"}
        assert isinstance(c["name"], str) and c["name"]
        assert isinstance(c["message"], str) and c["message"]
        assert "Traceback" not in c["message"]


def test_bbg_diagnostics_entry_point_prefers_real_module_now_that_it_exists():
    # data.bloomberg.bbg_diagnostics (bbg-data, 2026-09-16) now re-exports
    # tools.bbg_diagnostics.run_bloomberg_diagnostics; the entry point must prefer it
    # over the local placeholder.
    from data.bloomberg.bbg_diagnostics import run_bloomberg_diagnostics as real_fn

    fn = market_data._bbg_diagnostics_entry_point()
    assert fn is real_fn
    assert fn is not market_data._run_bloomberg_diagnostics_placeholder


# ---------------------------------------------------------------- BNP upload summary
# (2026-09-16 fix: a collaborator's change routed import_report()'s new/identical/
# excluded/closed-line summary to log.info only -- the app has no logging handler
# configured anywhere, so it was silently dropped and never seen. The summary must be
# rendered on the page, not just logged.)


def _report_confirm_callback(app):
    # Multi-output callbacks are keyed by all their outputs joined with '..', with a
    # hash suffix; match by prefix instead of the exact key.
    key = next(k for k in app.callback_map if k.startswith("..report-result.children...data-source-line"))
    cb = app.callback_map[key]["callback"]
    return getattr(cb, "__wrapped__", cb)


def test_blotter_confirm_shows_loader_summary_on_page(tmp_path, monkeypatch):
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fn = _report_confirm_callback(app)

    summary = ("Imported blotter.csv: 2 trades (1 forwards, 1 futures, 0 options, 0 rate swaps), "
               "4 legs. 0 currency rows seen (no position snapshot -- this file has no EOD balance "
               "grain). Excluded: 0 rows from other funds/status, 0 malformed IRS rows, "
               "0 other unsupported rows.")
    monkeypatch.setattr("ui.uploads.import_blotter", lambda *a, **k: summary)
    monkeypatch.setattr("ui.uploads.decode", lambda contents: b"irrelevant")
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "none", "trades": 2})

    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    result, source_line, stage_style, _data_rev, _book_rev = fn(1, contents,"blotter.csv")

    # The loader's summary must actually be visible in the rendered result -- not "",
    # not only passed to a logger.
    assert isinstance(result, dash.html.Div)
    assert result.className == "source-result--info"
    assert summary in str(result)
    assert source_line == "Loaded: 2 trades in database."


def test_blotter_confirm_surfaces_clean_rejection(tmp_path, monkeypatch):
    # import_blotter raises a plain ValueError when the file isn't blotter-shaped; the
    # UI must show that message, not a raw traceback.
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fn = _report_confirm_callback(app)

    def _reject(*a, **k):
        raise ValueError("This file is not a trade blotter. Missing columns: Fin Type, Status, Trade Id")

    monkeypatch.setattr("ui.uploads.import_blotter", _reject)
    monkeypatch.setattr("ui.uploads.decode", lambda contents: b"irrelevant")

    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    result, source_line, stage_style, data_rev, book_rev = fn(1, contents,"not-a-blotter.csv")

    assert "not a trade blotter" in str(result)
    assert source_line is dash.no_update
    # a failed import saved nothing, so it must not tell the page the data changed
    assert data_rev is dash.no_update and book_rev is dash.no_update


def test_selected_shows_filename_for_recognized_blotter_file(monkeypatch, tmp_path):
    # tmp_path, never db_path=None: None resolves to the user's live data/raw/risk.db and
    # create_app runs ensure_schema (DDL, migration, purge) on whatever it is given.
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)
    key = next(k for k in app.callback_map if k.startswith(f"..{uploads.STAGE_ID}.style"))
    cb = app.callback_map[key]["callback"]
    fn = getattr(cb, "__wrapped__", cb)

    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")
    monkeypatch.setattr(uploads, "preview_frame", lambda payload, filename: object())
    monkeypatch.setattr(uploads, "validate_blotter_shape", lambda frame: None)

    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    stage_style, fname, result = fn(contents, "blotter.csv")

    assert stage_style == {}
    assert fname == "blotter.csv"
    assert result == ""


def test_selected_shows_error_for_unrecognized_file(monkeypatch, tmp_path):
    app = uiapp.create_app(db_path=tmp_path / "risk.db", start_feed=False)  # never the live database
    key = next(k for k in app.callback_map if k.startswith(f"..{uploads.STAGE_ID}.style"))
    cb = app.callback_map[key]["callback"]
    fn = getattr(cb, "__wrapped__", cb)

    monkeypatch.setattr(uploads, "decode", lambda contents: b"irrelevant")

    def _reject(frame):
        raise ValueError("This file is not a trade blotter. Missing columns: Fin Type, Status, Trade Id")

    monkeypatch.setattr(uploads, "preview_frame", lambda payload, filename: object())
    monkeypatch.setattr(uploads, "validate_blotter_shape", _reject)

    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    stage_style, fname, result = fn(contents, "mystery.csv")

    assert stage_style == {"display": "none"}
    assert "not a trade blotter" in str(result)


def test_describe_source_trades_loaded():
    assert uploads.describe_source({"as_of_date": "none", "trades": 5}) == \
        "Loaded: 5 trades in database."


def test_describe_source_nothing_loaded():
    assert uploads.describe_source({"as_of_date": "none", "trades": 0}) == \
        "No data loaded yet."


def test_upload_button_label_is_format_neutral():
    layout = uploads.layout({})
    text = str(layout)
    assert "Upload trade file" in text
    assert "Upload BNP report" not in text


def test_launch_configures_root_logging_handler():
    """The app's single entry point must attach a logging handler so log.info/log.warning
    calls anywhere in the app (data/ingest/bnp.py, ui/uploads.py) actually go somewhere,
    instead of being dropped by the default unconfigured root logger."""
    import inspect
    from ui import launch

    assert "logging.basicConfig" in inspect.getsource(launch.main)


# ------------------------------------------------------ blotter FX sub-tab


def _seed_fx_trade(conn, trade_id="T1", instrument_id="EURUSD", trade_date="2026-06-18",
                    settle_date="2026-06-20"):
    """A single FX_FWD trade + its two legs. `settle_date` is the trade's OWN value
    date -- `engine.pnl.fx_blotter.fx_blotter_rows`/`value_book` mark it there directly
    (market-standard convention), unlike the retired xlsx-replica's shared
    WORKDAY(as_of,5) valuation node."""
    conn.execute(
        "INSERT INTO instruments VALUES (?,'FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')",
        (instrument_id,),
    )
    conn.execute(
        "INSERT INTO trades VALUES (?,'XLSX',?,'FX_FWD',?,?,1000000,1.10,"
        "'ACC','CPTY','HAHY7','TR','buy eur','')",
        (trade_id, instrument_id, trade_id, trade_date),
    )
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (trade_id, 1, "FX_NEAR", "EUR", 1000000, trade_date, settle_date, 1.10, 1),
            (trade_id, 2, "FX_NEAR", "USD", -1100000, trade_date, settle_date, 1.10, 1),
        ],
    )


def test_blotter_fx_scope_renders_table_columns(strict_marks, tmp_path):
    """The Blotter 'FX' sub-tab is `fx_blotter_rows`-shaped -- confirms the column set
    and labels are still the legacy sheet's layout even though the maths underneath
    changed."""
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    _seed_fx_trade(conn, settle_date="2026-06-20")
    # Real mark_eod at the trade's OWN settle_date (2026-06-20 == as_of here); no t-1/t-2
    # marks seeded, so those columns are genuinely missing.
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-06-20','EURUSD','2026-06-20','FWD_OUTRIGHT',1.1080,'BBG_BFXFORWARD',"
        "'2026-06-20T15:00:00-04:00')"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    try:
        layout = blotter.scope_layout("fx", conn, "2026-06-20")
    finally:
        conn.close()

    table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
    # What the user sees: the legacy sheet's columns. The bookkeeping columns behind
    # "P&L by currency, rows shown" (2026-09-21) are in `columns` too, all of them hidden.
    ids = [c["id"] for c in table.columns if c["id"] not in table.hidden_columns]
    assert ids == [
        "trade_date", "instrument_id", "quantity_usd_notional", "tenor", "fill",
        "mark_t1", "mark_eod", "mark_t2", "pnl_t1", "pnl_eod", "pnl_t2",
    ]
    assert [c["id"] for c in table.columns if c["id"] in table.hidden_columns] == blotter_fx._HIDDEN_COLUMNS
    names = [c["name"] for c in table.columns]
    assert "LTD P&L" in names and "LTD-1 P&L" in names and "LTD-2 P&L" in names
    assert len(table.data) == 1
    row = table.data[0]
    assert row["instrument_id"] == "EURUSD"
    # Only mark_eod was seeded for real -- mark_t1/mark_t2 and their dependent P&L
    # columns are genuinely missing, so (2026-09-17 user decision) they are illustrative
    # SAMPLE values, clearly suffixed, never blank/"0.00"/"n/a" (ui.tabs.blotter_fx
    # docstring). mark_eod/pnl_eod are the real computed values and carry no suffix.
    assert row["mark_t1"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert row["mark_t2"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert row["pnl_t1"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert row["pnl_t2"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert row["mark_eod"] != "" and not row["mark_eod"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert row["pnl_eod"] != "" and not row["pnl_eod"].endswith(blotter_fx.SAMPLE_SUFFIX)


def test_blotter_fx_scope_empty_still_renders_table(tmp_path):
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.commit()
    try:
        layout = blotter.scope_layout("fx", conn, "2026-06-20")
    finally:
        conn.close()
    table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
    assert table.data == []
    ids = [c["id"] for c in table.columns]
    assert "pnl_eod" in ids


def test_blotter_fx_scope_has_no_reactive_strip_filter_or_detail_callback(tmp_path):
    """FX (like Rates) is excluded from the generic strip/filter/detail callback loop --
    its rows aren't value_book-shaped (module docstrings). It still shows a P&L strip
    (2026-09-17), but that strip is built statically inside `blotter_fx.build_layout`
    on every render of the sub-tab, not re-scoped by any reactive callback of its own.
    The sub-tab's ONE callback (2026-09-21) reads the trade table's filtered rows and
    writes "P&L by currency, rows shown"; nothing writes to the trade table itself."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(str(db_path))
    assert not any("blotter-strip-fx" in k for k in app.callback_map)
    assert not any(blotter_fx.DATATABLE_ID in k for k in app.callback_map)
    shown = app.callback_map[f"{blotter_fx.SHOWN_CURRENCY_ID}.children"]
    assert [(i["id"], i["property"]) for i in shown["inputs"]] == [(blotter_fx.DATATABLE_ID, "derived_virtual_data")]
    assert shown["state"] == []


# --------------------------------------------------------- blotter FX P&L strip (2026-09-17)


def test_blotter_fx_scoped_trade_ids_is_fx_only():
    """2026-09-17: futures live on their own sub-tab, so the FX strip must not count
    them (it disagreed with the Total book's FX row when it did)."""
    df = pd.DataFrame({
        "trade_id": ["A", "B", "C", "D", "E"],
        "product": ["FX_FWD", "FUTURE", "IRS", "FX_OPTION", "FX_SWAP"],
    })
    assert blotter_fx._scoped_trade_ids(df) == ["A", "E"]
    assert blotter_fx._scoped_trade_ids(pd.DataFrame(columns=["trade_id", "product"])) == []


def test_blotter_fx_table_and_strip_agree_on_pnl(tmp_path):
    """Since the FX sub-tab table (`fx_blotter_rows`) and the strip (`_fx_strip`) both
    price through the identical `priced_value_book` pipeline (2026-09-17 reversal of the
    earlier xlsx-replica table, which deliberately used a DIFFERENT number from the
    strip), a priced trade's `pnl_eod` cell and the strip's LTD figure must now agree
    exactly: quantity * (mark - fill) = 1,000,000 * (1.1200 - 1.10) = 20,000 USD."""
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    _seed_fx_trade(conn)
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','EURUSD','2026-06-20','FWD_OUTRIGHT',"
        "1.1200,'BBG_BFXFORWARD','2026-06-20T15:00:00-04:00')"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    try:
        layout = blotter.scope_layout("fx", conn, "2026-06-20")
        rows = blotter_fx.fx_blotter_rows(conn, "2026-06-20", value_fn=blotter_fx._priced_value_fn)
        priced, _, _ = priced_value_book(conn, "2026-06-20")
    finally:
        conn.close()

    text = str(layout)
    assert "LTD P&L" in text
    assert "20,000" in text

    table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
    assert len(table.data) == 1
    assert not table.data[0]["mark_eod"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert table.data[0]["pnl_eod"] == format_cell(20_000.0)

    # The underlying maths: fx_blotter_rows's pnl_eod for this trade is exactly
    # priced_value_book's pnl_usd for the same trade_id -- one shared pricing path.
    row = rows[rows["trade_id"] == "T1"].iloc[0]
    priced_row = priced[priced["trade_id"] == "T1"].iloc[0]
    assert row["pnl_eod"] == pytest.approx(priced_row["pnl_usd"])
    assert row["pnl_eod"] == pytest.approx(20_000.0)


def test_blotter_fx_strip_renders_placeholder_when_no_fx_or_future_trades(tmp_path):
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.commit()
    try:
        layout = blotter.scope_layout("fx", conn, "2026-06-20")
    finally:
        conn.close()
    text = str(layout)
    assert "LTD P&L" in text  # strip still renders (with a 0/n.a. figure), never omitted


# --------------------------------------------------------- blotter FX sample values (2026-09-17)


def _sample_fixture_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "trade_id": "T1", "instrument_id": "EURUSD", "quantity_usd_notional": 1_000_000.0,
        "fill": 1.10, "mark_eod": 1.12, "mark_t1": None, "mark_t2": None,
        "pnl_eod": 20_000.0, "pnl_t1": None, "pnl_t2": None,
    }])


def test_blotter_fx_sample_values_fill_only_missing_cells():
    df = _sample_fixture_df()
    out, mask = blotter_fx._fill_sample_values(df)

    # Real cells are untouched.
    assert out.loc[0, "mark_eod"] == 1.12
    assert out.loc[0, "pnl_eod"] == 20_000.0
    assert mask["mark_eod"][0] is False
    assert mask["pnl_eod"][0] is False

    # Genuinely missing cells get a deterministic sample, derived from fill.
    expected_t1 = 1.10 * (1 + blotter_fx._MARK_SAMPLE_OFFSETS["mark_t1"])
    assert out.loc[0, "mark_t1"] == pytest.approx(expected_t1)
    assert mask["mark_t1"][0] is True
    assert out.loc[0, "pnl_t1"] is not None and out.loc[0, "pnl_t1"] == out.loc[0, "pnl_t1"]
    assert mask["pnl_t1"][0] is True

    # Re-running on the same input is deterministic (not random).
    out2, mask2 = blotter_fx._fill_sample_values(df)
    assert out2.loc[0, "mark_t1"] == out.loc[0, "mark_t1"]


def test_blotter_fx_sample_cells_are_visually_flagged():
    df = _sample_fixture_df()
    out, mask = blotter_fx._fill_sample_values(df)

    records = blotter_fx.format_rows(out, sample_mask=mask)
    assert records[0]["mark_t1"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert not records[0]["mark_eod"].endswith(blotter_fx.SAMPLE_SUFFIX)

    # One rule per column with any sample cell, keyed on the "(sample)" suffix -- not
    # one rule per cell, which would be thousands of rules on a full book.
    table = blotter_fx.fx_blotter_table(out, sample_mask=mask)
    sample_rules = [r for r in table.style_data_conditional
                     if r["if"].get("column_id") == "mark_t1"]
    assert len(sample_rules) == 1, "expected exactly one style rule for the mark_t1 column"
    assert sample_rules[0].get("fontStyle") == "italic"
    assert "(sample)" in sample_rules[0]["if"]["filter_query"]
    no_rules_for_real_cell = [r for r in table.style_data_conditional
                                if r["if"].get("column_id") == "mark_eod"]
    assert no_rules_for_real_cell == []
    assert len(table.style_data_conditional) <= len(blotter_fx._SAMPLE_MARK_COLS) + len(blotter_fx._SAMPLE_PNL_COLS)


def test_blotter_fx_sample_caption_present_only_when_samples_used(tmp_path):
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    _seed_fx_trade(conn)  # only mark_eod seeded below -> mark_t1/mark_t2 sample-filled
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-06-20','EURUSD','2026-06-20','FWD_OUTRIGHT',1.1080,'BBG_BFXFORWARD',"
        "'2026-06-20T15:00:00-04:00')"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    try:
        layout = blotter.scope_layout("fx", conn, "2026-06-20")
    finally:
        conn.close()
    assert blotter_fx.SAMPLE_CAPTION in str(layout)


def test_blotter_fx_sample_caption_absent_when_every_mark_is_real(tmp_path):
    from engine.pnl.aggregate import _n_business_days_back

    as_of = "2026-06-20"
    from engine.pnl.aggregate import load_holidays
    holidays = load_holidays()  # 2026-06-19 (Juneteenth) is a holiday: t-1 must skip it, as the engine does
    t1_date = _n_business_days_back(dt.date.fromisoformat(as_of), 1, holidays).isoformat()
    t2_date = _n_business_days_back(dt.date.fromisoformat(as_of), 2, holidays).isoformat()

    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    _seed_fx_trade(conn, trade_date="2026-06-15")  # dealt before t-2 (06-17) so all three days price
    conn.executemany(
        "INSERT INTO marks VALUES (?,?,'2026-06-20','FWD_OUTRIGHT',?,'BBG_BFXFORWARD',?)",
        [
            (as_of, "EURUSD", 1.1080, as_of + "T15:00:00-04:00"),
            (t1_date, "EURUSD", 1.1075, t1_date + "T15:00:00-04:00"),
            (t2_date, "EURUSD", 1.1070, t2_date + "T15:00:00-04:00"),
        ],
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    try:
        layout = blotter.scope_layout("fx", conn, as_of)
    finally:
        conn.close()

    table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
    row = table.data[0]
    assert not row["mark_t1"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert not row["mark_eod"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert not row["mark_t2"].endswith(blotter_fx.SAMPLE_SUFFIX)
    assert blotter_fx.SAMPLE_CAPTION not in str(layout)


# --------------------------------------------------------- blotter FX P&L by currency (2026-09-21)
# Two tables above the trade table: "P&L by currency" (whole FX book, the strip's own
# pricing path per currency) and "P&L by currency, rows shown" (sums of the rows the trade
# table shows after its native filter, made from hidden real numbers, never from samples).

_CCY_AS_OF = "2026-06-24"   # a Wednesday: T-1 = 06-23 and T-2 = 06-22, no holiday in between
_CCY_T1, _CCY_T2 = "2026-06-23", "2026-06-22"
_CCY_SETTLE = "2026-07-15"
_JPY_LTD = (1_000_000 * 2.0 - 500_000 * 1.0 + 1_000_000 * 3.0) / 152.5   # J1 + J2 + J3, quote P&L at spot


def _ccy_trade(conn, trade_id, pair, base, quote, quantity, price, trade_date="2026-06-15"):
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description, theme) "
        "VALUES (?,'XLSX',?,'FX_FWD',?,?,?,?,'ACC','CPTY','','TR','fwd','')",
        (trade_id, pair, trade_id, trade_date, quantity, price))
    conn.executemany(
        "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
        "settles_cash) VALUES (?,?,'FX_NEAR',?,?,?,?,?,1)",
        [(trade_id, 1, base, quantity, trade_date, _CCY_SETTLE, price),
         (trade_id, 2, quote, -quantity * price, trade_date, _CCY_SETTLE, price)])


def _ccy_marks(conn, pair, mark_type, by_date):
    for day, value in by_date.items():
        conn.execute(
            "INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,'BBG_BFXFORWARD',?)",
            (day, pair, _CCY_SETTLE if mark_type == "FWD_OUTRIGHT" else day, mark_type, value,
             day + "T17:00:00-04:00"))


def _currency_book(db_path, aud_history=True):
    """USDJPY x3 (one dealt on the as-of), AUDUSD, XAUUSD, the cross EURSEK (converted through
    USDSEK), USDMXN with no mark at all, and a future that must stay out of the FX sub-tab.
    Marks on the as-of, T-1 and T-2; `aud_history=False` leaves AUDUSD with today's only, so
    its T-1 / T-2 cells are illustrative samples."""
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    for pair, base, quote in [("USDJPY", "USD", "JPY"), ("AUDUSD", "AUD", "USD"), ("XAUUSD", "XAU", "USD"),
                              ("EURSEK", "EUR", "SEK"), ("USDSEK", "USD", "SEK"), ("USDMXN", "USD", "MXN")]:
        conn.execute(
            "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
            "bbg_ticker, expiry_date) VALUES (?,'FX',?,?,1,0,?,'9999-12-31')", (pair, base, quote, pair + " Curncy"))
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    _ccy_trade(conn, "J1", "USDJPY", "USD", "JPY", 1_000_000, 150.0)
    _ccy_trade(conn, "J2", "USDJPY", "USD", "JPY", -500_000, 151.0)
    _ccy_trade(conn, "J3", "USDJPY", "USD", "JPY", 1_000_000, 149.0, trade_date=_CCY_AS_OF)
    _ccy_trade(conn, "A1", "AUDUSD", "AUD", "USD", 2_000_000, 0.65)
    _ccy_trade(conn, "X1", "XAUUSD", "XAU", "USD", 100, 2400.0)
    _ccy_trade(conn, "E1", "EURSEK", "EUR", "SEK", 1_000_000, 11.40)
    _ccy_trade(conn, "M1", "USDMXN", "USD", "MXN", 1_000_000, 18.0)
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description, theme) "
        "VALUES ('F1','XLSX','ESU6 Index','FUTURE','F1','2026-06-15',2,5000,'ACC','CPTY','','TR','fut','')")
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
        "settles_cash) VALUES ('F1',1,'NOTIONAL','USD',500000,'2026-06-15','2026-09-18',5000,0)")

    def three(today, t1, t2):
        return {_CCY_AS_OF: today, _CCY_T1: t1, _CCY_T2: t2}

    _ccy_marks(conn, "USDJPY", "FWD_OUTRIGHT", three(152.0, 151.5, 151.0))
    _ccy_marks(conn, "USDJPY", "SPOT", three(152.5, 152.0, 151.5))
    _ccy_marks(conn, "AUDUSD", "FWD_OUTRIGHT", three(0.66, 0.658, 0.655) if aud_history else {_CCY_AS_OF: 0.66})
    _ccy_marks(conn, "XAUUSD", "FWD_OUTRIGHT", three(2450.0, 2440.0, 2430.0))
    _ccy_marks(conn, "EURSEK", "FWD_OUTRIGHT", three(11.50, 11.45, 11.42))
    _ccy_marks(conn, "USDSEK", "SPOT", three(10.0, 10.0, 10.0))
    conn.commit()
    conn.close()


def _nodes(component):
    """Every node under `component`, depth first, in document order."""
    stack = [component]
    while stack:
        node = stack.pop()
        if isinstance(node, (list, tuple)):
            stack.extend(reversed(node))
            continue
        yield node
        children = getattr(node, "children", None)
        if children is not None and not isinstance(children, str):
            stack.append(children)


def _currency_tables(component) -> list:
    """Each per-currency table under `component`, in document order, as
    `{"Currency": [heading texts], "<row label>": [its cells]}`."""
    tables = []
    for table in (n for n in _nodes(component) if isinstance(n, dash.html.Table)):
        rows = {}
        for tr in (n for n in _nodes(table) if isinstance(n, dash.html.Tr)):
            cells = list(tr.children)
            rows[cells[0].children] = [c.children for c in cells] if isinstance(cells[0], dash.html.Th) else cells
        tables.append(rows)
    return tables


def _cell(table_rows, row, column):
    return table_rows[row][table_rows["Currency"].index(column)]


def _fx_layout(db_path, as_of=_CCY_AS_OF):
    conn = sqlite3.connect(db_path)
    try:
        return blotter.scope_layout("fx", conn, as_of)
    finally:
        conn.close()


def test_blotter_fx_currency_label_is_the_non_usd_side_and_a_cross_keeps_its_pair_name():
    assert blotter_fx.currency_label("USDJPY", "USD", "JPY") == "JPY"
    assert blotter_fx.currency_label("AUDUSD", "AUD", "USD") == "AUD"
    assert blotter_fx.currency_label("XAUUSD", "XAU", "USD") == "XAU"
    assert blotter_fx.currency_label("EURSEK", "EUR", "SEK") == "EURSEK"   # one number, never split in two
    # No currencies on file for it: a six-letter id is read as the pair it names.
    assert blotter_fx.currency_label("USDTRY") == "TRY"
    assert blotter_fx.currency_label("NZDUSD") == "NZD"
    assert blotter_fx.currency_label("EURNOK") == "EURNOK"
    assert blotter_fx.currency_label("ESU6 Index") == "ESU6 Index"


def test_blotter_fx_by_currency_groups_the_whole_fx_book_including_a_cross(tmp_path):
    db_path = tmp_path / "risk.db"
    _currency_book(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows, total = blotter_fx.by_currency_rows(conn, _CCY_AS_OF)
    finally:
        conn.close()
    by = {r["currency"]: r for r in rows}
    # The cross is ONE row under its pair name (no "EUR", no "SEK"); USDSEK only converts
    # it and has no trade; the future belongs to another sub-tab.
    assert {c: r["trades"] for c, r in by.items()} == {"JPY": 3, "AUD": 1, "XAU": 1, "EURSEK": 1, "MXN": 1}
    assert (total["currency"], total["trades"]) == ("Total", 7)
    assert by["JPY"]["figures"]["ltd"]["value"] == pytest.approx(_JPY_LTD)
    assert by["AUD"]["figures"]["ltd"]["value"] == pytest.approx(20_000.0)      # 2m x (0.66 - 0.65)
    assert by["XAU"]["figures"]["ltd"]["value"] == pytest.approx(5_000.0)       # 100 x (2450 - 2400)
    assert by["EURSEK"]["figures"]["ltd"]["value"] == pytest.approx(10_000.0)   # 1m x 0.10 SEK at 10 SEK per USD
    assert not by["MXN"]["figures"]["ltd"]["available"]
    # |LTD| descending, the unavailable currency last.
    assert [r["currency"] for r in rows] == ["JPY", "AUD", "EURSEK", "XAU", "MXN"]


def test_blotter_fx_fixed_table_total_is_the_strip_and_rows_shown_start_equal_to_it(tmp_path):
    from ui.tabs.blotter_pricing import row_scoped_headline

    db_path = tmp_path / "risk.db"
    _currency_book(db_path)
    layout = _fx_layout(db_path)
    fixed, shown = _currency_tables(layout)
    assert fixed["Currency"] == ["Currency", "Trades", "LTD", "Daily", "Previous day", "5d", "MTD", "YTD"]
    assert shown["Currency"] == ["Currency", "Trades shown", "LTD", "LTD-1", "LTD-2", "Daily", "Previous day"]

    # The Total row reads exactly what the strip's cards read, figure for figure.
    cards = {n.children[0].children: n.children[1].children
             for n in _nodes(layout) if getattr(n, "className", "") == "card"}
    for card, column in [("LTD P&L", "LTD"), ("Daily P&L", "Daily"), ("Previous day P&L", "Previous day"),
                         ("5d", "5d"), ("MTD", "MTD"), ("YTD", "YTD"), ("Trades", "Trades")]:
        assert _cell(fixed, "Total", column).children == cards[card], column
    assert _cell(fixed, "Total", "LTD").children == format_cell(_JPY_LTD + 35_000.0)

    # ... and is the pricing path's own figure, not a sum made here.
    conn = sqlite3.connect(db_path)
    try:
        headline = row_scoped_headline(conn, _CCY_AS_OF, ["J1", "J2", "J3", "A1", "X1", "E1", "M1"])
        _rows, total = blotter_fx.by_currency_rows(conn, _CCY_AS_OF)
    finally:
        conn.close()
    for key, _label in blotter_fx.FIXED_PERIODS:
        assert total["figures"][key]["available"] == headline[key]["available"], key
        if headline[key]["available"]:
            assert total["figures"][key]["value"] == pytest.approx(headline[key]["value"]), key

    # With no filter typed the rows-shown table opens on the same LTD column, row for row.
    assert [k for k in shown if k != "Currency"] == [k for k in fixed if k != "Currency"]
    for currency in fixed:
        if currency != "Currency":
            assert _cell(shown, currency, "LTD").children == _cell(fixed, currency, "LTD").children, currency
            assert _cell(shown, currency, "Trades shown").children == _cell(fixed, currency, "Trades").children


def test_blotter_fx_currency_tables_sit_between_the_strip_and_the_trade_table(tmp_path):
    db_path = tmp_path / "risk.db"
    _currency_book(db_path)
    layout = _fx_layout(db_path)
    kinds = ["table" if isinstance(c, dash.dash_table.DataTable)
             else "currency" if getattr(c, "id", None) == blotter_fx.CURRENCY_TABLES_ID
             else "strip" if any(getattr(n, "className", "") == "cards" for n in _nodes(c)) else "other"
             for c in layout.children]
    assert [k for k in kinds if k != "other"] == ["strip", "currency", "table"]
    block = next(c for c in layout.children if getattr(c, "id", None) == blotter_fx.CURRENCY_TABLES_ID)
    assert block.className == "fx-ccy-tables" and len(block.children) == 2   # side by side, stacking when narrow
    text = str(layout)
    assert "Whole FX book" in text and "Rows shown in the table below" in text
    assert blotter_fx.SHOWN_CURRENCY_ID in _all_ids(layout)

    table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
    assert table.filter_action == "native"
    assert getattr(table, "sort_action", None) in (None, "none")   # its fixed order is kept
    assert "filter_query" in table.persisted_props               # a rebuild on new marks keeps what was typed
    assert table.hidden_columns == blotter_fx._HIDDEN_COLUMNS


def test_blotter_fx_sample_values_never_enter_the_rows_shown_sums(strict_marks, tmp_path):
    db_path = tmp_path / "risk.db"
    _currency_book(db_path, aud_history=False)   # AUDUSD has today's mark only; USDMXN has none
    layout = _fx_layout(db_path)
    table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
    by_id = {r["trade_id"]: r for r in table.data}
    # On screen: illustrative figures, flagged. Behind them: no number at all.
    assert by_id["A1"]["pnl_t1"].endswith(blotter_fx.SAMPLE_SUFFIX) and by_id["A1"]["pnl_t1_num"] is None
    assert by_id["M1"]["pnl_eod"].endswith(blotter_fx.SAMPLE_SUFFIX) and by_id["M1"]["pnl_eod_num"] is None
    assert by_id["A1"]["pnl_eod_num"] == pytest.approx(20_000.0)   # a real cell carries its real number
    assert by_id["J3"]["on_book_t1"] == 0 and by_id["J1"]["on_book_t1"] == 1

    rows, total = blotter_fx.shown_currency_rows(table.data)
    by = {r["currency"]: r["figures"] for r in rows}
    assert total["figures"]["ltd"]["value"] == pytest.approx(_JPY_LTD + 35_000.0)   # M1's sample is not in it
    assert total["figures"]["ltd"]["excluded_summary"] == "excludes 1 of 7 rows unpriced"
    assert not by["MXN"]["ltd"]["available"] and "no real LTD figure" in by["MXN"]["ltd"]["reason"]
    assert not by["AUD"]["ltd1"]["available"]                      # its only T-1 figure is a sample
    assert not by["AUD"]["daily"]["available"] and "LTD-1" in by["AUD"]["daily"]["reason"]
    # Six trades were on the book at T-1 (J3 was dealt today); A1 and M1 have no real value there.
    assert total["figures"]["ltd1"]["excluded_summary"] == "excludes 2 of 6 rows unpriced"
    ltd1_jpy = (1_000_000 * 1.5 - 500_000 * 0.5) / 152.0
    assert total["figures"]["ltd1"]["value"] == pytest.approx(ltd1_jpy + 100 * 40.0 + 1_000_000 * 0.05 / 10.0)

    # A formatted string, a sample string or junk in a number column is refused, never parsed.
    rows, total = blotter_fx.shown_currency_rows([
        {"currency": "JPY", "pnl_eod": "500", "pnl_eod_num": 500.0},
        {"currency": "JPY", "pnl_eod": "1,000 (sample)", "pnl_eod_num": None},
        {"currency": "JPY", "pnl_eod": "9,999", "pnl_eod_num": "9,999"},
    ])
    assert rows[0]["figures"]["ltd"]["value"] == 500.0
    assert rows[0]["figures"]["ltd"]["excluded_summary"] == "excludes 2 of 3 rows unpriced"


def test_blotter_fx_rows_shown_differences_use_rows_priced_at_both_ends():
    def row(eod, t1, t2=None, on_t1=1, on_t2=1):
        return {"currency": "JPY", "pnl_eod_num": eod, "pnl_t1_num": t1, "pnl_t2_num": t2,
                "on_book_t1": on_t1, "on_book_t2": on_t2}

    rows = [row(100.0, 60.0, 50.0),              # priced at every close
            row(30.0, None, on_t1=0, on_t2=0),   # dealt today: counts in full, as in the strip
            row(500.0, None),                    # priced today only: left out, no one-sided jump
            row(None, 10.0, 4.0)]                # unpriced today
    _by, total = blotter_fx.shown_currency_rows(rows)
    figures = total["figures"]
    assert figures["ltd"]["value"] == 630.0 and figures["ltd"]["excluded_summary"] == "excludes 1 of 4 rows unpriced"
    assert figures["ltd1"]["value"] == 70.0 and figures["ltd1"]["excluded_summary"] == "excludes 1 of 3 rows unpriced"
    assert figures["daily"]["value"] == 70.0     # (100 - 60) + 30
    assert figures["daily"]["excluded_summary"] == "excludes 2 of 4 rows unpriced"
    assert figures["ltd1_daily"]["value"] == 16.0   # (60 - 50) + (10 - 4), over the three on the book at T-1
    assert figures["ltd1_daily"]["excluded_summary"] == "excludes 1 of 3 rows unpriced"

    # Most rows have no T-1 figure: no Daily at all, with the reason, rather than a sum of a minority.
    _by, total = blotter_fx.shown_currency_rows([row(100.0, 60.0), row(500.0, None), row(700.0, None)])
    daily = total["figures"]["daily"]
    assert not daily["available"] and "2 of 3 rows shown" in daily["reason"] and "LTD-1" in daily["reason"]

    # Nothing shown: a Total of zero trades, not an error.
    by, total = blotter_fx.shown_currency_rows([])
    assert by == [] and total["trades"] == 0 and total["figures"]["ltd"]["value"] == 0.0
    assert blotter_fx.shown_currency_rows(None)[1]["trades"] == 0


def test_blotter_fx_rows_shown_callback_follows_the_filtered_rows(tmp_path):
    db_path = tmp_path / "risk.db"
    _currency_book(db_path)
    table = next(c for c in _fx_layout(db_path).children if isinstance(c, dash.dash_table.DataTable))

    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    blotter_fx.register_callbacks(app)
    callback = app.callback_map[f"{blotter_fx.SHOWN_CURRENCY_ID}.children"]["callback"]
    update = getattr(callback, "__wrapped__", callback)

    # What the browser sends once "JPY" is typed in the Instrument filter: the matching rows only.
    jpy_rows = [r for r in table.data if "JPY" in r["instrument_id"]]
    (shown,) = _currency_tables(update(jpy_rows))
    assert [k for k in shown if k != "Currency"] == ["JPY", "Total"]
    assert _cell(shown, "Total", "Trades shown").children == "3"
    assert _cell(shown, "Total", "LTD").children == format_cell(_JPY_LTD)
    assert _cell(shown, "JPY", "LTD").className.endswith("fx-ccy-num--pos")

    (everything,) = _currency_tables(update(table.data))
    assert _cell(everything, "Total", "Trades shown").children == "7"
    assert _cell(everything, "Total", "LTD").children == format_cell(_JPY_LTD + 35_000.0)

    (nothing,) = _currency_tables(update([]))   # a filter that matches no row
    assert [k for k in nothing if k != "Currency"] == ["Total"]
    assert _cell(nothing, "Total", "Trades shown").children == "0"
    assert update(None) is dash.no_update        # the table has not worked its rows out yet


def test_blotter_fx_unavailable_and_partial_figures_say_why(tmp_path):
    from ui.tabs.blotter_pricing import row_scoped_headline

    db_path = tmp_path / "risk.db"
    _currency_book(db_path)
    layout = _fx_layout(db_path)
    fixed, shown = _currency_tables(layout)
    conn = sqlite3.connect(db_path)
    try:
        reason = row_scoped_headline(conn, _CCY_AS_OF, ["M1"])["ltd"]["reason"]
    finally:
        conn.close()

    # Unavailable: "n/a", muted, with the pricing path's own reason on hover. Never a zero.
    cell = _cell(fixed, "MXN", "LTD")
    assert cell.children == "n/a" and "cell--unavailable" in cell.className
    assert reason and cell.title == reason
    cell = _cell(shown, "MXN", "LTD")
    assert cell.children == "n/a" and "cell--unavailable" in cell.className and "no real LTD figure" in cell.title

    # A sum of the priced trades only: flagged, the count on hover, and said in a visible line.
    cell = _cell(fixed, "Total", "LTD")
    assert "fx-ccy-num--partial" in cell.className and cell.title.startswith("excludes 1 of 7 trades unpriced")
    notes = [n.children for n in _nodes(layout) if getattr(n, "className", "") == "fx-ccy-note"]
    assert len(notes) == 2
    assert "Total LTD excludes 1 of 7 trades unpriced." in notes[0] and "n/a: hover it for the reason." in notes[0]
    assert "Total LTD excludes 1 of 7 rows unpriced." in notes[1]

    # A loss is in brackets and red, like everywhere else in the app.
    cell = blotter_fx._figure_cell({"value": -1234.4, "available": True})
    assert cell.children == "(1,234)" and "fx-ccy-num--neg" in cell.className
    # Anything else the pricing path puts in an entry is carried along and ignored ...
    cell = blotter_fx._figure_cell({"value": 10.0, "available": True, "chosen_close": "2026-06-22", "x": object()})
    assert cell.children == "10" and "fx-ccy-num--partial" not in cell.className
    # ... except its own caption for a period measured from an earlier close, shown on hover.
    cell = blotter_fx._figure_cell({"value": 10.0, "available": True, "ref_note": "from the 2026-06-19 close",
                                    "excluded_summary": "excludes 1 of 2 trades unpriced"})
    assert cell.title == "excludes 1 of 2 trades unpriced\nfrom the 2026-06-19 close"
    assert "fx-ccy-num--partial" in cell.className


def test_blotter_fx_by_currency_is_reworked_only_when_the_figures_can_have_moved(tmp_path, monkeypatch):
    """Coming back to the sub-tab with nothing changed costs one whole-book headline (the
    Total, which has to equal the strip), not one per currency; any change that moves the
    Total, or a trade from one currency to another, drops the kept rows even under the
    same cache key."""
    db_path = tmp_path / "risk.db"
    _currency_book(db_path)
    calls = []
    real = blotter_fx.row_scoped_headline
    monkeypatch.setattr(blotter_fx, "row_scoped_headline",
                        lambda conn, as_of, ids: calls.append(len(list(ids))) or real(conn, as_of, ids))
    monkeypatch.setattr(blotter_fx, "_render_cache_key", lambda conn: ("same-file", 1.0))   # mtime did not move
    blotter_fx._BY_CURRENCY_CACHE.clear()

    conn = sqlite3.connect(db_path)
    try:
        first, _total = blotter_fx.by_currency_rows(conn, _CCY_AS_OF)
        assert sorted(calls) == [1, 1, 1, 1, 3, 7]        # five currencies and the Total
        calls.clear()
        again, _total = blotter_fx.by_currency_rows(conn, _CCY_AS_OF)
        assert calls == [7] and again is first            # the Total only

        calls.clear()
        conn.execute("UPDATE marks SET value = 0.67 WHERE instrument_id = 'AUDUSD' AND as_of_date = ?", (_CCY_AS_OF,))
        conn.commit()
        moved, total = blotter_fx.by_currency_rows(conn, _CCY_AS_OF)
        assert len(calls) == 6
        aud = next(r for r in moved if r["currency"] == "AUD")
        assert aud["figures"]["ltd"]["value"] == pytest.approx(40_000.0)
        assert total["figures"]["ltd"]["value"] == pytest.approx(_JPY_LTD + 55_000.0)
    finally:
        conn.close()
        blotter_fx._BY_CURRENCY_CACHE.clear()
