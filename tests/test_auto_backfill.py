"""data/bloomberg/backfill.py: automatic backfill (2026-09-15 decision; extended
2026-09-18 to also backfill FWD_OUTRIGHT/FUTURE_PX, not SPOT alone).

`auto_backfill` fills every business day from the earliest trade date to yesterday that
lacks a complete official close, using `data.bloomberg.inventory.close_completeness`
(now SPOT + FWD_OUTRIGHT + FUTURE_PX); `start_auto_backfill` runs it in a background
thread and publishes progress into the Bloomberg status file. Both are exercised with a
fake fetch -- no blpapi required."""
import threading
import time
from datetime import date, timedelta

import pytest

from data.bloomberg import backfill, live
from data.ingest import schema


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
    assert [r["day"] for r in results] == [d.isoformat() for d in business_days]
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
