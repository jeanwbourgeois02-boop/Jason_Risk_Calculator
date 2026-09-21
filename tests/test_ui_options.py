"""Tests for ui/tabs/options.py (the Blotter "Options" sub-tab, options_calc merge
Phase 8). Owned by ui-shell.

Seeds a tiny synthetic DB via data.ingest.schema with a two-leg FX_OPTION structure
sharing one package_id (a call + a put) and one standalone single-leg FX_OPTION,
plus PREMIUM/DELTA/GAMMA/THETA/VEGA/RHO marks under QL_OPTIONS_PRICER (the shape
engine/options/store.py actually writes: settle_date = expiry, one instrument per
option leg), and SPOT/FWD_OUTRIGHT marks -- never re-prices anything itself.
"""
from __future__ import annotations

import contextvars
import copy
import csv
import io
import json
import math
import re
import sqlite3
import sys
import types
from pathlib import Path

import dash
import pandas as pd
import pytest

from data.ingest import schema
from ui.tabs import options

AS_OF = "2026-06-20"
SAMPLE_CSV = Path(__file__).resolve().parents[1] / "data" / "raw" / "new_sample_trades.csv"
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


def _drop_instrument_options_new_columns(conn):
    """Rebuild `instrument_options` with the pre-`payoff`-column shape found on the
    analyst's dev DB (`data/raw/risk.db`, 2026-09-17): `CREATE TABLE IF NOT EXISTS` in
    `data/ingest/schema.py` never adds a column to an already-existing table, so a DB
    created before `payoff` (and, in principle, any future column) landed keeps the old
    shape forever without a migration. Exact column set observed on that file via
    `PRAGMA table_info`: instrument_id/strike/option_type/barrier_level/avg_start_date --
    no `payoff`. Used to reproduce the HTTP 500 (`OperationalError: no such column:
    o.payoff`) this module's `_instrument_options_columns` guard now avoids."""
    conn.execute("DROP TABLE instrument_options")
    conn.execute(
        "CREATE TABLE instrument_options ("
        "  instrument_id   TEXT PRIMARY KEY,"
        "  strike          REAL NOT NULL DEFAULT 0,"
        "  option_type     TEXT NOT NULL DEFAULT '',"
        "  barrier_level   REAL NOT NULL DEFAULT 0,"
        "  avg_start_date  TEXT NOT NULL DEFAULT '9999-12-31'"
        ")"
    )
    conn.commit()


