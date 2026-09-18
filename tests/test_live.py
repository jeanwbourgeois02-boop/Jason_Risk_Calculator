"""data/bloomberg/live.py: rates from marks, status file, one pull with a fake session,
feed not started without Bloomberg. No blpapi needed."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from data.bloomberg import live
from data.ingest import schema


def _db(tmp_path):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
        ("EURSEK", "FX", "EUR", "SEK", 1, 0, "EURSEK Curncy", "9999-12-31"),
    ])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "HAHY7", "t", "d", ""),
        ("j1", "XLSX", "USDJPY", "FX_FWD", "j1", "2026-08-10", 1e6, 150.0, "acc", "cp", "HAHY7", "t", "d", ""),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-16", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-16", 0.65, 1),
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-08-10", "2026-09-18", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-08-10", "2026-09-18", 150.0, 1),
    ])
    conn.commit()
    return p, conn


def test_rates_from_marks_latest_inverted_and_stale(tmp_path):
    p, conn = _db(tmp_path)
    now = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(seconds=60)).isoformat()
    old = (now - timedelta(hours=3)).isoformat()
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-13", "AUDUSD", "2026-09-13", "SPOT", 0.60, "BBG_BFXFORWARD", old),
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", 0.66, "BBG_BFXFORWARD", fresh),   # latest wins
        ("2026-09-14", "USDJPY", "2026-09-14", "SPOT", 150.0, "BBG_BFXFORWARD", old),
        ("2026-09-14", "EURSEK", "2026-09-14", "SPOT", 11.0, "BBG_BFXFORWARD", fresh),   # cross ignored
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", 0.99, "BNP_BVAL", fresh),        # not official
    ])
    conn.commit()
    rates = live.rates_from_marks(conn, now=now)
    assert set(rates) == {"AUD", "JPY"}
    assert rates["AUD"]["rate"] == 0.66 and rates["AUD"]["inverted"] is False and rates["AUD"]["stale"] is False
    assert rates["JPY"]["rate"] == 150.0 and rates["JPY"]["inverted"] is True and rates["JPY"]["stale"] is True
    assert rates["AUD"]["source"] == "BBG_BFXFORWARD" and rates["AUD"]["timestamp"] == fresh
    for entry in rates.values():
        assert set(entry) >= {"rate", "inverted", "source", "timestamp", "stale"}


def test_build_requests_spot_per_pair_and_forward_per_open_date(tmp_path):
    p, conn = _db(tmp_path)
    reqs = live.build_requests(conn, "2026-08-17")
    keys = {(r.instrument_id, r.mark_type, r.settle_date) for r in reqs}
    assert ("AUDUSD", "SPOT", "2026-08-17") in keys and ("USDJPY", "SPOT", "2026-08-17") in keys
    assert ("AUDUSD", "FWD_OUTRIGHT", "2026-09-16") in keys and ("USDJPY", "FWD_OUTRIGHT", "2026-09-18") in keys
    # no shared workbook maturity request any more (BUILD_PLAN.md section 2): each leg is
    # only requested at its own settle_date
    assert ("AUDUSD", "FWD_OUTRIGHT", "2026-08-24") not in keys
    assert all(r.settle_date in ("2026-08-17", "2026-09-16", "2026-09-18") for r in reqs)
    assert live.build_requests(conn, "2026-10-01") == []           # everything settled


def test_build_requests_includes_open_futures(tmp_path):
    p, conn = _db(tmp_path)
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',6*50*7528.25,'2026-08-10','2026-09-18',0,0)")
    conn.commit()
    reqs = live.build_requests(conn, "2026-08-17")
    keys = {(r.instrument_id, r.mark_type, r.settle_date) for r in reqs}
    assert ("ESU6 Index", "FUTURE_PX", "2026-09-18") in keys
    later_keys = {(r.instrument_id, r.mark_type) for r in live.build_requests(conn, "2026-09-19")}
    assert ("ESU6 Index", "FUTURE_PX") not in later_keys       # expired future drops out


# --------------------------------------------------------------------------- cross USD-leg SPOT requests (2026-09-17)
def _cross_db(tmp_path, with_usd_leg_instruments=False):
    """A book with a lone EURSEK cross trade and nothing else -- no direct EURUSD or
    USDSEK trade, so neither currency's usd_delta has anywhere else to come from."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('EURSEK','FX','EUR','SEK',1,0,'EURSEK Curncy','9999-12-31')")
    if with_usd_leg_instruments:
        conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
        conn.execute("INSERT INTO instruments VALUES ('USDSEK','FX','USD','SEK',1,0,'USDSEK Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('e1','XLSX','EURSEK','FX_FWD','e1','2026-08-10',1e6,11.20,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("e1", 1, "FX_NEAR", "EUR", 1e6, "2026-08-10", "2026-09-16", 11.20, 1),
        ("e1", 2, "FX_NEAR", "SEK", -11200000, "2026-08-10", "2026-09-16", 11.20, 1),
    ])
    conn.commit()
    return p, conn


def test_build_requests_lone_cross_also_requests_both_usd_legs_conventional_ticker(tmp_path):
    """No EURUSD/USDSEK instrument on file: build_requests must still ask Bloomberg for
    them, using the conventional pair spelling and '<pair> Curncy' ticker (EUR is a major
    -> EURUSD; SEK is not -> USDSEK, not SEKUSD)."""
    p, conn = _cross_db(tmp_path)
    reqs = live.build_requests(conn, "2026-08-17")
    keys = {(r.instrument_id, r.mark_type, r.settle_date) for r in reqs}
    assert ("EURSEK", "SPOT", "2026-08-17") in keys
    assert ("EURUSD", "SPOT", "2026-08-17") in keys
    assert ("USDSEK", "SPOT", "2026-08-17") in keys
    by_id = {r.instrument_id: r for r in reqs if r.mark_type == "SPOT"}
    assert by_id["EURUSD"].bbg_ticker == "EURUSD Curncy"
    assert by_id["USDSEK"].bbg_ticker == "USDSEK Curncy"


def test_build_requests_cross_leg_uses_existing_instrument_row_when_present(tmp_path):
    """When EURUSD/USDSEK already exist as instruments (the normal case for any book that
    also trades majors), their own instrument_id/bbg_ticker are used rather than a
    freshly-constructed one."""
    p, conn = _cross_db(tmp_path, with_usd_leg_instruments=True)
    conn.execute("UPDATE instruments SET bbg_ticker = 'EURUSD Curncy BGN' WHERE instrument_id = 'EURUSD'")
    conn.commit()
    reqs = live.build_requests(conn, "2026-08-17")
    by_id = {r.instrument_id: r for r in reqs if r.mark_type == "SPOT"}
    assert by_id["EURUSD"].bbg_ticker == "EURUSD Curncy BGN"       # the instrument's own ticker, not reconstructed
    assert by_id["USDSEK"].bbg_ticker == "USDSEK Curncy"


def test_build_requests_cross_leg_not_duplicated_when_directly_traded(tmp_path):
    """A leg that's also directly traded (its pair already an open FX position) must not
    produce a second, duplicate SPOT request."""
    p, conn = _cross_db(tmp_path, with_usd_leg_instruments=True)
    conn.execute("INSERT INTO trades VALUES ('u1','XLSX','EURUSD','FX_FWD','u1','2026-08-10',1e6,1.08,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("u1", 1, "FX_NEAR", "EUR", 1e6, "2026-08-10", "2026-09-16", 1.08, 1),
        ("u1", 2, "FX_NEAR", "USD", -1080000, "2026-08-10", "2026-09-16", 1.08, 1),
    ])
    conn.commit()
    reqs = live.build_requests(conn, "2026-08-17")
    spot_ids = [r.instrument_id for r in reqs if r.mark_type == "SPOT"]
    assert spot_ids.count("EURUSD") == 1


