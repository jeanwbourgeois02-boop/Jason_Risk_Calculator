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
    import pytest
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
    import pytest
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
