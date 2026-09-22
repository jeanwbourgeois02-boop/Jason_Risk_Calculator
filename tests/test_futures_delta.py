import math
import sqlite3

from data.ingest.schema import create_schema
from engine.ladder.futures_delta import futures_usd_delta

AS_OF = "2026-09-15"


def _db():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.execute("INSERT INTO instruments VALUES ('ESU6 Index','FUTURE','ES','USD',50,0,'ESU6 Index','2026-09-18')")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    for tid, qty in [("F1", 6), ("F2", -2)]:
        vals = {"trade_id": tid, "source": "XLSX", "instrument_id": "ESU6 Index", "product": "FUTURE",
                "package_id": tid, "trade_date": "2026-09-01", "quantity": qty, "price": 7500.0,
                "account": "A", "counterparty": "C", "strategy": "S", "trader": "T", "description": "d", "theme": ""}
        conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [vals[c] for c in cols])
        conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL','USD',?,'2026-09-01','2026-09-18',7500.0,0)",
                     (tid, qty * 50 * 7500.0))
    return conn


def test_futures_delta_sums_contracts_times_multiplier_times_settlement():
    conn = _db()
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (AS_OF, "ESU6 Index", "2026-09-18", "FUTURE_PX", 7600.0, "BBG_BDH", "t"))
    out = futures_usd_delta(conn, AS_OF)
    assert out["value"] == 4 * 50 * 7600.0
    assert out["reason"] == ""
    assert out["details"]["ESU6 Index"] == {"contracts": 4.0, "multiplier": 50.0, "price": 7600.0,
                                            "expiry": "2026-09-18", "source": "BBG_BDH"}


def test_missing_mark_is_nan_and_named():
    out = futures_usd_delta(_db(), AS_OF)
    assert math.isnan(out["value"]) and out["missing"] == ["ESU6 Index"]


def test_expired_future_excluded():
    out = futures_usd_delta(_db(), "2026-09-18")
    assert out["value"] == 0.0 and out["by_instrument"] == {}