def _make_db_missing_payoff_column():
    """One priceable FX_OPTION trade on a DB whose `instrument_options` table predates
    the `payoff` column -- reproduces the 2026-09-17 dev-DB bug (module docstring on
    `_instrument_options_columns`): a bare `SELECT o.payoff` used to raise
    `OperationalError: no such column: o.payoff`, uncaught, turning into an HTTP 500
    that made the whole Options sub-tab look like it "did not load"."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    _drop_instrument_options_new_columns(conn)
    _insert_pair_spot(conn, "EURUSD", "EUR", "USD", spot=1.1050)
    conn.execute(
        "INSERT INTO instruments VALUES ('EURUSD092226C-1', 'FX_OPTION', 'EUR', 'USD', 1, 0, '', '2026-09-22')"
    )
    conn.execute("INSERT INTO instrument_options VALUES ('EURUSD092226C-1', 1.11, 'CALL', 0, '9999-12-31')")
    conn.execute(
        "INSERT INTO trades VALUES ('O1', 'XLSX', 'EURUSD092226C-1', 'FX_OPTION', 'O1', '2026-06-01', "
        "1000000, 0.0050, 'ACC', 'CPTY', 'HAHY7', 'TR', 'option', '')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('O1', 1, 'NOTIONAL', 'EUR', 1000000, '2026-06-01', '2026-09-22', 0.0050, 0)"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20', 'EURUSD092226C-1', '2026-09-22', 'PREMIUM', 0.0062, "
        "'QL_OPTIONS_PRICER', '2026-06-20T17:00:00-04:00')"
    )
    conn.commit()
    return conn


# ----------------------------------------------------- stale dev-DB (missing `payoff` column)

def test_instrument_options_columns_reports_the_reduced_set():
    conn = _make_db_missing_payoff_column()
    try:
        cols = options._instrument_options_columns(conn)
        assert "payoff" not in cols
        assert {"instrument_id", "strike", "option_type", "barrier_level"} <= cols
    finally:
        conn.close()


def test_leg_rows_survives_missing_payoff_column():
    conn = _make_db_missing_payoff_column()
    try:
        legs = options._leg_rows(conn, AS_OF)
        assert len(legs) == 1
        assert (legs[0]["payoff"], legs[0]["option_type"]) == ("Vanilla", "Call")  # payoff defaulted to VANILLA
        assert legs[0]["mktval"] == pytest.approx(0.0062 * 1_000_000.0 * 1.1050)
    finally:
        conn.close()


def test_option_rows_survives_missing_payoff_column():
    conn = _make_db_missing_payoff_column()
    try:
        df = options.option_rows(conn, AS_OF)
        row = df[df["instrument"] == "EURUSD092226C-1"].iloc[0]
        assert row["mktval"] == pytest.approx(0.0062 * 1_000_000.0 * 1.1050)
    finally:
        conn.close()


def test_build_layout_survives_missing_payoff_column():
    """The exact call `ui.tabs.blotter.scope_layout` makes for the Options sub-tab --
    this is the regression test for the HTTP 500 seen live against `data/raw/risk.db`."""
    conn = _make_db_missing_payoff_column()
    try:
        layout = options.build_layout(conn, AS_OF)
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert table.id == options.TABLE_ID
        assert any(r["instrument"] == "EURUSD092226C-1" for r in table.data)
    finally:
        conn.close()


def test_option_instruments_survives_missing_payoff_column():
    conn = _make_db_missing_payoff_column()
    try:
        insts = options.option_instruments(conn)
        row = next(i for i in insts if i["instrument_id"] == "EURUSD092226C-1")
        assert row["payoff"] == "VANILLA"
        assert row["strike"] == 1.11
    finally:
        conn.close()


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
        assert row["label"] == "USDJPY - Vanilla"  # MARS-style structure name; id in "instrument"
        assert row["instrument"] == "USDJPY091026P-1"
        assert (row["payoff"], row["option_type"], row["side"]) == ("Vanilla", "Put", "Buy")
        # This PACKAGE row IS the trade: its terms are editable, and it names the trade.
        assert row["is_leg"] == 1 and row["trade_id"] == "O3"
        # No LEG row anywhere has O3 as its parent (nothing nested beneath it).
        assert (df["parent_key"] == "O3").sum() == 0
    finally:
        conn.close()


def test_option_rows_multileg_package_has_nested_leg_rows():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        legs = df[(df["level"] == "LEG") & (df["parent_key"] == "PKG1")]
        assert set(legs["instrument"]) == {"EURUSD092226C-1", "EURUSD092226P-1"}
        assert set(legs["label"]) == {"Vanilla"}
        assert set(legs["is_leg"]) == {1} and set(legs["trade_id"]) == {"O1", "O2"}
        pkg = df[(df["level"] == "PACKAGE") & (df["group_key"] == "PKG1")].iloc[0]
        assert pkg["label"] == "EURUSD - Two Leg" and pkg["leg_count"] == 2
        # A summary row is not a trade: nothing on it is editable.
        assert pkg["is_leg"] == 0 and pkg["trade_id"] == ""
    finally:
        conn.close()


def test_option_rows_mktval_uses_base_ccy_spot():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["instrument"] == "EURUSD092226C-1"].iloc[0]
        # premium 0.0062 * quantity 1,000,000 * EUR->USD spot 1.1050
        assert leg["mktval"] == pytest.approx(0.0062 * 1_000_000.0 * 1.1050)
    finally:
        conn.close()


def test_option_rows_greeks_are_the_engines_usd_conversion():
    """Delta is the USD delta notional (quantity x DELTA x USD per BASE ccy), gamma the
    USD delta change per 1 % spot move, vega/theta/rho go through the quote currency --
    `engine/options/store.py`'s unit list (2026-09-18 units audit). The tab does not keep
    its own copy of those formulas: it must equal `engine.options.portfolio` exactly."""
    from types import SimpleNamespace

    from engine.options.portfolio import build_positions

    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["instrument"] == "EURUSD092226C-1"].iloc[0]
        # 1,000,000 EUR x DELTA 0.55 x 1.1050 USD per EUR (the old quote-ccy-only
        # conversion gave 550,000, and a USDJPY delta ~150x too small).
        assert leg["delta"] == pytest.approx(0.55 * 1_000_000.0 * 1.1050)
        assert leg["gamma"] == pytest.approx(0.02 * 1.1050 ** 2 / 100.0 * 1_000_000.0)
        assert leg["vega"] == pytest.approx(0.0018 * 1_000_000.0)      # quote ccy is USD: x 1.0
        assert leg["theta"] == pytest.approx(-0.0004 * 1_000_000.0)

        result = SimpleNamespace(quote_price=0.0, delta=0.55, gamma=0.02, theta=-0.0004, vega=0.0018, rho=0.0006)
        outcome = SimpleNamespace(instrument_id="EURUSD092226C-1", package_id="PKG1", quantity=1_000_000.0,
                                  priced=True, result=result)
        engine_legs, skipped = build_positions(conn, AS_OF, [outcome])
        assert not skipped
        scaled = engine_legs[0].position.scaled()
        for greek in ("delta", "gamma", "vega", "theta", "rho"):
            assert leg[greek] == pytest.approx(scaled[greek])
    finally:
        conn.close()


def test_option_rows_greeks_blank_with_the_engines_reason_when_no_spot():
    """No SPOT to convert with: every Greek is blank and the row says why -- never 0."""
    conn = _make_db()
    try:
        conn.execute("DELETE FROM marks WHERE instrument_id = 'USDJPY' AND mark_type = 'SPOT'")
        conn.execute("UPDATE instrument_options SET strike = 150 WHERE instrument_id = 'USDJPY091026P-1'")
        conn.commit()
        leg = next(l for l in options._leg_rows(conn, AS_OF) if l["instrument"] == "USDJPY091026P-1")
        assert all(leg[g] is None for g in ("delta", "gamma", "vega", "theta", "rho"))
        assert "SPOT" in leg["note"]
    finally:
        conn.close()


def test_option_rows_missing_rho_is_none_not_zero():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["instrument"] == "USDJPY091026P-1"].iloc[0]
        assert leg["rho"] is None or leg["rho"] != leg["rho"]
    finally:
        conn.close()


def test_option_rows_zero_strike_is_none():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        leg = df[df["instrument"] == "USDJPY091026P-1"].iloc[0]
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
        # 2026-09-21 (user: "too many columns"): the details, then what was paid, what it is
        # worth and the P&L, all in USD, then the four main Greeks; the rest travels hidden.
        expected_tail = ["expiry", "position", "notional", "start_value_usd", "mktval", "pnl_usd",
                          "delta", "gamma", "vega", "theta"]
        keys = list(records[0].keys())
        assert [c for c in options.DISPLAY_COLUMNS if c in expected_tail] == expected_tail
        assert set(expected_tail) <= set(keys) and {"mktpx", "underlying", "undfwdpx", "rho"} <= set(keys)
        assert {"mktpx", "underlying", "undfwdpx", "rho"} <= set(options.HIDDEN_COLUMNS)
        assert keys[0] == "level"  # hidden bookkeeping fields also travel with each row
        assert "label" in keys
        # Strike left its MARS slot (2026-09-21): there it was the 23rd column, off-screen,
        # while the row's note says "type it in the Strike cell". The three typed terms sit
        # together right after the structure name, in the order they are filled in.
        shown = options.DISPLAY_COLUMNS
        assert shown[:5] == ["label", "side", "option_type", "payoff", "strike"]
        assert all(shown.index(c) < 5 for c in options.EDITABLE_COLUMNS)
    finally:
        conn.close()


def test_format_rows_blank_rendering_for_missing_greek_and_zero_strike():
    """Missing is None -- a blank cell in a numeric column -- and the row says why."""
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        records, _style = options.format_rows(df)
        row = next(r for r in records if r["instrument"] == "USDJPY091026P-1")
        assert row["rho"] is None
        assert row["strike"] is None
        assert "no strike on file" in row["note"]
    finally:
        conn.close()


def test_format_rows_missing_value_never_renders_as_zero():
    """USDJPY091026P-1 has no RHO mark on file at all -- its cell must be blank (None),
    never 0. Contrast with PKG1, whose two legs' rho genuinely cancel to a real zero
    (600 + (-600)) -- that IS allowed to be 0, since it's a real computed sum, not a
    missing value."""
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        records, _style = options.format_rows(df)
        missing_row = next(r for r in records if r["instrument"] == "USDJPY091026P-1")
        assert missing_row["rho"] is None
        pkg_row = next(r for r in records if r["group_key"] == "PKG1")
        assert pkg_row["rho"] is not None and pkg_row["rho"] == pytest.approx(0.0)
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
        leg_ids = {r["instrument"] for r in records if r["level"] == "LEG"}
        assert leg_ids == {"EURUSD092226C-1", "EURUSD092226P-1"}
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

@pytest.fixture
def ui_app_stub(monkeypatch):
    """`ui.app.connect_readonly` without importing the whole app (every other tab's
    module, several of them other agents' lanes): a genuinely read-only handle, as in
    the app, so a test would fail if rendering ever tried to write."""
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    return stub


def _file_db(tmp_path, digital_payoff="VANILLA", digital_strike=0.0):
    """A database FILE (the callbacks open their own connections): the PKG1 call/put
    pair, priced, plus D1 -- a USDJPY put the blotter delivered the way it delivers the
    book's digitals: payoff VANILLA, no strike, so no marks."""
    db_path = tmp_path / "risk.db"
    conn = schema.connect(str(db_path))
    _insert_pair_spot(conn, "EURUSD", "EUR", "USD", spot=1.1050)
    _insert_option_leg(conn, "O1", "PKG1", "EURUSD092226C-1", "EUR", "USD", 1_000_000.0,
                        "2026-09-22", strike=1.11, option_type="CALL", premium=0.0062)
    _insert_option_leg(conn, "O2", "PKG1", "EURUSD092226P-1", "EUR", "USD", -1_000_000.0,
                        "2026-09-22", strike=1.10, option_type="PUT", premium=0.0048)
    _insert_pair_spot(conn, "USDJPY", "USD", "JPY", spot=147.50)
    _insert_option_leg(conn, "D1", "D1", "USDJPY111926P-1", "USD", "JPY", 2_000_000.0,
                        "2026-11-19", strike=digital_strike, option_type="PUT", fill=0.124, marks=False)
    conn.execute("UPDATE instrument_options SET payoff = ? WHERE instrument_id = 'USDJPY111926P-1'",
                 (digital_payoff,))
    conn.commit()
    conn.close()
    return db_path


def _terms(db_path, instrument_id="USDJPY111926P-1"):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT strike, option_type, payoff FROM instrument_options WHERE instrument_id = ?",
                            (instrument_id,)).fetchone()
    finally:
        conn.close()


def _records(db_path, collapsed=(), filter_query="", sort_by=None):
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        return options.table_records(conn, AS_OF, list(collapsed), filter_query, sort_by)
    finally:
        conn.close()


def _edit(db_path, group_key, column, value, **kwargs):
    """What the table hands the render callback after a user edit: `data` with the one
    cell changed, `data_previous` without."""
    previous = _records(db_path)
    rows = copy.deepcopy(previous)
    next(r for r in rows if r["group_key"] == group_key)[column] = value
    return options.handle_table_event(str(db_path), AS_OF, [], "", [], rows, previous, True, **kwargs)


def _status_text(status):
    return "" if status is None else str(status.children)


class _FakeOutcome:
    def __init__(self, priced, reason="", premium=None):
        self.priced, self.reason = priced, reason
        self.result = types.SimpleNamespace(premium=premium) if priced else None


@pytest.fixture
def fake_pricer(monkeypatch):
    """`engine.options.store.price_and_store` replaced by a recorder, so these tests pin
    THIS module's behaviour (what it saves, what it asks to be priced, what it shows)
    rather than the pricer's market-data needs."""
    import engine.options.store as store
    calls = []
    outcome = {"value": _FakeOutcome(False, "no vol")}

    def _price(conn, as_of, trade_id):
        calls.append((as_of, trade_id))
        return outcome["value"]

    monkeypatch.setattr(store, "price_and_store", _price)
    return types.SimpleNamespace(calls=calls, outcome=outcome)


# --------------------------------------------------------------------------- strike typed in the cell

def test_strike_cell_valid_edit_is_saved_priced_and_shown(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path)
    nudged = []
    records, status = _edit(db_path, "D1", "strike", 152, nudge=lambda: nudged.append(1))
    assert _terms(db_path) == (152.0, "PUT", "VANILLA")           # persisted via set_option_terms
    assert fake_pricer.calls == [(AS_OF, "D1")]                    # re-priced at once, that trade only
    assert nudged == [1]                                           # and the Bloomberg feed woken
    row = next(r for r in records if r["group_key"] == "D1")
    assert row["strike"] == 152.0                                  # refreshed without a reload
    assert "Saved USDJPY111926P-1" in _status_text(status)
    assert status.className == "source-result--info"


