"""Market data tab feed: what mark does the book need today, and do we have it.

`mark_inventory(conn, as_of)` lists exactly the marks `data/bloomberg/live.py::build_requests`
would ask Bloomberg for on `as_of` (one SPOT per open FX pair, one FWD_OUTRIGHT per open FX
leg's own settle_date, one FUTURE_PX per open future at its own expiry) and reports what is
actually in `marks` for each: OFFICIAL (present in `marks_official`), INTERP (present but only
as BBG_INTERP, so a fallback and never official for FWD_OUTRIGHT), MANUAL (present but only as
a MANUAL row -- official for DELTA/PREMIUM, informational only here since those mark_types are
never requested by build_requests), or MISSING (no row at all for as_of/instrument/settle/type).

`close_completeness(conn, start, end)` is the calendar strip: one row per business day with
the count of marks the book needed that day (traded USD FX pairs, per CLAUDE.md backfill
scope) versus how many have an official SPOT mark, so the Market data tab can show holes in
history at a glance.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List

import pandas as pd

STATUS_OFFICIAL = "OFFICIAL"
STATUS_INTERP = "INTERP"
STATUS_MANUAL = "MANUAL"
STATUS_MISSING = "MISSING"


def _needed_marks(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """Same (instrument_id, settle_date, mark_type) set as live.build_requests, without
    requiring blpapi (RequestRow construction there is Bloomberg-request specific)."""
    from data.bloomberg.live import _OPEN_FX_SQL, _OPEN_FUTURE_SQL
    out, seen = [], set()
    for instrument_id, _ticker, settle in conn.execute(_OPEN_FX_SQL, {"as_of": as_of}):
        if (instrument_id, as_of, "SPOT") not in seen:
            seen.add((instrument_id, as_of, "SPOT"))
            out.append({"instrument_id": instrument_id, "settle_date": as_of, "mark_type": "SPOT"})
        if (instrument_id, settle, "FWD_OUTRIGHT") not in seen:
            seen.add((instrument_id, settle, "FWD_OUTRIGHT"))
            out.append({"instrument_id": instrument_id, "settle_date": settle, "mark_type": "FWD_OUTRIGHT"})
    for instrument_id, _ticker, settle in conn.execute(_OPEN_FUTURE_SQL, {"as_of": as_of}):
        if (instrument_id, settle, "FUTURE_PX") not in seen:
            seen.add((instrument_id, settle, "FUTURE_PX"))
            out.append({"instrument_id": instrument_id, "settle_date": settle, "mark_type": "FUTURE_PX"})
    return out


def mark_inventory(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """One row per mark the book needs on `as_of`:
    instrument_id, settle_date, mark_type, value, source, snapped_at, status.
    status is OFFICIAL (from marks_official) | INTERP (BBG_INTERP present, official absent) |
    MANUAL (MANUAL present, official absent) | MISSING (nothing at all). `value`/`source`/
    `snapped_at` are the row backing that status, or None when MISSING."""
    needed = _needed_marks(conn, as_of)
    rows = []
    for item in needed:
        instrument_id, settle, mark_type = item["instrument_id"], item["settle_date"], item["mark_type"]
        params = {"as_of": as_of, "instrument_id": instrument_id, "settle": settle, "mark_type": mark_type}
        official = conn.execute(
            "SELECT value, source, snapped_at FROM marks_official WHERE as_of_date=:as_of "
            "AND instrument_id=:instrument_id AND settle_date=:settle AND mark_type=:mark_type",
            params).fetchone()
        if official is not None:
            value, source, snapped_at, status = official[0], official[1], official[2], STATUS_OFFICIAL
        else:
            fallback = conn.execute(
                "SELECT value, source, snapped_at FROM marks WHERE as_of_date=:as_of "
                "AND instrument_id=:instrument_id AND settle_date=:settle AND mark_type=:mark_type "
                "AND source IN ('BBG_INTERP', 'MANUAL') ORDER BY snapped_at DESC LIMIT 1",
                params).fetchone()
            if fallback is not None:
                value, source, snapped_at = fallback
                status = STATUS_INTERP if source == "BBG_INTERP" else STATUS_MANUAL
            else:
                value, source, snapped_at, status = None, None, None, STATUS_MISSING
        rows.append({"instrument_id": instrument_id, "settle_date": settle, "mark_type": mark_type,
                     "value": value, "source": source, "snapped_at": snapped_at, "status": status})
    return pd.DataFrame(rows, columns=["instrument_id", "settle_date", "mark_type", "value", "source",
                                       "snapped_at", "status"])


def close_completeness(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """One row per business day in [start, end]: as_of_date, needed (traded USD FX pairs),
    present (official SPOT marks that day), complete (present >= needed and needed > 0)."""
    from data.bloomberg.backfill import business_days, traded_pairs
    from datetime import date as _date
    pairs = traded_pairs(conn)
    needed = len(pairs)
    days = business_days(_date.fromisoformat(start), _date.fromisoformat(end))
    rows = []
    for d in days:
        day = d.isoformat()
        present = conn.execute(
            "SELECT COUNT(DISTINCT instrument_id) FROM marks_official "
            "WHERE mark_type='SPOT' AND as_of_date=?", (day,)).fetchone()[0]
        rows.append({"as_of_date": day, "needed": needed, "present": present,
                     "complete": needed > 0 and present >= needed})
    return pd.DataFrame(rows, columns=["as_of_date", "needed", "present", "complete"])
