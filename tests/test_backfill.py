"""data/bloomberg/backfill.py: history rebuilt from daily closes with a fake fetch. No blpapi.

Backfill only writes SPOT marks and (if importable) calls engine.pnl.ledger.realise_settled;
it no longer writes pnl_snapshots (BUILD_PLAN.md section 3/6, Task B)."""
from datetime import date, timedelta

import pytest

from data.bloomberg import backfill
from data.ingest import schema

import engine.pnl.calendar as _calendar
REAL_HOLIDAYS = _calendar._DEFAULT_HOLIDAYS_PATH   # config/holidays.txt, before any test pins the calendar


@pytest.fixture(autouse=True)
def _close_1500_on_every_date(monkeypatch):
    """The 15:00 New York close applies from backfill.CLOSE_1500_FROM (2026-09-21) and inside
    Bloomberg's intraday history counted from today. These tests exercise it on earlier dates,
    so the cutover is moved back and 'today' is pinned (a test that needs another today
    passes it or patches live.book_today itself). The real cutover has its own tests."""
    from data.bloomberg import live as _live
    monkeypatch.setattr(backfill, "CLOSE_1500_FROM", date(2000, 1, 1))
    monkeypatch.setattr(_live, "book_today", lambda: date(2026, 9, 21))


@pytest.fixture(autouse=True)
def _monday_to_friday_calendar(monkeypatch, tmp_path):
    """These tests work Mon 2026-09-07 (Labor Day in config/holidays.txt) as an ordinary day.
    The backfill now skips a listed holiday, so the calendar is pinned to Monday-Friday here;
    the holiday rule has its own test, which puts the real file back."""
    import engine.pnl.calendar as cal
    monkeypatch.setattr(cal, "_DEFAULT_HOLIDAYS_PATH", tmp_path / "no-holidays.txt")



def _db(tmp_path):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
        ("EURSEK", "FX", "EUR", "SEK", 1, 0, "EURSEK Curncy", "9999-12-31"),   # cross, no trade: ignored
    ])
    # a1 sold 1m AUD @0.65, settles Wed 09-09 (realised from 09-10 on); j1 open through the window
    # 'XLSX' (2026-09-16, trades_official double-count fix): ledger/backfill now read
    # trades_official, which excludes source='BNP' by design.
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "HAHY7", "t", "d", ""),
        ("j1", "XLSX", "USDJPY", "FX_FWD", "j1", "2026-09-08", 1e6, 150.0, "acc", "cp", "HAHY7", "t", "d", ""),
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


ASKED = {}      # day -> the tickers a close was asked for on that day (fake_fetch)


def fake_fetch(session, service, tickers, field, day):
    assert field == "PX_LAST" and set(tickers) <= {"AUDUSD Curncy", "USDJPY Curncy"}
    ASKED[day] = sorted(tickers)
    return CLOSES.get(day, {})


# 2026-09-18: backfill() also needs a batched forward-curve history (fwd_fetch) to
# resolve FWD_OUTRIGHT for AUDUSD (settles 2026-09-09) and USDJPY (settles 2026-10-20).
# Every standard-tenor ticker "quotes" the pair's own fixed leg settle date on every day
# requested, so fwd_curve.outright_for_date's EXACT-match branch resolves it directly --
# these tests only need FWD_OUTRIGHT to resolve, not a realistic curve shape.
_FWD_SETTLE = {"AUDUSD": "2026-09-09", "USDJPY": "2026-10-20"}
_FWD_VALUE = {"AUDUSD": 0.655, "USDJPY": 149.5}


def fake_fwd_fetch(session, service, tickers, fields, start, end):
    out = {}
    d = start
    while d <= end:
        for ticker in tickers:
            pair = next((p for p in _FWD_SETTLE if ticker.startswith(p)), None)
            if pair is not None:
                out.setdefault(ticker, {})[d.isoformat()] = {
                    "PX_LAST": _FWD_VALUE[pair], "SETTLE_DT": _FWD_SETTLE[pair]}
        d += timedelta(days=1)
    return out


def test_backfill_writes_marks_and_realises_in_order(tmp_path):
    p, conn = _db(tmp_path)
    log = []
    results = backfill.backfill(p, date(2026, 9, 5), date(2026, 9, 11), fetch=fake_fetch, fwd_fetch=fake_fwd_fetch, fut_fetch=fake_fwd_fetch, log=log.append)
    assert [r["day"] for r in results] == ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
    assert [r["status"] for r in results] == ["DONE", "DONE", "DONE", "DONE", "NO_CLOSES"]

    # official SPOT marks stamped at the close -- 15:00 New York (user decision 2026-09-21;
    # it was 17:00) -- with the date's offset (EDT in September)
    marks = conn.execute("SELECT as_of_date, instrument_id, value, source, snapped_at FROM marks ORDER BY 1,2").fetchall()
    assert ("2026-09-07", "AUDUSD", 0.60, "BBG_BFXFORWARD", "2026-09-07T15:00:00-04:00") in marks
    assert not any(m[1] == "EURSEK" for m in marks)
    # 09-10: USDJPY (still open) gets both its SPOT and its FWD_OUTRIGHT.
    assert len([m for m in marks if m[0] == "2026-09-10"]) == 2
    assert {m[1:4] for m in marks if m[0] == "2026-09-10"} == {
        ("USDJPY", 151.0, "BBG_BFXFORWARD"), ("USDJPY", 149.5, "BBG_BFXFORWARD")}

    # 2026-09-21 ("only the data necessary ... also for the backfill"): a day asks only for
    # the pairs the book needed THAT day. USDJPY was not traded before 09-08; AUDUSD settled
    # 09-09, so on 09-10 it is neither asked for nor reported missing (it used to be both).
    assert ASKED[date(2026, 9, 7)] == ["AUDUSD Curncy"]
    assert ASKED[date(2026, 9, 9)] == ["AUDUSD Curncy", "USDJPY Curncy"]
    assert ASKED[date(2026, 9, 10)] == ["USDJPY Curncy"]
    assert not any(m[0] == "2026-09-07" and m[1] == "USDJPY" for m in marks)
    by_day = {r["day"]: r for r in results}
    assert by_day["2026-09-10"]["missing_pairs"] == []
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

    backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=counting_fetch, fwd_fetch=fake_fwd_fetch, fut_fetch=fake_fwd_fetch, log=lambda s: None)
    assert calls == [date(2026, 9, 7), date(2026, 9, 8)]
    res = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=counting_fetch, fwd_fetch=fake_fwd_fetch, fut_fetch=fake_fwd_fetch, log=lambda s: None)
    assert [r["status"] for r in res] == ["SKIPPED", "SKIPPED"] and len(calls) == 2
    res = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=counting_fetch, fwd_fetch=fake_fwd_fetch, fut_fetch=fake_fwd_fetch, overwrite=True, log=lambda s: None)
    assert [r["status"] for r in res] == ["DONE", "DONE"] and len(calls) == 4


def test_backfill_without_realise_settled_still_writes_marks(tmp_path, monkeypatch):
    """If engine.pnl.ledger.realise_settled is not importable (mid-rewrite by the
    pnl-engine task), backfill still writes marks and reports realised=None, never raises."""
    p, conn = _db(tmp_path)
    monkeypatch.setattr(backfill, "_import_realise_settled", lambda: None)
    log = []
    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=fake_fetch, fwd_fetch=fake_fwd_fetch, fut_fetch=fake_fwd_fetch, log=log.append)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert all(r["realised"] is None for r in results)
    # 09-07: SPOT x1 + FWD_OUTRIGHT x1 (AUDUSD only -- USDJPY not yet traded that day, so
    # since 2026-09-21 its close is not asked for either); 09-08: SPOT x2 + FWD_OUTRIGHT x2
    # (both open) = 2 + 4 = 6.
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type='FWD_OUTRIGHT'").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 0
    assert any("realise_settled not importable" in s for s in log)


def test_a_listed_holiday_is_never_worked_or_listed_as_incomplete(tmp_path, monkeypatch):
    """Labor Day (Mon 2026-09-07, in config/holidays.txt): FX quotes that day but futures do
    not settle, so under the old Monday-Friday day list it was asked of Bloomberg on every
    run and could never complete. It is no trading day: not worked, not in the completeness
    list, and Fri 09-04 and Tue 09-08 are one stretch (one request, not two)."""
    import engine.pnl.calendar as cal
    from data.bloomberg.inventory import close_completeness
    monkeypatch.setattr(cal, "_DEFAULT_HOLIDAYS_PATH", REAL_HOLIDAYS)
    assert backfill.business_days(date(2026, 9, 4), date(2026, 9, 8)) == [date(2026, 9, 4), date(2026, 9, 8)]
    assert backfill._runs([date(2026, 9, 4), date(2026, 9, 8)]) == [(date(2026, 9, 4), date(2026, 9, 8))]
    p, conn = _db(tmp_path)
    ASKED.clear()
    results = backfill.backfill(p, date(2026, 9, 4), date(2026, 9, 8), fetch=fake_fetch, fwd_fetch=fake_fwd_fetch,
                                fut_fetch=lambda *a, **k: {}, log=lambda *_: None)
    assert [r["day"] for r in results] == ["2026-09-04", "2026-09-08"] and date(2026, 9, 7) not in ASKED
    assert list(close_completeness(conn, "2026-09-04", "2026-09-08")["as_of_date"]) == ["2026-09-04", "2026-09-08"]


