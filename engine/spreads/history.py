"""The daily history of one spread position (CLAUDE.md "Screens redesign plan", Phase C: the
drill-down chart on the Spreads tab).

Nothing here is a new figure. On each date asked for:

- **LTD USD** is the position's member trades' ``value_book`` LTD (``pnl_usd``) added up, by the
  rule the position's own period figures follow (``book._position``): each member spread's trades
  summed, a member with a trade unpriced on that date left out whole and counted, with its reason,
  never summed as 0. Only the trades already traded by that date count; a member none of whose
  trades was traded yet is simply not on, and a date before the position's first trade is left out.
  So ``LTD(a) - LTD(ref)`` over two dates of the history is the position's period P&L for that span
  whenever both closes are priced (the period's own step-back and fill aside, which only the
  period figures apply).
- **The level** is the same rule as ``level_now`` (``book.level_on``, no fallback) read from that
  date's rows: each leg's ``mark`` and, across currencies, its row's ``spot``. A leg that has
  expired, or has no price, leaves the level n/a with its reason.

Cost: one ``value_fn(conn, day)`` call per date (a whole-book valuation, since that is what
``value_book`` and the screens' readers are keyed on), plus one spot read per date for a level in a
non-USD unit. A year of business days is some 250 valuations; that is why ``value_fn`` is
injectable: a screen passes its memoised filled reader
(``ui/tabs/blotter_pricing.py::priced_value_book``), whose (frame, n_filled, n_total) tuple is
accepted, and a date the header already valued costs nothing more.
"""

from __future__ import annotations

import datetime as dt
import math
import sqlite3
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Union

import pandas as pd

from engine.pnl.calendar import load_holidays
from engine.pnl.valuation import value_book
from engine.spreads.book import ValueFn, level_on
from engine.spreads.levels import spec_from_dict


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _priced(row) -> bool:
    return not str(row.get("reason", "") or "") and _num(row.get("pnl_usd")) is not None


def _why(row) -> str:
    return str(row.get("reason", "") or "") or "no USD P&L on its row"


def _members(position: Union[Mapping, Sequence[str]]) -> Dict[str, List[str]]:
    """{member id: trade ids}. A bare list of trade ids is one member."""
    if isinstance(position, Mapping):
        members = position.get("member_trade_ids")
        if members:
            return {str(k): [str(t) for t in v] for k, v in members.items()}
        return {str(position.get("position_id") or "position"): [str(t) for t in position.get("trade_ids") or ()]}
    return {"position": [str(t) for t in position]}


def _trade_info(conn: sqlite3.Connection, ids: Iterable[str]) -> Dict[str, tuple]:
    """{trade_id: (trade_date, instrument expiry)} from ``trades_official``."""
    ids = sorted(set(ids))
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT t.trade_id, t.trade_date, i.expiry_date FROM trades_official t JOIN instruments i "
        f"USING (instrument_id) WHERE t.trade_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {str(r[0]): (str(r[1]), str(r[2])) for r in rows}


