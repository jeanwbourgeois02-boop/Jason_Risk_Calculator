"""Tests for data/bloomberg (marks CSV format, BNP_BVAL marks, Bloomberg pull script).

Real-file tests skip if the raw BNP CSV is absent. The pull_marks tests inject a fake
`blpapi` module into sys.modules so they run without the real SDK installed.
"""
from __future__ import annotations

import csv
import math
import sqlite3
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from data.bloomberg import bnp_marks, marks_csv
from data.ingest import bnp, schema

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "HA_PNL_20260818.csv"
AS_OF = "2026-08-17"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason=f"raw file absent: {RAW}")


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
    assert marks_csv.MARK_TYPES == {
        "SPOT", "FWD_OUTRIGHT", "FUTURE_PX", "PAR_RATE", "PV_USD", "DV01_USD", "PREMIUM", "DELTA"}
    assert marks_csv.SOURCES == {
        "BNP_BVAL", "BBG_BFXFORWARD", "BBG_BDH", "BBG_BDP", "MANUAL", "BBG_INTERP"}
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
        "'ACC','CPTY','STRAT','TRADER','synthetic')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('t1',1,'FX_NEAR','EUR',100.0,'2026-08-17','2026-09-16',1.1,1)"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('t2','MANUAL','ESU6 Index','FUTURE','t2','2026-08-17',10.0,4500.0,"
        "'ACC','CPTY','STRAT','TRADER','synthetic')"
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
    assert ("EURUSD", "FWD_OUTRIGHT", "2026-09-16") in kinds
    assert ("ESU6 Index", "FUTURE_PX", "2026-09-18") in kinds


def test_export_request_excludes_expired_future(tmp_path):
    conn = schema.connect(":memory:")
    # Expired future (expiry_date <= as_of) with an open leg: must NOT be requested.
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-08-10')")
    conn.execute(
        "INSERT INTO trades VALUES ('t1','MANUAL','ESU6 Index','FUTURE','t1','2026-07-01',10.0,4500.0,"
        "'ACC','CPTY','STRAT','TRADER','synthetic')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('t1',1,'NOTIONAL','USD',225000.0,'2026-07-01','2026-08-10',0,0)"
    )
    # Unexpired future but no open leg and no position row: must NOT be requested either.
    conn.execute("INSERT INTO instruments VALUES ('ESZ6 Index','FUTURE','ES','USD',50,0,'ESZ6 Index','2026-12-18')")
    # Unexpired future with a positions row (no trade_legs): must be requested.
    conn.execute("INSERT INTO instruments VALUES ('ESH7 Index','FUTURE','ES','USD',50,0,'ESH7 Index','2027-03-19')")
    conn.execute(
        "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (AS_OF, "BNP", "ACC", "ESH7 Index", "2027-03-19", 5.0, 0, 0, 1.0, 0, 0, 0, 0, 0),
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


# =========================================================================== bnp_marks
@needs_raw
def test_bnp_marks_every_forward_row_covered_by_distinct_keys():
    parsed = bnp.parse(RAW, as_of_date=AS_OF)
    result = bnp_marks.extract_bnp_marks(RAW, as_of_date=AS_OF)
    assert not result.rejects

    fwd_rows = [r for r in result.rows if r.mark_type == "FWD_OUTRIGHT"]
    # distinct (pair, value_date) from the parsed trades' near-base legs
    keys = set()
    trade_pair = {t.trade_id: t.instrument_id for t in parsed.trades}
    legs_by_trade = {}
    for l in parsed.legs:
        legs_by_trade.setdefault(l.trade_id, []).append(l)
    for trade_id, pair in trade_pair.items():
        base = pair[:3]
        for l in legs_by_trade[trade_id]:
            if l.ccy == base:
                keys.add((pair, l.settle_date))
    assert len(fwd_rows) == len(keys)
    assert {(r.instrument_id, r.settle_date) for r in fwd_rows} == keys


@needs_raw
def test_bnp_marks_no_conflicting_values():
    result = bnp_marks.extract_bnp_marks(RAW, as_of_date=AS_OF)
    assert result.rejects == []


@needs_raw
def test_bnp_marks_usdtry_spot():
    result = bnp_marks.extract_bnp_marks(RAW, as_of_date=AS_OF)
    spot = [r for r in result.rows if r.mark_type == "SPOT" and r.instrument_id == "USDTRY"]
    assert len(spot) == 1
    assert math.isclose(spot[0].value, 1.0 / 0.020877, rel_tol=1e-3)


@needs_raw
def test_bnp_marks_no_spot_is_1_0_and_xxxusd_pairs_have_no_spot():
    # BNP's Fx is quote_ccy -> USD, which is exactly 1.0 on every USD-quoted (XXXUSD) row
    # -- it is not a usable base spot for those pairs, so no SPOT should ever be emitted
    # with value 1.0, and no XXXUSD pair should get a SPOT at all.
    result = bnp_marks.extract_bnp_marks(RAW, as_of_date=AS_OF)
    spots = [r for r in result.rows if r.mark_type == "SPOT"]
    assert spots
    assert all(r.value != 1.0 for r in spots)

    xxxusd_pairs = {r.instrument_id for r in result.rows if r.mark_type == "FWD_OUTRIGHT" and r.instrument_id.endswith("USD")}
    assert xxxusd_pairs  # sanity: the reference file has XXXUSD pairs (AUD/EUR/GBP/XAU)
    spot_pairs = {r.instrument_id for r in spots}
    assert xxxusd_pairs.isdisjoint(spot_pairs)
    assert any(f"XXXUSD pair" in w for w in result.warnings)


@needs_raw
def test_load_bnp_marks_into_bnp_loaded_db():
    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    result = bnp_marks.load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=True)
    assert result.rows
    n = conn.execute("SELECT COUNT(*) FROM marks WHERE source = 'BNP_BVAL'").fetchone()[0]
    assert n == len(result.rows)


@needs_raw
def test_load_bnp_marks_twice_nonstrict_all_duplicate_rejects_no_double_insert():
    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    first = bnp_marks.load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=True)
    n_after_first = conn.execute("SELECT COUNT(*) FROM marks WHERE source = 'BNP_BVAL'").fetchone()[0]
    assert n_after_first == len(first.rows)

    second = bnp_marks.load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=False)
    assert second.rows == []
    assert len(second.rejects) == len(first.rows)
    assert all("duplicate" in r.reason for r in second.rejects)
    n_after_second = conn.execute("SELECT COUNT(*) FROM marks WHERE source = 'BNP_BVAL'").fetchone()[0]
    assert n_after_second == n_after_first  # no double insert


