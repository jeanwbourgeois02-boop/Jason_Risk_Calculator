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
from engine.pnl.valuation import _business_days_between, _mark_at, _mark_near, value_book


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


def test_a_swap_with_a_text_fixed_rate_still_prices_because_its_pnl_is_marks_only():
    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','2031-06-01')")
    _insert_trade(conn, "s1", "IRSOIS-USD-1", "IRS", "2026-06-01", 10_000_000, 3.85)
    _insert_legs(conn, [("s1", 1, "FIXED", "USD", -10_000_000, "2026-06-03", "2031-06-01", 3.85, 1),
                        ("s1", 2, "FLOAT", "USD", 10_000_000, "2026-06-03", "2031-06-01", 0.0, 1)])
    for mark_type, value in (("PV_USD", 12_500.0), ("CASHFLOW_USD", 500.0)):
        _insert_mark(conn, "2026-09-17", "IRSOIS-USD-1", "2031-06-01", mark_type, value, "QL_PRICER", "t")
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 's1'")
    conn.commit()
    vb = _by_id(conn)
    assert vb.loc["s1", "reason"] == "" and vb.loc["s1", "pnl_usd"] == 13_000.0
    assert vb.loc["s1", "fill"] != vb.loc["s1", "fill"]  # the cell is blank, never the raw text
    assert "trades.price is not a number ('24-Jul')" in vb.loc["s1", "note"]


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


# --------------------------------------------------------------------- NDF exit price (2026-09-22)
# User: "the exit price is the fix on that day, as pulled from bbg", then "each ndf has a unique
# fix". A fixed NDF's mark is the official NDF_FIX dated its fixing date exactly; a fix of another
# day is never estimated into its place. With no fix for that date on file, the SPOT of the fixing
# date (the near-marks estimate when that day's own is not on file), named as the substitute.
# Seen 2026-09-22 on the imported snapshot: no marks that day, and the 21 USDIDR / USDBRL tickets
# fixing that day took the 09-17 / 09-14 fixes through the time interpolation, moving Daily by
# -126,820 although nothing had been priced.


def _ndf_book(conn, settle="2026-09-24"):
    """One USDIDR NDF ticket, n1: sold 1m USD at 17,900 for `settle` (Thu 2026-09-24, fixing
    date = value date less 2 business days = Tue 2026-09-22)."""
    conn.execute("INSERT INTO instruments VALUES ('USDIDR','FX','USD','IDR',1,1,'USDIDR Curncy','9999-12-31')")
    _insert_trade(conn, "n1", "USDIDR", "FX_FWD", "2026-08-20", -1_000_000, 17_900.0)
    _insert_legs(conn, [
        ("n1", 1, "FX_NEAR", "USD", -1_000_000, "2026-08-20", settle, 17_900.0, 0),
        ("n1", 2, "FX_NEAR", "IDR", 17_900_000_000, "2026-08-20", settle, 17_900.0, 0),
    ])


def _idr_mark(conn, day, mark_type, value):
    source = "BBG_BDH" if mark_type == "NDF_FIX" else "BBG_BFXFORWARD"
    _insert_mark(conn, day, "USDIDR", day, mark_type, value, source, f"{day}T15:00:00-04:00")


def _n1(conn, day):
    return value_book(conn, day).set_index("trade_id").loc["n1"]


def test_a_fix_of_another_day_is_never_the_exit_price_the_spot_of_the_fixing_date_is():
    """(a) fixes on file the day before and the day after the fixing date, none on it: the
    ticket takes the SPOT of the fixing date -- not either fix, not a value between them --
    and its note says so."""
    conn = schema.connect()
    _ndf_book(conn)
    _idr_mark(conn, "2026-09-21", "NDF_FIX", 17_700.0)
    _idr_mark(conn, "2026-09-23", "NDF_FIX", 17_800.0)
    _idr_mark(conn, "2026-09-22", "SPOT", 17_850.0)
    conn.commit()
    row = _n1(conn, "2026-09-22")
    assert (row["mark"], row["mark_source"], row["mark_date"]) == (17_850.0, "BBG_BFXFORWARD", "2026-09-22")
    assert row["pnl_usd"] == pytest.approx(-1_000_000 * (17_850.0 - 17_900.0) / 17_850.0)
    assert row["note"] == ("NDF fixed 2026-09-22: no official fixing on file: at the spot of 2026-09-22 instead, "
                           "converted at that spot, no delta, no carry")
    assert row["reason"] == ""
    # and on a later valuation date still the fixing date's spot, still neither fix
    later = _n1(conn, "2026-09-25")
    assert later["mark"] == 17_850.0 and later["pnl_usd"] == pytest.approx(row["pnl_usd"])


