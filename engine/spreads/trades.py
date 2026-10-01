"""The trade as the unit (CLAUDE.md "Screens redesign plan", Phase G, user 2026-09-29).

A *trade* is Jason's trade name: the PBRoot text after the underscore (``trades.strategy``;
``JSHY10_ZNA1`` and ``JSHY10.3_ZNA1`` are one trade, ZNA1). ``trade_book(conn, as_of)`` gives
the Book row and the click panel of every trade in one dict each: its legs (hedges last), its
type by rule, what it is in plain words, its size and value balance, its level, carry, hedge,
leftover, rolls, next key date, P&L and flags. It adds nothing to the P&L: a trade's P&L is its
strategy position's (``book_spreads``: the ``value_book`` rows of its trades summed, the header's
period rule), a leg's LTD its trades' ``value_book`` rows summed, a leg's Daily its trades' parts
of the strategy's Daily split. Everything else is display arithmetic beside the P&L (levels,
sizes, notionals, carry), read from the machinery the Book already uses
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
A part of three or more legs that a ``config/spreads/`` template covers (a 3-2-1 crack, a soy
crush: every leg of the part one leg of the template, signs that fit its weights) reads the
template's own formula and unit, its lots' ratio against the template's said in ``note``. A trade
holding options only reads its net premium per unit (``_premium_level``: the open options'
size-weighted premium over its largest leg, from the fills at entry and the official option
price or PREMIUM marks on the previous close and now), a single option its price. A level that
rests on a near-marks estimate (hard rule 2: a spot or mark whose source starts ``INTERP:``) says
so in ``<key>_estimated`` / ``<key>_estimate_note``, never silently.
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

from data.contracts.tickers import format_strike, month_from_code, parse_option_ticker

from engine.pnl.valuation import value_book
from engine.spreads import strategies as st
from engine.spreads.book import ValueFn, is_estimate, level_on
from engine.spreads.grouping import CALENDAR, LEFTOVER_FLOOR, TOLERANCE, Leg, calendar_shape
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
TYPE_UNMATCHED = "UNMATCHED"         # a part's legs no equal-size rule pairs: listed, no level (2026-10-01)
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
          TYPE_MIXED: "mixed", TYPE_OUTRIGHT: "an outright", TYPE_NONE: "hedges only",
          TYPE_UNMATCHED: "unmatched legs"}
_ACRONYMS = frozenset({"hrc", "lpg", "pta", "meg", "pvc", "lldpe", "dap", "uan", "psf"})
# the link words a name keeps in lower case (user, 2026-10-01: Title Case in the engine's names): the
# same set as the screens' ``formatting.title_name``, so a name written here reads the same after it
_LINK_WORDS = frozenset({
    "vs", "v", "and", "or", "of", "per", "to", "in", "on", "at", "by", "the", "a", "an", "with", "for", "from",
    "into", "long", "short", "call", "put", "calls", "puts", "forward", "forwards", "spot", "option", "options",
    "lot", "lots", "t", "oz", "bbl", "gal", "more", "leg", "legs", "closed", "hedged", "hedge", "spread",
    "spreads", "part", "parts", "not", "recognised", "nothing", "open", "contract", "contracts", "est",
    "left", "no", "is", "are", "than", "under", "over", "each", "only", "one", "two", "fill", "fills"})
_LOWER_WORD = re.compile(r"(?<![\w\-’'.])([a-z]+)(?![\w'’])")
def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _sign(x: float) -> float:
    return 1.0 if x > 0 else -1.0


def title_words(text: str) -> str:
    """A name in Title Case: every all-lower-case word capitalised but the link words ('vs', 'and',
    'long', 'short', 'call' ...); acronyms and mixed-case words unchanged; the first letter a capital.
    'SHFE zinc vs LME zinc, Oct26' -> 'SHFE Zinc vs LME Zinc, Oct26'. For names, never sentences."""
    if not text:
        return text
    out = _LOWER_WORD.sub(lambda m: m.group(1) if m.group(1) in _LINK_WORDS
                          else m.group(1)[0].upper() + m.group(1)[1:], text)
    return out[0].upper() + out[1:]


def _cap(text: str) -> str:
    """A sentence with its first letter a capital (the rest as written)."""
    return text[0].upper() + text[1:] if text else text


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
    """'Oct/Nov26', 'Oct26/Feb27' (two months of two years: a calendar's own name), 'Oct/Nov26 &
    Feb/Mar27'."""
    by_year: Dict[str, List[str]] = defaultdict(list)
    ordered = sorted(set(k for k in keys if k))
    if len(ordered) == 2 and ordered[0][:4] != ordered[1][:4]:
        return "/".join(month_label(k) for k in ordered)
    for k in ordered:
        lab = month_label(k)
        by_year[lab[3:]].append(lab[:3])
    return " & ".join(f"{'/'.join(ms)}{yy}" for yy, ms in sorted(by_year.items()))


def commodity_words(root) -> str:
    """'Copper', 'Iron Ore', 'Feeder Cattle', 'HRC', 'USD/CNH' for a root: its subsector in words,
    Title Case (user, 2026-10-01), an acronym in capitals."""
    if root is None:
        return ""
    if root.sector == FX_SECTOR:
        return f"{root.size_unit.strip().upper()}/{root.currency}"
    sub = str(root.subsector or "").strip().lower()
    return sub.upper() if sub in _ACRONYMS else title_words(sub.replace("_", " "))


def _title(words: str) -> str:
    return words if not words or words[0].isupper() else words[0].upper() + words[1:]


def family_of(root) -> str:
    """The trade family of one root (contract-master's ``family``: copper, zinc, ferrous, cattle ...)."""
    if root is None:
        return ""
    return str(getattr(root, "family", "") or root.subsector or "").strip().lower().replace("_", " ")


def strike_words(strike) -> str:
    """A strike at its own quoted precision with thousands separators: '62', '80,000', '3.25'
    (Bloomberg's ticker form, ``data.contracts.format_strike``, no trailing zeros); the text
    itself when it is not a number."""
    try:
        text = format_strike(strike)
    except (TypeError, ValueError):
        return str(strike or "")
    places = len(text.partition(".")[2])
    return f"{float(text):,.{places}f}"


def leg_name(root, month: str, prompt: str = "", instrument_id: str = "", *,
             strike=None, option_type: str = "") -> str:
    """'COMEX Silver Dec26', 'LME Zinc 18 Nov26', 'SGX USD/CNH Nov26', and with ``strike`` and
    ``option_type`` an option on a future 'NYMEX Crude Oil Dec26 62 put' (the same plain form the
    Blotter and the Data tab write); the instrument id when the root is not in contract-master."""
    if root is None:
        return instrument_id
    words = _title(commodity_words(root))
    if prompt:
        try:
            d = dt.date.fromisoformat(prompt)
            return f"{root.exchange} {words} {d.day} {_MONTHS[d.month - 1]}{d.year % 100:02d}"
        except ValueError:
            pass
    name = f"{root.exchange} {words} {month_label(month)}".strip()
    kind = {"C": "call", "CALL": "call", "P": "put", "PUT": "put"}.get(str(option_type or "").strip().upper(), "")
    if kind and strike not in (None, ""):
        name = f"{name} {strike_words(strike)} {kind}"
    return name


def option_leg_name(root, instrument_id: str, month: str = "") -> str:
    """An option on a future in plain words from its canonical id ('CLZ26P 62 Comdty' -> 'NYMEX
    Crude oil Dec26 62 put'): the option's own contract month, the strike, call / put. ``month``
    ('2026-12') stands in for a one-digit year; '' when the id is not an option ticker."""
    parts = parse_option_ticker(instrument_id)
    if root is None or parts is None:
        return ""
    _root, code, year, cp, strike, _key = parts
    try:
        mm = month_from_code(code)
    except ValueError:
        return ""
    if len(year) == 2:
        key = f"{2000 + int(year):04d}-{mm:02d}"
    elif month[:4].isdigit():
        key = f"{month[:4]}-{mm:02d}"
    else:
        return ""
    return leg_name(root, key, strike=strike, option_type=cp)


# ------------------------------------------------------------------ the type rule
class _Part:
    """One part of a trade: a calendar on one root, the cross spread of the roots' directional
    parts, or an outright. ``legs``: {contract id: signed lots in the part}."""

    def __init__(self, kind: str, roots: Sequence[str], legs: Dict[str, float], note: str = ""):
        self.kind, self.roots, self.legs, self.note = kind, list(roots), dict(legs), note
        self.closed_roots: List[str] = []
        self.of_kind = kind          # an UNMATCHED part: the kind of the part its legs came from
        self.split = False           # a two-leg spread cut out of a bigger part by ``_split_parts``


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


def _tonnes_per_lot(a: st.PLeg, b: st.PLeg) -> Tuple[Optional[float], Optional[float], str]:
    """(one lot of ``a``, of ``b``, the unit) in the first physical unit both convert to
    (contract-master's unit table: t, bbl, mmbtu); (None, None, '') when none."""
    for unit in st.COMMON_UNITS:
        try:
            return a.physical(1.0, unit), b.physical(1.0, unit), unit
        except ValueError:
            continue
    return None, None, ""


def _month_pairs(legs: List[st.PLeg], rem: Dict[str, float]) -> List[_Part]:
    """Rule 0 of the type (user decision 2026-09-29, pair by tonnage per month): one commodity
    held on two exchanges across months pairs its SAME-MONTH legs of the two exchanges first,
    opposite ways, when their tonnage balances within 10 % (``UNBALANCED``): the smaller side in
    full, the larger side the same tonnage (whole lots on a future, exact tonnes on an LME
    ticket). ZNA1: Oct LME +3,150 t against SHFE -3,135 t, Nov LME -5,050 t against SHFE
    +5,015 t, two cross-exchange pairs. What is left goes to the calendar and cross rules.
    ``rem`` (open lots per contract) is reduced in place."""
    parts: List[_Part] = []
    by_sub: Dict[str, List[st.PLeg]] = defaultdict(list)
    for leg in legs:
        by_sub[leg.root.subsector].append(leg)
    for _sub, group in sorted(by_sub.items()):
        if len({leg.root.exchange for leg in group}) < 2:
            continue
        for month in sorted({leg.month for leg in group if leg.month}):
            cands = []
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if a.month != month or b.month != month or a.root.exchange == b.root.exchange:
                        continue
                    ta, tb, unit = _tonnes_per_lot(a, b)
                    if unit:
                        cands.append((a, b, ta, tb, unit))
            while True:
                live = []
                for a, b, ta, tb, unit in cands:
                    ra, rb = rem[a.contract_id], rem[b.contract_id]
                    if abs(ra) <= LEFTOVER_FLOOR or abs(rb) <= LEFTOVER_FLOOR or _sign(ra) == _sign(rb):
                        continue
                    wa, wb = abs(ra) * ta, abs(rb) * tb
                    gap = abs(wa - wb) / max(wa, wb)
                    if gap <= UNBALANCED + 1e-12:
                        live.append((gap, a.contract_id, b.contract_id, a, b, ta, tb, unit))
                if not live:
                    break
                _gap, _ca, _cb, a, b, ta, tb, unit = min(live, key=lambda x: x[:3])
                ra, rb = rem[a.contract_id], rem[b.contract_id]
                w = min(abs(ra) * ta, abs(rb) * tb)
                la = w / ta if not a.whole_lots or abs(ra) * ta <= w + 1e-9 else float(round(w / ta))
                lb = w / tb if not b.whole_lots or abs(rb) * tb <= w + 1e-9 else float(round(w / tb))
                la, lb = min(la, abs(ra)), min(lb, abs(rb))
                rem[a.contract_id] -= _sign(ra) * la
                rem[b.contract_id] -= _sign(rb) * lb
                parts.append(_Part(TYPE_CROSS_EXCHANGE, [a.root_id, b.root_id],
                                   {a.contract_id: _sign(ra) * la, b.contract_id: _sign(rb) * lb},
                                   f"same month on two exchanges, {la * ta:,.0f} against {lb * tb:,.0f} {unit} "
                                   f"(pair by tonnage per month, within {UNBALANCED:.0%})"))
    return parts


def _decompose(book, tids: Sequence[str], legs: List[st.PLeg], roll_ids: set) -> Tuple[List[_Part], Dict[str, float], str]:
    """(parts, the directional lots no part holds per contract, note) of the open legs."""
    rem = {leg.contract_id: leg.lots for leg in legs}
    month_parts = _month_pairs(legs, rem)
    by_root: Dict[str, List[st.PLeg]] = defaultdict(list)
    for leg in legs:
        by_root[leg.root_id].append(leg)
    cid_of, month_of, per_lot = {}, {}, {}
    for leg in legs:
        month_of[leg.contract_id] = leg.month
        per_lot[leg.contract_id] = 1.0 if leg.whole_lots else leg.root.contract_size
        for t in leg.trade_ids:
            cid_of[t] = leg.contract_id
    parts: List[_Part] = list(month_parts)
    residual: Dict[str, Dict[str, float]] = {}
    for root_id, rlegs in sorted(by_root.items()):
        lots = {leg.contract_id: rem[leg.contract_id] for leg in rlegs
                if abs(rem[leg.contract_id]) > LEFTOVER_FLOOR}
        if not lots:
            continue
        if (len(lots) >= 2 and abs(sum(lots.values())) < LEFTOVER_FLOOR
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
            if take < 1.0 - 1e-9:
                continue            # under one lot is leftover, never a spread
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
    if month_parts:
        flat_roots = []             # the month pairs are the spread: what is left is leftover
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


def _physical_roots(roots: Sequence) -> bool:
    """One commodity on several exchanges, or a crack (crude against refined products): sized in
    physical units (``strategies.physical_rule``, user 2026-09-29)."""
    if len({r.subsector for r in roots}) == 1:
        return True
    families = {str(getattr(r, "family", "") or "") for r in roots}
    return families == set(st.CRACK_FAMILIES)


def _shared_unit(book, plegs: Sequence[st.PLeg]) -> str:
    """The physical unit every leg converts to: the unit a pair of the first two roots is levelled
    in (``strategies._level_basis``: a template's quantity unit, the roots' shared quote quantity,
    the first common unit), else contract-master's common units, else a leg's own size unit; ''
    when none fits them all."""
    first = plegs[0]
    other = next((pl for pl in plegs if pl.root_id != first.root_id), None)
    cands: List[str] = []
    if other is not None:
        unit = st._level_basis(book, first, other)[0]
        if unit and unit != "lots":
            cands.append(unit)
    cands += list(st.COMMON_UNITS) + [pl.size_unit for pl in plegs]
    for unit in dict.fromkeys(cands):
        try:
            for pl in plegs:
                pl.physical(1.0, unit)
        except ValueError:
            continue
        return unit
    return ""


def _measure(book, kind: str, plegs: Sequence[st.PLeg]) -> Tuple[str, str, Dict[str, Optional[float]], float, str]:
    """(basis, unit, size of one lot per contract, tolerance, why when no measure) the legs of one
    part are matched in (user, 2026-10-01): a calendar (one root) in lots, within 5 %
    (``grouping.TOLERANCE``); one commodity across exchanges or a crack in its shared physical unit,
    within 10 %; two commodities in USD value at the fill (each leg's ``usd_per_lot``), within 10 %
    (``strategies.VALUE_TOLERANCE``)."""
    if kind == TYPE_CALENDAR or len({pl.root_id for pl in plegs}) == 1:
        return "lots", "lots", {pl.contract_id: 1.0 for pl in plegs}, TOLERANCE, ""
    roots = list({pl.root_id: pl.root for pl in plegs}.values())
    if _physical_roots(roots):
        unit = _shared_unit(book, plegs)
        if unit:
            return "physical", unit, {pl.contract_id: pl.physical(1.0, unit) for pl in plegs}, UNBALANCED, ""
        return "", "", {}, UNBALANCED, "the legs share no physical unit in contract-master's table"
    per = {pl.contract_id: pl.usd_per_lot for pl in plegs}
    missing = [f"{pl.contract_id}: {pl.value_why or 'value at the fill not known'}" for pl in plegs if not pl.usd_per_lot]
    return "value", "USD", per, st.VALUE_TOLERANCE, "; ".join(missing)


def _covering_template(book, part: "_Part", pleg_by_cid: Dict[str, st.PLeg]):
    """The ``grouping.candidates`` match of a ``config/spreads/`` template covering every leg of a
    part of three or more legs (a 3-2-1 crack, a soy crush), else None."""
    plegs = [pleg_by_cid[c] for c in sorted(part.legs) if c in pleg_by_cid]
    if len(plegs) < 3 or len(plegs) != len(part.legs):
        return None
    legs = [pl.as_leg(part.legs[pl.contract_id], pl.trade_ids) for pl in plegs]
    covering = [m for m in book.candidates(legs) if m.shape.kind != CALENDAR and len(m.legs) == len(legs)]
    if not covering:
        return None
    return min(covering, key=lambda x: (round(x.deviation, 9), x.shape.order))


def _amount_text(x: float, basis: str) -> str:
    """'2,521', '125.4', '3,135': a size at its own precision (lots to two decimals when not whole)."""
    if basis == "lots" and abs(x - round(x)) > 1e-6:
        return f"{x:,.2f}".rstrip("0").rstrip(".")
    return f"{x:,.0f}"


def _split_parts(book, parts: List["_Part"], pleg_by_cid: Dict[str, st.PLeg], one_roots: set,
                 names: Dict[str, str]) -> List["_Part"]:
    """Each part of three or more legs cut into its spreads (user, 2026-10-01): one long and one
    short leg of equal size make a spread of their own, each with its own level. SCO1's iron ore,
    short Oct26 2,521 and Nov26 1,000 against long Feb27 2,521 and Mar27 1,000, is two calendars,
    Oct26/Feb27 2,521 lots and Nov26/Mar27 1,000 lots, never one 4-leg spread.

    The rule, in this order, on the part's legs (``_measure``: lots on one root, physical units for
    one commodity across exchanges or a crack, USD value at the fill between two commodities):

    1. Equal size: a long and a short leg (two months of the root in a calendar, two roots in a
       cross part) whose sizes agree within the tolerance pair whole, the closest sizes first, then
       the nearest months.
    2. One leg left on one side: it is split across the legs left on the other side, the nearest
       month first, each taking its whole size (STEEL: long HRC Oct26 175 and Nov26 25 against
       short Dec26 200 is Oct/Dec26 175 and Nov/Dec26 25): only one way exists, so nothing is
       guessed.
    3. Anything left (several legs on each side and no two of equal size, or a side held one way
       beyond the spreads) is the part's **unmatched** remainder, a part of type UNMATCHED with no
       level and its reason in plain words; which month pairs with which is never guessed.

    Kept whole: a part of two legs or fewer, an outright, a cross part over the two commodities of
    a trade's one spread (``strategies``' one_spread rule, user 2026-09-29: CATTLE, sized by value
    side against side), a part a ``config/spreads/`` template covers whole (a crack, a crush), a
    part whose legs have no measure, and a cross part where no spread is found (its level stays the
    pairs' own). Grouping only: no P&L figure moves."""
    out: List[_Part] = []
    for part in parts:
        cids = list(part.legs)
        if (part.kind not in (TYPE_CALENDAR, TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT) or len(cids) <= 2
                or part.closed_roots or any(c not in pleg_by_cid for c in cids)):
            out.append(part)
            continue
        cross = part.kind != TYPE_CALENDAR
        if cross and (set(part.roots) <= one_roots or _covering_template(book, part, pleg_by_cid) is not None):
            out.append(part)
            continue
        plegs = {c: pleg_by_cid[c] for c in cids}
        basis, unit, per, tol, why = _measure(book, part.kind, list(plegs.values()))
        if why or not per or any(not per.get(c) for c in cids):
            out.append(part)
            continue
        rem = dict(part.legs)
        pieces: List[_Part] = []

        def size(c: str) -> float:
            return abs(rem[c]) * float(per[c])

        def words(x: float) -> str:
            return f"{_amount_text(x, basis)} {unit}" if basis != "value" else f"{x:,.0f} USD"

        def piece(a: str, b: str, la: float, lb: float, how: str) -> None:
            ra, rb = plegs[a].root, plegs[b].root
            kind = TYPE_CALENDAR if ra.root_id == rb.root_id else _cross_kind([ra, rb])
            p = _Part(kind, list(dict.fromkeys([ra.root_id, rb.root_id])), {a: la, b: lb}, how)
            p.split = True
            pieces.append(p)
            rem[a] -= la
            rem[b] -= lb

        while True:                                          # 1. equal size
            cands = []
            for i, a in enumerate(cids):
                for b in cids[i + 1:]:
                    if (abs(rem[a]) <= LEFTOVER_FLOOR or abs(rem[b]) <= LEFTOVER_FLOOR or _sign(rem[a]) == _sign(rem[b])
                            or (plegs[a].root_id == plegs[b].root_id) == cross):
                        continue
                    wa, wb = size(a), size(b)
                    gap = abs(wa - wb) / max(wa, wb)
                    if gap <= tol + 1e-12:
                        apart = abs(_month_index(plegs[a].month) - _month_index(plegs[b].month))
                        cands.append((round(gap, 12), apart, a, b))
            if not cands:
                break
            _gap, _apart, a, b = min(cands)
            piece(a, b, rem[a], rem[b], f"equal size: {words(size(a))} against {words(size(b))}")
        longs = [c for c in cids if rem[c] > LEFTOVER_FLOOR]
        shorts = [c for c in cids if rem[c] < -LEFTOVER_FLOOR]
        if longs and shorts and (len(longs) == 1 or len(shorts) == 1):   # 2. one leg left on one side
            single, others = (longs[0], shorts) if len(longs) == 1 else (shorts[0], longs)
            side = "long" if rem[single] > 0 else "short"
            for o in sorted(others, key=lambda c: (abs(_month_index(plegs[c].month) - _month_index(plegs[single].month)),
                                                   plegs[c].month, c)):
                if (plegs[o].root_id == plegs[single].root_id) == cross:
                    continue
                w = min(size(single), size(o))
                if w <= _EPS:
                    break
                ls, lo = w / float(per[single]), w / float(per[o])
                if cross:
                    if plegs[single].whole_lots:
                        ls = min(float(round(ls)), abs(rem[single]))
                    if plegs[o].whole_lots:
                        lo = min(float(round(lo)), abs(rem[o]))
                if ls < LEFTOVER_FLOOR or lo < LEFTOVER_FLOOR:
                    continue
                piece(single, o, _sign(rem[single]) * ls, _sign(rem[o]) * lo,
                      f"the one {side} leg left, {names.get(single, single)}, split across the other side: "
                      f"{words(ls * float(per[single]))} against {names.get(o, o)}")
        left = {c: rem[c] for c in cids if abs(rem[c]) > LEFTOVER_FLOOR}
        if cross and not pieces:
            out.append(part)
            continue
        out.extend(sorted(pieces, key=lambda p: (min(plegs[c].month for c in p.legs), sorted(p.legs))))
        if left:
            listed = ", ".join(f"{names.get(c, c)} {'long' if v > 0 else 'short'} "
                               f"{_amount_text(abs(v) * float(per[c]), basis)}"
                               f"{' lots' if basis == 'lots' else ' USD' if basis == 'value' else ' ' + unit}"
                               for c, v in sorted(left.items()))
            if pieces and len({_sign(v) for v in left.values()}) == 1:
                why_left = "held one way beyond the spreads above, so there is no spread to read a level from"
            else:
                why_left = (f"no two of these legs are of equal size (within {tol:.0%}) and more than one is held "
                            f"each way, so which month pairs with which is not known: no level")
            rest = _Part(TYPE_UNMATCHED, part.roots, left, f"Unmatched legs ({listed}): {why_left}")
            rest.of_kind = part.kind
            out.append(rest)
    return out


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
    return (f"Labelled {said} ({', '.join(pb_roots)}), but by the rule its legs make "
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


_FX_WORDS = {"FX_SPOT": "spot", "FX_FWD": "forward", "FX_SWAP": "swap"}


def _day_words(iso: str) -> str:
    """'20 Jan 27' for '2027-01-20'; the text itself when it is not a date."""
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year % 100:02d}"


def _option_terms(book) -> Dict[str, tuple]:
    """{instrument_id: (strike, option_type, payoff)} of ``instrument_options``, read once per book."""
    terms = getattr(book, "_trade_book_option_terms", None)
    if terms is None:
        try:
            terms = {str(r[0]): (_num(r[1]), str(r[2] or ""), str(r[3] or ""))
                     for r in book.conn.execute("SELECT instrument_id, strike, option_type, payoff FROM instrument_options")}
        except Exception:  # noqa: BLE001 -- an old database without the table: no terms, the plain name says 'option'
            terms = {}
        book._trade_book_option_terms = terms
    return terms


def fx_name(book, t: dict, prompt: str) -> str:
    """A currency leg in plain words: 'USDCNH 20 Jan 27 forward', 'EURUSD 18 Nov 26 call 1.0500'
    (strike and type from ``instrument_options`` when known, else 'option'; a payoff other than
    vanilla named after it)."""
    from engine.spreads.hedges import pair_currencies
    base, quote = pair_currencies(t["base_ccy"], t["quote_ccy"], t["instrument_id"])
    pair = f"{base}{quote}"
    if t["product"] != "FX_OPTION":
        return f"{pair} {_day_words(prompt)} {_FX_WORDS.get(t['product'], 'trade')}".strip()
    expiry = str(t["expiry_date"] or "")
    strike, kind, payoff = _option_terms(book).get(str(t["instrument_id"]), (None, "", ""))
    if not strike or kind not in ("CALL", "PUT"):
        return f"{pair} {_day_words(expiry)} option".strip()
    places = 3 if "JPY" in pair else 2 if {base, quote} & {"XAU", "XAG"} else 4
    tail = "" if payoff in ("", "VANILLA", "AMERICAN") else f" {payoff.lower().replace('_', ' ')}"
    return f"{pair} {_day_words(expiry)} {kind.lower()} {strike:.{places}f}{tail}"


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


# the products value_book values as quantity x (mark - fill) with no multiplier (the FX rule, an
# LME ticket, an FX option on its premium)
_NO_MULTIPLIER = st.FX_PRODUCTS + ("LME_FWD", "FX_OPTION")


def _split_leg(book, row: dict) -> Tuple[Optional[float], Optional[float], str]:
    """(open, locked in, why when None) of one leg's P&L since entry (user yes under hard rule 7,
    2026-09-30): a split of the leg's LTD, never a new figure.

    open = the open net x multiplier x (the as-of mark - the open lots' average entry) x the as-of
    USD spot, the average by ``strategies._entry_value``'s rule (the leg's ``avg_fill``: an add
    averages in, a reduction leaves the average, a day netting to zero changes nothing); locked in
    = the leg's LTD - open, so the two add up to the LTD exactly. A leg with nothing open is all
    locked in. The mark and spot are the ones the leg's own ``value_book`` rows carry, and every
    open row must be exactly quantity x multiplier x (mark - fill) x spot on them (the check that
    the open lots are priced on the average's terms); a leg that is not (a metal option dealt per
    ounce, open fills marked at two closes) is not split, with the reason, never guessed. Locked
    in then equals -sum(q x fill) + open net x average (times the spot): it does not move with the
    mark, and on a leg never reduced it is zero."""
    ltd = (row.get("pnl_usd") or {}).get("ltd")
    if row.get("unrecognised"):
        return None, None, row.get("unrecognised_reason") or "contract not recognised"
    if ltd is None:
        return None, None, (row.get("pnl_reasons") or {}).get("ltd") or "no P&L since entry"
    if row["status"] != "open":
        return 0.0, float(ltd), ""
    name = row.get("name") or row.get("contract_id")
    avg, mark = row.get("avg_fill"), row.get("mark")
    if avg is None:
        return None, None, f"{name}: no average entry of the open lots ({row.get('avg_fill_reason') or 'not known'})"
    if mark is None:
        return None, None, f"{name}: no mark ({row.get('mark_reason') or 'not valued'})"
    t = book.by_id[row["open_trade_ids"][0]]
    mult = 1.0 if t["product"] in _NO_MULTIPLIER else _num(t["multiplier"])
    if mult is None:
        return None, None, f"{name}: its multiplier is not a number"
    spots, marks = set(), set()
    for tid in row["open_trade_ids"]:
        r = book.today.get(tid) or {}
        q, f = book.lots(tid), _num(book.by_id[tid]["price"])
        m, s, p = _num(r.get("mark")), _num(r.get("spot")), _num(r.get("pnl_usd"))
        if q is None or f is None or m is None or s is None or p is None:
            return None, None, f"{name}: {tid} has no mark, spot or P&L to split on"
        if abs(q * mult * (m - f) * s - p) > max(0.005, 1e-9 * abs(p)):
            return None, None, (f"{name}: {tid}'s P&L is not lots x multiplier x (mark - fill) x spot, "
                                f"so its open lots cannot be split from what was taken off")
        spots.add(s)
        marks.add(m)
    if max(marks) - min(marks) > 1e-9 * max(1.0, abs(mark)) or max(spots) - min(spots) > 1e-12 * max(1.0, max(spots)):
        return None, None, f"{name}: its open fills are marked at different closes, so the open lots are not split"
    s, m = spots.pop(), marks.pop()
    if not _unwound(book, row):
        # never reduced: all of it is open (locked in is exactly 0 in theory; float dust never shows)
        return float(ltd), 0.0, ""
    opened = float(row["quantity"]) * mult * (m - float(avg)) * s
    return opened, float(ltd) - opened, ""


def _split_trade(rows: List[dict], ltd, ltd_why: str = "") -> Tuple[Optional[float], Optional[float], str]:
    """(open, locked in, why when None) of a trade: open = its legs' open summed, locked in = the
    trade's LTD - open, so the two add up to the LTD the Book shows. None both, with the first
    leg's reason, when any leg cannot be split; a closed trade is all locked in."""
    if ltd is None:
        return None, None, ltd_why or "no P&L since entry"
    bad = [r for r in rows if r.get("pnl_open") is None]
    if bad:
        why = "; ".join(str(r.get("pnl_split_reason") or f"{r.get('name')}: not split") for r in bad)
        return None, None, f"not split into locked in and open: {why}"
    opened = float(sum(float(r["pnl_open"]) for r in rows))
    locked = float(ltd) - opened
    if abs(locked) < 0.005:
        # float dust of the legs' sum: nothing locked in (open + locked still = LTD exactly)
        locked = 0.0
    return float(ltd) - locked, locked, ""


def _unwound(book, row: dict) -> bool:
    """Some lots of this leg were taken off (a reduction, a roll out of it, a close, an expiry or
    settlement): the leg has locked-in P&L. A leg never reduced has not."""
    if row.get("unrecognised"):
        return False
    if row["status"] != "open":
        return bool(row.get("trade_ids"))
    gross = sum(abs(book.lots(i) or 0.0) for i in row["trade_ids"])
    return gross > abs(float(row["quantity"])) + 1e-6 * max(1.0, gross)


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
            bad = _unrecognised_row(book, cid, ids, symbols)
            bad.update(pnl_open=None, pnl_locked=None, pnl_split_reason=bad["unrecognised_reason"], unwound=False)
            rows.append(bad)
            continue
        root = book.roots.get(str(t["base_ccy"] or ""))
        month, prompt = meta[cid]
        hedge = is_hedge(root, t["product"], t["base_ccy"], t["quote_ccy"], t["instrument_id"])
        open_ids = [i for i in ids if book.is_open(i)]
        qty = sum(book.lots(i) or 0.0 for i in open_ids)
        per_lot = root.contract_size if (t["product"] == "LME_FWD" and root is not None) else 1.0
        status = "open" if abs(qty) > _EPS else ("flat" if open_ids else "closed")
        lots = qty / per_lot if status == "open" else 0.0
        if t["product"] in st.FX_PRODUCTS or t["product"] == "FX_OPTION":
            name = fx_name(book, t, prompt)
        elif t["product"] in st.OPTION_PRODUCTS and root is not None:
            # an option in plain words with its strike ('NYMEX Crude oil Dec26 75 call'): two strikes
            # never read alike; the old ticker form only when the id is not an option ticker
            name = (option_leg_name(root, str(t["instrument_id"]), month)
                    or f"{root.exchange} {_title(commodity_words(root))} {str(t['instrument_id']).replace(' Comdty', '')}")
            if root.root_id in twins:
                name = name.replace(f"{root.exchange} {_title(commodity_words(root))}",
                                    f"{root.exchange} {_twin_words(root)}", 1)
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
        p_open, p_locked, p_why = _split_leg(book, row)
        row.update(pnl_open=p_open, pnl_locked=p_locked, pnl_split_reason=p_why, unwound=_unwound(book, row))
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
            "reason": why, "entry_estimated": False, "prev_estimated": False, "now_estimated": False,
            "entry_estimate_note": "", "prev_estimate_note": "", "now_estimate_note": "", "estimate_note": "",
            "template": "", "price_legs": []}


def _set_estimates(out: dict, notes: Dict[str, str]) -> dict:
    """``<key>_estimated`` / ``<key>_estimate_note`` for entry, prev and now from ``notes`` (the
    sentence naming each near-marks estimate the figure rests on, '' when exact), and
    ``estimate_note`` joining them. Only a figure that is there is estimated."""
    words = {"entry": "entry", "prev": "previous close", "now": "now"}
    joined = []
    for key in ("entry", "prev", "now"):
        note = str((notes or {}).get(key) or "") if out.get(key) is not None else ""
        out[f"{key}_estimated"] = bool(note)
        out[f"{key}_estimate_note"] = note
        if note:
            joined.append(f"{words[key]}: {note}")
    out["estimate_note"] = "; ".join(joined)
    return out


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
               sources={"entry": "the open lots' average fill", "now": r["mark_source"], "prev": r["prev_mark_source"]},
               price_legs=[{"instrument_id": r["instrument_id"], "trade_ids": list(r["open_trade_ids"]),
                            "weight": 1.0, "price_scale": 1.0}])
    return _set_estimates(out, {
        "now": f"{r['name']} price estimated ({r['mark_source']})" if is_estimate(r["mark_source"]) else "",
        "prev": (f"{r['name']} price estimated ({r['prev_mark_source']})"
                 if is_estimate(r["prev_mark_source"]) else "")})


