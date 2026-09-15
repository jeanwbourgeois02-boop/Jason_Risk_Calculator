"""data/bloomberg/backfill.py: history rebuilt from daily closes with a fake fetch. No blpapi."""
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
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("a1", "BNP", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "HAHY7", "t", "d"),
        ("j1", "BNP", "USDJPY", "FX_FWD", "j1", "2026-09-08", 1e6, 150.0, "acc", "cp", "HAHY7", "t", "d"),
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


def test_backfill_writes_marks_realises_in_order_and_snapshots(tmp_path):
    p, conn = _db(tmp_path)
    log = []
    results = backfill.backfill(p, date(2026, 9, 5), date(2026, 9, 11), fetch=fake_fetch, log=log.append)
    assert [r["day"] for r in results] == ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
    assert [r["status"] for r in results] == ["DONE", "DONE", "DONE", "DONE", "NO_CLOSES"]

    # official SPOT marks stamped 15:00 New York with the date's offset (EDT in September)
    marks = conn.execute("SELECT as_of_date, instrument_id, value, source, snapped_at FROM marks ORDER BY 1,2").fetchall()
    assert ("2026-09-07", "AUDUSD", 0.60, "BBG_BFXFORWARD", "2026-09-07T15:00:00-04:00") in marks
    assert not any(m[1] == "EURSEK" for m in marks)
    assert len([m for m in marks if m[0] == "2026-09-10"]) == 1          # AUD close missing that day

    # a1 realised at the 09-09 close (its settle date), not at any later spot
    a1 = conn.execute("SELECT spot_usd_per_local, spot_as_of_date, pnl_usd FROM realised_pnl WHERE trade_id='a1'").fetchone()
    assert a1[0] == 0.63 and a1[1] == "2026-09-09" and a1[2] == pytest.approx(-1e6 * 0.63 + 650000)

    snaps = {r[0]: r for r in conn.execute("SELECT as_of_date, unrealised_usd, realised_ltd_usd, open_trades, complete, missing FROM pnl_snapshots")}
    assert set(snaps) == {"2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"}
    assert snaps["2026-09-07"][1] == pytest.approx(-1e6 * 0.60 + 650000) and snaps["2026-09-07"][3] == 1
    assert snaps["2026-09-08"][3] == 2 and snaps["2026-09-08"][4] == 1
    # 09-10: a1 realised (+20,000), j1 open at 151; AUD had no close but no AUD trade is open -> complete
    assert snaps["2026-09-10"][2] == pytest.approx(20000) and snaps["2026-09-10"][3] == 1 and snaps["2026-09-10"][4] == 1
    assert results[3]["missing_pairs"] == ["AUDUSD"]
    assert "Finished: 4 days written, 4 complete, 1 with no closes" in log[-1]


def test_backfill_skips_complete_days_unless_overwrite(tmp_path):
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


def test_helpers():
    assert backfill.business_days(date(2026, 9, 4), date(2026, 9, 8)) == [date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)]
    assert backfill.close_stamp(date(2026, 1, 15)) == "2026-01-15T15:00:00-05:00"      # EST
    assert backfill.close_stamp(date(2026, 7, 15)) == "2026-07-15T15:00:00-04:00"      # EDT


def test_main_without_bloomberg_writes_nothing(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    from data.bloomberg import live
    monkeypatch.setattr(live, "availability", lambda host, port: (False, "blpapi is not installed"))
    assert backfill.main(["--db", str(p), "--start", "2026-09-07", "--end", "2026-09-08"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pnl_snapshots").fetchone()[0] == 0
