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
    assert labels == ["Total book", "FX", "Futures", "Rates", "Options", "Bundles", "Manual entry"]
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
        # Cells are raw numbers now (the table carries its own display format), checked
        # against the running code 2026-09-18.
        # MktPx is the raw premium mark, as the blotter quotes it (base-notional fraction).
        assert row["mktpx"] == 0.0062
        # MktVal = premium(0.0062) * quantity(1,000,000) * EUR->USD spot(1.1050) = 6,851
        assert row["mktval"] == pytest.approx(6851.0)
        # Delta USD-equivalent = 0.55 * 1,000,000 EUR * the BASE currency's rate, EUR->USD
        # spot 1.1050 = 607,750. It used to be converted at the quote currency's rate (USD,
        # 1.0 -> 550,000), a bug the pricer audit found: a delta is an amount of base currency.
        assert row["delta"] == pytest.approx(607750.0)
        assert row["strike"] == 1.11
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
    # `_update` has two Outputs since 2026-09-18: the content, and the trade-set signature
    # it was built from (`blotter.BUILT_TRADE_SET_ID`).
    update_key = next(k for k in app.callback_map if f"{blotter.CONTENT_ID}.children" in k)
    update_fn = app.callback_map[update_key]["callback"]
    update_fn = getattr(update_fn, "__wrapped__", update_fn)

    def boom(conn, as_of):
        raise RuntimeError("bundles boom")

    monkeypatch.setattr(blotter, "bundles_layout", boom)
    result, _built_from = update_fn("2026-06-20", "bundles")
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
    update_key = next(k for k in app.callback_map if f"{blotter.CONTENT_ID}.children" in k)
    update_fn = app.callback_map[update_key]["callback"]
    update_fn = getattr(update_fn, "__wrapped__", update_fn)

    def boom(scope, conn, as_of, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(blotter, "scope_layout", boom)
    result, _built_from = update_fn("2026-06-20", "total")
    assert "could not be rendered (boom)" in result.children


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


# --------------------------------------------------------------------------- missing option terms
def _db_with_option_missing_strike(tmp_path):
    from data.ingest import schema
    conn = schema.connect(tmp_path / "terms.db")
    conn.execute("INSERT INTO instruments VALUES ('EURSEK112526C-1','FX_OPTION','EUR','SEK',1,0,'EURSEK112526C-1','2026-11-25')")
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type) VALUES ('EURSEK112526C-1', 0, 'CALL')")
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
                 "account, counterparty, strategy, trader, description) VALUES "
                 "('o1','XLSX','EURSEK112526C-1','FX_OPTION','o1','2026-09-01',1000000,0.01,'acct','cp','','tr','')")
    conn.execute("INSERT INTO trade_legs VALUES ('o1',1,'NOTIONAL','EUR',1000000,'2026-09-01','2026-11-25',0,0)")
    conn.commit()
    return conn


def test_missing_terms_notice_names_the_option_and_where_to_enter_it(tmp_path):
    conn = _db_with_option_missing_strike(tmp_path)
    notice = blotter.missing_terms_notice(conn)
    assert notice is not None
    text = str(notice.to_plotly_json())
    assert "EURSEK112526C-1" in text
    assert "no strike on file" in text
    assert "Option terms" in text


def test_missing_terms_notice_says_the_editable_cell_first_then_the_alternatives(tmp_path):
    """2026-09-18: the Options table's Strike / Type / Payoff cells are editable, so the
    banner sends the user there first; the Manual entry form and a re-upload follow, one
    plain sentence each."""
    from ui.tabs import options as options_ui
    assert "strike" in options_ui.EDITABLE_COLUMNS and "payoff" in options_ui.EDITABLE_COLUMNS
    conn = _db_with_option_missing_strike(tmp_path)
    sentences = [c.children for c in blotter.missing_terms_notice(conn).children[2:]]
    assert sentences == [
        "Type the strike straight into the Strike cell under Options, and set Payoff to Digital where it is one. ",
        "You can also enter it under Manual entry ▸ Option terms. ",
        "Or re-upload a blotter export that includes a Strike column.",
    ]


def test_missing_terms_notice_absent_when_every_option_has_a_strike(tmp_path):
    conn = _db_with_option_missing_strike(tmp_path)
    conn.execute("UPDATE instrument_options SET strike = 11.25")
    conn.commit()
    assert blotter.missing_terms_notice(conn) is None


def test_scope_layout_shows_missing_terms_notice_on_every_sub_tab(tmp_path):
    conn = _db_with_option_missing_strike(tmp_path)
    for scope in ("total", "fx", "options"):
        text = str(blotter.scope_layout(scope, conn, "2026-09-17").to_plotly_json())
        assert "no strike on file" in text, scope


def test_options_table_flags_no_strike_rows_red():
    from ui.tabs import options as options_ui
    import pandas as pd
    df = pd.DataFrame([{**{c: None for c in options_ui.ALL_COLUMNS}, "label": "x", "level": "LEG",
                        "type": "Call (no strike on file)", "package_id": "p", "trade_id": "t"}])
    _, styles = options_ui.format_rows(df)
    assert any("no strike" in str(s.get("if", {}).get("filter_query", "")) for s in styles)