def test_strike_cell_skip_reason_is_shown_on_the_row_as_given(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path)
    fake_pricer.outcome["value"] = _FakeOutcome(False, "no curve/rate JPY")
    records, status = _edit(db_path, "D1", "strike", 152)
    row = next(r for r in records if r["group_key"] == "D1")
    assert row["note"] == "not priced: no curve/rate JPY"
    assert "Not priced yet: no curve/rate JPY" in _status_text(status)
    assert row["mktpx"] is None and row["pnl_usd"] is None         # blank, never 0
    # ... and it stays on the row on the next plain render (no edit), until a mark exists.
    again = next(r for r in _records(db_path) if r["group_key"] == "D1")
    assert again["note"] == "not priced: no curve/rate JPY"


def test_strike_cell_priced_outcome_clears_the_skip_reason(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path)
    _edit(db_path, "D1", "strike", 152)                            # skipped: "no vol"
    fake_pricer.outcome["value"] = _FakeOutcome(True, premium=0.1312)
    _records_after, status = _edit(db_path, "D1", "strike", 153)
    assert "Priced as of 2026-06-20: premium 0.1312" in _status_text(status)
    assert not any(key[2] == "D1" for key in options._LAST_SKIP if key[0] == options._norm_path(db_path))


def test_strike_cell_with_the_real_pricer_reports_its_reason(tmp_path, ui_app_stub):
    """No vols or curves on this database: whatever `price_and_store` answers, the terms
    are saved and its reason reaches the row -- nothing is invented."""
    db_path = _file_db(tmp_path)
    records, status = _edit(db_path, "D1", "strike", 152)
    assert _terms(db_path)[0] == 152.0
    row = next(r for r in records if r["group_key"] == "D1")
    assert row["note"].startswith("not priced: ") and len(row["note"]) > len("not priced: ")
    assert row["mktpx"] is None


@pytest.mark.parametrize("typed", ["abc", "", None, -5, 0, "11,4", "1e400", True, float("nan")])
def test_strike_cell_invalid_input_is_reverted_with_a_message(tmp_path, ui_app_stub, fake_pricer, typed):
    db_path = _file_db(tmp_path, digital_strike=150.0)
    before = db_path.stat().st_mtime_ns
    records, status = _edit(db_path, "D1", "strike", typed)
    assert _terms(db_path) == (150.0, "PUT", "VANILLA")            # nothing written ...
    assert db_path.stat().st_mtime_ns == before                    # ... not even a schema touch
    assert fake_pricer.calls == []
    row = next(r for r in records if r["group_key"] == "D1")
    assert row["strike"] == 150.0                                  # the cell is back to what is on file
    assert status.className == "source-result--error"
    assert "Strike must be a positive number" in _status_text(status)


def test_no_text_ever_reaches_a_numeric_column(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path)
    records, _status = _edit(db_path, "D1", "strike", "one fifty-two")
    for rec in records:
        for col in options.NUMERIC_COLUMNS:
            assert rec[col] is None or (isinstance(rec[col], float) and math.isfinite(rec[col])), (col, rec[col])


@pytest.mark.parametrize("group_key", ["TOTAL", "FX", "PKG1"])
def test_strike_cell_on_a_group_row_is_refused(tmp_path, ui_app_stub, fake_pricer, group_key):
    db_path = _file_db(tmp_path)
    records, status = _edit(db_path, group_key, "strike", 152)
    assert fake_pricer.calls == []
    assert _terms(db_path) == (0.0, "PUT", "VANILLA")
    assert _terms(db_path, "EURUSD092226C-1") == (1.11, "CALL", "VANILLA")
    assert next(r for r in records if r["group_key"] == group_key)["strike"] is None
    assert status.className == "source-result--error"
    assert "own row" in _status_text(status)


def test_strike_edit_keeps_a_digital_a_digital(tmp_path, ui_app_stub, fake_pricer):
    """`set_option_terms` WRITES the payoff it is given and defaults it to VANILLA: a
    strike-only save would silently turn a DIGITAL on file back into a vanilla, priced
    more than 10x off. The cell edit hands every other term back unchanged."""
    db_path = _file_db(tmp_path, digital_payoff="DIGITAL", digital_strike=150.0)
    records, _status = _edit(db_path, "D1", "strike", 152)
    assert _terms(db_path) == (152.0, "PUT", "DIGITAL")
    assert next(r for r in records if r["group_key"] == "D1")["payoff"] == "Digital"


def test_strike_edit_keeps_a_barrier_level(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path, digital_strike=150.0)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE instrument_options SET payoff = 'BARRIER_KO', barrier_level = 140 "
                 "WHERE instrument_id = 'USDJPY111926P-1'")
    conn.commit()
    conn.close()
    _edit(db_path, "D1", "strike", 152)
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT strike, payoff, barrier_level FROM instrument_options "
                            "WHERE instrument_id = 'USDJPY111926P-1'").fetchone() == (152.0, "BARRIER_KO", 140.0)
    finally:
        conn.close()


def test_payoff_chosen_before_the_strike_is_held_then_saved_with_it(tmp_path, ui_app_stub, fake_pricer):
    """The digital's flow: Payoff first (it cannot be stored without a strike, so it is
    held and shown), then the strike -- ONE save as DIGITAL, never priced as a vanilla."""
    db_path = _file_db(tmp_path)
    records, status = _edit(db_path, "D1", "payoff", "Digital")
    assert _terms(db_path) == (0.0, "PUT", "VANILLA")              # nothing saved yet
    assert fake_pricer.calls == []
    row = next(r for r in records if r["group_key"] == "D1")
    assert row["payoff"] == "Digital" and "not saved yet" in row["note"] and "no strike" in row["note"]
    assert "type its strike" in _status_text(status)

    records, status = _edit(db_path, "D1", "strike", "152")        # text that IS a number is fine
    assert _terms(db_path) == (152.0, "PUT", "DIGITAL")
    assert fake_pricer.calls == [(AS_OF, "D1")]                    # priced once, as a digital
    row = next(r for r in records if r["group_key"] == "D1")
    assert (row["payoff"], row["strike"]) == ("Digital", 152.0)
    assert "Saved USDJPY111926P-1: Digital Put strike 152" in _status_text(status)


def test_type_and_payoff_dropdown_cells_save_on_a_leg_with_a_strike(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path)
    _edit_expanded = lambda col, val: options.handle_table_event(  # noqa: E731
        str(db_path), AS_OF, [], "", [], _mutated(db_path, "O1", col, val), _records(db_path), True)
    _edit_expanded("payoff", "Digital")
    assert _terms(db_path, "EURUSD092226C-1") == (1.11, "CALL", "DIGITAL")
    _edit_expanded("option_type", "Put")
    assert _terms(db_path, "EURUSD092226C-1") == (1.11, "PUT", "DIGITAL")
    assert fake_pricer.calls == [(AS_OF, "O1"), (AS_OF, "O1")]
    _records_after, status = _edit_expanded("payoff", "Lookback")
    assert status.className == "source-result--error" and "Payoff must be one of" in _status_text(status)
    assert _terms(db_path, "EURUSD092226C-1") == (1.11, "PUT", "DIGITAL")


def _mutated(db_path, group_key, column, value):
    rows = copy.deepcopy(_records(db_path))
    next(r for r in rows if r["group_key"] == group_key)[column] = value
    return rows


def test_barrier_payoff_without_a_level_is_refused_with_where_to_enter_it(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path, digital_strike=150.0)
    _records_after, status = _edit(db_path, "D1", "payoff", "Knock-out")
    assert _terms(db_path) == (150.0, "PUT", "VANILLA")
    assert status.className == "source-result--error"
    assert "barrier" in _status_text(status) and "Option terms below" in _status_text(status)


def test_a_stale_data_previous_does_not_write_again(tmp_path, ui_app_stub, fake_pricer):
    """`data_previous` keeps the pre-edit rows after the callback has answered; an edit
    event whose cells already equal what is on file must not save or price again."""
    db_path = _file_db(tmp_path)
    _edit(db_path, "D1", "strike", 152)
    before = db_path.stat().st_mtime_ns
    stale_previous = copy.deepcopy(_records(db_path))
    next(r for r in stale_previous if r["group_key"] == "D1")["strike"] = None
    options.handle_table_event(str(db_path), AS_OF, [], "", [], _records(db_path), stale_previous, True)
    assert fake_pricer.calls == [(AS_OF, "D1")]
    assert db_path.stat().st_mtime_ns == before


