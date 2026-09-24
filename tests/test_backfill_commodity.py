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


# --------------------------------------------------------------------------- Phase 5 (2026-09-24)
# Options on commodity futures (CMDTY_OPTION) and LME forwards (LME_FWD): what the backfill
# asks of Bloomberg's history for a past close, and what it writes.

_INSTRUMENT = ("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
               "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)")
_TRADE = "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
_LEG = "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)"


def _option_db(tmp_path, option_id, root, ticker, expiry, und_id, und_ticker, und_expiry, trade_date):
    """One option on a commodity future, booked as ingest books it: its CMDTY_OPTION instrument,
    its underlying future's instrument with no trade of its own, one trade and its NOTIONAL leg
    (settle date = the option's expiry)."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute(_INSTRUMENT, (option_id, "CMDTY_OPTION", root, "USD", 1000, 0, ticker, expiry))
    conn.execute(_INSTRUMENT, (und_id, "FUTURE", root, "USD", 1000, 0, und_ticker, und_expiry))
    conn.execute(_TRADE, ("opt1", "XLSX", option_id, "CMDTY_OPTION", "opt1", trade_date, 10, 2.15,
                          "acc", "cp", "", "t", "d", ""))
    conn.execute(_LEG, ("opt1", 1, "NOTIONAL", "USD", 10 * 1000 * 2.15, trade_date, expiry, 2.15, 0))
    conn.commit()
    return p, conn


def _ois_history(session, service, tickers, fields, start, end):
    """The USD OIS quotes the option's Greeks discount on (a CMDTY option needs no smile)."""
    return {t: {d.isoformat(): {f: 4.0 for f in fields} for d in backfill.business_days(start, end)} for t in tickers}


def _pricer(monkeypatch, events, result):
    """price_close as a recording fake: it counts the day's FUTURE_PX on file when asked, so
    'prices written -> options priced' is proved, not assumed."""
    def fake_price_close(conn, day):
        n = conn.execute("SELECT COUNT(*) FROM marks_official WHERE as_of_date = ? AND mark_type = 'FUTURE_PX'",
                         (day,)).fetchone()[0]
        events.append(("price_close", day, n))
        return dict(result, day=day)

    def fake_realise_settled(conn, day, **kwargs):
        events.append(("realise_settled", day))
        return {"realised": 0, "unrealisable": []}

    monkeypatch.setattr(backfill, "_import_price_close", lambda: fake_price_close)
    monkeypatch.setattr(backfill, "_import_realise_settled", lambda: fake_realise_settled)


