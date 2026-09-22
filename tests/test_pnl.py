"""Database mark-selection and live P&L (value_book) regressions.

The retired workbook row arithmetic (`engine/pnl/pnl.py`: `ltd_per_trade`,
`workbook_valuation_date`, `workbook_fx_pnl`) and its aggregation layer
(`engine/pnl/aggregate.py`: `aggregate_by_pair`, `book_totals`, `period_pnl`) were
deleted 2026-09-17 along with the BNP CSV parser ("no bnp fall back",
docs/bnp-excel-removal.md); their tests were removed from this file in the same pass.
The live headline P&L path (`engine.pnl.valuation.value_book`, `engine.pnl.ledger`) is
unaffected and covered below.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from data.ingest import schema
from engine.pnl.aggregate import _n_business_days_back
from engine.pnl.fx_blotter import fx_blotter_rows
from engine.pnl.valuation import COLUMNS as VALUATION_COLUMNS

REPO = Path(__file__).resolve().parents[1]
AS_OF = "2026-08-17"

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



# --------------------------------------------------------------------- fx_blotter
# 2026-09-17 user reversal: the Blotter FX sub-tab keeps the old sheet's column shape
# (trade / tenor / fill / t-1-EOD-t-2) but is priced under the market-standard
# CLAUDE.md "P&L conventions" via value_book, NOT the retired xlsx_fx_replica workbook
# arithmetic. These tests replace the three xlsx_fx_replica ones removed the same day.

def _t1_t2(as_of=AS_OF):
    import datetime as dt
    d = dt.date.fromisoformat(as_of)
    return _n_business_days_back(d, 1).isoformat(), _n_business_days_back(d, 2).isoformat()


def _insert_future_instrument(conn, instrument_id="ESU6 Index", multiplier=50.0):
    conn.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "FUTURE", "ES", "USD", multiplier, 0, instrument_id, "2026-09-18"),
    )
    conn.commit()


def _insert_future_trade(conn, trade_id, instrument_id, contracts, fill, settle_date, trade_date="2026-08-01"):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "XLSX", instrument_id, "FUTURE", trade_id, trade_date, contracts, fill,
         "ACC", "CPTY", "STRAT", "TRADER", "test future", ""),
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 1, "NOTIONAL", "USD", contracts * 50.0 * fill, trade_date, settle_date, 0, 0),
    )
    conn.commit()


def test_fx_blotter_open_forward_uses_market_convention_not_workbook():
    """Each mark is the trade's OWN settle_date outright (no shared maturity), and each
    day's P&L converts at THAT day's spot -- the opposite of the retired replica's
    must-not-replicate items 3 and 4."""
    conn = _make_conn()
    _insert_trade(conn, "X", "USDJPY", 1_000_000, 147, "2026-09-01")
    t1, t2 = _t1_t2()
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148, as_of=AS_OF)
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 146, as_of=t1)
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 145, as_of=t2)
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 150, as_of=AS_OF)
    _insert_mark(conn, "USDJPY", t1, "SPOT", 149, as_of=t1)
    _insert_mark(conn, "USDJPY", t2, "SPOT", 151, as_of=t2)
    out = fx_blotter_rows(conn, AS_OF)
    row = out.iloc[0]
    assert row.tenor == "2026-09-01"
    assert row.quantity_usd_notional == pytest.approx(1_000_000)
    assert row.mark_eod == 148
    assert row.mark_t1 == 146
    assert row.mark_t2 == 145
    assert row.pnl_eod == pytest.approx(1_000_000 * (148 - 147) / 150)
    assert row.pnl_t1 == pytest.approx(1_000_000 * (146 - 147) / 149)
    assert row.pnl_t2 == pytest.approx(1_000_000 * (145 - 147) / 151)
    # explicitly not the retired workbook formula (C * (mark - fill) / mark for a pair
    # not ending "USD" -- see docs/bnp-excel-removal.md "Must not replicate"): the live
    # convention converts at spot (150), never at the outright mark itself (148).
    assert row.pnl_eod != pytest.approx(1_000_000 * (148 - 147) / 148)


def test_fx_blotter_futures_no_mark_divisor():
    conn = _make_conn()
    _insert_future_instrument(conn)
    _insert_future_trade(conn, "F1", "ESU6 Index", 10, 4500, "2026-09-18")
    _insert_mark(conn, "ESU6 Index", "2026-09-18", "FUTURE_PX", 4600, source="BBG_BDH", as_of=AS_OF)
    out = fx_blotter_rows(conn, AS_OF)
    row = out.iloc[0]
    notional = 10 * 50.0 * 4500
    assert row.quantity_usd_notional == pytest.approx(notional)
    assert row.mark_eod == 4600
    # market-standard futures P&L: contracts * multiplier * (m - f), never divided by m
    # (must-not-replicate item 1).
    assert row.pnl_eod == pytest.approx(10 * 50.0 * (4600 - 4500))


def test_fx_blotter_missing_t2_marks_take_the_nearest_close(strict_marks):
    """With the near-marks rule off, a day with no marks at all stays None (the fill and
    the reference step-back then act, engine/pnl/reference.py); with it on (the default
    since 2026-09-22) the day takes the nearest close's marks -- here t1, the only
    neighbour -- and its P&L is that close's."""
    conn = _make_conn()
    _insert_trade(conn, "X", "USDJPY", 1_000_000, 147, "2026-09-01")
    t1, t2 = _t1_t2()
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148, as_of=AS_OF)
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 150, as_of=AS_OF)
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 146, as_of=t1)
    _insert_mark(conn, "USDJPY", t1, "SPOT", 149, as_of=t1)
    # deliberately no marks at all on t2
    row = fx_blotter_rows(conn, AS_OF).iloc[0]
    assert (row.mark_eod, row.mark_t1, row.mark_t2, row.pnl_t2) == (148, 146, None, None)
    assert row.pnl_eod == pytest.approx(1_000_000 * (148 - 147) / 150)
    assert row.pnl_t1 == pytest.approx(1_000_000 * (146 - 147) / 149)


def test_fx_blotter_missing_t2_marks_take_the_nearest_close_with_the_near_rule_on():
    conn = _make_conn()
    _insert_trade(conn, "X", "USDJPY", 1_000_000, 147, "2026-09-01")
    t1, t2 = _t1_t2()
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148, as_of=AS_OF)
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 150, as_of=AS_OF)
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 146, as_of=t1)
    _insert_mark(conn, "USDJPY", t1, "SPOT", 149, as_of=t1)
    row = fx_blotter_rows(conn, AS_OF).iloc[0]
    assert row.mark_t2 == 146 and row.pnl_t2 == pytest.approx(row.pnl_t1)  # t1 is the nearest later close


def test_fx_blotter_injects_value_fn_and_calls_all_three_dates():
    conn = _make_conn()
    _insert_trade(conn, "X", "USDJPY", 1_000_000, 147, "2026-09-01")
    calls = []

    def stub(c, date):
        calls.append(date)
        return pd.DataFrame(columns=VALUATION_COLUMNS)

    out = fx_blotter_rows(conn, AS_OF, value_fn=stub)
    t1, t2 = _t1_t2()
    assert calls == [AS_OF, t1, t2]
    assert out.empty
    from engine.pnl.fx_blotter import OUTPUT_COLUMNS
    assert out.columns.tolist() == OUTPUT_COLUMNS


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


def test_value_book_near_dated_forward_reported_as_spot():
    """User decision 2026-09-15 item 2: an FX_FWD whose settle_date is at most 2
    business days after trade_date is reported as FX_SPOT in value_book's `product`
    column, even though `trades.product` on file is still FX_FWD."""
    conn = _vb_conn()
    _vb_fx_trade(conn, "T3s", "EURUSD", "EUR", "USD", 1_000_000, 1.1000, settle="2026-05-04")
    _vb_mark(conn, "EURUSD", "2026-05-04", "FWD_OUTRIGHT", 1.1080)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["product"] == "FX_SPOT"
    assert conn.execute("SELECT product FROM trades WHERE trade_id='T3s'").fetchone()[0] == "FX_FWD"


