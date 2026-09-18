"""Tests for data/ingest/manual.py (manual OTC trade booking, 2026-09-18) and the
upload's MANUAL exemption in data/ingest/upload.py."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data.ingest import manual, schema
from data.ingest.upload import import_blotter

RAW_BLOTTER = Path(__file__).resolve().parents[1] / "data/sample/blotter_sample.csv"


def _db(path=":memory:"):
    return schema.connect(path)


# --------------------------------------------------------------------------- options
def test_book_fx_option_writes_instrument_terms_trade_and_leg():
    conn = _db()
    trade_id = manual.book_fx_option(
        conn, pair="USDJPY", side="Buy", option_type="Put", payoff="DIGITAL", strike=147,
        expiry="2027-03-01", notional=10_000_000, premium=0.1025, trade_date="2026-09-01",
        counterparty="MLILUK")
    assert trade_id == "MANUAL-1"
    inst = conn.execute("SELECT * FROM instruments WHERE asset_class = 'FX_OPTION'").fetchone()
    assert inst == ("USDJPY030127P-MANUAL1", "FX_OPTION", "USD", "JPY", 1.0, 0, "USDJPY030127P-MANUAL1", "2027-03-01")
    terms = conn.execute("SELECT strike, option_type, barrier_level, payoff FROM instrument_options").fetchone()
    assert terms == (147.0, "PUT", 0.0, "DIGITAL")
    trade = conn.execute("SELECT source, product, package_id, trade_date, quantity, price, counterparty "
                         "FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    assert trade == ("MANUAL", "FX_OPTION", trade_id, "2026-09-01", 10_000_000.0, 0.1025, "MLILUK")
    leg = conn.execute("SELECT leg_no, leg_type, ccy, amount, settle_date, rate, settles_cash FROM trade_legs "
                       "WHERE trade_id = ?", (trade_id,)).fetchone()
    assert leg == (1, "NOTIONAL", "USD", 10_000_000.0, "2027-03-01", 0.1025, 0)
    # The plain pair instrument is created too, so a SPOT mark for it can be written.
    assert conn.execute("SELECT bbg_ticker FROM instruments WHERE instrument_id = 'USDJPY'").fetchone() == ("USDJPY Curncy",)
    # Visible to every engine query.
    assert conn.execute("SELECT COUNT(*) FROM trades_official WHERE trade_id = ?", (trade_id,)).fetchone()[0] == 1


def test_book_fx_option_sell_is_negative_quantity_and_ids_follow_the_highest_on_file():
    conn = _db()
    t1 = manual.book_fx_option(conn, pair="EURSEK", side="Sell", option_type="Call", strike=11.4,
                               expiry="2026-11-25", notional=1_000_000, premium=0.121, trade_date="2026-08-24")
    assert conn.execute("SELECT quantity FROM trades WHERE trade_id = ?", (t1,)).fetchone()[0] == -1_000_000.0
    t2 = manual.book_fx_forward(conn, pair="EURUSD", side="Buy", base_amount=1e6, rate=1.1,
                                value_date="2026-12-15", trade_date="2026-09-18")
    assert (t1, t2) == ("MANUAL-1", "MANUAL-2")
    manual.delete_manual_trade(conn, t2)
    t3 = manual.book_fx_option(conn, pair="EURSEK", side="Buy", option_type="Call", strike=11.4,
                               expiry="2026-11-25", notional=1_000_000, premium=0.121, trade_date="2026-08-24")
    assert t3 == "MANUAL-2"  # 1 + the highest number still on file (the option's own instrument counts too)
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE trade_id = 'MANUAL-2'").fetchone()[0] == 1


def test_book_fx_option_tolerates_formatting_but_rejects_contradictions():
    conn = _db()
    # Formatting variation is accepted: pair with a slash, date as d/m/Y, amount with commas.
    trade_id = manual.book_fx_option(conn, pair="eur/sek", side="b", option_type="c", strike="11.40",
                                     expiry="25/11/2026", notional="1,000,000", premium="0.121",
                                     trade_date="24/8/2026")
    row = conn.execute("SELECT instrument_id, quantity FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    assert row == ("EURSEK112526C-MANUAL1", 1_000_000.0)
    with pytest.raises(ValueError, match="strike"):
        manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", payoff="DIGITAL", strike=0,
                              expiry="2027-03-01", notional=1e6, premium=0.1)
    with pytest.raises(ValueError, match="expiry"):
        manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", strike=150,
                              expiry="2026-01-01", notional=1e6, premium=0.1, trade_date="2026-09-18")
    with pytest.raises(ValueError, match="six letters"):
        manual.book_fx_option(conn, pair="USD", side="Buy", option_type="Put", strike=150,
                              expiry="2027-01-01", notional=1e6, premium=0.1)
    with pytest.raises(ValueError, match="barrier"):
        manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", payoff="ONE_TOUCH",
                              expiry="2027-01-01", notional=1e6, premium=0.1)
    with pytest.raises(ValueError, match="notional"):
        manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", strike=150,
                              expiry="2027-01-01", notional=0, premium=0.1)
    with pytest.raises(ValueError, match="payoff"):
        manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", payoff="LOOKBACK", strike=150,
                              expiry="2027-01-01", notional=1e6, premium=0.1)
    # Nothing half-written by the rejected calls.
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


# --------------------------------------------------------------------------- forwards
def test_book_fx_forward_writes_two_legs_like_the_blotter_parser():
    conn = _db()
    trade_id = manual.book_fx_forward(conn, pair="EURSEK", side="Sell", base_amount=2_000_000, rate=11.05,
                                      value_date="2026-12-15", trade_date="2026-09-18")
    trade = conn.execute("SELECT source, product, quantity, price FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    assert trade == ("MANUAL", "FX_FWD", -2_000_000.0, 11.05)
    legs = conn.execute("SELECT leg_no, leg_type, ccy, amount, settle_date, settles_cash FROM trade_legs "
                        "WHERE trade_id = ? ORDER BY leg_no", (trade_id,)).fetchall()
    assert legs == [(1, "FX_NEAR", "EUR", -2_000_000.0, "2026-12-15", 1),
                    (2, "FX_NEAR", "SEK", 22_100_000.0, "2026-12-15", 1)]


def test_book_fx_forward_ndf_legs_settle_no_cash():
    conn = _db()
    trade_id = manual.book_fx_forward(conn, pair="USDBRL", side="Buy", base_amount=1_000_000, rate=5.4,
                                      value_date="2026-12-15", trade_date="2026-09-18")
    assert conn.execute("SELECT is_ndf FROM instruments WHERE instrument_id = 'USDBRL'").fetchone()[0] == 1
    assert {r[0] for r in conn.execute("SELECT settles_cash FROM trade_legs WHERE trade_id = ?", (trade_id,))} == {0}


def test_book_fx_forward_rejects_value_date_before_trade_date():
    conn = _db()
    with pytest.raises(ValueError, match="value date"):
        manual.book_fx_forward(conn, pair="EURUSD", side="Buy", base_amount=1e6, rate=1.1,
                               value_date="2026-09-01", trade_date="2026-09-18")


def test_manual_trade_inherits_its_pair_bundle():
    conn = _db()
    conn.execute("INSERT INTO instruments VALUES ('EURSEK','FX','EUR','SEK',1,0,'EURSEK Curncy','9999-12-31')")
    conn.execute("INSERT INTO instrument_theme VALUES ('EURSEK', 'Nordics')")
    conn.commit()
    trade_id = manual.book_fx_forward(conn, pair="EURSEK", side="Buy", base_amount=1e6, rate=11.0,
                                      value_date="2026-12-15", trade_date="2026-09-18")
    assert conn.execute("SELECT theme FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()[0] == "Nordics"


# --------------------------------------------------------------------------- listing / delete
def test_manual_trades_lists_newest_first_with_terms():
    conn = _db()
    manual.book_fx_forward(conn, pair="EURUSD", side="Buy", base_amount=1e6, rate=1.1,
                           value_date="2026-12-15", trade_date="2026-09-01")
    manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", payoff="DIGITAL", strike=147,
                          expiry="2027-03-01", notional=1e7, premium=0.1025, trade_date="2026-09-02")
    rows = manual.manual_trades(conn)
    assert [r["trade_id"] for r in rows] == ["MANUAL-2", "MANUAL-1"]
    assert rows[0]["payoff"] == "DIGITAL" and rows[0]["strike"] == 147.0 and rows[0]["settle_date"] == "2027-03-01"
    assert rows[1]["product"] == "FX_FWD" and rows[1]["settle_date"] == "2026-12-15"


def test_delete_manual_trade_removes_option_instrument_terms_and_marks():
    conn = _db()
    trade_id = manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", strike=150,
                                     expiry="2027-03-01", notional=1e6, premium=0.1, trade_date="2026-09-01")
    inst = conn.execute("SELECT instrument_id FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()[0]
    conn.execute("INSERT INTO marks VALUES ('2026-09-18', ?, '2027-03-01', 'PREMIUM', 0.12, 'QL_OPTIONS_PRICER', 'x')", (inst,))
    conn.execute("INSERT INTO realised_pnl VALUES (?, ?, 'FX_OPTION', 'USD', '2027-03-01', 0, 0, 'PREMIUM', 1, "
                 "'2027-03-01', 'QL_OPTIONS_PRICER', 0, 'now', '')", (trade_id, inst))
    conn.commit()
    manual.delete_manual_trade(conn, trade_id)
    for table in ("trades", "trade_legs", "realised_pnl"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE trade_id = ?", (trade_id,)).fetchone()[0] == 0
    for table in ("instruments", "instrument_options", "marks"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE instrument_id = ?", (inst,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM instruments WHERE instrument_id = 'USDJPY'").fetchone()[0] == 1  # pair kept


def test_delete_refuses_blotter_trades_and_unknown_ids():
    conn = _db()
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('T1','XLSX','EURUSD','FX_FWD','T1','2026-06-01',1e6,1.1,'','','','','','')")
    conn.commit()
    with pytest.raises(ValueError, match="loaded from the blotter"):
        manual.delete_manual_trade(conn, "T1")
    with pytest.raises(ValueError, match="no trade"):
        manual.delete_manual_trade(conn, "MANUAL-99")


def test_delete_dissolves_a_swap_package_the_trade_was_part_of():
    conn = _db()
    # CLAUDE.md package rule: same source/account/pair/trade date, opposite signs, equal
    # |USD leg| within 0.01 %, different value dates -- a USD-base pair keeps the USD leg
    # equal to the base amount on both legs.
    a = manual.book_fx_forward(conn, pair="USDJPY", side="Buy", base_amount=1e6, rate=147.0,
                               value_date="2026-10-01", trade_date="2026-09-18")
    b = manual.book_fx_forward(conn, pair="USDJPY", side="Sell", base_amount=1e6, rate=147.5,
                               value_date="2026-12-01", trade_date="2026-09-18")
    from data.ingest.swaps import package_swaps
    assert package_swaps(conn) == 2
    assert conn.execute("SELECT product FROM trades WHERE trade_id = ?", (a,)).fetchone()[0] == "FX_SWAP"
    manual.delete_manual_trade(conn, a)
    assert conn.execute("SELECT product, package_id FROM trades WHERE trade_id = ?", (b,)).fetchone() == ("FX_FWD", b)


# --------------------------------------------------------------------------- upload exemption
def test_full_replace_upload_keeps_manual_trades(tmp_path):
    db = tmp_path / "risk.db"
    conn = schema.connect(db)
    opt = manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", payoff="DIGITAL", strike=147,
                                expiry="2027-03-01", notional=1e7, premium=0.1025, trade_date="2026-09-01")
    fwd = manual.book_fx_forward(conn, pair="EURSEK", side="Sell", base_amount=2e6, rate=11.05,
                                 value_date="2026-12-15", trade_date="2026-09-18")
    conn.close()
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    message = import_blotter(payload, RAW_BLOTTER.name, db)  # a second upload replaces the blotter book only
    with sqlite3.connect(db) as conn:
        by_source = dict(conn.execute("SELECT source, COUNT(*) FROM trades GROUP BY source").fetchall())
        manual_legs = conn.execute("SELECT COUNT(*) FROM trade_legs WHERE trade_id IN (?, ?)", (opt, fwd)).fetchone()[0]
        terms = conn.execute("SELECT strike FROM instrument_options WHERE instrument_id LIKE '%MANUAL%'").fetchone()
    assert by_source == {"MANUAL": 2, "XLSX": 772}
    assert manual_legs == 3
    assert terms == (147.0,)
    assert "Replaced the previous book: 772 trade(s)" in message  # the count never includes the manual ones
