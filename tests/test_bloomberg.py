"""Tests for data/bloomberg (marks CSV format, BNP_BVAL marks, Bloomberg pull script).

Real-file tests skip if the raw BNP CSV is absent. The pull_marks tests inject a fake
`blpapi` module into sys.modules so they run without the real SDK installed.
"""
from __future__ import annotations

import csv
import importlib.util
import inspect
import json
import math
import sqlite3
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from data.bloomberg import marks_csv
from data.ingest import schema

REPO = Path(__file__).resolve().parents[1]
AS_OF = "2026-08-17"


def _book_today():
    """Today as the code under test sees it: the New York book date, not the PC's
    local date (a day ahead of New York every morning in Asia)."""
    from data.bloomberg.live import book_today
    return book_today()



def _mk_conn():
    conn = schema.connect(":memory:")
    conn.execute(
        "INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')"
    )
    conn.commit()
    return conn


def _mark_row(as_of=AS_OF, instrument_id="USDJPY", settle_date=AS_OF, mark_type="SPOT",
              value=150.0, source="BBG_BFXFORWARD", snapped_at="2026-08-17T15:00:00-04:00"):
    return [as_of, instrument_id, settle_date, mark_type, value, source, snapped_at]


# =========================================================================== marks_csv
def test_marks_columns_and_sets():
    assert marks_csv.MARKS_COLUMNS == [
        "as_of_date", "instrument_id", "settle_date", "mark_type", "value", "source", "snapped_at"]
    assert marks_csv.MARK_TYPES >= {
        "SPOT", "FWD_OUTRIGHT", "FUTURE_PX", "PAR_RATE", "PV_USD", "DV01_USD", "PREMIUM", "DELTA"}
    assert marks_csv.SOURCES == {
        "BNP_BVAL", "BBG_BFXFORWARD", "BBG_BDH", "BBG_BDP", "MANUAL", "BBG_INTERP", "WORKBOOK_REFERENCE"}
    # BBG_INTERP is never official.
    assert "BBG_INTERP" not in schema.OFFICIAL_MARK_SOURCE.values()


def test_write_then_load_round_trip(tmp_path):
    conn = _mk_conn()
    rows = [
        marks_csv.MarkRow(AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        marks_csv.MarkRow(AS_OF, "EURUSD", "2026-09-16", "FWD_OUTRIGHT", 1.105, "BBG_BFXFORWARD",
                          "2026-08-17T15:00:00-04:00"),
    ]
    path = tmp_path / "marks.csv"
    marks_csv.write_marks_csv(rows, path)

    with path.open() as f:
        header = next(csv.reader(f))
    assert header == marks_csv.MARKS_COLUMNS
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == marks_csv.MARKS_COLUMNS

    result = marks_csv.load_marks_csv(path, conn, strict=True)
    assert result.ok
    assert result.n_loaded == 2
    got = conn.execute(
        "SELECT as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at "
        "FROM marks ORDER BY instrument_id"
    ).fetchall()
    assert got == [
        (AS_OF, "EURUSD", "2026-09-16", "FWD_OUTRIGHT", 1.105, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        (AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
    ]


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(marks_csv.MARKS_COLUMNS)
        for r in rows:
            w.writerow(r)


def test_reject_bad_date(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(as_of="08/17/2026")])
    with pytest.raises(ValueError, match="as_of_date"):
        marks_csv.load_marks_csv(path, conn, strict=True)
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


def test_reject_nan_value(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(value="nan")])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "finite" in result.rejects[0].detail
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


def test_reject_inf_value(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(value="inf")])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "finite" in result.rejects[0].detail


def test_reject_compact_date(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(as_of="20260817")])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "as_of_date" in result.rejects[0].detail


def test_reject_bad_snapped_at(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(snapped_at="not-a-timestamp")])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "snapped_at" in result.rejects[0].detail


def test_reject_naive_snapped_at(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(snapped_at="2026-08-17T15:00:00")])  # no offset
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "snapped_at" in result.rejects[0].detail


def test_reject_unknown_mark_type(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(mark_type="NOT_A_TYPE")])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "mark_type" in result.rejects[0].detail
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


def test_reject_unknown_source(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(source="NOT_A_SOURCE")])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "source" in result.rejects[0].detail


def test_reject_unknown_instrument(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(instrument_id="NOPE")])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert not result.ok
    assert "instrument_id" in result.rejects[0].detail


def test_reject_duplicate_key_within_file(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(), _mark_row()])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert result.n_loaded == 1
    assert len(result.rejects) == 1
    assert "duplicate" in result.rejects[0].detail


def test_reject_duplicate_against_table(tmp_path):
    conn = _mk_conn()
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", _mark_row())
    conn.commit()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(value=999.0)])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert result.n_loaded == 0
    assert len(result.rejects) == 1
    assert "duplicate" in result.rejects[0].detail


def test_strict_raises_and_inserts_nothing(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [_mark_row(), _mark_row(instrument_id="NOPE")])
    with pytest.raises(ValueError):
        marks_csv.load_marks_csv(path, conn, strict=True)
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


def test_nonstrict_inserts_good_rows_returns_rejects(tmp_path):
    conn = _mk_conn()
    path = tmp_path / "marks.csv"
    _write_csv(path, [
        _mark_row(instrument_id="USDJPY", value=150.0),
        _mark_row(instrument_id="EURUSD", settle_date="2026-09-16", mark_type="FWD_OUTRIGHT", value=1.1),
        _mark_row(instrument_id="NOPE"),
    ])
    result = marks_csv.load_marks_csv(path, conn, strict=False)
    assert result.n_loaded == 2
    assert len(result.rejects) == 1
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 2


