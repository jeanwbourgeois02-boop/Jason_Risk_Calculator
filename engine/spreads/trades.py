"""The trade as the unit (CLAUDE.md "Screens redesign plan", Phase G, user 2026-09-29).

A *trade* is Jason's trade name: the PBRoot text after the underscore (``trades.strategy``;
``JSHY10_ZNA1`` and ``JSHY10.3_ZNA1`` are one trade, ZNA1). ``trade_book(conn, as_of)`` gives
the Book row and the click panel of every trade in one dict each: its legs (hedges last), its
type by rule, what it is in plain words, its size and value balance, its level, carry, hedge,
leftover, rolls, next key date, P&L and flags. It adds nothing to the P&L: a trade's P&L is its
strategy position's (``book_spreads``: the ``value_book`` rows of its trades summed, the header's
period rule), a leg's LTD its trades' ``value_book`` rows summed, a leg's Daily its trades' parts
of the strategy's Daily split. Everything else is display arithmetic beside the P&L (levels,
sizes, notionals, research carry), read from the machinery the Book already uses
(``strategies.py``, ``levels.py``, ``carry.py``, ``rolls.py``, ``engine.expiry``).

**The type rule** (written once, like the grouping rule): the trade's non-hedge legs
(``hedges.is_hedge``: a USD/CNH future or an FX product on a currency pair is a hedge, never a leg
for the type) are read per contract root on their OPEN lots.

1. A root whose open months net to zero is a **calendar** (SCO1's iron ore Oct/Nov against
   Feb/Mar; its HRC Oct/Nov).
2. Otherwise the root's calendars are the ones put on as calendars: on one trade date, two
   months of the root traded opposite ways with lots equal within 5 % (``grouping.TOLERANCE``;
   trade against trade first, then the day's rest pooled per contract), not a roll
   (``rolls.py``: a trade that moves a position already held from one month to another is a
   roll, never a calendar), capped by what is still open (ZNA1: SHFE zinc Oct/Nov 627 lots and
   LME zinc Oct/Nov 3,150 t, each put on that way on 2026-09-21).
3. What every root holds beyond its calendars is its directional part. Two or more roots with a
   directional part (a root traded in the trade and now flat counts too: SILARB1's COMEX silver)
   are a **cross-exchange** spread when they are one commodity (contract-master's ``subsector``)
   on two exchanges, else a **cross-product** spread (two commodities, or two contracts of one
   commodity on one exchange). One root alone with a directional part is the trade's leftover
   beside its calendars, or an **outright** when it is all the trade holds.
4. The type is the one kind of the parts (``CALENDAR`` | ``CROSS_EXCHANGE`` | ``CROSS_PRODUCT`` |
   ``OUTRIGHT``), or ``MIXED`` when the parts are of two kinds, each part in ``sub_spreads``. A
   trade with nothing open is typed from the roots and months it traded, said so. Never guessed:
   ``type_mismatch`` says it when the broker's PBRoot decimal (.3 cross exchange, .4 cross
   product, .5 term structure, ``trades.trade_type``) disagrees with the rule (a MIXED trade whose
   label names one of its parts does not disagree).

**The level** is the level of the trade's one spread: its cross part's (``strategies``' pair:
two quote units that agree read A - B, a template its unit; a China-against-the-West pair the
CONVERTED ratio, China on top, user 2026-09-29), else its one calendar's (near - far of the
largest lots on each side, ``levels.py``); a trade with several parts has its level per part.
``level_history`` gives it on past closes, with the rolls marked.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import sqlite3
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from engine.pnl.valuation import value_book
from engine.spreads import strategies as st
from engine.spreads.book import ValueFn, level_on
from engine.spreads.grouping import LEFTOVER_FLOOR, TOLERANCE, Leg, calendar_shape
from engine.spreads.hedges import FX_SECTOR, is_hedge
from engine.spreads.levels import spec_for, spec_from_dict, spec_to_dict, usd_per_level_unit
from engine.spreads.templates import lot_in_quote_units
from engine.spreads.trade_type import CROSS_EXCHANGE, CROSS_PRODUCT, TERM_STRUCTURE

TYPE_CALENDAR = "CALENDAR"
TYPE_CROSS_EXCHANGE = CROSS_EXCHANGE
TYPE_CROSS_PRODUCT = CROSS_PRODUCT
TYPE_MIXED = "MIXED"
TYPE_OUTRIGHT = "OUTRIGHT"           # one commodity held one way: not a spread
TYPE_NONE = ""                       # hedges only
TRADE_TYPES = (TYPE_CALENDAR, TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT, TYPE_MIXED, TYPE_OUTRIGHT)
LABEL_TYPE = {CROSS_EXCHANGE: TYPE_CROSS_EXCHANGE, CROSS_PRODUCT: TYPE_CROSS_PRODUCT, TERM_STRUCTURE: TYPE_CALENDAR}
LABEL_DECIMAL = {CROSS_EXCHANGE: ".3", CROSS_PRODUCT: ".4", TERM_STRUCTURE: ".5"}
FAMILY_CROSS = "cross-product"
UNBALANCED = st.VALUE_TOLERANCE      # 10 % of value
HEDGE_OVERSIZED = 0.50               # a hedge more than 50 % larger than the currency exposure it covers
PCT = 0.01

FLAG_UNBALANCED = "unbalanced"
FLAG_HEDGE_OVERSIZED = "hedge_oversized"
FLAG_TYPE_MISMATCH = "type_mismatch"
FLAG_NO_PRICE = "leg_without_price"
FLAG_UNRECOGNISED = "unrecognised"
UNRECOGNISED = "UNRECOGNISED"        # a blotter row the parser could not identify (hard rule 6, 2026-09-29)

_EPS = 1e-9
_PRECIOUS_FAMILY = {"XAU": "gold", "XAG": "silver", "XPT": "platinum", "XPD": "palladium"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WORDS = {TYPE_CALENDAR: "calendar", TYPE_CROSS_EXCHANGE: "cross exchange", TYPE_CROSS_PRODUCT: "cross product",
          TYPE_MIXED: "mixed", TYPE_OUTRIGHT: "an outright", TYPE_NONE: "hedges only"}
_ACRONYMS = frozenset({"hrc", "lpg", "pta", "meg", "pvc", "lldpe", "dap", "uan", "psf"})
def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _sign(x: float) -> float:
    return 1.0 if x > 0 else -1.0


def _join(parts: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(p for p in parts if p))


def _with_rows(value_fn: ValueFn, as_of: str, rows) -> ValueFn:
    """``value_fn`` with the caller's own valuation of ``as_of`` (``trade_book(rows=...)``)."""
    def read(conn: sqlite3.Connection, day: str):
        return rows if day == as_of else value_fn(conn, day)
    return read


def _cached(value_fn: ValueFn) -> ValueFn:
    """``value_fn`` read once per date within one call (``book_spreads``, the trade book's own
    ``_Book`` and ``level_history`` share it). A screen's memoised reader costs nothing more."""
    memo: Dict[str, object] = {}

    def read(conn: sqlite3.Connection, day: str):
        if day not in memo:
            memo[day] = value_fn(conn, day)
        return memo[day]
    return read


# ------------------------------------------------------------------ words
def month_label(key: str) -> str:
    """'Nov26' for '2026-11'; the key itself when it is not a month."""
    try:
        y, m = int(key[:4]), int(key[5:7])
        return f"{_MONTHS[m - 1]}{y % 100:02d}"
    except (TypeError, ValueError, IndexError):
        return str(key or "")


def _months_text(keys: Iterable[str]) -> str:
    """'Oct/Nov26', 'Oct/Nov26 & Feb/Mar27'."""
    by_year: Dict[str, List[str]] = defaultdict(list)
    for k in sorted(set(k for k in keys if k)):
        lab = month_label(k)
        by_year[lab[3:]].append(lab[:3])
    return " & ".join(f"{'/'.join(ms)}{yy}" for yy, ms in sorted(by_year.items()))


def commodity_words(root) -> str:
    """'copper', 'iron ore', 'HRC', 'USD/CNH' for a root: its subsector in words."""
    if root is None:
        return ""
    if root.sector == FX_SECTOR:
        return f"{root.size_unit.strip().upper()}/{root.currency}"
    sub = str(root.subsector or "").strip().lower()
    return sub.upper() if sub in _ACRONYMS else sub.replace("_", " ")


def _title(words: str) -> str:
    return words if not words or words[0].isupper() else words[0].upper() + words[1:]


def family_of(root) -> str:
    """The trade family of one root (contract-master's ``family``: copper, zinc, ferrous, cattle ...)."""
    if root is None:
        return ""
    return str(getattr(root, "family", "") or root.subsector or "").strip().lower().replace("_", " ")


def leg_name(root, month: str, prompt: str = "", instrument_id: str = "") -> str:
    """'COMEX Silver Dec26', 'LME Zinc 18 Nov26', 'SGX USD/CNH Nov26'; the instrument id when the
    root is not in contract-master."""
    if root is None:
        return instrument_id
    words = _title(commodity_words(root))
    if prompt:
        try:
            d = dt.date.fromisoformat(prompt)
            return f"{root.exchange} {words} {d.day} {_MONTHS[d.month - 1]}{d.year % 100:02d}"
        except ValueError:
            pass
    return f"{root.exchange} {words} {month_label(month)}".strip()


# ------------------------------------------------------------------ the type rule
class _Part:
    """One part of a trade: a calendar on one root, the cross spread of the roots' directional
    parts, or an outright. ``legs``: {contract id: signed lots in the part}."""

    def __init__(self, kind: str, roots: Sequence[str], legs: Dict[str, float], note: str = ""):
        self.kind, self.roots, self.legs, self.note = kind, list(roots), dict(legs), note
        self.closed_roots: List[str] = []


def _roll_trade_ids(roll_data: dict, name: str) -> set:
    return {str(t) for r in (roll_data or {}).get("rolls") or [] if r.get("trade_name") == name
            for t in list(r.get("from_trade_ids") or []) + list(r.get("to_trade_ids") or [])}


def _month_index(key: str) -> int:
    try:
        return int(key[:4]) * 12 + int(key[5:7])
    except (TypeError, ValueError):
        return 0


