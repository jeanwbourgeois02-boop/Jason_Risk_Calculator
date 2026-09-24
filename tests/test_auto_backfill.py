"""data/bloomberg/backfill.py: automatic backfill (2026-09-15 decision; extended
2026-09-18 to also backfill FWD_OUTRIGHT/FUTURE_PX, not SPOT alone).

`auto_backfill` fills every business day from the earliest trade date to yesterday that
lacks a complete official close, using `data.bloomberg.inventory.close_completeness`
(now SPOT + FWD_OUTRIGHT + FUTURE_PX); `start_auto_backfill` runs it in a background
thread and publishes progress into the Bloomberg status file. Both are exercised with a
fake fetch -- no blpapi required."""
import json
import threading
from datetime import date, datetime, timedelta

import pytest

from data.bloomberg import backfill, live
from data.ingest import schema

REAL_BOOK_TODAY = live.book_today     # the unpatched rule, captured before any fixture pins the book date

@pytest.fixture(autouse=True)
def _close_1500_on_every_date(monkeypatch):
    """The 15:00 New York close applies to every past day inside Bloomberg's intraday history
    counted from today (2026-09-22; the 2026-09-21 cut-over is gone). 'today' is pinned so
    the days these tests work stay within reach (a test that needs another today patches
    live.book_today itself)."""
    from data.bloomberg import live as _live
    monkeypatch.setattr(_live, "book_today", lambda: date(2026, 9, 21))



def _book_today():
    """Today as the code under test sees it: the New York book date, not the PC's
    local date (a day ahead of New York every morning in Asia)."""
    from data.bloomberg.live import book_today
    return book_today()


# The leg's settle date must be (a) always "open" (>= every business day these tests
# backfill, all within ~10 days of "yesterday") and (b) reachable by a standard forward
# tenor's interpolation range so FWD_OUTRIGHT can actually resolve and a day can reach
# "complete" -- unlike a literal never-settling sentinel (e.g. year 2099), which
# fwd_curve.outright_for_date would correctly refuse to extrapolate to, forever.
def _settle_date() -> str:
    return (_book_today() + timedelta(days=60)).isoformat()


def _db(tmp_path, earliest_trade_date: str):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"))
    settle = _settle_date()
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", earliest_trade_date, -1e6, 0.65,
                  "acc", "cp", "HAHY7", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, earliest_trade_date, settle, 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, earliest_trade_date, settle, 0.65, 1),
    ])
    conn.commit()
    return p, conn


def _fake_fetch(session, service, tickers, field, day):
    assert set(tickers) == {"AUDUSD Curncy"}
    return {"AUDUSD Curncy": 0.61}


def _fake_fwd_fetch(session, service, tickers, fields, start, end):
    """Every tenor ticker "quotes" the SAME fixed settle date (the fixture's own leg
    settle date) on every day in [start, end], so outright_for_date's EXACT-match branch
    resolves it directly -- this test only needs FWD_OUTRIGHT to resolve to SOME value,
    not a realistic curve shape."""
    settle = _settle_date()
    out = {}
    d = start
    while d <= end:
        for ticker in tickers:
            out.setdefault(ticker, {})[d.isoformat()] = {"PX_LAST": 0.665, "SETTLE_DT": settle}
        d += timedelta(days=1)
    return out


def test_auto_backfill_fills_from_earliest_trade_to_yesterday(tmp_path):
    yesterday = _book_today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=5)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    p, conn = _db(tmp_path, earliest.isoformat())
    progress = []
    results = backfill.auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch,
                                     log=lambda *_: None, on_progress=progress.append)
    business_days = backfill.business_days(earliest, yesterday)
    # every day, worked newest first (2026-09-21; the order itself is tested further down)
    assert sorted(r["day"] for r in results) == [d.isoformat() for d in business_days]
    assert all(r["status"] == "DONE" for r in results)
    assert progress[0] == len(business_days)
    assert progress[-1] == 0

    conn2 = schema.connect(p)
    have_spot = {r[0] for r in conn2.execute(
        "SELECT as_of_date FROM marks WHERE instrument_id='AUDUSD' AND mark_type='SPOT'")}
    assert have_spot == {d.isoformat() for d in business_days}
    have_fwd = {r[0] for r in conn2.execute(
        "SELECT as_of_date FROM marks WHERE instrument_id='AUDUSD' AND mark_type='FWD_OUTRIGHT'")}
    assert have_fwd == {d.isoformat() for d in business_days}


def test_auto_backfill_option_only_book_fills_closing_spots_then_reports_complete(tmp_path):
    """2026-09-18: a book holding only a EURSEK option gets the closing SPOT of its pair
    and of its own USD-conversion pairs for every business day since it was traded, and
    those days then count as complete. Before, the option pair's SPOT was 'needed' but
    fetched by nobody, so every such day stayed incomplete and was asked of Bloomberg
    again on every feed cycle. 2026-09-22: the day's smile and the EUR OIS curve (SEK has
    none in scope) come from the daily history too, one request per kind for the stretch;
    once on file the day is complete and nothing is asked again."""
    yesterday = _book_today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=4)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    expiry = _settle_date()
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("EURSEK-OPT-1", "FX_OPTION", "EUR", "SEK", 1, 0, "EURSEK-OPT-1", expiry))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("o1", "XLSX", "EURSEK-OPT-1", "FX_OPTION", "o1", earliest.isoformat(), 1e6, 0.01,
                  "acc", "cp", "", "t", "d", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("o1", 1, "NOTIONAL", "EUR", 1e6, earliest.isoformat(), expiry, 0, 0))
    conn.commit()
    pairs = live.option_spot_pair_names("EUR", "SEK")           # own pair + base->USD + quote->USD, helper's orientation
    assert len(pairs) == 3

    def fetch(session, service, tickers, field, day):
        assert sorted(tickers) == sorted(f"{pair} Curncy" for pair in pairs)
        return {t: 1.5 for t in tickers}

    def never(*a, **k):
        raise AssertionError("no forward at an option's expiry, no future: no such history request")

    quotes_asked = []

    def quotes(session, service, tickers, fields, start, end):
        quotes_asked.append((len(tickers), tickers[0].split(" ")[0][:6], list(fields), start, end))
        return {t: {d.isoformat(): {"PX_LAST": 8.0} for d in backfill.business_days(start, end)} for t in tickers}

    results = backfill.auto_backfill(p, fetch=fetch, fwd_fetch=never, fut_fetch=never, quote_fetch=quotes,
                                     log=lambda *_: None)
    business_days = backfill.business_days(earliest, yesterday)
    assert sorted(r["day"] for r in results) == [d.isoformat() for d in business_days]
    assert all(r["status"] == "DONE" and r["closes"] == 3 and r["missing_marks"] == [] for r in results)
    assert all(r["vol_quotes"] == 45 and r["curve_quotes"] == 12 and r["missing_inputs"] == [] for r in results)
    have = {(r[0], r[1]) for r in schema.connect(p).execute(
        "SELECT instrument_id, as_of_date FROM marks_official WHERE mark_type='SPOT'")}
    assert have == {(pair, d.isoformat()) for pair in pairs for d in business_days}
    # two history requests for the one stretch: the 45 EURSEK vol tickers, then EUR's 12 OIS tickers
    last = business_days[-1]
    assert quotes_asked == [(45, "EURSEK", ["PX_LAST"], earliest, last), (12, "EESWE1", ["PX_LAST"], earliest, last)]
    conn = schema.connect(p)
    assert conn.execute("SELECT COUNT(DISTINCT as_of_date), COUNT(*), MIN(source) FROM vol_quotes").fetchone() == (
        len(business_days), 45 * len(business_days), "BBG_BDH")
    assert conn.execute("SELECT COUNT(DISTINCT as_of_date), COUNT(*), MIN(ccy), MAX(ccy) FROM curve_quotes").fetchone() == (
        len(business_days), 12 * len(business_days), "EUR", "EUR")

    progress = []
    again = backfill.auto_backfill(p, fetch=never, fwd_fetch=never, fut_fetch=never, quote_fetch=never,
                                   log=lambda *_: None, on_progress=progress.append)
    assert again == [] and progress == [0]                      # history complete: nothing re-requested


