"""Blotter-vs-BNP end-of-day trade reconciliation.

This is a new, small, independent check. It is NOT a resurrection of the retired
workbook-parity arithmetic in `engine/pnl/pnl.py` (`workbook_fx_pnl` and friends) and NOT
a resurrection of the retired `ui/tabs/reconciliation.py` -- it does not read `pnl.py` and
does not touch marks at all.

Context (CLAUDE.md "Repository layout and ownership", user decision 2026-09-16): the app
now has two independent trade sources for the same macro trader's book that should agree
at end of day --

- the real-time blotter (`data/ingest/blotter.py`), `trades.source = 'XLSX'`;
- the once-daily BNP PB snapshot taken at EOD Hong Kong close (`data/ingest/bnp.py`),
  `trades.source = 'BNP'`.

`reconcile_blotter_vs_bnp` nets both sides per `instrument_id` and flags pairs that
disagree outside tolerance, for a UI table to render. It computes nothing else: no P&L,
no marks, no USD conversion beyond the notional netting described below.

Netting logic mirrors CLAUDE.md "Reconciliation checks and tolerances": "Netting xlsx
Quantity by pair for trades dated <= T-1 must equal the BNP file dated T converted to USD
notional." Here "xlsx" trades are blotter (source XLSX) trades in place of the retired
workbook's rows, and both sides are netted using the "Display notional" convention from
CLAUDE.md "P&L conventions": per trade, USD notional = sign(trades.quantity) * |amount of
the trade_legs row with ccy = 'USD'|, summed per instrument_id.
`engine/pnl/aggregate.py::aggregate_by_pair` computes the analogous quantity from a
`ltd_per_trade` frame (the workbook-parity path, which needs marks); this module
recomputes the USD-leg sum directly from `trades`/`trade_legs` instead, so it has no
dependency on `pnl.py` or on any mark being present.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

BLOTTER_SOURCE = "XLSX"
BNP_SOURCE = "BNP"

COLUMNS = [
    "instrument_id", "blotter_usd_notional", "bnp_usd_notional", "diff_usd",
    "tolerance_usd", "within_tolerance", "n_blotter_trades", "n_bnp_trades",
]

# Default tolerance. CLAUDE.md's "Reconciliation checks and tolerances" section gives
# per-row identity tolerances (e.g. |Quantity*rate - Local Cost| <= 1 quote unit) but no
# number for this pair-level *netted* comparison. Netting N trades per side can
# accumulate up to roughly N quote units of BNP Local-Cost rounding residue (BNP rounds
# Local Cost to whole quote units per trade), so a fixed cent-level tolerance would
# false-flag a clean book with many small trades. Chosen default: max($1.00, 0.5e-6 *
# larger side) -- the same $1 absolute floor and 0.5e-6 relative scale CLAUDE.md already
# uses for the MV Local -> MV Base identity, reused here by analogy since the contract
# does not state a pair-level number.
DEFAULT_ABS_TOLERANCE_USD = 1.0
DEFAULT_REL_TOLERANCE = 0.5e-6


def bnp_snapshot_date(conn: sqlite3.Connection, as_of_date: str) -> str | None:
    """The latest date BNP has actually reported through, on or before as_of_date, or
    None if BNP has never reported anything by then.

    BNP is a once-daily EOD-Hong-Kong snapshot; the blotter is real-time. Comparing
    "blotter trades up to today" against "BNP trades up to today" would flag every
    trade booked since BNP's last snapshot as a mismatch, when nothing is actually
    wrong -- BNP simply has not caught up yet. The correct EOD check compares both
    sides only through the date BNP itself has actually reported.

    Preferred signal: MAX(positions.as_of_date) where source='BNP' -- the date BNP's
    own snapshot claims to be as of. Falls back to MAX(trades.trade_date) where
    source='BNP' if BNP has trades but (for this date range) no positions row yet --
    e.g. a BNP file containing only IRS rows, which write no positions row per
    CLAUDE.md's BNP-file contract.
    """
    row = conn.execute(
        "SELECT MAX(as_of_date) FROM positions WHERE source = ? AND as_of_date <= ?",
        (BNP_SOURCE, as_of_date),
    ).fetchone()
    if row and row[0]:
        return row[0]
    row = conn.execute(
        "SELECT MAX(trade_date) FROM trades WHERE source = ? AND trade_date <= ?",
        (BNP_SOURCE, as_of_date),
    ).fetchone()
    return row[0] if row and row[0] else None


def _net_usd_notional_by_pair(conn: sqlite3.Connection, source: str, as_of_date: str) -> pd.DataFrame:
    """Sum of sign(quantity) * |USD leg amount| per instrument_id, over `source` trades
    with trade_date <= as_of_date.

    Uses the same USD-leg lookup as `aggregate.aggregate_by_pair`: query
    `trade_legs WHERE ccy = 'USD'` rather than assume a leg position, because the USD leg
    is leg 1 for a USDXXX pair (amount = quantity) and leg 2 for an XXXUSD pair
    (amount = -quantity * price) -- see agent memory data_quirks.md. A trade with no USD
    leg at all (a genuine cross, e.g. EURSEK) contributes 0 to this check; this
    reconciliation only nets the pair's own USD leg, mirroring the xlsx display
    convention it is checked against, and the reference book has no crosses.
    """
    trades = pd.read_sql_query(
        "SELECT trade_id, instrument_id, quantity FROM trades "
        "WHERE source = :source AND trade_date <= :as_of",
        conn, params={"source": source, "as_of": as_of_date},
    )
    if trades.empty:
        return pd.DataFrame(columns=["instrument_id", "usd_notional", "n_trades"])

    trade_ids = trades["trade_id"].tolist()
    placeholders = ",".join("?" for _ in trade_ids)
    legs = pd.read_sql_query(
        f"SELECT trade_id, amount FROM trade_legs "
        f"WHERE ccy = 'USD' AND trade_id IN ({placeholders})",
        conn, params=trade_ids,
    )
    usd_leg_abs = legs.groupby("trade_id")["amount"].sum().abs().rename("usd_leg_abs")

    df = trades.merge(usd_leg_abs, on="trade_id", how="left")
    df["usd_leg_abs"] = df["usd_leg_abs"].fillna(0.0)
    df["signed_usd"] = np.sign(df["quantity"]) * df["usd_leg_abs"]

    return (
        df.groupby("instrument_id")
        .agg(usd_notional=("signed_usd", "sum"), n_trades=("trade_id", "count"))
        .reset_index()
    )


@dataclass(frozen=True)
class PairReconciliation:
    instrument_id: str
    blotter_usd_notional: float
    bnp_usd_notional: float
    diff_usd: float
    tolerance_usd: float
    within_tolerance: bool
    n_blotter_trades: int
    n_bnp_trades: int


def reconcile_blotter_vs_bnp(
    conn: sqlite3.Connection,
    as_of_date: str,
    abs_tolerance_usd: float = DEFAULT_ABS_TOLERANCE_USD,
    rel_tolerance: float = DEFAULT_REL_TOLERANCE,
) -> pd.DataFrame:
    """Per-instrument_id comparison of blotter (source='XLSX') vs BNP (source='BNP') net
    USD notional, both netted through the date BNP has actually reported to (see
    `bnp_snapshot_date`), which may be earlier than `as_of_date`.

    `as_of_date` is the caller's live/requested date (e.g. the Ladder tab's selected
    date, which can be "today"). BNP is a once-daily EOD-Hong-Kong snapshot, so it is
    usually at least one day behind: comparing both sides through `as_of_date` directly
    would flag every blotter trade booked since BNP's last snapshot as a false
    mismatch. Instead, both sides are netted through `bnp_snapshot_date(conn,
    as_of_date)` -- the latest date BNP has actually reported through, on or before
    `as_of_date` -- so a trade too new for BNP to have seen yet is correctly excluded
    from the comparison window on both sides, not just missing from one of them.

    Returns an empty frame (no `instrument_id` rows) if BNP has never reported through
    `as_of_date` at all -- nothing to reconcile yet is not a mismatch. Otherwise, one
    row per instrument_id present on either side as of the resolved snapshot date
    (outer join; a pair on only one side compares against 0.0 on the other and is
    flagged unless the notional itself is within tolerance of 0), columns per
    `COLUMNS`, sorted by instrument_id.
    """
    snapshot_date = bnp_snapshot_date(conn, as_of_date)
    if snapshot_date is None:
        return pd.DataFrame(columns=COLUMNS)
    blotter = _net_usd_notional_by_pair(conn, BLOTTER_SOURCE, snapshot_date)
    bnp = _net_usd_notional_by_pair(conn, BNP_SOURCE, snapshot_date)

    merged = blotter.merge(bnp, on="instrument_id", how="outer", suffixes=("_blotter", "_bnp"))
    for col in ("usd_notional_blotter", "usd_notional_bnp"):
        merged[col] = merged[col].fillna(0.0)
    for col in ("n_trades_blotter", "n_trades_bnp"):
        merged[col] = merged[col].fillna(0).astype(int)

    merged["diff_usd"] = merged["usd_notional_blotter"] - merged["usd_notional_bnp"]
    merged["tolerance_usd"] = np.maximum(
        abs_tolerance_usd,
        rel_tolerance * merged[["usd_notional_blotter", "usd_notional_bnp"]].abs().max(axis=1),
    )
    merged["within_tolerance"] = merged["diff_usd"].abs() <= merged["tolerance_usd"]

    out = merged.rename(columns={
        "usd_notional_blotter": "blotter_usd_notional",
        "usd_notional_bnp": "bnp_usd_notional",
        "n_trades_blotter": "n_blotter_trades",
        "n_trades_bnp": "n_bnp_trades",
    })
    return out[COLUMNS].sort_values("instrument_id").reset_index(drop=True)


def to_records(df: pd.DataFrame) -> List[PairReconciliation]:
    """Convenience conversion of `reconcile_blotter_vs_bnp`'s DataFrame to a list of
    `PairReconciliation` dataclasses, for callers that prefer records over a frame."""
    return [PairReconciliation(**row) for row in df[COLUMNS].to_dict(orient="records")]