def test_needed_marks_stays_in_step_with_build_requests_for_cross_legs(tmp_path):
    from data.bloomberg import inventory
    p, conn = _cross_db(tmp_path)
    reqs = live.build_requests(conn, "2026-08-17")
    needed = inventory._needed_marks(conn, "2026-08-17")
    req_keys = {(r.instrument_id, r.mark_type, r.settle_date) for r in reqs}
    needed_keys = {(n["instrument_id"], n["mark_type"], n["settle_date"]) for n in needed}
    assert req_keys == needed_keys


def test_pull_once_creates_missing_cross_leg_pair_instruments_and_writes_their_spot(tmp_path, monkeypatch):
    """2026-09-18: the USD-conversion pairs a cross needs (EURUSD / USDSEK for EURSEK) used
    to be requested but could never be written when no instrument row existed -- the gap
    was only reported as a warning and the ladder's USD conversion stayed NaN.
    build_requests now creates the plain FX pair rows first, so the SPOT Bloomberg
    returns is persisted."""
    p, conn = _cross_db(tmp_path)
    from data.bloomberg import pull_marks as pm
    from datetime import date as _date

    def fake_fetch_reference(session, service, tickers, fields, overrides=None, diag=None, tag=None):
        return {t: {"PX_LAST": 1.2345} for t in tickers}  # every ticker "succeeds"

    monkeypatch.setattr(pm, "fetch_reference", fake_fetch_reference)
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    from data.bloomberg import fwd_curve
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=_date(2026, 8, 17))
    assert status["connected"] is True
    assert not [w for w in status["warnings"] if "instrument is on file" in w]
    written_ids = {r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM marks")}
    assert {"EURSEK", "EURUSD", "USDSEK"} <= written_ids
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT instrument_id, asset_class, base_ccy, quote_ccy, bbg_ticker FROM instruments "
        "WHERE instrument_id IN ('EURUSD', 'USDSEK')")}
    assert rows["EURUSD"] == ("FX", "EUR", "USD", "EURUSD Curncy")
    assert rows["USDSEK"] == ("FX", "USD", "SEK", "USDSEK Curncy")