def test_the_fix_of_the_fixing_date_is_the_exit_price_once_on_file():
    """(b) with the fixing date's own fix on file it is the mark, whatever the spot and the
    other days' fixes say; converted to USD at the fix itself (user decision 2026-09-22: a
    USDXXX NDF settles its quote-currency difference at the fix), never at the spot."""
    conn = schema.connect()
    _ndf_book(conn)
    _idr_mark(conn, "2026-09-21", "NDF_FIX", 17_700.0)
    _idr_mark(conn, "2026-09-22", "NDF_FIX", 17_820.0)
    _idr_mark(conn, "2026-09-22", "SPOT", 17_850.0)
    conn.commit()
    row = _n1(conn, "2026-09-23")
    assert (row["mark"], row["mark_source"], row["mark_date"]) == (17_820.0, "BBG_BDH", "2026-09-22")
    assert row["pnl_usd"] == pytest.approx(-1_000_000 * (17_820.0 - 17_900.0) / 17_820.0)
    assert (row["spot"], row["spot_source"]) == (pytest.approx(1 / 17_820.0), "BBG_BDH")
    assert row["note"] == "NDF fixed 2026-09-22: at the official fixing of 2026-09-22, converted at the fixing, no delta, no carry"


def test_a_fixing_date_with_no_marks_at_all_carries_the_previous_close_and_the_pnl_stands_still():
    """(c) the 2026-09-22 case: no pull yet on the fixing date, the previous close on file and
    an older fix. The ticket carries the previous close's spot (named as the estimate it is),
    never the older fix, so its P&L is exactly what it was the day before."""
    conn = schema.connect()
    _ndf_book(conn)
    _idr_mark(conn, "2026-09-17", "NDF_FIX", 17_753.0)
    _idr_mark(conn, "2026-09-21", "SPOT", 17_880.0)
    conn.commit()
    before = _n1(conn, "2026-09-21")   # open: marked along the day's curve, spot alone being spot
    assert before["status"] == "OPEN"
    assert before["pnl_usd"] == pytest.approx(-1_000_000 * (17_880.0 - 17_900.0) / 17_880.0)
    row = _n1(conn, "2026-09-22")
    assert row["mark"] == 17_880.0 and row["mark_source"] == "INTERP: SPOT of 2026-09-21 (nearest earlier close)"
    assert row["pnl_usd"] == before["pnl_usd"]
    assert row["note"] == ("NDF fixed 2026-09-22: no official fixing on file: at the spot of 2026-09-22 instead "
                           "(INTERP: SPOT of 2026-09-21 (nearest earlier close)), converted at that spot, no delta, no carry")
    assert row["reason"] == ""


def test_mark_near_never_estimates_a_fixing():
    """The near-marks rule stops at NDF_FIX: the exact row or nothing, whatever neighbours exist."""
    conn = schema.connect()
    _ndf_book(conn)
    _idr_mark(conn, "2026-09-21", "NDF_FIX", 17_700.0)
    _idr_mark(conn, "2026-09-23", "NDF_FIX", 17_800.0)
    conn.commit()
    assert _mark_near(conn, "USDIDR", "2026-09-22", "NDF_FIX", "2026-09-22") is None
    assert _mark_near(conn, "USDIDR", "2026-09-21", "NDF_FIX", "2026-09-21") == (17_700.0, "BBG_BDH")


