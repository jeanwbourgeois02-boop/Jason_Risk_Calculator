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
from ui import uploads  # noqa: E402


def _seed(conn):
    conn.execute(
        "INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES "
        "('t1','BNP','USDJPY','FX_SPOT','t1','2026-08-17',1000000,147.10,"
        "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc','')"
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
    conn.execute(
        "INSERT INTO positions VALUES "
        "('2026-08-17','BNP','BNPP-IPBFX-NMMF','USDJPY','2026-08-17',1000000,147100000,147.12,"
        "0.0068,147120000,1000000,1000,1000,1000)"
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
    assert _tab_labels(layout) == ["Ladder", "Blotter", "Market data"]
    assert uiapp.VISIBLE_TABS == ["Ladder", "Blotter", "Market data"]


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
    key = [k for k in app.callback_map if k.startswith("..tab-body-ladder.style")]
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


def test_ladder_heading_text_and_today_button(tmp_path):
    """Coordinator addition 2026-09-15: the bare date-picker card is replaced by a
    heading naming the as-of date, the picker, and a Today button."""
    assert cash_ladder.heading_date_text("2026-09-15") == "Tuesday 15 September 2026"
    assert cash_ladder.heading_date_text(None) == "As of - no date selected"

    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    ids = _all_ids(app.layout)
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


def test_bnp_forward_proxy_rates_uses_earliest_settle_date(tmp_path):
    """Coordinator addition 2026-09-15, item 5: BNP carries no SPOT for AUD/EUR/GBP/XAU
    (Fx = 1 on those rows); when even bnp_bval_rates finds nothing, the earliest-
    settle_date BNP_BVAL forward outright from the latest snapshot on or before as_of
    is used as a spot proxy, labelled distinctly."""
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')"
    )
    # Two forward outrights on the same snapshot: the earlier settle_date must win.
    conn.executemany(
        "INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
        [
            ("2026-08-17", "EURUSD", "2026-09-01", "FWD_OUTRIGHT", 1.1050, "BNP_BVAL", "2026-08-17T15:00:00-04:00"),
            ("2026-08-17", "EURUSD", "2026-10-01", "FWD_OUTRIGHT", 1.1100, "BNP_BVAL", "2026-08-17T15:00:00-04:00"),
        ],
    )
    conn.commit()
    conn.close()

    conn = uiapp.connect_readonly(db_path)
    try:
        out = cash_ladder.bnp_forward_proxy_rates(conn, "2026-08-18", {"EUR"})
    finally:
        conn.close()
    assert out["EUR"]["rate"] == 1.1050
    assert out["EUR"]["source"] == cash_ladder.FORWARD_PROXY_SOURCE_LABEL
    assert out["EUR"]["inverted"] is False


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


def test_every_static_callback_id_exists_in_layout():
    """A callback Input/State/Output whose component is not in the initial layout
    fires with a missing argument ('Inputs do not match callback definition', HTTP 500)
    or never fires. Components created inside another callback's output are exempt
    only if listed here with a reason."""
    import ui.app as a
    app = a.create_app()
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
    walk(app.layout)
    # rendered inside blotter-table-container; options-datatable/-collapsed-packages
    # (ui.tabs.options, Phase 8 options_calc merge) only exist once the "options"
    # sub-tab is selected, same as the other blotter-* dynamic ids below.
    dynamic_ok = {"blotter-datatable", "blotter-subtotal",
                  "options-datatable", "options-collapsed-packages"}
    dynamic_prefixes = ("blotter-datatable-", "blotter-strip-", "blotter-bundle-",
                        "blotter-row-detail-", "options-terms-")  # per-sub-tab, rendered by callback
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
    settling today is still cash that moves today) but Net/Gross USD and per-currency
    delta must use settle_date > as_of (no delta by close on the settlement day) --
    engine/ladder/exposure_adapter.exposure_records_from_db vs records_from_db. t1
    (seeded, settles 2026-08-17 = as_of) must appear in the grid record set but drop out
    of the exposure record set and out of net_gross_usd; t2 (settles 2026-08-20, still
    open) must appear in both and drive net_gross_usd's non-zero total."""
    from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db

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
        assert {r["trade_id"] for r in exposure_records} == {"t2"}

        result = cash_ladder.net_gross_usd(conn, "2026-08-17")
    finally:
        conn.close()
    assert result["available"] is True
    # Only t2's 2,000,000 USD notional drives Net/Gross -- t1 (settling on as_of) is
    # excluded from the delta math even though it is still present in the grid above.
    assert result["gross"] == pytest.approx(2_000_000, rel=1e-3)


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
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "none", "trades": 2, "positions": 0})

    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    result, source_line, stage_style = fn(1, contents,"blotter.csv")

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
    result, source_line, stage_style = fn(1, contents,"not-a-blotter.csv")

    assert "not a trade blotter" in str(result)
    assert source_line is dash.no_update


def test_selected_shows_filename_for_recognized_blotter_file(monkeypatch):
    app = uiapp.create_app(db_path=None, start_feed=False)
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


def test_selected_shows_error_for_unrecognized_file(monkeypatch):
    app = uiapp.create_app(db_path=None, start_feed=False)
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
    assert uploads.describe_source({"as_of_date": "none", "trades": 5, "positions": 0}) == \
        "Loaded: 5 trades in database."


def test_describe_source_nothing_loaded():
    assert uploads.describe_source({"as_of_date": "none", "trades": 0, "positions": 0}) == \
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


def test_blotter_fx_scope_renders_table_columns(tmp_path):
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
    ids = [c["id"] for c in table.columns]
    assert ids == [
        "trade_date", "instrument_id", "quantity_usd_notional", "tenor", "fill",
        "mark_t1", "mark_eod", "mark_t2", "pnl_t1", "pnl_eod", "pnl_t2",
    ]
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
    on every render of the sub-tab, not re-scoped by any reactive callback of its own."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(str(db_path))
    assert not any("blotter-strip-fx" in k for k in app.callback_map)
    assert not any(blotter_fx.DATATABLE_ID in k for k in app.callback_map)


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