def _day_calendars(book, tids: Sequence[str], cid_of: Dict[str, str], month_of: Dict[str, str],
                   per_lot: Dict[str, float], skip: set) -> List[Tuple[str, str, float, float]]:
    """``(near contract, far contract, lots, sign of the near leg)`` of every calendar put on as one
    (rule 2 of the module docstring): per trade date, trade against trade within 5 % (the closest
    sizes first), then the day's rest pooled per contract, never a roll's trades."""
    by_day: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
    for tid in tids:
        if tid in skip or tid not in cid_of:
            continue
        q = book.lots(tid)
        if q is None or abs(q) < _EPS:
            continue
        by_day[str(book.by_id[tid]["trade_date"])].append((tid, cid_of[tid], q / per_lot[cid_of[tid]]))
    out: List[Tuple[str, str, float, float]] = []

    def pair(items: List[Tuple[str, str, float]]) -> List[Tuple[str, str, float]]:
        cands = []
        for i, (ta, ca, la) in enumerate(items):
            for tb, cb, lb in items[i + 1:]:
                if ca == cb or month_of[ca] == month_of[cb] or _sign(la) == _sign(lb):
                    continue
                dev = abs(abs(la) - abs(lb)) / max(abs(la), abs(lb))
                if dev <= TOLERANCE + 1e-12:
                    apart = abs(_month_index(month_of[ca]) - _month_index(month_of[cb]))
                    cands.append((dev, apart, ta, tb, (ta, ca, la), (tb, cb, lb)))
        cands.sort(key=lambda c: c[:4])
        used: set = set()
        for _dev, _apart, ta, tb, a, b in cands:
            if ta in used or tb in used:
                continue
            used |= {ta, tb}
            near, far = (a, b) if month_of[a[1]] < month_of[b[1]] else (b, a)
            out.append((near[1], far[1], min(abs(a[2]), abs(b[2])), _sign(near[2])))
        return [x for x in items if x[0] not in used]

    for day in sorted(by_day):
        rest = pair(by_day[day])
        pooled: Dict[str, float] = defaultdict(float)
        for _t, cid, lots in rest:
            pooled[cid] += lots
        pair([(f"pool:{cid}", cid, lots) for cid, lots in sorted(pooled.items()) if abs(lots) > _EPS])
    return out


def _cross_kind(roots: Sequence) -> str:
    """One commodity on two exchanges: cross exchange; anything else: cross product."""
    if len({r.subsector for r in roots}) == 1 and len({r.exchange for r in roots}) > 1:
        return TYPE_CROSS_EXCHANGE
    return TYPE_CROSS_PRODUCT


def _traded_roots(book, tids: Sequence[str]) -> Dict[str, set]:
    """{root id: months traded} of the trade's non-hedge futures and LME trades."""
    out: Dict[str, set] = defaultdict(set)
    for t in tids:
        row = book.by_id[t]
        root = book.roots.get(str(row["base_ccy"] or ""))
        if row["product"] in st.PAIRABLE_PRODUCTS and root is not None and not is_hedge(root, row["product"]):
            out[root.root_id].add(str(row["month_key"] or row["leg_date"] or "")[:7])
    return out


def _decompose(book, tids: Sequence[str], legs: List[st.PLeg], roll_ids: set) -> Tuple[List[_Part], Dict[str, float], str]:
    """(parts, the directional lots no part holds per contract, note) of the open legs."""
    by_root: Dict[str, List[st.PLeg]] = defaultdict(list)
    for leg in legs:
        by_root[leg.root_id].append(leg)
    cid_of, month_of, per_lot = {}, {}, {}
    for leg in legs:
        month_of[leg.contract_id] = leg.month
        per_lot[leg.contract_id] = 1.0 if leg.whole_lots else leg.root.contract_size
        for t in leg.trade_ids:
            cid_of[t] = leg.contract_id
    parts: List[_Part] = []
    residual: Dict[str, Dict[str, float]] = {}
    for root_id, rlegs in sorted(by_root.items()):
        lots = {leg.contract_id: leg.lots for leg in rlegs}
        if (len(rlegs) >= 2 and abs(sum(lots.values())) < LEFTOVER_FLOOR
                and len({_sign(v) for v in lots.values()}) == 2):
            parts.append(_Part(TYPE_CALENDAR, [root_id], lots, "its open months net to zero"))
            continue
        cal: Dict[str, float] = defaultdict(float)
        rtids = [t for leg in rlegs for t in leg.trade_ids]
        found = _day_calendars(book, rtids, cid_of, month_of, per_lot, roll_ids)
        for near, far, size, near_sign in sorted(found, key=lambda c: (-c[2], c[0], c[1])):
            n, f = lots.get(near, 0.0), lots.get(far, 0.0)
            avail_n = n if abs(n) > _EPS and _sign(n) == near_sign else 0.0
            avail_f = f if abs(f) > _EPS and _sign(f) == -near_sign else 0.0
            take = min(size, abs(avail_n), abs(avail_f))
            if take <= LEFTOVER_FLOOR:
                continue
            lots[near] -= near_sign * take
            lots[far] += near_sign * take
            cal[near] += near_sign * take
            cal[far] -= near_sign * take
        if cal:
            parts.append(_Part(TYPE_CALENDAR, [root_id], dict(cal),
                               "put on as a calendar (one trade date, opposite months, lots within 5 %)"))
        left = {c: v for c, v in lots.items() if abs(v) > LEFTOVER_FLOOR}
        if left:
            residual[root_id] = left
    note = ""
    # a root traded in the trade and flat now is the other side when one root alone is left
    flat_roots = sorted(set(_traded_roots(book, tids)) - set(by_root))
    participants = sorted(residual)
    if len(participants) >= 2 or (len(participants) == 1 and flat_roots and not parts):
        extra = flat_roots if len(participants) == 1 else []
        roots = [book.roots[r] for r in participants + extra]
        kind = _cross_kind(roots)
        nets = [sum(residual[r].values()) for r in participants]
        if len(participants) >= 2 and len({_sign(x) for x in nets}) == 1:
            kind = TYPE_OUTRIGHT
            note = f"{', '.join(participants)} are all held {'long' if nets[0] > 0 else 'short'}: not a spread"
        part = _Part(kind, [r.root_id for r in roots], {c: v for r in participants for c, v in residual[r].items()},
                     note)
        if extra:
            part.closed_roots = list(extra)
            part.note = f"{', '.join(extra)} traded in this trade and flat now: only {participants[0]} is still open"
        parts.append(part)
        residual = {}
    elif len(participants) == 1 and not parts:
        parts.append(_Part(TYPE_OUTRIGHT, participants, residual[participants[0]], "one commodity held one way"))
        residual = {}
    left = {c: v for r in residual.values() for c, v in r.items()}
    return parts, left, note


def _type_closed(book, tids: Sequence[str]) -> Tuple[str, List[str]]:
    """The type of a trade with nothing open, from the roots and months it traded."""
    months = _traded_roots(book, tids)
    if not months:
        return TYPE_NONE, []
    if len(months) == 1:
        (root_id, ms), = months.items()
        return (TYPE_CALENDAR if len(ms) > 1 else TYPE_OUTRIGHT), [root_id]
    return _cross_kind([book.roots[r] for r in months]), sorted(months)


def _type_other(book, tids: Sequence[str], rows: List[dict]) -> Tuple[str, str]:
    """(type, note) of a trade with no open futures or LME leg: its open options by their
    underlying root and month, a precious-metal forward as its own position, currency trades
    alone as a currency position (never blank while anything is open); with nothing open, the
    roots and months it traded."""
    open_rows = [r for r in rows if r["status"] == "open"]
    if not open_rows:
        kind, closed_roots = _type_closed(book, tids)
        return kind, (f"nothing is open: typed from the roots and months it traded ({', '.join(closed_roots)})"
                      if closed_roots else "nothing is open and no commodity leg was traded")
    commodity = [r for r in open_rows if not r["hedge"]]
    if not commodity:
        return TYPE_OUTRIGHT, "currency trades only (FX forwards / options): a currency position, no commodity leg"
    months: Dict[str, set] = defaultdict(set)
    for r in commodity:
        months[r["root_id"] or r["instrument_id"]].add(r["month"] or r["prompt"][:7])
    known = [book.roots[k] for k in months if k in book.roots]
    words = "; ".join(r["name"] for r in commodity)
    if len(months) == 1:
        (_k, ms), = months.items()
        kind = TYPE_CALENDAR if len({m for m in ms if m}) > 1 else TYPE_OUTRIGHT
    elif len(known) == len(months):
        kind = _cross_kind(known)
    else:
        kind = TYPE_OUTRIGHT
    what = ("options, typed by their underlying" if any(r["product"] in st.OPTION_PRODUCTS for r in commodity)
            else "a position of its own (not a currency hedge)")
    return kind, f"no open futures or LME leg: {words}: {what}"


def _mismatch(kind: str, part_kinds: Sequence[str], labels: Sequence[str], pb_roots: Sequence[str],
              parts_text: str) -> str:
    """The sentence when the PBRoot decimal disagrees with the rule, else ''."""
    wanted = sorted({LABEL_TYPE[x] for x in labels if x in LABEL_TYPE})
    if not wanted or kind == TYPE_NONE:
        return ""
    off = [w for w in wanted if w not in part_kinds] if kind == TYPE_MIXED else [w for w in wanted if w != kind]
    if not off:
        return ""
    said = " and ".join(f"{LABEL_DECIMAL[x]} {_WORDS[LABEL_TYPE[x]]}" for x in labels if x in LABEL_TYPE)
    return (f"labelled {said} ({', '.join(pb_roots)}), but by the rule its legs make "
            f"{_WORDS.get(kind, kind.lower())}: {parts_text}")


# ------------------------------------------------------------------ the trade's legs
def _contract_key(t: dict, root) -> Tuple[str, str, str]:
    """(contract id, month, prompt / value date) of a trade: a future its instrument, an LME ticket
    '<root> <prompt>' (``expiry_schedule``'s id), an FX trade '<pair> <value date>'."""
    prompt = str(t["leg_date"] or "")
    if t["product"] == "LME_FWD":
        return f"{root.root_id if root else t['base_ccy']} {prompt}", prompt[:7], prompt
    if t["product"] in st.FX_PRODUCTS:
        return f"{t['instrument_id']} {prompt}", prompt[:7], prompt
    return str(t["instrument_id"]), str(t["month_key"] or ""), ""


def _broker_symbols(conn: sqlite3.Connection, tids: Sequence[str]) -> Dict[str, str]:
    """{trade id: the file's Symbol cell as written} ('' on a database loaded before 2026-09-29)."""
    if not tids or not any(r[1] == "broker_symbol" for r in conn.execute("PRAGMA table_info(trades)")):
        return {}
    ids = sorted(set(tids))
    rows = conn.execute(f"SELECT trade_id, broker_symbol FROM trades WHERE trade_id IN ({','.join('?' * len(ids))})",
                        ids).fetchall()
    return {str(r[0]): str(r[1] or "") for r in rows}


def product_word(root) -> str:
    """The product a root trades, to tell two roots of one commodity on one exchange apart: the
    first all-capitals word of its contract-master name that is not its exchange or a country
    code ('ICE Endex Dutch TTF natural gas' -> 'TTF', 'ICE UK NBP natural gas' -> 'NBP'), else
    its root code."""
    for word in str(root.name or "").replace("(", " ").replace(")", " ").split():
        if len(word) > 2 and word.isupper() and word != root.exchange.upper() and word.isalpha():
            return word
    return root.root_id.partition(":")[2] or root.root_id


