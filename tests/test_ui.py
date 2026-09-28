"""Smoke tests for the assembled Dash app (ui/app.py), per docs/BUILD_PLAN.md section 5
("Tabs (layer 3)") and the Task C5 wiring prompt.

These are smoke tests only: each tab module (ui/tabs/blotter.py,
market_data.py, header.py) owns its own detailed tests (tests/test_ui_ladder.py
etc.). This file only checks that ui/app.py assembles them correctly -- layout
builds with and without a database, the three tabs are present in the right order,
the header is present, no component id collides across modules, and each module's
register_callbacks is invoked exactly once by create_app().
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui.tabs import blotter, controls, header, market_data  # noqa: E402
from ui import revision, uploads  # noqa: E402


def _seed(conn):
    conn.execute(
        "INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES "
        "('t1','XLSX','USDJPY','FX_SPOT','t1','2026-08-17',1000000,147.10,"
        "'BNPP-IPBFX-NMMF','CPTY','HAHY7','trader','desc','','','')"
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
        "('2026-08-17','USDJPY','2026-08-17','SPOT',147.12,'BBG_BFXFORWARD','2026-08-17T17:00:00-04:00')"
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
    if comp_id is not None:   # a pattern-matching id (a tab link) is a dict: hashed as its JSON
        ids.append(comp_id if isinstance(comp_id, str) else json.dumps(comp_id, sort_keys=True))
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
        "1000000,147.0,'acc','cp','HAHY7','t','d','','','')"
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
    nesting pushed the navy top-bar around the whole page); all eight bodies live in one
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
    assert slugs == {uiapp.tab_body_id(label) for label in uiapp.VISIBLE_TABS}


def test_tab_show_hide_callback_toggles_bodies(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    key = [k for k in app.callback_map if k.startswith(f"..{uiapp.tab_body_id(uiapp.VISIBLE_TABS[0])}.style")]
    assert key, "expected a callback outputting tab-body-*.style keyed on main-tabs value"
    cb = app.callback_map[key[0]]
    assert any(d["id"] == uiapp.MAIN_TABS_ID and d["property"] == "value" for d in cb["inputs"])


def test_header_as_of_defaults_to_today_follows_a_pick_and_rolls_over_at_midnight(tmp_path):
    """User, 2026-09-22: "by default, always price pnl as of today, so that the top bar
    numbers all reflect todays numbers, unless changed specifically otherwise"."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    assert callable(app.layout)                                   # built on every page load: today is fresh
    ids = _all_ids(app.layout())
    assert header.AS_OF_STORE_ID in ids and header.AS_OF_PICKED_ID in ids
    # the header's own picker feeds the store; the poll rolls the store and the picker to the new day
    keys = list(app.callback_map)
    follow = next(k for k in keys if k.startswith(f"..{header.AS_OF_STORE_ID}.data...{header.AS_OF_PICKED_ID}.data.."))
    inputs = {d["id"] for d in app.callback_map[follow]["inputs"]}
    assert inputs == {header.DATE_PICKER_ID}
    roll = next(k for k in keys if f"{header.DATE_PICKER_ID}.date@" in k and header.AS_OF_STORE_ID in k)
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
    assert controls.ROLLOVER_HOUR_NY == 17
    assert controls.today_ny(datetime(2026, 9, 22, 16, 59, tzinfo=ny)) == "2026-09-22"
    assert controls.today_ny(datetime(2026, 9, 22, 17, 0, tzinfo=ny)) == "2026-09-23"
    assert controls.today_ny(datetime(2026, 9, 22, 23, 30, tzinfo=ny)) == "2026-09-23"
    assert controls.today_ny(datetime(2026, 9, 23, 0, 5, tzinfo=ny)) == "2026-09-23"


def test_heading_date_text_names_the_day_or_says_none_is_selected():
    """`ui.tabs.controls.heading_date_text` (the Ladder's heading helper until 2026-09-28)."""
    assert controls.heading_date_text("2026-09-15") == "Tuesday 15 September 2026"
    assert controls.heading_date_text(None) == "As of - no date selected"


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

    calls = {"header": 0, "blotter": 0, "market_data": 0}

    def _counted(name, original):
        def wrapper(app, get_db_path):
            calls[name] += 1
            return original(app, get_db_path)
        return wrapper

    monkeypatch.setattr(header, "register_callbacks", _counted("header", header.register_callbacks))
    monkeypatch.setattr(blotter, "register_callbacks", _counted("blotter", blotter.register_callbacks))
    monkeypatch.setattr(market_data, "register_callbacks", _counted("market_data", market_data.register_callbacks))

    uiapp.create_app(db_path=db_path, start_feed=False)

    assert calls == {"header": 1, "blotter": 1, "market_data": 1}


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
                  "options-datatable", "options-collapsed-packages",
                  # Screens redesign Phase B (2026-09-25), each rendered inside another callback's
                  # output and filled in place once it is there: the header's VaR chip (inside
                  # header-figures), the Book's alerts (inside book-body, after the VaR pass),
                  # and the Spreads positions table with its drill-down (inside spreads-body).
                  "header-var-chip", "book-alerts",
                  "spreads-table", "spreads-detail-store", "spreads-detail",
                  # Phase C (2026-09-25): the Curve grid, its commodity selector, chart panel and
                  # chart-data store are built by ui.tabs.curve.body() inside curve-body; the
                  # Spreads drill-down's history store and slot by history_slot() inside
                  # spreads-detail (itself inside spreads-body), both filled in place.
                  "curve-grid", "curve-select", "curve-chart", "curve-chart-data",
                  "spreads-history-request", "spreads-history",
                  # UI redesign (2026-09-28): the Book table inside book-body, the P&L tab's LTD
                  # chart details and container inside pnl-body, each filled in place.
                  "book-table", "pnl-ltd-details", "pnl-ltd-details-summary", "pnl-ltd-chart-container",
                  "book-detail", "book-detail-history-request", "book-detail-history"}
    dynamic_prefixes = ("blotter-datatable-", "blotter-strip-", "blotter-bundle-",
                        "blotter-row-detail-", "options-terms-",
                        "blotter-fx-",  # the FX sub-tab's trade table and its "rows shown" currency table (2026-09-21)
                        "manual-")  # per-sub-tab, rendered by callback (manual-*: Manual entry sub-tab, 2026-09-18)
    missing = []
    for key, cb in app.callback_map.items():
        for kind in ("inputs", "state"):
            for d in cb.get(kind, []):
                # Pattern-matching ids (e.g. tab-link, ui/app.py, 2026-09-25) match components
                # rendered anywhere, possibly later; not a static id.
                if d["id"].startswith("{"):
                    continue
                if d["id"] not in ids and d["id"] not in dynamic_ok and not d["id"].startswith(dynamic_prefixes):
                    missing.append((kind, d["id"]))
        for out in key.strip(".").split("..."):
            oid = out.split(".")[0]
            if oid and oid not in ids and oid not in dynamic_ok and not oid.startswith(dynamic_prefixes):
                missing.append(("output", oid))
    assert not missing, missing


