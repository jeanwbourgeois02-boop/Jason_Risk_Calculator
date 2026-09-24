"""The roll calendar of the open commodity futures positions (expiry-monitor lane).

``expiry_schedule(conn, as_of)`` lists every commodity futures contract still held on
``as_of`` with its next event, the business days left to it on the contract's own exchange
calendar and an alert level (``levels.py``), so the screens warn before a position is held
into delivery.

Positions. Net lots per contract are summed here from ``trades_official`` (trade_date <=
as_of). A commodity future is a ``FUTURE`` whose instrument's ``base_ccy`` is a contract root
id (``'NYMEX:CL'``, ingest-parser's layout); the equity index futures (``base_ccy = 'ES'``) are
not commodity contracts and are left out. A flat contract is not listed; a contract still held
past its event is listed as ``EXPIRED``, the worst case.

Dates come from contract-master (``data.contracts.contract_for`` with ``conn``): Bloomberg's
own when stored, else its conservative estimate of the last trade date (the last weekday of
the contract month, often weeks later than the real one) and no first notice date at all.
Every row says which (``dates_source``, ``estimated``); an estimate is never shown as
Bloomberg's.

Next event.
- Physically delivered, or delivery not on file (treated as physical and said so): the first
  notice day when Bloomberg's is on file, else the last trade date. Where Bloomberg's first
  notice falls after the last trade date (CL: notices start the day after trading stops) the
  last trade date comes first and is the event, since a position still open when trading
  stops goes to delivery.
- Cash settled: the last trade date.

Alert date (user yes via the housekeeper, 2026-09-24). An ESTIMATED date is only "no later
than", and the real event of a physical contract comes weeks earlier (CL stops trading about
the 20th of the month before; COMEX metals' first notice is the end of the month before). So
while a physical (or unknown-delivery) contract's dates are estimated, its level is set from a
conservative ``alert_date``: the first business day, on its own calendar, of the month before
the contract month (``alert_basis`` 'estimated: first business day of Oct 2026'). Past that
date but not past the estimate the row is RED, since the real event may already be past.
Otherwise ``alert_date`` is the event date and ``alert_basis`` the event's name. The row's
``last_trade_date`` and ``dates_source`` are unchanged either way.

Business days are ``engine.calendars.business_days_between(calendar, as_of, alert_date)`` on the
root's own calendar, never the book's. A count that reaches past the calendar file's coverage
is flagged (``beyond_calendar_coverage``): beyond it only weekends are known to be closed, so
the count may be too high. A calendar that is not on file leaves the count blank and the row
``RED``, with the reason.

Options on futures, LME prompt dates and the declining delta of monthly-average contracts are
later phases (CLAUDE.md "Commodity conversion plan", Phase 5): not listed yet.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from typing import Optional, Union

from data.contracts import UnknownContract, contract_for
from engine import calendars
from engine.expiry import levels

FIRST_NOTICE = "first notice"
LAST_TRADE = "last trade"

_ROOT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*:[A-Z0-9]+$")
_ZERO_LOTS = 1e-9

_POSITIONS_SQL = """
SELECT t.instrument_id, i.base_ccy, SUM(t.quantity) AS lots
FROM trades_official t
JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE t.product = 'FUTURE' AND i.asset_class = 'FUTURE' AND t.trade_date <= ?
GROUP BY t.instrument_id, i.base_ccy
"""


def _as_date(value: Union[dt.date, str]) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value).strip()[:10])


def _iso(d: Optional[dt.date]) -> Optional[str]:
    return d.isoformat() if d is not None else None


def _lots_text(lots: float) -> str:
    n = int(lots) if float(lots).is_integer() else lots
    return f"{n:+,} lot{'s' if abs(lots) != 1 else ''}"


def _positions(conn: sqlite3.Connection, as_of: dt.date) -> list[tuple[str, str, float]]:
    """(contract_id, root_id, net lots) of every commodity future held on ``as_of``."""
    out = []
    for instrument_id, base_ccy, lots in conn.execute(_POSITIONS_SQL, (as_of.isoformat(),)):
        root_id = str(base_ccy or "").strip()
        if not _ROOT_ID_RE.match(root_id):
            continue  # not a commodity contract (the equity index futures: base_ccy 'ES')
        if lots is None or abs(float(lots)) <= _ZERO_LOTS:
            continue
        out.append((str(instrument_id), root_id, float(lots)))
    return out


def _unresolved_row(contract_id: str, root_id: str, lots: float, why: str) -> dict:
    """A held contract contract-master cannot place: listed, never dropped, RED."""
    return {
        "root_id": root_id, "name": "", "sector": "", "exchange": root_id.split(":")[0],
        "calendar": "", "delivery": "", "delivery_assumed": "physical",
        "contract_id": contract_id, "lots": lots,
        "last_trade_date": None, "first_notice_date": None, "dates_source": "", "estimated": True,
        "next_event": None, "next_event_date": None, "alert_date": None, "alert_basis": "",
        "business_days": None,
        "level": levels.level_for(None, False),
        "reason": f"{_lots_text(lots)} held, but its dates are unknown: {why}. "
                  "Treated as close to delivery until the contract is in config/contracts.csv.",
        "beyond_calendar_coverage": False,
    }


def _row(conn: sqlite3.Connection, contract_id: str, root_id: str, lots: float,
         as_of: dt.date) -> dict:
    try:
        contract = contract_for(root_id, contract_id, conn)
    except UnknownContract as exc:
        return _unresolved_row(contract_id, root_id, lots, str(exc))
    root = contract.root
    ltd = contract.last_trade_date
    fnd = contract.first_notice_date
    physical = root.delivery != "cash"
    notes: list[str] = []

    if physical and fnd is not None and fnd <= ltd:
        event, event_date = FIRST_NOTICE, fnd
    else:
        event, event_date = LAST_TRADE, ltd
        if physical and fnd is not None:
            notes.append(f"first notice {fnd.isoformat()} follows the last trade date, "
                         "so the last trade date is the event")

    cal = root.calendar
    held = _lots_text(lots)
    # An estimated date is "no later than": for a physical contract the level is held early,
    # from the first business day of the month before the contract month.
    early = contract.estimated and physical
    if early:
        prev_year, prev_month = (contract.year - 1, 12) if contract.month == 1 else             (contract.year, contract.month - 1)
        month_start = dt.date(prev_year, prev_month, 1)
        alert_basis = f"estimated: first business day of {month_start.strftime('%b %Y')}"
        try:
            alert_date = calendars.next_business_day(cal, month_start - dt.timedelta(days=1))
        except ValueError:
            wd = month_start.weekday()  # calendar not on file: the first weekday
            alert_date = month_start + dt.timedelta(days=(7 - wd) % 7 if wd >= 5 else 0)
    else:
        alert_date, alert_basis = event_date, event

    business_days: Optional[int]
    try:
        business_days = calendars.business_days_between(cal, as_of, alert_date)
        first, last = calendars.coverage(cal)
        beyond = min(as_of, alert_date) < first or max(as_of, alert_date) > last
        if beyond:
            notes.append(f"the count reaches outside the {cal} calendar's coverage ({first.isoformat()} "
                         f"to {last.isoformat()}), where only weekends are known closed: it may be too high")
    except ValueError as exc:
        business_days, beyond = None, True
        notes.append(f"business days not counted: {exc}")

    event_past = event_date < as_of
    alert_past = alert_date < as_of
    if early and alert_past and not event_past:
        # Past the conservative alert date but not the estimate: the real event may be past.
        level = levels.RED
    else:
        level = levels.level_for(business_days, event_past)

    def _in(days: Optional[int], what: str, when: dt.date) -> str:
        if days is None:
            return f"{what} on {when.isoformat()}"
        if days == 0:
            return f"{what} is today"
        return f"{what} in {days} business day{'s' if days != 1 else ''} on the {cal} calendar"

    if event_past:
        head = (f"{event} was {event_date.isoformat()}"
                + (" (estimated)" if contract.estimated else "")
                + f", and {held} are still held: "
                + ("delivery risk, close now" if physical else "close or check the cash settlement"))
    elif early and alert_past:
        head = (f"alert held early from {alert_date.isoformat()} ({alert_basis}), now past, {held} held: "
                "the real first notice or last trade may already have passed")
    elif early:
        head = (f"alert held early: {_in(business_days, 'the alert date', alert_date)} "
                f"({alert_basis}, {alert_date.isoformat()}), {held} held; the real first notice or "
                f"last trade date is not on file, estimated {event} {event_date.isoformat()} is the latest "
                "it can be")
    else:
        head = f"{_in(business_days, event, event_date)}, {held} held"

    if root.delivery == "":
        notes.insert(0, "delivery method not on file: treated as physically delivered")
    if contract.estimated:
        text = ("dates estimated (last weekday of the contract month), not Bloomberg's: "
                "the real last trade date may be earlier")
        if physical:
            text += ", and with no first notice date on file delivery notices may start earlier still"
        notes.insert(0, text)
    elif physical and fnd is None:
        notes.insert(0, "Bloomberg gives no first notice date: the last trade date is used")

    tail = "; ".join(notes)
    reason = head[0].upper() + head[1:] + (". " + tail[0].upper() + tail[1:] if tail else "") + "."
    return {
        "root_id": root.root_id, "name": root.name, "sector": root.sector, "exchange": root.exchange,
        "calendar": cal, "delivery": root.delivery,
        "delivery_assumed": "physical" if physical else "cash",
        "contract_id": contract.contract_id, "lots": lots,
        "last_trade_date": _iso(ltd), "first_notice_date": _iso(fnd),
        "dates_source": contract.dates_source, "estimated": contract.estimated,
        "next_event": event, "next_event_date": _iso(event_date),
        "alert_date": _iso(alert_date), "alert_basis": alert_basis, "business_days": business_days,
        "level": level, "reason": reason, "beyond_calendar_coverage": beyond,
    }


def _sort_key(row: dict):
    bd = row["business_days"]
    return (levels.LEVELS.index(row["level"]),
            bd if bd is not None else -10 ** 9,
            row["next_event_date"] or "",
            row["contract_id"])


def expiry_schedule(conn: sqlite3.Connection, as_of: Union[dt.date, str]) -> dict:
    """The roll calendar of the commodity futures held on ``as_of``.

    Returns ``{as_of, rows, counts, thresholds, note}``:
    - ``rows``, worst first (level, then fewest business days): ``root_id, name, sector,
      exchange, calendar, delivery`` (the contract file's: 'physical' | 'cash' | ''),
      ``delivery_assumed`` ('physical' | 'cash', unknown read as physical), ``contract_id,
      lots`` (signed net), ``last_trade_date, first_notice_date`` (ISO or None),
      ``dates_source`` ('BLOOMBERG' | 'ESTIMATED'; '' when unresolved), ``estimated``,
      ``next_event`` ('first notice' | 'last trade'), ``next_event_date``, ``alert_date`` (ISO:
      the date the level is counted to), ``alert_basis`` ('estimated: first business day of
      <Mon YYYY>' or the event name), ``business_days`` (to ``alert_date``, negative once past;
      None when not countable), ``level`` ('EXPIRED' | 'RED' | 'AMBER' |
      'GREEN'), ``reason`` (one plain sentence), ``beyond_calendar_coverage``;
    - ``counts``: rows per level, every level present;
    - ``thresholds``: ``levels.thresholds()``;
    - ``note``: '' or why the list is empty.
    """
    day = _as_date(as_of)
    rows = [_row(conn, cid, root_id, lots, day) for cid, root_id, lots in _positions(conn, day)]
    rows.sort(key=_sort_key)
    counts = {lvl: sum(1 for r in rows if r["level"] == lvl) for lvl in levels.LEVELS}
    note = "" if rows else f"No commodity futures position is open as of {day.isoformat()}."
    return {"as_of": day.isoformat(), "rows": rows, "counts": counts,
            "thresholds": levels.thresholds(), "note": note}
