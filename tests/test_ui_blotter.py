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
from dash import html

from data.ingest import schema, themes
from ui.tabs import blotter, blotter_bundles, blotter_pricing, header, options


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
    """FX sub-tab rebuilt 2026-09-17 in the legacy sheet's column layout -- see
    `ui.tabs.blotter_fx`. It no longer renders `value_book`-shaped rows with a `product`
    column; it sources from `engine.pnl.fx_blotter.fx_blotter_rows`, which only covers
    the FX_SPOT/FX_FWD/FX_SWAP/FUTURE trades `value_book` builds, so an IRS trade must
    not appear in the table at all."""
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
        ids = [c["id"] for c in table.columns]
        assert "instrument_id" in ids and "pnl_eod" in ids
        assert all(r["instrument_id"] != "IRSOIS-USD-1" for r in table.data)
    finally:
        conn.close()


def _find_tables(component) -> list:
    """Every DataTable nested anywhere under `component`."""
    found = []
    stack = [component]
    while stack:
        node = stack.pop()
        if isinstance(node, dash.dash_table.DataTable):
            found.append(node)
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            stack.extend(children)
        elif children is not None and not isinstance(children, str):
            stack.append(children)
    return found


def _add_option(conn, trade_id="O1", premium_mark=0.0062, as_of="2026-06-20"):
    """A long 1m EURUSD call at 0.0050 (base-ccy fraction), PREMIUM mark on as_of.
    EURUSD SPOT on 2026-06-20 is already in _make_db (1.1050)."""
    conn.execute("INSERT OR IGNORE INTO instruments VALUES ('EURUSD092226C-1','FX_OPTION','EUR','USD',1,0,'','2026-09-22')")
    conn.execute(
        "INSERT INTO trades VALUES (?,'XLSX','EURUSD092226C-1','FX_OPTION',?,'2026-06-01',1000000,0.0050,"
        "'ACC','CPTY','HAHY7','TR','call','')", (trade_id, trade_id))
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL','EUR',1000000,'2026-06-01','2026-09-22',0.0050,0)",
                 (trade_id,))
    if premium_mark is not None:
        conn.execute("INSERT INTO marks VALUES (?,'EURUSD092226C-1','2026-09-22','PREMIUM',?,'QL_OPTIONS_PRICER',?)",
                     (as_of, premium_mark, f"{as_of}T17:00:00-04:00"))
    conn.commit()


def _add_irs(conn, trade_id="S1", pv=250_000.0, cashflow=0.0, as_of="2026-06-20"):
    conn.execute("INSERT OR IGNORE INTO instruments VALUES ('IRSOIS-USD-9','IRS','USD','USD',1,0,'','2027-06-20')")
    conn.execute(
        "INSERT INTO trades VALUES (?,'XLSX','IRSOIS-USD-9','IRS',?,'2026-06-01',10000000,0.04,"
        "'ACC','CPTY','HAHY7','TR','irs','')", (trade_id, trade_id))
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'FIXED','USD',-10000000,'2026-06-20','2027-06-20',0.04,0)", (trade_id,))
    conn.execute("INSERT INTO trade_legs VALUES (?,2,'FLOAT','USD',10000000,'2026-06-20','2027-06-20',0.0,0)", (trade_id,))
    for mark_type, value in (("PV_USD", pv), ("CASHFLOW_USD", cashflow)):
        if value is not None:
            conn.execute("INSERT INTO marks VALUES (?,'IRSOIS-USD-9','2027-06-20',?,?,'QL_PRICER',?)",
                         (as_of, mark_type, value, f"{as_of}T17:00:00-04:00"))
    conn.commit()


def test_options_scope_is_a_real_view_with_no_trades_message():
    """2026-09-17 Phase 8: Options is the grouped MARS-style table (`ui.tabs.options`),
    not the generic priced_value_book path any more -- with no option trades it still
    renders Portfolio Totals plus FX/Equity/Commodity asset-class rows (rows must
    always render), not a "no trades" placeholder."""
    conn = _make_db()
    try:
        layout = blotter.scope_layout("options", conn, "2026-06-20")
        assert layout.children[0].id == "blotter-strip-options"
        table = next(t for t in _find_tables(layout) if t.id == options.TABLE_ID)
        labels = [r["label"] for r in table.data]
        assert any("Portfolio Totals" in label for label in labels)
        assert any(label == "FX" for label in labels)
        assert any(label == "Equity" for label in labels)
        assert any(label == "Commodity" for label in labels)
        assert blotter.PLACEHOLDER_SCOPES == {}
    finally:
        conn.close()


