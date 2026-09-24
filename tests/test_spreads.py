"""spreads-engine: the book's futures grouped into spreads, P&L added up from value_book rows."""
import sqlite3
from pathlib import Path

import pytest

from data.contracts import get_root
from data.ingest import blotter
from data.ingest.schema import create_schema
from engine.pnl.valuation import value_book
from engine.spreads import (
    CALENDAR, KIND_BUNDLE, KIND_PINNED, REVIEW_ACCOUNTS, REVIEW_AMBIGUOUS, REVIEW_RATIO, book_spreads,
    ensure_overrides_table, load_templates, read_overrides,
)
from engine.spreads.grouping import TOLERANCE, fit

AS_OF = "2026-09-15"          # a Tuesday: Daily is measured from the 2026-09-14 close
PREV = "2026-09-14"
TD = "2026-09-01"
SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "blotter_sample.csv"

CLZ6, CLF7, CLG7, CLX6 = "CLZ26 Comdty", "CLF27 Comdty", "CLG27 Comdty", "CLX26 Comdty"
BRENT_Z6 = "COZ26 Comdty"
RBX6, HOX6 = "XBX26 Comdty", "HOX26 Comdty"
CU_Z6, HG_Z6 = "CUZ26 Comdty", "HGZ26 Comdty"
EXPIRY = {CLZ6: "2026-12-31", CLF7: "2027-01-29", CLG7: "2027-02-26", CLX6: "2026-11-30",
          BRENT_Z6: "2026-12-31", RBX6: "2026-11-30", HOX6: "2026-11-30", CU_Z6: "2026-12-15",
          HG_Z6: "2026-12-31"}
ROOT = {CLZ6: "NYMEX:CL", CLF7: "NYMEX:CL", CLG7: "NYMEX:CL", CLX6: "NYMEX:CL", BRENT_Z6: "ICE:B",
        RBX6: "NYMEX:RB", HOX6: "NYMEX:HO", CU_Z6: "SHFE:CU", HG_Z6: "COMEX:HG"}


def _db():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _future(conn, tid, inst, lots, fill, account="ACC", trade_date=TD, expiry=None):
    root = get_root(ROOT[inst])
    expiry = expiry or EXPIRY[inst]
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (inst, root.root_id, root.currency, root.multiplier, inst, expiry))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": inst, "product": "FUTURE", "package_id": tid,
            "trade_date": trade_date, "quantity": lots, "price": fill, "account": account, "counterparty": "C",
            "strategy": "", "trader": "JB", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, lots * root.multiplier * fill, trade_date, expiry, fill))


def _px(conn, inst, value, day=AS_OF, expiry=None):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH','t')",
                 (day, inst, expiry or EXPIRY[inst], value))


def _spot(conn, pair, value, day=AS_OF):
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FX',?,?,1,0,?,'9999-12-31')",
                 (pair, pair[:3], pair[3:], pair + " Curncy"))
    conn.execute("INSERT INTO marks VALUES (?,?,?,'SPOT',?,'BBG_BFXFORWARD','t')", (day, pair, day, value))


def _only(items, **match):
    hits = [x for x in items if all(x.get(k) == v for k, v in match.items())]
    assert len(hits) == 1, items
    return hits[0]


def _vb_ltd(conn, ids, day=AS_OF):
    vb = value_book(conn, day)
    return float(vb[vb["trade_id"].isin(ids)]["pnl_usd"].sum())


# ------------------------------------------------------------------ calendar
def _wti_calendar(conn=None, near=2, far=-2):
    conn = conn or _db()
    _future(conn, "W1", CLZ6, near, 70.0)
    _future(conn, "W2", CLF7, far, 69.5)
    for day, z, f in ((PREV, 70.5, 69.8), (AS_OF, 71.0, 70.2)):
        _px(conn, CLZ6, z, day)
        _px(conn, CLF7, f, day)
    return conn


