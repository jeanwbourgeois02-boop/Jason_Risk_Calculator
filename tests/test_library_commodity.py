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


# --------------------------------------------------------------------------- Phase 5
# Options on commodity futures (CMDTY_OPTION) and LME forwards (LME_FWD), 2026-09-24: what
# each needs from Bloomberg, and until when.

def _phase5_db(tmp_path):
    """A WTI call (USD) on CLZ26, a SHFE copper call (CNY) on CUZ26 whose underlying is
    booked as an instrument only, an expired COMEX gold call, an LME copper ticket (prompt
    2026-12-10) and an LME nickel ticket whose prompt (2026-09-16) has passed."""
    conn = schema.connect(tmp_path / "risk.db")
    conn.executemany(_INSTRUMENT, [
        ("CLZ26C 75 Comdty", "CMDTY_OPTION", "NYMEX:CL", "USD", 1000, 0, "CLZ6C 75 Comdty", "2026-11-17"),
        ("CLZ26 Comdty", "FUTURE", "NYMEX:CL", "USD", 1000, 0, "CLZ6 Comdty", "2026-11-19"),
        ("CUZ26C 80000 Comdty", "CMDTY_OPTION", "SHFE:CU", "CNY", 5, 0, "CUZ6C 80000 Comdty", "2026-11-24"),
        ("CUZ26 Comdty", "FUTURE", "SHFE:CU", "CNY", 5, 0, "CUZ6 Comdty", "2026-12-15"),
        ("GCQ26C 3300 Comdty", "CMDTY_OPTION", "COMEX:GC", "USD", 100, 0, "GCQ6C 3300 Comdty", "2026-07-28"),
        ("GCQ26 Comdty", "FUTURE", "COMEX:GC", "USD", 100, 0, "GCQ6 Comdty", "2026-08-27"),
        ("LME:CA", "LME_FWD", "LME:CA", "USD", 1, 0, "LMCADY Comdty", "9999-12-31"),
        ("LME:NI", "LME_FWD", "LME:NI", "USD", 1, 0, "LMNIDY Comdty", "9999-12-31"),
    ])
    options = [("wti", "CLZ26C 75 Comdty", "2026-08-24", 10, 2.15, "USD", "2026-11-17"),
               ("cu", "CUZ26C 80000 Comdty", "2026-09-07", 4, 1850.0, "CNY", "2026-11-24"),
               ("gold", "GCQ26C 3300 Comdty", "2026-07-06", 2, 45.2, "USD", "2026-07-28")]
    for trade_id, instrument, trade_date, qty, px, ccy, expiry in options:
        conn.execute(_TRADE, (trade_id, "XLSX", instrument, "CMDTY_OPTION", trade_id, trade_date, qty, px,
                              "acc", "cp", "", "t", "d", ""))
        conn.execute(_LEG, (trade_id, 1, "NOTIONAL", ccy, qty * px, trade_date, expiry, px, 0))
    for trade_id, root, trade_date, tonnes, px, prompt in (("ca", "LME:CA", "2026-09-10", 100.0, 9850.0, "2026-12-10"),
                                                           ("ni", "LME:NI", "2026-07-02", 12.0, 15420.0, "2026-09-16")):
        conn.execute(_TRADE, (trade_id, "XLSX", root, "LME_FWD", trade_id, trade_date, tonnes, px,
                              "acc", "cp", "", "t", "d", ""))
        conn.execute(_LEG, (trade_id, 1, "FX_NEAR", root, tonnes, trade_date, prompt, px, 0))
        conn.execute(_LEG, (trade_id, 2, "FX_NEAR", "USD", -tonnes * px, trade_date, prompt, px, 1))
    conn.commit()
    return conn


def _kinds(conn, trade_id):
    return {(r["kind"], r["key"], r["settle_date"], r["role"]): r for r in library.rows(conn) if r["trade_id"] == trade_id}


def _asked(conn, as_of):
    from data.bloomberg import live
    return {(r.instrument_id, r.mark_type, r.settle_date, r.bbg_ticker) for r in live.build_requests(conn, as_of)}


