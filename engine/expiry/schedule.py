"""The roll calendar of the open commodity positions (expiry-monitor lane).

``expiry_schedule(conn, as_of)`` lists every commodity contract still held on ``as_of`` with its
next event, the business days left to it on the contract's own exchange calendar and an alert
level (``levels.py``), so the screens warn before a position is held into delivery.

Positions. Net quantity per instrument is summed here from ``trades_official`` (trade_date <=
as_of), per product. Each product the schedule covers has one row builder in ``_BUILDERS``
(product -> builder); the positions query asks for exactly those products:
- ``FUTURE``: a commodity future is a ``FUTURE`` whose instrument's ``base_ccy`` is a contract
  root id (``'NYMEX:CL'``, ingest-parser's layout); anything else (an equity index future left
  on an old database, ``base_ccy = 'ES'``) is not a commodity contract and is left out;
- ``CMDTY_OPTION``: an option on a commodity future, instrument = contract-master's canonical
  option id (``'CLZ26C 70 Comdty'``), ``base_ccy`` = the root id, quantity = lots;
- ``LME_FWD``: an LME forward, instrument = the root id (``'LME:CA'``), quantity = tonnes. One
  instrument holds many prompts, so these products (``_SPLIT_BY_LEG_DATE``) are also split by
  their legs' date: a position is (root, prompt date).
A flat position (net zero) is not listed.

Settled contracts (decided 2026-09-24, golden-book finding on CLQ26). A contract every one of
whose trades the ledger has frozen (a ``realised_pnl`` row per trade, dated before ``as_of``)
has left the book: it is not a row and raises no alert. It is listed once under
``settled_expired`` instead, so it stays visible. A contract past its event that the ledger
has NOT frozen stays a row, ``EXPIRED``: that is a real gap.

Dates come from contract-master (``data.contracts.contract_for`` with ``conn``): Bloomberg's
own when stored, else its conservative estimate of the last trade date (the last weekday of
the contract month, often weeks later than the real one) and no first notice date at all.
Every row says which (``dates_source``, ``estimated``); an estimate is never shown as
Bloomberg's.

Next event of a future.
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
root's own calendar, never the book's (``_count``). A count that reaches past the calendar
file's coverage is flagged (``beyond_calendar_coverage``): beyond it only weekends are known to
be closed, so the count may be too high. A calendar that is not on file leaves the count blank
and the row ``RED``, with the reason.

Next event of an option on a future (``CMDTY_OPTION``, Phase 5): its own expiry
(``OPTION_EXPIRY``) on the root's calendar, from ``data.contracts.option_for``: Bloomberg's
option date when stored, else contract-master's estimate (the underlying future's last trade
date, never earlier than the real expiry). While the option's date is estimated its alert is
held early the way a future's is: the first business day of the month ``_option_months_before``
before the option's contract month (one month, or the root's ``option_lead_months`` when more:
ICE Brent options expire two months ahead). That holds for every estimated option, cash or
physical underlying: the option's own expiry is the event, and the estimate always lies after
it. When the underlying future is physically delivered (or its delivery is not on file) the
reason adds that future's first notice or last trade, since an exercised option becomes it.

Next event of an LME forward (``LME_FWD``, Phase 5): the prompt date (``LME_PROMPT``), the
ticket's own date (``dates_source`` 'TICKET', never estimated). The alert date is the day the
prompt becomes the cash date, the prompt less 2 LME business days (``engine.lme.cash_date`` of
that day is the prompt), and business days are counted on the LME calendar. The row carries
net tonnes and lots = tonnes / ``engine.lme.lot_tonnes``.

The declining delta of monthly-average contracts is curve-positions', not listed here.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional, Union

from data.contracts import UnknownContract, contract_for, option_for
from engine import calendars, lme
from engine.expiry import levels

FIRST_NOTICE = "first notice"
LAST_TRADE = "last trade"
OPTION_EXPIRY = "option expiry"
LME_PROMPT = "LME prompt"

# The prompt becomes the cash date this many LME business days before it (cash = T+2).
LME_CASH_DAYS = 2

_ROOT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*:[A-Z0-9]+$")
_ZERO_LOTS = 1e-9
# Products whose one instrument holds several positions, one per leg date (LME prompts).
_SPLIT_BY_LEG_DATE = ("LME_FWD",)


@dataclass(frozen=True)
class Position:
    """One instrument held on ``as_of``, as a row builder receives it."""

    product: str
    instrument_id: str
    asset_class: str
    base_ccy: str
    lots: float                  # signed net, trades dated on or before as_of
    trades: int                  # trades making the position
    frozen: int                  # of those, frozen by the ledger with settle_date < as_of
    frozen_at: Optional[str]     # the latest realised_pnl.frozen_at among them
    settled_on: Optional[str]    # the latest realised_pnl.settle_date among them
    leg_date: Optional[str] = None  # the legs' date for a product split by it (an LME prompt)

    @property
    def settled(self) -> bool:
        """Every trade frozen by the ledger: the contract has left the book."""
        return self.trades > 0 and self.frozen == self.trades


_POSITIONS_SQL = """
SELECT t.product, t.instrument_id, i.asset_class, i.base_ccy, SUM(t.quantity) AS lots,
       COUNT(*) AS trades,
       SUM(CASE WHEN r.trade_id IS NOT NULL AND r.settle_date < :as_of THEN 1 ELSE 0 END) AS frozen,
       MAX(CASE WHEN r.settle_date < :as_of THEN r.frozen_at END) AS frozen_at,
       MAX(CASE WHEN r.settle_date < :as_of THEN r.settle_date END) AS settled_on,
       CASE WHEN t.product IN ({split})
            THEN COALESCE((SELECT MAX(l.settle_date) FROM trade_legs l WHERE l.trade_id = t.trade_id), '')
            ELSE '' END AS leg_date