def test_render_without_an_edit_never_applies_anything(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path)
    previous = _records(db_path)
    rows = _mutated(db_path, "D1", "strike", 152)
    records, status = options.handle_table_event(str(db_path), AS_OF, [], "", [], rows, previous, False)
    assert status is None and fake_pricer.calls == [] and _terms(db_path)[0] == 0.0
    assert next(r for r in records if r["group_key"] == "D1")["strike"] is None


def test_parse_strike_accepts_numbers_and_plain_decimal_text_only():
    assert options.parse_strike(152) == 152.0
    assert options.parse_strike(" 11.4 ") == 11.4
    assert options.parse_strike(".5") == 0.5
    for bad in ("11,4", "1,000", "abc", "1e3", "-1", "", None, 0, -0.0, float("inf"), True, [152]):
        with pytest.raises(ValueError, match="positive number"):
            options.parse_strike(bad)


# --------------------------------------------------------------------------- register_callbacks

def _callback(app, fragment):
    matches = [v for k, v in app.callback_map.items() if fragment in k]
    assert len(matches) == 1, (fragment, list(app.callback_map))
    return matches[0], getattr(matches[0]["callback"], "__wrapped__", matches[0]["callback"])


def _with_trigger(prop_id, fn, *args):
    """Call a wrapped callback the way Dash would for one triggering prop (`dash.ctx`)."""
    from dash._callback_context import context_value
    from dash._utils import AttributeDict

    def _run():
        context_value.set(AttributeDict(triggered_inputs=[{"prop_id": prop_id, "value": None}]))
        return fn(*args)
    return contextvars.copy_context().run(_run)


def test_register_callbacks_toggle_and_render_via_wrapped_functions(tmp_path, ui_app_stub, fake_pricer):
    db_path = _file_db(tmp_path)
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: str(db_path))

    _spec, toggle_fn = _callback(app, f"{options.COLLAPSED_STORE_ID}.data")
    _spec, render_fn = _callback(app, f"{options.TABLE_ID}.data.")

    viewport = [{"level": "PACKAGE", "group_key": "PKG1", "leg_count": 2}]
    new_collapsed = toggle_fn({"row": 0, "column": 0}, viewport, None, [])
    assert new_collapsed == ["PKG1"]
    # The click indexes the rows ON SCREEN, not `data`: a single-leg row is no toggle.
    assert toggle_fn({"row": 0, "column": 0}, [{"level": "PACKAGE", "group_key": "D1", "leg_count": 1}],
                     viewport, []) is dash.no_update

    data, status, note = _with_trigger(f"{options.COLLAPSED_STORE_ID}.data", render_fn,
                                       new_collapsed, "", [], None, None, None, None, AS_OF)
    assert not any(r["level"] == "LEG" for r in data) and note == ""
    assert status is dash.no_update

    # A cell edit arrives as `data_timestamp`: applied, answered with fresh rows + a line.
    previous = _records(db_path, collapsed=["PKG1"])
    rows = copy.deepcopy(previous)
    next(r for r in rows if r["group_key"] == "D1")["strike"] = 152
    data, status, _note = _with_trigger(f"{options.TABLE_ID}.data_timestamp", render_fn,
                                        ["PKG1"], "", [], 1, None, rows, previous, AS_OF)
    assert _terms(db_path)[0] == 152.0 and "Saved" in _status_text(status)
    assert next(r for r in data if r["group_key"] == "D1")["strike"] == 152.0

    # The same rows + previous on any OTHER trigger are not an edit (no phantom re-save).
    before = len(fake_pricer.calls)
    _with_trigger(f"{options.TABLE_ID}.filter_query", render_fn,
                  ["PKG1"], "{notional} > 1", [], 1, None, rows, previous, AS_OF)
    assert len(fake_pricer.calls) == before


def test_render_on_a_revision_that_changes_nothing_leaves_the_table_alone(tmp_path, ui_app_stub):
    """A revision the gate lets through that does not move any option number does not
    re-send `data`; one that does, refreshes in place."""
    db_path = _file_db(tmp_path)
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: str(db_path))
    _spec, render_fn = _callback(app, f"{options.TABLE_ID}.data.")
    on_screen = _records(db_path, collapsed=["PKG1"])
    args = (["PKG1"], "", [], None, "rev-2|book-1", on_screen, None, AS_OF)
    data, status, _note = _with_trigger(f"{options.REFRESH_ID}.data", render_fn, *args)
    assert data is dash.no_update and status is dash.no_update

    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE marks SET value = 0.0070 WHERE instrument_id = 'EURUSD092226C-1' AND mark_type = 'PREMIUM'")
    conn.commit()
    conn.close()
    data, _status, _note = _with_trigger(f"{options.REFRESH_ID}.data", render_fn, *args)
    assert next(r for r in data if r["group_key"] == "PKG1")["mktval"] == pytest.approx(
        (0.0070 - 0.0048) * 1_000_000.0 * 1.1050)


# --------------------------------------------------------------------------- a revision never lands on a cell being typed into

def _table_outputs(key):
    """The props of the Options table a callback writes, from its `callback_map` key."""
    outs = [part.split("@")[0] for part in key.strip(".").split("...") if part]
    return {o.split(".", 1)[1] for o in outs if o.split(".", 1)[0] == options.TABLE_ID}


def test_no_callback_that_writes_the_table_listens_to_a_revision_store(tmp_path):
    """ROOT CAUSE of "make it so you can input strike in the table" (2026-09-21).
    dash-table renders the active editable cell as a LABEL -- its input unmounted, the
    typed text gone -- while any callback with `Output(table, "data")` is in flight, and
    the renderer sets that state when the callback is DISPATCHED, whatever it returns. The
    render callback listened to both revision stores, so every Bloomberg write, and the
    data revision the Blotter publishes after each saved strike, wiped the strike being
    typed. Structural guard: a revision may only reach the table through the gate."""
    from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID

    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: str(tmp_path / "unused.db"))
    revisions = {DATA_REVISION_ID, BOOK_REVISION_ID}
    listeners = []
    for key, spec in app.callback_map.items():
        listens = {d["id"] for d in spec["inputs"]} & revisions
        if listens:
            listeners.append(key)
            assert not _table_outputs(key), (key, listens)
    # exactly one listener in this module, the gate, and it listens to BOTH stores
    gate_spec, _gate = _callback(app, f"{options.REFRESH_ID}.data")
    assert listeners == [k for k in app.callback_map if f"{options.REFRESH_ID}.data" in k]
    assert revisions <= {d["id"] for d in gate_spec["inputs"]}
    assert (options.TABLE_ID, "active_cell") in {(d["id"], d["property"]) for d in gate_spec["inputs"]}
    # the render callback hears of a revision through the gate's store, and only there
    render_spec, _render = _callback(app, f"{options.TABLE_ID}.data.")
    render_inputs = {d["id"] for d in render_spec["inputs"]}
    assert options.REFRESH_ID in render_inputs and not (render_inputs & revisions)
    # both new components are in the sub-tab's own layout, next to the table: a callback
    # with an Output missing from the page is dropped by Dash without a word
    conn = _make_db()
    try:
        ids = _component_ids(options.build_layout(conn, AS_OF))
    finally:
        conn.close()
    assert {options.REFRESH_ID, options.REFRESH_NOTE_ID, options.TABLE_ID} <= ids


