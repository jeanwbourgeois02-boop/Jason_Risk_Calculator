"""Smoke tests for the assembled Dash app (ui/app.py), per docs/BUILD_PLAN.md section 5
("Tabs (layer 3)") and the Task C5 wiring prompt.

These are smoke tests only: each tab module (ui/tabs/cash_ladder.py, blotter.py,
market_data.py, reconciliation.py, header.py) owns its own detailed tests
(tests/test_ui_ladder.py etc.). This file only checks that ui/app.py assembles them
correctly -- layout builds with and without a database, the four tabs are present in
the right order, the header is present, no component id collides across modules, and
each module's register_callbacks is invoked exactly once by create_app().
"""
from __future__ import annotations

import sqlite3

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui.tabs import blotter, cash_ladder, header, market_data, reconciliation  # noqa: E402


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


def test_four_tabs_present_in_order(tmp_path):
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    layout = uiapp.build_layout(uiapp.load_summary(db_path))
    assert _tab_labels(layout) == ["Ladder", "Blotter", "Market data", "Reconciliation"]
    assert uiapp.VISIBLE_TABS == ["Ladder", "Blotter", "Market data", "Reconciliation"]


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

    calls = {"header": 0, "cash_ladder": 0, "blotter": 0, "market_data": 0, "reconciliation": 0}

    def _counted(name, original):
        def wrapper(app, get_db_path):
            calls[name] += 1
            return original(app, get_db_path)
        return wrapper

    monkeypatch.setattr(header, "register_callbacks", _counted("header", header.register_callbacks))
    monkeypatch.setattr(cash_ladder, "register_callbacks", _counted("cash_ladder", cash_ladder.register_callbacks))
    monkeypatch.setattr(blotter, "register_callbacks", _counted("blotter", blotter.register_callbacks))
    monkeypatch.setattr(market_data, "register_callbacks", _counted("market_data", market_data.register_callbacks))
    monkeypatch.setattr(reconciliation, "register_callbacks", _counted("reconciliation", reconciliation.register_callbacks))

    uiapp.create_app(db_path=db_path, start_feed=False)

    assert calls == {"header": 1, "cash_ladder": 1, "blotter": 1, "market_data": 1, "reconciliation": 1}


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
    dynamic_ok = {"blotter-datatable", "blotter-subtotal"}  # rendered inside blotter-table-container
    missing = []
    for key, cb in app.callback_map.items():
        for kind in ("inputs", "state"):
            for d in cb.get(kind, []):
                if d["id"] not in ids and d["id"] not in dynamic_ok:
                    missing.append((kind, d["id"]))
        for out in key.strip(".").split("..."):
            oid = out.split(".")[0]
            if oid and oid not in ids and oid not in dynamic_ok:
                missing.append(("output", oid))
    assert not missing, missing