def test_value_book_eur_sek_cross_no_invented_usd_leg():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T3", "EURSEK", "EUR", "SEK", 1_000_000, 11.00)
    _vb_mark(conn, "EURSEK", VB_SETTLE, "FWD_OUTRIGHT", 11.20)
    _vb_mark(conn, "USDSEK", VB_AS_OF, "SPOT", 10.50)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["pnl_local"] == pytest.approx(200_000)
    assert row["pnl_usd"] == pytest.approx(200_000 / 10.5, rel=1e-6)
    assert row["spot_source"] != ""


def _vb_brl(conn):
    conn.execute("INSERT INTO instruments VALUES ('USDBRL','FX','USD','BRL',1.0,1,'USDBRL Curncy','9999-12-31')")
    conn.commit()
    return conn


def test_value_book_ndf_that_has_fixed_is_frozen_at_the_fixing_dates_spot():
    """User, 2026-09-22: "NDFs - once they expire, they should disappear ... 0 delta and 0
    carry" (reversing 2026-09-21's "like settled cash"). Value date Wed 2026-06-03 -> fixing
    Mon 2026-06-01: from the fixing on the P&L is Q x (S_fix - f) at the fixing date's SPOT,
    converted at that date, and does not move with later spots; the ledger then freezes the
    same figure after the value date, at the fixing date's spot, not the value date's."""
    from engine.pnl import ledger
    conn = _vb_brl(_vb_conn())
    _vb_fx_trade(conn, "N1", "USDBRL", "USD", "BRL", -1_000_000, 5.20, settle="2026-06-03")
    _vb_mark(conn, "USDBRL", "2026-06-03", "FWD_OUTRIGHT", 5.30)  # on file, but no longer the mark
    _vb_mark(conn, "USDBRL", VB_AS_OF, "SPOT", 5.10)
    for day, spot in (("2026-06-02", 5.00), ("2026-06-03", 4.90), ("2026-06-04", 4.80)):
        _vb_mark(conn, "USDBRL", day, "SPOT", spot, as_of=day)
    expected = 100_000 / 5.10
    for day in (VB_AS_OF, "2026-06-02", "2026-06-03"):          # fixing day, then open until the value date
        row = value_book(conn, day).iloc[0]
        assert (row["status"], row["mark"], row["mark_date"]) == ("OPEN", 5.10, VB_AS_OF)
        assert row["pnl_usd"] == pytest.approx(expected) and row["spot"] == pytest.approx(1 / 5.10)
        assert row["pnl_spot_usd"] == pytest.approx(expected) and row["pnl_carry_usd"] == 0.0
        assert row["reason"] == "" and row["note"] == ("NDF fixed 2026-06-01: no official fixing on file: at the spot of "
                                                       "2026-06-01 instead, converted at that spot, no delta, no carry")
    assert ledger.realise_settled(conn, "2026-06-04")["realised"] == 1
    frozen = conn.execute("SELECT pnl_usd, spot_as_of_date, note, mark_type FROM realised_pnl WHERE trade_id='N1'").fetchone()
    assert frozen[0] == pytest.approx(expected) and frozen[1] == VB_AS_OF
    assert frozen[2] == "spot dated 2026-06-01 (NDF fixing), converted at that spot" and frozen[3] == "SPOT"
    row = value_book(conn, "2026-06-04").iloc[0]
    assert row["status"] == "SETTLED" and row["pnl_usd"] == pytest.approx(expected)


def test_value_book_ndf_exit_price_is_the_official_fixing_of_the_fixing_date():
    """User, 2026-09-22: "the entry price is where we traded, and the exit price is the fix
    on that day, as pulled from bbg". The NDF_FIX mark of the fixing date beats the spot;
    the ledger records it as the freeze (mark_type NDF_FIX); a fix from a neighbouring day
    is never used ("each ndf has a unique fix"): the fixing date's spot stands in until the
    fix lands."""
    from engine.pnl import ledger
    conn = _vb_brl(_vb_conn())
    _vb_fx_trade(conn, "N1", "USDBRL", "USD", "BRL", -1_000_000, 5.20, settle="2026-06-03")   # fixes 06-01
    _vb_mark(conn, "USDBRL", VB_AS_OF, "SPOT", 5.10)
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (VB_AS_OF, "USDBRL", VB_AS_OF, "NDF_FIX", 5.15, "BBG_BDH", f"{VB_AS_OF}T15:00:00-04:00"))
    conn.commit()
    row = value_book(conn, "2026-06-02").iloc[0]
    assert (row["mark"], row["mark_source"], row["mark_date"]) == (5.15, "BBG_BDH", VB_AS_OF)
    assert row["pnl_usd"] == pytest.approx(-1_000_000 * (5.15 - 5.20) / 5.15)   # exit = fix, converted at the fix itself (2026-09-22)
    assert row["spot"] == pytest.approx(1 / 5.15) and row["spot_source"] == "BBG_BDH"
    assert row["note"] == "NDF fixed 2026-06-01: at the official fixing of 2026-06-01, converted at the fixing, no delta, no carry"
    assert ledger.realise_settled(conn, "2026-06-04")["realised"] == 1
    frozen = conn.execute("SELECT pnl_usd, spot_as_of_date, mark_type, note FROM realised_pnl WHERE trade_id='N1'").fetchone()
    assert frozen[0] == pytest.approx(row["pnl_usd"]) and frozen[1:] == (
        VB_AS_OF, "NDF_FIX", "official fixing dated 2026-06-01 (NDF fixing), converted at the fixing")

    # the fixing day's own fix not on file, a neighbour's is: the neighbour is never the exit
    # price (user, 2026-09-22: "each ndf has a unique fix"); the fixing date's spot is, named
    conn.execute("DELETE FROM realised_pnl")
    conn.execute("UPDATE marks SET as_of_date = '2026-05-29', settle_date = '2026-05-29' WHERE mark_type = 'NDF_FIX'")
    conn.commit()
    row = value_book(conn, "2026-06-02").iloc[0]
    assert (row["mark"], row["mark_source"], row["mark_date"]) == (5.10, "BBG_BFXFORWARD", VB_AS_OF)
    assert row["pnl_usd"] == pytest.approx(-1_000_000 * (5.10 - 5.20) / 5.10)
    assert row["note"] == ("NDF fixed 2026-06-01: no official fixing on file: at the spot of 2026-06-01 instead, "
                           "converted at that spot, no delta, no carry")
    assert ledger.realise_settled(conn, "2026-06-04")["realised"] == 1
    assert conn.execute("SELECT mark_type, spot_as_of_date, note FROM realised_pnl WHERE trade_id='N1'").fetchone() == (
        "SPOT", VB_AS_OF, "spot dated 2026-06-01 (NDF fixing), converted at that spot")


def test_value_book_any_pair_with_no_forward_takes_the_days_curve_spot_alone_being_spot():
    """User decisions 2026-09-21/22: an NDF or a metal with no forward was marked at spot;
    since "always interpolate/extrapolate with near marks" every pair's missing forward is
    read off the day's own curve, and with spot the only pillar that is spot."""
    conn = _vb_brl(_vb_conn())
    _vb_fx_trade(conn, "N1", "USDBRL", "USD", "BRL", -1_000_000, 5.20, settle="2026-06-24")
    _vb_fx_trade(conn, "J1", "USDJPY", "USD", "JPY", 1_000_000, 150.00, settle="2026-06-24")
    _vb_mark(conn, "USDBRL", VB_AS_OF, "SPOT", 5.10)
    _vb_mark(conn, "USDJPY", VB_AS_OF, "SPOT", 149.00)
    book = value_book(conn, VB_AS_OF).set_index("trade_id")
    ndf, jpy = book.loc["N1"], book.loc["J1"]
    assert ndf["mark"] == 5.10 and ndf["pnl_usd"] == pytest.approx(100_000 / 5.10)
    assert ndf["mark_source"] == "INTERP: USDBRL 2026-06-03 mark of 2026-06-01 (the only pillar)"
    assert jpy["mark"] == 149.0 and jpy["pnl_usd"] == pytest.approx(-1_000_000 / 149.0)
    assert jpy["mark_source"].startswith("INTERP: USDJPY 2026-06-03 mark")
    assert ndf["note"] == "" and jpy["reason"] == ""

    # with its forward on file the ticket is marked at the forward, as always
    _vb_mark(conn, "USDBRL", "2026-06-24", "FWD_OUTRIGHT", 5.30)
    ndf = value_book(conn, VB_AS_OF).set_index("trade_id").loc["N1"]
    assert ndf["mark"] == 5.30 and ndf["mark_source"] == "BBG_BFXFORWARD"
    assert ndf["pnl_usd"] == pytest.approx(-100_000 / 5.10)