def test_priced_diff_scoped_unavailable_when_blocked_trades_outnumber_anchored_ones(monkeypatch):
    """Reference-date gap (2026-09-18): most rows open on the reference date are unpriced
    there, so the strip's Daily is n/a with a reason naming that date and the count, not
    "0 -- excludes 771 of 772"."""
    df_a = _frame([("T1", "FX_FWD", "", 10.0), ("T2", "FX_FWD", "", 20.0), ("T3", "FUTURE", "", 5.0)])
    df_b = _frame([("T1", "FX_FWD", "", 4.0),
                   ("T2", "FX_FWD", "no FWD_OUTRIGHT mark for T2", float("nan")),
                   ("T3", "FUTURE", "no FUTURE_PX mark for T3", float("nan"))])
    # every earlier close is just as unpriced, so the step-back (2026-09-21) finds nothing
    monkeypatch.setattr(blotter_pricing, "priced_value_book",
                         lambda conn, date: (df_a, 0, len(df_a)) if date == "2026-06-20" else (df_b, 0, len(df_b)))
    entry = blotter_pricing._priced_diff_scoped(None, "2026-06-20", "2026-06-19", ["T1", "T2", "T3"],
                                                 "2026-06-19", "2026-06-19")
    assert entry["available"] is False
    assert entry["reason"].startswith("needs the 2026-06-19 close: 2 of 3 trades open that day have no official mark there")
    assert "1 forward: no FWD_OUTRIGHT" in entry["reason"] and "1 future: no FUTURE_PX" in entry["reason"]
    assert "backfill" in entry["reason"]
    assert "No earlier close within 5 business days has one either" in entry["reason"]
    assert entry["ref_note"] == "" and entry["ref_date"] == "2026-06-19"


def test_priced_diff_scoped_steps_back_to_the_previous_close_that_has_value(monkeypatch):
    """User decision 2026-09-21 ("use previous date until has value"): the 2026-06-19 close
    is unusable, the 2026-06-18 close is priced, so the strip's Daily is measured from
    2026-06-18 and says so; `ref_date` keeps its meaning."""
    df_a = _frame([("T1", "FX_FWD", "", 10.0), ("T2", "FX_FWD", "", 20.0), ("T3", "FUTURE", "", 5.0)])
    df_bad = _frame([("T1", "FX_FWD", "", 4.0),
                     ("T2", "FX_FWD", "no FWD_OUTRIGHT mark for T2", float("nan")),
                     ("T3", "FUTURE", "no FUTURE_PX mark for T3", float("nan"))])
    df_good = _frame([("T1", "FX_FWD", "", 3.0), ("T2", "FX_FWD", "", 8.0), ("T3", "FUTURE", "", 1.0)])
    frames = {"2026-06-20": df_a, "2026-06-19": df_bad, "2026-06-18": df_good}
    monkeypatch.setattr(blotter_pricing, "priced_value_book",
                         lambda conn, date: (frames[date], 0, len(frames[date])))  # KeyError = valued too far back
    entry = blotter_pricing._priced_diff_scoped(None, "2026-06-20", "2026-06-19", ["T1", "T2", "T3"],
                                                 "2026-06-20", "2026-06-19")
    assert entry["available"] is True
    assert entry["value"] == pytest.approx((10 - 3) + (20 - 8) + (5 - 1))
    assert entry["ref_date"] == "2026-06-20" and entry["ref_date_used"] == "2026-06-18"
    assert entry["ref_note"] == "from the 2026-06-18 close: 2026-06-19 has no usable close"
    assert "needs the 2026-06-19 close: 2 of 3" in entry["ref_note_detail"]


def test_nothing_priced_reason_summarises_instead_of_dumping_ids():
    rows = [(f"T{i}", "FX_FWD", "no FWD_OUTRIGHT mark for X", float("nan")) for i in range(9)]
    rows.append(("O1", "FX_OPTION", "no PREMIUM mark for O1", float("nan")))
    reason = blotter_pricing._nothing_priced_reason(_frame(rows), "2026-08-31")
    assert reason.startswith("nothing priced on 2026-08-31: 9 forwards: no FWD_OUTRIGHT; 1 option: no PREMIUM (e.g. ")
    assert reason.count("T") <= 6 and reason.endswith(", ...)")


# --------------------------------------------------------------------------- 2026-09-18
# Bloomberg PC: "Blotter > FX, Total book and the forwards view do not load" -- an error card
# ending "could not convert string to float: '<a date>'" -- "and none of the top headlines
# of the app and in the blotter work". Root cause: `realised_pnl.pnl_usd` holding a date
# (engine/pnl/ledger.py's then-positional INSERT on a table migrated from 12 columns), which
# only ever happens once marks exist. Pinned here: (d) a whole book WITH official marks
# shows real figures on the header and on every scope's strip; (b) one bad stored value
# never blanks a view; (c) what is shown names the trade, the column and the value.

_AS_OF = "2026-09-18"
_HEADLINE_PERIODS = ("LTD P&L", "Daily P&L", "Previous day P&L", "5d", "MTD", "YTD")


