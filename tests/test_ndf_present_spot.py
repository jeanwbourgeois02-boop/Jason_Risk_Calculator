"""A settled NDF with no past fix on file takes a present spot (user decision 2026-09-21: "we
can use a present spot for the past fixes"): engine/pnl/ledger.realise_settled
(`ndf_present_spot`), engine/pnl/valuation.present_spot_for_ndf, and the backfill's hook."""
import math

import pytest

from data.bloomberg import backfill
from data.ingest import schema
from engine.pnl import ledger
from engine.pnl.valuation import PRESENT_SPOT_NOTE, value_book

AS_OF = "2026-09-21"


def _load(conn, past_brl_spot=None):
    conn.executemany(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, "
        "expiry_date) VALUES (?,?,?,?,?,?,?,?)",
        [("USDBRL", "FX", "USD", "BRL", 1, 1, "USDBRL Curncy", "9999-12-31"),
         ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31")])
    # b1: bought 1m USD against BRL @ 5.20, j1: bought 1m USD against JPY @ 150; both settled Wed
    # 09-16 -- b1 is an NDF, so its freeze reads the spot on or before its FIXING, Mon 09-14
    # (user, 2026-09-22: an NDF is done at its fixing), j1's the spot on or before 09-16
    for trade_id, pair, fill in (("b1", "USDBRL", 5.20), ("j1", "USDJPY", 150.0)):
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (trade_id, "XLSX", pair, "FX_FWD", trade_id, "2026-08-14", 1e6, fill, "acc", "cp", "", "t", "d", ""))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
            (trade_id, 1, "FX_NEAR", "USD", 1e6, "2026-08-14", "2026-09-16", fill, 0),
            (trade_id, 2, "FX_NEAR", pair[3:], -1e6 * fill, "2026-08-14", "2026-09-16", fill, 0)])
    marks = [("2026-09-18", "USDBRL", "2026-09-18", "SPOT", 5.30), (AS_OF, "USDBRL", AS_OF, "SPOT", 5.40),
             ("2026-09-18", "USDJPY", "2026-09-18", "SPOT", 152.0), (AS_OF, "USDJPY", AS_OF, "SPOT", 153.0)]
    if past_brl_spot is not None:
        marks.append(("2026-09-14", "USDBRL", "2026-09-14", "SPOT", past_brl_spot))
        marks.append(("2026-09-15", "USDBRL", "2026-09-15", "SPOT", 9.99))   # after the fixing: never the freeze
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                     [m + ("BBG_BFXFORWARD", f"{m[0]}T15:00:00-04:00") for m in marks])
    conn.commit()
    return conn


def _frozen(conn):
    return {r[0]: r for r in conn.execute("SELECT trade_id, pnl_usd, spot_as_of_date, note FROM realised_pnl")}


def test_the_live_pulls_own_call_still_leaves_the_ndf_unrealised():
    conn = _load(schema.connect())
    res = ledger.realise_settled(conn, AS_OF)
    assert res["realised"] == 0 and sorted(u["trade_id"] for u in res["unrealisable"]) == ["b1", "j1"]


def test_after_the_backfill_the_ndf_freezes_once_at_the_present_spot_and_a_deliverable_pair_does_not():
    conn = _load(schema.connect())
    res = ledger.realise_settled(conn, AS_OF, ndf_present_spot=True)
    assert res["realised"] == 1 and [u["trade_id"] for u in res["unrealisable"]] == ["j1"]
    b1 = _frozen(conn)["b1"]
    assert b1[1] == pytest.approx(1e6 * (5.40 - 5.20) / 5.40)      # Q x (m - f), converted at the same spot
    assert b1[2] == AS_OF and PRESENT_SPOT_NOTE in b1[3]
    # frozen: a newer spot never moves it
    conn.execute("INSERT INTO marks VALUES ('2026-09-22','USDBRL','2026-09-22','SPOT',5.60,'BBG_BFXFORWARD','2026-09-22T15:00:00-04:00')")
    assert ledger.realise_settled(conn, "2026-09-22", ndf_present_spot=True)["realised"] == 0
    assert _frozen(conn)["b1"][1] == pytest.approx(1e6 * (5.40 - 5.20) / 5.40)


def test_the_true_close_replaces_the_present_spot_once_it_lands():
    conn = _load(schema.connect())
    ledger.realise_settled(conn, AS_OF, ndf_present_spot=True)
    assert PRESENT_SPOT_NOTE in _frozen(conn)["b1"][3]
    conn.execute("INSERT INTO marks VALUES ('2026-09-16','USDBRL','2026-09-16','SPOT',9.99,'BBG_BFXFORWARD','2026-09-16T17:00:00-04:00')")
    assert ledger.realise_settled(conn, "2026-09-22")["realised"] == 0   # a spot after the fixing is not the fix
    conn.execute("INSERT INTO marks VALUES ('2026-09-14','USDBRL','2026-09-14','SPOT',5.25,'BBG_BFXFORWARD','2026-09-14T17:00:00-04:00')")
    assert ledger.realise_settled(conn, "2026-09-22")["realised"] == 1   # any later call, the live pull's included
    b1 = _frozen(conn)["b1"]
    assert b1[1] == pytest.approx(1e6 * (5.25 - 5.20) / 5.25) and b1[2] == "2026-09-14" and b1[3] == "spot dated 2026-09-14 (NDF fixing)"
    assert ledger.realise_settled(conn, "2026-09-22", ndf_present_spot=True)["realised"] == 0   # and it stays there


def test_a_spot_on_or_before_settlement_always_wins():
    conn = _load(schema.connect(), past_brl_spot=5.25)
    ledger.realise_settled(conn, AS_OF, ndf_present_spot=True)
    b1 = _frozen(conn)["b1"]
    assert b1[1] == pytest.approx(1e6 * (5.25 - 5.20) / 5.25) and b1[2] == "2026-09-14"
    assert PRESENT_SPOT_NOTE not in b1[3]


def test_value_book_prices_the_ndf_at_the_present_spot_and_says_so():
    conn = _load(schema.connect())
    book = value_book(conn, AS_OF).set_index("trade_id")
    assert book.loc["b1", "pnl_usd"] == pytest.approx(1e6 * (5.40 - 5.20) / 5.40)
    assert PRESENT_SPOT_NOTE in book.loc["b1", "note"] and AS_OF in book.loc["b1", "note"]
    assert math.isnan(book.loc["j1", "pnl_usd"]) and "no official mark on or before" in book.loc["j1", "reason"]
    # on an earlier date it is the latest spot on or before THAT date
    earlier = value_book(conn, "2026-09-18").set_index("trade_id")
    assert earlier.loc["b1", "pnl_usd"] == pytest.approx(1e6 * (5.30 - 5.20) / 5.30)


def test_the_backfills_hook_is_what_freezes_it(tmp_path):
    db = tmp_path / "risk.db"
    _load(schema.connect(db)).close()
    lines = []
    backfill._freeze_ndfs_at_present_spot(db, backfill.date.fromisoformat(AS_OF), lines.append)
    conn = schema.connect(db)
    try:
        assert set(_frozen(conn)) == {"b1"} and any("1 settled trade" in line for line in lines)
    finally:
        conn.close()