def _twins(book, tids: Sequence[str]) -> set:
    """The roots of the trade that share their exchange and commodity words with another of its
    roots (ICE TTF and NBP, both 'natural gas'): named by product."""
    seen: Dict[Tuple[str, str], set] = defaultdict(set)
    for tid in tids:
        root = book.roots.get(str(book.by_id[tid]["base_ccy"] or ""))
        if root is not None:
            seen[(root.exchange, commodity_words(root))].add(root.root_id)
    return {r for ids in seen.values() if len(ids) > 1 for r in ids}


def _twin_words(root) -> str:
    """'TTF gas' for ICE TTF natural gas: the product word and the commodity's last word."""
    return f"{product_word(root)} {commodity_words(root).rsplit(' ', 1)[-1]}"


_MARK_TYPES = {"FUTURE": ("FUTURE_PX",), "CMDTY_OPTION": ("FUTURE_PX",), "EQ_OPTION": ("FUTURE_PX",),
               "FX_OPTION": ("PREMIUM",), "LME_FWD": ("FWD_OUTRIGHT", "SPOT"), "FX_FWD": ("FWD_OUTRIGHT", "SPOT"),
               "FX_SPOT": ("FWD_OUTRIGHT", "SPOT"), "FX_SWAP": ("FWD_OUTRIGHT", "SPOT")}


def _mark_close(row: Optional[dict], day: str) -> str:
    """The close a ``value_book`` row's mark is from: ``day``, or the earlier close the screens'
    filled reader took it from (its note 'no price on <d>: value of the <earlier> close ...')."""
    m = re.search(r"value of the (\d{4}-\d{2}-\d{2}) close", str((row or {}).get("note") or ""))
    return m.group(1) if m else day


def _mark_stamp(conn: sqlite3.Connection, row: Optional[dict], product: str, day: str) -> str:
    """The ``snapped_at`` of the official mark a ``value_book`` row read on ``day`` (keyed on the
    row's ``mark_date``, the leg's own settle date); '' for an estimate (``INTERP:``), a frozen
    or closed-out row, or a mark not found as one official row: ``mark_source`` says what it is."""
    if not row or not row.get("mark_date") or str(row.get("mark_source") or "").startswith("INTERP"):
        return ""
    types = _MARK_TYPES.get(product, ())
    if not types or str(row.get("status") or "") != "OPEN":
        return ""
    hit = conn.execute(
        f"SELECT snapped_at FROM marks_official WHERE instrument_id = ? AND as_of_date = ? "
        f"AND mark_type IN ({','.join('?' * len(types))}) AND settle_date = ? ORDER BY snapped_at DESC LIMIT 1",
        (str(row.get("instrument_id")), day, *types, str(row.get("mark_date")))).fetchone()
    return str(hit[0]) if hit else ""


def _unrecognised_row(book, cid: str, ids: List[str], symbols: Dict[str, str]) -> dict:
    """A leg the parser could not identify (product UNRECOGNISED, hard rule 6 "Every row loads"):
    the broker's symbol as written, its lots and fills as the file gave them, no mark, no value,
    no P&L (the reason instead); never typed, never in a level, balance, hedge or carry."""
    t = book.by_id[ids[0]]
    sym = next((symbols[i] for i in ids if symbols.get(i)), "") or str(t["instrument_id"]).partition(":")[2]
    rows = [book.today.get(i) for i in ids]
    why = next((str(r.get("reason")) for r in rows if r and r.get("reason")), "") or (
        f"contract not recognised: {sym}: P&L can't be computed until it is mapped (add it to config/contracts.csv)")
    qty = float(sum(book.lots(i) or 0.0 for i in ids))
    px, _net, px_why = st._entry_value(book, ids, quoted=True)
    return {
        "contract_id": cid, "instrument_id": str(t["instrument_id"]), "root_id": "", "product": UNRECOGNISED,
        "hedge": False, "name": sym, "exchange": "", "commodity": "", "month": "", "prompt": "",
        "broker_symbols": sorted({symbols.get(i, "") for i in ids} - {""}),
        "side": "long" if qty > _EPS else "short" if qty < -_EPS else "flat", "lots": qty, "quantity": qty,
        "status": UNRECOGNISED.lower(), "currency": str(t["quote_ccy"] or ""), "trade_ids": list(ids),
        "open_trade_ids": [], "avg_fill": px, "avg_fill_reason": px_why if px is None else "the file's price, as written",
        "mark": None, "mark_source": "", "mark_as_of": "", "mark_snapped_at": "", "mark_reason": why,
        "prev_mark": None, "prev_mark_date": "", "prev_mark_source": "", "prev_mark_reason": why,
        "value_local": None, "value_local_reason": why, "value_usd": None, "value_reason": why,
        "pnl_usd": {"daily": None, "ltd": None}, "pnl_reasons": {"daily": why, "ltd": why},
        "roll_down": None, "roll_down_unit": "", "roll_down_usd_per_month": None, "horizon_months": None,
        "roll_down_reason": why, "unrecognised": True, "unrecognised_reason": why,
    }


def _leg_rows(book, entry: dict, tids: Sequence[str], symbols: Dict[str, str], prev_day: str = "",
              prev_rows: Optional[Dict[str, dict]] = None) -> List[dict]:
    """One row per contract of the trade (an LME ticket per prompt, an FX trade per value date):
    open legs first, then the legs now flat or expired, hedges last."""
    prev_rows = prev_rows or {}
    twins = _twins(book, tids)
    groups: Dict[str, List[str]] = defaultdict(list)
    meta: Dict[str, Tuple[str, str]] = {}
    for tid in tids:
        t = book.by_id[tid]
        cid, month, prompt = _contract_key(t, book.roots.get(str(t["base_ccy"] or "")))
        groups[cid].append(tid)
        meta[cid] = (month, prompt)
    hedge_rows = {h["instrument_id"]: h for h in entry.get("hedges") or []}
    split = entry.get("daily") or {}
    by_trade = split.get("by_trade") or {}
    split_ok = not split.get("reason") and (entry.get("pnl_usd") or {}).get("daily") is not None
    daily_why = split.get("reason") or (entry.get("pnl_reasons") or {}).get("daily") or "no Daily P&L"
    rows = []
    for cid, ids in groups.items():
        ids = sorted(ids, key=lambda i: (str(book.by_id[i]["trade_date"]), i))
        t = book.by_id[ids[0]]
        if t["product"] == UNRECOGNISED:
            rows.append(_unrecognised_row(book, cid, ids, symbols))
            continue
        root = book.roots.get(str(t["base_ccy"] or ""))
        month, prompt = meta[cid]
        hedge = is_hedge(root, t["product"], t["base_ccy"], t["quote_ccy"], t["instrument_id"])
        open_ids = [i for i in ids if book.is_open(i)]
        qty = sum(book.lots(i) or 0.0 for i in open_ids)
        per_lot = root.contract_size if (t["product"] == "LME_FWD" and root is not None) else 1.0
        status = "open" if abs(qty) > _EPS else ("flat" if open_ids else "closed")
        lots = qty / per_lot if status == "open" else 0.0
        if t["product"] in st.FX_PRODUCTS:
            name = f"{t['instrument_id']} {prompt}"
        elif t["product"] in st.OPTION_PRODUCTS and root is not None:
            # an option names its own contract ('NYMEX Crude oil CLZ26C 75'): two strikes never read alike
            name = f"{root.exchange} {_title(commodity_words(root))} {str(t['instrument_id']).replace(' Comdty', '')}"
        else:
            name = leg_name(root, month, prompt if t["product"] == "LME_FWD" else "", str(t["instrument_id"]))
            if root is not None and root.root_id in twins:
                name = name.replace(f"{root.exchange} {_title(commodity_words(root))}",
                                    f"{root.exchange} {_twin_words(root)}", 1)
        row = {
            "contract_id": cid, "instrument_id": str(t["instrument_id"]), "root_id": str(t["base_ccy"] or ""),
            "product": t["product"], "hedge": hedge, "name": name,
            "exchange": root.exchange if root is not None else "", "commodity": commodity_words(root),
            "month": month, "prompt": prompt,
            "broker_symbols": sorted({symbols.get(i, "") for i in ids} - {""}),
            "side": "long" if lots > _EPS else "short" if lots < -_EPS else "flat",
            "lots": lots, "quantity": qty if status == "open" else 0.0,
            "status": status, "currency": str(t["quote_ccy"] or ""),
            "trade_ids": list(ids), "open_trade_ids": list(open_ids),
        }
        # the average entry of the open lots, in the fills' quoted price
        if status == "open":
            px, _net, why = st._entry_value(book, open_ids, quoted=True)
            row.update(avg_fill=px, avg_fill_reason=why if px is None else "")
        else:
            row.update(avg_fill=None, avg_fill_reason="no open lots")
        # the mark and the value at it, read off the leg's own value_book row
        today = [book.today.get(i) for i in (open_ids or ids)]
        marked = next((r for r in today if r and _num(r.get("mark")) is not None), None)
        first = next((r for r in today if r), None)
        row.update(mark=_num(marked["mark"]) if marked else None,
                   mark_source=str(marked.get("mark_source") or "") if marked else "",
                   mark_as_of=_mark_close(marked, book.as_of) if marked else "",
                   mark_snapped_at=(_mark_stamp(book.conn, marked, t["product"], _mark_close(marked, book.as_of))
                                    if marked else ""),
                   mark_reason="" if marked else (str((first or {}).get("reason") or "")
                                                  or f"not valued by value_book on {book.as_of}"))
        # the previous close's mark: the row the trade's Daily is measured from
        before = [prev_rows.get(i) for i in (open_ids or ids)]
        prev_marked = next((r for r in before if r and _num(r.get("mark")) is not None), None)
        prev_first = next((r for r in before if r), None) or {}
        row.update(prev_mark=_num(prev_marked["mark"]) if prev_marked else None,
                   prev_mark_date=_mark_close(prev_marked, prev_day) if prev_marked else prev_day,
                   prev_mark_source=str(prev_marked.get("mark_source") or "") if prev_marked else "",
                   prev_mark_reason="" if prev_marked else (str(prev_first.get("reason") or "")
                                                            or f"not held or not valued on the {prev_day} close"))
        # the value in the leg's own currency: open quantity x multiplier x mark (never for an option)
        mult = _num(t["multiplier"])
        if status != "open":
            row.update(value_local=0.0, value_local_reason="")
        elif t["product"] in st.OPTION_PRODUCTS:
            row.update(value_local=None, value_local_reason="an option's lots x price is its value, not a notional")
        elif row["mark"] is None or mult is None:
            row.update(value_local=None, value_local_reason=row["mark_reason"] or "its multiplier is not a number")
        else:
            row.update(value_local=qty * mult * row["mark"], value_local_reason="")
        if status != "open":
            row.update(value_usd=0.0, value_reason="")
        elif hedge and t["instrument_id"] in hedge_rows:
            h = hedge_rows[t["instrument_id"]]
            row.update(value_usd=h.get("usd_notional"), value_reason=h.get("notional_reason", ""))
        elif t["product"] in st.OPTION_PRODUCTS:
            row.update(value_usd=None,
                       value_reason=f"{t['instrument_id']}: an option's lots x price is its value, not a notional")
        else:
            leg = Leg(str(t["instrument_id"]), str(t["base_ccy"] or ""), str(t["account"]), str(t["trade_date"]),
                      lots, tuple(open_ids), month)
            v, why = book.notional(leg, qty)
            row.update(value_usd=v, value_reason=why)
        # P&L: the trades' value_book rows (LTD) and their parts of the trade's Daily split
        all_rows = [book.today.get(i) for i in ids]
        unpriced = [f"{i} ({(r or {}).get('reason') or 'not valued by value_book'})" for i, r in zip(ids, all_rows)
                    if r is None or str(r.get("reason") or "") or _num(r.get("pnl_usd")) is None]
        ltd = None if unpriced else float(sum(float(r["pnl_usd"]) for r in all_rows))
        if split_ok and all(i in by_trade for i in ids):
            daily, daily_reason = float(sum(sum(by_trade[i].values()) for i in ids)), ""
        else:
            daily = None
            daily_reason = daily_why if not split_ok else "a trade of this leg is not in the trade's Daily split"
        row.update(pnl_usd={"daily": daily, "ltd": ltd},
                   pnl_reasons={"daily": daily_reason, "ltd": ("unpriced: " + "; ".join(unpriced)) if unpriced else ""},
                   roll_down=None, roll_down_unit="", roll_down_usd_per_month=None, horizon_months=None,
                   roll_down_reason="a hedge: no roll-down" if hedge else "",
                   unrecognised=False, unrecognised_reason="")
        rows.append(row)
    order = {"open": 0, UNRECOGNISED.lower(): 1, "flat": 2, "closed": 2}
    rows.sort(key=lambda r: (r["hedge"], order[r["status"]], r["root_id"], r["month"], r["prompt"], r["contract_id"]))
    return rows