def test_helpers():
    assert backfill.business_days(date(2026, 9, 4), date(2026, 9, 8)) == [date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)]
    assert backfill.business_days(date(2026, 9, 4), date(2026, 9, 8), frozenset({"2026-09-07"})) == [
        date(2026, 9, 4), date(2026, 9, 8)]
    # the official close is 15:00 New York (user decision 2026-09-21), one constant for it
    from data.bloomberg import pull_marks
    assert pull_marks.CLOSE_HOUR_NY == 15
    assert backfill.close_stamp(date(2026, 1, 15), today=date(2026, 3, 2)) == "2026-01-15T15:00:00-05:00"   # EST
    assert backfill.close_stamp(date(2026, 7, 15)) == "2026-07-15T15:00:00-04:00"      # EDT
    # a day Bloomberg's intraday history no longer reaches closes at the 17:00 daily close
    assert backfill.close_stamp(date(2026, 1, 15), today=date(2026, 9, 21)) == "2026-01-15T17:00:00-05:00"
    assert pull_marks.snapped_at(date(2026, 7, 15)) == "2026-07-15T15:00:00-04:00"    # the same stamp


def test_main_without_bloomberg_writes_nothing(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    from data.bloomberg import live
    monkeypatch.setattr(live, "availability", lambda host, port: (False, "blpapi is not installed"))
    assert backfill.main(["--db", str(p), "--start", "2026-09-07", "--end", "2026-09-08"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


# =========================================================================== 2026-09-18: FWD_OUTRIGHT / FUTURE_PX
# One forward (both legs, settling on the SECOND of the two backfilled days) and one
# future, exercising: genuine linear-in-forward-points interpolation on the day BEFORE
# settlement (hand-verified below), the "settles on/before this day -> mark at spot" rule
# on the settlement day itself, FUTURE_PX on both days, and close_completeness reporting
# the interpolated (BBG_INTERP, never official) day as incomplete while the fully-official
# day reads complete.
def _db_with_future(tmp_path):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('AUDUSD','FX','AUD','USD',1,0,'AUDUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-12-19')")
    conn.execute("INSERT INTO trades VALUES ('a1','XLSX','AUDUSD','FX_FWD','a1','2026-08-10',-1e6,0.65,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-08", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-08", 0.65, 1),
    ])
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',6*50*7528.25,'2026-08-10','2026-12-19',0,0)")
    conn.commit()
    return p, conn


_SPOT_2 = {date(2026, 9, 7): {"AUDUSD Curncy": 0.6505}, date(2026, 9, 8): {"AUDUSD Curncy": 0.6520}}
_FUTURE_PX_2 = {date(2026, 9, 7): 7550.0, date(2026, 9, 8): 7560.0}


def spot_fetch_2(session, service, tickers, field, day):
    assert set(tickers) == {"AUDUSD Curncy"}
    return _SPOT_2.get(day, {})


def fwd_fetch_2(session, service, tickers, fields, start, end):
    """A flat two-point curve (SP @ 2026-09-07 = 0.6500, 1W @ 2026-09-14 = 0.6570) on
    every day in the range, for the interpolation to be hand-verified against."""
    assert set(fields) == {"PX_LAST", "SETTLE_DT"}
    out = {}
    d = start
    while d <= end:
        out.setdefault("AUDUSDSP Curncy", {})[d.isoformat()] = {"PX_LAST": 0.6500, "SETTLE_DT": "2026-09-07"}
        out.setdefault("AUDUSD1W Curncy", {})[d.isoformat()] = {"PX_LAST": 0.6570, "SETTLE_DT": "2026-09-14"}
        d += timedelta(days=1)
    return out


def fut_fetch_2(session, service, tickers, fields, start, end):
    assert set(tickers) == {"ESU6 Index"} and fields == ["PX_SETTLE"]
    out = {}
    d = start
    while d <= end:
        out.setdefault("ESU6 Index", {})[d.isoformat()] = {"PX_SETTLE": _FUTURE_PX_2[d]}
        d += timedelta(days=1)
    return out


def test_backfill_interpolates_fwd_outright_and_prices_future_hand_verified(tmp_path):
    p, conn = _db_with_future(tmp_path)
    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=spot_fetch_2, fwd_fetch=fwd_fetch_2,
                                fut_fetch=fut_fetch_2, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert [r["fwd_outrights"] for r in results] == [1, 1]
    assert [r["future_px"] for r in results] == [1, 1]
    assert [r["missing_marks"] for r in results] == [[], []]

    rows = {(r[0], r[1], r[2]): (r[3], r[4]) for r in conn.execute(
        "SELECT as_of_date, mark_type, settle_date, value, source FROM marks")}

    # 2026-09-07: target settle 2026-09-08 falls strictly between the SP (09-07) and 1W
    # (09-14) tenor points -- linear-in-forward-points interpolation, by hand:
    #   w = (09-08 - 09-07).days / (09-14 - 09-07).days = 1/7
    #   value = 0.6500 + (1/7) * (0.6570 - 0.6500) = 0.6500 + 0.001 = 0.6510
    # written under BBG_INTERP, never as BBG_BFXFORWARD, so the provenance stays visible
    # -- but per the 2026-09-18 user decision (data/ingest/schema.py
    # OFFICIAL_FALLBACK_SOURCE) BBG_INTERP is now the OFFICIAL fallback for FWD_OUTRIGHT
    # wherever no BBG_BFXFORWARD row exists for the same key, so this row IS official.
    value, source = rows[("2026-09-07", "FWD_OUTRIGHT", "2026-09-08")]
    assert value == pytest.approx(0.6510, abs=1e-9)
    assert source == "BBG_INTERP"

    # 2026-09-08: the leg settles THIS day -- marked at that day's own SPOT close, not
    # interpolated, and official (BBG_BFXFORWARD) directly, no fallback needed.
    value, source = rows[("2026-09-08", "FWD_OUTRIGHT", "2026-09-08")]
    assert value == pytest.approx(0.6520) and source == "BBG_BFXFORWARD"

    # FUTURE_PX both days, source BBG_BDH (official).
    assert rows[("2026-09-07", "FUTURE_PX", "2026-12-19")] == (7550.0, "BBG_BDH")
    assert rows[("2026-09-08", "FUTURE_PX", "2026-12-19")] == (7560.0, "BBG_BDH")

    # Completeness: both days now read complete -- 09-07's FWD_OUTRIGHT is official via
    # the BBG_INTERP fallback (no competing BBG_BFXFORWARD row for that key), 09-08's is
    # official directly.
    from data.bloomberg.inventory import close_completeness
    comp = {r.as_of_date: r for r in close_completeness(conn, "2026-09-07", "2026-09-08").itertuples()}
    assert comp["2026-09-07"].complete is True and comp["2026-09-07"].missing == []
    assert comp["2026-09-08"].complete is True and comp["2026-09-08"].missing == []


def test_backfill_never_overwrites_an_existing_official_mark_even_on_an_incomplete_day(tmp_path):
    """A day that is NOT fully complete (FUTURE_PX still missing) must still leave its
    already-official SPOT/FWD_OUTRIGHT untouched -- the per-item guard, not just the
    day-level skip (which alone would not protect this case, since the day is reprocessed
    at all)."""
    p, conn = _db_with_future(tmp_path)
    day = "2026-09-08"
    stamp = backfill.close_stamp(date(2026, 9, 8))
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (day, "AUDUSD", day, "SPOT", 0.9001, "BBG_BFXFORWARD", stamp),
        (day, "AUDUSD", day, "FWD_OUTRIGHT", 0.9002, "BBG_BFXFORWARD", stamp),
        # FUTURE_PX deliberately absent -> this day is NOT complete, so backfill() will
        # reprocess it rather than skip it outright.
    ])
    conn.commit()

    results = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=spot_fetch_2, fwd_fetch=fwd_fetch_2,
                                fut_fetch=fut_fetch_2, log=lambda *_: None)
    assert results[0]["status"] == "DONE"

    rows = dict(conn.execute(
        "SELECT mark_type, value FROM marks WHERE as_of_date=? AND instrument_id='AUDUSD'", (day,)).fetchall())
    assert rows["SPOT"] == 0.9001            # untouched, not overwritten with 0.6520
    assert rows["FWD_OUTRIGHT"] == 0.9002    # untouched, not overwritten with 0.6520
    future_value = conn.execute(
        "SELECT value FROM marks WHERE as_of_date=? AND instrument_id='ESU6 Index'", (day,)).fetchone()[0]
    assert future_value == 7560.0            # the genuinely missing mark IS written


