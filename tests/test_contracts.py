"""The commodity contract universe (data/contracts/, config/contracts.csv): contract-master lane."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml

from data.contracts import (
    AmbiguousContract,
    ContractRoot,
    UnknownContract,
    contract_for,
    contract_month,
    estimated_last_trade_date,
    get_root,
    load_roots,
    request_ticker,
    resolve_future,
    static_dates,
    store_static_dates,
)
from data.contracts.universe import quantity_factor

TRADE_DATE = date(2026, 9, 24)


# --- the universe -------------------------------------------------------------------------

def test_universe_loads_every_root_with_its_multiplier():
    roots = load_roots()
    assert len(roots) == 202
    for root in roots.values():
        assert isinstance(root, ContractRoot)
        quote_qty = root.quote_unit.split("/")[1]
        expected = root.contract_size * quantity_factor(root.size_unit, quote_qty) * root.price_scale
        assert root.multiplier == pytest.approx(expected, rel=1e-12), root.root_id
        if root.size_unit == quote_qty:
            assert root.multiplier == pytest.approx(root.contract_size * root.price_scale), root.root_id
        assert root.active_months and all(1 <= m <= 12 for m in root.active_months)
    assert sum(r.bbg_verified for r in roots.values()) == 11


@pytest.mark.parametrize("root_id, multiplier, currency", [
    ("CBOT:ZC", 50.0, "USD"),     # 5,000 bu x 0.01 (quoted in cents)
    ("ICE:M", 300.0, "GBP"),      # NBP 30,000 therms x 0.01 (pence)
    ("SHFE:CU", 5.0, "CNY"),
    ("NYMEX:CL", 1000.0, "USD"),
    ("COMEX:HG", 250.0, "USD"),   # 25,000 lb x 0.01
    ("CME:HE", 400.0, "USD"),     # sized in lb, quoted per cwt: 40,000 lb = 400 cwt
    ("SGX:TF", 50.0, "USD"),      # sized in t, quoted in US cents per kg: 5,000 kg x 0.01
    ("DCE:JD", 10.0, "CNY"),      # quoted per 500 kg: 5 t x 2
])
def test_multiplier_of_known_contracts(root_id, multiplier, currency):
    root = get_root(root_id)
    assert root.multiplier == pytest.approx(multiplier)
    assert root.currency == currency


def test_unknown_root_id_names_close_matches():
    with pytest.raises(KeyError, match="NYMEX:CL"):
        get_root("NYMEX:CLL")


def test_spread_templates_name_only_roots_in_the_universe():
    roots = load_roots()
    files = sorted(Path(__file__).resolve().parents[1].joinpath("config", "spreads").glob("*.yaml"))
    assert [f.stem for f in files] == ["agriculture", "chemicals", "energy", "ferrous", "metals"]
    for f in files:
        for spread in yaml.safe_load(f.read_text(encoding="utf-8")):
            for leg in spread["legs"]:
                assert leg["instrument"] in roots, (f.name, spread["id"], leg["instrument"])


# --- contract months ----------------------------------------------------------------------

def test_one_character_root_canonical_id_and_request_ticker():
    c = contract_month("CBOT:ZC", 12, 2026)
    assert c.contract_id == "C Z26 Comdty"
    assert c.month_code == "Z"
    assert request_ticker(c, date(2026, 11, 2)) == "C Z6 Comdty"
    assert request_ticker(c, c.last_trade_date) == "C Z6 Comdty"
    assert request_ticker(c, date(2027, 1, 4)) == "C Z26 Comdty"
    assert contract_month("NYMEX:CL", 12, 2026).contract_id == "CLZ26 Comdty"


@pytest.mark.parametrize("month, year, expected", [
    (10, 2026, date(2026, 10, 30)),   # the 31st is a Saturday
    (5, 2026, date(2026, 5, 29)),     # the 31st is a Sunday
    (12, 2026, date(2026, 12, 31)),   # a Thursday
    (2, 2026, date(2026, 2, 27)),     # the 28th is a Saturday
])
def test_estimate_is_last_weekday_of_the_contract_month(month, year, expected):
    assert estimated_last_trade_date(month, year) == expected
    c = contract_month("NYMEX:CL", month, year)
    assert c.last_trade_date == expected
    assert c.dates_source == "ESTIMATED" and c.estimated
    assert c.first_notice_date is None


def test_stored_bloomberg_dates_beat_the_estimate():
    conn = sqlite3.connect(":memory:")
    assert contract_month("NYMEX:CL", 12, 2026, conn=conn).dates_source == "ESTIMATED"  # no table yet
    # Bloomberg's live one-digit form is stored under the canonical id
    store_static_dates(conn, [
        {"contract_id": "CLZ6 Comdty", "last_trade_date": "2026-11-19", "source": "BBG_BDP"},
        {"contract_id": "C Z26 Comdty", "last_trade_date": date(2026, 12, 14),
         "first_notice_date": "2026-11-30", "source": "BBG_BDP"},
    ])
    assert static_dates(conn, "CLZ26 Comdty")["last_trade_date"] == "2026-11-19"
    cl = contract_month("NYMEX:CL", 12, 2026, conn=conn)
    assert (cl.last_trade_date, cl.first_notice_date, cl.dates_source) == (date(2026, 11, 19), None, "BLOOMBERG")
    corn = resolve_future("C Z6 Comdty", trade_date=TRADE_DATE, conn=conn)
    assert corn.last_trade_date == date(2026, 12, 14)
    assert corn.first_notice_date == date(2026, 11, 30)
    assert corn.dates_source == "BLOOMBERG"
    # past Bloomberg's last trade date the request ticker is the two-digit id
    assert request_ticker(cl, date(2026, 11, 20)) == "CLZ26 Comdty"
    # a later store replaces the row
    store_static_dates(conn, [{"contract_id": "CLZ26 Comdty", "last_trade_date": "2026-11-20", "source": "BBG_BDP"}])
    assert contract_month("NYMEX:CL", 12, 2026, conn=conn).last_trade_date == date(2026, 11, 20)


# --- symbols ------------------------------------------------------------------------------

@pytest.mark.parametrize("symbol, root_id, month, year", [
    ("CLZ6-USAA", "NYMEX:CL", 12, 2026),       # prime-broker export
    ("CLZ6 Comdty", "NYMEX:CL", 12, 2026),     # Bloomberg, one-digit year
    ("CLZ26 Comdty", "NYMEX:CL", 12, 2026),    # Bloomberg, canonical
    ("clz6 comdty", "NYMEX:CL", 12, 2026),
    ("C Z6 Comdty", "CBOT:ZC", 12, 2026),      # one-character Bloomberg root (not DCE:C, ICE:C)
    ("CLZ6", "NYMEX:CL", 12, 2026),
    ("CLZ5", "NYMEX:CL", 12, 2025),            # nearest year >= trade year - 1
    ("CLZ4", "NYMEX:CL", 12, 2034),
    ("CU2611", "SHFE:CU", 11, 2026),           # Chinese root + YYMM
    ("cu2611", "SHFE:CU", 11, 2026),
    ("I2609", "DCE:I", 9, 2026),
    ("SR611", "ZCE:SR", 11, 2026),             # ZCE root + YMM (not LME:SR)
    ("SHFE:CU2611", "SHFE:CU", 11, 2026),      # explicit prefix
    ("NYMEX:CLZ6", "NYMEX:CL", 12, 2026),
    ("LPZ6 Comdty", "LME:CA", 12, 2026),       # Bloomberg root differs from the exchange code
])
def test_each_symbol_form_resolves(symbol, root_id, month, year):
    c = resolve_future(symbol, trade_date=TRADE_DATE)
    assert (c.root_id, c.month, c.year) == (root_id, month, year)


def test_ambiguous_bare_code_raises_with_candidates_and_resolves_when_disambiguated():
    with pytest.raises(AmbiguousContract) as err:
        resolve_future("ZCZ6-USAA", trade_date=TRADE_DATE)
    assert set(err.value.candidates) == {"CBOT:ZC", "ZCE:ZC"}
    assert "CBOT:ZC" in str(err.value) and "ZCE:ZC" in str(err.value)
    assert isinstance(err.value, ValueError)

    assert resolve_future("ZCZ6-USAA", trade_date=TRADE_DATE, currency="USD").root_id == "CBOT:ZC"
    assert resolve_future("CBOT:ZCZ6", trade_date=TRADE_DATE).root_id == "CBOT:ZC"
    assert resolve_future("ZCZ6", trade_date=TRADE_DATE, venue="CBOT").root_id == "CBOT:ZC"
    assert resolve_future("SIZ6", trade_date=TRADE_DATE, description="COMEX SILVER FUT").root_id == "COMEX:SI"
    # a code that is one root's exchange code and another's Bloomberg root is never picked silently
    with pytest.raises(AmbiguousContract) as err:
        resolve_future("COZ6-USAA", trade_date=TRADE_DATE)
    assert set(err.value.candidates) == {"LME:CO", "ICE:B"}


def test_unknown_root_or_contradiction_raises():
    for symbol in ("ESU6-USAA", "XXZ6 Comdty", "not a future", "FOO:CLZ6"):
        with pytest.raises(UnknownContract):
            resolve_future(symbol, trade_date=TRADE_DATE)
    # a populated currency that the only candidate contradicts
    with pytest.raises(UnknownContract, match="CNY"):
        resolve_future("CLZ6-USAA", trade_date=TRADE_DATE, currency="CNY")
    assert issubclass(UnknownContract, ValueError)


# --- stored canonical ids back to contract months -----------------------------------------

def test_contract_for_turns_a_stored_canonical_id_back_into_its_contract():
    for root_id, contract_id, month, year in [
        ("NYMEX:CL", "CLZ26 Comdty", 12, 2026),
        ("CBOT:ZC", "C Z26 Comdty", 12, 2026),     # one-character root, padded
        ("LME:CA", "LPH27 Comdty", 3, 2027),       # Bloomberg root differs from the exchange code
        ("ZCE:SR", "CBX26 Comdty", 11, 2026),
    ]:
        c = contract_for(root_id, contract_id)
        assert (c.root_id, c.month, c.year, c.contract_id) == (root_id, month, year, contract_id)
        assert c == contract_month(root_id, month, year)


def test_contract_for_takes_bloomberg_dates_when_stored():
    conn = sqlite3.connect(":memory:")
    store_static_dates(conn, [{"contract_id": "C Z26 Comdty", "last_trade_date": "2026-12-14",
                               "first_notice_date": "2026-11-30", "source": "BBG_BDP"}])
    c = contract_for("CBOT:ZC", "C Z26 Comdty", conn=conn)
    assert (c.last_trade_date, c.first_notice_date, c.dates_source) == (
        date(2026, 12, 14), date(2026, 11, 30), "BLOOMBERG")
    assert contract_for("CBOT:ZC", "C Z26 Comdty").dates_source == "ESTIMATED"


@pytest.mark.parametrize("root_id, contract_id", [
    ("NYMEX:CL", "CLZ6 Comdty"),      # one-digit year: the decade would be a guess
    ("NYMEX:CL", "CLZ26"),            # no yellow key
    ("NYMEX:CL", "not an id"),
    ("NYMEX:CL", "HOZ26 Comdty"),     # another root's Bloomberg root
    ("CBOT:ZC", "CLZ26 Comdty"),
    ("DCE:C", "C Z26 Comdty"),        # CBOT corn's id is not DCE corn's (DCE:C is 'AC')
    ("NYMEX:CL", "CLZ26 Index"),      # wrong yellow key
    ("NYMEX:XX", "CLZ26 Comdty"),     # unknown root
])
def test_contract_for_refuses_an_id_that_is_not_that_roots(root_id, contract_id):
    with pytest.raises(UnknownContract):
        contract_for(root_id, contract_id)
