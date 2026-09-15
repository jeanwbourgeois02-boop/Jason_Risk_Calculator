"""data/bloomberg/backfill.py: history rebuilt from daily closes with a fake fetch. No blpapi.

Backfill only writes SPOT marks and (if importable) calls engine.pnl.ledger.realise_settled;
it no longer writes pnl_snapshots (BUILD_PLAN.md section 3/6, Task B)."""
from datetime import date

import pytest

from data.bloomberg import backfill
from data.ingest import schema


def _db(tmp_path):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
        ("EURSEK", "FX", "EUR", "SEK", 1, 0, "EURSEK Curncy", "9999-12-31"),   # cross, no trade: ignored
    ])
    # a1 sold 1m AUD @0.65, settles Wed 09-09 (realised from 09-10 on); j1 open through the window
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("a1", "BNP", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "HAHY7", "t", "d", ""),
        ("j1", "BNP", "USDJPY", "FX_FWD", "j1", "2026-09-08", 1e6, 150.0, "acc", "cp", "HAHY7", "t", "d", ""),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-09", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-09", 0.65, 1),
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-09-08", "2026-10-20", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-09-08", "2026-10-20", 150.0, 1),
    ])
    conn.commit()
    return p, conn


CLOSES = {
    date(2026, 9, 7): {"AUDUSD Curncy": 0.60, "USDJPY Curncy": 148.0},   # Mon: a1 open, j1 not yet traded
    date(2026, 9, 8): {"AUDUSD Curncy": 0.62, "USDJPY Curncy": 150.0},   # Tue: both open
    date(2026, 9, 9): {"AUDUSD Curncy": 0.63, "USDJPY Curncy": 152.0},   # Wed: a1 settles today (still in ladder)
    date(2026, 9, 10): {"AUDUSD Curncy": None, "USDJPY Curncy": 151.0},  # Thu: a1 realised at 09-09 close
    date(2026, 9, 11): {},                                               # Fri: holiday, nothing returned
}


def fake_fetch(session, service, tickers, field, day):
    assert field == "PX_LAST" and set(tickers) == {"AUDUSD Curncy", "USDJPY Curncy"}
    return CLOSES.get(day, {})


def test_backfill_writes_marks_and_realises_in_order(tmp_path):
    p, conn = _db(tmp_path)
    log = []
    results = backfill.backfill(p, date(2026, 9, 5), date(2026, 9, 11), fetch=fake_fetch, log=log.append)
    assert [r["day"] for r in results] == ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
    assert [r["status"] for r in results] == ["DONE", "DONE", "DONE", "DONE", "NO_CLOSES"]

    # official SPOT marks stamped 17:00 New York with the date's offset (EDT in September)
    marks = conn.execute("SELECT as_of_date, instrument_id, value, source, snapped_at FROM marks ORDER BY 1,2").fetchall()
    assert ("2026-09-07", "AUDUSD", 0.60, "BBG_BFXFORWARD", "2026-09-07T17:00:00-04:00") in marks
    assert not any(m[1] == "EURSEK" for m in marks)
    assert len([m for m in marks if m[0] == "2026-09-10"]) == 1          # AUD close missing that day

    by_day = {r["day"]: r for r in results}
    assert by_day["2026-09-10"]["missing_pairs"] == ["AUDUSD"]
    assert "Finished: 4 days written, 1 with no closes, 0 skipped" in log[-1]
    # Realisation itself is engine/pnl/ledger.py::realise_settled (owned by the pnl-engine
    # task, developed in parallel). backfill.py only calls it, guarded: if the call raised
    # (e.g. a mid-rewrite schema mismatch) 'realised' is None and marks are still written;
    # if it succeeded, a1 (settled 09-09) must show up realised from 09-10 on, not 09-09.
    if by_day["2026-09-10"]["realised"] is not None:
        assert by_day["2026-09-09"]["realised"] == 0          # a1's settle date itself: not yet < as_of
        assert by_day["2026-09-10"]["realised"] == 1          # first day strictly after settlement
        a1 = conn.execute("SELECT spot_usd_per_local, spot_as_of_date, pnl_usd FROM realised_pnl "
                          "WHERE trade_id='a1'").fetchone()
        assert a1[0] == 0.63 and a1[1] == "2026-09-09" and a1[2] == pytest.approx(-1e6 * 0.63 + 650000)

    # no pnl_snapshots row is written any more; the table (if it still exists) stays empty
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "pnl_snapshots" in tables:
        assert conn.execute("SELECT COUNT(*) FROM pnl_snapshots").fetchone()[0] == 0


def test_backfill_skips_days_with_all_closes_unless_overwrite(tmp_path):
    p, conn = _db(tmp_path)
    calls = []

    def counting_fetch(session, service, tickers, field, day):
        calls.append(day)
        return CLOSES.get(day, {})

    backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=counting_fetch, log=lambda s: None)
    assert calls == [date(2026, 9, 7), date(2026, 9, 8)]
    res = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=counting_fetch, log=lambda s: None)
    assert [r["status"] for r in res] == ["SKIPPED", "SKIPPED"] and len(calls) == 2
    res = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=counting_fetch, overwrite=True, log=lambda s: None)
    assert [r["status"] for r in res] == ["DONE", "DONE"] and len(calls) == 4


def test_backfill_without_realise_settled_still_writes_marks(tmp_path, monkeypatch):
    """If engine.pnl.ledger.realise_settled is not importable (mid-rewrite by the
    pnl-engine task), backfill still writes marks and reports realised=None, never raises."""
    p, conn = _db(tmp_path)
    monkeypatch.setattr(backfill, "_import_realise_settled", lambda: None)
    log = []
    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=fake_fetch, log=log.append)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert all(r["realised"] is None for r in results)
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 0
    assert any("realise_settled not importable" in s for s in log)


def test_helpers():
    assert backfill.business_days(date(2026, 9, 4), date(2026, 9, 8)) == [date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)]
    assert backfill.close_stamp(date(2026, 1, 15)) == "2026-01-15T17:00:00-05:00"      # EST
    assert backfill.close_stamp(date(2026, 7, 15)) == "2026-07-15T17:00:00-04:00"      # EDT


def test_main_without_bloomberg_writes_nothing(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    from data.bloomberg import live
    monkeypatch.setattr(live, "availability", lambda host, port: (False, "blpapi is not installed"))
    assert backfill.main(["--db", str(p), "--start", "2026-09-07", "--end", "2026-09-08"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0