# =========================================================================== 2026-09-18: crosses + expiry-day boundary
# Reference-book audit finding: a book with 33 EURSEK forwards (a cross) and an ESU6
# future expiring inside the backfill range. traded_pairs() being USD-pairs-only meant
# (a) EURSEK's own SPOT was never backfilled at all, so realise_settled could never
# freeze a settled EURSEK forward (it needs the pair's own official SPOT on or before
# settlement), and (b) a book with EURSEK but no *direct* USD-pair FX trade short-
# circuited backfill()'s very first line (`if not pairs: return []`) before ever
# reaching the future logic, so ESU6's FUTURE_PX was silently never attempted either.
def test_backfill_lone_cross_forward_gets_historical_spot_and_fwd_outright(tmp_path):
    """A book holding ONLY a EURSEK forward (no direct USD-pair FX trade at all) must
    still get EURSEK's own SPOT AND FWD_OUTRIGHT backfilled -- proving traded_pairs()
    no longer gates on USD pairs, and that the gate no longer short-circuits the whole
    run for a crosses-only book."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('EURSEK','FX','EUR','SEK',1,0,'EURSEK Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('e1','XLSX','EURSEK','FX_FWD','e1','2026-08-10',1e6,11.20,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("e1", 1, "FX_NEAR", "EUR", 1e6, "2026-08-10", "2026-09-25", 11.20, 1),
        ("e1", 2, "FX_NEAR", "SEK", -11200000, "2026-08-10", "2026-09-25", 11.20, 1),
    ])
    conn.commit()

    from data.bloomberg import live
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")

    def spot_fetch(session, service, tickers, field, day):
        # 2026-09-18: the cross's USD-conversion pairs are asked for too even though no
        # instrument row existed for them -- backfill() now creates the plain pair rows
        # first (live._ensure_fx_instruments), as the live pull does. Before, they were
        # "needed" forever and fetched by nobody, so a cross that settled before this PC
        # first connected could never be converted to USD, hence never realised.
        assert set(tickers) == {"EURSEK Curncy", f"{eur_usd} Curncy", f"{usd_sek} Curncy"}
        return {"EURSEK Curncy": 11.21, f"{eur_usd} Curncy": 1.17, f"{usd_sek} Curncy": 9.58}

    def fwd_fetch(session, service, tickers, fields, start, end):
        # exact-match trick: every tenor ticker quotes the leg's own settle date, so
        # outright_for_date resolves it directly regardless of which day is asked.
        # SPOT only for the conversion pairs: no forward curve is ever asked for them.
        assert all(t.startswith("EURSEK") for t in tickers)
        out = {}
        d = start
        while d <= end:
            for ticker in tickers:
                out.setdefault(ticker, {})[d.isoformat()] = {"PX_LAST": 11.25, "SETTLE_DT": "2026-09-25"}
            d += timedelta(days=1)
        return out

    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=spot_fetch, fwd_fetch=fwd_fetch,
                                fut_fetch=fwd_fetch, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    conversion_spots = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT instrument_id, as_of_date, value FROM marks_official WHERE mark_type='SPOT' "
        "AND instrument_id IN (?, ?)", (eur_usd, usd_sek))}
    assert conversion_spots == {(eur_usd, "2026-09-07"): 1.17, (eur_usd, "2026-09-08"): 1.17,
                                (usd_sek, "2026-09-07"): 9.58, (usd_sek, "2026-09-08"): 9.58}
    from data.bloomberg.inventory import close_completeness
    assert list(close_completeness(conn, "2026-09-07", "2026-09-08")["complete"]) == [True, True]

    spot_days = {r[0] for r in conn.execute(
        "SELECT as_of_date FROM marks WHERE instrument_id='EURSEK' AND mark_type='SPOT'")}
    fwd_days = {r[0] for r in conn.execute(
        "SELECT as_of_date FROM marks WHERE instrument_id='EURSEK' AND mark_type='FWD_OUTRIGHT'")}
    assert spot_days == {"2026-09-07", "2026-09-08"}
    assert fwd_days == {"2026-09-07", "2026-09-08"}

    value, source = conn.execute(
        "SELECT value, source FROM marks WHERE instrument_id='EURSEK' AND mark_type='SPOT' "
        "AND as_of_date='2026-09-07'").fetchone()
    assert value == 11.21 and source == "BBG_BFXFORWARD"
    value, source = conn.execute(
        "SELECT value, source FROM marks WHERE instrument_id='EURSEK' AND mark_type='FWD_OUTRIGHT' "
        "AND as_of_date='2026-09-07'").fetchone()
    assert value == 11.25 and source == "BBG_BFXFORWARD"      # EXACT match, not interpolated


def test_backfill_future_expiring_inside_range_gets_future_px_on_expiry_day(tmp_path):
    """A future whose expiry (settle_date) falls INSIDE the backfilled range -- not just
    strictly after it -- must get a FUTURE_PX on the expiry day itself, or a PC first
    connected after expiry would leave it unpriced forever (the freeze wants the last
    official FUTURE_PX on or before the expiry day to realise it)."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESU6 Index','FUTURE','f1','2026-08-10',11,7500.0,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',11*50*7500.0,'2026-08-10','2026-09-18',0,0)")
    conn.commit()

    future_closes = {date(2026, 9, 17): 7600.0, date(2026, 9, 18): 7610.0}

    def fut_fetch(session, service, tickers, fields, start, end):
        assert set(tickers) == {"ESU6 Index"} and fields == ["PX_SETTLE"]
        out = {}
        d = start
        while d <= end:
            if d in future_closes:
                out.setdefault("ESU6 Index", {})[d.isoformat()] = {"PX_SETTLE": future_closes[d]}
            d += timedelta(days=1)
        return out

    # A book with only a future and no FX trade at all: the upstream "no pairs" bailout
    # must not also block the future logic (2026-09-18 fix).
    results = backfill.backfill(p, date(2026, 9, 17), date(2026, 9, 18), fetch=lambda *a, **k: {},
                                fwd_fetch=lambda *a, **k: {}, fut_fetch=fut_fetch, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert [r["future_px"] for r in results] == [1, 1]

    rows = dict(conn.execute(
        "SELECT as_of_date, value FROM marks WHERE instrument_id='ESU6 Index' AND mark_type='FUTURE_PX'"))
    assert rows == {"2026-09-17": 7600.0, "2026-09-18": 7610.0}      # expiry day itself included

    from data.bloomberg.inventory import close_completeness
    comp = {r.as_of_date: r for r in close_completeness(conn, "2026-09-17", "2026-09-18").itertuples()}
    assert comp["2026-09-18"].complete is True and comp["2026-09-18"].missing == []


# =========================================================================== 2026-09-18: FX options' closing SPOT
# traded_pairs() looked at FX legs only, so a pair held only through options never got a
# historical SPOT close -- nor did the option's own USD-conversion pairs (an option's value
# converts base->USD at spot; the EURSEK digital pays EUR). On a missed expiry day
# engine/options writes the payoff on a later pull FROM THE EXPIRY DATE'S CLOSING SPOT and
# the ledger converts it at the base->USD SPOT of that same date: without those closes an
# expired option stays unrealisable. SPOT only: no historical forward, no historical vol.
def _options_only_db(tmp_path, options):
    """Only EURSEK options: no forward, and no plain pair instrument row of any kind."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    for trade_id, option_id, trade_date, qty, expiry in options:
        conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                     (option_id, "FX_OPTION", "EUR", "SEK", 1, 0, option_id, expiry))
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (trade_id, "XLSX", option_id, "FX_OPTION", trade_id, trade_date, qty, 0.01,
                      "acc", "cp", "", "t", "d", ""))
        conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                     (trade_id, 1, "NOTIONAL", "EUR", qty, trade_date, expiry, 0, 0))
    conn.commit()
    return p, conn


def _never_called(*a, **k):
    raise AssertionError("no forward-curve / future history may be requested for an option-only book")


def test_backfill_option_only_pair_gets_closing_spot_and_usd_conversion_spots_through_expiry(tmp_path):
    from data.bloomberg import live
    from data.bloomberg.inventory import close_completeness
    # traded Fri 09-18, expires Wed 09-23; the range runs a day either side
    p, conn = _options_only_db(tmp_path, [("o1", "EURSEK092326C-1", "2026-09-18", 35e6, "2026-09-23")])
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")
    tickers_wanted = {"EURSEK Curncy", f"{eur_usd} Curncy", f"{usd_sek} Curncy"}
    assert backfill.spot_only_pair_names(conn, date(2026, 9, 17), date(2026, 9, 24)) == sorted(
        live.option_spot_pair_names("EUR", "SEK"))

    def spot_fetch(session, service, tickers, field, day):
        assert field == "PX_LAST" and set(tickers) == tickers_wanted and len(tickers) == 3   # each once
        bump = day.day / 1000.0
        return {"EURSEK Curncy": 11.0 + bump, f"{eur_usd} Curncy": 1.1 + bump, f"{usd_sek} Curncy": 9.4 + bump}

    results = backfill.backfill(p, date(2026, 9, 17), date(2026, 9, 24), fetch=spot_fetch, fwd_fetch=_never_called,
                                fut_fetch=_never_called, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] * 6
    assert all(r["fwd_outrights"] == 0 and r["missing_marks"] == [] and r["missing_pairs"] == [] for r in results)

    open_days = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"]          # trade date .. EXPIRY DATE inclusive
    spots = {(r[0], r[1]): (r[2], r[3], r[4]) for r in conn.execute(
        "SELECT instrument_id, as_of_date, value, source, snapped_at FROM marks_official WHERE mark_type = 'SPOT'")}
    for day in open_days:
        for pair in ("EURSEK", eur_usd, usd_sek):
            assert (pair, day) in spots, (pair, day)
    assert spots[("EURSEK", "2026-09-23")] == (pytest.approx(11.023), "BBG_BFXFORWARD", "2026-09-23T15:00:00-04:00")
    assert spots[(eur_usd, "2026-09-23")][0] == pytest.approx(1.123)
    # SPOT only: nothing but SPOT was written for anything
    assert {r[0] for r in conn.execute("SELECT DISTINCT mark_type FROM marks")} == {"SPOT"}
    # the plain pair rows were created by the live pull's own helper, not a second one
    assert {r[0] for r in conn.execute("SELECT instrument_id FROM instruments WHERE asset_class = 'FX'")} == {
        "EURSEK", eur_usd, usd_sek}

    # the expiry date's closes are found by the two readers that need them
    from engine.options.inputs import get_spot
    from engine.pnl.valuation import usd_per_quote
    assert get_spot(conn, "2026-09-23", "EURSEK") == pytest.approx(11.023)         # the catch-up's payoff spot
    s, pair, _src = usd_per_quote(conn, "EUR", "2026-09-23")                       # the ledger's base->USD conversion
    assert pair == eur_usd and s == pytest.approx(1.123 if eur_usd == "EURUSD" else 1 / 1.123)

    # every open day is now a complete close (SPOT is all a past option day needs), so a
    # second run has nothing to do on them
    comp = {r.as_of_date: r for r in close_completeness(conn, "2026-09-17", "2026-09-24").itertuples()}
    assert all(comp[d].complete and comp[d].needed == 3 and comp[d].missing == [] for d in open_days)
    assert comp["2026-09-17"].needed == 0 and comp["2026-09-24"].needed == 0       # not traded yet / expired
    again = backfill.backfill(p, date(2026, 9, 18), date(2026, 9, 23), fetch=_never_called, fwd_fetch=_never_called,
                              fut_fetch=_never_called, log=lambda *_: None)
    assert [r["status"] for r in again] == ["SKIPPED"] * 4


def test_backfill_still_fetches_the_expiry_close_of_an_option_the_ledger_already_realised(tmp_path):
    """The catch-up case: the app did not run on the expiry date, a later pull froze the
    option from an older premium, and only THEN does the backfill reach the expiry date.
    That close must still be fetched (engine/options drops the stale realised row and the
    ledger freezes it afresh at the payoff) -- a realised filter here would strand it."""
    from data.bloomberg import live
    p, conn = _options_only_db(tmp_path, [("o1", "EURSEK092326C-1", "2026-09-18", 35e6, "2026-09-23")])
    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
                 "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, "
                 "note) VALUES ('o1','EURSEK092326C-1','FX_OPTION','EUR','2026-09-23',35e6,0,'PREMIUM',0.01,"
                 "'2026-09-22','QL_OPTIONS_PRICER',0,'t','premium dated 2026-09-22 (last before expiry)')")
    conn.commit()
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")
    closes = {"EURSEK Curncy": 11.05, f"{eur_usd} Curncy": 1.17, f"{usd_sek} Curncy": 9.44}
    results = backfill.backfill(p, date(2026, 9, 23), date(2026, 9, 23),
                                fetch=lambda session, service, tickers, field, day: {t: closes[t] for t in tickers},
                                fwd_fetch=_never_called, fut_fetch=_never_called, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["closes"] == 3
    assert dict(conn.execute("SELECT instrument_id, value FROM marks_official WHERE as_of_date = '2026-09-23' "
                             "AND mark_type = 'SPOT'")) == {"EURSEK": 11.05, eur_usd: 1.17, usd_sek: 9.44}


def test_backfill_option_pair_shared_with_a_forward_gets_no_historical_forward_at_the_option_expiry(tmp_path):
    """SPOT only for options, also where the pair has a forward curve history because a
    forward is open in it: the forward's own leg date is backfilled, the option's expiry
    is not (nothing prices an option on a past date)."""
    p, conn = _options_only_db(tmp_path, [("o3", "EURSEK112526C-3", "2026-08-24", 1e6, "2026-11-25")])
    conn.execute("INSERT INTO instruments VALUES ('EURSEK','FX','EUR','SEK',1,0,'EURSEK Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('e1','XLSX','EURSEK','FX_FWD','e1','2026-08-10',1e6,11.20,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("e1", 1, "FX_NEAR", "EUR", 1e6, "2026-08-10", "2026-09-25", 11.20, 1),
        ("e1", 2, "FX_NEAR", "SEK", -11200000, "2026-08-10", "2026-09-25", 11.20, 1),
    ])
    conn.commit()

    def fwd_fetch(session, service, tickers, fields, start, end):
        assert all(t.startswith("EURSEK") for t in tickers)             # never a curve for a conversion pair
        return {t: {"2026-09-08": {"PX_LAST": 11.25, "SETTLE_DT": "2026-09-25"}} for t in tickers}

    results = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8),
                                fetch=lambda session, service, tickers, field, day: {t: 10.0 for t in tickers},
                                fwd_fetch=fwd_fetch, fut_fetch=_never_called, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    assert [r[0] for r in conn.execute("SELECT settle_date FROM marks WHERE mark_type = 'FWD_OUTRIGHT'")] == ["2026-09-25"]
    from data.bloomberg.inventory import close_completeness
    assert bool(close_completeness(conn, "2026-09-08", "2026-09-08")["complete"].iloc[0]) is True


# =========================================================================== 2026-09-21: no SETTLE_DT in history, points
# Found from the Bloomberg PC ("5d n/a ... no official FUTURE_PX/FWD_OUTRIGHT/SPOT for
# 2026-09-14"): SETTLE_DT is a static reference field that HistoricalDataRequest does not
# serve, so every tenor of every past day was dropped and no past forward was ever written.
# The tenor tickers' PX_LAST is also forward POINTS by the live tenor path's own account,
# which the backfill read as outrights.
def _usdjpy_db(tmp_path, settle="2026-10-01"):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('j1','XLSX','USDJPY','FX_FWD','j1','2026-08-10',1e6,150.0,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-08-10", settle, 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-08-10", settle, 150.0, 1),
    ])
    conn.commit()
    return p, conn


def _usdjpy_spot(session, service, tickers, field, day):
    return {"USDJPY Curncy": 147.0}


def _usdjpy_points(session, service, tickers, fields, start, end):
    """What a terminal is expected to send: PX_LAST only (forward points, negative for
    USDJPY), never a SETTLE_DT, on every weekday of the range."""
    points = {"USDJPYSP Curncy": 0.0, "USDJPY1W Curncy": -12.0, "USDJPY1M Curncy": -50.0, "USDJPY1Y Curncy": -550.0}
    out, d = {}, start
    while d <= end:
        if d.weekday() < 5:
            for ticker, value in points.items():
                out.setdefault(ticker, {})[d.isoformat()] = {"PX_LAST": value}
        d += timedelta(days=1)
    return out


def _scale_100(session, service, tickers, fields):
    # both candidate fields in ONE request (2026-09-21); this terminal answers the first
    assert fields == ["FWD_POINTS_SCALE", "FWD_SCALE"] and tickers == ["USDJPY Curncy"]
    return {"USDJPY Curncy": {"FWD_POINTS_SCALE": 100.0}}


def test_backfill_without_settle_dt_converts_points_and_writes_the_forward_as_bbg_interp(tmp_path):
    p, conn = _usdjpy_db(tmp_path)
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=_scale_100, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    # By hand, as of Mon 2026-09-14: spot date Wed 09-16 (T+2); 1W = 09-23, 1M = Fri 10-16
    # (computed by convention, Bloomberg sent no dates). Outright = spot + points / 100, as
    # pull_marks.outright_from_points: 1W 147 - 0.12 = 146.88, 1M 147 - 0.50 = 146.50.
    # Target 2026-10-01 is 8 of the 23 days from 09-23 to 10-16:
    #   146.88 + (8 / 23) * (146.50 - 146.88) = 146.747826...
    value, source, snapped = conn.execute(
        "SELECT value, source, snapped_at FROM marks WHERE mark_type = 'FWD_OUTRIGHT' AND settle_date = '2026-10-01'").fetchone()
    assert value == pytest.approx(146.88 + (8 / 23) * (146.50 - 146.88), abs=1e-9)
    assert source == "BBG_INTERP" and snapped == "2026-09-14T15:00:00-04:00"
    from data.bloomberg.inventory import close_completeness
    assert bool(close_completeness(conn, "2026-09-14", "2026-09-14")["complete"].iloc[0]) is True


def test_backfill_forward_on_a_computed_tenor_date_is_never_written_as_bloombergs_own_quote(tmp_path):
    """The leg settles exactly on the computed 1M date: the value is that tenor's own
    number, but the DATE is this app's, so the row is BBG_INTERP, not BBG_BFXFORWARD."""
    p, conn = _usdjpy_db(tmp_path, settle="2026-10-16")
    backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                      fut_fetch=_never_called, scale_fetch=_scale_100, log=lambda *_: None)
    assert conn.execute("SELECT value, source FROM marks WHERE mark_type = 'FWD_OUTRIGHT'").fetchall() == [
        (pytest.approx(146.50), "BBG_INTERP")]