def test_an_option_on_a_future_needs_its_price_conversion_underlying_curve_and_dates(tmp_path):
    conn = _phase5_db(tmp_path)
    wti = _kinds(conn, "wti")
    assert set(wti) == {
        ("FUTURE_PX", "CLZ26C 75 Comdty", "2026-11-17", library.ROLE_PAIR),
        ("FUTURE_PX", "CLZ26 Comdty", "2026-11-19", library.ROLE_UNDERLYING),   # at the future's own expiry
        ("OIS_CURVE", "USD", library.SENTINEL, library.ROLE_PAIR),
        (library.CONTRACT_DATES, "CLZ26C 75 Comdty", library.SENTINEL, library.ROLE_PAIR)}
    assert wti[("FUTURE_PX", "CLZ26C 75 Comdty", "2026-11-17", library.ROLE_PAIR)]["bbg_ticker"] == "CLZ6C 75 Comdty"
    und = wti[("FUTURE_PX", "CLZ26 Comdty", "2026-11-19", library.ROLE_UNDERLYING)]
    assert (und["bbg_ticker"], und["needed_until"]) == ("CLZ6 Comdty", "2026-11-17")    # until the OPTION expires
    assert all(r["requestable"] for r in wti.values())
    # a CNY option: its conversion spot, and USD SOFR (no CNY OIS curve is in scope)
    cu = _kinds(conn, "cu")
    assert ("SPOT", "USDCNY", library.SENTINEL, library.ROLE_CONVERSION) in cu
    assert ("OIS_CURVE", "USD", library.SENTINEL, library.ROLE_PAIR) in cu
    assert ("FUTURE_PX", "CUZ26 Comdty", "2026-12-15", library.ROLE_UNDERLYING) in cu     # instrument-only underlying
    assert (library.option_discount_ccy("CNY"), library.option_discount_ccy("EUR")) == ("USD", "EUR")

    # today's pull: the option's price and, for the Greeks, the underlying's price
    assert {("CLZ26C 75 Comdty", "FUTURE_PX", "2026-11-17", "CLZ6C 75 Comdty"),
            ("CLZ26 Comdty", "FUTURE_PX", "2026-11-19", "CLZ6 Comdty"),
            ("USDCNY", "SPOT", AS_OF, "USDCNY Curncy")} <= _asked(conn, AS_OF)
    listed = {(t["ticker"], t["field"]): t for t in library.tickers(conn, AS_OF)}
    assert listed[("CLZ6C 75 Comdty", "PX_MID")]["used_for"] == "CLZ26C 75 Comdty listed option price"
    assert "underlying" in listed[("CLZ6 Comdty", "PX_LAST")]["used_for"]
    assert listed[("CLZ6C 75 Comdty", "OPT_EXPIRE_DT, LAST_TRADEABLE_DT")]["used_for"] == "CLZ26C 75 Comdty option expiry"
    assert library.keys(conn, AS_OF, "OIS_CURVE") == ["USD"]
    # a past close: the same, for the Greeks the backfill prices from that day's own inputs
    # (options-store, 2026-09-24); never the option's contract dates
    past = library.needed_in_range(conn, "2026-09-10", "2026-09-11")
    assert {(r["kind"], r["key"], r["role"]) for r in past if r["product"] == "CMDTY_OPTION"} == {
        ("FUTURE_PX", "CLZ26C 75 Comdty", library.ROLE_PAIR), ("FUTURE_PX", "CUZ26C 80000 Comdty", library.ROLE_PAIR),
        ("FUTURE_PX", "CLZ26 Comdty", library.ROLE_UNDERLYING), ("FUTURE_PX", "CUZ26 Comdty", library.ROLE_UNDERLYING),
        ("SPOT", "USDCNY", library.ROLE_CONVERSION), ("OIS_CURVE", "USD", library.ROLE_PAIR)}
    assert library.history_inputs_needed(conn, "2026-09-10") == [{"kind": "OIS_CURVE", "key": "USD"}]
    assert library.history_inputs_needed(conn, "2026-08-25") == [{"kind": "OIS_CURVE", "key": "USD"}]   # WTI alone
    assert library.history_inputs_needed(conn, "2026-08-21") == []                                      # none open
    day = inventory.close_completeness(conn, "2026-09-10", "2026-09-10", today=AS_OF).iloc[0]
    assert {"instrument_id": "CUZ26 Comdty", "settle_date": "2026-12-15", "mark_type": "FUTURE_PX"} in day["missing"]
    assert day["inputs_missing"] == [{"kind": "OIS_CURVE", "key": "USD"}]

    # the option's own expiry, asked with the option fields until Bloomberg's date is stored
    entries = {e["contract_id"]: e for e in library.contract_dates_needed(conn, AS_OF)}
    assert set(entries) == {"CLZ26C 75 Comdty", "CUZ26C 80000 Comdty"}
    assert entries["CLZ26C 75 Comdty"]["fields"] == library.OPTION_CONTRACT_DATES_FIELDS
    assert entries["CLZ26C 75 Comdty"]["product"] == "CMDTY_OPTION"
    store_static_dates(conn, [{"contract_id": "CLZ26C 75 Comdty", "last_trade_date": "2026-11-16", "source": "BBG_BDP"}])
    assert [e["contract_id"] for e in library.contract_dates_needed(conn, AS_OF)] == ["CUZ26C 80000 Comdty"]
    assert not library.is_out_of_date(conn)                          # met at read time, no resync


