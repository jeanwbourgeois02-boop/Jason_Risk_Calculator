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
    """The 15:00 New York close applies to every past day inside Bloomberg's intraday history
    counted from today (2026-09-22; the 2026-09-21 cut-over is gone). These tests work days
    around 2026-09-07, so 'today' is pinned to keep them within reach (a test that needs
    another today passes it or patches live.book_today itself)."""
    from data.bloomberg import live as _live
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
    assert set(tickers) == {"ESU6 Index"} and fields == ["PX_LAST"]
    out = {}
    d = start
    while d <= end:
        out.setdefault("ESU6 Index", {})[d.isoformat()] = {"PX_LAST": _FUTURE_PX_2[d]}
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
        assert set(tickers) == {"ESU6 Index"} and fields == ["PX_LAST"]
        out = {}
        d = start
        while d <= end:
            if d in future_closes:
                out.setdefault("ESU6 Index", {})[d.isoformat()] = {"PX_LAST": future_closes[d]}
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
# expired option stays unrealisable. No historical forward at the option's expiry; since
# 2026-09-22 the day's vol smile and OIS curves ARE asked of the daily history (`quote_history`
# below), so the day's options price from their own inputs.
QUOTE_ASKED = []      # (tickers, fields, start, end) of every vol / OIS history request (quote_history)


def quote_history(session, service, tickers, fields, start, end):
    """Bloomberg's daily history of the vol and OIS tickers: PX_LAST on every business day
    of the stretch, vol tickers in vol points, OIS tickers in per cent (as Bloomberg quotes)."""
    QUOTE_ASKED.append((sorted(tickers), list(fields), start, end))
    return {t: {d.isoformat(): {"PX_LAST": 7.5 if "BGN" in t else 3.9} for d in backfill.business_days(start, end)}
            for t in tickers}


def _vol_tickers(pair):
    from data.bloomberg import vol_marketdata as vm
    return sorted(vm.vol_ticker(pair, tenor, qt) for tenor in vm.VOL_TENORS for qt in vm.VOL_QUOTE_TYPES)


def _ois_tickers(ccy):
    from data.bloomberg import rates_marketdata as rm
    return sorted(spec.ticker for spec in rm.ois_curve(ccy))


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

    QUOTE_ASKED.clear()
    results = backfill.backfill(p, date(2026, 9, 17), date(2026, 9, 24), fetch=spot_fetch, fwd_fetch=_never_called,
                                fut_fetch=_never_called, quote_fetch=quote_history, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] * 6
    assert all(r["fwd_outrights"] == 0 and r["missing_marks"] == [] and r["missing_pairs"] == [] for r in results)

    open_days = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"]          # trade date .. EXPIRY DATE inclusive
    # the smile of the option's pair and the OIS curve of EUR (SEK has none in scope), from the
    # daily history: ONE request per kind for the stretch, the live steps' own tickers, PX_LAST
    assert QUOTE_ASKED == [(_vol_tickers("EURSEK"), ["PX_LAST"], date(2026, 9, 17), date(2026, 9, 24)),
                           (_ois_tickers("EUR"), ["PX_LAST"], date(2026, 9, 17), date(2026, 9, 24))]
    assert len(_vol_tickers("EURSEK")) == 45
    vols = conn.execute("SELECT as_of_date, pair, COUNT(*), MIN(source), MAX(source), MIN(value), MIN(snapped_at) "
                        "FROM vol_quotes GROUP BY 1, 2 ORDER BY 1").fetchall()
    assert vols == [(d, "EURSEK", 45, "BBG_BDH", "BBG_BDH", 7.5, f"{d}T15:00:00-04:00") for d in open_days]
    curves = conn.execute('SELECT as_of_date, ccy, "index", COUNT(*), MIN(source), MIN(value), MAX(value), quote_type '
                          "FROM curve_quotes GROUP BY 1, 2 ORDER BY 1").fetchall()
    assert curves == [(d, "EUR", "ESTR", len(_ois_tickers("EUR")), "BBG_BDH", 0.039, 0.039, "OIS") for d in open_days]
    by_day = {r["day"]: r for r in results}
    for d in open_days:
        assert (by_day[d]["vol_quotes"], by_day[d]["curve_quotes"], by_day[d]["missing_inputs"]) == (45, 12, [])
        assert by_day[d]["rates_priced"] is None and by_day[d]["rates_failed"] == [] and by_day[d]["rates_note"] == ""
    assert (by_day["2026-09-17"]["vol_quotes"], by_day["2026-09-24"]["curve_quotes"]) == (0, 0)   # not open: nothing asked
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

    # every open day is now a complete close (its SPOT marks) holding its inputs (the smile,
    # the EUR curve), so a second run has nothing to do on them and asks the history nothing
    comp = {r.as_of_date: r for r in close_completeness(conn, "2026-09-17", "2026-09-24").itertuples()}
    assert all(comp[d].complete and comp[d].needed == 3 and comp[d].missing == [] for d in open_days)
    assert all(comp[d].inputs_missing == [] for d in comp)
    assert comp["2026-09-17"].needed == 0 and comp["2026-09-24"].needed == 0       # not traded yet / expired
    again = backfill.backfill(p, date(2026, 9, 18), date(2026, 9, 23), fetch=_never_called, fwd_fetch=_never_called,
                              fut_fetch=_never_called, quote_fetch=_never_called, log=lambda *_: None)
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
                                fwd_fetch=_never_called, fut_fetch=_never_called, quote_fetch=lambda *a, **k: {},
                                log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["closes"] == 3
    assert dict(conn.execute("SELECT instrument_id, value FROM marks_official WHERE as_of_date = '2026-09-23' "
                             "AND mark_type = 'SPOT'")) == {"EURSEK": 11.05, eur_usd: 1.17, usd_sek: 9.44}
    # the history had nothing for the smile or the curve: named, nothing written, the day still DONE
    assert [(m["kind"], m["key"]) for m in results[0]["missing_inputs"]] == [("VOL_SMILE", "EURSEK"), ("OIS_CURVE", "EUR")]
    assert results[0]["missing_inputs"][0]["reason"] == "Bloomberg returned no vol quotes (PX_LAST) for EURSEK on 2026-09-23"
    assert results[0]["missing_inputs"][1]["reason"].startswith("fewer than 4 OIS quotes for EUR on 2026-09-23: Bloomberg "
                                                                "returned no value for EESWE1Z Curncy, ")
    for table in ("vol_quotes", "curve_quotes"):           # a table only exists once something was written to it
        if conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (table,)).fetchone():
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)