def test_backfill_outrights_without_settle_dt_are_used_as_they_come_at_computed_dates(tmp_path):
    """If the tenor tickers turn out to quote outrights (same order of magnitude as spot),
    nothing is converted; the dates are still computed, so the row is still BBG_INTERP."""
    p, conn = _usdjpy_db(tmp_path)

    def outrights(session, service, tickers, fields, start, end):
        return {"USDJPY1W Curncy": {"2026-09-14": {"PX_LAST": 146.88}},
                "USDJPY1M Curncy": {"2026-09-14": {"PX_LAST": 146.50}}}

    backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=outrights,
                      fut_fetch=_never_called, scale_fetch=_never_called, log=lambda *_: None)
    assert conn.execute("SELECT value, source FROM marks WHERE mark_type = 'FWD_OUTRIGHT'").fetchall() == [
        (pytest.approx(146.88 + (8 / 23) * (146.50 - 146.88)), "BBG_INTERP")]


def test_backfill_points_without_a_scale_or_beyond_the_last_tenor_stay_missing_with_a_reason(tmp_path):
    p, conn = _usdjpy_db(tmp_path)
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=lambda *a: {}, log=lambda *_: None)
    assert results[0]["status"] == "DONE"                                       # the SPOT close was written
    assert [m["mark_type"] for m in results[0]["missing_marks"]] == ["FWD_OUTRIGHT"]
    assert "FWD_POINTS_SCALE" in results[0]["missing_marks"][0]["reason"]
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type = 'FWD_OUTRIGHT'").fetchone()[0] == 0

    (tmp_path / "far").mkdir()
    far, conn_far = _usdjpy_db(tmp_path / "far", settle="2028-03-15")
    results = backfill.backfill(far, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=_scale_100, log=lambda *_: None)
    assert "not extrapolated" in results[0]["missing_marks"][0]["reason"]       # 1Y is the last tenor
    assert conn_far.execute("SELECT COUNT(*) FROM marks WHERE mark_type = 'FWD_OUTRIGHT'").fetchone()[0] == 0