# ------------------------------------------------------------------ level
def _closes_apart(roots: Sequence, as_of: str) -> str:
    """'legs closed ~9h apart (SHFE zinc 15:00 Asia/Shanghai, LME zinc 17:00 Europe/London)' when
    the legs close in different time zones (contract-master's ``ContractRoot.close``: the root's
    own close, else its exchange's; approximate, never a mark time), else ''."""
    from zoneinfo import ZoneInfo
    seen = {}
    day = dt.date.fromisoformat(as_of)
    for r in roots:
        try:
            when, tz = r.close
        except (KeyError, ValueError, AttributeError):
            continue
        key = (r.exchange, when, tz)
        if key not in seen:
            at = dt.datetime.combine(day, when, tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc)
            seen[key] = (f"{r.exchange} {commodity_words(r)}", when, tz, at)
    if len({v[2] for v in seen.values()}) < 2:
        return ""
    ats = [v[3] for v in seen.values()]
    hours = (max(ats) - min(ats)).total_seconds() / 3600.0
    if hours < 1.0:
        return ""
    return (f"legs closed ~{round(hours)}h apart ("
            + ", ".join(f"{name} {when:%H:%M} {tz}" for name, when, tz, _at in seen.values()) + ")")


def _blank_level(why: str) -> dict:
    return {"unit": "", "entry": None, "prev": None, "now": None, "change": None, "prev_date": "",
            "entry_reason": why, "prev_reason": why, "now_reason": why, "change_reason": why,
            "usd_per_unit": None, "usd_per_unit_reason": why, "label": "", "sources": {},
            "alt": None, "unit_alt": "", "spec": None, "mode": "", "china_leg": -1, "note": "", "source": "",
            "reason": why}


def _price_level(rows: List[dict], blank: dict) -> dict:
    """An outright of one open contract: its price is its level (entry the open lots' average
    fill, prev and now the leg's marks; mode 'price', no spec). Anything else: ``blank``."""
    legs = [r for r in rows if not r["hedge"] and r["status"] == "open"]
    if len(legs) != 1:
        return blank
    r = legs[0]
    out = _blank_level("")
    now, prev = r["mark"], r["prev_mark"]
    out.update(unit=f"{r['currency']} (price)", entry=r["avg_fill"], entry_reason=r["avg_fill_reason"],
               now=now, now_reason=r["mark_reason"], prev=prev, prev_reason=r["prev_mark_reason"],
               prev_date=r["prev_mark_date"], change=(now - prev) if now is not None and prev is not None else None,
               change_reason=_join([r["mark_reason"], r["prev_mark_reason"]]), label=r["name"], mode="price",
               source="price", usd_per_unit_reason="an outright's price: see the leg's value",
               sources={"entry": "the open lots' average fill", "now": r["mark_source"], "prev": r["prev_mark_source"]})
    return out


def _pair_level(book, pair: dict) -> dict:
    """A strategies pair's level (the one level rule, ``strategies._levels``)."""
    out = _blank_level("")
    ratio = pair.get("unit") == st.RATIO_UNIT
    out.update(unit=pair.get("unit", ""), entry=pair.get("level_entry"), prev=pair.get("level_prev"),
               now=pair.get("level_now"), change=pair.get("level_change"), prev_date=pair.get("level_prev_date", ""),
               entry_reason=pair.get("level_entry_reason", ""), prev_reason=pair.get("level_prev_reason", ""),
               now_reason=pair.get("level_now_reason", ""), change_reason=pair.get("level_change_reason", ""),
               usd_per_unit=pair.get("usd_per_unit"), usd_per_unit_reason=pair.get("usd_per_unit_reason", ""),
               label=pair.get("level_label", ""), sources=pair.get("level_sources") or {},
               alt=pair.get("level_alt"), unit_alt=pair.get("unit_alt", ""), spec=pair.get("level_spec"),
               mode="ratio" if ratio else "difference", china_leg=int(pair.get("level_china_leg", -1)),
               source="pair")
    spec = spec_from_dict(pair.get("level_spec"))
    if spec is not None:
        out["note"] = _closes_apart([book.roots[leg.root_id] for leg in spec.legs if leg.root_id in book.roots],
                                    book.as_of)
    return out


def _calendar_level(book, part: _Part, pleg_by_cid: Dict[str, st.PLeg], prev_day: str,
                    prev_rows: Dict[str, dict]) -> dict:
    """Near - far of a calendar part's largest lots on each side (``levels.py``'s calendar)."""
    longs = [(c, v) for c, v in part.legs.items() if v > 0]
    shorts = [(c, v) for c, v in part.legs.items() if v < 0]
    if not longs or not shorts:
        return _blank_level("the calendar has no leg on one side")
    a = pleg_by_cid[max(longs, key=lambda x: (abs(x[1]), x[0]))[0]]
    b = pleg_by_cid[max(shorts, key=lambda x: (abs(x[1]), x[0]))[0]]
    near, far = (a, b) if a.month <= b.month else (b, a)
    size = min(abs(part.legs[near.contract_id]), abs(part.legs[far.contract_id]))
    ln = near.as_leg(_sign(part.legs[near.contract_id]) * size, near.trade_ids)
    lf = far.as_leg(_sign(part.legs[far.contract_id]) * size, far.trade_ids)
    spec, why = spec_for(calendar_shape(ln, lf, near.root.quote_unit, near.root.exchange_code), [ln, lf],
                         book.roots, book.templates)
    if spec is None:
        return _blank_level(why)
    out = _blank_level("")
    entry, entry_why, entry_src, _ = book.entry_level(spec)
    now, now_why, now_src, _ = book.level_on(spec, book.as_of, book.today, fallback=False)
    prev, prev_why, prev_src, _ = book.level_on(spec, prev_day, prev_rows, fallback=True)
    s_unit, s_why = book.unit_spot(spec, book.as_of, exact=False)
    out.update(unit=spec.unit, entry=entry, entry_reason=entry_why, now=now, now_reason=now_why, prev=prev,
               prev_reason=prev_why, prev_date=prev_day,
               change=(now - prev) if now is not None and prev is not None else None,
               change_reason=_join([now_why, prev_why]),
               label=f"{near.contract_id} - {far.contract_id}"
                     + (" (the largest lots on each side)" if len(part.legs) > 2 else ""),
               sources={"entry": entry_src, "now": now_src, "prev": prev_src},
               spec=spec_to_dict(spec), mode="difference", source="calendar",
               usd_per_unit=(None if s_unit is None
                             else usd_per_level_unit(_sign(part.legs[near.contract_id]) * size, spec, s_unit)),
               usd_per_unit_reason=s_why if s_unit is None else "")
    return out


# ------------------------------------------------------------------ carry
def _roll_downs(book, rows: List[dict], pleg_by_cid: Dict[str, st.PLeg], history,
                memo: Optional[dict] = None) -> Tuple[Optional[float], str, str]:
    """Each open non-hedge leg's roll-down per month (``carry.curve_roll_downs``, research), written
    on its row; (the trade's carry per month in USD when every such leg is on one curve and read,
    else None, why, the research date)."""
    from engine.spreads.carry import curve_roll_downs
    legs = [r for r in rows if not r["hedge"] and r["status"] == "open" and r["contract_id"] in pleg_by_cid]
    if not legs:
        return None, "no open leg to roll down", ""
    if history is None:
        for r in legs:
            r["roll_down_reason"] = "research history not loaded"
        return None, "research history not loaded", ""
    by_root: Dict[str, List[dict]] = defaultdict(list)
    for r in legs:
        by_root[r["root_id"]].append(r)
    dates = []
    for root_id, rs in by_root.items():
        root = book.roots[root_id]
        res = curve_roll_downs(history, root, [(r["contract_id"], r["contract_id"], pleg_by_cid[r["contract_id"]].month)
                                               for r in rs], book.as_of, memo)
        s, s_why = st._spot_on(book, root.currency, book.as_of)
        s_why = s_why.replace(", the trade date", "")
        if res["research_date"]:
            dates.append(res["research_date"])
        for r in rs:
            got = res["legs"][r["contract_id"]]
            r["roll_down_unit"] = root.quote_unit
            r["horizon_months"] = got["horizon_months"]
            if got["roll_down"] is None:
                r["roll_down_reason"] = got["reason"] or res["reason"]
                continue
            r["roll_down"] = got["roll_down"] / (got["horizon_months"] or 1)
            if s is None:
                r["roll_down_reason"] = f"{s_why}: the roll-down in {root.quote_unit} only"
            else:
                r["roll_down_usd_per_month"] = r["roll_down"] * r["lots"] * lot_in_quote_units(root) * s
    if len(by_root) > 1:
        names = ", ".join(f"{book.roots[x].exchange} {commodity_words(book.roots[x])}" for x in sorted(by_root))
        return None, f"legs on {len(by_root)} curves ({names}): each leg's roll-down is shown, not summed", ""
    missing = [r for r in legs if r["roll_down_usd_per_month"] is None]
    if missing:
        return None, _join(f"{r['name']}: {r['roll_down_reason']}" for r in missing), ""
    return float(sum(r["roll_down_usd_per_month"] for r in legs)), "", (dates[0] if dates else "")