def test_wti_calendar_spread():
    conn = _wti_calendar()
    out = book_spreads(conn, AS_OF)
    s = _only(out["spreads"])
    assert s["kind"] == CALENDAR and s["name"] == "CL Z26/F27 calendar" and s["template"] == ""
    assert s["spread_id"] == "SPREAD-W1" and s["trade_ids"] == ["W1", "W2"]
    assert s["size"] == 2.0 and s["size_unit"] == "lots" and s["unit"] == "USD/bbl"
    assert [(leg["instrument_id"], leg["lots"], leg["weight"], leg["contract_month"]) for leg in s["legs"]] == [
        (CLZ6, 2.0, 1.0, "2026-12"), (CLF7, -2.0, -1.0, "2027-01")]
    ltd = 2 * 1000 * (71.0 - 70.0) - 2 * 1000 * (70.2 - 69.5)
    assert s["pnl_usd"]["ltd"] == pytest.approx(ltd)
    prev = 2 * 1000 * (70.5 - 70.0) - 2 * 1000 * (69.8 - 69.5)
    assert s["pnl_usd"]["daily"] == pytest.approx(ltd - prev) and s["ref_dates"]["daily"] == PREV
    assert s["pnl_usd"]["mtd"] == pytest.approx(ltd)       # booked 2026-09-01: nothing on the 08-31 close
    assert s["pnl_reasons"] == {p: "" for p in ("ltd", "daily", "d5", "mtd", "ytd")}
    assert s["leftover"] == [{"root_id": "NYMEX:CL", "lots": 0.0, "usd_notional": 0.0, "reason": ""}]
    assert s["status"] == "open" and out["outrights"] == [] and out["review"] == []


def test_calendar_within_tolerance_leaves_the_extra_lot_outright():
    conn = _wti_calendar(near=20, far=-19)       # 5 % apart: still a calendar
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == CALENDAR and s["size"] == 19.0 and s["deviation"] == pytest.approx(0.05)
    assert s["leftover"] == [{"root_id": "NYMEX:CL", "lots": 1.0, "usd_notional": pytest.approx(1 * 1000 * 71.0),
                              "reason": ""}]
    conn = _wti_calendar(near=20, far=-18)       # 10 % apart: listed for review, left outright
    out = book_spreads(conn, AS_OF)
    assert out["spreads"] == []
    r = _only(out["review"])
    assert r["kind"] == REVIEW_RATIO and r["trade_ids"] == ["W1", "W2"] and "off by 10.0%" in r["reason"]
    assert {o["trade_id"] for o in out["outrights"]} == {"W1", "W2"}
    assert all(o["review_ids"] == [r["review_id"]] for o in out["outrights"])


# ------------------------------------------------------------------ templates
def test_brent_against_wti_via_the_template():
    conn = _db()
    _future(conn, "B1", BRENT_Z6, 5, 72.3)
    _future(conn, "C1", CLZ6, -5, 68.6)
    _px(conn, BRENT_Z6, 73.0)
    _px(conn, CLZ6, 68.9)
    out = book_spreads(conn, AS_OF)
    s = _only(out["spreads"])
    assert s["kind"] == "bench.crude.brent_vs_wti" == s["template"] and s["name"] == "Brent vs WTI"
    assert s["family"] == "benchmark" and s["size"] == 5000.0 and s["size_unit"] == "bbl"
    assert s["pnl_usd"]["ltd"] == pytest.approx(5 * 1000 * 0.7 - 5 * 1000 * 0.3)
    assert [e["lots"] for e in s["leftover"]] == [0.0, 0.0]


def test_brent_leg_expired_leaves_the_wti_leg_as_leftover():
    conn = _db()
    _future(conn, "B1", BRENT_Z6, 5, 72.3, expiry="2026-09-10")
    _future(conn, "C1", CLZ6, -5, 68.6)
    _px(conn, BRENT_Z6, 73.0, day="2026-09-10", expiry="2026-09-10")
    _px(conn, CLZ6, 68.9)
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["status"] == "open"
    by_root = {e["root_id"]: e for e in s["leftover"]}
    assert by_root["ICE:B"]["lots"] == 0.0 and by_root["NYMEX:CL"]["lots"] == -5.0
    assert by_root["NYMEX:CL"]["usd_notional"] == pytest.approx(-5 * 1000 * 68.9)


def test_321_crack_with_its_ratio():
    conn = _db()
    _future(conn, "K1", CLX6, -3, 69.2)
    _future(conn, "K2", RBX6, 2, 205.4)          # cents per gallon, 42,000 gal = 1,000 bbl a lot
    _future(conn, "K3", HOX6, 1, 238.5)
    for inst, px in ((CLX6, 70.0), (RBX6, 207.0), (HOX6, 240.0)):
        _px(conn, inst, px)
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == "proc.us.crack_321" and s["size"] == pytest.approx(3000.0, rel=1e-5)
    assert s["deviation"] == pytest.approx(0.0, abs=1e-5)
    assert [leg["instrument_id"] for leg in s["legs"]] == [RBX6, HOX6, CLX6]
    assert all(e["lots"] == 0.0 and e["usd_notional"] == 0.0 for e in s["leftover"])
    ltd = -3 * 1000 * 0.8 + 2 * 420 * 1.6 + 1 * 420 * 1.5
    assert s["pnl_usd"]["ltd"] == pytest.approx(ltd) == pytest.approx(_vb_ltd(conn, ["K1", "K2", "K3"]))