def _open_side_level(book, part: _Part, rows: List[dict], pleg_by_cid: Dict[str, st.PLeg], prev_day: str,
                     prev_rows: Dict[str, dict]) -> dict:
    """The level of a cross part whose other side is flat now (``part.closed_roots``; SILARB1:
    COMEX silver bought and sold, SHFE silver still short): the open side's own level, by the
    rule for its shape. One open contract: its price (``_price_level``, entry the open lots'
    average fill, now its mark). Several months of the one root, long and short: the calendar
    level. Anything else: blank. The part's note (which side is flat) rides along as ``note``.
    A level only: nothing here enters a P&L figure."""
    why = part.note or "no open pair of legs to read a level from"
    cids = set(part.legs)
    if len(cids) == 1:
        own = [r for r in rows if r["contract_id"] in cids]
        level = _price_level(own, _blank_level(why))
    elif (len({pleg_by_cid[c].root_id for c in cids if c in pleg_by_cid}) == 1 and all(c in pleg_by_cid for c in cids)
          and len({_sign(v) for v in part.legs.values()}) == 2):
        level = _calendar_level(book, part, pleg_by_cid, prev_day, prev_rows)
    else:
        return _blank_level(why)
    if level.get("entry") is None and level.get("now") is None and not level.get("mode"):
        return _blank_level(_join([level.get("reason", ""), why]))
    return {**level, "note": _join([level.get("note", ""), part.note])}