def test_a_revision_arriving_while_a_strike_is_typed_is_held_and_the_edit_still_saves(tmp_path, ui_app_stub,
                                                                                     fake_pricer):
    db_path = _file_db(tmp_path)
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: str(db_path))
    _spec, gate_fn = _callback(app, f"{options.REFRESH_ID}.data")
    _spec, render_fn = _callback(app, f"{options.TABLE_ID}.data.")
    strike_col = options.DISPLAY_COLUMNS.index("strike")

    # The table is up to date with revision d1|b1; the user clicks D1's Strike cell.
    on_screen = _records(db_path, collapsed=["PKG1"])
    d1_row = next(i for i, r in enumerate(on_screen) if r["group_key"] == "D1")
    typing = {"row": d1_row, "column": strike_col, "column_id": "strike"}
    token, note = gate_fn("d1", "b1", typing, "d1|b1")
    assert token is dash.no_update and note == options.REFRESH_PAUSED_NOTE

    # Bloomberg writes marks while "152" is half typed: two revisions, both HELD -- the
    # gate answers no_update for its store, so `_render` (the only callback that writes
    # the table) is never dispatched and the cell's input stays mounted.
    before = db_path.stat().st_mtime_ns
    for data_rev in ("d2", "d3"):
        token, note = gate_fn(data_rev, "b1", typing, "d1|b1")
        assert token is dash.no_update and note == options.REFRESH_PAUSED_NOTE
    assert db_path.stat().st_mtime_ns == before and fake_pricer.calls == []   # the gate touches no database

    # Enter: the edit commits and saves exactly as before the gate existed.
    rows = copy.deepcopy(on_screen)
    rows[d1_row]["strike"] = 152
    data, status, _note = _with_trigger(f"{options.TABLE_ID}.data_timestamp", render_fn,
                                        ["PKG1"], "", [], 1, "d1|b1", rows, on_screen, AS_OF)
    assert _terms(db_path)[0] == 152.0 and fake_pricer.calls == [(AS_OF, "D1")]
    assert "Saved USDJPY111926P-1" in _status_text(status)
    assert next(r for r in data if r["group_key"] == "D1")["strike"] == 152.0    # shown at once, no gate involved

    # Enter moved the selection one row down, still in the Strike column, where the next
    # strike is about to be typed; the Blotter publishes the save as a data revision
    # (`_publish_option_cell_edit`) and the poll follows with a book revision: all held.
    below = {**typing, "row": d1_row + 1}
    assert gate_fn("d4", "b1", below, "d1|b1")[0] is dash.no_update
    assert gate_fn("d5", "b2", below, "d1|b1")[0] is dash.no_update
    # The Type / Payoff dropdown cells are protected the same way (an open menu closes on a refresh).
    for column_id in ("option_type", "payoff"):
        assert gate_fn("d5", "b2", {"row": d1_row, "column": 2, "column_id": column_id}, "d1|b1")[0] is dash.no_update

    # The selection leaves the term cells: the LATEST revision goes through, once.
    elsewhere = {"row": d1_row, "column": 0, "column_id": "label"}
    token, note = gate_fn("d5", "b2", elsewhere, "d1|b1")
    assert token == "d5|b2" and note == ""
    assert gate_fn("d5", "b2", elsewhere, "d5|b2") == (dash.no_update, "")        # already on screen: nothing to do
    assert gate_fn("d6", "b2", None, "d5|b2") == ("d6|b2", "")                    # no selection at all: straight through
    # ... and `_render` then refreshes the rows in place from the database.
    data, status, _note = _with_trigger(f"{options.REFRESH_ID}.data", render_fn,
                                        ["PKG1"], "", [], 1, "d5|b2", on_screen, None, AS_OF)
    assert next(r for r in data if r["group_key"] == "D1")["strike"] == 152.0 and status is dash.no_update
    assert fake_pricer.calls == [(AS_OF, "D1")]                                   # a refresh never saves or prices


def test_refresh_gate_holds_on_the_three_term_columns_only():
    for column_id in options.EDITABLE_COLUMNS:
        assert options.cell_takes_typing({"row": 0, "column": 1, "column_id": column_id})
        assert options.refresh_gate({"column_id": column_id}, "d2", "b1", "d1|b1") == (None, options.REFRESH_PAUSED_NOTE)
    for active_cell in (None, {}, {"row": 0, "column": 0, "column_id": "label"}, {"column_id": "pnl_usd"}):
        assert not options.cell_takes_typing(active_cell)
        assert options.refresh_gate(active_cell, "d2", "b1", "d1|b1") == ("d2|b1", "")
    assert options.refresh_gate(None, "d1", "b1", "d1|b1") == (None, "")
    assert options.refresh_gate(None, None, None, None) == ("|", "")   # a page with no signature yet still renders


def test_a_package_with_a_leg_that_needs_its_strike_starts_expanded():
    """Collapsed, the flagged package row says "type it in the Strike cell" while its own
    Strike cell takes no input and the leg that does is hidden."""
    conn = _make_db()
    try:
        _insert_option_leg(conn, "O4", "PKG2", "EURUSD102226C-1", "EUR", "USD", 1_000_000.0,
                            "2026-10-22", strike=1.12, option_type="CALL")
        _insert_option_leg(conn, "O5", "PKG2", "EURUSD102226P-1", "EUR", "USD", -1_000_000.0,
                            "2026-10-22", strike=0.0, option_type="PUT", marks=False)
        conn.commit()
        df = options.option_rows(conn, AS_OF)
        assert options.default_collapsed_packages(df) == ["PKG1"]      # PKG2 stays open, PKG1 as before
        layout = options.build_layout(conn, AS_OF)
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        leg = next(r for r in table.data if r["trade_id"] == "O5" and r["level"] == "LEG")
        assert leg["is_leg"] == 1 and leg["strike"] is None and "no strike on file" in leg["note"]
        assert not any(r["level"] == "LEG" and r["parent_key"] == "PKG1" for r in table.data)
    finally:
        conn.close()


def test_clear_filters_callback_resets_filter_and_sort():
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: "unused")
    _spec, clear_fn = _callback(app, f"{options.TABLE_ID}.filter_query")
    assert clear_fn(1) == ("", [])


# --------------------------------------------------------------------------- the Option-terms editor

def _component_ids(component, out=None):
    out = set() if out is None else out
    if isinstance(component, (list, tuple)):
        for c in component:
            _component_ids(c, out)
        return out
    cid = getattr(component, "id", None)
    if isinstance(cid, str):
        out.add(cid)
    children = getattr(component, "children", None)
    if children is not None and not isinstance(children, (str, int, float)):
        _component_ids(children, out)
    return out


def test_terms_editor_callbacks_need_nothing_outside_the_editor(tmp_path, ui_app_stub):
    """ROOT CAUSE of "you cannot input the strike" (2026-09-18): Save listed
    `options-collapsed-packages` as an Output and a State. That store exists only under
    Options; Manual entry embeds the same editor without it, and Dash drops a callback
    with a missing Output in the browser -- Save silently did nothing there. Every id
    the editor's callbacks touch must be in the editor itself or always on the page."""
    from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID

    db_path = _file_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        editor_ids = _component_ids(options.terms_editor(conn))
    finally:
        conn.close()
    always_on_page = {DATA_REVISION_ID, BOOK_REVISION_ID, options.DEFAULT_DATE_PICKER_ID}

    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: str(db_path))
    for fragment in (f"{options.TERMS_STATUS_ID}.children", f"{options.TERMS_PAYOFF_ID}.value"):
        key = next(k for k in app.callback_map if fragment in k)
        spec = app.callback_map[key]
        used = {d["id"] for kind in ("inputs", "state") for d in spec.get(kind, [])}
        used |= {part.split(".")[0] for part in key.strip(".").split("...") if part}
        assert options.COLLAPSED_STORE_ID not in used and options.TABLE_ID not in used
        assert used <= editor_ids | always_on_page, used - editor_ids - always_on_page


def test_terms_editor_save_prices_without_pulling_bloomberg_and_publishes_revisions(tmp_path, ui_app_stub, fake_pricer):
    from ui import revision

    db_path = _file_db(tmp_path)
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    woken = []
    app.bloomberg_feed = types.SimpleNamespace(trigger_now=lambda: woken.append(1))
    options.register_callbacks(app, get_db_path=lambda: str(db_path))
    _spec, save_fn = _callback(app, f"{options.TERMS_STATUS_ID}.children")

    status, dropdown, data_rev, book_rev = save_fn(1, "USDJPY111926P-1", "DIGITAL", "PUT", 152, None, AS_OF)
    assert _terms(db_path) == (152.0, "PUT", "DIGITAL")
    # priced at once from what is on file; 2026-09-21: no Bloomberg pull, that is on request only
    assert fake_pricer.calls == [(AS_OF, "D1")] and woken == []
    assert "Saved USDJPY111926P-1: Digital Put strike 152" in status.children
    assert not any("NO STRIKE" in o["label"] for o in dropdown)      # the list no longer flags it
    assert data_rev == revision.file_signature(str(db_path)) and book_rev == revision.book_signature(str(db_path))

    # Under Options that very revision rebuilds the editor: the rebuilt one still says so.
    conn = sqlite3.connect(str(db_path))
    try:
        rebuilt = json.dumps(options.terms_editor(conn).to_plotly_json(), default=lambda o: o.to_plotly_json())
    finally:
        conn.close()
    assert "Saved USDJPY111926P-1" in rebuilt

    refused = save_fn(2, "USDJPY111926P-1", "DIGITAL", "PUT", None, None, AS_OF)
    assert refused[0].className == "source-result--error" and refused[1:] == (dash.no_update,) * 3
    assert _terms(db_path) == (152.0, "PUT", "DIGITAL")


def test_terms_editor_keeps_the_selected_option_across_a_rebuild():
    conn = _make_db()
    try:
        editor = options.terms_editor(conn)
    finally:
        conn.close()
    found = []

    def _walk(c):
        if getattr(c, "id", None) == options.TERMS_INSTRUMENT_ID:
            found.append(c)
        children = getattr(c, "children", None)
        for child in (children if isinstance(children, (list, tuple)) else [children]):
            if child is not None and not isinstance(child, (str, int, float)):
                _walk(child)
    _walk(editor)
    assert found[0].persistence is True and found[0].persistence_type == "session"


