"""engine/pnl/valuation.py:

  1. The 2026-09-17 "no bnp fall back" user decision (`docs/bnp-excel-removal.md`):
     `value_book`'s old `marks_source='BNP_BVAL'` fallback path (retry an unpriced row
     against a non-official source, `_mark_at`'s old `source is not None` branch) was
     deleted outright that morning, initially leaving `marks_source` as an accepted-
     and-ignored parameter so not-yet-updated callers would not crash. That afternoon,
     once every caller across the repo was confirmed to never pass a non-default value,
     the parameter itself was removed from every function in this module (`value_book`,
     `usd_per_quote`, `_mark_at`, every row-builder helper) and from
     `engine/pnl/ledger.py`'s `ltd`/`period_pnl`/`period_pnl_by` and the `_mark_at` call
     in `engine/ladder/futures_delta.py`. A row with no official mark stays NaN with a
     reason, even when a non-official mark for the exact same (instrument, settle_date,
     mark_type) genuinely exists on file -- there is no argument left to even attempt
     retrying it.
  2. The 2026-09-17 complaint-B perf fix for the FX_FWD/FX_SPOT relabelling helper:
     `_business_days_between(..., cap=2)` short-circuits once the running count exceeds
     `cap` instead of walking the whole date range -- `_reported_product`'s <=2 boundary
     must land on exactly the same day as before.
"""
from __future__ import annotations

import datetime as dt

import pytest

from data.ingest import schema
from engine.pnl.valuation import _business_days_between, _mark_at, value_book


def _insert_instrument(conn, instrument_id, base_ccy, quote_ccy):
    conn.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,1,0,?,'9999-12-31')",
        (instrument_id, "FX", base_ccy, quote_ccy, f"{instrument_id} Curncy"),
    )


def _insert_trade(conn, trade_id, instrument_id, product, trade_date, quantity, price):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "XLSX", instrument_id, product, trade_id, trade_date, quantity, price,
         "acc", "cp", "HAHY7", "t", "d", ""),
    )


def _insert_legs(conn, rows):
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", rows)


def _insert_mark(conn, as_of, instrument_id, settle_date, mark_type, value, source, snapped_at):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (as_of, instrument_id, settle_date, mark_type, value, source, snapped_at))


def _one_open_fx_trade(conn, settle_date="2026-09-17"):
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", "2026-08-01", 1_000_000, 147.0)
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-01", settle_date, 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-08-01", settle_date, 147.0, 1),
    ])


# --------------------------------------------------------------------- no bnp fall back


def test_value_book_has_no_marks_source_parameter_any_more():
    """The parameter itself is gone (2026-09-17 afternoon), not merely inert: passing it
    is a TypeError, same as any other unknown keyword argument."""
    conn = schema.connect()
    with pytest.raises(TypeError):
        value_book(conn, "2026-09-17", marks_source="BNP_BVAL")


def test_non_official_mark_on_file_never_prices_a_row():
    """A BNP_BVAL row for exactly the (instrument, settle_date, mark_type) the trade
    needs is on file, but only an *official* mark prices a row, ever -- there is no
    parameter left to even attempt retrying a non-official source."""
    conn = schema.connect()
    _one_open_fx_trade(conn)
    _insert_mark(conn, "2026-08-18", "USDJPY", "2026-09-17", "FWD_OUTRIGHT", 145.0, "BNP_BVAL", "2026-08-18T15:00:00-04:00")
    _insert_mark(conn, "2026-08-18", "USDJPY", "2026-08-18", "SPOT", 148.0, "BNP_BVAL", "2026-08-18T15:00:00-04:00")
    conn.commit()

    df = value_book(conn, "2026-09-17")
    row = df[df["trade_id"] == "t1"].iloc[0]
    assert row["reason"] != ""
    assert row["pnl_usd"] != row["pnl_usd"]  # NaN
    assert row["mark_source"] != "BNP_BVAL"


def test_official_mark_still_prices_the_row_normally():
    """The removal must not touch the official path: a genuine BBG_BFXFORWARD mark
    still prices the row exactly as before."""
    conn = schema.connect()
    _one_open_fx_trade(conn)
    _insert_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD", "2026-09-17T17:00:00-04:00")
    _insert_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 149.0, "BBG_BFXFORWARD", "2026-09-17T17:00:00-04:00")
    conn.commit()

    df = value_book(conn, "2026-09-17")
    row = df[df["trade_id"] == "t1"].iloc[0]
    assert row["reason"] == ""
    assert row["mark"] == 148.0
    assert row["mark_source"] == "BBG_BFXFORWARD"
    # pnl_local = 1,000,000 * (148 - 147) = 1,000,000 JPY; pnl_usd = pnl_local / 149
    assert row["pnl_usd"] == 1_000_000 / 149.0


def test_mark_at_has_no_source_parameter_and_only_ever_reads_official():
    """Direct pin on `_mark_at` itself (other modules call it directly, e.g.
    `engine/ladder/futures_delta.py`): it takes no `source` argument at all any more,
    and a non-official mark on file for the exact key it looks up is never returned."""
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 149.0, "BBG_BFXFORWARD", "2026-09-17T17:00:00-04:00")
    _insert_mark(conn, "2026-08-18", "USDJPY", "2026-09-17", "FWD_OUTRIGHT", 145.0, "BNP_BVAL", "2026-08-18T15:00:00-04:00")
    conn.commit()

    with pytest.raises(TypeError):
        _mark_at(conn, "USDJPY", "2026-09-17", "SPOT", "2026-09-17", "BNP_BVAL")

    assert _mark_at(conn, "USDJPY", "2026-09-17", "SPOT", "2026-09-17") == (149.0, "BBG_BFXFORWARD")
    assert _mark_at(conn, "USDJPY", "2026-09-17", "FWD_OUTRIGHT", "2026-09-17") is None  # the BNP_BVAL row is never seen


# --------------------------------------------------------------------- FX_FWD/FX_SPOT relabel


def test_business_days_between_cap_matches_uncapped_count_at_the_boundary():
    holidays = frozenset()
    start = dt.date(2026, 9, 1)  # Tuesday
    for offset, expected in [(1, 1), (2, 2), (3, 3), (10, 8)]:
        end = start + dt.timedelta(days=offset)
        uncapped = _business_days_between(start, end, holidays)
        capped = _business_days_between(start, end, holidays, cap=2)
        assert capped == min(uncapped, 3) or capped == uncapped  # capped never undercounts <=2 cases
        if uncapped <= 2:
            assert capped == uncapped


def test_reported_product_relabel_boundary_unchanged_by_the_cap():
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    # Monday 2026-09-14 -> Wednesday 2026-09-16 is 2 business days -> relabelled FX_SPOT.
    _insert_trade(conn, "near", "USDJPY", "FX_FWD", "2026-09-14", 1_000_000, 147.0)
    _insert_legs(conn, [
        ("near", 1, "FX_NEAR", "USD", 1_000_000, "2026-09-14", "2026-09-16", 147.0, 1),
        ("near", 2, "FX_NEAR", "JPY", -147_000_000, "2026-09-14", "2026-09-16", 147.0, 1),
    ])
    # Monday 2026-09-14 -> a much later date -> still FX_FWD (well beyond the cap).
    _insert_trade(conn, "far", "USDJPY", "FX_FWD", "2026-09-14", 1_000_000, 147.0)
    _insert_legs(conn, [
        ("far", 1, "FX_NEAR", "USD", 1_000_000, "2026-09-14", "2027-03-14", 147.0, 1),
        ("far", 2, "FX_NEAR", "JPY", -147_000_000, "2026-09-14", "2027-03-14", 147.0, 1),
    ])
    conn.commit()
    df = value_book(conn, "2026-09-17")
    assert df[df["trade_id"] == "near"].iloc[0]["product"] == "FX_SPOT"
    assert df[df["trade_id"] == "far"].iloc[0]["product"] == "FX_FWD"


# --------------------------------------------------------------------- one bad value, one trade
# 2026-09-18 (Bloomberg PC: every Blotter view showed "could not convert string to float:
# '<a date>'" and no headline had a figure). SQLite keeps text it cannot convert as TEXT
# even in a REAL column; one such cell raised out of `value_book` and blanked everything
# built on it. Now it leaves ONE trade unpriced, with a reason naming the trade id, the
# table.column and the offending value. Nothing is substituted for the bad value.