def test_options_scope_prices_an_option_row():
    conn = _make_db()
    try:
        _add_option(conn)
        conn.execute(
            "INSERT INTO instrument_options VALUES ('EURUSD092226C-1',1.11,'CALL',0,'9999-12-31','VANILLA')"
        )
        conn.execute(
            "INSERT INTO marks VALUES ('2026-06-20','EURUSD092226C-1','2026-09-22','DELTA',0.55,"
            "'QL_OPTIONS_PRICER','2026-06-20T17:00:00-04:00')"
        )
        conn.commit()
        layout = blotter.scope_layout("options", conn, "2026-06-20")
        assert layout.children[0].id == "blotter-strip-options"
        table = next(t for t in _find_tables(layout) if t.id == options.TABLE_ID)
        row = next(r for r in table.data if r["instrument"] == "EURUSD092226C-1")
        # MktPx is the raw premium mark, shown as the blotter quotes it (base-notional fraction).
        assert row["mktpx"] == "0.006200"
        # MktVal = premium(0.0062) * quantity(1,000,000) * EUR->USD spot(1.1050) = 6,851
        assert row["mktval"] == "6,851"
        # Delta USD-equivalent = 0.55 * (1,000,000 * multiplier 1) * USD spot(1.0) = 550,000
        assert row["delta"] == "550,000"
        assert row["strike"] == "1.110000"
    finally:
        conn.close()


def test_options_scope_survives_stale_dev_db_schema():
    """Reproduces, at the full `blotter.scope_layout` call `_update` makes, the 2026-09-17
    bug found live against `data/raw/risk.db`: that DB's `instrument_options` table
    predates the `payoff` column (`CREATE TABLE IF NOT EXISTS` never adds a column to an
    existing table), so `ui.tabs.options`'s query raised `OperationalError: no such
    column: o.payoff`, uncaught -- an HTTP 500 that made the Options sub-tab look like
    it "did not load" with no on-page message at all. Fixed in `ui.tabs.options`
    (`_instrument_options_columns` guard); this asserts the sub-tab still renders."""
    conn = _make_db()
    try:
        _add_option(conn)
        # Rebuild instrument_options with the pre-payoff-column shape observed on the
        # analyst's dev DB via PRAGMA table_info (5 columns, no `payoff`).
        conn.execute("DROP TABLE instrument_options")
        conn.execute(
            "CREATE TABLE instrument_options (instrument_id TEXT PRIMARY KEY, strike REAL NOT NULL DEFAULT 0, "
            "option_type TEXT NOT NULL DEFAULT '', barrier_level REAL NOT NULL DEFAULT 0, "
            "avg_start_date TEXT NOT NULL DEFAULT '9999-12-31')"
        )
        conn.execute("INSERT INTO instrument_options VALUES ('EURUSD092226C-1', 1.11, 'CALL', 0, '9999-12-31')")
        conn.commit()
        layout = blotter.scope_layout("options", conn, "2026-06-20")
        assert layout.children[0].id == "blotter-strip-options"
        table = next(t for t in _find_tables(layout) if t.id == options.TABLE_ID)
        assert any(r["instrument"] == "EURUSD092226C-1" for r in table.data)
    finally:
        conn.close()


def test_error_card_shows_label_and_message():
    card = blotter._error_card("Rates", RuntimeError("boom"))
    assert "Rates could not be rendered (boom)." in card.children[0].children


def test_safe_section_passes_through_on_success():
    """`_safe_section` must not add any wrapping/nesting on success -- every other test
    in this file asserting on `scope_layout`'s exact returned structure depends on
    this being a pure pass-through."""
    sentinel = html.Div(id="sentinel")
    assert blotter._safe_section("X", lambda: sentinel) is sentinel


def test_safe_section_turns_exception_into_error_card():
    def boom():
        raise ValueError("bad data")
    result = blotter._safe_section("Rates", boom)
    assert "Rates could not be rendered (bad data)." in result.children[0].children