@needs_raw
def test_load_bnp_marks_twice_strict_raises():
    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    bnp_marks.load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=True)
    with pytest.raises(ValueError):
        bnp_marks.load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=True)


@needs_raw
def test_bnp_bval_never_official_in_ladder_convert_to_usd():
    from engine.ladder.ladder import cash_ladder, convert_to_usd, spot_table

    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    bnp_marks.load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=True)

    # marks_official is still empty: BNP_BVAL never qualifies as official for SPOT.
    n_official = conn.execute("SELECT COUNT(*) FROM marks_official").fetchone()[0]
    assert n_official == 0

    spot = spot_table(conn, AS_OF)
    assert spot.empty

    ladder = cash_ladder(conn, AS_OF)
    out = convert_to_usd(ladder, spot)
    non_usd = out[out["ccy"] != "USD"]
    assert non_usd["amount_usd"].isna().all()


def test_bnp_marks_conflicting_rows_are_rejected(tmp_path, monkeypatch):
    # Build a tiny synthetic BNP CSV with two FORWARD rows sharing (pair, value date)
    # but different Price, to exercise the conflict-reject path directly (the real file
    # has none).
    import pandas as pd

    cols = [
        "Fund", "Financial Type", "Symbol", "Symbol Description", "Currency", "Quantity",
        "Local Cost", "Price", "Fx", "Market Value Local", "Market Value Base", "Account",
        "CounterParty", "NM Strategy", "Trader Name", "DTD Total P&L", "DTD Trading P&L",
        "MTD Total P&L", "Start Date Dirty MV", "Previous Month End Market Value Base",
        "Position", "Trade Factor",
    ]
    desc = "TD 08/15/2026 VD 09/16/2026 SELL USD VS .BUY JPY @ 150.00000000"
    row1 = {c: 0 for c in cols}
    row1.update({
        "Fund": "NMMF", "Financial Type": "FORWARD", "Symbol": "USDJPY091626-1",
        "Symbol Description": desc, "Currency": "JPY.C-XXAA", "Quantity": -1000000.0,
        "Local Cost": -150000000.0, "Price": 150.10, "Fx": 0.0067,
        "Account": "ACC", "CounterParty": "CPTY", "NM Strategy": "HAHY7", "Trader Name": "T",
        "Position": -1000000.0, "Trade Factor": 1,
    })
    row2 = dict(row1)
    row2.update({"Symbol": "USDJPY091626-2", "Price": 150.20})

    df = pd.DataFrame([row1, row2])
    path = tmp_path / "HA_PNL_20260818.csv"
    df.to_csv(path, index=False)

    result = bnp_marks.extract_bnp_marks(path, as_of_date=AS_OF)
    assert any("conflicting FWD_OUTRIGHT" in r.reason for r in result.rejects)


# =========================================================================== pull_marks
def _install_fake_blpapi(monkeypatch, responder):
    fake = types.ModuleType("blpapi")

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

    class FakeMsg:
        def __init__(self, data):
            self._data = data

        def hasElement(self, name):
            return name in self._data

        def getElement(self, name):
            return FakeStructElement(self._data[name])

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

    class Session:
        def __init__(self, opts):
            self.opts = opts
            self._last_request = None

        def start(self):
            return True

        def openService(self, name):
            return True

        def getService(self, name):
            return self

        def createRequest(self, req_type):
            return FakeRequest(req_type)

        def sendRequest(self, request):
            self._last_request = request

        def nextEvent(self, timeout=None):
            data = responder(self._last_request)
            return FakeEvent([FakeMsg(d) for d in data], Event.RESPONSE)

        def stop(self):
            pass

    fake.SessionOptions = SessionOptions
    fake.Session = Session
    fake.Event = Event
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
            ticker = request.securities[0]
            field = request.fields[0]
            value = hist_data.get(ticker)
            field_data = [{field: value}] if value is not None else []
            return [{"securityData": {"security": ticker, "fieldData": field_data}}]
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
    rows, warnings = pull_marks.run(session, service, request_rows, date(2026, 8, 17))

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


def _responder_ref_and_hist(ref_data, hist_data):
    def responder(request):
        if request.req_type == "HistoricalDataRequest":
            ticker = request.securities[0]
            field = request.fields[0]
            value = hist_data.get(ticker)
            field_data = [{field: value}] if value is not None else []
            return [{"securityData": {"security": ticker, "fieldData": field_data}}]
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
    rows, warnings = pull_marks.run(session, service, request_rows, date(2026, 8, 17))
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
    rows, warnings = pull_marks.run(session, service, request_rows, date(2026, 8, 17))
    fwd_rows = [r for r in rows if r["mark_type"] == "FWD_OUTRIGHT"]
    assert fwd_rows == []
    assert any("before spot" in w for w in warnings)


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
    rows, warnings = pull_marks.build_fwd_outright_rows(
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