def _two_pairs(conn):
    """t1: USDJPY forward (as `_one_open_fx_trade`); t2: EURUSD forward. Both open and
    fully marked on 2026-09-17."""
    _one_open_fx_trade(conn, settle_date="2026-10-20")
    _insert_instrument(conn, "EURUSD", "EUR", "USD")
    _insert_trade(conn, "t2", "EURUSD", "FX_FWD", "2026-08-01", 2_000_000, 1.10)
    _insert_legs(conn, [
        ("t2", 1, "FX_NEAR", "EUR", 2_000_000, "2026-08-01", "2026-10-20", 1.10, 1),
        ("t2", 2, "FX_NEAR", "USD", -2_200_000, "2026-08-01", "2026-10-20", 1.10, 1),
    ])
    stamp = "2026-09-17T17:00:00-04:00"
    _insert_mark(conn, "2026-09-17", "USDJPY", "2026-10-20", "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD", stamp)
    _insert_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 149.0, "BBG_BFXFORWARD", stamp)
    _insert_mark(conn, "2026-09-17", "EURUSD", "2026-10-20", "FWD_OUTRIGHT", 1.12, "BBG_INTERP", stamp)
    _insert_mark(conn, "2026-09-17", "EURUSD", "2026-09-17", "SPOT", 1.11, "BBG_BFXFORWARD", stamp)
    conn.commit()


def _by_id(conn, as_of="2026-09-17"):
    return value_book(conn, as_of).set_index("trade_id")


def test_clean_book_is_priced_exactly_as_before_the_guard():
    conn = schema.connect()
    _two_pairs(conn)
    vb = _by_id(conn)
    assert vb.loc["t1", "pnl_usd"] == 1_000_000 * (148.0 - 147.0) / 149.0
    assert vb.loc["t2", "pnl_usd"] == 2_000_000 * (1.12 - 1.10)
    assert list(vb["reason"]) == ["", ""]
    assert vb.loc["t1", "quantity"] == 1_000_000 and vb.loc["t1", "fill"] == 147.0


def test_text_in_trades_price_leaves_that_one_trade_unpriced_and_names_it():
    conn = schema.connect()
    _two_pairs(conn)
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 't1'")
    conn.commit()
    assert conn.execute("SELECT typeof(price) FROM trades WHERE trade_id = 't1'").fetchone()[0] == "text"
    vb = _by_id(conn)
    assert vb.loc["t1", "reason"] == "trade t1: trades.price is not a number ('24-Jul')"
    assert vb.loc["t1", "pnl_usd"] != vb.loc["t1", "pnl_usd"]          # NaN: never 0, never invented
    assert vb.loc["t1", "fill"] != vb.loc["t1", "fill"]                # shown as missing, not as the raw text
    assert vb.loc["t1", "quantity"] == 1_000_000 and vb.loc["t1", "status"] == "OPEN"
    assert vb.loc["t2", "reason"] == "" and vb.loc["t2", "pnl_usd"] == 2_000_000 * (1.12 - 1.10)


def test_text_in_trades_quantity_leaves_that_one_trade_unpriced_and_names_it():
    conn = schema.connect()
    _two_pairs(conn)
    conn.execute("UPDATE trades SET quantity = '2026-07-24' WHERE trade_id = 't2'")
    conn.commit()
    vb = _by_id(conn)
    assert vb.loc["t2", "reason"] == "trade t2: trades.quantity is not a number ('2026-07-24')"
    assert vb.loc["t2", "quantity"] != vb.loc["t2", "quantity"]
    assert vb.loc["t1", "reason"] == "" and vb.loc["t1", "pnl_usd"] == 1_000_000 / 149.0


def test_text_in_a_forward_mark_unprices_only_the_trades_that_need_that_mark():
    conn = schema.connect()
    _two_pairs(conn)
    conn.execute("UPDATE marks SET value = '24-Jul' WHERE instrument_id = 'USDJPY' AND mark_type = 'FWD_OUTRIGHT'")
    conn.commit()
    vb = _by_id(conn)
    assert vb.loc["t1", "reason"] == ("trade t1: marks.value (FWD_OUTRIGHT for USDJPY settle 2026-10-20 on "
                                      "2026-09-17) is not a number ('24-Jul')")
    assert vb.loc["t1", "pnl_usd"] != vb.loc["t1", "pnl_usd"]
    assert vb.loc["t2", "reason"] == "" and vb.loc["t2", "pnl_usd"] == 2_000_000 * (1.12 - 1.10)


def test_text_in_the_conversion_spot_unprices_the_trades_converted_with_it():
    conn = schema.connect()
    _two_pairs(conn)
    conn.execute("UPDATE marks SET value = '24-Jul' WHERE instrument_id = 'USDJPY' AND mark_type = 'SPOT'")
    conn.commit()
    vb = _by_id(conn)
    assert "marks.value (SPOT for USDJPY settle 2026-09-17 on 2026-09-17) is not a number ('24-Jul')" in vb.loc["t1", "reason"]
    assert vb.loc["t1", "reason"].startswith("trade t1: ")
    assert vb.loc["t2", "reason"] == ""


def test_text_in_a_pair_spot_used_only_for_the_carry_split_keeps_the_pnl():
    """EURUSD's own SPOT splits the P&L into spot and carry; the P&L itself needs only the
    outright (quote is USD). A bad one costs the split, named in `note`, not the P&L."""
    conn = schema.connect()
    _two_pairs(conn)
    conn.execute("UPDATE marks SET value = '24-Jul' WHERE instrument_id = 'EURUSD' AND mark_type = 'SPOT'")
    conn.commit()
    vb = _by_id(conn)
    assert vb.loc["t2", "reason"] == "" and vb.loc["t2", "pnl_usd"] == 2_000_000 * (1.12 - 1.10)
    assert "is not a number ('24-Jul')" in vb.loc["t2", "note"] and "carry split unavailable" in vb.loc["t2", "note"]


def _future(conn, multiplier=50):
    conn.execute("INSERT INTO instruments VALUES ('ESZ6 Index','FUTURE','ES','USD',?,0,'ESZ6 Index','2026-12-18')",
                 (multiplier,))
    _insert_trade(conn, "f1", "ESZ6 Index", "FUTURE", "2026-08-01", 3, 6000.0)
    _insert_legs(conn, [("f1", 1, "NOTIONAL", "USD", 900_000, "2026-08-01", "2026-12-18", 6000.0, 0)])
    _insert_mark(conn, "2026-09-17", "ESZ6 Index", "2026-12-18", "FUTURE_PX", 6100.0, "BBG_BDH", "2026-09-17T17:00:00-04:00")
    conn.commit()


def test_text_in_a_futures_multiplier_unprices_the_future_only():
    conn = schema.connect()
    _two_pairs(conn)
    _future(conn)
    assert _by_id(conn).loc["f1", "pnl_usd"] == 3 * 50 * (6100.0 - 6000.0)
    conn.execute("UPDATE instruments SET multiplier = '24-Jul' WHERE instrument_id = 'ESZ6 Index'")
    conn.commit()
    vb = _by_id(conn)
    assert vb.loc["f1", "reason"] == "trade f1: instruments.multiplier is not a number ('24-Jul')"
    assert vb.loc["f1", "pnl_usd"] != vb.loc["f1", "pnl_usd"]
    assert list(vb.loc[["t1", "t2"], "reason"]) == ["", ""]


def test_a_malformed_trade_date_unprices_that_trade_instead_of_raising():
    conn = schema.connect()
    _two_pairs(conn)
    conn.execute("UPDATE trades SET trade_date = '07/24/2026' WHERE trade_id = 't1'")
    conn.commit()
    vb = value_book(conn, "2026-09-17")
    # '07/24/2026' sorts before the as-of, so the trade is still selected; the relabel's date parse fails on it.
    bad = vb[vb["trade_id"] == "t1"].iloc[0]
    assert bad["reason"].startswith("trade t1: could not be valued (ValueError")
    assert vb[vb["trade_id"] == "t2"].iloc[0]["reason"] == ""


