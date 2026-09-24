"""trades_official view.

2026-09-16: BNP was, at the time, kept alongside the live blotter as a second trade
source (for its `positions` cash-balance snapshot and for a BNP-vs-blotter
reconciliation check), so this view filtered `source != 'BNP'` to stop the same
economic trade loaded from both sources (docs/open-questions.md item 55) being summed
twice into the ladder, delta and P&L.

2026-09-17 ("no bnp fall back", docs/bnp-excel-removal.md): BNP is no longer a trade
source at all -- nothing writes `trades.source = 'BNP'` any more -- so the view's filter
is now a no-op and has been simplified to a plain passthrough (`SELECT * FROM trades`).
The double-count *scenario* this view used to guard against can no longer arise (there
is only one live trade source), so the tests that exercised it directly were removed
along with `engine/pnl/reconcile.py` (the module that explicitly needed to see both
sources to compare them). What remains here: the view exists, is a passthrough, and has
the same columns as `trades`.
"""
from __future__ import annotations

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


def test_trades_official_is_a_passthrough_of_every_source():
    """The view no longer filters by source (see module docstring): every row in
    `trades`, including a legacy source='BNP' row that might still exist in an old
    database nothing writes to any more, surfaces through trades_official unchanged."""
    conn = _conn()
    _seed_instrument(conn)
    _insert_trade(conn, "bnp-1", "BNP", "USDJPY", 1_000_000, 147.0)
    _insert_trade(conn, "xlsx-1", "XLSX", "USDJPY", 1_000_000, 147.0)
    _insert_trade(conn, "manual-1", "MANUAL", "USDJPY", 1_000_000, 147.0)

    ids = {r[0] for r in conn.execute("SELECT trade_id FROM trades_official")}
    assert ids == {"bnp-1", "xlsx-1", "manual-1"}


def test_trades_official_has_same_columns_as_trades():
    conn = _conn()
    trades_cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    view_cols = [r[1] for r in conn.execute("PRAGMA table_info(trades_official)")]
    assert view_cols == trades_cols


# --------------------------------------------------------------------------- passthrough sanity
# 2026-09-17: the double-count scenario these tests used to guard (the same economic
# trade loaded once from BNP and once from the blotter under different trade_id
# schemes, docs/open-questions.md item 55) can no longer arise -- nothing writes
# trades.source='BNP' any more, so there is only ever one live trade source. What
# remains worth pinning is that ladder/delta queries simply see every trade in
# `trades_official` (now a plain passthrough), with no special-casing by source.

def test_ladder_and_delta_see_every_source_now_the_view_is_a_passthrough():
    from engine.ladder.ladder import cash_ladder, delta_per_ccy

    conn = _conn()
    _seed_instrument(conn)
    _insert_trade(conn, "a-1", "MANUAL", "USDJPY", 1_000_000, 147.0, settle_date="2026-08-20")
    _insert_trade(conn, "b-1", "XLSX", "USDJPY", 500_000, 147.0, settle_date="2026-08-20")

    delta = delta_per_ccy(conn, "2026-08-17")
    usd_row = delta[delta["ccy"] == "USD"]
    assert len(usd_row) == 1
    assert usd_row["delta"].iloc[0] == pytest.approx(1_500_000)

    ladder = cash_ladder(conn, "2026-08-17")
    leg_rows = ladder[(ladder["kind"] == "LEG") & (ladder["ccy"] == "USD")]
    assert leg_rows["amount"].sum() == pytest.approx(1_500_000)