# --------------------------------------------------------------------------- cost, value and P&L per leg

def _book(conn):
    from ui.tabs.blotter_pricing import priced_value_book
    df, _n_fallback, _n_total = priced_value_book(conn, AS_OF)
    return {r["trade_id"]: r for r in df.to_dict("records")}


def test_leg_cost_value_and_pnl_columns():
    conn = _make_db()
    try:
        legs = {l["trade_id"]: l for l in options._leg_rows(conn, AS_OF)}
        bought, sold = legs["O1"], legs["O2"]
        assert (bought["side"], sold["side"]) == ("Buy", "Sell")
        assert bought["premium_paid"] == 0.0050 and bought["premium_ccy"] == "EUR"
        assert bought["start_value"] == pytest.approx(1_000_000.0 * 0.0050)       # sign(side) x |qty| x fill
        assert sold["start_value"] == pytest.approx(-1_000_000.0 * 0.0050)
        assert bought["current_value"] == pytest.approx(1_000_000.0 * 0.0062)
        assert bought["pnl_ccy"] == pytest.approx(1_000_000.0 * (0.0062 - 0.0050))
        assert sold["pnl_ccy"] == pytest.approx(-1_000_000.0 * (0.0048 - 0.0050))
        assert bought["mktpx"] == 0.0062                                           # the current premium
    finally:
        conn.close()


def test_pnl_usd_is_the_books_own_per_trade_figure():
    """The tab can never disagree with the headline: P&L USD is READ from
    `priced_value_book`, trade by trade -- exact equality, not approx."""
    conn = _make_db()
    try:
        book = _book(conn)
        legs = options._leg_rows(conn, AS_OF)
        assert {l["trade_id"] for l in legs} == {"O1", "O2", "O3"}
        for leg in legs:
            assert book[leg["trade_id"]]["reason"] == ""
            assert leg["pnl_usd"] == book[leg["trade_id"]]["pnl_usd"]
            # ... and the two USD value columns bracket it to the cent.
            assert leg["mktval"] - leg["start_value_usd"] == pytest.approx(leg["pnl_usd"], abs=1e-6)

        df = options.option_rows(conn, AS_OF)
        total = df[df["level"] == "TOTAL"].iloc[0]
        assert total["pnl_usd"] == pytest.approx(sum(book[t]["pnl_usd"] for t in ("O1", "O2", "O3")))

        from ui.tabs.blotter_pricing import row_scoped_headline
        strip_ltd = row_scoped_headline(conn, AS_OF, ["O1", "O2", "O3"])["ltd"]["value"]
        headline = options.headline_totals(options.format_rows(df, ["PKG1"])[0])
        assert headline["pnl_usd"] == pytest.approx(strip_ltd)
    finally:
        conn.close()


def test_pnl_usd_blank_with_the_books_reason_when_the_book_cannot_price():
    conn = _make_db()
    try:
        conn.execute("DELETE FROM marks WHERE instrument_id = 'EURUSD092226C-1' AND mark_type = 'PREMIUM'")
        conn.commit()
        book = _book(conn)
        leg = next(l for l in options._leg_rows(conn, AS_OF) if l["trade_id"] == "O1")
        assert leg["pnl_usd"] is None and leg["current_value"] is None and leg["pnl_ccy"] is None
        assert leg["start_value"] == pytest.approx(5_000.0)          # the cost is known regardless
        assert leg["note"] == book["O1"]["reason"] and "no PREMIUM mark" in leg["note"]
    finally:
        conn.close()


def test_group_rows_sum_their_legs_and_count_the_unpriced():
    """`_agg`'s convention: the sum of the PRICED legs, with "n of m priced" said on the
    row; premium-currency sums only where the legs share that currency."""
    conn = _make_db()
    try:
        conn.execute("DELETE FROM marks WHERE instrument_id = 'EURUSD092226P-1' AND source = 'QL_OPTIONS_PRICER'")
        conn.commit()
        df = options.option_rows(conn, AS_OF)
        legs = {l["trade_id"]: l for l in options._leg_rows(conn, AS_OF)}
        pkg = df[df["group_key"] == "PKG1"].iloc[0]
        assert (pkg["priced_count"], pkg["leg_count"]) == (1, 2)
        assert pkg["note"].startswith("1 of 2 priced") and "EURUSD092226P-1" in pkg["note"]
        assert pkg["pnl_usd"] == pytest.approx(legs["O1"]["pnl_usd"])             # priced leg only
        assert pkg["start_value"] == pytest.approx(0.0)                            # +5,000 and -5,000 EUR
        assert pkg["premium_ccy"] == "EUR"

        total = df[df["level"] == "TOTAL"].iloc[0]
        assert total["note"] == "2 of 3 priced"
        assert total["premium_ccy"] == "mixed"                                      # EUR and USD premiums
        assert total["start_value"] is None or math.isnan(total["start_value"])    # ... do not add up
        assert total["start_value_usd"] == pytest.approx(sum(l["start_value_usd"] for l in legs.values()))
    finally:
        conn.close()


# --------------------------------------------------------------------------- headline

def test_headline_totals_are_the_sum_of_the_rows_shown():
    conn = _make_db()
    try:
        df = options.option_rows(conn, AS_OF)
        legs = options._leg_rows(conn, AS_OF)
        collapsed, _ = options.format_rows(df, ["PKG1"])
        expanded, _ = options.format_rows(df, [])
        for records in (collapsed, expanded):                # an expanded package is not counted twice
            totals = options.headline_totals(records)
            for field in ("delta", "gamma", "vega", "theta", "start_priced_usd", "mktval", "pnl_usd"):
                assert totals[field] == pytest.approx(sum(l[field] for l in legs)), field
            assert totals["rho"] == pytest.approx(sum(l["rho"] for l in legs if l["rho"] is not None))
            assert (totals["priced"], totals["options"]) == (3, 3)
        total_row = next(r for r in collapsed if r["level"] == "TOTAL")
        assert options.headline_totals(collapsed)["delta"] == pytest.approx(total_row["delta"])

        flat, _ = options.format_rows(options.option_rows(conn, AS_OF, flat=True))
        eurusd = [r for r in flat if r["underlying"] == "EURUSD"]                  # what a filter leaves
        totals = options.headline_totals(eurusd)
        assert (totals["priced"], totals["options"]) == (2, 2)
        assert totals["pnl_usd"] == pytest.approx(sum(l["pnl_usd"] for l in legs if l["underlying"] == "EURUSD"))
    finally:
        conn.close()


def test_headline_counts_and_names_what_is_not_priced(tmp_path, ui_app_stub):
    db_path = _file_db(tmp_path)
    totals = options.headline_totals(_records(db_path, collapsed=["PKG1"]))
    assert (totals["priced"], totals["options"]) == (2, 3)
    assert len(totals["unpriced"]) == 1 and "USDJPY111926P-1" in totals["unpriced"][0]
    assert "no strike on file" in totals["unpriced"][0]
    # Start value, Current value and P&L cover the SAME (priced) options, so they
    # reconcile: the unpriced ticket's 248,000 premium must not read as a loss. The
    # table's own Start value USD column still shows every ticket's cost.
    assert totals["mktval"] - totals["start_priced_usd"] == pytest.approx(totals["pnl_usd"], abs=1e-6)
    total_row = next(r for r in _records(db_path, collapsed=["PKG1"]) if r["level"] == "TOTAL")
    assert total_row["start_value_usd"] == pytest.approx(total_row["start_priced_usd"] + 2_000_000.0 * 0.124)
    text = json.dumps(options.headline_strip(totals).to_plotly_json(), default=lambda o: o.to_plotly_json())
    assert "2 of 3" in text and "USDJPY111926P-1" in text


def test_headline_strip_says_na_with_a_reason_never_zero():
    conn = _make_db_empty()
    try:
        layout = options.build_layout(conn, AS_OF)
    finally:
        conn.close()
    strip = next(c for c in layout.children if getattr(c, "id", None) == options.HEADLINE_ID)
    text = json.dumps(strip.to_plotly_json(), default=lambda o: o.to_plotly_json())
    assert text.count('"n/a"') == len(options.HEADLINE_FIELDS)
    assert "0 of 0" in text and "No option trades on file." in text
    filtered = json.dumps(options.headline_strip(options.headline_totals([]), filtered=True).to_plotly_json(),
                          default=lambda o: o.to_plotly_json())
    assert "No option matches the filter." in filtered


