"""Tests for data/bloomberg (marks CSV format, BNP_BVAL marks, Bloomberg pull script).

Real-file tests skip if the raw BNP CSV is absent. The pull_marks tests inject a fake
`blpapi` module into sys.modules so they run without the real SDK installed.
"""
from __future__ import annotations

import csv
import inspect
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
    assert ("EURUSD", "FWD_OUTRIGHT", "2026-08-24") in kinds
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


def test_bnp_marks_skip_closed_lines(tmp_path):
    """Quantity = 0 lines (NDF fixed / settled, 2026-09-15 file) carry a fixing or a
    stale Price and possibly a blank Fx: no FWD_OUTRIGHT or SPOT from them, no reject,
    and they must not seed the quote-ccy -> USD map used for cross spots."""
    import math

    import pandas as pd

    cols = [
        "Fund", "Financial Type", "Symbol", "Symbol Description", "Currency", "Quantity",
        "Local Cost", "Price", "Fx", "Market Value Local", "Market Value Base", "Account",
        "CounterParty", "NM Strategy", "Trader Name", "DTD Total P&L", "DTD Trading P&L",
        "MTD Total P&L", "Start Date Dirty MV", "Previous Month End Market Value Base",
        "Position", "Trade Factor",
    ]
    base = {c: 0 for c in cols}
    base.update({"Fund": "NMMF", "Financial Type": "FORWARD", "Account": "ACC", "CounterParty": "CPTY",
                 "NM Strategy": "HAHY7", "Trader Name": "T", "Trade Factor": 1})
    # settled USDHKD line first in file order: Price 0, Fx blank (NaN)
    settled = dict(base, Symbol="USDHKD090826-1", Currency="HKD.C-HKAA", Quantity=0.0, Price=0.0, Fx=float("nan"),
                   **{"Symbol Description": "TD 08/05/2026 VD 09/08/2026 SELL USD VS .BUY HKD @ 7.83444000"})
    # fixed USDBRL NDF lines with two different "Price" values on one value date
    fixed1 = dict(base, Symbol="USDBRL091626-2", Currency="BRL.C-BRAA", Quantity=0.0, Price=5.14965, Fx=0.194189,
                  **{"Symbol Description": "TD 07/23/2026 VD 09/16/2026 SELL USD VS .BUY BRL @ 5.14017400"})
    fixed2 = dict(fixed1, Symbol="USDBRL091626-3", Price=5.13257)
    # live lines: a USDHKD forward (its Fx must be the one that seeds HKD) and a USDBRL later date
    live_hkd = dict(base, Symbol="USDHKD101526-4", Currency="HKD.C-HKAA", Quantity=1000000.0,
                    **{"Local Cost": 7800000.0, "Price": 7.79, "Fx": 0.128205, "Position": 1000000.0,
                       "Symbol Description": "TD 09/01/2026 VD 10/15/2026 BUY USD VS .SELL HKD @ 7.80000000"})
    live_brl = dict(base, Symbol="USDBRL092426-5", Currency="BRL.C-BRAA", Quantity=-1000000.0,
                    **{"Local Cost": -5225640.0, "Price": 5.15904, "Fx": 0.194189, "Position": -1000000.0,
                       "Symbol Description": "TD 08/20/2026 VD 09/24/2026 SELL USD VS .BUY BRL @ 5.22564000"})
    path = tmp_path / "HA_PNL_20260915.csv"
    pd.DataFrame([settled, fixed1, fixed2, live_hkd, live_brl]).to_csv(path, index=False)

    result = bnp_marks.extract_bnp_marks(path, as_of_date="2026-09-14")
    assert result.rejects == [] and result.n_skipped_closed == 3
    marks = {(r.instrument_id, r.settle_date, r.mark_type): r.value for r in result.rows}
    assert marks == {
        ("USDHKD", "2026-10-15", "FWD_OUTRIGHT"): 7.79,
        ("USDHKD", "2026-09-14", "SPOT"): pytest.approx(1 / 0.128205),
        ("USDBRL", "2026-09-24", "FWD_OUTRIGHT"): 5.15904,
        ("USDBRL", "2026-09-14", "SPOT"): pytest.approx(1 / 0.194189),
    }
    assert not any(math.isnan(r.value) for r in result.rows)


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
    conn.execute("INSERT INTO trades VALUES ('a1','BNP','AUDUSD','FX_FWD','a1','2026-08-10',-1e6,0.65,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trades VALUES ('j1','BNP','USDJPY','FX_FWD','j1','2026-08-10',1e6,150.0,"
                 "'acc','cp','HAHY7','t','d','')")
    conn.execute("INSERT INTO trades VALUES ('f1','BNP','ESU6 Index','FUTURE','f1','2026-08-10',6,7528.25,"
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
    assert by[("AUDUSD", "FWD_OUTRIGHT", "2026-09-16")] == "INTERP"
    assert by[("USDJPY", "FWD_OUTRIGHT", "2026-09-18")] == "MANUAL"
    assert by[("USDJPY", "SPOT", "2026-08-17")] == "MISSING"
    assert by[("ESU6 Index", "FUTURE_PX", "2026-09-18")] == "MISSING"
    assert set(df["mark_type"]) == {"SPOT", "FWD_OUTRIGHT", "FUTURE_PX"}


def test_close_completeness(tmp_path):
    from data.bloomberg import inventory
    conn = _inventory_db()
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-08-17", "AUDUSD", "2026-08-17", "SPOT", 0.66, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        ("2026-08-18", "AUDUSD", "2026-08-18", "SPOT", 0.67, "BBG_BFXFORWARD", "2026-08-18T15:00:00-04:00"),
        ("2026-08-18", "USDJPY", "2026-08-18", "SPOT", 150.5, "BBG_BFXFORWARD", "2026-08-18T15:00:00-04:00"),
    ])
    conn.commit()
    df = inventory.close_completeness(conn, "2026-08-17", "2026-08-18")
    rows = {r.as_of_date: r for r in df.itertuples()}
    assert rows["2026-08-17"].needed == 2 and rows["2026-08-17"].present == 1 and not rows["2026-08-17"].complete
    assert rows["2026-08-18"].present == 2 and rows["2026-08-18"].complete


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
    snap = src.get_curve_quotes("USD", date.today())
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
        src.get_curve_quotes("USD", date.today())


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
