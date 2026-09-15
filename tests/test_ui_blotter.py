"""Tests for ui/tabs/header.py and ui/tabs/blotter.py (agent C2). Owned by ui-shell.

Builds a tiny synthetic DB via data.ingest.schema so pure helpers (apply_filters,
detail_table, group_summary_table, transpose-free header formatting) and the full
Dash callback wiring can both be exercised without a real BNP/xlsx upload.
"""
from __future__ import annotations

import sqlite3

import dash
import pandas as pd
import pytest

from data.ingest import schema
from ui.tabs import blotter, header


def _make_db():
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute(
        "INSERT INTO trades VALUES ('T1','XLSX','EURUSD','FX_FWD','T1','2026-06-01',1000000,1.10,"
        "'ACC','CPTY','HAHY7','TR','buy eur','FX')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',1,'FX_NEAR','EUR',1000000,'2026-06-01','2026-06-20',1.10,1)"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',2,'FX_NEAR','USD',-1100000,'2026-06-01','2026-06-20',1.10,1)"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','EURUSD','2026-06-20','FWD_OUTRIGHT',1.1080,'BBG_BFXFORWARD','2026-06-20T15:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','EURUSD','2026-06-20','SPOT',1.1050,'BBG_BFXFORWARD','2026-06-20T15:00:00-04:00')"
    )
    conn.commit()
    return conn


# --------------------------------------------------------------------------- blotter

def test_apply_filters_status():
    df = pd.DataFrame({"status": ["OPEN", "SETTLED"], "trade_date": ["2026-06-01", "2026-06-02"],
                        "product": ["FX_FWD", "FX_FWD"], "instrument_id": ["EURUSD", "EURUSD"],
                        "strategy": ["HAHY7", "HAHY7"], "theme": ["", ""]})
    out = blotter.apply_filters(df, status="OPEN")
    assert list(out["status"]) == ["OPEN"]


def test_apply_filters_date_range():
    df = pd.DataFrame({"status": ["OPEN", "OPEN"], "trade_date": ["2026-06-01", "2026-06-10"],
                        "product": ["FX_FWD", "FX_FWD"], "instrument_id": ["EURUSD", "EURUSD"],
                        "strategy": ["HAHY7", "HAHY7"], "theme": ["", ""]})
    out = blotter.apply_filters(df, date_from="2026-06-05")
    assert list(out["trade_date"]) == ["2026-06-10"]


def test_apply_filters_all_is_noop():
    df = pd.DataFrame({"status": ["OPEN"], "trade_date": ["2026-06-01"], "product": ["FX_FWD"],
                        "instrument_id": ["EURUSD"], "strategy": ["HAHY7"], "theme": [""]})
    out = blotter.apply_filters(df, status=blotter._ALL, product=None)
    assert len(out) == 1


def test_filter_options_empty_frame():
    opts = blotter._filter_options(pd.DataFrame(), "status")
    assert opts == [{"label": "All", "value": "All"}]


def test_detail_table_formats_usd_and_rates():
    df = pd.DataFrame({
        "trade_id": ["T1"], "instrument_id": ["EURUSD"], "product": ["FX_FWD"],
        "strategy": ["HAHY7"], "theme": [""], "trade_date": ["2026-06-01"],
        "settle_date": ["2026-06-20"], "status": ["OPEN"], "quantity": [1000000.0],
        "fill": [1.1], "mark": [1.108], "spot": [1.105], "pnl_local": [8000.0],
        "pnl_usd": [8843.4], "pnl_spot_usd": [5525.0], "pnl_carry_usd": [3318.4], "reason": [""],
    })
    table = blotter.detail_table(df)
    row = table.data[0]
    assert row["pnl_usd"] == "8,843"
    assert row["fill"] == "1.100000"