def test_build_layout_has_the_greeks_headline_with_their_units():
    conn = _make_db()
    try:
        layout = options.build_layout(conn, AS_OF)
    finally:
        conn.close()
    strip = next(c for c in layout.children if getattr(c, "id", None) == options.HEADLINE_ID)
    text = json.dumps(strip.to_plotly_json(), default=lambda o: o.to_plotly_json())
    for title in ("Delta", "Gamma", "Vega", "Theta", "Rho", "Start value", "Current value", "P&L USD",
                  "Options priced"):
        assert f'"{title}"' in text, title
    for unit in ("USD delta notional", "per 1% spot move", "per vol point", "per calendar day", "3 of 3"):
        assert unit in text, unit


def test_headline_callback_follows_the_visible_rows(tmp_path, ui_app_stub):
    db_path = _file_db(tmp_path)
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: str(db_path))
    _spec, headline_fn = _callback(app, f"{options.HEADLINE_ID}.children")

    flat = _records(db_path, filter_query="{underlying} contains EURUSD")
    visible = [r for r in flat if r["underlying"] == "EURUSD"]          # what `derived_virtual_data` holds
    text = json.dumps(headline_fn(visible, "{underlying} contains EURUSD", []).to_plotly_json(),
                      default=lambda o: o.to_plotly_json())
    assert "2 of 2" in text and "rows shown by the filter" in text

    # The instant after a filter is typed the table still holds the GROUPED rows: skipped.
    from dash.exceptions import PreventUpdate
    with pytest.raises(PreventUpdate):
        headline_fn(_records(db_path, collapsed=["PKG1"]), "{underlying} contains EURUSD", [])


# --------------------------------------------------------------------------- column filters

def test_numeric_columns_are_typed_numeric_and_carry_raw_numbers():
    conn = _make_db()
    try:
        table = options.options_table(options.option_rows(conn, AS_OF), [])
    finally:
        conn.close()
    columns = {c["id"]: c for c in table.columns}
    for col in options.NUMERIC_COLUMNS:
        assert columns[col]["type"] == "numeric", col
        assert columns[col]["format"]["specifier"], col                 # display is a Format, not a string
    assert columns["notional"]["format"]["specifier"] == "(,.0f"         # 35,000,000 / (197,575)
    assert columns["strike"]["format"]["specifier"] == ",.6~f"           # 11.0584, 152
    for col in options.TEXT_COLUMNS:
        assert columns[col]["type"] == "text", col
    assert columns["expiry"]["type"] == "text"                           # ISO text: sorts, and >= 2026-11 works
    for rec in table.data:
        for col in options.NUMERIC_COLUMNS:
            assert rec[col] is None or isinstance(rec[col], float), (col, rec[col])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}|", rec["expiry"])
    # Serialisable as is: no NaN sneaks into the payload.
    assert "NaN" not in json.dumps(table.data)


def test_table_uses_native_case_insensitive_filtering_with_hints():
    conn = _make_db()
    try:
        table = options.options_table(options.option_rows(conn, AS_OF), [])
    finally:
        conn.close()
    assert table.filter_action == "native" and table.sort_action == "native"
    assert table.filter_options["case"] == "insensitive"
    for col in table.columns:
        if col["id"] in options.DISPLAY_COLUMNS:
            assert col["filter_options"]["case"] == "insensitive", col["id"]
            assert col["filter_options"]["placeholder_text"], col["id"]
    hints = {c["id"]: c["filter_options"]["placeholder_text"] for c in table.columns}
    assert hints["notional"] == "> 1000000" and hints["expiry"] == ">= 2026-11"
    # What the user filtered / sorted by survives the Blotter's rebuild of the sub-tab.
    assert table.persistence is True and set(table.persisted_props) == {"filter_query", "sort_by"}
    # The hints must be SEEN: style.css paints every `th` (filter cells included) navy and
    # dash-table keeps placeholders transparent until hover -- both overridden for this
    # table through its own `css` prop (real browser, 2026-09-18).
    rules = {c["selector"]: c["rule"] for c in table.css}
    cell = ".dash-spreadsheet-container .dash-spreadsheet-inner th.dash-filter"
    assert "background: var(--card) !important" in rules[cell]
    assert "color: var(--text) !important" in rules[f"{cell} input"]
    assert "opacity: 0.55 !important" in rules[f"{cell} input::placeholder"]


def test_only_the_three_term_cells_are_editable_and_only_on_an_options_own_row():
    conn = _make_db()
    try:
        table = options.options_table(options.option_rows(conn, AS_OF), [])
    finally:
        conn.close()
    assert not table.editable
    assert {c["id"] for c in table.columns if c.get("editable")} == set(options.EDITABLE_COLUMNS)
    columns = {c["id"]: c for c in table.columns}
    assert columns["option_type"]["presentation"] == "dropdown" and columns["payoff"]["presentation"] == "dropdown"
    assert columns["strike"]["type"] == "numeric" and columns["strike"]["on_change"]["action"] == "coerce"
    for rule in table.dropdown_conditional:
        assert rule["if"]["filter_query"] == "{is_leg} = 1"            # no dropdown on a group row
    payoff_words = [o["value"] for o in table.dropdown_conditional[1]["options"]]
    assert "Vanilla" in payoff_words and "Digital" in payoff_words
    fenced = [s for s in table.style_data_conditional
              if s.get("pointerEvents") == "none" and s["if"].get("filter_query") == "{is_leg} = 0"]
    assert {s["if"]["column_id"] for s in fenced} == set(options.EDITABLE_COLUMNS)
    assert {r["group_key"] for r in table.data if r["is_leg"]} == {"O1", "O2", "O3"}


def test_editable_cells_are_cued_by_a_gold_outline_never_a_border():
    """ui/assets/style.css forces `border-color: var(--line) !important` on every table
    cell, so a gold BORDER renders grey and the "you can type here" cue was invisible
    (real browser, 2026-09-18). The cue is an `outline`: quiet (1px dashed) on an option's
    own cells, strongest (2px solid, filled) on the Strike cell of a leg whose strike is
    still missing -- a LATER rule, so it wins -- and absent from every group row."""
    styles = options.table_styles()
    for rule in styles:                                   # nothing in the grid leans on a border colour
        assert not any(key.lower().startswith("border") for key in rule if key != "if"), rule
    # ... nor on a variable dash-table redefines inside the table (its own --muted is
    # #c8c8c8, its --accent hotpink): the Note column's reasons came out near-white.
    conn = _make_db_empty()
    try:
        table = options.options_table(options.option_rows(conn, AS_OF), [])
    finally:
        conn.close()
    painted = json.dumps([styles, table.style_cell_conditional, table.style_cell, table.style_header, table.css])
    assert "var(--muted)" not in painted and "var(--accent)" not in painted
    assert next(s for s in styles if s["if"] == {"column_id": "note"})["color"] == "var(--warn)"

    cued = [s for s in styles if "outline" in s]
    assert cued and all("var(--gold)" in s["outline"] and s["outlineOffset"] for s in cued)
    assert all(s["if"]["filter_query"].startswith("{is_leg} = 1") for s in cued)       # never a group row
    quiet = {s["if"]["column_id"]: s for s in cued if s["if"]["filter_query"] == options.OWN_ROW_QUERY}
    assert set(quiet) == set(options.EDITABLE_COLUMNS)
    assert all(s["outline"] == "1px dashed var(--gold)" for s in quiet.values())

    strong = [s for s in cued if s["if"]["filter_query"] == options.NEEDS_STRIKE_QUERY]
    assert options.NEEDS_STRIKE_QUERY == "{is_leg} = 1 && {note} contains 'no strike'"
    strike = next(s for s in strong if s["if"]["column_id"] == "strike")
    assert strike["outline"] == "2px solid var(--gold)" and "0.30" in strike["backgroundColor"]
    assert next(s for s in strong if s["if"]["column_id"] == "payoff")["outline"] == "2px dashed var(--gold)"
    assert styles.index(strike) > styles.index(quiet["strike"])                         # later rule wins

    # The rows those queries pick out: the no-strike leg gets the strong cue, group rows none.
    conn = _make_db()
    try:
        records, _ = options.format_rows(options.option_rows(conn, AS_OF), [])
    finally:
        conn.close()
    needs = [r["group_key"] for r in records if r["is_leg"] == 1 and "no strike" in r["note"]]
    assert needs == ["O3"]
    assert all(r["is_leg"] == 0 for r in records if r["level"] in ("TOTAL", "ASSET_CLASS") or r["group_key"] == "PKG1")