# =========================================================================== export_request
def test_export_request(tmp_path):
    conn = schema.connect(":memory:")
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    conn.execute(
        "INSERT INTO trades VALUES ('t1','MANUAL','EURUSD','FX_FWD','t1','2026-08-17',100.0,1.1,"
        "'ACC','CPTY','STRAT','TRADER','synthetic','')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('t1',1,'FX_NEAR','EUR',100.0,'2026-08-17','2026-09-16',1.1,1)"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('t2','MANUAL','ESU6 Index','FUTURE','t2','2026-08-17',10.0,4500.0,"
        "'ACC','CPTY','STRAT','TRADER','synthetic','')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('t2',1,'NOTIONAL','USD',225000.0,'2026-08-17','2026-09-18',0,0)"
    )
    conn.commit()

    path = tmp_path / "request.csv"
    n = marks_csv.export_request(conn, AS_OF, path)
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert n == len(rows)
    kinds = {(r["instrument_id"], r["mark_type"], r["settle_date"]) for r in rows}
    assert ("EURUSD", "SPOT", AS_OF) in kinds
    # FWD_OUTRIGHT is requested at the leg's OWN settle_date (CLAUDE.md "Mark date"),
    # not a shared WORKDAY(as_of,5) date -- the trade_legs row above settles 2026-09-16.
    assert ("EURUSD", "FWD_OUTRIGHT", "2026-09-16") in kinds
    assert ("ESU6 Index", "FUTURE_PX", "2026-09-18") in kinds


def test_export_request_excludes_expired_future(tmp_path):
    conn = schema.connect(":memory:")
    # Expired future (expiry_date <= as_of) with an open leg: must NOT be requested.
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-08-10')")
    conn.execute(
        "INSERT INTO trades VALUES ('t1','MANUAL','ESU6 Index','FUTURE','t1','2026-07-01',10.0,4500.0,"
        "'ACC','CPTY','STRAT','TRADER','synthetic','')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('t1',1,'NOTIONAL','USD',225000.0,'2026-07-01','2026-08-10',0,0)"
    )
    # Unexpired future but no open leg at all: must NOT be requested either.
    conn.execute("INSERT INTO instruments VALUES ('ESZ6 Index','FUTURE','ES','USD',50,0,'ESZ6 Index','2026-12-18')")
    # Unexpired future with an open trade_legs row: must be requested.
    conn.execute("INSERT INTO instruments VALUES ('ESH7 Index','FUTURE','ES','USD',50,0,'ESH7 Index','2027-03-19')")
    conn.execute(
        "INSERT INTO trades VALUES ('t2','MANUAL','ESH7 Index','FUTURE','t2','2026-08-01',5.0,4500.0,"
        "'ACC','CPTY','STRAT','TRADER','synthetic','')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('t2',1,'NOTIONAL','USD',22500.0,'2026-08-01','2027-03-19',0,0)"
    )
    conn.commit()

    path = tmp_path / "request.csv"
    marks_csv.export_request(conn, AS_OF, path)
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    instruments = {r["instrument_id"] for r in rows if r["mark_type"] == "FUTURE_PX"}
    assert "ESU6 Index" not in instruments
    assert "ESZ6 Index" not in instruments
    assert "ESH7 Index" in instruments


# =========================================================================== pull_marks
class MultiEvent:
    """Wrap a list of event specs (each a plain data list, a (data, event_type) tuple, or
    a (data, event_type, correlation_id) triple) so a single sendRequest() can queue up
    several nextEvent() results -- used to script a late/stale response arriving before
    the real one (see W-1 correlation-id tests). Not needed for ordinary single-event
    responders, which may keep returning a plain data list or a (data, event_type) tuple
    as before."""

    def __init__(self, specs):
        self.specs = specs


def _install_fake_blpapi(monkeypatch, responder):
    fake = types.ModuleType("blpapi")
    responder_takes_cid = len(inspect.signature(responder).parameters) >= 2

    class SessionOptions:
        def setServerHost(self, host):
            self.host = host

        def setServerPort(self, port):
            self.port = port

    class _ListAppender:
        def __init__(self, lst):
            self._lst = lst

        def appendValue(self, v):
            self._lst.append(v)

    class _OverrideElement:
        def __init__(self, d):
            self._d = d
            self._field_id = None

        def setElement(self, name, value):
            if name == "fieldId":
                self._field_id = value
            elif name == "value":
                self._d[self._field_id] = value

    class _OverrideAppender:
        def __init__(self, d):
            self._d = d

        def appendElement(self):
            return _OverrideElement(self._d)

    class FakeRequest:
        def __init__(self, req_type):
            self.req_type = req_type
            self.securities = []
            self.fields = []
            self.overrides = {}
            self.startDate = None
            self.endDate = None

        def getElement(self, name):
            if name == "securities":
                return _ListAppender(self.securities)
            if name == "fields":
                return _ListAppender(self.fields)
            if name == "overrides":
                return _OverrideAppender(self.overrides)
            raise KeyError(name)

        def set(self, name, value):
            setattr(self, name, value)

    class FakeStructElement:
        def __init__(self, value):
            self._value = value

        def hasElement(self, name):
            return isinstance(self._value, dict) and name in self._value

        def getElement(self, name):
            return FakeStructElement(self._value[name])

        def getElementAsString(self, name):
            return str(self._value[name])

        def numValues(self):
            return len(self._value)

        def getValueAsElement(self, i):
            return FakeStructElement(self._value[i])

        def getValue(self):
            return self._value

    class CorrelationId:
        """Minimal stand-in for blpapi.CorrelationId: equality/hash by wrapped value, like
        the real one, so `correlation_id not in msg.correlationIds()` works."""

        def __init__(self, value):
            self.value = value

        def __eq__(self, other):
            return isinstance(other, CorrelationId) and self.value == other.value

        def __hash__(self):
            return hash(self.value)

        def __repr__(self):
            return f"CorrelationId({self.value!r})"

    class FakeMsg:
        def __init__(self, data, correlation_id=None):
            self._data = data
            self._correlation_id = correlation_id

        def hasElement(self, name):
            return name in self._data

        def getElement(self, name):
            return FakeStructElement(self._data[name])

        def correlationIds(self):
            return [self._correlation_id] if self._correlation_id is not None else []

    class Event:
        RESPONSE = "RESPONSE"
        TIMEOUT = "TIMEOUT"

    class FakeEvent:
        def __init__(self, msgs, event_type):
            self._msgs = msgs
            self._event_type = event_type

        def __iter__(self):
            return iter(self._msgs)

        def eventType(self):
            return self._event_type

    def _normalize_spec(spec, default_corr):
        # A spec is either a plain data list (-> RESPONSE, default correlation id), a
        # (data, event_type) tuple, or a (data, event_type, correlation_id) triple.
        if isinstance(spec, tuple):
            if len(spec) == 3:
                data, event_type, corr = spec
            else:
                data, event_type = spec
                corr = default_corr
        else:
            data, event_type, corr = spec, Event.RESPONSE, default_corr
        return data, event_type, corr

    class Session:
        def __init__(self, opts):
            self.opts = opts
            self._last_request = None
            self._event_queue = []

        def start(self):
            return True

        def openService(self, name):
            return True

        def getService(self, name):
            return self

        def createRequest(self, req_type):
            return FakeRequest(req_type)

        def sendRequest(self, request, correlationId=None):
            self._last_request = request
            self._last_correlation_id = correlationId
            if responder_takes_cid:
                result = responder(request, correlationId)
            else:
                result = responder(request)
            # Extended (not replaced): a responder may return a plain list of message
            # dicts (-> one RESPONSE event, original behaviour), a (data, event_type)
            # tuple (-> one event of that type, e.g. TIMEOUT), or a MultiEvent(specs) to
            # queue up several nextEvent() results in order -- used to script a late/stale
            # response (tagged with an old correlation id) arriving before the real one.
            specs = result.specs if isinstance(result, MultiEvent) else [result]
            for spec in specs:
                self._event_queue.append(_normalize_spec(spec, correlationId))

        def nextEvent(self, timeout=None):
            if not self._event_queue:
                return FakeEvent([], Event.TIMEOUT)
            data, event_type, corr = self._event_queue.pop(0)
            return FakeEvent([FakeMsg(d, corr) for d in data], event_type)

        def stop(self):
            pass

    fake.SessionOptions = SessionOptions
    fake.Session = Session
    fake.Event = Event
    fake.CorrelationId = CorrelationId
    fake.MultiEvent = MultiEvent
    monkeypatch.setitem(sys.modules, "blpapi", fake)
    return fake


def test_interpolate_forward_points_pure():
    from data.bloomberg.pull_marks import TenorPoint, interpolate_forward_points

    pts = [
        TenorPoint("SP", date(2026, 8, 19), 0.0),
        TenorPoint("1W", date(2026, 8, 24), 50.0),
    ]
    got = interpolate_forward_points(date(2026, 8, 20), pts)
    assert math.isclose(got, 10.0)

    # exact hit on a tenor date
    assert math.isclose(interpolate_forward_points(date(2026, 8, 19), pts), 0.0)

    # outside the range: never extrapolated
    assert interpolate_forward_points(date(2026, 8, 10), pts) is None
    assert interpolate_forward_points(date(2026, 9, 1), pts) is None

    # fewer than 2 points: None
    assert interpolate_forward_points(date(2026, 8, 20), [pts[0]]) is None


def test_pull_marks_end_to_end_with_fake_blpapi(monkeypatch, tmp_path):
    ref_data = {
        ("EURUSD Curncy", "PX_LAST"): 1.1000,
        ("EURUSD Curncy", "FWD_POINTS_SCALE"): 10000.0,
        ("EURUSDSP Curncy", "PX_LAST"): 0.0,
        ("EURUSDSP Curncy", "SETTLE_DT"): "2026-08-19",
        ("EURUSD1W Curncy", "PX_LAST"): 50.0,
        ("EURUSD1W Curncy", "SETTLE_DT"): "2026-08-24",
        ("USDJPY Curncy", "FWD_CURVE"): 149.85,  # direct outright succeeds
    }
    hist_data = {
        "ESU6 Index": 4500.25,
        "EURUSD Curncy": 1.1000,  # SPOT now goes via HistoricalDataRequest PX_LAST too
    }

    def responder(request):
        if request.req_type == "HistoricalDataRequest":
            # S-4: answer every requested security, not just securities[0] -- a batched
            # HistoricalDataRequest for several tickers must not be misread as answered
            # when only the first one got a response.
            field = request.fields[0]
            out = []
            for ticker in request.securities:
                value = hist_data.get(ticker)
                field_data = [{field: value}] if value is not None else []
                out.append({"securityData": {"security": ticker, "fieldData": field_data}})
            return out
        elif request.req_type == "ReferenceDataRequest":
            sec_list = []
            for t in request.securities:
                row = {}
                for f in request.fields:
                    val = ref_data.get((t, f))
                    if val is not None:
                        row[f] = val
                sec_list.append({"security": t, "fieldData": row})
            return [{"securityData": sec_list}]
        raise AssertionError(f"unexpected request type {request.req_type}")

    _install_fake_blpapi(monkeypatch, responder)

    from data.bloomberg import pull_marks

    request_rows = [
        pull_marks.RequestRow("EURUSD", "EURUSD Curncy", AS_OF, "SPOT"),
        pull_marks.RequestRow("EURUSD", "EURUSD Curncy", "2026-08-20", "FWD_OUTRIGHT"),  # -> fallback interp
        pull_marks.RequestRow("USDJPY", "USDJPY Curncy", "2026-09-01", "FWD_OUTRIGHT"),  # -> direct
        pull_marks.RequestRow("ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX"),
    ]

    session, service = pull_marks.open_session("localhost", 8194)
    rows, warnings, failures = pull_marks.run(session, service, request_rows, date(2026, 8, 17))

    by_key = {(r["instrument_id"], r["mark_type"], r["settle_date"]): r for r in rows}

    spot_row = by_key[("EURUSD", "SPOT", AS_OF)]
    assert math.isclose(spot_row["value"], 1.1000)
    assert spot_row["source"] == "BBG_BFXFORWARD"

    interp_row = by_key[("EURUSD", "FWD_OUTRIGHT", "2026-08-20")]
    assert interp_row["source"] == "BBG_INTERP"
    assert math.isclose(interp_row["value"], 1.1000 + 10.0 / 10000.0)

    direct_row = by_key[("USDJPY", "FWD_OUTRIGHT", "2026-09-01")]
    assert direct_row["source"] == "BBG_BFXFORWARD"
    assert math.isclose(direct_row["value"], 149.85)

    future_row = by_key[("ESU6 Index", "FUTURE_PX", "2026-09-18")]
    assert future_row["source"] == "BBG_BDH"
    assert math.isclose(future_row["value"], 4500.25)

    out_path = tmp_path / "marks.csv"
    pull_marks.write_marks_csv(rows, out_path)

    # The output CSV must be accepted by load_marks_csv against a DB that knows these
    # instruments.
    conn = schema.connect(":memory:")
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute(
        "INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')"
    )
    conn.commit()
    result = marks_csv.load_marks_csv(out_path, conn, strict=True)
    assert result.ok
    assert result.n_loaded == 4


# =========================================================================== build_future_rows(live=True), 2026-09-17
# Live pull FUTURE_PX fix: PX_SETTLE for `as_of` doesn't exist until after that day's US
# close, so a live pull running earlier in the day (found on the Bloomberg PC at 06:27
# America/New_York) always reported MISSING for every future. live=True tries a live
# ReferenceDataRequest PX_LAST first, falling back to the latest prior PX_SETTLE only if
# that comes back empty. Source stays BBG_BDH either way (CLAUDE.md's official source for
# FUTURE_PX is unchanged); `detail` records which path was actually used.
def test_build_future_rows_live_uses_px_last_and_never_sends_historical(monkeypatch):
    from data.bloomberg import pull_marks

    def responder(request):
        if request.req_type == "ReferenceDataRequest":
            assert request.fields == ["PX_LAST"]
            return [{"securityData": [{"security": "ESU6 Index", "fieldData": {"PX_LAST": 7601.0}}]}]
        raise AssertionError(f"HistoricalDataRequest must not be sent when live PX_LAST succeeds "
                             f"({request.req_type})")

    _install_fake_blpapi(monkeypatch, responder)
    session, service = pull_marks.open_session()
    reqs = [pull_marks.RequestRow("ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX")]
    rows, warnings, failures = pull_marks.build_future_rows(session, service, reqs, date(2026, 9, 17), live=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["value"] == 7601.0 and row["source"] == "BBG_BDH" and row["detail"] == "live PX_LAST"
    assert warnings == [] and failures == []


def test_build_future_rows_live_falls_back_to_latest_px_settle_before_close(monkeypatch):
    """The exact Bloomberg-PC scenario: no PX_LAST yet (before the US close), so the
    fallback HistoricalDataRequest is tried over a multi-day range and must pick the MOST
    RECENT settle in that window, not the earliest one in the response."""
    from data.bloomberg import pull_marks

    def responder(request):
        if request.req_type == "ReferenceDataRequest":
            return [{"securityData": [{"security": "ESU6 Index", "fieldData": {}}]}]  # no PX_LAST yet
        if request.req_type == "HistoricalDataRequest":
            assert request.fields == ["PX_SETTLE"]
            assert request.startDate < request.endDate        # a real multi-day range, not a single day
            # ascending date order; the fallback must use the LAST (most recent) point
            return [{"securityData": {"security": "ESU6 Index",
                                      "fieldData": [{"PX_SETTLE": 7550.0}, {"PX_SETTLE": 7598.5}]}}]
        raise AssertionError(request.req_type)

    _install_fake_blpapi(monkeypatch, responder)
    session, service = pull_marks.open_session()
    reqs = [pull_marks.RequestRow("ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX")]
    rows, warnings, failures = pull_marks.build_future_rows(session, service, reqs, date(2026, 9, 17), live=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["value"] == 7598.5 and row["source"] == "BBG_BDH"
    assert "no live PX_LAST" in row["detail"] and "PX_SETTLE" in row["detail"]
    assert warnings == [] and failures == []


def test_build_future_rows_live_fails_clearly_when_both_px_last_and_px_settle_empty(monkeypatch):
    from data.bloomberg import pull_marks

    def responder(request):
        if request.req_type == "ReferenceDataRequest":
            return [{"securityData": [{"security": "ESU6 Index", "fieldData": {}}]}]
        if request.req_type == "HistoricalDataRequest":
            return [{"securityData": {"security": "ESU6 Index", "fieldData": []}}]
        raise AssertionError(request.req_type)

    _install_fake_blpapi(monkeypatch, responder)
    session, service = pull_marks.open_session()
    reqs = [pull_marks.RequestRow("ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX")]
    rows, warnings, failures = pull_marks.build_future_rows(session, service, reqs, date(2026, 9, 17), live=True)
    assert rows == []
    assert len(failures) == 1
    assert failures[0]["instrument_id"] == "ESU6 Index"
    assert "no live PX_LAST" in failures[0]["detail"] and "PX_SETTLE" in failures[0]["detail"]
    assert any("ESU6 Index" in w for w in warnings)


def test_build_future_rows_default_is_unaffected_single_day_historical_only(monkeypatch):
    """The historical/backfill path (live=False, the default): unchanged -- a single-day
    PX_SETTLE HistoricalDataRequest, no ReferenceDataRequest at all, no `detail` key."""
    from data.bloomberg import pull_marks

    def responder(request):
        assert request.req_type == "HistoricalDataRequest"
        assert request.startDate == request.endDate      # single day, not a range
        return [{"securityData": {"security": "ESU6 Index", "fieldData": [{"PX_SETTLE": 7528.25}]}}]

    _install_fake_blpapi(monkeypatch, responder)
    session, service = pull_marks.open_session()
    reqs = [pull_marks.RequestRow("ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX")]
    rows, warnings, failures = pull_marks.build_future_rows(session, service, reqs, date(2026, 8, 17))
    assert len(rows) == 1 and rows[0]["value"] == 7528.25 and rows[0]["source"] == "BBG_BDH"
    assert "detail" not in rows[0]


def _responder_ref_and_hist(ref_data, hist_data):
    def responder(request):
        if request.req_type == "HistoricalDataRequest":
            # S-4: answer every requested security (see the analogous comment above).
            field = request.fields[0]
            out = []
            for ticker in request.securities:
                value = hist_data.get(ticker)
                field_data = [{field: value}] if value is not None else []
                out.append({"securityData": {"security": ticker, "fieldData": field_data}})
            return out
        elif request.req_type == "ReferenceDataRequest":
            sec_list = []
            for t in request.securities:
                row = {}
                for f in request.fields:
                    val = ref_data.get((t, f))
                    if val is not None:
                        row[f] = val
                sec_list.append({"security": t, "fieldData": row})
            return [{"securityData": sec_list}]
        raise AssertionError(f"unexpected request type {request.req_type}")
    return responder


def test_pull_marks_rejects_settle_date_outside_tenor_range(monkeypatch):
    ref_data = {
        ("EURUSD Curncy", "FWD_POINTS_SCALE"): 10000.0,
        ("EURUSDSP Curncy", "PX_LAST"): 0.0,
        ("EURUSDSP Curncy", "SETTLE_DT"): "2026-08-19",
        ("EURUSD1W Curncy", "PX_LAST"): 50.0,
        ("EURUSD1W Curncy", "SETTLE_DT"): "2026-08-24",
    }
    hist_data = {"EURUSD Curncy": 1.1000}

    _install_fake_blpapi(monkeypatch, _responder_ref_and_hist(ref_data, hist_data))
    from data.bloomberg import pull_marks

    request_rows = [
        pull_marks.RequestRow("EURUSD", "EURUSD Curncy", AS_OF, "SPOT"),
        pull_marks.RequestRow("EURUSD", "EURUSD Curncy", "2026-12-01", "FWD_OUTRIGHT"),  # past 1W tenor
    ]
    session, service = pull_marks.open_session()
    rows, warnings, failures = pull_marks.run(session, service, request_rows, date(2026, 8, 17))
    fwd_rows = [r for r in rows if r["mark_type"] == "FWD_OUTRIGHT"]
    assert fwd_rows == []
    assert any("outside standard tenor range" in w for w in warnings)


def test_pull_marks_standard_tenors_excludes_on_tn():
    from data.bloomberg import pull_marks
    assert "ON" not in pull_marks.STANDARD_TENORS
    assert "TN" not in pull_marks.STANDARD_TENORS
    assert pull_marks.STANDARD_TENORS[0] == "SP"


def test_pull_marks_rejects_settle_date_before_spot(monkeypatch):
    ref_data = {
        ("EURUSD Curncy", "FWD_POINTS_SCALE"): 10000.0,
        ("EURUSDSP Curncy", "PX_LAST"): 0.0,
        ("EURUSDSP Curncy", "SETTLE_DT"): "2026-08-19",
        ("EURUSD1W Curncy", "PX_LAST"): 50.0,
        ("EURUSD1W Curncy", "SETTLE_DT"): "2026-08-24",
    }
    hist_data = {"EURUSD Curncy": 1.1000}

    _install_fake_blpapi(monkeypatch, _responder_ref_and_hist(ref_data, hist_data))
    from data.bloomberg import pull_marks

    # settle_date before SP (spot): would previously have been mis-marked using the
    # ON/TN pre-spot points convention; must be rejected, never interpolated.
    request_rows = [
        pull_marks.RequestRow("EURUSD", "EURUSD Curncy", AS_OF, "SPOT"),
        pull_marks.RequestRow("EURUSD", "EURUSD Curncy", "2026-08-18", "FWD_OUTRIGHT"),
    ]
    session, service = pull_marks.open_session()
    rows, warnings, failures = pull_marks.run(session, service, request_rows, date(2026, 8, 17))
    fwd_rows = [r for r in rows if r["mark_type"] == "FWD_OUTRIGHT"]
    assert fwd_rows == []
    assert any("before spot" in w for w in warnings)


# =========================================================================== fetch_historical_series (2026-09-18 backfill)
def test_fetch_historical_series_keeps_every_day_not_just_the_latest(monkeypatch):
    """Unlike fetch_historical (latest point only), this is backfill's batch-friendly
    forward/future history: one request over the whole range, every day kept."""
    from data.bloomberg import pull_marks

    def responder(request):
        assert request.req_type == "HistoricalDataRequest"
        assert request.fields == ["PX_LAST", "SETTLE_DT"]
        assert request.startDate == "20260817" and request.endDate == "20260819"
        return [{"securityData": {"security": "EURUSD1M Curncy", "fieldData": [
            {"date": "2026-08-17", "PX_LAST": 1.1000, "SETTLE_DT": "2026-09-17"},
            {"date": "2026-08-18", "PX_LAST": 1.1010, "SETTLE_DT": "2026-09-18"},
            {"date": "2026-08-19", "PX_LAST": 1.1020, "SETTLE_DT": "2026-09-21"},
        ]}}]

    _install_fake_blpapi(monkeypatch, responder)
    session, service = pull_marks.open_session()
    out = pull_marks.fetch_historical_series(
        session, service, ["EURUSD1M Curncy"], ["PX_LAST", "SETTLE_DT"],
        date(2026, 8, 17), date(2026, 8, 19))
    assert set(out["EURUSD1M Curncy"]) == {"2026-08-17", "2026-08-18", "2026-08-19"}
    assert out["EURUSD1M Curncy"]["2026-08-18"] == {"PX_LAST": 1.1010, "SETTLE_DT": "2026-09-18"}


def test_fetch_historical_series_multiple_tickers_each_own_series(monkeypatch):
    from data.bloomberg import pull_marks

    def responder(request):
        return [
            {"securityData": {"security": "EURUSDSP Curncy",
                              "fieldData": [{"date": "2026-08-17", "PX_LAST": 0.0}]}},
            {"securityData": {"security": "EURUSD1M Curncy",
                              "fieldData": [{"date": "2026-08-17", "PX_LAST": 55.0}]}},
        ]

    _install_fake_blpapi(monkeypatch, responder)
    session, service = pull_marks.open_session()
    out = pull_marks.fetch_historical_series(
        session, service, ["EURUSDSP Curncy", "EURUSD1M Curncy"], ["PX_LAST"],
        date(2026, 8, 17), date(2026, 8, 17))
    assert out["EURUSDSP Curncy"]["2026-08-17"] == {"PX_LAST": 0.0}
    assert out["EURUSD1M Curncy"]["2026-08-17"] == {"PX_LAST": 55.0}


def test_fetch_historical_series_point_with_no_date_is_skipped_never_guessed(monkeypatch):
    from data.bloomberg import pull_marks

    def responder(request):
        return [{"securityData": {"security": "EURUSD1M Curncy", "fieldData": [
            {"PX_LAST": 1.10},  # no "date" element at all
            {"date": "2026-08-18", "PX_LAST": 1.1010},
        ]}}]

    _install_fake_blpapi(monkeypatch, responder)
    session, service = pull_marks.open_session()
    out = pull_marks.fetch_historical_series(
        session, service, ["EURUSD1M Curncy"], ["PX_LAST"], date(2026, 8, 17), date(2026, 8, 18))
    assert list(out["EURUSD1M Curncy"]) == ["2026-08-18"]


# =========================================================================== fwd_curve.historical_points_by_day
def test_historical_points_by_day_builds_one_curve_per_day():
    from data.bloomberg import fwd_curve

    tenor_series = {
        "EURUSD1M Curncy": {
            "2026-08-17": {"PX_LAST": 1.1010, "SETTLE_DT": "2026-09-17"},
            "2026-08-18": {"PX_LAST": 1.1015, "SETTLE_DT": "2026-09-18"},
        },
        "EURUSD3M Curncy": {
            "2026-08-17": {"PX_LAST": 1.1030, "SETTLE_DT": "2026-11-17"},
        },
    }
    by_day = fwd_curve.historical_points_by_day(
        tenor_series, {"1M": "EURUSD1M Curncy", "3M": "EURUSD3M Curncy"})
    assert by_day["2026-08-17"] == [(date(2026, 9, 17), 1.1010), (date(2026, 11, 17), 1.1030)]
    assert by_day["2026-08-18"] == [(date(2026, 9, 18), 1.1015)]


def test_historical_points_by_day_drops_incomplete_or_non_positive_points():
    from data.bloomberg import fwd_curve

    tenor_series = {
        "EURUSD1M Curncy": {
            "2026-08-17": {"PX_LAST": 1.1010},                                  # no SETTLE_DT
            "2026-08-18": {"SETTLE_DT": "2026-09-18"},                          # no PX_LAST
            "2026-08-19": {"PX_LAST": 0.0, "SETTLE_DT": "2026-09-19"},          # non-positive
            "2026-08-20": {"PX_LAST": 1.10, "SETTLE_DT": "not-a-date"},         # unparseable date
        },
    }
    by_day = fwd_curve.historical_points_by_day(tenor_series, {"1M": "EURUSD1M Curncy"})
    assert by_day == {}


# =========================================================================== request_fwd_curves correlation id (2026-09-18)
def test_request_fwd_curves_discards_late_reply_with_mismatched_correlation_id(monkeypatch):
    """Found live: a late reply to an unrelated, already-timed-out request (tagged with a
    DIFFERENT correlationId) used to be read as if it were this request's own RESPONSE
    event and broke the wait loop before the real FWD_CURVE data ever arrived, leaving
    every ticker at its "no response" sentinel -- silently treated downstream as an empty
    curve. The stale event here carries zero messages (as an unrelated event would) and
    must not end the loop; only the later, correctly-tagged event may."""
    from data.bloomberg import fwd_curve, pull_marks

    def responder(request, correlation_id):
        assert request.req_type == "ReferenceDataRequest"
        blp = sys.modules["blpapi"]
        stale_cid = blp.CorrelationId(999999)
        # A securityError response needs no bulk-table element walk (element_rows'
        # numElements()/getElement(j) machinery is exercised elsewhere) -- it's enough
        # here to prove the real, correctly-tagged event is what actually got processed,
        # not the stale one.
        real = [{"securityData": [{"security": "EURUSD Curncy",
                                   "securityError": {"message": "not authorised"}}]}]
        return blp.MultiEvent([
            ([], "RESPONSE", stale_cid),          # a stale, unrelated RESPONSE event
            (real, "RESPONSE", correlation_id),   # the real one, correctly tagged
        ])

    _install_fake_blpapi(monkeypatch, responder)
    blp = sys.modules["blpapi"]
    session, service = pull_marks.open_session()
    result = fwd_curve.request_fwd_curves(blp, session, service, ["EURUSD Curncy"])
    # Proves the real event was read (not the stale one's "no response for ticker"
    # sentinel, and not skipped past because the stale event ended the loop early).
    assert result["EURUSD Curncy"]["error"] == "securityError: not authorised"


def test_pull_marks_fwd_curve_bulk_value_falls_back_to_tenor(monkeypatch):
    # A real terminal can return FWD_CURVE as a bulk field (a list) when the override
    # doesn't pin it to a scalar; float(list) would raise TypeError. Must fall back to
    # tenor interpolation instead of crashing.
    ref_data = {
        ("USDJPY Curncy", "FWD_CURVE"): [{"row": 1}, {"row": 2}],  # non-scalar
        ("USDJPY Curncy", "FWD_POINTS_SCALE"): 100.0,
        ("USDJPYSP Curncy", "PX_LAST"): 0.0,
        ("USDJPYSP Curncy", "SETTLE_DT"): "2026-08-19",
        ("USDJPY1W Curncy", "PX_LAST"): 70.0,
        ("USDJPY1W Curncy", "SETTLE_DT"): "2026-08-24",
    }

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        sec_list = []
        for t in request.securities:
            row = {}
            for f in request.fields:
                val = ref_data.get((t, f))
                if val is not None:
                    row[f] = val
            sec_list.append({"security": t, "fieldData": row})
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)
    from data.bloomberg import pull_marks

    spot_by_instrument = {"USDJPY": 149.85}
    session, service = pull_marks.open_session()
    rows, warnings, failures = pull_marks.build_fwd_outright_rows(
        session, service,
        [pull_marks.RequestRow("USDJPY", "USDJPY Curncy", "2026-08-20", "FWD_OUTRIGHT")],
        date(2026, 8, 17), spot_by_instrument,
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "BBG_INTERP"
    assert any("non-scalar" in w for w in warnings)


def test_check_not_stale_refuses_and_allows_override():
    from data.bloomberg.pull_marks import RequestRow, StaleAsOfError, check_not_stale

    requests = [RequestRow("EURUSD", "EURUSD Curncy", "2026-09-16", "FWD_OUTRIGHT")]
    today = date(2026, 8, 18)
    as_of = date(2026, 8, 17)

    with pytest.raises(StaleAsOfError):
        check_not_stale(as_of, requests, allow_stale=False, today=today)

    # allowed with the override flag: no exception.
    check_not_stale(as_of, requests, allow_stale=True, today=today)

    # as_of == today: never refused, regardless of the flag.
    check_not_stale(today, requests, allow_stale=False, today=today)

    # no FWD_OUTRIGHT rows: never refused, regardless of the flag.
    spot_only = [RequestRow("EURUSD", "EURUSD Curncy", "2026-08-17", "SPOT")]
    check_not_stale(as_of, spot_only, allow_stale=False, today=today)


# =========================================================================== diagnostics
def _full_probe_responder(ref_data, hist_data):
    def responder(request):
        if request.req_type == "HistoricalDataRequest":
            # S-4: answer every requested security (see the analogous comment above).
            field = request.fields[0]
            out = []
            for ticker in request.securities:
                value = hist_data.get(ticker)
                field_data = [{field: value}] if value is not None else []
                out.append({"securityData": {"security": ticker, "fieldData": field_data}})
            return out
        elif request.req_type == "ReferenceDataRequest":
            sec_list = []
            for t in request.securities:
                row = {}
                for f in request.fields:
                    val = ref_data.get((t, f))
                    if val is not None:
                        row[f] = val
                sec_list.append({"security": t, "fieldData": row})
            return [{"securityData": sec_list}]
        elif request.req_type == "IntradayBarRequest":
            # 2026-09-21 probe step `intraday_close_bar`: one hourly bar, starting when asked
            return [{"barData": {"barTickData": [{"time": request.startDateTime, "close": 1.1712}]}}]
        raise AssertionError(f"unexpected request type {request.req_type}")
    return responder


def test_pull_marks_probe_writes_diag_and_diagnose_reads_it(monkeypatch, tmp_path):
    ref_data = {
        ("EURUSD Curncy", "PX_LAST"): 1.1000,
        ("EURUSD Curncy", "FWD_CURVE"): 1.1050,  # primary candidate "succeeds"
        ("EURUSD Curncy", "FWD_POINTS_SCALE"): 10000.0,
        ("EURUSD1M Curncy", "PX_LAST"): 30.0,
        ("EURUSD1M Curncy", "SETTLE_DT"): "2026-09-19",
        ("EURUSD3M Curncy", "PX_LAST"): 90.0,
        ("EURUSD3M Curncy", "SETTLE_DT"): "2026-11-19",
    }
    hist_data = {
        "EURUSD Curncy": 1.1000,
        "ESU6 Index": 4500.25,
    }
    _install_fake_blpapi(monkeypatch, _full_probe_responder(ref_data, hist_data))
    from data.bloomberg import pull_report as diagnose, pull_marks

    out = tmp_path / "probe"
    exit_code = pull_marks.main(["--probe", "--as-of", AS_OF, "--out", str(out)])
    assert exit_code == 0

    diag_path = Path(str(out) + ".diag.json")
    assert diag_path.exists()
    diag = diagnose.load_diag(diag_path)

    assert diag["summary"]["mode"] == "probe"
    probe_names = {r["probe_name"] for r in diag["requests"] if r.get("probe_name")}
    assert "session_start" in probe_names
    assert "fwd_outright_direct_primary" in probe_names
    assert "fwd_outright_direct_alt_reference_date" in probe_names
    assert "fwd_outright_direct_alt_fwd_outright_field" in probe_names
    assert "tenor_1m" in probe_names
    assert "es_settle_px_settle" in probe_names

    report = diagnose.render_report(diag)
    assert "probe results" in report
    assert "open questions 27-31" in report
    assert "27." in report
    assert "fwd_outright_direct_primary" in report
    # the primary candidate returned a value -> should show up as OK evidence for Q27.
    assert "no evidence" not in report.split("27.")[1].split("28.")[0]


def test_pull_marks_normal_run_diag_and_diagnose(monkeypatch, tmp_path):
    ref_data = {
        ("EURUSD Curncy", "PX_LAST"): 1.1000,
        ("EURUSD Curncy", "FWD_POINTS_SCALE"): 10000.0,
        ("EURUSDSP Curncy", "PX_LAST"): 0.0,
        ("EURUSDSP Curncy", "SETTLE_DT"): "2026-08-19",
        ("EURUSD1W Curncy", "PX_LAST"): 50.0,
        ("EURUSD1W Curncy", "SETTLE_DT"): "2026-08-24",
        ("USDJPY Curncy", "FWD_CURVE"): 149.85,
    }
    hist_data = {
        "ESU6 Index": 4500.25,
        "EURUSD Curncy": 1.1000,
    }
    _install_fake_blpapi(monkeypatch, _responder_ref_and_hist(ref_data, hist_data))
    from data.bloomberg import pull_report as diagnose, pull_marks

    request_path = tmp_path / "request.csv"
    with request_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pull_marks.REQUEST_COLUMNS)
        w.writerow(["EURUSD", "EURUSD Curncy", AS_OF, "SPOT"])
        w.writerow(["EURUSD", "EURUSD Curncy", "2026-08-20", "FWD_OUTRIGHT"])
        w.writerow(["USDJPY", "USDJPY Curncy", "2026-09-01", "FWD_OUTRIGHT"])
        w.writerow(["ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX"])

    out_path = tmp_path / "marks.csv"
    exit_code = pull_marks.main(
        ["--request", str(request_path), "--as-of", AS_OF, "--out", str(out_path), "--allow-stale-as-of"]
    )
    assert exit_code == 0
    assert out_path.exists()

    diag_path = Path(str(out_path) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    assert diag["summary"]["mode"] == "pull"
    assert diag["summary"]["outcome"] == "OK"
    assert diag["summary"]["marks_csv_partial"] is False
    assert not diagnose.has_failure(diag)

    outcomes = {(r["instrument_id"], r["settle_date"]): r["outcome"] for r in diag["fwd_outright_results"]}
    assert outcomes[("EURUSD", "2026-08-20")] == "interp"
    assert outcomes[("USDJPY", "2026-09-01")] == "direct"
    assert diag["interpolations"]  # the EURUSD fallback recorded tenor points/scale/result

    report = diagnose.render_report(diag)
    assert "EURUSD 2026-08-20: interp" in report
    assert "USDJPY 2026-09-01: direct" in report


def test_pull_marks_timeout_is_nonzero_exit_and_partial_csv(monkeypatch, tmp_path):
    ref_data = {
        ("EURUSD Curncy", "PX_LAST"): 1.1000,
    }

    def responder(request):
        if request.req_type == "HistoricalDataRequest":
            if "ESU6 Index" in request.securities:
                # Simulate a stalled FUTURE_PX request: nextEvent times out, no data.
                return [], "TIMEOUT"
            # S-4: answer every requested security (see the analogous comment above).
            field = request.fields[0]
            out = []
            for ticker in request.securities:
                value = ref_data.get((ticker, field))
                field_data = [{field: value}] if value is not None else []
                out.append({"securityData": {"security": ticker, "fieldData": field_data}})
            return out
        raise AssertionError(f"unexpected request type {request.req_type}")

    _install_fake_blpapi(monkeypatch, responder)
    from data.bloomberg import pull_report as diagnose, pull_marks

    request_path = tmp_path / "request.csv"
    with request_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pull_marks.REQUEST_COLUMNS)
        w.writerow(["EURUSD", "EURUSD Curncy", AS_OF, "SPOT"])
        w.writerow(["ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX"])

    out_path = tmp_path / "marks.csv"
    exit_code = pull_marks.main(["--request", str(request_path), "--as-of", AS_OF, "--out", str(out_path)])
    assert exit_code != 0

    # A partial CSV is still written (SPOT succeeded) but the run must never claim success.
    assert out_path.exists()
    with out_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["mark_type"] == "SPOT"

    diag_path = Path(str(out_path) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    assert diag["summary"]["outcome"] == "FAILED"
    assert diag["summary"]["marks_csv_partial"] is True
    assert diag["summary"]["exit_code"] != 0
    assert any(f["stage"] == "FUTURE_PX" for f in diag["summary"]["failures"])
    assert diagnose.has_failure(diag)

    timeout_reqs = [r for r in diag["requests"] if r["classification"] == "TIMEOUT"]
    assert timeout_reqs
    assert timeout_reqs[0]["request_type"] == "HistoricalDataRequest"

    report = diagnose.render_report(diag)
    assert "TIMEOUT" in report
    assert "marks_csv_partial: True" in report


def test_pull_marks_field_exception_classified_and_reported(monkeypatch, tmp_path):
    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        assert request.fields == ["FWD_CURVE"]
        sec_list = [{
            "security": request.securities[0],
            "fieldData": {},
            "fieldExceptions": [
                {"fieldId": "FWD_CURVE", "errorInfo": {"message": "NOT_APPLICABLE_TO_REF_DATA"}}
            ],
        }]
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)
    from data.bloomberg import pull_report as diagnose, pull_marks

    diag = pull_marks.Diagnostics()
    session, service = pull_marks.open_session()
    val = pull_marks.fetch_fwd_outright_direct(
        session, service, "USDJPY Curncy", "2026-09-01", warnings=[], diag=diag,
        tag={"purpose": "fwd_outright_direct", "instrument_id": "USDJPY", "settle_date": "2026-09-01"},
    )
    assert val is None  # no usable value -> caller falls back to tenor interpolation

    rec = next(r for r in diag.requests if r["request_type"] == "ReferenceDataRequest")
    assert rec["classification"] == "FIELD_EXCEPTION"
    assert "NOT_APPLICABLE_TO_REF_DATA" not in rec["detail"]  # detail only lists tickers, not messages
    assert rec["raw_response"][0]["fieldExceptions"][0]["message"] == "NOT_APPLICABLE_TO_REF_DATA"

    diag_path = tmp_path / "manual.diag.json"
    pull_marks.write_diagnostics(diag, diag_path)
    loaded = diagnose.load_diag(diag_path)
    report = diagnose.render_report(loaded)
    assert "FIELD_EXCEPTION" in report
    assert "NOT_APPLICABLE_TO_REF_DATA" in report


def test_pull_marks_unhandled_exception_still_writes_diag_with_traceback(monkeypatch, tmp_path):
    _install_fake_blpapi(monkeypatch, lambda request: [])
    from data.bloomberg import pull_report as diagnose, pull_marks

    out_path = tmp_path / "marks.csv"
    missing_request = tmp_path / "does-not-exist.csv"
    exit_code = pull_marks.main(
        ["--request", str(missing_request), "--as-of", AS_OF, "--out", str(out_path)]
    )
    assert exit_code == 3
    assert not out_path.exists()  # never reached the point of writing a marks CSV

    diag_path = Path(str(out_path) + ".diag.json")
    assert diag_path.exists()
    diag = diagnose.load_diag(diag_path)
    assert diag["exception"] is not None
    assert diag["exception"]["type"] in ("FileNotFoundError", "OSError")
    assert "Traceback" in diag["exception"]["traceback"]
    assert diagnose.has_failure(diag)

    report = diagnose.render_report(diag)
    assert "unhandled exception" in report


# =============================================================== C-A / C-B / W-* fixes
def test_pull_marks_security_error_on_spot_is_partial_nonzero_exit_and_in_failures(monkeypatch, tmp_path):
    def responder(request):
        assert request.req_type == "HistoricalDataRequest"
        # S-4: answer every requested security (see the analogous comment above).
        return [{"securityData": {"security": ticker, "fieldData": [],
                                   "securityError": {"message": "UNKNOWN_SECURITY"}}}
                for ticker in request.securities]

    _install_fake_blpapi(monkeypatch, responder)
    from data.bloomberg import pull_report as diagnose, pull_marks

    request_path = tmp_path / "request.csv"
    with request_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pull_marks.REQUEST_COLUMNS)
        w.writerow(["EURUSD", "EURUSD Curncy", AS_OF, "SPOT"])

    out_path = tmp_path / "marks.csv"
    exit_code = pull_marks.main(["--request", str(request_path), "--as-of", AS_OF, "--out", str(out_path)])
    assert exit_code != 0

    with out_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows == []  # SECURITY_ERROR -> no SPOT row written

    diag_path = Path(str(out_path) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    assert diag["summary"]["marks_csv_partial"] is True
    assert diag["summary"]["exit_code"] != 0
    fkeys = {(f.get("instrument_id"), f.get("settle_date"), f.get("mark_type")) for f in diag["summary"]["failures"]}
    assert ("EURUSD", AS_OF, "SPOT") in fkeys
    assert diagnose.has_failure(diag)


def test_pull_marks_probe_continues_after_non_candidate_step_failure(monkeypatch, tmp_path):
    def responder(request):
        if request.req_type == "ReferenceDataRequest" and request.fields == ["PX_LAST"]:
            # spot_reference (non-candidate, answers_question=29): force a FIELD_EXCEPTION.
            sec = {"security": request.securities[0], "fieldData": {},
                   "fieldExceptions": [{"fieldId": "PX_LAST", "errorInfo": {"message": "BAD_FLD"}}]}
            return [{"securityData": [sec]}]
        if request.req_type == "HistoricalDataRequest":
            # S-4: answer every requested security (see the analogous comment above).
            field = request.fields[0]
            return [
                {"securityData": {"security": ticker,
                                   "fieldData": [{field: 1.1 if ticker == "EURUSD Curncy" else 4500.25}]}}
                for ticker in request.securities
            ]
        # every other ReferenceDataRequest (candidates, tenor, scale): empty -> NO_VALUE,
        # not a failure classification.
        sec_list = [{"security": t, "fieldData": {}} for t in request.securities]
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)
    from data.bloomberg import pull_report as diagnose, pull_marks

    out = tmp_path / "probe"
    exit_code = pull_marks.main(["--probe", "--as-of", AS_OF, "--out", str(out)])
    assert exit_code == 0  # probe mode always exits 0 once the session/service open

    diag_path = Path(str(out) + ".diag.json")
    diag = diagnose.load_diag(diag_path)

    # Every fixed probe step must still have run, despite the early (non-candidate)
    # failure -- one failing step must never abort the rest of the exploratory sequence.
    probe_names = {r["probe_name"] for r in diag["requests"] if r.get("probe_name")}
    assert probe_names == {
        "session_start", "spot_reference", "spot_historical", "fwd_outright_direct_primary",
        "fwd_outright_direct_alt_reference_date", "fwd_outright_direct_alt_fwd_outright_field",
        "tenor_1m", "tenor_3m", "fwd_points_scale", "es_settle_px_settle", "es_settle_px_last",
        "intraday_close_bar",                                    # 2026-09-21: the 15:00 New York close bar
    }
    assert diag["summary"]["outcome"] == "PROBE_COMPLETE_WITH_FAILURES"
    assert diag["summary"]["outcome"] != "OK"
    assert diagnose.has_failure(diag)


def test_pull_marks_probe_flags_non_scalar_fwd_curve_candidate(monkeypatch, tmp_path):
    ref_data = {
        ("EURUSD Curncy", "PX_LAST"): 1.1000,
        ("EURUSD Curncy", "FWD_CURVE"): [{"row": 1}, {"row": 2}],  # bulk field, non-scalar
    }
    hist_data = {"EURUSD Curncy": 1.1000, "ESU6 Index": 4500.25}
    _install_fake_blpapi(monkeypatch, _full_probe_responder(ref_data, hist_data))
    from data.bloomberg import pull_report as diagnose, pull_marks

    out = tmp_path / "probe"
    exit_code = pull_marks.main(["--probe", "--as-of", AS_OF, "--out", str(out)])
    assert exit_code == 0

    diag_path = Path(str(out) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    primary = next(r for r in diag["requests"] if r.get("probe_name") == "fwd_outright_direct_primary")
    assert primary["classification"] == "OK"
    assert primary.get("scalar") is False
    assert "non-scalar" in primary["detail"]

    report = diagnose.render_report(diag)
    assert "scalar=False" in report


def test_pull_marks_late_response_after_timeout_is_discarded_and_recorded(monkeypatch, tmp_path):
    from data.bloomberg import pull_marks

    captured = {}

    def responder(request, correlation_id):
        if request.req_type == "HistoricalDataRequest":
            ticker = request.securities[0]
            if ticker == "EURUSD Curncy":
                # SPOT request stalls: capture its correlation id, then time out.
                captured["spot_cid"] = correlation_id
                return [], "TIMEOUT"
            if ticker == "ESU6 Index":
                # FUTURE_PX request: a late, stale response for the (already timed-out)
                # SPOT request arrives first, tagged with the OLD correlation id, followed
                # by the real FUTURE_PX response tagged with the current one.
                stale = [{"securityData": {"security": "EURUSD Curncy", "fieldData": [{"PX_LAST": 1.1}]}}]
                real = [{"securityData": {"security": "ESU6 Index", "fieldData": [{"PX_SETTLE": 4500.25}]}}]
                MultiEventCls = sys.modules["blpapi"].MultiEvent
                return MultiEventCls([
                    (stale, "RESPONSE", captured["spot_cid"]),
                    (real, "RESPONSE", correlation_id),
                ])
        raise AssertionError(f"unexpected request {request.req_type} {request.securities}")

    _install_fake_blpapi(monkeypatch, responder)

    request_rows = [
        pull_marks.RequestRow("EURUSD", "EURUSD Curncy", AS_OF, "SPOT"),
        pull_marks.RequestRow("ESU6 Index", "ESU6 Index", "2026-09-18", "FUTURE_PX"),
    ]
    diag = pull_marks.Diagnostics()
    session, service = pull_marks.open_session()
    rows, warnings, failures = pull_marks.run(session, service, request_rows, date(2026, 8, 17), diag)

    # FUTURE_PX must resolve correctly -- the stale EURUSD message must neither be
    # mistaken for FUTURE_PX's own response nor stop the loop before the real event.
    future_rows = [r for r in rows if r["mark_type"] == "FUTURE_PX"]
    assert len(future_rows) == 1
    assert future_rows[0]["instrument_id"] == "ESU6 Index"
    assert math.isclose(future_rows[0]["value"], 4500.25)

    # SPOT failed with TIMEOUT, unrelated to (and not polluted by) the late response.
    assert any(f.get("mark_type") == "SPOT" and f.get("classification") == "TIMEOUT" for f in failures)

    future_req = next(r for r in diag.requests if r.get("tickers") == ["ESU6 Index"])
    assert future_req["late_responses"], "the stale EURUSD message must be recorded, not silently dropped"


def test_pull_marks_exit_zero_implies_all_requested_rows_written(monkeypatch, tmp_path):
    ref_data = {}
    hist_data = {"EURUSD Curncy": 1.1000}
    _install_fake_blpapi(monkeypatch, _responder_ref_and_hist(ref_data, hist_data))
    from data.bloomberg import pull_report as diagnose, pull_marks

    request_path = tmp_path / "request.csv"
    with request_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pull_marks.REQUEST_COLUMNS)
        w.writerow(["EURUSD", "EURUSD Curncy", AS_OF, "SPOT"])

    out_path = tmp_path / "marks.csv"
    exit_code = pull_marks.main(["--request", str(request_path), "--as-of", AS_OF, "--out", str(out_path)])
    assert exit_code == 0

    diag_path = Path(str(out_path) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    assert diag["summary"]["exit_code"] == 0
    assert diag["summary"]["written_rows"] == diag["summary"]["requested_rows"]
    assert diag["summary"]["marks_csv_partial"] is False
    assert diag["summary"]["failures"] == []


def test_pull_marks_environment_block_present_on_stale_refusal_exit(monkeypatch, tmp_path):
    _install_fake_blpapi(monkeypatch, lambda request: [])
    from data.bloomberg import pull_report as diagnose, pull_marks

    request_path = tmp_path / "request.csv"
    with request_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pull_marks.REQUEST_COLUMNS)
        w.writerow(["EURUSD", "EURUSD Curncy", "2026-09-16", "FWD_OUTRIGHT"])

    out_path = tmp_path / "marks.csv"
    exit_code = pull_marks.main(
        ["--request", str(request_path), "--as-of", "2020-01-01", "--out", str(out_path)]
    )
    assert exit_code == 2

    diag_path = Path(str(out_path) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    assert diag["environment"]
    assert diag["environment"].get("host") == "localhost"
    assert diag["environment"].get("port") == 8194

    report = diagnose.render_report(diag)
    assert "environment" in report


def test_write_diagnostics_creates_missing_output_directory(tmp_path):
    from data.bloomberg import pull_marks

    diag = pull_marks.Diagnostics()
    diag.record_environment("localhost", 8194, True, True)
    nested = tmp_path / "does" / "not" / "exist" / "out.diag.json"
    pull_marks.write_diagnostics(diag, nested)
    assert nested.exists()


def test_write_diagnostics_sanitizes_nan_and_inf():
    import json

    from data.bloomberg import pull_marks

    diag = pull_marks.Diagnostics()
    diag.record_interpolation("EURUSD", "2026-09-16", [], float("nan"), float("inf"), float("-inf"), 1.1)
    d = pull_marks._sanitize_for_json(diag.to_dict())
    text = json.dumps(d)  # must not raise and must contain no bare NaN/Infinity tokens
    assert "NaN" not in text.replace('"NaN"', "")  # only the quoted string form is allowed
    assert "Infinity" not in text


# =============================================================== round-2 review (W-*/S-*)
def test_write_marks_csv_creates_missing_output_directory(tmp_path):
    """S-1: a missing parent directory must not discard rows already pulled -- write_marks_csv
    now mkdir(parents=True, exist_ok=True)s the output directory first."""
    from data.bloomberg import pull_marks

    nested = tmp_path / "does" / "not" / "exist" / "marks.csv"
    rows = [{
        "as_of_date": AS_OF, "instrument_id": "EURUSD", "settle_date": AS_OF, "mark_type": "SPOT",
        "value": 1.1, "source": "BBG_BFXFORWARD", "snapped_at": "2026-08-17T15:00:00-04:00",
    }]
    pull_marks.write_marks_csv(rows, nested)
    assert nested.exists()
    with nested.open(newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f))[0]["instrument_id"] == "EURUSD"


def test_pull_marks_bad_as_of_is_argument_error_exit_2(monkeypatch, tmp_path):
    """S-2: a malformed --as-of is an argument error (exit 2, validated with
    date.fromisoformat before run_pull()/run_probe()), not the generic unhandled-exception
    path (exit 3) -- and a diag JSON documenting the rejection is still written."""
    _install_fake_blpapi(monkeypatch, lambda request: [])
    from data.bloomberg import pull_report as diagnose, pull_marks

    request_path = tmp_path / "request.csv"
    with request_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pull_marks.REQUEST_COLUMNS)
        w.writerow(["EURUSD", "EURUSD Curncy", AS_OF, "SPOT"])

    out_path = tmp_path / "marks.csv"
    exit_code = pull_marks.main(
        ["--request", str(request_path), "--as-of", "not-a-date", "--out", str(out_path)]
    )
    assert exit_code == 2
    assert not out_path.exists()  # never reached the point of pulling/writing marks

    diag_path = Path(str(out_path) + ".diag.json")
    assert diag_path.exists()
    diag = diagnose.load_diag(diag_path)
    assert diag["summary"]["exit_code"] == 2
    assert diag["summary"]["outcome"] != "OK"
    assert diagnose.has_failure(diag)
    assert any(f.get("stage") == "as_of_validation" for f in diag["summary"]["failures"])