def test_rates_strip_failure_still_renders_rates_table():
    """2026-09-17 coordinator instruction: a failure in one sub-section of a scope's
    layout (here, the P&L strip feeding off `row_scoped_headline`) must not blank a
    sibling section (the delegated Rates table) that doesn't depend on it."""
    conn = _make_db()
    try:
        _add_irs(conn, pv=250_000.0, cashflow=1_000.0)

        def boom(*args, **kwargs):
            raise RuntimeError("strip boom")

        # blotter.py does `from ui.tabs.blotter_pricing import row_scoped_headline`, so
        # the name to patch is the one bound into blotter's own namespace, not the
        # attribute on the blotter_pricing module (patching that would have no effect
        # here -- blotter.py already holds its own reference to the original function).
        original = blotter.row_scoped_headline
        blotter.row_scoped_headline = boom
        try:
            layout = blotter.scope_layout("rates", conn, "2026-06-20")
        finally:
            blotter.row_scoped_headline = original

        strip_section = layout.children[0]
        assert "P&L strip could not be rendered (strip boom)." in strip_section.children[0].children
        table = next(t for t in _find_tables(layout) if t.id == "rates-datatable")
        assert table.data  # Rates table still rendered despite the strip failure
    finally:
        conn.close()


def test_options_delegated_build_failure_still_renders_strip():
    """The reverse pairing of the previous test: a failure in the delegated Options
    table build must not blank the P&L strip above it."""
    conn = _make_db()
    try:
        _add_option(conn)
        conn.execute(
            "INSERT INTO instrument_options VALUES ('EURUSD092226C-1',1.11,'CALL',0,'9999-12-31','VANILLA')"
        )
        conn.commit()

        def boom(*args, **kwargs):
            raise RuntimeError("options boom")

        original = options.build_layout
        options.build_layout = boom
        try:
            layout = blotter.scope_layout("options", conn, "2026-06-20")
        finally:
            options.build_layout = original

        assert layout.children[0].id == "blotter-strip-options"  # strip unaffected
        options_section = layout.children[1]
        assert "Options could not be rendered (options boom)." in options_section.children[0].children
    finally:
        conn.close()


def test_asset_class_table_failure_still_renders_strip_and_trade_table():
    """Total book: a failure in the asset-class-by-P&L rollup must not blank the strip
    above it or the trade table below it."""
    conn = _make_db()
    try:
        def boom(*args, **kwargs):
            raise RuntimeError("asset class boom")

        original = blotter.asset_class_pnl_table
        blotter.asset_class_pnl_table = boom
        try:
            layout = blotter.scope_layout("total", conn, "2026-06-20")
        finally:
            blotter.asset_class_pnl_table = original

        assert layout.children[0].id == "blotter-strip-total"
        asset_class_section = layout.children[1]
        assert "P&L by asset class could not be rendered (asset class boom)." in asset_class_section.children[0].children
        table = next(t for t in _find_tables(layout) if t.id == "blotter-datatable-total")
        assert table.data  # trade table still rendered
    finally:
        conn.close()


def test_pricing_df_failure_shows_single_error_card_not_a_crash():
    """`scope_df` itself failing (e.g. a broken query) is the one case with nothing
    partial to preserve -- everything downstream needs `df` -- but it must still
    degrade to one inline card rather than raising past `scope_layout`."""
    conn = _make_db()
    try:
        def boom(*args, **kwargs):
            raise RuntimeError("db boom")

        original = blotter.scope_df
        blotter.scope_df = boom
        try:
            layout = blotter.scope_layout("total", conn, "2026-06-20")
        finally:
            blotter.scope_df = original

        assert "Pricing could not be rendered (db boom)." in layout.children[0].children[0].children
    finally:
        conn.close()


def test_bundles_scope_failure_via_update_does_not_crash_whole_callback(tmp_path, monkeypatch):
    """The "Bundles" section is dispatched from `_update`, not `scope_layout` -- confirm
    it gets the same `_safe_section` treatment so a bundles-specific bug degrades to an
    inline card (via `_update`'s own return, since `bundles_layout` has no sibling
    section to preserve) instead of the whole callback raising."""
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.close()

    app = dash.Dash(__name__)
    blotter.register_callbacks(app, get_db_path=lambda: str(db_path))
    update_key = next(k for k in app.callback_map if k.startswith(blotter.CONTENT_ID))
    update_fn = app.callback_map[update_key]["callback"]
    update_fn = getattr(update_fn, "__wrapped__", update_fn)

    def boom(conn, as_of):
        raise RuntimeError("bundles boom")

    monkeypatch.setattr(blotter, "bundles_layout", boom)
    result = update_fn("2026-06-20", "bundles")
    assert "Bundles could not be rendered (bundles boom)." in result.children[0].children