def test_value_book_missing_forward_is_interpolated_along_the_days_curve_and_extrapolated_past_it():
    conn = _vb_conn()
    _vb_mark(conn, "USDJPY", VB_AS_OF, "SPOT", 149.00)                # spot date 2026-06-03
    _vb_mark(conn, "USDJPY", "2026-06-13", "FWD_OUTRIGHT", 148.00)    # 10 days after spot date
    _vb_mark(conn, "USDJPY", "2026-07-03", "FWD_OUTRIGHT", 147.00)    # 30 days after
    for tid, settle in (("A", "2026-06-23"), ("B", "2026-07-13"), ("C", "2026-06-02"), ("D", "2026-06-13")):
        _vb_fx_trade(conn, tid, "USDJPY", "USD", "JPY", 1_000_000, 150.00, settle=settle)
    book = value_book(conn, VB_AS_OF).set_index("trade_id")
    assert book.loc["A", "mark"] == pytest.approx(147.5)              # halfway between the pillars
    assert book.loc["A", "mark_source"] == "INTERP: between USDJPY 2026-06-13 and 2026-07-03 marks of 2026-06-01"
    assert book.loc["B", "mark"] == pytest.approx(146.5)              # 10 days past the last: same slope
    assert book.loc["B", "mark_source"] == "INTERP: extrapolated from USDJPY 2026-06-13 and 2026-07-03 marks of 2026-06-01"
    assert book.loc["C", "mark"] == 149.0                             # before the spot date: spot
    assert book.loc["C", "mark_source"].startswith("INTERP: USDJPY 2026-06-03 mark of 2026-06-01 (nearest")
    assert book.loc["D", "mark"] == 148.0 and book.loc["D", "mark_source"] == "BBG_BFXFORWARD"   # exact: untouched
    assert (book["pnl_usd"].notna()).all()


def test_value_book_missing_mark_is_interpolated_in_time_between_the_nearest_closes():
    """A day with no spot and no forward of its own: spot and forward come from the closes
    either side (linear in calendar days); with a close on one side only that close is
    carried; the row names the closes. Nothing is written to marks."""
    conn = _vb_conn()
    _vb_fx_trade(conn, "T", "USDJPY", "USD", "JPY", 1_000_000, 150.00)
    for day, spot, fwd in (("2026-05-29", 148.0, 147.0), ("2026-06-04", 152.0, 151.0)):   # Fri, Thu
        _vb_mark(conn, "USDJPY", day, "SPOT", spot, as_of=day)
        _vb_mark(conn, "USDJPY", VB_SETTLE, "FWD_OUTRIGHT", fwd, as_of=day)
    n_marks = conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    row = value_book(conn, "2026-06-01").iloc[0]                       # Mon: 3 of 6 days along
    assert row["mark"] == pytest.approx(149.0) and row["spot"] == pytest.approx(1 / 150.0)
    assert row["mark_source"] == "INTERP: FWD_OUTRIGHT between the 2026-05-29 and 2026-06-04 closes"
    assert row["spot_source"] == "INTERP: SPOT between the 2026-05-29 and 2026-06-04 closes"
    assert row["pnl_usd"] == pytest.approx(1_000_000 * (149.0 - 150.0) / 150.0)
    before = value_book(conn, "2026-05-20").iloc[0]                    # nothing earlier: the 05-29 close carried
    assert before["mark"] == 147.0 and before["mark_source"] == "INTERP: FWD_OUTRIGHT of 2026-05-29 (nearest later close, none earlier)"
    after = value_book(conn, "2026-06-10").iloc[0]
    assert after["mark"] == 151.0 and after["mark_source"] == "INTERP: FWD_OUTRIGHT of 2026-06-04 (nearest earlier close)"
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == n_marks


def test_value_book_forward_on_a_day_with_no_marks_is_read_off_the_nearest_closes_curve():
    """Seen 2026-09-22 on the imported snapshot: no pull yet today, and a USDTWD leg date no
    close ever quoted exactly, so 21 forwards were blank although yesterday's curve was on
    file. The forward is read off the nearest earlier close's curve at the leg's date (and
    between the two neighbouring curves when there is a later one too)."""
    conn = _vb_conn()
    _vb_fx_trade(conn, "T", "USDJPY", "USD", "JPY", 1_000_000, 150.00, settle="2026-06-23")
    _vb_mark(conn, "USDJPY", "2026-05-29", "SPOT", 149.00, as_of="2026-05-29")             # spot date 06-02
    _vb_mark(conn, "USDJPY", "2026-06-12", "FWD_OUTRIGHT", 148.00, as_of="2026-05-29")     # 10 days after
    _vb_mark(conn, "USDJPY", "2026-07-02", "FWD_OUTRIGHT", 147.00, as_of="2026-05-29")     # 30 days after
    row = value_book(conn, VB_AS_OF).iloc[0]                                               # 06-01: no marks at all
    assert row["mark"] == pytest.approx(147.45)           # 06-23 on the 05-29 curve: 148 - 11/20 x 1
    assert row["mark_source"] == "INTERP: FWD_OUTRIGHT from the 2026-05-29 curve of USDJPY (nearest earlier close)"
    assert row["spot_source"].startswith("INTERP: SPOT of 2026-05-29") and row["pnl_usd"] == row["pnl_usd"]
    _vb_mark(conn, "USDJPY", "2026-06-03", "SPOT", 151.00, as_of="2026-06-03")             # a later close's curve too
    _vb_mark(conn, "USDJPY", "2026-06-26", "FWD_OUTRIGHT", 150.00, as_of="2026-06-03")     # spot date 06-05, pillar 06-26
    later = 151.0 + (150.0 - 151.0) * 18 / 21                                                # 06-23 on the 06-03 curve
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["mark"] == pytest.approx(147.45 + (later - 147.45) * 3 / 5)                  # 3 of 5 days along
    assert row["mark_source"] == "INTERP: FWD_OUTRIGHT between the 2026-05-29 and 2026-06-03 curves of USDJPY"
    # an exact quote for the leg date on a neighbouring close is carried as it stands, before any curve
    _vb_mark(conn, "USDJPY", "2026-06-23", "FWD_OUTRIGHT", 149.50, as_of="2026-06-03")
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["mark"] == 149.5 and row["mark_source"] == "INTERP: FWD_OUTRIGHT of 2026-06-03 (nearest later close, none earlier)"


def test_value_book_swap_pv_is_interpolated_in_time_and_settled_coupons_take_the_earlier_close():
    conn = _vb_conn()
    conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1.0,0,'','2030-06-01')")
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("S1", "MANUAL", "IRSOIS-USD-1", "IRS", "S1", "2026-05-01", 10_000_000, 0.04,
                  "ACC", "CPTY", "STRAT", "TRADER", "test", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("S1", 1, "FIXED", "USD", -10_000_000, "2026-05-01", "2030-06-01", 0.04, 1),
        ("S1", 2, "FLOAT", "USD", 10_000_000, "2026-05-01", "2030-06-01", 0.0, 1)])
    for day, pv, cf in (("2026-05-29", 1000.0, 0.0), ("2026-06-04", 4000.0, 500.0)):
        conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (day, "IRSOIS-USD-1", "2030-06-01", "PV_USD", pv, "QL_PRICER", f"{day}T15:00:00-04:00"))
        conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (day, "IRSOIS-USD-1", "2030-06-01", "CASHFLOW_USD", cf, "QL_PRICER", f"{day}T15:00:00-04:00"))
    conn.commit()
    row = value_book(conn, "2026-06-01").iloc[0]
    assert row["pnl_usd"] == pytest.approx(2500.0 + 0.0)   # PV halfway, coupons as of the earlier close
    assert "PV_USD between the 2026-05-29 and 2026-06-04 closes" in row["mark_source"]
    assert value_book(conn, "2026-05-20").iloc[0]["pnl_usd"] == pytest.approx(1000.0)   # carried back