# --------------------------------------------------------------------------- FWD_OUTRIGHT settling today (2026-09-17)
def _same_day_db(tmp_path, settle: str = "2026-08-20"):
    """A single AUDUSD forward whose leg settles exactly on `settle` -- the FWD_CURVE
    boundary case (fwd_curve.outright_for_date has no interpolation range when
    settle_date == as_of, found on the Bloomberg PC as a USDMXN MISSING)."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('AUDUSD','FX','AUD','USD',1,0,'AUDUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('a1','XLSX','AUDUSD','FX_FWD','a1','2026-08-10',-1e6,0.65,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", settle, 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", settle, 0.65, 1),
    ])
    conn.commit()
    return p, conn


def test_fwd_outright_settling_today_is_marked_at_spot_not_via_curve(tmp_path, monkeypatch):
    p, conn = _same_day_db(tmp_path)
    from data.bloomberg import pull_marks as pm
    from datetime import date as _date

    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {"AUDUSD Curncy": {"PX_LAST": 0.6612}})

    def boom(*a, **k):
        raise AssertionError("FWD_CURVE must not be requested when every open leg settles on or before as_of")

    from data.bloomberg import fwd_curve
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", boom)
    status = live.pull_once(p, "2026-08-20", session_factory=lambda: (object(), object()), today=_date(2026, 8, 20))
    assert status["connected"] is True
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    item = by[("AUDUSD", "FWD_OUTRIGHT", "2026-08-20")]
    assert item["status"] == "OK"
    assert item["value"] == 0.6612
    assert item["source"] == "BBG_BFXFORWARD"
    assert item["detail"] == "settles today: marked at spot"
    row = conn.execute("SELECT value, source FROM marks WHERE instrument_id='AUDUSD' AND "
                       "mark_type='FWD_OUTRIGHT'").fetchone()
    assert row == (0.6612, "BBG_BFXFORWARD")
    official = conn.execute("SELECT value FROM marks_official WHERE instrument_id='AUDUSD' AND "
                            "mark_type='FWD_OUTRIGHT'").fetchone()
    assert official == (0.6612,)          # BBG_BFXFORWARD is official for FWD_OUTRIGHT, not a fallback


def test_fwd_outright_settling_today_fails_clearly_when_spot_also_missing(tmp_path, monkeypatch):
    p, conn = _same_day_db(tmp_path)
    from data.bloomberg import pull_marks as pm
    from datetime import date as _date

    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {})  # SPOT itself returns nothing this cycle

    def boom(*a, **k):
        raise AssertionError("FWD_CURVE must not be requested for a same-day settle even when spot is missing")

    from data.bloomberg import fwd_curve
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", boom)
    status = live.pull_once(p, "2026-08-20", session_factory=lambda: (object(), object()), today=_date(2026, 8, 20))
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    item = by[("AUDUSD", "FWD_OUTRIGHT", "2026-08-20")]
    assert item["status"] == "FAILED"
    assert "no live SPOT" in item["detail"]
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type='FWD_OUTRIGHT'").fetchone()[0] == 0


def test_fwd_outright_mixes_same_day_spot_mark_and_later_curve_lookup(tmp_path, monkeypatch):
    """Two open legs in the same cycle -- one settling today (marked at spot), one settling
    later (still goes through the FWD_CURVE path) -- must not interfere with each other."""
    p, conn = _same_day_db(tmp_path, settle="2026-08-20")
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('j1','XLSX','USDJPY','FX_FWD','j1','2026-08-10',1e6,150.0,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-08-10", "2026-09-18", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-08-10", "2026-09-18", 150.0, 1),
    ])
    conn.commit()
    from data.bloomberg import pull_marks as pm
    from datetime import date as _date

    monkeypatch.setattr(pm, "fetch_reference",
                        lambda *a, **k: {"AUDUSD Curncy": {"PX_LAST": 0.6612}, "USDJPY Curncy": {"PX_LAST": 150.2}})
    from data.bloomberg import fwd_curve

    def fake_curves(blpapi, session, service, tickers, timeout_ms=15000):
        assert tickers == ["USDJPY Curncy"]     # AUDUSD (settles today) must never be requested here
        return {"USDJPY Curncy": {"points": [(_date(2026, 9, 18), 150.5)], "columns": [], "error": ""}}

    monkeypatch.setattr(fwd_curve, "request_fwd_curves", fake_curves)
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    status = live.pull_once(p, "2026-08-20", session_factory=lambda: (object(), object()), today=_date(2026, 8, 20))
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    assert by[("AUDUSD", "FWD_OUTRIGHT", "2026-08-20")]["detail"] == "settles today: marked at spot"
    assert by[("USDJPY", "FWD_OUTRIGHT", "2026-09-18")]["status"] == "OK"
    assert by[("USDJPY", "FWD_OUTRIGHT", "2026-09-18")]["value"] == 150.5
    assert by[("USDJPY", "FWD_OUTRIGHT", "2026-09-18")]["detail"] == ""


def test_pull_once_without_bloomberg_writes_status_and_no_marks(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    monkeypatch.setattr(live, "availability", lambda host, port: (False, "blpapi is not installed on this computer"))
    status = live.pull_once(p, "2026-08-17")
    assert status["connected"] is False and "blpapi" in status["reason"] and status["items"] == []
    assert live.read_status(p) == status
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0
    feed, why = live.start_feed_if_available(p)
    assert feed is None and "blpapi" in why and live.read_status(p)["connected"] is False


def test_pull_once_with_fake_session_writes_marks_and_itemised_status(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    from data.bloomberg import pull_marks as pm

    def fake_fetch_reference(session, service, tickers, fields, overrides=None, diag=None, tag=None):
        return {"AUDUSD Curncy": {"PX_LAST": 0.6612}}          # USDJPY missing -> FAILED

    from datetime import date as _date
    from data.bloomberg import fwd_curve

    def fake_curves(blpapi, session, service, tickers, timeout_ms=15000):
        # AUDUSD: exact tenor on 2026-09-16 plus a later one; USDJPY: securityError
        return {"AUDUSD Curncy": {"points": [(_date(2026, 9, 16), 0.6620), (_date(2026, 10, 16), 0.6630)],
                                  "columns": ["Tenor", "Settlement Date", "Bid", "Ask"], "error": ""},
                "USDJPY Curncy": {"points": [], "columns": [], "error": "securityError: not authorised"}}

    monkeypatch.setattr(pm, "fetch_reference", fake_fetch_reference)
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", fake_curves)
    live_day = _date(2026, 8, 20)   # injected "today"
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=live_day)
    # no shared workbook maturity request any more: only one FWD_OUTRIGHT per pair,
    # at its own open settle date (BUILD_PLAN.md section 2)
    assert status["connected"] is True and status["requested"] == 4 and status["as_of_marks"] == "2026-08-20"
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    assert by[("AUDUSD", "SPOT", status["as_of_marks"])]["status"] == "OK"
    assert by[("AUDUSD", "SPOT", status["as_of_marks"])]["value"] == 0.6612
    assert by[("USDJPY", "SPOT", status["as_of_marks"])] == {**by[("USDJPY", "SPOT", status["as_of_marks"])],
                                                             "status": "FAILED", "detail": "no PX_LAST returned"}
    jpy_fwd = by[("USDJPY", "FWD_OUTRIGHT", "2026-09-18")]
    assert jpy_fwd["status"] == "FAILED" and "not authorised" in jpy_fwd["detail"]
    aud_exact = by[("AUDUSD", "FWD_OUTRIGHT", "2026-09-16")]
    assert aud_exact["status"] == "OK" and aud_exact["value"] == 0.6620 and aud_exact["source"] == "BBG_BFXFORWARD"
    assert status["failed"] == 2 and status["written"] == 2
    # plus Bloomberg's own AUDUSD tenor point at 2026-10-16, official at its own date (2026-09-18),
    # reported separately so "written N of M requested" stays exact
    assert status["curve_points_written"] == 1
    marks = conn.execute("SELECT instrument_id, mark_type, value, source FROM marks ORDER BY mark_type, settle_date").fetchall()
    assert ("AUDUSD", "SPOT", 0.6612, "BBG_BFXFORWARD") in marks and len(marks) == 3
    assert ("AUDUSD", "FWD_OUTRIGHT", 0.6630, "BBG_BFXFORWARD") in marks
    # the ladder now sees the live spot, and USDJPY is simply missing (never invented)
    rates = live.rates_from_marks(conn)
    assert rates["AUD"]["rate"] == 0.6612 and "JPY" not in rates
    # realisation ran through the guarded engine.pnl.ledger.realise_settled call, not a snapshot write
    assert "ledger" in status and "error" not in status["ledger"]
    # second cycle replaces rather than duplicates (same primary key, new snapped_at)
    live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=live_day)
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type='SPOT'").fetchone()[0] == 1


def test_pull_once_includes_future_px_via_px_settle_fallback_when_no_live_px_last(tmp_path, monkeypatch):
    """live.py always calls build_future_rows(live=True): with no PX_LAST at all this
    cycle (fetch_reference -> {}), it must fall back to the latest PX_SETTLE, not just
    report MISSING (2026-09-17 fix)."""
    p, conn = _db(tmp_path)
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',6*50*7528.25,'2026-08-10','2026-09-18',0,0)")
    conn.commit()
    from data.bloomberg import pull_marks as pm
    from datetime import date as _date

    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {})
    monkeypatch.setattr(pm, "fetch_historical",
                        lambda session, service, tickers, field, as_of, diag=None, tag=None, start=None:
                        {"ESU6 Index": 7598.5})
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    from data.bloomberg import fwd_curve
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=_date(2026, 8, 20))
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    fut = by[("ESU6 Index", "FUTURE_PX", "2026-09-18")]
    assert fut["status"] == "OK" and fut["value"] == 7598.5 and fut["source"] == "BBG_BDH"
    assert "PX_SETTLE" in fut["detail"] and "no live PX_LAST" in fut["detail"]
    row = conn.execute("SELECT value, source FROM marks WHERE instrument_id='ESU6 Index'").fetchone()
    assert row == (7598.5, "BBG_BDH")


def test_pull_once_future_px_uses_live_px_last_when_available(tmp_path, monkeypatch):
    """The normal case (intraday, after the future has traded today): PX_LAST is used
    directly, never falling back to a historical request at all."""
    p, conn = _db(tmp_path)
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',6*50*7528.25,'2026-08-10','2026-09-18',0,0)")
    conn.commit()
    from data.bloomberg import pull_marks as pm
    from datetime import date as _date

    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {"ESU6 Index": {"PX_LAST": 7601.0}})

    def boom(*a, **k):
        raise AssertionError("fetch_historical must not be called when live PX_LAST is available")

    monkeypatch.setattr(pm, "fetch_historical", boom)
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    from data.bloomberg import fwd_curve
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=_date(2026, 8, 20))
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    fut = by[("ESU6 Index", "FUTURE_PX", "2026-09-18")]
    assert fut["status"] == "OK" and fut["value"] == 7601.0 and fut["source"] == "BBG_BDH"
    assert fut["detail"] == "live PX_LAST"


def test_pull_once_exception_is_reported_not_raised(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    from data.bloomberg import pull_marks as pm
    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()))
    assert status["connected"] is False and "boom" in status["reason"] and "traceback" in status


def test_cli_status_exit_code(tmp_path, capsys):
    p, conn = _db(tmp_path)
    live.write_status(p, {"time": "t", "connected": True, "reason": "", "requested": 1, "written": 1, "failed": 0,
                          "items": [{"instrument_id": "AUDUSD", "mark_type": "SPOT", "settle_date": "2026-09-14",
                                     "status": "OK", "value": 0.66, "source": "BBG_BFXFORWARD", "detail": ""}]})
    assert live.main(["--db", str(p), "--status"]) == 0
    out = capsys.readouterr().out
    assert "OK     AUDUSD" in out and "0.66000000" in out
    live.write_status(p, {"connected": False, "reason": "blpapi is not installed", "items": []})
    assert live.main(["--db", str(p), "--status"]) == 1


# --------------------------------------------------------------------------- rates step (2026-09-17)
def test_pull_once_prices_irs_from_injected_rates_source(tmp_path):
    """With only an IRS on the book (no FX requests) pull_once still pulls the OIS curve
    and fixings through the injected source, prices the swap and writes QL_PRICER marks."""
    from pathlib import Path
    from data.bloomberg.rates_marketdata import RatesFileSource
    from datetime import date as _date

    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'IRSOIS-USD-1','2031-08-19')")
    conn.execute("INSERT INTO trades VALUES ('s1','XLSX','IRSOIS-USD-1','IRS','s1','2026-08-14',10000000,0.041,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('s1',1,'FIXED','USD',-10000000,'2026-08-14','2031-08-19',0.041,0)")
    conn.execute("INSERT INTO trade_legs VALUES ('s1',2,'FLOAT','USD',10000000,'2026-08-14','2031-08-19',0.0,0)")
    conn.commit()
    fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "ois_snapshot_v1.json"
    source = RatesFileSource(fixture)
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()),
                            today=_date(2026, 8, 17), rates_source=source)
    assert status["connected"] is True
    rates = status["rates"]
    assert rates["currencies"]["USD"]["quotes"] >= 4 and rates["currencies"]["USD"]["fixings"] == 2
    assert rates["priced"] == 1 and rates["failed"] == []
    assert status["options"]["skipped"] == "no FX_OPTION trades to price"
    marks = dict(conn.execute("SELECT mark_type, source FROM marks WHERE instrument_id = 'IRSOIS-USD-1'").fetchall())
    assert marks == {"PV_USD": "QL_PRICER", "DV01_USD": "QL_PRICER", "CASHFLOW_USD": "QL_PRICER", "PAR_RATE": "QL_PRICER"}
    assert conn.execute('SELECT COUNT(*) FROM index_fixings WHERE "index" = \'SOFR\'').fetchone()[0] == 2


def test_pull_once_rates_step_reports_per_currency_failure_not_raise(tmp_path):
    from datetime import date as _date

    class Broken:
        def get_curve_quotes(self, ccy, as_of):
            raise RuntimeError("terminal down")

        def get_fixings(self, ccy, start, end):
            return []

    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'IRSOIS-USD-1','2031-08-19')")
    conn.execute("INSERT INTO trades VALUES ('s1','XLSX','IRSOIS-USD-1','IRS','s1','2026-08-14',10000000,0.041,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('s1',1,'FIXED','USD',-10000000,'2026-08-14','2031-08-19',0.041,0)")
    conn.commit()
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()),
                            today=_date(2026, 8, 17), rates_source=Broken())
    assert status["connected"] is True
    assert "terminal down" in status["rates"]["currencies"]["USD"]["error"]
    assert status["rates"]["priced"] == 0 and status["rates"]["failed"][0]["trade_id"] == "s1"
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


# --------------------------------------------------------------------------- rates/vol for FX_OPTION currencies (2026-09-17)
def _option_db(tmp_path, base="EUR", quote="SEK", option_id="EURSEK091826C-1", pair_ticker="EURSEK Curncy",
              expiry="2026-09-18"):
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.execute(f"INSERT INTO instruments VALUES ('{option_id}','FX_OPTION','{base}','{quote}',1,0,"
                f"'{pair_ticker}','{expiry}')")
    conn.execute(f"INSERT INTO trades VALUES ('o1','XLSX','{option_id}','FX_OPTION','o1','2026-08-14',"
                f"1000000,0.01,'acc','cp','HAHY7','t','d','')")
    conn.execute(f"INSERT INTO trade_legs VALUES ('o1',1,'NOTIONAL','{base}',1000000,'2026-08-14','{expiry}',0,0)")
    conn.commit()
    return p, conn


def test_rates_step_pulls_curves_for_option_currencies_with_no_irs_at_all(tmp_path):
    """2026-09-17 fix: an FX_OPTION-only book (no IRS trade anywhere) must still get its
    pair's currencies' OIS curves pulled -- engine/options/inputs.py needs a domestic AND
    a foreign discount curve per option (resolve_fx_rates)."""
    from data.bloomberg.rates_marketdata import RatesFileSource
    from pathlib import Path
    from datetime import date as _date

    p, conn = _option_db(tmp_path)  # EUR/SEK: EUR in Phase 1 OIS scope, SEK is not
    fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "ois_snapshot_v1.json"
    out = live._rates_step(conn, _date(2026, 8, 17), "localhost", 8194, rates_source=RatesFileSource(fixture))
    assert out["currencies"]["EUR"]["quotes"] > 0 and out["currencies"]["EUR"]["error"] == ""
    assert out["currencies"]["SEK"]["quotes"] == 0
    assert "no OIS index in Phase 1 scope" in out["currencies"]["SEK"]["error"]
    assert conn.execute("SELECT COUNT(*) FROM curve_quotes WHERE ccy = 'EUR'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM curve_quotes WHERE ccy = 'SEK'").fetchone()[0] == 0


def test_rates_step_skipped_message_when_neither_irs_nor_option(tmp_path):
    from datetime import date as _date
    p, conn = _db(tmp_path)  # AUDUSD/USDJPY forwards only, no IRS, no FX_OPTION
    out = live._rates_step(conn, _date(2026, 8, 17), "localhost", 8194, rates_source=object())
    assert out["skipped"] == "no IRS or FX_OPTION trades to price"


def test_vol_step_writes_vol_quotes_for_option_pairs_via_injected_source(tmp_path):
    from data.bloomberg.vol_marketdata import VolFileSource
    from pathlib import Path
    from datetime import date as _date

    p, conn = _option_db(tmp_path, base="EUR", quote="USD", option_id="EURUSD091826C-1", pair_ticker="EURUSD Curncy")
    fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "fx_vol_snapshot_v1.json"
    out = live._vol_step(conn, _date(2026, 9, 17), "localhost", 8194, vol_source=VolFileSource(fixture))
    assert out["written"] > 0
    assert out["pairs"].get("EURUSD", 0) > 0
    assert conn.execute("SELECT COUNT(*) FROM vol_quotes WHERE pair = 'EURUSD'").fetchone()[0] > 0


def test_vol_step_records_ticker_checks_so_the_live_pull_is_the_probe(tmp_path):
    """2026-09-18: the live vol pull itself now doubles as the UNVERIFIED ticker/field
    probe -- every cycle with an open option must persist a vol_ticker_checks verdict per
    assumption, so tools/bbg_diagnostics.py can report a real PASS/FAIL instead of always
    pointing the user at --probe."""
    from data.bloomberg import vol_marketdata as vm
    from pathlib import Path
    from datetime import date as _date

    p, conn = _option_db(tmp_path, base="EUR", quote="USD", option_id="EURUSD091826C-1", pair_ticker="EURUSD Curncy")
    fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "fx_vol_snapshot_v1.json"
    out = live._vol_step(conn, _date(2026, 9, 17), "localhost", 8194, vol_source=vm.VolFileSource(fixture))
    assert out["ticker_checks_recorded"] == len(vm.ALL_ASSUMPTION_IDS)
    checked = vm.read_vol_ticker_checks(conn)
    assert set(checked) == set(vm.ALL_ASSUMPTION_IDS)
    assert all(c["outcome"] == "OK" for c in checked.values())


def test_vol_step_skipped_when_no_open_option(tmp_path):
    from datetime import date as _date
    p, conn = _db(tmp_path)
    out = live._vol_step(conn, _date(2026, 8, 17), "localhost", 8194, vol_source=object())
    assert out["skipped"] == "no FX_OPTION trades to price"


def test_options_step_enriches_no_vol_reason_with_failing_ticker(tmp_path, monkeypatch):
    """3c: a "no vol" skip must name the specific Bloomberg ticker(s) that failed, from
    the vol step's own per-ticker diagnostics, so the user knows exactly what to check
    with `py -3 -m data.bloomberg.vol_marketdata --probe`."""
    from datetime import date as _date
    p, conn = _option_db(tmp_path)

    class _Outcome:
        priced = False
        trade_id = "o1"
        skip_reason = "no vol"

    monkeypatch.setattr("engine.options.store.price_all_and_store", lambda conn_, as_of: [_Outcome()])
    vol_diag = [{"pair": "EURSEK", "tenor": "1M", "quote_type": "ATM", "ticker": "EURSEKV1M BGN Curncy",
                "status": "MISSING", "detail": "no value returned for this ticker"}]
    out = live._options_step(conn, _date(2026, 8, 17), vol_diagnostics=vol_diag)
    assert out["priced"] == 0
    assert len(out["skipped"]) == 1
    reason = out["skipped"][0]["reason"]
    assert "no vol" in reason and "EURSEKV1M BGN Curncy" in reason
    assert "vol_marketdata --probe" in reason


def test_options_step_leaves_other_skip_reasons_and_missing_diagnostics_unchanged(tmp_path, monkeypatch):
    from datetime import date as _date
    p, conn = _option_db(tmp_path)

    class _NoSpot:
        priced = False
        trade_id = "o1"
        skip_reason = "no SPOT mark"

    monkeypatch.setattr("engine.options.store.price_all_and_store", lambda conn_, as_of: [_NoSpot()])
    # A "no vol" diagnostic exists for a DIFFERENT pair -- must not be applied here, and a
    # non-"no vol" reason must never be rewritten at all.
    vol_diag = [{"pair": "USDJPY", "ticker": "USDJPYV1M BGN Curncy", "status": "MISSING", "detail": "x"}]
    out = live._options_step(conn, _date(2026, 8, 17), vol_diagnostics=vol_diag)
    assert out["skipped"] == [{"trade_id": "o1", "reason": "no SPOT mark"}]
    # No vol_diagnostics at all: a "no vol" reason is returned as-is, not garbled.
    monkeypatch.setattr("engine.options.store.price_all_and_store",
                        lambda conn_, as_of: [type("O", (), {"priced": False, "trade_id": "o1", "skip_reason": "no vol"})()])
    out2 = live._options_step(conn, _date(2026, 8, 17), vol_diagnostics=None)
    assert out2["skipped"] == [{"trade_id": "o1", "reason": "no vol"}]


def test_pull_once_option_only_book_requests_its_pair_and_pulls_curves_and_vol(tmp_path, monkeypatch):
    """The exact Bloomberg-PC scenario (2026-09-17): an FX_OPTION-only book (no direct FX
    forward/spot trade, no IRS) must still pull OIS curves and vol quotes for the option's
    pair currencies. Since 2026-09-18 such a book no longer takes the early-return branch
    at all: build_requests asks Bloomberg for the option pair's own SPOT and a forward at
    the option's expiry (engine/options needs both), so this runs the main path."""
    from data.bloomberg.rates_marketdata import RatesFileSource
    from data.bloomberg.vol_marketdata import VolFileSource
    from data.bloomberg import pull_marks as pm, fwd_curve
    from pathlib import Path
    from datetime import date as _date

    p, conn = _option_db(tmp_path, base="EUR", quote="USD", option_id="EURUSD091826C-1", pair_ticker="EURUSD Curncy")
    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {"EURUSD Curncy": {"PX_LAST": 1.17}})
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    rates_fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "ois_snapshot_v1.json"
    vol_fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "fx_vol_snapshot_v1.json"
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()),
                            today=_date(2026, 8, 17), rates_source=RatesFileSource(rates_fixture),
                            vol_source=VolFileSource(vol_fixture))
    assert status["connected"] is True
    keys = {(i["instrument_id"], i["mark_type"], i["settle_date"]) for i in status["items"]}
    assert ("EURUSD", "SPOT", "2026-08-17") in keys and ("EURUSD", "FWD_OUTRIGHT", "2026-09-18") in keys
    assert conn.execute("SELECT value FROM marks_official WHERE instrument_id='EURUSD' AND mark_type='SPOT'"
                        ).fetchone()[0] == 1.17
    assert status["rates"]["currencies"]["EUR"]["quotes"] > 0
    assert status["vol"]["pairs"].get("EURUSD", 0) > 0
    assert conn.execute("SELECT COUNT(*) FROM curve_quotes WHERE ccy='EUR'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM vol_quotes WHERE pair='EURUSD'").fetchone()[0] > 0


