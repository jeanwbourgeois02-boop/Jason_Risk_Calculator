"""tools/bbg_diagnostics.py -- item 3a (2026-09-17): the FX option PREMIUM/DELTA FAIL and
the "Last marks pull" WARNING must show *why*, not just a bare count, by reading the last
recorded pull's status file (data.bloomberg.live's status JSON). tools/ is normally owned
by a different agent; this file covers the specific fix bbg-data was asked to make there
(coordinator message, 2026-09-17) -- data/bloomberg/** tests stay in tests/test_bloomberg.py
and tests/test_live.py as usual."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from data.ingest import schema

REPO = Path(__file__).resolve().parents[1]


def _load_tool():
    spec = importlib.util.spec_from_file_location("bbg_diagnostics_tests", REPO / "tools" / "bbg_diagnostics.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AS_OF = "2026-08-17"


def _option_db(path):
    conn = schema.connect(path)
    conn.execute("INSERT INTO instruments VALUES "
                 "('EURSEK091826C-1','FX_OPTION','EUR','SEK',1,0,'EURSEK Curncy','2026-09-18')")
    conn.execute("INSERT INTO instrument_options VALUES ('EURSEK091826C-1',11.2,'CALL',0,'9999-12-31','VANILLA')")
    conn.execute("INSERT INTO trades VALUES ('o1','XLSX','EURSEK091826C-1','FX_OPTION','o1','2026-08-14',"
                 "1000000,0.01,'acc','cp','HAHY7','t','d','')")
    conn.commit()
    conn.close()


def test_check_option_coverage_shows_verbatim_skip_reason_from_status_file(tmp_path):
    tool = _load_tool()
    db = tmp_path / "risk.db"
    _option_db(db)
    (tmp_path / "risk.db.bloomberg_status.json").write_text(json.dumps({
        "time": "2026-09-17T15:23:23+08:00", "connected": True, "requested": 26, "written": 21, "failed": 5,
        "items": [],
        "options": {"priced": 0, "skipped": [{"trade_id": "o1", "reason": "no curve/rate SEK"}]},
    }), encoding="utf-8")
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        checks = tool.check_option_coverage(conn, AS_OF, db_path=db)
    finally:
        conn.close()
    fails = {c["name"]: c["message"] for c in checks if c["status"] == "fail"}
    assert "FX option PREMIUM coverage" in fails
    assert "EURSEK091826C-1 (o1): no curve/rate SEK" in fails["FX option PREMIUM coverage"]
    assert "EURSEK091826C-1 (o1): no curve/rate SEK" in fails["FX option DELTA coverage"]


def test_check_option_coverage_generic_message_when_no_status_file(tmp_path):
    tool = _load_tool()
    db = tmp_path / "risk.db"
    _option_db(db)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        checks = tool.check_option_coverage(conn, AS_OF, db_path=db)
    finally:
        conn.close()
    fails = {c["name"]: c["message"] for c in checks if c["status"] == "fail"}
    assert "FX option PREMIUM coverage" in fails
    # No status file at all: never crashes, never fabricates a reason.
    assert "did not price them" in fails["FX option PREMIUM coverage"]


def test_check_option_coverage_passes_when_marks_present(tmp_path):
    tool = _load_tool()
    db = tmp_path / "risk.db"
    _option_db(db)
    conn = schema.connect(db)
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (AS_OF, "EURSEK091826C-1", "2026-09-18", "PREMIUM", 0.012, "QL_OPTIONS_PRICER", AS_OF + "T17:00:00-04:00"),
        (AS_OF, "EURSEK091826C-1", AS_OF, "DELTA", 0.45, "QL_OPTIONS_PRICER", AS_OF + "T17:00:00-04:00"),
    ])
    conn.commit()
    conn.close()
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        checks = tool.check_option_coverage(conn, AS_OF, db_path=db)
    finally:
        conn.close()
    statuses = {c["name"]: c["status"] for c in checks}
    assert statuses["FX option PREMIUM coverage"] == "pass"
    assert statuses["FX option DELTA coverage"] == "pass"


def test_check_last_pull_warning_lists_failed_items_instrument_and_detail(tmp_path):
    tool = _load_tool()
    db = tmp_path / "risk.db"
    schema.connect(db).close()
    (tmp_path / "risk.db.bloomberg_status.json").write_text(json.dumps({
        "time": "2026-09-17T15:23:23+08:00", "connected": True, "requested": 27, "written": 25, "failed": 2,
        "items": [
            {"instrument_id": "USDMXN", "mark_type": "FWD_OUTRIGHT", "settle_date": "2026-09-17",
             "status": "FAILED", "value": None, "source": "", "detail": "outside curve 2026-09-24..2026-12-17"},
            {"instrument_id": "ESU6 Index", "mark_type": "FUTURE_PX", "settle_date": "2026-09-18",
             "status": "FAILED", "value": None, "source": "", "detail": "no live PX_LAST and no PX_SETTLE"},
            {"instrument_id": "EURUSD", "mark_type": "SPOT", "settle_date": "2026-09-17",
             "status": "OK", "value": 1.08, "source": "BBG_BFXFORWARD", "detail": ""},
        ],
    }), encoding="utf-8")
    checks = tool.check_last_pull(db)
    assert len(checks) == 1
    assert checks[0]["status"] == "warning"
    msg = checks[0]["message"]
    assert "2 of 27" in msg
    assert "USDMXN FWD_OUTRIGHT" in msg and "outside curve" in msg
    assert "ESU6 Index FUTURE_PX" in msg and "no live PX_LAST" in msg


def test_check_last_pull_warning_with_no_items_still_reports_the_count(tmp_path):
    """Never crash / never fabricate an item list when the status predates `items`."""
    tool = _load_tool()
    db = tmp_path / "risk.db"
    schema.connect(db).close()
    (tmp_path / "risk.db.bloomberg_status.json").write_text(json.dumps({
        "time": "t", "connected": True, "requested": 5, "written": 3, "failed": 2,
    }), encoding="utf-8")
    checks = tool.check_last_pull(db)
    assert checks[0]["status"] == "warning"
    assert "2 of 5" in checks[0]["message"]


# --------------------------------------------------------------------------- check_unverified_assumptions
# 2026-09-18: this check used to just count UNVERIFIED lines in vol_marketdata.py's
# docstring and unconditionally tell the user to run --probe by hand. Since
# data/bloomberg/live.py's `_vol_step` requests every one of those tickers on every live
# pull with an open FX_OPTION in the book, the live pull itself is now the probe, and this
# check reads what it actually confirmed/rejected from the `vol_ticker_checks` table
# (data.bloomberg.vol_marketdata.record_vol_ticker_checks writes it -- see
# tests/test_bloomberg.py and tests/test_live.py for that side). These tests build the
# VolFetchResult from a real fake blpapi session (tests.test_bloomberg's fixture), the
# same "fake session" the live pull itself would talk to.
def _vol_result_all_confirmed(monkeypatch):
    from data.bloomberg import vol_marketdata as vm
    from tests.test_bloomberg import _install_fake_blpapi

    def responder(request):
        return [{"securityData": [{"security": t, "fieldData": {"PX_LAST": 7.5}} for t in request.securities]}]

    _install_fake_blpapi(monkeypatch, responder)
    src = vm.VolBloombergSource("localhost", 8194)
    return src.get_vol_quotes(["EURUSD"], as_of=None)


def test_check_unverified_assumptions_pass_when_every_assumption_confirmed(tmp_path, monkeypatch):
    from data.bloomberg import vol_marketdata as vm

    tool = _load_tool()
    db = tmp_path / "risk.db"
    conn = schema.connect(db)
    result = _vol_result_all_confirmed(monkeypatch)
    vm.record_vol_ticker_checks(conn, result, checked_at="2026-09-18T17:00:00-04:00")
    conn.commit()
    conn.close()

    checks = tool.check_unverified_assumptions(db)
    assert len(checks) == 1
    assert checks[0]["status"] == "pass"
    assert "All 9" in checks[0]["message"] or f"All {len(vm.ALL_ASSUMPTION_IDS)}" in checks[0]["message"]


def test_check_unverified_assumptions_fail_names_rejected_assumption_and_ticker(tmp_path, monkeypatch):
    from data.bloomberg import vol_marketdata as vm
    from tests.test_bloomberg import _install_fake_blpapi

    tool = _load_tool()
    db = tmp_path / "risk.db"
    conn = schema.connect(db)

    def responder(request):
        sec_list = []
        for t in request.securities:
            if "25R" in t:
                sec_list.append({"security": t, "fieldData": {}, "securityError": {"message": "UNKNOWN_SECURITY"}})
            else:
                sec_list.append({"security": t, "fieldData": {"PX_LAST": 7.5}})
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)
    src = vm.VolBloombergSource("localhost", 8194)
    result = src.get_vol_quotes(["EURUSD"], as_of=None)
    vm.record_vol_ticker_checks(conn, result, checked_at="2026-09-18T17:00:00-04:00")
    conn.commit()
    conn.close()

    checks = tool.check_unverified_assumptions(db)
    assert len(checks) == 1
    assert checks[0]["status"] == "fail"
    msg = checks[0]["message"]
    assert "rr25_ticker" in msg
    assert "25R" in msg
    assert "SECURITY_ERROR" in msg
    assert "UNKNOWN_SECURITY" in msg


def test_check_unverified_assumptions_warning_when_no_record_yet(tmp_path):
    tool = _load_tool()
    db = tmp_path / "risk.db"
    schema.connect(db).close()  # a real, current schema -- just no live vol pull has run

    checks = tool.check_unverified_assumptions(db)
    assert len(checks) == 1
    assert checks[0]["status"] == "warning"
    assert "not yet exercised" in checks[0]["message"]


def test_check_unverified_assumptions_warning_when_db_path_missing():
    tool = _load_tool()
    checks = tool.check_unverified_assumptions(None)
    assert checks[0]["status"] == "warning"
    assert "not yet exercised" in checks[0]["message"]


# --------------------------------------------------------------------------- 2026-09-21: the backfill block
# The diagnostics paste says which field gave the forward-points divisor (the Bloomberg PC
# returned nothing for FWD_POINTS_SCALE) and that past days are re-requested at 15:00 NY.
def _backfill_status(tmp_path, block):
    db = tmp_path / "risk.db"
    schema.connect(db).close()
    (tmp_path / "risk.db.bloomberg_status.json").write_text(json.dumps(
        {"time": "t", "connected": True, "requested": 1, "written": 1, "failed": 0, "items": [], "backfill": block}),
        encoding="utf-8")
    return db


def test_check_backfill_report_names_the_scale_field_that_answered_and_the_1500_note(tmp_path):
    tool = _load_tool()
    db = _backfill_status(tmp_path, {
        "points_scale": {"AUDUSD": {"field": "FWD_SCALE", "divisor": 10000.0, "raw": {"FWD_SCALE": 4}, "errors": {}},
                         "USDJPY": {"field": "FWD_SCALE", "divisor": 100.0, "raw": {"FWD_SCALE": 2}, "errors": {}}},
        "note": "3 past day(s) hold FX marks that are not the 15:00 New York close; they are asked of Bloomberg again once."})
    rows = {c["name"]: c for c in tool.check_backfill_report(db)}
    assert rows["Forward points divisor"]["status"] == "pass"
    assert "FWD_SCALE answered for all 2 pair(s)" in rows["Forward points divisor"]["message"]
    assert "AUDUSD: FWD_SCALE = 4, divisor 10000" in rows["Forward points divisor"]["message"]
    assert rows["Past closes at 15:00 New York"]["status"] == "warning"
    assert "3 past day(s)" in rows["Past closes at 15:00 New York"]["message"]


def test_check_backfill_report_fails_naming_both_fields_when_neither_answered(tmp_path):
    tool = _load_tool()
    db = _backfill_status(tmp_path, {"points_scale": {
        "EURSEK": {"field": "", "divisor": None, "raw": {}, "errors": {"FWD_POINTS_SCALE": "Field not valid"}},
        "AUDUSD": {"field": "FWD_SCALE", "divisor": 10000.0, "raw": {"FWD_SCALE": 4}, "errors": {}}}})
    rows = tool.check_backfill_report(db)
    assert [c["status"] for c in rows] == ["fail"]
    message = rows[0]["message"]
    assert "1 of 2 pair(s)" in message and "EURSEK: FWD_POINTS_SCALE: Field not valid, FWD_SCALE: not returned" in message
    # nothing to say: no row at all (no status file / a backfill that needed no divisor)
    assert tool.check_backfill_report(_backfill_status(tmp_path, {"running": False})) == []
    assert tool.check_backfill_report(tmp_path / "absent.db") == []


def test_check_clock_passes_for_the_next_date_after_1700_new_york():
    """User decision 2026-09-22 ("all date rollover at hkt 5am", 17:00 New York): the book's
    day turns at 17:00 New York for everything at once (data.bloomberg.live.book_today), so
    a pull at 18:00 New York on the 22nd stamps its marks 2026-09-23. The clock check must
    pass for that as-of and warn for the calendar date -- before this it did the reverse and
    warned every Hong Kong morning that the pull's as-of was not today."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tool = _load_tool()
    evening = datetime(2026, 9, 22, 18, 0, tzinfo=ZoneInfo("America/New_York"))
    [row] = tool.check_clock("2026-09-23", now=evening)
    assert row["status"] == "pass", row
    assert "stamped 2026-09-23" in row["message"] and "rolled at 17:00" in row["message"]
    [row] = tool.check_clock("2026-09-22", now=evening)
    assert row["status"] == "warning", row
    assert "as-of 2026-09-22" in row["message"] and "2026-09-23" in row["message"]
    # before the roll the book date is still the New York calendar date, as it always was
    afternoon = datetime(2026, 9, 22, 16, 59, tzinfo=ZoneInfo("America/New_York"))
    assert tool.check_clock("2026-09-22", now=afternoon)[0]["status"] == "pass"
    # the run's default as-of is the same date the check compares against
    assert tool._today_ny(evening) == "2026-09-23" and tool._today_ny(afternoon) == "2026-09-22"