def test_near_marks_never_price_off_a_stored_value_that_is_not_a_number_and_the_ladder_stays_exact():
    from engine.pnl.valuation import _mark_at, _mark_near
    conn = _vb_conn()
    _vb_fx_trade(conn, "T", "USDJPY", "USD", "JPY", 1_000_000, 150.00)
    _vb_mark(conn, "USDJPY", "2026-05-29", "SPOT", 148.0, as_of="2026-05-29")
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", ("2026-05-29", "USDJPY", VB_SETTLE, "FWD_OUTRIGHT", "n/a", "BBG_BFXFORWARD", "2026-05-29T15:00:00-04:00"))
    conn.commit()
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"]) and "is not a number" in row["reason"]
    assert _mark_at(conn, "USDJPY", "2026-05-29", "SPOT", VB_AS_OF) is None          # exact stays exact
    assert _mark_near(conn, "USDJPY", VB_AS_OF, "SPOT", VB_AS_OF)[0] == 148.0


def test_value_book_gold_with_no_forward_takes_spot_as_the_only_pillar():
    """User decision 2026-09-22: "xauusd has no fwd outright, this should be handled similar
    to the ndfs and settled cash". Long 482.474 oz at 4145.30 for 2026-06-24, no forward on
    file: marked off the day's curve, i.e. at the XAUUSD spot when that is all there is;
    with a forward on file, at the forward."""
    conn = _vb_conn()
    conn.execute("INSERT INTO instruments VALUES ('XAUUSD','FX','XAU','USD',1.0,0,'XAUUSD Curncy','9999-12-31')")
    conn.commit()
    _vb_fx_trade(conn, "G1", "XAUUSD", "XAU", "USD", 482.474, 4145.30, settle="2026-06-24")
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"]) and row["reason"].startswith("no FWD_OUTRIGHT mark for XAUUSD settle 2026-06-24")
    _vb_mark(conn, "XAUUSD", VB_AS_OF, "SPOT", 4200.0)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert (row["mark"], row["status"]) == (4200.0, "OPEN")
    assert row["pnl_usd"] == pytest.approx(482.474 * (4200.0 - 4145.30))
    assert row["mark_source"] == "INTERP: XAUUSD 2026-06-03 mark of 2026-06-01 (the only pillar)"
    _vb_mark(conn, "XAUUSD", "2026-06-24", "FWD_OUTRIGHT", 4210.0)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["mark"] == 4210.0 and row["mark_source"] == "BBG_BFXFORWARD"
    assert row["pnl_usd"] == pytest.approx(482.474 * (4210.0 - 4145.30))


def test_value_book_fixed_ndf_with_no_spot_is_blank_and_says_why():
    conn = _vb_brl(_vb_conn())
    _vb_fx_trade(conn, "N1", "USDBRL", "USD", "BRL", -1_000_000, 5.20, settle="2026-06-03")
    _vb_mark(conn, "USDBRL", "2026-06-03", "FWD_OUTRIGHT", 5.30)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"])
    assert row["reason"] == "no official fixing and no SPOT mark for USDBRL on 2026-06-01 (NDF fixed that day)"


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


VB_OPT = "EURUSD092226C-1"
VB_OPT_EXPIRY = "2026-09-22"


def _vb_option(conn, trade_id, instrument_id, base_ccy, quote_ccy, quantity, fill, expiry=VB_OPT_EXPIRY):
    conn.execute("INSERT OR IGNORE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 (instrument_id, "FX_OPTION", base_ccy, quote_ccy, 1.0, 0, instrument_id, expiry))
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "XLSX", instrument_id, "FX_OPTION", trade_id, "2026-05-01", quantity, fill,
         "ACC", "CPTY", "", "TRADER", "test", ""),
    )
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "NOTIONAL", base_ccy, quantity, "2026-05-01", expiry, fill, 0))
    conn.commit()


def _vb_premium(conn, instrument_id, value, expiry=VB_OPT_EXPIRY, as_of=VB_AS_OF):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (as_of, instrument_id, expiry, "PREMIUM", value, "QL_OPTIONS_PRICER", f"{as_of}T17:00:00-04:00"))
    conn.commit()


def test_value_book_option_premium_pnl_in_base_ccy_converted_at_base_spot():
    """CLAUDE.md: PnL = (premium_mark - premium_fill) x Size; premium and fill are both a
    fraction of BASE notional, so the USD conversion is the base currency's spot."""
    conn = _vb_conn()
    _vb_option(conn, "O1", VB_OPT, "EUR", "USD", 35_000_000, 0.0050)
    _vb_premium(conn, VB_OPT, 0.0062)
    _vb_mark(conn, "EURUSD", VB_AS_OF, "SPOT", 1.10)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["product"] == "FX_OPTION" and row["status"] == "OPEN"
    assert row["pnl_local"] == pytest.approx(35_000_000 * 0.0012)
    assert row["pnl_usd"] == pytest.approx(35_000_000 * 0.0012 * 1.10)
    assert row["mark_source"] == "QL_OPTIONS_PRICER"


def test_value_book_short_option_loses_when_premium_rises():
    conn = _vb_conn()
    _vb_option(conn, "O2", "USDJPY111926P-1", "USD", "JPY", -10_000_000, 0.0100)
    _vb_premium(conn, "USDJPY111926P-1", 0.0130)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["pnl_usd"] == pytest.approx(-10_000_000 * 0.0030)  # base USD: S = 1, no SPOT needed


def test_value_book_option_without_premium_mark_is_unavailable_with_reason():
    conn = _vb_conn()
    _vb_option(conn, "O3", VB_OPT, "EUR", "USD", 1_000_000, 0.0050)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"])
    assert "PREMIUM" in row["reason"]


def test_value_book_option_never_reads_delta_or_non_official_premium():
    conn = _vb_conn()
    _vb_option(conn, "O4", VB_OPT, "EUR", "USD", 1_000_000, 0.0050)
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (VB_AS_OF, VB_OPT, VB_OPT_EXPIRY, "PREMIUM", 0.0099, "MANUAL", "t"))
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (VB_AS_OF, VB_OPT, VB_OPT_EXPIRY, "DELTA", 0.5, "QL_OPTIONS_PRICER", "t"))
    conn.commit()
    assert math.isnan(value_book(conn, VB_AS_OF).iloc[0]["pnl_usd"])


def test_value_book_expired_option_is_unavailable_not_stale():
    conn = _vb_conn()
    _vb_option(conn, "O5", "EURUSD051526C-1", "EUR", "USD", 1_000_000, 0.0050, expiry="2026-05-15")
    _vb_premium(conn, "EURUSD051526C-1", 0.0062, expiry="2026-05-15")
    _vb_mark(conn, "EURUSD", VB_AS_OF, "SPOT", 1.10)
    row = value_book(conn, VB_AS_OF).iloc[0]
    # 2026-09-17: an expired option is SETTLED like any other trade -- its row must come
    # from realised_pnl (engine/pnl/ledger.realise_settled), never a stale premium.
    assert row["status"] == "SETTLED"
    assert math.isnan(row["pnl_usd"])
    assert "cannot be frozen" in row["reason"]


# ----- closed-out options (user, 2026-09-21: "for options closed out theyre not live ... for
# the pnl calculations this has to be factored in"). The reference sample's own case: the EURSEK
# 23-Sep put bought 35m at 0.0057 and sold back at 0.005645 under a second instrument id.
VB_PUT_BOUGHT, VB_PUT_SOLD = "EURSEK092326P-197728105", "EURSEK092326P-197838147"


def _vb_terms(conn, instrument_id, strike, option_type="PUT", payoff="VANILLA"):
    conn.execute("INSERT OR REPLACE INTO instrument_options (instrument_id, strike, option_type, payoff) "
                 "VALUES (?,?,?,?)", (instrument_id, strike, option_type, payoff))
    conn.commit()