def test_settled_trade_whose_realised_row_is_unreadable_and_has_no_mark_names_the_row():
    """The misaligned `realised_pnl` row of the 2026-09-18 incident (`pnl_usd` holding a
    date), with no official mark on or before settlement to value the trade from instead:
    unpriced, reason naming the trade id, the column and the value -- never a raise."""
    conn = schema.connect()
    _two_pairs(conn)
    _insert_trade(conn, "old", "EURUSD", "FX_FWD", "2026-06-01", 1_000_000, 1.08)
    _insert_legs(conn, [
        ("old", 1, "FX_NEAR", "EUR", 1_000_000, "2026-06-01", "2026-07-24", 1.08, 1),
        ("old", 2, "FX_NEAR", "USD", -1_080_000, "2026-06-01", "2026-07-24", 1.08, 1),
    ])
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
        "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) "
        "VALUES ('old','EURUSD','FX_FWD','USD','2026-07-24',1000000,1080000,'SPOT',1.09,'2026-07-24','BBG_BFXFORWARD',"
        "'2026-07-24','t','')")
    conn.commit()
    vb = _by_id(conn)
    assert vb.loc["old", "pnl_usd"] != vb.loc["old", "pnl_usd"]
    assert "settled trade old" in vb.loc["old", "reason"]
    assert "realised_pnl.pnl_usd is not a number ('2026-07-24')" in vb.loc["old", "reason"]
    assert list(vb.loc[["t1", "t2"], "reason"]) == ["", ""]


# --------------------------------------------------------------------- non-USD futures
# User decision 2026-09-24, "Spot of valuation date" (CLAUDE.md "P&L conventions -> Futures"):
# a future's P&L is `contracts x multiplier x (m - f)` in its quote currency, converted to USD
# at spot of the valuation date like an FX row, so a CNY or EUR contract is never summed as
# dollars; a settled one at the spot of its expiry date (user decision 2026-09-24, C9: the last
# official spot on or before expiry, exact rows only), never of the date of the price it freezes
# at. A USD contract is exactly what it was.


def _commodity_future(conn, trade_id, instrument_id, quote_ccy, multiplier, expiry, quantity, fill):
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?, 'FUTURE', ?, ?, ?, 0, ?, ?)",
        (instrument_id, instrument_id[:2], quote_ccy, multiplier, instrument_id, expiry))
    _insert_trade(conn, trade_id, instrument_id, "FUTURE", "2026-08-03", quantity, fill)
    _insert_legs(conn, [(trade_id, 1, "NOTIONAL", quote_ccy, quantity * multiplier * fill, "2026-08-03",
                         expiry, fill, 0)])


_CLOSE = "2026-09-17T17:00:00-04:00"


def test_a_cny_future_is_converted_to_usd_at_spot_of_the_valuation_date():
    conn = schema.connect()
    _commodity_future(conn, "cu1", "CUZ6 Comdty", "CNY", 5, "2026-12-15", 10, 78_000.0)
    _commodity_future(conn, "eb1", "EBMZ6 Comdty", "EUR", 50, "2026-12-10", -4, 230.0)
    _insert_instrument(conn, "USDCNY", "USD", "CNY")
    _insert_instrument(conn, "EURUSD", "EUR", "USD")
    _insert_mark(conn, "2026-09-17", "CUZ6 Comdty", "2026-12-15", "FUTURE_PX", 78_500.0, "BBG_BDH", _CLOSE)
    _insert_mark(conn, "2026-09-17", "EBMZ6 Comdty", "2026-12-10", "FUTURE_PX", 228.5, "BBG_BDH", _CLOSE)
    _insert_mark(conn, "2026-09-17", "USDCNY", "2026-09-17", "SPOT", 7.10, "BBG_BFXFORWARD", _CLOSE)
    _insert_mark(conn, "2026-09-17", "EURUSD", "2026-09-17", "SPOT", 1.10, "BBG_BFXFORWARD", _CLOSE)
    conn.commit()
    vb = _by_id(conn)
    cu = vb.loc["cu1"]
    assert cu["pnl_local"] == 10 * 5 * (78_500.0 - 78_000.0) == 25_000.0          # CNY
    assert cu["pnl_usd"] == pytest.approx(25_000.0 / 7.10, rel=1e-15)            # USD per CNY = 1 / USDCNY
    assert cu["pnl_spot_usd"] == cu["pnl_usd"] and cu["pnl_carry_usd"] == 0.0
    assert (cu["spot"], cu["spot_source"]) == (pytest.approx(1 / 7.10, rel=1e-15), "BBG_BFXFORWARD")
    assert (cu["mark"], cu["mark_source"], cu["reason"]) == (78_500.0, "BBG_BDH", "")
    eb = vb.loc["eb1"]                                                             # EURUSD quoted direct
    assert eb["pnl_local"] == -4 * 50 * (228.5 - 230.0) == 300.0                  # EUR
    assert eb["pnl_usd"] == pytest.approx(300.0 * 1.10, rel=1e-15)
    assert (eb["spot"], eb["spot_source"], eb["reason"]) == (1.10, "BBG_BFXFORWARD", "")


def test_a_cny_future_with_no_usdcny_spot_anywhere_shows_its_local_pnl_and_no_usd_figure():
    conn = schema.connect()
    _commodity_future(conn, "cu1", "CUZ6 Comdty", "CNY", 5, "2026-12-15", 10, 78_000.0)
    _insert_mark(conn, "2026-09-17", "CUZ6 Comdty", "2026-12-15", "FUTURE_PX", 78_500.0, "BBG_BDH", _CLOSE)
    conn.commit()
    cu = _by_id(conn).loc["cu1"]
    assert (cu["mark"], cu["pnl_local"]) == (78_500.0, 25_000.0)
    assert cu["pnl_usd"] != cu["pnl_usd"] and cu["pnl_spot_usd"] != cu["pnl_spot_usd"]   # NaN: never 1, never 0
    assert cu["pnl_carry_usd"] != cu["pnl_carry_usd"]                                     # NaN too, as `_unpriced`
    assert cu["spot"] != cu["spot"] and cu["spot_source"] == ""
    assert cu["reason"] == "no SPOT for USD conversion of CNY on 2026-09-17"
    # no price either: no USD figure, and the spot column claims no identity conversion
    conn.execute("DELETE FROM marks")
    conn.commit()
    cu = _by_id(conn).loc["cu1"]
    assert cu["reason"] == "no FUTURE_PX mark for CUZ6 Comdty expiry 2026-12-15 on 2026-09-17"
    assert cu["spot"] != cu["spot"] and cu["spot_source"] == ""


def test_a_usd_future_is_exactly_what_it_was():
    conn = schema.connect()
    _future(conn)
    es = _by_id(conn).loc["f1"]
    pnl = 3 * 50 * (6100.0 - 6000.0)
    assert (es["pnl_local"], es["pnl_usd"], es["pnl_spot_usd"], es["pnl_carry_usd"]) == (pnl, pnl, pnl, 0.0)
    assert (es["spot"], es["spot_source"], es["reason"]) == (1.0, "identity", "")


def _settled_cny_future(conn):
    """cu1 expired 2026-09-15, last price on file dated 2026-09-11 (the reviewer's case); USDCNY
    7.00 on that price date, 7.20 the day before expiry, 7.40 on the expiry date itself and 7.10
    on the valuation date 2026-09-17."""
    _commodity_future(conn, "cu1", "CUZ6 Comdty", "CNY", 5, "2026-09-15", 10, 78_000.0)
    _insert_instrument(conn, "USDCNY", "USD", "CNY")
    _insert_mark(conn, "2026-09-11", "CUZ6 Comdty", "2026-09-15", "FUTURE_PX", 78_400.0, "BBG_BDH",
                 "2026-09-11T17:00:00-04:00")
    for day, spot in (("2026-09-11", 7.00), ("2026-09-14", 7.20), ("2026-09-15", 7.40), ("2026-09-17", 7.10)):
        _insert_mark(conn, day, "USDCNY", day, "SPOT", spot, "BBG_BFXFORWARD", f"{day}T15:00:00-04:00")
    conn.commit()