def test_an_expired_option_is_dropped_after_its_expiry(tmp_path):
    conn = _phase5_db(tmp_path)
    assert {r["key"] for r in library.needed_on(conn, "2026-07-28") if r["trade_id"] == "gold"} == {
        "GCQ26C 3300 Comdty", "GCQ26 Comdty", "USD"}                  # its expiry day: still asked
    assert not [r for r in library.needed_on(conn, "2026-07-29") if r["trade_id"] == "gold"]
    assert not [r for r in library.needed_on(conn, AS_OF) if r["trade_id"] == "gold"]
    assert "GCQ26 Comdty" not in {a[0] for a in _asked(conn, AS_OF)}
    # the WTI option stops on its own expiry, whatever its underlying's later expiry
    assert not [r for r in library.needed_on(conn, "2026-11-18") if r["trade_id"] == "wti"]


def test_an_lme_ticket_needs_cash_the_prompt_outright_and_the_curve_until_its_prompt(tmp_path):
    conn = _phase5_db(tmp_path)
    ca = _kinds(conn, "ca")
    assert set(ca) == {("SPOT", "LME:CA", library.SENTINEL, library.ROLE_PAIR),
                       ("FWD_OUTRIGHT", "LME:CA", "2026-12-10", library.ROLE_PAIR),
                       (library.LME_CURVE, "LME:CA", library.SENTINEL, library.ROLE_PAIR)}
    spot = ca[("SPOT", "LME:CA", library.SENTINEL, library.ROLE_PAIR)]
    assert (spot["bbg_ticker"], spot["needed_from"], spot["needed_until"]) == ("LMCADY Comdty", "2026-09-10", "2026-12-10")
    assert ca[("FWD_OUTRIGHT", "LME:CA", "2026-12-10", library.ROLE_PAIR)]["bbg_ticker"] == ""
    assert all(r["requestable"] and r["reason"] == "" for r in ca.values())      # a curve read, not a gap
    # the root id is never taken for an FX pair: no FX instrument, and the FX marks request
    # asks nothing of it (bbg-live's LME step writes the cash and the curve, 2026-09-24)
    assert conn.execute("SELECT asset_class FROM instruments WHERE instrument_id = 'LME:CA'").fetchone() == ("LME_FWD",)
    assert not {a for a in _asked(conn, AS_OF) if a[0] == "LME:CA"}
    assert {"instrument_id": "LME:CA", "settle_date": AS_OF, "mark_type": "SPOT"} in inventory._needed_marks(conn, AS_OF)
    assert library.keys(conn, AS_OF, library.LME_CURVE) == ["LME:CA"]
    # the curve's pillars, trimmed to the first one on or after the furthest prompt (plus cash and 3M)
    [curve] = library.lme_curves_needed(conn, AS_OF)
    assert (curve["root_id"], curve["needed_until"], curve["trades"]) == ("LME:CA", "2026-12-10", 1)
    assert [(p["kind"], p["pillar_date"]) for p in curve["pillars"]] == [
        ("CASH", "2026-09-28"), ("MONTHLY", "2026-10-21"), ("MONTHLY", "2026-11-18"), ("MONTHLY", "2026-12-16"),
        ("3M", "2026-12-24")]
    listed = {t["ticker"] for t in library.tickers(conn, AS_OF) if "LME:CA" in t["used_for"]}
    assert listed == {p["ticker"] for p in curve["pillars"]}           # the cash ticker once, no prompt security
    # past closes: left out of the backfill's FX paths unless it asks for the LME rows
    assert not [r for r in library.needed_in_range(conn, "2026-09-10", "2026-09-11") if library.is_lme_row(r)]
    lme = library.needed_in_range(conn, "2026-09-10", "2026-09-11", include_lme=True)
    assert {(r["kind"], r["key"]) for r in lme if r["trade_id"] == "ca"} == {
        ("SPOT", "LME:CA"), ("FWD_OUTRIGHT", "LME:CA"), (library.LME_CURVE, "LME:CA")}