def test_auto_backfill_no_trades_does_nothing(tmp_path):
    p = tmp_path / "risk.db"
    schema.connect(p).commit()
    log = []
    assert backfill.auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, log=log.append) == []
    assert any("no trades" in m.lower() for m in log)


def test_auto_backfill_already_complete_reports_zero_remaining(tmp_path):
    yesterday = _book_today() - timedelta(days=1)
    p, conn = _db(tmp_path, yesterday.isoformat())
    # First pass fills everything (SPOT and FWD_OUTRIGHT both).
    backfill.auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch, log=lambda *_: None)
    progress = []
    results = backfill.auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch,
                                     log=lambda *_: None, on_progress=progress.append)
    assert results == []
    assert progress == [0]


def test_start_auto_backfill_without_bloomberg_writes_status_reason(tmp_path, monkeypatch):
    p = tmp_path / "risk.db"
    schema.connect(p).commit()
    monkeypatch.setattr(live, "availability", lambda host, port: (False, "no Terminal"))
    t = backfill.start_auto_backfill(p)
    assert t is None
    status = live.read_status(p)
    assert status["backfill"] == {"running": False, "reason": "no Terminal"}


def test_start_auto_backfill_runs_in_background_and_publishes_progress(tmp_path, monkeypatch):
    yesterday = _book_today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=3)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    p, conn = _db(tmp_path, earliest.isoformat())

    thread = backfill.start_auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch,
                                          session_factory=lambda: (None, None))
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()

    status = live.read_status(p)
    assert status["backfill"]["running"] is False
    assert status["backfill"]["remaining"] == 0

    # A second trigger while the lock is free should also complete cleanly (idempotent).
    thread2 = backfill.start_auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch,
                                           session_factory=lambda: (None, None))
    assert thread2 is not None
    thread2.join(timeout=5)


def test_start_auto_backfill_second_call_while_running_is_a_noop(tmp_path):
    yesterday = _book_today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=10)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    p, conn = _db(tmp_path, earliest.isoformat())

    started = threading.Event()
    release = threading.Event()

    def slow_fetch(session, service, tickers, field, day):
        started.set()
        release.wait(timeout=5)
        return {"AUDUSD Curncy": 0.61}

    t1 = backfill.start_auto_backfill(p, fetch=slow_fetch, fwd_fetch=_fake_fwd_fetch,
                                      session_factory=lambda: (None, None))
    assert t1 is not None
    started.wait(timeout=5)
    t2 = backfill.start_auto_backfill(p, fetch=slow_fetch, fwd_fetch=_fake_fwd_fetch,
                                      session_factory=lambda: (None, None))
    assert t2 is None  # lock held by t1
    release.set()
    t1.join(timeout=5)


def test_backfill_never_opens_a_real_session_when_all_fetches_are_injected(tmp_path):
    """2026-09-18 session-leak fix: when fetch/fwd_fetch/fut_fetch are all supplied
    (the normal test-injection shape), backfill() must never call pull_marks.open_session
    at all -- and therefore never try to `.stop()` anything either, since it never owned a
    session in the first place."""
    yesterday = _book_today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=2)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    p, conn = _db(tmp_path, earliest.isoformat())

    def boom(*a, **k):
        raise AssertionError("pull_marks.open_session must not be called when every fetch is injected")

    import data.bloomberg.pull_marks as pm
    import unittest.mock
    with unittest.mock.patch.object(pm, "open_session", boom):
        results = backfill.backfill(p, earliest, yesterday, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch,
                                    fut_fetch=_fake_fwd_fetch, log=lambda *_: None)
    assert all(r["status"] == "DONE" for r in results)


def test_backfill_stops_a_session_it_opened_itself(tmp_path, monkeypatch):
    """The actual leak this fixes: when backfill() falls back to pull_marks.open_session
    (no fetch/fwd_fetch/fut_fetch/session_factory given -- real usage shape), it must
    stop that session again once done -- and must NOT stop a session supplied via an
    injected session_factory (that stays the caller's responsibility)."""
    yesterday = _book_today() - timedelta(days=1)
    while yesterday.weekday() >= 5:      # run on a Monday, "yesterday" is a Sunday: no business day, no session
        yesterday -= timedelta(days=1)
    p, conn = _db(tmp_path, yesterday.isoformat())

    stopped = []

    class _FakeSession:
        def stop(self):
            stopped.append(True)

    def fake_open_session(host, port):
        return _FakeSession(), object()

    def fake_fetch_historical(session, service, tickers, field, day):
        return _fake_fetch(session, service, tickers, field, day)

    def fake_fetch_historical_series(session, service, tickers, fields, start, end):
        return _fake_fwd_fetch(session, service, tickers, fields, start, end)

    import data.bloomberg.pull_marks as pm
    monkeypatch.setattr(pm, "open_session", fake_open_session)
    monkeypatch.setattr(pm, "fetch_historical", fake_fetch_historical)
    monkeypatch.setattr(pm, "fetch_historical_series", fake_fetch_historical_series)
    # 2026-09-21: FX closes (SPOT and the tenor series) default to the 15:00 New York
    # intraday close; futures still default to the daily series above.
    monkeypatch.setattr(pm, "fetch_intraday_close_series", fake_fetch_historical_series)

    # No fetch/fwd_fetch/fut_fetch/session_factory at all: the exact shape that makes
    # backfill() fall back to pm.open_session() itself.
    backfill.backfill(p, yesterday, yesterday, log=lambda *_: None)
    assert stopped == [True]

    # An injected session_factory's session must be left alone (overwrite=True so the
    # already-complete day above is recomputed rather than skipped, exercising the same
    # code path with a real fetch call).
    stopped.clear()
    backfill.backfill(p, yesterday, yesterday, session_factory=lambda: (_FakeSession(), object()),
                      overwrite=True, log=lambda *_: None)
    assert stopped == []