# ------------------------------------------------------------------ hedge
def _cny_at_fill(book, cny: List[st.PLeg], entry: dict) -> Tuple[Optional[float], str, str]:
    """(the open CNY legs' value at the fill in USD, basis, why when None): at the USD spot of each
    trade date (``strategies._entry_value``); with no spot on file, the CNY at the fill converted at
    the average fill of the trade's own open USD/CNH hedge futures (a rate from the blotter, for
    this sizing check only: never a mark, never a P&L)."""
    if all(leg.usd_per_lot is not None for leg in cny):
        return float(sum(leg.lots * leg.usd_per_lot for leg in cny)), "fill, at the USD spot of each trade date", ""
    total_cny = 0.0
    for leg in cny:
        px, _net, why = st._entry_value(book, leg.trade_ids, quoted=True)
        mult = _num(book.by_id[leg.trade_ids[0]]["multiplier"])
        if px is None or mult is None:
            return None, "", f"{leg.contract_id}: {why or 'its multiplier is not a number'}"
        total_cny += leg.lots * (1.0 if leg.whole_lots else leg.root.contract_size) * mult * px
    num = den = 0.0
    for h in entry.get("hedges") or []:
        root = book.roots.get(str(h.get("root_id") or ""))
        if root is None or root.sector != FX_SECTOR:
            continue
        for tid in h.get("open_trade_ids") or []:
            q, f = book.lots(tid), _num(book.by_id[tid]["price"])
            if q is not None and f:
                num += abs(q) * f
                den += abs(q)
    if den <= _EPS:
        return None, "", ("no USD spot for CNY on the trade dates and no USD/CNH future fill to convert the CNY "
                          "legs' value at the fill")
    rate = num / den
    return total_cny / rate, f"fill, CNY at the hedge's own average USD/CNH fill {rate:.4f} (no USD spot on file)", ""


def _hedge_block(book, entry: dict, legs: List[st.PLeg]) -> dict:
    """The trade's currency hedge against the CNY exposure it covers (``strategies._coverage``'s
    figures; the exposure at the fill when no mark is on file), and the oversized test."""
    hedges = entry.get("hedges") or []
    cny = [leg for leg in legs if leg.currency in st.CNY_CCYS]
    out = {"present": bool(hedges), "currency": "", "hedge_usd": None, "exposure_usd": None, "exposure_basis": "",
           "coverage": None, "hedge_oversized": "", "direction_note": "", "reason": "",
           "instruments": [h["instrument_id"] for h in hedges]}
    if not hedges:
        out["reason"] = "no hedge in this trade"
        if cny:
            out.update(currency=cny[0].currency, exposure_usd=entry.get("cny_net_usd"), exposure_basis="mark",
                       reason="no hedge in this trade: its CNY legs are unhedged")
        return out
    cny_hedges = [h for h in hedges if st._cny_hedge(h)]
    if not cny_hedges:
        out.update(currency="/".join(sorted({str(h.get("currency") or "") for h in hedges} - {""})),
                   hedge_usd=entry.get("hedge_usd"), reason="the hedge is not on CNY / CNH: its coverage is not read")
        return out
    out["currency"] = "CNH" if any("CNH" in (h.get("currency"), h.get("root_id")) for h in cny_hedges) else "CNY"
    out["hedge_usd"] = entry.get("hedge_cny_usd")
    if out["hedge_usd"] is None:
        out["reason"] = _join(h.get("notional_reason", "") for h in cny_hedges) or "the hedge's USD notional is not known"
        return out
    exposure, basis, why = entry.get("cny_net_usd"), "mark", ""
    if not cny:
        exposure, basis = 0.0, "no CNY leg open"
    elif exposure is None:
        exposure, basis, why = _cny_at_fill(book, cny, entry)
    out.update(exposure_usd=exposure, exposure_basis=basis)
    if exposure is None:
        out["reason"] = why or entry.get("hedge_reason", "")
        return out
    hedge = float(out["hedge_usd"])
    if abs(exposure) > _EPS:
        out["coverage"] = -hedge / exposure + 0.0
        if hedge * exposure > 0:
            out["direction_note"] = ("the hedge runs with the CNY exposure, not against it (a long China leg needs a "
                                     "short USD/CNH hedge)")
    if abs(hedge) > (1.0 + HEDGE_OVERSIZED) * abs(exposure):
        times = f"{abs(hedge) / abs(exposure):.1f}x " if abs(exposure) > _EPS else ""
        out["hedge_oversized"] = (f"the {out['currency']} hedge, {abs(hedge):,.0f} USD, is {times}the trade's CNY "
                                  f"exposure ({abs(exposure):,.0f} USD at the {basis}): more than "
                                  f"{HEDGE_OVERSIZED:.0%} larger")
    return out


# ------------------------------------------------------------------ size
def _lots_text(lots: Optional[float]) -> str:
    if lots is None:
        return ""
    return f"{abs(lots):g}" if abs(lots - round(lots)) < 1e-6 else f"{abs(lots):.2f}"


def _size_block(book, entry: dict, legs: List[st.PLeg]) -> dict:
    """Lots, value at the fill and at the mark, physical per side, the lot ratio and the value gap
    (balance by dollar value, user 2026-09-29)."""
    out = {"basis": "", "sides": [], "ratio_text": "", "value_gap": None, "unbalanced": None, "reason": "",
           "tolerance": UNBALANCED}
    one = next((p for p in entry.get("pairs") or [] if p.get("rule") == st.RULE_ONE_SPREAD), None)
    if one is not None:
        out["basis"] = "the two commodities of one spread, every month of each (user, 2026-09-29)"
        for s in one["sides"]:
            lots = s.get("net_lots")
            if lots is None:
                lots = float(sum(c["lots"] for c in s.get("contracts") or []))
            roots = [book.roots[r] for r in s.get("root_ids") or [] if r in book.roots]
            out["sides"].append({"label": " / ".join(f"{r.exchange} {commodity_words(r)}" for r in roots),
                                 "root_ids": list(s.get("root_ids") or []), "lots": lots,
                                 "value_fill_usd": s.get("value_fill_usd"),
                                 "value_fill_reason": s.get("value_fill_reason", ""),
                                 "value_mark_usd": s.get("value_mark_usd"),
                                 "value_mark_reason": s.get("value_mark_reason", ""),
                                 "physical": s.get("physical"), "physical_unit": s.get("physical_unit", "")})
    else:
        open_legs = [leg for leg in legs if abs(leg.lots) > _EPS]
        if not open_legs:
            out["reason"] = "nothing open"
            return out
        out["basis"] = "long legs against short legs"
        for label, sel in (("long", [leg for leg in open_legs if leg.lots > 0]),
                           ("short", [leg for leg in open_legs if leg.lots < 0])):
            fill = [None if leg.usd_per_lot is None else leg.lots * leg.usd_per_lot for leg in sel]
            marks = [book.notional(leg.as_leg(leg.lots, leg.trade_ids),
                                   leg.lots if leg.whole_lots else leg.lots * leg.root.contract_size) for leg in sel]
            units = {leg.size_unit for leg in sel}
            out["sides"].append({
                "label": label, "root_ids": sorted({leg.root_id for leg in sel}),
                "lots": float(sum(leg.lots for leg in sel)),
                "value_fill_usd": None if any(v is None for v in fill) else float(sum(fill)),
                "value_fill_reason": _join(leg.value_why for leg in sel if leg.usd_per_lot is None),
                "value_mark_usd": None if any(m[0] is None for m in marks) else float(sum(m[0] for m in marks)),
                "value_mark_reason": _join(m[1] for m in marks if m[0] is None),
                "physical": float(sum(leg.physical(leg.lots, leg.size_unit) for leg in sel)) if len(units) == 1 else None,
                "physical_unit": next(iter(units)) if len(units) == 1 else "",
            })
    a, b = out["sides"]
    out["ratio_text"] = f"{_lots_text(a['lots'])} : {_lots_text(b['lots'])}"
    va, vb = a["value_fill_usd"], b["value_fill_usd"]
    if va is None or vb is None:
        out["reason"] = _join([a["value_fill_reason"], b["value_fill_reason"]]) or "the value at the fill is not known"
    elif max(abs(va), abs(vb)) > _EPS:
        out["value_gap"] = abs(va + vb) / max(abs(va), abs(vb))
        out["unbalanced"] = out["value_gap"] > UNBALANCED + 1e-12
    return out


# ------------------------------------------------------------------ leftover
def _leftover_block(book, entry: dict, rows_by_cid: Dict[str, dict], pleg_by_cid: Dict[str, st.PLeg]) -> dict:
    """The directional part no spread covers: the pairs' leftover (on the heavier side's front leg,
    ``strategies``' rule, the one Exposure reads) and the whole lots no pair takes, per contract, in
    lots, physical units and USD per 1 % move (the notional at the mark off the leg's own
    ``value_book`` row, x 1 %)."""
    items: Dict[str, float] = defaultdict(float)
    whys = []
    for p in entry.get("pairs") or []:
        for x in p.get("leftover_legs") or []:
            items[str(x["contract_id"])] += float(x["lots"])
        if p.get("leftover_lots") is None and p.get("leftover_reason"):
            whys.append(p["leftover_reason"])
    for r in entry.get("residuals") or []:
        if "contract_id" in r:
            items[str(r["contract_id"])] += float(r["lots"])
        else:
            whys.append(f"{r.get('instrument_id')}: {r.get('why')}")
    legs, usd, pct_whys = [], 0.0, []
    for cid, lots in sorted(items.items()):
        if abs(lots) <= LEFTOVER_FLOOR:
            continue
        pl = pleg_by_cid.get(cid)
        item = {"contract_id": cid, "name": rows_by_cid.get(cid, {}).get("name", cid), "lots": lots, "physical": None,
                "physical_unit": "", "usd_per_1pct": None, "reason": ""}
        if pl is None:
            item["reason"] = f"{cid}: not an open leg of the trade"
        else:
            item.update(physical=pl.physical(lots, pl.size_unit), physical_unit=pl.size_unit)
            v, why = book.notional(pl.as_leg(lots, pl.trade_ids), lots if pl.whole_lots else lots * pl.root.contract_size)
            if v is None:
                item["reason"] = why
            else:
                item["usd_per_1pct"] = v * PCT
                usd += v * PCT
        if item["reason"]:
            pct_whys.append(item["reason"])
        legs.append(item)
    physical: Dict[str, float] = defaultdict(float)
    for x in legs:
        if x["physical"] is not None:
            physical[x["physical_unit"]] += x["physical"]
    return {"legs": legs, "usd_per_1pct": None if pct_whys else usd, "reason": _join(pct_whys + whys),
            "physical": dict(physical)}


