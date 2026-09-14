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
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("a1", "BNP", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "HAHY7", "t", "d"),
        ("j1", "BNP", "USDJPY", "FX_FWD", "j1", "2026-08-10", 1e6, 150.0, "acc", "cp", "HAHY7", "t", "d"),
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
    assert ("AUDUSD", "FWD_OUTRIGHT", "2026-08-24") in keys        # workbook maturity WORKDAY(+5)
    assert live.build_requests(conn, "2026-10-01") == []           # everything settled


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
    live_day = _date(2026, 8, 20)   # injected "today": the 2026-08-24 workbook maturity lies between spot and 1st tenor
    status = live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=live_day)
    assert status["connected"] is True and status["requested"] == 6 and status["as_of_marks"] == "2026-08-20"
    by = {(i["instrument_id"], i["mark_type"], i["settle_date"]): i for i in status["items"]}
    assert by[("AUDUSD", "SPOT", status["as_of_marks"])]["status"] == "OK"
    assert by[("AUDUSD", "SPOT", status["as_of_marks"])]["value"] == 0.6612
    assert by[("USDJPY", "SPOT", status["as_of_marks"])] == {**by[("USDJPY", "SPOT", status["as_of_marks"])],
                                                             "status": "FAILED", "detail": "no PX_LAST returned"}
    jpy_fwd = by[("USDJPY", "FWD_OUTRIGHT", "2026-09-18")]
    assert jpy_fwd["status"] == "FAILED" and "not authorised" in jpy_fwd["detail"]
    aud_exact = by[("AUDUSD", "FWD_OUTRIGHT", "2026-09-16")]
    assert aud_exact["status"] == "OK" and aud_exact["value"] == 0.6620 and aud_exact["source"] == "BBG_BFXFORWARD"
    aud_interp = by[("AUDUSD", "FWD_OUTRIGHT", "2026-08-24")]        # workbook maturity: before first tenor
    assert aud_interp["status"] == "OK" and aud_interp["source"] == "BBG_INTERP" and 0.6612 < aud_interp["value"] < 0.6620
    assert status["failed"] == 3 and status["written"] == 3 and any("interp_from_spot" in w for w in status["warnings"])
    marks = conn.execute("SELECT instrument_id, mark_type, value, source FROM marks ORDER BY mark_type, settle_date").fetchall()
    assert ("AUDUSD", "SPOT", 0.6612, "BBG_BFXFORWARD") in marks and len(marks) == 3
    # the ladder now sees the live spot, and USDJPY is simply missing (never invented)
    rates = live.rates_from_marks(conn)
    assert rates["AUD"]["rate"] == 0.6612 and "JPY" not in rates
    # second cycle replaces rather than duplicates (same primary key, new snapped_at)
    live.pull_once(p, "2026-08-17", session_factory=lambda: (object(), object()), today=live_day)
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE mark_type='SPOT'").fetchone()[0] == 1


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