def _book_with_marks():
    """Every product the Blotter shows, each with official marks on the as-of and on
    every reference date the periods subtract (t-1, t-2, 5d, month-end, year-end): two
    open forwards (one marked by a direct quote, one by BBG_INTERP), a settled forward, a
    future, a swap and an option."""
    from engine.pnl.ledger import period_reference_dates

    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.executescript("""
        INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31');
        INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31');
        INSERT INTO instruments VALUES ('ESZ6 Index','FUTURE','ES','USD',50,0,'ESZ6 Index','2026-12-18');
        INSERT INTO instruments VALUES ('IRSOIS-USD-9','IRS','USD','USD',1,0,'','2031-06-20');
        INSERT INTO instruments VALUES ('EURUSD121526C-1','FX_OPTION','EUR','USD',1,0,'','2026-12-15');
        INSERT INTO trades VALUES ('E1','XLSX','EURUSD','FX_FWD','E1','2026-06-01',2000000,1.10,'ACC','CP','','TR','d','');
        INSERT INTO trades VALUES ('J1','XLSX','USDJPY','FX_FWD','J1','2026-06-01',1000000,147.0,'ACC','CP','','TR','d','');
        INSERT INTO trades VALUES ('OLD','XLSX','EURUSD','FX_FWD','OLD','2026-06-01',1000000,1.08,'ACC','CP','','TR','d','');
        INSERT INTO trades VALUES ('F1','XLSX','ESZ6 Index','FUTURE','F1','2026-06-01',3,6000.0,'ACC','CP','','TR','d','');
        INSERT INTO trades VALUES ('S1','XLSX','IRSOIS-USD-9','IRS','S1','2026-06-01',10000000,3.85,'ACC','CP','','TR','d','');
        INSERT INTO trades VALUES ('O1','XLSX','EURUSD121526C-1','FX_OPTION','O1','2026-06-01',1000000,0.005,'ACC','CP','','TR','d','');
        INSERT INTO trade_legs VALUES ('E1',1,'FX_NEAR','EUR',2000000,'2026-06-01','2026-10-20',1.10,1);
        INSERT INTO trade_legs VALUES ('E1',2,'FX_NEAR','USD',-2200000,'2026-06-01','2026-10-20',1.10,1);
        INSERT INTO trade_legs VALUES ('J1',1,'FX_NEAR','USD',1000000,'2026-06-01','2026-11-05',147.0,1);
        INSERT INTO trade_legs VALUES ('J1',2,'FX_NEAR','JPY',-147000000,'2026-06-01','2026-11-05',147.0,1);
        INSERT INTO trade_legs VALUES ('OLD',1,'FX_NEAR','EUR',1000000,'2026-06-01','2026-07-24',1.08,1);
        INSERT INTO trade_legs VALUES ('OLD',2,'FX_NEAR','USD',-1080000,'2026-06-01','2026-07-24',1.08,1);
        INSERT INTO trade_legs VALUES ('F1',1,'NOTIONAL','USD',900000,'2026-06-01','2026-12-18',6000.0,0);
        INSERT INTO trade_legs VALUES ('S1',1,'FIXED','USD',-10000000,'2026-06-03','2031-06-20',3.85,1);
        INSERT INTO trade_legs VALUES ('S1',2,'FLOAT','USD',10000000,'2026-06-03','2031-06-20',0.0,1);
        INSERT INTO trade_legs VALUES ('O1',1,'NOTIONAL','EUR',1000000,'2026-06-01','2026-12-15',0.005,0);
        INSERT INTO marks VALUES ('2026-07-24','EURUSD','2026-07-24','SPOT',1.09,'BBG_BFXFORWARD','2026-07-24T17:00:00-04:00');
    """)
    refs = period_reference_dates(_AS_OF)
    days = sorted({_AS_OF, refs["daily"], refs["previous_day"], refs["d5"], refs["mtd"]})
    for k, day in enumerate(days):
        stamp, step = f"{day}T17:00:00-04:00", k * 0.001
        conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
            (day, "EURUSD", day, "SPOT", 1.11 + step, "BBG_BFXFORWARD", stamp),
            (day, "EURUSD", "2026-10-20", "FWD_OUTRIGHT", 1.12 + step, "BBG_BFXFORWARD", stamp),
            (day, "USDJPY", day, "SPOT", 149.0 + k, "BBG_BFXFORWARD", stamp),
            (day, "USDJPY", "2026-11-05", "FWD_OUTRIGHT", 148.0 + k, "BBG_INTERP", stamp),
            (day, "ESZ6 Index", "2026-12-18", "FUTURE_PX", 6100.0 + 10 * k, "BBG_BDH", stamp),
            (day, "IRSOIS-USD-9", "2031-06-20", "PV_USD", 12_000.0 + 500 * k, "QL_PRICER", stamp),
            (day, "IRSOIS-USD-9", "2031-06-20", "CASHFLOW_USD", 0.0, "QL_PRICER", stamp),
            (day, "EURUSD121526C-1", "2026-12-15", "PREMIUM", 0.006 + step, "QL_OPTIONS_PRICER", stamp),
        ])
    conn.commit()
    return conn


def _strip_cards(layout, scope) -> dict:
    """{card label: (value text, [caption texts], [caption tooltips])} of `scope`'s P&L strip."""
    # The FX sub-tab builds its strip statically, with no id: it is the layout's only `.cards` row.
    wanted = (lambda n: getattr(n, "className", "") == "cards") if scope == "fx" else \
        (lambda n: getattr(n, "id", None) == f"blotter-strip-{scope}")
    stack, strip = [layout], None
    while stack and strip is None:
        node = stack.pop()
        if wanted(node):
            strip = node
            break
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            stack.extend(children)
        elif children is not None and not isinstance(children, str):
            stack.append(children)
    assert strip is not None, f"no P&L strip for {scope} in the layout"
    cards = {}

    def collect(node):  # depth-first, in document order: the dict keeps the cards' on-screen order
        if getattr(node, "className", "") == "card":
            label, value, *rest = node.children
            cards[label.children] = (value.children, [c.children for c in rest], [getattr(c, "title", None) for c in rest])
            return
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            for child in children:
                collect(child)
        elif children is not None and not isinstance(children, str):
            collect(children)

    collect(strip)
    return cards


def _all_text(component) -> str:
    parts, stack = [], [component]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            parts.append(node)
            continue
        if isinstance(node, (list, tuple)):
            stack.extend(node)
            continue
        children = getattr(node, "children", None)
        if children is not None:
            stack.append(children)
    return " ".join(parts)


