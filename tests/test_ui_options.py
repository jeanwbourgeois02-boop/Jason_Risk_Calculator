"""Tests for ui/tabs/options.py (the Blotter "Options" sub-tab, options_calc merge
Phase 8). Owned by ui-shell.

Seeds a tiny synthetic DB via data.ingest.schema with a two-leg FX_OPTION structure
sharing one package_id (a call + a put) and one standalone single-leg FX_OPTION,
plus PREMIUM/DELTA/GAMMA/THETA/VEGA/RHO marks under QL_OPTIONS_PRICER (the shape
engine/options/store.py actually writes: settle_date = expiry, one instrument per
option leg), and SPOT/FWD_OUTRIGHT marks -- never re-prices anything itself.
"""
from __future__ import annotations

import math
import sqlite3

import dash
import pandas as pd
import pytest

from data.ingest import schema
from ui.tabs import options

AS_OF = "2026-06-20"
_GREEKS = ("DELTA", "GAMMA", "THETA", "VEGA", "RHO")
_GREEK_VALUES = {"DELTA": 0.55, "GAMMA": 0.02, "THETA": -0.0004, "VEGA": 0.0018, "RHO": 0.0006}


def _insert_pair_spot(conn, pair="EURUSD", base="EUR", quote="USD", spot=1.1050, fwd=1.1080,
                        expiry="9999-12-31"):
    conn.execute(
        "INSERT OR IGNORE INTO instruments VALUES (?, 'FX', ?, ?, 1, 0, ?, ?)",
        (pair, base, quote, pair + " Curncy", expiry),
    )
    conn.execute(
        "INSERT INTO marks VALUES (?, ?, ?, 'SPOT', ?, 'BBG_BFXFORWARD', ?)",
        (AS_OF, pair, AS_OF, spot, f"{AS_OF}T17:00:00-04:00"),
    )


def _insert_option_leg(conn, trade_id, package_id, instrument_id, base, quote, quantity,
                        expiry, strike, option_type, fill=0.0050,
                        premium=0.0062, greeks=None, marks=True):
    conn.execute(
        "INSERT INTO instruments VALUES (?, 'FX_OPTION', ?, ?, 1, 0, '', ?)",
        (instrument_id, base, quote, expiry),
    )
    conn.execute(
        "INSERT INTO instrument_options VALUES (?, ?, ?, 0, '9999-12-31', 'VANILLA')",
        (instrument_id, strike, option_type),
    )
    conn.execute(
        "INSERT INTO trades VALUES (?, 'XLSX', ?, 'FX_OPTION', ?, '2026-06-01', ?, ?, "
        "'ACC', 'CPTY', 'HAHY7', 'TR', 'option', '')",
        (trade_id, instrument_id, package_id, quantity, fill),
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES (?, 1, 'NOTIONAL', ?, ?, '2026-06-01', ?, ?, 0)",
        (trade_id, base, quantity, expiry, fill),
    )
    if marks:
        vals = dict(_GREEK_VALUES)
        if greeks:
            vals.update(greeks)
        conn.execute(
            "INSERT INTO marks VALUES (?, ?, ?, 'PREMIUM', ?, 'QL_OPTIONS_PRICER', ?)",
            (AS_OF, instrument_id, expiry, premium, f"{AS_OF}T17:00:00-04:00"),
        )
        for mt in _GREEKS:
            if mt in vals and vals[mt] is not None:
                conn.execute(
                    "INSERT INTO marks VALUES (?, ?, ?, ?, ?, 'QL_OPTIONS_PRICER', ?)",
                    (AS_OF, instrument_id, expiry, mt, vals[mt], f"{AS_OF}T17:00:00-04:00"),
                )
    conn.execute(
        "INSERT OR IGNORE INTO marks VALUES (?, ?, ?, 'FWD_OUTRIGHT', 1.1080, 'BBG_BFXFORWARD', ?)",
        (AS_OF, base + quote, expiry, f"{AS_OF}T17:00:00-04:00"),
    )