# ------------------------------------------------------------------ next key date
def _next_block(book, rows: List[dict], pleg_by_cid: Dict[str, st.PLeg]) -> Tuple[Optional[dict], str]:
    """The nearest key date of an open leg (``engine.expiry.expiry_schedule``, hedges included)."""
    events = []
    for r in rows:
        if r["status"] != "open":
            continue
        pl = pleg_by_cid.get(r["contract_id"])
        if pl is not None:
            e = st._event_of(book, pl)
        else:
            s = book.schedule().get(r["contract_id"]) or book.schedule().get(r["instrument_id"])
            e = None if s is None else {"contract_id": r["contract_id"], "event": s.get("next_event"),
                                        "date": s.get("next_event_date"), "alert_date": s.get("alert_date"),
                                        "business_days": s.get("business_days"), "level": s.get("level"),
                                        "estimated": bool(s.get("estimated")), "reason": s.get("reason", "")}
        if e and e.get("date"):
            events.append({**e, "leg": r["name"], "hedge": r["hedge"]})
    if not events:
        return None, "no open leg with a key date in the expiry schedule"
    return sorted(events, key=lambda e: (str(e["date"]), str(e["contract_id"])))[0], ""


# ------------------------------------------------------------------ one trade
def _what_it_is(book, kind: str, parts: List[_Part], rows: List[dict], hedge: dict) -> str:
    """'COMEX vs LME copper, Nov/Dec26', 'CME feeder vs live cattle, Oct/Nov/Dec26', 'SHFE vs LME
    zinc, Oct/Nov26, CNH hedged': China first, else the order held, every exchange named."""
    open_rows = [r for r in rows if not r["hedge"] and r["status"] == "open"]
    roots = [book.roots[x] for x in dict.fromkeys(r["root_id"] for r in open_rows) if x in book.roots]
    roots += [book.roots[x] for p in parts for x in p.closed_roots if x in book.roots and book.roots[x] not in roots]
    roots.sort(key=lambda r: r.country != st.CHINA)
    months = _months_text(r["month"] for r in open_rows)
    subs = list(dict.fromkeys(r.subsector for r in roots))
    unknown = [r["name"] for r in rows if r.get("unrecognised")]
    if not roots:
        named = open_rows or [r for r in rows if r["status"] == "open"]
        text = " + ".join(r["name"] for r in named) if named else (
            f"contract not recognised: {', '.join(unknown)}" if unknown else "nothing open")
        months = ""
    elif len(subs) == 1 and len({r.exchange for r in roots}) == 1 and len(roots) > 1:
        # two contracts of one commodity on one exchange (TTF and NBP): named by their codes
        codes = " vs ".join(product_word(r) for r in roots)
        text = f"{roots[0].exchange} {commodity_words(roots[0])} ({codes})"
    elif len(subs) == 1:
        text = f"{' vs '.join(dict.fromkeys(r.exchange for r in roots))} {commodity_words(roots[0])}"
    else:
        words = [commodity_words(r) for r in roots]
        tails = {w.rsplit(' ', 1)[-1] for w in words}
        if len({r.exchange for r in roots}) == 1:
            joined = (" vs ".join(w.rsplit(" ", 1)[0] for w in words) + f" {tails.pop()}"
                      if len(tails) == 1 and all(" " in w for w in words) else " vs ".join(words))
            text = f"{roots[0].exchange} {joined}"
        else:
            text = " vs ".join(f"{r.exchange} {commodity_words(r)}" for r in roots)
    if kind == TYPE_CALENDAR and len(parts) > 1:
        text = " + ".join(_part_what(book, p, {r["contract_id"]: r for r in rows}) for p in parts)
    elif kind == TYPE_CALENDAR and roots:
        text += f" calendar, {months}" if months else " calendar"
    elif roots and open_rows and all(r["product"] in st.OPTION_PRODUCTS for r in open_rows):
        text += " options (" + ", ".join(r["name"] for r in open_rows) + ")"
    elif months:
        text += f", {months}"
    if hedge.get("present") and hedge.get("currency") and roots:
        text += f", {hedge['currency']} hedged"
    return text


def _part_what(book, part: _Part, rows_by_cid: Dict[str, dict]) -> str:
    """'SGX iron ore Oct/Nov26 & Feb/Mar27 calendar', 'SHFE copper vs COMEX copper, Nov/Dec26'."""
    roots = [book.roots[r] for r in part.roots if r in book.roots]
    months = _months_text(rows_by_cid[c]["month"] for c in part.legs if c in rows_by_cid)
    if part.kind == TYPE_CALENDAR and roots:
        return f"{roots[0].exchange} {commodity_words(roots[0])} {months} calendar"
    if roots:
        roots.sort(key=lambda r: r.country != st.CHINA)
        return " vs ".join(f"{r.exchange} {commodity_words(r)}" for r in roots) + (f", {months}" if months else "")
    return ""


def _part_row(book, part: _Part, rows_by_cid: Dict[str, dict], level: dict) -> dict:
    what = _part_what(book, part, rows_by_cid)
    legs = sorted(part.legs.items(), key=lambda kv: (rows_by_cid.get(kv[0], {}).get("month", ""), kv[0]))
    return {"type": part.kind, "what_it_is": what, "root_ids": list(part.roots), "closed_root_ids": list(part.closed_roots),
            "legs": [{"contract_id": c, "name": rows_by_cid.get(c, {}).get("name", c), "lots": v} for c, v in legs],
            "lots": max((abs(v) for v in part.legs.values()), default=0.0) if part.kind == TYPE_CALENDAR else None,
            "level": level, "note": part.note}


def _trade(book, name: str, entry: dict, roll_data: dict, symbols: Dict[str, str], history,
           memo: Optional[dict] = None) -> dict:
    tids = list(entry["trade_ids"])
    legs, _hedge_ids, _options, _closed = st._build_legs(book, tids)
    pleg_by_cid = {leg.contract_id: leg for leg in legs}
    prev_day = (entry.get("ref_dates") or {}).get("daily") or book.refs["daily"]
    try:
        prev_rows = {r["trade_id"]: r for r in book.frames.records(prev_day)}
    except Exception:  # noqa: BLE001 -- a close that cannot be valued: each level and leg says so
        prev_rows = {}
    rows = _leg_rows(book, entry, tids, symbols, prev_day, prev_rows)
    rows_by_cid = {r["contract_id"]: r for r in rows}
    parts, _left, type_note = _decompose(book, tids, legs, _roll_trade_ids(roll_data, name))
    kinds = [p.kind for p in parts]
    unknown = [r for r in rows if r["unrecognised"]]
    if not parts and unknown and all(r["unrecognised"] or r["hedge"] for r in rows):
        kind, type_note = TYPE_NONE, ("only contracts the app does not recognise: not typed until they are mapped "
                                      f"({', '.join(r['name'] for r in unknown)})")
    elif not parts:
        kind, type_note = _type_other(book, tids, rows)
    else:
        kind = kinds[0] if len(set(kinds)) == 1 else TYPE_MIXED
    # the levels: one per part; the trade's is its one spread's
    cross_pairs = [p for p in entry.get("pairs") or [] if p.get("type") in (CROSS_EXCHANGE, CROSS_PRODUCT)]
    part_levels: List[dict] = []
    for part in parts:
        if part.kind == TYPE_CALENDAR:
            part_levels.append(_calendar_level(book, part, pleg_by_cid, prev_day, prev_rows))
        elif part.kind in (TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT):
            fit = [p for p in cross_pairs if {x["root_id"] for x in p["legs"]} <= set(part.roots)]
            if len(fit) == 1:
                part_levels.append(_pair_level(book, fit[0]))
            elif not fit:
                part_levels.append(_blank_level(part.note or "no open pair of legs to read a level from"))
            else:
                part_levels.append(_blank_level(f"{len(fit)} pairs in this part: each has its own level"))
        else:
            part_levels.append(_blank_level("an outright has no spread level"))
    subs = [_part_row(book, p, rows_by_cid, lv) for p, lv in zip(parts, part_levels)]
    cross_idx = [n for n, p in enumerate(parts) if p.kind in (TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT)]
    cal_idx = [n for n, p in enumerate(parts) if p.kind == TYPE_CALENDAR]
    if len(parts) == 1 and parts[0].kind == TYPE_OUTRIGHT:
        level = _price_level(rows, part_levels[0])
    elif not parts:
        level = _price_level(rows, _blank_level("nothing open: no level"))
    elif len(parts) == 1:
        level = part_levels[0]
    elif len(cross_idx) == 1:
        level = part_levels[cross_idx[0]]
    elif not cross_idx and len(cal_idx) == 1:
        level = part_levels[cal_idx[0]]
    elif not parts:
        level = _blank_level("nothing open: no level")
    else:
        level = _blank_level(f"{name} holds {len(parts)} spreads ({', '.join(_WORDS[k] for k in kinds)}): "
                             f"each has its level under sub_spreads")
    carry, carry_why, carry_date = _roll_downs(book, rows, pleg_by_cid, history, memo)
    hedge = _hedge_block(book, entry, legs)
    size = _size_block(book, entry, legs)
    if kind == TYPE_OUTRIGHT:
        size.update(value_gap=None, unbalanced=None, reason="an outright: not a spread, so no balance")
    leftover = _leftover_block(book, entry, rows_by_cid, pleg_by_cid)
    nxt, nxt_why = _next_block(book, rows, pleg_by_cid)
    split = entry.get("daily") or {}
    split_ok = not split.get("reason") and (entry.get("pnl_usd") or {}).get("daily") is not None
    mismatch = _mismatch(kind, kinds, entry.get("type_labels") or [], entry.get("pb_roots") or [],
                         "; ".join(s["what_it_is"] for s in subs) or type_note)
    families = sorted({family_of(book.roots[r["root_id"]]) for r in rows
                       if not r["hedge"] and r["root_id"] in book.roots} - {""})
    if not families:
        # no commodity root: a precious-metal pair's metal, else a currency position
        metals = {_PRECIOUS_FAMILY[c] for r in rows if r["product"] in st.FX_HEDGE_PRODUCTS
                  for c in (r["instrument_id"][:3], r["instrument_id"][3:6]) if c in _PRECIOUS_FAMILY}
        families = sorted(metals) or (["fx"] if rows else [])
    flags = []
    if size.get("unbalanced"):
        a, b = size["sides"]
        flags.append({"code": FLAG_UNBALANCED, "label": "unbalanced", "severity": "amber",
                      "sentence": (f"the sides differ by {size['value_gap']:.0%} of their value at the fill "
                                   f"({a['label']} {abs(a['value_fill_usd']):,.0f} USD against {b['label']} "
                                   f"{abs(b['value_fill_usd']):,.0f} USD); balanced is within {UNBALANCED:.0%}")})
    if hedge.get("hedge_oversized"):
        flags.append({"code": FLAG_HEDGE_OVERSIZED, "label": "hedge oversized", "severity": "amber", "sentence": hedge["hedge_oversized"]})
    if mismatch:
        flags.append({"code": FLAG_TYPE_MISMATCH, "label": "type mismatch", "severity": "amber", "sentence": mismatch})
    for r in unknown:
        flags.append({"code": FLAG_UNRECOGNISED, "label": "contract not recognised", "severity": "red",
                      "sentence": f"contract not recognised: {r['name']}: P&L can't be computed until it is mapped"})
    no_price = [r for r in rows if r["status"] == "open" and r["mark"] is None]
    if no_price:
        flags.append({"code": FLAG_NO_PRICE, "severity": "amber",
                      "label": f"{len(no_price)} leg{'s' if len(no_price) > 1 else ''} without price",
                      "sentence": "; ".join(f"{r['name']}: {r['mark_reason']}" for r in no_price)})
    pnl = entry.get("pnl_usd") or {}
    return {
        "trade": name, "trade_ids": sorted(tids), "position_id": f"POSITION-{entry['spread_id']}",
        "status": "open" if any(r["status"] in ("open", UNRECOGNISED.lower()) for r in rows) else "closed",
        "first_trade_date": min(str(book.by_id[t]["trade_date"]) for t in tids),
        "pb_roots": list(entry.get("pb_roots") or []), "type_labels": list(entry.get("type_labels") or []),
        "type": kind, "type_note": type_note, "type_mismatch": mismatch, "sub_spreads": subs,
        "commodity_family": families[0] if len(families) == 1 else (FAMILY_CROSS if families else ""),
        "what_it_is": _what_it_is(book, kind, parts, rows, hedge),
        "legs": rows, "size": size, "level": level,
        "carry_per_month": carry, "carry_reason": carry_why, "carry_research_date": carry_date,
        "hedge": hedge, "leftover": leftover,
        "rolls": [r for r in (roll_data or {}).get("rolls") or [] if r.get("trade_name") == name],
        "next": nxt, "next_reason": nxt_why,
        "pnl": {"daily": pnl.get("daily"), "ltd": pnl.get("ltd"), "periods": dict(pnl),
                "reasons": dict(entry.get("pnl_reasons") or {}), "notes": dict(entry.get("pnl_notes") or {}),
                "ref_dates": dict(entry.get("ref_dates") or {}),
                "split": {k: split.get(k) for k in st.SPLIT_COMPONENTS} if split_ok else None,
                "split_reason": "" if split_ok else (split.get("reason") or "no Daily split")},
        "gross_usd": entry.get("gross_usd"), "net_usd": entry.get("net_usd"),
        "notional_reason": entry.get("notional_reason", ""),
        "flags": flags,
    }