# =========================================================================== 2026-09-21: order, outcome, retry
# Found from the Bloomberg PC ("5d n/a -- 5d needs the 2026-09-14 close ... run the
# Bloomberg backfill"): the run walked oldest day first with one session per day, so the
# closes the header needs came last; every incomplete day was asked of Bloomberg again
# after every feed cycle; and why a day stayed incomplete was printed, never kept.
_MONDAY = date(2026, 9, 21)     # reference dates: 09-18 (t-1), 09-17 (t-2), 09-14 (5d), 08-31 (MTD), 2025-12-31 (YTD)


def _no_tenor_prices(session, service, tickers, fields, start, end):
    return {}


def test_auto_backfill_works_the_headers_reference_dates_first_then_newest_first_in_one_call(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-08-27")
    calls = []
    real = backfill.backfill

    def counting(*a, **k):
        calls.append((a[1], a[2]))
        return real(*a, **k)

    monkeypatch.setattr(backfill, "backfill", counting)
    results = backfill.auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch,
                                     log=lambda *_: None)
    assert backfill.reference_dates(_MONDAY) == [date(2026, 9, 18), date(2026, 9, 17), date(2026, 9, 14),
                                                 date(2026, 8, 31), date(2025, 12, 31)]
    worked = [r["day"] for r in results]
    assert worked[:4] == ["2026-09-18", "2026-09-17", "2026-09-14", "2026-08-31"]      # the header's closes first
    assert worked[4:] == sorted(worked[4:], reverse=True) and worked[4] == "2026-09-16"  # then newest first
    assert len(worked) == len(backfill.business_days(date(2026, 8, 27), date(2026, 9, 18)))
    assert calls == [(date(2026, 8, 27), date(2026, 9, 18))]                           # ONE backfill() call, one session
    forwards = [r[0] for r in conn.execute("SELECT as_of_date FROM marks WHERE mark_type='FWD_OUTRIGHT' ORDER BY rowid")]
    assert forwards == worked


def test_auto_backfill_does_not_ask_bloomberg_again_for_a_day_that_cannot_complete_until_an_hour_has_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-16")
    requests, now = [], [1000.0]

    def fetch(session, service, tickers, field, day):
        requests.append(day.isoformat())
        return _fake_fetch(session, service, tickers, field, day)

    def run():
        return backfill.auto_backfill(p, fetch=fetch, fwd_fetch=_no_tenor_prices, fut_fetch=_no_tenor_prices,
                                      log=lambda *_: None, clock=lambda: now[0])

    first = run()                                       # no forward tenors: every day stays incomplete
    assert sorted(requests) == ["2026-09-16", "2026-09-17", "2026-09-18"] and len(first) == 3
    assert all(r["status"] == "DONE" and r["missing_marks"] for r in first)
    requests.clear()
    now[0] += 120                                       # the next feed cycle
    assert run() == [] and requests == []
    now[0] += backfill.RETRY_SECONDS                    # an hour on: tried again
    assert len(run()) == 3 and sorted(requests) == ["2026-09-16", "2026-09-17", "2026-09-18"]

    # what a day lacks has changed (a new trade): it is tried at once, the others still wait
    requests.clear()
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("NZDUSD", "FX", "NZD", "USD", 1, 0, "NZDUSD Curncy", "9999-12-31"))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("n1", "XLSX", "NZDUSD", "FX_FWD", "n1", "2026-09-18", 1e6, 0.59, "acc", "cp", "", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("n1", 1, "FX_NEAR", "NZD", 1e6, "2026-09-18", _settle_date(), 0.59, 1),
        ("n1", 2, "FX_NEAR", "USD", -590000, "2026-09-18", _settle_date(), 0.59, 1)])
    conn.commit()
    backfill.auto_backfill(p, fetch=lambda s, sv, tickers, field, day: requests.append(day.isoformat()) or {
        t: 0.6 for t in tickers}, fwd_fetch=_no_tenor_prices, fut_fetch=_no_tenor_prices, log=lambda *_: None,
        clock=lambda: now[0])
    assert requests == ["2026-09-18"]


def test_start_auto_backfill_publishes_why_each_reference_date_is_incomplete_and_survives_the_feeds_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-10")
    thread = backfill.start_auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_no_tenor_prices,
                                          fut_fetch=_no_tenor_prices, session_factory=lambda: (None, None))
    thread.join(timeout=10)
    block = live.read_status(p)["backfill"]
    assert block["running"] is False and block["remaining"] == 0 and block["reason"] == ""   # the existing keys
    assert datetime.fromisoformat(block["last_run"]).tzinfo is not None
    days = block["days"]
    # every reference date is there; the ones before the first trade need nothing, so DONE
    assert {d.isoformat() for d in backfill.reference_dates(_MONDAY)} <= set(days)
    assert days["2026-08-31"] == {"status": "DONE", "missing_count": 0, "missing": []}
    entry = days["2026-09-14"]
    assert set(entry) == {"status", "missing_count", "missing"}
    assert entry["status"] == "INCOMPLETE" and entry["missing_count"] == 1                   # the forward; SPOT was written
    assert entry["missing"] == ["Bloomberg returned no forward tenor prices for AUDUSD on 2026-09-14"]
    assert all(v["status"] in ("DONE", "NO_CLOSES", "INCOMPLETE") and len(v["missing"]) <= 5 for v in days.values())
    assert "2026-09-11" in days and len(days) <= backfill.MAX_STATUS_DAYS + 5                # other open days, bounded

    # live.pull_once rewrites the whole status file every cycle with no "backfill" key of
    # its own: write_status carries the block on file over (2026-09-21; it used to wipe
    # it), and the trigger that follows republishes the same block (nothing is due: the
    # days that stayed incomplete wait for their hour).
    live.write_status(p, {"time": "t", "connected": True})
    assert live.read_status(p)["backfill"]["days"] == days
    asked = []
    again = backfill.start_auto_backfill(p, fetch=lambda *a: asked.append(a) or {}, fwd_fetch=_no_tenor_prices,
                                         fut_fetch=_no_tenor_prices, session_factory=lambda: (None, None))
    again.join(timeout=10)
    assert asked == [] and live.read_status(p)["backfill"]["days"] == days


