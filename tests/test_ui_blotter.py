"""Tests for ui/tabs/header.py and ui/tabs/blotter.py (agent C2, then ui-shell 2026-09-15
rebuild into sub-tabs). Owned by ui-shell.

Builds a tiny synthetic DB via data.ingest.schema so pure helpers and the full Dash
callback wiring can both be exercised without a real BNP/xlsx upload.
"""
from __future__ import annotations

import sqlite3

import dash
import pandas as pd
import pytest

from data.ingest import schema, themes
from ui.tabs import blotter, blotter_bundles, blotter_pricing, header


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


def _make_db_bnp_only():
    """Same trade, but the only mark on file is BNP_BVAL -- the shape of the real DB on
    the analyst's PC per the 2026-09-15 decision (no Bloomberg marks pulled yet)."""
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
        "INSERT INTO marks VALUES ('2026-06-20','EURUSD','2026-06-20','FWD_OUTRIGHT',1.1080,'BNP_BVAL','2026-06-20T15:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','EURUSD','2026-06-20','SPOT',1.1050,'BNP_BVAL','2026-06-20T15:00:00-04:00')"
    )
    conn.commit()
    return conn


def _make_db_no_marks():
    """No marks at all: rows must still render (trade, fill, dates, status)."""
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
    conn.commit()
    return conn


# --------------------------------------------------------------------------- pure filters / formatting

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


def _sample_df(reason="", note=""):
    return pd.DataFrame({
        "trade_id": ["T1"], "instrument_id": ["EURUSD"], "product": ["FX_FWD"],
        "strategy": ["HAHY7"], "theme": [""], "trade_date": ["2026-06-01"],
        "settle_date": ["2026-06-20"], "status": ["OPEN"], "quantity": [1000000.0],
        "fill": [1.1], "mark": [1.108], "mark_source": ["BBG_BFXFORWARD"], "spot": [1.105],
        "pnl_local": [8000.0], "pnl_usd": [8843.4], "pnl_spot_usd": [5525.0],
        "pnl_carry_usd": [3318.4], "reason": [reason], "note": [note],
    })


def test_detail_table_formats_usd_and_rates():
    df = _sample_df()
    table = blotter.detail_table(df)
    row = table.data[0]
    assert row["pnl_usd"] == "8,843"
    assert row["fill"] == "1.100000"


def test_detail_table_native_filter_and_sort_enabled():
    table = blotter.detail_table(_sample_df())
    assert table.filter_action == "native"
    assert table.sort_action == "native"
    assert table.sort_mode == "multi"


def test_detail_table_has_status_and_instrument_columns():
    table = blotter.detail_table(_sample_df())
    ids = {c["id"] for c in table.columns}
    assert "status" in ids
    assert "instrument_id" in ids


def test_detail_table_note_column_present_and_styled_grey():
    table = blotter.detail_table(_sample_df(note="fyi"))
    assert any(c["id"] == "note" for c in table.columns)
    note_style = next(s for s in table.style_data_conditional
                       if s.get("if", {}).get("column_id") == "note")
    assert note_style["color"] == "gray"


def test_detail_table_unavailable_only_on_nonempty_reason():
    table = blotter.detail_table(_sample_df(reason="no mark"))
    style = next(s for s in table.style_data_conditional
                 if "reason" in s.get("if", {}).get("filter_query", ""))
    assert "reason" in style["if"]["filter_query"]
    table_ok = blotter.detail_table(_sample_df(reason=""))
    assert table_ok.data[0]["reason"] == ""


def test_detail_table_empty_frame_still_renders_columns():
    empty = pd.DataFrame(columns=blotter._DISPLAY_COLUMNS)
    table = blotter.detail_table(empty, table_id="blotter-datatable-rates")
    assert table.data == []
    assert len(table.columns) == len(blotter._DISPLAY_COLUMNS)


def test_subtotal_line_sums_visible_rows():
    rows = [{"pnl_usd": "8,843"}, {"pnl_usd": "(1,000)"}]
    line = blotter.subtotal_line(rows)
    assert "7,843" in line.children


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


def test_message_box():
    box = blotter.message_box("hello")
    assert box.children == "hello"


# --------------------------------------------------------------------------- sub-tabs

def test_subtab_presence_and_order():
    layout = blotter.build_layout(default_date="2026-06-20")
    tabs = next(c for c in layout.children if getattr(c, "id", None) == blotter.SUBTABS_ID)
    labels = [t.label for t in tabs.children]
    assert labels == ["Total book", "FX", "Rates", "Options", "Bundles"]
    assert tabs.className == "subtabs"
    assert all(t.className == "subtab" for t in tabs.children)
    assert all(t.selected_className == "subtab--selected" for t in tabs.children)


def test_scope_products_covers_task_products():
    assert blotter.SCOPE_PRODUCTS["total"] is None
    assert set(blotter.SCOPE_PRODUCTS["fx"]) == {"FX_SPOT", "FX_FWD", "FX_SWAP", "FUTURE"}
    assert blotter.SCOPE_PRODUCTS["rates"] == ("IRS",)
    assert blotter.SCOPE_PRODUCTS["options"] == ("FX_OPTION",)