def _is_figure(text) -> bool:
    return isinstance(text, str) and text not in ("", "n/a") and any(ch.isdigit() for ch in text)


@pytest.mark.parametrize("scope", ["total", "futures", "rates", "options"])
def test_every_scope_strip_shows_real_figures_on_a_book_with_official_marks(scope):
    conn = _book_with_marks()
    try:
        cards = _strip_cards(blotter.scope_layout(scope, conn, _AS_OF), scope)
        for label in _HEADLINE_PERIODS:
            assert _is_figure(cards[label][0]), f"{scope} strip: {label} shows {cards[label][0]!r}"
        assert cards["Trades"][0] == {"total": "6", "futures": "1", "rates": "1", "options": "1"}[scope]
    finally:
        conn.close()


def test_fx_scope_strip_and_table_show_real_figures_on_a_book_with_official_marks():
    conn = _book_with_marks()
    try:
        layout = blotter.scope_layout("fx", conn, _AS_OF)
        assert "could not be rendered" not in _all_text(layout)
        cards = _strip_cards(layout, "fx")
        for label in _HEADLINE_PERIODS:
            assert _is_figure(cards[label][0]), f"fx strip: {label} shows {cards[label][0]!r}"
        assert cards["Trades"][0] == "3"
        table = next(t for t in _find_tables(layout) if t.id == "blotter-fx-datatable")
        by_pair_fill = {(r["instrument_id"], r["fill"]): r for r in table.data}
        assert by_pair_fill[("EURUSD", "1.100000")]["pnl_eod"] == "48,000"   # 2,000,000 x (1.124 - 1.10), direct quote
        assert _is_figure(by_pair_fill[("USDJPY", "147.000000")]["pnl_eod"])  # BBG_INTERP outright is official
        assert by_pair_fill[("EURUSD", "1.080000")]["pnl_eod"] == "10,000"   # settled: frozen at the 2026-07-24 spot
    finally:
        conn.close()


def test_header_cards_show_real_figures_on_a_book_with_official_marks():
    conn = _book_with_marks()
    try:
        cards = [c for c in header._build_figures(conn, _AS_OF) if getattr(c, "className", "") == "header-figure"]
        by_title = {c.children[0].children: c for c in cards}
        for title in ("LTD", "Daily", "Previous day", "5d", "MTD", "YTD", "Trading"):
            assert _is_figure(by_title[title].children[1].children), f"header {title}: {by_title[title].children[1].children!r}"
            assert len(by_title[title].children) == 2, f"header {title} carries a caption: {by_title[title].children[2:]}"
        assert by_title["Trades"].children[1].children == "6"
        assert _is_figure(by_title["Net USD delta"].children[1].children)
    finally:
        conn.close()


def _misaligned_realised_row(conn):
    """What engine/pnl/ledger.py's old positional INSERT left for trade OLD on a database
    whose `realised_pnl` was migrated from 12 columns: every value two columns off, so
    `pnl_usd` holds `spot_as_of_date` -- text in a REAL column."""
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
        "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) "
        "VALUES ('OLD','EURUSD','2026-09-18T17:00:00','FX_FWD','USD','2026-07-24',1000000,'',1080000,'SPOT','1.09',"
        "'2026-07-24','BBG_BFXFORWARD','10000.0')")
    conn.commit()
    assert conn.execute("SELECT typeof(pnl_usd) FROM realised_pnl").fetchone()[0] == "text"


def test_the_2026_09_18_incident_no_longer_blanks_any_view():
    conn = _book_with_marks()
    try:
        clean = {s: _strip_cards(blotter.scope_layout(s, conn, _AS_OF), s) for s in ("total", "futures", "rates")}
        _misaligned_realised_row(conn)
        for scope in ("total", "fx", "futures", "rates", "options"):
            layout = blotter.scope_layout(scope, conn, _AS_OF)
            text = _all_text(layout)
            assert "could not convert string to float" not in text, scope
            # the banner names the table.column, the row and the value, and says it heals itself
            assert "realised_pnl.pnl_usd: 1 value that is not a number -- '2026-07-24' (trade_id OLD)" in text
            assert "rebuilt automatically by the next Bloomberg pull" in text
            if scope in clean:  # same figures as with no bad row: OLD is valued as not yet frozen
                assert _strip_cards(layout, scope)["LTD P&L"][0] == clean[scope]["LTD P&L"][0]
        ltd = next(c for c in header._build_figures(conn, _AS_OF) if c.children[0].children == "LTD")
        assert _is_figure(ltd.children[1].children) and len(ltd.children) == 2
    finally:
        conn.close()


def test_one_text_price_unprices_one_row_and_every_view_still_renders():
    conn = _book_with_marks()
    try:
        conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 'J1'")
        conn.commit()
        layout = blotter.scope_layout("total", conn, _AS_OF)
        text = _all_text(layout)
        assert "could not be rendered" not in text
        assert "trades.price: 1 value that is not a number -- '24-Jul' (trade_id J1)" in text   # the banner
        cards = _strip_cards(layout, "total")
        value, captions, tooltips = cards["LTD P&L"]
        assert _is_figure(value)
        assert "excludes 1 of 6 trades unpriced (1 with a stored value is not a number)" in captions
        assert any("trade J1: trades.price is not a number ('24-Jul')" in (t or "") for t in tooltips)
        table = next(t for t in _find_tables(layout) if t.id == "blotter-datatable-total")
        row = next(r for r in table.data if r["trade_id"] == "J1")
        assert row["pnl_usd"] == "n/a" and row["fill"] == "n/a"          # missing stays missing: no 0, no raw text
        tip = table.tooltip_data[table.data.index(row)]
        assert tip["pnl_usd"]["value"] == "trade J1: trades.price is not a number ('24-Jul')"
        assert sum(1 for r in table.data if r["pnl_usd"] != "n/a") == 5  # every other trade prices
        fx = blotter.scope_layout("fx", conn, _AS_OF)
        assert "could not be rendered" not in _all_text(fx)
        assert len(next(t for t in _find_tables(fx) if t.id == "blotter-fx-datatable").data) == 3
    finally:
        conn.close()


