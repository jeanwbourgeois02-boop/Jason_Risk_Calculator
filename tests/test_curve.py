"""curve-positions: the book's commodity futures by commodity x contract month."""
import sqlite3

import pytest

from data.contracts import get_root
from data.ingest.schema import create_schema
from engine.curve import curve_positions
from engine.pnl.valuation import value_book

AS_OF = "2026-09-15"

CLZ6, CLF7 = "CLZ26 Comdty", "CLF27 Comdty"
CUZ6 = "CUZ26 Comdty"          # SHFE copper (CNY)
CORN = "C Z26 Comdty"          # CBOT corn (US cents per bushel)


def _db():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _instrument(conn, instrument_id, root_id, expiry, multiplier=None, ccy=None):
    root = get_root(root_id)
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (instrument_id, root_id, ccy or root.currency,
                  root.multiplier if multiplier is None else multiplier, instrument_id, expiry))


def _trade(conn, tid, instrument_id, contracts, fill, expiry, ccy="USD", multiplier=1.0, trade_date="2026-09-01"):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": instrument_id, "product": "FUTURE",
            "package_id": tid, "trade_date": trade_date, "quantity": contracts, "price": fill,
            "account": "A", "counterparty": "C", "strategy": "", "trader": "T", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, ccy, contracts * multiplier * fill, trade_date, expiry, fill))


def _future(conn, tid, instrument_id, root_id, expiry, contracts, fill, **kw):
    _instrument(conn, instrument_id, root_id, expiry, **kw)
    root = get_root(root_id)
    _trade(conn, tid, instrument_id, contracts, fill, expiry, ccy=root.currency, multiplier=root.multiplier)


def _px(conn, instrument_id, expiry, value, as_of=AS_OF, source="BBG_BDH"):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,?,'t')", (as_of, instrument_id, expiry, value, source))


def _spot(conn, pair, value, as_of=AS_OF):
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FX',?,?,1,0,?,'9999-12-31')",
                 (pair, pair[:3], pair[3:], pair + " Curncy"))
    conn.execute("INSERT INTO marks VALUES (?,?,?,'SPOT',?,'BBG_BFXFORWARD','t')", (as_of, pair, as_of, value))


def _row(out, contract_id):
    hits = [r for r in out["rows"] if r["contract_id"] == contract_id]
    assert len(hits) == 1, out["rows"]
    return hits[0]


def _wti_spread():
    conn = _db()
    _future(conn, "W1", CLZ6, "NYMEX:CL", "2026-11-30", 1, 70.0)
    _future(conn, "W2", CLF7, "NYMEX:CL", "2026-12-31", -1, 69.5)
    _px(conn, CLZ6, "2026-11-30", 71.0)
    _px(conn, CLF7, "2026-12-31", 70.2)
    return conn


def test_wti_calendar_spread_offsets_by_month():
    out = curve_positions(_wti_spread(), AS_OF)
    z, f = _row(out, CLZ6), _row(out, CLF7)
    assert (z["month"], z["year"], z["month_code"], z["lots"]) == (12, 2026, "Z", 1.0)
    assert (f["month"], f["year"], f["month_code"], f["lots"]) == (1, 2027, "F", -1.0)
    assert z["units"] == 1000.0 and z["unit"] == "bbl" and f["units"] == -1000.0
    assert z["notional_local"] == 1 * 1000 * 71.0 and z["notional_usd"] == 71000.0
    assert f["notional_usd"] == -70200.0
    assert z["usd_per_unit"] == 1.0 and z["price_source"] == "BBG_BDH" and z["reason"] == ""
    assert z["dates_source"] == "ESTIMATED"
    cl = out["by_commodity"]["NYMEX:CL"]
    assert cl["net_lots"] == 0.0 and cl["gross_lots"] == 2.0 and cl["net_units"] == 0.0
    assert cl["months"] == {"2026-12": 1.0, "2027-01": -1.0}
    assert cl["net_usd"] == pytest.approx(800.0) and cl["gross_usd"] == pytest.approx(141200.0)
    assert out["months"] == ["2026-12", "2027-01"]
    assert out["available"] and out["reasons"] == [] and out["currency_exposure"] == {}