def test_scope_layout_fx_filters_out_nonfx_products():
    conn = _make_db()
    try:
        conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')")
        conn.execute(
            "INSERT INTO trades VALUES ('T2','BNP','IRSOIS-USD-1','IRS','T2','2026-06-01',1000000,1.0,"
            "'ACC','CPTY','HAHY7','TR','irs','')"
        )
        conn.commit()
        layout = blotter.scope_layout("fx", conn, "2026-06-20")
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert all(r["product"] == "FX_FWD" for r in table.data)
    finally:
        conn.close()


def test_placeholder_scope_shows_reason_text():
    conn = _make_db()
    try:
        layout = blotter.scope_layout("rates", conn, "2026-06-20")
        strip_div = layout.children[0]
        text = strip_div.children.children.children
        assert "no IRS trades loaded; view not built yet" in text
    finally:
        conn.close()


def test_placeholder_scope_options_wording():
    conn = _make_db()
    try:
        layout = blotter.scope_layout("options", conn, "2026-06-20")
        strip_div = layout.children[0]
        text = strip_div.children.children.children
        assert "no option trades loaded; view not built yet" in text
    finally:
        conn.close()


def test_placeholder_scope_table_has_same_columns_and_is_empty():
    conn = _make_db()
    try:
        layout = blotter.scope_layout("rates", conn, "2026-06-20")
        table = layout.children[1]
        assert table.data == []
        assert len(table.columns) == len(blotter._DISPLAY_COLUMNS)
    finally:
        conn.close()


def test_scope_layout_rows_render_with_no_marks_at_all():
    conn = _make_db_no_marks()
    try:
        layout = blotter.scope_layout("total", conn, "2026-06-20")
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert len(table.data) == 1
        row = table.data[0]
        assert row["trade_id"] == "T1"
        assert row["status"] in ("OPEN", "SETTLED")
        assert row["fill"] == "1.100000"
        assert row["pnl_usd"] == ""  # unpriced, blank not zero
    finally:
        conn.close()


# --------------------------------------------------------------------------- fallback pricing

def test_priced_value_book_no_fallback_needed():
    conn = _make_db()
    try:
        df, n_fallback, n_total = blotter_pricing.priced_value_book(conn, "2026-06-20")
        assert n_fallback == 0
        assert n_total == 1
        assert not df["priced_from_bnp"].any()
    finally:
        conn.close()


def test_priced_value_book_falls_back_to_bnp_bval():
    conn = _make_db_bnp_only()
    try:
        df, n_fallback, n_total = blotter_pricing.priced_value_book(conn, "2026-06-20")
        assert n_fallback == 1
        assert n_total == 1
        row = df.iloc[0]
        assert row["priced_from_bnp"]
        assert row["mark_source"] == blotter_pricing.FALLBACK_LABEL
        assert row["pnl_usd"] == pytest.approx(1000000 * (1.1080 - 1.10))
    finally:
        conn.close()


def test_priced_value_book_no_marks_stays_unpriced():
    conn = _make_db_no_marks()
    try:
        df, n_fallback, n_total = blotter_pricing.priced_value_book(conn, "2026-06-20")
        assert n_fallback == 0
        assert n_total == 1
        row = df.iloc[0]
        assert row["reason"] != ""
        assert row["pnl_usd"] != row["pnl_usd"]  # NaN
    finally:
        conn.close()


def test_fallback_caption_wording():
    assert blotter.fallback_caption(1, 2) == "1 of 2 rows priced from BNP file rates, not Bloomberg."
    assert blotter.fallback_caption(0, 2) is None


def test_scope_layout_shows_fallback_caption():
    conn = _make_db_bnp_only()
    try:
        layout = blotter.scope_layout("total", conn, "2026-06-20")
        strip_div = layout.children[0]
        caption_p = strip_div.children.children[1]
        assert "priced from BNP file rates, not Bloomberg" in caption_p.children
    finally:
        conn.close()


# --------------------------------------------------------------------------- row-scoped P&L strip

def test_row_scoped_period_pnl_has_previous_day_after_daily():
    assert blotter_pricing.PERIOD_ORDER.index("previous_day") == blotter_pricing.PERIOD_ORDER.index("daily") + 1


def test_row_scoped_period_pnl_ltd_matches_priced_sum():
    conn = _make_db()
    try:
        periods = blotter_pricing.row_scoped_period_pnl(conn, "2026-06-20", ["T1"])
        assert periods["ltd"]["available"]
        assert periods["ltd"]["value"] == pytest.approx(1000000 * (1.1080 - 1.10))
        assert periods["ltd"]["ref_date"] == "2026-06-20"
    finally:
        conn.close()