def _premium_level(book, rows: List[dict]) -> Optional[dict]:
    """A trade holding options only (WTIRR1's call against put, EURVOL1's EURUSD options): its net
    premium per unit, None when it holds anything else open. The legs are the open non-hedge rows
    (a trade of currency options alone: all its open rows, they are its position). Each option's
    open quantity ``q`` (lots; an FX option's notional) and price (a listed option's official
    FUTURE_PX, an FX option's PREMIUM, as quoted), the level

        sum q x price x price_scale / N,  N = the largest |q|

    at entry from each leg's open lots' average fill, now and on the previous close from each
    leg's ``value_book`` mark (the price its P&L reads). A listed option's is in its root's quote
    unit (price x ``price_scale``, like every level); an FX option's premium is a fraction of the
    pair's base notional, unit ''. Options on two underlyings have no common unit: a blank with
    its reason. ``usd_per_unit``: N x multiplier / price_scale x the as-of USD spot of the quote
    currency (an FX option: N x the base currency's), so change x usd_per_unit is the options'
    P&L on the move. A level only: nothing here enters a P&L figure."""
    live = [r for r in rows if r["status"] == "open" and not r["unrecognised"]]
    core = [r for r in live if not r["hedge"]] or live
    if not core or any(r["product"] not in st.OPTION_PRODUCTS for r in core):
        return None
    out = _blank_level("")
    fx = [r for r in core if r["product"] == "FX_OPTION"]
    if fx and len(fx) != len(core):
        return _blank_level("listed options and FX options in one trade: no common premium unit, so no level")
    if fx:
        pairs = {r["instrument_id"][:6] for r in core}
        if len(pairs) != 1:
            return _blank_level(f"FX options on {len(pairs)} pairs ({', '.join(sorted(pairs))}): no common "
                                f"premium unit, so no level")
        pair = pairs.pop()
        base = pair[:3]
        unit, scale, ccy_usd = "", 1.0, base
        unit_words = f"{base} per {base} of notional"
        size_word = f" {base}"
        mults = {1.0}
    else:
        roots = {r["root_id"] for r in core}
        root = book.roots.get(next(iter(roots))) if len(roots) == 1 else None
        if root is None:
            return _blank_level(f"options on {len(roots)} underlyings ({', '.join(sorted(roots))}): no common "
                                f"premium unit, so no level" if len(roots) > 1 else
                                f"{next(iter(roots))} is not in config/contracts.csv, so the premium has no unit")
        unit, scale, ccy_usd = root.quote_unit, float(root.price_scale), root.currency
        unit_words = unit
        size_word = " lots"
        mults = {_num(book.by_id[t]["multiplier"]) for r in core for t in r["open_trade_ids"]}
    n_max = max(abs(float(r["quantity"])) for r in core)
    legs = [{"instrument_id": r["instrument_id"], "trade_ids": list(r["open_trade_ids"]),
             "weight": float(r["quantity"]) / n_max, "price_scale": scale} for r in core]

    def read(key: str, why_key: str) -> Tuple[Optional[float], str]:
        missing = [f"{r['name']}: {r[why_key] or 'no price'}" for r in core if r[key] is None]
        if missing:
            return None, "; ".join(missing)
        return float(sum(leg["weight"] * float(r[key]) * scale for leg, r in zip(legs, core))), ""

    entry, entry_why = read("avg_fill", "avg_fill_reason")
    now, now_why = read("mark", "mark_reason")
    prev, prev_why = read("prev_mark", "prev_mark_reason")
    prev_day = next((r["prev_mark_date"] for r in core if r["prev_mark_date"]), "")
    names = ", ".join(f"{'long' if r['quantity'] > 0 else 'short'} {r['name']}" for r in core)
    basis = (f"the options' net premium per unit of the largest leg ({abs(n_max):,.0f}{size_word}): "
             f"sum of quantity x premium / {abs(n_max):,.0f}, in {unit_words}")
    out.update(unit=unit, entry=entry, entry_reason=entry_why, now=now, now_reason=now_why, prev=prev,
               prev_reason=prev_why, prev_date=prev_day,
               change=(now - prev) if now is not None and prev is not None else None,
               change_reason=_join([now_why, prev_why]), label=names, mode="premium", source="premium",
               note=basis, price_legs=legs,
               sources={"entry": "each option's open lots' average fill",
                        "now": _join(f"{r['name']} {r['mark_source']}" for r in core),
                        "prev": _join(f"{r['name']} {r['prev_mark_source']}" for r in core)})
    s, _pair, _src = (1.0, "", "")
    if ccy_usd != "USD":
        try:
            from engine.pnl.valuation import usd_per_quote
            s, _pair, _src = usd_per_quote(book.conn, ccy_usd, book.as_of)
        except Exception as exc:  # noqa: BLE001 -- a stored spot that is not a number: named, not raised
            s, _src = None, f"the {ccy_usd} SPOT of {book.as_of} could not be read ({exc})"
    s = _num(s)
    mult = next(iter(mults)) if len(mults) == 1 else None
    if len(mults) != 1 or mult is None:
        out["usd_per_unit_reason"] = "the options' multipliers differ or are not numbers: no USD per unit"
    elif not s:
        out["usd_per_unit_reason"] = _src or f"no SPOT for USD conversion of {ccy_usd} on {book.as_of}"
    else:
        out.update(usd_per_unit=n_max * mult / scale * s, usd_per_unit_reason="")
    return _set_estimates(out, {
        "now": _join(f"{r['name']} price estimated ({r['mark_source']})" for r in core if is_estimate(r["mark_source"])),
        "prev": _join(f"{r['name']} price estimated ({r['prev_mark_source']})" for r in core
                      if is_estimate(r["prev_mark_source"]))})


