"""Tests for ui/tabs/rates.py (the Blotter "Rates" sub-tab). Owned by ui-shell.

Builds a tiny synthetic DB via data.ingest.schema with an IRS trade + QL_PRICER marks,
mirroring the shape engine/rates/store.py actually writes (one instrument per swap,
settle_date = maturity, source='QL_PRICER'), plus an optional reconciliation-only
BBG_BDH mark straight in `marks` (never `marks_official`, since BBG_BDH is not
official for PAR_RATE/PV_USD/DV01_USD any more -- data/ingest/schema.py
OFFICIAL_MARK_SOURCE).
"""
from __future__ import annotations

import sqlite3

import dash

from data.ingest import schema
from ui.tabs import rates


def _make_db_with_irs(pay_fixed=True, with_bbg=True, bbg_value=1_050_000.0):
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO instruments VALUES "
        "('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')"
    )
    quantity = 10_000_000.0 if pay_fixed else -10_000_000.0
    conn.execute(
        "INSERT INTO trades VALUES ('T1','XLSX','IRSOIS-USD-1','IRS','T1','2026-06-01',"
        f"{quantity},0.04,'ACC','CPTY','HAHY7','TR','irs swap','')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',1,'FIXED','USD',-1,'2026-06-01','2031-06-01',0.04,0)"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',2,'FLOAT','USD',1,'2026-06-01','2031-06-01',0.0,0)"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','PAR_RATE',0.0398,"
        "'QL_PRICER','2026-06-20T17:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','PV_USD',1000000.0,"
        "'QL_PRICER','2026-06-20T17:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','DV01_USD',-4200.0,"
        "'QL_PRICER','2026-06-20T17:00:00-04:00')"
    )
    if with_bbg:
        conn.execute(
            "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','PV_USD',"
            f"{bbg_value},'BBG_BDH','2026-06-20T17:00:00-04:00')"
        )
    conn.commit()
    return conn


def _make_db_no_irs():
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    return conn


# --------------------------------------------------------------------------- irs_rows

def test_irs_rows_renders_trade_and_official_marks():
    conn = _make_db_with_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["trade_id"] == "T1"
        assert row["instrument_id"] == "IRSOIS-USD-1"
        assert row["ccy"] == "USD"
        assert row["notional"] == 10_000_000.0
        assert row["par_rate"] == 0.0398
        assert row["pv_usd"] == 1000000.0
        assert row["dv01_usd"] == -4200.0
    finally:
        conn.close()


def test_irs_rows_direction_pay_fixed_when_quantity_positive():
    conn = _make_db_with_irs(pay_fixed=True)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["direction"] == "Pay fixed"
    finally:
        conn.close()


def test_irs_rows_direction_receive_fixed_when_quantity_negative():
    conn = _make_db_with_irs(pay_fixed=False)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["direction"] == "Receive fixed"
        assert df.iloc[0]["notional"] == 10_000_000.0  # unsigned
    finally:
        conn.close()


def test_irs_rows_empty_when_no_irs_trades():
    conn = _make_db_no_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.empty
    finally:
        conn.close()


def test_irs_rows_missing_marks_does_not_crash():
    """No marks at all for the trade/date -- mark columns stay NaN, row still renders."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('T1','XLSX','IRSOIS-USD-1','IRS','T1','2026-06-01',"
        "10000000,0.04,'ACC','CPTY','HAHY7','TR','irs swap','')"
    )
    conn.commit()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["par_rate"] != row["par_rate"]  # NaN
        assert row["pv_usd"] != row["pv_usd"]
        assert row["recon_status"] == "MISSING"
    finally:
        conn.close()


# --------------------------------------------------------------------------- recon_status

def test_recon_status_ok_within_tolerance():
    assert rates.recon_status(1_000_000.0, 1_000_500.0) == "OK"


def test_recon_status_warn_outside_tolerance():
    assert rates.recon_status(1_000_000.0, 1_100_000.0) == "WARN"


def test_recon_status_missing_when_no_bbg_mark():
    assert rates.recon_status(1_000_000.0, None) == "MISSING"


def test_recon_status_missing_when_no_official_mark():
    import math
    assert rates.recon_status(float("nan"), 1_000_000.0) == "MISSING"
    assert rates.recon_status(math.nan, 1_000_000.0) == "MISSING"


def test_irs_rows_recon_status_end_to_end_ok():
    conn = _make_db_with_irs(with_bbg=True, bbg_value=1_000_500.0)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["recon_status"] == "OK"
    finally:
        conn.close()


def test_irs_rows_recon_status_end_to_end_warn():
    conn = _make_db_with_irs(with_bbg=True, bbg_value=1_200_000.0)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["recon_status"] == "WARN"
    finally:
        conn.close()


def test_irs_rows_recon_status_end_to_end_missing():
    conn = _make_db_with_irs(with_bbg=False)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["recon_status"] == "MISSING"
    finally:
        conn.close()


# --------------------------------------------------------------------------- formatting / table

def test_format_rows_formats_rate_and_usd_columns():
    conn = _make_db_with_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        records, _style = rates.format_rows(df)
        row = records[0]
        assert row["par_rate"] == "3.9800%"
        assert row["pv_usd"] == "1,000,000"
        assert row["notional"] == "10,000,000"
    finally:
        conn.close()


def test_format_rows_missing_marks_show_na():
    conn = _make_db_with_irs(with_bbg=False)
    try:
        conn.execute("DELETE FROM marks")
        df = rates.irs_rows(conn, "2026-06-20")
        records, _style = rates.format_rows(df)
        assert records[0]["par_rate"] == "n/a"
        assert records[0]["pv_usd"] == "n/a"
        assert records[0]["dv01_usd"] == "n/a"
    finally:
        conn.close()


def test_rates_table_has_recon_status_column_and_conditional_styling():
    conn = _make_db_with_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        table = rates.rates_table(df)
        ids = {c["id"] for c in table.columns}
        assert "recon_status" in ids
        assert "direction" in ids
        colours = {s["color"] for s in table.style_data_conditional if "color" in s}
        assert "var(--pos)" in colours
        assert "var(--neg)" in colours
    finally:
        conn.close()


def test_rates_table_empty_frame_still_renders_columns():
    import pandas as pd
    empty = pd.DataFrame(columns=rates._DISPLAY_COLUMNS)
    table = rates.rates_table(empty)
    assert table.data == []
    assert len(table.columns) == len(rates._DISPLAY_COLUMNS)


# --------------------------------------------------------------------------- build_layout

def test_build_layout_renders_trade_rows():
    conn = _make_db_with_irs()
    try:
        layout = rates.build_layout(conn, "2026-06-20")
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert len(table.data) == 1
        assert table.data[0]["trade_id"] == "T1"
    finally:
        conn.close()


def test_build_layout_no_irs_trades_shows_message_and_empty_table():
    conn = _make_db_no_irs()
    try:
        layout = rates.build_layout(conn, "2026-06-20")
        text = layout.children[0].children
        assert "No IRS trades on file" in text
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert table.data == []
    finally:
        conn.close()


def test_build_layout_missing_mark_does_not_crash():
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('T1','XLSX','IRSOIS-USD-1','IRS','T1','2026-06-01',"
        "10000000,0.04,'ACC','CPTY','HAHY7','TR','irs swap','')"
    )
    conn.commit()
    try:
        layout = rates.build_layout(conn, "2026-06-20")
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert table.data[0]["par_rate"] == "n/a"
    finally:
        conn.close()
