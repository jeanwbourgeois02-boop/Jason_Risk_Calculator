"""The NDF "present spot" exception is retired (user decision 2026-09-22, reversing 2026-09-21's
"we can use a present spot for the past fixes"): engine/pnl/ledger.realise_settled has no
`ndf_present_spot`, engine/pnl/valuation no `present_spot_for_ndf`, and an NDF with no close on or
before its fixing takes the standard rule (`valuation.ndf_fixed_valuation`: the exact-day NDF_FIX,
else the fixing date's SPOT as the near-marks estimate, named; with nothing on file at all, blank
with its reason like a deliverable trade). A row the retired path froze on an existing database
is dropped by the ledger's re-freeze rule and frozen again by the standard one."""
import math

import pytest

from data.ingest import schema
from engine.pnl import ledger, valuation
from engine.pnl.valuation import value_book

AS_OF = "2026-09-21"
RETIRED_NOTE = "spot dated 2026-09-21 (present spot: none on file on or before settlement)"


def _load(conn, brl_closes=(("2026-09-18", 5.30), (AS_OF, 5.40))):
    conn.executemany(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, "
        "expiry_date) VALUES (?,?,?,?,?,?,?,?)",
        [("USDBRL", "FX", "USD", "BRL", 1, 1, "USDBRL Curncy", "9999-12-31"),
         ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31")])
    # b1: bought 1m USD against BRL @ 5.20, j1: bought 1m USD against JPY @ 150; both value Wed
    # 09-16 -- b1 is an NDF, fixing Mon 09-14; the only closes on file are AFTER the fixing.
    for trade_id, pair, fill in (("b1", "USDBRL", 5.20), ("j1", "USDJPY", 150.0)):
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (trade_id, "XLSX", pair, "FX_FWD", trade_id, "2026-08-14", 1e6, fill, "acc", "cp", "", "t", "d", ""))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
            (trade_id, 1, "FX_NEAR", "USD", 1e6, "2026-08-14", "2026-09-16", fill, 0),
            (trade_id, 2, "FX_NEAR", pair[3:], -1e6 * fill, "2026-08-14", "2026-09-16", fill, 0)])
    marks = [(d, "USDBRL", d, "SPOT", v) for d, v in brl_closes]
    marks += [("2026-09-18", "USDJPY", "2026-09-18", "SPOT", 152.0), (AS_OF, "USDJPY", AS_OF, "SPOT", 153.0)]
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                     [m + ("BBG_BFXFORWARD", f"{m[0]}T15:00:00-04:00") for m in marks])
    conn.commit()
    return conn


def _frozen(conn):
    return {r[0]: r for r in conn.execute("SELECT trade_id, pnl_usd, spot_as_of_date, mark_type, note FROM realised_pnl")}


def test_the_present_spot_path_is_gone():
    conn = _load(schema.connect())
    with pytest.raises(TypeError):
        ledger.realise_settled(conn, AS_OF, ndf_present_spot=True)
    for name in ("present_spot_for_ndf", "PRESENT_SPOT_NOTE", "ndf_fixing_marks_on_file"):
        assert not hasattr(valuation, name)
    for name in ("purge_superseded_present_spot", "purge_superseded_ndf_fix"):
        assert not hasattr(ledger, name)