def test_total_book_asset_class_rows_sum_to_total():
    """2026-09-17 user request: the Total book shows P&L by asset class (FX / Futures /
    Rates / Options) plus a Total that equals the strip."""
    conn = _make_db()
    try:
        _add_option(conn)
        _add_irs(conn, pv=250_000.0, cashflow=1_000.0)
        df = blotter.scope_df(conn, "total", "2026-06-20")
        rows = {r["asset_class"]: r for r in blotter.asset_class_pnl_rows(conn, "2026-06-20", df)}
        assert list(rows) == ["FX", "Rates", "Options", "Total"]
        assert rows["FX"]["ltd"]["value"] == pytest.approx(8_000.0)           # 1m EUR * (1.108 - 1.10)
        assert rows["Rates"]["ltd"]["value"] == pytest.approx(251_000.0)      # PV + settled cashflows
        assert rows["Options"]["ltd"]["value"] == pytest.approx(1_326.0)
        assert rows["Total"]["ltd"]["value"] == pytest.approx(8_000.0 + 251_000.0 + 1_326.0)
        assert rows["Total"]["trades"] == 3
        headline = blotter.row_scoped_headline(conn, "2026-06-20", df["trade_id"].tolist())
        assert headline["ltd"]["value"] == pytest.approx(rows["Total"]["ltd"]["value"])
        layout = blotter.scope_layout("total", conn, "2026-06-20")
        table = next(t for t in _find_tables(layout) if t.id == blotter.ASSET_TABLE_ID)
        assert [r["asset_class"] for r in table.data] == ["FX", "Rates", "Options", "Total"]
        assert table.data[-1]["ltd"] == "260,326"
    finally:
        conn.close()


def test_total_book_asset_class_missing_mark_is_unavailable_with_reason():
    """2026-09-17 partial-pricing follow-up: a class or the Total row with a MIX of
    priced and unpriced trades now shows its real (priced-only) sum with an
    `excluded_summary` caption, not "n/a" -- only a class where EVERYTHING is unpriced
    (here, Rates, whose one IRS trade has no CASHFLOW_USD mark) stays fully
    unavailable, matching `ui/tabs/header.py`'s same-day equivalent fix."""
    conn = _make_db()
    try:
        _add_irs(conn, pv=250_000.0, cashflow=None)   # PV but no CASHFLOW_USD mark
        df = blotter.scope_df(conn, "total", "2026-06-20")
        rows = {r["asset_class"]: r for r in blotter.asset_class_pnl_rows(conn, "2026-06-20", df)}
        assert rows["FX"]["ltd"]["available"] is True
        assert rows["Rates"]["ltd"]["available"] is False and "S1" in rows["Rates"]["ltd"]["reason"]
        assert rows["Total"]["ltd"]["available"] is True
        assert rows["Total"]["ltd"]["value"] == pytest.approx(8_000.0)  # FX only; IRS excluded
        assert rows["Total"]["ltd"]["excluded_summary"] == "excludes 1 of 2 trades unpriced"
        assert "S1" in rows["Total"]["ltd"]["excluded_detail"] or "swap" in rows["Total"]["ltd"]["excluded_detail"]
        layout = blotter.scope_layout("total", conn, "2026-06-20")
        table = next(t for t in _find_tables(layout) if t.id == blotter.ASSET_TABLE_ID)
        assert table.data[1]["ltd"] == "n/a" and "S1" in table.tooltip_data[1]["ltd"]["value"]
        assert table.data[-1]["ltd"] != "n/a"  # Total row: a real value, not blanked
        assert "excludes 1 of 2" in table.tooltip_data[-1]["ltd"]["value"]
    finally:
        conn.close()


def test_rates_scope_has_a_strip_scoped_to_irs_trades():
    conn = _make_db()
    try:
        _add_irs(conn, pv=250_000.0, cashflow=1_000.0)
        layout = blotter.scope_layout("rates", conn, "2026-06-20")
        assert layout.children[0].id == "blotter-strip-rates"
        strip_text = str(layout.children[0].children.to_plotly_json())
        assert "251,000" in strip_text and "8,000" not in strip_text   # IRS only, not the FX trade
    finally:
        conn.close()


