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


def _sample_df(reason="", note="", pnl_usd=8843.4):
    return pd.DataFrame({
        "trade_id": ["T1"], "instrument_id": ["EURUSD"], "product": ["FX_FWD"],
        "strategy": ["HAHY7"], "theme": [""], "trade_date": ["2026-06-01"],
        "settle_date": ["2026-06-20"], "status": ["OPEN"], "quantity": [1000000.0],
        "fill": [1.1], "mark": [1.108], "mark_date": ["2026-06-20"],
        "mark_source": ["BBG_BFXFORWARD"], "spot": [1.105],
        "pnl_local": [8000.0], "pnl_usd": [pnl_usd], "pnl_spot_usd": [5525.0],
        "pnl_carry_usd": [3318.4], "reason": [reason], "note": [note],
        "side": ["Buy"], "notional_usd": [1100000.0], "t1_rate": [1.109],
    })


def test_detail_table_formats_usd_and_rates():
    df = _sample_df()
    table = blotter.detail_table(df)
    row = table.data[0]
    assert row["pnl_usd"] == "8,843"
    assert row["fill"] == "1.100000"
    assert row["quantity"] == "1,000,000"  # unsigned; direction carried by side


def test_detail_table_has_no_native_filter_or_sort():
    """Replaced 2026-09-15 by the dropdown filter bar (`_filter_bar`) and a fixed sort
    order (`_sorted_scope_df`): native filter_action/sort_action were verified inert in
    the installed Dash version (a bare reproduction outside this app never filtered or
    sorted either), so the table no longer advertises controls that do nothing."""
    table = blotter.detail_table(_sample_df())
    assert not hasattr(table, "filter_action")
    assert not hasattr(table, "sort_action")


def test_sorted_scope_df_orders_by_settle_date_then_pair():
    df = pd.DataFrame({
        "settle_date": ["2026-06-21", "2026-06-20", "2026-06-20"],
        "instrument_id": ["EURUSD", "USDJPY", "AUDUSD"],
    })
    out = blotter._sorted_scope_df(df)
    assert out["instrument_id"].tolist() == ["AUDUSD", "USDJPY", "EURUSD"]


def test_filter_bar_lists_distinct_values_and_clear_button():
    df = pd.concat([_sample_df(), _sample_df()], ignore_index=True)
    df.loc[1, "instrument_id"] = "USDJPY"
    df.loc[1, "trade_id"] = "T2"
    bar = blotter._filter_bar(df, "blotter-datatable-total", blotter._DISPLAY_COLUMNS, blotter._COLUMN_LABELS)
    dropdowns = {c.id: c for c in bar.children if getattr(c, "id", None) is None for c in []}
    # dropdowns live one level down, inside each .blotter-filter wrapper
    pair_dropdown = next(
        wrap.children[1] for wrap in bar.children
        if getattr(wrap, "className", "") == "blotter-filter"
        and wrap.children[1].id == "blotter-datatable-total-filter-instrument_id"
    )
    values = {opt["value"] for opt in pair_dropdown.options}
    assert values == {"EURUSD", "USDJPY"}
    assert pair_dropdown.multi is True
    clear_buttons = [c for c in bar.children if getattr(c, "id", "") == "blotter-datatable-total-filter-clear"]
    assert len(clear_buttons) == 1


def test_detail_table_has_status_and_instrument_columns():
    table = blotter.detail_table(_sample_df())
    ids = {c["id"] for c in table.columns}
    assert "status" in ids
    assert "instrument_id" in ids


def test_detail_table_columns_include_notional_and_t1_rate():
    table = blotter.detail_table(_sample_df())
    ids = [c["id"] for c in table.columns]
    names = [c["name"] for c in table.columns]
    assert ids.index("mark_date") < ids.index("notional_usd") < ids.index("mark") < ids.index("t1_rate")
    assert "Notional (USD)" in names
    assert "Live rate" in names
    assert "T-1 rate" in names


def test_detail_table_status_is_title_cased():
    table = blotter.detail_table(_sample_df())
    assert table.data[0]["status"] == "Open"


def test_detail_table_unpriced_pnl_shows_na_with_tooltip():
    table = blotter.detail_table(_sample_df(reason="no mark", pnl_usd=float("nan")))
    assert table.data[0]["pnl_usd"] == "n/a"
    assert table.tooltip_data[0]["pnl_usd"]["value"] == "no mark"


def test_detail_table_pnl_colour_conditional_present():
    table = blotter.detail_table(_sample_df())
    colours = {s["color"] for s in table.style_data_conditional if "color" in s}
    assert "var(--pos)" in colours
    assert "var(--neg)" in colours


def test_detail_table_empty_frame_still_renders_columns():
    empty = pd.DataFrame(columns=blotter._DISPLAY_COLUMNS)
    table = blotter.detail_table(empty, table_id="blotter-datatable-rates")
    assert table.data == []
    assert len(table.columns) == len(blotter._DISPLAY_COLUMNS)


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

def test_title_row_matches_ladder_class_names():
    """Coordinator instruction 2026-09-15: single title row, "Blotter" left, date
    heading + picker + Today button right, no card, sharing the Ladder's class names
    so one stylesheet rule styles both tabs."""
    # As-of defaults to today (ui.tabs.cash_ladder.today_ny) regardless of the
    # `default_date` argument, matching the Ladder's own "today" default -- so assert
    # against heading_date_text(today_ny()) rather than a fixed date.
    from ui.tabs.cash_ladder import heading_date_text, today_ny
    layout = blotter.build_layout(default_date="2026-06-20")
    toolbar = next(c for c in layout.children if getattr(c, "id", None) == blotter.TOOLBAR_ID)
    assert toolbar.className == "ladder-title-row"
    heading = toolbar.children[0]
    assert heading.children == "Blotter"
    assert heading.className == "ladder-title-row-heading"
    right = toolbar.children[1]
    assert right.className == "ladder-title-row-right"
    title = next(c for c in right.children if getattr(c, "id", None) == blotter.TITLE_ID)
    assert heading_date_text(today_ny()) == title.children
    today_button = next(c for c in right.children if getattr(c, "id", None) == blotter.TODAY_BUTTON_ID)
    assert today_button.children == "Today"


