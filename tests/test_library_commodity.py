"""data/bloomberg/library.py and inventory.py for commodity futures (commodity conversion
plan, Phase 1 step 2, 2026-09-24).

A non-USD future's P&L converts to USD at spot of the valuation date (user decision
2026-09-24), so the library lists that currency's USD pair SPOT as a CONVERSION need; a
future of a contract root needs Bloomberg's own contract dates until they are stored; and
a future whose root has no verified Bloomberg ticker is listed but never asked for.
"""
from __future__ import annotations

import pytest

from data.bloomberg import inventory, library
from data.contracts import store_static_dates
from data.ingest import schema

AS_OF = "2026-09-24"
_INSTRUMENT = ("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
               "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)")
_TRADE = "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
_LEG = "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)"


def _db(tmp_path):
    """A CNY future (SHFE rebar), a EUR future, a USD future (WTI), a future of a
    placeholder root (SHFE stainless, no Bloomberg ticker) and the macro book's ES future."""
    conn = schema.connect(tmp_path / "risk.db")
    conn.executemany(_INSTRUMENT, [
        ("RBTF27 Comdty", "FUTURE", "SHFE:RB", "CNY", 10, 0, "RBTF7 Comdty", "2027-01-15"),
        ("TFMZ26 Comdty", "FUTURE", "ICE:TFM", "EUR", 720, 0, "TFMZ6 Comdty", "2026-11-27"),
        ("CLZ26 Comdty", "FUTURE", "NYMEX:CL", "USD", 1000, 0, "CLZ6 Comdty", "2026-11-19"),
        ("ZZSSF27 Comdty", "FUTURE", "SHFE:SS", "CNY", 5, 0, "", "2027-01-15"),
        ("ESZ6 Index", "FUTURE", "ES", "USD", 50, 0, "ESZ6 Index", "2026-12-18"),
    ])
    trades = [("rb1", "RBTF27 Comdty", "2026-09-01", 10, 3200.0, "2027-01-15"),
              ("tf1", "TFMZ26 Comdty", "2026-09-02", -3, 35.5, "2026-11-27"),
              ("cl1", "CLZ26 Comdty", "2026-09-03", 2, 68.4, "2026-11-19"),
              ("ss1", "ZZSSF27 Comdty", "2026-09-04", 4, 13500.0, "2027-01-15"),
              ("es1", "ESZ6 Index", "2026-09-05", 1, 6500.0, "2026-12-18")]
    for trade_id, instrument, trade_date, qty, px, expiry in trades:
        conn.execute(_TRADE, (trade_id, "XLSX", instrument, "FUTURE", trade_id, trade_date, qty, px,
                              "acc", "cp", "", "t", "d", ""))
        conn.execute(_LEG, (trade_id, 1, "NOTIONAL", "USD", qty * px, trade_date, expiry, px, 0))
    conn.commit()
    return conn


def _rows(conn, trade_id):
    return {(r["kind"], r["key"], r["role"]): r for r in library.rows(conn) if r["trade_id"] == trade_id}


def test_a_cny_future_needs_the_usdcny_spot_as_conversion_until_expiry(tmp_path):
    conn = _db(tmp_path)
    rb = _rows(conn, "rb1")
    conv = rb[("SPOT", "USDCNY", library.ROLE_CONVERSION)]
    assert (conv["needed_from"], conv["needed_until"], conv["bbg_ticker"]) == ("2026-09-01", "2027-01-15", "USDCNY Curncy")
    assert conv["product"] == "FUTURE" and conv["requestable"]
    # the pair's instrument row exists once the library is synced, so the pull can write it
    assert conn.execute("SELECT 1 FROM instruments WHERE instrument_id = 'USDCNY'").fetchone()
    # a EUR future converts on the pair the rest of the app quotes (EURUSD, never USDEUR)
    assert ("SPOT", "EURUSD", library.ROLE_CONVERSION) in _rows(conn, "tf1")
    # the live list and the past-close list both carry it (the backfill fetches its closes)
    assert ("USDCNY", "SPOT") in {(r["key"], r["kind"]) for r in library.needed_on(conn, AS_OF)}
    past = library.needed_in_range(conn, "2026-09-10", "2026-09-11")
    assert any(r["key"] == "USDCNY" and r["role"] == library.ROLE_CONVERSION for r in past)
    # nothing is asked after expiry
    assert not any(r["key"] == "USDCNY" for r in library.needed_on(conn, "2027-01-18"))
    # the inventory counts it among what is needed
    assert {"instrument_id": "USDCNY", "settle_date": AS_OF, "mark_type": "SPOT"} in inventory._needed_marks(conn, AS_OF)