def test_crack_out_of_ratio_is_not_a_211_crack_either():
    conn = _db()
    _future(conn, "K1", CLX6, -3, 69.2)
    _future(conn, "K2", RBX6, 1, 205.4)
    _future(conn, "K3", HOX6, 1, 238.5)
    out = book_spreads(conn, AS_OF)
    assert out["spreads"] == []
    r = _only(out["review"])
    assert r["kind"] == REVIEW_RATIO          # signs fit both cracks; 1:1:-3 is 33 % off the 2-1-1, 50 % off the 3-2-1
    assert r["candidates"][0]["kind"] == "proc.us.crack_211"
    assert r["candidates"][0]["also_matches"] == ["proc.us.crack_321"]
    assert {o["trade_id"] for o in out["outrights"]} == {"K1", "K2", "K3"}


def test_shfe_copper_against_comex_in_two_currencies():
    conn = _db()
    _future(conn, "CU", CU_Z6, -9, 78450.0)      # 45 t, CNY per tonne
    _future(conn, "HG", HG_Z6, 4, 455.2)         # 100,000 lb = 45.36 t, US cents per lb
    _px(conn, CU_Z6, 78000.0)
    _px(conn, HG_Z6, 450.0)
    _spot(conn, "USDCNY", 7.1)
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == "bench.cu.shfe_vs_comex" and s["size_unit"] == "t"
    assert s["size"] == pytest.approx(-45.0) and s["deviation"] == pytest.approx(1 - 45 / 45.359237)
    cu, hg = _only(s["legs"], instrument_id=CU_Z6), _only(s["legs"], instrument_id=HG_Z6)
    assert cu["currency"] == "CNY" and hg["currency"] == "USD"
    assert cu["pnl_local"] == pytest.approx(-9 * 5 * (78000.0 - 78450.0))
    assert cu["pnl_usd"] == pytest.approx(cu["pnl_local"] / 7.1)
    assert hg["pnl_usd"] == pytest.approx(4 * 250 * (450.0 - 455.2))
    assert s["pnl_usd"]["ltd"] == pytest.approx(cu["pnl_usd"] + hg["pnl_usd"])
    left = {e["root_id"]: e for e in s["leftover"]}
    assert left["SHFE:CU"]["lots"] == 0.0
    extra_hg = 4 - 45 / 11.33980925
    assert left["COMEX:HG"]["lots"] == pytest.approx(extra_hg, abs=1e-6)
    assert left["COMEX:HG"]["usd_notional"] == pytest.approx(extra_hg * 250 * 450.0, rel=1e-6)


def test_cny_leg_without_a_spot_blanks_the_spread_with_its_reason():
    conn = _db()
    _future(conn, "CU", CU_Z6, -9, 78450.0)
    _future(conn, "HG", HG_Z6, 4, 455.2)
    _px(conn, CU_Z6, 78000.0)
    _px(conn, HG_Z6, 450.0)
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["pnl_usd"]["ltd"] is None and "no SPOT for USD conversion of CNY" in s["pnl_reasons"]["ltd"]
    assert _only(s["legs"], instrument_id=CU_Z6)["pnl_local"] == pytest.approx(-9 * 5 * (78000.0 - 78450.0))


def test_cny_against_usd_groups_across_two_accounts():
    conn = _db()
    _future(conn, "CU", CU_Z6, -9, 78450.0, account="PB-CN")     # onshore clearing account
    _future(conn, "HG", HG_Z6, 4, 455.2, account="PB-FUT")
    _px(conn, CU_Z6, 78000.0)
    _px(conn, HG_Z6, 450.0)
    _spot(conn, "USDCNY", 7.1)
    out = book_spreads(conn, AS_OF)
    s = _only(out["spreads"])
    assert s["kind"] == "bench.cu.shfe_vs_comex" and s["accounts"] == ["PB-CN", "PB-FUT"]
    assert s["pnl_usd"]["ltd"] == pytest.approx(_vb_ltd(conn, ["CU", "HG"]))
    assert out["review"] == [] and out["outrights"] == []