def _open_blank(book, rows: List[dict]) -> dict:
    """The level of a trade no level rule fits, with the right reason: 'nothing open' only when
    nothing is."""
    live = [r for r in rows if r["status"] == "open"]
    if not live:
        unknown = [r["name"] for r in rows if r.get("unrecognised")]
        if unknown:
            return _blank_level(f"only contracts the app does not recognise ({', '.join(unknown)}): no level "
                                f"until they are mapped")
        return _blank_level("nothing open: no level")
    return _blank_level("no level rule fits these open legs (" + ", ".join(r["name"] for r in live)
                        + "): a level is a spread's, one contract's price or an options trade's premium")


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
    _set_estimates(out, pair.get("level_estimated") or {})
    spec = spec_from_dict(pair.get("level_spec"))
    if spec is not None:
        out["note"] = _closes_apart([book.roots[leg.root_id] for leg in spec.legs if leg.root_id in book.roots],
                                    book.as_of)
    return out


def _month_pair_level(book, part: _Part, pleg_by_cid: Dict[str, st.PLeg], prev_day: str,
                      prev_rows: Dict[str, dict]) -> dict:
    """The level of one two-leg cross pair (a same-month pair of one commodity on two exchanges, or
    a spread ``_split_parts`` cut out): ``strategies``' level rule on its two legs, a
    China-against-the-West pair the converted ratio, else the template's or common unit."""
    (ca, la), (cb, lb) = sorted(part.legs.items())
    a, b = pleg_by_cid[ca], pleg_by_cid[cb]
    rule = CROSS_PRODUCT if part.kind == TYPE_CROSS_PRODUCT else CROSS_EXCHANGE
    level_unit, lupl_a, lupl_b, template, _also, level_why = st._level_basis(book, a, b)
    first, second = st._order(rule, a, b, template)
    if first is b:
        la, lb, lupl_a, lupl_b = lb, la, lupl_b, lupl_a
    p = {"rule": rule, "a": first, "b": second, "lots_a": la, "lots_b": lb, "template": template,
         "level_unit": level_unit, "lupl_a": lupl_a, "lupl_b": lupl_b, "level_why": level_why}
    lv = st._levels(book, p, prev_day, prev_rows)
    label = (f"{first.contract_id} / {second.contract_id}" if lv.get("unit") == st.RATIO_UNIT
             else f"{first.contract_id} - {second.contract_id}")
    return _pair_level(book, {**lv, "level_label": label})


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
    entry, entry_why, entry_src, _, entry_est = book.entry_level(spec)
    now, now_why, now_src, _, now_est = book.level_on(spec, book.as_of, book.today, fallback=False)
    prev, prev_why, prev_src, _, prev_est = book.level_on(spec, prev_day, prev_rows, fallback=True)
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
    return _set_estimates(out, {"entry": "; ".join(entry_est), "now": "; ".join(now_est), "prev": "; ".join(prev_est)})