def test_a_usd_future_needs_no_conversion_and_the_macro_future_is_unchanged(tmp_path):
    conn = _db(tmp_path)
    assert not [k for k in _rows(conn, "cl1") if k[2] == library.ROLE_CONVERSION]
    # ES (base_ccy 'ES', not a contract root): only its price, as before
    assert set(_rows(conn, "es1")) == {("FUTURE_PX", "ESZ6 Index", library.ROLE_PAIR)}


def test_a_future_without_bloomberg_dates_needs_contract_dates_until_they_are_stored(tmp_path):
    conn = _db(tmp_path)
    cl = _rows(conn, "cl1")[(library.CONTRACT_DATES, "CLZ26 Comdty", library.ROLE_PAIR)]
    assert (cl["settle_date"], cl["bbg_ticker"], cl["product"]) == (library.SENTINEL, "CLZ6 Comdty", "FUTURE")
    assert (cl["needed_from"], cl["needed_until"]) == ("2026-09-03", "2026-11-19")
    assert [e["contract_id"] for e in library.contract_dates_needed(conn, AS_OF)] == [
        "CLZ26 Comdty", "RBTF27 Comdty", "TFMZ26 Comdty"]            # never the placeholder, never ES
    assert library.contract_dates_needed(conn, AS_OF)[0]["fields"] == ("FUT_LAST_TRADE_DT", "FUT_NOTICE_FIRST")
    listed = {(t["ticker"], t["field"]): t for t in library.tickers(conn, AS_OF)}
    entry = listed[("CLZ6 Comdty", "FUT_LAST_TRADE_DT, FUT_NOTICE_FIRST")]
    assert entry["used_for"] == "CLZ26 Comdty contract dates (expiry and first notice)" and entry["requestable"]
    # today's pull only: a past close never asks for them
    assert not any(r["kind"] == library.CONTRACT_DATES for r in library.needed_in_range(conn, "2026-09-10", "2026-09-11"))

    store_static_dates(conn, [{"contract_id": "CLZ26 Comdty", "last_trade_date": "2026-11-19",
                               "first_notice_date": "2026-11-20", "source": "BBG_BDP"}])
    # the need is met: no longer asked for, with no change to the library itself
    assert not library.is_out_of_date(conn)
    assert [e["contract_id"] for e in library.contract_dates_needed(conn, AS_OF)] == ["RBTF27 Comdty", "TFMZ26 Comdty"]
    assert ("CLZ6 Comdty", "FUT_LAST_TRADE_DT, FUT_NOTICE_FIRST") not in {
        (t["ticker"], t["field"]) for t in library.tickers(conn, AS_OF)}
    inv = inventory.contract_dates_inventory(conn, AS_OF).set_index("contract_id")
    assert inv.loc["CLZ26 Comdty", "status"] == inventory.STATUS_ON_FILE
    assert inv.loc["CLZ26 Comdty", "first_notice_date"] == "2026-11-20"
    assert inv.loc["RBTF27 Comdty", "status"] == inventory.STATUS_MISSING and inv.loc["RBTF27 Comdty", "reason"] == ""
    assert inv.loc["ZZSSF27 Comdty", "reason"] == "no verified Bloomberg ticker for SHFE:SS"
    assert "ESZ6 Index" not in inv.index