def test_a_settled_cny_future_not_yet_frozen_converts_at_the_spot_of_its_expiry_date():
    """User decision 2026-09-24 (C9): the provisional row converts at 09-15's 7.40, never at the
    price date's 7.00, the day before's 7.20 or the valuation date's 7.10."""
    conn = schema.connect()
    _settled_cny_future(conn)
    cu = _by_id(conn).loc["cu1"]
    assert cu["status"] == "SETTLED" and cu["reason"] == ""
    assert cu["pnl_local"] == 10 * 5 * (78_400.0 - 78_000.0) == 20_000.0
    assert cu["pnl_usd"] == 20_000.0 * (1.0 / 7.40) == cu["pnl_spot_usd"] and cu["pnl_carry_usd"] == 0.0
    assert (cu["mark"], cu["mark_date"], cu["mark_source"]) == (78_400.0, "2026-09-11", "BBG_BDH")
    assert (cu["spot"], cu["spot_source"]) == (1.0 / 7.40, "BBG_BFXFORWARD")
    assert cu["note"] == ("frozen at settlement price dated 2026-09-11 (last before settlement); "
                          "not yet recorded in realised_pnl")


def test_a_settled_cny_future_with_no_spot_on_its_expiry_date_takes_the_last_one_before_it():
    conn = schema.connect()
    _settled_cny_future(conn)
    conn.execute("DELETE FROM marks WHERE instrument_id = 'USDCNY' AND as_of_date = '2026-09-15'")
    conn.commit()
    cu = _by_id(conn).loc["cu1"]
    assert cu["pnl_usd"] == 20_000.0 * (1.0 / 7.20) and cu["spot"] == 1.0 / 7.20       # 09-14, the last before expiry
    assert cu["note"] == ("frozen at settlement price dated 2026-09-11 (last before settlement); converted at "
                          "USDCNY spot dated 2026-09-14 (last before expiry); not yet recorded in realised_pnl")


def test_a_settled_cny_future_with_no_spot_on_or_before_its_expiry_is_blank_with_the_reason():
    """Exact rows only: the spots after expiry (09-17's 7.10, and a 09-16 one the near-marks rule
    would carry back) are never used; blank, never at 1, the price and local P&L still shown."""
    conn = schema.connect()
    _settled_cny_future(conn)
    conn.execute("DELETE FROM marks WHERE instrument_id = 'USDCNY' AND as_of_date <= '2026-09-15'")
    _insert_mark(conn, "2026-09-16", "USDCNY", "2026-09-16", "SPOT", 7.30, "BBG_BFXFORWARD",
                 "2026-09-16T15:00:00-04:00")
    conn.commit()
    cu = _by_id(conn).loc["cu1"]
    assert cu["pnl_usd"] != cu["pnl_usd"] and cu["spot"] != cu["spot"] and cu["spot_source"] == ""
    assert cu["reason"] == ("settled trade cu1: no SPOT for USD conversion of CNY on or before its expiry "
                            "2026-09-15, so it cannot be frozen")
    assert (cu["mark"], cu["mark_date"], cu["pnl_local"]) == (78_400.0, "2026-09-11", 20_000.0)


def test_a_settled_usd_future_is_frozen_exactly_as_before():
    conn = schema.connect()
    _future(conn)
    conn.execute("UPDATE trade_legs SET settle_date = '2026-09-15' WHERE trade_id = 'f1'")
    conn.execute("UPDATE marks SET as_of_date = '2026-09-11', settle_date = '2026-09-15'")
    conn.commit()
    es = _by_id(conn).loc["f1"]
    pnl = 3 * 50 * (6100.0 - 6000.0)
    assert es["status"] == "SETTLED" and es["reason"] == ""
    assert (es["pnl_local"], es["pnl_usd"], es["pnl_spot_usd"], es["pnl_carry_usd"]) == (pnl, pnl, pnl, 0.0)
    assert (es["spot"], es["spot_source"], es["mark_date"]) == (1.0, "identity", "2026-09-11")
    assert es["note"] == ("frozen at settlement price dated 2026-09-11 (last before settlement); "
                          "not yet recorded in realised_pnl")


def test_a_cny_future_priced_on_its_expiry_date_is_one_figure_open_provisional_and_frozen():
    """Reviewer W-4: the open row on the expiry date, the settled row the day after before the
    ledger has run, and the ledger's frozen row give the same USD P&L; the next day's spot
    (7.00) never enters."""
    from engine.pnl import ledger
    conn = schema.connect()
    _commodity_future(conn, "cu1", "CUZ6 Comdty", "CNY", 5, "2026-09-15", 10, 78_000.0)
    _insert_instrument(conn, "USDCNY", "USD", "CNY")
    _insert_mark(conn, "2026-09-15", "CUZ6 Comdty", "2026-09-15", "FUTURE_PX", 78_400.0, "BBG_BDH",
                 "2026-09-15T17:00:00-04:00")
    _insert_mark(conn, "2026-09-15", "USDCNY", "2026-09-15", "SPOT", 7.20, "BBG_BFXFORWARD", "2026-09-15T15:00:00-04:00")
    _insert_mark(conn, "2026-09-16", "USDCNY", "2026-09-16", "SPOT", 7.00, "BBG_BFXFORWARD", "2026-09-16T15:00:00-04:00")
    conn.commit()
    open_row = _by_id(conn, "2026-09-15").loc["cu1"]
    provisional = _by_id(conn, "2026-09-16").loc["cu1"]
    assert (open_row["status"], provisional["status"]) == ("OPEN", "SETTLED")
    assert "not yet recorded in realised_pnl" in provisional["note"]
    assert ledger.realise_settled(conn, "2026-09-16")["realised"] == 1
    frozen = _by_id(conn, "2026-09-16").loc["cu1"]
    assert "not yet recorded" not in frozen["note"]
    expected = 10 * 5 * (78_400.0 - 78_000.0) / 7.20
    for row in (open_row, provisional, frozen):
        assert row["pnl_usd"] == pytest.approx(expected, rel=1e-9, abs=1e-9)
    assert provisional["pnl_usd"] == pytest.approx(open_row["pnl_usd"], rel=1e-9, abs=1e-9)
    assert frozen["pnl_usd"] == pytest.approx(provisional["pnl_usd"], rel=1e-9, abs=1e-9)


def test_a_settled_future_shows_the_conversion_its_realised_row_was_frozen_in():
    """The P&L is the stored figure, never recomputed; a row frozen in CNY shows the spot of
    its expiry date (C9), a USD row spot 1 / identity as always."""
    conn = schema.connect()
    _settled_cny_future(conn)
    _future(conn)
    conn.execute("UPDATE trade_legs SET settle_date = '2026-09-15' WHERE trade_id = 'f1'")
    columns = ("trade_id, instrument_id, product, currency, settle_date, local_amount, usd_entry_amount, "
               "mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note")
    s = 1 / 7.40
    conn.executemany(f"INSERT INTO realised_pnl ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("cu1", "CUZ6 Comdty", "FUTURE", "CNY", "2026-09-15", 10, 10 * 5 * 78_000.0 * s, "FUTURE_PX",
         5 * 78_400.0 * s, "2026-09-11", "BBG_BDH", 20_000.0 * s, "2026-09-16T00:00:00", "stored note"),
        ("f1", "ESZ6 Index", "FUTURE", "USD", "2026-09-15", 3, 3 * 50 * 6000.0, "FUTURE_PX",
         50 * 6100.0, "2026-09-15", "BBG_BDH", 15_000.0, "2026-09-16T00:00:00", ""),
    ])
    conn.commit()
    vb = _by_id(conn)
    cu, es = vb.loc["cu1"], vb.loc["f1"]
    assert cu["pnl_usd"] == 20_000.0 * s and cu["note"] == "stored note"
    assert (cu["spot"], cu["spot_source"]) == (pytest.approx(s, rel=1e-15), "BBG_BFXFORWARD")
    assert (es["pnl_usd"], es["spot"], es["spot_source"]) == (15_000.0, 1.0, "identity")


# --------------------------------------------------------------------- Phase 2 removal pin (2026-09-24)
# The user approved on 2026-09-24 that IRS and NDF valuation leave the app (CLAUDE.md "Commodity
# conversion plan", Phase 2). No P&L of a product that stays may move: every figure below was
# computed with the code at HEAD e660974, before the removal, on this very book, and is pinned
# to the bit.
PIN_AS_OF = "2026-09-17"