def test_text_quantity_has_no_side_and_no_notional():
    conn = _book_with_marks()
    try:
        conn.execute("UPDATE trades SET quantity = '24-Jul' WHERE trade_id = 'E1'")
        conn.commit()
        df = blotter.scope_df(conn, "total", _AS_OF).set_index("trade_id")
        assert df.loc["E1", "side"] == "" and df.loc["E1", "notional_usd"] != df.loc["E1", "notional_usd"]
        assert df.loc["J1", "side"] == "Buy" and df.loc["J1", "notional_usd"] == 1_000_000
    finally:
        conn.close()


def test_a_text_t1_mark_blanks_the_t1_rate_cell_only():
    from engine.pnl.ledger import period_reference_dates
    conn = _book_with_marks()
    try:
        t1 = period_reference_dates(_AS_OF)["daily"]
        conn.execute("UPDATE marks SET value = '24-Jul' WHERE as_of_date = ? AND instrument_id = 'USDJPY' "
                     "AND mark_type = 'FWD_OUTRIGHT'", (t1,))
        conn.commit()
        df = blotter.scope_df(conn, "total", _AS_OF).set_index("trade_id")
        assert df.loc["J1", "t1_rate"] != df.loc["J1", "t1_rate"]     # NaN, not a raise
        assert df.loc["J1", "reason"] == "" and df.loc["E1", "t1_rate"] == df.loc["E1", "t1_rate"]
    finally:
        conn.close()


def test_error_card_names_table_column_row_value_and_the_fix():
    conn = _book_with_marks()
    try:
        conn.execute("UPDATE instrument_options SET strike = 'x' WHERE 0")  # no-op: table exists
        conn.execute("UPDATE trade_legs SET rate = '24-Jul' WHERE trade_id = 'F1'")
        conn.commit()
        card = blotter._error_card("FX", ValueError("could not convert string to float: '24-Jul'"), conn)
        text = _all_text(card)
        assert "FX could not be rendered (could not convert string to float: '24-Jul')." in text
        assert "trade_legs.rate: 1 value that is not a number -- '24-Jul' (trade_id F1 leg_no 1)" in text
        assert "to fix: re-upload the blotter" in text
        # without a connection, or with nothing bad on file, the card is exactly the old one-liner
        assert len(blotter._error_card("FX", ValueError("boom")).children) == 1
        assert len(blotter._error_card("FX", ValueError("boom"), _book_with_marks()).children) == 1
    finally:
        conn.close()


def test_bad_stored_values_is_empty_on_a_clean_book_and_survives_a_missing_table():
    conn = _book_with_marks()
    try:
        assert blotter_pricing.bad_stored_values(conn) == []
        assert blotter.bad_values_notice(conn) is None
        conn.execute("DROP TABLE instrument_options")
        assert blotter_pricing.bad_stored_values(conn) == []
    finally:
        conn.close()


def test_fx_sub_tab_keeps_its_strip_when_its_table_fails(monkeypatch):
    from ui.tabs import blotter_fx

    def boom(*args, **kwargs):
        raise ValueError("could not convert string to float: '24-Jul'")

    conn = _book_with_marks()
    try:
        monkeypatch.setattr(blotter_fx, "fx_blotter_rows", boom)
        layout = blotter.scope_layout("fx", conn, _AS_OF)
        assert _is_figure(_strip_cards(layout, "fx")["LTD P&L"][0])   # the strip is still there, still a figure
        assert "FX trade table could not be rendered (could not convert string to float: '24-Jul')." in _all_text(layout)
    finally:
        conn.close()


# --------------------------------------------------------------------------- 2026-09-18 integration
# Rates callbacks hooked in; Options no longer rebuilt on a marks-only revision; the strip
# above the Options table shows only the cards that have a value.

def _blotter_app(db_path):
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    blotter.register_callbacks(app, get_db_path=lambda: str(db_path))
    return app


def _wrapped(app, key_test):
    keys = [k for k in app.callback_map if key_test(k)]
    assert len(keys) == 1, keys
    fn = app.callback_map[keys[0]]["callback"]
    return getattr(fn, "__wrapped__", fn)


def _call_triggered_by(prop_ids, fn, *args):
    """Call a wrapped callback the way Dash would when exactly `prop_ids` changed."""
    import contextvars
    from dash._callback_context import context_value
    from dash._utils import AttributeDict

    def _run():
        context_value.set(AttributeDict(triggered_inputs=[{"prop_id": p, "value": None} for p in prop_ids]))
        return fn(*args)
    return contextvars.copy_context().run(_run)


def _empty_file_db(tmp_path):
    db_path = tmp_path / "risk.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.close()
    return db_path