def test_cny_against_usd_across_accounts_still_needs_the_ratio():
    conn = _db()
    _future(conn, "CU", CU_Z6, -20, 78450.0, account="PB-CN")    # 100 t against 45 t
    _future(conn, "HG", HG_Z6, 4, 455.2, account="PB-FUT")
    out = book_spreads(conn, AS_OF)
    assert out["spreads"] == []
    r = _only(out["review"])
    assert r["kind"] == REVIEW_RATIO and "off by 54.6%" in r["reason"]


def test_single_currency_legs_on_two_accounts_go_to_review():
    conn = _db()
    _future(conn, "B1", BRENT_Z6, 5, 72.3, account="ONSHORE")
    _future(conn, "C1", CLZ6, -5, 68.6, account="OFFSHORE")
    out = book_spreads(conn, AS_OF)
    assert out["spreads"] == []
    r = _only(out["review"])
    assert r["kind"] == REVIEW_ACCOUNTS and r["accounts"] == ["OFFSHORE", "ONSHORE"]
    assert "the lots fit the ratio" in r["reason"]


# ------------------------------------------------------------------ the user's grouping wins
def test_bundle_overrides_the_rule():
    conn = _wti_calendar()
    _future(conn, "B1", BRENT_Z6, 2, 72.3)       # unbundled, Brent/WTI(F7) and the calendar would share F7
    conn.execute("UPDATE trades SET theme = 'WTI roll' WHERE trade_id IN ('W1', 'W2')")
    out = book_spreads(conn, AS_OF)
    s = _only(out["spreads"])
    assert s["kind"] == KIND_BUNDLE and s["name"] == "WTI roll" and s["spread_id"] == "BUNDLE-WTI roll"
    assert s["trade_ids"] == ["W1", "W2"] and s["template"] == "" and s["family"] == CALENDAR
    assert s["leftover"] == [{"root_id": "NYMEX:CL", "lots": 0.0, "usd_notional": 0.0, "reason": ""}]
    assert s["pnl_usd"]["ltd"] == pytest.approx(_vb_ltd(conn, ["W1", "W2"]))
    assert [o["trade_id"] for o in out["outrights"]] == ["B1"]


def test_bundle_takes_one_leg_out_of_a_template_match():
    conn = _db()
    _future(conn, "B1", BRENT_Z6, 5, 72.3)
    _future(conn, "C1", CLZ6, -5, 68.6)
    conn.execute("UPDATE trades SET theme = 'my brent' WHERE trade_id = 'B1'")
    out = book_spreads(conn, AS_OF)
    s = _only(out["spreads"])
    assert s["kind"] == KIND_BUNDLE and s["trade_ids"] == ["B1"]
    assert s["leftover"][0]["root_id"] == "ICE:B" and s["leftover"][0]["lots"] == 5.0   # net per root
    assert "net per root" in s["leftover_basis"]
    assert [o["trade_id"] for o in out["outrights"]] == ["C1"] and out["review"] == []


def test_instrument_theme_counts_as_the_bundle():
    conn = _wti_calendar()
    conn.execute("INSERT INTO instrument_theme VALUES (?, 'curve')", (CLZ6,))
    conn.execute("INSERT INTO instrument_theme VALUES (?, 'curve')", (CLF7,))
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == KIND_BUNDLE and s["name"] == "curve"


def test_overrides_table_pin_and_split():
    conn = _wti_calendar()
    assert ensure_overrides_table(conn) and read_overrides(conn) == {}
    conn.execute("INSERT INTO spread_overrides (trade_id, action) VALUES ('W1', 'SPLIT')")
    out = book_spreads(conn, AS_OF)
    assert out["spreads"] == [] and {o["trade_id"] for o in out["outrights"]} == {"W1", "W2"}
    assert _only(out["outrights"], trade_id="W1")["why_outright"].startswith("split by hand")
    conn.execute("DELETE FROM spread_overrides")
    _future(conn, "X1", CLX6, 1, 69.0, trade_date="2026-09-02")
    conn.execute("INSERT INTO spread_overrides (trade_id, action, group_name) VALUES ('X1', 'PIN', 'mine'), "
                 "('W1', 'PIN', 'mine'), ('W2', 'bogus', '')")
    out = book_spreads(conn, AS_OF)
    s = _only(out["spreads"])
    assert s["kind"] == KIND_PINNED and s["trade_ids"] == ["W1", "X1"] and s["spread_id"] == "PIN-mine"
    assert [o["trade_id"] for o in out["outrights"]] == ["W2"]
    assert any("W2" in r and "ignored" in r for r in out["reasons"])