def test_an_ndf_with_closes_only_after_its_fixing_takes_the_near_marks_estimate_and_says_so():
    """The standard rule: `ndf_fix` reads the fixing date's SPOT, estimated from the nearest
    close when the day's own is not on file -- here the later 09-18 close, none earlier -- and
    the P&L converts at that same price. The Blotter shows it from the fixing on and the ledger
    records the same figure; a deliverable pair in the same position stays blank."""
    conn = _load(schema.connect())
    est_src = "INTERP: SPOT of 2026-09-18 (nearest later close, none earlier)"
    book = value_book(conn, AS_OF).set_index("trade_id")
    assert book.loc["b1", "pnl_usd"] == pytest.approx(1e6 * (5.30 - 5.20) / 5.30)
    assert (book.loc["b1", "mark"], book.loc["b1", "mark_source"], book.loc["b1", "mark_date"]) == (5.30, est_src, "2026-09-14")
    assert book.loc["b1", "note"] == (f"NDF fixed 2026-09-14: no official fixing on file: at the spot of 2026-09-14 instead "
                                      f"({est_src}), converted at that spot, no delta, no carry; not yet recorded in realised_pnl")
    assert math.isnan(book.loc["j1", "pnl_usd"]) and "no official mark on or before" in book.loc["j1", "reason"]
    res = ledger.realise_settled(conn, AS_OF)
    assert res["realised"] == 1 and [u["trade_id"] for u in res["unrealisable"]] == ["j1"] and res["refrozen"] == []
    b1 = _frozen(conn)["b1"]
    assert b1[1] == pytest.approx(1e6 * (5.30 - 5.20) / 5.30) and b1[2:4] == ("2026-09-14", "SPOT")
    assert b1[4] == (f"spot dated 2026-09-14 (NDF fixing), converted at that spot; no official fixing on file: "
                     f"at the spot of 2026-09-14 instead ({est_src})")
    assert value_book(conn, AS_OF).set_index("trade_id").loc["b1", "pnl_usd"] == pytest.approx(b1[1])
    # a newer close never moves it; the fixing date's own close or fix does, through the same rule
    conn.execute("INSERT INTO marks VALUES ('2026-09-22','USDBRL','2026-09-22','SPOT',5.60,'BBG_BFXFORWARD','2026-09-22T15:00:00-04:00')")
    conn.commit()
    assert ledger.realise_settled(conn, "2026-09-22")["refrozen"] == []
    conn.execute("INSERT INTO marks VALUES ('2026-09-14','USDBRL','2026-09-14','NDF_FIX',5.22,'BBG_BDH','2026-09-14T17:00:00-04:00')")
    conn.commit()
    assert ledger.realise_settled(conn, "2026-09-22")["refrozen"] == ["b1"]
    b1 = _frozen(conn)["b1"]
    assert b1[1] == pytest.approx(1e6 * (5.22 - 5.20) / 5.22) and b1[2:4] == ("2026-09-14", "NDF_FIX")


def test_a_row_the_retired_path_froze_is_dropped_and_frozen_again_by_the_standard_rule():
    conn = _load(schema.connect())
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, usd_entry_amount, "
        "mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("b1", "USDBRL", "FX_FWD", "BRL", "2026-09-16", 1e6, 1e6 * 5.20 / 5.40, "SPOT", 5.40 / 5.40, AS_OF,
         "BBG_BFXFORWARD", 1e6 * (5.40 - 5.20) / 5.40, "2026-09-21T17:00:00-04:00", RETIRED_NOTE))
    conn.commit()
    assert RETIRED_NOTE in value_book(conn, AS_OF).set_index("trade_id").loc["b1", "note"]   # read back as stored, until the ledger runs
    res = ledger.realise_settled(conn, "2026-09-22")
    assert res["refrozen"] == ["b1"] and res["realised"] == 1
    b1 = _frozen(conn)["b1"]
    assert b1[2:4] == ("2026-09-14", "SPOT") and "present spot" not in b1[4]
    assert b1[1] == pytest.approx(1e6 * (5.30 - 5.20) / 5.30)


def test_an_ndf_with_no_close_of_its_pair_at_all_is_blank_with_its_reason_like_a_deliverable_trade():
    conn = _load(schema.connect(), brl_closes=())
    book = value_book(conn, AS_OF).set_index("trade_id")
    assert math.isnan(book.loc["b1", "pnl_usd"]) and math.isnan(book.loc["j1", "pnl_usd"])
    assert "no official mark on or before its settlement" in book.loc["b1", "reason"]
    res = ledger.realise_settled(conn, AS_OF)
    reasons = {u["trade_id"]: u["reason"] for u in res["unrealisable"]}
    assert res["realised"] == 0 and set(reasons) == {"b1", "j1"}
    assert reasons["b1"] == "no official fixing and no SPOT mark for USDBRL on 2026-09-14 (NDF fixed that day)"
    assert _frozen(conn) == {}
