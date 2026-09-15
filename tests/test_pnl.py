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
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, "FX_FWD", trade_id, trade_date, quantity, price,
         "ACC", "CPTY", "STRAT", "TRADER", "test trade", ""),
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


# =========================================================================================
# engine/pnl/valuation.py -- docs/BUILD_PLAN.md section 2 worked examples
# =========================================================================================
from engine.pnl import stress
from engine.pnl.valuation import value_book

VB_AS_OF = "2026-06-01"
VB_SETTLE = "2026-06-20"


def _vb_conn():
    conn = schema.connect(":memory:")
    conn.executemany(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        [
            ("EURUSD", "FX", "EUR", "USD", 1.0, 0, "EURUSD Curncy", "9999-12-31"),
            ("USDJPY", "FX", "USD", "JPY", 1.0, 0, "USDJPY Curncy", "9999-12-31"),
            ("EURSEK", "FX", "EUR", "SEK", 1.0, 0, "EURSEK Curncy", "9999-12-31"),
            ("USDSEK", "FX", "USD", "SEK", 1.0, 0, "USDSEK Curncy", "9999-12-31"),
            ("ESU6 Index", "FUTURE", "ES", "USD", 50.0, 0, "ESU6 Index", "2026-09-18"),
        ],
    )
    conn.commit()
    return conn


def _vb_fx_trade(conn, trade_id, instrument_id, base_ccy, quote_ccy, quantity, fill, settle=VB_SETTLE):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, "FX_FWD", trade_id, "2026-05-01", quantity, fill,
         "ACC", "CPTY", "STRAT", "TRADER", "test", ""),
    )
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "FX_NEAR", base_ccy, quantity, "2026-05-01", settle, fill, 1))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 2, "FX_NEAR", quote_ccy, -quantity * fill, "2026-05-01", settle, fill, 1))
    conn.commit()


def _vb_mark(conn, instrument_id, settle_date, mark_type, value, as_of=VB_AS_OF):
    source = "BBG_BDH" if mark_type == "FUTURE_PX" else "BBG_BFXFORWARD"
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (as_of, instrument_id, settle_date, mark_type, value, source, f"{as_of}T15:00:00-04:00"))
    conn.commit()


def test_value_book_eur_usd_forward():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T1", "EURUSD", "EUR", "USD", 1_000_000, 1.1000)
    _vb_mark(conn, "EURUSD", VB_SETTLE, "FWD_OUTRIGHT", 1.1080)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["pnl_usd"] == pytest.approx(8_000)


def test_value_book_usd_jpy_forward():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T2", "USDJPY", "USD", "JPY", 1_000_000, 150.00)
    _vb_mark(conn, "USDJPY", VB_SETTLE, "FWD_OUTRIGHT", 148.00)
    _vb_mark(conn, "USDJPY", VB_AS_OF, "SPOT", 149.00)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["pnl_local"] == pytest.approx(-2_000_000)
    assert row["pnl_usd"] == pytest.approx(-2_000_000 / 149, rel=1e-6)


def test_value_book_eur_sek_cross_no_invented_usd_leg():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T3", "EURSEK", "EUR", "SEK", 1_000_000, 11.00)
    _vb_mark(conn, "EURSEK", VB_SETTLE, "FWD_OUTRIGHT", 11.20)
    _vb_mark(conn, "USDSEK", VB_AS_OF, "SPOT", 10.50)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["pnl_local"] == pytest.approx(200_000)
    assert row["pnl_usd"] == pytest.approx(200_000 / 10.5, rel=1e-6)
    assert row["spot_source"] != ""


def test_value_book_future():
    conn = _vb_conn()
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("T4", "MANUAL", "ESU6 Index", "FUTURE", "T4", "2026-05-01", 6, 7528.25,
         "ACC", "CPTY", "STRAT", "TRADER", "test", ""),
    )
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("T4", 1, "NOTIONAL", "USD", 6 * 50 * 7528.25, "2026-05-01", "2026-09-18", 7528.25, 0))
    conn.commit()
    _vb_mark(conn, "ESU6 Index", "2026-09-18", "FUTURE_PX", 7598.50, as_of=VB_AS_OF)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["pnl_usd"] == pytest.approx(21_075)


