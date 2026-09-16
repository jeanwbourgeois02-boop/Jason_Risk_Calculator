"""trades_official view (2026-09-16): BNP is no longer an authoritative trade source
for live exposure/P&L, only the blotter ('XLSX') and 'MANUAL' are -- BNP is kept only
for its `positions` cash-balance snapshot and for engine/pnl/reconcile.py's explicit
BNP-vs-blotter comparison. Without this view, the same economic trade loaded from both
sources (docs/open-questions.md item 55 -- their trade_id schemes differ, so they don't
collide on the primary key) would be summed twice into the ladder, delta and P&L.

This file tests the view and the actual double-count scenario directly. Individual
engine modules (engine/ladder/ladder.py, engine/pnl/pnl.py, etc.) have their own tests
covering their specific query correctness; this file is about the cross-cutting
guarantee that BNP trades never leak into those calculations.
"""
from __future__ import annotations

import sqlite3

import pytest

from data.ingest import schema


def _conn():
    return schema.connect(":memory:")


def _seed_instrument(conn, instrument_id="USDJPY", base="USD", quote="JPY"):
    conn.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "FX", base, quote, 1, 0, f"{instrument_id} Curncy", "9999-12-31"),
    )


def _insert_trade(conn, trade_id, source, instrument_id, quantity, price,
                  trade_date="2026-08-01", settle_date="2026-08-20"):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, source, instrument_id, "FX_FWD", trade_id, trade_date, quantity, price,
         "acc", "cp", "HAHY7", "trader", "desc", ""),
    )
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (trade_id, 1, "FX_NEAR", "USD", quantity, trade_date, settle_date, price, 1),
            (trade_id, 2, "FX_NEAR", "JPY", -quantity * price, trade_date, settle_date, price, 1),
        ],
    )


# --------------------------------------------------------------------------- the view itself

def test_trades_official_view_exists():
    conn = _conn()
    views = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view'"
    )}
    assert "trades_official" in views


def test_trades_official_excludes_bnp_includes_xlsx_and_manual():
    conn = _conn()
    _seed_instrument(conn)
    _insert_trade(conn, "bnp-1", "BNP", "USDJPY", 1_000_000, 147.0)
    _insert_trade(conn, "xlsx-1", "XLSX", "USDJPY", 1_000_000, 147.0)
    _insert_trade(conn, "manual-1", "MANUAL", "USDJPY", 1_000_000, 147.0)

    ids = {r[0] for r in conn.execute("SELECT trade_id FROM trades_official")}
    assert ids == {"xlsx-1", "manual-1"}


def test_trades_official_has_same_columns_as_trades():
    conn = _conn()
    trades_cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    view_cols = [r[1] for r in conn.execute("PRAGMA table_info(trades_official)")]
    assert view_cols == trades_cols


# --------------------------------------------------------------------------- the actual bug

def test_same_pair_from_both_sources_is_not_double_counted_in_delta():
    """The scenario item 55 warns about: the same economic USDJPY trade loaded once
    from BNP and once from the blotter under different trade_ids. delta_per_ccy must
    count it once (the blotter side), not twice."""
    from engine.ladder.ladder import delta_per_ccy

    conn = _conn()
    _seed_instrument(conn)
    _insert_trade(conn, "bnp-1", "BNP", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")
    _insert_trade(conn, "xlsx-1", "XLSX", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")

    delta = delta_per_ccy(conn, "2026-08-17")
    usd_row = delta[delta["ccy"] == "USD"]
    assert len(usd_row) == 1
    # If both sources were counted, this would be 2,000,000, not 1,000,000.
    assert usd_row["delta"].iloc[0] == pytest.approx(1_000_000)


def test_same_pair_from_both_sources_is_not_double_counted_in_cash_ladder_legs():
    from engine.ladder.ladder import cash_ladder

    conn = _conn()
    _seed_instrument(conn)
    _insert_trade(conn, "bnp-1", "BNP", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")
    _insert_trade(conn, "xlsx-1", "XLSX", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")

    ladder = cash_ladder(conn, "2026-08-17")
    leg_rows = ladder[(ladder["kind"] == "LEG") & (ladder["ccy"] == "USD")]
    assert leg_rows["amount"].sum() == pytest.approx(1_000_000)


def test_same_pair_from_both_sources_is_not_double_counted_in_ltd_per_trade():
    from engine.pnl.pnl import ltd_per_trade

    conn = _conn()
    _seed_instrument(conn)
    _insert_trade(conn, "bnp-1", "BNP", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")
    _insert_trade(conn, "xlsx-1", "XLSX", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")

    out = ltd_per_trade(conn, "2026-08-17", strict=False)
    assert list(out["trade_id"]) == ["xlsx-1"]


def test_engine_pnl_reconcile_is_the_one_module_that_still_sees_both_sources():
    """engine/pnl/reconcile.py is the sole exception -- it explicitly needs both
    sources to compare them, so it must read raw `trades`, not `trades_official`."""
    from engine.pnl.reconcile import reconcile_blotter_vs_bnp

    conn = _conn()
    _seed_instrument(conn)
    _insert_trade(conn, "bnp-1", "BNP", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")
    _insert_trade(conn, "xlsx-1", "XLSX", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")

    out = reconcile_blotter_vs_bnp(conn, "2026-08-17")
    row = out[out["instrument_id"] == "USDJPY"].iloc[0]
    assert row["n_blotter_trades"] == 1
    assert row["n_bnp_trades"] == 1