def test_row_scoped_period_pnl_unavailable_when_selected_row_unpriced():
    conn = _make_db_no_marks()
    try:
        periods = blotter_pricing.row_scoped_period_pnl(conn, "2026-06-20", ["T1"])
        assert not periods["ltd"]["available"]
        assert "T1" in periods["ltd"]["reason"]
    finally:
        conn.close()


def test_row_scoped_period_pnl_empty_selection_is_zero():
    conn = _make_db()
    try:
        periods = blotter_pricing.row_scoped_period_pnl(conn, "2026-06-20", [])
        assert periods["ltd"]["value"] == 0.0
        assert periods["ltd"]["available"]
    finally:
        conn.close()


def test_render_pnl_strip_shows_ref_date_underneath():
    conn = _make_db()
    try:
        periods = blotter_pricing.row_scoped_period_pnl(conn, "2026-06-20", ["T1"])
        div = blotter.render_pnl_strip(periods)
        cards = div.children[0].children
        ltd_card = cards[blotter_pricing.PERIOD_ORDER.index("ltd")]
        assert ltd_card.children[2].children == "2026-06-20"
    finally:
        conn.close()


def test_render_pnl_strip_order_includes_previous_day():
    titles = [blotter_pricing.PERIOD_TITLES[k] for k in blotter_pricing.PERIOD_ORDER]
    assert titles == ["LTD", "Daily", "Previous day", "5d", "MTD", "YTD", "Trading"]


# --------------------------------------------------------------------------- bundles

def test_create_bundle_and_list():
    conn = _make_db()
    try:
        themes.create_bundle(conn, "Core EM", "core em longs")
        bundles = themes.list_bundles(conn)
        assert len(bundles) == 1
        assert bundles[0]["name"] == "Core EM"
        assert bundles[0]["pairs"] == []
    finally:
        conn.close()


def test_add_pair_to_bundle_and_list_reflects_it():
    conn = _make_db()
    try:
        themes.create_bundle(conn, "Core EM")
        themes.add_pair_to_bundle(conn, "Core EM", "EURUSD")
        assert themes.bundle_pairs(conn, "Core EM") == ["EURUSD"]
    finally:
        conn.close()


def test_add_pair_to_unknown_bundle_raises():
    conn = _make_db()
    try:
        with pytest.raises(ValueError):
            themes.add_pair_to_bundle(conn, "Nope", "EURUSD")
    finally:
        conn.close()


def test_remove_pair_from_bundle():
    conn = _make_db()
    try:
        themes.create_bundle(conn, "Core EM")
        themes.add_pair_to_bundle(conn, "Core EM", "EURUSD")
        themes.remove_pair_from_bundle(conn, "Core EM", "EURUSD")
        assert themes.bundle_pairs(conn, "Core EM") == []
    finally:
        conn.close()


def test_bundle_list_table_shows_unassigned_line():
    table = blotter_bundles.bundle_list_table(
        [{"name": "Core EM", "description": "d", "pairs": ["EURUSD"]}], {})
    names = [r["name"] for r in table.data]
    assert "Core EM" in names
    assert blotter_bundles.UNASSIGNED_LABEL in names


def test_bundles_layout_smoke():
    conn = _make_db()
    try:
        themes.create_bundle(conn, "Core EM")
        layout = blotter.bundles_layout(conn, "2026-06-20")
        assert any(isinstance(c, dash.dash_table.DataTable) for c in layout.children)
    finally:
        conn.close()


# --------------------------------------------------------------------------- layout / wiring

def test_build_layout_smoke():
    layout = blotter.build_layout(default_date="2026-06-20")
    assert layout.className == "blotter"


def test_register_callbacks_and_render_via_app():
    app = dash.Dash(__name__)
    app.layout = blotter.build_layout(default_date="2026-06-20")

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
    assert any(blotter.CONTENT_ID in k for k in callback_map)
    assert any("blotter-strip-total" in k for k in callback_map)
    os.remove(path)


def test_build_layout_has_no_toolbar_filter_dropdowns():
    """User decision 2026-09-15: filtering/sorting lives in the DataTable header and
    the sub-tabs; the toolbar keeps only the date picker and theme-edit controls."""
    layout = blotter.build_layout(default_date="2026-06-20")
    toolbar = next(c for c in layout.children if getattr(c, "id", None) == blotter.TOOLBAR_ID)

    def _ids(node):
        found = []
        node_id = getattr(node, "id", None)
        if node_id:
            found.append(node_id)
        for child in getattr(node, "children", []) or []:
            if isinstance(child, list):
                for c in child:
                    found.extend(_ids(c))
            elif hasattr(child, "children") or hasattr(child, "id"):
                found.extend(_ids(child))
        return found

    all_ids = _ids(toolbar)
    for removed in ("blotter-filter-status", "blotter-filter-product", "blotter-filter-pair",
                    "blotter-filter-strategy", "blotter-filter-theme",
                    "blotter-filter-date-from", "blotter-filter-date-to", "blotter-group-by"):
        assert removed not in all_ids
    assert blotter.DATE_PICKER_ID in all_ids


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