def test_backfill_works_the_days_in_the_callers_order_and_one_bad_day_does_not_end_the_run(tmp_path, monkeypatch):
    p, conn = _usdjpy_db(tmp_path)
    real = backfill.fc.historical_curve

    def curve(day, *a, **k):
        if day == date(2026, 9, 15):
            raise RuntimeError("boom")
        return real(day, *a, **k)

    monkeypatch.setattr(backfill.fc, "historical_curve", curve)
    seen = []
    order = [date(2026, 9, 16), date(2026, 9, 14), date(2026, 9, 15)]
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 16), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=_scale_100, order=order,
                                on_day=lambda r: seen.append(r["day"]), log=lambda *_: None)
    assert seen == ["2026-09-16", "2026-09-14", "2026-09-15"]                   # the caller's order
    assert [(r["day"], r["status"]) for r in results] == [                     # returned in date order
        ("2026-09-14", "DONE"), ("2026-09-15", "ERROR"), ("2026-09-16", "DONE")]
    assert "boom" in results[1]["error"]
    # every day's SPOT went in together, before any forward; the bad day's forward alone is absent
    assert [r[0] for r in conn.execute("SELECT as_of_date FROM marks WHERE mark_type = 'SPOT' ORDER BY 1")] == [
        "2026-09-14", "2026-09-15", "2026-09-16"]
    assert [r[0] for r in conn.execute("SELECT as_of_date FROM marks WHERE mark_type = 'FWD_OUTRIGHT' ORDER BY rowid")] == [
        "2026-09-16", "2026-09-14"]


# --------------------------------------------------------------------------- only what is needed (2026-09-21)
# User: "only the data necessary for the pnl calcs of the trades being done is being pulled
# also for the backfill". Days that are not being worked, pairs a day does not need and
# tenors beyond the legs' reach are not asked of Bloomberg's history.
_TENOR_OUTRIGHT = {"SP": 0.6600, "1W": 0.6601, "2W": 0.6602, "1M": 0.6604, "2M": 0.6608, "3M": 0.6612,
                   "6M": 0.6624, "1Y": 0.6648}


def _needed_only_db(tmp_path, name="risk.db"):
    """AUDUSD sold 08-10 for 09-30 (three weeks from the days worked); USDJPY bought 09-01
    for 2027-02-15 (five months); a June AUDUSD trade long settled."""
    p = tmp_path / name
    conn = schema.connect(p)
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
    ])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("old", "XLSX", "AUDUSD", "FX_FWD", "old", "2026-06-01", 1e6, 0.64, "acc", "cp", "", "t", "d", ""),
        ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "", "t", "d", ""),
        ("j1", "XLSX", "USDJPY", "FX_FWD", "j1", "2026-09-01", 1e6, 150.0, "acc", "cp", "", "t", "d", ""),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("old", 1, "FX_NEAR", "AUD", 1e6, "2026-06-01", "2026-06-10", 0.64, 1),
        ("old", 2, "FX_NEAR", "USD", -640000, "2026-06-01", "2026-06-10", 0.64, 1),
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-30", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-30", 0.65, 1),
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-09-01", "2027-02-15", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-09-01", "2027-02-15", 150.0, 1),
    ])
    conn.commit()
    return p, conn


def _recording_fetchers():
    asked = {"spot": [], "fwd": []}

    def spot_fetch(session, service, tickers, field, day):
        asked["spot"].append((day, sorted(tickers)))
        return {"AUDUSD Curncy": 0.66, "USDJPY Curncy": 150.0}

    def fwd_fetch(session, service, tickers, fields, start, end):
        asked["fwd"].append((start, end, sorted(tickers)))
        out = {}
        for d in backfill.business_days(start, end):
            for ticker in tickers:
                pair, tenor = ticker[:6], ticker[6:].split(" ")[0]
                scale = 1.0 if pair == "AUDUSD" else 150.0 / 0.66
                out.setdefault(ticker, {})[d.isoformat()] = {"PX_LAST": _TENOR_OUTRIGHT[tenor] * scale}
        return out
    return asked, spot_fetch, fwd_fetch


def test_runs_are_stretches_of_consecutive_business_days_of_at_most_a_month():
    days = [date(2026, 6, 8), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21)]
    assert backfill._runs(list(reversed(days))) == [(date(2026, 6, 8), date(2026, 6, 8)),
                                                    (date(2026, 9, 17), date(2026, 9, 21))]   # over the weekend
    long = backfill.business_days(date(2026, 6, 1), date(2026, 8, 31))
    runs = backfill._runs(long)
    assert all(len(backfill.business_days(a, b)) <= backfill.RUN_MAX_DAYS for a, b in runs)
    assert [d for a, b in runs for d in backfill.business_days(a, b)] == long