def test_value_book_missing_mark_is_nan_with_reason():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T5", "EURUSD", "EUR", "USD", 1_000_000, 1.1000)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"])
    assert "FWD_OUTRIGHT" in row["reason"]


def test_value_book_fallback_source_uses_latest_mark_on_or_before():
    """Official marks must be dated as_of; an explicit fallback source (BNP file) may be
    stale: the latest on or before as_of is used, labelled with that source."""
    conn = _vb_conn()
    _vb_fx_trade(conn, "T5b", "USDJPY", "USD", "JPY", 1_000_000, 150.0)
    stale = "2026-05-29"
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (stale, "USDJPY", VB_SETTLE, "FWD_OUTRIGHT", 148.0, "BNP_BVAL", "t"))
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (stale, "USDJPY", stale, "SPOT", 149.0, "BNP_BVAL", "t"))
    assert math.isnan(value_book(conn, VB_AS_OF).iloc[0]["pnl_usd"])
    row = value_book(conn, VB_AS_OF, marks_source="BNP_BVAL").iloc[0]
    assert row["mark_source"] == "BNP_BVAL"
    assert abs(row["pnl_usd"] - (-2_000_000 / 149.0)) < 1e-6


def test_value_book_settled_unrealised_is_provisional_only_with_fallback_source():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T6b", "EURUSD", "EUR", "USD", 1_000_000, 1.1000, settle="2026-05-15")
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", ("2026-05-14", "EURUSD", "2026-05-15", "FWD_OUTRIGHT", 1.1150, "BNP_BVAL", "t"))
    official = value_book(conn, VB_AS_OF).iloc[0]
    assert official["status"] == "SETTLED" and math.isnan(official["pnl_usd"]) and "not yet realised" in official["reason"]
    prov = value_book(conn, VB_AS_OF, marks_source="BNP_BVAL").iloc[0]
    assert prov["pnl_usd"] == pytest.approx(1_000_000 * 0.0150) and prov["reason"] == ""
    assert "provisional" in prov["note"]


def test_value_book_settled_row_frozen_from_realised_pnl():
    from engine.pnl import ledger
    conn = _vb_conn()
    _vb_fx_trade(conn, "T6", "EURUSD", "EUR", "USD", 1_000_000, 1.1000, settle="2026-05-15")
    _vb_mark(conn, "EURUSD", "2026-05-15", "SPOT", 1.1200, as_of="2026-05-15")
    ledger.realise_settled(conn, VB_AS_OF)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["status"] == "SETTLED"
    assert row["pnl_usd"] == pytest.approx(1_000_000 * (1.1200 - 1.1000))


def test_value_book_settled_but_not_yet_realised_is_unavailable():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T7", "EURUSD", "EUR", "USD", 1_000_000, 1.1000, settle="2026-05-15")
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["status"] == "SETTLED"
    assert math.isnan(row["pnl_usd"])
    assert "T7" in row["reason"]


# --------------------------------------------------------------------------- stress
def test_stress_move_1pct_and_scenario_arithmetic():
    delta = {"BRL": -1_000_000.0, "EUR": 2_000_000.0}
    moves = stress.move_1pct(delta)
    assert moves["BRL"] == pytest.approx(-10_000.0)
    result = stress.apply_scenario(delta, {"BRL": -0.10}, futures_usd_delta=500_000.0, futures_pct=0.02)
    assert result["fx_total"] == pytest.approx(100_000.0)
    assert result["futures_pnl"] == pytest.approx(10_000.0)
    assert result["total"] == pytest.approx(110_000.0)


def test_stress_scenario_missing_currency_contributes_zero():
    result = stress.apply_scenario({"EUR": 1_000_000.0}, {"TRY": -0.10})
    assert result["fx_pnl"]["TRY"] == 0.0