def test_start_auto_backfill_run_that_raises_says_failed_in_its_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-16")

    def broken(session, service, tickers, fields, start, end):
        raise RuntimeError("terminal went away")

    thread = backfill.start_auto_backfill(p, fetch=_fake_fetch, fwd_fetch=broken, fut_fetch=broken,
                                          session_factory=lambda: (None, None))
    thread.join(timeout=10)
    block = live.read_status(p)["backfill"]
    assert block["running"] is False and "failed" in block["reason"] and "terminal went away" in block["reason"]
    assert block["days"]["2026-09-18"] == {"status": "INCOMPLETE", "missing_count": 2,
                                           "missing": ["the backfill has not reached this day yet"]}


# =========================================================================== 2026-09-21: the 15:00 New York close
# A past day's FX row that is not stamped at the close (an earlier pull's last price, or a
# 17:00 PX_LAST row written under the 2026-09-21 cut-over) is not a close: the day is due
# again, the run says so once, and the row is replaced. 2026-09-22 (user: "for fx use new
# 3pm" for every previous close): that holds for a day before 2026-09-21 too.
def test_auto_backfill_says_that_past_days_not_stamped_at_the_close_are_requested_again_once(tmp_path):
    from zoneinfo import ZoneInfo
    yesterday = _book_today() - timedelta(days=1)          # Fri 2026-09-18
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    before = date(2026, 9, 16)                              # Wed: before the retired cut-over, within intraday reach
    assert before < date(2026, 9, 21) and backfill.intraday_floor(_book_today()) < before
    p, conn = _db(tmp_path, before.isoformat())
    day, old_day = yesterday.isoformat(), before.isoformat()
    # yesterday's last live pull, not its close; the older day holds 17:00 rows the old rule wrote
    old_stamp = datetime(yesterday.year, yesterday.month, yesterday.day, 11, 40, 12,
                         tzinfo=ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    daily_stamp = f"{old_day}T17:00:00-04:00"
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (day, "AUDUSD", day, "SPOT", 0.9001, "BBG_BFXFORWARD", old_stamp),
        (day, "AUDUSD", _settle_date(), "FWD_OUTRIGHT", 0.9002, "BBG_BFXFORWARD", old_stamp),
        (old_day, "AUDUSD", old_day, "SPOT", 0.9101, "BBG_BFXFORWARD", daily_stamp),
        (old_day, "AUDUSD", _settle_date(), "FWD_OUTRIGHT", 0.9102, "BBG_INTERP", daily_stamp),
        # the day between is already at its 15:00 close: never asked for again
        ("2026-09-17", "AUDUSD", "2026-09-17", "SPOT", 0.9201, "BBG_BFXFORWARD", backfill.close_stamp(date(2026, 9, 17))),
        ("2026-09-17", "AUDUSD", _settle_date(), "FWD_OUTRIGHT", 0.9202, "BBG_INTERP", backfill.close_stamp(date(2026, 9, 17))),
    ])
    conn.commit()
    from data.bloomberg.inventory import close_completeness
    strip = {r.as_of_date: r for r in close_completeness(conn, old_day, day).itertuples()}
    assert (strip[old_day].not_closed, strip["2026-09-17"].not_closed, strip[day].not_closed) == (2, 0, 2)
    key = backfill._db_key(p)
    log = []
    asked = []
    results = backfill.auto_backfill(p, fetch=lambda s, v, tickers, field, d: asked.append(d) or _fake_fetch(s, v, tickers, field, d),
                                     fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch, log=log.append)
    assert sorted(r["day"] for r in results) == [old_day, day] and {r["status"] for r in results} == {"DONE"}
    assert sorted(asked) == [before, yesterday]                                   # (d) the older day is re-requested
    note = backfill._notes[key]
    assert note.startswith("2 past day(s) hold marks that are not that day's close (an FX row not at 15:00 New York")
    assert f"every past day from {backfill.intraday_floor(_book_today())} on" in note
    assert "is asked for at 15:00 New York and those rows replaced" in note
    assert "a future's settlement is asked for on any past day" in note
    assert "closes at Bloomberg's 17:00 daily close" in note and "keep the marks they have" not in note
    assert any(note in line for line in log)
    # both days' rows are now the 15:00 close (the older day's 17:00 rows overwritten by the fake 15:00 series)
    for d, iso in ((yesterday, day), (before, old_day)):
        assert conn.execute("SELECT mark_type, value, snapped_at FROM marks_official WHERE as_of_date = ? ORDER BY 1",
                            (iso,)).fetchall() == [("FWD_OUTRIGHT", 0.665, backfill.close_stamp(d)),
                                                   ("SPOT", 0.61, backfill.close_stamp(d))]
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE snapped_at = ?", (daily_stamp,)).fetchone() == (0,)
    assert conn.execute("SELECT value FROM marks_official WHERE as_of_date = '2026-09-17' AND mark_type = 'SPOT'"
                        ).fetchone() == (0.9201,)                                # untouched
    # so a second run has nothing to ask for and nothing to say
    assert backfill.auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch,
                                  log=lambda *_: None) == []
    assert backfill._notes[key] == ""

    # the status file's "backfill" block carries the note and which scale field answered
    t = backfill.start_auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch)
    t.join(timeout=10)
    block = live.read_status(p)["backfill"]
    assert block["note"] == "" and "points_scale" in block