def test_group_summary_table_unavailable_shows_reason():
    grouped = {
        "EURUSD": {
            "daily": {"value": 100.0, "available": True, "reason": ""},
            "d5": {"value": float("nan"), "available": False, "reason": "missing mark for T1"},
            "mtd": {"value": 100.0, "available": True, "reason": ""},
            "ytd": {"value": 100.0, "available": True, "reason": ""},
        }
    }
    table = blotter.group_summary_table(grouped, "instrument_id")
    row = table.data[0]
    assert row["5d"] == "Unavailable (missing mark for T1)"
    assert row["Daily"] == "100"


def test_package_ids_returns_empty_when_no_swaps():
    conn = _make_db()
    try:
        assert blotter._package_ids(conn) == {}
    finally:
        conn.close()


def test_row_expand_panel_lists_legs():
    conn = _make_db()
    try:
        from engine.pnl.valuation import value_book
        df = value_book(conn, "2026-06-20")
        row = df.iloc[0]
        panel = blotter.row_expand_panel(conn, "T1", row)
        assert "Trade T1" in panel.children[0].children
    finally:
        conn.close()


def test_build_layout_smoke():
    layout = blotter.build_layout(default_date="2026-06-20")
    assert layout.className == "blotter"


def test_register_callbacks_and_render_via_app():
    app = dash.Dash(__name__)
    app.layout = blotter.build_layout(default_date="2026-06-20")
    conn = _make_db()
    conn.close()

    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    disk_conn = sqlite3.connect(path)
    schema.create_schema(disk_conn)
    disk_conn.executescript("""
        INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31');
        INSERT INTO trades VALUES ('T1','XLSX','EURUSD','FX_FWD','T1','2026-06-01',1000000,1.10,
            'ACC','CPTY','HAHY7','TR','buy eur','FX');
        INSERT INTO trade_legs VALUES ('T1',1,'FX_NEAR','EUR',1000000,'2026-06-01','2026-06-20',1.10,1);
        INSERT INTO trade_legs VALUES ('T1',2,'FX_NEAR','USD',-1100000,'2026-06-01','2026-06-20',1.10,1);
        INSERT INTO marks VALUES ('2026-06-20','EURUSD','2026-06-20','FWD_OUTRIGHT',1.1080,'BBG_BFXFORWARD','2026-06-20T15:00:00-04:00');
        INSERT INTO marks VALUES ('2026-06-20','EURUSD','2026-06-20','SPOT',1.1050,'BBG_BFXFORWARD','2026-06-20T15:00:00-04:00');
    """)
    disk_conn.commit()
    disk_conn.close()

    blotter.register_callbacks(app, get_db_path=lambda: path)
    callback_map = app.callback_map
    assert any("blotter-table-container" in k for k in callback_map)
    os.remove(path)


def test_message_box():
    box = blotter.message_box("hello")
    assert box.children == "hello"


# --------------------------------------------------------------------------- header

def test_fmt_usd_negative():
    assert header._fmt_usd(-1234.6) == "-$1,235"


def test_fmt_usd_nan():
    assert header._fmt_usd(float("nan")) == "Unavailable"


def test_layout_smoke():
    layout = header.layout()
    assert layout.id == header.HEADER_ID


def test_build_figures_includes_all_periods():
    conn = _make_db()
    try:
        cards = header._build_figures(conn, "2026-06-20")
        titles = [c.children[0].children for c in cards]
        assert "LTD" in titles
        assert "Daily" in titles
        assert "Trading" in titles
    finally:
        conn.close()


def test_build_chart_returns_graph():
    conn = _make_db()
    try:
        graph = header._build_chart(conn, "2026-06-20")
        assert graph.id == "header-ltd-graph"
        assert len(graph.figure["data"][0]["x"]) == header._CHART_LOOKBACK_DAYS
    finally:
        conn.close()


def test_register_callbacks_smoke():
    app = dash.Dash(__name__)
    app.layout = header.layout()
    header.register_callbacks(app, get_db_path=lambda: ":memory:")
    assert any(header.CHART_CONTAINER_ID in k for k in app.callback_map)