def test_register_callbacks_hooks_the_rates_callback_and_no_output_is_registered_twice(tmp_path):
    from ui.tabs import rates
    assert rates.DEFAULT_DATE_PICKER_ID == blotter.DATE_PICKER_ID   # registered with its default picker
    app = _blotter_app(_empty_file_db(tmp_path))
    # `id.prop` per Output; an `allow_duplicate` Output carries an "@<hash>" suffix and is
    # unique by construction. Every plain one must appear exactly once across the app.
    outputs = [o for key in app.callback_map for o in key.strip(".").split("...")]
    plain = [o for o in outputs if "@" not in o]
    assert sorted(plain) == sorted(set(plain)), [o for o in set(plain) if plain.count(o) > 1]
    assert len(outputs) == len(set(outputs))                 # nor the same duplicate twice
    # the Direction write is hooked in: exactly one callback owns the plain `rates-datatable.data`
    assert plain.count(f"{rates.DATATABLE_ID}.data") == 1
    # and it was registered once, not once here and once somewhere else
    standalone = dash.Dash(__name__, suppress_callback_exceptions=True)
    rates.register_callbacks(standalone, get_db_path=lambda: ":memory:")
    assert set(standalone.callback_map) <= set(app.callback_map)
    # the strips this module builds above the Options and Rates tables follow new marks in place
    assert "blotter-strip-options.children" in plain and "blotter-strip-rates.children" in plain


def test_options_and_rates_sub_tabs_are_not_rebuilt_on_a_marks_only_revision(tmp_path):
    """Both modules refresh their own table in place from the revision stores
    (`ui.tabs.options._render`, `ui.tabs.rates._refresh`), so a Bloomberg pull must not
    rebuild the sub-tab under the user's hands; a new book, date or sub-tab still does."""
    from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID
    from ui.tabs import rates
    for module in (options, rates):   # the premise: each listens to both revision stores itself
        probe = dash.Dash(__name__, suppress_callback_exceptions=True)
        module.register_callbacks(probe, get_db_path=lambda: ":memory:")
        listened = {d["id"] for cb in probe.callback_map.values() for d in cb["inputs"]}
        assert {DATA_REVISION_ID, BOOK_REVISION_ID} <= listened, module.__name__
    db_path = _empty_file_db(tmp_path)
    app = _blotter_app(db_path)
    update = _wrapped(app, lambda k: f"{blotter.CONTENT_ID}.children" in k)
    data, book = f"{DATA_REVISION_ID}.data", f"{BOOK_REVISION_ID}.data"
    date, tab = f"{blotter.DATE_PICKER_ID}.date", f"{blotter.SUBTABS_ID}.value"
    from ui import revision
    on_screen = revision.trade_set_signature(db_path)   # what the content on screen was built from

    def rebuilt(scope, *triggers, built_from=on_screen):
        content, built = _call_triggered_by(triggers, update, "2026-06-20", scope, "b1", "d1", built_from)
        assert (content is dash.no_update) == (built is dash.no_update)
        return content is not dash.no_update

    assert not rebuilt("options", data)                 # a Bloomberg pull: Options refreshes itself in place
    assert not rebuilt("options", book)                 # a book revision with the SAME trade set: a saved term
    assert not rebuilt("options", data, book)
    assert rebuilt("options", book, built_from="an-older-trade-set")        # a genuine book change rebuilds
    assert rebuilt("options", data, book, built_from="an-older-trade-set")  # both published in one tick
    assert rebuilt("options", book, built_from=None)    # nothing recorded yet: rebuild, the safe side
    assert rebuilt("options", date) and rebuilt("options", tab)
    assert not rebuilt("rates", data) and not rebuilt("rates", book)        # same treatment: a flipped swap
    assert rebuilt("rates", book, built_from="an-older-trade-set")
    assert rebuilt("rates", date) and rebuilt("rates", tab)
    assert rebuilt("fx", data) and rebuilt("bundles", data)   # unchanged: one static block, rebuilt on new marks
    assert not rebuilt("total", data)                   # unchanged: rows refresh through _apply_filters
    assert not rebuilt("total", book)                   # a saved term is no reason to rebuild the Total book either
    assert rebuilt("total", book, built_from="an-older-trade-set")
    assert not rebuilt("manual", data, book, built_from="an-older-trade-set")  # unchanged: the form is never rebuilt
    assert blotter._MARKS_REBUILD_SCOPES == ("bundles", "fx")
    assert blotter._SELF_REFRESHING_SCOPES == ("options", "rates")
    # a rebuild records the trade set it was built from, for the next comparison
    _content, built = _call_triggered_by((tab,), update, "2026-06-20", "options", "b1", "d1", None)
    assert built == on_screen


