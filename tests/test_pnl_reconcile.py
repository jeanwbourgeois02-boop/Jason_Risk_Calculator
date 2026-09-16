"""Blotter (XLSX source) vs BNP EOD snapshot per-pair netting reconciliation."""
from __future__ import annotations

from pathlib import Path

import pytest

from data.ingest import schema
from engine.pnl.reconcile import (
    COLUMNS,
    PairReconciliation,
    bnp_snapshot_date,
    reconcile_blotter_vs_bnp,
    to_records,
)

AS_OF = "2026-08-17"


def _make_conn():
    conn = schema.connect(":memory:")
    conn.executemany(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        [
            ("USDJPY", "FX", "USD", "JPY", 1.0, 0, "USDJPY Curncy", "9999-12-31"),
            ("AUDUSD", "FX", "AUD", "USD", 1.0, 0, "AUDUSD Curncy", "9999-12-31"),
            ("USDCAD", "FX", "USD", "CAD", 1.0, 0, "USDCAD Curncy", "9999-12-31"),
        ],
    )
    return conn


def _insert_trade(conn, trade_id, source, instrument_id, quantity, price,
                   settle_date, trade_date="2026-08-01"):
    """Mirrors tests/test_pnl.py::_insert_trade, with an explicit `source` so both the
    XLSX (blotter) and BNP sources can be populated in the same in-memory DB."""
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, source, instrument_id, "FX_FWD", trade_id, trade_date, quantity, price,
         "ACC", "CPTY", "STRAT", "TRADER", "test trade", ""),
    )
    base_ccy = instrument_id[:3]
    quote_ccy = instrument_id[3:]
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 1, "FX_NEAR", base_ccy, quantity, trade_date, settle_date, price, 1),
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 2, "FX_NEAR", quote_ccy, -quantity * price, trade_date, settle_date, price, 1),
    )
    conn.commit()


def test_matching_pair_reconciles_clean():
    conn = _make_conn()
    # USDJPY: blotter bought 10m USD, BNP sees the same trade -- same USD leg, same sign.
    _insert_trade(conn, "XL-1", "XLSX", "USDJPY", 10_000_000, 148.50, "2026-08-03")
    _insert_trade(conn, "BNP-1", "BNP", "USDJPY", 10_000_000, 148.50, "2026-08-03")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)

    assert list(out.columns) == COLUMNS
    row = out[out["instrument_id"] == "USDJPY"].iloc[0]
    assert row["blotter_usd_notional"] == pytest.approx(10_000_000)
    assert row["bnp_usd_notional"] == pytest.approx(10_000_000)
    assert row["diff_usd"] == pytest.approx(0.0)
    assert bool(row["within_tolerance"]) is True
    assert row["n_blotter_trades"] == 1
    assert row["n_bnp_trades"] == 1


def test_mismatched_pair_is_flagged():
    conn = _make_conn()
    # AUDUSD: blotter has 5m AUD long (USD leg = -5m*0.66), BNP only booked 3m so far --
    # a genuine, well outside-tolerance disagreement.
    _insert_trade(conn, "XL-2", "XLSX", "AUDUSD", 5_000_000, 0.66, "2026-08-03")
    _insert_trade(conn, "BNP-2", "BNP", "AUDUSD", 3_000_000, 0.66, "2026-08-03")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)

    row = out[out["instrument_id"] == "AUDUSD"].iloc[0]
    assert row["blotter_usd_notional"] == pytest.approx(5_000_000 * 0.66)
    assert row["bnp_usd_notional"] == pytest.approx(3_000_000 * 0.66)
    assert row["diff_usd"] == pytest.approx(2_000_000 * 0.66)
    assert bool(row["within_tolerance"]) is False


def test_small_rounding_residual_is_within_tolerance():
    conn = _make_conn()
    # Two trades netting to the same USD notional up to a $0.50 residual (BNP rounds
    # Local Cost to whole quote units) should not be flagged by the default tolerance.
    _insert_trade(conn, "XL-3", "XLSX", "USDCAD", 1_000_000, 1.35, "2026-08-03")
    _insert_trade(conn, "BNP-3", "BNP", "USDCAD", 999_999.50, 1.35, "2026-08-03")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)

    row = out[out["instrument_id"] == "USDCAD"].iloc[0]
    assert row["diff_usd"] == pytest.approx(0.50)
    assert bool(row["within_tolerance"]) is True


def test_pair_present_only_in_blotter_is_flagged_against_zero():
    conn = _make_conn()
    _insert_trade(conn, "XL-4", "XLSX", "USDJPY", 1_000_000, 148.0, "2026-08-03")
    # A BNP baseline (unrelated pair) so bnp_snapshot_date resolves to a real date --
    # without any BNP presence at all, reconcile_blotter_vs_bnp correctly treats this
    # as "BNP has never reported, nothing to reconcile yet" (its own dedicated case,
    # see test_reconcile_returns_empty_frame_when_bnp_has_never_reported below), not a
    # flagged mismatch. This test is about the outer-join zero-fill for a pair BNP
    # genuinely never had, given BNP has reported at all.
    _insert_trade(conn, "BNP-baseline", "BNP", "USDCAD", 500_000, 1.35, "2026-08-20", trade_date="2026-08-01")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)

    row = out[out["instrument_id"] == "USDJPY"].iloc[0]
    assert row["blotter_usd_notional"] == pytest.approx(1_000_000)
    assert row["bnp_usd_notional"] == pytest.approx(0.0)
    assert row["n_blotter_trades"] == 1
    assert row["n_bnp_trades"] == 0
    assert bool(row["within_tolerance"]) is False