def test_an_expired_option_and_its_untraded_underlying_are_asked_under_their_canonical_ids(tmp_path, monkeypatch):
    """COMEX gold Aug 2026 3300 call, expired 2026-07-28: on the request day (2026-09-21) its
    stored one-digit ticker 'GCQ6C 3300 Comdty' names the 2036 option, so its history is asked
    as 'GCQ26C 3300 Comdty' (data.contracts.option_request_ticker). Its underlying GCQ26, booked
    as an instrument only (no trade), is asked too, under its expired two-digit id, for the
    Greeks (library role UNDERLYING). Both closes land on their own ids and expiries at 17:00."""
    p, conn = _option_db(tmp_path, "GCQ26C 3300 Comdty", "COMEX:GC", "GCQ6C 3300 Comdty", "2026-07-28",
                         "GCQ26 Comdty", "GCQ6 Comdty", "2026-08-27", "2026-07-06")
    events = []
    _pricer(monkeypatch, events, {"priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 1})
    asked = []
    fut = _daily(asked, {"GCQ26C 3300 Comdty": 51.0, "GCQ26 Comdty": 3340.0,
                         "GCQ6C 3300 Comdty": -1.0, "GCQ6 Comdty": -1.0})   # the stale forms must never be read
    day = date(2026, 7, 20)
    results = backfill.backfill(p, day, day, fetch=_no_fx, fwd_fetch=_no_fx, fut_fetch=fut, quote_fetch=_ois_history,
                                today=TODAY, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    assert asked == [(["GCQ26 Comdty", "GCQ26C 3300 Comdty"], ["PX_LAST"], day, day)]
    stamp = backfill.settle_stamp(day)
    assert _future_marks(conn) == [
        ("2026-07-20", "GCQ26 Comdty", "2026-08-27", 3340.0, "BBG_BDH", stamp),
        ("2026-07-20", "GCQ26C 3300 Comdty", "2026-07-28", 51.0, "BBG_BDH", stamp)]
    # its OIS curve (USD) came from the history for the day, no vol smile was asked
    assert results[0]["curve_quotes"] > 0 and results[0]["vol_quotes"] == 0 and results[0]["missing_inputs"] == []
    # asked while the option still traded: the one-digit live form
    rows = [r for r in library.needed_in_range(conn, "2026-07-20", "2026-07-20") if r["kind"] == "FUTURE_PX"]
    asked_live, not_asked = backfill._future_request_tickers(conn, rows, date(2026, 7, 21))
    assert asked_live == {"GCQ6C 3300 Comdty": "GCQ26C 3300 Comdty", "GCQ6 Comdty": "GCQ26 Comdty"}
    assert not_asked == {}


def test_an_option_id_the_contract_universe_cannot_read_is_named_not_asked():
    conn = schema.connect(":memory:")
    conn.execute(_INSTRUMENT, ("CLZ6C 75 Comdty", "CMDTY_OPTION", "NYMEX:CL", "USD", 1000, 0, "CLZ6C 75 Comdty",
                               "2026-11-17"))
    asked, not_asked = backfill._future_request_tickers(
        conn, [{"kind": "FUTURE_PX", "key": "CLZ6C 75 Comdty", "bbg_ticker": "CLZ6C 75 Comdty"}], TODAY)
    assert asked == {} and set(not_asked) == {"CLZ6C 75 Comdty"}
    reason = not_asked["CLZ6C 75 Comdty"]
    assert "an option of NYMEX:CL" in reason and "not asked of Bloomberg" in reason


def test_a_day_with_only_commodity_options_open_is_priced_after_its_prices_land(tmp_path, monkeypatch):
    """A WTI Dec 2026 75 call and nothing else (no FX option): price_close is still called on
    each day it is open, after that day's FUTURE_PX of the option and of its underlying are on
    file, and its `futures_options_priced` is carried on the day and in the status block."""
    p, conn = _option_db(tmp_path, "CLZ26C 75 Comdty", "NYMEX:CL", "CLZ6C 75 Comdty", "2026-11-17",
                         "CLZ26 Comdty", "CLZ6 Comdty", "2026-11-19", "2026-09-15")
    assert backfill._options_open_on(conn, "2026-09-14") is False       # before its trade date
    assert backfill._options_open_on(conn, "2026-09-15") is True
    assert backfill._options_open_on(conn, "2026-11-17") is True        # its NOTIONAL leg's settle date
    assert backfill._options_open_on(conn, "2026-11-18") is False
    events = []
    _pricer(monkeypatch, events, {"priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 1})
    fut = _daily([], {"CLZ6C 75 Comdty": 2.40, "CLZ6 Comdty": 71.2})
    results = backfill.backfill(p, date(2026, 9, 15), date(2026, 9, 16), fetch=_no_fx, fwd_fetch=_no_fx, fut_fetch=fut,
                                quote_fetch=_ois_history, today=TODAY, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert events == [("price_close", "2026-09-15", 2), ("realise_settled", "2026-09-15"),
                      ("price_close", "2026-09-16", 2), ("realise_settled", "2026-09-16")]
    assert [(r["options_priced"], r["futures_options_priced"]) for r in results] == [(1, 1), (1, 1)]
    assert backfill._skipped("2026-09-17")["futures_options_priced"] is None

    # the status file's options block says so per worked day
    from data.bloomberg import live
    thread = backfill.start_auto_backfill(p, fetch=_no_fx, fwd_fetch=_no_fx, fut_fetch=fut,
                                          session_factory=lambda: (None, None), quote_fetch=_ois_history)
    thread.join(timeout=30)
    block = live.read_status(p)["backfill"]
    assert block["options"] and all(v["futures_options_priced"] == 1 for v in block["options"].values())


# ---- LME forwards
LME_DAY = date(2026, 9, 15)


def _lme_db(tmp_path):
    """An AUDUSD forward (the FX paths run beside the LME step) and an LME copper ticket,
    100 t, traded 2026-09-10 for prompt 2026-12-10, legs as ingest books them."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute(_INSTRUMENT, ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"))
    conn.execute(_INSTRUMENT, ("LME:CA", "LME_FWD", "LME:CA", "USD", 1, 0, "LMCADY Comdty", "9999-12-31"))
    conn.execute(_TRADE, ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-09-10", -1e6, 0.65, "acc", "cp", "", "t", "d", ""))
    conn.execute(_LEG, ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-09-10", "2026-10-20", 0.65, 1))
    conn.execute(_LEG, ("a1", 2, "FX_NEAR", "USD", 650000, "2026-09-10", "2026-10-20", 0.65, 1))
    conn.execute(_TRADE, ("ca", "XLSX", "LME:CA", "LME_FWD", "ca", "2026-09-10", 100.0, 9850.0,
                          "acc", "cp", "", "t", "d", ""))
    conn.execute(_LEG, ("ca", 1, "FX_NEAR", "LME:CA", 100.0, "2026-09-10", "2026-12-10", 9850.0, 0))
    conn.execute(_LEG, ("ca", 2, "FX_NEAR", "USD", -985000.0, "2026-09-10", "2026-12-10", 9850.0, 1))
    conn.commit()
    return p, conn


def _fx_spot(session, service, tickers, field, day):
    assert set(tickers) <= {"AUDUSD Curncy"}, tickers          # never an LME name as an FX pair
    return {t: 0.655 for t in tickers}


def _fx_fwd(session, service, tickers, fields, start, end):
    assert all(t.startswith("AUDUSD") for t in tickers), tickers  # no 'LME:CA1M Curncy' tenor ask
    return {t: {d.isoformat(): {"PX_LAST": 0.656, "SETTLE_DT": "2026-10-20"} for d in backfill.business_days(start, end)}
            for t in tickers}


def _lme_values():
    pillars = library.lme_curve_pillars("LME:CA", LME_DAY.isoformat(), through="2026-12-10")
    values = {p["ticker"]: 9800.0 + 10 * i for i, p in enumerate(pillars)}
    return pillars, values


def _lme_marks(conn):
    return conn.execute("SELECT as_of_date, settle_date, mark_type, value, source, snapped_at FROM marks "
                        "WHERE instrument_id = 'LME:CA' ORDER BY mark_type DESC, settle_date").fetchall()


def test_an_lme_stretch_writes_cash_as_spot_and_the_curve_as_forwards_at_the_daily_close(tmp_path):
    """The pillars (cash, the monthlies up to the furthest prompt, 3M) are asked of the daily
    history once for the stretch, PX_LAST, with the futures' fetcher; the FX closes and tenors
    never see an LME name. fwd_curve.lme_history_marks builds the rows and they are written as
    they come: the cash price as the metal's SPOT under BBG_BFXFORWARD keyed on the day itself
    (settle_date = as_of_date: the only key the P&L reads it by), the pillars and the ticket's
    prompt as FWD_OUTRIGHT, every row stamped 17:00 New York. A live press's row of the day is
    not a close and is replaced."""
    p, conn = _lme_db(tmp_path)
    day = LME_DAY.isoformat()
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (day, "LME:CA", day, "SPOT", 9700.0, "BBG_BFXFORWARD", f"{day}T11:40:00-04:00"))
    conn.commit()
    pillars, values = _lme_values()
    asked = []
    results = backfill.backfill(p, LME_DAY, LME_DAY, fetch=_fx_spot, fwd_fetch=_fx_fwd, fut_fetch=_daily(asked, values),
                                today=TODAY, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    assert asked == [(sorted(values), ["PX_LAST"], LME_DAY, LME_DAY)]
    assert "LMCADY Comdty" in values and "LMCADS03 Comdty" in values
    stamp = backfill.settle_stamp(LME_DAY)
    assert stamp == f"{day}T17:00:00-04:00"
    marks = _lme_marks(conn)
    spot = [m for m in marks if m[2] == "SPOT"]
    assert spot == [(day, day, "SPOT", values["LMCADY Comdty"], "BBG_BFXFORWARD", stamp)]   # the live press replaced
    fwd = {m[1]: m for m in marks if m[2] == "FWD_OUTRIGHT"}
    assert {x["settle_date"] for x in pillars if x["kind"] != "CASH"} | {"2026-12-10"} == set(fwd)
    assert all(m[5] == stamp and m[4] in ("BBG_BFXFORWARD", "BBG_INTERP") for m in fwd.values())
    assert values["LPX6 Comdty"] < fwd["2026-12-10"][3] < values["LMCADS03 Comdty"]   # between its pillars
    assert results[0]["lme_marks"] == len(marks)
    # the official view serves them: the cash price as SPOT at the day, the prompt outright
    assert conn.execute("SELECT source FROM marks_official WHERE instrument_id = 'LME:CA' AND mark_type = 'SPOT' "
                        "AND as_of_date = ? AND settle_date = as_of_date", (day,)).fetchone() == ("BBG_BFXFORWARD",)
    assert conn.execute("SELECT COUNT(*) FROM marks_official WHERE instrument_id = 'LME:CA' AND "
                        "mark_type = 'FWD_OUTRIGHT' AND settle_date = '2026-12-10'").fetchone() == (1,)
    # a close already on file is never rewritten
    backfill.backfill(p, LME_DAY, LME_DAY, fetch=_fx_spot, fwd_fetch=_fx_fwd,
                      fut_fetch=_daily([], {t: v + 500 for t, v in values.items()}), today=TODAY, log=lambda *_: None)
    assert _lme_marks(conn) == marks


def test_an_lme_close_is_the_daily_close_told_by_the_instrument():
    day = LME_DAY.isoformat()
    for mark_type in ("SPOT", "FWD_OUTRIGHT"):
        assert backfill.is_close_row(mark_type, day, f"{day}T17:00:00-04:00", TODAY, instrument_id="LME:CA") is True
        assert backfill.is_close_row(mark_type, day, f"{day}T11:40:00-04:00", TODAY, instrument_id="LME:CA") is False
        assert backfill.is_close_row(mark_type, day, f"{day}T15:00:00-04:00", TODAY, instrument_id="LME:CA") is False
        # an FX pair keeps the 15:00 rule, with or without its id
        assert backfill.is_close_row(mark_type, day, f"{day}T15:00:00-04:00", TODAY, instrument_id="AUDUSD") is True
        assert backfill.is_close_row(mark_type, day, f"{day}T17:00:00-04:00", TODAY, instrument_id="AUDUSD") is False
        assert backfill.is_close_row(mark_type, day, f"{day}T15:00:00-04:00", TODAY) is True
    assert backfill.is_lme_instrument("LME:CA") and not backfill.is_lme_instrument("AUDUSD")


def test_without_the_lme_helper_the_lme_marks_are_named_missing_and_nothing_is_written(tmp_path, monkeypatch):
    p, conn = _lme_db(tmp_path)
    monkeypatch.delattr(backfill.fc, "lme_history_marks", raising=False)
    _, values = _lme_values()
    results = backfill.backfill(p, LME_DAY, LME_DAY, fetch=_fx_spot, fwd_fetch=_fx_fwd, fut_fetch=_daily([], values),
                                today=TODAY, log=lambda *_: None)
    assert results[0]["status"] == "DONE" and results[0]["lme_marks"] == 0
    missing = {(m["mark_type"], m["settle_date"]): m["reason"] for m in results[0]["missing_marks"]}
    assert set(missing) == {("SPOT", LME_DAY.isoformat()), ("FWD_OUTRIGHT", "2026-12-10")}
    assert all(r == backfill.LME_MARKS_UNAVAILABLE for r in missing.values())
    assert _lme_marks(conn) == []


def test_an_lme_pillar_bloomberg_has_no_close_for_leaves_the_mark_missing_with_its_reason(tmp_path):
    p, conn = _lme_db(tmp_path)
    _, values = _lme_values()
    del values["LMCADY Comdty"]                                          # no cash close that day
    results = backfill.backfill(p, LME_DAY, LME_DAY, fetch=_fx_spot, fwd_fetch=_fx_fwd, fut_fetch=_daily([], values),
                                today=TODAY, log=lambda *_: None)
    missing = [m for m in results[0]["missing_marks"] if m["mark_type"] == "SPOT"]
    assert len(missing) == 1 and "LMCADY Comdty" in missing[0]["reason"]
    assert not [m for m in _lme_marks(conn) if m[2] == "SPOT"]
