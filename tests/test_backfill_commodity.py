"""data/bloomberg/backfill.py and commodity futures (2026-09-24, commodity conversion plan).

A commodity future is stored under its canonical two-digit-year id ('CLZ26 Comdty') with
Bloomberg's live one-digit form as bbg_ticker ('CLZ6 Comdty'). Bloomberg names an expired
contract with the two-digit year, so the backfill asks each contract's history under the
form valid on the day of the request (data.contracts.request_ticker), writes the close on
the instrument's own id and expiry, never asks a placeholder with no ticker, and backfills a
non-USD future's USD-conversion close from its library row. No blpapi."""
from datetime import date, datetime, timezone

import pytest

from data.bloomberg import backfill, library
from data.bloomberg.live import _usd_pair_name
from data.ingest import schema

TODAY = date(2026, 9, 21)


@pytest.fixture(autouse=True)
def _pinned_clock_and_calendar(monkeypatch, tmp_path):
    """Today pinned (the tests' days sit inside the intraday reach of it) and a
    Monday-to-Friday calendar, as in tests/test_backfill.py."""
    from data.bloomberg import live as _live
    import engine.pnl.calendar as cal
    monkeypatch.setattr(_live, "book_today", lambda: TODAY)
    monkeypatch.setattr(cal, "_DEFAULT_HOLIDAYS_PATH", tmp_path / "no-holidays.txt")


def _instrument(conn, instrument_id, root, quote, multiplier, ticker, expiry):
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
                 (instrument_id, "FUTURE", root, quote, multiplier, 0, ticker, expiry))


def _future_trade(conn, trade_id, instrument_id, quote, contracts, price, multiplier, expiry, trade_date="2026-08-10"):
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (trade_id, "XLSX", instrument_id, "FUTURE", trade_id, trade_date, contracts, price,
                  "acc", "cp", "", "t", "d", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "NOTIONAL", quote, contracts * multiplier * price, trade_date, expiry, 0, 0))


def _no_fx(*_args, **_kwargs):
    return {}


def _daily(asked, values):
    def fetch(session, service, tickers, fields, start, end):
        asked.append((sorted(tickers), list(fields), start, end))
        return {t: {d.isoformat(): {"PX_LAST": values[t]} for d in backfill.business_days(start, end)}
                for t in tickers if t in values}
    return fetch


def _future_marks(conn):
    return conn.execute("SELECT as_of_date, instrument_id, settle_date, value, source, snapped_at FROM marks "
                        "WHERE mark_type = 'FUTURE_PX' ORDER BY instrument_id").fetchall()