def test_a_settled_prompt_is_dropped(tmp_path):
    conn = _phase5_db(tmp_path)
    assert {r["kind"] for r in library.needed_on(conn, "2026-09-16") if r["trade_id"] == "ni"} == {
        "SPOT", "FWD_OUTRIGHT", library.LME_CURVE}                    # the prompt day itself
    assert not [r for r in library.needed_on(conn, "2026-09-17") if r["trade_id"] == "ni"]
    assert library.keys(conn, AS_OF, library.LME_CURVE) == ["LME:CA"]
    assert "LME:NI" not in {a[0] for a in _asked(conn, AS_OF)}
    assert not [r for r in library.needed_in_range(conn, "2026-09-17", "2026-09-30", include_lme=True)
                if r["trade_id"] == "ni"]


def test_an_lme_metal_the_universe_does_not_know_is_a_listed_gap(tmp_path):
    conn = _phase5_db(tmp_path)
    conn.execute(_INSTRUMENT, ("LME:ZZ", "LME_FWD", "LME:ZZ", "USD", 1, 0, "", "9999-12-31"))
    conn.execute(_TRADE, ("zz", "XLSX", "LME:ZZ", "LME_FWD", "zz", "2026-09-10", 5.0, 100.0, "acc", "cp", "", "t", "d", ""))
    conn.execute(_LEG, ("zz", 1, "FX_NEAR", "LME:ZZ", 5.0, "2026-09-10", "2026-12-10", 100.0, 0))
    conn.commit()
    zz = _kinds(conn, "zz")
    assert {r["requestable"] for r in zz.values()} == {False}
    assert zz[(library.LME_CURVE, "LME:ZZ", library.SENTINEL, library.ROLE_PAIR)]["reason"] == (
        "LME:ZZ is not an LME metal of config/contracts.csv")
    assert zz[("SPOT", "LME:ZZ", library.SENTINEL, library.ROLE_PAIR)]["reason"] == "no verified Bloomberg ticker for LME:ZZ"
    assert "LME:ZZ" not in library.keys(conn, AS_OF, library.LME_CURVE)
    gaps = [t for t in library.tickers(conn, AS_OF) if not t["requestable"]]
    assert {t["field"] for t in gaps} == {"PX_LAST"} and all(t["ticker"] == "" for t in gaps)


def test_the_version_is_bumped_so_an_existing_library_gains_the_phase5_rows(tmp_path):
    conn = _phase5_db(tmp_path)
    assert library.LIBRARY_VERSION not in ("2026-09-24.1", "2026-09-24.2")
    library.sync(conn)
    conn.execute("DELETE FROM bbg_library WHERE product IN ('CMDTY_OPTION', 'LME_FWD')")   # what .2 wrote
    conn.execute("UPDATE bbg_library_state SET dirty = 0, code_version = '2026-09-24.2'")
    conn.commit()
    assert library.is_out_of_date(conn)
    found = library.rows(conn)                                        # a read resyncs it
    assert {r["kind"] for r in found if r["product"] == "LME_FWD"} == {"SPOT", "FWD_OUTRIGHT", library.LME_CURVE}
    assert any(r["role"] == library.ROLE_UNDERLYING for r in found)
    assert conn.execute("SELECT code_version FROM bbg_library_state").fetchone() == (library.LIBRARY_VERSION,)