def _vb_round_trip(conn, sold=-35_000_000, sold_on="2026-05-05", strikes=(11.2, 11.2), expiry=VB_OPT_EXPIRY):
    _vb_option(conn, "BUY", VB_PUT_BOUGHT, "EUR", "SEK", 35_000_000, 0.0057, expiry=expiry)
    _vb_option(conn, "SELL", VB_PUT_SOLD, "EUR", "SEK", sold, 0.005645, expiry=expiry)
    conn.execute("UPDATE trades SET trade_date = ? WHERE trade_id = 'SELL'", (sold_on,))
    for instrument_id, strike in zip((VB_PUT_BOUGHT, VB_PUT_SOLD), strikes):
        if strike:
            _vb_terms(conn, instrument_id, strike)
    conn.commit()


def _vb_rows(conn, as_of=VB_AS_OF):
    return value_book(conn, as_of).set_index("trade_id")


VB_CLOSED_ON = "2026-05-05"   # the sell-back's trade date: the close-out date


def test_value_book_closed_out_option_is_realised_at_its_closing_fill_with_no_premium_mark():
    """Frozen in dollars too (user, 2026-09-21: "of course you freeze the usd converstion"):
    converted at the close-out date's spot, so a later spot never moves it."""
    conn = _vb_conn()
    _vb_round_trip(conn)
    _vb_mark(conn, "EURUSD", VB_CLOSED_ON, "SPOT", 1.08, as_of=VB_CLOSED_ON)
    _vb_mark(conn, "EURUSD", VB_AS_OF, "SPOT", 1.10)
    vb = _vb_rows(conn)
    assert list(vb["status"]) == ["CLOSED", "CLOSED"] and list(vb["reason"]) == ["", ""]
    assert vb.loc["BUY", "pnl_local"] == pytest.approx(35_000_000 * (0.005645 - 0.0057))   # -1,925 EUR
    assert vb.loc["BUY", "pnl_usd"] == pytest.approx(-1_925 * 1.08)
    assert _vb_rows(conn, VB_CLOSED_ON)["pnl_usd"].sum() == pytest.approx(vb["pnl_usd"].sum())   # the same on every date
    assert vb.loc["SELL", "pnl_usd"] == pytest.approx(0.0)
    assert vb.loc["BUY", "mark"] == pytest.approx(0.005645) and vb.loc["BUY", "mark_source"] == "CLOSE_OUT_FILL"
    assert "closed out 2026-05-05" in vb.loc["BUY", "note"]


def test_value_book_closed_out_total_on_the_close_out_date_is_what_the_marked_formula_gives():
    """The PREMIUM mark cancels over a flat position, so the close-out day's total is the marked
    formula's own; it is that day's figure that stays."""
    conn = _vb_conn()
    _vb_round_trip(conn)
    _vb_mark(conn, "EURUSD", VB_CLOSED_ON, "SPOT", 1.08, as_of=VB_CLOSED_ON)
    marked = (35_000_000 * (0.0031 - 0.0057) - 35_000_000 * (0.0031 - 0.005645)) * 1.08
    assert _vb_rows(conn, VB_CLOSED_ON)["pnl_usd"].sum() == pytest.approx(marked)


@pytest.mark.parametrize("kwargs", [
    {"strikes": (0.0, 0.0)},          # no strike on file: two unknown strikes could be a spread
    {"strikes": (11.2, 11.4)},        # a put spread, not a close-out
    {"sold": -20_000_000},            # sold back in part: 15m is still live
])
def test_value_book_option_that_is_not_fully_closed_out_still_needs_its_premium_mark(kwargs):
    conn = _vb_conn()
    _vb_round_trip(conn, **kwargs)
    _vb_mark(conn, "EURUSD", VB_AS_OF, "SPOT", 1.10)
    vb = _vb_rows(conn)
    assert list(vb["status"]) == ["OPEN", "OPEN"]
    assert vb["pnl_usd"].isna().all() and all("PREMIUM" in reason for reason in vb["reason"])


def test_value_book_before_the_sell_back_the_option_is_still_live():
    conn = _vb_conn()
    _vb_round_trip(conn, sold_on="2026-06-10")
    _vb_mark(conn, "EURUSD", VB_AS_OF, "SPOT", 1.10)
    vb = _vb_rows(conn)   # 2026-06-01: only the purchase is on the book
    assert list(vb.index) == ["BUY"] and vb.loc["BUY", "status"] == "OPEN" and math.isnan(vb.loc["BUY", "pnl_usd"])


def test_value_book_closed_out_option_without_a_conversion_spot_says_so():
    conn = _vb_conn()
    _vb_round_trip(conn)
    _vb_mark(conn, "EURUSD", VB_AS_OF, "SPOT", 1.10)   # a spot AFTER the close-out is no substitute
    vb = _vb_rows(conn)
    assert vb["pnl_usd"].isna().all()
    assert "no official SPOT to convert EUR to USD on or before that date" in vb.loc["BUY", "reason"]


def test_value_book_closed_out_gold_option_dealt_per_ounce_is_in_dollars():
    conn = _vb_conn()
    for trade_id, instrument_id, ounces, fill in (("G1", "XAUUSD092226C-1", 1_000, 38.5), ("G2", "XAUUSD092226C-2", -1_000, 41.0)):
        _vb_option(conn, trade_id, instrument_id, "XAU", "USD", ounces, fill)
        _vb_terms(conn, instrument_id, 4_200.0, option_type="CALL")
    vb = _vb_rows(conn)   # no XAU spot on file and none needed: the fills are dollars per ounce
    assert vb.loc["G1", "pnl_usd"] == pytest.approx(2_500.0) and vb.loc["G2", "pnl_usd"] == pytest.approx(0.0)


def test_value_book_closed_out_option_past_expiry_is_recorded_at_the_same_close_out_figure():
    from engine.pnl import ledger
    conn = _vb_conn()
    _vb_round_trip(conn, expiry="2026-05-20")
    _vb_mark(conn, "EURUSD", "2026-05-04", "SPOT", 1.15, as_of="2026-05-04")   # the last before the close-out
    _vb_mark(conn, "EURUSD", "2026-05-19", "SPOT", 1.20, as_of="2026-05-19")
    _vb_mark(conn, "EURUSD", VB_AS_OF, "SPOT", 1.10)
    before_expiry = _vb_rows(conn, "2026-05-12")["pnl_usd"].sum()
    vb = _vb_rows(conn)   # not yet in realised_pnl: the figure the ledger will record
    assert list(vb["status"]) == ["SETTLED", "SETTLED"] and "last before the close-out" in vb.loc["BUY", "note"]
    assert vb["pnl_usd"].sum() == pytest.approx(-1_925 * 1.15) == pytest.approx(before_expiry)
    assert ledger.realise_settled(conn, VB_AS_OF) == {"realised": 2, "unrealisable": [], "repaired": [], "refrozen": [],
                                                      "kept": []}
    assert conn.execute("SELECT DISTINCT mark_type, spot_as_of_date FROM realised_pnl").fetchall() == [("CLOSE_OUT", "2026-05-04")]
    assert _vb_rows(conn)["pnl_usd"].sum() == pytest.approx(-1_925 * 1.15)
    assert ledger.ltd(conn, VB_AS_OF) == pytest.approx(-1_925 * 1.15)


def test_value_book_missing_mark_is_nan_with_reason():
    conn = _vb_conn()
    _vb_fx_trade(conn, "T5", "EURUSD", "EUR", "USD", 1_000_000, 1.1000)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"])
    assert "FWD_OUTRIGHT" in row["reason"]


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


# --------------------------------------------------------------------- value_book: IRS
VB_IRS = "IRSOIS-USD-77"
VB_IRS_MATURITY = "2027-06-20"


def _vb_irs(conn, trade_id="S1", quantity=10_000_000.0, fixed=0.04, maturity=VB_IRS_MATURITY):
    conn.execute("INSERT OR IGNORE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 (VB_IRS, "IRS", "USD", "USD", 1.0, 0, VB_IRS, maturity))
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", VB_IRS, "IRS", trade_id, "2026-05-01", quantity, fixed,
         "ACC", "CPTY", "STRAT", "TRADER", "irs", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        (trade_id, 1, "FIXED", "USD", -quantity, "2026-05-05", maturity, fixed, 0),
        (trade_id, 2, "FLOAT", "USD", quantity, "2026-05-05", maturity, 0.0, 0),
    ])
    conn.commit()


def _vb_irs_mark(conn, mark_type, value, as_of=VB_AS_OF, maturity=VB_IRS_MATURITY):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (as_of, VB_IRS, maturity, mark_type, value, "QL_PRICER", f"{as_of}T17:00:00-04:00"))
    conn.commit()


