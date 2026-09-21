"""data/bloomberg/snapshot.py: the marks snapshot that carries market data through git
from the Bloomberg PC to a PC with no Terminal. Pure sqlite."""
import sqlite3

import pytest

from data.bloomberg import snapshot
from data.ingest import schema

INSTRUMENT_COLS = ("instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                   "bbg_ticker, expiry_date")
SNAP = "2026-09-14T15:00:00-04:00"
# Floats with no short decimal form: the CSV must give back the identical bits.
AWKWARD = 0.1 + 0.2
TINY = 1e-9 / 3


def _instruments(conn, rows):
    conn.executemany(f"INSERT INTO instruments ({INSTRUMENT_COLS}) VALUES (?,?,?,?,?,?,?,?)", rows)


def _marks(conn, rows):
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", rows)


def _bloomberg_pc(path):
    conn = schema.connect(path)
    _instruments(conn, [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDKRW", "FX", "USD", "KRW", 1, 1, "USDKRW Curncy", "9999-12-31"),
        ("CASH-CAD", "CASH", "CAD", "CAD", 1, 0, "", "9999-12-31"),  # no mark: stays behind
    ])
    _marks(conn, [
        ("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.62, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", AWKWARD, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-30", "FWD_OUTRIGHT", 0.71, "BBG_INTERP", SNAP),
        ("2026-09-14", "USDKRW", "2026-09-14", "NDF_1M", 1391.5, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-30", "DELTA", TINY, "QL_OPTIONS_PRICER", SNAP),
    ])
    conn.execute("INSERT INTO curves VALUES ('USD-SOFR-OIS','2026-09-14','2027-09-14',?,0.04,'QL_PRICER')",
                 (AWKWARD / 3,))
    conn.execute("INSERT INTO curve_quotes VALUES ('2026-09-14','USD','SOFR','1Y','USOSFR1 Curncy',"
                 "4.05,'OIS','PX_LAST','BBG_BDP')")
    conn.execute("INSERT INTO index_fixings VALUES ('SOFR','2026-09-11',0.0405,'BBG_BDH')")
    conn.commit()
    return conn


def _dump(conn, table):
    return sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)


def test_round_trip_gives_the_other_pc_identical_market_data(tmp_path):
    pc = _bloomberg_pc(tmp_path / "pc.db")
    manifest = snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")
    assert manifest["rows"] == {"instruments": 2, "marks": 5, "curves": 1, "curve_quotes": 1,
                                "index_fixings": 1}
    assert (manifest["marks_from"], manifest["marks_through"]) == ("2026-09-09", "2026-09-14")

    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert out["rows"]["marks"] == 5 and out["instruments_added"] == 2 and out["skipped_marks"] == 0
    assert out["manifest"] == manifest

    mac = sqlite3.connect(tmp_path / "mac.db")
    for table in snapshot.MARKET_TABLES:
        assert _dump(mac, table) == _dump(pc, table)  # exact floats, sources and snap times
    assert [r[0] for r in mac.execute("SELECT instrument_id FROM instruments ORDER BY 1")] == ["AUDUSD", "USDKRW"]
    # is_ndf came back an integer, not the text '1'
    assert mac.execute("SELECT typeof(is_ndf), is_ndf FROM instruments WHERE instrument_id='USDKRW'").fetchone() == ("integer", 1)


def test_export_is_byte_stable_so_a_second_export_of_the_same_marks_commits_nothing(tmp_path):
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "a")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "b")
    for table in ("instruments",) + snapshot.MARKET_TABLES:
        data = (tmp_path / "a" / f"{table}.csv").read_bytes()
        assert data == (tmp_path / "b" / f"{table}.csv").read_bytes()
        assert b"\r" not in data  # '\n' on every OS
    assert not list((tmp_path / "a").glob("*.tmp"))

    # exporting over an unchanged snapshot keeps its manifest, export time included
    manifest = tmp_path / "a" / snapshot.MANIFEST
    manifest.write_text(manifest.read_text().replace('"exported_at": "2', '"exported_at": "1'))
    kept = manifest.read_bytes()
    assert snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "a")["exported_at"].startswith("1")
    assert manifest.read_bytes() == kept