def _template_level(book, part: _Part, pleg_by_cid: Dict[str, st.PLeg], prev_day: str,
                    prev_rows: Dict[str, dict]) -> Optional[dict]:
    """The level of a part of three or more legs that one ``config/spreads/`` template covers
    whole (a 3-2-1 crack: RBOB, heating oil, WTI; a soy board crush: meal, oil, beans): every leg
    of the part one leg of the template, signs that fit its weights (``grouping.candidates``, the
    grouping rule's own matcher; the closest ratio, then the template file's order, when two
    fit). The level is the template's formula in its unit (``levels.py``), at
    entry from the fills, on the previous close and now from the marks, like every level; a
    ratio of lots outside the template's 5 % is said in ``note``, and the size (USD per unit) is
    the template's fitted size, its smallest leg. None when no template covers the part."""
    m = _covering_template(book, part, pleg_by_cid)
    if m is None:
        return None
    spec, why = spec_for(m.shape, m.legs, book.roots, book.templates)
    if spec is None:
        return _blank_level(why)
    diff = st._difference(book, spec, prev_day, prev_rows)
    s_unit, s_why = book.unit_spot(spec, book.as_of, exact=False)
    out = _blank_level("")
    note = (f"{m.shape.name}: the lots are {m.deviation:.0%} off the template's ratio; the level is the "
            f"template's formula, the size its smallest leg" if not m.matched else m.shape.name)
    label = m.shape.name
    out.update(unit=spec.unit, entry=diff["entry"], entry_reason=diff["entry_reason"], now=diff["now"],
               now_reason=diff["now_reason"], prev=diff["prev"], prev_reason=diff["prev_reason"], prev_date=prev_day,
               change=diff["change"], change_reason=_join([diff["now_reason"], diff["prev_reason"]]),
               label=label,
               sources=dict(diff["sources"]), spec=spec_to_dict(spec), mode="difference", source="template",
               template=m.shape.kind, note=note,
               usd_per_unit=None if s_unit is None else usd_per_level_unit(m.size, spec, s_unit),
               usd_per_unit_reason=s_why if s_unit is None else "")
    return _set_estimates(out, diff.get("estimated") or {})


# ------------------------------------------------------------------ carry
def _roll_downs(book, rows: List[dict], pleg_by_cid: Dict[str, st.PLeg], history,
                memo: Optional[dict] = None) -> Tuple[Optional[float], str, str]:
    """Each open non-hedge leg's roll-down per month (``carry.curve_roll_downs``, on the chain's
    Bloomberg closes), written on its row; (the trade's carry per month in USD when every such leg
    is on one curve and read, else None, why, the date of the closes read)."""
    from engine.spreads.carry import curve_roll_downs
    legs = [r for r in rows if not r["hedge"] and r["status"] == "open" and r["contract_id"] in pleg_by_cid]
    if not legs:
        return None, "no open leg to roll down", ""
    if history is None:
        for r in legs:
            r["roll_down_reason"] = "price history not loaded"
        return None, "price history not loaded", ""
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
        if res["history_date"]:
            dates.append(res["history_date"])
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
            f"Contract not recognised: {', '.join(unknown)}" if unknown else "Nothing open")
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
    if kind == TYPE_CALENDAR and len(parts) > 1 and len({tuple(sorted(p.roots)) for p in parts}) > 1:
        text = " + ".join(_part_what(book, p, {r["contract_id"]: r for r in rows}) for p in parts)
    elif kind == TYPE_CALENDAR and roots:
        text += f" Calendar, {months}" if months else " Calendar"
    elif roots and open_rows and all(r["product"] in st.OPTION_PRODUCTS for r in open_rows):
        text += " Options (" + ", ".join(r["name"] for r in open_rows) + ")"
    elif months:
        text += f", {months}"
    if hedge.get("present") and hedge.get("currency") and roots:
        text += f", {hedge['currency']} hedged"
    return text


def _part_what(book, part: _Part, rows_by_cid: Dict[str, dict]) -> str:
    """'SGX Iron Ore Oct26/Feb27 Calendar', 'SHFE Copper vs COMEX Copper, Nov/Dec26', 'Unmatched:
    SGX Iron Ore Oct/Nov26 & Feb/Mar27' (Title Case, user 2026-10-01)."""
    roots = [book.roots[r] for r in part.roots if r in book.roots]
    months = _months_text(rows_by_cid[c]["month"] for c in part.legs if c in rows_by_cid)
    if part.kind == TYPE_UNMATCHED:
        named = " + ".join(rows_by_cid.get(c, {}).get("name", c) for c in sorted(
            part.legs, key=lambda c: (rows_by_cid.get(c, {}).get("month", ""), c)))
        return f"Unmatched: {named}"
    if part.kind == TYPE_CALENDAR and roots:
        return f"{roots[0].exchange} {commodity_words(roots[0])} {months} Calendar"
    if roots:
        roots.sort(key=lambda r: r.country != st.CHINA)
        return " vs ".join(f"{r.exchange} {commodity_words(r)}" for r in roots) + (f", {months}" if months else "")
    return ""


def _side_text(x: float, basis: str, unit: str) -> str:
    if basis == "value":
        return f"${x:,.0f}"
    return f"{_amount_text(x, basis)} {unit}" if basis != "lots" else _amount_text(x, basis)


def _size_sides(book, items: Sequence[Tuple[st.PLeg, float]], names: Dict[str, str],
                one: Optional[dict] = None) -> dict:
    """The size of each side of a spread in its natural unit (user, 2026-10-01; display data, never
    a P&L): one commodity on one exchange in lots ('2,521 v 2,521 lots'; an LME ticket in tonnes);
    one commodity across exchanges, or a crack, in the physical unit they share ('3,135 t v 3,135
    t', ``_shared_unit``); two commodities in USD value at the fill ('$15,094,650 v $14,928,200',
    each leg's ``usd_per_lot``; a trade's one spread, ``one``, its sides' own values, the balance
    rule's). Lots of two contracts are never added. ``long`` / ``short`` are sizes (>= 0)."""
    out = {"basis": "", "unit": "", "long": None, "short": None, "long_label": "", "short_label": "", "text": "",
           "reason": ""}
    items = [(pl, q) for pl, q in items if abs(q) > LEFTOVER_FLOOR]
    if not items:
        out["reason"] = "nothing open"
        return out
    for key, sel in (("long", [x for x in items if x[1] > 0]), ("short", [x for x in items if x[1] < 0])):
        out[f"{key}_label"] = " + ".join(names.get(pl.contract_id, pl.contract_id) for pl, _q in sel)
    roots = list({pl.root_id: pl.root for pl, _q in items}.values())
    unit = ""
    if len(roots) == 1:
        basis = "lots" if all(pl.whole_lots for pl, _q in items) else "physical"
        unit = "lots" if basis == "lots" else items[0][0].size_unit
    elif _physical_roots(roots):
        unit = _shared_unit(book, [pl for pl, _q in items])
        basis = "physical" if unit else "value"
    else:
        basis = "value"
    if basis == "value":
        unit = "USD"
        if one is not None:
            vals = [s.get("value_fill_usd") for s in one.get("sides") or []]
            if any(v is None for v in vals):
                out.update(basis=basis, unit=unit, reason=_join(s.get("value_fill_reason", "") for s in one["sides"])
                           or "the value at the fill is not known")
                return out
            out["long_label"] = " + ".join(" / ".join(f"{book.roots[r].exchange} {commodity_words(book.roots[r])}"
                                                       for r in s.get("root_ids") or [] if r in book.roots)
                                           for s in one["sides"] if s["value_fill_usd"] > 0)
            out["short_label"] = " + ".join(" / ".join(f"{book.roots[r].exchange} {commodity_words(book.roots[r])}"
                                                        for r in s.get("root_ids") or [] if r in book.roots)
                                            for s in one["sides"] if s["value_fill_usd"] < 0)
            longs = sum(v for v in vals if v > 0)
            shorts = -sum(v for v in vals if v < 0)
        else:
            missing = [f"{names.get(pl.contract_id, pl.contract_id)}: {pl.value_why or 'value at the fill not known'}"
                       for pl, _q in items if not pl.usd_per_lot]
            if missing:
                out.update(basis=basis, unit=unit, reason="; ".join(missing))
                return out
            longs = sum(q * pl.usd_per_lot for pl, q in items if q > 0)
            shorts = -sum(q * pl.usd_per_lot for pl, q in items if q < 0)
    elif basis == "lots":
        longs = sum(q for _pl, q in items if q > 0)
        shorts = -sum(q for _pl, q in items if q < 0)
    else:
        longs = sum(pl.physical(q, unit) for pl, q in items if q > 0)
        shorts = -sum(pl.physical(q, unit) for pl, q in items if q < 0)
    out.update(basis=basis, unit=unit, long=float(longs), short=float(shorts))
    tail = "" if basis != "lots" else " lots"
    if longs > _EPS and shorts > _EPS:
        out["text"] = f"{_side_text(longs, basis, unit)} v {_side_text(shorts, basis, unit)}{tail}"
    else:
        word, x = ("Long", longs) if longs > _EPS else ("Short", shorts)
        out["text"] = f"{word} {_side_text(x, basis, unit)}{tail}"
    return out


