"""Workbook row arithmetic and database mark-selection regressions."""
from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data.bloomberg.bnp_marks import load_bnp_marks
from data.bloomberg.marks_csv import MarkRow
from data.ingest import bnp, schema
from engine.pnl.pnl import ltd_per_trade, workbook_valuation_date, workbook_fx_pnl
from engine.pnl.aggregate import aggregate_by_pair, book_totals, period_pnl

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "HA_PNL_20260818.csv"
AS_OF = "2026-08-17"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason=f"raw file absent: {RAW}")

OFFICIAL_FX_SOURCE = "BBG_BFXFORWARD"
SNAPPED_AT = "2026-08-17T15:00:00-04:00"


# --------------------------------------------------------------------- synthetic setup
def _make_conn():
    conn = schema.connect(":memory:")
    conn.executemany(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        [
            ("USDJPY", "FX", "USD", "JPY", 1.0, 0, "USDJPY Curncy", "9999-12-31"),
            ("AUDUSD", "FX", "AUD", "USD", 1.0, 0, "AUDUSD Curncy", "9999-12-31"),
        ],
    )
    return conn


def _insert_trade(conn, trade_id, instrument_id, quantity, price, settle_date, trade_date="2026-08-01"):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, "FX_FWD", trade_id, trade_date, quantity, price,
         "ACC", "CPTY", "STRAT", "TRADER", "test trade"),
    )
    base_ccy = instrument_id[:3]
    quote_ccy = instrument_id[3:]
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 1, "FX_NEAR", base_ccy, quantity, trade_date, settle_date, price, 1),
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 2, "FX_NEAR", quote_ccy, -quantity * price, trade_date, settle_date, price, 1),
    )
    conn.commit()


def _insert_mark(conn, instrument_id, settle_date, mark_type, value, source=OFFICIAL_FX_SOURCE, as_of=AS_OF):
    conn.execute(
        "INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
        (as_of, instrument_id, settle_date, mark_type, value, source, SNAPPED_AT),
    )
    conn.commit()



def _rate(conn, pair, value, as_of=AS_OF, maturity="2026-08-24"):
    _insert_mark(conn, pair, maturity, "FWD_OUTRIGHT", value, as_of=as_of)


@pytest.mark.parametrize("as_of,expected", [("2026-08-17", "2026-08-24"),
    ("2026-09-11", "2026-09-18"), ("2026-09-12", "2026-09-18")])
def test_workday_literal(as_of, expected):
    assert workbook_valuation_date(as_of) == expected


@pytest.mark.parametrize("pair,c,fill,mark,expected", [
    ("USDJPY", 1_000_000, 147, 148, 1_000_000 / 148),
    ("USDJPY", -500_000, 147, 148, -500_000 / 148),
    ("AUDUSD", 1_300_000, .65, .66, 20_000),
    ("AUDUSD", -650_000, .65, .66, -10_000),
])
def test_literal_row_formula(pair, c, fill, mark, expected):
    assert workbook_fx_pnl(pair, c, fill, mark) == pytest.approx(expected)


def test_t2_uses_f_not_j_denominator():
    assert workbook_fx_pnl("USDJPY", 1_000_000, 147, 149, 148) == pytest.approx(2_000_000 / 148)
    assert workbook_fx_pnl("AUDUSD", 650_000, .65, .67, .66) == pytest.approx(20_000)


def test_shared_maturity_and_conversion_ignore_trade_maturity_and_spot():
    conn = _make_conn()
    for name, settle in [("PAST", "2026-08-10"), ("TODAY", AS_OF), ("FUTURE", "2026-09-01")]:
        _insert_trade(conn, name, "USDJPY", 1_000_000, 147, settle)
    _rate(conn, "USDJPY", 148)
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 999)
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 999)
    out = ltd_per_trade(conn, AS_OF)
    assert set(out.trade_id) == {"PAST", "TODAY", "FUTURE"}
    assert (out.mark == 148).all()
    assert (out.valuation_date == "2026-08-24").all()
    assert out.pnl_usd.tolist() == pytest.approx([1_000_000 / 148] * 3)
    assert ltd_per_trade(conn, AS_OF, include_settled=False).trade_id.tolist() == ["FUTURE"]


def test_usd_notional_comes_from_actual_usd_leg():
    conn = _make_conn()
    _insert_trade(conn, "AUD", "AUDUSD", 2_000_000, .65, "2026-09-01")
    _rate(conn, "AUDUSD", .66)
    row = ltd_per_trade(conn, AS_OF).iloc[0]
    assert row.workbook_quantity == 1_300_000
    assert row.usd_entry == -1_300_000
    assert row.denominator == .65
    assert row.pnl_usd == pytest.approx(20_000)


def test_missing_shared_rate_never_uses_pb_or_trade_settlement_rate():
    conn = _make_conn()
    _insert_trade(conn, "MISSING", "USDJPY", 1_000_000, 147, "2026-09-01")
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148)
    _insert_mark(conn, "USDJPY", "2026-08-24", "FWD_OUTRIGHT", 149, source="BNP_BVAL")
    out = ltd_per_trade(conn, AS_OF, strict=False)
    assert out.pnl_usd.isna().all()
    with pytest.raises(ValueError, match="MISSING"):
        ltd_per_trade(conn, AS_OF)


