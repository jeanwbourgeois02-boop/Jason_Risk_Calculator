"""Manual mark entry for the Market data tab.

MANUAL is official only for DELTA and PREMIUM (CLAUDE.md "Official marks" table). For
SPOT / FWD_OUTRIGHT / FUTURE_PX / PAR_RATE / PV_USD / DV01_USD a MANUAL row is visible on
the Market data tab (see data/bloomberg/inventory.py::mark_inventory, STATUS_MANUAL) but is
never picked up by `marks_official` and never feeds valuation at all for those mark_types
-- `engine/pnl/valuation.py::value_book` reads `marks_official` unconditionally (2026-09-17,
"no bnp fall back" removed the `marks_source` parameter that used to let a caller retry a
non-official source; docs/bnp-excel-removal.md). Do not change `marks_official`
(data/ingest/schema.py) to make MANUAL win for those mark_types.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def write_manual_mark(conn: sqlite3.Connection, as_of: str, instrument_id: str, settle_date: str,
                      mark_type: str, value: float) -> None:
    """Insert/replace one MANUAL mark row, snapped_at = now (resolved local offset)."""
    snapped_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?,?,?)",
                 (as_of, instrument_id, settle_date, mark_type, float(value), "MANUAL", snapped_at))
    conn.commit()