def test_pull_once_defaults_book_date_to_today_not_last_bnp_snapshot(tmp_path, monkeypatch):
    """2026-09-17 fix: with no as_of given, the request list is built as of the live
    mark date, not MAX(positions.as_of_date) (the retired BNP snapshot, 2026-08-17 in
    this fixture), which silently dropped every trade dated after that snapshot."""
    p, conn = _db(tmp_path)
    seen = {}

    def fake_build_requests(conn_, as_of):
        seen["as_of"] = as_of
        return []

    monkeypatch.setattr(live, "build_requests", fake_build_requests)
    from datetime import date as _date
    status = live.pull_once(p, session_factory=lambda: (object(), object()), today=_date(2026, 9, 17))
    assert seen["as_of"] == "2026-09-17"
    assert status["as_of_date"] == "2026-09-17"


# --------------------------------------------------------------------------- availability() speed (2026-09-17)
def _with_fake_blpapi(monkeypatch):
    monkeypatch.setitem(sys.modules, "blpapi", types.ModuleType("blpapi"))


def test_availability_resolves_localhost_to_127_0_0_1(monkeypatch):
    """Dual-stack 'localhost' (::1 + 127.0.0.1) made a single availability() check pay up
    to ~2s when nothing answers on ::1 -- found on the Bloomberg PC 2026-09-17, and doubled
    again because start_auto_backfill probes the same host:port a moment later inside the
    same create_app(start_feed=True) call. availability() must connect to 127.0.0.1
    directly rather than leave resolution of the literal string 'localhost' to the OS."""
    _with_fake_blpapi(monkeypatch)
    seen = []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_create_connection(addr, timeout=None):
        seen.append((addr, timeout))
        return _FakeConn()

    monkeypatch.setattr("socket.create_connection", fake_create_connection)
    ok, why = live.availability("localhost", 8194)
    assert ok is True and why == ""
    assert seen == [(("127.0.0.1", 8194), 0.5)]


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_availability_leaves_explicit_non_localhost_host_untouched(monkeypatch):
    _with_fake_blpapi(monkeypatch)
    seen = []

    def fake_create_connection(addr, timeout=None):
        seen.append((addr, timeout))
        return _FakeConn()

    monkeypatch.setattr("socket.create_connection", fake_create_connection)
    live.availability("bpipe.example.com", 8194)
    assert seen == [(("bpipe.example.com", 8194), 0.5)]