def test_scope_layout_rates_delegates_to_rates_module():
    """The "rates" scope is a real view (ui.tabs.rates), not the placeholder path --
    verifies the wiring in blotter.scope_layout, not ui.tabs.rates' own logic (that's
    tests/test_ui_rates.py)."""
    conn = _make_db()
    try:
        conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')")
        # 'XLSX' (2026-09-16, trades_official double-count fix): ui/tabs/rates.py now
        # reads trades_official, which excludes source='BNP' by design.
        conn.execute(
            "INSERT INTO trades VALUES ('T2','XLSX','IRSOIS-USD-1','IRS','T2','2026-06-01',1000000,0.04,"
            "'ACC','CPTY','HAHY7','TR','irs','')"
        )
        conn.commit()
        layout = blotter.scope_layout("rates", conn, "2026-06-20")
        table = next(t for t in _find_tables(layout) if t.id == "rates-datatable")
        assert table.data[0]["trade_id"] == "T2"
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
        assert "priced_from_bnp" not in df.columns
    finally:
        conn.close()


def test_priced_value_book_never_retries_against_bnp_bval():
    """2026-09-17 user decision "no bnp fall back": a DB with only a `BNP_BVAL` mark on
    file (the shape `_make_db_bnp_only` builds) must NOT be priced from it -- CLAUDE.md
    says BNP_BVAL is reconciliation-only, never official, and this module no longer
    retries a missing official mark against it at all. The row stays unpriced (`reason`
    non-empty, `pnl_usd` NaN) exactly like a DB with no marks whatsoever."""
    conn = _make_db_bnp_only()
    try:
        df, n_fallback, n_total = blotter_pricing.priced_value_book(conn, "2026-06-20")
        assert n_fallback == 0
        assert n_total == 1
        row = df.iloc[0]
        assert row["reason"] != ""
        assert row["pnl_usd"] != row["pnl_usd"]  # NaN
        assert row["mark_source"] != "BNP_BVAL"  # never adopted as if it were official
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


# ------------------------------------------------------ partial pricing (2026-09-17)
# Live-Bloomberg-PC follow-up: a strip with even ONE unpriced visible row (one forward
# settling today, one future, five options, in the reported case) used to poison the
# WHOLE figure to NaN. Matches ui/tabs/header.py's same-day fix to its own headline
# cards -- see ui.tabs.blotter_pricing's module docstring ("row-scoped strip" section).

def _frame(rows):
    """A minimal `priced_value_book`-shaped frame: rows are
    (trade_id, product, reason, pnl_usd) tuples."""
    return pd.DataFrame({
        "trade_id": [r[0] for r in rows], "product": [r[1] for r in rows],
        "reason": [r[2] for r in rows], "pnl_usd": [r[3] for r in rows],
        "trade_date": ["2026-06-01"] * len(rows),
    })


def test_priced_single_scoped_fully_priced_has_no_excluded_summary(monkeypatch):
    df = _frame([("T1", "FX_FWD", "", 100.0), ("T2", "FUTURE", "", 50.0)])
    monkeypatch.setattr(blotter_pricing, "priced_value_book", lambda conn, date: (df, 0, len(df)))
    entry = blotter_pricing._priced_single_scoped(None, "2026-06-20", ["T1", "T2"], "2026-06-20")
    assert entry["available"] and entry["value"] == pytest.approx(150.0)
    assert entry["excluded_summary"] == "" and entry["excluded_detail"] == ""


def test_priced_single_scoped_mixed_sums_priced_rows_with_caption(monkeypatch):
    """The exact bug reported: "one forward settling today, one future, five options"
    unpriced among an otherwise-priced book used to poison the whole strip to NaN --
    now it sums the priced rows and shows a visible caption instead."""
    df = _frame([
        ("T1", "FX_FWD", "", 100.0), ("T2", "FUTURE", "", 50.0),
        ("T3", "FX_FWD", "no FWD_OUTRIGHT mark for T3", float("nan")),
        ("O1", "FX_OPTION", "no PREMIUM mark for O1", float("nan")),
    ])
    monkeypatch.setattr(blotter_pricing, "priced_value_book", lambda conn, date: (df, 0, len(df)))
    entry = blotter_pricing._priced_single_scoped(None, "2026-06-20", ["T1", "T2", "T3", "O1"], "2026-06-20")
    assert entry["available"] is True
    assert entry["value"] == pytest.approx(150.0)
    assert entry["reason"] == ""
    assert entry["excluded_summary"] == "excludes 2 of 4 trades unpriced"
    assert "forward" in entry["excluded_detail"] and "option" in entry["excluded_detail"]