def test_backfill_asks_only_for_the_days_pairs_and_tenors_the_book_needed(tmp_path):
    p, conn = _needed_only_db(tmp_path)
    asked, spot_fetch, fwd_fetch = _recording_fetchers()
    # one old day and two recent ones: it used to request every ticker from 06-08 to 09-09
    order = [date(2026, 9, 9), date(2026, 9, 8), date(2026, 6, 8)]
    results = backfill.backfill(p, date(2026, 6, 8), date(2026, 9, 9), fetch=spot_fetch, fwd_fetch=fwd_fetch,
                                fut_fetch=fwd_fetch, order=order, log=lambda s: None)
    assert [r["day"] for r in results if r["status"] == "DONE"] == ["2026-06-08", "2026-09-08", "2026-09-09"]
    # days: two requests, one per stretch worked, never the three months between them
    assert sorted((start, end) for start, end, _ in asked["fwd"]) == [
        (date(2026, 6, 8), date(2026, 6, 8)), (date(2026, 9, 8), date(2026, 9, 9))]
    by_start = {start: tickers for start, _end, tickers in asked["fwd"]}
    # pairs: in June only the AUDUSD trade of that month was open (USDJPY is not asked for)
    assert all(t.startswith("AUDUSD") for t in by_start[date(2026, 6, 8)])
    assert dict(asked["spot"])[date(2026, 6, 8)] == ["AUDUSD Curncy"]
    assert dict(asked["spot"])[date(2026, 9, 8)] == ["AUDUSD Curncy", "USDJPY Curncy"]
    # tenors: the three-week AUDUSD leg needs SP..1M; the five-month USDJPY leg needs up to
    # 6M; nobody needs 1Y, and AUDUSD needs nothing beyond 1M
    september = by_start[date(2026, 9, 8)]
    assert [t for t in september if t.startswith("AUDUSD")] == sorted(f"AUDUSD{t} Curncy" for t in ("SP", "1W", "2W", "1M"))
    assert "USDJPY6M Curncy" in september and "USDJPY1Y Curncy" not in september
    assert not any(t.endswith("1Y Curncy") for _s, _e, tickers in asked["fwd"] for t in tickers)


def test_trimmed_tenors_give_the_same_forwards_as_all_eight(tmp_path, monkeypatch):
    """A leg's forward is read between the two tenors either side of its date, so leaving
    the longer ones out changes no mark."""
    from data.bloomberg.pull_marks import STANDARD_TENORS

    def run(name, all_tenors):
        p, conn = _needed_only_db(tmp_path, name)
        _asked, spot_fetch, fwd_fetch = _recording_fetchers()
        with monkeypatch.context() as m:
            if all_tenors:
                m.setattr(backfill, "_tenors_needed", lambda pair, legs, run_start, holidays: list(STANDARD_TENORS))
            backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 9), fetch=spot_fetch, fwd_fetch=fwd_fetch,
                              fut_fetch=fwd_fetch, log=lambda s: None)
        return conn.execute("SELECT as_of_date, instrument_id, settle_date, mark_type, value, source FROM marks "
                            "ORDER BY 1, 2, 3, 4").fetchall()

    trimmed, full = run("trimmed.db", False), run("full.db", True)
    assert trimmed == full and any(m[3] == "FWD_OUTRIGHT" and m[5] == "BBG_INTERP" for m in trimmed)


# =========================================================================== 2026-09-21: the points divisor
# Bloomberg PC paste: "5d needs the 2026-09-14 close ... could not fill 45 marks: Bloomberg
# returned forward points for AUDUSD but no FWD_POINTS_SCALE". That field name was never
# verified; FWD_SCALE (decimal places the points are shifted) is asked for in the same request.
def _scale_exponent_2(session, service, tickers, fields):
    """The terminal as found: nothing for FWD_POINTS_SCALE, FWD_SCALE = 2 for USDJPY."""
    assert fields == ["FWD_POINTS_SCALE", "FWD_SCALE"] and tickers == ["USDJPY Curncy"]
    return {"USDJPY Curncy": {"FWD_SCALE": 2}}


def test_backfill_reads_the_points_divisor_from_fwd_scale_when_fwd_points_scale_does_not_answer(tmp_path):
    p, conn = _usdjpy_db(tmp_path)
    log = []
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=_scale_exponent_2, log=log.append)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    # 10 ** 2 = the divisor 100 of the FWD_POINTS_SCALE test above: the same forward, to the digit
    assert conn.execute("SELECT value, source FROM marks WHERE mark_type = 'FWD_OUTRIGHT'").fetchall() == [
        (pytest.approx(146.88 + (8 / 23) * (146.50 - 146.88), abs=1e-9), "BBG_INTERP")]
    # the run says which field answered, in its log and for the status file
    assert any("FWD_SCALE = 2" in line and "divisor 100" in line for line in log)
    report = backfill._scale_reports[backfill._db_key(p)]["USDJPY"]
    assert report["field"] == "FWD_SCALE" and report["divisor"] == 100.0 and report["raw"] == {"FWD_SCALE": 2}


def test_backfill_with_neither_scale_field_names_both_in_the_reason(tmp_path):
    p, conn = _usdjpy_db(tmp_path)
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=lambda *a: {"USDJPY Curncy": {"FWD_SCALE": 2.5}},
                                log=lambda *_: None)
    reason = results[0]["missing_marks"][0]["reason"]
    assert "neither FWD_POINTS_SCALE nor FWD_SCALE" in reason and "FWD_SCALE: 2.5, not usable" in reason
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type = 'FWD_OUTRIGHT'").fetchone()[0] == 0
    assert backfill._plain_reasons(results[0], [])[0] == reason                  # what the header is shown


def test_backfill_never_writes_a_forward_that_a_wrong_divisor_throws_more_than_20_percent_off_spot(tmp_path):
    """FWD_SCALE = 0 would mean a divisor of 1: the 1M pillar becomes 147 - 50 = 97, a third
    below spot. Not written, and the reason says why."""
    p, conn = _usdjpy_db(tmp_path, settle="2026-10-16")                          # the computed 1M date
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=lambda *a: {"USDJPY Curncy": {"FWD_SCALE": 0}},
                                log=lambda *_: None)
    assert results[0]["status"] == "DONE" and results[0]["fwd_outrights"] == 0
    reason = results[0]["missing_marks"][0]["reason"]
    assert "more than 20% away from that day's spot 147" in reason and "FWD_SCALE" in reason
    assert "nothing was written" in reason
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type = 'FWD_OUTRIGHT'").fetchone()[0] == 0


