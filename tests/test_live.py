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


def test_pull_once_warns_when_cross_leg_instrument_missing_and_never_writes_it(tmp_path, monkeypatch):
    """The gap must be visible (status["warnings"], naming the pair) rather than the mark
    silently never appearing -- and it truly is never written, even if Bloomberg happily
    returns a price for the conventional ticker."""
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
    joined = " ".join(status["warnings"])
    assert "EURUSD" in joined and "USDSEK" in joined
    # Bloomberg "returned" a price for both synthetic tickers, but neither has an
    # instrument row, so neither is ever persisted.
    written_ids = {r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM marks")}
    assert "EURUSD" not in written_ids and "USDSEK" not in written_ids
    assert "EURSEK" in written_ids       # the actually-traded pair's own SPOT still writes fine


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
    marks = conn.execute("SELECT instrument_id, mark_type, value, source FROM marks ORDER BY mark_type, settle_date").fetchall()
    assert ("AUDUSD", "SPOT", 0.6612, "BBG_BFXFORWARD") in marks and len(marks) == 2
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


def test_pull_once_early_return_branch_still_pulls_curves_and_vol_for_options(tmp_path):
    """The exact Bloomberg-PC scenario (2026-09-17): an FX_OPTION-only book (no direct FX
    forward/spot trade, no IRS) takes pull_once's early-return branch
    (build_requests() == []), but must still pull OIS curves and vol quotes for the
    option's pair currencies rather than skip them along with the (correctly) skipped FX
    request."""
    from data.bloomberg.rates_marketdata import RatesFileSource
    from data.bloomberg.vol_marketdata import VolFileSource
    from pathlib import Path
    from datetime import date as _date

    p, conn = _option_db(tmp_path, base="EUR", quote="USD", option_id="EURUSD091826C-1", pair_ticker="EURUSD Curncy")
    rates_fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "ois_snapshot_v1.json"
    vol_fixture = Path(__file__).resolve().parents[1] / "data" / "bloomberg" / "fixtures" / "fx_vol_snapshot_v1.json"
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()),
                            today=_date(2026, 8, 17), rates_source=RatesFileSource(rates_fixture),
                            vol_source=VolFileSource(vol_fixture))
    assert status["connected"] is True and status["reason"] == "no open FX legs or futures to price"
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