def _swap_and_digital_book(tmp_path):
    """On disk: one swap and one digital with no strike on file -- the two things the user
    edits several of in a row right after a restart."""
    db_path = tmp_path / "edits.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.executescript("""
        INSERT INTO instruments VALUES ('IRSOIS-USD-9','IRS','USD','USD',1,0,'','2031-06-20');
        INSERT INTO instruments VALUES ('USDJPY111926P-1','FX_OPTION','USD','JPY',1,0,'','2026-11-19');
        INSERT INTO instrument_options (instrument_id, strike, option_type) VALUES ('USDJPY111926P-1', 0, 'PUT');
        INSERT INTO trades VALUES ('S1','XLSX','IRSOIS-USD-9','IRS','S1','2026-06-01',10000000,3.85,'ACC','CP','','TR','d','');
        INSERT INTO trades VALUES ('O1','XLSX','USDJPY111926P-1','FX_OPTION','O1','2026-06-01',5000000,0.01,'ACC','CP','','TR','d','');
        INSERT INTO trade_legs VALUES ('S1',1,'FIXED','USD',-10000000,'2026-06-03','2031-06-20',3.85,1);
        INSERT INTO trade_legs VALUES ('S1',2,'FLOAT','USD',10000000,'2026-06-03','2031-06-20',0.0,1);
        INSERT INTO trade_legs VALUES ('O1',1,'NOTIONAL','USD',5000000,'2026-06-01','2026-11-19',0.01,0);
    """)
    conn.commit()
    return db_path, conn


def test_a_saved_term_or_a_flipped_swap_moves_the_book_revision_but_not_the_trade_set(tmp_path):
    """The whole point: both edits ARE published as book revisions (`book_signature` sums
    strikes and signed quantities, and other modules' tests pin that), yet neither may
    rebuild a sub-tab -- so the Blotter compares the narrower `trade_set_signature`."""
    from ui import revision
    db_path, conn = _swap_and_digital_book(tmp_path)
    book, trade_set = revision.book_signature(db_path), revision.trade_set_signature(db_path)

    conn.execute("UPDATE instrument_options SET strike = 152, payoff = 'DIGITAL' WHERE instrument_id = 'USDJPY111926P-1'")
    conn.commit()
    assert revision.book_signature(db_path) != book
    assert revision.trade_set_signature(db_path) == trade_set          # a strike typed in: not a book change

    book = revision.book_signature(db_path)
    conn.execute("UPDATE trades SET quantity = -quantity WHERE trade_id = 'S1'")     # pay fixed -> receive fixed,
    conn.execute("UPDATE trade_legs SET amount = -amount WHERE trade_id = 'S1'")     # as irs_direction writes it
    conn.commit()
    assert revision.book_signature(db_path) != book
    assert revision.trade_set_signature(db_path) == trade_set          # a flipped swap: not a book change

    conn.execute("DELETE FROM trade_legs WHERE trade_id = 'O1'")
    conn.execute("DELETE FROM trades WHERE trade_id = 'O1'")            # a manual trade deleted
    conn.commit()
    assert revision.trade_set_signature(db_path) != trade_set
    conn.close()


def test_the_banners_live_outside_the_rebuilt_content_and_follow_every_revision(tmp_path):
    db_path, conn = _swap_and_digital_book(tmp_path)
    shell = blotter.build_layout(default_date="2026-06-20")
    ids = [getattr(c, "id", None) for c in shell.children]
    assert ids.index(blotter.NOTICES_ID) < ids.index(blotter.CONTENT_ID)       # above the content, not in it
    assert blotter.BUILT_TRADE_SET_ID in ids

    app = _blotter_app(db_path)
    notices = _wrapped(app, lambda k: k == f"{blotter.NOTICES_ID}.children")
    update = _wrapped(app, lambda k: f"{blotter.CONTENT_ID}.children" in k)
    shown = notices("d1", "b1")
    assert len(shown) == 1 and "1 option cannot be priced: no strike on file." in _all_text(shown)
    assert "USDJPY111926P-1" in _all_text(shown)
    content, _built = update("2026-06-20", "options", "b1", "d1", None)
    assert "cannot be priced: no strike on file" not in _all_text(content)   # the banner is never twice on the page

    conn.execute("UPDATE instrument_options SET strike = 152 WHERE instrument_id = 'USDJPY111926P-1'")
    conn.commit()
    assert notices("d2", "b1") == []                                     # gone on the very next revision
    # `scope_layout`'s direct callers still get the banners on top, as before
    conn.execute("UPDATE instrument_options SET strike = 0")
    conn.commit()
    assert "no strike on file" in _all_text(blotter.scope_layout("rates", conn, "2026-06-20"))
    assert "no strike on file" not in _all_text(blotter.scope_layout("rates", conn, "2026-06-20", with_notices=False))
    conn.close()


def test_a_saved_options_cell_publishes_the_data_revision_at_once_and_only_when_the_file_moved(tmp_path):
    from ui import revision
    from ui.revision import DATA_REVISION_ID
    db_path, conn = _swap_and_digital_book(tmp_path)
    conn.close()
    app = _blotter_app(db_path)
    key = next(k for k in app.callback_map if k.startswith(f"{DATA_REVISION_ID}.data@"))
    spec = app.callback_map[key]
    assert [i["id"] for i in spec["inputs"]] == [options.EDIT_STATUS_ID]   # an OUTPUT of options._render: after the save
    publish = getattr(spec["callback"], "__wrapped__", spec["callback"])
    now = revision.file_signature(db_path)
    assert publish("Saved USDJPY111926P-1: ...", "an-older-signature") == now
    assert publish("Digital noted for ...", now) is dash.no_update       # nothing written: nothing published
    assert revision.publish_if_changed("", "x") is dash.no_update        # an unreadable file is never news


def _options_book(tmp_path, premium_on_earlier_closes: bool):
    """`_book_with_marks` on disk. Without `premium_on_earlier_closes` the option has a
    PREMIUM mark on the as-of only -- the app has run with Bloomberg today and never before."""
    db_path = tmp_path / "options.db"
    src = _book_with_marks()
    disk = sqlite3.connect(db_path)
    src.backup(disk)
    src.close()
    if not premium_on_earlier_closes:
        disk.execute("DELETE FROM marks WHERE mark_type = 'PREMIUM' AND as_of_date < ?", (_AS_OF,))
        disk.commit()
    return db_path, disk


_WAITING = ("Daily P&L, Previous day P&L, 5d, MTD, LTD-1 P&L and LTD-2 P&L appear once option marks exist for "
            "the earlier close; they are written each day the app runs with Bloomberg.")


def test_options_strip_shows_only_cards_with_a_value_and_one_caption_for_the_rest(tmp_path):
    _path, conn = _options_book(tmp_path, premium_on_earlier_closes=False)
    try:
        layout = blotter.scope_layout("options", conn, _AS_OF)
        cards = _strip_cards(layout, "options")
        assert list(cards) == ["LTD P&L", "YTD", "Trades", "Trading P&L", "Trading P&L T-1"]
        assert all(value != "n/a" for value, _c, _t in cards.values())
        assert _is_figure(cards["LTD P&L"][0]) and cards["LTD P&L"][0] != "0"
        assert cards["LTD P&L"][0] == cards["YTD"][0]        # the option is new since the year-end: all of its LTD
        text = _all_text(layout)
        assert text.count(_WAITING) == 1                     # ONE line, naming every hidden card and why
        # never a 0 in place of an n/a: the hidden cards are absent, not zeroed
        assert blotter.options_hidden_cards(conn, _AS_OF, ["O1"], blotter_pricing.row_scoped_headline(conn, _AS_OF, ["O1"])) \
            == ("daily", "ltd1_daily", "d5", "mtd", "ltd1", "ltd2")
    finally:
        conn.close()


def test_options_strip_is_the_full_row_with_no_caption_once_every_close_has_option_marks(tmp_path):
    _path, conn = _options_book(tmp_path, premium_on_earlier_closes=True)
    try:
        layout = blotter.scope_layout("options", conn, _AS_OF)
        cards = _strip_cards(layout, "options")
        assert list(cards) == [blotter.HEADLINE_TITLES[k] for k in blotter.HEADLINE_ORDER]
        assert all(_is_figure(cards[label][0]) for label in _HEADLINE_PERIODS)
        assert "appear once option marks exist" not in _all_text(layout)
    finally:
        conn.close()


def test_options_strip_keeps_a_card_that_is_na_for_any_other_reason(tmp_path):
    """Yesterday's PREMIUM is on file but is not a number: LTD-1 is "n/a" because of a data
    error, not because the app did not run -- it stays on screen with its reason. Daily
    (2026-09-21, "use previous date until has value") is measured from the close before
    and says so on the card."""
    from engine.pnl.ledger import period_reference_dates
    _path, conn = _options_book(tmp_path, premium_on_earlier_closes=True)
    try:
        t1 = period_reference_dates(_AS_OF)["daily"]
        conn.execute("UPDATE marks SET value = '24-Jul' WHERE mark_type = 'PREMIUM' AND as_of_date = ?", (t1,))
        conn.commit()
        layout = blotter.scope_layout("options", conn, _AS_OF)
        cards = _strip_cards(layout, "options")
        assert cards["LTD-1 P&L"][0] == "n/a"
        assert _is_figure(cards["Daily P&L"][0])
        t2 = period_reference_dates(_AS_OF)["previous_day"]
        assert f"from the {t2} close: {t1} has no usable close" in _all_text(layout)
        assert _is_figure(cards["5d"][0]) and _is_figure(cards["LTD-2 P&L"][0])
    finally:
        conn.close()


def test_options_strip_hides_nothing_about_today_when_today_itself_is_unpriced(tmp_path):
    _path, conn = _options_book(tmp_path, premium_on_earlier_closes=False)
    try:
        conn.execute("DELETE FROM marks WHERE mark_type = 'PREMIUM'")
        conn.commit()
        cards = _strip_cards(blotter.scope_layout("options", conn, _AS_OF), "options")
        for label in ("LTD P&L", "Daily P&L", "5d", "MTD", "YTD"):   # today's problem: said, with its reason
            assert cards[label][0] == "n/a", label
        assert "LTD-1 P&L" not in cards and "LTD-2 P&L" not in cards and "Previous day P&L" not in cards
    finally:
        conn.close()


def test_only_the_options_strip_hides_cards(tmp_path):
    """Same gap on the Rates side (no swap marks before today): every card stays, "n/a"."""
    _path, conn = _options_book(tmp_path, premium_on_earlier_closes=False)
    try:
        conn.execute("DELETE FROM marks WHERE mark_type IN ('PV_USD', 'CASHFLOW_USD') AND as_of_date < ?", (_AS_OF,))
        conn.commit()
        cards = _strip_cards(blotter.scope_layout("rates", conn, _AS_OF), "rates")
        assert list(cards) == [blotter.HEADLINE_TITLES[k] for k in blotter.HEADLINE_ORDER]
        assert cards["Daily P&L"][0] == "n/a" and _is_figure(cards["LTD P&L"][0])
        assert "appear once option marks exist" not in _all_text(blotter.scope_layout("total", conn, _AS_OF))
    finally:
        conn.close()


def test_options_strip_refreshes_in_place_on_a_data_revision(tmp_path):
    db_path, conn = _options_book(tmp_path, premium_on_earlier_closes=False)
    conn.close()
    app = _blotter_app(db_path)
    refresh = _wrapped(app, lambda k: k == "blotter-strip-options.children")
    strip = refresh("rev-2", _AS_OF)
    assert _WAITING in _all_text(strip)
    labels = [c.children[0].children for c in strip.children[0].children]
    assert labels == ["LTD P&L", "YTD", "Trades", "Trading P&L", "Trading P&L T-1"]
    assert refresh("rev-3", None) is dash.no_update          # no date yet: leave what is on screen
    # Rates: the same in-place refresh, the full generic row (no card is ever hidden there)
    rates_strip = _wrapped(app, lambda k: k == "blotter-strip-rates.children")("rev-2", _AS_OF)
    rates_labels = [c.children[0].children for c in rates_strip.children[0].children]
    assert rates_labels == [blotter.HEADLINE_TITLES[k] for k in blotter.HEADLINE_ORDER]
    assert len(rates_strip.children) == 1                     # no caption line
