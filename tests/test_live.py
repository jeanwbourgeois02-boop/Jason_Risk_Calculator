"""data/bloomberg/live.py: rates from marks, status file, one pull with a fake session,
feed not started without Bloomberg. No blpapi needed."""
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
        ("a1", "BNP", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "HAHY7", "t", "d", ""),
        ("j1", "BNP", "USDJPY", "FX_FWD", "j1", "2026-08-10", 1e6, 150.0, "acc", "cp", "HAHY7", "t", "d", ""),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-16", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-16", 0.65, 1),
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-08-10", "2026-09-18", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-08-10", "2026-09-18", 150.0, 1),
    ])
    conn.execute("INSERT INTO positions VALUES ('2026-08-17','BNP','acc','AUDUSD','2026-09-16',-1e6,650000,0.66,1,0,0,0,0,0)")
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
    conn.execute("INSERT INTO trades VALUES ('f1','BNP','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',6*50*7528.25,'2026-08-10','2026-09-18',0,0)")
    conn.commit()
    reqs = live.build_requests(conn, "2026-08-17")
    keys = {(r.instrument_id, r.mark_type, r.settle_date) for r in reqs}
    assert ("ESU6 Index", "FUTURE_PX", "2026-09-18") in keys
    later_keys = {(r.instrument_id, r.mark_type) for r in live.build_requests(conn, "2026-09-19")}
    assert ("ESU6 Index", "FUTURE_PX") not in later_keys       # expired future drops out


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


def test_pull_once_includes_future_px(tmp_path, monkeypatch):
    p, conn = _db(tmp_path)
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    conn.execute("INSERT INTO trades VALUES ('f1','BNP','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trade_legs VALUES ('f1',1,'NOTIONAL','USD',6*50*7528.25,'2026-08-10','2026-09-18',0,0)")
    conn.commit()
    from data.bloomberg import pull_marks as pm
    from datetime import date as _date

    monkeypatch.setattr(pm, "fetch_reference", lambda *a, **k: {})
    monkeypatch.setattr(pm, "fetch_historical",
                        lambda session, service, tickers, field, as_of, diag=None, tag=None: {"ESU6 Index": 7598.5})
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    from data.bloomberg import fwd_curve
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=_date(2026, 8, 20))
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    fut = by[("ESU6 Index", "FUTURE_PX", "2026-09-18")]
    assert fut["status"] == "OK" and fut["value"] == 7598.5 and fut["source"] == "BBG_BDH"
    row = conn.execute("SELECT value, source FROM marks WHERE instrument_id='ESU6 Index'").fetchone()
    assert row == (7598.5, "BBG_BDH")


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