def test_pull_marks_partial_batch_field_exception_with_all_keys_written_is_not_a_failure(monkeypatch, tmp_path):
    """W-1: one tenor ticker (of eight) in the fallback interpolation batch comes back with
    a fieldException, but the target settle_date only needs the bracketing SP/1W tenors, so
    interpolation still succeeds and every requested key is written. This must exit 0 with
    outcome OK, and diagnose.has_failure() must be False in pull mode -- a request-level
    classification alone (FIELD_EXCEPTION on the unrelated 1Y ticker) must not flip the
    exit code when nothing was actually lost. The fieldException message must still show up
    in the report (counts/detail section) so it stays visible."""
    ref_data = {
        ("EURUSD Curncy", "FWD_POINTS_SCALE"): 10000.0,
        ("EURUSDSP Curncy", "PX_LAST"): 0.0,
        ("EURUSDSP Curncy", "SETTLE_DT"): "2026-08-19",
        ("EURUSD1W Curncy", "PX_LAST"): 50.0,
        ("EURUSD1W Curncy", "SETTLE_DT"): "2026-08-24",
        # 2W/1M/2M/3M/6M simply have no data (ordinary NO_VALUE, not requested by name below).
    }
    hist_data = {"EURUSD Curncy": 1.1000}

    def responder(request):
        if request.req_type == "HistoricalDataRequest":
            field = request.fields[0]
            out = []
            for ticker in request.securities:
                value = hist_data.get(ticker)
                field_data = [{field: value}] if value is not None else []
                out.append({"securityData": {"security": ticker, "fieldData": field_data}})
            return out
        elif request.req_type == "ReferenceDataRequest":
            sec_list = []
            for t in request.securities:
                if t == "EURUSD1Y Curncy":
                    # The one ticker of eight that comes back with a fieldException --
                    # irrelevant to interpolating a settle_date that only needs SP/1W.
                    sec_list.append({
                        "security": t, "fieldData": {},
                        "fieldExceptions": [{"fieldId": "PX_LAST", "errorInfo": {"message": "BAD_FLD"}}],
                    })
                    continue
                row = {}
                for f in request.fields:
                    val = ref_data.get((t, f))
                    if val is not None:
                        row[f] = val
                sec_list.append({"security": t, "fieldData": row})
            return [{"securityData": sec_list}]
        raise AssertionError(f"unexpected request type {request.req_type}")

    _install_fake_blpapi(monkeypatch, responder)
    from data.bloomberg import pull_report as diagnose, pull_marks

    request_path = tmp_path / "request.csv"
    with request_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pull_marks.REQUEST_COLUMNS)
        w.writerow(["EURUSD", "EURUSD Curncy", AS_OF, "SPOT"])
        w.writerow(["EURUSD", "EURUSD Curncy", "2026-08-20", "FWD_OUTRIGHT"])  # -> SP/1W interp only

    out_path = tmp_path / "marks.csv"
    exit_code = pull_marks.main(
        ["--request", str(request_path), "--as-of", AS_OF, "--out", str(out_path), "--allow-stale-as-of"]
    )
    assert exit_code == 0

    with out_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2  # both SPOT and the interpolated FWD_OUTRIGHT were written

    diag_path = Path(str(out_path) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    assert diag["summary"]["outcome"] == "OK"
    assert diag["summary"]["marks_csv_partial"] is False
    assert diag["summary"]["failures"] == []
    # The batched tenor request is still classified FIELD_EXCEPTION at the request level --
    # visible in counts/detail -- even though it didn't cause a partial CSV.
    assert any(r.get("classification") == "FIELD_EXCEPTION" for r in diag["requests"])
    assert not diagnose.has_failure(diag)  # <- the point of W-1

    report = diagnose.render_report(diag)
    assert "FIELD_EXCEPTION" in report
    assert "BAD_FLD" in report


def test_diagnose_probe_mode_still_flags_non_candidate_hard_failure(monkeypatch, tmp_path):
    """W-1: probe mode keeps the request-level check (unlike pull mode) -- a non-candidate
    probe step classified as a hard failure still makes has_failure() True even if outcome
    somehow ended up "OK" (defensive; run_probe() itself already sets a non-OK outcome in
    this situation, see test_pull_marks_probe_continues_after_non_candidate_step_failure)."""
    from data.bloomberg import pull_report as diagnose

    diag = {
        "summary": {"mode": "probe", "outcome": "OK", "marks_csv_partial": False, "failures": []},
        "requests": [
            {"probe_name": "spot_reference", "classification": "FIELD_EXCEPTION", "candidate": False},
        ],
    }
    assert diagnose.has_failure(diag)

    diag["requests"][0]["candidate"] = True
    assert not diagnose.has_failure(diag)


def test_probe_scalar_check_flags_fwd_outright_price_candidate(monkeypatch, tmp_path):
    """S-3: the probe's non-scalar detection previously only inspected fields named
    FWD_CURVE -- the FWD_OUTRIGHT_PRICE candidate (fwd_outright_direct_alt_fwd_outright_field)
    must get the same protection when Bloomberg returns it as a bulk value."""
    ref_data = {
        ("EURUSD Curncy", "PX_LAST"): 1.1000,
        ("EURUSD Curncy", "FWD_CURVE"): 1.1050,  # primary candidate: scalar, OK
        ("EURUSD Curncy", "FWD_OUTRIGHT_PRICE"): [{"row": 1}, {"row": 2}],  # bulk, non-scalar
    }
    hist_data = {"EURUSD Curncy": 1.1000, "ESU6 Index": 4500.25}
    _install_fake_blpapi(monkeypatch, _full_probe_responder(ref_data, hist_data))
    from data.bloomberg import pull_report as diagnose, pull_marks

    out = tmp_path / "probe"
    exit_code = pull_marks.main(["--probe", "--as-of", AS_OF, "--out", str(out)])
    assert exit_code == 0

    diag_path = Path(str(out) + ".diag.json")
    diag = diagnose.load_diag(diag_path)
    alt = next(r for r in diag["requests"] if r.get("probe_name") == "fwd_outright_direct_alt_fwd_outright_field")
    assert alt["classification"] == "OK"
    assert alt.get("scalar") is False
    assert "non-scalar" in alt["detail"]

    report = diagnose.render_report(diag)
    assert "scalar=False" in report


def test_pull_marks_missing_tzdata_writes_diag_and_exits_6(monkeypatch, tmp_path):
    """Item 39: main() must resolve _ny() immediately after Diagnostics() is created,
    before the first record_environment() call, so a machine missing the `tzdata`
    package (ZoneInfoNotFoundError constructing America/New_York) still gets a diag
    JSON + stderr hint + exit 6, instead of crashing with no .diag.json written."""
    from data.bloomberg import pull_report as diagnose, pull_marks

    monkeypatch.setattr(pull_marks, "_NY_ZONE", None)  # clear _ny()'s cache

    def _raise(*args, **kwargs):
        raise pull_marks.ZoneInfoNotFoundError("no time zone found with key 'America/New_York'")

    monkeypatch.setattr(pull_marks, "ZoneInfo", _raise)  # simulate tzdata not installed

    out = tmp_path / "probe"
    exit_code = pull_marks.main(["--probe", "--as-of", AS_OF, "--out", str(out)])
    assert exit_code == 6

    diag_path = Path(str(out) + ".diag.json")
    assert diag_path.exists()
    diag = diagnose.load_diag(diag_path)
    assert diag["summary"]["exit_code"] == 6
    failures = diag["summary"]["failures"]
    tz_failure = next(f for f in failures if f.get("stage") == "tz_prerequisite")
    assert tz_failure["hint"] == "py -3 -m pip install tzdata"


# --------------------------------------------------------------------------- inventory.py
def _inventory_db():
    conn = schema.connect(":memory:")
    conn.execute("INSERT INTO instruments VALUES ('AUDUSD','FX','AUD','USD',1,0,'AUDUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    conn.execute("INSERT INTO trades VALUES ('a1','XLSX','AUDUSD','FX_FWD','a1','2026-08-10',-1e6,0.65,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trades VALUES ('j1','XLSX','USDJPY','FX_FWD','j1','2026-08-10',1e6,150.0,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trades VALUES ('f1','XLSX','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-16", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-16", 0.65, 1),
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-08-10", "2026-09-18", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-08-10", "2026-09-18", 150.0, 1),
        ("f1", 1, "NOTIONAL", "USD", 6 * 50 * 7528.25, "2026-08-10", "2026-09-18", 0, 0),
    ])
    conn.commit()
    return conn


def test_mark_inventory_statuses(tmp_path):
    """2026-09-18 user decision: BBG_INTERP is now the OFFICIAL fallback source for
    FWD_OUTRIGHT wherever no BBG_BFXFORWARD row exists for the same key (data/ingest
    /schema.py OFFICIAL_FALLBACK_SOURCE) -- 720 of 743 forwards in the reference book
    had no P&L before this, since a broken-date leg's only Bloomberg-servable value IS
    the interpolated one. mark_inventory reports that row OFFICIAL (source BBG_INTERP),
    not INTERP; inventory.STATUS_INTERP is now only reachable for a BBG_INTERP row of a
    mark_type other than FWD_OUTRIGHT (none has a fallback entry yet)."""
    from data.bloomberg import inventory
    conn = _inventory_db()
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-08-17", "AUDUSD", "2026-08-17", "SPOT", 0.66, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "AUDUSD", "2026-09-16", "FWD_OUTRIGHT", 0.665, "BBG_INTERP", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-09-18", "FWD_OUTRIGHT", 149.0, "MANUAL", "2026-08-17T15:00:00-04:00"),
    ])
    conn.commit()
    df = inventory.mark_inventory(conn, "2026-08-17")
    by = {(r.instrument_id, r.mark_type, r.settle_date): r.status for r in df.itertuples()}
    assert by[("AUDUSD", "SPOT", "2026-08-17")] == "OFFICIAL"
    assert by[("AUDUSD", "FWD_OUTRIGHT", "2026-09-16")] == "OFFICIAL"
    by_source = {(r.instrument_id, r.mark_type, r.settle_date): r.source for r in df.itertuples()}
    assert by_source[("AUDUSD", "FWD_OUTRIGHT", "2026-09-16")] == "BBG_INTERP"
    # MANUAL has no fallback entry in OFFICIAL_FALLBACK_SOURCE -- stays non-official.
    assert by[("USDJPY", "FWD_OUTRIGHT", "2026-09-18")] == "MANUAL"
    assert by[("USDJPY", "SPOT", "2026-08-17")] == "MISSING"
    assert by[("ESU6 Index", "FUTURE_PX", "2026-09-18")] == "MISSING"
    assert set(df["mark_type"]) == {"SPOT", "FWD_OUTRIGHT", "FUTURE_PX"}


