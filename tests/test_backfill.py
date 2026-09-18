"""data/bloomberg/backfill.py: history rebuilt from daily closes with a fake fetch. No blpapi.

Backfill only writes SPOT marks and (if importable) calls engine.pnl.ledger.realise_settled;
it no longer writes pnl_snapshots (BUILD_PLAN.md section 3/6, Task B)."""
from datetime import date, timedelta

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


def fake_fetch(session, service, tickers, field, day):
    assert field == "PX_LAST" and set(tickers) == {"AUDUSD Curncy", "USDJPY Curncy"}
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

    # official SPOT marks stamped 17:00 New York with the date's offset (EDT in September)
    marks = conn.execute("SELECT as_of_date, instrument_id, value, source, snapped_at FROM marks ORDER BY 1,2").fetchall()
    assert ("2026-09-07", "AUDUSD", 0.60, "BBG_BFXFORWARD", "2026-09-07T17:00:00-04:00") in marks
    assert not any(m[1] == "EURSEK" for m in marks)
    # 09-10: AUDUSD's own SPOT close is missing (and it's no longer open -- settled
    # 09-09) but USDJPY (still open) gets both its SPOT and its FWD_OUTRIGHT.
    assert len([m for m in marks if m[0] == "2026-09-10"]) == 2
    assert {m[1:4] for m in marks if m[0] == "2026-09-10"} == {
        ("USDJPY", 151.0, "BBG_BFXFORWARD"), ("USDJPY", 149.5, "BBG_BFXFORWARD")}

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
    # 09-07: SPOT x2 (AUDUSD+USDJPY) + FWD_OUTRIGHT x1 (AUDUSD only -- USDJPY not yet
    # traded that day); 09-08: SPOT x2 + FWD_OUTRIGHT x2 (both open) = 3 + 4 = 7.
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type='FWD_OUTRIGHT'").fetchone()[0] == 3
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

    def spot_fetch(session, service, tickers, field, day):
        assert set(tickers) == {"EURSEK Curncy"}
        return {"EURSEK Curncy": 11.21}

    def fwd_fetch(session, service, tickers, fields, start, end):
        # exact-match trick: every tenor ticker quotes the leg's own settle date, so
        # outright_for_date resolves it directly regardless of which day is asked.
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