def _make_db():
    """One two-leg package (PKG1: a call + a put on EURUSD, same expiry) and one
    standalone single-leg option (O3, USDJPY put) with RHO deliberately missing and
    strike 0 (both blank-rendering cases)."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    _insert_pair_spot(conn, "EURUSD", "EUR", "USD", spot=1.1050)
    _insert_option_leg(conn, "O1", "PKG1", "EURUSD092226C-1", "EUR", "USD", 1_000_000.0,
                        "2026-09-22", strike=1.11, option_type="CALL", premium=0.0062)
    _insert_option_leg(conn, "O2", "PKG1", "EURUSD092226P-1", "EUR", "USD", -1_000_000.0,
                        "2026-09-22", strike=1.10, option_type="PUT", premium=0.0048)
    _insert_pair_spot(conn, "USDJPY", "USD", "JPY", spot=147.50)
    _insert_option_leg(conn, "O3", "O3", "USDJPY091026P-1", "USD", "JPY", 500_000.0,
                        "2026-09-10", strike=0.0, option_type="PUT", premium=0.0031,
                        greeks={"RHO": None})
    conn.commit()
    return conn


def _make_db_empty():
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    return conn


# --------------------------------------------------------------------------- option_rows

def test_option_rows_total_equals_sum_of_asset_class_rows():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        total = df[df["level"] == "TOTAL"].iloc[0]
        classes = df[df["level"] == "ASSET_CLASS"]
        for field in ("notional", "mktval", "delta", "theta", "gamma", "vega"):
            summed = classes[field].fillna(0).sum()
            assert total[field] == pytest.approx(summed)
    finally:
        conn.close()


def test_option_rows_package_equals_sum_of_its_legs():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        pkg = df[(df["level"] == "PACKAGE") & (df["group_key"] == "PKG1")].iloc[0]
        legs = df[(df["level"] == "LEG") & (df["parent_key"] == "PKG1")]
        assert len(legs) == 2
        for field in ("notional", "mktval", "delta", "theta", "gamma", "vega", "rho"):
            assert pkg[field] == pytest.approx(legs[field].sum())
    finally:
        conn.close()


def test_option_rows_single_leg_package_is_flat_no_separate_leg_row():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        assert (df["group_key"] == "O3").sum() == 1  # one row total for this package
        row = df[df["group_key"] == "O3"].iloc[0]
        assert row["level"] == "PACKAGE"
        assert row["leg_count"] == 1
        assert row["label"] == "USDJPY091026P-1"
        # No LEG row anywhere has O3 as its parent (nothing nested beneath it).
        assert (df["parent_key"] == "O3").sum() == 0
    finally:
        conn.close()


def test_option_rows_multileg_package_has_nested_leg_rows():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        legs = df[(df["level"] == "LEG") & (df["parent_key"] == "PKG1")]
        assert set(legs["label"]) == {"EURUSD092226C-1", "EURUSD092226P-1"}
    finally:
        conn.close()


def test_option_rows_mktval_uses_base_ccy_spot():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["label"] == "EURUSD092226C-1"].iloc[0]
        # premium 0.0062 * quantity 1,000,000 * EUR->USD spot 1.1050
        assert leg["mktval"] == pytest.approx(0.0062 * 1_000_000.0 * 1.1050)
    finally:
        conn.close()


def test_option_rows_delta_is_usd_equivalent_via_quote_spot():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["label"] == "EURUSD092226C-1"].iloc[0]
        # quote_ccy USD -> spot 1.0, delta = 0.55 * (1,000,000 * multiplier 1) * 1.0
        assert leg["delta"] == pytest.approx(0.55 * 1_000_000.0)
    finally:
        conn.close()


def test_option_rows_missing_rho_is_none_not_zero():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["label"] == "USDJPY091026P-1"].iloc[0]
        assert leg["rho"] is None or leg["rho"] != leg["rho"]
    finally:
        conn.close()


def test_option_rows_zero_strike_is_none():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["label"] == "USDJPY091026P-1"].iloc[0]
        assert leg["strike"] is None or math.isnan(leg["strike"])
    finally:
        conn.close()


def test_option_rows_equity_and_commodity_groups_present_and_empty():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        classes = df[df["level"] == "ASSET_CLASS"]
        assert set(classes["group_key"]) == {"FX", "Equity", "Commodity"}
        for cls in ("Equity", "Commodity"):
            row = classes[classes["group_key"] == cls].iloc[0]
            assert row["leg_count"] == 0
            assert row["notional"] is None or math.isnan(row["notional"])
            assert row["mktval"] is None or math.isnan(row["mktval"])
    finally:
        conn.close()


def test_option_rows_empty_db_still_has_total_and_all_three_groups():
    conn = _make_db_empty()
    try:
        df = options.option_rows(conn, AS_OF)
        assert set(df.loc[df["level"] == "ASSET_CLASS", "group_key"]) == {"FX", "Equity", "Commodity"}
        total = df[df["level"] == "TOTAL"].iloc[0]
        assert total["leg_count"] == 0
        assert total["mktval"] is None
    finally:
        conn.close()


# --------------------------------------------------------------------------- format_rows

def test_format_rows_column_set_and_order():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        records, _style = options.format_rows(df)
        expected_tail = ["position", "notional", "mktval", "mktpx", "delta", "theta",
                          "gamma", "vega", "expiry", "underlying", "strike", "undfwdpx", "rho"]
        keys = list(records[0].keys())
        display_keys = [k for k in keys if k in expected_tail]
        assert display_keys == expected_tail
        assert keys[0] == "level"  # hidden bookkeeping fields also travel with each row
        assert "label" in keys
    finally:
        conn.close()


def test_format_rows_blank_rendering_for_missing_greek_and_zero_strike():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        records, _style = options.format_rows(df)
        row = next(r for r in records if r["label"] == "USDJPY091026P-1")
        assert row["rho"] == "n/a"
        assert row["strike"] == ""
    finally:
        conn.close()


def test_format_rows_missing_value_never_renders_as_zero():
    """USDJPY091026P-1 has no RHO mark on file at all -- its rendered cell must be
    'n/a', never '0'. Contrast with PKG1, whose two legs' rho genuinely cancel to a
    real zero (600 + (-600)) -- that IS allowed to print as '0', since it's a real
    computed sum, not a missing value."""
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        records, _style = options.format_rows(df)
        missing_row = next(r for r in records if r["label"] == "USDJPY091026P-1")
        assert missing_row["rho"] == "n/a"
        pkg_row = next(r for r in records if r["group_key"] == "PKG1")
        assert pkg_row["rho"] == "0"
    finally:
        conn.close()


def test_format_rows_collapses_multileg_package_by_default():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        collapsed = options.default_collapsed_packages(df)
        assert collapsed == ["PKG1"]
        records, _style = options.format_rows(df, collapsed)
        assert not any(r["level"] == "LEG" for r in records)
        pkg_row = next(r for r in records if r["group_key"] == "PKG1")
        assert pkg_row["label"].startswith("▸")  # collapsed arrow
    finally:
        conn.close()


def test_format_rows_expanded_shows_leg_rows():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        records, _style = options.format_rows(df, collapsed=[])
        leg_labels = {r["label"] for r in records if r["level"] == "LEG"}
        assert leg_labels == {"EURUSD092226C-1", "EURUSD092226P-1"}
        pkg_row = next(r for r in records if r["group_key"] == "PKG1")
        assert pkg_row["label"].startswith("▾")  # expanded arrow
    finally:
        conn.close()


def test_default_collapsed_packages_excludes_single_leg_packages():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        collapsed = options.default_collapsed_packages(df)
        assert "O3" not in collapsed
    finally:
        conn.close()


def test_format_rows_empty_frame():
    empty = pd.DataFrame(columns=["level", "group_key", "parent_key", "label", "leg_count",
                                   *options.NUMERIC_FIELDS, *options.PASSTHROUGH_FIELDS])
    records, style = options.format_rows(empty)
    assert records == []
    assert isinstance(style, list)


# --------------------------------------------------------------------------- options_table

def test_options_table_columns_are_hidden_bookkeeping_plus_display():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        table = options.options_table(df)
        ids = [c["id"] for c in table.columns]
        assert ids == options.ALL_COLUMNS
        assert set(table.hidden_columns) == set(options.HIDDEN_COLUMNS)
    finally:
        conn.close()


# --------------------------------------------------------------------------- build_layout

def test_build_layout_renders_table_and_default_collapsed_store():
    conn = _make_db()
    try:
        layout = options.build_layout(conn, AS_OF)
        store = next(c for c in layout.children if isinstance(c, dash.dcc.Store))
        assert store.data == ["PKG1"]
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert table.id == options.TABLE_ID
        assert not any(r["level"] == "LEG" for r in table.data)
    finally:
        conn.close()


def test_build_layout_no_trades_still_renders_groups():
    conn = _make_db_empty()
    try:
        layout = options.build_layout(conn, AS_OF)
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        labels = [r["label"] for r in table.data]
        assert "Portfolio Totals" in labels
        assert "FX" in labels and "Equity" in labels and "Commodity" in labels
    finally:
        conn.close()


# --------------------------------------------------------------------------- register_callbacks

def test_register_callbacks_toggle_and_refresh_via_wrapped_functions(tmp_path):
    db_path = tmp_path / "risk.db"
    conn = schema.connect(str(db_path))
    _insert_pair_spot(conn, "EURUSD", "EUR", "USD", spot=1.1050)
    _insert_option_leg(conn, "O1", "PKG1", "EURUSD092226C-1", "EUR", "USD", 1_000_000.0,
                        "2026-09-22", strike=1.11, option_type="CALL", premium=0.0062)
    _insert_option_leg(conn, "O2", "PKG1", "EURUSD092226P-1", "EUR", "USD", -1_000_000.0,
                        "2026-09-22", strike=1.10, option_type="PUT", premium=0.0048)
    conn.commit()
    conn.close()

    import sys
    import types
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    original = sys.modules.get("ui.app")
    sys.modules["ui.app"] = stub
    try:
        app = dash.Dash(__name__)
        options.register_callbacks(app, get_db_path=lambda: str(db_path))

        toggle_matches = [v for k, v in app.callback_map.items() if k.startswith(options.COLLAPSED_STORE_ID)]
        refresh_matches = [v for k, v in app.callback_map.items()
                            if options.TABLE_ID in k and "data" in k]
        assert toggle_matches and refresh_matches

        toggle_fn = getattr(toggle_matches[0]["callback"], "__wrapped__", toggle_matches[0]["callback"])
        refresh_fn = getattr(refresh_matches[0]["callback"], "__wrapped__", refresh_matches[0]["callback"])

        rows = [{"level": "PACKAGE", "group_key": "PKG1", "leg_count": 2}]
        new_collapsed = toggle_fn({"row": 0, "column": 0}, rows, [])
        assert new_collapsed == ["PKG1"]

        data, style = refresh_fn(new_collapsed, "2026-06-20")
        assert not any(r["level"] == "LEG" for r in data)
        assert isinstance(style, list)
    finally:
        if original is not None:
            sys.modules["ui.app"] = original
        else:
            del sys.modules["ui.app"]