# =========================================================================== 2026-09-22: one day boundary
def test_after_the_1700_roll_the_day_just_ended_is_past_for_the_backfill_and_the_new_days_live_rows_stay(tmp_path, monkeypatch):
    """User decision 2026-09-22 (a Hong Kong user: "all date rollover at hkt 5am", 17:00 New
    York): from 17:00 New York on D the book is on D+1 (live.book_today), so in the same
    button press the backfill treats D as past -- D's 16:00 press is no close (is_close_row),
    D's 15:00 bars are asked for and replace it -- while D+1's rows, snapped at 18:00 on D by
    that press's pull, are D+1's live marks and stay. LTD(D) is then at the 15:00 close and
    the live D+1 valuation moves against it. When D+1 turns past (17:00 New York on D+1) its
    evening-of-D row is replaced by D+1's own 15:00 bar like any row not stamped at the close."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    d, d1, d2 = date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)                  # Mon, Tue, Wed

    def clock(when):
        monkeypatch.setattr(live, "book_today", lambda now=None: REAL_BOOK_TODAY(now if now is not None else when))

    clock(datetime(2026, 9, 21, 18, 0, tzinfo=ny))                                        # 18:00 NY on D = 06:00 HK on D+1
    assert live.book_today() == d1
    p, conn = _db(tmp_path, d.isoformat())
    settle = _settle_date()

    def fwd(session, service, tickers, fields, start, end):                                # as _fake_fwd_fetch, settle pinned
        return {t: {day.isoformat(): {"PX_LAST": 0.665, "SETTLE_DT": settle} for day in backfill.business_days(start, end)}
                for t in tickers}

    press16 = datetime(2026, 9, 21, 16, 0, 12, tzinfo=ny).isoformat(timespec="seconds")   # D's last press before the roll
    press18 = datetime(2026, 9, 21, 18, 0, 5, tzinfo=ny).isoformat(timespec="seconds")    # this press's pull, dated D+1
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (d.isoformat(), "AUDUSD", d.isoformat(), "SPOT", 0.9001, "BBG_BFXFORWARD", press16),
        (d.isoformat(), "AUDUSD", settle, "FWD_OUTRIGHT", 0.9002, "BBG_INTERP", press16),
        (d1.isoformat(), "AUDUSD", d1.isoformat(), "SPOT", 0.9101, "BBG_BFXFORWARD", press18),
        (d1.isoformat(), "AUDUSD", settle, "FWD_OUTRIGHT", 0.9102, "BBG_INTERP", press18),
    ])
    conn.commit()
    # D is past: a 16:00 or an 18:00 row of D is no close; D+1 is today, its rows count as they are
    assert backfill.is_close_row("SPOT", d.isoformat(), press16, d1) is False
    assert backfill.is_close_row("SPOT", d.isoformat(), press18, d1) is False
    assert backfill.is_close_row("SPOT", d.isoformat(), backfill.close_stamp(d, d1), d1) is True
    from data.bloomberg.inventory import close_completeness
    strip = {r.as_of_date: r for r in close_completeness(conn, d.isoformat(), d1.isoformat()).itertuples()}
    assert (strip[d.isoformat()].complete, strip[d.isoformat()].not_closed) == (False, 2)
    assert (strip[d1.isoformat()].complete, strip[d1.isoformat()].not_closed) == (True, 0)
    asked = []
    fetch = lambda s, v, tickers, field, day: asked.append(day) or _fake_fetch(s, v, tickers, field, day)   # noqa: E731
    results = backfill.auto_backfill(p, fetch=fetch, fwd_fetch=fwd, fut_fetch=fwd, log=lambda *_: None)
    assert asked == [d] and [(r["day"], r["status"]) for r in results] == [(d.isoformat(), "DONE")]
    assert conn.execute("SELECT mark_type, value, snapped_at FROM marks_official WHERE as_of_date = ? ORDER BY 1",
                        (d.isoformat(),)).fetchall() == [("FWD_OUTRIGHT", 0.665, backfill.close_stamp(d, d1)),
                                                         ("SPOT", 0.61, backfill.close_stamp(d, d1))]
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE snapped_at = ?", (press16,)).fetchone() == (0,)
    assert conn.execute("SELECT mark_type, value, snapped_at FROM marks_official WHERE as_of_date = ? ORDER BY 1",
                        (d1.isoformat(),)).fetchall() == [("FWD_OUTRIGHT", 0.9102, press18), ("SPOT", 0.9101, press18)]
    # so LTD(D) is at D's 15:00 close and the live day is valued against it: Daily is not 0
    from engine.pnl.ledger import ltd
    assert ltd(conn, d.isoformat()) == pytest.approx(-1e6 * (0.665 - 0.65))
    assert ltd(conn, d1.isoformat()) == pytest.approx(-1e6 * (0.9102 - 0.65))
    # 17:00 New York on D+1: D+1 turns past and its evening-of-D row goes the way of any non-close row
    clock(datetime(2026, 9, 22, 17, 0, tzinfo=ny))
    assert live.book_today() == d2
    asked.clear()
    results = backfill.auto_backfill(p, fetch=fetch, fwd_fetch=fwd, fut_fetch=fwd, log=lambda *_: None)
    assert asked == [d1] and [(r["day"], r["status"]) for r in results] == [(d1.isoformat(), "DONE")]
    assert conn.execute("SELECT mark_type, value, snapped_at FROM marks_official WHERE as_of_date = ? ORDER BY 1",
                        (d1.isoformat(),)).fetchall() == [("FWD_OUTRIGHT", 0.665, backfill.close_stamp(d1, d2)),
                                                          ("SPOT", 0.61, backfill.close_stamp(d1, d2))]
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE snapped_at = ?", (press18,)).fetchone() == (0,)


# =========================================================================== 2026-09-22: a day's smile and curve
def test_auto_backfill_works_a_day_whose_closes_are_complete_but_whose_inputs_are_missing_and_says_why(tmp_path, monkeypatch):
    """A day with every mark at its close still lacks the smile and the curves its option
    prices from: it is due, the history is asked for them (and for nothing else that is on
    file), the day is complete once they land, and a curve the history has nothing for is
    named in the status block's reasons and tried again within the hour, not on every run."""
    p, conn = _db(tmp_path, "2026-09-16")                                        # AUDUSD forward, Wed 09-16 on
    conn.execute("INSERT INTO instruments VALUES ('USDJPY101526C-1','FX_OPTION','USD','JPY',1,0,'USDJPY101526C-1','2026-10-15')")
    conn.execute("INSERT INTO trades VALUES ('o1','XLSX','USDJPY101526C-1','FX_OPTION','o1','2026-09-16',1e6,0.01,"
                 "'acc','cp','','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('o1',1,'NOTIONAL','USD',1e6,'2026-09-16','2026-10-15',0,0)")
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    days = backfill.business_days(date(2026, 9, 16), date(2026, 9, 18))
    for d in days:                                                               # every mark already at the close
        stamp = backfill.close_stamp(d)
        conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
            (d.isoformat(), "AUDUSD", d.isoformat(), "SPOT", 0.65, "BBG_BFXFORWARD", stamp),
            (d.isoformat(), "AUDUSD", _settle_date(), "FWD_OUTRIGHT", 0.655, "BBG_INTERP", stamp),
            (d.isoformat(), "USDJPY", d.isoformat(), "SPOT", 147.0, "BBG_BFXFORWARD", stamp)])
    conn.commit()
    from data.bloomberg.inventory import close_completeness
    strip = close_completeness(conn, "2026-09-16", "2026-09-18")
    assert list(strip["complete"]) == [True] * 3
    assert list(strip["inputs_missing"]) == [[{"kind": "OIS_CURVE", "key": "JPY"}, {"kind": "OIS_CURVE", "key": "USD"},
                                             {"kind": "VOL_SMILE", "key": "USDJPY"}]] * 3
    monkeypatch.setattr(backfill, "_import_price_close", lambda: (lambda conn, day: {"day": day, "priced": 1, "skipped": []}))
    asked = []

    def quotes(session, service, tickers, fields, start, end):                    # nothing for JPY's curve
        asked.append((len(tickers), start, end))
        return {t: {d.isoformat(): {"PX_LAST": 4.0} for d in backfill.business_days(start, end)}
                for t in tickers if not t.startswith("JYSO")}

    now = [1000.0]
    fetch = lambda s, v, tickers, field, day: {"AUDUSD Curncy": 0.61, "USDJPY Curncy": 147.5}   # noqa: E731
    run = lambda: backfill.auto_backfill(p, fetch=fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=_fake_fwd_fetch,  # noqa: E731
                                         quote_fetch=quotes, log=lambda *_: None, clock=lambda: now[0])
    results = run()
    assert sorted(r["day"] for r in results) == [d.isoformat() for d in days]
    assert all(r["status"] == "DONE" and r["vol_quotes"] == 45 and r["curve_quotes"] == 17 for r in results)
    assert all([(m["kind"], m["key"]) for m in r["missing_inputs"]] == [("OIS_CURVE", "JPY")] for r in results)
    assert asked == [(45, date(2026, 9, 16), date(2026, 9, 18)), (17 + 9, date(2026, 9, 16), date(2026, 9, 18))]
    # the marks at the close were left alone
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone() == (9,)
    assert conn.execute("SELECT DISTINCT value FROM marks WHERE mark_type = 'SPOT' AND instrument_id = 'AUDUSD'").fetchall() == [(0.65,)]
    # the status block: each day INCOMPLETE for JPY's curve alone, with the history's own reason
    key = backfill._db_key(p)
    for d in days:
        state = backfill._day_state[(key, d.isoformat())]
        assert state["status"] == "INCOMPLETE" and state["missing_count"] == 1
        assert state["missing"] == [f"fewer than 4 OIS quotes for JPY on {d}: Bloomberg returned no value for "
                                    "JYSO1Z Curncy, JYSOA Curncy, JYSOC Curncy, JYSOF Curncy, JYSO1 Curncy, JYSO2 Curncy, "
                                    "JYSO5 Curncy, JYSO10 Curncy, JYSO30 Curncy"]
    days_block = backfill._days_block[key]
    assert days_block["2026-09-18"] == {"status": "INCOMPLETE", "missing_count": 1,
                                        "missing": [backfill._day_state[(key, "2026-09-18")]["missing"][0]]}
    inputs_block = backfill._inputs_block[key]                                    # "rates" until 2026-09-24
    assert inputs_block["2026-09-18"] == {"vol_quotes": 45, "curve_quotes": 17,
                                          "missing_inputs": results[0]["missing_inputs"]}
    # within the hour: nothing is asked again; after it, JPY's curve alone is asked for once more
    asked.clear()
    now[0] += 60
    assert run() == [] and asked == []
    now[0] += backfill.RETRY_SECONDS
    results = run()
    assert [r["day"] for r in results] == [d.isoformat() for d in reversed(days)]          # reference dates first, newest first
    assert asked == [(9, date(2026, 9, 16), date(2026, 9, 18))] and all(r["vol_quotes"] == 0 for r in results)


