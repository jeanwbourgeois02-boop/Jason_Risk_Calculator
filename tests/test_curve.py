"""curve-positions: the book's commodity futures by commodity x contract month."""
import sqlite3

import pytest

from data.contracts import get_root, option_for
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
    assert out["flat_contracts"] == [{"product": "FUTURE", "root_id": "NYMEX:CL", "contract_id": CLZ6,
                                      "expiry": "2026-11-30",
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


# --------------------------------------------------------------------------- Phase 5: delta
# Options on futures (CMDTY_OPTION), LME forwards (LME_FWD) and the declining delta of
# monthly-average contracts, in the table layout the housekeeper fixed for Phase 5.

CL_CALL = "CLZ26C 70 Comdty"      # option on CLZ26, expiring 2026-11-17 in these tests
TIOX6 = "TIOX26 Comdty"           # CME iron ore, averaging over November 2026 (US calendar)
OLD_COMMODITY_KEYS = {"name", "sector", "subsector", "exchange", "currency", "net_lots", "gross_lots", "net_units",
                      "unit", "net_usd", "gross_usd", "months", "missing", "reason"}
OLD_SECTOR_KEYS = {"net_usd", "gross_usd", "missing", "reason", "commodities"}


def _book(conn, tid, instrument_id, product, qty, fill, legs, trade_date="2026-09-01"):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": instrument_id, "product": product,
            "package_id": tid, "trade_date": trade_date, "quantity": qty, "price": fill,
            "account": "A", "counterparty": "C", "strategy": "", "trader": "T", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    for n, (leg_type, ccy, amount, settle, settles_cash) in enumerate(legs, start=1):
        conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                     (tid, n, leg_type, ccy, amount, trade_date, settle, fill, settles_cash))


def _option(conn, tid, lots, fill=2.5, expiry="2026-11-17", instrument_id=CL_CALL):
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'CMDTY_OPTION','NYMEX:CL','USD',1000,0,?,?)",
                 (instrument_id, "CLZ6C 70 Comdty", expiry))
    conn.execute("INSERT OR IGNORE INTO instrument_options (instrument_id, strike, option_type, payoff) "
                 "VALUES (?,70,'CALL','AMERICAN')", (instrument_id,))
    _book(conn, tid, instrument_id, "CMDTY_OPTION", lots, fill, [("NOTIONAL", "USD", lots * 1000 * fill, expiry, 0)])


def _delta_mark(conn, value, instrument_id=CL_CALL, expiry="2026-11-17", source="QL_OPTIONS_PRICER"):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'DELTA',?,?,'t')", (AS_OF, instrument_id, expiry, value, source))


def _lme(conn, tid, tonnes, fill, prompt="2026-12-16", root_id="LME:CA"):
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'LME_FWD',?,'USD',1,0,'','9999-12-31')",
                 (root_id, root_id))
    _book(conn, tid, root_id, "LME_FWD", tonnes, fill,
          [("FX_NEAR", root_id, tonnes, prompt, 0), ("FX_NEAR", "USD", -tonnes * fill, prompt, 1)])


def _outright(conn, value, prompt="2026-12-16", root_id="LME:CA", as_of=AS_OF, source="BBG_BFXFORWARD"):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FWD_OUTRIGHT',?,?,'t')", (as_of, root_id, prompt, value, source))


def test_plain_future_has_delta_factor_one_and_delta_equal_to_notional():
    out = curve_positions(_wti_spread(), AS_OF)
    for r in out["rows"]:
        assert r["product"] == "FUTURE" and r["delta_factor"] == 1.0 and r["note"] == ""
        assert r["delta_lots"] == r["lots"] and r["delta_units"] == r["units"]
        assert r["delta_usd"] == r["notional_usd"] and r["delta_local"] == r["notional_local"]
        assert r["underlying_id"] == r["contract_id"] == r["instrument_id"]
    cl = out["by_commodity"]["NYMEX:CL"]
    assert cl["net_delta_lots"] == 0.0 and cl["net_delta_usd"] == pytest.approx(800.0)
    assert cl["gross_delta_usd"] == pytest.approx(141200.0) and cl["delta_missing"] == []
    assert cl["delta_months"] == cl["months"] == {"2026-12": 1.0, "2027-01": -1.0}
    assert cl["products"] == ["FUTURE"] and out["products_present"] == ["FUTURE"]
    energy = out["by_sector"]["energy"]
    assert energy["net_delta_usd"] == pytest.approx(800.0) and energy["gross_delta_usd"] == pytest.approx(141200.0)


