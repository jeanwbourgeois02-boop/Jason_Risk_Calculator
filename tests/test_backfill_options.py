"""data/bloomberg/backfill.py: a past day's FX options are priced from that day's own inputs
once its closes are written (2026-09-22; user: "options daily pnl 0, that cannot be right,
everything is moving" -- the Bloomberg PC's snapshot held vol_quotes and curves for
2026-09-17..21 but pricer marks for 09-21 alone, so the near-marks rule carried that premium
back and Daily was 0).

engine.options.store.price_close is options-pricer's; here it is a recording fake reached
through backfill._import_price_close, the same guard shape as _import_realise_settled. Nothing
is asked of Bloomberg for it, and no blpapi is needed. The fixtures follow tests/test_backfill.py
(its own autouse pins are module-scoped, so they are repeated here rather than imported)."""
from datetime import date, timedelta

import pytest

from data.bloomberg import backfill, live
from data.ingest import schema


@pytest.fixture(autouse=True)
def _today_and_calendar(monkeypatch, tmp_path):
    """'today' pinned to Mon 2026-09-21 so the worked days sit inside Bloomberg's intraday
    reach; the calendar pinned to Monday-Friday (no listed holiday can move a day)."""
    import engine.pnl.calendar as cal
    monkeypatch.setattr(live, "book_today", lambda: date(2026, 9, 21))
    monkeypatch.setattr(cal, "_DEFAULT_HOLIDAYS_PATH", tmp_path / "no-holidays.txt")


OPTION_ID = "USDJPY101526C-1"          # USDJPY: its only closing SPOT is the pair's own (no conversion pair)


