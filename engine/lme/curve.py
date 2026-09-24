"""Reading an LME curve from the official marks (lme-forwards lane).

A day's LME curve is the metal's official SPOT (the cash price) placed at that day's cash
date, and its official FWD_OUTRIGHT rows (3M and the monthly prompts, plus any broken date
bbg-curves interpolated) at their own prompt dates. It reads `marks_official` only (hard
rule 3) and writes nothing.

This is the reference reader for the lanes that want a prompt's forward without a P&L
(curve-positions, expiry-monitor). It never extrapolates beyond the last pillar Bloomberg
gave: past it the answer is None. The P&L's own estimate for a missing mark is
pnl-valuation's near-marks rule (`engine/pnl/valuation.py::_mark_near`, hard rule 2), which
uses the same pillars once it places the cash price at ``cash_date`` (see the Handoff).
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from typing import Optional

from engine.lme.metals import lme_root
from engine.lme.prompts import DateLike, _d, cash_date

__all__ = ["day_curve", "forward_at", "settlement_price"]


def _number(value, what: str) -> float:
    """A stored value that is not a number is a data error, never estimated (hard rule 2)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: stored value {value!r} is not a number") from None
    if math.isnan(out) or math.isinf(out):
        raise ValueError(f"{what}: stored value {value!r} is not a number")
    return out


def day_curve(conn: sqlite3.Connection, root_id: str, as_of: DateLike) -> list[tuple[str, float, str]]:
    """[(prompt_date ISO, value, source)] of the metal's official curve on `as_of`, sorted
    by date, one per date: every FWD_OUTRIGHT dated `as_of`, and the SPOT of `as_of` at the
    cash date unless a FWD_OUTRIGHT already sits there. Empty when nothing is on file."""
    rid, day = lme_root(root_id), _d(as_of).isoformat()
    by_date: dict[str, tuple[float, str]] = {}
    for settle, value, source in conn.execute(
            "SELECT settle_date, value, source FROM marks_official WHERE instrument_id = ? "
            "AND mark_type = 'FWD_OUTRIGHT' AND as_of_date = ? ORDER BY settle_date, snapped_at",
            (rid, day)):
        by_date[str(settle)] = (_number(value, f"FWD_OUTRIGHT {rid} {settle} on {day}"), source)
    spot = conn.execute(
        "SELECT value, source FROM marks_official WHERE instrument_id = ? AND mark_type = 'SPOT' "
        "AND as_of_date = ? AND settle_date = as_of_date ORDER BY snapped_at DESC LIMIT 1",
        (rid, day)).fetchone()
    if spot is not None:
        by_date.setdefault(cash_date(day).isoformat(), (_number(spot[0], f"SPOT {rid} on {day}"), spot[1]))
    return [(d, v, s) for d, (v, s) in sorted(by_date.items())]


def forward_at(conn: sqlite3.Connection, root_id: str, prompt: DateLike,
               as_of: DateLike) -> Optional[tuple[float, str]]:
    """(value, source) of the metal's forward for `prompt` on `as_of`: the pillar itself when
    one sits on that date; linear in calendar days between the two pillars either side; the
    cash price for a prompt before the cash date (Tom, today); None beyond the last pillar
    (never extrapolated) or with no curve on file."""
    curve = day_curve(conn, root_id, as_of)
    if not curve:
        return None
    target = _d(prompt)
    dates = [dt.date.fromisoformat(d) for d, _, _ in curve]
    for day, (_, value, source) in zip(dates, curve):
        if day == target:
            return value, source
    if target < dates[0]:
        return curve[0][1], f"cash price of {_d(as_of).isoformat()} (prompt before the cash date {dates[0]})"
    for k in range(1, len(dates)):
        if target < dates[k]:
            (d0, v0, _), (d1, v1, _) = curve[k - 1], curve[k]
            w = (target - dates[k - 1]).days / (dates[k] - dates[k - 1]).days
            return v0 + w * (v1 - v0), f"between the {d0} and {d1} prompts of {_d(as_of).isoformat()}"
    return None


def settlement_price(conn: sqlite3.Connection, root_id: str,
                     prompt: DateLike) -> Optional[tuple[float, str, str]]:
    """(value, as_of_date, source) of the price an LME forward to `prompt` is frozen at under
    the rule approved on 2026-09-24: the metal's last official cash price (SPOT) on or before
    the prompt date. None when no cash price of the metal is on file by then."""
    rid, day = lme_root(root_id), _d(prompt).isoformat()
    row = conn.execute(
        "SELECT value, as_of_date, source FROM marks_official WHERE instrument_id = ? "
        "AND mark_type = 'SPOT' AND settle_date = as_of_date AND as_of_date <= ? "
        "ORDER BY as_of_date DESC, snapped_at DESC LIMIT 1", (rid, day)).fetchone()
    if row is None:
        return None
    return _number(row[0], f"SPOT {rid} on {row[1]}"), str(row[1]), row[2]