# --------------------------------------------------------------------------- retry rule (2026-09-22, "yes do part 2")
# A day is asked again only when what it lacks changes, when the ticker rules change
# (backfill.state_version), or -- for a day within the last RECENT_BUSINESS_DAYS business
# days that is not waiting on a ticker Bloomberg rejects -- when RETRY_SECONDS have passed;
# and the per-day state survives a restart (a JSON sidecar next to the status file).
_REJECTION = "Unknown/Invalid security [nid:11150] "


class _RejectingFetch:
    """A spot fetch Bloomberg rejects: every ticker unknown, with Bloomberg's own words
    on `.reason` (what pull_marks.fetch_intraday_close_series exposes)."""
    def __init__(self):
        self.asked = []

    def __call__(self, session, service, tickers, field, day):
        self.asked.append(day.isoformat())
        return {}

    @staticmethod
    def reason(ticker, day):
        return f"Bloomberg answered the intraday request for {ticker} with: {_REJECTION}"


def test_a_day_whose_only_failure_is_a_ticker_bloomberg_rejects_waits_for_the_ticker_list_not_the_hour(tmp_path, monkeypatch):
    """(a) and (d): 09-14 / 09-15 are older than 3 business days before Monday 09-21, 09-16..18
    are within them; every day's SPOT ticker is rejected. None is due an hour later; all
    are due when the version stamp changes; a day whose needs change is due at once."""
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-14")
    fetch, now = _RejectingFetch(), [1000.0]
    run = lambda f=fetch: backfill.auto_backfill(p, fetch=f, fwd_fetch=_no_tenor_prices, fut_fetch=_no_tenor_prices,  # noqa: E731
                                                  log=lambda *_: None, clock=lambda: now[0])
    first = run()
    assert sorted(fetch.asked) == ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    assert all(r["status"] == "NO_CLOSES" for r in first)
    key = backfill._db_key(p)
    state = backfill._day_state[(key, "2026-09-14")]
    assert state["rejected"] == ["AUDUSD Curncy"] and state["rejected_only"] is True
    assert state["version"] == backfill.state_version()
    assert backfill.is_rejection(state["missing"][-1])
    fetch.asked.clear()
    now[0] += backfill.RETRY_SECONDS + 1                # an hour on: neither the old days nor the recent ones
    assert run() == [] and fetch.asked == []
    now[0] += 24 * 3600                                 # a day on: still nothing
    assert run() == [] and fetch.asked == []
    # the ticker rules change (a corrected ticker in the code): every day is asked once more
    monkeypatch.setattr(backfill, "state_version", lambda: "2099-01-01.1+corrected")
    assert len(run()) == 5 and sorted(fetch.asked) == ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    assert backfill._day_state[(key, "2026-09-14")]["version"] == "2099-01-01.1+corrected"
    fetch.asked.clear()
    assert run() == [] and fetch.asked == []            # stamped by the new version: not again
    # what a day lacks changes (a new trade dated 09-18): that day is due at once, the others still wait
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("NZDUSD", "FX", "NZD", "USD", 1, 0, "NZDUSD Curncy", "9999-12-31"))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("n1", "XLSX", "NZDUSD", "FX_FWD", "n1", "2026-09-18", 1e6, 0.59, "acc", "cp", "", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("n1", 1, "FX_NEAR", "NZD", 1e6, "2026-09-18", _settle_date(), 0.59, 1),
        ("n1", 2, "FX_NEAR", "USD", -590000, "2026-09-18", _settle_date(), 0.59, 1)])
    conn.commit()
    assert len(run()) == 1 and fetch.asked == ["2026-09-18"]