def test_futures_only_book_keeps_every_existing_key_and_value():
    out = curve_positions(_wti_spread(), AS_OF)
    cl = out["by_commodity"]["NYMEX:CL"]
    assert OLD_COMMODITY_KEYS <= set(cl)
    assert (cl["net_lots"], cl["gross_lots"], cl["net_units"], cl["unit"]) == (0.0, 2.0, 0.0, "bbl")
    assert cl["net_usd"] == pytest.approx(800.0) and cl["gross_usd"] == pytest.approx(141200.0)
    assert cl["months"] == {"2026-12": 1.0, "2027-01": -1.0} and cl["missing"] == [] and cl["reason"] == ""
    energy = out["by_sector"]["energy"]
    assert OLD_SECTOR_KEYS <= set(energy) and energy["commodities"] == ["NYMEX:CL"]
    assert energy["net_usd"] == pytest.approx(800.0) and energy["gross_usd"] == pytest.approx(141200.0)
    assert out["months"] == ["2026-12", "2027-01"] and out["note"] == "" and out["reasons"] == []


def _iron_ore(as_of, expiry="2026-12-04"):
    conn = _db()
    _future(conn, "T1", TIOX6, "CME:TIO", expiry, 4, 100.0)
    _px(conn, TIOX6, expiry, 100.0, as_of=as_of)
    return conn


@pytest.mark.parametrize("as_of, factor, words", [
    ("2026-10-30", 1.0, "has not started"),                 # before November
    ("2026-11-13", 0.5, "10 of the 20 business days"),       # 16-20, 23-25, 27, 30 left; 26 Nov a holiday
    ("2026-11-30", 0.0, "is over"),                          # the last averaging day: price set
    ("2026-12-01", 0.0, "is over"),
])
def test_averaging_contract_delta_declines_through_its_period(as_of, factor, words):
    r = _row(curve_positions(_iron_ore(as_of), as_of), TIOX6)
    assert r["delta_factor"] == pytest.approx(factor) and words in r["note"]
    assert "2026-11-02..2026-11-30, 20 business days on the US calendar" in r["note"]
    assert r["lots"] == 4.0 and r["units"] == 2000.0 and r["unit"] == "t"          # the whole position
    assert r["notional_usd"] == pytest.approx(4 * 500 * 100.0)
    assert r["delta_lots"] == pytest.approx(4 * factor) and r["delta_units"] == pytest.approx(2000 * factor)
    assert r["delta_usd"] == pytest.approx(4 * factor * 500 * 100.0)
    assert r["reason"] == ""


def test_averaging_contract_aggregates_keep_full_lots_and_shrink_delta():
    out = curve_positions(_iron_ore("2026-11-13"), "2026-11-13")
    tio = out["by_commodity"]["CME:TIO"]
    assert tio["net_lots"] == 4.0 and tio["net_usd"] == pytest.approx(200000.0)
    assert tio["net_delta_lots"] == pytest.approx(2.0) and tio["net_delta_usd"] == pytest.approx(100000.0)
    assert tio["months"] == {"2026-11": 4.0} and tio["delta_months"] == {"2026-11": pytest.approx(2.0)}