def test_backfill_option_pair_shared_with_a_forward_gets_no_historical_forward_at_the_option_expiry(tmp_path):
    """No forward at an option's expiry, also where the pair has a forward curve history
    because a forward is open in it: the forward's own leg date is backfilled, the option's
    expiry is not (a past day's option is priced off the day's curve, which the pricer reads
    itself). The smile and the curve the option needs are a day's inputs, not marks: their
    absence is `inputs_missing`, never a missing mark, and the day's marks still count complete."""
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
                                fwd_fetch=fwd_fetch, fut_fetch=_never_called, quote_fetch=lambda *a, **k: {},
                                log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    assert [r[0] for r in conn.execute("SELECT settle_date FROM marks WHERE mark_type = 'FWD_OUTRIGHT'")] == ["2026-09-25"]
    from data.bloomberg.inventory import close_completeness
    row = close_completeness(conn, "2026-09-08", "2026-09-08").iloc[0]
    assert bool(row["complete"]) is True and row["missing"] == []
    assert row["inputs_missing"] == [{"kind": "OIS_CURVE", "key": "EUR"}, {"kind": "VOL_SMILE", "key": "EURSEK"}]


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
# tested with a fake blpapi in tests/test_bloomberg.py); futures keep the daily PX_LAST.
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
        return {"ESU6 Index": {d.isoformat(): {"PX_LAST": 7550.0} for d in backfill.business_days(start, end)}}

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
    # the daily history is asked for the future's PX_LAST and for nothing FX
    assert asked["daily"] == [(("ESU6 Index",), ("PX_LAST",))]
    stamps = dict(conn.execute("SELECT mark_type, snapped_at FROM marks"))
    assert stamps == {"SPOT": "2026-09-07T15:00:00-04:00", "FWD_OUTRIGHT": "2026-09-07T15:00:00-04:00",
                      "FUTURE_PX": "2026-09-07T17:00:00-04:00"}          # a settlement is stamped 17:00 (2026-09-22)
    assert stamps["FUTURE_PX"] == backfill.settle_stamp(day)
    assert conn.execute("SELECT value FROM marks WHERE mark_type = 'SPOT'").fetchone()[0] == 0.6505


