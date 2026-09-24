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
                                            "expiry": "2026-09-18", "source": "BBG_BDH", "currency": "USD",
                                            "usd_per_unit": 1.0, "reason": ""}


def test_missing_mark_is_nan_and_named():
    out = futures_usd_delta(_db(), AS_OF)
    assert math.isnan(out["value"]) and out["missing"] == ["ESU6 Index"]
    assert out["reason"] == f"no FUTURE_PX on {AS_OF} for ESU6 Index"
    assert out["details"]["ESU6 Index"]["price"] is None
    assert out["details"]["ESU6 Index"]["reason"] == f"no FUTURE_PX on {AS_OF}"


def test_expired_future_excluded():
    out = futures_usd_delta(_db(), "2026-09-18")
    assert out["value"] == 0.0 and out["by_instrument"] == {}


# --- non-USD futures (user decision 2026-09-24: convert at spot of the valuation date) ------


def _add_future(conn, instrument_id, ccy, multiplier, expiry, contracts, fill):
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (instrument_id, instrument_id[:2], ccy, multiplier, instrument_id, expiry))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    tid = "T-" + instrument_id
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": instrument_id, "product": "FUTURE",
            "package_id": tid, "trade_date": "2026-09-01", "quantity": contracts, "price": fill,
            "account": "A", "counterparty": "C", "strategy": "", "trader": "T", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,'2026-09-01',?,?,0)",
                 (tid, ccy, contracts * multiplier * fill, expiry, fill))


def _spot(conn, pair, value, as_of=AS_OF):
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FX',?,?,1,0,?,'9999-12-31')",
                 (pair, pair[:3], pair[3:], pair + " Curncy"))
    conn.execute("INSERT INTO marks VALUES (?,?,?,'SPOT',?,'BBG_BFXFORWARD','t')", (as_of, pair, as_of, value))


def _cny_book():
    conn = _db()
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (AS_OF, "ESU6 Index", "2026-09-18", "FUTURE_PX", 7600.0, "BBG_BDH", "t"))
    _add_future(conn, "CUZ6 Comdty", "CNY", 5.0, "2026-12-15", 3.0, 80000.0)
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (AS_OF, "CUZ6 Comdty", "2026-12-15", "FUTURE_PX", 81000.0, "BBG_BDH", "t"))
    return conn


def test_cny_future_converts_at_the_usdcny_spot_of_the_day():
    conn = _cny_book()
    _spot(conn, "USDCNY", 7.10)
    out = futures_usd_delta(conn, AS_OF)
    cny_usd = 3.0 * 5.0 * 81000.0 * (1.0 / 7.10)
    assert out["by_instrument"]["CUZ6 Comdty"] == cny_usd
    assert out["by_instrument"]["ESU6 Index"] == 4 * 50 * 7600.0          # the USD future unchanged
    assert out["value"] == 4 * 50 * 7600.0 + cny_usd and out["missing"] == [] and out["reason"] == ""
    d = out["details"]["CUZ6 Comdty"]
    assert (d["currency"], d["usd_per_unit"], d["price"], d["reason"]) == ("CNY", 1.0 / 7.10, 81000.0, "")


def test_eur_future_converts_at_the_eurusd_spot_when_there_is_no_usdeur():
    conn = _db()
    _add_future(conn, "TGZ6 Comdty", "EUR", 10.0, "2026-12-15", -2.0, 35.0)
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (AS_OF, "TGZ6 Comdty", "2026-12-15", "FUTURE_PX", 36.0, "BBG_BDH", "t"))
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (AS_OF, "ESU6 Index", "2026-09-18", "FUTURE_PX", 7600.0, "BBG_BDH", "t"))
    _spot(conn, "EURUSD", 1.10)
    out = futures_usd_delta(conn, AS_OF)
    assert out["by_instrument"]["TGZ6 Comdty"] == -2.0 * 10.0 * 36.0 * 1.10
    assert out["details"]["TGZ6 Comdty"]["usd_per_unit"] == 1.10


def test_cny_future_with_no_usdcny_spot_is_missing_named_and_the_total_nan():
    conn = _cny_book()
    _spot(conn, "USDCNY", 7.05, as_of="2026-09-14")     # yesterday's spot: never carried into the delta
    out = futures_usd_delta(conn, AS_OF)
    assert math.isnan(out["value"]) and out["missing"] == ["CUZ6 Comdty"]
    assert out["reason"] == f"no SPOT for CNY on {AS_OF} (CUZ6 Comdty)"
    assert out["by_instrument"] == {"ESU6 Index": 4 * 50 * 7600.0}         # the USD future still counted
    d = out["details"]["CUZ6 Comdty"]
    assert d["currency"] == "CNY" and d["usd_per_unit"] is None and d["price"] == 81000.0
    assert d["reason"] == f"no SPOT for CNY on {AS_OF}"


def test_no_price_and_no_spot_are_both_named():
    conn = _cny_book()
    conn.execute("DELETE FROM marks WHERE instrument_id = 'ESU6 Index'")
    out = futures_usd_delta(conn, AS_OF)
    assert sorted(out["missing"]) == ["CUZ6 Comdty", "ESU6 Index"]
    assert out["reason"] == f"no FUTURE_PX on {AS_OF} for ESU6 Index; no SPOT for CNY on {AS_OF} (CUZ6 Comdty)"