def test_value_book_irs_is_pv_plus_settled_cashflows():
    """CLAUDE.md P&L conventions, IRS (2026-09-17): pnl_usd = PV_USD + CASHFLOW_USD from
    marks_official at the swap's maturity; spot is identity (already USD)."""
    conn = _vb_conn()
    _vb_irs(conn)
    _vb_irs_mark(conn, "PV_USD", 123_456.0)
    _vb_irs_mark(conn, "CASHFLOW_USD", 10_000.0)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["product"] == "IRS" and row["status"] == "OPEN"
    assert row["mark"] == pytest.approx(123_456.0) and row["mark_source"] == "QL_PRICER"
    assert row["pnl_usd"] == pytest.approx(133_456.0) and row["spot"] == 1.0
    assert row["reason"] == "" and "10,000.00" in row["note"]


def test_value_book_irs_missing_cashflow_mark_is_nan_with_reason():
    conn = _vb_conn()
    _vb_irs(conn)
    _vb_irs_mark(conn, "PV_USD", 123_456.0)
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"]) and "CASHFLOW_USD" in row["reason"]


def test_value_book_irs_never_reads_reconciliation_only_bbg_pv():
    """BBG_BDH PV_USD is reconciliation-only (CLAUDE.md official marks): a swap with only
    a SWPM PV on file is Unavailable, not priced off it."""
    conn = _vb_conn()
    _vb_irs(conn)
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (VB_AS_OF, VB_IRS, VB_IRS_MATURITY, "PV_USD", 999.0, "BBG_BDH", "t"))
    conn.commit()
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert math.isnan(row["pnl_usd"]) and "PV_USD" in row["reason"]


def test_value_book_matured_swap_reads_realised_row_only():
    conn = _vb_conn()
    _vb_irs(conn, maturity="2026-05-20")
    _vb_irs_mark(conn, "PV_USD", 1.0, maturity="2026-05-20")   # a live mark that must NOT be used
    _vb_irs_mark(conn, "CASHFLOW_USD", 1.0, maturity="2026-05-20")
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["status"] == "SETTLED" and math.isnan(row["pnl_usd"]) and "cannot be frozen" in row["reason"]
    conn.execute("INSERT INTO realised_pnl VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("S1", VB_IRS, "IRS", "USD", "2026-05-20", 7_500.0, 0.0, "PV_USD", 1.0,
                  "2026-05-20", "QL_PRICER", 7_500.0, "t", ""))
    conn.commit()
    row = value_book(conn, VB_AS_OF).iloc[0]
    assert row["status"] == "SETTLED" and row["pnl_usd"] == pytest.approx(7_500.0) and row["reason"] == ""


# =========================================================================================
# engine/pnl/reference.py -- which close a period difference is measured from
# (user decision 2026-09-21: "use previous date until has value")
# =========================================================================================
from engine.pnl import reference

REF_AS_OF = "2026-09-15"          # Tuesday
REF_D5 = "2026-09-08"             # 5 business days back: 2026-09-07 is a holiday (Labor Day)
REF_SETTLE = "2026-10-20"
REF_HOLIDAYS = frozenset({"2026-09-07"})


def _ref_trade(conn, trade_id, trade_date="2026-05-01", quantity=1_000_000.0, fill=1.1000):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", "EURUSD", "FX_FWD", trade_id, trade_date, quantity, fill,
         "ACC", "CPTY", "STRAT", "TRADER", "test", ""),
    )
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "FX_NEAR", "EUR", quantity, trade_date, REF_SETTLE, fill, 1))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 2, "FX_NEAR", "USD", -quantity * fill, trade_date, REF_SETTLE, fill, 1))
    conn.commit()


def _ref_book(marked_days, trade_date="2026-05-01", n_trades=3):
    """EURUSD forwards with an official FWD_OUTRIGHT on `marked_days` only ({iso: mark})."""
    conn = _vb_conn()
    for i in range(n_trades):
        _ref_trade(conn, f"R{i + 1}", trade_date)
    for day, mark in marked_days.items():
        _vb_mark(conn, "EURUSD", REF_SETTLE, "FWD_OUTRIGHT", mark, as_of=day)
    return conn


def _recording_reader(conn):
    calls = []

    def frame_for(iso):
        calls.append(iso)
        return value_book(conn, iso)
    return frame_for, calls


def test_reference_usable_close_is_returned_untouched_with_no_note():
    conn = _ref_book({REF_AS_OF: 1.1100, REF_D5: 1.1050})
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    assert calls == [REF_D5]                      # no other date is valued
    assert (choice.found, choice.stepped_back, choice.skipped) == (True, False, ())
    assert (choice.ref_date, choice.ref_date_used, choice.note) == (REF_D5, REF_D5, "")
    assert choice.split.status == reference.OK

    before = {"value": 15_000.0, "ref_date": REF_D5, "available": True, "reason": "",
              "excluded_summary": "", "excluded_detail": ""}
    after = reference.annotate(before, choice, lambda s: "never asked")
    assert {k: after[k] for k in before} == before      # every existing key keeps its value
    assert after["ref_date_used"] == REF_D5
    assert (after["ref_note"], after["ref_note_detail"], after["ref_dates_skipped"]) == ("", "", ())


def test_reference_minority_blocked_walks_back_for_that_trade_and_leaves_it_out_when_it_has_no_earlier_price(strict_marks):
    """One of three trades unpriced on the reference date: the date itself stays (no whole-date
    step-back); the trade's own earlier closes are tried, up to 5, and with no price on any of
    them it stays left out, as before."""
    conn = _ref_book({REF_AS_OF: 1.1100, REF_D5: 1.1050}, n_trades=2)
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("GBPUSD", "FX", "GBP", "USD", 1.0, 0, "GBPUSD Curncy", "9999-12-31"))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("G1", "MANUAL", "GBPUSD", "FX_FWD", "G1", "2026-05-01", 1e6, 1.30,
                  "ACC", "CPTY", "STRAT", "TRADER", "test", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("G1", 1, "FX_NEAR", "GBP", 1e6, "2026-05-01", REF_SETTLE, 1.30, 1))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("G1", 2, "FX_NEAR", "USD", -1.3e6, "2026-05-01", REF_SETTLE, 1.30, 1))
    _vb_mark(conn, "GBPUSD", REF_SETTLE, "FWD_OUTRIGHT", 1.31, as_of=REF_AS_OF)   # none on REF_D5
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    assert calls == [REF_D5, "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01", "2026-08-31"]
    assert not choice.stepped_back and choice.filled == () and choice.note == ""
    assert choice.split.status == reference.OK
    assert (choice.split.n_blocked, choice.split.n_open_then) == (1, 3)


def test_reference_single_trade_with_no_price_on_the_close_takes_its_own_last_earlier_price(strict_marks):
    """User, 2026-09-21: "how is that possible given the fill function??" -- a few trades with no
    price on the period's close were dropped from that figure. Each now takes ITS OWN valuation
    from the last earlier business day it is priced on (up to 5 back); nothing is written."""
    conn = _ref_book({REF_AS_OF: 1.1100, REF_D5: 1.1050}, n_trades=2)
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("GBPUSD", "FX", "GBP", "USD", 1.0, 0, "GBPUSD Curncy", "9999-12-31"))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("G1", "MANUAL", "GBPUSD", "FX_FWD", "G1", "2026-05-01", 1e6, 1.30,
                  "ACC", "CPTY", "STRAT", "TRADER", "test", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("G1", 1, "FX_NEAR", "GBP", 1e6, "2026-05-01", REF_SETTLE, 1.30, 1))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("G1", 2, "FX_NEAR", "USD", -1.3e6, "2026-05-01", REF_SETTLE, 1.30, 1))
    _vb_mark(conn, "GBPUSD", REF_SETTLE, "FWD_OUTRIGHT", 1.31, as_of=REF_AS_OF)
    _vb_mark(conn, "GBPUSD", REF_SETTLE, "FWD_OUTRIGHT", 1.305, as_of="2026-09-03")   # none on REF_D5 or 09-04
    marks_before = conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    assert calls == [REF_D5, "2026-09-04", "2026-09-03"]            # stops once every trade has a price
    assert not choice.stepped_back and choice.ref_date_used == REF_D5 and choice.filled == (("2026-09-03", 1),)
    assert choice.split.n_blocked == 0
    g1 = choice.frame[choice.frame["trade_id"] == "G1"].iloc[0]
    assert g1["reason"] == "" and g1["pnl_usd"] == pytest.approx(1e6 * (1.305 - 1.30))   # its 09-03 valuation
    assert choice.note == "1 trade with no price on 2026-09-08 measured from its last earlier close (back to 2026-09-03)"
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == marks_before