def test_pair_present_only_in_bnp_is_flagged_against_zero():
    conn = _make_conn()
    _insert_trade(conn, "BNP-4", "BNP", "AUDUSD", 2_000_000, 0.65, "2026-08-03")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)

    row = out[out["instrument_id"] == "AUDUSD"].iloc[0]
    assert row["blotter_usd_notional"] == pytest.approx(0.0)
    assert row["bnp_usd_notional"] == pytest.approx(2_000_000 * 0.65)
    assert row["n_blotter_trades"] == 0
    assert row["n_bnp_trades"] == 1
    assert bool(row["within_tolerance"]) is False


def test_trade_dated_after_as_of_is_excluded_from_both_sides():
    conn = _make_conn()
    _insert_trade(conn, "XL-5", "XLSX", "USDJPY", 1_000_000, 148.0, "2026-08-25",
                   trade_date="2026-08-20")
    _insert_trade(conn, "BNP-5", "BNP", "USDJPY", 1_000_000, 148.0, "2026-08-25",
                   trade_date="2026-08-20")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)

    assert out.empty


def test_empty_book_returns_empty_frame_with_expected_columns():
    conn = _make_conn()
    out = reconcile_blotter_vs_bnp(conn, AS_OF)
    assert out.empty
    assert list(out.columns) == COLUMNS


# --------------------------------------------------------------------------- lag handling
# (user finding, 2026-09-16: BNP is a once-daily EOD-Hong-Kong snapshot, the blotter is
# real-time, so comparing both sides through "today" would flag every trade booked
# since BNP's last snapshot as a false mismatch.)

def test_reconcile_returns_empty_frame_when_bnp_has_never_reported():
    # Blotter has real data, but BNP has zero presence anywhere (no positions, no BNP
    # trades) -- not a mismatch, just "nothing to reconcile yet".
    conn = _make_conn()
    _insert_trade(conn, "XL-7", "XLSX", "USDJPY", 1_000_000, 148.0, "2026-08-03")
    out = reconcile_blotter_vs_bnp(conn, AS_OF)
    assert out.empty
    assert list(out.columns) == COLUMNS


def test_bnp_snapshot_date_resolves_from_positions(tmp_path=None):
    conn = _make_conn()
    conn.execute(
        "INSERT INTO instruments VALUES ('CASH-USD','CASH','USD','USD',1,0,'','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO positions VALUES ('2026-08-15','BNP','ACC','CASH-USD','2026-08-15',"
        "1000,1000,1,1,1000,1000,0,0,0)"
    )
    assert bnp_snapshot_date(conn, AS_OF) == "2026-08-15"


def test_bnp_snapshot_date_falls_back_to_trades_when_no_positions():
    conn = _make_conn()
    _insert_trade(conn, "BNP-7", "BNP", "USDCAD", 500_000, 1.35, "2026-08-20", trade_date="2026-08-05")
    assert bnp_snapshot_date(conn, AS_OF) == "2026-08-05"


def test_bnp_snapshot_date_none_when_bnp_never_reported():
    conn = _make_conn()
    _insert_trade(conn, "XL-8", "XLSX", "USDJPY", 1_000_000, 148.0, "2026-08-03")
    assert bnp_snapshot_date(conn, AS_OF) is None


def test_bnp_snapshot_date_ignores_positions_after_as_of():
    conn = _make_conn()
    conn.execute(
        "INSERT INTO instruments VALUES ('CASH-USD','CASH','USD','USD',1,0,'','9999-12-31')"
    )
    conn.executemany(
        "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2026-08-10", "BNP", "ACC", "CASH-USD", "2026-08-10", 1000, 1000, 1, 1, 1000, 1000, 0, 0, 0),
            ("2026-08-25", "BNP", "ACC", "CASH-USD", "2026-08-25", 2000, 2000, 1, 1, 2000, 2000, 0, 0, 0),
        ],
    )
    # AS_OF is 2026-08-17: the 08-25 snapshot is in the future relative to AS_OF and
    # must not be picked, even though it is BNP's most recent snapshot overall.
    assert bnp_snapshot_date(conn, AS_OF) == "2026-08-10"


def test_blotter_trade_newer_than_bnp_snapshot_excluded_from_comparison():
    # BNP's last snapshot is 2026-08-10. A blotter trade booked 2026-08-15 (after that,
    # but still <= AS_OF) is real-time activity BNP has not caught up to yet -- it must
    # not appear in the comparison at all (neither flagged nor silently zero-filled),
    # since including it would either false-flag it or falsely claim it reconciles.
    conn = _make_conn()
    conn.execute(
        "INSERT INTO instruments VALUES ('CASH-USD','CASH','USD','USD',1,0,'','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO positions VALUES ('2026-08-10','BNP','ACC','CASH-USD','2026-08-10',"
        "1000,1000,1,1,1000,1000,0,0,0)"
    )
    _insert_trade(conn, "XL-9", "XLSX", "USDJPY", 1_000_000, 148.0, "2026-09-01",
                   trade_date="2026-08-15")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)

    assert "USDJPY" not in set(out["instrument_id"])


def test_to_records_round_trips_dataframe_rows():
    conn = _make_conn()
    _insert_trade(conn, "XL-6", "XLSX", "USDJPY", 1_000_000, 148.0, "2026-08-03")
    _insert_trade(conn, "BNP-6", "BNP", "USDJPY", 1_000_000, 148.0, "2026-08-03")

    out = reconcile_blotter_vs_bnp(conn, AS_OF)
    records = to_records(out)

    assert len(records) == len(out)
    assert isinstance(records[0], PairReconciliation)
    assert records[0].instrument_id == "USDJPY"
    assert bool(records[0].within_tolerance) is True