def _row_unit(book, row: dict) -> str:
    """The unit an open leg row of no futures part is sized in (user, 2026-10-01): an FX spot,
    forward or option its base currency ('EUR', 'USD'), troy ounces for a precious-metal pair;
    an option on a future (or any listed contract) its lots."""
    if row["product"] in st.FX_PRODUCTS or row["product"] == "FX_OPTION":
        ids = row.get("open_trade_ids") or row.get("trade_ids") or []
        base = str(book.by_id[ids[0]]["base_ccy"] or "") if ids and ids[0] in book.by_id else ""
        if not base:
            base = str(row.get("instrument_id") or "")[:3]
        return "oz" if base in _PRECIOUS_FAMILY else base
    return "lots"


def _row_sides(book, rows: Sequence[dict]) -> dict:
    """``_size_sides`` for a trade with no open futures or LME leg (user, 2026-10-01): its open FX
    spot / forward, FX option or option-on-future legs in their own unit, the same shape. An FX
    trade its base amount ('Short 100 oz' on XAUUSD, 'Long 10,000,000 EUR'; basis 'notional', unit
    the base currency or 'oz'), an FX option its notional in the base currency, an option on a
    future its lots (basis 'lots'); calls against puts their lots a side ('10 v 10 lots'). The
    trade's own legs first, its hedges only when it holds nothing else. 'nothing open' only when
    nothing is (an open leg the contract list does not know says so instead); legs in two units are
    never added (each leg's size is on its row)."""
    out = {"basis": "", "unit": "", "long": None, "short": None, "long_label": "", "short_label": "", "text": "",
           "reason": ""}
    live = [r for r in rows if r.get("status") == "open" and not r.get("unrecognised")
            and abs(_num(r.get("lots")) or 0.0) > _EPS]
    sel = [r for r in live if not r.get("hedge")] or live
    if not sel:
        unknown = [r for r in rows if r.get("unrecognised")]
        out["reason"] = ("contract not recognised: no size until it is mapped ("
                         + ", ".join(str(r.get("name") or r["contract_id"]) for r in unknown) + ")"
                         if unknown else "nothing open")
        return out
    units = {_row_unit(book, r) for r in sel}
    if len(units) != 1 or "" in units:
        out["reason"] = ("its legs are sized in different units (" + ", ".join(sorted(u or "unknown" for u in units))
                         + "): each leg's size is on its row")
        return out
    unit = units.pop()
    basis = "lots" if unit == "lots" else "notional"
    longs = float(sum(float(r["lots"]) for r in sel if float(r["lots"]) > 0))
    shorts = float(-sum(float(r["lots"]) for r in sel if float(r["lots"]) < 0))
    out.update(basis=basis, unit=unit, long=longs, short=shorts,
               long_label=" + ".join(str(r.get("name") or r["contract_id"]) for r in sel if float(r["lots"]) > 0),
               short_label=" + ".join(str(r.get("name") or r["contract_id"]) for r in sel if float(r["lots"]) < 0))
    if basis == "lots":
        body = (lambda x: _amount_text(x, "lots"))
        tail = " lots"
    else:
        body = (lambda x: f"{x:,.0f} {unit}")
        tail = ""
    if longs > _EPS and shorts > _EPS:
        out["text"] = f"{body(longs)} v {body(shorts)}{tail}"
    else:
        word, x = ("Long", longs) if longs > _EPS else ("Short", shorts)
        out["text"] = f"{word} {body(x)}{tail}"
    return out


_PORTION_FIGS = ("daily", "ltd", "open", "locked", "quantity", "value_local", "value_usd", "roll_down_usd_per_month")


def _shares(total: Optional[float], weights: Sequence[float], cents: bool = True) -> List[Optional[float]]:
    """``total`` cut by ``weights`` (shares summing to 1): each portion but the last rounded to the
    cent, the last the rest, so the portions add up to ``total`` exactly (None stays None)."""
    if total is None:
        return [None] * len(weights)
    out: List[Optional[float]] = []
    for w in weights[:-1]:
        x = float(total) * w
        out.append(round(x, 2) if cents else x)
    out.append(float(total) - sum(out))
    return out


def _leg_portions(subs: List[dict], rows: Sequence[dict]) -> List[dict]:
    """Each spread's legs carry their own portion of the leg (user, 2026-10-01): a leg two spreads
    share (STEEL's HRC Dec26 short 200: 175 in Oct/Dec, 25 in Nov/Dec) is cut by the lots each
    spread holds of it, so each spread's row is the sum of its own legs. A portion is the trade's
    leg row with ``lots`` (the lots this spread holds, as before), ``share`` (its part of the leg:
    its lots / the lots every spread holding the leg holds, so a leg in one spread is whole, share
    1, as before), ``shared`` (another spread holds part of it too), ``leg_lots`` (the whole leg's
    open lots) and quantity, value_local, value_usd, roll_down_usd_per_month, pnl_usd {daily, ltd},
    pnl_open and pnl_locked times the share; fill, mark and reasons unchanged. Each portion but the
    last rounded to the cent and the last the rest, so a leg's portions add up to the leg exactly
    (pnl_locked of a portion = its ltd - its open, the last the leg's rest). A split for display:
    no trade's, leg's or ``value_book`` figure changes."""
    by_cid = {r["contract_id"]: r for r in rows}
    holders: Dict[str, List[Tuple[int, int, float]]] = defaultdict(list)
    for i, sub in enumerate(subs):
        for j, leg in enumerate(sub.get("legs") or []):
            if leg.get("contract_id") in by_cid:
                holders[leg["contract_id"]].append((i, j, abs(float(_num(leg.get("lots")) or 0.0))))
    new_legs = [list(sub.get("legs") or []) for sub in subs]
    for cid, held in holders.items():
        row = by_cid[cid]
        tot = sum(w for _i, _j, w in held)
        weights = [w / tot for _i, _j, w in held] if tot > _EPS else [1.0 / len(held)] * len(held)
        figs = {
            "daily": (row.get("pnl_usd") or {}).get("daily"), "ltd": (row.get("pnl_usd") or {}).get("ltd"),
            "open": row.get("pnl_open"), "locked": row.get("pnl_locked"),
            "quantity": _num(row.get("quantity")), "value_local": _num(row.get("value_local")),
            "value_usd": _num(row.get("value_usd")),
            "roll_down_usd_per_month": _num(row.get("roll_down_usd_per_month")),
        }
        cut = {k: _shares(v, weights, cents=k not in ("quantity",)) for k, v in figs.items()}
        if figs["ltd"] is not None and figs["open"] is not None and figs["locked"] is not None:
            # locked in = the portion's ltd - its open, the last the leg's rest (open + locked = ltd)
            lk = [cut["ltd"][n] - cut["open"][n] for n in range(len(held) - 1)]
            cut["locked"] = lk + [float(figs["locked"]) - sum(lk)]
        for n, (i, j, _w) in enumerate(held):
            leg = new_legs[i][j]
            portion = {**row, **leg}
            portion.update(share=weights[n], shared=len(held) > 1, leg_lots=_num(row.get("lots")),
                           quantity=cut["quantity"][n], value_local=cut["value_local"][n],
                           value_usd=cut["value_usd"][n], roll_down_usd_per_month=cut["roll_down_usd_per_month"][n],
                           pnl_usd={**(row.get("pnl_usd") or {}), "daily": cut["daily"][n], "ltd": cut["ltd"][n]},
                           pnl_reasons=dict(row.get("pnl_reasons") or {}),
                           pnl_open=cut["open"][n], pnl_locked=cut["locked"][n])
            new_legs[i][j] = portion
    return [{**sub, "legs": legs} for sub, legs in zip(subs, new_legs)]


def _level_meta(book, level: dict, legs: Dict[str, float], rows_by_cid: Dict[str, dict]) -> dict:
    """``level`` with the spread it is read on (2026-10-01, so each spread's level stands alone):
    ``legs`` [{contract_id, instrument_id, root_id, name, month, prompt, lots}] and ``entry_date``
    (the first fill of its legs' open lots)."""
    rows = [(c, v, rows_by_cid.get(c, {})) for c, v in legs.items()]
    rows.sort(key=lambda x: (x[2].get("month", ""), x[2].get("prompt", ""), x[0]))
    days = [str(book.by_id[t]["trade_date"])[:10] for _c, _v, r in rows for t in r.get("open_trade_ids") or []
            if t in book.by_id]
    return {**level, "legs": [{"contract_id": c, "instrument_id": r.get("instrument_id", ""),
                               "root_id": r.get("root_id", ""), "name": r.get("name", c), "month": r.get("month", ""),
                               "prompt": r.get("prompt", ""), "lots": float(v)} for c, v, r in rows],
            "entry_date": min(days) if days else ""}