def test_a_past_fx_row_that_is_not_the_1500_close_is_replaced_and_one_that_is_never_is(tmp_path):
    from data.bloomberg.inventory import close_completeness
    p, conn = _db_with_future(tmp_path)
    fri, mon = "2026-09-04", "2026-09-07"
    fri_close = backfill.close_stamp(date(2026, 9, 4))
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        # Monday as its last live pull left it: a spot, a direct-quote forward and a live
        # futures price (PX_LAST at the press: not a close either, 2026-09-22)
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
    assert before[mon].complete is False and before[mon].present == 0 and before[mon].not_closed == 3
    assert {m["mark_type"] for m in before[mon].missing} == {"SPOT", "FWD_OUTRIGHT", "FUTURE_PX"}
    assert before[fri].present == 2 and before[fri].not_closed == 0               # its closes count; the future is missing
    # ... but today's rows are live and count as they are
    assert bool(close_completeness(conn, mon, mon, today=mon)["complete"].iloc[0]) is True

    results = backfill.backfill(p, date(2026, 9, 4), date(2026, 9, 7), fwd_fetch=fwd_fetch_2,
                                fetch=lambda session, service, tickers, field, day: {"AUDUSD Curncy": 0.6505},
                                fut_fetch=lambda session, service, tickers, fields, start, end: {
                                    "ESU6 Index": {d.isoformat(): {"PX_LAST": 7550.0}
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
    # the live futures price is replaced by the day's PX_LAST, stamped at the settlement (17:00)
    assert rows[(mon, "FUTURE_PX", "BBG_BDH")] == (7550.0, backfill.settle_stamp(date(2026, 9, 7)))
    assert (mon, "FUTURE_PX", "BBG_BDH") in rows and rows[(mon, "FUTURE_PX", "BBG_BDH")][1] == "2026-09-07T17:00:00-04:00"
    # Friday: rows already stamped at the close are never rewritten; the missing future is written
    assert rows[(fri, "SPOT", "BBG_BFXFORWARD")] == (0.8001, fri_close)
    assert rows[(fri, "FWD_OUTRIGHT", "BBG_INTERP")] == (0.8002, fri_close)
    assert rows[(fri, "FUTURE_PX", "BBG_BDH")] == (7550.0, backfill.settle_stamp(date(2026, 9, 4)))
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
            out["ESU6 Index"] = {d.isoformat(): {"PX_LAST": 7550.0} for d in backfill.business_days(start, end)}
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
    assert ("daily", ("AUDUSD Curncy",), ("PX_LAST",)) in asked and ("daily", ("ESU6 Index",), ("PX_LAST",)) in asked
    assert r["status"] == "DONE" and (r["closes"], r["future_px"]) == (1, 1) and r["missing_pairs"] == []
    assert conn.execute("SELECT value, snapped_at FROM marks WHERE mark_type = 'SPOT'").fetchall() == [
        (0.6505, "2026-09-07T17:00:00-04:00")]
    assert any("take Bloomberg's daily close (17:00 New York)" in line for line in log)
    # that 17:00 row IS the day's close: never asked for again
    done = close_completeness(conn, "2026-09-07", "2026-09-07", today=today.isoformat())
    assert [m["mark_type"] for m in done["missing"].iloc[0]] == ["FWD_OUTRIGHT"] and done["not_closed"].iloc[0] == 0


def test_every_past_day_within_bloombergs_intraday_history_closes_at_1500_new_york(monkeypatch):
    """User decision 2026-09-22 ("I want to see the ltd line chart, which requires all the
    previous closes, and fixes. For futures, can use market close, for fx use new 3pm"): the
    15:00 close applies to every past day Bloomberg's intraday history reaches, on either
    side of 2026-09-21 -- the cut-over of 2026-09-21 ("surely for like a month ago its not
    that serious") is gone. Only a day beyond that history keeps the 17:00 daily close."""
    from data.bloomberg import pull_marks as pm
    today = date(2026, 9, 22)
    floor = backfill.intraday_floor(today)
    assert backfill.first_1500_day(today) == floor == date(2026, 3, 10)          # no calendar cut-over any more
    assert not hasattr(backfill, "CLOSE_1500_FROM")
    # (a) a day before 2026-09-21 within reach: a live-stamped or a 17:00 row is NOT a close, 15:00 is
    assert backfill.is_close_row("SPOT", "2026-09-18", "2026-09-18T11:40:12-04:00", today) is False
    assert backfill.is_close_row("SPOT", "2026-09-18", "2026-09-19T10:31:12+08:00", today) is False   # a 22:31 NY press
    assert backfill.is_close_row("FWD_OUTRIGHT", "2026-09-14", "2026-09-14T17:00:00-04:00", today) is False
    assert backfill.is_close_row("SPOT", "2026-09-18", "2026-09-18T15:00:00-04:00", today) is True
    assert backfill.is_close_row("SPOT", "2026-09-18", "2026-09-19T03:00:00+08:00", today) is True   # the same instant
    assert backfill.is_close_row("FWD_OUTRIGHT", "2026-07-22", "2026-07-22T15:00:00-04:00", today) is True
    assert backfill.is_close_row("SPOT", "2026-07-22", "2026-07-22T17:00:00-04:00", today) is False
    # today given as an ISO string (close_completeness passes one), and defaulting to the book date
    assert backfill.is_close_row("SPOT", "2026-09-18", "2026-09-18T17:00:00-04:00", "2026-09-22") is False
    monkeypatch.setattr(__import__("data.bloomberg.live", fromlist=["x"]), "book_today", lambda: today)
    assert backfill.is_close_row("SPOT", "2026-09-18", "2026-09-18T17:00:00-04:00") is False
    before = floor - timedelta(days=3)
    # a future's close is its PX_LAST, stamped at the settlement, 17:00 New York (2026-09-22: "for
    # futures, can use market close"): a live press's row (the press time, or 15:00 of the book date
    # as pull_marks.build_future_rows stamps it) is not a close, on any past day, intraday reach or not
    assert backfill.is_close_row("FUTURE_PX", "2026-09-18", "2026-09-18T11:40:12-04:00", today) is False
    assert backfill.is_close_row("FUTURE_PX", "2026-09-18", "2026-09-18T15:00:00-04:00", today) is False
    assert backfill.is_close_row("FUTURE_PX", "2026-09-18", "2026-09-18T17:00:00-04:00", today) is True
    assert backfill.is_close_row("FUTURE_PX", "2026-09-18", backfill.settle_stamp(date(2026, 9, 18)), today) is True
    assert backfill.is_close_row("FUTURE_PX", before.isoformat(), f"{before}T15:00:00-05:00", today) is False
    assert backfill.is_close_row("FUTURE_PX", before.isoformat(), f"{before}T17:00:00-05:00", today) is True
    assert backfill.settle_stamp(date(2026, 1, 15)) == "2026-01-15T17:00:00-05:00"
    assert backfill.is_close_row("NDF_FIX", "2026-09-16", "2026-09-16T17:00:00-04:00", today) is True
    assert backfill.is_close_row("NDF_FIX", "2026-09-16", "2026-09-16T15:00:00-04:00", today) is True
    # (b) beyond the intraday floor the 17:00 daily close counts (and so would a 15:00 row); a live press never
    assert backfill.is_close_row("SPOT", before.isoformat(), f"{before}T17:00:00-05:00", today) is True
    assert backfill.is_close_row("SPOT", before.isoformat(), f"{before}T15:00:00-05:00", today) is True
    assert backfill.is_close_row("SPOT", before.isoformat(), f"{before}T11:40:12-05:00", today) is False
    assert backfill.is_close_row("SPOT", floor.isoformat(), f"{floor}T17:00:00-04:00", today) is False   # the floor itself is within reach
    # (c) the stamp the backfill writes: 15:00 on any day within reach, 17:00 beyond it
    assert backfill.close_stamp(date(2026, 9, 18), today=today) == "2026-09-18T15:00:00-04:00"
    assert backfill.close_stamp(date(2026, 7, 22), today=today) == "2026-07-22T15:00:00-04:00"
    assert backfill.close_stamp(floor, today=today) == f"{floor}T15:00:00-04:00"
    assert backfill.close_stamp(before, today=today) == f"{before}T17:00:00-05:00"
    # a stretch across the intraday floor is asked for in its two parts: daily before, 15:00 from it on
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


# =========================================================================== 2026-09-22: the day's inputs and its swaps
# A past day prices its options and swaps from its own inputs (user: "options daily pnl 0,
# that cannot be right, everything is moving"): the backfill asks Bloomberg's daily history
# (PX_LAST) for the smile of every pair with an option open and the OIS curve of every
# currency an option or a swap needs that day, writes them like the live steps do, and
# prices the day's swaps from them (engine.rates.store.recalc_on_file for that day) before
# its options. A day that holds a pair's or a currency's quotes is not asked again.
def _swap_and_forward_db(tmp_path, with_forward=True):
    """A USD OIS swap dealt Tue 09-01 to 2031, and (unless `with_forward` is False) the
    AUDUSD forward of `_db`, so the days worked have closes of their own."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'',  '2031-09-03')")
    conn.execute("INSERT INTO trades VALUES ('s1','XLSX','IRSOIS-USD-1','IRS','s1','2026-09-01',10e6,0.035,"
                 "'acc','cp','','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("s1", 1, "FIXED", "USD", -10e6, "2026-09-03", "2031-09-03", 0.035, 1),
        ("s1", 2, "FLOAT", "USD", 10e6, "2026-09-03", "2031-09-03", 0.0, 1),
    ])
    if with_forward:
        conn.execute("INSERT INTO instruments VALUES ('AUDUSD','FX','AUD','USD',1,0,'AUDUSD Curncy','9999-12-31')")
        conn.execute("INSERT INTO trades VALUES ('a1','XLSX','AUDUSD','FX_FWD','a1','2026-08-10',-1e6,0.65,"
                     "'acc','cp','','t','d','')")
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
            ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-10-20", 0.65, 1),
            ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-10-20", 0.65, 1),
        ])
    conn.commit()
    return p, conn


def _aud_spot(session, service, tickers, field, day):
    return {t: 0.65 for t in tickers}


def _aud_fwd(session, service, tickers, fields, start, end):
    return {t: {d.isoformat(): {"PX_LAST": 0.655, "SETTLE_DT": "2026-10-20"} for d in backfill.business_days(start, end)}
            for t in tickers}


def _fake_rates(monkeypatch, calls, failed=()):
    """engine.rates.store.recalc_on_file as a recording fake behind backfill._import_recalc_rates:
    prices "the swaps" only from the day's OIS quotes on file, as the real one does, and
    records how many curve_quotes rows the day held when it was asked."""
    def fake_recalc(conn, as_of, since=None):
        n = conn.execute("SELECT COUNT(*) FROM curve_quotes WHERE as_of_date = ? AND quote_type = 'OIS'", (as_of,)).fetchone()[0]
        calls.append((as_of, since, n))
        if not n:
            return {"as_of": as_of, "since": since, "days": [], "priced": 0, "failed": 0}
        day = {"day": as_of, "priced": 1, "failed": [dict(f) for f in failed]}
        return {"as_of": as_of, "since": since, "days": [day], "priced": 1, "failed": len(day["failed"])}
    monkeypatch.setattr(backfill, "_import_recalc_rates", lambda: fake_recalc)
    return fake_recalc


def test_backfill_asks_the_history_for_the_swaps_curve_and_prices_the_day_from_it(tmp_path, monkeypatch):
    p, conn = _swap_and_forward_db(tmp_path)
    calls = []
    _fake_rates(monkeypatch, calls, failed=[{"trade_id": "s9", "error": "missing fixing"}])
    QUOTE_ASKED.clear()
    log = []
    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=_aud_spot, fwd_fetch=_aud_fwd,
                                fut_fetch=_never_called, quote_fetch=quote_history, log=log.append)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    # ONE daily-history request for the stretch: USD's own OIS tickers (rates_marketdata.ois_curve), PX_LAST
    assert QUOTE_ASKED == [(_ois_tickers("USD"), ["PX_LAST"], date(2026, 9, 7), date(2026, 9, 8))]
    assert len(_ois_tickers("USD")) == 17
    # written as the live rates step writes them: per cent scaled to a decimal, index SOFR, BBG_BDH
    rows = conn.execute('SELECT as_of_date, ccy, "index", tenor, ticker, value, quote_type, field, source '
                        "FROM curve_quotes ORDER BY as_of_date, ticker").fetchall()
    assert len(rows) == 34 and {r[1:3] for r in rows} == {("USD", "SOFR")}
    assert {(r[5], r[6], r[7], r[8]) for r in rows} == {(0.039, "OIS", "PX_LAST", "BBG_BDH")}
    assert ("2026-09-07", "USD", "SOFR", "1W", "USOSFR1Z Curncy", 0.039, "OIS", "PX_LAST", "BBG_BDH") in rows
    # the swaps priced for each day, from that day's quotes, once the quotes were on file
    assert calls == [("2026-09-07", "2026-09-07", 17), ("2026-09-08", "2026-09-08", 17)]
    for r in results:
        assert (r["curve_quotes"], r["vol_quotes"], r["missing_inputs"]) == (17, 0, [])
        assert r["rates_priced"] == 1 and r["rates_failed"] == [{"trade_id": "s9", "error": "missing fixing"}]
        assert r["rates_note"] == ""
    assert any("2026-09-08  DONE" in s and "curve_quotes=17" in s and "rates=1" in s and "rates_failed=1" in s for s in log)
    # the days hold their curves now: nothing is asked again, and the swaps are priced again
    # from what is on file (a re-run is idempotent) only when the day is worked
    QUOTE_ASKED.clear()
    calls.clear()
    from data.bloomberg.inventory import close_completeness
    assert list(close_completeness(conn, "2026-09-07", "2026-09-08")["inputs_missing"]) == [[], []]
    again = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=_never_called, fwd_fetch=_never_called,
                              fut_fetch=_never_called, quote_fetch=_never_called, log=lambda *_: None)
    assert [r["status"] for r in again] == ["SKIPPED", "SKIPPED"] and calls == [] and QUOTE_ASKED == []


def test_a_book_of_swaps_alone_still_gets_its_curves_and_is_priced(tmp_path, monkeypatch):
    """The early bail-out used to need an FX pair or a future; a swap's curve is a need too."""
    p, conn = _swap_and_forward_db(tmp_path, with_forward=False)
    calls = []
    _fake_rates(monkeypatch, calls)
    results = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=_never_called, fwd_fetch=_never_called,
                                fut_fetch=_never_called, quote_fetch=quote_history, log=lambda *_: None)
    assert [(r["status"], r["closes"], r["curve_quotes"], r["rates_priced"]) for r in results] == [("DONE", 0, 17, 1)]
    assert calls == [("2026-09-08", "2026-09-08", 17)]


def test_a_day_that_holds_the_smile_or_the_curve_is_not_asked_for_it_again(tmp_path, monkeypatch):
    """The live pull wrote Monday's USD curve (BBG_BDP) and USDJPY smile; Tuesday has neither.
    One request per kind still goes for the stretch (Tuesday lacks them), Monday's rows are
    left as they are, and a run over Monday alone asks nothing."""
    p, conn = _swap_and_forward_db(tmp_path)
    conn.execute("INSERT INTO instruments VALUES ('USDJPY101526C-1','FX_OPTION','USD','JPY',1,0,'USDJPY101526C-1','2026-10-15')")
    conn.execute("INSERT INTO trades VALUES ('o1','XLSX','USDJPY101526C-1','FX_OPTION','o1','2026-09-01',1e6,0.01,"
                 "'acc','cp','','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('o1',1,'NOTIONAL','USD',1e6,'2026-09-01','2026-10-15',0,0)")
    from data.bloomberg import rates_marketdata as rm, vol_marketdata as vm
    mon = "2026-09-07"
    rm.write_curve_quotes(conn, rm.CurveSnapshot("USD", "SOFR", date(2026, 9, 7), [
        rm.CurveQuote(spec.tenor, spec.ticker, rm.scale_quote(__import__("decimal").Decimal("4.1")))
        for spec in rm.ois_curve("USD")]), mon)                                      # BBG_BDP: the live pull's
    vm.write_vol_quotes(conn, {"USDJPY": vm.PairVolSnapshot("USDJPY", date(2026, 9, 7), [
        vm.VolQuote("1M", "ATM", vm.vol_ticker("USDJPY", "1M", "ATM"), 9.1)])}, mon)  # BBG_BDP, one quote is enough
    conn.commit()
    from data.bloomberg.inventory import close_completeness
    strip = {r.as_of_date: r.inputs_missing for r in close_completeness(conn, "2026-09-07", "2026-09-08").itertuples()}
    assert strip == {mon: [{"kind": "OIS_CURVE", "key": "JPY"}],
                     "2026-09-08": [{"kind": "OIS_CURVE", "key": "JPY"}, {"kind": "OIS_CURVE", "key": "USD"},
                                    {"kind": "VOL_SMILE", "key": "USDJPY"}]}
    calls = []
    _fake_rates(monkeypatch, calls)
    monkeypatch.setattr(backfill, "_import_price_close", lambda: (lambda conn, day: {"day": day, "priced": 1, "skipped": []}))
    QUOTE_ASKED.clear()
    fetch = lambda s, v, tickers, field, day: {t: {"AUDUSD Curncy": 0.65, "USDJPY Curncy": 147.0}[t] for t in tickers}  # noqa: E731
    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=fetch, fwd_fetch=_aud_fwd,
                                fut_fetch=_never_called, quote_fetch=quote_history, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    assert QUOTE_ASKED == [(_vol_tickers("USDJPY"), ["PX_LAST"], date(2026, 9, 7), date(2026, 9, 8)),
                           (sorted(_ois_tickers("JPY") + _ois_tickers("USD")), ["PX_LAST"], date(2026, 9, 7), date(2026, 9, 8))]
    by_day = {r["day"]: r for r in results}
    assert (by_day[mon]["vol_quotes"], by_day[mon]["curve_quotes"]) == (0, 9)             # JPY alone (9 tickers)
    assert (by_day["2026-09-08"]["vol_quotes"], by_day["2026-09-08"]["curve_quotes"]) == (45, 17 + 9)
    # Monday's own rows stand: the live curve's value and source, its one smile quote
    assert conn.execute("SELECT DISTINCT source, value FROM curve_quotes WHERE as_of_date = ? AND ccy = 'USD'",
                        (mon,)).fetchall() == [("BBG_BDP", 0.041)]
    assert conn.execute("SELECT COUNT(*), MIN(source) FROM vol_quotes WHERE as_of_date = ?", (mon,)).fetchone() == (1, "BBG_BDP")
    assert conn.execute("SELECT COUNT(*), MIN(source) FROM vol_quotes WHERE as_of_date = '2026-09-08'").fetchone() == (45, "BBG_BDH")
    assert calls == [(mon, mon, 17 + 9), ("2026-09-08", "2026-09-08", 17 + 9)]
    # a run over Monday alone, now complete with its inputs, asks nothing
    QUOTE_ASKED.clear()
    again = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 7), fetch=_never_called, fwd_fetch=_never_called,
                              fut_fetch=_never_called, quote_fetch=_never_called, log=lambda *_: None)
    assert [r["status"] for r in again] == ["SKIPPED"] and QUOTE_ASKED == []