def test_close_completeness(tmp_path):
    """2026-09-18: a day is complete only once SPOT, FWD_OUTRIGHT (per open leg's own
    settle_date) and FUTURE_PX (per open future) are all official -- SPOT alone used to
    be enough, silently leaving LTD(t) unpriced for every forward/future on that day."""
    from data.bloomberg import inventory
    conn = _inventory_db()
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-08-17", "AUDUSD", "2026-08-17", "SPOT", 0.66, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        ("2026-08-18", "AUDUSD", "2026-08-18", "SPOT", 0.67, "BBG_BFXFORWARD", "2026-08-18T15:00:00-04:00"),
        ("2026-08-18", "USDJPY", "2026-08-18", "SPOT", 150.5, "BBG_BFXFORWARD", "2026-08-18T15:00:00-04:00"),
        # 2026-08-18 completes every remaining need: FWD_OUTRIGHT for both pairs' own
        # settle dates, and FUTURE_PX for the one open future.
        ("2026-08-18", "AUDUSD", "2026-09-16", "FWD_OUTRIGHT", 0.665, "BBG_BFXFORWARD", "2026-08-18T15:00:00-04:00"),
        ("2026-08-18", "USDJPY", "2026-09-18", "FWD_OUTRIGHT", 149.0, "BBG_BFXFORWARD", "2026-08-18T15:00:00-04:00"),
        ("2026-08-18", "ESU6 Index", "2026-09-18", "FUTURE_PX", 7550.0, "BBG_BDH", "2026-08-18T15:00:00-04:00"),
    ])
    conn.commit()
    df = inventory.close_completeness(conn, "2026-08-17", "2026-08-18")
    rows = {r.as_of_date: r for r in df.itertuples()}
    # needed = 2 (SPOT+FWD_OUTRIGHT) per pair x2 pairs + 1 FUTURE_PX = 5.
    assert rows["2026-08-17"].needed == 5 and rows["2026-08-17"].present == 1 and not rows["2026-08-17"].complete
    assert {m["mark_type"] for m in rows["2026-08-17"].missing} == {"SPOT", "FWD_OUTRIGHT", "FUTURE_PX"}
    assert rows["2026-08-18"].needed == 5 and rows["2026-08-18"].present == 5 and rows["2026-08-18"].complete
    assert rows["2026-08-18"].missing == []


# --------------------------------------------------------------------------- inventory.py: stale_empty_pull_reason
# (2026-09-17: the Bloomberg PC diagnostics panel reported the last live-feed pull as PASS
# even though it had asked Bloomberg for 0 marks while the book genuinely needed some --
# see data.bloomberg.live.LiveFeed.trigger_now's docstring for the root cause.)
def test_stale_empty_pull_reason_none_when_nothing_needed(tmp_path):
    from data.bloomberg import inventory
    conn = schema.connect(":memory:")  # no trades at all -> genuinely nothing to price
    status = {"connected": True, "requested": 0, "time": "2026-09-17T15:23:23+08:00"}
    assert inventory.stale_empty_pull_reason(conn, status, "2026-09-17") is None


def test_stale_empty_pull_reason_none_when_status_did_not_connect_or_is_missing():
    from data.bloomberg import inventory
    conn = _inventory_db()  # has open FX legs -> marks are needed
    assert inventory.stale_empty_pull_reason(conn, None, "2026-08-17") is None
    assert inventory.stale_empty_pull_reason(conn, {"connected": False, "reason": "x"}, "2026-08-17") is None


def test_stale_empty_pull_reason_none_when_pull_actually_requested_something(tmp_path):
    from data.bloomberg import inventory
    conn = _inventory_db()
    status = {"connected": True, "requested": 26, "time": "t"}
    assert inventory.stale_empty_pull_reason(conn, status, "2026-08-17") is None


def test_stale_empty_pull_reason_fails_when_requested_zero_but_marks_now_needed(tmp_path):
    """The exact trap found on the Bloomberg PC: connected=True, requested=0 (the very
    first pull ran before any trade was in the database), but the book now has open FX
    legs, futures and options needing marks for as_of."""
    from data.bloomberg import inventory
    conn = _inventory_db()  # open AUDUSD/USDJPY forwards + an ESU6 future as of 2026-08-17
    status = {"connected": True, "requested": 0, "written": 0, "time": "2026-09-17T15:23:23+08:00"}
    reason = inventory.stale_empty_pull_reason(conn, status, "2026-08-17")
    assert reason is not None
    assert "2026-09-17T15:23:23+08:00" in reason
    assert "0 marks" in reason
    assert "Pull Bloomberg now" in reason
    assert "automatic" not in reason and "on request only" in reason


# --------------------------------------------------------------------------- manual.py
def test_write_manual_mark_is_visible_but_not_official(tmp_path):
    from data.bloomberg import manual, inventory
    conn = _inventory_db()
    manual.write_manual_mark(conn, "2026-08-17", "AUDUSD", "2026-09-16", "FWD_OUTRIGHT", 0.67)
    row = conn.execute("SELECT value, source FROM marks WHERE instrument_id='AUDUSD' AND mark_type='FWD_OUTRIGHT'").fetchone()
    assert row == (0.67, "MANUAL")
    official = conn.execute("SELECT * FROM marks_official WHERE instrument_id='AUDUSD' AND mark_type='FWD_OUTRIGHT'").fetchone()
    assert official is None                                     # MANUAL never wins FWD_OUTRIGHT
    df = inventory.mark_inventory(conn, "2026-08-17")
    row = df[(df.instrument_id == "AUDUSD") & (df.mark_type == "FWD_OUTRIGHT")].iloc[0]
    assert row["status"] == "MANUAL" and row["value"] == 0.67

    manual.write_manual_mark(conn, "2026-08-17", "AUDUSD", "2026-08-17", "DELTA", 0.5)
    official_delta = conn.execute(
        "SELECT value FROM marks_official WHERE instrument_id='AUDUSD' AND mark_type='DELTA'").fetchone()
    # Since 2026-09-17 DELTA is official from QL_OPTIONS_PRICER (engine/options); a
    # MANUAL delta is visible in `marks` but reconciliation-only, never official.
    assert official_delta is None


# =========================================================================== rates_marketdata.py
RATES_FIXTURE = REPO / "data" / "bloomberg" / "fixtures" / "ois_snapshot_v1.json"


def test_ois_index_and_ticker_map_cover_scope_currencies():
    from data.bloomberg import rates_marketdata as rmd

    expected = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD"}
    assert set(rmd.OIS_INDEX) == expected
    assert set(rmd.OIS_CURVES) == expected
    assert rmd.OIS_INDEX["USD"] == "SOFR"
    for ccy in expected:
        specs = rmd.ois_curve(ccy)
        assert len(specs) >= 4
        assert all(isinstance(s.tenor, str) and s.ticker for s in specs)
        assert rmd.ois_fixing_ticker(ccy)
    assert rmd.ois_bbg_curve_id("USD") == "YCSW0490 Index"
    assert rmd.ois_bbg_curve_id("USD") == rmd.OIS_CURVES["USD"]["bbg_curve_id"]


def test_ois_curve_unknown_currency_raises():
    from data.bloomberg import rates_marketdata as rmd

    with pytest.raises(rmd.TickerMapError):
        rmd.ois_curve("XXX")
    with pytest.raises(rmd.TickerMapError):
        rmd.ois_fixing_ticker("XXX")
    assert rmd.ois_bbg_curve_id("XXX") is None


def test_tenor_to_days_sorting_and_scale_quote():
    from data.bloomberg import rates_marketdata as rmd
    import decimal

    assert rmd.tenor_to_days("1W") == 7
    assert rmd.tenor_to_days("2Z") == 14  # Bloomberg weekly-ticker convention
    assert rmd.tenor_to_days("1M") == 30
    assert rmd.tenor_to_days("1Y") == 365
    assert rmd.tenor_to_days("1w") == rmd.tenor_to_days("1W")  # normalised

    with pytest.raises(ValueError):
        rmd.tenor_to_days("bogus")

    assert rmd.scale_quote(decimal.Decimal("3.98")) == decimal.Decimal("3.98") / 100


def test_build_refdata_and_histdata_specs_are_pure():
    from data.bloomberg import rates_marketdata as rmd
    from datetime import date as d

    specs = rmd.build_refdata_spec(["A", "B", "C"], ["PX_LAST"], chunk=2)
    assert len(specs) == 2
    assert specs[0]["securities"] == ["A", "B"]
    assert specs[1]["securities"] == ["C"]
    assert specs[0]["request_type"] == "ReferenceDataRequest"

    hspec = rmd.build_histdata_spec(["A"], ["PX_LAST"], d(2026, 8, 17), d(2026, 8, 17), non_trading_day_fill=True)
    assert hspec["startDate"] == "20260817"
    assert hspec["endDate"] == "20260817"
    assert hspec["nonTradingDayFillOption"] == "NON_TRADING_WEEKDAYS"


# -- RatesFileSource: never touches blpapi -------------------------------------------------

def test_rates_file_source_get_curve_quotes():
    from data.bloomberg import rates_marketdata as rmd

    src = rmd.RatesFileSource(RATES_FIXTURE)
    assert src.name() == "RatesFileSource(test-fixture)"
    snap = src.get_curve_quotes("USD", date(2026, 8, 17))
    assert snap.currency == "USD"
    assert snap.index == "SOFR"
    tenors = [q.tenor for q in snap.quotes]
    assert tenors == ["1W", "1M", "3M", "1Y", "5Y", "10Y"]
    one_week = next(q for q in snap.quotes if q.tenor == "1W")
    assert math.isclose(float(one_week.value), 0.0530)

    # case-insensitive currency lookup
    snap_lower = src.get_curve_quotes("usd", date(2026, 8, 17))
    assert snap_lower.currency == "USD"


def test_rates_file_source_wrong_as_of_raises():
    from data.bloomberg import rates_marketdata as rmd

    src = rmd.RatesFileSource(RATES_FIXTURE)
    with pytest.raises(rmd.MarketDataUnavailable):
        src.get_curve_quotes("USD", date(2026, 8, 18))


def test_rates_file_source_unknown_currency_raises():
    from data.bloomberg import rates_marketdata as rmd

    src = rmd.RatesFileSource(RATES_FIXTURE)
    with pytest.raises(rmd.MarketDataUnavailable):
        src.get_curve_quotes("GBP", date(2026, 8, 17))  # not in the fixture


def test_rates_file_source_get_fixings_filters_range():
    from data.bloomberg import rates_marketdata as rmd

    src = rmd.RatesFileSource(RATES_FIXTURE)
    fixings = src.get_fixings("USD", date(2026, 8, 15), date(2026, 8, 17))
    assert [f.date.isoformat() for f in fixings] == ["2026-08-17"]

    with pytest.raises(rmd.MarketDataUnavailable):
        src.get_fixings("EUR", date(2026, 8, 1), date(2026, 8, 17))  # no fixings in fixture


def test_rates_file_source_get_bbg_curve_reconciliation_only():
    from data.bloomberg import rates_marketdata as rmd

    src = rmd.RatesFileSource(RATES_FIXTURE)
    curve = src.get_bbg_curve("USD", date(2026, 8, 17))
    assert curve is not None
    assert curve.curve_id == "YCSW0490 Index"
    assert [p.tenor for p in curve.points] == ["1Y", "5Y"]

    # never raises, just returns None when unavailable
    assert src.get_bbg_curve("EUR", date(2026, 8, 17)) is None