def _copper(spot=True):
    conn = _db()
    _future(conn, "CU1", CUZ6, "SHFE:CU", "2026-12-15", 3, 80000.0)
    _px(conn, CUZ6, "2026-12-15", 80500.0)
    if spot:
        _spot(conn, "USDCNY", 7.1)
    return conn


def test_cny_copper_converts_at_exact_usdcny():
    r = _row(curve_positions(_copper(), AS_OF), CUZ6)
    assert r["currency"] == "CNY" and r["units"] == 15.0 and r["unit"] == "t"
    assert r["notional_local"] == 3 * 5 * 80500.0
    assert r["usd_per_unit"] == pytest.approx(1 / 7.1) and r["usd_source"].startswith("USDCNY")
    assert r["notional_usd"] == pytest.approx(3 * 5 * 80500.0 / 7.1)


def test_cny_copper_without_usdcny_keeps_lots_and_names_the_gap():
    conn = _copper(spot=False)
    _spot(conn, "USDCNY", 7.1, as_of="2026-09-14")   # another day's spot is never used
    out = curve_positions(conn, AS_OF)
    r = _row(out, CUZ6)
    assert r["lots"] == 3.0 and r["units"] == 15.0 and r["notional_local"] == 3 * 5 * 80500.0
    assert r["notional_usd"] is None and r["usd_per_unit"] is None
    assert "no SPOT for CNY on 2026-09-15" in r["reason"]
    cu = out["by_commodity"]["SHFE:CU"]
    assert cu["net_lots"] == 3.0 and cu["net_usd"] is None and cu["gross_usd"] is None
    assert cu["missing"] == [CUZ6] and "no SPOT for CNY" in cu["reason"]
    assert any("no SPOT for CNY" in x for x in out["reasons"])


def test_contract_with_no_price_shows_lots_and_units_only():
    conn = _wti_spread()
    conn.execute("DELETE FROM marks WHERE instrument_id = ?", (CLF7,))
    _px(conn, CLF7, "2026-12-31", 70.2, source="MANUAL")      # not official: never read
    _px(conn, CLF7, "2026-12-31", 70.1, as_of="2026-09-14")   # another day: never estimated
    out = curve_positions(conn, AS_OF)
    f = _row(out, CLF7)
    assert f["lots"] == -1.0 and f["units"] == -1000.0
    assert f["price"] is None and f["notional_local"] is None and f["notional_usd"] is None
    assert f["reason"] == f"no FUTURE_PX for {CLF7} (expiry 2026-12-31) on {AS_OF}"
    cl = out["by_commodity"]["NYMEX:CL"]
    assert cl["net_usd"] is None and cl["missing"] == [CLF7] and cl["net_lots"] == 0.0
    assert out["by_sector"]["energy"]["net_usd"] is None


def test_corn_units_in_bushels_and_notional_at_50_usd_per_cent():
    conn = _db()
    _future(conn, "C1", CORN, "CBOT:ZC", "2026-12-14", 2, 440.0)
    _px(conn, CORN, "2026-12-14", 450.25)
    r = _row(curve_positions(conn, AS_OF), CORN)
    assert r["unit"] == "bu" and r["units"] == 10000.0 and r["multiplier"] == 50.0
    assert r["notional_usd"] == pytest.approx(2 * 50 * 450.25)


def test_multiplier_on_file_that_disagrees_leaves_usd_blank():
    conn = _db()
    _future(conn, "C1", CORN, "CBOT:ZC", "2026-12-14", 2, 440.0, multiplier=5000.0)
    _px(conn, CORN, "2026-12-14", 450.25)
    r = _row(curve_positions(conn, AS_OF), CORN)
    assert r["lots"] == 2.0 and r["notional_usd"] is None and "multiplier on file" in r["reason"]