def test_a_curve_the_history_cannot_fill_is_named_and_the_swaps_say_why_they_were_not_priced(tmp_path, monkeypatch):
    """Fewer than rates_marketdata._MIN_QUOTES (the live source's own floor) OIS quotes come
    back: nothing is written for that currency, the day names it under missing_inputs and
    stays due for it, and the rates step says the swaps had no quotes to price from."""
    p, conn = _swap_and_forward_db(tmp_path)
    calls = []
    _fake_rates(monkeypatch, calls)

    def thin(session, service, tickers, fields, start, end):
        kept = [t for t in tickers if t in ("USOSFR1Z Curncy", "USOSFR2Z Curncy", "USOSFR3Z Curncy")]   # three of 17
        return {t: {d.isoformat(): {"PX_LAST": 3.9} for d in backfill.business_days(start, end)} for t in kept}

    results = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=_aud_spot, fwd_fetch=_aud_fwd,
                                fut_fetch=_never_called, quote_fetch=thin, log=lambda *_: None)
    r = results[0]
    assert r["status"] == "DONE" and r["curve_quotes"] == 0
    assert [(m["kind"], m["key"]) for m in r["missing_inputs"]] == [("OIS_CURVE", "USD")]
    assert r["missing_inputs"][0]["reason"].startswith("fewer than 4 OIS quotes for USD on 2026-09-08: Bloomberg returned "
                                                       "no value for USOSFRA Curncy, USOSFRB Curncy")
    assert conn.execute("SELECT COUNT(*) FROM curve_quotes").fetchone() == (0,)
    assert calls == [("2026-09-08", "2026-09-08", 0)]
    assert (r["rates_priced"], r["rates_failed"], r["rates_note"]) == (0, [], "no OIS quotes on file for 2026-09-08: swaps not priced")
    from data.bloomberg.inventory import close_completeness
    row = close_completeness(conn, "2026-09-08", "2026-09-08").iloc[0]
    assert bool(row["complete"]) is True and row["inputs_missing"] == [{"kind": "OIS_CURVE", "key": "USD"}]
    # the pricer not importable, or raising: the day stands and says so, like the options step
    monkeypatch.setattr(backfill, "_import_recalc_rates", lambda: None)
    r = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=_aud_spot, fwd_fetch=_aud_fwd,
                          fut_fetch=_never_called, quote_fetch=quote_history, log=lambda *_: None)[0]
    assert (r["status"], r["curve_quotes"], r["rates_priced"], r["rates_note"]) == ("DONE", 17, None, backfill.RATES_RECALC_UNAVAILABLE)

    def boom(conn, as_of, since=None):
        raise RuntimeError("QuantLib refused the curve")

    monkeypatch.setattr(backfill, "_import_recalc_rates", lambda: boom)
    r = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=_aud_spot, fwd_fetch=_aud_fwd,
                          fut_fetch=_never_called, quote_fetch=quote_history, overwrite=True, log=lambda *_: None)[0]
    assert r["rates_priced"] is None and "QuantLib refused the curve" in r["rates_note"]
    # no swap open: the pricer is never called
    conn.execute("DELETE FROM trade_legs WHERE trade_id = 's1'")
    conn.execute("DELETE FROM trades WHERE trade_id = 's1'")
    conn.commit()
    calls.clear()
    _fake_rates(monkeypatch, calls)
    r = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=_aud_spot, fwd_fetch=_aud_fwd,
                          fut_fetch=_never_called, quote_fetch=_never_called, overwrite=True, log=lambda *_: None)[0]
    assert calls == [] and (r["rates_priced"], r["rates_failed"], r["rates_note"]) == (None, [], "")