def test_a_recent_day_with_no_settlement_yet_is_retried_hourly_until_it_is_older_than_three_business_days(tmp_path, monkeypatch):
    """(b): a future's PX_LAST that Bloomberg has not returned ("returned no ...", a valid
    request) on a day within the last 3 business days is asked again after RETRY_SECONDS;
    once the day is older than that it is not asked again by time."""
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-16")
    conn.execute("INSERT INTO instruments VALUES ('ESZ6 Index','FUTURE','ES','USD',50,0,'ESZ6 Index','2026-12-18')")
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESZ6 Index','FUTURE','f1','2026-09-16',2,6500,"
                 "'acc','cp','','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',2*50*6500,'2026-09-16','2026-12-18',0,0)")
    conn.commit()
    settles, now = [], [1000.0]

    def no_settles(session, service, tickers, fields, start, end):
        settles.append((tuple(tickers), start, end))
        return {}

    run = lambda: backfill.auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch, fut_fetch=no_settles,  # noqa: E731
                                         log=lambda *_: None, clock=lambda: now[0])
    first = run()
    assert len(first) == 3 and all(r["missing_marks"][0]["reason"].startswith("Bloomberg returned no PX_LAST for ESZ6 Index")
                                   for r in first)
    key = backfill._db_key(p)
    state = backfill._day_state[(key, "2026-09-18")]
    assert state["rejected"] == [] and state["rejected_only"] is False
    settles.clear()
    now[0] += 120
    assert run() == [] and settles == []
    now[0] += backfill.RETRY_SECONDS                    # within the last 3 business days: tried again
    assert [r["day"] for r in run()] == ["2026-09-18", "2026-09-17", "2026-09-16"]
    assert settles == [(("ESZ6 Index",), date(2026, 9, 16), date(2026, 9, 18))]
    # Thursday 09-24: the last 3 business days are 09-21..23; 09-16..18 are older and wait
    monkeypatch.setattr(live, "book_today", lambda: date(2026, 9, 24))
    settles.clear()
    now[0] += backfill.RETRY_SECONDS
    worked = [r["day"] for r in run()]
    assert sorted(worked) == ["2026-09-21", "2026-09-22", "2026-09-23"]           # the new days only
    assert all(start >= date(2026, 9, 21) for _, start, _ in settles)
    settles.clear()
    now[0] += backfill.RETRY_SECONDS
    assert sorted(r["day"] for r in run()) == ["2026-09-21", "2026-09-22", "2026-09-23"]
    assert "2026-09-18" not in {d for _, start, end in settles for d in [start.isoformat(), end.isoformat()]}
    assert backfill._waiting[key] == ("3 older day(s) got nothing more from Bloomberg's history; asked again when what "
                                      "they lack, or the ticker list, changes; 3 day(s) within the last 3 business days "
                                      "are tried again within the hour")