def test_expired_contract_is_asked_under_its_two_digit_id_a_live_one_under_the_one_digit_form(tmp_path):
    """CL Aug 2026 expired (estimated last trade 2026-08-31) before the request day: its
    history is asked as 'CLQ26 Comdty', not the stored 'CLQ6 Comdty' (Bloomberg's name for
    Aug 2036 by now). Corn Dec 2026 still trades: 'C Z6 Comdty'. ES, a macro future whose
    base_ccy is no contract root, is asked under its own ticker as before. Every close is
    PX_LAST, written on the instrument's own id and expiry, stamped 17:00 New York."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    _instrument(conn, "CLQ26 Comdty", "NYMEX:CL", "USD", 1000, "CLQ6 Comdty", "2026-08-31")
    _instrument(conn, "C Z26 Comdty", "CBOT:ZC", "USD", 50, "C Z6 Comdty", "2026-12-31")
    _instrument(conn, "ESU6 Index", "ES", "USD", 50, "ESU6 Index", "2026-12-19")
    _future_trade(conn, "cl1", "CLQ26 Comdty", "USD", 2, 70.0, 1000, "2026-08-31")
    _future_trade(conn, "c1", "C Z26 Comdty", "USD", -3, 4.20, 50, "2026-12-31")
    _future_trade(conn, "es1", "ESU6 Index", "USD", 1, 7500.0, 50, "2026-12-19")
    conn.commit()
    asked = []
    fut = _daily(asked, {"CLQ26 Comdty": 71.5, "C Z6 Comdty": 4.31, "ESU6 Index": 7550.0,
                         "CLQ6 Comdty": -1.0})   # the stale one-digit form must never be the one read
    day = date(2026, 8, 28)
    results = backfill.backfill(p, day, day, fetch=_no_fx, fwd_fetch=_no_fx, fut_fetch=fut, today=TODAY,
                                log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    assert asked == [(["C Z6 Comdty", "CLQ26 Comdty", "ESU6 Index"], ["PX_LAST"], day, day)]
    stamp = backfill.settle_stamp(day)
    assert stamp == "2026-08-28T17:00:00-04:00"
    assert _future_marks(conn) == [
        ("2026-08-28", "C Z26 Comdty", "2026-12-31", 4.31, "BBG_BDH", stamp),
        ("2026-08-28", "CLQ26 Comdty", "2026-08-31", 71.5, "BBG_BDH", stamp),
        ("2026-08-28", "ESU6 Index", "2026-12-19", 7550.0, "BBG_BDH", stamp)]
    assert all(backfill.is_close_row("FUTURE_PX", "2026-08-28", m[5], TODAY) for m in _future_marks(conn))


def test_the_request_form_follows_the_request_day_not_the_historical_day(tmp_path):
    """The same past day asked before the contract expired uses the one-digit form."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    _instrument(conn, "CLQ26 Comdty", "NYMEX:CL", "USD", 1000, "CLQ6 Comdty", "2026-08-31")
    _future_trade(conn, "cl1", "CLQ26 Comdty", "USD", 2, 70.0, 1000, "2026-08-31")
    conn.commit()
    asked = []
    day = date(2026, 8, 20)
    backfill.backfill(p, day, day, fetch=_no_fx, fwd_fetch=_no_fx,
                      fut_fetch=_daily(asked, {"CLQ6 Comdty": 69.0}), today=date(2026, 8, 25), log=lambda *_: None)
    assert asked == [(["CLQ6 Comdty"], ["PX_LAST"], day, day)]
    assert [m[1:4] for m in _future_marks(conn)] == [("CLQ26 Comdty", "2026-08-31", 69.0)]


def test_a_future_with_no_bloomberg_ticker_is_never_asked(tmp_path):
    """A placeholder root ('ZZSS' for SHFE stainless) is booked with bbg_ticker ''. The
    library flags its FUTURE_PX not requestable and leaves it out of what a past close
    needs (its default filter, the one the backfill uses), listing the gap itself with its
    reason: nothing is asked for it, and the backfill's day does not count it missing."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    _instrument(conn, "ZZSSZ26 Comdty", "SHFE:SS", "CNY", 5, "", "2026-12-15")
    _instrument(conn, "ESU6 Index", "ES", "USD", 50, "ESU6 Index", "2026-12-19")
    _future_trade(conn, "ss1", "ZZSSZ26 Comdty", "CNY", 4, 13000.0, 5, "2026-12-15")
    _future_trade(conn, "es1", "ESU6 Index", "USD", 1, 7500.0, 50, "2026-12-19")
    conn.commit()
    asked = []
    day = date(2026, 9, 8)
    results = backfill.backfill(p, day, day, fetch=lambda s, v, tickers, f, d: {t: 7.1 for t in tickers},
                                fwd_fetch=_no_fx, fut_fetch=_daily(asked, {"ESU6 Index": 7550.0}), today=TODAY,
                                log=lambda *_: None)
    assert asked == [(["ESU6 Index"], ["PX_LAST"], day, day)]
    assert [m[1] for m in _future_marks(conn)] == ["ESU6 Index"]
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    listed = [r for r in library.needed_in_range(conn, "2026-09-08", "2026-09-08", include_unrequestable=True)
              if r["key"] == "ZZSSZ26 Comdty"]
    assert listed and not listed[0]["requestable"] and "SHFE:SS" in listed[0]["reason"]


def test_request_tickers_name_what_they_will_not_ask():
    """`_future_request_tickers` directly: a commodity id the contract universe cannot read
    back (a one-digit year: its decade would be a guess) is not asked, with its reason; a
    listed option's row and a macro future keep the library ticker."""
    conn = schema.connect(":memory:")
    _instrument(conn, "CLZ6 Comdty", "NYMEX:CL", "USD", 1000, "CLZ6 Comdty", "2026-12-31")
    _instrument(conn, "CLZ26 Comdty", "NYMEX:CL", "USD", 1000, "CLZ6 Comdty", "2026-12-31")
    _instrument(conn, "ESZ6 Index", "ES", "USD", 50, "ESZ6 Index", "2026-12-18")
    rows = [
        {"kind": "FUTURE_PX", "key": "CLZ6 Comdty", "bbg_ticker": "CLZ6 Comdty"},
        {"kind": "FUTURE_PX", "key": "CLZ26 Comdty", "bbg_ticker": "CLZ6 Comdty"},
        {"kind": "FUTURE_PX", "key": "ESZ6 Index", "bbg_ticker": "ESZ6 Index"},
        {"kind": "FUTURE_PX", "key": "SPX/E261016P7615-USAA", "bbg_ticker": "SPX US 10/16/26 P7615 Index"},
        {"kind": "SPOT", "key": "USDCNY", "bbg_ticker": "USDCNY Curncy"},
    ]
    asked, not_asked = backfill._future_request_tickers(conn, rows, date(2027, 1, 15))
    assert asked == {"CLZ26 Comdty": "CLZ26 Comdty", "ESZ6 Index": "ESZ6 Index",
                     "SPX US 10/16/26 P7615 Index": "SPX/E261016P7615-USAA"}
    assert set(not_asked) == {"CLZ6 Comdty"}
    assert "NYMEX:CL" in not_asked["CLZ6 Comdty"] and "not asked of Bloomberg" in not_asked["CLZ6 Comdty"]