def test_flat_leg_rows_under_an_active_filter_or_sort():
    """Grouping cannot hide a match: with a filter (or a sort) on, the rows are every
    option on its own row -- collapsed packages included, group rows excluded."""
    conn = _make_db()
    try:
        grouped = options.table_records(conn, AS_OF, ["PKG1"], "", [])
        assert [r["level"] for r in grouped if r["group_key"] in ("O1", "O2")] == []   # hidden in PKG1
        for filter_query, sort_by in (("{notional} > 100", []), ("", [{"column_id": "pnl_usd", "direction": "asc"}])):
            flat = options.table_records(conn, AS_OF, ["PKG1"], filter_query, sort_by)
            assert {r["level"] for r in flat} == {"LEG"}
            assert sorted(r["trade_id"] for r in flat) == ["O1", "O2", "O3"]
            assert all(r["is_leg"] == 1 and r["parent_key"] == "" for r in flat)
            assert {r["label"] for r in flat} == {"EURUSD - Vanilla", "USDJPY - Vanilla"}   # no tree arrows
        legs = {l["trade_id"]: l for l in options._leg_rows(conn, AS_OF)}
        for r in flat:                                                                   # same numbers
            assert r["pnl_usd"] == pytest.approx(legs[r["trade_id"]]["pnl_usd"])
        assert options.view_is_flat("  ", []) is False and options.view_is_flat(None, None) is False
    finally:
        conn.close()


def test_flat_view_of_an_empty_book_is_an_empty_table():
    conn = _make_db_empty()
    try:
        assert options.table_records(conn, AS_OF, [], "{notional} > 1", []) == []
    finally:
        conn.close()


# --------------------------------------------------------------------------- the book's eight sample options

_OPTION_SYMBOL = re.compile(r"^[A-Z]{6}\d{6}[CP]-\d+$")
DIGITALS = {"EURSEK112526C-197906813": ("Call", 11.4), "USDJPY111926P-197957397": ("Put", 152.0),
            "USDJPY111926P-197571137": ("Put", 152.0)}


@pytest.fixture(scope="module")
def sample_options_csv():
    """Header + the eight option rows of the reference blotter export, as bytes."""
    if not SAMPLE_CSV.exists():
        pytest.skip("data/raw/new_sample_trades.csv is not on this machine (data/raw is gitignored)")
    text = SAMPLE_CSV.read_bytes().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    option_rows = [r for r in rows if _OPTION_SYMBOL.match((r.get("Symbol") or "").strip())]
    assert len(option_rows) == 8
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(option_rows)
    return out.getvalue().encode("utf-8"), option_rows


@pytest.fixture
def sample_db(tmp_path, sample_options_csv):
    from data.ingest.upload import import_blotter
    payload, option_rows = sample_options_csv
    db_path = tmp_path / "risk.db"
    import_blotter(payload, "sample_options.csv", str(db_path))
    return db_path, option_rows


def test_sample_start_value_equals_net_invoice(sample_db, ui_app_stub):
    db_path, option_rows = sample_db
    invoice = {r["Trade Id"].strip(): abs(float(r["NetInvoice"].replace(",", ""))) for r in option_rows}
    side = {r["Trade Id"].strip(): r["Side"].strip() for r in option_rows}
    conn = sqlite3.connect(str(db_path))
    try:
        legs = options._leg_rows(conn, "2026-09-18")
    finally:
        conn.close()
    assert len(legs) == 8
    for leg in legs:
        assert abs(leg["start_value"]) == pytest.approx(invoice[leg["trade_id"]], abs=0.005), leg["instrument"]
        assert leg["side"] == side[leg["trade_id"]]
        assert (leg["start_value"] > 0) == (leg["side"] == "Buy")      # a sold option is a negative cost
    sold = next(l for l in legs if l["side"] == "Sell")
    assert sold["instrument"] == "EURSEK092326P-197838147" and sold["start_value"] == pytest.approx(-197_575.0)


def test_the_three_digitals_accept_terms_in_the_table(sample_db, ui_app_stub, fake_pricer):
    """They arrive as VANILLA with no strike (the export marks none of them as digital):
    Payoff, then the strike from the user's old Excel book, typed in the grid."""
    db_path, _option_rows = sample_db
    as_of = "2026-09-18"

    def records():
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            return options.table_records(conn, as_of, [], "", [])
        finally:
            conn.close()

    start = {r["instrument"]: r for r in records() if r["is_leg"]}
    assert len(start) == 8
    for instrument in DIGITALS:                                  # each is its own editable row, flagged
        assert start[instrument]["strike"] is None and "no strike on file" in start[instrument]["note"]

    for instrument, (call_put, strike) in DIGITALS.items():
        for column, value in (("payoff", "Digital"), ("strike", strike)):
            previous = records()
            rows = copy.deepcopy(previous)
            next(r for r in rows if r["instrument"] == instrument)[column] = value
            _data, status = options.handle_table_event(str(db_path), as_of, [], "", [], rows, previous, True)
            assert status.className == "source-result--info", status.children
        assert _terms(db_path, instrument) == (strike, call_put.upper(), "DIGITAL")

    done = {r["instrument"]: r for r in records() if r["is_leg"]}
    for instrument, (call_put, strike) in DIGITALS.items():
        assert (done[instrument]["payoff"], done[instrument]["option_type"], done[instrument]["strike"]) == \
               ("Digital", call_put, strike)
        assert "no strike" not in done[instrument]["note"]
    assert len(fake_pricer.calls) == 3                            # each priced once, as a digital
    # The five vanillas were not touched.
    assert sum(1 for i, r in done.items() if i not in DIGITALS and r["payoff"] == "Vanilla") == 5


def test_the_three_digitals_accept_terms_in_the_editor(sample_db, ui_app_stub, fake_pricer):
    db_path, _option_rows = sample_db
    conn = sqlite3.connect(str(db_path))
    try:
        listed = [i["instrument_id"] for i in options.option_instruments(conn)]
    finally:
        conn.close()
    assert set(listed[:3]) == set(DIGITALS)                       # in the dropdown, first
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    options.register_callbacks(app, get_db_path=lambda: str(db_path))
    _spec, save_fn = _callback(app, f"{options.TERMS_STATUS_ID}.children")
    for n, (instrument, (call_put, strike)) in enumerate(DIGITALS.items(), start=1):
        status = save_fn(n, instrument, "DIGITAL", call_put.upper(), strike, None, "2026-09-18")[0]
        assert status.className == "source-result--info", status.children
        assert _terms(db_path, instrument) == (strike, call_put.upper(), "DIGITAL")


def test_digital_terms_survive_a_re_upload(sample_db, ui_app_stub, fake_pricer, sample_options_csv):
    from data.ingest.upload import import_blotter
    db_path, _option_rows = sample_db
    instrument = "USDJPY111926P-197957397"
    trade_id = "943920760"
    options.save_and_price(str(db_path), "2026-09-18", trade_id, instrument,
                           {"strike": 152.0, "option_type": "PUT", "payoff": "DIGITAL", "barrier_level": 0.0})
    import_blotter(sample_options_csv[0], "sample_options.csv", str(db_path))
    assert _terms(db_path, instrument) == (152.0, "PUT", "DIGITAL")


def test_four_portfolio_tables_sit_above_the_table_and_add_up_in_usd():
    """User, 2026-09-21: by pair (risk), expiry ladder (decay), by structure (what is working)
    and spot against strike (what is in play), above the options table, all in dollars."""
    conn = _make_db()
    try:
        legs = options.option_rows(conn, AS_OF, flat=True)
        priced = legs[legs["pnl_usd"].notna()]
        for keys in (legs["underlying"], legs["expiry"].map(lambda e: options.expiry_bucket(e, AS_OF)),
                     options.structure_names(conn, AS_OF, legs)):
            rows = options.grouped_rows(legs, keys)
            total = rows[-1]
            assert total["group"] == "Total" and total["options"] == len(legs)
            assert sum(r["options"] for r in rows[:-1]) == len(legs)
            if len(priced):
                assert total["pnl"] == pytest.approx(priced["pnl_usd"].sum())
                assert total["value"] - total["paid"] == pytest.approx(total["pnl"])   # current value - premium paid
                assert sum(r["pnl"] or 0.0 for r in rows[:-1]) == pytest.approx(total["pnl"])
        assert options.expiry_bucket("2026-09-23", "2026-09-21") == "This week"
        assert options.expiry_bucket("2026-11-19", "2026-09-21") == "1 to 3 months"
        assert options.expiry_bucket("2026-09-18", "2026-09-21") == "Expired"
        for row in options.in_play_rows(conn, AS_OF, legs):
            if row["away_pct"] is not None:
                assert row["away_pct"] == pytest.approx((row["strike"] - row["spot"]) / row["spot"] * 100.0)
        layout = options.build_layout(conn, AS_OF)
        ids = [getattr(c, "id", None) for c in layout.children]
        assert ids.index(options.BREAKDOWNS_ID) < ids.index(options.TABLE_ID)
        assert len(layout.children[ids.index(options.BREAKDOWNS_ID)].children) == 4
    finally:
        conn.close()