def _part_row(book, part: _Part, rows_by_cid: Dict[str, dict], level: dict,
              pleg_by_cid: Optional[Dict[str, st.PLeg]] = None, one: Optional[dict] = None) -> dict:
    what = _part_what(book, part, rows_by_cid)
    legs = sorted(part.legs.items(), key=lambda kv: (rows_by_cid.get(kv[0], {}).get("month", ""), kv[0]))
    names = {c: r["name"] for c, r in rows_by_cid.items()}
    items = [(pleg_by_cid[c], v) for c, v in part.legs.items() if c in (pleg_by_cid or {})]
    one_here = one if one is not None and set(part.roots) <= {r for s in one.get("sides") or []
                                                             for r in s.get("root_ids") or []} else None
    return {"type": part.kind, "what_it_is": what, "root_ids": list(part.roots), "closed_root_ids": list(part.closed_roots),
            "legs": [{"contract_id": c, "name": rows_by_cid.get(c, {}).get("name", c), "lots": v} for c, v in legs],
            "lots": max((abs(v) for v in part.legs.values()), default=0.0) if part.kind == TYPE_CALENDAR else None,
            "level": _level_meta(book, level, part.legs, rows_by_cid), "note": part.note,
            "unmatched": part.kind == TYPE_UNMATCHED,
            "size_sides": _size_sides(book, items, names, one_here)}


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
    one = next((p for p in entry.get("pairs") or [] if p.get("rule") == st.RULE_ONE_SPREAD), None)
    one_roots = {r for s in (one or {}).get("sides") or [] for r in s.get("root_ids") or []}
    parts = _split_parts(book, parts, pleg_by_cid, one_roots, {c: r["name"] for c, r in rows_by_cid.items()})
    kinds = [p.of_kind for p in parts]
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
    n_cross = sum(1 for p in parts if p.kind in (TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT))
    for part in parts:
        if part.kind == TYPE_UNMATCHED:
            part_levels.append(_blank_level(part.note))
        elif part.kind == TYPE_CALENDAR:
            part_levels.append(_calendar_level(book, part, pleg_by_cid, prev_day, prev_rows))
        elif part.kind in (TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT) and part.split and len(part.legs) == 2:
            # a spread cut out of a bigger part (equal size): its own level, by the pairs' one level rule
            part_levels.append(_month_pair_level(book, part, pleg_by_cid, prev_day, prev_rows))
        elif part.kind == TYPE_CROSS_EXCHANGE and n_cross > 1 and len(part.legs) == 2:
            # one of several month pairs: its own level, by the pairs' one level rule
            part_levels.append(_month_pair_level(book, part, pleg_by_cid, prev_day, prev_rows))
        elif part.kind in (TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT):
            fit = [p for p in cross_pairs if {x["root_id"] for x in p["legs"]} <= set(part.roots)]
            covered = _template_level(book, part, pleg_by_cid, prev_day, prev_rows) if len(part.legs) >= 3 else None
            if covered is not None:
                part_levels.append(covered)
            elif len(fit) == 1:
                part_levels.append(_pair_level(book, fit[0]))
            elif not fit and part.closed_roots:
                part_levels.append(_open_side_level(book, part, rows, pleg_by_cid, prev_day, prev_rows))
            elif not fit:
                part_levels.append(_blank_level(part.note or "no open pair of legs to read a level from"))
            else:
                part_levels.append(_blank_level(f"{len(fit)} pairs in this part: each has its own level"))
        else:
            part_levels.append(_blank_level("an outright has no spread level"))
    subs = [_part_row(book, p, rows_by_cid, lv, pleg_by_cid, one) for p, lv in zip(parts, part_levels)]
    real = [n for n, p in enumerate(parts) if p.kind != TYPE_UNMATCHED]
    cross_idx = [n for n in real if parts[n].kind in (TYPE_CROSS_EXCHANGE, TYPE_CROSS_PRODUCT)]
    cal_idx = [n for n in real if parts[n].kind == TYPE_CALENDAR]
    src: Optional[int] = None            # the part the trade's level is read from
    if len(parts) == 1 and parts[0].kind == TYPE_OUTRIGHT:
        level = _price_level(rows, part_levels[0])
        src = None if level is not part_levels[0] else 0
    elif not parts:
        blank = _open_blank(book, rows)
        level = _price_level(rows, blank)
        if level is blank:
            level = _premium_level(book, rows) or blank
    elif not real:
        level = _blank_level(parts[0].note)
    elif len(real) < len(parts):
        # unmatched legs beside the spreads: no one spread speaks for the trade (never guessed)
        level = _blank_level(f"{name} holds {len(real)} spread{'s' if len(real) > 1 else ''} and legs no rule "
                             f"pairs: each spread's level, and why the rest have none, is under sub_spreads")
    elif len(real) == 1:
        src = real[0]
        level = part_levels[src]
    elif len(cross_idx) == 1:
        src = cross_idx[0]
        level = part_levels[src]
    elif not cross_idx and len(cal_idx) == 1:
        src = cal_idx[0]
        level = part_levels[src]
    elif len({parts[n].kind for n in real}) == 1 and len({tuple(sorted(parts[n].roots)) for n in real}) == 1:
        # spreads of one kind on the same contracts (ZNA1's month pairs, STEEL's two HRC calendars):
        # the largest spread's level
        src = max(real, key=lambda n: (max(abs(v) for v in parts[n].legs.values()), -n))
        many = "month pairs" if parts[src].kind != TYPE_CALENDAR else "calendars"
        level = {**part_levels[src], "note": _join([part_levels[src].get("note", ""),
                                                    f"the largest of {len(real)} {many}"])}
    else:
        level = _blank_level(f"{name} holds {len(real)} spreads ({', '.join(_WORDS[parts[n].kind] for n in real)}): "
                             f"each has its level under sub_spreads")
    if src is not None:
        level = _level_meta(book, level, parts[src].legs, rows_by_cid)
    else:
        live = [r for r in rows if r["status"] == "open" and not r["unrecognised"]]
        core = [r for r in live if not r["hedge"]] or live
        level = _level_meta(book, level, {r["contract_id"]: float(r["lots"]) for r in core}, rows_by_cid)
    names = {c: r["name"] for c, r in rows_by_cid.items()}
    size_sides = _size_sides(book, [(leg, leg.lots) for leg in legs], names, one)
    if not any(abs(leg.lots) > LEFTOVER_FLOOR for leg in legs):
        # no open futures or LME leg: its FX, FX option or option-on-future legs in their own unit
        size_sides = _row_sides(book, rows)
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
        flags.append({"code": FLAG_UNBALANCED, "label": "Unbalanced", "severity": "amber",
                      "sentence": (f"The sides differ by {size['value_gap']:.0%} of their value at the fill "
                                   f"({a['label']} {abs(a['value_fill_usd']):,.0f} USD against {b['label']} "
                                   f"{abs(b['value_fill_usd']):,.0f} USD); balanced is within {UNBALANCED:.0%}")})
    if hedge.get("hedge_oversized"):
        flags.append({"code": FLAG_HEDGE_OVERSIZED, "label": "Hedge oversized", "severity": "amber",
                      "sentence": _cap(hedge["hedge_oversized"])})
    if mismatch:
        flags.append({"code": FLAG_TYPE_MISMATCH, "label": "Type mismatch", "severity": "amber", "sentence": mismatch})
    for r in unknown:
        flags.append({"code": FLAG_UNRECOGNISED, "label": "Contract not recognised", "severity": "red",
                      "sentence": f"Contract not recognised: {r['name']}: P&L can't be computed until it is mapped"})
    no_price = [r for r in rows if r["status"] == "open" and r["mark"] is None]
    if no_price:
        flags.append({"code": FLAG_NO_PRICE, "severity": "amber",
                      "label": f"{len(no_price)} leg{'s' if len(no_price) > 1 else ''} without price",
                      "sentence": "; ".join(f"{r['name']}: {r['mark_reason']}" for r in no_price)})
    if rows and all(r["hedge"] or r["unrecognised"] for r in rows) and any(r["hedge"] for r in rows):
        # nothing but currency trades: they are the trade's position, not a hedge of it
        for r in rows:
            if r["hedge"]:
                r.update(hedge=False, roll_down_reason="a currency trade: no roll-down")
        if nxt is not None:
            nxt = {**nxt, "hedge": False}
    subs = _leg_portions(subs, rows)
    pnl = entry.get("pnl_usd") or {}
    pnl_open, pnl_locked, pnl_split_reason = _split_trade(rows, pnl.get("ltd"),
                                                          (entry.get("pnl_reasons") or {}).get("ltd"))
    return {
        "trade": name, "trade_ids": sorted(tids), "position_id": f"POSITION-{entry['spread_id']}",
        "status": "open" if any(r["status"] in ("open", UNRECOGNISED.lower()) for r in rows) else "closed",
        "first_trade_date": min(str(book.by_id[t]["trade_date"]) for t in tids),
        "pb_roots": list(entry.get("pb_roots") or []), "type_labels": list(entry.get("type_labels") or []),
        "type": kind, "type_note": type_note, "type_mismatch": mismatch, "sub_spreads": subs,
        "commodity_family": families[0] if len(families) == 1 else (FAMILY_CROSS if families else ""),
        "what_it_is": _what_it_is(book, kind, parts, rows, hedge),
        "legs": rows, "size": size, "size_sides": size_sides, "level": level,
        "carry_per_month": carry, "carry_reason": carry_why, "carry_history_date": carry_date,
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
        "pnl_open": pnl_open, "pnl_locked": pnl_locked, "pnl_split_reason": pnl_split_reason,
        "unwound": any(r.get("unwound") for r in rows),
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
           "exit_date": "", "exit_basis": "", "reason": "", "type": "", "spec": None, "sub_spreads": []}
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
    # the level formula and the spreads as they stood on open_date (risk-metrics reads them for the
    # exit z without valuing the book again on that day, 2026-10-01); each spread's exit is the close
    subs = [{**sub, "exit_date": close} for sub in was["sub_spreads"]]
    out.update(unit=level.get("unit", ""), mode=level.get("mode", ""), type=was["type"],
               entry=level.get("entry"), entry_reason=level.get("entry_reason", ""),
               spec=level.get("spec"), sub_spreads=subs)
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
    if level.get("spec") is None and not level.get("price_legs"):
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
               value_fn: Optional[ValueFn] = value_book, history=None, rows=None) -> dict:
    """Every trade of the book on ``as_of`` (the module docstring), for the Book row and its panel.

    ``spreads``: the ``book_spreads(conn, as_of, value_fn=...)`` result the caller already holds
    (built here with ``value_fn`` when None); pass the SAME ``value_fn`` it was built with (a
    screen: its memoised filled reader), so the legs read the rows the trade's P&L was summed
    from. ``history``: a ``commodity_history.CommodityHistory`` for the roll-down (the book
    database's own ``price_history``, loaded here from ``conn`` when None; context only). Deterministic: the same database and arguments give the same
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
      ``sub_spreads`` ([{type, what_it_is, root_ids, closed_root_ids, legs, lots (a calendar's
      size), level (as ``level``), note, unmatched (bool), size_sides (as below)}], one per spread:
      a part of three or more legs is cut into its equal-size spreads by ``_split_parts`` (user,
      2026-10-01), each with its own level; legs no rule pairs are one sub of type ``UNMATCHED``
      with a blank level and its reason, never in the trade's type). Each sub's ``legs`` are its
      own portions of the trade's leg rows (``_leg_portions``, 2026-10-01): the leg row's keys
      with ``lots`` the lots this spread holds, ``share``, ``shared``, ``leg_lots``, and quantity,
      value_local, value_usd, roll_down_usd_per_month, pnl_usd {daily, ltd}, pnl_open, pnl_locked
      times the share, so each spread is the sum of its legs and a leg's portions add up to it.
    - ``size_sides`` (trade and each sub, 2026-10-01): {basis ('lots' | 'physical' | 'value' |
      'notional'), unit ('lots', 't', 'bbl', 'oz' ..., 'USD'; a currency for 'notional'), long,
      short (sizes >= 0; None with ``reason`` when the value at the fill is not known), long_label,
      short_label (the legs' names, '+'-joined; a one-spread trade its commodities), text ('2,521 v
      2,521 lots', '3,135 t v 3,135 t', '$15,094,650 v $14,928,200', 'Short 2 lots', 'Short 100
      oz', 'Long 10,000,000 EUR'), reason}: one root in lots (an LME ticket in tonnes), one
      commodity across exchanges or a crack in its shared physical unit, two commodities in USD at
      the fill. Lots of two contracts are never added. A trade with no open futures or LME leg
      (``_row_sides``): an FX spot / forward its base amount and an FX option its notional
      (basis 'notional', unit the base currency, 'oz' for a precious metal), an option on a future
      its lots; 'nothing open' only when nothing is.
    - ``commodity_family`` ('copper', 'ferrous', 'cattle' ...; 'cross-product' when two families
      mix), ``what_it_is`` ('COMEX vs LME copper, Nov/Dec26').
    - ``legs``: one per contract, open first, hedges last: {contract_id, instrument_id, root_id,
      product, hedge, name ('COMEX Silver Dec26'), exchange, commodity, month, prompt,
      broker_symbols, side, lots (open, signed; an LME ticket in lots of its contract size),
      quantity (open, in ``trades.quantity`` units), status ('open' | 'flat' | 'closed'),
      currency, trade_ids, open_trade_ids, avg_fill / avg_fill_reason (the open lots' average
      entry, quoted), mark / mark_source / mark_reason (the as-of ``value_book`` row), value_usd /
      value_reason (open lots x multiplier x mark x spot; a hedge's USD notional), pnl_usd {daily,
      ltd} / pnl_reasons, roll_down (quote unit per month, Bloomberg history), roll_down_unit,
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
      | 'difference' | 'price' (one contract) | 'premium' (options only: the net premium per unit
      of the largest leg; unit the root's quote unit, '' for FX options, a fraction of the base
      notional)), china_leg (the ratio's numerator leg), note ('legs closed ~9h apart ...', a
      template's name and its ratio, the premium's basis), source ('pair' | 'calendar' |
      'template' | 'price' | 'premium' | ''), template (the ``config/spreads/`` id of a template
      level, else ''), price_legs ([{instrument_id, trade_ids, weight, price_scale}] of a
      'price' / 'premium' level, the legs ``level_history`` reads; [] otherwise),
      entry_estimated / prev_estimated / now_estimated (bool: the figure rests on a near-marks
      estimate of hard rule 2, a spot or mark whose source starts ``INTERP:``) with
      entry_estimate_note / prev_estimate_note / now_estimate_note (the sentence naming each
      estimate, '' when exact) and estimate_note (them joined), reason, legs ([{contract_id,
      instrument_id, root_id, name, month, prompt, lots}]: the spread the level is read on),
      entry_date (the first fill of those legs' open lots; 2026-10-01: each level stands alone)}.
    - ``carry_per_month`` (USD, Bloomberg history: the legs' roll-downs summed only when every
      non-hedge open leg is on one curve) / ``carry_reason`` / ``carry_history_date``.
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
    - ``pnl_open`` / ``pnl_locked`` / ``pnl_split_reason`` (2026-09-30, user yes under hard rule
      7): the LTD split into the P&L on the lots still held and the P&L locked in from lots taken
      off (``_split_leg``, ``_split_trade``): open + locked = ``pnl['ltd']`` exactly; both None
      with the reason when a leg cannot be split; a closed trade is all locked in. ``unwound``
      (bool): some lots were taken off (a reduction, a roll, a close, an expiry). Each leg carries
      the same four keys (``pnl_open`` + ``pnl_locked`` = its ``pnl_usd['ltd']``).
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
      exit_basis (the level on the close date's marks, else on ``open_date``), reason, spec (the
      level's ``spec_to_dict`` as it stood on ``open_date``; None for a trade of several spreads,
      the reason in ``reason``), sub_spreads (the spreads as they stood on ``open_date``, the open
      trade's shape, each with ``exit_date`` = close_date; 2026-10-01)}.

    Legs also carry (2026-09-29) ``mark_as_of`` (the close the mark is from: the as-of, or the
    earlier close the filled reader carried) / ``mark_snapped_at`` (the official mark's stamp, ''
    for an estimate or a frozen row), ``prev_mark`` / ``prev_mark_date`` / ``prev_mark_source`` /
    ``prev_mark_reason`` (the Daily's reference close) and ``value_local`` / ``value_local_reason``
    (open quantity x multiplier x mark, in ``currency``); two roots of one commodity on one exchange
    are named by product ('ICE TTF gas Nov26', 'ICE NBP gas Nov26').

    Every figure that cannot be given is None with its reason, never 0 (hard rule 2)."""
    from engine.spreads.book import _Book, book_spreads
    value_fn = value_fn if value_fn is not None else value_book
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
            history = load_commodity_history(conn)
        except Exception as exc:  # noqa: BLE001 -- context: a reason, never a failure
            notes.append(f"the price history could not be read ({type(exc).__name__}: {exc}): no roll-down")
    if history is not None and not getattr(history, "available", False):
        notes.append(f"no roll-down: {getattr(history, 'reason', '') or 'no price history on file'}")
    entries = {str(s.get("name") or ""): s for s in spreads.get("strategies") or []}
    symbols = _broker_symbols(conn, [t for s in entries.values() for t in s.get("trade_ids") or []])
    memo: dict = {}          # the history lookups of the roll-downs, shared by every trade
    if history is not None and getattr(history, "available", False):
        # every root a roll-down will read, in one history query (served from memory after)
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