def test_book_spreads_on_a_database_without_the_table_creates_it():
    conn = _wti_calendar()
    book_spreads(conn, AS_OF)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'spread_overrides'").fetchone()


def test_a_read_only_database_without_the_table_has_no_overrides(tmp_path):
    path = tmp_path / "risk.db"
    conn = sqlite3.connect(path)
    create_schema(conn)
    _wti_calendar(conn)
    conn.commit()
    conn.close()
    ro = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    out = book_spreads(ro, AS_OF)
    assert _only(out["spreads"])["kind"] == CALENDAR and out["reasons"] == []
    assert read_overrides(ro) == {}


# ------------------------------------------------------------------ never guessed, never silent
def test_ambiguous_group_goes_to_review():
    conn = _db()
    _future(conn, "A1", CLZ6, 2, 70.0)
    _future(conn, "A2", CLF7, -2, 69.5)
    _future(conn, "A3", CLG7, -2, 69.1)          # Z6 against F7 or against G7: two ways
    out = book_spreads(conn, AS_OF)
    assert out["spreads"] == []
    r = _only(out["review"])
    assert r["kind"] == REVIEW_AMBIGUOUS and r["trade_ids"] == ["A1", "A2", "A3"]
    assert {tuple(c["trade_ids"]) for c in r["candidates"]} == {("A1", "A2"), ("A1", "A3")}
    assert {o["trade_id"] for o in out["outrights"]} == {"A1", "A2", "A3"}


def test_a_leg_with_no_price_blanks_the_spread_with_its_reason():
    conn = _db()
    _future(conn, "W1", CLZ6, 2, 70.0)
    _future(conn, "W2", CLF7, -2, 69.5)
    _px(conn, CLZ6, 71.0)                        # CLF7 has no price on any day
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == CALENDAR
    assert all(v is None for v in s["pnl_usd"].values())
    assert "W2" in s["pnl_reasons"]["ltd"] and f"no FUTURE_PX mark for {CLF7}" in s["pnl_reasons"]["ltd"]
    assert all(s["pnl_reasons"][p] for p in ("daily", "d5", "mtd", "ytd"))
    w2 = _only(s["legs"], instrument_id=CLF7)
    assert w2["pnl_usd"] is None and "no FUTURE_PX" in w2["reason"]
    assert _only(s["legs"], instrument_id=CLZ6)["pnl_usd"] == pytest.approx(2000.0)


def test_outrights_are_left_alone():
    conn = _db()
    _future(conn, "O1", CLZ6, 1, 70.0)
    _future(conn, "O2", BRENT_Z6, 1, 72.0, trade_date="2026-09-02")    # another day: no Brent/WTI
    _future(conn, "O3", CLF7, 3, 69.0)                                 # same sign as O1: no calendar
    _future(conn, "R1", CLG7, 2, 69.0, trade_date="2026-09-03")
    _future(conn, "R2", CLG7, -2, 69.4, trade_date="2026-09-03")      # a same-day round trip
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": "FX1", "source": "XLSX", "instrument_id": "EURUSD", "product": "FX_FWD",
            "package_id": "FX1", "trade_date": TD, "quantity": 1e6, "price": 1.17, "account": "ACC",
            "counterparty": "C", "strategy": "", "trader": "JB", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    _px(conn, CLZ6, 71.0)
    out = book_spreads(conn, AS_OF)
    assert out["spreads"] == [] and out["review"] == []
    assert [o["trade_id"] for o in out["outrights"]] == ["O1", "O2", "O3", "R1", "R2"]   # never the FX hedge
    o1 = _only(out["outrights"], trade_id="O1")
    assert o1["pnl_usd"]["ltd"] == pytest.approx(1000.0) and o1["root_id"] == "NYMEX:CL"
    assert o1["contract_month"] == "2026-12" and o1["why_outright"] == ""
    assert "round trip" in _only(out["outrights"], trade_id="R1")["why_outright"]