def _close_date(book, tids: Sequence[str]) -> str:
    """The day a closed trade went flat: its last fill, or the settlement of a leg that expired
    after it (``value_book``'s ``settle_date`` of a SETTLED row), whichever is later."""
    days = [str(book.by_id[t]["trade_date"])[:10] for t in tids]
    for t in tids:
        r = book.today.get(t) or {}
        if str(r.get("status") or "") == "SETTLED" and r.get("settle_date"):
            days.append(min(str(r["settle_date"])[:10], book.as_of))
    return max(days)


def _closed_block(conn: sqlite3.Connection, book, name: str, entry: dict, read: ValueFn, roll_data: dict,
                  symbols: Dict[str, str]) -> dict:
    """A closed trade's level at entry and when it went flat (the Book's closed fold): the trade is
    read as it stood on the last business day it was open (its parts and level by the same rules),
    its entry level from the fills, its exit level on the close date's marks (a leg not held that
    day, or settled, reads that day's official price or leaves the exit on the last open close,
    said in ``exit_basis``). Display only: the P&L is the trade's LTD, as for an open trade."""
    from engine.pnl.calendar import _prev_business_day
    from engine.spreads.book import _Book, type_legs
    from engine.spreads.trade_type import type_fields
    tids = list(entry["trade_ids"])
    close = _close_date(book, tids)
    out = {"close_date": close, "open_date": "", "unit": "", "mode": "", "entry": None, "exit": None,
           "exit_date": "", "exit_basis": "", "reason": "", "type": ""}
    day = _prev_business_day(dt.date.fromisoformat(close), book.holidays).isoformat()
    first = min(str(book.by_id[t]["trade_date"])[:10] for t in tids)
    if day < first:
        out["reason"] = f"opened and closed on {close}: no close on which it was open, so no level"
        return out
    out["open_date"] = day
    try:
        then = _Book(conn, day, read, None)
        ids = [t for t in tids if t in then.by_id]
        sid = entry["spread_id"]
        stub = {"spread_id": sid, **then.label_fields(ids), "pnl_usd": {}, "pnl_reasons": {}, "pnl_notes": {},
                "ref_dates": {"daily": day}, "gross_usd": None, "net_usd": None, "notional_reason": ""}
        stub["legs"] = then.leg_rows(ids)
        stub.update(type_fields(stub["type_labels"], type_legs(stub["legs"]), then.roots, None))
        then.daily[sid] = {"rows": {r["trade_id"]: r for r in then.frames.records(day)}, "date": day}
        was = _trade(then, name, st.strategy_entry(then, name, ids, stub), roll_data, symbols, None)
    except Exception as exc:  # noqa: BLE001 -- the fold shows the reason, never a failure of the book
        out["reason"] = f"its level on {day} could not be read ({type(exc).__name__}: {exc})"
        return out
    level = was["level"]
    out.update(unit=level.get("unit", ""), mode=level.get("mode", ""), type=was["type"],
               entry=level.get("entry"), entry_reason=level.get("entry_reason", ""))
    if level.get("mode") == "price":
        # an outright of one contract: its exit is its price on the close date (the mark its row
        # carries that day, the settlement price once it has expired)
        leg = next(r for r in was["legs"] if not r["hedge"] and r["status"] == "open")
        got = read(conn, close)
        frame = got[0] if isinstance(got, tuple) else got
        rows = ({r["trade_id"]: r for r in frame.to_dict("records") if r["trade_id"] in set(leg["trade_ids"])}
                if isinstance(frame, pd.DataFrame) and not frame.empty else {})
        hit = next((r for r in rows.values() if _num(r.get("mark")) is not None), None)
        if hit is not None:
            out.update(exit=_num(hit["mark"]), exit_date=close,
                       exit_basis=f"{leg['name']} on the {close} close ({hit.get('mark_source') or 'value_book'})")
        elif level.get("now") is not None:
            out.update(exit=level["now"], exit_date=day, exit_basis=f"{leg['name']} on the {day} close, the last it "
                                                                   f"was open (no price on {close})")
        else:
            out["reason"] = level.get("now_reason") or "no price on the close it went flat"
        return out
    if level.get("spec") is None:
        out["reason"] = level.get("reason") or level.get("now_reason") or "no level"
        return out
    point = level_history(conn, was, [close], read)["points"]
    if point and point[0]["level"] is not None:
        out.update(exit=point[0]["level"], exit_date=close, exit_basis=f"the {close} close, the day it went flat")
    elif level.get("now") is not None:
        why = point[0]["level_reason"] if point else "not valued"
        out.update(exit=level["now"], exit_date=day,
                   exit_basis=f"the {day} close, the last it was open (on {close}: {why})")
    else:
        out["reason"] = level.get("now_reason") or "no price on the close it went flat"
    return out