def _pin_book(conn):
    """An FX forward (EURUSD, exact outright), a cross (EURGBP, outright interpolated along the
    day's curve, converted at GBPUSD), XAUUSD with spot alone on file, a CNY future converted at a
    USDCNY spot of the previous close (near marks in time), an open FX option, a closed-out FX
    option pair, and a settled USDCNH forward with no realised row yet."""
    def inst(iid, asset, base, quote, mult=1.0, expiry="9999-12-31"):
        conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,0,?,?)", (iid, asset, base, quote, mult, iid, expiry))

    def fx(tid, pair, base, quote, qty, fill, settle, trade_date="2026-08-03"):
        _insert_trade(conn, tid, pair, "FX_FWD", trade_date, qty, fill)
        _insert_legs(conn, [(tid, 1, "FX_NEAR", base, qty, trade_date, settle, fill, 1),
                            (tid, 2, "FX_NEAR", quote, -qty * fill, trade_date, settle, fill, 1)])

    def mark(day, iid, settle, mark_type, value, source):
        _insert_mark(conn, day, iid, settle, mark_type, value, source, f"{day}T15:00:00-04:00")

    for pair, base, quote in (("EURUSD", "EUR", "USD"), ("EURGBP", "EUR", "GBP"), ("GBPUSD", "GBP", "USD"),
                              ("XAUUSD", "XAU", "USD"), ("USDCNY", "USD", "CNY"), ("USDCNH", "USD", "CNH")):
        inst(pair, "FX", base, quote)
    fx("fx1", "EURUSD", "EUR", "USD", 2_000_000, 1.10, "2026-10-20")
    fx("cr1", "EURGBP", "EUR", "GBP", -1_500_000, 0.8450, "2026-11-18")
    fx("au1", "XAUUSD", "XAU", "USD", 482.474, 4145.30, "2026-10-26")
    fx("st1", "USDCNH", "USD", "CNH", 1_000_000, 7.18, "2026-08-19", trade_date="2026-07-15")
    inst("CUZ6 Comdty", "FUTURE", "CU", "CNY", 5.0, "2026-12-15")
    _insert_trade(conn, "cu1", "CUZ6 Comdty", "FUTURE", "2026-08-03", 10, 78_000.0)
    _insert_legs(conn, [("cu1", 1, "NOTIONAL", "CNY", 3_900_000, "2026-08-03", "2026-12-15", 78_000.0, 0)])
    for iid, tid, qty, fill, strike, typ, trade_date in (
            ("EURUSD111926C-1", "op1", 5_000_000, 0.0123, 1.12, "CALL", "2026-08-03"),
            ("EURUSD120126P-2", "cl1", 1_000_000, 0.0100, 1.08, "PUT", "2026-08-03"),
            ("EURUSD120126P-3", "cl2", -1_000_000, 0.0130, 1.08, "PUT", "2026-09-01")):
        expiry = "2026-11-19" if tid == "op1" else "2026-12-01"
        inst(iid, "FX_OPTION", "EUR", "USD", 1.0, expiry)
        conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type) VALUES (?,?,?)",
                     (iid, strike, typ))
        _insert_trade(conn, tid, iid, "FX_OPTION", trade_date, qty, fill)
        _insert_legs(conn, [(tid, 1, "NOTIONAL", "EUR", qty, trade_date, expiry, fill, 0)])
    mark(PIN_AS_OF, "EURUSD", "2026-10-20", "FWD_OUTRIGHT", 1.1234, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "EURUSD", PIN_AS_OF, "SPOT", 1.1150, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "EURGBP", "2026-10-19", "FWD_OUTRIGHT", 0.8470, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "EURGBP", "2026-12-18", "FWD_OUTRIGHT", 0.8490, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "EURGBP", PIN_AS_OF, "SPOT", 0.8460, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "GBPUSD", PIN_AS_OF, "SPOT", 1.3310, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "XAUUSD", PIN_AS_OF, "SPOT", 4201.75, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "CUZ6 Comdty", "2026-12-15", "FUTURE_PX", 78_650.0, "BBG_BDH")
    mark("2026-09-16", "USDCNY", "2026-09-16", "SPOT", 7.1234, "BBG_BFXFORWARD")
    mark(PIN_AS_OF, "EURUSD111926C-1", "2026-11-19", "PREMIUM", 0.0150, "QL_OPTIONS_PRICER")
    mark("2026-09-01", "EURUSD", "2026-09-01", "SPOT", 1.1050, "BBG_BFXFORWARD")
    mark("2026-08-19", "USDCNH", "2026-08-19", "SPOT", 7.1650, "BBG_BFXFORWARD")
    conn.commit()


_PIN_FIELDS = ("status", "product", "mark", "mark_date", "mark_source", "spot", "spot_source", "pnl_local",
               "pnl_usd", "pnl_spot_usd", "pnl_carry_usd", "reason", "note")

_PIN_AT_HEAD = {
    "fx1": ("OPEN", "FX_FWD", 1.1234, "2026-10-20", "BBG_BFXFORWARD", 1.0, "identity", 46799.99999999973,
            46799.99999999973, 29999.999999999804, 16799.999999999927, "", ""),
    "cr1": ("OPEN", "FX_FWD", 0.848, "2026-11-18", "INTERP: between EURGBP 2026-10-19 and 2026-12-18 marks of 2026-09-17",
            1.331, "BBG_BFXFORWARD", -4500.000000000004, -5989.500000000005, -1996.500000000001,
            -3993.0000000000036, "", ""),
    "au1": ("OPEN", "FX_FWD", 4201.75, "2026-10-26", "INTERP: XAUUSD 2026-09-21 mark of 2026-09-17 (the only pillar)",
            1.0, "identity", 27235.65729999991, 27235.65729999991, 27235.65729999991, 0.0, "", ""),
    "st1": ("SETTLED", "FX_FWD", 7.165, "2026-08-19", "BBG_BFXFORWARD", 0.13956734124214934, "BBG_BFXFORWARD",
            -14999.99999999968, -2093.5101186321954, -2093.5101186321954, 0.0, "",
            "frozen at spot; not yet recorded in realised_pnl"),
    "cu1": ("OPEN", "FUTURE", 78650.0, "2026-12-15", "BBG_BDH", 0.14038240166212762,
            "INTERP: SPOT of 2026-09-16 (nearest earlier close)", 32500.0, 4562.428054019148, 4562.428054019148,
            0.0, "", ""),
    "op1": ("OPEN", "FX_OPTION", 0.015, "2026-11-19", "QL_OPTIONS_PRICER", 1.115, "BBG_BFXFORWARD",
            13499.999999999996, 15052.499999999996, 15052.499999999996, 0.0, "", ""),
    "cl1": ("CLOSED", "FX_OPTION", 0.013, "2026-09-01", "CLOSE_OUT_FILL", 1.105, "BBG_BFXFORWARD",
            2999.999999999999, 3314.999999999999, 3314.999999999999, 0.0, "",
            "closed out 2026-09-01: bought and sold back in full (trades cl1, cl2); realised at the closing fill "
            "0.013, no PREMIUM mark needed"),
    "cl2": ("CLOSED", "FX_OPTION", 0.013, "2026-09-01", "CLOSE_OUT_FILL", 1.105, "BBG_BFXFORWARD",
            0.0, 0.0, 0.0, 0.0, "",
            "closed out 2026-09-01: bought and sold back in full (trades cl1, cl2); realised at the closing fill "
            "0.013, no PREMIUM mark needed"),
}


@pytest.mark.parametrize("trade_id", sorted(_PIN_AT_HEAD))
def test_the_products_that_stay_price_to_the_bit_what_they_did_before_the_irs_and_ndf_removal(trade_id):
    conn = schema.connect()
    _pin_book(conn)
    vb = _by_id(conn, PIN_AS_OF)
    assert sorted(vb.index) == sorted(_PIN_AT_HEAD)
    assert tuple(vb.loc[trade_id, f] for f in _PIN_FIELDS) == _PIN_AT_HEAD[trade_id]