@pytest.mark.parametrize("rate", [0, -1, float("nan")])
def test_invalid_rates_are_unavailable(rate):
    assert math.isnan(workbook_fx_pnl("USDJPY", 1_000_000, 147, rate))


def test_explicit_sources_do_not_duplicate_or_fallback():
    conn = _make_conn()
    _insert_trade(conn, "X", "USDJPY", 1_000_000, 147, "2026-09-01")
    _rate(conn, "USDJPY", 148)
    _insert_mark(conn, "USDJPY", "2026-08-24", "FWD_OUTRIGHT", 149, source="WORKBOOK_REFERENCE")
    official = ltd_per_trade(conn, AS_OF)
    reference = ltd_per_trade(conn, AS_OF, source="WORKBOOK_REFERENCE")
    assert len(official) == len(reference) == 1
    assert official.iloc[0].mark == 148
    assert reference.iloc[0].mark == 149


def test_missing_trade_poison_pair_aggregate_instead_of_zero():
    conn = _make_conn()
    _insert_trade(conn, "MISSING", "USDJPY", 1_000_000, 147, "2026-09-01")
    by_pair = aggregate_by_pair(ltd_per_trade(conn, AS_OF, strict=False), conn)
    assert math.isnan(by_pair.iloc[0].ltd_usd)
    assert by_pair.iloc[0].usd_notional == 1_000_000


def test_aggregate_empty():
    conn = _make_conn()
    assert aggregate_by_pair(ltd_per_trade(conn, AS_OF), conn).empty


def test_period_marks_use_current_shared_maturity_and_t2_f_denominator():
    conn = _make_conn()
    _insert_trade(conn, "OLD", "USDJPY", 1_000_000, 147, "2026-08-01", trade_date="2026-07-01")
    _insert_trade(conn, "NEW", "USDJPY", 500_000, 147, "2026-09-01", trade_date=AS_OF)
    _rate(conn, "USDJPY", 150)
    _rate(conn, "USDJPY", 149, as_of="2026-08-14")
    _rate(conn, "USDJPY", 148, as_of="2026-08-13")
    result = period_pnl(conn, AS_OF)
    assert result["ltd"] == pytest.approx(1_500_000 * 3 / 150)
    assert result["daily"] == pytest.approx(result["ltd"] - 1_000_000 * 2 / 149)
    assert result["previous_daily"] == pytest.approx(1_000_000 / 149)
    assert result["trading"] == pytest.approx(500_000 * 3 / 150)
    assert all(math.isnan(result[k]) for k in ("d5", "mtd", "ytd"))


def test_brl_previous_rate_reuses_current_workbook_cell():
    conn = _make_conn()
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("USDBRL", "FX", "USD", "BRL", 1, 0, "USDBRL Curncy", "9999-12-31"))
    _insert_trade(conn, "BRL", "USDBRL", 1_000_000, 5, "2026-09-01")
    _rate(conn, "USDBRL", 6)
    _rate(conn, "USDBRL", 99, as_of="2026-08-14")
    _rate(conn, "USDBRL", 4, as_of="2026-08-13")
    out = period_pnl(conn, AS_OF)
    assert out["daily"] == 0
    assert out["previous_daily"] == pytest.approx(2_000_000 / 6)


@needs_raw
def test_actual_pb_rates_are_not_workbook_shared_rates():
    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=False)
    out = ltd_per_trade(conn, AS_OF, source="BNP_BVAL", strict=False)
    assert len(out) == 229
    assert out.pnl_usd.isna().all()


def test_no_trades_period_unavailable():
    result = period_pnl(_make_conn(), AS_OF)
    assert math.isnan(result["ltd"])
    assert math.isnan(result["daily"])

# ------------------------------------------------------------------ book_totals
def test_book_totals_net_and_gross_exclude_gold_and_futures():
    conn = schema.connect(":memory:")
    conn.executemany(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        [
            ("USDJPY", "FX", "USD", "JPY", 1.0, 0, "USDJPY Curncy", "9999-12-31"),
            ("AUDUSD", "FX", "AUD", "USD", 1.0, 0, "AUDUSD Curncy", "9999-12-31"),
            ("XAUUSD", "FX", "XAU", "USD", 1.0, 0, "XAUUSD Curncy", "9999-12-31"),
            ("ESU6 Index", "FUTURE", "ES", "USD", 50.0, 0, "ESU6 Index", "2026-09-18"),
        ],
    )
    conn.commit()
    by_pair = pd.DataFrame(
        {
            "instrument_id": ["USDJPY", "AUDUSD", "XAUUSD", "ESU6 Index"],
            "usd_notional": [1_000_000.0, -2_000_000.0, 500_000.0, 250_000.0],
            "ltd_usd": [0.0, 0.0, 0.0, 0.0],
            "n_trades": [1, 1, 1, 1],
        }
    )

    totals = book_totals(by_pair, conn)

    assert math.isnan(totals["net_usd"])
    assert math.isnan(totals["gross_usd"])
    assert "manual option" in totals["status"]


def test_book_totals_empty_input():
    totals = book_totals(pd.DataFrame(), _make_conn())
    assert math.isnan(totals["net_usd"])
    assert "unavailable" in totals["status"]