def test_reference_steps_back_over_holiday_and_weekend_and_names_both_dates(strict_marks):
    conn = _ref_book({REF_AS_OF: 1.1100, "2026-09-04": 1.1050})     # nothing dated 2026-09-08
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    # 2026-09-07 (holiday) and 2026-09-05/06 (weekend) are never candidates, never valued
    assert calls == [REF_D5, "2026-09-04"]
    assert choice.found and choice.stepped_back
    assert (choice.ref_date, choice.ref_date_used) == (REF_D5, "2026-09-04")
    assert choice.note == "from the 2026-09-04 close: 2026-09-08 has no usable close"
    assert [(s.date, s.n_blocked, s.n_open_then) for s in choice.skipped] == [(REF_D5, 3, 3)]
    assert set(choice.skipped[0].unpriced["trade_id"]) == {"R1", "R2", "R3"}
    # the frame handed back is that date's own book, row for row
    pd.testing.assert_frame_equal(choice.frame.reset_index(drop=True),
                                  value_book(conn, "2026-09-04").reset_index(drop=True))

    entry = reference.annotate({"value": 15_000.0, "ref_date": REF_D5, "available": True, "reason": ""},
                               choice, lambda s: f"full reason for {s.date}: {s.n_blocked} of {s.n_open_then}")
    assert entry["ref_date"] == REF_D5                   # unchanged meaning
    assert entry["ref_date_used"] == "2026-09-04"
    assert REF_D5 in entry["ref_note"] and "2026-09-04" in entry["ref_note"]
    assert entry["ref_note_detail"] == "full reason for 2026-09-08: 3 of 3"
    assert entry["ref_dates_skipped"] == (REF_D5,)


def test_reference_default_calendar_is_config_holidays(strict_marks):
    """holidays=None reads the trading calendar the period dates use (config/holidays.txt,
    which lists 2026-09-07)."""
    conn = _ref_book({REF_AS_OF: 1.1100, "2026-09-04": 1.1050})
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for)
    assert calls == [REF_D5, "2026-09-04"] and choice.ref_date_used == "2026-09-04"


def test_reference_several_skipped_closes_caption_and_lazy_walk(strict_marks):
    conn = _ref_book({REF_AS_OF: 1.1100, "2026-09-03": 1.1050, "2026-09-01": 1.0900})
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    # stops at the FIRST usable close; 2026-09-02 / 2026-09-01 are never valued
    assert calls == [REF_D5, "2026-09-04", "2026-09-03"]
    assert choice.ref_date_used == "2026-09-03"
    assert choice.note == ("from the 2026-09-03 close: 2026-09-08 and the 1 business day "
                           "before it have no usable close")
    assert [s.date for s in choice.skipped] == [REF_D5, "2026-09-04"]


def test_reference_nothing_usable_within_five_business_days_is_na_with_extended_reason(strict_marks):
    conn = _ref_book({REF_AS_OF: 1.1100, "2026-08-28": 1.1050})     # 6 business days before REF_D5
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    assert calls == [REF_D5, "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01", "2026-08-31"]
    assert all(c <= REF_D5 for c in calls)               # never forward
    assert (choice.found, choice.stepped_back, choice.note) == (False, False, "")
    assert choice.ref_date_used == REF_D5                # the original date, with its own frame
    assert choice.split.status == reference.REFERENCE_MISSING
    assert set(choice.frame["trade_id"]) == {"R1", "R2", "R3"} and (choice.frame["reason"] != "").all()

    today = "5d needs the 2026-09-08 close: 3 of 3 trades open that day have no official mark dated 2026-09-08"
    entry = reference.annotate({"value": float("nan"), "available": False, "reason": today}, choice)
    assert not entry["available"] and math.isnan(entry["value"])
    assert entry["reason"] == (today + ". No earlier close within 5 business days has one either "
                               "(checked back to 2026-08-31).")
    assert (entry["ref_date_used"], entry["ref_note"]) == (REF_D5, "")
    assert len(entry["ref_dates_skipped"]) == 6


def test_reference_trade_less_date_is_a_zero_reference_and_ends_the_walk(strict_marks):
    # nothing in scope open yet on the period's own reference date: usable as it is today
    conn = _ref_book({REF_AS_OF: 1.1100}, trade_date="2026-09-10")
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    assert calls == [REF_D5] and not choice.stepped_back and choice.frame.empty

    # traded ON the reference date and unmarked there: the walk stops at the first earlier
    # close, where the book is empty, and never goes past it
    conn = _ref_book({REF_AS_OF: 1.1100}, trade_date=REF_D5)
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    assert calls == [REF_D5, "2026-09-04"]
    assert choice.ref_date_used == "2026-09-04" and choice.frame.empty


def test_reference_date_a_with_nothing_priced_never_steps_back(strict_marks):
    """"Previous day" when t-1 itself has no marks: n/a for its own reason, no walk."""
    conn = _ref_book({"2026-09-04": 1.1050})             # nothing dated REF_AS_OF
    frame_for, calls = _recording_reader(conn)
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS)
    assert calls == [REF_D5]
    assert choice.split.status == reference.NOTHING_PRICED and not choice.split.usable
    assert choice.found and not choice.stepped_back
    entry = reference.annotate({"value": float("nan"), "available": False, "reason": "no marks today"}, choice)
    assert entry["reason"] == "no marks today"           # nothing appended: no walk was exhausted

    empty = reference.diff_split(value_book(conn, "2026-01-02"), value_book(conn, "2026-01-01"))
    assert empty.status == reference.EMPTY and empty.usable


def test_reference_writes_no_mark_and_leaves_per_trade_pnl_unchanged(strict_marks):
    conn = _ref_book({REF_AS_OF: 1.1100, "2026-09-03": 1.1050})
    days = [REF_AS_OF, REF_D5, "2026-09-04", "2026-09-03"]
    marks_sql = "SELECT * FROM marks ORDER BY as_of_date, instrument_id, settle_date, mark_type, source"
    marks_before = conn.execute(marks_sql).fetchall()
    books_before = {day: value_book(conn, day) for day in days}
    changes_before = conn.total_changes

    frame_for, _calls = _recording_reader(conn)
    choice = reference.resolve_reference(books_before[REF_AS_OF], REF_D5, frame_for, REF_HOLIDAYS)
    reference.annotate({"value": 0.0, "available": True, "reason": ""}, choice, lambda s: s.date)
    assert choice.ref_date_used == "2026-09-03"

    assert conn.total_changes == changes_before          # not one row written, in any table
    assert conn.execute(marks_sql).fetchall() == marks_before
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE as_of_date IN (?, ?)",
                        (REF_D5, "2026-09-04")).fetchone()[0] == 0      # skipped closes stay unmarked
    for day in days:
        pd.testing.assert_frame_equal(value_book(conn, day), books_before[day])
    skipped_book = value_book(conn, REF_D5)               # still blank with its reason, never zero
    assert skipped_book["pnl_usd"].isna().all() and (skipped_book["reason"] != "").all()