def test_import_mirrors_the_bloomberg_pc_but_keeps_manual_marks_and_local_instruments(tmp_path):
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")

    mac = schema.connect(tmp_path / "mac.db")
    _instruments(mac, [("AUDUSD", "FX", "AUD", "USD", 1, 0, "LOCAL TICKER", "9999-12-31")])
    _marks(mac, [
        # a last live press the Bloomberg PC's backfill has since replaced under another source:
        # left on, this direct quote would beat the snapshot's BBG_INTERP row in marks_official
        ("2026-09-14", "AUDUSD", "2026-09-30", "FWD_OUTRIGHT", 0.99, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", 0.5, "MANUAL", SNAP),
    ])
    mac.commit()
    mac.close()

    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert out["dropped"]["marks"] == 1 and out["instruments_added"] == 1

    mac = sqlite3.connect(tmp_path / "mac.db")
    official = mac.execute("SELECT value, source FROM marks_official WHERE mark_type='FWD_OUTRIGHT'").fetchall()
    assert official == [(0.71, "BBG_INTERP")]
    assert mac.execute("SELECT value FROM marks WHERE source='MANUAL'").fetchall() == [(0.5,)]
    assert mac.execute("SELECT bbg_ticker FROM instruments WHERE instrument_id='AUDUSD'").fetchone() == ("LOCAL TICKER",)

    # importing the same snapshot again changes nothing
    before = _dump(mac, "marks")
    mac.close()
    snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert _dump(sqlite3.connect(tmp_path / "mac.db"), "marks") == before


def test_import_freezes_the_settled_trades_because_no_pull_runs_without_bloomberg(tmp_path):
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")

    mac = schema.connect(tmp_path / "mac.db")
    _instruments(mac, [("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31")])
    # sold 1m AUD @0.65 settling 09-10: frozen at the snapshot's 09-09 spot, 0.62
    mac.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "", "t", "d", ""))
    mac.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-10", 0.65, 1),
    ])
    mac.commit()
    mac.close()

    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert out["ledger"]["realised"] == 1
    row = sqlite3.connect(tmp_path / "mac.db").execute(
        "SELECT pnl_usd, spot_as_of_date FROM realised_pnl WHERE trade_id='a1'").fetchone()
    assert row == (pytest.approx(30000.0), "2026-09-09")


def test_nothing_to_export_and_nothing_to_import_say_so_and_touch_nothing(tmp_path):
    schema.connect(tmp_path / "empty.db").close()
    with pytest.raises(snapshot.SnapshotError, match="no marks on file"):
        snapshot.export_snapshot(tmp_path / "empty.db", tmp_path / "snap")
    assert not (tmp_path / "snap").exists()
    with pytest.raises(snapshot.SnapshotError, match="no database"):
        snapshot.export_snapshot(tmp_path / "absent.db", tmp_path / "snap")

    with pytest.raises(snapshot.SnapshotError, match="no snapshot"):
        snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap")
    assert not (tmp_path / "mac.db").exists()


def test_a_stored_value_that_is_not_a_number_travels_as_the_data_error_it_is(tmp_path):
    pc = _bloomberg_pc(tmp_path / "pc.db")
    _marks(pc, [("2026-09-14", "AUDUSD", "2026-10-30", "FWD_OUTRIGHT", "n/a", "BBG_BFXFORWARD", SNAP)])
    pc.commit()
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")
    snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    got = sqlite3.connect(tmp_path / "mac.db").execute(
        "SELECT typeof(value), value FROM marks WHERE settle_date='2026-10-30'").fetchone()
    assert got == ("text", "n/a")