def test_the_per_day_state_survives_a_restart(tmp_path, monkeypatch):
    """(c): the in-memory state is cleared (a restart); the next run loads the sidecar and
    asks nothing again. A recent day's hour still runs from its last try, so it is asked
    again once the hour has passed."""
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-14")
    requests, now = [], [1000.0]

    def fetch(session, service, tickers, field, day):
        requests.append(day.isoformat())
        return _fake_fetch(session, service, tickers, field, day)

    run = lambda: backfill.auto_backfill(p, fetch=fetch, fwd_fetch=_no_tenor_prices, fut_fetch=_no_tenor_prices,  # noqa: E731
                                         log=lambda *_: None, clock=lambda: now[0])
    assert len(run()) == 5                              # no forward tenors: every day stays incomplete
    key = backfill._db_key(p)
    sidecar = backfill._state_path(p)
    assert sidecar.exists() and sidecar.name == "risk.db.backfill_state.json"
    on_file = json.loads(sidecar.read_text(encoding="utf-8"))
    assert on_file["version"] == backfill.state_version() and sorted(on_file["days"]) == [
        "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    assert on_file["days"]["2026-09-18"]["missing"] == ["Bloomberg returned no forward tenor prices for AUDUSD on 2026-09-18"]
    before = dict(backfill._day_state)
    backfill._day_state.clear()                         # the restart
    requests.clear()
    now[0] = 5.0                                        # a fresh monotonic clock
    assert run() == [] and requests == []
    loaded = backfill._day_state[(key, "2026-09-18")]
    assert loaded["signature"] == before[(key, "2026-09-18")]["signature"]
    assert loaded["missing"] == before[(key, "2026-09-18")]["missing"] and loaded["version"] == backfill.state_version()
    assert abs(loaded["at"] - 5.0) < 60                 # tried moments ago, on the new clock
    now[0] += backfill.RETRY_SECONDS                    # the hour passes: the 3 recent days only
    assert sorted(requests) == [] and sorted(r["day"] for r in run()) == ["2026-09-16", "2026-09-17", "2026-09-18"]
    assert sorted(requests) == ["2026-09-16", "2026-09-17", "2026-09-18"]


def test_a_sidecar_stamped_by_another_version_counts_as_never_tried(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-14")
    fetch = _RejectingFetch()
    run = lambda: backfill.auto_backfill(p, fetch=fetch, fwd_fetch=_no_tenor_prices, fut_fetch=_no_tenor_prices,  # noqa: E731
                                         log=lambda *_: None, clock=lambda: 1000.0)
    assert len(run()) == 5
    sidecar = backfill._state_path(p)
    on_file = json.loads(sidecar.read_text(encoding="utf-8"))
    for entry in on_file["days"].values():
        entry["version"] = "2026-01-01.1+old"
    sidecar.write_text(json.dumps(on_file), encoding="utf-8")
    backfill._day_state.clear()
    fetch.asked.clear()
    assert len(run()) == 5 and len(fetch.asked) == 5
    # and a sidecar that is not JSON is an empty state, never an error
    sidecar.write_text("{not json", encoding="utf-8")
    backfill._day_state.clear()
    fetch.asked.clear()
    assert len(run()) == 5 and json.loads(sidecar.read_text(encoding="utf-8"))["days"]


def test_the_status_block_names_the_tickers_bloomberg_rejects(tmp_path, monkeypatch):
    """(e): "waiting_on_tickers" in the status file's "backfill" block, and the log line."""
    monkeypatch.setattr(live, "book_today", lambda: _MONDAY)
    p, conn = _db(tmp_path, "2026-09-14")
    thread = backfill.start_auto_backfill(p, fetch=_RejectingFetch(), fwd_fetch=_no_tenor_prices,
                                          fut_fetch=_no_tenor_prices, session_factory=lambda: (None, None))
    thread.join(timeout=10)
    block = live.read_status(p)["backfill"]
    assert block["waiting_on_tickers"] == ("5 day(s) wait on tickers Bloomberg rejects (AUDUSD Curncy); "
                                           "asked again when the ticker list changes")
    assert block["days"]["2026-09-18"]["status"] == "NO_CLOSES"
    lines = []
    fetch = _RejectingFetch()
    assert backfill.auto_backfill(p, fetch=fetch, fwd_fetch=_no_tenor_prices, fut_fetch=_no_tenor_prices,
                                  log=lines.append, clock=lambda: 1e9) == []
    assert fetch.asked == []
    assert lines[0] == ("Auto-backfill: 5 day(s) cannot be completed yet; nothing asked of Bloomberg: 5 day(s) wait on "
                        "tickers Bloomberg rejects (AUDUSD Curncy); asked again when the ticker list changes.")


def test_rejection_texts_and_ticker_labels():
    assert backfill.is_rejection("Bloomberg answered the intraday request for USDBRLSP Curncy with: Unknown/Invalid security [nid:11150] ")
    assert backfill.is_rejection("securityError: Unknown/Invalid Security [nid:11150]")
    assert backfill.is_rejection("PX_LAST: Field not valid") and backfill.is_rejection("FWD_CURVE: invalid field")
    assert not backfill.is_rejection("Bloomberg returned no PX_LAST for SPX/E261016C7615 on 2026-09-21")
    assert not backfill.is_rejection("Bloomberg returned no forward tenor prices for USDBRL on 2026-09-18")
    assert backfill._reason_label("Bloomberg answered the intraday request for USDBRLSP Curncy with: Unknown/Invalid security") == "USDBRLSP Curncy"
    assert backfill._reason_label("Bloomberg returned no PX_LAST for SPX/E261016C7615 on 2026-09-21") == "PX_LAST of SPX/E261016C7615"
    assert backfill.recent_business_days(date(2026, 9, 21)) == ["2026-09-16", "2026-09-17", "2026-09-18"]
    assert backfill.recent_business_days(date(2026, 9, 8)) == ["2026-09-02", "2026-09-03", "2026-09-04"]   # Labor Day 09-07 skipped
    assert backfill.state_version().startswith(__import__("data.bloomberg.library", fromlist=["x"]).LIBRARY_VERSION + "+")


# =========================================================================== 2026-09-22: the closing step's ledger block in the status file
_NEW_LEDGER = {"realised": 2, "unrealisable": [{"trade_id": "u1", "reason": "no close on file"}], "repaired": ["r1"],
               "refrozen": [{"trade_id": "a1", "product": "FX_FWD", "mark_type": "SPOT", "spot_as_of_date": "2026-09-09",
                             "pnl_from": 1.0, "pnl_to": 2.0, "why": "the 15:00 close replaced a live row"}],
               "kept": [{"trade_id": "z9", "product": "FX_OPTION", "reason": "no close-out spot on file yet"}]}


def test_closing_step_records_the_ledgers_refreeze_in_the_status_file(tmp_path):
    """`_realise_after_backfill` publishes what the ledger did under backfill.ledger: the
    live pull's status["ledger"] shape (refrozen, kept, refrozen_count, refrozen_summary),
    and returns it (user yes, 2026-09-22)."""
    p, conn = _db(tmp_path, _book_today().isoformat())
    conn.close()
    backfill._published.pop(backfill._db_key(p), None)
    fake = lambda c, as_of, **kw: dict(_NEW_LEDGER)  # noqa: E731
    log = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(backfill, "_import_realise_settled", lambda: fake)
        block = backfill._realise_after_backfill(p, date(2026, 9, 22), log.append)
    assert block["as_of_date"] == "2026-09-22" and block["realised"] == 2
    assert block["refrozen"] == _NEW_LEDGER["refrozen"] and block["kept"] == _NEW_LEDGER["kept"]
    assert block["refrozen_count"] == 1 and block["refrozen_summary"] == "1 settled trade re-frozen at the close"
    assert "Auto-backfill: 2 settled trade(s) frozen after the backfill." in log
    assert "Auto-backfill: 1 settled trade re-frozen at the close." in log
    published = live.read_status(p)["backfill"]["ledger"]
    assert published["as_of_date"] == "2026-09-22" and published["realised"] == 2
    assert published["refrozen"] == _NEW_LEDGER["refrozen"] and published["kept"] == _NEW_LEDGER["kept"]
    assert published["unrealisable"] == _NEW_LEDGER["unrealisable"] and published["repaired"] == ["r1"]
    assert published["refrozen_count"] == 1 and published["refrozen_summary"] == "1 settled trade re-frozen at the close"
    assert [s["step"] for s in published["steps"]] == ["closing"]


def test_closing_step_merges_the_after_last_day_call_and_starts_afresh_next_run(tmp_path):
    """auto_backfill's re-freeze at a past close lands in backfill()'s call after the last
    worked day, then the closing step runs at today: the published block sums both, and a
    later run with no due days (closing alone) does not carry the earlier run's."""
    p, conn = _db(tmp_path, _book_today().isoformat())
    conn.close()
    backfill._published.pop(backfill._db_key(p), None)
    after = live.ledger_block({"realised": 1, "refrozen": [{"trade_id": "a1", "why": "close"}]}, "2026-09-21")
    backfill._record_ledger(p, "after_last_day", after)
    closing = live.ledger_block({"realised": 0, "refrozen": ["b2"], "kept": [{"trade_id": "k1"}]}, "2026-09-22")
    backfill._record_ledger(p, "closing", closing)
    block = live.read_status(p)["backfill"]["ledger"]
    assert block["as_of_date"] == "2026-09-22" and block["realised"] == 1
    assert block["refrozen"] == [{"trade_id": "a1", "why": "close"}, {"trade_id": "b2"}]
    assert block["kept"] == [{"trade_id": "k1"}]
    assert block["refrozen_count"] == 2 and block["refrozen_summary"] == "2 settled trades re-frozen at the close"
    assert [s["step"] for s in block["steps"]] == ["after_last_day", "closing"]
    # the next run has nothing due: its closing step alone, nothing of the last run
    backfill._record_ledger(p, "closing", live.ledger_block({"realised": 0}, "2026-09-23"))
    block = live.read_status(p)["backfill"]["ledger"]
    assert block["realised"] == 0 and block["refrozen"] == [] and block["refrozen_summary"] == ""
    assert [s["step"] for s in block["steps"]] == ["closing"]


def test_start_auto_backfill_keeps_the_ledger_block_in_the_published_backfill_block(tmp_path, monkeypatch):
    yesterday = _book_today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=3)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    p, conn = _db(tmp_path, earliest.isoformat())
    conn.close()
    seen = []
    monkeypatch.setattr(backfill, "_import_realise_settled",
                        lambda: (lambda c, as_of, **kw: seen.append(as_of) or {"realised": 0, "unrealisable": [],
                                                                               "refrozen": ["a1"], "kept": []}))
    thread = backfill.start_auto_backfill(p, fetch=_fake_fetch, fwd_fetch=_fake_fwd_fetch,
                                          session_factory=lambda: (None, None))
    thread.join(timeout=10)
    assert not thread.is_alive()
    block = live.read_status(p)["backfill"]
    assert block["running"] is False
    assert seen and seen[-1] == _book_today().isoformat()                      # the closing step ran at today
    assert block["ledger"]["as_of_date"] == _book_today().isoformat()
    assert block["ledger"]["refrozen"][0] == {"trade_id": "a1"}                 # the old bare-id shape tolerated
    assert block["ledger"]["refrozen_count"] == len(seen) and block["ledger"]["refrozen_summary"].endswith("re-frozen at the close")
    assert [s["step"] for s in block["ledger"]["steps"]] == ["after_last_day", "closing"]