def test_the_library_lists_a_past_days_curve_and_smile_for_options_and_swaps_only(tmp_path):
    """What a past close needs: the options' and swaps' OIS_CURVE / VOL_SMILE rows, a currency
    out of the OIS scope never, a listed option's curve (its Greeks today) never, and the
    live-only kinds (fixings, the 1M NDF, a dividend yield) never."""
    from data.bloomberg import library
    p, conn = _swap_and_forward_db(tmp_path)
    conn.execute("INSERT INTO instruments VALUES ('EURSEK-OPT-1','FX_OPTION','EUR','SEK',1,0,'EURSEK-OPT-1','2026-10-15')")
    conn.execute("INSERT INTO trades VALUES ('o1','XLSX','EURSEK-OPT-1','FX_OPTION','o1','2026-09-01',1e6,0.01,"
                 "'acc','cp','','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('o1',1,'NOTIONAL','EUR',1e6,'2026-09-01','2026-10-15',0,0)")
    conn.commit()
    # 2026-09-24: a commodity future's Bloomberg contract dates are asked by today's pull only
    assert library.LIVE_ONLY_KINDS == ("FIXINGS", library.NDF_1M, library.DIV_YIELD, library.CONTRACT_DATES)
    assert library.HISTORY_INPUT_KINDS == ("OIS_CURVE", "VOL_SMILE")
    past = {(r["kind"], r["key"], r["product"]) for r in library.needed_on(conn, "2026-09-08", historical=True)
            if r["kind"] in library.HISTORY_INPUT_KINDS}
    assert past == {("OIS_CURVE", "USD", "IRS"), ("OIS_CURVE", "EUR", "FX_OPTION"), ("OIS_CURVE", "SEK", "FX_OPTION"),
                    ("VOL_SMILE", "EURSEK", "FX_OPTION")}
    assert not [r for r in library.needed_on(conn, "2026-09-08", historical=True) if r["kind"] in library.LIVE_ONLY_KINDS]
    assert library.history_inputs_needed(conn, "2026-09-08") == [
        {"kind": "OIS_CURVE", "key": "EUR"}, {"kind": "OIS_CURVE", "key": "USD"}, {"kind": "VOL_SMILE", "key": "EURSEK"}]
    assert library.history_inputs_needed(conn, "2026-08-31") == []                       # before either was dealt
    assert {(r["kind"], r["key"]) for r in library.needed_in_range(conn, "2026-09-01", "2026-09-08")} >= {
        ("OIS_CURVE", "USD"), ("VOL_SMILE", "EURSEK"), ("SPOT", "AUDUSD")}