def test_call_option_on_crude_takes_its_delta_mark_and_the_underlying_price():
    conn = _db()
    _option(conn, "O1", 10)
    _delta_mark(conn, 0.4)
    # the underlying CLZ26 is on file (no trade in it) at Bloomberg's date: its price is keyed there
    assert option_for("NYMEX:CL", CL_CALL).underlying.contract_id == CLZ6
    _instrument(conn, CLZ6, "NYMEX:CL", "2026-11-19")
    _px(conn, CLZ6, "2026-11-19", 71.0)
    _px(conn, CLZ6, "2026-12-31", 99.0)      # contract-master's estimate is not the key once the future is on file
    out = curve_positions(conn, AS_OF)
    r = _row(out, CL_CALL)
    assert r["product"] == "CMDTY_OPTION" and r["underlying_id"] == CLZ6 and r["expiry"] == "2026-11-17"
    assert (r["month"], r["year"], r["month_code"]) == (12, 2026, "Z")
    assert r["lots"] == 10.0 and r["units"] == 10000.0
    assert r["notional_local"] is None and r["notional_usd"] is None
    assert r["price"] == 71.0 and r["delta_factor"] == 0.4
    assert r["delta_lots"] == pytest.approx(4.0) and r["delta_units"] == pytest.approx(4000.0)
    assert r["delta_usd"] == pytest.approx(4 * 1000 * 71.0) and r["reason"] == ""
    assert "no notional" in r["note"]
    cl = out["by_commodity"]["NYMEX:CL"]
    assert cl["net_lots"] == 0.0 and cl["gross_lots"] == 0.0 and cl["months"] == {}   # not lots of the future
    assert cl["net_usd"] == 0.0 and cl["missing"] == []
    assert cl["net_delta_lots"] == pytest.approx(4.0) and cl["net_delta_usd"] == pytest.approx(284000.0)
    assert cl["delta_months"] == {"2026-12": pytest.approx(4.0)} and cl["products"] == ["CMDTY_OPTION"]
    assert out["months"] == ["2026-12"] and out["products_present"] == ["CMDTY_OPTION"]


def test_call_option_without_a_delta_mark_keeps_its_lots_and_names_the_gap():
    conn = _db()
    _option(conn, "O1", 10)
    _delta_mark(conn, 0.5, source="MANUAL")      # not official: never read
    _future(conn, "W1", CLZ6, "NYMEX:CL", "2026-11-30", 1, 70.0)
    _px(conn, CLZ6, "2026-11-30", 71.0)            # keyed on the future instrument's own expiry
    out = curve_positions(conn, AS_OF)
    r = _row(out, CL_CALL)
    assert r["lots"] == 10.0 and r["price"] == 71.0
    assert r["delta_factor"] is None and r["delta_lots"] is None and r["delta_usd"] is None
    assert r["reason"] == "no DELTA mark: the option has not been priced"
    cl = out["by_commodity"]["NYMEX:CL"]
    assert cl["net_lots"] == 1.0 and cl["net_usd"] == pytest.approx(71000.0)       # the future alone
    assert cl["net_delta_lots"] is None and cl["net_delta_usd"] is None and cl["gross_delta_usd"] is None
    assert cl["delta_missing"] == [CL_CALL] and "no DELTA mark" in cl["delta_reason"]
    assert cl["delta_months"] == {"2026-12": None}
    assert any("no DELTA mark" in x for x in out["reasons"])


def test_lme_forward_is_lots_of_its_prompt_month_at_the_exact_outright():
    conn = _db()
    _lme(conn, "L1", 50, 9800.0)
    _outright(conn, 9900.0)
    out = curve_positions(conn, AS_OF)
    r = _row(out, "LME:CA 2026-12-16")
    assert r["product"] == "LME_FWD" and r["instrument_id"] == "LME:CA" and r["expiry"] == "2026-12-16"
    assert (r["month"], r["year"], r["dates_source"]) == (12, 2026, "PROMPT")
    assert r["lots"] == 2.0 and r["units"] == 50.0 and r["unit"] == "t"
    assert r["price"] == 9900.0 and r["price_source"] == "BBG_BFXFORWARD" and r["usd_per_unit"] == 1.0
    assert r["notional_usd"] == pytest.approx(50 * 9900.0) and r["delta_factor"] == 1.0
    assert r["delta_usd"] == pytest.approx(50 * 9900.0) and r["reason"] == ""
    ca = out["by_commodity"]["LME:CA"]
    assert ca["net_lots"] == 2.0 and ca["months"] == {"2026-12": 2.0} and ca["net_usd"] == pytest.approx(495000.0)
    assert out["products_present"] == ["LME_FWD"]