# ---------------------------------------------------------------- header compaction (2026-09-15)
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
    assert value_span.children == header.MISSING
    assert value_span.title == "no mark"


def test_header_gross_card_is_never_colour_coded():
    card = header._pnl_card("Gross USD", {"value": -5.0, "available": True}, colour=False)
    assert "header-figure-value--neutral" in card.children[1].className


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
            "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc','','','')"
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

    finally:
        conn.close()


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
    # The button runs tools/bbg_diagnostics.py::run_bloomberg_diagnostics through the
    # data.bloomberg.bbg_diagnostics shim, imported lazily on each click (the 2026-09-16
    # placeholder left with fb65379); the safety net must swallow whatever it raises.
    import data.bloomberg.bbg_diagnostics as shim

    def _boom():
        raise RuntimeError("blpapi not installed")

    monkeypatch.setattr(shim, "run_bloomberg_diagnostics", _boom)
    result = market_data.run_bloomberg_diagnostics_safe()
    assert isinstance(result, list)
    assert result[0]["status"] == "fail"
    assert "Could not reach Bloomberg" in result[0]["message"]
    # No raw exception text/traceback leaks into the message shown to the user.
    assert "RuntimeError" not in result[0]["message"]
    assert "Traceback" not in result[0]["message"]


def test_run_bloomberg_diagnostics_safe_rejects_non_list_result(monkeypatch):
    import data.bloomberg.bbg_diagnostics as shim

    monkeypatch.setattr(shim, "run_bloomberg_diagnostics", lambda: {"not": "a list"})
    result = market_data.run_bloomberg_diagnostics_safe()
    assert isinstance(result, list)
    assert result[0]["status"] == "fail"


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

    summary = ("Imported blotter.csv: 2 trades (1 forwards, 1 futures, 0 options), "
               "4 legs. 0 currency rows seen (no position snapshot -- this file has no EOD balance "
               "grain). Excluded: 0 rows from other funds/status, 0 other unsupported rows.")
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
    assert "Upload blotter" in text
    assert "Upload BNP report" not in text


def test_launch_configures_root_logging_handler():
    """The app's single entry point must attach a logging handler so log.info/log.warning
    calls anywhere in the app (data/ingest/bnp.py, ui/uploads.py) actually go somewhere,
    instead of being dropped by the default unconfigured root logger."""
    import inspect
    from ui import launch

    assert "logging.basicConfig" in inspect.getsource(launch.main)


# --------------------------------------------------------- commodity conversion Phase 2 (2026-09-24)
def test_unpriced_breakdown_labels_have_no_irs_but_keep_the_manual_fx_swap():
    """IRS left the app; an FX swap booked on Manual entry is an FX hedge and stays."""
    from ui.tabs import blotter_pricing as bp
    assert "IRS" not in bp._PRODUCT_LABELS
    assert bp._product_label("FX_SWAP", 2) == "swaps" and bp._product_label("FUTURE", 1) == "future"
    assert "FX_SWAP" in bp.FX_PRODUCTS_FOR_T1_RATE
    unpriced = pd.DataFrame({"trade_id": ["S1", "F1"], "product": ["FX_SWAP", "FUTURE"],
                             "reason": ["no FWD_OUTRIGHT mark for EURUSD", "no FUTURE_PX mark for CLZ26 Comdty"]})
    assert bp._unpriced_breakdown(unpriced) in ("1 swap: no FWD_OUTRIGHT; 1 future: no FUTURE_PX",
                                                 "1 future: no FUTURE_PX; 1 swap: no FWD_OUTRIGHT")


def test_reason_tag_names_a_retired_product_instead_of_unpriced():
    """A leftover IRS / swaption / cap on an old database carries value_book's own "left the app"
    reason: the "excludes" summary says so rather than "unpriced"."""
    from engine.pnl.valuation import RETIRED_PRODUCTS
    from ui.tabs import blotter_pricing as bp
    for reason in RETIRED_PRODUCTS.values():                     # the engine's real wording
        assert bp._reason_tag(reason) == bp.RETIRED_PRODUCT_TAG == "product left the app"
    unpriced = pd.DataFrame({"trade_id": ["I1"], "product": ["IRS"], "reason": [RETIRED_PRODUCTS["IRS"]]})
    assert bp._unpriced_breakdown(unpriced) == "1 irs: product left the app"
    assert bp._reason_tag("something unrecognised entirely") == "unpriced"   # the fallback is unchanged
