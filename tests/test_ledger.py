"""engine/pnl/ledger.py: realise on settlement, snapshots, period P&L. Pure sqlite."""
import math

import pytest

from data.ingest import schema
from engine.pnl import ledger


def rate(v, inverted=False):
    return {"rate": v, "inverted": inverted, "source": "T", "timestamp": "t", "stale": False}


def _db():
    conn = schema.connect()
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31")])
    # a1: sold 1m AUD @0.65 settling 09-10 (settles before as_of 09-14); a2: open, settles 09-30
    # j1: bought 150m JPY for 1m USD settling 09-12 (settled); no spot for JPY on/before -> unrealisable
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("a1", "BNP", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "HAHY7", "t", "d"),
        ("a2", "BNP", "AUDUSD", "FX_FWD", "a2", "2026-09-14", 2e6, 0.70, "acc", "cp", "HAHY7", "t", "d"),
        ("j1", "BNP", "USDJPY", "FX_FWD", "j1", "2026-08-10", -1e6, 150.0, "acc", "cp", "HAHY7", "t", "d")])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a2", 1, "FX_NEAR", "AUD", 2e6, "2026-09-14", "2026-09-30", 0.70, 1),
        ("a2", 2, "FX_NEAR", "USD", -1400000, "2026-09-14", "2026-09-30", 0.70, 1),
        ("j1", 1, "FX_NEAR", "USD", -1e6, "2026-08-10", "2026-09-12", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", 150e6, "2026-08-10", "2026-09-12", 150.0, 1)])
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.62, "BBG_BFXFORWARD", "2026-09-09T15:00:00+00:00"),  # last before 09-10
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", 0.71, "BBG_BFXFORWARD", "2026-09-14T15:00:00+00:00")])
    conn.commit()
    return conn


def test_realise_settled_freezes_once_with_prior_spot_and_reports_unrealisable():
    conn = _db()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["realised"] == 1 and [u["trade_id"] for u in res["unrealisable"]] == ["j1"]
    rows = ledger.realised_rows(conn, "2026-09-14")
    a1 = rows.iloc[0]
    assert a1["trade_id"] == "a1" and a1["spot_usd_per_local"] == 0.62 and a1["spot_as_of_date"] == "2026-09-09"
    assert a1["pnl_usd"] == pytest.approx(-1e6 * 0.62 - (-650000)) == pytest.approx(30000)   # sold AUD, AUD fell
    assert "last before settlement" in a1["note"]
    # idempotent: second call realises nothing new and never re-prices a1 at the newer 0.71 spot
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 0
    assert ledger.realised_rows(conn, "2026-09-14").iloc[0]["spot_usd_per_local"] == 0.62
    assert ledger.realised_rows(conn, "2026-09-10").empty                                   # earlier as_of excludes it


def test_snapshot_and_ledger_summary_with_periods():
    conn = _db()
    rates = {"AUD": rate(0.71)}
    # seed a complete snapshot for the previous business day (Fri 2026-09-11) and month-end (Mon 2026-08-31)
    conn.executemany("INSERT INTO pnl_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("2026-09-11", "x", 0.0, 10000.0, 10000.0, 1.0, 1.0, 0.0, 2, 0, 1, ""),
        ("2026-08-31", "x", 0.0, -5000.0, -5000.0, 1.0, 1.0, 0.0, 2, 0, 1, ""),
        ("2026-09-04", "x", 0.0, 1000.0, 1000.0, 1.0, 1.0, 0.0, 2, 0, 0, "JPY")])   # incomplete 5d reference
    conn.commit()
    led = ledger.take_snapshot(conn, "2026-09-14", rates)
    # j1 is settled but unrealisable -> snapshot stored but incomplete
    assert led["complete"] is False and [u["trade_id"] for u in led["unrealisable"]] == ["j1"]
    assert led["realised_ltd_usd"] == pytest.approx(30000)
    assert led["unrealised_usd"] == pytest.approx(2e6 * 0.71 - 1400000) == pytest.approx(20000)   # a2 open
    assert led["trading_usd"] == pytest.approx(20000)                                              # a2 dated today
    snap = ledger.snapshots(conn).set_index("as_of_date").loc["2026-09-14"]
    assert snap["complete"] == 0 and snap["missing"] == "j1" and snap["total_ltd_usd"] == pytest.approx(50000)
    summary = ledger.ledger_summary(conn, "2026-09-14", rates)
    p = summary["periods"]
    assert p["daily"]["available"] and p["daily"]["value"] == pytest.approx(50000 - 10000)
    assert p["mtd"]["available"] and p["mtd"]["value"] == pytest.approx(50000 + 5000)
    assert not p["d5"]["available"] and "incomplete" in p["d5"]["reason"]
    assert not p["ytd"]["available"] and "no snapshot" in p["ytd"]["reason"]
    assert summary["last_snapshot"]["as_of_date"] == "2026-09-14" and summary["snapshot_count"] == 4


def test_missing_rate_makes_totals_and_periods_unavailable():
    conn = _db()
    led = ledger.take_snapshot(conn, "2026-09-14", {})          # no AUD rate
    assert led["missing"] == ["AUD"] and math.isnan(led["unrealised_usd"]) and math.isnan(led["total_ltd_usd"])
    summary = ledger.ledger_summary(conn, "2026-09-14", {})
    assert all(not v["available"] and "unavailable" in v["reason"] for v in summary["periods"].values())
    assert ledger.snapshots(conn).iloc[0]["complete"] == 0