def test_availability_default_timeout_is_half_a_second(monkeypatch):
    _with_fake_blpapi(monkeypatch)
    seen = []

    def fake_create_connection(addr, timeout=None):
        seen.append(timeout)
        return _FakeConn()

    monkeypatch.setattr("socket.create_connection", fake_create_connection)
    live.availability()
    assert seen == [0.5]


def test_availability_no_blpapi_never_touches_the_socket(monkeypatch):
    monkeypatch.delitem(sys.modules, "blpapi", raising=False)  # not installed on this dev PC

    def boom(*a, **k):
        raise AssertionError("socket.create_connection must not be called when blpapi is not installed")

    monkeypatch.setattr("socket.create_connection", boom)
    ok, why = live.availability()
    assert ok is False and "blpapi is not installed" in why


# --------------------------------------------------------------------------- LiveFeed.trigger_now() (2026-09-17)
def test_trigger_now_wakes_the_loop_immediately_instead_of_waiting_the_full_interval(tmp_path, monkeypatch):
    """Root-cause fix: without this, a blotter uploaded moments after the app starts is
    not priced until the next scheduled pull (up to INTERVAL_SECONDS later). A caller
    (ui/uploads.py, after import_blotter) is expected to call feed.trigger_now()."""
    p, conn = _db(tmp_path)
    calls = []

    def fake_pull_once(db_path, host="localhost", port=8194):
        calls.append(1)
        return {"connected": True, "requested": 0, "written": 0, "failed": 0, "items": [], "warnings": []}

    monkeypatch.setattr(live, "pull_once", fake_pull_once)
    monkeypatch.setattr("data.bloomberg.backfill.start_auto_backfill", lambda *a, **k: None)
    feed = live.LiveFeed(p, interval=60)
    feed.start()
    try:
        for _ in range(200):  # wait for the first (immediate) cycle
            if calls:
                break
            import time as _t
            _t.sleep(0.01)
        assert calls == [1]
        feed.trigger_now()  # must not require waiting out the 60s interval
        for _ in range(200):
            if len(calls) >= 2:
                break
            import time as _t
            _t.sleep(0.01)
        assert len(calls) >= 2
    finally:
        feed.stop()


