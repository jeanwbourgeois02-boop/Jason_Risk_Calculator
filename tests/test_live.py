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
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
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