def test_priced_single_scoped_all_unpriced_is_unavailable(monkeypatch):
    df = _frame([("T1", "FX_FWD", "no FWD_OUTRIGHT mark for T1", float("nan"))])
    monkeypatch.setattr(blotter_pricing, "priced_value_book", lambda conn, date: (df, 0, len(df)))
    entry = blotter_pricing._priced_single_scoped(None, "2026-06-20", ["T1"], "2026-06-20")
    assert entry["available"] is False
    assert entry["value"] != entry["value"]  # NaN
    assert "T1" in entry["reason"]
    assert entry["excluded_summary"] == ""


def test_priced_single_scoped_empty_scope_is_zero_available(monkeypatch):
    df = _frame([])
    monkeypatch.setattr(blotter_pricing, "priced_value_book", lambda conn, date: (df, 0, 0))
    entry = blotter_pricing._priced_single_scoped(None, "2026-06-20", [], "2026-06-20")
    assert entry == {"value": 0.0, "ref_date": "2026-06-20", "available": True, "reason": "",
                      "excluded_summary": "", "excluded_detail": ""}


def test_priced_diff_scoped_excludes_trade_priced_now_unpriced_before(monkeypatch):
    """`ui/tabs/header.py::_priced_diff`'s rule, replicated: a trade priced today but
    unpriced on the reference date is excluded from the diff outright (never credited
    with a fake one-sided jump the size of its whole LTD)."""
    df_a = _frame([("T1", "FX_FWD", "", 120.0), ("T2", "FX_FWD", "", 40.0)])
    df_b = _frame([("T1", "FX_FWD", "", 100.0), ("T2", "FX_FWD", "no mark for T2 on b", float("nan"))])
    frames = {"2026-06-20": df_a, "2026-06-19": df_b}
    monkeypatch.setattr(blotter_pricing, "priced_value_book",
                         lambda conn, date: (frames[date], 0, len(frames[date])))
    entry = blotter_pricing._priced_diff_scoped(None, "2026-06-20", "2026-06-19", ["T1", "T2"],
                                                 "2026-06-19", "2026-06-19")
    assert entry["available"] is True
    assert entry["value"] == pytest.approx(20.0)  # T1 only: 120 - 100; T2 excluded both ways
    assert entry["excluded_summary"] == "excludes 1 of 2 trades unpriced"
    assert "priced now but unpriced on 2026-06-19" in entry["excluded_detail"]


def test_priced_diff_scoped_new_trade_since_reference_contributes_fully(monkeypatch):
    """A trade with no row at all on the reference date (traded after it) is NOT
    "excluded" -- its full current value flows through normally, same as ordinary
    trading P&L."""
    df_a = _frame([("T1", "FX_FWD", "", 120.0), ("T2", "FX_FWD", "", 40.0)])
    df_b = _frame([("T1", "FX_FWD", "", 100.0)])  # T2 did not exist yet on the reference date
    frames = {"2026-06-20": df_a, "2026-06-19": df_b}
    monkeypatch.setattr(blotter_pricing, "priced_value_book",
                         lambda conn, date: (frames[date], 0, len(frames[date])))
    entry = blotter_pricing._priced_diff_scoped(None, "2026-06-20", "2026-06-19", ["T1", "T2"],
                                                 "2026-06-19", "2026-06-19")
    assert entry["available"] is True
    assert entry["value"] == pytest.approx(60.0)  # (120-100) + 40 (T2 new, full value)
    assert entry["excluded_summary"] == ""


def test_priced_diff_scoped_all_unpriced_is_unavailable(monkeypatch):
    df_a = _frame([("T1", "FX_FWD", "no mark for T1", float("nan"))])
    df_b = _frame([("T1", "FX_FWD", "", 100.0)])
    frames = {"2026-06-20": df_a, "2026-06-19": df_b}
    monkeypatch.setattr(blotter_pricing, "priced_value_book",
                         lambda conn, date: (frames[date], 0, len(frames[date])))
    entry = blotter_pricing._priced_diff_scoped(None, "2026-06-20", "2026-06-19", ["T1"],
                                                 "2026-06-19", "2026-06-19")
    assert entry["available"] is False
    assert entry["value"] != entry["value"]
    assert "T1" in entry["reason"]