def _price_legs_on(legs: List[dict], rows: Dict[str, dict], day: str) -> Tuple[Optional[float], str, str, str]:
    """(level, why when None, source, estimate note) of a spec-less level on ``day``: sum weight x
    the leg's marked row's ``mark`` x price_scale over ``legs`` (``_price_level`` /
    ``_premium_level``'s ``price_legs``)."""
    total, srcs, est = 0.0, [], []
    for leg in legs:
        hit = next((rows[t] for t in leg["trade_ids"] if t in rows and _num(rows[t].get("mark")) is not None), None)
        if hit is None:
            why = next((str(rows[t].get("reason") or "") for t in leg["trade_ids"] if t in rows), "")
            return None, f"{leg['instrument_id']} has no price on {day}" + (f" ({why})" if why else
                                                                          " (not held or not valued then)"), "", ""
        total += float(leg["weight"]) * float(hit["mark"]) * float(leg.get("price_scale") or 1.0)
        srcs.append(f"{leg['instrument_id']} {hit.get('mark_source') or 'value_book'}")
        if is_estimate(hit.get("mark_source")):
            est.append(f"{leg['instrument_id']} price of {day} estimated ({hit.get('mark_source')})")
    return total, "", "; ".join(srcs), "; ".join(est)


def level_history(conn: sqlite3.Connection, trade: dict, dates: Iterable[str], value_fn: ValueFn = value_book) -> dict:
    """A trade's level and LTD on each of ``dates``, with its rolls marked (the panel's chart).

    ``trade``: an item of ``trade_book(...)["trades"]``. The level is the trade's ``level``
    formula (its ``spec``: the same legs every day, so the chart is the spread held now) read
    from each day's ``value_fn`` rows: each leg's mark and, across currencies, its row's spot; a
    leg not yet held that day reads that day's official FUTURE_PX and SPOT (``book.level_on``'s
    fallback); a ratio China over foreign, converted. The LTD is ``history.position_history``'s
    (the trade's trades' ``value_book`` rows summed, a date with one unpriced left out with its
    reason). A level with no spec (one contract's price, an options trade's net premium) reads its
    ``price_legs`` off each day's rows: sum weight x mark x price_scale, a leg with no marked row
    that day leaving the point blank with its reason. Returns ``{trade, position_id, level_unit,
    level_mode, level_entry, first_trade_date, dates_left_out, points [{date, ltd_usd, ltd_reasons,
    ltd_filled, ltd_notes, level, level_reason, level_source, level_estimated ('' or the sentence
    naming each near-marks estimate the point rests on), rolls [labels]}], rolls}``. ``value_fn``: a screen passes its
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

    price_legs = list(level.get("price_legs") or [])
    points = []
    for p in base["points"]:
        day = p["date"]
        point = {k: p[k] for k in ("date", "ltd_usd", "ltd_reasons", "ltd_filled", "ltd_notes")}
        point.update(level=None, level_reason=level.get("reason") or level.get("now_reason") or "no level",
                     level_source="", level_estimated="", rolls=list(roll_on.get(day, [])))
        if spec is not None or price_legs:
            out = read(conn, day)
            frame = out[0] if isinstance(out, tuple) else out
            rows = ({r["trade_id"]: r for r in frame.to_dict("records")
                     if r["trade_id"] in info and info[r["trade_id"]][0] <= day}
                    if isinstance(frame, pd.DataFrame) and not frame.empty else {})
            if spec is None:
                lvl, why, src, est = _price_legs_on(price_legs, rows, day)
                point.update(level=lvl, level_reason=why, level_source=src, level_estimated=est)
            elif mode == "ratio" and cn in (0, 1):
                vc, why_c, src, _, est_c = level_on(conn, st.one_leg_spec(spec, cn), day, rows, True, expiry)
                vf, why_f, _src, _, est_f = level_on(conn, st.one_leg_spec(spec, 1 - cn), day, rows, True, expiry)
                if vc is None or vf is None or abs(vf) < 1e-12:
                    point["level_reason"] = why_c or why_f or "the foreign leg's price is 0"
                else:
                    point.update(level=vc / vf, level_reason="", level_source=src,
                                 level_estimated="; ".join(dict.fromkeys(list(est_c) + list(est_f))))
            else:
                lvl, why, src, _, est = level_on(conn, spec, day, rows, True, expiry)
                point.update(level=lvl, level_reason=why, level_source=src,
                             level_estimated="; ".join(est) if lvl is not None else "")
        points.append(point)
    return {"trade": trade.get("trade", ""), "position_id": trade.get("position_id", ""),
            "level_unit": level.get("unit", ""), "level_mode": mode, "level_entry": level.get("entry"),
            "first_trade_date": base["first_trade_date"], "dates_left_out": base["dates_left_out"],
            "points": points, "rolls": list(trade.get("rolls") or [])}


__all__ = ["FAMILY_CROSS", "FLAG_HEDGE_OVERSIZED", "FLAG_NO_PRICE", "FLAG_TYPE_MISMATCH", "FLAG_UNBALANCED", "FLAG_UNRECOGNISED",
           "TRADE_TYPES", "TYPE_CALENDAR", "TYPE_CROSS_EXCHANGE", "TYPE_CROSS_PRODUCT", "TYPE_MIXED", "TYPE_NONE",
           "TYPE_OUTRIGHT", "commodity_words", "family_of", "leg_name", "level_history", "month_label", "trade_book"]