def test_a_settled_ndf_with_no_realised_row_is_the_fixed_branch_figure_never_the_value_dates_spot():
    """Reviewer, 2026-09-22 (critical): `_frozen_row` had no NDF branch, so a settled NDF with
    no realised_pnl row (every settled NDF after an upload, until the next realise_settled) was
    valued at the last SPOT on or before the VALUE date -- 28,037 before the ledger ran, 3,810
    after, on this probe. One rule, one function: the provisional figure is exactly what the
    open fixed branch gives at the fixing date, before and after the ledger runs."""
    from engine.pnl import ledger
    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES ('USDBRL','FX','USD','BRL',1,1,'USDBRL Curncy','9999-12-31')")
    _insert_trade(conn, "b1", "USDBRL", "FX_FWD", "2026-08-14", 1_000_000, 5.20)   # value Wed 09-16, fixing Mon 09-14
    _insert_legs(conn, [("b1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-14", "2026-09-16", 5.20, 0),
                        ("b1", 2, "FX_NEAR", "BRL", -5_200_000, "2026-08-14", "2026-09-16", 5.20, 0)])
    _insert_mark(conn, "2026-09-14", "USDBRL", "2026-09-14", "SPOT", 5.25, "BBG_BFXFORWARD", "2026-09-14T15:00:00-04:00")
    _insert_mark(conn, "2026-09-16", "USDBRL", "2026-09-16", "SPOT", 5.35, "BBG_BFXFORWARD", "2026-09-16T15:00:00-04:00")
    conn.commit()

    def b1(day):
        return value_book(conn, day).set_index("trade_id").loc["b1"]

    # no fix yet: the fixing date's spot stands in, on the fixing date (open) and after the value
    # date (settled, not yet frozen) alike; the value date's 5.35 never enters
    open_row, settled_row = b1("2026-09-15"), b1("2026-09-21")
    assert (open_row["status"], settled_row["status"]) == ("OPEN", "SETTLED")
    assert open_row["pnl_usd"] == pytest.approx(1_000_000 * (5.25 - 5.20) / 5.25)
    assert settled_row["pnl_usd"] == open_row["pnl_usd"]
    assert settled_row["note"] == ("NDF fixed 2026-09-14: no official fixing on file: at the spot of 2026-09-14 instead, "
                                   "converted at that spot, no delta, no carry; not yet recorded in realised_pnl")
    # the fix lands: the same 3,810 on every screen, before the ledger runs and after
    _insert_mark(conn, "2026-09-14", "USDBRL", "2026-09-14", "NDF_FIX", 5.22, "BBG_BDH", "2026-09-14T17:00:00-04:00")
    conn.commit()
    open_row, settled_row = b1("2026-09-15"), b1("2026-09-21")
    assert open_row["pnl_usd"] == pytest.approx(1_000_000 * (5.22 - 5.20) / 5.22)   # converted at the fix itself
    assert settled_row["pnl_usd"] == open_row["pnl_usd"]
    assert (settled_row["mark"], settled_row["mark_date"], settled_row["mark_source"]) == (5.22, "2026-09-14", "BBG_BDH")
    assert settled_row["note"] == ("NDF fixed 2026-09-14: at the official fixing of 2026-09-14, converted at the fixing, "
                                   "no delta, no carry; not yet recorded in realised_pnl")
    assert ledger.realise_settled(conn, "2026-09-21")["realised"] == 1
    assert b1("2026-09-21")["pnl_usd"] == settled_row["pnl_usd"]