def test_lme_forward_without_its_prompt_outright_keeps_lots_and_names_the_gap():
    conn = _db()
    _lme(conn, "L1", -75, 9800.0)
    _outright(conn, 9900.0, as_of="2026-09-14")         # another day: never estimated
    _outright(conn, 9950.0, prompt="2027-01-20")        # another prompt: never interpolated
    _lme(conn, "L2", 25, 9700.0, prompt="2026-09-15")   # prompt on as_of: settled, not open
    out = curve_positions(conn, AS_OF)
    assert [r["contract_id"] for r in out["rows"]] == ["LME:CA 2026-12-16"]
    r = out["rows"][0]
    assert r["lots"] == -3.0 and r["units"] == -75.0
    assert r["notional_usd"] is None and r["delta_usd"] is None and r["delta_lots"] == -3.0
    assert r["reason"] == f"no FWD_OUTRIGHT for LME:CA (prompt 2026-12-16) on {AS_OF}"
    assert out["by_commodity"]["LME:CA"]["net_usd"] is None


def test_aggregates_over_futures_options_and_lme_forwards():
    conn = _wti_spread()
    _option(conn, "O1", 10)
    _delta_mark(conn, 0.4)
    _lme(conn, "L1", 50, 9800.0)
    _outright(conn, 9900.0)
    out = curve_positions(conn, AS_OF)
    assert out["products_present"] == ["FUTURE", "LME_FWD", "CMDTY_OPTION"]
    assert out["months"] == ["2026-12", "2027-01"] and out["reasons"] == []
    cl = out["by_commodity"]["NYMEX:CL"]
    assert (cl["net_lots"], cl["gross_lots"]) == (0.0, 2.0)                       # futures only
    assert cl["net_usd"] == pytest.approx(800.0) and cl["gross_usd"] == pytest.approx(141200.0)
    assert cl["months"] == {"2026-12": 1.0, "2027-01": -1.0}
    assert cl["net_delta_lots"] == pytest.approx(4.0)                              # 1 - 1 + 10 x 0.4
    assert cl["net_delta_usd"] == pytest.approx(71000.0 - 70200.0 + 284000.0)
    assert cl["gross_delta_usd"] == pytest.approx(71000.0 + 70200.0 + 284000.0)
    assert cl["delta_months"] == {"2026-12": pytest.approx(5.0), "2027-01": -1.0}
    assert cl["products"] == ["FUTURE", "CMDTY_OPTION"]
    energy, metals = out["by_sector"]["energy"], out["by_sector"]["metals"]
    assert energy["net_usd"] == pytest.approx(800.0) and energy["net_delta_usd"] == pytest.approx(284800.0)
    assert energy["net_delta_lots"] == pytest.approx(4.0)
    assert metals["commodities"] == ["LME:CA"] and metals["net_usd"] == pytest.approx(495000.0)
    assert metals["net_delta_usd"] == pytest.approx(495000.0) and metals["net_delta_lots"] == 2.0
    kinds = [(r["contract_id"], r["product"]) for r in out["rows"]]
    assert kinds == [(CL_CALL, "CMDTY_OPTION"), (CLZ6, "FUTURE"), (CLF7, "FUTURE"),
                     ("LME:CA 2026-12-16", "LME_FWD")]


def test_flat_option_is_left_out_of_rows():
    conn = _wti_spread()
    _option(conn, "O1", 10)
    _book(conn, "O2", CL_CALL, "CMDTY_OPTION", -10, 3.0, [("NOTIONAL", "USD", -10 * 1000 * 3.0, "2026-11-17", 0)])
    out = curve_positions(conn, AS_OF)
    assert all(r["product"] == "FUTURE" for r in out["rows"])
    assert out["flat_contracts"] == [{"product": "CMDTY_OPTION", "root_id": "NYMEX:CL", "contract_id": CL_CALL,
                                      "expiry": "2026-11-17", "trade_ids": ["O1", "O2"]}]