def test_a_forward_flagged_non_deliverable_is_valued_as_a_deliverable_forward_and_a_swap_is_not_valued():
    """After the removal a forward on an `is_ndf = 1` instrument, looked at past what used to be
    its fixing date, is marked at its own value date's outright and converted at spot like any
    FX forward (no fixing, no NDF_FIX read), and a leftover IRS trade is not valued: its row is
    blank with its reason, whatever PV_USD is on file (user decision 2026-09-24, C10)."""
    conn = schema.connect()
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('USDIDR','FX','USD','IDR',1,1,'USDIDR Curncy','9999-12-31')")
    _insert_trade(conn, "n1", "USDIDR", "FX_FWD", "2026-08-20", -1_000_000, 17_900.0)
    _insert_legs(conn, [("n1", 1, "FX_NEAR", "USD", -1_000_000, "2026-08-20", "2026-09-24", 17_900.0, 0),
                        ("n1", 2, "FX_NEAR", "IDR", 17_900_000_000, "2026-08-20", "2026-09-24", 17_900.0, 0)])
    stamp = "2026-09-23T15:00:00-04:00"
    _insert_mark(conn, "2026-09-23", "USDIDR", "2026-09-24", "FWD_OUTRIGHT", 17_860.0, "BBG_BFXFORWARD", stamp)
    _insert_mark(conn, "2026-09-23", "USDIDR", "2026-09-23", "SPOT", 17_850.0, "BBG_BFXFORWARD", stamp)
    _insert_mark(conn, "2026-09-22", "USDIDR", "2026-09-22", "NDF_FIX", 17_820.0, "BBG_BDH", stamp)
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','2031-06-01')")
    _insert_trade(conn, "s1", "IRSOIS-USD-1", "IRS", "2026-06-01", 10_000_000, 3.85)
    _insert_legs(conn, [("s1", 1, "FIXED", "USD", -10_000_000, "2026-06-03", "2031-06-01", 3.85, 1),
                        ("s1", 2, "FLOAT", "USD", 10_000_000, "2026-06-03", "2031-06-01", 0.0, 1)])
    _insert_mark(conn, "2026-09-23", "IRSOIS-USD-1", "2031-06-01", "PV_USD", 12_500.0, "QL_PRICER", stamp)
    conn.commit()
    vb = _by_id(conn, "2026-09-23")
    assert sorted(vb.index) == ["n1", "s1"]
    assert vb.loc["s1", "pnl_usd"] != vb.loc["s1", "pnl_usd"] and vb.loc["s1", "reason"] == _SWAP_GONE
    n1 = vb.loc["n1"]
    assert (n1["status"], n1["mark"], n1["mark_date"], n1["mark_source"]) == ("OPEN", 17_860.0, "2026-09-24", "BBG_BFXFORWARD")
    assert (n1["spot"], n1["spot_source"]) == (1 / 17_850.0, "BBG_BFXFORWARD")
    assert n1["pnl_usd"] == -1_000_000 * (17_860.0 - 17_900.0) * (1 / 17_850.0)
    assert n1["note"] == "" and n1["reason"] == ""


# --------------------------------------------------------------------- leftover retired products (C10)
# User decision 2026-09-24 (C10): an old database's leftover trade of a retired product is never
# valued and never dropped: blank P&L with its reason, status as its dates say; one the ledger
# froze before the removal keeps showing its frozen figure.
_SWAP_GONE = "rate swaps left the app on 2026-09-24; this old trade is not valued and leaves at the next upload"


def _leftover_swap(conn, trade_id, maturity, product="IRS"):
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES (?, 'IRS', 'USD', 'USD', 1, 0, '', ?)", (f"IRS-{trade_id}", maturity))
    _insert_trade(conn, trade_id, f"IRS-{trade_id}", product, "2026-06-01", 10_000_000, 3.85)
    _insert_legs(conn, [(trade_id, 1, "FIXED", "USD", -10_000_000, "2026-06-03", maturity, 3.85, 1),
                        (trade_id, 2, "FLOAT", "USD", 10_000_000, "2026-06-03", maturity, 0.0, 1)])
    _insert_mark(conn, "2026-09-17", f"IRS-{trade_id}", maturity, "PV_USD", 12_500.0, "QL_PRICER", _CLOSE)


def _is_blank(row):
    return all(row[c] != row[c] for c in ("mark", "spot", "pnl_local", "pnl_usd", "pnl_spot_usd", "pnl_carry_usd"))


def test_a_leftover_open_swap_has_a_blank_row_with_its_reason_never_a_figure():
    conn = schema.connect()
    _two_pairs(conn)
    _leftover_swap(conn, "s1", "2031-06-01")
    conn.commit()
    vb = _by_id(conn)
    s1 = vb.loc["s1"]
    assert (s1["status"], s1["product"], s1["settle_date"], s1["quantity"], s1["fill"]) == (
        "OPEN", "IRS", "2031-06-01", 10_000_000, 3.85)
    assert _is_blank(s1) and s1["reason"] == _SWAP_GONE and s1["note"] == ""
    assert list(vb.loc[["t1", "t2"], "reason"]) == ["", ""]                # the rest of the book prices as before
    assert list(value_book(conn, "2026-09-17", trade_ids=["s1"])["trade_id"]) == ["s1"]
    assert "s1" not in set(value_book(conn, "2026-05-29")["trade_id"])     # before its trade date, like any trade


def test_a_leftover_matured_swap_shows_its_frozen_figure_or_else_a_blank_row():
    conn = schema.connect()
    _leftover_swap(conn, "s1", "2026-09-10")
    _leftover_swap(conn, "s2", "2026-09-11")
    _leftover_swap(conn, "w1", "2031-06-01", product="SWAPTION")
    columns = ("trade_id, instrument_id, product, currency, settle_date, local_amount, usd_entry_amount, "
               "mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note")
    conn.execute(f"INSERT INTO realised_pnl ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("s1", "IRS-s1", "IRS", "USD", "2026-09-10", 10_000_000, 0.0, "PV_USD", 1.0, "2026-09-10",
                  "QL_PRICER", -8_250.5, "2026-09-11T00:00:00", "frozen at PV + cashflows"))
    conn.commit()
    vb = _by_id(conn)
    s1, s2, w1 = vb.loc["s1"], vb.loc["s2"], vb.loc["w1"]
    assert (s1["status"], s1["pnl_usd"], s1["pnl_spot_usd"], s1["spot"], s1["spot_source"], s1["reason"]) == (
        "SETTLED", -8_250.5, -8_250.5, 1.0, "identity", "")
    assert (s1["mark_date"], s1["mark_source"], s1["note"]) == ("2026-09-10", "QL_PRICER", "frozen at PV + cashflows")
    assert s2["status"] == "SETTLED" and _is_blank(s2) and s2["reason"] == _SWAP_GONE
    assert w1["status"] == "OPEN" and _is_blank(w1)
    assert w1["reason"] == ("rate options left the app on 2026-09-24; this old trade is not valued and "
                            "leaves at the next upload")
    # a frozen row that is unreadable is no frozen figure: blank, the reason naming both
    conn.execute("UPDATE realised_pnl SET pnl_usd = '10-Sep' WHERE trade_id = 's1'")
    conn.commit()
    s1 = _by_id(conn).loc["s1"]
    assert _is_blank(s1)
    assert s1["reason"] == _SWAP_GONE + "; its realised_pnl row is unreadable (realised_pnl.pnl_usd is not a number ('10-Sep'))"


# --------------------------------------------------------------------- options on commodity futures (C12)
# User decision 2026-09-24 (C12): product CMDTY_OPTION joins the listed path, `contracts x
# multiplier x (m - f)` at Bloomberg's own option price, in the quote currency, converted at spot
# of the valuation date; frozen after expiry at the last price on or before it, converted at the
# spot of its expiry date.


def _commodity_option(conn, trade_id, instrument_id, quote_ccy, multiplier, expiry, quantity, fill):
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?, 'CMDTY_OPTION', ?, ?, ?, 0, ?, ?)",
        (instrument_id, instrument_id[:2], quote_ccy, multiplier, instrument_id, expiry))
    _insert_trade(conn, trade_id, instrument_id, "CMDTY_OPTION", "2026-08-03", quantity, fill)
    _insert_legs(conn, [(trade_id, 1, "NOTIONAL", quote_ccy, quantity * multiplier, "2026-08-03", expiry, fill, 0)])


