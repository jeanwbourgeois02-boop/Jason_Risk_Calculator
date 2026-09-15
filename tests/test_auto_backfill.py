"""data/bloomberg/backfill.py: automatic backfill (2026-09-15 decision).

`auto_backfill` fills every business day from the earliest trade date to yesterday that
lacks a complete official close, using `data.bloomberg.inventory.close_completeness`;
`start_auto_backfill` runs it in a background thread and publishes progress into the
Bloomberg status file. Both are exercised with a fake fetch -- no blpapi required."""
import threading
import time
from datetime import date, timedelta

import pytest

from data.bloomberg import backfill, live
from data.ingest import schema


def _db(tmp_path, earliest_trade_date: str):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("a1", "BNP", "AUDUSD", "FX_FWD", "a1", earliest_trade_date, -1e6, 0.65,
                  "acc", "cp", "HAHY7", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, earliest_trade_date, "2099-01-01", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, earliest_trade_date, "2099-01-01", 0.65, 1),
    ])
    conn.commit()
    return p, conn


def _fake_fetch(session, service, tickers, field, day):
    assert set(tickers) == {"AUDUSD Curncy"}
    return {"AUDUSD Curncy": 0.61}


def test_auto_backfill_fills_from_earliest_trade_to_yesterday(tmp_path):
    yesterday = date.today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=5)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    p, conn = _db(tmp_path, earliest.isoformat())
    progress = []
    results = backfill.auto_backfill(p, fetch=_fake_fetch, log=lambda *_: None,
                                     on_progress=progress.append)
    business_days = backfill.business_days(earliest, yesterday)
    assert [r["day"] for r in results] == [d.isoformat() for d in business_days]
    assert all(r["status"] == "DONE" for r in results)
    assert progress[0] == len(business_days)
    assert progress[-1] == 0

    conn2 = schema.connect(p)
    have = {r[0] for r in conn2.execute(
        "SELECT as_of_date FROM marks WHERE instrument_id='AUDUSD' AND mark_type='SPOT'")}
    assert have == {d.isoformat() for d in business_days}


def test_auto_backfill_no_trades_does_nothing(tmp_path):
    p = tmp_path / "risk.db"
    schema.connect(p).commit()
    log = []
    assert backfill.auto_backfill(p, fetch=_fake_fetch, log=log.append) == []
    assert any("no trades" in m.lower() for m in log)


def test_auto_backfill_already_complete_reports_zero_remaining(tmp_path):
    yesterday = date.today() - timedelta(days=1)
    p, conn = _db(tmp_path, yesterday.isoformat())
    # First pass fills everything.
    backfill.auto_backfill(p, fetch=_fake_fetch, log=lambda *_: None)
    progress = []
    results = backfill.auto_backfill(p, fetch=_fake_fetch, log=lambda *_: None,
                                     on_progress=progress.append)
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
    yesterday = date.today() - timedelta(days=1)
    earliest = yesterday - timedelta(days=3)
    while earliest.weekday() >= 5:
        earliest -= timedelta(days=1)
    p, conn = _db(tmp_path, earliest.isoformat())

    thread = backfill.start_auto_backfill(p, fetch=_fake_fetch, session_factory=lambda: (None, None))
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()

    status = live.read_status(p)
    assert status["backfill"]["running"] is False
    assert status["backfill"]["remaining"] == 0

    # A second trigger while the lock is free should also complete cleanly (idempotent).
    thread2 = backfill.start_auto_backfill(p, fetch=_fake_fetch, session_factory=lambda: (None, None))
    assert thread2 is not None
    thread2.join(timeout=5)


def test_start_auto_backfill_second_call_while_running_is_a_noop(tmp_path):
    yesterday = date.today() - timedelta(days=1)
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

    t1 = backfill.start_auto_backfill(p, fetch=slow_fetch, session_factory=lambda: (None, None))
    assert t1 is not None
    started.wait(timeout=5)
    t2 = backfill.start_auto_backfill(p, fetch=slow_fetch, session_factory=lambda: (None, None))
    assert t2 is None  # lock held by t1
    release.set()
    t1.join(timeout=5)