# =========================================================================== 2026-09-22: each day's ledger block records the re-freeze
_NEW_LEDGER = {"realised": 1, "unrealisable": [], "repaired": [],
               "refrozen": [{"trade_id": "a1", "product": "FX_FWD", "mark_type": "SPOT", "spot_as_of_date": "2026-09-09",
                             "pnl_from": 1.0, "pnl_to": 2.0, "why": "the 15:00 close replaced a live row"}],
               "kept": [{"trade_id": "z9", "product": "FX_OPTION", "reason": "no close-out spot on file yet"}]}


def test_each_backfill_day_carries_the_ledgers_refrozen_and_kept(tmp_path, monkeypatch):
    """A direct backfill() call runs the ledger after every day: the day's result carries
    refrozen / kept / refrozen_count / refrozen_summary as the ledger gave them (user yes,
    2026-09-22), and the log names the sentence."""
    p, conn = _db(tmp_path)
    monkeypatch.setattr(backfill, "_import_realise_settled", lambda: (lambda c, as_of, **kw: dict(_NEW_LEDGER)))
    log = []
    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 8), fetch=fake_fetch, fwd_fetch=fake_fwd_fetch,
                                fut_fetch=fake_fwd_fetch, log=log.append)
    assert [r["status"] for r in results] == ["DONE", "DONE"]
    for r in results:
        assert r["realised"] == 1 and r["unrealisable"] == []
        assert r["refrozen"] == _NEW_LEDGER["refrozen"] and r["kept"] == _NEW_LEDGER["kept"]
        assert r["refrozen_count"] == 1 and r["refrozen_summary"] == "1 settled trade re-frozen at the close"
    assert any("1 settled trade re-frozen at the close" in s for s in log)


