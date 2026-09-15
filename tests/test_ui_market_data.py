"""Tests for ui/tabs/market_data.py (C3 Market data tab, pair-organised rewrite)."""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from ui.tabs import market_data as md


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE instruments (instrument_id TEXT PRIMARY KEY, asset_class TEXT, base_ccy TEXT,
                                   quote_ccy TEXT, multiplier REAL, is_ndf INTEGER, bbg_ticker TEXT,
                                   expiry_date TEXT);
        CREATE TABLE trades (trade_id TEXT PRIMARY KEY, source TEXT, instrument_id TEXT, product TEXT,
                              package_id TEXT, trade_date TEXT, quantity REAL, price REAL, account TEXT,
                              counterparty TEXT, strategy TEXT, trader TEXT, description TEXT);
        CREATE TABLE trade_legs (trade_id TEXT, leg_no INTEGER, leg_type TEXT, ccy TEXT, amount REAL,
                                  start_date TEXT, settle_date TEXT, rate REAL, settles_cash INTEGER);
        CREATE TABLE marks (as_of_date TEXT, instrument_id TEXT, settle_date TEXT, mark_type TEXT,
                             value REAL, source TEXT, snapped_at TEXT,
                             PRIMARY KEY (as_of_date, instrument_id, settle_date, mark_type, source));
        CREATE VIEW marks_official AS
            SELECT * FROM marks WHERE
            (mark_type IN ('SPOT','FWD_OUTRIGHT') AND source='BBG_BFXFORWARD') OR
            (mark_type='FUTURE_PX' AND source='BBG_BDH') OR
            (mark_type IN ('PAR_RATE','PV_USD','DV01_USD') AND source='BBG_BDH') OR
            (mark_type IN ('DELTA','PREMIUM') AND source='MANUAL');
        """
    )
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("EURUSD", "FX", "EUR", "USD", 1, 0, "EURUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
    ])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("T1", "BNP", "EURUSD", "FX_FWD", "T1", "2026-08-01", 1_000_000, 1.10,
         "ACC", "CP", "HAHY7", "trader", "desc"),
        ("T2", "BNP", "EURUSD", "FX_FWD", "T2", "2026-07-01", 500_000, 1.08,
         "ACC", "CP", "HAHY7", "trader", "desc"),
        ("T3", "BNP", "USDJPY", "FX_FWD", "T3", "2026-08-01", 1_000_000, 150.0,
         "ACC", "CP", "HAHY7", "trader", "desc"),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("T1", 1, "FX_NEAR", "EUR", 1_000_000, "2026-08-01", "2026-09-18", 1.10, 1),
        ("T1", 2, "FX_NEAR", "USD", -1_100_000, "2026-08-01", "2026-09-18", 1.10, 1),
        ("T2", 1, "FX_NEAR", "EUR", 500_000, "2026-07-01", "2026-10-19", 1.08, 1),
        ("T2", 2, "FX_NEAR", "USD", -540_000, "2026-07-01", "2026-10-19", 1.08, 1),
        ("T3", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-01", "2026-09-18", 150.0, 1),
        ("T3", 2, "FX_NEAR", "JPY", -150_000_000, "2026-08-01", "2026-09-18", 150.0, 1),
    ])
    as_of = "2026-08-18"
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (as_of, "EURUSD", as_of, "SPOT", 1.1050, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "EURUSD", "2026-09-18", "FWD_OUTRIGHT", 1.1080, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "EURUSD", "2026-10-19", "FWD_OUTRIGHT", 1.1120, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "EURUSD", "2026-11-05", "FWD_OUTRIGHT", 1.1150, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "USDJPY", as_of, "SPOT", 149.00, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "USDJPY", "2026-09-18", "FWD_OUTRIGHT", 148.00, "BNP_BVAL", as_of + "T15:00:00-04:00"),
    ])
    conn.commit()
    return conn


AS_OF = "2026-08-18"


def test_pair_options_lists_fx_pairs_and_defaults_to_most_open_trades():
    conn = _db()
    options, default = md.pair_options(conn, AS_OF)
    values = {o["value"] for o in options}
    assert values == {"EURUSD", "USDJPY"}
    # EURUSD has two open trades (T1, T2) vs USDJPY's one -> default EURUSD.
    assert default == "EURUSD"


def test_curve_table_rows_and_tenor_labels():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "EURUSD")
    assert list(df["settle_date"]) == ["2026-09-18", "2026-10-19", "2026-11-05"]
    tenors = dict(zip(df["settle_date"], df["tenor"]))
    assert tenors["2026-09-18"] == "1M"
    assert tenors["2026-10-19"] == "2M"
    assert tenors["2026-11-05"] == "broken"
    # BNP_BVAL is never official -> "reconciliation only" status, source label "BNP file".
    assert set(df["status"]) == {"reconciliation only"}
    assert set(df["source"]) == {"BNP file"}


def test_forward_points_non_jpy_pair_four_decimals():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "EURUSD")
    row = df[df["settle_date"] == "2026-09-18"].iloc[0]
    assert row["points"] == pytest.approx(round(1.1080 - 1.1050, 4))


def test_forward_points_jpy_pair_two_decimals():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "USDJPY")
    row = df[df["settle_date"] == "2026-09-18"].iloc[0]
    assert row["points"] == pytest.approx(round(148.00 - 149.00, 2))
    assert md.decimals_for_pair("USDJPY") == 2
    assert md.decimals_for_pair("EURUSD") == 4


def test_used_by_book_marker_counts_open_trades():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "EURUSD")
    used = dict(zip(df["settle_date"], df["used_by_book"]))
    assert used["2026-09-18"] == 1  # T1
    assert used["2026-10-19"] == 1  # T2
    assert used["2026-11-05"] == ""  # no trade settles here


def test_forward_curve_empty_state():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "GBPUSD")
    assert df.empty
    body = md.pair_body(conn, AS_OF, "GBPUSD")
    rendered = str(body)
    assert "No spot mark for GBPUSD" in rendered
    assert "No forward marks for GBPUSD on 2026-08-18" in rendered


def test_pair_body_renders_chart_when_curve_present():
    conn = _db()
    body = md.pair_body(conn, AS_OF, "EURUSD")
    rendered = str(body)
    assert md.CURVE_TABLE_ID in rendered
    assert md.CURVE_CHART_ID in rendered


def test_manual_form_prefill():
    form = md.manual_entry_form(default_pair="EURUSD")
    rendered = str(form)
    assert "EURUSD" in rendered
    assert md.MANUAL_INSTRUMENT_ID in rendered


def test_build_layout_has_expected_ids():
    layout = md.build_layout(default_date="2026-08-18")
    rendered = str(layout)
    for expected_id in (md.DATE_PICKER_ID, md.PAIR_DROPDOWN_ID, md.STATUS_ID, md.BODY_ID, md.PULL_NOW_ID,
                        md.PULL_NOW_STATUS_ID, md.PULL_REVISION_ID, md.REFRESH_ID, md.MANUAL_INSTRUMENT_ID):
        assert expected_id in rendered


def test_feed_headline_and_backfill_headline_and_top_bar_status():
    assert md.feed_headline(None) == "Bloomberg: no pull recorded yet"
    assert "not connected" in md.feed_headline({"connected": False, "reason": "no port"})
    text = md.feed_headline({"connected": True, "time": "t", "written": 3, "failed": 1})
    assert "3 marks written, 1 failed" in text
    status = {"connected": True, "time": "t", "written": 1, "failed": 0,
              "backfill": {"running": True, "remaining": 4}}
    assert "Backfill: 4 day(s) remaining" in md.top_bar_status(status)
    assert md.backfill_headline(None) is None


def test_tenor_label_boundaries():
    assert md.tenor_label("2026-08-18", "2026-08-25") == "1W"
    assert md.tenor_label("2026-08-18", "2027-08-18") == "1Y"
    assert md.tenor_label("2026-08-18", "2026-08-20") == "broken"