def trade_book(conn: sqlite3.Connection, as_of: str, spreads: Optional[dict] = None,
               value_fn: ValueFn = value_book, history=None, rows=None) -> dict:
    """Every trade of the book on ``as_of`` (the module docstring), for the Book row and its panel.

    ``spreads``: the ``book_spreads(conn, as_of, value_fn=...)`` result the caller already holds
    (built here with ``value_fn`` when None); pass the SAME ``value_fn`` it was built with (a
    screen: its memoised filled reader), so the legs read the rows the trade's P&L was summed
    from. ``history``: a ``commodity_history.CommodityHistory`` for the roll-down (loaded here
    when None; research context). Deterministic: the same database and arguments give the same
    dict, so a screen may memoise it on its database revision and as-of. ``rows``: the as-of
    valuation the caller already holds (``value_fn(conn, as_of)``'s frame, or its tuple), used
    for ``as_of`` instead of valuing it again; it must be what ``value_fn`` would return.

    Returns ``{as_of, trades, unassigned, notes}``: ``unassigned`` the trade ids with no trade
    name (no PBRoot suffix), on no trade; ``notes`` book-level sentences. Each trade:

    - ``trade`` (the name), ``trade_ids`` (every fill), ``position_id``
      ('POSITION-STRATEGY-<name>', the Book's and the P&L tab's row id), ``status``,
      ``first_trade_date``, ``pb_roots``, ``type_labels`` (the PBRoot decimals' codes).
    - ``type`` (``CALENDAR`` | ``CROSS_EXCHANGE`` | ``CROSS_PRODUCT`` | ``MIXED`` | ``OUTRIGHT`` |
      '' for hedges only), ``type_note``, ``type_mismatch`` ('' or the sentence),
      ``sub_spreads`` ([{type, what_it_is, root_ids, closed_root_ids, legs [{contract_id, name,
      lots}], lots (a calendar's size), level (as ``level``), note}], one per part).
    - ``commodity_family`` ('copper', 'ferrous', 'cattle' ...; 'cross-product' when two families
      mix), ``what_it_is`` ('COMEX vs LME copper, Nov/Dec26').
    - ``legs``: one per contract, open first, hedges last: {contract_id, instrument_id, root_id,
      product, hedge, name ('COMEX Silver Dec26'), exchange, commodity, month, prompt,
      broker_symbols, side, lots (open, signed; an LME ticket in lots of its contract size),
      quantity (open, in ``trades.quantity`` units), status ('open' | 'flat' | 'closed'),
      currency, trade_ids, open_trade_ids, avg_fill / avg_fill_reason (the open lots' average
      entry, quoted), mark / mark_source / mark_reason (the as-of ``value_book`` row), value_usd /
      value_reason (open lots x multiplier x mark x spot; a hedge's USD notional), pnl_usd {daily,
      ltd} / pnl_reasons, roll_down (quote unit per month, research), roll_down_unit,
      roll_down_usd_per_month, horizon_months, roll_down_reason}.
    - ``size``: {basis, sides [{label, root_ids, lots, value_fill_usd, value_fill_reason,
      value_mark_usd, value_mark_reason, physical, physical_unit}] (two), ratio_text ('91 : 167'),
      value_gap (|A + B| / the larger, at the fill), unbalanced (beyond 10 %; None when the
      value is not known), tolerance, reason}.
    - ``level``: THE level of the trade (the one its Entry / Now / z are read on): {unit ('ratio'
      for China against the West, converted, China on top), entry, prev, now, change (the day's
      move), prev_date, each with ``<key>_reason``, usd_per_unit / usd_per_unit_reason (USD of a
      1.0 rise on the open size), label, sources, alt / unit_alt (the other form), spec (a
      ``levels.spec_to_dict``; None with ``reason`` for a trade of several spreads), mode ('ratio'
      | 'difference'), china_leg (the ratio's numerator leg), note ('legs closed ~9h apart ...'),
      source ('pair' | 'calendar' | ''), reason}.
    - ``carry_per_month`` (USD, research: the legs' roll-downs summed only when every non-hedge
      open leg is on one curve) / ``carry_reason`` / ``carry_research_date``.
    - ``hedge``: {present, currency, hedge_usd, exposure_usd, exposure_basis, coverage (-hedge /
      exposure, 1.0 = hedged), hedge_oversized ('' or the sentence: more than 50 % larger than the
      exposure), direction_note, reason, instruments}.
    - ``leftover``: {legs [{contract_id, name, lots, physical, physical_unit, usd_per_1pct,
      reason}], usd_per_1pct, physical ({unit: amount}), reason}.
    - ``rolls``: ``rolls.rolls``' rows of this trade name. ``next`` / ``next_reason``: the
      nearest key date of an open leg (``engine.expiry``'s row plus ``leg`` and ``hedge``).
    - ``pnl``: {daily, ltd (the strategy position's, ``value_book`` rows summed), periods,
      reasons, notes, ref_dates, split ({spread, fx, hedge, new_trades, realised, other}, or None
      with ``split_reason``)}; ``gross_usd`` / ``net_usd`` / ``notional_reason``.
    - ``flags``: [{code ('unbalanced' | 'hedge_oversized' | 'type_mismatch' |
      'leg_without_price' | 'unrecognised'), label, sentence, severity ('red' on 'unrecognised', else 'amber')}].
    - A row the parser could not identify (product UNRECOGNISED, hard rule 6) stays in its trade as
      a leg: ``unrecognised`` True, ``status`` 'unrecognised', ``name`` the broker's symbol as
      written, lots and average fill as the file gave them, no mark, value or P&L (``unrecognised_
      reason`` instead), never in the type, level, balance, hedge, leftover or carry; its trade
      carries the red ``unrecognised`` flag and counts as open. A trade of such rows only is type
      '' with that flag, never dropped. Every leg carries ``unrecognised`` (False on the others).
    - ``closed`` (a closed trade, else None; 2026-09-29): {close_date (its last fill or a leg's
      settlement), open_date (the last business day it was open), type, unit, mode, entry /
      entry_reason (the level at entry, read as it stood on ``open_date``), exit, exit_date,
      exit_basis (the level on the close date's marks, else on ``open_date``), reason}.

    Legs also carry (2026-09-29) ``mark_as_of`` (the close the mark is from: the as-of, or the
    earlier close the filled reader carried) / ``mark_snapped_at`` (the official mark's stamp, ''
    for an estimate or a frozen row), ``prev_mark`` / ``prev_mark_date`` / ``prev_mark_source`` /
    ``prev_mark_reason`` (the Daily's reference close) and ``value_local`` / ``value_local_reason``
    (open quantity x multiplier x mark, in ``currency``); two roots of one commodity on one exchange
    are named by product ('ICE TTF gas Nov26', 'ICE NBP gas Nov26').

    Every figure that cannot be given is None with its reason, never 0 (hard rule 2)."""
    from engine.spreads.book import _Book, book_spreads
    read = _cached(value_fn)
    if rows is not None:
        read = _cached(_with_rows(value_fn, as_of, rows))
    notes: List[str] = []
    if spreads is None:
        spreads = book_spreads(conn, as_of, value_fn=read)
    book = _Book(conn, as_of, read, None)
    try:
        from engine.spreads.rolls import rolls as read_rolls
        roll_data = read_rolls(conn, as_of, spreads)
    except Exception as exc:  # noqa: BLE001 -- the rolls are beside the trades, never in their way
        roll_data = {"rolls": []}
        notes.append(f"the rolls could not be read ({type(exc).__name__}: {exc}): no calendar is told from a roll")
    if history is None:
        try:
            from engine.risk.commodity_history import load_commodity_history
            history = load_commodity_history()
        except Exception as exc:  # noqa: BLE001 -- research context: a reason, never a failure
            notes.append(f"the research history could not be read ({type(exc).__name__}: {exc}): no roll-down")
    if history is not None and not getattr(history, "available", False):
        notes.append(f"research history not found ({getattr(history, 'reason', '')}): no roll-down")
    entries = {str(s.get("name") or ""): s for s in spreads.get("strategies") or []}
    symbols = _broker_symbols(conn, [t for s in entries.values() for t in s.get("trade_ids") or []])
    memo: dict = {}          # the research lookups of the roll-downs, shared by every trade
    if history is not None and getattr(history, "available", False):
        # every root a roll-down will read, in one research query (served from memory after)
        try:
            history.prefetch_roots(sorted({str(book.by_id[t]["base_ccy"] or "") for s in entries.values()
                                           if s.get("name") for t in s.get("trade_ids") or []
                                           if book.by_id.get(t) and str(book.by_id[t]["base_ccy"] or "") in book.roots}))
        except Exception:  # noqa: BLE001 -- a failed batch read: each root reads on its own and says why
            pass
    trades = []
    for name in sorted(n for n in entries if n):
        entry = entries[name]
        if not all(t in book.by_id for t in entry.get("trade_ids") or []):
            notes.append(f"{name}: its trades are not the ones on file on {as_of} (spreads built on another book?)")
            continue
        try:
            row = _trade(book, name, entry, roll_data, symbols, history, memo)
            row["closed"] = (_closed_block(conn, book, name, entry, read, roll_data, symbols)
                             if row["status"] == "closed" else None)
            trades.append(row)
        except Exception as exc:  # noqa: BLE001 -- one trade's reason, never the whole book
            notes.append(f"{name} could not be read ({type(exc).__name__}: {exc})")
    unassigned = sorted(entries[""]["trade_ids"]) if "" in entries else []
    if unassigned:
        notes.append(f"{len(unassigned)} trade(s) carry no trade name (no PBRoot suffix): on no trade, listed "
                     f"under unassigned")
    return {"as_of": as_of, "trades": trades, "unassigned": unassigned, "notes": notes + list(book.reasons)}


def level_history(conn: sqlite3.Connection, trade: dict, dates: Iterable[str], value_fn: ValueFn = value_book) -> dict:
    """A trade's level and LTD on each of ``dates``, with its rolls marked (the panel's chart).

    ``trade``: an item of ``trade_book(...)["trades"]``. The level is the trade's ``level``
    formula (its ``spec``: the same legs every day, so the chart is the spread held now) read
    from each day's ``value_fn`` rows: each leg's mark and, across currencies, its row's spot; a
    leg not yet held that day reads that day's official FUTURE_PX and SPOT (``book.level_on``'s
    fallback); a ratio China over foreign, converted. The LTD is ``history.position_history``'s
    (the trade's trades' ``value_book`` rows summed, a date with one unpriced left out with its
    reason). Returns ``{trade, position_id, level_unit, level_mode, level_entry, first_trade_date,
    dates_left_out, points [{date, ltd_usd, ltd_reasons, ltd_filled, ltd_notes, level,
    level_reason, level_source, rolls [labels]}], rolls}``. ``value_fn``: a screen passes its
    memoised filled reader (one whole-book valuation per date otherwise)."""
    from engine.spreads.history import _trade_info, position_history
    read = _cached(value_fn)
    level = trade.get("level") or {}
    spec = spec_from_dict(level.get("spec"))
    mode, cn = str(level.get("mode") or ""), int(level.get("china_leg", -1))
    tids = list(trade.get("trade_ids") or [])
    base = position_history(conn, {"position_id": trade.get("position_id", ""),
                                   "member_trade_ids": {trade.get("trade", "trade"): tids}}, dates, value_fn=read)
    info = _trade_info(conn, tids)
    roll_on: Dict[str, List[str]] = defaultdict(list)
    for r in trade.get("rolls") or []:
        roll_on[str(r.get("date"))].append(f"{r.get('root_name') or r.get('root_id')} {r.get('label')}")

    def expiry(leg) -> str:
        return info.get(leg.trade_ids[0], ("", ""))[1]

    points = []
    for p in base["points"]:
        day = p["date"]
        point = {k: p[k] for k in ("date", "ltd_usd", "ltd_reasons", "ltd_filled", "ltd_notes")}
        point.update(level=None, level_reason=level.get("reason") or level.get("now_reason") or "no level",
                     level_source="", rolls=list(roll_on.get(day, [])))
        if spec is not None:
            out = read(conn, day)
            frame = out[0] if isinstance(out, tuple) else out
            rows = ({r["trade_id"]: r for r in frame.to_dict("records")
                     if r["trade_id"] in info and info[r["trade_id"]][0] <= day}
                    if isinstance(frame, pd.DataFrame) and not frame.empty else {})
            if mode == "ratio" and cn in (0, 1):
                vc, why_c, src, _ = level_on(conn, st.one_leg_spec(spec, cn), day, rows, True, expiry)
                vf, why_f, _src, _ = level_on(conn, st.one_leg_spec(spec, 1 - cn), day, rows, True, expiry)
                if vc is None or vf is None or abs(vf) < 1e-12:
                    point["level_reason"] = why_c or why_f or "the foreign leg's price is 0"
                else:
                    point.update(level=vc / vf, level_reason="", level_source=src)
            else:
                lvl, why, src, _ = level_on(conn, spec, day, rows, True, expiry)
                point.update(level=lvl, level_reason=why, level_source=src)
        points.append(point)
    return {"trade": trade.get("trade", ""), "position_id": trade.get("position_id", ""),
            "level_unit": level.get("unit", ""), "level_mode": mode, "level_entry": level.get("entry"),
            "first_trade_date": base["first_trade_date"], "dates_left_out": base["dates_left_out"],
            "points": points, "rolls": list(trade.get("rolls") or [])}


__all__ = ["FAMILY_CROSS", "FLAG_HEDGE_OVERSIZED", "FLAG_NO_PRICE", "FLAG_TYPE_MISMATCH", "FLAG_UNBALANCED", "FLAG_UNRECOGNISED",
           "TRADE_TYPES", "TYPE_CALENDAR", "TYPE_CROSS_EXCHANGE", "TYPE_CROSS_PRODUCT", "TYPE_MIXED", "TYPE_NONE",
           "TYPE_OUTRIGHT", "commodity_words", "family_of", "leg_name", "level_history", "month_label", "trade_book"]