def test_each_backfill_day_tolerates_the_old_ledger_shape(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    monkeypatch.setattr(backfill, "_import_realise_settled",
                        lambda: (lambda c, as_of, **kw: {"realised": 0, "unrealisable": [], "refrozen": ["a1"]}))
    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 7), fetch=fake_fetch, fwd_fetch=fake_fwd_fetch,
                                fut_fetch=fake_fwd_fetch, log=lambda s: None)
    assert results[0]["status"] == "DONE" and results[0]["realised"] == 0
    assert results[0]["refrozen"] == [{"trade_id": "a1"}] and results[0]["kept"] == []
    assert results[0]["refrozen_count"] == 1


# =========================================================================== 2026-09-22: NDF tenor families, PX_LAST for FUTURE_PX
# User: "for the ones that dont have, we need to use forward points to get the forward. I
# checked bcn1m works for points, and similarly for ihn and ntn". Bloomberg rejects
# 'USDBRLSP Curncy' / 'USDBRL1M Curncy' ("Unknown/Invalid security"), so the history asks
# the NDF families (pull_marks.NDF_TENOR_FAMILIES): no SP ticker, spot is the first pillar.
def _usdbrl_db(tmp_path, settle="2026-10-01"):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('USDBRL','FX','USD','BRL',1,1,'USDBRL Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('b1','XLSX','USDBRL','FX_FWD','b1','2026-08-10',1e6,5.40,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("b1", 1, "FX_NEAR", "USD", 1e6, "2026-08-10", settle, 5.40, 1),
        ("b1", 2, "FX_NEAR", "BRL", -5.4e6, "2026-08-10", settle, 5.40, 0),
    ])
    conn.commit()
    return p, conn