FROM trades_official t
JOIN instruments i ON i.instrument_id = t.instrument_id
LEFT JOIN realised_pnl r ON r.trade_id = t.trade_id
WHERE t.product IN ({products}) AND t.trade_date <= :as_of
GROUP BY t.product, t.instrument_id, i.asset_class, i.base_ccy, leg_date
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


def _positions(conn: sqlite3.Connection, as_of: dt.date) -> list[Position]:
    """Every instrument of a scheduled product held (non-zero net lots) on ``as_of``."""
    names = sorted(_BUILDERS)
    params = {"as_of": as_of.isoformat(), **{f"p{i}": p for i, p in enumerate(names)},
              **{f"s{i}": p for i, p in enumerate(_SPLIT_BY_LEG_DATE)}}
    sql = _POSITIONS_SQL.format(products=", ".join(f":p{i}" for i in range(len(names))),
                                split=", ".join(f":s{i}" for i in range(len(_SPLIT_BY_LEG_DATE))))
    out = []
    for product, instrument_id, asset_class, base_ccy, lots, n, frozen, frozen_at, settled_on, leg_date in \
            conn.execute(sql, params):
        if lots is None or abs(float(lots)) <= _ZERO_LOTS:
            continue
        out.append(Position(str(product), str(instrument_id), str(asset_class or ""),
                            str(base_ccy or "").strip(), float(lots), int(n), int(frozen or 0),
                            frozen_at, settled_on, str(leg_date or "").strip()[:10] or None))
    return out


def _count(cal: str, as_of: dt.date, alert_date: dt.date,
           notes: list[str]) -> tuple[Optional[int], bool]:
    """Business days from ``as_of`` to ``alert_date`` on calendar ``cal``, and whether the count
    reaches outside the calendar file's coverage; any caveat is appended to ``notes``."""
    try:
        business_days = calendars.business_days_between(cal, as_of, alert_date)
        first, last = calendars.coverage(cal)
    except ValueError as exc:
        notes.append(f"business days not counted: {exc}")
        return None, True
    beyond = min(as_of, alert_date) < first or max(as_of, alert_date) > last
    if beyond:
        notes.append(f"the count reaches outside the {cal} calendar's coverage ({first.isoformat()} "
                     f"to {last.isoformat()}), where only weekends are known closed: it may be too high")
    return business_days, beyond