def test_a_cny_futures_usd_conversion_close_is_backfilled_from_its_library_row(tmp_path):
    """bbg-library lists, for every non-USD future, the SPOT of its currency's USD pair (role
    CONVERSION) from trade date to expiry. The backfill reads the library as it is: the
    USDCNY close is asked and written at the 15:00 New York close, and the rebar contract's
    own history under its one-digit form. The row is put in by hand when this checkout's
    library does not list it yet, so the test holds either way."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    _instrument(conn, "RBTF27 Comdty", "SHFE:RB", "CNY", 10, "RBTF7 Comdty", "2027-01-15")
    _future_trade(conn, "rb1", "RBTF27 Comdty", "CNY", 5, 3200.0, 10, "2027-01-15")
    conn.commit()
    library.sync(conn)
    pair = _usd_pair_name("CNY")
    assert pair == "USDCNY"
    conn.execute("INSERT OR IGNORE INTO bbg_library (trade_id, kind, key, settle_date, bbg_ticker, role, product, "
                 "needed_from, needed_until, added_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("rb1", "SPOT", pair, library.SENTINEL, f"{pair} Curncy", library.ROLE_CONVERSION, "FUTURE",
                  "2026-08-10", "2027-01-15", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    spot_asked, fut_asked = [], []

    def spot(session, service, tickers, field, day):
        spot_asked.append((sorted(tickers), field, day))
        return {"USDCNY Curncy": 7.1234}

    day = date(2026, 9, 8)
    results = backfill.backfill(p, day, day, fetch=spot, fwd_fetch=_no_fx,
                                fut_fetch=_daily(fut_asked, {"RBTF7 Comdty": 3250.0}), today=TODAY,
                                log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    assert results[0]["missing_pairs"] == []
    assert spot_asked == [(["USDCNY Curncy"], "PX_LAST", day)]
    assert fut_asked == [(["RBTF7 Comdty"], ["PX_LAST"], day, day)]
    spot_rows = conn.execute("SELECT as_of_date, instrument_id, settle_date, value, source, snapped_at FROM marks "
                             "WHERE mark_type = 'SPOT'").fetchall()
    assert spot_rows == [("2026-09-08", "USDCNY", "2026-09-08", 7.1234, "BBG_BFXFORWARD",
                          backfill.close_stamp(day, TODAY))]
    assert spot_rows[0][5] == "2026-09-08T15:00:00-04:00"
    assert [m[1:4] for m in _future_marks(conn)] == [("RBTF27 Comdty", "2027-01-15", 3250.0)]