# =========================================================================== 2026-09-21: the 15:00 New York close
# User: "the EOD is 3pm New York time"; "Marks: for previous or any closes in FX, we need to
# use NY 3pm". Past FX closes come from intraday bars (pull_marks.fetch_intraday_close_series,
# tested with a fake blpapi in tests/test_bloomberg.py); futures keep the daily PX_SETTLE.
def test_backfill_by_default_reads_fx_closes_from_the_1500_series_and_futures_from_the_daily_one(tmp_path, monkeypatch):
    from data.bloomberg import pull_marks as pm
    p, conn = _db_with_future(tmp_path)
    asked = {"intraday": [], "daily": []}
    closes = {"AUDUSD Curncy": 0.6505, "AUDUSDSP Curncy": 0.6500, "AUDUSD1W Curncy": 0.6570}

    def intraday(session, service, tickers, fields, start, end, **kw):
        asked["intraday"].append((tuple(sorted(tickers)), tuple(fields), start, end))
        return {t: {d.isoformat(): {"PX_LAST": closes.get(t, 0.66)} for d in backfill.business_days(start, end)}
                for t in tickers}

    def daily(session, service, tickers, fields, start, end, **kw):
        asked["daily"].append((tuple(sorted(tickers)), tuple(fields)))
        return {"ESU6 Index": {d.isoformat(): {"PX_SETTLE": 7550.0} for d in backfill.business_days(start, end)}}

    monkeypatch.setattr(pm, "fetch_intraday_close_series", intraday)
    monkeypatch.setattr(pm, "fetch_historical_series", daily)
    day = date(2026, 9, 7)
    results = backfill.backfill(p, day, day, session_factory=lambda: (object(), object()), today=date(2026, 9, 21),
                                log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    # the SPOT close and the tenor series: ONE intraday call each for the stretch, the price
    # alone (no SETTLE_DT, no second round)
    tenors = tuple(sorted(f"AUDUSD{t} Curncy" for t in ("SP", "1W", "2W", "1M")))
    assert sorted(asked["intraday"]) == sorted([(("AUDUSD Curncy",), ("PX_LAST",), day, day),
                                                (tenors, ("PX_LAST",), day, day)])
    # the daily history is asked for the future's PX_SETTLE and for nothing FX
    assert asked["daily"] == [(("ESU6 Index",), ("PX_SETTLE",))]
    stamps = {r[0] for r in conn.execute("SELECT snapped_at FROM marks")}
    assert stamps == {"2026-09-07T15:00:00-04:00"}
    assert conn.execute("SELECT value FROM marks WHERE mark_type = 'SPOT'").fetchone()[0] == 0.6505


def test_a_past_fx_row_that_is_not_the_1500_close_is_replaced_and_one_that_is_never_is(tmp_path):
    from data.bloomberg.inventory import close_completeness
    p, conn = _db_with_future(tmp_path)
    fri, mon = "2026-09-04", "2026-09-07"
    fri_close = backfill.close_stamp(date(2026, 9, 4))
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        # Monday as its last live pull left it: a spot, a direct-quote forward and a live
        # futures price (a future keeps what it has)
        (mon, "AUDUSD", mon, "SPOT", 0.9001, "BBG_BFXFORWARD", "2026-09-07T11:40:12-04:00"),
        (mon, "AUDUSD", "2026-09-08", "FWD_OUTRIGHT", 0.9002, "BBG_BFXFORWARD", "2026-09-07T11:40:12-04:00"),
        (mon, "ESU6 Index", "2026-12-19", "FUTURE_PX", 7111.0, "BBG_BDH", "2026-09-07T11:40:12-04:00"),
        # Friday already holds its 15:00 closes; only its future is missing
        (fri, "AUDUSD", fri, "SPOT", 0.8001, "BBG_BFXFORWARD", fri_close),
        (fri, "AUDUSD", "2026-09-08", "FWD_OUTRIGHT", 0.8002, "BBG_INTERP", fri_close),
    ])
    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
                 "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, "
                 "note) VALUES ('a1','AUDUSD','FX_FWD','AUD','2026-09-08',-1e6,650000,'SPOT',0.9001,'2026-09-07',"
                 "'BBG_BFXFORWARD',-250100,'t','')")
    conn.commit()
    frozen = conn.execute("SELECT * FROM realised_pnl").fetchall()

    # a past day's row that is not stamped at the close is not a close ...
    before = {r.as_of_date: r for r in close_completeness(conn, fri, mon, today="2026-09-21").itertuples()}
    assert before[mon].complete is False and before[mon].present == 1 and before[mon].not_closed == 2
    assert {m["mark_type"] for m in before[mon].missing} == {"SPOT", "FWD_OUTRIGHT"}
    assert before[fri].present == 2 and before[fri].not_closed == 0               # its closes count; the future is missing
    # ... but today's rows are live and count as they are
    assert bool(close_completeness(conn, mon, mon, today=mon)["complete"].iloc[0]) is True

    results = backfill.backfill(p, date(2026, 9, 4), date(2026, 9, 7), fwd_fetch=fwd_fetch_2,
                                fetch=lambda session, service, tickers, field, day: {"AUDUSD Curncy": 0.6505},
                                fut_fetch=lambda session, service, tickers, fields, start, end: {
                                    "ESU6 Index": {d.isoformat(): {"PX_SETTLE": 7550.0}
                                                   for d in backfill.business_days(start, end)}},
                                today=date(2026, 9, 21), log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE", "DONE"]

    rows = {(r[0], r[1], r[2]): r[3:] for r in conn.execute(
        "SELECT as_of_date, mark_type, source, value, snapped_at FROM marks")}
    mon_close = "2026-09-07T15:00:00-04:00"
    # Monday: the live spot is replaced by the close; the stale direct-quote forward is gone,
    # so the interpolated 15:00 forward is what marks_official serves (a direct row would win)
    assert rows[(mon, "SPOT", "BBG_BFXFORWARD")] == (0.6505, mon_close)
    assert (mon, "FWD_OUTRIGHT", "BBG_BFXFORWARD") not in rows
    assert rows[(mon, "FWD_OUTRIGHT", "BBG_INTERP")] == (pytest.approx(0.6510), mon_close)
    assert conn.execute("SELECT value, source FROM marks_official WHERE as_of_date = ? AND mark_type = 'FWD_OUTRIGHT'",
                        (mon,)).fetchall() == [(pytest.approx(0.6510), "BBG_INTERP")]
    assert rows[(mon, "FUTURE_PX", "BBG_BDH")][0] == 7111.0                        # futures keep PX_SETTLE / what they have
    # Friday: rows already stamped at the close are never rewritten; the missing future is written
    assert rows[(fri, "SPOT", "BBG_BFXFORWARD")] == (0.8001, fri_close)
    assert rows[(fri, "FWD_OUTRIGHT", "BBG_INTERP")] == (0.8002, fri_close)
    assert rows[(fri, "FUTURE_PX", "BBG_BDH")][0] == 7550.0
    # frozen rows stay frozen
    assert conn.execute("SELECT * FROM realised_pnl").fetchall() == frozen

    after = close_completeness(conn, fri, mon, today="2026-09-21")
    assert list(after["complete"]) == [True, True] and list(after["not_closed"]) == [0, 0]
    again = backfill.backfill(p, date(2026, 9, 4), date(2026, 9, 7), fetch=_never_called, fwd_fetch=_never_called,
                              fut_fetch=_never_called, today=date(2026, 9, 21), log=lambda *_: None)
    assert [r["status"] for r in again] == ["SKIPPED", "SKIPPED"]


def test_todays_rows_are_never_replaced_by_the_close_rule(tmp_path):
    """Intraday = live: a row dated today keeps its own stamp, whatever the backfill holds."""
    p, conn = _db_with_future(tmp_path)
    today = "2026-09-07"
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (today, "AUDUSD", today, "SPOT", 0.7001, "BBG_BFXFORWARD", "2026-09-07T11:40:12-04:00"))
    conn.commit()
    row = {"as_of_date": today, "instrument_id": "AUDUSD", "settle_date": today, "mark_type": "SPOT", "value": 0.5,
           "source": "BBG_BFXFORWARD", "snapped_at": backfill.close_stamp(date(2026, 9, 7))}
    assert backfill._write_closes(conn, [row], today=today) == 0
    assert conn.execute("SELECT value FROM marks WHERE mark_type = 'SPOT'").fetchall() == [(0.7001,)]
    # the same row a day later is a past day's live row: replaced
    assert backfill._write_closes(conn, [row], today="2026-09-08") == 1
    assert conn.execute("SELECT value FROM marks WHERE mark_type = 'SPOT'").fetchall() == [(0.5,)]


def test_a_day_older_than_bloombergs_intraday_history_takes_the_daily_close(tmp_path, monkeypatch):
    """User decision 2026-09-21: an old day is "not that serious" -- where Bloomberg no longer
    holds a 15:00 price the day closes at Bloomberg's daily close, stamped 17:00."""
    from data.bloomberg import pull_marks as pm
    from data.bloomberg.inventory import close_completeness
    p, conn = _db_with_future(tmp_path)
    asked = []

    def intraday(session, service, tickers, fields, start, end, **kw):
        asked.append(("intraday", tuple(sorted(tickers))))
        return {}

    def daily(session, service, tickers, fields, start, end, **kw):
        asked.append(("daily", tuple(sorted(tickers)), tuple(fields)))
        out = {t: {d.isoformat(): {"PX_LAST": 0.6505} for d in backfill.business_days(start, end)}
               for t in tickers if t == "AUDUSD Curncy"}
        if "ESU6 Index" in tickers:
            out["ESU6 Index"] = {d.isoformat(): {"PX_SETTLE": 7550.0} for d in backfill.business_days(start, end)}
        return out

    monkeypatch.setattr(pm, "fetch_intraday_close_series", intraday)
    monkeypatch.setattr(pm, "fetch_historical_series", daily)
    day, today = date(2026, 9, 7), date(2027, 6, 1)
    assert backfill.intraday_floor(date(2026, 9, 21)) == date(2026, 3, 9)         # 140 business days = 28 weeks
    assert backfill.intraday_floor(today) > day
    log = []
    results = backfill.backfill(p, day, day, session_factory=lambda: (object(), object()), today=today, log=log.append)
    r = results[0]
    # no intraday request for a day Bloomberg no longer holds: the daily series answers instead
    assert not [a for a in asked if a[0] == "intraday"]
    assert ("daily", ("AUDUSD Curncy",), ("PX_LAST",)) in asked and ("daily", ("ESU6 Index",), ("PX_SETTLE",)) in asked
    assert r["status"] == "DONE" and (r["closes"], r["future_px"]) == (1, 1) and r["missing_pairs"] == []
    assert conn.execute("SELECT value, snapped_at FROM marks WHERE mark_type = 'SPOT'").fetchall() == [
        (0.6505, "2026-09-07T17:00:00-04:00")]
    assert any("take Bloomberg's daily close (17:00 New York)" in line for line in log)
    # that 17:00 row IS the day's close: never asked for again
    done = close_completeness(conn, "2026-09-07", "2026-09-07", today=today.isoformat())
    assert [m["mark_type"] for m in done["missing"].iloc[0]] == ["FWD_OUTRIGHT"] and done["not_closed"].iloc[0] == 0