def test_spread_ltd_is_the_sum_of_its_legs_value_book_ltd():
    conn = _wti_calendar()
    _future(conn, "W3", CLZ6, 1, 70.4)                   # a second fill of the near month, same day
    _future(conn, "W4", CLF7, -1, 69.9)
    out = book_spreads(conn, AS_OF)
    s = _only(out["spreads"])
    assert s["trade_ids"] == ["W1", "W2", "W3", "W4"] and s["size"] == 3.0
    assert s["pnl_usd"]["ltd"] == pytest.approx(_vb_ltd(conn, s["trade_ids"]))
    assert sum(leg["pnl_usd"] for leg in s["legs"]) == pytest.approx(s["pnl_usd"]["ltd"])
    daily = _vb_ltd(conn, s["trade_ids"]) - _vb_ltd(conn, s["trade_ids"], PREV)
    assert s["pnl_usd"]["daily"] == pytest.approx(daily)


# ------------------------------------------------------------------ the rule's arithmetic and inputs
def test_fit_is_the_written_rule():
    assert fit([2, -2], [1, -1], [1, 1]) == (2.0, 0.0, (2.0, 2.0))
    assert fit([2, 2], [1, -1], [1, 1]) is None                   # signs that do not fit the weights
    size, dev, _ = fit([-10, 11, 9], [-1.0, 0.733333, 0.183333], [5000, 3333.33, 1000.002])   # CME crush package
    assert size == pytest.approx(49091, rel=1e-3) and dev < TOLERANCE


def test_templates_load_with_their_units_converted():
    templates, problems = load_templates()
    assert problems == ()
    by_id = {t.template_id: t for t in templates}
    crush = by_id["proc.us.board_crush"]
    assert [leg.root_id for leg in crush.legs] == ["CBOT:ZM", "CBOT:ZL", "CBOT:ZS"]
    assert crush.legs[0].units_per_lot == pytest.approx(100 * 33.3333)      # short tons x bu per st
    assert crush.legs[2].units_per_lot == 5000.0
    assert by_id["proc.us.crack_321"].legs[0].units_per_lot == pytest.approx(1000.0)   # 42,000 gal


def test_a_template_file_that_cannot_be_read_is_named_not_raised(tmp_path):
    (tmp_path / "bad.yaml").write_text("- id: x\n  legs: [{instrument: NOPE:ZZ, weight: 1}, "
                                       "{instrument: NYMEX:CL, weight: -1}]\n  unit: USD/bbl\n", encoding="utf-8")
    conn = _wti_calendar()
    out = book_spreads(conn, AS_OF, templates_dir=tmp_path)
    assert any("NOPE:ZZ" in r for r in out["reasons"])
    assert _only(out["spreads"])["kind"] == CALENDAR


def test_the_synthetic_jason_book():
    conn = _db()
    blotter.load(SAMPLE, conn)
    out = book_spreads(conn, AS_OF)
    kinds = sorted(s["kind"] for s in out["spreads"])
    assert kinds.count(CALENDAR) == 2
    assert {"bench.crude.brent_vs_wti", "proc.us.crack_321"} <= set(kinds)
    ore = _only(out["spreads"], kind="bench.ore.dce_vs_sgx62")   # CNY on PB-CN-NMMF against USD on PB-FUT-NMMF
    assert ore["accounts"] == ["PB-CN-NMMF", "PB-FUT-NMMF"] and ore["trade_ids"] == ["910000019", "910000020"]
    copper = [r for r in out["review"] if any(c["kind"] == "bench.cu.shfe_vs_comex" for c in r["candidates"])]
    assert [r["kind"] for r in copper] == [REVIEW_RATIO]       # 4 HG (45 t) against 20 CU (100 t): 55 % off
    assert not [r for r in out["review"] if r["kind"] == REVIEW_ACCOUNTS]
    crush = [r for r in out["review"] if any("proc.us.board_crush" in [c["kind"], *c["also_matches"]]
                                             for c in r["candidates"])]
    assert crush and crush[0]["kind"] == REVIEW_RATIO          # 10:10:10 is 17 % off the board crush
    grouped = [t for s in out["spreads"] for t in s["trade_ids"]]
    outright = [o["trade_id"] for o in out["outrights"]]
    assert len(grouped) == len(set(grouped)) and not set(grouped) & set(outright)
    products = {tid: p for tid, p in conn.execute("SELECT trade_id, product FROM trades")}
    assert {products[t] for t in outright} == {"FUTURE"}      # the FX hedges and FX options are not the rule's