def test_stop_also_wakes_a_waiting_loop(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    monkeypatch.setattr(live, "pull_once", lambda *a, **k: {"connected": False, "reason": "x", "requested": 0,
                                                             "written": 0, "failed": 0, "items": [], "warnings": []})
    monkeypatch.setattr("data.bloomberg.backfill.start_auto_backfill", lambda *a, **k: None)
    feed = live.LiveFeed(p, interval=120)
    feed.start()
    feed.stop()
    feed._thread.join(timeout=5)
    assert not feed._thread.is_alive()


# --------------------------------------------------------------------------- 2026-09-18 audit fixes
def test_build_requests_includes_option_pair_spot_and_expiry_forward(tmp_path):
    """An open FX_OPTION needs its PAIR's SPOT (engine/options/inputs.get_spot) and a
    FWD_OUTRIGHT at its own expiry (covered-interest-parity rate for a currency with no
    OIS curve); until 2026-09-18 neither was ever requested for an option-only pair, and a
    pair with no `instruments` row could not even have the mark written."""
    p, conn = _db(tmp_path)
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("USDJPY111926P-1", "FX_OPTION", "USD", "JPY", 1, 0, "USDJPY111926P-1", "2026-11-19"),
        ("USDMXN120126C-2", "FX_OPTION", "USD", "MXN", 1, 0, "USDMXN120126C-2", "2026-12-01"),
        ("USDBRL120126C-3", "FX_OPTION", "USD", "BRL", 1, 0, "USDBRL120126C-3", "2026-12-01"),
    ])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("o1", "XLSX", "USDJPY111926P-1", "FX_OPTION", "o1", "2026-08-19", 1e6, 0.1425, "acc", "cp", "", "t", "d", ""),
        ("o2", "XLSX", "USDMXN120126C-2", "FX_OPTION", "o2", "2026-08-19", 1e6, 0.01, "acc", "cp", "", "t", "d", ""),
        ("o3", "XLSX", "USDBRL120126C-3", "FX_OPTION", "o3", "2026-08-19", 1e6, 0.01, "acc", "cp", "", "t", "d", ""),
    ])
    conn.commit()
    # 2026-10-01: every FX forward leg in _db has settled, so only the options drive requests
    keys = {(r.instrument_id, r.mark_type, r.settle_date) for r in live.build_requests(conn, "2026-10-01")}
    assert ("USDJPY", "SPOT", "2026-10-01") in keys and ("USDJPY", "FWD_OUTRIGHT", "2026-11-19") in keys
    assert ("USDMXN", "SPOT", "2026-10-01") in keys and ("USDMXN", "FWD_OUTRIGHT", "2026-12-01") in keys
    assert ("USDBRL", "SPOT", "2026-10-01") in keys
    # the USDMXN / USDBRL pair rows did not exist: created as plain FX instruments, NDF flag per currency
    rows = {r[0]: r for r in conn.execute(
        "SELECT instrument_id, asset_class, base_ccy, quote_ccy, is_ndf, bbg_ticker FROM instruments "
        "WHERE instrument_id IN ('USDMXN', 'USDBRL')")}
    assert rows["USDMXN"][1:] == ("FX", "USD", "MXN", 0, "USDMXN Curncy")
    assert rows["USDBRL"][4] == 1
    # an expired option drives nothing
    later = {r.instrument_id for r in live.build_requests(conn, "2026-11-20")}
    assert "USDJPY" not in later and {"USDMXN", "USDBRL"} <= later


def test_pull_once_stops_the_session_it_opened(tmp_path, monkeypatch):
    """One blpapi session per 2-minute cycle (and per Pull-now click) was never stopped
    before 2026-09-18 -- cleanup was left to garbage collection."""
    p, conn = _db(tmp_path)
    from data.bloomberg import pull_marks as pm, fwd_curve
    from datetime import date as _date
    stopped = []

    class _Session:
        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(live, "availability", lambda host, port: (True, ""))
    monkeypatch.setattr(pm, "open_session", lambda host, port, diag=None: (_Session(), object()))
    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {})
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    status = live.pull_once(p, "2026-08-17", today=_date(2026, 8, 20))
    assert status["connected"] is True
    assert stopped == [True]


def test_pull_once_writes_fwd_curve_tenor_points_as_official_forwards(tmp_path, monkeypatch):
    """Bloomberg's own FWD_CURVE points are quoted outrights, so they are written as
    official BBG_BFXFORWARD FWD_OUTRIGHT rows at their own tenor dates (2026-09-18) --
    what engine/options' implied-rate path needs for a currency with no OIS curve. A
    leg's own broken date is interpolated (BBG_INTERP) and, since the user's 2026-09-18
    decision, official as the FWD_OUTRIGHT fallback -- so the leg prices."""
    p, conn = _db(tmp_path)
    from data.bloomberg import pull_marks as pm, fwd_curve
    from datetime import date as _date

    def fake_fetch_reference(session, service, tickers, fields, overrides=None, diag=None, tag=None):
        return {"AUDUSD Curncy": {"PX_LAST": 0.6612}, "USDJPY Curncy": {"PX_LAST": 150.0}}

    def fake_curves(blpapi, session, service, tickers, timeout_ms=15000):
        return {"AUDUSD Curncy": {"points": [(_date(2026, 8, 24), 0.6615), (_date(2026, 9, 21), 0.6620),
                                             (_date(2026, 10, 20), 0.6630)], "columns": [], "error": ""},
                "USDJPY Curncy": {"points": [(_date(2026, 8, 24), 149.9), (_date(2026, 9, 18), 149.5),
                                             (_date(2026, 10, 20), 149.0)], "columns": [], "error": ""}}

    monkeypatch.setattr(pm, "fetch_reference", fake_fetch_reference)
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", fake_curves)
    status = live.pull_once(p, "2026-08-20", session_factory=lambda: (object(), object()), today=_date(2026, 8, 20))
    assert status["connected"] is True
    # requested: 2 SPOT + AUDUSD fwd 2026-09-16 (INTERP) + USDJPY fwd 2026-09-18 (EXACT); all written
    assert status["requested"] == 4 and status["written"] == 4 and status["failed"] == 0
    # 3 AUD + 3 JPY tenor points, minus the JPY 2026-09-18 point that was itself a requested mark
    assert status["curve_points_written"] == 5
    official = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT instrument_id, settle_date, value FROM marks_official "
        "WHERE mark_type='FWD_OUTRIGHT' AND as_of_date='2026-08-20'")}
    assert official[("AUDUSD", "2026-10-20")] == 0.6630 and official[("USDJPY", "2026-09-18")] == 149.5
    assert ("AUDUSD", "2026-09-16") in official
    assert conn.execute("SELECT source FROM marks_official WHERE instrument_id='AUDUSD' AND settle_date='2026-09-16'"
                        ).fetchone()[0] == "BBG_INTERP"


def test_write_status_is_atomic_and_patch_status_merges_under_the_lock(tmp_path):
    p = tmp_path / "risk.db"
    live.write_status(p, {"connected": True, "written": 3, "items": []})
    merged = live.patch_status(p, "backfill", {"running": True, "remaining": 4})
    assert merged["backfill"] == {"running": True, "remaining": 4} and merged["written"] == 3
    live.patch_status(p, "backfill", {"remaining": 3})
    on_disk = live.read_status(p)
    assert on_disk["backfill"] == {"running": True, "remaining": 3} and on_disk["connected"] is True
    assert not list(tmp_path.glob("*.tmp"))


def test_option_pair_forward_curve_gives_the_implied_rate_fallback_its_official_points(tmp_path, monkeypatch):
    """The Bloomberg-PC failure of 2026-09-17: three EURSEK options skipped "no curve/rate
    SEK" although engine/options/rates.py can imply SEK from EUR's OIS curve and the EURSEK
    forward -- every EURSEK forward on file was a broken date (BBG_INTERP only), so the
    fallback had no official point to read. The pull must fetch the FWD_CURVE for an open
    option's pair even with no FX leg in it, store its tenors as official, and the option's
    expiry (between two tenors) must then resolve an IMPLIED_FORWARD SEK rate end to end."""
    from data.bloomberg.rates_marketdata import RatesFileSource
    from data.bloomberg import pull_marks as pm, fwd_curve
    from engine.options.rates import resolve_fx_rates, IMPLIED_FORWARD, OIS_CURVE
    from pathlib import Path
    from datetime import date as _date

    p, conn = _option_db(tmp_path, option_id="EURSEK092326C-1", expiry="2026-09-23")   # EUR/SEK, no pair row
    fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "ois_snapshot_v1.json"
    seen = {}

    def fake_curves(blpapi, session, service, tickers, timeout_ms=15000):
        seen["tickers"] = list(tickers)
        return {"EURSEK Curncy": {"points": [(_date(2026, 9, 21), 11.05), (_date(2026, 9, 28), 11.06)],
                                  "columns": [], "error": ""}}

    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {"EURSEK Curncy": {"PX_LAST": 11.04}})
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", fake_curves)
    today = _date(2026, 8, 17)   # the OIS fixture's own as_of
    status = live.pull_once(p, today.isoformat(), session_factory=lambda: (object(), object()), today=today,
                            rates_source=RatesFileSource(fixture))
    assert status["connected"] is True
    assert "EURSEK Curncy" in seen["tickers"]
    official_dates = {r[0] for r in conn.execute(
        "SELECT settle_date FROM marks_official WHERE as_of_date=? AND instrument_id='EURSEK' "
        "AND mark_type='FWD_OUTRIGHT'", (today.isoformat(),))}
    assert {"2026-09-21", "2026-09-28"} <= official_dates
    assert "2026-09-23" in official_dates                         # the broken expiry: BBG_INTERP, official as the fallback
    rates, reason = resolve_fx_rates(conn, today.isoformat(), "EURSEK", "2026-09-23")
    assert reason == "" and rates is not None
    assert rates.foreign_rate_source.source_kind == OIS_CURVE          # EUR: the real ESTR curve
    assert rates.domestic_rate_source.source_kind == IMPLIED_FORWARD   # SEK: implied, no manual entry needed