def test_subtab_presence_and_order():
    layout = blotter.build_layout(default_date="2026-06-20")
    tabs = next(c for c in layout.children if getattr(c, "id", None) == blotter.SUBTABS_ID)
    labels = [t.label for t in tabs.children]
    assert labels == ["Total book", "FX", "Futures", "Rates", "Options", "Bundles"]
    assert tabs.className == "subtabs"
    assert all(t.className == "subtab" for t in tabs.children)
    assert all(t.selected_className == "subtab--selected" for t in tabs.children)


def test_scope_products_covers_task_products():
    assert blotter.SCOPE_PRODUCTS["total"] is None
    assert set(blotter.SCOPE_PRODUCTS["fx"]) == {"FX_SPOT", "FX_FWD", "FX_SWAP"}
    assert blotter.SCOPE_PRODUCTS["futures"] == ("FUTURE",)
    assert blotter.SCOPE_PRODUCTS["rates"] == ("IRS",)
    assert blotter.SCOPE_PRODUCTS["options"] == ("FX_OPTION",)


def test_futures_scope_no_trades_shows_reason_and_empty_table():
    """User decision 2026-09-15 item 1: no futures trades on this PC (only a BNP netted
    position), so the strip reads n/a with the documented reason and the table (with
    futures-specific columns) is empty, never hidden."""
    conn = _make_db()
    try:
        layout = blotter.scope_layout("futures", conn, "2026-06-20")
        strip_div = layout.children[0]
        text = strip_div.children.children.children
        assert blotter.FUTURES_NO_TRADES_REASON in text
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert table.data == []
        ids = [c["id"] for c in table.columns]
        assert ids == blotter._FUTURES_DISPLAY_COLUMNS
        names = [c["name"] for c in table.columns]
        assert "Contract" in names and "Contracts" in names and "Expiry" in names
    finally:
        conn.close()


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
        assert all(r["product"] == "Forward" for r in table.data)
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
        assert row["status"] in ("Open", "Settled")
        assert row["fill"] == "1.100000"
        assert row["pnl_usd"] == "n/a"  # unpriced -> n/a with a tooltip reason, not blank/zero
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


def test_row_scoped_headline_order_matches_excel_header():
    """Regrouped 2026-09-15: period series, then activity group, then raw LTD levels."""
    titles = [blotter_pricing.HEADLINE_TITLES[k] for k in blotter_pricing.HEADLINE_ORDER]
    assert titles == ["LTD P&L", "Daily P&L", "Previous day P&L", "5d", "MTD", "YTD",
                       "Trades", "Trading P&L", "Trading P&L T-1", "LTD-1 P&L", "LTD-2 P&L"]


def test_row_scoped_headline_ltd_and_trades_count():
    conn = _make_db()
    try:
        headline = blotter_pricing.row_scoped_headline(conn, "2026-06-20", ["T1"])
        assert headline["ltd"]["available"]
        assert headline["ltd"]["value"] == pytest.approx(1000000 * (1.1080 - 1.10))
        assert headline["trades"]["value"] == 1.0
    finally:
        conn.close()


def test_render_headline_strip_shows_ref_date_and_count():
    conn = _make_db()
    try:
        headline = blotter_pricing.row_scoped_headline(conn, "2026-06-20", ["T1"])
        div = blotter.render_headline_strip(headline)
        cards = div.children[0].children
        ltd_card = cards[blotter_pricing.HEADLINE_ORDER.index("ltd")]
        assert ltd_card.children[2].children == "2026-06-20"
        trades_card = cards[blotter_pricing.HEADLINE_ORDER.index("trades")]
        assert trades_card.children[1].children == "1"
    finally:
        conn.close()


def test_render_headline_strip_unavailable_shows_na_with_tooltip():
    headline = {"ltd": {"value": float("nan"), "ref_date": "2026-06-20", "available": False,
                          "reason": "no mark for T1"}}
    for key in blotter_pricing.HEADLINE_ORDER:
        headline.setdefault(key, {"value": 0.0, "ref_date": "2026-06-20", "available": True, "reason": ""})
    div = blotter.render_headline_strip(headline)
    ltd_card = div.children[0].children[blotter_pricing.HEADLINE_ORDER.index("ltd")]
    value_div = ltd_card.children[1]
    assert value_div.children == "n/a"
    assert value_div.title == "no mark for T1"


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
        # header._build_chart charts business days only, capped at the earliest trade
        # date -- the fixture's single trade (2026-06-01) yields fewer than the full
        # lookback window, so just check the series is non-empty and within bounds.
        n_points = len(graph.figure["data"][0]["x"])
        assert 1 <= n_points <= header._CHART_LOOKBACK_DAYS
    finally:
        conn.close()


def test_register_callbacks_smoke():
    app = dash.Dash(__name__)
    app.layout = header.layout()
    header.register_callbacks(app, get_db_path=lambda: ":memory:")
    assert any(header.CHART_CONTAINER_ID in k for k in app.callback_map)
