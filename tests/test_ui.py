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
    assert slugs == {"tab-body-ladder", "tab-body-blotter", "tab-body-market-data", "tab-body-reconciliation"}


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
    dynamic_prefixes = ("blotter-datatable-", "blotter-strip-", "blotter-bundle-",
                        "blotter-row-detail-")  # per-sub-tab, rendered by callback
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
        result = cash_ladder.net_gross_usd(conn, "2026-08-17")
    finally:
        conn.close()
    assert result["available"] is True
    assert abs(result["net"]) == pytest.approx(result["gross"])  # single currency, single pair


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


def _futures_confirm_callback(app):
    cb = app.callback_map["reconciliation-futures-result.children"]["callback"]
    return getattr(cb, "__wrapped__", cb)


class _FakeImportResult(int):
    """Mimics data.ingest.xlsx_futures.ImportResult (int subclass + .issues/.messages)."""

    def __new__(cls, inserted, messages):
        obj = int.__new__(cls, inserted)
        obj._messages = list(messages)
        return obj

    @property
    def messages(self):
        return list(self._messages)


def test_futures_confirm_clean_upload_shows_plain_success_message(tmp_path, monkeypatch):
    """No skipped rows -> the existing simple success string, no warning panel."""
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fn = _futures_confirm_callback(app)

    monkeypatch.setattr(
        "data.ingest.xlsx_futures.load_futures_fills",
        lambda path, conn: _FakeImportResult(4, []),
    )
    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    out = fn(1, contents, "workbook.xlsx")
    assert out == "Imported 4 new futures fill(s) from workbook.xlsx."


# ---------------------------------------------------------------- reconciliation layout
# (2026-09-16 user decision: the as-of date picker confusingly sat directly above the
# futures-import button, though it only ever drives the reconciliation tables below --
# data.ingest.xlsx_futures.load_futures_fills() reads trade dates from the workbook
# rows and never touches this UI field. The date picker moves into its own toolbar;
# the import card stands alone with just choose-file + Confirm.)


def test_futures_import_control_has_no_date_picker_and_only_upload_plus_confirm():
    card = reconciliation.futures_import_control()
    ids = _all_ids(card)
    assert reconciliation.DATE_PICKER_ID not in ids
    assert reconciliation.FUTURES_UPLOAD_ID in ids
    assert reconciliation.FUTURES_CONFIRM_ID in ids


def test_reconciliation_layout_keeps_date_picker_separate_from_upload_card():
    layout = reconciliation.build_layout(default_date="2026-08-18")
    ids = _all_ids(layout)
    # The date picker still exists and still drives the tables (same component id
    # the content callback listens on), just no longer bundled into the upload card.
    assert reconciliation.DATE_PICKER_ID in ids
    assert reconciliation.FUTURES_UPLOAD_ID in ids
    assert reconciliation.FUTURES_CONFIRM_ID in ids


def test_reconciliation_content_callback_still_driven_by_date_picker(tmp_path):
    """Moving the date picker must not break the reconciliation tables' data source:
    the content callback is still wired to DATE_PICKER_ID as an Input."""
    db_path = tmp_path / "risk.db"
    _seeded_db(db_path)
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    key = f"{reconciliation.CONTENT_CONTAINER_ID}.children"
    assert key in app.callback_map
    input_ids = [dep["id"] for dep in app.callback_map[key]["inputs"]]
    assert reconciliation.DATE_PICKER_ID in input_ids


def test_futures_confirm_with_skipped_rows_shows_warning_panel(tmp_path, monkeypatch):
    """Rows skipped -> a non-blocking summary panel listing the plain-English messages,
    not styled as an error (valid rows still loaded)."""
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fn = _futures_confirm_callback(app)

    messages = [
        "Row 3, column 'fill': expected a number, found 'TBD' — this row was skipped.",
        "Row 9, column 'Date': could not parse date — this row was skipped.",
    ]
    monkeypatch.setattr(
        "data.ingest.xlsx_futures.load_futures_fills",
        lambda path, conn: _FakeImportResult(12, messages),
    )
    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    out = fn(1, contents, "workbook.xlsx")

    assert isinstance(out, dash.html.Div)
    assert out.className == "source-result--warning"
    assert "error" not in (out.className or "")
    rendered = str(out)
    assert "12" in rendered and "2" in rendered
    for msg in messages:
        assert msg in rendered


def test_futures_confirm_hard_failure_shows_plain_message_no_traceback(tmp_path, monkeypatch):
    """A hard failure (e.g. missing sheet/columns) must never surface a raw exception
    string or traceback -- only a plain-English message."""
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fn = _futures_confirm_callback(app)

    def _boom(path, conn):
        raise KeyError("'All FX trades' sheet not found -- some very internal detail")

    monkeypatch.setattr("data.ingest.xlsx_futures.load_futures_fills", _boom)
    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    out = fn(1, contents, "workbook.xlsx")

    assert isinstance(out, dash.html.Span)
    rendered = str(out)
    assert "Traceback" not in rendered
    assert "'All FX trades' sheet not found" not in rendered
    assert "Import failed" in rendered


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


def test_bnp_confirm_shows_loader_summary_on_page(tmp_path, monkeypatch):
    db_path = tmp_path / "risk.db"
    app = uiapp.create_app(db_path=db_path, start_feed=False)
    fn = _report_confirm_callback(app)

    summary = ("Imported 2026-08-17: 3 new trades, 2 new positions, 1 new BNP marks. "
               "0 identical records already present. Excluded: 0 malformed IRS rows, "
               "0 other unsupported rows, 0 rows from other funds. Futures are positions only.")
    monkeypatch.setattr("ui.uploads.import_report", lambda *a, **k: summary)
    monkeypatch.setattr("ui.uploads.decode", lambda contents: b"irrelevant")
    monkeypatch.setattr("ui.app.load_summary", lambda db_path: {"as_of_date": "2026-08-17", "trades": 3, "positions": 2})

    contents = "data:application/octet-stream;base64," + __import__("base64").b64encode(b"x").decode()
    result, source_line, history, as_of, stage_style = fn(1, contents, "HA_PNL_20260818.csv", "2026-08-17", None)

    # The loader's summary must actually be visible in the rendered result -- not "",
    # not only passed to a logger.
    assert isinstance(result, dash.html.Div)
    assert result.className == "source-result--info"
    assert summary in str(result)
    assert as_of == "2026-08-17"


def test_launch_configures_root_logging_handler():
    """The app's single entry point must attach a logging handler so log.info/log.warning
    calls anywhere in the app (data/ingest/bnp.py, ui/uploads.py) actually go somewhere,
    instead of being dropped by the default unconfigured root logger."""
    import inspect
    from ui import launch

    assert "logging.basicConfig" in inspect.getsource(launch.main)