def _mark(conn, day, instrument_id, settle, mark_type, stamp):
    conn.execute("INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
                 "VALUES (?,?,?,?,?,?,?)", (day, instrument_id, settle, mark_type, 9800.0, "BBG_BFXFORWARD", stamp))
    conn.commit()


def test_an_lme_curve_is_complete_once_its_cash_and_3m_are_on_file(tmp_path):
    conn = _phase5_db(tmp_path)
    day = "2026-09-23"                                                 # a past close
    three_m = next(p["settle_date"] for p in library.lme_curve_pillars("LME:CA", day) if p["kind"] == "3M")

    def curve_items():
        row = inventory.close_completeness(conn, day, day, today=AS_OF).iloc[0]
        return row, [m for m in row["missing"] if m["mark_type"] == inventory.LME_CURVE]

    row, items = curve_items()
    assert items == [{"instrument_id": "LME:CA", "settle_date": day, "mark_type": inventory.LME_CURVE,
                      "detail": "cash, 3M not on file"}]
    assert not row["complete"]
    # an LME close is the daily close, 17:00 New York (backfill.is_close_row given the root id)
    _mark(conn, day, "LME:CA", day, "SPOT", f"{day}T17:00:00-04:00")
    assert curve_items()[1][0]["detail"] == "3M not on file"
    # a live press's row of a past day is not the close, nor is the FX 15:00: still missing
    _mark(conn, day, "LME:CA", three_m, "FWD_OUTRIGHT", f"{day}T11:40:00-04:00")
    assert curve_items()[1][0]["detail"] == "3M not on file"
    conn.execute("UPDATE marks SET snapped_at = ? WHERE settle_date = ?", (f"{day}T15:00:00-04:00", three_m))
    conn.commit()
    assert curve_items()[1][0]["detail"] == "3M not on file"
    conn.execute("UPDATE marks SET snapped_at = ? WHERE settle_date = ?", (f"{day}T17:00:00-04:00", three_m))
    conn.commit()
    row, items = curve_items()
    assert items == []                                                 # cash and 3M at the close: complete
    assert inventory.lme_curve_status(conn, day, "LME:CA", today=AS_OF) == {
        "complete": True, "missing": [], "snapped_at": f"{day}T17:00:00-04:00"}
    # the cash SPOT, an ordinary needed mark, counts at the same 17:00 close
    assert not [m for m in row["missing"] if (m["instrument_id"], m["mark_type"]) == ("LME:CA", "SPOT")]
    # the prompt outright is still its own needed mark, read off the curve by the curves step
    assert {"instrument_id": "LME:CA", "settle_date": "2026-12-10", "mark_type": "FWD_OUTRIGHT"} in row["missing"]
    # today's inventory: one row per curve, OFFICIAL / MISSING by the same rule
    inv = inventory.mark_inventory(conn, AS_OF)
    curve = inv[inv["mark_type"] == inventory.LME_CURVE].iloc[0]
    assert (curve["instrument_id"], curve["status"], curve["source"]) == ("LME:CA", inventory.STATUS_MISSING,
                                                                          "missing: cash, 3M")
    _mark(conn, AS_OF, "LME:CA", AS_OF, "SPOT", f"{AS_OF}T10:00:00-04:00")
    _mark(conn, AS_OF, "LME:CA", next(p["settle_date"] for p in library.lme_curve_pillars("LME:CA", AS_OF)
                                      if p["kind"] == "3M"), "FWD_OUTRIGHT", f"{AS_OF}T10:00:00-04:00")
    inv = inventory.mark_inventory(conn, AS_OF)
    assert inv[inv["mark_type"] == inventory.LME_CURVE].iloc[0]["status"] == inventory.STATUS_OFFICIAL