def test_ois_snapshot_round_trip(tmp_path):
    from data.bloomberg import rates_marketdata as rmd

    snap = rmd.load_ois_snapshot(RATES_FIXTURE)
    out_path = tmp_path / "roundtrip.json"
    rmd.save_ois_snapshot(snap, out_path)
    reloaded = rmd.load_ois_snapshot(out_path)
    assert reloaded.as_of == snap.as_of
    assert set(reloaded.curves) == set(snap.curves)
    assert reloaded.curves["USD"].quotes[0].ticker == snap.curves["USD"].quotes[0].ticker


# -- curve_quotes staging table -------------------------------------------------------------

def test_write_curve_quotes_creates_table_and_inserts(tmp_path):
    from data.bloomberg import rates_marketdata as rmd

    conn = sqlite3.connect(":memory:")
    # table does not exist yet -- write_curve_quotes must create it defensively
    tables_before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "curve_quotes" not in tables_before

    src = rmd.RatesFileSource(RATES_FIXTURE)
    snap = src.get_curve_quotes("USD", date(2026, 8, 17))
    n = rmd.write_curve_quotes(conn, snap, as_of_date="2026-08-17", source="BBG_BDP")
    assert n == len(snap.quotes)

    rows = conn.execute(
        "SELECT as_of_date, ccy, \"index\", tenor, ticker, value, quote_type, field, source "
        "FROM curve_quotes ORDER BY tenor"
    ).fetchall()
    assert len(rows) == len(snap.quotes)
    one_week = [r for r in rows if r[3] == "1W"][0]
    assert one_week[1] == "USD"
    assert one_week[2] == "SOFR"
    assert one_week[6] == "OIS"
    assert one_week[7] == "PX_LAST"
    assert one_week[8] == "BBG_BDP"
    assert math.isclose(one_week[5], 0.0530)


def test_write_curve_quotes_is_idempotent_via_insert_or_replace(tmp_path):
    from data.bloomberg import rates_marketdata as rmd

    conn = sqlite3.connect(":memory:")
    src = rmd.RatesFileSource(RATES_FIXTURE)
    snap = src.get_curve_quotes("USD", date(2026, 8, 17))
    rmd.write_curve_quotes(conn, snap, as_of_date="2026-08-17", source="BBG_BDP")
    n_again = rmd.write_curve_quotes(conn, snap, as_of_date="2026-08-17", source="BBG_BDP")
    assert n_again == len(snap.quotes)
    count = conn.execute("SELECT COUNT(*) FROM curve_quotes").fetchone()[0]
    assert count == len(snap.quotes)  # no duplicate rows on re-run


def test_ensure_curve_quotes_table_noop_if_already_created_elsewhere():
    from data.bloomberg import rates_marketdata as rmd

    conn = sqlite3.connect(":memory:")
    rmd.ensure_curve_quotes_table(conn)  # simulate data-ingest's schema.py having created it
    rmd.ensure_curve_quotes_table(conn)  # this module's defensive call must not error
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "curve_quotes" in tables


# -- RatesBloombergSource: fake blpapi, never the real SDK -----------------------------------

def test_rates_bloomberg_source_requires_blpapi_installed():
    from data.bloomberg import rates_marketdata as rmd

    # blpapi genuinely is not installed in this environment (by design -- see module docstring).
    with pytest.raises(rmd.MarketDataError):
        rmd.RatesBloombergSource()


def test_rates_bloomberg_source_get_curve_quotes_with_fake_blpapi(monkeypatch):
    from data.bloomberg import rates_marketdata as rmd

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        sec_list = []
        px_by_ticker = {
            "USOSFR1Z Curncy": 5.30, "USOSFR2Z Curncy": 5.29, "USOSFR3Z Curncy": 5.28,
            "USOSFRA Curncy": 5.28, "USOSFRB Curncy": 5.26, "USOSFRC Curncy": 5.21,
            "USOSFRF Curncy": 5.10, "USOSFRI Curncy": 4.95, "USOSFR1 Curncy": 4.75,
        }
        for t in request.securities:
            row = {}
            if t in px_by_ticker:
                row["PX_LAST"] = px_by_ticker[t]
            sec_list.append({"security": t, "fieldData": row})
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)

    src = rmd.RatesBloombergSource("localhost", 8194)
    assert src.name() == "RatesBloombergSource(localhost:8194)"
    snap = src.get_curve_quotes("USD", _book_today())
    assert snap.currency == "USD"
    assert snap.index == "SOFR"
    assert len(snap.quotes) == 9  # only tickers with a PX_LAST in the fake response
    one_week = next(q for q in snap.quotes if q.tenor == "1W")
    assert math.isclose(float(one_week.value), 0.0530)
    # sorted by tenor
    assert [q.tenor for q in snap.quotes][:3] == ["1W", "2W", "3W"]
    src.close()


def test_rates_bloomberg_source_get_curve_quotes_too_few_raises(monkeypatch):
    from data.bloomberg import rates_marketdata as rmd

    def responder(request):
        # Only ever answer one ticker -- fewer than _MIN_QUOTES.
        sec_list = [{"security": request.securities[0], "fieldData": {"PX_LAST": 5.30}}]
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)

    src = rmd.RatesBloombergSource("localhost", 8194)
    with pytest.raises(rmd.MarketDataUnavailable):
        src.get_curve_quotes("USD", _book_today())


def test_rates_bloomberg_source_get_fixings_with_fake_blpapi(monkeypatch):
    from data.bloomberg import rates_marketdata as rmd

    def responder(request):
        assert request.req_type == "HistoricalDataRequest"
        ticker = request.securities[0]
        field_data = [
            {"date": "2026-08-14", "PX_LAST": 5.31},
            {"date": "2026-08-15", "PX_LAST": 5.31},
            {"date": "2026-08-17", "PX_LAST": 5.30},
        ]
        return [{"securityData": {"security": ticker, "fieldData": field_data}}]

    _install_fake_blpapi(monkeypatch, responder)

    src = rmd.RatesBloombergSource("localhost", 8194)
    fixings = src.get_fixings("USD", date(2026, 8, 14), date(2026, 8, 17))
    assert [f.date.isoformat() for f in fixings] == ["2026-08-14", "2026-08-15", "2026-08-17"]
    assert math.isclose(float(fixings[-1].value), 0.0530)


def test_rates_bloomberg_source_get_bbg_curve_reconciliation_with_fake_blpapi(monkeypatch):
    from data.bloomberg import rates_marketdata as rmd

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        assert request.securities == ["YCSW0490 Index"]
        rows = [{"tenor": "1Y", "rate": 4.74}, {"tenor": "5Y", "rate": 3.91}]
        return [{"securityData": [{"security": "YCSW0490 Index", "fieldData": {"CURVE_TENOR_RATES": rows}}]}]

    _install_fake_blpapi(monkeypatch, responder)

    src = rmd.RatesBloombergSource("localhost", 8194)
    curve = src.get_bbg_curve("USD", date(2026, 8, 17))
    assert curve is not None
    assert curve.curve_id == "YCSW0490 Index"
    assert [p.tenor for p in curve.points] == ["1Y", "5Y"]
    assert math.isclose(float(curve.points[0].rate), 0.0474)


def test_rates_bloomberg_source_get_bbg_curve_returns_none_when_unavailable(monkeypatch):
    from data.bloomberg import rates_marketdata as rmd

    def responder(request):
        return [{"securityData": [{"security": request.securities[0], "fieldData": {}}]}]

    _install_fake_blpapi(monkeypatch, responder)

    src = rmd.RatesBloombergSource("localhost", 8194)
    assert src.get_bbg_curve("USD", date(2026, 8, 17)) is None


def test_rates_bloomberg_source_no_curve_id_returns_none_without_request(monkeypatch):
    from data.bloomberg import rates_marketdata as rmd

    def responder(request):
        raise AssertionError("should not be called: currency has no bbg_curve_id path exercised")

    _install_fake_blpapi(monkeypatch, responder)
    src = rmd.RatesBloombergSource("localhost", 8194)
    # Monkeypatch the ticker map lookup to simulate a currency with no curve id configured.
    monkeypatch.setattr(rmd, "ois_bbg_curve_id", lambda ccy: None)
    assert src.get_bbg_curve("USD", date(2026, 8, 17)) is None


def test_bbg_diagnostics_shim_reexports_run_bloomberg_diagnostics():
    from data.bloomberg.bbg_diagnostics import run_bloomberg_diagnostics

    assert callable(run_bloomberg_diagnostics)
    result = run_bloomberg_diagnostics(db_path=":memory:")
    assert isinstance(result, list)


# =========================================================================== vol_marketdata.py
VOL_FIXTURE = REPO / "data" / "bloomberg" / "fixtures" / "fx_vol_snapshot_v1.json"


def test_vol_ticker_construction():
    from data.bloomberg import vol_marketdata as vmd

    assert vmd.vol_ticker("EURUSD", "1M", "ATM") == "EURUSDV1M BGN Curncy"
    assert vmd.vol_ticker("EURUSD", "1M", "RR25") == "EURUSD25R1M BGN Curncy"
    assert vmd.vol_ticker("EURUSD", "1M", "BF25") == "EURUSD25B1M BGN Curncy"
    assert vmd.vol_ticker("EURUSD", "1M", "RR10") == "EURUSD10R1M BGN Curncy"
    assert vmd.vol_ticker("EURUSD", "1M", "BF10") == "EURUSD10B1M BGN Curncy"

    # normalises pair/tenor case
    assert vmd.vol_ticker("eurusd", "1m", "ATM") == "EURUSDV1M BGN Curncy"

    # a new pair works purely by name -- no per-pair table to extend
    assert vmd.vol_ticker("GBPUSD", "3M", "ATM") == "GBPUSDV3M BGN Curncy"

    with pytest.raises(vmd.TickerMapError):
        vmd.vol_ticker("EURUSD", "1M", "RR35")


def test_tenor_to_days_handles_on_and_units():
    from data.bloomberg import vol_marketdata as vmd

    assert vmd.tenor_to_days("ON") == 1
    assert vmd.tenor_to_days("on") == 1
    assert vmd.tenor_to_days("1W") == 7
    assert vmd.tenor_to_days("1M") == 30
    assert vmd.tenor_to_days("1Y") == 365
    with pytest.raises(ValueError):
        vmd.tenor_to_days("bogus")


def test_ensure_vol_quotes_table_idempotent():
    from data.bloomberg import vol_marketdata as vmd

    conn = sqlite3.connect(":memory:")
    vmd.ensure_vol_quotes_table(conn)
    vmd.ensure_vol_quotes_table(conn)  # must not raise the second time
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "vol_quotes" in tables


def test_vol_file_source_get_vol_quotes_and_round_trip_write():
    from data.bloomberg import vol_marketdata as vmd

    src = vmd.VolFileSource(VOL_FIXTURE)
    assert src.name() == "VolFileSource(test-fixture-synthetic)"

    result = src.get_vol_quotes(["EURUSD", "USDJPY"], as_of=date(2026, 9, 17))
    assert not result.diagnostics
    assert set(result.pairs) == {"EURUSD", "USDJPY"}
    eurusd_1m = [q for q in result.pairs["EURUSD"].quotes if q.tenor == "1M"]
    assert {q.quote_type for q in eurusd_1m} == set(vmd.VOL_QUOTE_TYPES)
    atm_1m = next(q for q in eurusd_1m if q.quote_type == "ATM")
    assert math.isclose(atm_1m.value, 6.60)
    assert atm_1m.ticker == "EURUSDV1M BGN Curncy"

    conn = sqlite3.connect(":memory:")
    n = vmd.write_vol_quotes(conn, result, as_of_date="2026-09-17", source="BBG_BDP")
    assert n == sum(len(pv.quotes) for pv in result.pairs.values())

    rows = conn.execute(
        "SELECT as_of_date, pair, tenor, quote_type, value, ticker, field, source, snapped_at "
        "FROM vol_quotes WHERE pair = 'EURUSD' AND tenor = '1M' AND quote_type = 'ATM'"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "2026-09-17"
    assert row[1] == "EURUSD"
    assert math.isclose(row[4], 6.60)
    assert row[5] == "EURUSDV1M BGN Curncy"
    assert row[6] == "PX_LAST"
    assert row[7] == "BBG_BDP"
    # snapped_at: the close stamp engine/rates/store.py gives (its hour is that module's to set)
    from engine.rates.store import snapped_at as _pricer_stamp
    assert row[8] == _pricer_stamp(date(2026, 9, 17))


def test_write_vol_quotes_is_idempotent_via_insert_or_replace():
    from data.bloomberg import vol_marketdata as vmd

    conn = sqlite3.connect(":memory:")
    src = vmd.VolFileSource(VOL_FIXTURE)
    result = src.get_vol_quotes(["EURUSD"], as_of=date(2026, 9, 17))
    n1 = vmd.write_vol_quotes(conn, result, as_of_date="2026-09-17", source="BBG_BDP")
    n2 = vmd.write_vol_quotes(conn, result, as_of_date="2026-09-17", source="BBG_BDP")
    assert n1 == n2
    count = conn.execute("SELECT COUNT(*) FROM vol_quotes").fetchone()[0]
    assert count == n1  # no duplicate rows on re-run


def test_vol_file_source_wrong_as_of_raises():
    from data.bloomberg import vol_marketdata as vmd

    src = vmd.VolFileSource(VOL_FIXTURE)
    with pytest.raises(vmd.MarketDataUnavailable):
        src.get_vol_quotes(["EURUSD"], as_of=date(2026, 9, 18))


def test_vol_file_source_unknown_pair_recorded_as_diagnostic_not_raised():
    from data.bloomberg import vol_marketdata as vmd

    src = vmd.VolFileSource(VOL_FIXTURE)
    result = src.get_vol_quotes(["EURUSD", "AUDNZD"], as_of=date(2026, 9, 17))
    assert "EURUSD" in result.pairs
    assert "AUDNZD" not in result.pairs
    assert any(d["pair"] == "AUDNZD" and d["status"] == "MISSING" for d in result.diagnostics)


def test_vol_smile_source_preference_rule():
    from data.bloomberg import vol_marketdata as vmd

    conn = sqlite3.connect(":memory:")
    vmd.ensure_vol_quotes_table(conn)
    # Two sources for the same (as_of, pair, tenor, quote_type): BBG_BDP must win.
    conn.execute(
        "INSERT INTO vol_quotes VALUES ('2026-09-17','EURUSD','1M','ATM',6.60,'EURUSDV1M BGN Curncy','PX_LAST','BBG_BDP','2026-09-17T17:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO vol_quotes VALUES ('2026-09-17','EURUSD','1M','ATM',6.99,'EURUSDV1M BGN Curncy','PX_LAST','BBG_BDH','2026-09-17T17:00:00-04:00')"
    )
    smile = vmd.vol_smile(conn, "2026-09-17", "EURUSD")
    assert math.isclose(smile["1M"]["ATM"], 6.60)

    # Only BBG_BDH and MANUAL present (no BBG_BDP): BBG_BDH must win over MANUAL.
    conn2 = sqlite3.connect(":memory:")
    vmd.ensure_vol_quotes_table(conn2)
    conn2.execute(
        "INSERT INTO vol_quotes VALUES ('2026-09-17','EURUSD','3M','ATM',7.10,'EURUSDV3M BGN Curncy','PX_LAST','MANUAL','2026-09-17T17:00:00-04:00')"
    )
    conn2.execute(
        "INSERT INTO vol_quotes VALUES ('2026-09-17','EURUSD','3M','ATM',7.00,'EURUSDV3M BGN Curncy','PX_LAST','BBG_BDH','2026-09-17T17:00:00-04:00')"
    )
    smile2 = vmd.vol_smile(conn2, "2026-09-17", "EURUSD")
    assert math.isclose(smile2["3M"]["ATM"], 7.00)

    # Neither BBG_BDP nor BBG_BDH: alphabetically-first source wins, deterministically.
    conn3 = sqlite3.connect(":memory:")
    vmd.ensure_vol_quotes_table(conn3)
    conn3.execute(
        "INSERT INTO vol_quotes VALUES ('2026-09-17','EURUSD','6M','ATM',7.50,'EURUSDV6M BGN Curncy','PX_LAST','ZZZ_SRC','2026-09-17T17:00:00-04:00')"
    )
    conn3.execute(
        "INSERT INTO vol_quotes VALUES ('2026-09-17','EURUSD','6M','ATM',7.40,'EURUSDV6M BGN Curncy','PX_LAST','AAA_SRC','2026-09-17T17:00:00-04:00')"
    )
    smile3 = vmd.vol_smile(conn3, "2026-09-17", "EURUSD")
    assert math.isclose(smile3["6M"]["ATM"], 7.40)

    # Nothing staged at all -> empty dict, not an error.
    assert vmd.vol_smile(conn, "2026-09-17", "GBPUSD") == {}


def test_atm_vol_for_expiry_interpolation_and_bounds():
    from data.bloomberg import vol_marketdata as vmd

    conn = sqlite3.connect(":memory:")
    src = vmd.VolFileSource(VOL_FIXTURE)
    result = src.get_vol_quotes(["EURUSD"], as_of=date(2026, 9, 17))
    vmd.write_vol_quotes(conn, result, as_of_date="2026-09-17", source="BBG_BDP")

    # Exact tenor hit: 1M = 30 days from 2026-09-17 -> 2026-10-17.
    exact = vmd.atm_vol_for_expiry(conn, "2026-09-17", "EURUSD", "2026-10-17")
    assert math.isclose(exact, 6.60)

    # Interior date: strictly between 1M (30d, 6.60) and 2M (60d, 6.80) tenor nodes.
    interior = vmd.atm_vol_for_expiry(conn, "2026-09-17", "EURUSD", "2026-11-01")
    assert interior is not None
    assert 6.60 < interior < 6.80

    # Outside the tenor range (beyond 1Y = 365 days): never extrapolates.
    outside = vmd.atm_vol_for_expiry(conn, "2026-09-17", "EURUSD", "2029-09-17")
    assert outside is None

    # Before the first node (ON = 1 day, i.e. before 2026-09-18): never extrapolates.
    before = vmd.atm_vol_for_expiry(conn, "2026-09-17", "EURUSD", "2026-09-17")
    assert before is None

    # No data staged for the pair at all.
    assert vmd.atm_vol_for_expiry(conn, "2026-09-17", "GBPUSD", "2026-10-17") is None


def test_vol_pairs_needed_seeded_db():
    from data.bloomberg import vol_marketdata as vmd
    from data.ingest import schema

    conn = schema.connect(":memory:")
    conn.execute(
        "INSERT INTO instruments VALUES "
        "('EURUSD090126C-1','FX_OPTION','EUR','USD',1,0,'EURUSD Curncy','2026-09-01')"
    )
    conn.execute(
        "INSERT INTO instruments VALUES "
        "('USDJPY100126P-2','FX_OPTION','USD','JPY',1,0,'USDJPY Curncy','2026-10-01')"
    )
    conn.execute(
        "INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.commit()
    pairs = vmd.vol_pairs_needed(conn)
    assert pairs == ["EURUSD", "USDJPY"]


def test_vol_pairs_needed_defaults_when_empty():
    from data.bloomberg import vol_marketdata as vmd
    from data.ingest import schema

    conn = schema.connect(":memory:")
    assert vmd.vol_pairs_needed(conn) == list(vmd.DEFAULT_PAIRS)

    # also defaults gracefully when the instruments table doesn't exist at all
    bare = sqlite3.connect(":memory:")
    assert vmd.vol_pairs_needed(bare) == list(vmd.DEFAULT_PAIRS)


def test_vol_snapshot_round_trip(tmp_path):
    from data.bloomberg import vol_marketdata as vmd

    snap = vmd.load_vol_snapshot(VOL_FIXTURE)
    out_path = tmp_path / "roundtrip.json"
    vmd.save_vol_snapshot(snap, out_path)
    reloaded = vmd.load_vol_snapshot(out_path)
    assert reloaded.as_of == snap.as_of
    assert set(reloaded.pairs) == set(snap.pairs)
    assert reloaded.pairs["EURUSD"].quotes[0].ticker == snap.pairs["EURUSD"].quotes[0].ticker


# -- CLI ---------------------------------------------------------------------------------

def test_cli_file_mode_writes_vol_quotes(tmp_path):
    from data.bloomberg import vol_marketdata as vmd

    db_path = tmp_path / "risk.db"
    exit_code = vmd.main([
        "--db", str(db_path), "--as-of", "2026-09-17", "--file", str(VOL_FIXTURE),
    ])
    assert exit_code == 0

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM vol_quotes").fetchone()[0]
    # empty DB -> vol_pairs_needed defaults to EURUSD/EURSEK/USDJPY, all three fully
    # covered by the fixture across 9 tenors x 5 quote_types each.
    assert count == 3 * 9 * 5

    diag_path = Path(str(db_path) + ".diag.json")
    assert diag_path.exists()
    diag = json.loads(diag_path.read_text())
    assert diag["summary"]["outcome"] == "OK"
    assert diag["summary"]["exit_code"] == 0


def test_cli_file_mode_bad_as_of_exits_2(tmp_path):
    from data.bloomberg import vol_marketdata as vmd

    db_path = tmp_path / "risk.db"
    exit_code = vmd.main(["--db", str(db_path), "--as-of", "not-a-date", "--file", str(VOL_FIXTURE)])
    assert exit_code == 2
    diag = json.loads(Path(str(db_path) + ".diag.json").read_text())
    assert diag["summary"]["exit_code"] == 2


def test_cli_file_mode_missing_as_of_exits_2(tmp_path):
    from data.bloomberg import vol_marketdata as vmd

    db_path = tmp_path / "risk.db"
    exit_code = vmd.main(["--db", str(db_path), "--file", str(VOL_FIXTURE)])
    assert exit_code == 2


def test_cli_probe_mode_file_source_writes_diag(tmp_path):
    from data.bloomberg import vol_marketdata as vmd

    db_path = tmp_path / "risk.db"
    exit_code = vmd.main([
        "--db", str(db_path), "--as-of", "2026-09-17", "--file", str(VOL_FIXTURE), "--probe",
    ])
    assert exit_code == 0
    diag = json.loads(Path(str(db_path) + ".diag.json").read_text())
    assert diag["mode"] == "probe"
    assert len(diag["probe"]["steps"]) == 5
    assert all(s["outcome"] == "OK" for s in diag["probe"]["steps"])
    # no vol_quotes rows in probe mode
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "vol_quotes" not in tables


def test_cli_file_mode_bad_path_exits_4(tmp_path):
    from data.bloomberg import vol_marketdata as vmd

    db_path = tmp_path / "risk.db"
    exit_code = vmd.main([
        "--db", str(db_path), "--as-of", "2026-09-17", "--file", str(tmp_path / "does_not_exist.json"),
    ])
    assert exit_code in (3, 4)  # a bad file path surfaces as either, never a crash without a diag
    assert Path(str(db_path) + ".diag.json").exists()


# -- VolBloombergSource: fake blpapi, never the real SDK ---------------------------------

def test_vol_bloomberg_source_requires_blpapi_when_absent():
    from data.bloomberg import vol_marketdata as vmd

    try:
        import blpapi  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("blpapi is installed in this environment; nothing to test for the absent-package path")

    with pytest.raises(vmd.MarketDataError):
        vmd.VolBloombergSource()


def test_vol_bloomberg_source_get_vol_quotes_with_fake_blpapi(monkeypatch):
    from data.bloomberg import vol_marketdata as vmd

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        sec_list = []
        for t in request.securities:
            row = {}
            # Only answer ATM 1M for EURUSD -- everything else in the batch comes back
            # with no fieldData, exercising the per-ticker MISSING diagnostics path.
            if t == "EURUSDV1M BGN Curncy":
                row["PX_LAST"] = 6.60
            sec_list.append({"security": t, "fieldData": row})
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)

    src = vmd.VolBloombergSource("localhost", 8194)
    assert src.name() == "VolBloombergSource(localhost:8194)"
    result = src.get_vol_quotes(["EURUSD"], as_of=None, tenors=["1M"])
    assert result.source == "BBG_BDP"
    atm = next(q for q in result.pairs["EURUSD"].quotes if q.quote_type == "ATM")
    assert math.isclose(atm.value, 6.60)
    # the other 4 quote_types for 1M never got a value -> recorded as diagnostics, not raised
    missing_quote_types = {d["quote_type"] for d in result.diagnostics}
    assert missing_quote_types == {"RR25", "BF25", "RR10", "BF10"}
    src.close()


def test_vol_bloomberg_source_historical_request_for_explicit_as_of(monkeypatch):
    from data.bloomberg import vol_marketdata as vmd

    def responder(request):
        assert request.req_type == "HistoricalDataRequest"
        # HistoricalDataRequest returns one securityData message per requested security
        # (see rates_marketdata.py's _fetch_historical_single) -- echo all of them back.
        return [{"securityData": {"security": t, "fieldData": [{"PX_LAST": 6.60}]}} for t in request.securities]

    _install_fake_blpapi(monkeypatch, responder)

    src = vmd.VolBloombergSource("localhost", 8194)
    result = src.get_vol_quotes(["EURUSD"], as_of=date(2026, 9, 17), tenors=["1M"])
    assert result.source == "BBG_BDH"
    atm = next(q for q in result.pairs["EURUSD"].quotes if q.quote_type == "ATM")
    assert math.isclose(atm.value, 6.60)


def test_vol_marketdata_module_imports_without_blpapi():
    # Importing the module must never require blpapi to be installed -- only
    # instantiating VolBloombergSource does.
    import importlib

    import data.bloomberg.vol_marketdata as vmd
    importlib.reload(vmd)
    assert callable(vmd.vol_ticker)


# -- UNVERIFIED assumption verification: vol_ticker_checks (2026-09-18) ------------------
# The live pull itself is the probe: data/bloomberg/live.py's `_vol_step` requests every
# UNVERIFIED ticker/field on every cycle with an open FX_OPTION in the book, and
# assess_ticker_assumptions / record_vol_ticker_checks turn VolFetchResult.diagnostics
# (already produced, no extra request) into a persistent per-assumption verdict that
# tools/bbg_diagnostics.py::check_unverified_assumptions reads.

def test_get_vol_quotes_diagnostics_carry_bbg_status_for_security_and_field_errors(monkeypatch):
    """The live per-ticker classification (2026-09-18) must distinguish a rejected
    ticker (SECURITY_ERROR) from a rejected field on an otherwise-valid ticker
    (FIELD_EXCEPTION) -- both previously collapsed into a bare 'MISSING'."""
    from data.bloomberg import vol_marketdata as vmd

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        sec_list = []
        for t in request.securities:
            if t == "EURUSDV1M BGN Curncy":
                sec_list.append({"security": t, "fieldData": {},
                                  "securityError": {"message": "UNKNOWN_SECURITY"}})
            elif t == "EURUSD25R1M BGN Curncy":
                sec_list.append({"security": t, "fieldData": {},
                                  "fieldExceptions": [{"fieldId": "PX_LAST",
                                                        "errorInfo": {"message": "NOT_APPLICABLE_TO_REF_DATA"}}]})
            else:
                sec_list.append({"security": t, "fieldData": {"PX_LAST": 7.5}})
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)
    src = vmd.VolBloombergSource("localhost", 8194)
    result = src.get_vol_quotes(["EURUSD"], as_of=None, tenors=["1M"])

    by_ticker = {d["ticker"]: d for d in result.diagnostics}
    assert by_ticker["EURUSDV1M BGN Curncy"]["bbg_status"] == "SECURITY_ERROR"
    assert "UNKNOWN_SECURITY" in by_ticker["EURUSDV1M BGN Curncy"]["detail"]
    assert by_ticker["EURUSD25R1M BGN Curncy"]["bbg_status"] == "FIELD_EXCEPTION"
    assert "NOT_APPLICABLE_TO_REF_DATA" in by_ticker["EURUSD25R1M BGN Curncy"]["detail"]
    # everything else in the 1M batch still resolved fine
    assert "EURUSD25B1M BGN Curncy" not in by_ticker