def test_an_open_option_on_a_cny_future_and_on_a_usd_future_are_valued_like_futures():
    conn = schema.connect()
    _commodity_option(conn, "co1", "CU2611C80000 Comdty", "CNY", 5, "2026-10-26", 2, 1_000.0)
    _commodity_option(conn, "co2", "CLZ6C75 Comdty", "USD", 1000, "2026-11-17", -3, 3.10)
    _insert_instrument(conn, "USDCNY", "USD", "CNY")
    _insert_mark(conn, "2026-09-17", "CU2611C80000 Comdty", "2026-10-26", "FUTURE_PX", 1_250.0, "BBG_BDH", _CLOSE)
    _insert_mark(conn, "2026-09-17", "CLZ6C75 Comdty", "2026-11-17", "FUTURE_PX", 2.85, "BBG_BDH", _CLOSE)
    _insert_mark(conn, "2026-09-17", "USDCNY", "2026-09-17", "SPOT", 7.10, "BBG_BFXFORWARD", _CLOSE)
    conn.commit()
    vb = _by_id(conn)
    co1, co2 = vb.loc["co1"], vb.loc["co2"]
    assert (co1["product"], co1["status"], co1["reason"]) == ("CMDTY_OPTION", "OPEN", "")
    assert co1["pnl_local"] == 2 * 5 * (1_250.0 - 1_000.0) == 2_500.0                        # CNY
    assert co1["pnl_usd"] == 2_500.0 * (1.0 / 7.10) == co1["pnl_spot_usd"] and co1["pnl_carry_usd"] == 0.0
    assert (co1["mark"], co1["mark_source"], co1["spot"], co1["spot_source"]) == (
        1_250.0, "BBG_BDH", 1.0 / 7.10, "BBG_BFXFORWARD")
    pnl = -3 * 1000 * (2.85 - 3.10)
    assert (co2["pnl_local"], co2["pnl_usd"], co2["pnl_spot_usd"], co2["pnl_carry_usd"]) == (pnl, pnl, pnl, 0.0)
    assert (co2["spot"], co2["spot_source"], co2["reason"]) == (1.0, "identity", "")
    # no price: blank with the listed option's reason, never a model value
    conn.execute("DELETE FROM marks WHERE instrument_id = 'CLZ6C75 Comdty'")
    conn.commit()
    co2 = _by_id(conn).loc["co2"]
    assert co2["pnl_usd"] != co2["pnl_usd"]
    assert co2["reason"] == "no Bloomberg price for the listed option CLZ6C75 Comdty on 2026-09-17"


def test_an_expired_option_on_a_cny_future_freezes_at_its_last_price_and_the_spot_of_its_expiry_date():
    conn = schema.connect()
    _commodity_option(conn, "co1", "CU2609C80000 Comdty", "CNY", 5, "2026-09-15", 2, 1_000.0)
    _insert_instrument(conn, "USDCNY", "USD", "CNY")
    _insert_mark(conn, "2026-09-11", "CU2609C80000 Comdty", "2026-09-15", "FUTURE_PX", 1_400.0, "BBG_BDH",
                 "2026-09-11T17:00:00-04:00")
    _insert_mark(conn, "2026-09-17", "CU2609C80000 Comdty", "2026-09-15", "FUTURE_PX", 9_999.0, "BBG_BDH", _CLOSE)
    for day, spot in (("2026-09-11", 7.00), ("2026-09-14", 7.20), ("2026-09-15", 7.40), ("2026-09-17", 7.10)):
        _insert_mark(conn, day, "USDCNY", day, "SPOT", spot, "BBG_BFXFORWARD", f"{day}T15:00:00-04:00")
    conn.commit()
    co1 = _by_id(conn).loc["co1"]                                     # provisional: no realised row yet
    assert (co1["status"], co1["reason"], co1["mark"], co1["mark_date"]) == ("SETTLED", "", 1_400.0, "2026-09-11")
    assert co1["pnl_local"] == 2 * 5 * (1_400.0 - 1_000.0) == 4_000.0
    assert co1["pnl_usd"] == 4_000.0 * (1.0 / 7.40) and co1["spot"] == 1.0 / 7.40
    # frozen by the ledger: the stored figure, shown with its expiry date's spot
    s = 1.0 / 7.40
    columns = ("trade_id, instrument_id, product, currency, settle_date, local_amount, usd_entry_amount, "
               "mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note")
    conn.execute(f"INSERT INTO realised_pnl ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("co1", "CU2609C80000 Comdty", "CMDTY_OPTION", "CNY", "2026-09-15", 2, 2 * 5 * 1_000.0 * s,
                  "FUTURE_PX", 5 * 1_400.0 * s, "2026-09-11", "BBG_BDH", 4_000.0 * s, "2026-09-16T00:00:00", "stored"))
    conn.commit()
    co1 = _by_id(conn).loc["co1"]
    assert (co1["pnl_usd"], co1["note"], co1["reason"]) == (4_000.0 * s, "stored", "")
    assert (co1["spot"], co1["spot_source"]) == (s, "BBG_BFXFORWARD")


# --------------------------------------------------------------------- usd_per_quote_on_or_before (C9)

def test_usd_per_quote_on_or_before_reads_the_last_exact_official_spot_on_or_before_the_date():
    from engine.pnl.valuation import usd_per_quote_on_or_before
    conn = schema.connect()
    for pair, base, quote in (("USDCNY", "USD", "CNY"), ("EURUSD", "EUR", "USD"), ("USDJPY", "USD", "JPY")):
        _insert_instrument(conn, pair, base, quote)
    _insert_mark(conn, "2026-09-14", "USDCNY", "2026-09-14", "SPOT", 7.20, "BBG_BFXFORWARD", _CLOSE)
    _insert_mark(conn, "2026-09-16", "USDCNY", "2026-09-16", "SPOT", 7.30, "BBG_BFXFORWARD", _CLOSE)
    _insert_mark(conn, "2026-09-15", "USDCNY", "2026-09-15", "SPOT", 9.99, "MANUAL", _CLOSE)       # not official
    _insert_mark(conn, "2026-09-10", "EURUSD", "2026-09-10", "SPOT", 1.10, "BBG_BFXFORWARD", _CLOSE)
    _insert_mark(conn, "2026-09-16", "USDJPY", "2026-09-16", "SPOT", 147.0, "BBG_BFXFORWARD", _CLOSE)  # after only
    conn.commit()
    assert usd_per_quote_on_or_before(conn, "USD", "2026-09-15") == (1.0, "USD", "identity", "2026-09-15")
    assert usd_per_quote_on_or_before(conn, "CNY", "2026-09-15") == (1.0 / 7.20, "USDCNY", "BBG_BFXFORWARD", "2026-09-14")
    assert usd_per_quote_on_or_before(conn, "EUR", "2026-09-15") == (1.10, "EURUSD", "BBG_BFXFORWARD", "2026-09-10")
    s, pair, source, day = usd_per_quote_on_or_before(conn, "JPY", "2026-09-15")
    assert s != s and (pair, source, day) == (None, None, None)
    s, pair, source, day = usd_per_quote_on_or_before(conn, "GBP", "2026-09-15")
    assert s != s and (pair, source, day) == (None, None, None)


# --------------------------------------------------------------------- LME forwards (Phase 5)
# User decision 2026-09-24, CLAUDE.md "P&L conventions -> LME forwards": the FX-forward rule on the
# metal's root id, PnL_USD = tonnes x (m - f), S = 1; the curve's cash price sits at the LME cash
# date; settled, frozen at the last official cash price on or before the prompt date.

def _lme_ticket(conn, tid, tonnes, fill, prompt, trade_date="2026-08-20", root="LME:CA"):
    """An LME ticket as ingest-parser writes it: the root instrument, product LME_FWD, two FX_NEAR
    legs on the prompt (the metal leg, then the USD leg)."""
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?, 'LME_FWD', ?, 'USD', 1, 0, 'LMCADY Comdty', "
                 "'9999-12-31')", (root, root))
    _insert_trade(conn, tid, root, "LME_FWD", trade_date, tonnes, fill)
    _insert_legs(conn, [(tid, 1, "FX_NEAR", root, tonnes, trade_date, prompt, fill, 0),
                        (tid, 2, "FX_NEAR", "USD", -tonnes * fill, trade_date, prompt, fill, 1)])


def _lme_curve(conn, as_of, cash, points, root="LME:CA"):
    """The day's official LME curve: the cash price (SPOT, keyed on its own day) and the given
    {prompt: outright} FWD_OUTRIGHT pillars."""
    stamp = f"{as_of}T17:00:00-04:00"
    if cash is not None:
        _insert_mark(conn, as_of, root, as_of, "SPOT", cash, "BBG_BFXFORWARD", stamp)
    for prompt, value in points.items():
        _insert_mark(conn, as_of, root, prompt, "FWD_OUTRIGHT", value, "BBG_BFXFORWARD", stamp)


