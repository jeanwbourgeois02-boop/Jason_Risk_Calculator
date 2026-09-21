"""A listed index option (EQ_OPTION, the user's live export: 'SPX/E261016P7615', 15 contracts
at 121.5 index points) is marked at Bloomberg's own price of it (user decision 2026-09-21):
the library asks for the option's own ticker like a future's, the P&L is
contracts x multiplier x (price - fill), and the index level and dividend yield are asked
for today only, for the Greeks (tests/test_options_pricing.py covers those).
"""
from __future__ import annotations

from datetime import date

import pytest

from data.bloomberg import library, live, pull_marks as pm
from data.ingest import schema
from engine.pnl.valuation import value_book

OPTION = "SPX/E261016P7615"
TICKER = "SPX US 10/16/26 P7615 Index"
EXPIRY = "2026-10-16"
AS_OF = "2026-09-21"


def _db(tmp_path):
    conn = schema.connect(tmp_path / "risk.db")
    # the rows data/ingest/blotter.py::_parse_index_option writes
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
                 (OPTION, "EQ_OPTION", "SPX", "USD", 100.0, 0, "SPX Index", EXPIRY))
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, payoff) VALUES (?,?,?,?)",
                 (OPTION, 7615.0, "PUT", "VANILLA"))
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description, theme) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("spx1", "XLSX", OPTION, "EQ_OPTION", "spx1", "2026-09-14", 15.0, 121.5, "acc", "cp", "", "t", "d", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("spx1", 1, "NOTIONAL", "USD", 15 * 100 * 7615.0, "2026-09-14", EXPIRY, 121.5, 0))
    conn.commit()
    return conn


def _mark(conn, as_of, value):
    conn.execute("INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
                 "snapped_at) VALUES (?,?,?,?,?,?,?)",
                 (as_of, OPTION, EXPIRY, "FUTURE_PX", value, "BBG_BDH", f"{as_of}T15:00:00-04:00"))
    conn.commit()


def test_the_bloomberg_ticker_of_a_listed_option():
    assert library.listed_option_ticker("SPX", EXPIRY, "PUT", 7615.0) == TICKER
    assert library.listed_option_ticker("SPX", "2026-12-18", "CALL", 7612.5) == "SPX US 12/18/26 C7612.5 Index"
    assert library.listed_option_ticker("SPX", EXPIRY, "", 7615.0) == ""      # a missing term: nothing is guessed
    assert library.listed_option_ticker("SPX", EXPIRY, "PUT", 0.0) == ""


def test_the_pull_asks_for_the_options_own_price_and_the_index_level(tmp_path):
    conn = _db(tmp_path)
    requested = {(r.instrument_id, r.mark_type, r.settle_date, r.bbg_ticker) for r in live.build_requests(conn, AS_OF)}
    assert requested == {(OPTION, "FUTURE_PX", EXPIRY, TICKER), ("SPX Index", "SPOT", AS_OF, "SPX Index")}
    assert library.keys(conn, AS_OF, library.DIV_YIELD) == ["SPX Index"]
    assert library.keys(conn, AS_OF, "OIS_CURVE") == ["USD"]
    # the index gets an instrument row, or write_marks would drop its level
    assert conn.execute("SELECT asset_class FROM instruments WHERE instrument_id = 'SPX Index'").fetchone() == ("INDEX",)
    # a past close needs the option's settlement price only: the index level is for today's Greeks
    past = {(r["kind"], r["key"]) for r in library.needed_in_range(conn, "2026-09-14", "2026-09-18")}
    assert past == {("FUTURE_PX", OPTION)}
    # nothing is asked for after expiry
    assert live.build_requests(conn, "2026-10-19") == []
    # and the Market data tab's list says what each ticker is for
    listed = {t["ticker"]: t["field"] for t in library.tickers(conn, AS_OF)}
    assert listed[TICKER] == "PX_MID" and listed["SPX Index"] in ("PX_LAST", library.DIV_YIELD_FIELDS[0])


def test_a_listed_option_is_priced_live_at_bloombergs_mid_and_a_future_still_at_its_last(monkeypatch):
    asked = {}

    def fake_reference(session, service, tickers, fields, **_kwargs):
        asked["fields"] = list(fields)
        return {TICKER: {"PX_LAST": 150.0, "PX_MID": 140.0}, "ESZ6 Index": {"PX_LAST": 6500.0, "PX_MID": 6499.0}}

    monkeypatch.setattr(pm, "fetch_reference", fake_reference)
    requests = [pm.RequestRow(OPTION, TICKER, EXPIRY, "FUTURE_PX"),
                pm.RequestRow("ESZ6 Index", "ESZ6 Index", "2026-12-18", "FUTURE_PX")]
    rows, _warnings, failures = pm.build_future_rows(None, None, requests, date(2026, 9, 21), live=True,
                                                     mid_first={TICKER})
    assert not failures and asked["fields"] == ["PX_LAST", "PX_MID"]
    got = {r["instrument_id"]: (r["value"], r["detail"], r["source"]) for r in rows}
    assert got[OPTION] == (140.0, "live PX_MID", "BBG_BDH")
    assert got["ESZ6 Index"] == (6500.0, "live PX_LAST", "BBG_BDH")


def test_pnl_is_contracts_x_multiplier_x_price_less_fill(tmp_path):
    conn = _db(tmp_path)
    row = value_book(conn, AS_OF).set_index("trade_id").loc["spx1"]
    assert row["pnl_usd"] != row["pnl_usd"]                                  # no price on file: blank, with its reason
    assert row["reason"] == f"no Bloomberg price for the listed option {OPTION} on {AS_OF}"

    _mark(conn, AS_OF, 140.0)
    row = value_book(conn, AS_OF).set_index("trade_id").loc["spx1"]
    assert row["product"] == "EQ_OPTION" and row["status"] == "OPEN" and row["reason"] == ""
    assert row["pnl_usd"] == pytest.approx(15 * 100 * (140.0 - 121.5))       # 27,750
    assert row["mark"] == 140.0 and row["mark_source"] == "BBG_BDH"


def test_the_options_tab_values_it_at_bloombergs_price_and_says_why_the_greeks_are_blank(tmp_path):
    from ui.tabs.options import _leg_rows

    conn = _db(tmp_path)
    _mark(conn, AS_OF, 140.0)
    (leg,) = _leg_rows(conn, AS_OF)
    assert leg["priced"] and leg["mktval"] == pytest.approx(15 * 140.0 * 100)     # $210,000
    assert leg["start_value_usd"] == pytest.approx(15 * 121.5 * 100)              # $182,250
    assert leg["pnl_usd"] == pytest.approx(27_750.0)
    assert leg["delta"] is None and leg["note"].startswith("Greeks not calculated")


def test_an_expired_listed_option_freezes_at_its_last_price(tmp_path):
    from engine.pnl.ledger import realise_settled

    conn = _db(tmp_path)
    _mark(conn, EXPIRY, 95.0)
    realise_settled(conn, "2026-10-19")
    row = value_book(conn, "2026-10-19").set_index("trade_id").loc["spx1"]
    assert row["status"] == "SETTLED"
    assert row["pnl_usd"] == pytest.approx(15 * 100 * (95.0 - 121.5))