def test_assess_ticker_assumptions_all_confirmed_from_bloomberg_source(monkeypatch):
    from data.bloomberg import vol_marketdata as vmd

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        return [{"securityData": [{"security": t, "fieldData": {"PX_LAST": 7.5}} for t in request.securities]}]

    _install_fake_blpapi(monkeypatch, responder)
    src = vmd.VolBloombergSource("localhost", 8194)
    result = src.get_vol_quotes(["EURUSD"], as_of=None)  # default tenors: all of VOL_TENORS, incl. 'ON'
    assert not result.diagnostics

    rows = vmd.assess_ticker_assumptions(result)
    ids = {r["assumption_id"] for r in rows}
    assert ids == set(vmd.ALL_ASSUMPTION_IDS)
    assert all(r["outcome"] == "OK" for r in rows)


def test_assess_ticker_assumptions_flags_rejected_ticker_shape(monkeypatch):
    """Every RR25 ticker (all tenors) is rejected outright by Bloomberg -- the ticker
    SHAPE assumption must FAIL naming that exact ticker, while unrelated assumptions
    (ATM ticker shape, etc.) stay confirmed."""
    from data.bloomberg import vol_marketdata as vmd

    def responder(request):
        sec_list = []
        for t in request.securities:
            if "25R" in t:
                sec_list.append({"security": t, "fieldData": {}, "securityError": {"message": "UNKNOWN_SECURITY"}})
            else:
                sec_list.append({"security": t, "fieldData": {"PX_LAST": 7.5}})
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)
    src = vmd.VolBloombergSource("localhost", 8194)
    result = src.get_vol_quotes(["EURUSD"], as_of=None)

    rows = {r["assumption_id"]: r for r in vmd.assess_ticker_assumptions(result)}
    assert rows["rr25_ticker"]["outcome"] == "SECURITY_ERROR"
    assert "25R" in rows["rr25_ticker"]["tickers"]
    assert "UNKNOWN_SECURITY" in rows["rr25_ticker"]["detail"]
    # unrelated assumptions are unaffected
    assert rows["atm_ticker"]["outcome"] == "OK"
    assert rows["bf25_ticker"]["outcome"] == "OK"


def test_assess_ticker_assumptions_flags_out_of_range_scale(monkeypatch):
    """ATM values coming back as a decimal fraction (0.075) rather than vol points (7.5)
    must fail the scale assumption specifically -- the ticker shape itself is still fine
    since a value WAS returned under PX_LAST."""
    from data.bloomberg import vol_marketdata as vmd

    atm_tickers = {vmd.vol_ticker("EURUSD", t, "ATM") for t in vmd.VOL_TENORS}

    def responder(request):
        sec_list = [{"security": t, "fieldData": {"PX_LAST": 0.075 if t in atm_tickers else 7.5}}
                    for t in request.securities]
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)
    src = vmd.VolBloombergSource("localhost", 8194)
    result = src.get_vol_quotes(["EURUSD"], as_of=None)

    rows = {r["assumption_id"]: r for r in vmd.assess_ticker_assumptions(result)}
    assert rows["vol_scale"]["outcome"] == "OUT_OF_RANGE"
    assert "decimal fraction" in rows["vol_scale"]["detail"]
    assert rows["atm_ticker"]["outcome"] == "OK"  # ticker shape confirmed independent of scale


def test_record_and_read_vol_ticker_checks_round_trip():
    from data.bloomberg import vol_marketdata as vmd

    conn = sqlite3.connect(":memory:")
    src = vmd.VolFileSource(VOL_FIXTURE)
    result = src.get_vol_quotes(["EURUSD"], as_of=date(2026, 9, 17))
    n = vmd.record_vol_ticker_checks(conn, result, checked_at="2026-09-17T17:00:00-04:00")
    assert n == len(vmd.ALL_ASSUMPTION_IDS)

    checked = vmd.read_vol_ticker_checks(conn)
    assert set(checked) == set(vmd.ALL_ASSUMPTION_IDS)
    assert all(c["outcome"] == "OK" for c in checked.values())
    assert checked["atm_ticker"]["last_checked"] == "2026-09-17T17:00:00-04:00"

    # Idempotent / defensive table creation: calling again on the same conn never raises.
    n2 = vmd.record_vol_ticker_checks(conn, result, checked_at="2026-09-17T17:00:00-04:00")
    assert n2 == n


def test_read_vol_ticker_checks_empty_when_no_pull_has_run():
    from data.bloomberg import vol_marketdata as vmd

    conn = sqlite3.connect(":memory:")
    assert vmd.read_vol_ticker_checks(conn) == {}  # table doesn't exist yet -- never raises


def test_record_vol_ticker_checks_leaves_unexercised_assumption_untouched():
    """An assumption with no evidence this cycle (e.g. a narrower probe that never asked
    for the 'ON' tenor) must keep its previous verdict, not be wiped or marked stale."""
    from data.bloomberg import vol_marketdata as vmd

    conn = sqlite3.connect(":memory:")
    src = vmd.VolFileSource(VOL_FIXTURE)
    full = src.get_vol_quotes(["EURUSD"], as_of=date(2026, 9, 17))
    vmd.record_vol_ticker_checks(conn, full, checked_at="2026-09-17T17:00:00-04:00")

    partial = vmd.VolFetchResult(
        as_of=date(2026, 9, 18), source="BBG_BDP",
        pairs={"EURUSD": vmd.PairVolSnapshot(pair="EURUSD", as_of=date(2026, 9, 18), quotes=[
            vmd.VolQuote(tenor="1M", quote_type="ATM", ticker="EURUSDV1M BGN Curncy", value=7.10),
        ])},
    )
    vmd.record_vol_ticker_checks(conn, partial, checked_at="2026-09-18T17:00:00-04:00")

    checked = vmd.read_vol_ticker_checks(conn)
    assert checked["atm_ticker"]["last_checked"] == "2026-09-18T17:00:00-04:00"
    assert math.isclose(checked["atm_ticker"]["value"], 7.10)
    assert checked["on_tenor"]["last_checked"] == "2026-09-17T17:00:00-04:00"  # untouched


# =========================================================================== rates_vol_marketdata.py
RATE_VOL_FIXTURE = REPO / "data" / "bloomberg" / "fixtures" / "rate_vol_snapshot_v1.json"


def _only_vol_type(currencies, vol_type, instrument=None):
    """Filter a {ccy: CcyRateVolSnapshot} mapping down to one vol_type (and optionally
    one instrument) before writing -- rate_vol_quotes' primary key does not include
    vol_type (see module "Known schema quirk"), so writing both LOGNORMAL and NORMAL
    quotes for the same (ccy, instrument, expiry, underlying_tenor, source) in one call
    means only the last-written vol_type survives; tests that need a specific vol_type's
    grid intact stage that vol_type alone (optionally scoped to one instrument, so a
    second filtered write can patch just that instrument's rows without disturbing rows
    already staged for the other instrument -- instrument IS part of the primary key,
    so the two never collide with each other)."""
    from data.bloomberg import rates_vol_marketdata as rvm

    return {
        ccy: rvm.CcyRateVolSnapshot(
            ccy=snap.ccy, index=snap.index, as_of=snap.as_of,
            quotes=[
                q for q in snap.quotes
                if q.vol_type == vol_type and (instrument is None or q.instrument == instrument)
            ],
        )
        for ccy, snap in currencies.items()
    }


def test_rate_vol_ticker_construction():
    from data.bloomberg import rates_vol_marketdata as rvm

    # Task's own worked example: 1Y expiry x 10Y underlying tenor.
    assert rvm.rate_vol_ticker("USD", "SWAPTION", "1Y", "10Y", "LOGNORMAL") == "USSV0110 Curncy"
    assert rvm.rate_vol_ticker("USD", "SWAPTION", "1Y", "10Y", "NORMAL") == "USSN0110 Curncy"
    assert rvm.rate_vol_ticker("EUR", "SWAPTION", "1Y", "10Y", "LOGNORMAL") == "EUSV0110 Curncy"
    assert rvm.rate_vol_ticker("GBP", "SWAPTION", "1Y", "10Y", "LOGNORMAL") == "BPSV0110 Curncy"

    # sub-year expiry uses the letter code, normalises case
    assert rvm.rate_vol_ticker("usd", "swaption", "1m", "10y", "lognormal") == "USSVA10 Curncy"
    assert rvm.rate_vol_ticker("USD", "SWAPTION", "3M", "30Y", "LOGNORMAL") == "USSVC30 Curncy"
    assert rvm.rate_vol_ticker("USD", "SWAPTION", "6M", "2Y", "NORMAL") == "USSNF02 Curncy"

    # cap ticker: expiry code only, no underlying-tenor axis
    assert rvm.rate_vol_ticker("USD", "CAP", "1Y", "", "LOGNORMAL") == "USCV01 Curncy"
    assert rvm.rate_vol_ticker("USD", "CAP", "1M", "", "NORMAL") == "USCNA Curncy"

    with pytest.raises(rvm.TickerMapError):
        rvm.rate_vol_ticker("XXX", "SWAPTION", "1Y", "10Y", "LOGNORMAL")
    with pytest.raises(rvm.TickerMapError):
        rvm.rate_vol_ticker("USD", "SWAPTION", "1Y", "10Y", "BOGUS")
    with pytest.raises(rvm.TickerMapError):
        rvm.rate_vol_ticker("USD", "SWAPTION", "1Y", "", "LOGNORMAL")  # underlying_tenor required
    with pytest.raises(rvm.TickerMapError):
        rvm.rate_vol_ticker("USD", "BOND", "1Y", "10Y", "LOGNORMAL")


def test_tenor_to_years():
    from data.bloomberg import rates_vol_marketdata as rvm

    assert math.isclose(rvm.tenor_to_years("1M"), 1 / 12)
    assert math.isclose(rvm.tenor_to_years("3M"), 0.25)
    assert math.isclose(rvm.tenor_to_years("1Y"), 1.0)
    assert math.isclose(rvm.tenor_to_years("30Y"), 30.0)
    with pytest.raises(ValueError):
        rvm.tenor_to_years("bogus")


def test_ensure_rate_vol_quotes_table_idempotent():
    from data.bloomberg import rates_vol_marketdata as rvm

    conn = sqlite3.connect(":memory:")
    rvm.ensure_rate_vol_quotes_table(conn)
    rvm.ensure_rate_vol_quotes_table(conn)  # must not raise the second time
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "rate_vol_quotes" in tables