def test_backfill_asks_the_ndf_family_tickers_for_usdbrl_and_converts_their_points_at_the_pairs_divisor(tmp_path):
    asked = []

    def points(session, service, tickers, fields, start, end):
        asked.append(sorted(tickers))
        # BCN2W is rejected on this terminal: no series for it, the others come back as points
        out, d = {}, start
        while d <= end:
            if d.weekday() < 5:
                out.setdefault("BCN1W Curncy", {})[d.isoformat()] = {"PX_LAST": 100.0}
                out.setdefault("BCN1M Curncy", {})[d.isoformat()] = {"PX_LAST": 400.0}
            d += timedelta(days=1)
        return out

    def scale(session, service, tickers, fields):
        assert fields == ["FWD_POINTS_SCALE", "FWD_SCALE"] and tickers == ["USDBRL Curncy"]
        return {"USDBRL Curncy": {"FWD_SCALE": 4}}                                 # what the Bloomberg PC recorded

    p, conn = _usdbrl_db(tmp_path)
    results = backfill.backfill(p, date(2026, 9, 14), date(2026, 9, 14), fwd_fetch=points, fut_fetch=_never_called,
                                fetch=lambda s, v, tickers, field, day: {"USDBRL Curncy": 5.30},
                                scale_fetch=scale, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    # the family tickers, SP..1M less the SP the family has no ticker for; never the pair spelling
    assert asked == [["BCN1M Curncy", "BCN1W Curncy", "BCN2W Curncy"]]
    assert not any(t.startswith("USDBRL") for call in asked for t in call)
    # spot date Wed 09-16, 1W = 09-23 at 5.30 + 100 / 10**4 = 5.31, 1M = Fri 10-16 at 5.34; the
    # rejected 2W is simply absent; target 10-01 is 8 of the 23 days between them
    value, source, snapped = conn.execute(
        "SELECT value, source, snapped_at FROM marks WHERE mark_type = 'FWD_OUTRIGHT' AND settle_date = '2026-10-01'").fetchone()
    assert value == pytest.approx(5.31 + (8 / 23) * (5.34 - 5.31), abs=1e-9)
    assert source == "BBG_INTERP" and snapped == "2026-09-14T15:00:00-04:00"


def test_tenor_tickers_keep_the_pair_spelling_for_krw_and_inr_and_deliverable_pairs():
    from data.bloomberg import pull_marks as pm
    assert backfill._tenor_tickers("USDKRW", ["SP", "1W", "1M"]) == {
        "SP": "USDKRWSP Curncy", "1W": "USDKRW1W Curncy", "1M": "USDKRW1M Curncy"}
    assert backfill._tenor_tickers("USDINR", ["SP", "1M"]) == {"SP": "USDINRSP Curncy", "1M": "USDINR1M Curncy"}
    assert backfill._tenor_tickers("USDJPY", ["SP", "1Y"]) == {"SP": "USDJPYSP Curncy", "1Y": "USDJPY1Y Curncy"}
    assert backfill._tenor_tickers("USDIDR", pm.STANDARD_TENORS) == {
        t: f"IHN{t} Curncy" for t in ("1W", "2W", "1M", "2M", "3M", "6M", "1Y")}
    assert backfill._tenor_tickers("USDTWD", ["SP", "1M"]) == {"1M": "NTN1M Curncy"}
    # a family change re-asks every day the backfill gave up on (backfill.state_version)
    version = backfill.state_version()
    assert version.startswith(__import__("data.bloomberg.library", fromlist=["x"]).LIBRARY_VERSION + "+")


def test_backfill_asks_the_daily_px_last_for_futures_and_listed_options_and_stamps_it_at_the_daily_close(tmp_path):
    """User, 2026-09-22: "all futures for past date pnl calculation, use px last" (Bloomberg
    served no PX_SETTLE history for 'SPX US 10/16/26 P7615 Index'). Both kinds in one
    request, PX_LAST, written as the official FUTURE_PX stamped settle_stamp (17:00 New York)."""
    p, conn = _db_with_future(tmp_path)
    conn.execute("INSERT INTO instruments VALUES ('SPX/E261016P7615-USAA','EQ_OPTION','SPX','USD',100,0,'SPX Index','2026-10-16')")
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type) VALUES ('SPX/E261016P7615-USAA', 7615, 'PUT')")
    conn.execute("INSERT INTO trades VALUES ('s1','XLSX','SPX/E261016P7615-USAA','EQ_OPTION','s1','2026-08-10',-3,120.5,"
                 "'acc','cp','','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('s1',1,'NOTIONAL','USD',-3*100*120.5,'2026-08-10','2026-10-16',0,0)")
    conn.commit()
    asked = []

    def daily(session, service, tickers, fields, start, end):
        asked.append((sorted(tickers), list(fields), start, end))
        return {t: {d.isoformat(): {"PX_LAST": 7550.0 if t == "ESU6 Index" else 98.25, "PX_SETTLE": -1.0}
                    for d in backfill.business_days(start, end)} for t in tickers}

    results = backfill.backfill(p, date(2026, 9, 7), date(2026, 9, 7), fetch=spot_fetch_2, fwd_fetch=fwd_fetch_2,
                                fut_fetch=daily, log=lambda *_: None)
    assert [r["status"] for r in results] == ["DONE"] and results[0]["missing_marks"] == []
    assert asked == [(["ESU6 Index", "SPX US 10/16/26 P7615 Index"], ["PX_LAST"], date(2026, 9, 7), date(2026, 9, 7))]
    rows = conn.execute("SELECT instrument_id, settle_date, value, source, snapped_at FROM marks "
                        "WHERE mark_type = 'FUTURE_PX' ORDER BY instrument_id").fetchall()
    assert rows == [("ESU6 Index", "2026-12-19", 7550.0, "BBG_BDH", backfill.settle_stamp(date(2026, 9, 7))),
                    ("SPX/E261016P7615-USAA", "2026-10-16", 98.25, "BBG_BDH", backfill.settle_stamp(date(2026, 9, 7)))]
    assert rows[0][4] == "2026-09-07T17:00:00-04:00"
    assert all(backfill.is_close_row("FUTURE_PX", "2026-09-07", r[4], date(2026, 9, 21)) for r in rows)
    # a listed option with no PX_LAST that day is reported under that field's name
    results = backfill.backfill(p, date(2026, 9, 8), date(2026, 9, 8), fetch=spot_fetch_2, fwd_fetch=fwd_fetch_2,
                                fut_fetch=lambda *a: {"ESU6 Index": {"2026-09-08": {"PX_LAST": 7560.0}}}, log=lambda *_: None)
    assert [m["reason"] for m in results[0]["missing_marks"]] == [
        "Bloomberg returned no PX_LAST for SPX/E261016P7615-USAA on 2026-09-08"]
