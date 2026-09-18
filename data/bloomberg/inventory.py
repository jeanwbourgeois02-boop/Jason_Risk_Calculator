"""Market data tab feed: what mark does the book need today, and do we have it.

`mark_inventory(conn, as_of)` lists exactly the marks `data/bloomberg/live.py::build_requests`
would ask Bloomberg for on `as_of` (one SPOT per open FX pair, one FWD_OUTRIGHT per open FX
leg's own settle_date, one FUTURE_PX per open future at its own expiry) and reports what is
actually in `marks` for each: OFFICIAL (present in `marks_official` -- since 2026-09-18 this
includes a BBG_INTERP row for FWD_OUTRIGHT when no BBG_BFXFORWARD row exists for the same key,
data/ingest/schema.py's OFFICIAL_FALLBACK_SOURCE, so INTERP status below is not reachable for
FWD_OUTRIGHT any more), INTERP (present but only as BBG_INTERP for a mark_type with no
official-fallback entry), MANUAL (present but only as a MANUAL row -- official for
DELTA/PREMIUM, informational only here since those mark_types are never requested by
build_requests), or MISSING (no row at all for as_of/instrument/settle/type).

`close_completeness(conn, start, end)` is the calendar strip: one row per business day with
the count of marks the book needed that day (SPOT + FWD_OUTRIGHT + FUTURE_PX per
`_needed_marks`, 2026-09-18 -- SPOT alone used to leave every forward/future's LTD(t)
unpriced on an otherwise "complete" day) versus how many are official, so the Market data tab
can show holes in history at a glance.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

import pandas as pd

STATUS_OFFICIAL = "OFFICIAL"
STATUS_INTERP = "INTERP"
STATUS_MANUAL = "MANUAL"
STATUS_MISSING = "MISSING"


def _option_needed_marks(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """FX_OPTION SPOT/FWD_OUTRIGHT needs, from `data.bloomberg.live.option_needed_marks`
    when that function exists -- looked up via getattr so this module never hard-depends
    on a live.py function that may not have landed in this working tree yet (2026-09-18:
    landed on origin/main as `option_needed_marks`, public, already returning exactly
    [{instrument_id, settle_date, mark_type}] -- one SPOT per open FX_OPTION pair, one
    FWD_OUTRIGHT at each open option's own expiry -- so no reshaping is needed here).
    Returns `[]`, unchanged from before this integration, when the function is absent."""
    from data.bloomberg import live
    fn = getattr(live, "option_needed_marks", None)
    if fn is None:
        return []
    return list(fn(conn, as_of))


def _needed_marks(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """Same (instrument_id, settle_date, mark_type) set as live.build_requests, without
    requiring blpapi (RequestRow construction there is Bloomberg-request specific).

    Includes one SPOT per USD-conversion pair an open cross's legs need
    (live._cross_usd_legs, 2026-09-17) even when no instrument row backs it: a cross like
    EURSEK with no direct EURUSD/USDSEK trade must still show that gap here (MISSING,
    forever, until an instrument is added or the pair is traded directly) rather than
    silently never asking -- the same reasoning live.build_requests uses, shared via that
    one function so the two definitions of "needed" can never drift apart.

    Also includes FX_OPTION pair SPOT/FWD_OUTRIGHT needs via `_option_needed_marks`
    (2026-09-18), which calls `live.option_needed_marks` when it exists -- same "call
    live, don't reimplement" discipline, so this can never drift from build_requests."""
    from data.bloomberg.live import _OPEN_FX_SQL, _OPEN_FUTURE_SQL, _cross_usd_legs
    out, seen = [], set()
    for instrument_id, _ticker, settle in conn.execute(_OPEN_FX_SQL, {"as_of": as_of}):
        if (instrument_id, as_of, "SPOT") not in seen:
            seen.add((instrument_id, as_of, "SPOT"))
            out.append({"instrument_id": instrument_id, "settle_date": as_of, "mark_type": "SPOT"})
        if (instrument_id, settle, "FWD_OUTRIGHT") not in seen:
            seen.add((instrument_id, settle, "FWD_OUTRIGHT"))
            out.append({"instrument_id": instrument_id, "settle_date": settle, "mark_type": "FWD_OUTRIGHT"})
    for leg in _cross_usd_legs(conn, as_of):
        key = (leg["instrument_id"], as_of, "SPOT")
        if key not in seen:
            seen.add(key)
            out.append({"instrument_id": leg["instrument_id"], "settle_date": as_of, "mark_type": "SPOT"})
    for item in _option_needed_marks(conn, as_of):
        key = (item["instrument_id"], item["settle_date"], item["mark_type"])
        if key not in seen:
            seen.add(key)
            out.append(item)
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


def stale_empty_pull_reason(conn: sqlite3.Connection, status: Optional[dict], as_of: str) -> Optional[str]:
    """None when the last recorded feed status (data.bloomberg.live.read_status) looks
    trustworthy right now; otherwise a plain-English reason a "Last marks pull" diagnostic
    must not report PASS, even though the pull itself reported connected=True.

    Found on the Bloomberg PC 2026-09-17: the live feed's first pull runs the instant the
    app starts, almost always before any trade has been uploaded through the browser, so
    live.build_requests() finds nothing open to price and the cycle writes a perfectly
    honest {connected: True, requested: 0, written: 0} -- correct at the moment it ran.
    That status is never refreshed until the next scheduled pull (data.bloomberg.live
    .INTERVAL_SECONDS later, or data.bloomberg.live.LiveFeed.trigger_now() sooner), so a
    user who uploads a blotter and checks diagnostics inside that window sees a stale
    "0 requested" pull reported as PASS while marks the book now needs are missing
    entirely. This compares what the DB needs *right now* (_needed_marks) against what the
    *last recorded pull* actually asked for, not against the live book state at pull time,
    which the stored status has no way to express.

    A real connection failure (connected=False) is not this function's job -- the caller's
    ordinary status.get("connected") handling already reports that as FAIL. This only
    covers the specific "connected fine, asked for nothing, but something is now needed"
    trap."""
    if not status or not status.get("connected"):
        return None
    if status.get("requested", 0):
        return None
    needed = _needed_marks(conn, as_of)
    if not needed:
        return None
    example = needed[0]
    when = status.get("time", "an unknown time")
    return (f"the last recorded pull ({when}) asked Bloomberg for 0 marks, but {len(needed)} "
            f"mark(s) are needed right now for {as_of} (e.g. {example['instrument_id']} "
            f"{example['mark_type']} {example['settle_date']}) -- trades were likely imported "
            "after that pull ran. Press \"Pull now\" on the Market data tab, or wait for the "
            "next automatic pull (every 2 minutes).")


def close_completeness(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """One row per business day in [start, end]: as_of_date, needed, present, complete,
    missing (list of {instrument_id, settle_date, mark_type} still missing that day).

    2026-09-18 (BUILD_PLAN.md section 3 / CLAUDE.md "P&L conventions": Daily/5d/MTD/YTD
    all difference LTD(t) against LTD(t-1bd) etc., and every FX leg's LTD needs the
    FWD_OUTRIGHT for its own settle_date, every future's needs FUTURE_PX): `needed`/
    `present` now cover every mark_type the book needed on that day -- SPOT,
    FWD_OUTRIGHT (per open leg's own settle_date) and FUTURE_PX (per open future) -- via
    the same `_needed_marks` `live.build_requests` and `backfill.backfill` both use, not
    SPOT alone. A day used to count as "complete" from a bare SPOT close even though
    every forward's LTD(t) was unpriced -- found live on the Bloomberg PC's first launch,
    where the header's period cards silently excluded the whole FX book on every
    reference date. `needed == 0` still reports `complete = False` (nothing to confirm
    against), unchanged from before this fix."""
    from data.bloomberg.backfill import business_days
    from datetime import date as _date
    days = business_days(_date.fromisoformat(start), _date.fromisoformat(end))
    rows = []
    for d in days:
        day = d.isoformat()
        needed_items = _needed_marks(conn, day)
        present = 0
        missing = []
        for item in needed_items:
            hit = conn.execute(
                "SELECT 1 FROM marks_official WHERE as_of_date=:as_of AND instrument_id=:instrument_id "
                "AND settle_date=:settle AND mark_type=:mark_type",
                {"as_of": day, "instrument_id": item["instrument_id"], "settle": item["settle_date"],
                 "mark_type": item["mark_type"]}).fetchone()
            if hit:
                present += 1
            else:
                missing.append(item)
        rows.append({"as_of_date": day, "needed": len(needed_items), "present": present,
                     "complete": len(needed_items) > 0 and present >= len(needed_items), "missing": missing})
    return pd.DataFrame(rows, columns=["as_of_date", "needed", "present", "complete", "missing"])