# --------------------------------------------------------------------------- options' own USD-conversion spots (2026-09-18)
# An FX option's value and P&L are in the pair's BASE currency and convert to USD at the
# base->USD SPOT of the same as_of (CLAUDE.md; the EURSEK digital pays EUR); its vega /
# theta / rho convert through quote->USD. Only forwards used to drive USD-conversion SPOT
# requests, so a book holding just the EURSEK options asked for EURSEK SPOT and the EURSEK
# forwards at expiry and nothing else -- the reproduction below.
_EURSEK_OPTIONS = [   # the reference book's EURSEK options: two 23 Sep vanillas and the 25 Nov digital
    ("o1", "EURSEK092326C-197727826", "2026-08-21", 35e6, "2026-09-23"),
    ("o2", "EURSEK092326P-197838147", "2026-08-24", -35e6, "2026-09-23"),
    ("o3", "EURSEK112526C-197906813", "2026-08-24", 1e6, "2026-11-25"),
]


def _options_only_db(tmp_path, options, base="EUR", quote="SEK"):
    """A book holding ONLY FX options: no forward, no spot trade, and no plain pair
    instrument row at all (not the option's pair, not a USD-conversion pair)."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    for trade_id, option_id, trade_date, qty, expiry in options:
        conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                     (option_id, "FX_OPTION", base, quote, 1, 0, option_id, expiry))
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (trade_id, "XLSX", option_id, "FX_OPTION", trade_id, trade_date, qty, 0.01,
                      "acc", "cp", "", "t", "d", ""))
        conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                     (trade_id, 1, "NOTIONAL", base, qty, trade_date, expiry, 0, 0))
    conn.commit()
    return p, conn


def test_build_requests_eursek_options_only_book_requests_both_usd_conversion_spots_once(tmp_path):
    p, conn = _options_only_db(tmp_path, _EURSEK_OPTIONS)
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")   # the forwards' own orientation helper
    assert live.option_spot_pair_names("EUR", "SEK") == ["EURSEK", eur_usd, usd_sek]
    reqs = live.build_requests(conn, "2026-09-18")
    keys = [(r.instrument_id, r.mark_type, r.settle_date) for r in reqs]
    assert keys == [("EURSEK", "SPOT", "2026-09-18"),
                    ("EURSEK", "FWD_OUTRIGHT", "2026-09-23"), ("EURSEK", "FWD_OUTRIGHT", "2026-11-25"),
                    (eur_usd, "SPOT", "2026-09-18"), (usd_sek, "SPOT", "2026-09-18")]      # each exactly once
    by_id = {r.instrument_id: r for r in reqs if r.mark_type == "SPOT"}
    assert by_id[eur_usd].bbg_ticker == f"{eur_usd} Curncy" and by_id[usd_sek].bbg_ticker == f"{usd_sek} Curncy"
    # SPOT only for a conversion pair: never a forward, never anything else
    assert {r.mark_type for r in reqs if r.instrument_id in (eur_usd, usd_sek)} == {"SPOT"}
    # the plain pair rows a written mark needs, created by the existing _ensure_fx_instruments
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT instrument_id, asset_class, base_ccy, quote_ccy, bbg_ticker, expiry_date FROM instruments "
        "WHERE asset_class = 'FX'")}
    assert rows == {"EURSEK": ("FX", "EUR", "SEK", "EURSEK Curncy", "9999-12-31"),
                    eur_usd: ("FX", eur_usd[:3], eur_usd[3:], f"{eur_usd} Curncy", "9999-12-31"),
                    usd_sek: ("FX", usd_sek[:3], usd_sek[3:], f"{usd_sek} Curncy", "9999-12-31")}
    # The expiry day itself still counts as open (`expiry_date >= as_of`): that day's
    # payoff mark needs that day's SPOT of the pair AND of the base->USD pair.
    expiry_day = {(r.instrument_id, r.mark_type) for r in live.build_requests(conn, "2026-11-25")}
    assert {("EURSEK", "SPOT"), (eur_usd, "SPOT"), (usd_sek, "SPOT")} <= expiry_day
    assert live.build_requests(conn, "2026-11-26") == []                       # the day after: nothing open


def test_build_requests_usdjpy_only_option_book_requests_usdjpy_spot_and_no_usdusd(tmp_path):
    """USD is the base: no base->USD conversion exists, and quote->USD is the option's own
    pair, already requested -- one USDJPY SPOT, never a 'USDUSD'."""
    p, conn = _options_only_db(tmp_path, [("o1", "USDJPY111926P-197571137", "2026-08-19", 1e6, "2026-11-19")],
                               base="USD", quote="JPY")
    assert live.option_spot_pair_names("USD", "JPY") == [live._usd_pair_name("JPY")] == ["USDJPY"]
    reqs = live.build_requests(conn, "2026-09-18")
    assert [(r.instrument_id, r.mark_type, r.settle_date) for r in reqs] == [
        ("USDJPY", "SPOT", "2026-09-18"), ("USDJPY", "FWD_OUTRIGHT", "2026-11-19")]
    assert not any("USDUSD" in r.instrument_id or "USDUSD" in r.bbg_ticker for r in reqs)
    assert [r[0] for r in conn.execute("SELECT instrument_id FROM instruments WHERE asset_class = 'FX'")] == ["USDJPY"]


def test_build_requests_option_conversion_spot_not_duplicated_when_a_forward_already_asks_for_it(tmp_path):
    """De-duplicated against every request already made: a direct forward in the
    conversion pair, a cross's own conversion legs, or another option's own pair."""
    p, conn = _cross_db(tmp_path, with_usd_leg_instruments=True)     # a EURSEK forward: asks for EURUSD + USDSEK SPOT
    conn.execute("INSERT INTO instruments VALUES ('EURSEK112526C-3','FX_OPTION','EUR','SEK',1,0,'x','2026-11-25')")
    conn.execute("INSERT INTO trades VALUES ('o3','XLSX','EURSEK112526C-3','FX_OPTION','o3','2026-08-10',1e6,0.01,"
                 "'acc','cp','','t','d','')")
    conn.commit()
    spot_ids = [r.instrument_id for r in live.build_requests(conn, "2026-08-17") if r.mark_type == "SPOT"]
    assert sorted(spot_ids) == sorted({"EURSEK", live._usd_pair_name("EUR"), live._usd_pair_name("SEK")})
    # once the forward has settled the option alone keeps all three alive
    later = [r.instrument_id for r in live.build_requests(conn, "2026-10-01") if r.mark_type == "SPOT"]
    assert sorted(later) == sorted({"EURSEK", live._usd_pair_name("EUR"), live._usd_pair_name("SEK")})


def test_inventory_names_the_option_conversion_spots_and_stays_in_step_with_build_requests(tmp_path):
    from data.bloomberg import inventory
    p, conn = _options_only_db(tmp_path, _EURSEK_OPTIONS)
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")
    needed = inventory._needed_marks(conn, "2026-09-18")             # read before any instrument row exists
    needed_keys = [(n["instrument_id"], n["mark_type"], n["settle_date"]) for n in needed]
    assert (eur_usd, "SPOT", "2026-09-18") in needed_keys and (usd_sek, "SPOT", "2026-09-18") in needed_keys
    req_keys = [(r.instrument_id, r.mark_type, r.settle_date) for r in live.build_requests(conn, "2026-09-18")]
    assert needed_keys == req_keys                                    # same set, same order, no duplicate
    # the diagnostics panel reads mark_inventory: a missing conversion spot is there BY NAME
    df = inventory.mark_inventory(conn, "2026-09-18")
    status = {(r.instrument_id, r.mark_type): r.status for r in df.itertuples()}
    assert status[(eur_usd, "SPOT")] == "MISSING" and status[(usd_sek, "SPOT")] == "MISSING"
    conn.execute("INSERT INTO marks VALUES ('2026-09-18',?,'2026-09-18','SPOT',1.17,'BBG_BFXFORWARD','t')", (eur_usd,))
    conn.commit()
    status = {(r.instrument_id, r.mark_type): r.status
              for r in inventory.mark_inventory(conn, "2026-09-18").itertuples()}
    assert status[(eur_usd, "SPOT")] == "OFFICIAL" and status[(usd_sek, "SPOT")] == "MISSING"


def test_historical_needed_marks_for_options_are_spot_only_and_keep_a_realised_option(tmp_path):
    """What a PAST close needs for an option (close_completeness, the backfill): the
    closing SPOT of its pair and of its USD-conversion pairs, nothing else, on every day
    it was open including the expiry date -- and still when the ledger has realised it:
    engine/options' catch-up re-freezes an option frozen from an older premium, but only
    once the expiry date's closing SPOT is on file."""
    from data.bloomberg import inventory
    p, conn = _options_only_db(tmp_path, _EURSEK_OPTIONS[:1])          # o1: traded 08-21, expires 09-23
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")
    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
                 "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, "
                 "note) VALUES ('o1','EURSEK092326C-197727826','FX_OPTION','EUR','2026-09-23',35e6,0,'PREMIUM',"
                 "0.01,'2026-09-22','QL_OPTIONS_PRICER',0,'t','premium dated 2026-09-22 (last before expiry)')")
    conn.commit()
    expected = [("EURSEK", "SPOT"), (eur_usd, "SPOT"), (usd_sek, "SPOT")]
    for day in ("2026-08-21", "2026-09-22", "2026-09-23"):             # trade date .. expiry date inclusive
        items = inventory._needed_marks(conn, day, historical=True)
        assert [(i["instrument_id"], i["mark_type"]) for i in items] == expected
        assert all(i["settle_date"] == day for i in items)
    assert inventory._needed_marks(conn, "2026-08-20", historical=True) == []   # not traded yet
    assert inventory._needed_marks(conn, "2026-09-24", historical=True) == []   # expired
    # the live list is unchanged: a realised option drives no live request
    assert inventory._needed_marks(conn, "2026-09-22") == [] and live.build_requests(conn, "2026-09-22") == []