def test_rate_vol_file_source_get_quotes_and_round_trip_write():
    from data.bloomberg import rates_vol_marketdata as rvm

    src = rvm.RateVolFileSource(RATE_VOL_FIXTURE)
    assert src.name() == "RateVolFileSource(test-fixture-synthetic)"

    result = src.get_rate_vol_quotes(["USD", "EUR"], as_of=date(2026, 9, 17))
    assert not result.diagnostics
    assert set(result.currencies) == {"USD", "EUR"}

    usd = result.currencies["USD"]
    assert usd.index == "SOFR"
    node = [q for q in usd.quotes if q.instrument == "SWAPTION" and q.expiry_tenor == "1Y"
            and q.underlying_tenor == "10Y" and q.vol_type == "LOGNORMAL"]
    assert len(node) == 1
    assert node[0].ticker == "USSV0110 Curncy"

    conn = sqlite3.connect(":memory:")
    # Stage LOGNORMAL only for this round trip -- see _only_vol_type docstring: writing
    # both vol_types for the same node/source collides on rate_vol_quotes' primary key.
    lognormal_only = _only_vol_type(result.currencies, "LOGNORMAL")
    n = rvm.write_rate_vol_quotes(conn, lognormal_only, as_of_date="2026-09-17", source="BBG_BDP")
    assert n == sum(len(s.quotes) for s in lognormal_only.values())

    rows = conn.execute(
        "SELECT as_of_date, ccy, \"index\", instrument, expiry_tenor, underlying_tenor, "
        "quote_type, strike_offset_bp, value, vol_type, ticker, field, source, snapped_at "
        "FROM rate_vol_quotes WHERE ccy = 'USD' AND instrument = 'SWAPTION' "
        "AND expiry_tenor = '1Y' AND underlying_tenor = '10Y' AND vol_type = 'LOGNORMAL'"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "2026-09-17"
    assert row[1] == "USD"
    assert row[2] == "SOFR"
    assert row[6] == "ATM"
    assert row[7] == 0.0
    assert row[10] == "USSV0110 Curncy"
    assert row[11] == "PX_LAST"
    assert row[12] == "BBG_BDP"
    # snapped_at: the close stamp engine/rates/store.py gives (its hour is that module's to set)
    from engine.rates.store import snapped_at as _pricer_stamp
    assert row[13] == _pricer_stamp(date(2026, 9, 17))


def test_write_rate_vol_quotes_is_idempotent_via_insert_or_replace():
    from data.bloomberg import rates_vol_marketdata as rvm

    conn = sqlite3.connect(":memory:")
    src = rvm.RateVolFileSource(RATE_VOL_FIXTURE)
    result = src.get_rate_vol_quotes(["USD"], as_of=date(2026, 9, 17))
    n1 = rvm.write_rate_vol_quotes(conn, result, as_of_date="2026-09-17", source="BBG_BDP")
    n2 = rvm.write_rate_vol_quotes(conn, result, as_of_date="2026-09-17", source="BBG_BDP")
    assert n1 == n2
    count = conn.execute("SELECT COUNT(*) FROM rate_vol_quotes").fetchone()[0]
    # NOTE: rate_vol_quotes' primary key does not include vol_type (see module
    # docstring "Known schema quirk"), so LOGNORMAL/NORMAL pairs for the same node
    # collide -- the persisted row count is HALF the number of quotes fetched (35
    # SWAPTION nodes + 7 CAP nodes, each with 2 vol_types = 84 quotes -> 42 rows).
    fetched = sum(len(s.quotes) for s in result.currencies.values())
    assert fetched == 84
    assert count == 42
    assert count < fetched


def test_rate_vol_file_source_wrong_as_of_raises():
    from data.bloomberg import rates_vol_marketdata as rvm

    src = rvm.RateVolFileSource(RATE_VOL_FIXTURE)
    with pytest.raises(rvm.MarketDataUnavailable):
        src.get_rate_vol_quotes(["USD"], as_of=date(2026, 9, 18))


def test_rate_vol_file_source_unknown_ccy_recorded_as_diagnostic_not_raised():
    from data.bloomberg import rates_vol_marketdata as rvm

    src = rvm.RateVolFileSource(RATE_VOL_FIXTURE)
    result = src.get_rate_vol_quotes(["USD", "NZD"], as_of=date(2026, 9, 17))
    assert "USD" in result.currencies
    assert "NZD" not in result.currencies
    assert any(d["ccy"] == "NZD" and d["status"] == "MISSING" for d in result.diagnostics)


def test_rate_vol_grid_source_preference_rule():
    from data.bloomberg import rates_vol_marketdata as rvm

    conn = sqlite3.connect(":memory:")
    rvm.ensure_rate_vol_quotes_table(conn)
    # Two sources for the same node: BBG_BDP must win.
    conn.execute(
        "INSERT INTO rate_vol_quotes VALUES "
        "('2026-09-17','USD','SOFR','SWAPTION','1Y','10Y','ATM',0.0,20.0,'LOGNORMAL',"
        "'USSV0110 Curncy','PX_LAST','BBG_BDP','2026-09-17T17:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO rate_vol_quotes VALUES "
        "('2026-09-17','USD','SOFR','SWAPTION','1Y','10Y','ATM',0.0,25.0,'LOGNORMAL',"
        "'USSV0110 Curncy','PX_LAST','BBG_BDH','2026-09-17T17:00:00-04:00')"
    )
    grid = rvm.rate_vol_grid(conn, "2026-09-17", "USD", "SWAPTION", "LOGNORMAL")
    assert math.isclose(grid[("1Y", "10Y")], 20.0)

    # Neither BBG_BDP nor BBG_BDH: alphabetically-first source wins, deterministically.
    conn2 = sqlite3.connect(":memory:")
    rvm.ensure_rate_vol_quotes_table(conn2)
    conn2.execute(
        "INSERT INTO rate_vol_quotes VALUES "
        "('2026-09-17','USD','SOFR','SWAPTION','2Y','10Y','ATM',0.0,19.0,'LOGNORMAL',"
        "'USSV0210 Curncy','PX_LAST','ZZZ_SRC','2026-09-17T17:00:00-04:00')"
    )
    conn2.execute(
        "INSERT INTO rate_vol_quotes VALUES "
        "('2026-09-17','USD','SOFR','SWAPTION','2Y','10Y','ATM',0.0,18.0,'LOGNORMAL',"
        "'USSV0210 Curncy','PX_LAST','AAA_SRC','2026-09-17T17:00:00-04:00')"
    )
    grid2 = rvm.rate_vol_grid(conn2, "2026-09-17", "USD", "SWAPTION", "LOGNORMAL")
    assert math.isclose(grid2[("2Y", "10Y")], 18.0)

    # Nothing staged at all -> empty dict, not an error.
    assert rvm.rate_vol_grid(conn, "2026-09-17", "GBP", "SWAPTION", "LOGNORMAL") == {}


def test_atm_swaption_vol_interpolation_grid_hit_and_bounds():
    from data.bloomberg import rates_vol_marketdata as rvm

    conn = sqlite3.connect(":memory:")
    src = rvm.RateVolFileSource(RATE_VOL_FIXTURE)
    result = src.get_rate_vol_quotes(["USD"], as_of=date(2026, 9, 17))
    # LOGNORMAL only -- see _only_vol_type docstring (NORMAL would otherwise overwrite it).
    lognormal_only = _only_vol_type(result.currencies, "LOGNORMAL")
    rvm.write_rate_vol_quotes(conn, lognormal_only, as_of_date="2026-09-17", source="BBG_BDP")

    grid = rvm.rate_vol_grid(conn, "2026-09-17", "USD", "SWAPTION", "LOGNORMAL")

    # Exact grid hit: 1Y expiry (365d from as_of) x 10Y underlying tenor.
    exact = rvm.atm_swaption_vol(
        conn, "2026-09-17", "USD", "2027-09-17", 10.0, "LOGNORMAL", "2026-09-17",
    )
    assert exact is not None
    assert math.isclose(exact, grid[("1Y", "10Y")])

    # Interior point on the expiry axis only (underlying_tenor_years=10.0 hits the 10Y
    # tenor node exactly, so tenor interpolation is a no-op and this isolates the
    # variance-time expiry interpolation -- same style as vol_marketdata.py's
    # atm_vol_for_expiry test): 2028-03-17 is strictly between the 1Y node (2027-09-17)
    # and the 2Y node (2028-09-17), at the 10Y-tenor nodes.
    interior = rvm.atm_swaption_vol(
        conn, "2026-09-17", "USD", "2028-03-17", 10.0, "LOGNORMAL", "2026-09-17",
    )
    assert interior is not None
    lo = min(grid[("1Y", "10Y")], grid[("2Y", "10Y")])
    hi = max(grid[("1Y", "10Y")], grid[("2Y", "10Y")])
    assert lo <= interior <= hi

    # Interior point requiring interpolation on BOTH axes: sanity-bounded by the whole
    # staged LOGNORMAL SWAPTION grid (bilinear variance-time-on-expiry interpolation is
    # not guaranteed to sit strictly inside the 4 surrounding corners' raw values -- see
    # module docstring -- so this is a loose sanity check, not a tight bracket).
    both_axes = rvm.atm_swaption_vol(
        conn, "2026-09-17", "USD", "2028-03-17", 7.0, "LOGNORMAL", "2026-09-17",
    )
    assert both_axes is not None
    all_values = list(grid.values())
    assert min(all_values) - 1.0 <= both_axes <= max(all_values) + 1.0

    # Outside the expiry range (beyond 10Y expiry): never extrapolates.
    outside_expiry = rvm.atm_swaption_vol(
        conn, "2026-09-17", "USD", "2040-09-17", 10.0, "LOGNORMAL", "2026-09-17",
    )
    assert outside_expiry is None

    # Outside the tenor range (beyond 30Y underlying): never extrapolates.
    outside_tenor = rvm.atm_swaption_vol(
        conn, "2026-09-17", "USD", "2027-09-17", 40.0, "LOGNORMAL", "2026-09-17",
    )
    assert outside_tenor is None

    # No data staged at all for this ccy/vol_type.
    assert rvm.atm_swaption_vol(conn, "2026-09-17", "GBP", "2027-09-17", 10.0, "LOGNORMAL", "2026-09-17") is None


def test_to_rate_vols_rows_mapping():
    from data.bloomberg import rates_vol_marketdata as rvm

    conn = sqlite3.connect(":memory:")
    src = rvm.RateVolFileSource(RATE_VOL_FIXTURE)
    result = src.get_rate_vol_quotes(["USD"], as_of=date(2026, 9, 17))
    # Stage NORMAL for everything first, then patch SWAPTION rows to LOGNORMAL -- see
    # _only_vol_type docstring: instrument is part of the primary key so this leaves
    # the CAP NORMAL rows (written first) untouched while SWAPTION ends up LOGNORMAL.
    rvm.write_rate_vol_quotes(conn, _only_vol_type(result.currencies, "NORMAL"), as_of_date="2026-09-17", source="BBG_BDP")
    rvm.write_rate_vol_quotes(
        conn, _only_vol_type(result.currencies, "LOGNORMAL", instrument="SWAPTION"),
        as_of_date="2026-09-17", source="BBG_BDP",
    )

    rows = rvm.to_rate_vols_rows(conn, "2026-09-17", "USD", "SWAPTION", "LOGNORMAL")
    assert len(rows) == len(rvm.EXPIRY_TENORS) * len(rvm.UNDERLYING_TENORS)
    row = next(r for r in rows if r["expiry_tenor_or_date"] == "1Y" and r["underlying_tenor"] == "10Y")
    assert row["as_of_date"] == "2026-09-17"
    assert row["ccy"] == "USD"
    assert row["index"] == "SOFR"
    assert row["strike_or_ATM"] == "ATM"
    assert row["vol_type"] == "LOGNORMAL"
    assert row["source"] == "BBG_BDP"
    assert math.isclose(row["vol"], rvm.rate_vol_grid(conn, "2026-09-17", "USD", "SWAPTION", "LOGNORMAL")[("1Y", "10Y")])

    cap_rows = rvm.to_rate_vols_rows(conn, "2026-09-17", "USD", "CAP", "NORMAL")
    assert len(cap_rows) == len(rvm.EXPIRY_TENORS)
    assert all(r["underlying_tenor"] == "" for r in cap_rows)

    # Nothing staged for this combination -> empty list, not an error.
    assert rvm.to_rate_vols_rows(conn, "2026-09-17", "GBP", "SWAPTION", "LOGNORMAL") == []


def test_rate_vol_snapshot_round_trip(tmp_path):
    from data.bloomberg import rates_vol_marketdata as rvm

    snap = rvm.load_rate_vol_snapshot(RATE_VOL_FIXTURE)
    out_path = tmp_path / "roundtrip.json"
    rvm.save_rate_vol_snapshot(snap, out_path)
    reloaded = rvm.load_rate_vol_snapshot(out_path)
    assert reloaded.as_of == snap.as_of
    assert set(reloaded.currencies) == set(snap.currencies)
    assert reloaded.currencies["USD"].quotes[0].ticker == snap.currencies["USD"].quotes[0].ticker


# -- CLI ---------------------------------------------------------------------------------

def test_rate_vol_cli_file_mode_writes_rate_vol_quotes(tmp_path):
    from data.bloomberg import rates_vol_marketdata as rvm

    db_path = tmp_path / "risk.db"
    exit_code = rvm.main([
        "--db", str(db_path), "--as-of", "2026-09-17", "--file", str(RATE_VOL_FIXTURE),
    ])
    assert exit_code == 5  # fixture only has USD/EUR; the CLI requests all 7 conventions ccys

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM rate_vol_quotes").fetchone()[0]
    assert count > 0

    diag_path = Path(str(db_path) + ".diag.json")
    assert diag_path.exists()
    diag = json.loads(diag_path.read_text())
    assert diag["summary"]["outcome"] == "PARTIAL"
    assert diag["summary"]["exit_code"] == 5


def test_rate_vol_cli_file_mode_bad_as_of_exits_2(tmp_path):
    from data.bloomberg import rates_vol_marketdata as rvm

    db_path = tmp_path / "risk.db"
    exit_code = rvm.main(["--db", str(db_path), "--as-of", "not-a-date", "--file", str(RATE_VOL_FIXTURE)])
    assert exit_code == 2
    diag = json.loads(Path(str(db_path) + ".diag.json").read_text())
    assert diag["summary"]["exit_code"] == 2


def test_rate_vol_cli_file_mode_missing_as_of_exits_2(tmp_path):
    from data.bloomberg import rates_vol_marketdata as rvm

    db_path = tmp_path / "risk.db"
    exit_code = rvm.main(["--db", str(db_path), "--file", str(RATE_VOL_FIXTURE)])
    assert exit_code == 2


def test_rate_vol_cli_probe_mode_file_source_writes_diag(tmp_path):
    from data.bloomberg import rates_vol_marketdata as rvm

    db_path = tmp_path / "risk.db"
    exit_code = rvm.main([
        "--db", str(db_path), "--as-of", "2026-09-17", "--file", str(RATE_VOL_FIXTURE), "--probe",
    ])
    assert exit_code == 0
    diag = json.loads(Path(str(db_path) + ".diag.json").read_text())
    assert diag["mode"] == "probe"
    assert len(diag["probe"]["steps"]) == 3
    assert all(s["outcome"] == "OK" for s in diag["probe"]["steps"])
    # no rate_vol_quotes rows in probe mode
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "rate_vol_quotes" not in tables


def test_rate_vol_cli_file_mode_bad_path_exits_4(tmp_path):
    from data.bloomberg import rates_vol_marketdata as rvm

    db_path = tmp_path / "risk.db"
    exit_code = rvm.main([
        "--db", str(db_path), "--as-of", "2026-09-17", "--file", str(tmp_path / "does_not_exist.json"),
    ])
    assert exit_code in (3, 4)  # a bad file path surfaces as either, never a crash without a diag
    assert Path(str(db_path) + ".diag.json").exists()


# -- RateVolBloombergSource: fake blpapi, never the real SDK -----------------------------

def test_rate_vol_bloomberg_source_requires_blpapi_when_absent():
    from data.bloomberg import rates_vol_marketdata as rvm

    try:
        import blpapi  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("blpapi is installed in this environment; nothing to test for the absent-package path")

    with pytest.raises(rvm.MarketDataError):
        rvm.RateVolBloombergSource()


def test_rate_vol_bloomberg_source_get_quotes_with_fake_blpapi(monkeypatch):
    from data.bloomberg import rates_vol_marketdata as rvm

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        sec_list = []
        for t in request.securities:
            row = {}
            # Only answer the LOGNORMAL swaption ticker -- everything else in the batch
            # comes back with no fieldData, exercising the per-ticker MISSING diagnostics path.
            if t == "USSV0110 Curncy":
                row["PX_LAST"] = 19.5
            sec_list.append({"security": t, "fieldData": row})
        return [{"securityData": sec_list}]

    _install_fake_blpapi(monkeypatch, responder)

    src = rvm.RateVolBloombergSource("localhost", 8194)
    assert src.name() == "RateVolBloombergSource(localhost:8194)"
    result = src.get_rate_vol_quotes(
        ["USD"], as_of=None, instruments=["SWAPTION"], vol_types=["LOGNORMAL"],
        expiries=["1Y"], underlying_tenors=["10Y"],
    )
    assert result.source == "BBG_BDP"
    quote = result.currencies["USD"].quotes[0]
    assert quote.ticker == "USSV0110 Curncy"
    assert math.isclose(quote.value, 19.5)
    assert not result.diagnostics
    src.close()


def test_rate_vol_bloomberg_source_historical_request_for_explicit_as_of(monkeypatch):
    from data.bloomberg import rates_vol_marketdata as rvm

    def responder(request):
        assert request.req_type == "HistoricalDataRequest"
        return [{"securityData": {"security": t, "fieldData": [{"PX_LAST": 19.5}]}} for t in request.securities]

    _install_fake_blpapi(monkeypatch, responder)

    src = rvm.RateVolBloombergSource("localhost", 8194)
    result = src.get_rate_vol_quotes(
        ["USD"], as_of=date(2026, 9, 17), instruments=["SWAPTION"], vol_types=["LOGNORMAL"],
        expiries=["1Y"], underlying_tenors=["10Y"],
    )
    assert result.source == "BBG_BDH"
    quote = result.currencies["USD"].quotes[0]
    assert math.isclose(quote.value, 19.5)


def test_rate_vol_bloomberg_source_missing_ticker_recorded_as_diagnostic(monkeypatch):
    from data.bloomberg import rates_vol_marketdata as rvm

    def responder(request):
        # Answer nothing -- every requested ticker comes back with no fieldData.
        return [{"securityData": [{"security": t, "fieldData": {}} for t in request.securities]}]

    _install_fake_blpapi(monkeypatch, responder)

    src = rvm.RateVolBloombergSource("localhost", 8194)
    result = src.get_rate_vol_quotes(
        ["USD"], as_of=None, instruments=["CAP"], vol_types=["LOGNORMAL"], expiries=["1Y"],
    )
    assert result.currencies == {}
    assert len(result.diagnostics) == 1
    d = result.diagnostics[0]
    assert d["ccy"] == "USD" and d["instrument"] == "CAP" and d["status"] == "MISSING"
    assert d["ticker"] == "USCV01 Curncy"


def test_rate_vol_marketdata_module_imports_without_blpapi():
    # Importing the module must never require blpapi to be installed -- only
    # instantiating RateVolBloombergSource does.
    import importlib

    import data.bloomberg.rates_vol_marketdata as rvm
    importlib.reload(rvm)
    assert callable(rvm.rate_vol_ticker)


# =========================================================================== 2_launcher.py: startup speed (2026-09-17)
# The launcher is owned by bbg-data alongside data/bloomberg/ for this fix (a cross-cutting
# startup-speed pass across 2_launcher.py + data/bloomberg/live.py + backfill.py); loaded by
# file path the same way tests/test_risk_cli.py does (the filename starts with a digit, so
# it cannot be a normal `import`). importlib.util.module_from_spec + exec_module never
# registers into sys.modules, so loading it again here under an independent name cannot
# collide with test_risk_cli.py's own separate load of the same file.
def _load_launcher_module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("risk_launcher_perf", root / "2_launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_packages_stamp_round_trips_and_changes_with_packages_list(tmp_path, monkeypatch):
    risk = _load_launcher_module()
    monkeypatch.setattr(risk, "VENV", tmp_path / "venv")
    monkeypatch.setattr(risk, "PACKAGES_STAMP", tmp_path / "venv" / "packages.stamp")
    (tmp_path / "venv").mkdir()
    assert risk._packages_stamp_matches() is False       # no stamp written yet
    risk._write_packages_stamp()
    assert risk._packages_stamp_matches() is True
    # Editing PACKAGES invalidates a previously-written stamp.
    monkeypatch.setattr(risk, "PACKAGES", risk.PACKAGES + ["a-new-dependency>=1"])
    assert risk._packages_stamp_matches() is False


def test_venv_imports_ok_skips_subprocess_when_stamp_matches(tmp_path, monkeypatch):
    """The real perf fix: on a matching stamp, venv_imports_ok() must never spawn the
    VENV_PY subprocess (measured 1.5s/start on a slow PC, 2026-09-17)."""
    risk = _load_launcher_module()
    monkeypatch.setattr(risk, "VENV", tmp_path / "venv")
    monkeypatch.setattr(risk, "PACKAGES_STAMP", tmp_path / "venv" / "packages.stamp")
    (tmp_path / "venv").mkdir()
    risk._write_packages_stamp()

    def boom(*a, **k):
        raise AssertionError("subprocess.call must not run when the stamp matches")

    monkeypatch.setattr(risk.subprocess, "call", boom)
    assert risk.venv_imports_ok() is True


def test_venv_imports_ok_falls_back_to_subprocess_and_writes_stamp_on_success(tmp_path, monkeypatch):
    risk = _load_launcher_module()
    monkeypatch.setattr(risk, "VENV", tmp_path / "venv")
    monkeypatch.setattr(risk, "PACKAGES_STAMP", tmp_path / "venv" / "packages.stamp")
    (tmp_path / "venv").mkdir()
    assert not (tmp_path / "venv" / "packages.stamp").exists()
    monkeypatch.setattr(risk.subprocess, "call", lambda *a, **k: 0)  # simulate a clean import check
    assert risk.venv_imports_ok() is True
    assert risk._packages_stamp_matches() is True  # cached for the next call


def test_cmd_start_skips_pip_install_when_refresh_packages_flag_set_but_stamp_matches(tmp_path, monkeypatch):
    """2026-09-17 fix: --refresh-packages (set on every start after a GitHub code update)
    used to force an unconditional `pip install` even when nothing needed installing --
    the single biggest cost on a PC with slow PyPI access. Only venv_imports_ok() (stamp-
    backed) may trigger it now."""
    risk = _load_launcher_module()
    monkeypatch.setattr(risk, "in_venv", lambda: False)
    monkeypatch.setattr(risk, "sync_with_github", lambda: (False, "code is current with GitHub (test)"))
    monkeypatch.setattr(risk.VENV_PY.__class__, "exists", lambda self: True)
    monkeypatch.setattr(risk, "venv_imports_ok", lambda: True)  # stamp says: nothing to do

    def boom(cmd, **kw):
        if "pip" in cmd:
            raise AssertionError("pip install must not run merely because --refresh-packages was set")
        return 0

    monkeypatch.setattr(risk.subprocess, "call", boom)
    monkeypatch.setattr(risk.sys, "argv", ["2_launcher.py", "start", "--refresh-packages"])
    args = risk.build_parser().parse_args(["start", "--refresh-packages"])
    risk.cmd_start(args)  # must not raise via the boom() guard above


def test_cmd_setup_sample_skipped_when_trades_already_present(tmp_path, monkeypatch):
    """2026-09-17: `setup --sample` must only ever seed an empty database -- the blotter
    upload is the one live trade source and replaces everything on a real PC."""
    risk = _load_launcher_module()
    db = tmp_path / "risk.db"
    conn = schema.connect(db)
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('a1','XLSX','USDJPY','FX_FWD','a1','2026-08-10',1e6,150.0,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.commit()
    monkeypatch.setenv("RISK_DB", str(db))
    assert risk._trades_count() == 1

    calls = []
    monkeypatch.setattr(risk, "run", lambda cmd, **kw: calls.append(cmd))
    args = risk.build_parser().parse_args(["setup", "--sample", "--skip-tests", "--no-bloomberg"])
    monkeypatch.setattr(risk.sys, "version_info", risk.sys.version_info)  # keep real (>= MIN_PYTHON)
    monkeypatch.setattr(risk, "install_pnl_function", lambda: [])
    risk.cmd_setup(args)
    assert not any("_load_sample" in str(c) for call in calls for c in call)


def test_trades_count_zero_when_database_missing(tmp_path, monkeypatch):
    risk = _load_launcher_module()
    monkeypatch.setenv("RISK_DB", str(tmp_path / "nope.db"))
    assert risk._trades_count() == 0


def test_sync_with_github_fetch_timeout_reports_slow_github_not_a_generic_git_error(monkeypatch):
    """A blocked/slow GitHub (seen from China, 2026-09-17) must not stall every `start`
    for the default 90s subprocess timeout, and must say plainly what happened."""
    import subprocess as _subprocess
    risk = _load_launcher_module()
    monkeypatch.setattr(risk, "ROOT", risk.ROOT)  # ROOT is a real git repo in this checkout

    def fake_git(*args, timeout=90):
        assert args[:2] == ("fetch", "origin") and timeout == 10  # short-timeout ask
        raise _subprocess.TimeoutExpired(cmd="git fetch", timeout=timeout)

    monkeypatch.setattr(risk, "_git", fake_git)
    changed, message = risk.sync_with_github()
    assert changed is False
    assert message == "GitHub slow, running the code on disk"


def test_tcp_open_resolves_localhost_to_127_0_0_1(monkeypatch):
    risk = _load_launcher_module()
    seen = []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_create_connection(addr, timeout=None):
        seen.append(addr)
        return _FakeConn()

    monkeypatch.setattr(risk.socket, "create_connection", fake_create_connection)
    assert risk.tcp_open("localhost", 8194) is True
    assert seen == [("127.0.0.1", 8194)]


# =========================================================================== 2026-09-21: points divisor
# Found on the Bloomberg PC: "Bloomberg returned forward points for AUDUSD but no
# FWD_POINTS_SCALE, so they cannot be converted to outrights" -- 45 of 67 marks of a past
# close. The field name was never verified. Both candidates are now asked for in ONE
# request: FWD_POINTS_SCALE as the divisor itself, else 10 ** FWD_SCALE.
def test_scale_from_fields_prefers_fwd_points_scale_then_ten_to_the_fwd_scale():
    from data.bloomberg.pull_marks import scale_from_fields
    assert scale_from_fields({"FWD_POINTS_SCALE": 10000.0, "FWD_SCALE": 2}) == (10000.0, "FWD_POINTS_SCALE")
    assert scale_from_fields({"FWD_SCALE": 4}) == (10000.0, "FWD_SCALE")            # EURUSD: 4 decimal places
    assert scale_from_fields({"FWD_SCALE": 2.0}) == (100.0, "FWD_SCALE")            # USDJPY
    assert scale_from_fields({"FWD_SCALE": 0}) == (1.0, "FWD_SCALE")
    # FWD_POINTS_SCALE that is not a positive number falls through to FWD_SCALE
    assert scale_from_fields({"FWD_POINTS_SCALE": 0, "FWD_SCALE": 4}) == (10000.0, "FWD_SCALE")
    assert scale_from_fields({"FWD_POINTS_SCALE": "n.a.", "FWD_SCALE": "4"}) == (10000.0, "FWD_SCALE")
    # never a guess: not a whole number, out of range, a bool, nothing at all
    for row in ({"FWD_SCALE": 2.5}, {"FWD_SCALE": 9}, {"FWD_SCALE": -1}, {"FWD_SCALE": True},
                {"FWD_POINTS_SCALE": float("nan")}, {}, None):
        assert scale_from_fields(row) == (None, "")


def _scale_responder(sent):
    """A terminal that does not know FWD_POINTS_SCALE (a field exception for that field
    only) and answers FWD_SCALE for AUDUSD and USDJPY, nothing for EURSEK."""
    answers = {"AUDUSD Curncy": {"FWD_SCALE": 4}, "USDJPY Curncy": {"FWD_SCALE": 2}, "EURSEK Curncy": {}}

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        sent.append((list(request.securities), list(request.fields)))
        secs = []
        for t in request.securities:
            exceptions = [{"fieldId": "FWD_POINTS_SCALE", "errorInfo": {"message": "Field not valid"}}]
            if not answers.get(t):
                exceptions.append({"fieldId": "FWD_SCALE", "errorInfo": {"message": "Field not applicable to security"}})
            secs.append({"security": t, "fieldData": dict(answers.get(t) or {}), "fieldExceptions": exceptions})
        return [{"securityData": secs}]
    return responder


def test_fetch_points_scales_asks_both_fields_in_one_request_and_survives_a_field_exception(monkeypatch):
    sent = []
    _install_fake_blpapi(monkeypatch, _scale_responder(sent))
    from data.bloomberg import pull_marks as pm
    session, service = pm.open_session("localhost", 8194)
    reports = pm.fetch_points_scales(session, service, ["AUDUSD", "USDJPY", "EURSEK"])
    # ONE ReferenceDataRequest for every pair, both fields in it
    assert sent == [(["AUDUSD Curncy", "USDJPY Curncy", "EURSEK Curncy"], ["FWD_POINTS_SCALE", "FWD_SCALE"])]
    # the refused field costs nothing: the other one is still read
    assert (reports["AUDUSD"]["scale"], reports["AUDUSD"]["field"]) == (10000.0, "FWD_SCALE")
    assert (reports["USDJPY"]["scale"], reports["USDJPY"]["field"]) == (100.0, "FWD_SCALE")
    assert reports["AUDUSD"]["raw"] == {"FWD_SCALE": 4}
    assert reports["AUDUSD"]["errors"] == {"FWD_POINTS_SCALE": "Field not valid"}
    assert "FWD_SCALE = 4" in pm.describe_scale("AUDUSD", reports["AUDUSD"])        # names the field that answered
    # neither answered: no scale (never a hard-coded pip size), and the reason names BOTH
    # fields, each with Bloomberg's own words
    assert reports["EURSEK"]["scale"] is None and reports["EURSEK"]["field"] == ""
    reason = pm.describe_scale("EURSEK", reports["EURSEK"])
    assert "neither FWD_POINTS_SCALE nor FWD_SCALE" in reason
    assert "Field not valid" in reason and "Field not applicable to security" in reason


def _tenor_path_responder(scale_row):
    ref = {("EURUSDSP Curncy", "PX_LAST"): 0.0, ("EURUSDSP Curncy", "SETTLE_DT"): "2026-08-19",
           ("EURUSD1W Curncy", "PX_LAST"): 50.0, ("EURUSD1W Curncy", "SETTLE_DT"): "2026-08-24"}
    ref.update({("EURUSD Curncy", f): v for f, v in scale_row.items()})

    def responder(request):
        assert request.req_type == "ReferenceDataRequest"
        return [{"securityData": [{"security": t, "fieldData": {f: ref[(t, f)] for f in request.fields if (t, f) in ref}}
                                  for t in request.securities]}]
    return responder


def test_live_tenor_path_uses_the_shared_scale_helper_and_never_writes_an_implausible_outright(monkeypatch):
    from data.bloomberg import pull_marks as pm
    rows = [pm.RequestRow("EURUSD", "EURUSD Curncy", "2026-08-20", "FWD_OUTRIGHT")]

    # FWD_SCALE alone answers: 10 points / 10 ** 4 on a 1.1000 spot
    _install_fake_blpapi(monkeypatch, _tenor_path_responder({"FWD_SCALE": 4}))
    session, service = pm.open_session("localhost", 8194)
    diag = pm.Diagnostics()
    out, _warnings, failures = pm.build_fwd_outright_rows(session, service, rows, date(2026, 8, 17), {"EURUSD": 1.1}, diag)
    assert failures == [] and math.isclose(out[0]["value"], 1.1 + 10.0 / 10000.0) and out[0]["source"] == "BBG_INTERP"
    assert diag.interpolations[-1]["scale_field"] == "FWD_SCALE"                    # the report names the field
    scale_requests = [r for r in diag.requests if r.get("purpose") == "tenor_fallback_scale"]
    assert [r["fields"] for r in scale_requests] == [["FWD_POINTS_SCALE", "FWD_SCALE"]]

    # a divisor that throws the outright more than 20 % off spot is never written
    _install_fake_blpapi(monkeypatch, _tenor_path_responder({"FWD_POINTS_SCALE": 10.0}))
    session, service = pm.open_session("localhost", 8194)
    out, _warnings, failures = pm.build_fwd_outright_rows(session, service, rows, date(2026, 8, 17), {"EURUSD": 1.1})
    assert out == [] and failures[0]["classification"] == pm.CLASS_REJECTED
    assert "20%" in failures[0]["detail"] and "FWD_POINTS_SCALE" in failures[0]["detail"]

    # neither field: the failure names both
    _install_fake_blpapi(monkeypatch, _tenor_path_responder({}))
    session, service = pm.open_session("localhost", 8194)
    out, _warnings, failures = pm.build_fwd_outright_rows(session, service, rows, date(2026, 8, 17), {"EURUSD": 1.1})
    assert out == [] and "neither FWD_POINTS_SCALE nor FWD_SCALE" in failures[0]["detail"]


# =========================================================================== 2026-09-21: the 15:00 New York close
# User: "the EOD is 3pm New York time"; "for previous or any closes in FX, we need to use NY
# 3pm". Bloomberg's daily history has no 15:00 field, so a past FX close is read from
# hourly intraday bars: one IntradayBarRequest per security per side (BID, ASK) per stretch.
def _bars_responder(sent, bars, errors=None):
    """bars: {(ticker, side): [(naive UTC bar start, close), ...]}; errors: {ticker: message}."""
    def responder(request):
        assert request.req_type == "IntradayBarRequest"
        sent.append({"security": request.security, "eventType": request.eventType, "interval": request.interval,
                     "start": request.startDateTime, "end": request.endDateTime, "gapFill": request.gapFillInitialBar})
        if (errors or {}).get(request.security):
            return [{"responseError": {"message": errors[request.security]}}]
        ticks = [{"time": when, "close": close} for when, close in bars.get((request.security, request.eventType), [])]
        return [{"barData": {"barTickData": ticks}}]
    return responder


def test_close_bar_times_resolve_the_new_york_offset_per_date():
    from datetime import datetime, timezone
    from data.bloomberg import pull_marks as pm
    assert pm.CLOSE_HOUR_NY == 15 and pm.snapped_at(date(2026, 1, 15)) == "2026-01-15T15:00:00-05:00"
    # 15:00 New York is 19:00 UTC in summer and 20:00 UTC in winter; the bar starts an hour before
    assert pm.close_time_utc(date(2026, 7, 15)) == datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)
    assert pm.close_time_utc(date(2026, 1, 15)) == datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
    assert pm.close_bar_start_utc(date(2026, 7, 15)) == datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
    assert pm.close_bar_start_utc(date(2026, 1, 15)) == datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc)


def test_fetch_intraday_close_series_takes_the_bar_ending_1500_new_york_mid_of_bid_and_ask(monkeypatch):
    """A stretch across the US clock change (Sunday 2026-03-08): Friday's close bar starts
    19:00 UTC (EST), Monday's 18:00 UTC (EDT). Decoy bars sit at the other hour."""
    from datetime import datetime
    sent = []
    fri, mon = datetime(2026, 3, 6, 19, 0), datetime(2026, 3, 9, 18, 0)
    bars = {
        ("EURUSD Curncy", "BID"): [(datetime(2026, 3, 6, 18, 0), 9.0), (fri, 1.1700), (mon, 1.1800), (datetime(2026, 3, 9, 19, 0), 9.0)],
        ("EURUSD Curncy", "ASK"): [(datetime(2026, 3, 6, 18, 0), 9.0), (fri, 1.1704), (mon, 1.1806), (datetime(2026, 3, 9, 19, 0), 9.0)],
        # forward points, negative: a mid all the same; Monday has a BID bar only
        ("USDJPY1M Curncy", "BID"): [(fri, -51.0), (mon, -49.0)],
        ("USDJPY1M Curncy", "ASK"): [(fri, -49.0)],
        # a ticker with no bar at the close on either day (only an earlier hour)
        ("USDTHB Curncy", "BID"): [(datetime(2026, 3, 6, 15, 0), 32.0)],
        ("USDTHB Curncy", "ASK"): [(datetime(2026, 3, 6, 15, 0), 32.1)],
    }
    _install_fake_blpapi(monkeypatch, _bars_responder(sent, bars, errors={"XXXYYY Curncy": "Security is not valid"}))
    from data.bloomberg import pull_marks as pm
    session, service = pm.open_session("localhost", 8194)
    tickers = ["EURUSD Curncy", "USDJPY1M Curncy", "USDTHB Curncy", "XXXYYY Curncy"]
    out = pm.fetch_intraday_close_series(session, service, tickers, ["PX_LAST"], date(2026, 3, 6), date(2026, 3, 9))

    # one request per (ticker, side) for the whole stretch -- never one per day
    assert [(r["security"], r["eventType"]) for r in sent] == [(t, side) for t in tickers for side in ("BID", "ASK")]
    # hourly bars, gap fill on, UTC from the first day's bar start to the last day's close
    assert all(r["interval"] == 60 and r["gapFill"] is True for r in sent)
    assert all(r["start"] == fri and r["end"] == datetime(2026, 3, 9, 19, 0) for r in sent)

    # the shape fetch_historical_series returns for PX_LAST; weekdays only
    assert set(out) == set(tickers) and set(out["EURUSD Curncy"]) == {"2026-03-06", "2026-03-09"}
    assert out["EURUSD Curncy"]["2026-03-06"] == {"PX_LAST": pytest.approx(1.1702)}   # mid of 1.1700 / 1.1704
    assert out["EURUSD Curncy"]["2026-03-09"] == {"PX_LAST": pytest.approx(1.1803)}   # the 18:00 UTC bar, not 19:00
    assert out["USDJPY1M Curncy"]["2026-03-06"] == {"PX_LAST": pytest.approx(-50.0)}

    # one side only, no bar at the close, Bloomberg's own error: missing with a plain
    # reason, never a substitute value
    one_side = out["USDJPY1M Curncy"]["2026-03-09"]
    assert set(one_side) == {pm.CLOSE_REASON} and "only the BID side" in one_side[pm.CLOSE_REASON]
    no_bar = out["USDTHB Curncy"]["2026-03-06"]
    assert set(no_bar) == {pm.CLOSE_REASON} and "no hourly bar ending 15:00 New York" in no_bar[pm.CLOSE_REASON]
    refused = out["XXXYYY Curncy"]["2026-03-09"]
    assert set(refused) == {pm.CLOSE_REASON} and "Security is not valid" in refused[pm.CLOSE_REASON]


def test_fetch_intraday_bars_reads_aware_bar_times_too_and_records_the_request(monkeypatch):
    from datetime import datetime, timedelta, timezone
    sent = []
    aware = datetime(2026, 7, 15, 14, 0, tzinfo=timezone(timedelta(hours=-4)))
    _install_fake_blpapi(monkeypatch, _bars_responder(sent, {("EURUSD Curncy", "BID"): [(aware, 1.17)]}))
    from data.bloomberg import pull_marks as pm
    session, service = pm.open_session("localhost", 8194)
    diag = pm.Diagnostics()
    day = date(2026, 7, 15)
    bars, error = pm.fetch_intraday_bars(session, service, "EURUSD Curncy", "BID", pm.close_bar_start_utc(day),
                                         pm.close_time_utc(day), diag=diag)
    assert error == "" and bars == [{"time": pm.close_bar_start_utc(day), "close": 1.17}]   # 14:00-04:00 == 18:00 UTC
    rec = diag.requests[-1]
    assert rec["request_type"] == "IntradayBarRequest" and rec["classification"] == pm.CLASS_OK
    assert rec["overrides"]["startDateTime"] == "2026-07-15T18:00:00Z" and rec["overrides"]["interval"] == "60"


def test_probe_asks_both_scale_fields_and_runs_one_intraday_bar_request(monkeypatch, tmp_path, capsys):
    ref_data = {("EURUSD Curncy", "FWD_SCALE"): 4, ("USDJPY Curncy", "FWD_SCALE"): 2, ("EURUSD Curncy", "PX_LAST"): 1.17}
    seen = []
    inner = _full_probe_responder(ref_data, {"EURUSD Curncy": 1.17, "ESU6 Index": 6500.0})

    def responder(request):
        seen.append(request)
        return inner(request)

    _install_fake_blpapi(monkeypatch, responder)
    from data.bloomberg import pull_marks, pull_report
    out = tmp_path / "probe"
    assert pull_marks.main(["--probe", "--as-of", "2026-09-21", "--out", str(out)]) == 0      # a Monday
    diag = pull_report.load_diag(Path(str(out) + ".diag.json"))
    steps = {r["probe_name"]: r for r in diag["requests"] if r.get("probe_name")}

    scale = steps["fwd_points_scale"]
    assert scale["fields"] == ["FWD_POINTS_SCALE", "FWD_SCALE"] and scale["tickers"] == ["EURUSD Curncy", "USDJPY Curncy"]
    assert "'FWD_SCALE': 4" in scale["detail"] and "'FWD_SCALE': 2" in scale["detail"]      # what came back, printed
    printed = capsys.readouterr().err
    assert "points divisor 10000 from FWD_SCALE" in printed and "points divisor 100 from FWD_SCALE" in printed

    bar = steps["intraday_close_bar"]
    assert bar["request_type"] == "IntradayBarRequest" and bar["tickers"] == ["EURUSD Curncy"] and bar["fields"] == ["BID"]
    # the business day before --as-of (Friday 2026-09-18), 14:00-15:00 New York = 18:00-19:00 UTC
    assert bar["overrides"]["startDateTime"] == "2026-09-18T18:00:00Z"
    assert bar["overrides"]["endDateTime"] == "2026-09-18T19:00:00Z"
    assert bar["classification"] == "OK" and "1.1712" in bar["detail"]
    request = next(r for r in seen if r.req_type == "IntradayBarRequest")
    assert (request.security, request.eventType, request.interval) == ("EURUSD Curncy", "BID", 60)
    assert "intraday_close_bar" in pull_report.render_report(diag)