def history_dates(conn: sqlite3.Connection, position: Union[Mapping, Sequence[str]], as_of: str,
                  holidays=None) -> List[str]:
    """The business days (weekdays off ``config/holidays.txt``, the header's calendar) from the
    position's first trade date to ``as_of``, both included when business days: the ``dates`` a
    drill-down chart usually asks for. [] when no trade of it is on file."""
    info = _trade_info(conn, (t for ids in _members(position).values() for t in ids))
    if not info:
        return []
    holidays = load_holidays() if holidays is None else holidays
    day, end = dt.date.fromisoformat(min(d for d, _e in info.values())), dt.date.fromisoformat(as_of)
    out = []
    while day <= end:
        if day.weekday() < 5 and day.isoformat() not in holidays:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def position_history(conn: sqlite3.Connection, position: Union[Mapping, Sequence[str]], dates: Iterable[str],
                     value_fn: ValueFn = value_book) -> dict:
    """The position's LTD USD and level on each of ``dates``.

    ``position``: an item of ``book_spreads(...)["positions"]`` (its ``member_trade_ids`` and
    ``level_spec`` are read), or a bare list of trade ids (one member, no level). ``dates``: ISO
    dates in any order; duplicates are read once. ``value_fn(conn, day)``: ``value_book`` by
    default; a screen passes its filled reader (see the module docstring for the cost).

    Returns ``{position_id, level_unit, first_trade_date, dates_left_out, points}``:

    - ``first_trade_date``: the earliest trade date of the position's trades ('' when none is on
      file); ``dates_left_out``: the dates asked for that fall before it (not in ``points``).
    - ``points``: one dict per remaining date, ascending: ``date``; ``ltd_usd`` (the priced
      members' LTD summed, None when no member traded by then is priced: never 0 for missing);
      ``ltd_excluded`` (members on by then but left out); ``ltd_reasons`` ('' or
      '<member>: why; ...'); ``ltd_filled`` (member trades whose row the filled reader took from
      an earlier close) with ``ltd_notes`` (those rows' notes); ``members_on`` (members traded
      by then); ``level`` (None when n/a) with ``level_reason`` ('' or why) and
      ``level_source`` (what was read, as ``level_sources["now"]``).
    """
    members = _members(position)
    all_ids = [t for ids in members.values() for t in ids]
    info = _trade_info(conn, all_ids)
    is_map = isinstance(position, Mapping)
    spec = spec_from_dict(position.get("level_spec")) if is_map else None
    if not is_map:
        no_level = "no level: only trade ids were given, not a position"
    else:
        no_level = (str(position.get("level_now_reason") or "")
                    or "no level: this position has no calendar or template formula")
    first = min((d for d, _e in info.values()), default="")
    asked = sorted(set(str(d) for d in dates))
    left_out = [d for d in asked if not first or d < first]
    points = []
    for day in asked:
        if not first or day < first:
            continue
        traded = {t for t in all_ids if t in info and info[t][0] <= day}
        point = {"date": day, "ltd_usd": None, "ltd_excluded": 0, "ltd_reasons": "", "ltd_filled": 0,
                 "ltd_notes": "", "members_on": 0, "level": None, "level_reason": "", "level_source": ""}
        try:
            out = value_fn(conn, day)
            frame = out[0] if isinstance(out, tuple) else out
        except Exception as exc:  # noqa: BLE001 -- one date that cannot be valued blanks that point only
            why = f"the {day} close could not be valued ({type(exc).__name__}: {exc})"
            point.update(ltd_reasons=why, level_reason=why)
            point["members_on"] = sum(1 for ids in members.values() if traded & set(ids))
            point["ltd_excluded"] = point["members_on"]
            points.append(point)
            continue
        rows = ({r["trade_id"]: r for r in frame.to_dict("records") if r["trade_id"] in traded}
                if isinstance(frame, pd.DataFrame) and not frame.empty else {})
        priced, left, notes = [], [], []
        for mid, ids in members.items():
            on = sorted(t for t in ids if t in traded)
            if not on:
                continue
            point["members_on"] += 1
            missing = [t for t in on if t not in rows]
            unpriced = [f"{t} ({_why(rows[t])})" for t in on if t in rows and not _priced(rows[t])]
            for t in on:
                note = str((rows.get(t) or {}).get("note") or "")
                if note.startswith("no price on"):
                    point["ltd_filled"] += 1
                    notes.append(f"{t}: {note}")
            if missing or unpriced:
                parts = []
                if unpriced:
                    parts.append(f"unpriced on {day}: " + "; ".join(unpriced))
                if missing:
                    parts.append(f"not valued by value_book on {day}: {', '.join(missing)}")
                left.append(f"{mid}: {' and '.join(parts)}")
                continue
            priced.append(float(sum(float(rows[t]["pnl_usd"]) for t in on)))
        point["ltd_usd"] = float(sum(priced)) if priced else None
        point["ltd_excluded"] = len(left)
        point["ltd_reasons"] = "; ".join(left)
        point["ltd_notes"] = "; ".join(notes)
        if spec is None:
            point["level_reason"] = no_level
        else:
            lvl, why, src, _px = level_on(conn, spec, day, rows, False,
                                          lambda leg: info.get(leg.trade_ids[0], ("", ""))[1])
            point.update(level=lvl, level_reason=why, level_source=src)
        points.append(point)
    return {
        "position_id": str(position.get("position_id") or "") if is_map else "",
        "level_unit": spec.unit if spec is not None else "",
        "first_trade_date": first, "dates_left_out": left_out, "points": points,
    }