def test_pull_once_writes_option_conversion_spots_where_valuation_and_portfolio_look_them_up(tmp_path, monkeypatch):
    """The written mark must be FOUND: engine/pnl/valuation.usd_per_quote (the option's
    base->USD conversion) and engine/options/portfolio._quote_ccy_to_usd (the Greeks'
    quote->USD conversion) both key marks_official by the plain pair name, trying both
    orientations, so whichever one `_usd_pair_name` yields is the one they read."""
    from data.bloomberg import pull_marks as pm, fwd_curve
    from datetime import date as _date
    from engine.pnl.valuation import usd_per_quote
    from engine.options.portfolio import _quote_ccy_to_usd
    p, conn = _options_only_db(tmp_path, _EURSEK_OPTIONS)
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")
    px = {"EURSEK Curncy": 11.04, f"{eur_usd} Curncy": 1.17, f"{usd_sek} Curncy": 9.44}
    asked = []

    def fake_fetch_reference(session, service, tickers, fields, overrides=None, diag=None, tag=None):
        asked.extend(tickers)
        return {t: {"PX_LAST": px[t]} for t in tickers}

    monkeypatch.setattr(pm, "fetch_reference", fake_fetch_reference)
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    monkeypatch.setattr(live, "_rates_step", lambda *a, **k: {"skipped": "not under test"})
    monkeypatch.setattr(live, "_vol_step", lambda *a, **k: {"skipped": "not under test"})
    monkeypatch.setattr(live, "_options_step", lambda *a, **k: {"skipped": "not under test"})
    status = live.pull_once(p, "2026-09-18", session_factory=lambda: (object(), object()), today=_date(2026, 9, 18))
    assert status["connected"] is True
    assert sorted(asked) == sorted(px)                                  # one SPOT request, each ticker once
    ok = {(i["instrument_id"], i["mark_type"]): i for i in status["items"] if i["status"] == "OK"}
    assert ok[(eur_usd, "SPOT")]["value"] == 1.17 and ok[(usd_sek, "SPOT")]["value"] == 9.44
    assert ok[(eur_usd, "SPOT")]["source"] == "BBG_BFXFORWARD"         # the official SPOT source
    s, pair, source = usd_per_quote(conn, "EUR", "2026-09-18")          # option value / P&L: base -> USD
    assert pair == eur_usd and source == "BBG_BFXFORWARD"
    assert s == pytest.approx(1.17 if eur_usd == "EURUSD" else 1 / 1.17)
    sek_to_usd, reason = _quote_ccy_to_usd(conn, "2026-09-18", "SEK")   # vega / theta / rho: quote -> USD
    assert reason == "" and sek_to_usd == pytest.approx(1 / 9.44 if usd_sek == "USDSEK" else 9.44)


_SAMPLE_CSV = __import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "raw" / "new_sample_trades.csv"


@pytest.mark.skipif(not _SAMPLE_CSV.exists(), reason="data/raw/new_sample_trades.csv is not on this machine")
def test_sample_book_request_list_is_unchanged_except_for_deduplicated_conversion_spots(tmp_path, monkeypatch):
    """Before/after on the real reference book. 'Before' is build_requests with the new
    options' conversion legs switched off -- the only thing this change adds."""
    from data.ingest.upload import import_blotter
    p = tmp_path / "risk.db"
    import_blotter(_SAMPLE_CSV.read_bytes(), _SAMPLE_CSV.name, p)
    conn = schema.connect(p)
    eur_usd, usd_sek = live._usd_pair_name("EUR"), live._usd_pair_name("SEK")

    def before_and_after(as_of):
        with monkeypatch.context() as m:
            m.setattr(live, "_option_usd_legs", lambda conn_, as_of_date, historical=False: [])
            before = [(r.instrument_id, r.mark_type, r.settle_date, r.bbg_ticker) for r in live.build_requests(conn, as_of)]
        after = [(r.instrument_id, r.mark_type, r.settle_date, r.bbg_ticker) for r in live.build_requests(conn, as_of)]
        assert len(after) == len(set(after))                            # nothing requested twice
        added = [k for k in after if k not in before]
        assert [k for k in after if k in before] == before              # everything else: same rows, same order
        return before, added

    # Today's book: every option's conversion pair is already asked for by an open forward
    # (EURUSD), by the EURSEK forwards' cross legs (USDSEK) or is the option's own pair
    # (USDJPY) -- so the difference is exactly nothing, on every date checked.
    for as_of in ("2026-08-24", "2026-09-18", "2026-09-23", "2026-10-15", "2026-11-25"):
        before, added = before_and_after(as_of)
        assert before and added == [], (as_of, added)
    # The day the EUR forwards are gone (here: removed), the EURSEK digital keeps its own
    # conversion spots alive. EURUSD is still asked for by the EURUSD options' own pair, so
    # the one addition on 2026-09-18 is the quote->USD spot; after those expire, both.
    conn.execute("DELETE FROM trade_legs WHERE trade_id IN (SELECT trade_id FROM trades "
                 "WHERE instrument_id IN ('EURSEK', 'EURUSD'))")
    conn.execute("DELETE FROM trades WHERE instrument_id IN ('EURSEK', 'EURUSD')")
    conn.commit()
    _, added = before_and_after("2026-09-18")
    assert added == [(usd_sek, "SPOT", "2026-09-18", f"{usd_sek} Curncy")]
    _, added = before_and_after("2026-10-15")
    assert added == [(eur_usd, "SPOT", "2026-10-15", f"{eur_usd} Curncy"),
                     (usd_sek, "SPOT", "2026-10-15", f"{usd_sek} Curncy")]