def test_an_open_lme_three_month_ticket_is_marked_at_its_own_pillar_in_usd():
    from engine.lme import three_month_date
    conn = schema.connect()
    three_m = three_month_date("2026-09-15").isoformat()                  # 2026-12-15
    _lme_ticket(conn, "lme1", 100.0, 9_800.0, three_m)
    _lme_curve(conn, "2026-09-15", 9_850.0, {three_m: 9_900.0})
    conn.commit()
    r = _by_id(conn, "2026-09-15").loc["lme1"]
    assert (r["status"], r["product"], r["reason"]) == ("OPEN", "LME_FWD", "")     # never relabelled FX_SPOT
    assert (r["mark"], r["mark_date"], r["mark_source"]) == (9_900.0, three_m, "BBG_BFXFORWARD")
    assert (r["spot"], r["spot_source"]) == (1.0, "identity")
    assert r["pnl_local"] == r["pnl_usd"] == 100.0 * (9_900.0 - 9_800.0) == 10_000.0
    assert r["pnl_carry_usd"] == 100.0 * (9_900.0 - 9_850.0)            # against the cash price
    assert r["pnl_spot_usd"] == r["pnl_usd"] - r["pnl_carry_usd"]


def test_a_broken_lme_prompt_is_read_between_the_cash_date_and_the_next_pillar():
    """2026-08-27: the FX spot date is Monday 31 August, a London bank holiday, so the LME cash
    date is Tuesday 1 September; the cash price is placed there, and a broken prompt is linear in
    calendar days from it to the next pillar, the source naming both marks."""
    from engine.lme import cash_date
    from engine.pnl.calendar import spot_date
    as_of = "2026-08-27"
    cash_day = cash_date(as_of)
    assert cash_day.isoformat() == "2026-09-01" and spot_date(as_of, "LME:CA").isoformat() == "2026-08-31"
    conn = schema.connect()
    _lme_ticket(conn, "lme2", -50.0, 9_700.0, "2026-10-21", trade_date="2026-08-26")
    _lme_curve(conn, as_of, 9_600.0, {"2026-11-27": 9_690.0})
    conn.commit()
    r = _by_id(conn, as_of).loc["lme2"]
    w = (dt.date(2026, 10, 21) - cash_day).days / (dt.date(2026, 11, 27) - cash_day).days
    m = 9_600.0 + w * (9_690.0 - 9_600.0)
    assert r["mark"] == m and r["reason"] == ""
    assert r["mark_source"] == "INTERP: between LME:CA 2026-09-01 and 2026-11-27 marks of 2026-08-27"
    assert r["pnl_usd"] == -50.0 * (m - 9_700.0) and r["spot"] == 1.0


def test_an_lme_prompt_before_the_cash_date_is_marked_at_the_cash_price():
    from engine.lme import cash_date
    conn = schema.connect()
    _lme_ticket(conn, "lme3", 25.0, 9_800.0, "2026-09-16", trade_date="2026-09-14")   # tom; cash is the 17th
    _lme_curve(conn, "2026-09-15", 9_850.0, {"2026-12-15": 9_900.0})
    conn.commit()
    r = _by_id(conn, "2026-09-15").loc["lme3"]
    assert cash_date("2026-09-15").isoformat() == "2026-09-17"
    assert r["mark"] == 9_850.0 and r["pnl_usd"] == 25.0 * 50.0
    assert r["mark_source"] == "INTERP: LME:CA 2026-09-17 mark of 2026-09-15 (nearest, no earlier pillar)"


def test_a_settled_unfrozen_lme_ticket_shows_the_settlement_price_the_ledger_freezes():
    from engine.lme import settlement_price
    from engine.pnl import ledger
    conn = schema.connect()
    _lme_ticket(conn, "lme4", 100.0, 9_800.0, "2026-09-16")
    _lme_ticket(conn, "lme5", -25.0, 9_800.0, "2026-09-13")              # a Sunday: the Friday cash price
    for day, cash in (("2026-09-11", 9_760.0), ("2026-09-15", 9_700.0), ("2026-09-16", 9_720.0),
                      ("2026-09-17", 9_990.0)):
        _lme_curve(conn, day, cash, {})
    conn.commit()
    vb = _by_id(conn, "2026-09-18")
    r4, r5 = vb.loc["lme4"], vb.loc["lme5"]
    assert settlement_price(conn, "LME:CA", "2026-09-16") == (9_720.0, "2026-09-16", "BBG_BFXFORWARD")
    assert (r4["status"], r4["reason"], r4["mark"], r4["mark_date"]) == ("SETTLED", "", 9_720.0, "2026-09-16")
    assert r4["pnl_usd"] == 100.0 * (9_720.0 - 9_800.0) and r4["spot"] == 1.0
    assert r4["note"] == "frozen at cash price; not yet recorded in realised_pnl"
    assert (r5["mark"], r5["mark_date"], r5["pnl_usd"]) == (9_760.0, "2026-09-11", -25.0 * (9_760.0 - 9_800.0))
    assert r5["note"].startswith("frozen at cash price dated 2026-09-11 (last before settlement)")
    # the ledger freezes the same figures (its FX path, which LME_FWD joins through FX_PRODUCTS)
    ledger.realise_settled(conn, "2026-09-18")
    frozen = _by_id(conn, "2026-09-18")
    assert frozen.loc["lme4", "note"] != r4["note"]                      # read back from realised_pnl now
    assert frozen.loc["lme4", "pnl_usd"] == pytest.approx(r4["pnl_usd"], rel=1e-12)
    assert frozen.loc["lme5", "pnl_usd"] == pytest.approx(r5["pnl_usd"], rel=1e-12)


def test_an_lme_ticket_with_no_curve_at_all_is_blank_with_its_reason():
    conn = schema.connect()
    _lme_ticket(conn, "lme6", 100.0, 9_800.0, "2026-12-15")
    conn.commit()
    r = _by_id(conn, "2026-09-15").loc["lme6"]
    assert r["status"] == "OPEN" and r["pnl_usd"] != r["pnl_usd"] and r["mark"] != r["mark"]
    assert r["reason"] == ("no LME price of LME:CA for prompt 2026-12-15 on 2026-09-15: no cash price or forward "
                           "of the metal on file on that day or any other close")


def test_an_fx_forward_is_unchanged_beside_an_lme_ticket():
    """The FX pillar stays at the FX spot date (2026-08-31 here, a London holiday the LME skips),
    and the FX row is the same with or without an LME ticket in the book."""
    as_of = "2026-08-27"
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_trade(conn, "fx1", "USDJPY", "FX_FWD", "2026-08-20", 1_000_000, 147.0)
    _insert_legs(conn, [("fx1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-20", "2026-10-21", 147.0, 1),
                        ("fx1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-08-20", "2026-10-21", 147.0, 1)])
    _insert_mark(conn, as_of, "USDJPY", as_of, "SPOT", 146.0, "BBG_BFXFORWARD", _CLOSE)
    _insert_mark(conn, as_of, "USDJPY", "2026-11-30", "FWD_OUTRIGHT", 145.0, "BBG_BFXFORWARD", _CLOSE)
    conn.commit()
    before = _by_id(conn, as_of).loc["fx1"]
    w = (dt.date(2026, 10, 21) - dt.date(2026, 8, 31)).days / (dt.date(2026, 11, 30) - dt.date(2026, 8, 31)).days
    m = 146.0 + w * (145.0 - 146.0)
    assert before["mark"] == m and before["product"] == "FX_FWD"
    assert before["mark_source"] == "INTERP: between USDJPY 2026-08-31 and 2026-11-30 marks of 2026-08-27"
    assert before["pnl_usd"] == 1_000_000 * (m - 147.0) * (1.0 / 146.0)
    _lme_ticket(conn, "lme7", 100.0, 9_800.0, "2026-10-21")
    _lme_curve(conn, as_of, 9_600.0, {"2026-11-27": 9_690.0})
    conn.commit()
    after = _by_id(conn, as_of)
    assert sorted(after.index) == ["fx1", "lme7"]
    assert after.loc["fx1"].to_dict() == before.to_dict()