def test_row_scoped_headline_partial_pricing_shows_excluded_summary():
    """End-to-end through the real DB/engine (not a monkeypatched frame): an FX trade
    (priced) plus an IRS trade with no CASHFLOW_USD mark (unpriced) in the same
    row-scoped set -- LTD sums the FX trade only and carries the caption, matching
    `asset_class_pnl_rows`'s own coverage of this same fixture for the Total row."""
    conn = _make_db()
    try:
        _add_irs(conn, pv=250_000.0, cashflow=None)
        df = blotter.scope_df(conn, "total", "2026-06-20")
        headline = blotter_pricing.row_scoped_headline(conn, "2026-06-20", df["trade_id"].tolist())
        assert headline["ltd"]["available"] is True
        assert headline["ltd"]["value"] == pytest.approx(8_000.0)
        assert headline["ltd"]["excluded_summary"] == "excludes 1 of 2 trades unpriced"
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


def test_render_headline_strip_shows_excluded_summary_caption():
    """2026-09-17 partial-pricing follow-up: an AVAILABLE card with `excluded_summary`
    shows it as a visible caption line, with `excluded_detail` as the caption's
    tooltip -- the value itself stays a real number (not "n/a")."""
    headline = {"ltd": {"value": 150.0, "ref_date": "2026-06-20", "available": True, "reason": "",
                          "excluded_summary": "excludes 2 of 4 trades unpriced",
                          "excluded_detail": "1 forward: no FWD_OUTRIGHT; 1 option: no PREMIUM"}}
    for key in blotter_pricing.HEADLINE_ORDER:
        headline.setdefault(key, {"value": 0.0, "ref_date": "2026-06-20", "available": True, "reason": "",
                                   "excluded_summary": "", "excluded_detail": ""})
    div = blotter.render_headline_strip(headline)
    ltd_card = div.children[0].children[blotter_pricing.HEADLINE_ORDER.index("ltd")]
    value_div = ltd_card.children[1]
    assert value_div.children == "150"
    caption = ltd_card.children[-1]
    assert caption.children == "excludes 2 of 4 trades unpriced"
    assert caption.title == "1 forward: no FWD_OUTRIGHT; 1 option: no PREMIUM"


def test_render_headline_strip_no_caption_when_fully_priced():
    headline = {key: {"value": 0.0, "ref_date": "2026-06-20", "available": True, "reason": "",
                       "excluded_summary": "", "excluded_detail": ""}
                for key in blotter_pricing.HEADLINE_ORDER}
    div = blotter.render_headline_strip(headline)
    ltd_card = div.children[0].children[blotter_pricing.HEADLINE_ORDER.index("ltd")]
    assert len(ltd_card.children) == 3  # label, value, ref_date -- no caption row


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


def test_update_content_degrades_to_message_on_unexpected_exception(tmp_path, monkeypatch):
    """2026-09-17: only `ImportError` was ever caught around `scope_layout`/
    `bundles_layout` in `_update` -- any other exception (e.g. the stale-dev-DB
    `OperationalError` `ui.tabs.options` hit live) propagated past Dash's callback
    wrapper as an uncaught HTTP 500, so `Output(blotter-content, "children")` never
    fired and the tab just stayed on its previous/blank content: the user-visible
    shape of "the blotter sub tabs do not load". Every exception now degrades to a
    message_box for that one render instead of taking the whole tab down."""
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.close()

    app = dash.Dash(__name__)
    blotter.register_callbacks(app, get_db_path=lambda: str(db_path))
    update_key = next(k for k in app.callback_map if k.startswith(blotter.CONTENT_ID))
    update_fn = app.callback_map[update_key]["callback"]
    update_fn = getattr(update_fn, "__wrapped__", update_fn)

    def boom(scope, conn, as_of):
        raise RuntimeError("boom")

    monkeypatch.setattr(blotter, "scope_layout", boom)
    result = update_fn("2026-06-20", "total")
    assert "could not be rendered" in result.children


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