def _early_alert(cal: str, year: int, month: int, months_before: int) -> tuple[dt.date, str]:
    """The conservative alert date of an estimated date: the first business day, on ``cal``, of
    the month ``months_before`` before contract month ``year``-``month``, and its basis text."""
    m0 = year * 12 + month - 1 - months_before
    month_start = dt.date(m0 // 12, m0 % 12 + 1, 1)
    alert_basis = f"estimated: first business day of {month_start.strftime('%b %Y')}"
    try:
        alert_date = calendars.next_business_day(cal, month_start - dt.timedelta(days=1))
    except ValueError:
        wd = month_start.weekday()  # calendar not on file: the first weekday
        alert_date = month_start + dt.timedelta(days=(7 - wd) % 7 if wd >= 5 else 0)
    return alert_date, alert_basis


def _level(business_days: Optional[int], event_past: bool, early_alert_past: bool) -> str:
    """The level; past a conservative (early) alert date but not the estimated event it is RED,
    since the real event may already be past."""
    if early_alert_past and not event_past:
        return levels.RED
    return levels.level_for(business_days, event_past)


def _in_days(days: Optional[int], what: str, when: dt.date, cal: str) -> str:
    if days is None:
        return f"{what} on {when.isoformat()}"
    if days == 0:
        return f"{what} is today"
    return f"{what} in {days} business day{'s' if days != 1 else ''} on the {cal} calendar"


def _sentence(head: str, notes: list[str]) -> str:
    tail = "; ".join(notes)
    return head[0].upper() + head[1:] + (". " + tail[0].upper() + tail[1:] if tail else "") + "."


def _tonnes_text(tonnes: float, lots: Optional[float]) -> str:
    t = int(tonnes) if float(tonnes).is_integer() else tonnes
    if lots is None:
        return f"{t:+,} t"
    n = int(lots) if float(lots).is_integer() else round(lots, 2)
    return f"{t:+,} t ({n:+,} lot{'s' if abs(lots) != 1 else ''})"


def _unresolved_row(pos: Position, root_id: str, why: str) -> dict:
    """A held contract contract-master cannot place: listed, never dropped, RED."""
    return {
        "product": pos.product,
        "root_id": root_id, "name": "", "sector": "", "exchange": root_id.split(":")[0],
        "calendar": "", "delivery": "", "delivery_assumed": "physical",
        "contract_id": pos.instrument_id, "lots": pos.lots,
        "last_trade_date": None, "first_notice_date": None, "dates_source": "", "estimated": True,
        "next_event": None, "next_event_date": None, "alert_date": None, "alert_basis": "",
        "business_days": None,
        "level": levels.level_for(None, False),
        "reason": f"{_lots_text(pos.lots)} held, but its dates are unknown: {why}. "
                  "Treated as close to delivery until the contract is in config/contracts.csv.",
        "beyond_calendar_coverage": False,
    }


def _future_row(conn: sqlite3.Connection, pos: Position, as_of: dt.date) -> Optional[dict]:
    """The row of a commodity future; None for a future that is not a commodity contract."""
    root_id = pos.base_ccy
    if pos.asset_class != "FUTURE" or not _ROOT_ID_RE.match(root_id):
        return None  # not a commodity contract (the equity index futures: base_ccy 'ES')
    try:
        contract = contract_for(root_id, pos.instrument_id, conn)
    except UnknownContract as exc:
        return _unresolved_row(pos, root_id, str(exc))
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
    held = _lots_text(pos.lots)
    # An estimated date is "no later than": for a physical contract the level is held early,
    # from the first business day of the month before the contract month.
    early = contract.estimated and physical
    if early:
        alert_date, alert_basis = _early_alert(cal, contract.year, contract.month, 1)
    else:
        alert_date, alert_basis = event_date, event

    business_days, beyond = _count(cal, as_of, alert_date, notes)

    event_past = event_date < as_of
    alert_past = alert_date < as_of
    level = _level(business_days, event_past, early and alert_past)

    def _in(days: Optional[int], what: str, when: dt.date) -> str:
        return _in_days(days, what, when, cal)

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

    reason = _sentence(head, notes)
    return {
        "product": pos.product,
        "root_id": root.root_id, "name": root.name, "sector": root.sector, "exchange": root.exchange,
        "calendar": cal, "delivery": root.delivery,
        "delivery_assumed": "physical" if physical else "cash",
        "contract_id": contract.contract_id, "lots": pos.lots,
        "last_trade_date": _iso(ltd), "first_notice_date": _iso(fnd),
        "dates_source": contract.dates_source, "estimated": contract.estimated,
        "next_event": event, "next_event_date": _iso(event_date),
        "alert_date": _iso(alert_date), "alert_basis": alert_basis, "business_days": business_days,
        "level": level, "reason": reason, "beyond_calendar_coverage": beyond,
    }


def _option_months_before(root) -> int:
    """How many months before its contract month an estimated option is alerted from: one, or
    the root's ``option_lead_months`` when more (its options expire that far ahead)."""
    lead = root.option_lead_months
    return max(1, int(lead)) if lead else 1


def _option_row(conn: sqlite3.Connection, pos: Position, as_of: dt.date) -> dict:
    """The row of an option on a commodity future: its own expiry is the event."""
    root_id = pos.base_ccy
    if not _ROOT_ID_RE.match(root_id):
        return _unresolved_row(pos, root_id, f"its root {root_id!r} is not a contract root id")
    try:
        option = option_for(root_id, pos.instrument_id, conn)
    except (UnknownContract, ValueError) as exc:
        return _unresolved_row(pos, root_id, str(exc))
    root = option.root
    und = option.underlying
    physical = root.delivery != "cash"
    cal = root.calendar
    expiry = option.last_trade_date
    held = _lots_text(pos.lots)
    notes: list[str] = []

    early = option.estimated
    if early:
        alert_date, alert_basis = _early_alert(cal, option.year, option.month, _option_months_before(root))
        alert_date = min(alert_date, expiry)
    else:
        alert_date, alert_basis = expiry, OPTION_EXPIRY

    business_days, beyond = _count(cal, as_of, alert_date, notes)
    event_past = expiry < as_of
    alert_past = alert_date < as_of
    level = _level(business_days, event_past, early and alert_past)

    if event_past:
        head = (f"{OPTION_EXPIRY} was {expiry.isoformat()}" + (" (estimated)" if early else "")
                + f", and {held} are still held: check whether it was exercised and close now")
    elif early and alert_past:
        head = (f"alert held early from {alert_date.isoformat()} ({alert_basis}), now past, {held} held: "
                "the real option expiry may already have passed")
    elif early:
        head = (f"alert held early: {_in_days(business_days, 'the alert date', alert_date, cal)} "
                f"({alert_basis}, {alert_date.isoformat()}), {held} held; Bloomberg's option expiry is not "
                f"on file, estimated {OPTION_EXPIRY} {expiry.isoformat()} is the latest it can be")
    else:
        head = f"{_in_days(business_days, OPTION_EXPIRY, expiry, cal)}, {held} held"

    und_fnd = und.first_notice_date
    if und_fnd is not None and und_fnd <= und.last_trade_date:
        und_event, und_date = FIRST_NOTICE, und_fnd
    else:
        und_event, und_date = LAST_TRADE, und.last_trade_date
    if physical:
        text = (f"exercised, it becomes the future {und.contract_id}, physically delivered"
                + (" (delivery method not on file: treated as physical)" if root.delivery == "" else "")
                + f": its {und_event} is {und_date.isoformat()}"
                + (" (estimated)" if und.estimated else ""))
        if und_date <= expiry:
            text += ", on or before the option's expiry, so an exercise lands in the delivery period"
        notes.insert(0, text)
    if early:
        notes.insert(0, f"option expiry not Bloomberg's but {option.dates_note}")

    return {
        "product": pos.product,
        "root_id": root.root_id, "name": root.name, "sector": root.sector, "exchange": root.exchange,
        "calendar": cal, "delivery": root.delivery,
        "delivery_assumed": "physical" if physical else "cash",
        "contract_id": option.contract_id, "lots": pos.lots,
        "last_trade_date": _iso(expiry), "first_notice_date": None,
        "dates_source": option.dates_source, "estimated": option.estimated,
        "next_event": OPTION_EXPIRY, "next_event_date": _iso(expiry),
        "alert_date": _iso(alert_date), "alert_basis": alert_basis, "business_days": business_days,
        "level": level, "reason": _sentence(head, notes), "beyond_calendar_coverage": beyond,
        "option_type": option.option_type, "strike": option.strike, "style": option.style,
        "underlying_id": und.contract_id, "underlying_event": und_event,
        "underlying_event_date": _iso(und_date), "underlying_estimated": und.estimated,
    }


def _lme_row(conn: sqlite3.Connection, pos: Position, as_of: dt.date) -> dict:
    """The row of one LME prompt (root, prompt date): alerted from the day it becomes cash."""
    root_id = pos.instrument_id
    tonnes = pos.lots

    def _unplaced(why: str) -> dict:
        row = _unresolved_row(pos, root_id, why)
        row.update(tonnes=tonnes, lots=None, prompt_date=pos.leg_date,
                   reason=f"{_tonnes_text(tonnes, None)} held, but its prompt cannot be placed: {why}. "
                          "Treated as close to delivery until it is fixed.")
        return row

    if not lme.is_lme_instrument(root_id):
        return _unplaced(f"{root_id!r} is not an LME forward metal in config/contracts.csv")
    root = lme.metal_root(root_id)
    try:
        prompt = _as_date(pos.leg_date or "")
    except ValueError:
        return _unplaced("its legs carry no prompt date")
    cal = lme.LME_CALENDAR
    notes: list[str] = []
    try:
        lots: Optional[float] = tonnes / lme.lot_tonnes(root_id)
    except ValueError as exc:
        lots = None
        notes.append(f"lots not counted: {exc}")
    held = _tonnes_text(tonnes, lots)

    try:
        alert_date = calendars.add_business_days(cal, prompt, -LME_CASH_DAYS)
    except ValueError:
        alert_date = prompt  # calendar not on file: step back weekdays only; _count names it
        for _ in range(LME_CASH_DAYS):
            alert_date -= dt.timedelta(days=1)
            while alert_date.weekday() >= 5:
                alert_date -= dt.timedelta(days=1)
    alert_basis = f"cash date: prompt less {LME_CASH_DAYS} {cal} business days"

    business_days, beyond = _count(cal, as_of, alert_date, notes)
    event_past = prompt < as_of
    level = levels.level_for(business_days, event_past)
    physical = root.delivery != "cash"

    if event_past:
        head = (f"the {LME_PROMPT} was {prompt.isoformat()}, and {held} are still held: "
                + ("delivery risk, close now" if physical else "check the settlement"))
    elif alert_date == as_of:
        head = (f"the {prompt.isoformat()} prompt becomes the cash date today ({alert_basis}), {held} held: "
                "roll or close now")
    elif alert_date < as_of:
        settles = "today" if prompt == as_of else f"on {prompt.isoformat()}"
        head = (f"the {prompt.isoformat()} prompt became the cash date on {alert_date.isoformat()} "
                f"({alert_basis}) and settles {settles}, {held} held: roll or close now")
    else:
        count = ("" if business_days is None else
                 f"in {business_days} business day{'s' if business_days != 1 else ''} on the {cal} calendar, ")
        head = (f"the {prompt.isoformat()} prompt becomes the cash date {count}on {alert_date.isoformat()} "
                f"({alert_basis}), {held} held")

    return {
        "product": pos.product,
        "root_id": root.root_id, "name": root.name, "sector": root.sector, "exchange": root.exchange,
        "calendar": cal, "delivery": root.delivery,
        "delivery_assumed": "physical" if physical else "cash",
        "contract_id": f"{root.root_id} {prompt.isoformat()}", "lots": lots,
        "last_trade_date": None, "first_notice_date": None,
        "dates_source": "TICKET", "estimated": False,
        "next_event": LME_PROMPT, "next_event_date": _iso(prompt),
        "alert_date": _iso(alert_date), "alert_basis": alert_basis, "business_days": business_days,
        "level": level, "reason": _sentence(head, notes), "beyond_calendar_coverage": beyond,
        "tonnes": tonnes, "prompt_date": _iso(prompt), "instrument_id": root_id,
    }


# The row builder per product: the one place a product joins the schedule. A builder takes
# (conn, Position, as_of) and returns the row dict (every key ``expiry_schedule`` documents,
# ``product`` included), or None when the position is not a commodity contract. The positions
# query, the settled-contract rule, sorting and counts are shared and need no change.
_BUILDERS: dict[str, Callable[[sqlite3.Connection, Position, dt.date], Optional[dict]]] = {
    "FUTURE": _future_row,
    "CMDTY_OPTION": _option_row,
    "LME_FWD": _lme_row,
}


def _settled_entry(row: dict, pos: Position) -> dict:
    """A contract the ledger has frozen in full: listed once, never an alert."""
    frozen_day = str(pos.frozen_at or "")[:10]
    tail = (f"; the ledger has frozen all {pos.trades} trade{'s' if pos.trades != 1 else ''}"
            + (f" (on {frozen_day})" if frozen_day else "")
            + ", so it has left the book: no alert.")
    if "tonnes" in row:  # an LME prompt: its prompt date stands where a last trade date would
        prompt = row.get("prompt_date") or pos.settled_on
        return {
            "product": pos.product, "contract_id": row["contract_id"], "root_id": row["root_id"],
            "name": row["name"], "lots": row["lots"], "tonnes": row["tonnes"],
            "last_trade_date": prompt, "prompt_date": prompt,
            "dates_source": row["dates_source"], "estimated": row["estimated"],
            "frozen_at": pos.frozen_at,
            "reason": (f"Prompt{' ' + prompt if prompt else ''} passed with "
                       f"{_tonnes_text(row['tonnes'], row['lots'])} held" + tail),
        }
    last_trade = row.get("last_trade_date") or pos.settled_on
    return {
        "product": pos.product, "contract_id": row["contract_id"], "root_id": row["root_id"],
        "name": row["name"], "lots": pos.lots, "last_trade_date": last_trade,
        "dates_source": row["dates_source"], "estimated": row["estimated"],
        "frozen_at": pos.frozen_at,
        "reason": f"Expired{' ' + last_trade if last_trade else ''} with {_lots_text(pos.lots)} held" + tail,
    }


def _sort_key(row: dict):
    bd = row["business_days"]
    return (levels.LEVELS.index(row["level"]),
            bd if bd is not None else -10 ** 9,
            row["next_event_date"] or "",
            row["contract_id"])


def expiry_schedule(conn: sqlite3.Connection, as_of: Union[dt.date, str]) -> dict:
    """The roll calendar of the commodity contracts held on ``as_of``.

    Returns ``{as_of, rows, counts, thresholds, note, settled_expired}``:
    - ``rows``, worst first (level, then fewest business days): ``product`` ('FUTURE' |
      'CMDTY_OPTION' | 'LME_FWD'),
      ``root_id, name, sector, exchange, calendar, delivery`` (the contract file's: 'physical' |
      'cash' | ''), ``delivery_assumed`` ('physical' | 'cash', unknown read as physical),
      ``contract_id`` (an LME prompt: '<root id> <prompt ISO>', 'LME:CA 2026-12-16'), ``lots``
      (signed net; an LME prompt: tonnes / lot size, None when not countable),
      ``last_trade_date`` (an option: its expiry; an LME prompt: None), ``first_notice_date``
      (ISO or None; None for options and LME prompts),
      ``dates_source`` ('BLOOMBERG' | 'ESTIMATED' | 'SYMBOL' for an option's own expiry as booked,
      earlier than the estimate | 'TICKET' for an LME prompt, the ticket's own date; '' when
      unresolved), ``estimated`` (the one test of "not a real date"), ``next_event`` ('first notice' | 'last trade' |
      'option expiry' | 'LME prompt'), ``next_event_date``, ``alert_date`` (ISO: the date the
      level is counted to), ``alert_basis`` ('estimated: first business day of <Mon YYYY>', 'cash
      date: prompt less 2 LME business days', or the event name), ``business_days`` (to
      ``alert_date``, negative once past; None when not countable), ``level`` ('EXPIRED' | 'RED'
      | 'AMBER' | 'GREEN'), ``reason`` (one plain sentence), ``beyond_calendar_coverage``.
      Option rows add ``option_type`` ('CALL' | 'PUT'), ``strike``, ``style``, ``underlying_id``,
      ``underlying_event`` ('first notice' | 'last trade'), ``underlying_event_date``,
      ``underlying_estimated``; LME rows add ``tonnes`` (signed net), ``prompt_date``,
      ``instrument_id`` ('LME:CA'). Futures rows carry no extra key;
    - ``counts``: rows per level, every level present (settled contracts not counted);
    - ``thresholds``: ``levels.thresholds()``;
    - ``note``: '' or why ``rows`` is empty;
    - ``settled_expired``: the contracts still showing net lots whose every trade the ledger
      has frozen (no alert), ordered by last trade date: ``product, contract_id, root_id, name,
      lots, last_trade_date`` (contract-master's, else the ledger's settle date; an LME prompt:
      its prompt date), ``dates_source, estimated, frozen_at`` (the latest
      ``realised_pnl.frozen_at``), ``reason``; an LME prompt adds ``tonnes, prompt_date``.
    """
    day = _as_date(as_of)
    rows: list[dict] = []
    settled: list[dict] = []
    for pos in _positions(conn, day):
        row = _BUILDERS[pos.product](conn, pos, day)
        if row is None:
            continue
        if pos.settled:
            settled.append(_settled_entry(row, pos))
        else:
            rows.append(row)
    rows.sort(key=_sort_key)
    settled.sort(key=lambda e: (e["last_trade_date"] or "", e["contract_id"]))
    counts = {lvl: sum(1 for r in rows if r["level"] == lvl) for lvl in levels.LEVELS}
    note = "" if rows else f"No commodity futures position is open as of {day.isoformat()}."
    return {"as_of": day.isoformat(), "rows": rows, "counts": counts,
            "thresholds": levels.thresholds(), "note": note, "settled_expired": settled}