def test_round_trip_is_flat_and_left_out_of_rows():
    conn = _wti_spread()
    _trade(conn, "W3", CLZ6, -1, 70.8, "2026-11-30", multiplier=1000.0)
    out = curve_positions(conn, AS_OF)
    assert [r["contract_id"] for r in out["rows"]] == [CLF7]
    assert out["flat_contracts"] == [{"root_id": "NYMEX:CL", "contract_id": CLZ6, "expiry": "2026-11-30",
                                      "trade_ids": ["W1", "W3"]}]
    assert out["by_commodity"]["NYMEX:CL"]["months"] == {"2027-01": -1.0}
    assert out["months"] == ["2027-01"]


def test_sector_totals():
    conn = _wti_spread()
    _future(conn, "CU1", CUZ6, "SHFE:CU", "2026-12-15", 3, 80000.0)
    _px(conn, CUZ6, "2026-12-15", 80500.0)
    _spot(conn, "USDCNY", 7.1)
    _future(conn, "C1", CORN, "CBOT:ZC", "2026-12-14", -2, 440.0)
    _px(conn, CORN, "2026-12-14", 450.25)
    out = curve_positions(conn, AS_OF)
    assert set(out["by_sector"]) == {"energy", "metals", "agriculture"}
    energy = out["by_sector"]["energy"]
    assert energy["net_usd"] == pytest.approx(800.0) and energy["gross_usd"] == pytest.approx(141200.0)
    assert energy["commodities"] == ["NYMEX:CL"] and energy["missing"] == []
    metals = out["by_sector"]["metals"]
    assert metals["net_usd"] == pytest.approx(3 * 5 * 80500.0 / 7.1) == metals["gross_usd"]
    ag = out["by_sector"]["agriculture"]
    assert ag["net_usd"] == pytest.approx(-2 * 50 * 450.25) and ag["gross_usd"] == pytest.approx(2 * 50 * 450.25)
    assert [r["sector"] for r in out["rows"]] == ["agriculture", "energy", "energy", "metals"]


def test_currency_exposure_of_a_cny_future_is_its_value_book_pnl_local():
    conn = _copper()
    out = curve_positions(conn, AS_OF)
    book = value_book(conn, AS_OF)
    expected = float(book.loc[book["trade_id"] == "CU1", "pnl_local"].iloc[0])
    assert expected == 3 * 5 * (80500.0 - 80000.0)
    cny = out["currency_exposure"]["CNY"]
    assert cny["pnl_local"] == expected and cny["contracts"] == [CUZ6]
    assert cny["pnl_usd"] == pytest.approx(expected / 7.1) and cny["missing"] == []


def test_es_future_is_ignored():
    conn = _db()
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('ESZ6 Index','FUTURE','ES','USD',50,0,'ESZ6 Index','2026-12-18')")
    _trade(conn, "ES1", "ESZ6 Index", 4, 7500.0, "2026-12-18", multiplier=50.0)
    _px(conn, "ESZ6 Index", "2026-12-18", 7600.0)
    out = curve_positions(conn, AS_OF)
    assert out["available"] and out["rows"] == [] and out["by_commodity"] == {}
    assert out["note"] == f"no open commodity futures on {AS_OF}" and out["reasons"] == []


def test_expired_and_not_yet_traded_contracts_are_not_open():
    conn = _db()
    _future(conn, "W1", CLZ6, "NYMEX:CL", "2026-09-15", 1, 70.0)     # expires on as_of: not open
    _instrument(conn, CLF7, "NYMEX:CL", "2026-12-31")
    _trade(conn, "W2", CLF7, 1, 70.0, "2026-12-31", multiplier=1000.0, trade_date="2026-09-16")
    out = curve_positions(conn, AS_OF)
    assert out["rows"] == [] and out["flat_contracts"] == []