def _db(tmp_path, option_trade_date="2026-09-15", option_expiry="2026-10-15"):
    """One AUDUSD forward (a1, open all through, so every day worked is DONE with or without
    an option) and one USDJPY call (o1) open from `option_trade_date` to `option_expiry`."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        (OPTION_ID, "FX_OPTION", "USD", "JPY", 1, 0, OPTION_ID, option_expiry),
    ])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-09-14", -1e6, 0.65, "acc", "cp", "", "t", "d", ""),
        ("o1", "XLSX", OPTION_ID, "FX_OPTION", "o1", option_trade_date, 1e6, 0.01, "acc", "cp", "", "t", "d", ""),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-09-14", "2026-10-20", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-09-14", "2026-10-20", 0.65, 1),
        ("o1", 1, "NOTIONAL", "USD", 1e6, option_trade_date, option_expiry, 0, 0),
    ])
    conn.commit()
    return p, conn


def spot_fetch(session, service, tickers, field, day):
    assert field == "PX_LAST" and set(tickers) <= {"AUDUSD Curncy", "USDJPY Curncy"}
    return {t: {"AUDUSD Curncy": 0.65, "USDJPY Curncy": 147.0}[t] + day.day / 1000.0 for t in tickers}


def fwd_fetch(session, service, tickers, fields, start, end):
    """Every AUDUSD tenor quotes the leg's own settle date, so FWD_OUTRIGHT resolves exactly
    (tests/test_backfill.py's pattern); the option's pair needs no historical curve."""
    assert all(t.startswith("AUDUSD") for t in tickers)
    out, d = {}, start
    while d <= end:
        for t in tickers:
            out.setdefault(t, {})[d.isoformat()] = {"PX_LAST": 0.655, "SETTLE_DT": "2026-10-20"}
        d += timedelta(days=1)
    return out


def _never_called(*a, **k):
    raise AssertionError("no future history may be requested for this book")


QUOTES_ASKED = []


def quote_history(session, service, tickers, fields, start, end):
    """The option's smile (USDJPY) and the OIS curves of USD and JPY, from the daily history
    (2026-09-22): PX_LAST on every business day of the stretch."""
    QUOTES_ASKED.append((len(tickers), start, end))
    return {t: {d.isoformat(): {"PX_LAST": 5.0} for d in backfill.business_days(start, end)} for t in tickers}


def _fakes(monkeypatch, events, price_result=None, raise_exc=None, pricer_available=True):
    """price_close and realise_settled as recording fakes behind the module's two import
    helpers. The price_close fake also counts the day's closes on file when it is asked, so
    the order 'closes written -> options priced -> freeze' is proved, not assumed."""
    def fake_price_close(conn, day):
        n_spot = conn.execute("SELECT COUNT(*) FROM marks_official WHERE as_of_date = ? AND mark_type = 'SPOT'",
                              (day,)).fetchone()[0]
        n_fwd = conn.execute("SELECT COUNT(*) FROM marks_official WHERE as_of_date = ? AND mark_type = 'FWD_OUTRIGHT'",
                             (day,)).fetchone()[0]
        events.append(("price_close", day, n_spot, n_fwd))
        if raise_exc is not None:
            raise raise_exc
        return dict(price_result or {"day": day, "priced": 1, "skipped": []}, day=day)

    def fake_realise_settled(conn, day, **kwargs):
        events.append(("realise_settled", day))
        return {"realised": 0, "unrealisable": []}

    monkeypatch.setattr(backfill, "_import_price_close", lambda: fake_price_close if pricer_available else None)
    monkeypatch.setattr(backfill, "_import_realise_settled", lambda: fake_realise_settled)
    return fake_price_close, fake_realise_settled


def _run(p, start, end, log=None, **kwargs):
    kwargs.setdefault("quote_fetch", quote_history)
    return backfill.backfill(p, start, end, fetch=spot_fetch, fwd_fetch=fwd_fetch, fut_fetch=_never_called,
                             log=(log.append if log is not None else (lambda *_: None)), **kwargs)


# --------------------------------------------------------------------------- (a) called once, after the closes, before the freeze
def test_price_close_runs_once_per_done_day_after_its_closes_and_before_the_freeze(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    events = []
    skipped = [{"trade_id": "o9", "reason": "no vol smile for USDJPY on file that day"}]
    _fakes(monkeypatch, events, price_result={"priced": 1, "skipped": skipped})
    log = []
    QUOTES_ASKED.clear()
    results = _run(p, date(2026, 9, 17), date(2026, 9, 18), log=log)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    # the smile and the two curves the option prices from, from the history: one request per
    # kind for the stretch (45 vol tickers; USD's 17 + JPY's 9 OIS tickers), written for each day
    assert QUOTES_ASKED == [(45, date(2026, 9, 17), date(2026, 9, 18)), (26, date(2026, 9, 17), date(2026, 9, 18))]
    assert all(r["vol_quotes"] == 45 and r["curve_quotes"] == 26 and r["missing_inputs"] == [] for r in results)
    assert conn.execute("SELECT COUNT(*) FROM vol_quotes WHERE source = 'BBG_BDH'").fetchone() == (90,)
    assert conn.execute("SELECT COUNT(*) FROM curve_quotes WHERE source = 'BBG_BDH'").fetchone() == (52,)
    # once per day, with (conn, day); the day's SPOT closes (AUDUSD + USDJPY) and its
    # FWD_OUTRIGHT (a1's leg) are on file when the pricer is asked; realise_settled follows
    assert events == [("price_close", "2026-09-17", 2, 1), ("realise_settled", "2026-09-17"),
                      ("price_close", "2026-09-18", 2, 1), ("realise_settled", "2026-09-18")]
    for r in results:
        assert r["options_priced"] == 1 and r["options_skipped"] == skipped and r["options_note"] == ""
        assert r["options_closed_out"] == []                       # the fake lists none
    assert any("2026-09-18  DONE" in s and "options=1" in s and "options_skipped=1" in s for s in log)

    # the way auto_backfill runs it (order given: one freeze after the last day): every day
    # is still priced before that freeze, and a re-run reprices the day (user: "the latest
    # data is also logged / overwriting previous marks")
    events.clear()
    QUOTES_ASKED.clear()
    results = _run(p, date(2026, 9, 17), date(2026, 9, 18), order=[date(2026, 9, 18), date(2026, 9, 17)], overwrite=True)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert events == [("price_close", "2026-09-18", 2, 1), ("price_close", "2026-09-17", 2, 1),
                      ("realise_settled", "2026-09-18")]
    # the days hold their smile and curves: the history is not asked again, nothing rewritten
    assert QUOTES_ASKED == [] and all(r["vol_quotes"] == 0 and r["curve_quotes"] == 0 for r in results)


# --------------------------------------------------------------------------- (b) no option open that day: not called
def test_price_close_is_not_called_on_a_day_with_no_fx_option_open(tmp_path, monkeypatch):
    # traded Fri 09-18: not open on Thu 09-17, open from its trade date on
    p, conn = _db(tmp_path, option_trade_date="2026-09-18")
    events = []
    _fakes(monkeypatch, events)
    results = _run(p, date(2026, 9, 17), date(2026, 9, 18))
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert [e for e in events if e[0] == "price_close"] == [("price_close", "2026-09-18", 2, 1)]
    by_day = {r["day"]: r for r in results}
    assert by_day["2026-09-17"]["options_priced"] is None and by_day["2026-09-17"]["options_skipped"] == []
    assert by_day["2026-09-17"]["options_note"] == ""
    assert by_day["2026-09-18"]["options_priced"] == 1
    # the freeze still ran on both days
    assert [e for e in events if e[0] == "realise_settled"] == [("realise_settled", "2026-09-17"),
                                                                ("realise_settled", "2026-09-18")]


def test_the_expiry_date_is_priced_and_the_day_after_it_is_not(tmp_path, monkeypatch):
    p, conn = _db(tmp_path, option_trade_date="2026-09-15", option_expiry="2026-09-17")
    events = []
    _fakes(monkeypatch, events)
    results = _run(p, date(2026, 9, 17), date(2026, 9, 18))
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert [e[1] for e in events if e[0] == "price_close"] == ["2026-09-17"]
    assert [r["options_priced"] for r in results] == [1, None]


def test_options_open_on_reads_trade_date_to_expiry_inclusive(tmp_path):
    p, conn = _db(tmp_path, option_trade_date="2026-09-15", option_expiry="2026-09-17")
    assert backfill._options_open_on(conn, "2026-09-14") is False
    assert backfill._options_open_on(conn, "2026-09-15") is True
    assert backfill._options_open_on(conn, "2026-09-17") is True
    assert backfill._options_open_on(conn, "2026-09-18") is False


# --------------------------------------------------------------------------- (c) unavailable or raising: the day still stands
def test_pricer_not_importable_leaves_marks_and_realisation_and_names_it(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    events = []
    _fakes(monkeypatch, events, pricer_available=False)
    log = []
    results = _run(p, date(2026, 9, 17), date(2026, 9, 17), log=log)
    assert [r["status"] for r in results] == ["DONE"]
    assert results[0]["closes"] == 2 and results[0]["fwd_outrights"] == 1
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE as_of_date = '2026-09-17'").fetchone()[0] == 3
    assert events == [("realise_settled", "2026-09-17")]                       # the freeze still ran
    assert results[0]["options_priced"] is None and results[0]["options_skipped"] == []
    assert results[0]["options_note"] == backfill.PRICE_CLOSE_UNAVAILABLE
    assert any("price_close not importable" in s for s in log)
    assert any("2026-09-17  DONE" in s and backfill.PRICE_CLOSE_UNAVAILABLE in s for s in log)


def test_pricer_raising_leaves_marks_and_realisation_and_names_the_failure(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    events = []
    _fakes(monkeypatch, events, raise_exc=RuntimeError("QuantLib refused the smile"))
    log = []
    results = _run(p, date(2026, 9, 17), date(2026, 9, 17), log=log)
    assert [r["status"] for r in results] == ["DONE"]
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE as_of_date = '2026-09-17'").fetchone()[0] == 3
    assert events == [("price_close", "2026-09-17", 2, 1), ("realise_settled", "2026-09-17")]
    assert results[0]["options_priced"] is None and results[0]["options_skipped"] == []
    assert "price_close raised" in results[0]["options_note"] and "QuantLib refused the smile" in results[0]["options_note"]
    assert any("2026-09-17  DONE" in s and "QuantLib refused the smile" in s for s in log)


def test_closed_out_options_are_listed_under_their_own_head_and_not_counted_as_skipped(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    events = []
    skipped = [{"trade_id": "o9", "reason": "no vol"}]
    _fakes(monkeypatch, events, price_result={"priced": 1, "skipped": skipped, "closed_out": ["o5"]})
    log = []
    r = _run(p, date(2026, 9, 17), date(2026, 9, 17), log=log)[0]
    assert (r["options_priced"], r["options_skipped"], r["options_closed_out"]) == (1, skipped, ["o5"])
    assert any("options=1" in s and "options_skipped=1" in s and "options_closed_out=1" in s for s in log)
    # the pricer not importable, raising, or with no option open: an empty list
    _fakes(monkeypatch, events, pricer_available=False)
    assert _run(p, date(2026, 9, 17), date(2026, 9, 17), overwrite=True)[0]["options_closed_out"] == []
    _fakes(monkeypatch, events, raise_exc=RuntimeError("x"))
    assert _run(p, date(2026, 9, 17), date(2026, 9, 17), overwrite=True)[0]["options_closed_out"] == []


def test_pricer_reporting_its_own_run_error_is_carried_as_the_note(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    events = []
    _fakes(monkeypatch, events, price_result={"priced": 0, "skipped": [], "error": "vol_quotes table missing"})
    results = _run(p, date(2026, 9, 17), date(2026, 9, 17))
    assert results[0]["status"] == "DONE" and results[0]["options_priced"] == 0
    assert results[0]["options_note"] == "vol_quotes table missing"


def test_import_helper_returns_the_pricer_or_none_never_raises():
    fn = backfill._import_price_close()
    assert fn is None or callable(fn)


# --------------------------------------------------------------------------- every result has the same shape
def test_skipped_no_closes_and_error_days_carry_the_option_keys(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    events = []
    _fakes(monkeypatch, events)
    keys = {"options_priced": None, "options_skipped": [], "options_note": "", "options_closed_out": [],
            "vol_quotes": 0, "curve_quotes": 0, "missing_inputs": []}
    assert {k: backfill._skipped("2026-09-17")[k] for k in keys} == keys
    assert not any(k.startswith("rates_") for k in backfill._skipped("2026-09-17"))   # the swaps' keys left 2026-09-24

    # NO_CLOSES: Bloomberg returned nothing that day
    results = backfill.backfill(p, date(2026, 9, 17), date(2026, 9, 17), fetch=lambda *a, **k: {}, fwd_fetch=fwd_fetch,
                                fut_fetch=_never_called, quote_fetch=quote_history, log=lambda *_: None)
    assert results[0]["status"] == "NO_CLOSES" and {k: results[0][k] for k in keys} == keys
    assert events == []                                                          # nothing to price, nothing to freeze

    # ERROR: the day's own processing raised (the curve builder here), before any pricing
    def _boom(*a, **k):
        raise RuntimeError("boom")

    with monkeypatch.context() as m:
        m.setattr(backfill.fc, "historical_curve", _boom)
        results = _run(p, date(2026, 9, 17), date(2026, 9, 17))
    assert results[0]["status"] == "ERROR" and {k: results[0][k] for k in keys} == keys
    assert events == []

    # SKIPPED: a day already complete is not worked and not priced again
    _run(p, date(2026, 9, 17), date(2026, 9, 17))
    events.clear()
    results = _run(p, date(2026, 9, 17), date(2026, 9, 17))
    assert results[0]["status"] == "SKIPPED" and {k: results[0][k] for k in keys} == keys and events == []


# --------------------------------------------------------------------------- the status file says whether a day's options were priced
def test_status_file_carries_an_options_block_per_worked_day(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    events = []
    skipped = [{"trade_id": "o1", "reason": "no OIS curve for JPY on file that day"}]
    _fakes(monkeypatch, events, price_result={"priced": 0, "skipped": skipped, "closed_out": ["o7", "o8"]})
    thread = backfill.start_auto_backfill(p, fetch=spot_fetch, fwd_fetch=fwd_fetch, fut_fetch=_never_called,
                                          session_factory=lambda: (None, None), quote_fetch=quote_history)
    thread.join(timeout=10)
    block = live.read_status(p)["backfill"]
    assert block["running"] is False
    # the days from a1's trade date (Mon 09-14) to yesterday (Fri 09-18): o1 open from 09-15
    assert set(block["options"]) == {"2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"}
    assert list(block["options"]) == sorted(block["options"], reverse=True)
    # a closed-out option is under its own head, never among the skipped (2026-09-22)
    # (2026-09-24) the options on commodity futures among them: None where the pricer says nothing of them
    assert block["options"]["2026-09-18"] == {"priced": 0, "skipped": skipped, "note": "", "closed_out": ["o7", "o8"],
                                              "futures_options_priced": None}
    assert block["options"]["2026-09-14"] == {"priced": None, "skipped": [], "note": "", "closed_out": [],
                                              "futures_options_priced": None}
    # and an "inputs" block ("rates", with the swaps' pricing, until 2026-09-24): the day's smile /
    # curve rows from the history
    assert "rates" not in block and set(block["inputs"]) == set(block["options"])
    assert block["inputs"]["2026-09-18"] == {"vol_quotes": 45, "curve_quotes": 26, "missing_inputs": []}
    assert block["inputs"]["2026-09-14"] == {"vol_quotes": 0, "curve_quotes": 0, "missing_inputs": []}
    # the "days" block keeps its own pinned shape, untouched by the options step
    assert all(set(v) == {"status", "missing_count", "missing"} for v in block["days"].values())
    assert all(v["status"] == "DONE" for v in block["days"].values())