def test_a_placeholder_ticker_future_is_listed_but_never_requestable(tmp_path):
    conn = _db(tmp_path)
    ss = _rows(conn, "ss1")
    px = ss[("FUTURE_PX", "ZZSSF27 Comdty", library.ROLE_PAIR)]
    assert px["bbg_ticker"] == "" and px["requestable"] is False
    assert px["reason"] == "no verified Bloomberg ticker for SHFE:SS"
    assert ss[(library.CONTRACT_DATES, "ZZSSF27 Comdty", library.ROLE_PAIR)]["requestable"] is False
    conv = ss[("SPOT", "USDCNY", library.ROLE_CONVERSION)]
    assert conv["requestable"]                     # its conversion spot has a ticker
    # what a pull or the backfill reads never carries it
    assert not any(r["key"] == "ZZSSF27 Comdty" for r in library.needed_on(conn, AS_OF))
    assert not any(r["key"] == "ZZSSF27 Comdty" for r in library.needed_in_range(conn, "2026-09-10", "2026-09-11"))
    assert not any(not r["bbg_ticker"] for r in library.needed_on(conn, AS_OF)
                   if r["kind"] not in library.SET_KINDS)
    # ... but the listings show the gap
    assert any(r["key"] == "ZZSSF27 Comdty" for r in library.needed_on(conn, AS_OF, include_unrequestable=True))
    gaps = [t for t in library.tickers(conn, AS_OF) if not t["requestable"]]
    assert {t["field"] for t in gaps} == {"PX_LAST", "FUT_LAST_TRADE_DT, FUT_NOTICE_FIRST"}
    assert all(t["ticker"] == "" and t["reason"] == "no verified Bloomberg ticker for SHFE:SS" for t in gaps)
    assert any(t["used_for"] == ("ZZSSF27 Comdty futures price -- not asked of Bloomberg: "
                                 "no verified Bloomberg ticker for SHFE:SS") for t in gaps)
    summary = library.summary(conn, AS_OF)
    assert summary["not_requestable"] == 2
    assert summary["tickers"] == sum(1 for t in library.tickers(conn, AS_OF) if t["requestable"])
    # the Market data inventory lists it as missing, with the reason
    inv = inventory.mark_inventory(conn, AS_OF)
    row = inv[inv["instrument_id"] == "ZZSSF27 Comdty"].iloc[0]
    assert (row["status"], row["reason"]) == (inventory.STATUS_MISSING, "no verified Bloomberg ticker for SHFE:SS")
    assert (inv[inv["instrument_id"] != "ZZSSF27 Comdty"]["reason"] == "").all()
    # a past close's completeness counts only what can be asked for; the gap is listed apart
    day = inventory.close_completeness(conn, "2026-09-10", "2026-09-10", today=AS_OF).iloc[0]
    assert not any(m["instrument_id"] == "ZZSSF27 Comdty" for m in day["missing"])
    assert day["not_requestable"] == [{"instrument_id": "ZZSSF27 Comdty", "settle_date": "2027-01-15",
                                       "mark_type": "FUTURE_PX", "reason": "no verified Bloomberg ticker for SHFE:SS"}]


def test_the_version_is_bumped_so_an_existing_library_resyncs_with_the_conversion_spots(tmp_path):
    conn = _db(tmp_path)
    assert library.LIBRARY_VERSION != "2026-09-22.2"
    library.sync(conn)
    # the library an older code wrote: no conversion spot for a future, no contract dates
    conn.execute("DELETE FROM bbg_library WHERE product = 'FUTURE' AND (role = 'CONVERSION' OR kind = 'CONTRACT_DATES')")
    conn.execute("UPDATE bbg_library_state SET dirty = 0, code_version = '2026-09-22.2'")
    conn.commit()
    assert library.is_out_of_date(conn)
    assert ("SPOT", "USDCNY", library.ROLE_CONVERSION) in _rows(conn, "rb1")          # a read resynced it
    assert (library.CONTRACT_DATES, "RBTF27 Comdty", library.ROLE_PAIR) in _rows(conn, "rb1")
    assert not library.is_out_of_date(conn)


@pytest.mark.parametrize("base, expected", [("NYMEX:CL", True), ("SHFE:RB", True), ("ES", False),
                                            ("USD", False), ("", False), (None, False)])
def test_is_contract_root(base, expected):
    assert library.is_contract_root(base) is expected