def test_the_1500_close_applies_from_2026_09_21_and_older_days_keep_what_they_have(monkeypatch):
    """User decision 2026-09-21: "from now on its 3pm new york but surely for like a month ago
    its not that serious"."""
    from data.bloomberg import pull_marks as pm
    monkeypatch.setattr(backfill, "CLOSE_1500_FROM", date(2026, 9, 21))      # the real cutover, not the fixture's
    assert backfill.first_1500_day(date(2026, 9, 23)) == date(2026, 9, 21)
    # before the cutover: whatever is on file is that day's close (a live pull's row, an old 17:00 row)
    assert backfill.is_close_row("SPOT", "2026-09-18", "2026-09-18T11:40:12-04:00") is True
    assert backfill.is_close_row("FWD_OUTRIGHT", "2026-09-14", "2026-09-14T17:00:00-04:00") is True
    assert backfill.close_stamp(date(2026, 9, 18), today=date(2026, 9, 23)) == "2026-09-18T17:00:00-04:00"
    # from the cutover on: only the 15:00 row (or the daily close of a day beyond the intraday window)
    assert backfill.is_close_row("SPOT", "2026-09-21", "2026-09-21T11:40:12-04:00") is False
    assert backfill.is_close_row("SPOT", "2026-09-21", "2026-09-21T15:00:00-04:00") is True
    assert backfill.close_stamp(date(2026, 9, 22), today=date(2026, 9, 23)) == "2026-09-22T15:00:00-04:00"
    assert backfill.is_close_row("FUTURE_PX", "2026-09-21", "2026-09-21T11:40:12-04:00") is True
    # a stretch across the cutover is asked for in its two parts: daily before, 15:00 from it on
    asked = []
    monkeypatch.setattr(pm, "fetch_historical_series",
                        lambda s, v, tickers, fields, start, end, **kw: asked.append(("daily", start, end)) or {
                            "EURUSD Curncy": {"2026-09-18": {"PX_LAST": 1.10}}})
    monkeypatch.setattr(pm, "fetch_intraday_close_series",
                        lambda s, v, tickers, fields, start, end, **kw: asked.append(("15:00", start, end)) or {
                            "EURUSD Curncy": {"2026-09-21": {"PX_LAST": 1.11}}})
    series = backfill._close_series_fetch(date(2026, 9, 21))(None, None, ["EURUSD Curncy"], ["PX_LAST"],
                                                             date(2026, 9, 18), date(2026, 9, 22))
    assert asked == [("daily", date(2026, 9, 18), date(2026, 9, 20)), ("15:00", date(2026, 9, 21), date(2026, 9, 22))]
    assert series == {"EURUSD Curncy": {"2026-09-18": {"PX_LAST": 1.10}, "2026-09-21": {"PX_LAST": 1.11}}}


def test_a_missing_close_is_reported_in_the_sources_own_words(tmp_path, monkeypatch):
    """One side only, or no bar at the close: the mark is missing, with that reason."""
    from data.bloomberg import pull_marks as pm
    p, conn = _usdjpy_db(tmp_path)
    why = "only the BID side of USDJPY Curncy came back for the hour ending 15:00 New York on 2026-09-14 (no ASK), and a mid needs both"

    def intraday(session, service, tickers, fields, start, end, **kw):
        return {t: {"2026-09-14": {pm.CLOSE_REASON: why.replace("USDJPY Curncy", t)}} for t in tickers}

    monkeypatch.setattr(pm, "fetch_intraday_close_series", intraday)
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fut_fetch=_never_called,
                                session_factory=lambda: (object(), object()), today=date(2026, 9, 21),
                                log=lambda *_: None)
    assert results[0]["status"] == "NO_CLOSES" and results[0]["missing_pair_reasons"] == {"USDJPY": why}
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


def test_points_divisor_is_worked_out_from_bloombergs_own_forwards_when_neither_field_answers(tmp_path):
    """The user's terminal (2026-09-21): past forwards come as points and no scale field answers,
    so 5d / MTD stayed n/a. The live pull's own outrights are on file though (FWD_CURVE, as of
    Fri 2026-09-18: spot date 09-22, 1M = 10-22, 1Y = 2027-09-22): points / (outright - spot) is
    -50 / -0.49 = 102 and -550 / -5.4 = 101.9, i.e. a divisor of 100, from Bloomberg's numbers alone."""
    p, conn = _usdjpy_db(tmp_path)
    stamp = "2026-09-18T11:40:12-04:00"
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-18", "USDJPY", "2026-09-18", "SPOT", 147.2, "BBG_BFXFORWARD", stamp),
        ("2026-09-18", "USDJPY", "2026-10-22", "FWD_OUTRIGHT", 146.71, "BBG_BFXFORWARD", stamp),
        ("2026-09-18", "USDJPY", "2027-09-22", "FWD_OUTRIGHT", 141.8, "BBG_BFXFORWARD", stamp),
    ])
    conn.commit()
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fetch=_usdjpy_spot, fwd_fetch=_usdjpy_points,
                                fut_fetch=_never_called, scale_fetch=lambda *a: {}, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    value, source = conn.execute("SELECT value, source FROM marks WHERE mark_type = 'FWD_OUTRIGHT' "
                                 "AND as_of_date = '2026-09-14'").fetchone()
    assert value == pytest.approx(146.88 + (8 / 23) * (146.50 - 146.88), abs=1e-9) and source == "BBG_INTERP"
    report = backfill._scale_reports[backfill._db_key(p)]["USDJPY"]
    assert report["divisor"] == 100.0 and report["field"] == backfill.INFERRED_SCALE_FIELD


def test_points_divisor_is_not_guessed_when_the_tenors_disagree_or_nothing_is_on_file(tmp_path):
    p, conn = _usdjpy_db(tmp_path)
    rows = {"2026-09-14": {"1M": {"PX_LAST": -50.0}, "1Y": {"PX_LAST": -550.0}}}
    assert backfill._infer_points_scale(conn, "USDJPY", rows) is None                 # no forwards on file
    stamp = "2026-09-18T11:40:12-04:00"
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-18", "USDJPY", "2026-09-18", "SPOT", 147.2, "BBG_BFXFORWARD", stamp),
        ("2026-09-18", "USDJPY", "2026-10-22", "FWD_OUTRIGHT", 146.71, "BBG_BFXFORWARD", stamp),   # 1M says 100
        ("2026-09-18", "USDJPY", "2027-09-22", "FWD_OUTRIGHT", 146.65, "BBG_BFXFORWARD", stamp),   # 1Y says 1,000
    ])
    conn.commit()
    assert backfill._infer_points_scale(conn, "USDJPY", rows) is None                 # the votes disagree


# =========================================================================== 2026-09-22: NDF_FIX
def test_backfill_writes_the_ndf_fixing_on_the_fixing_date_from_its_own_ticker(tmp_path):
    """A past fixing date's official fixing (PX_LAST of the currency's fixing ticker, one
    history request per stretch) lands as NDF_FIX on the pair, source BBG_BDH, and the day
    is not complete without it; a fixing Bloomberg has no value for is in missing_marks."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('USDBRL','FX','USD','BRL',1,1,'USDBRL Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('b1','XLSX','USDBRL','FX_FWD','b1','2026-09-01',1e6,5.2,'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("b1", 1, "FX_NEAR", "USD", 1e6, "2026-09-01", "2026-09-10", 5.2, 0),      # Thu 09-10 fixes Tue 09-08
        ("b1", 2, "FX_NEAR", "BRL", -5.2e6, "2026-09-01", "2026-09-10", 5.2, 0)])
    conn.commit()
    asked = []

    def history(session, service, tickers, fields, start, end):
        asked.append((sorted(tickers), list(fields), start, end))
        if tickers == ["BZFXPTAX Index"]:
            return {"BZFXPTAX Index": {"2026-09-08": {"PX_LAST": 5.3399}}}
        return {}

    def spot(session, service, tickers, field, day):
        return {"USDBRL Curncy": 5.30} if day <= date(2026, 9, 8) else {}

    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=spot, fwd_fetch=history,
                                fut_fetch=history, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert any(t == ["BZFXPTAX Index"] and f == ["PX_LAST"] for t, f, _, _ in asked)     # one request, the fixing ticker
    assert conn.execute("SELECT as_of_date, settle_date, value, source FROM marks WHERE mark_type='NDF_FIX'").fetchall() == \
        [("2026-09-08", "2026-09-08", 5.3399, "BBG_BDH")]
    assert conn.execute("SELECT value FROM marks_official WHERE mark_type='NDF_FIX'").fetchone() == (5.3399,)
    assert results[0]["future_px"] == 0 and results[1]["future_px"] == 1                # counted with the day's single-value marks
    from data.bloomberg.inventory import close_completeness
    comp = {r.as_of_date: r for r in close_completeness(conn, "2026-09-07", "2026-09-08").itertuples()}
    assert "NDF_FIX" not in {m["mark_type"] for m in comp["2026-09-08"].missing}
    assert "NDF_FIX" not in {m["mark_type"] for m in comp["2026-09-07"].missing}      # the day before: not needed
    # no fixing on file for the day: the day is incomplete for it, the backfill names it, nothing written
    conn.execute("DELETE FROM marks WHERE mark_type='NDF_FIX'")
    conn.commit()
    comp = {r.as_of_date: r for r in close_completeness(conn, "2026-09-08", "2026-09-08").itertuples()}
    assert {"instrument_id": "USDBRL", "settle_date": "2026-09-08", "mark_type": "NDF_FIX"} in comp["2026-09-08"].missing
    results = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=spot, fwd_fetch=lambda *a, **k: {},
                                fut_fetch=lambda *a, **k: {}, log=lambda *_: None)
    assert ("NDF_FIX", "Bloomberg returned no fixing (PX_LAST) for USDBRL on 2026-09-08") in \
        [(m["mark_type"], m["reason"]) for m in results[0]["missing_marks"]]