# --------------------------------------------------------------------- non-USD futures
# User decision 2026-09-24, "Spot of valuation date" (CLAUDE.md "P&L conventions -> Futures"):
# a future's P&L is `contracts x multiplier x (m - f)` in its quote currency, converted to USD
# at spot of the valuation date like an FX row, so a CNY or EUR contract is never summed as
# dollars; a settled one at spot of the date of the price it freezes at. A USD contract is
# exactly what it was.


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
    """cu1 expired 2026-09-15, last price on file dated 2026-09-14; USDCNY 7.20 that day and
    7.00 on the valuation date 2026-09-17."""
    _commodity_future(conn, "cu1", "CUZ6 Comdty", "CNY", 5, "2026-09-15", 10, 78_000.0)
    _insert_instrument(conn, "USDCNY", "USD", "CNY")
    _insert_mark(conn, "2026-09-14", "CUZ6 Comdty", "2026-09-15", "FUTURE_PX", 78_400.0, "BBG_BDH",
                 "2026-09-14T17:00:00-04:00")
    _insert_mark(conn, "2026-09-14", "USDCNY", "2026-09-14", "SPOT", 7.20, "BBG_BFXFORWARD", "2026-09-14T15:00:00-04:00")
    _insert_mark(conn, "2026-09-17", "USDCNY", "2026-09-17", "SPOT", 7.00, "BBG_BFXFORWARD", _CLOSE)
    conn.commit()


def test_a_settled_cny_future_not_yet_frozen_converts_at_spot_of_its_prices_date_not_of_as_of():
    conn = schema.connect()
    _settled_cny_future(conn)
    cu = _by_id(conn).loc["cu1"]
    assert cu["status"] == "SETTLED" and cu["reason"] == ""
    assert cu["pnl_local"] == 10 * 5 * (78_400.0 - 78_000.0) == 20_000.0
    assert cu["pnl_usd"] == pytest.approx(20_000.0 / 7.20, rel=1e-15)           # 09-14's spot, never 09-17's 7.00
    assert (cu["mark"], cu["mark_date"]) == (78_400.0, "2026-09-14")
    assert (cu["spot"], cu["spot_source"]) == (pytest.approx(1 / 7.20, rel=1e-15), "BBG_BFXFORWARD")
    assert cu["note"] == ("frozen at settlement price dated 2026-09-14 (last before settlement); "
                          "not yet recorded in realised_pnl")
    # with no USDCNY spot on file at all it cannot be frozen: blank, never at 1, and the reason
    # names the real gap, the conversion, as the ledger does (reviewer W-3)
    conn.execute("DELETE FROM marks WHERE instrument_id = 'USDCNY'")
    conn.commit()
    cu = _by_id(conn).loc["cu1"]
    assert cu["pnl_usd"] != cu["pnl_usd"] and cu["spot"] != cu["spot"]
    assert cu["reason"] == ("settled trade cu1: no SPOT for USD conversion of CNY on 2026-09-14, "
                            "so it cannot be frozen")
    assert (cu["mark"], cu["mark_date"], cu["pnl_local"]) == (78_400.0, "2026-09-14", 20_000.0)


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
    the date it was frozen at, a USD row spot 1 / identity as always."""
    conn = schema.connect()
    _settled_cny_future(conn)
    _future(conn)
    conn.execute("UPDATE trade_legs SET settle_date = '2026-09-15' WHERE trade_id = 'f1'")
    columns = ("trade_id, instrument_id, product, currency, settle_date, local_amount, usd_entry_amount, "
               "mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note")
    s = 1 / 7.20
    conn.executemany(f"INSERT INTO realised_pnl ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("cu1", "CUZ6 Comdty", "FUTURE", "CNY", "2026-09-15", 10, 10 * 5 * 78_000.0 * s, "FUTURE_PX",
         5 * 78_400.0 * s, "2026-09-14", "BBG_BDH", 20_000.0 * s, "2026-09-16T00:00:00", "stored note"),
        ("f1", "ESZ6 Index", "FUTURE", "USD", "2026-09-15", 3, 3 * 50 * 6000.0, "FUTURE_PX",
         50 * 6100.0, "2026-09-15", "BBG_BDH", 15_000.0, "2026-09-16T00:00:00", ""),
    ])
    conn.commit()
    vb = _by_id(conn)
    cu, es = vb.loc["cu1"], vb.loc["f1"]
    assert cu["pnl_usd"] == 20_000.0 * s and cu["note"] == "stored note"
    assert (cu["spot"], cu["spot_source"]) == (pytest.approx(s, rel=1e-15), "BBG_BFXFORWARD")
    assert (es["pnl_usd"], es["spot"], es["spot_source"]) == (15_000.0, 1.0, "identity")