def test_reference_usable_rule_matches_the_header_display_rule(strict_marks):
    """`diff_split` must agree with `ui/tabs/header.py::_priced_diff`, the rule it holds once
    for both screens; and the stepped-back frame prices through that function unchanged."""
    pytest.importorskip("dash")
    from ui.tabs.header import _priced_diff

    def book(rows):
        return pd.DataFrame([{"trade_id": t, "reason": r, "pnl_usd": p, "product": "FX_FWD",
                              "trade_date": "2026-05-01"} for t, r, p in rows],
                            columns=["trade_id", "reason", "pnl_usd", "product", "trade_date"])

    nan, miss = float("nan"), "no FWD_OUTRIGHT mark for EURUSD 2026-10-20 on 2026-09-08"
    a_full = book([("A", "", 10.0), ("B", "", 20.0), ("C", "", 30.0)])
    cases = [
        (a_full, book([("A", "", 1.0), ("B", "", 2.0), ("C", "", 3.0)])),          # all priced
        (a_full, book([("A", miss, nan), ("B", miss, nan), ("C", miss, nan)])),   # close missing
        (a_full, book([("A", "", 1.0), ("B", "", 2.0), ("C", miss, nan)])),       # minority blocked
        (a_full, book([("A", "", 1.0), ("B", miss, nan)])),                       # tie: not outnumbered
        (a_full, book([])),                                                       # zero reference
        (book([("A", miss, nan)]), book([("A", "", 1.0)])),                       # nothing priced on a
        (book([]), book([("A", "", 1.0)])),                                       # empty scope
    ]
    for df_a, df_b in cases:
        split = reference.diff_split(df_a, df_b)
        assert split.usable == _priced_diff(df_a, df_b, "root", "ref")["available"]

    conn = _ref_book({REF_AS_OF: 1.1100, "2026-09-04": 1.1050})
    df_today = value_book(conn, REF_AS_OF)
    choice = reference.resolve_reference(df_today, REF_D5, lambda iso: value_book(conn, iso), REF_HOLIDAYS)
    entry = _priced_diff(df_today, choice.frame, "root", choice.ref_date_used)
    assert entry["available"] and entry["value"] == pytest.approx(3 * 1_000_000 * (1.1100 - 1.1050))


# ---------------------------------------------------------------- the fill (user decision 2026-09-21:
# "there should be a fill when bloomberg doesnt have the data"; "let it backfill up to 5 days")
def _fill(conn, as_of):
    return reference.fill_book(value_book(conn, as_of), as_of,
                               lambda iso, ids: value_book(conn, iso, trade_ids=ids), REF_HOLIDAYS)


def test_fill_gives_a_trade_with_no_price_its_own_value_from_the_last_earlier_close(strict_marks):
    """No mark on Tue 09-15; the last close with one is Thu 09-10 (3 business days back).
    By hand: 1m EUR x (1.1050 - 1.1000) = 5,000 USD per trade, the 09-10 valuation whole."""
    conn = _ref_book({"2026-09-10": 1.1050, "2026-09-03": 1.2000})
    raw = value_book(conn, REF_AS_OF)
    assert raw["pnl_usd"].isna().all()
    frame, filled = _fill(conn, REF_AS_OF)
    assert filled == (("2026-09-10", 3),)
    assert list(frame["pnl_usd"]) == [pytest.approx(5000.0)] * 3 and list(frame["mark"]) == [1.1050] * 3
    assert (frame["reason"] == "").all() and list(frame.columns) == VALUATION_COLUMNS
    note = frame["note"].iloc[0]
    assert note.startswith("no price on 2026-09-15: value of the 2026-09-10 close (no FWD_OUTRIGHT mark")
    assert reference.filled_from(note) == "2026-09-10" and reference.filled_days(frame) == filled
    assert reference.fill_caption(frame, REF_AS_OF) == (
        "3 trades with no price on 2026-09-15 valued at their last earlier close (back to 2026-09-10)")
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE as_of_date = ?", (REF_AS_OF,)).fetchone()[0] == 0  # nothing written


def test_fill_reaches_five_business_days_back_over_a_holiday_and_no_further(strict_marks):
    """From Tue 09-15 the five days are 09-14, 09-11, 09-10, 09-09, 09-08 (Labor Day 09-07 is
    no business day): a price on 09-08 is in reach, one on Fri 09-04 only is not."""
    frame, filled = _fill(_ref_book({"2026-09-08": 1.1050}), REF_AS_OF)
    assert filled == (("2026-09-08", 3),) and frame["pnl_usd"].notna().all()
    frame, filled = _fill(_ref_book({"2026-09-04": 1.1050}), REF_AS_OF)
    assert filled == () and frame["pnl_usd"].isna().all() and (frame["reason"] != "").all()   # stays blank, with its reason


def test_fill_leaves_a_priced_book_alone_and_a_priced_trade_untouched():
    conn = _ref_book({REF_AS_OF: 1.1100, "2026-09-14": 1.3000})
    raw = value_book(conn, REF_AS_OF)
    frame, filled = _fill(conn, REF_AS_OF)
    assert filled == () and frame.equals(raw)


def test_value_book_for_some_trades_gives_the_rows_the_whole_book_gives():
    conn = _ref_book({REF_AS_OF: 1.1100})
    whole = value_book(conn, REF_AS_OF)
    some = value_book(conn, REF_AS_OF, trade_ids={"R2"})
    assert list(some["trade_id"]) == ["R2"]
    assert some.iloc[0].to_dict() == whole[whole["trade_id"] == "R2"].iloc[0].to_dict()


def test_reference_reads_the_fill_off_filled_frames_and_never_walks_back_twice(strict_marks):
    """The screens' reader hands `resolve_reference` frames that already carry the fill:
    the reference close is used as it is, only that one date is read, and the caption
    names the trades filled on it."""
    conn = _ref_book({REF_AS_OF: 1.1100, "2026-09-04": 1.1050})   # REF_D5 = 09-08: filled from 09-04, 2 days back
    calls = []

    def frame_for(iso):
        calls.append(iso)
        return _fill(conn, iso)[0]
    choice = reference.resolve_reference(value_book(conn, REF_AS_OF), REF_D5, frame_for, REF_HOLIDAYS, frames_filled=True)
    assert calls == [REF_D5] and (choice.found, choice.stepped_back) == (True, False)
    assert choice.filled == (("2026-09-04", 3),) and not choice.split.blocked_ids
    assert "3 trades with no price on 2026-09-08" in choice.note


# --------------------------------------------------------------------- the spot pillar (2026-09-22)
# engine.pnl.calendar.spot_date is the app's one spot-date rule: the day's SPOT sits at the
# pair's own spot date on the curve `_day_pillars` builds (T+1 for USDCAD, rolled off a holiday),
# where engine.ladder.usd_marks.spot_date (T+2 weekdays for every pair) put it before.

def test_value_book_spot_pillar_sits_at_the_pairs_own_spot_date():
    conn = _vb_conn()
    conn.execute("INSERT INTO instruments VALUES ('USDCAD','FX','USD','CAD',1.0,0,'USDCAD Curncy','9999-12-31')")
    conn.commit()
    for pair, spot, fwd in (("USDCAD", 1.35, 1.36), ("USDJPY", 150.0, 149.0)):
        _vb_fx_trade(conn, pair[3:], pair, "USD", pair[3:], 1_000_000, spot, settle="2026-06-03")   # Wed, T+2 of Mon 06-01
        _vb_mark(conn, pair, VB_AS_OF, "SPOT", spot)
        _vb_mark(conn, pair, "2026-06-30", "FWD_OUTRIGHT", fwd)
    book = value_book(conn, VB_AS_OF).set_index("trade_id")
    # USDJPY spots on 06-03: the leg IS the spot pillar
    assert book.loc["JPY", "mark"] == 150.0
    assert book.loc["JPY", "mark_source"] == "INTERP: USDJPY 2026-06-03 mark of 2026-06-01 (nearest, no earlier pillar)"
    # USDCAD spots on 06-02 (T+1): a leg on 06-03 is one day along the curve, between spot and the 06-30 pillar
    assert book.loc["CAD", "mark"] == pytest.approx(1.35 + (1 / 28) * (1.36 - 1.35))
    assert book.loc["CAD", "mark_source"] == "INTERP: between USDCAD 2026-06-02 and 2026-06-30 marks of 2026-06-01"


def test_day_pillars_put_spot_on_the_next_good_day_after_a_holiday():
    from engine.pnl.valuation import _day_pillars
    conn = _vb_conn()
    _vb_mark(conn, "USDJPY", "2026-06-17", "SPOT", 150.0, as_of="2026-06-17")   # Wed; T+2 is Fri 06-19, a config/holidays.txt holiday
    assert [d for d, _v, _s in _day_pillars(conn, "USDJPY", "2026-06-17")] == ["2026-06-22"]
