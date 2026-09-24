"""The commodity contract universe (data/contracts/, config/contracts.csv): contract-master lane."""

import csv
import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml

from data.contracts import (
    FIXABLE_FIELDS,
    WORKSHEET_COLUMNS,
    AmbiguousContract,
    ContractRoot,
    UnknownContract,
    apply_fixes,
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
from data.contracts.universe import CONTRACTS_CSV, quantity_factor

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


@pytest.mark.parametrize("root_id, delivery", [
    ("NYMEX:CL", "physical"),
    ("ICE:B", "cash"),        # cash settled against the ICE Brent Index
    ("SGX:FEF", "cash"),      # Platts iron ore average
    ("COMEX:GC", "physical"),
    ("SHFE:CU", "physical"),
    ("CBOT:ZC", "physical"),
    ("NYMEX:NG", "physical"),
    ("LME:CA", "physical"),
    ("CME:HE", "cash"),
])
def test_delivery_from_the_exchange_specification(root_id, delivery):
    assert get_root(root_id).delivery == delivery


def test_delivery_is_blank_only_where_not_confirmed():
    blank = sorted(r.root_id for r in load_roots().values() if not r.delivery)
    assert blank == ["COMEX:ZNC"]


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


# --- Bloomberg roots (Phase 2: best guesses until the Bloomberg check) ----------------------

def test_no_placeholder_bloomberg_root_is_left():
    roots = load_roots()
    left = sorted(r.root_id for r in roots.values() if r.bbg_placeholder or r.bbg_root.startswith("ZZ"))
    assert left == []
    # a best guess is never marked verified: the 11 terminal-checked roots stay the only ones
    assert sum(r.bbg_verified for r in roots.values()) == 11
    guesses = [r for r in roots.values() if "is a best guess: the" in r.notes]
    assert len(guesses) == 102
    for r in guesses:
        assert "(low confidence)" in r.notes and not r.bbg_verified, r.root_id


def test_no_two_rows_share_a_bloomberg_root_and_key():
    seen = {}
    for r in load_roots().values():
        key = (r.bbg_root, r.bbg_yellow_key)
        assert key not in seen, (key, seen.get(key), r.root_id)
        seen[key] = r.root_id


@pytest.mark.parametrize("root_id, bbg_root", [
    ("NYMEX:JA", "NJA"),    # bare JA is OSE platinum's root
    ("ZCE:SH", "ZSH"),      # bare SH is DCE soybean oil's root
    ("DCE:LH", "DLH"),      # bare LH is CME lean hogs' root
    ("LME:CO", "LCO"),      # bare CO is ICE Brent's root
    ("SHFE:SS", "SS"),
    ("NYMEX:7H", "7H"),     # a root that starts with a digit
])
def test_guessed_roots_avoid_other_roots_and_resolve_back(root_id, bbg_root):
    root = get_root(root_id)
    assert root.bbg_root == bbg_root and not root.bbg_verified
    c = contract_month(root_id, 12, 2026)
    assert c.contract_id == f"{bbg_root}Z26 Comdty"
    assert contract_for(root_id, c.contract_id) == c
    assert resolve_future(f"{bbg_root}Z6 Comdty", trade_date=TRADE_DATE).root_id == root_id


# --- the fixes worksheet --------------------------------------------------------------------

def _contracts_copy(tmp_path):
    path = tmp_path / "contracts.csv"
    path.write_bytes(CONTRACTS_CSV.read_bytes())
    return path


def _worksheet(tmp_path, rows, name="fixes.csv"):
    path = tmp_path / name
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(WORKSHEET_COLUMNS)
        for r in rows:
            w.writerow([r.get(c, "") for c in WORKSHEET_COLUMNS])
    return path


def _fix(root_id, field, current, suggested, apply="yes"):
    return {"root_id": root_id, "field": field, "current": current, "suggested": suggested,
            "verdict": "WRONG", "reason": "Bloomberg says so", "apply": apply}


def test_the_worksheet_columns_and_fields_are_the_agreed_ones():
    assert WORKSHEET_COLUMNS == ("root_id", "field", "current", "suggested", "verdict", "reason", "apply")
    assert set(FIXABLE_FIELDS) == {"bbg_root", "bbg_yellow_key", "bbg_verified", "currency",
                                   "contract_size", "size_unit", "quote_unit", "price_scale", "delivery"}


def test_a_price_scale_fix_is_applied_and_the_multiplier_recomputed(tmp_path):
    contracts = _contracts_copy(tmp_path)
    before = contracts.read_bytes()
    assert load_roots(contracts)["NYMEX:B0"].multiplier == 42000.0
    ws = _worksheet(tmp_path, [
        _fix("NYMEX:B0", "price_scale", "1.0", "0.01"),              # '1.0' is the file's '1'
        _fix("NYMEX:B0", "bbg_verified", "False", "TRUE", apply="Y"),
        _fix("NYMEX:CL", "bbg_root", "CL", "CL", apply=""),          # not marked: skipped
    ])
    out = apply_fixes(ws, contracts_path=contracts)
    assert out["written"] is True and out["refused"] == []
    assert [(a["root_id"], a["field"], a["before"], a["after"]) for a in out["applied"]] == [
        ("NYMEX:B0", "price_scale", "1", "0.01"), ("NYMEX:B0", "bbg_verified", "false", "true")]
    assert out["applied"][0]["multiplier_before"] == "42000"
    assert out["applied"][0]["multiplier_after"] == "420"
    assert [(s["row"], s["root_id"]) for s in out["skipped"]] == [(4, "NYMEX:CL")]
    b0 = load_roots(contracts)["NYMEX:B0"]             # the cache was cleared: the new file is read
    assert (b0.price_scale, b0.multiplier, b0.bbg_verified) == (0.01, 420.0, True)
    # only that row changed; column order, row order and LF endings kept
    after = contracts.read_bytes()
    assert b"\r" not in after
    old_lines, new_lines = before.split(b"\n"), after.split(b"\n")
    assert len(old_lines) == len(new_lines) and old_lines[0] == new_lines[0]
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(changed) == 1 and new_lines[changed[0]].startswith(b"NYMEX:B0,")
    assert b",0.01,420," in new_lines[changed[0]]
    assert list(tmp_path.glob(".contracts-*")) == []


@pytest.mark.parametrize("row, why", [
    (_fix("NYMEX:CL", "price_scale", "0.01", "1"), "stale worksheet"),       # the file has 1
    (_fix("NYMEX:CL", "multiplier", "1000", "100"), "cannot be fixed"),     # never from the worksheet
    (_fix("NYMEX:CL", "name", "x", "y"), "cannot be fixed"),
    (_fix("NYMEX:CLL", "price_scale", "1", "0.01"), "unknown contract root"),
    (_fix("ICE:M", "currency", "GBP", "GBp"), "minor unit"),                  # pence: a price_scale fix
    (_fix("NYMEX:CL", "contract_size", "1000", "-5"), "positive"),
    (_fix("NYMEX:B0", "price_scale", "1", "0,01"), "decimal point"),         # never read as 1
])
def test_a_row_is_refused_and_nothing_is_written(tmp_path, row, why):
    contracts = _contracts_copy(tmp_path)
    before = contracts.read_bytes()
    out = apply_fixes(_worksheet(tmp_path, [row]), contracts_path=contracts)
    assert out["applied"] == [] and out["written"] is False
    (refused,) = out["refused"]
    assert refused["row"] == 2 and refused["root_id"] == row["root_id"] and why in refused["why"]
    assert contracts.read_bytes() == before


def test_a_result_that_would_not_load_writes_nothing(tmp_path):
    contracts = _contracts_copy(tmp_path)
    before = contracts.read_bytes()
    ws = _worksheet(tmp_path, [
        _fix("NYMEX:B0", "price_scale", "1", "0.01"),     # fine on its own
        _fix("ICE:B", "bbg_root", "CO", "CL"),            # WTI's root: two rows would share it
    ])
    out = apply_fixes(ws, contracts_path=contracts)
    assert out["applied"] == [] and out["written"] is False
    assert [r["row"] for r in out["refused"]] == [2, 3]
    assert all("would not load" in r["why"] and "'CL'" in r["why"] for r in out["refused"])
    assert str(contracts) in out["refused"][0]["why"]             # the file named, not a temp copy
    assert contracts.read_bytes() == before
    assert list(tmp_path.glob(".contracts-*")) == []


def test_a_currency_fix_carries_the_quote_unit(tmp_path):
    contracts = _contracts_copy(tmp_path)
    out = apply_fixes(_worksheet(tmp_path, [_fix("ICEUS:CC", "currency", "USD", "gbp")]),
                      contracts_path=contracts)
    (a,) = out["applied"]
    assert (a["after"], a["quote_unit_before"], a["quote_unit_after"]) == ("GBP", "USD/t", "GBP/t")
    cc = load_roots(contracts)["ICEUS:CC"]
    assert (cc.currency, cc.quote_unit, cc.multiplier) == ("GBP", "GBP/t", 10.0)


def test_dry_run_reports_and_writes_nothing(tmp_path):
    contracts = _contracts_copy(tmp_path)
    before = contracts.read_bytes()
    load_roots(contracts)                                         # cached
    out = apply_fixes(_worksheet(tmp_path, [_fix("NYMEX:B0", "price_scale", "1", "0.01")]),
                      contracts_path=contracts, dry_run=True)
    assert out["written"] is False and out["refused"] == []
    assert out["applied"][0]["multiplier_after"] == "420"
    assert contracts.read_bytes() == before
    assert load_roots(contracts)["NYMEX:B0"].multiplier == 42000.0
    assert list(tmp_path.glob(".contracts-*")) == []


def test_the_cache_is_cleared_after_a_write(tmp_path):
    contracts = _contracts_copy(tmp_path)
    assert get_root("SHFE:SS", contracts).bbg_root == "SS"              # read and cached
    out = apply_fixes(_worksheet(tmp_path, [_fix("shfe:ss", "bbg_root", "SS", "sss")]),
                      contracts_path=contracts)
    assert out["written"] is True
    assert get_root("SHFE:SS", contracts).bbg_root == "SSS"


def test_a_semicolon_worksheet_saved_by_excel_is_read(tmp_path):
    contracts = _contracts_copy(tmp_path)
    path = tmp_path / "fixes_excel.csv"
    lines = [";".join(c.upper() for c in WORKSHEET_COLUMNS),
             "NYMEX:B0;price_scale;1;0.01;WRONG;Bloomberg quotes cents;Yes"]
    path.write_bytes(("﻿" + "\r\n".join(lines) + "\r\n").encode("utf-8"))
    out = apply_fixes(path, contracts_path=contracts)
    assert out["refused"] == [] and out["applied"][0]["multiplier_after"] == "420"
    assert b"\r" not in contracts.read_bytes()


def test_a_worksheet_without_its_columns_raises(tmp_path):
    contracts = _contracts_copy(tmp_path)
    path = tmp_path / "bad.csv"
    path.write_text("root_id,field,suggested\nNYMEX:CL,price_scale,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="current"):
        apply_fixes(path, contracts_path=contracts)
