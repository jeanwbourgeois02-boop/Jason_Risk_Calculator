"""The spreads Jason put on inside one trade, found from the fills (user, 2026-10-02).

A trade (the PBRoot name: STEEL, ZNA1, SCO1 ...) can hold several spreads. Each one is a
**structure**: a set of the trade's fills, every fill in exactly one structure (or listed as
outside every structure, with why), and a structure is never split across trades. Nothing is
typed by hand: the structures come from the fills by the rule below, and ``spread_overrides``
(``overrides.py``: PIN, SPLIT) is the only correction. Grouping and display only: a structure's
P&L is its fills' ``value_book`` rows summed, so the trade's, a contract's and the book's LTD
never move; what moves is the split of LTD into open and locked in (user yes under hard rule 7,
2026-10-02), which is per (structure, contract) by ``avg_cost``.

**The rule**, in this order, on the trade's futures and LME fills (hedges apart, ``hedges.is_hedge``):

0. Overrides. Fills pinned to one name (PIN) form one structure; a fill split out (SPLIT) is never
   grouped (it is one of the trade's unmatched legs).
1. A trade over the two commodities of one value-sized spread (``strategies``' one_spread rule,
   user 2026-09-29: CATTLE) is one structure: no calendar is cut out of it.
2. Calendars put on as one: on one trade date, two months of one root traded opposite ways with
   lots equal within 5 % (``grouping.TOLERANCE``), fill against fill (the closest sizes first, then
   the nearest months), then the day's rest pooled per contract. A ticket that moves an outright
   position the root already held (``rolls.py``'s roll, the root not flat before that day) is a
   roll, never a calendar. Tickets on the same two contracts are one structure, whatever the day
   or the direction (STEEL: Oct/Nov 150 on 18 Sep and 25 on 22 Sep are one Oct/Nov 175).
3. Boxes: two of those calendars on one commodity (contract-master's ``subsector``), on two
   exchanges, over the same two months, opposite ways, their tonnage within 10 % (ZNA1: SHFE zinc
   short Oct / long Nov 627 lots = 3,135 t against LME zinc long Oct / short Nov 126 lots =
   3,150 t, dealt on different days). The box carries its two same-month arbs as components.
4. Every other fill is pooled per contract across days (tickets over several days into the same
   contracts are one structure) and read by the open-lots rule of ``trades``: same-month pairs of
   one commodity on two exchanges by tonnage, calendars whose months net to zero, the cross part,
   cut into equal-size spreads (``trades._decompose``, ``trades._split_parts``). A contract one
   such spread holds takes all its pooled fills; a contract two of them share (the one-leg-left
   rule) is cut between them by lots, every figure of it by the same share. A contract no spread
   holds joins the one structure that already holds it (a later fill on one leg of a calendar), a
   contract now flat joins the spread whose other side it was (SILARB1's COMEX silver), else it is
   one of the trade's **unmatched** legs (COPAR3's HG Nov 3 lots): listed, never dropped, never
   guessed into a spread.
5. Hedges (a USD/CNH future, an FX product on a currency pair) go to the structure whose China legs
   they cover: a hedge calendar dealt as one (two months, opposite, equal lots) to the structure
   with China legs in both months the opposite way (a long China leg is hedged by a short USD/CNH);
   any other hedge contract to the one structure with a China leg in its month (the one not yet
   hedged in that month when two are); else to the trade's one structure. A hedge no rule places
   stays a hedge of the trade, outside every structure, with why.

**Average cost** (``avg_cost``, the spec's 7.1): the fills in trade date then trade id order; a fill
with the position (or from flat) averages in, a fill against it closes min(|q|, |pos|) at the
average (the locked-in part), a flip opens the rest at its price, flat resets.

**The quote** (``structure_quote``, display only, never a P&L figure): each leg's price in USD per
the structure's unit (``ratio.ratio_spec``: one commodity in one physical unit, China on top), a
China leg converted at its linked USD/CNH hedge's average fill (entry) and mark (now), else at the
spot of the leg's first fill and the valuation's spot ("FX unhedged"); the level = sum of weight x
converted price (a calendar near +1 / far -1, a cross pair China or the first leg +1, a box the far
arb minus the near arb), its size the first leg's position in the unit, each leg's balance against
that size and each China leg's hedge cover.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from data.contracts import quantity_factor
from engine.spreads import strategies as st
from engine.spreads.book import is_estimate, spot_near
from engine.spreads.grouping import LEFTOVER_FLOOR, TOLERANCE, Leg
from engine.spreads.hedges import FX_SECTOR, is_hedge
from engine.spreads.overrides import PIN, SPLIT
from engine.spreads.ratio import RATIO_PRODUCTS, ratio_spec, spread_ratio

BALANCE_TOLERANCE = st.VALUE_TOLERANCE      # a leg, a box's two sides or a hedge's cover beyond 10 % of size
TERM_STRUCTURE = "TERM_STRUCTURE"
CROSS_EXCHANGE = "CROSS_EXCHANGE"
CROSS_PRODUCT = "CROSS_PRODUCT"
BOX = "BOX"
OUTRIGHT = "OUTRIGHT"
UNMATCHED = "UNMATCHED"
STRUCTURE_WORDS = {TERM_STRUCTURE: "Term structure", CROSS_EXCHANGE: "Cross-exchange",
                   CROSS_PRODUCT: "Cross-product", BOX: "Box", OUTRIGHT: "Outright", UNMATCHED: "Unmatched legs"}
_KIND_TO_STRUCTURE = {"CALENDAR": TERM_STRUCTURE, CROSS_EXCHANGE: CROSS_EXCHANGE, CROSS_PRODUCT: CROSS_PRODUCT,
                      BOX: BOX, OUTRIGHT: OUTRIGHT, UNMATCHED: UNMATCHED}
ORIGIN_TICKET = "ticket"       # calendars put on as one (rule 2)
ORIGIN_BOX = "box"             # two calendar tickets forming a box (rule 3)
ORIGIN_POOLED = "pooled"       # the open-lots rule on the pooled fills (rule 4)
ORIGIN_PINNED = "pinned"       # spread_overrides PIN
ORIGIN_LEFTOVER = "leftover"   # the trade's unmatched legs
_EPS = 1e-9


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _sign(x: float) -> float:
    return 1.0 if x > 0 else -1.0


def _join(parts) -> str:
    return "; ".join(dict.fromkeys(p for p in parts if p))


def structure_type(kind: str) -> str:
    """The structure's type code from the part kind ('CALENDAR' -> 'TERM_STRUCTURE')."""
    return _KIND_TO_STRUCTURE.get(kind, kind)


# ------------------------------------------------------------------ average cost (7.1)
def fill_key(book, tid: str) -> tuple:
    """A fill's place in time: its trade date, then its trade id (numeric ids in number order)."""
    t = book.by_id[tid]
    return (str(t["trade_date"])[:10], (0, int(tid), "") if str(tid).isdigit() else (1, 0, str(tid)))


def avg_cost(book, tids: Sequence[str]) -> Tuple[Optional[float], float, float, str]:
    """(average cost of the open position in the fills' quoted price, the open position in
    ``trades.quantity`` units, the locked-in amount in price x quantity units (x multiplier for
    money), why when the average is None).

    The fills in trade date then trade id order (the spec's 7.1): a fill with the position, or
    from flat, averages in, avg = (avg x |pos| + price x |q|) / (|pos| + |q|); a fill against it
    closes n = min(|q|, |pos|), locking in n x sign(pos) x (price - avg) and leaving the average;
    a flip opens the rest at the fill's price; flat resets the average."""
    pos = avg = locked = 0.0
    for tid in sorted(tids, key=lambda t: fill_key(book, t)):
        q, f = book.lots(tid), _num(book.by_id[tid]["price"])
        if q is None or f is None:
            return None, 0.0, 0.0, f"{tid}: its quantity or fill is not a number, so its average cost is not known"
        if abs(q) < _EPS:
            continue
        if abs(pos) < _EPS or _sign(q) == _sign(pos):
            avg = (avg * abs(pos) + f * abs(q)) / (abs(pos) + abs(q))
            pos += q
            continue
        n = min(abs(q), abs(pos))
        locked += n * _sign(pos) * (f - avg)
        if abs(q) > abs(pos) + _EPS * max(1.0, abs(q)):
            pos, avg = pos + q, f
        else:
            pos += q
            if abs(pos) < _EPS * max(1.0, abs(q)):
                pos = avg = 0.0
    if abs(pos) < _EPS:
        return None, 0.0, locked, "nothing open"
    return avg, pos, locked, ""


# ------------------------------------------------------------------ the finder
class _Fill:
    __slots__ = ("tid", "cid", "root_id", "month", "lots", "day")

    def __init__(self, tid, cid, root_id, month, lots, day):
        self.tid, self.cid, self.root_id, self.month, self.lots, self.day = tid, cid, root_id, month, lots, day


class Structure:
    """One structure while it is found: ``fills`` {contract id: [trade ids]} (a contract shared
    with another structure carries the same fills and its ``share`` of them), ``hedge_fills`` the
    same for its hedges."""

    def __init__(self, origin: str, kind: str = "", note: str = ""):
        self.origin, self.kind, self.note = origin, kind, note
        self.fills: Dict[str, List[str]] = defaultdict(list)
        self.share: Dict[str, float] = {}
        self.hedge_fills: Dict[str, List[str]] = defaultdict(list)
        self.closed_roots: List[str] = []
        self.of_kind = kind
        self.split = False
        self.components: List[dict] = []
        self.name = ""            # a pinned structure's group name

    def ids(self) -> List[str]:
        return sorted({t for v in self.fills.values() for t in v} | {t for v in self.hedge_fills.values() for t in v})


def _month_index(key: str) -> int:
    try:
        return int(key[:4]) * 12 + int(key[5:7])
    except (TypeError, ValueError):
        return 0


def _open_lots(book, info: Dict[str, _Fill], ids: Sequence[str]) -> float:
    return float(sum(info[t].lots for t in ids if t in info and book.is_open(t)))


def _physical_per_lot(root, unit: str) -> Optional[float]:
    try:
        return float(root.contract_size) * quantity_factor(root.size_unit.strip().lower(), unit)
    except ValueError:
        return None


def _shared_unit(a, b) -> str:
    for unit in list(st.COMMON_UNITS) + [a.size_unit.strip().lower()]:
        if _physical_per_lot(a, unit) is not None and _physical_per_lot(b, unit) is not None:
            return unit
    return ""


def _tickets(book, info: Dict[str, _Fill], pool: Sequence[str], roll_ids: set) -> List[Tuple[str, Dict[str, List[str]]]]:
    """Rule 2: (root, {contract: fills}) of every calendar put on as one, per trade date and root."""
    by_dr: Dict[Tuple[str, str], List[_Fill]] = defaultdict(list)
    for tid in pool:
        f = info[tid]
        by_dr[(f.day, f.root_id)].append(f)
    net_before: Dict[Tuple[str, str], float] = {}
    for (day, root) in by_dr:
        net_before[(day, root)] = float(sum(f.lots for f in info.values() if f.root_id == root and f.day < day))
    out: List[Tuple[str, Dict[str, List[str]]]] = []
    for (day, root) in sorted(by_dr):
        items = sorted(by_dr[(day, root)], key=lambda f: fill_key(book, f.tid))
        if len({f.month for f in items}) < 2:
            continue
        rolled = abs(net_before[(day, root)]) > LEFTOVER_FLOOR

        def is_roll(ids) -> bool:
            # a roll moves an outright the root already held; a calendar beside a calendar is a new one
            return rolled and any(i in roll_ids for i in ids)

        def pairs(cands) -> List[tuple]:
            cands.sort(key=lambda c: c[:4])
            used, got = set(), []
            for c in cands:
                if set(c[4]) & used or set(c[5]) & used:
                    continue
                used |= set(c[4]) | set(c[5])
                got.append(c)
            return got

        cands = []
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if a.cid == b.cid or a.month == b.month or _sign(a.lots) == _sign(b.lots):
                    continue
                dev = abs(abs(a.lots) - abs(b.lots)) / max(abs(a.lots), abs(b.lots))
                if dev <= TOLERANCE + 1e-12 and not is_roll([a.tid, b.tid]):
                    cands.append((round(dev, 12), abs(_month_index(a.month) - _month_index(b.month)),
                                  fill_key(book, a.tid), fill_key(book, b.tid), (a.tid,), (b.tid,), a.cid, b.cid))
        taken = set()
        for c in pairs(cands):
            taken |= set(c[4]) | set(c[5])
            out.append((root, {c[6]: list(c[4]), c[7]: list(c[5])}))
        rest: Dict[str, List[str]] = defaultdict(list)
        for f in items:
            if f.tid not in taken:
                rest[f.cid].append(f.tid)
        pooled = {cid: float(sum(info[t].lots for t in ids)) for cid, ids in rest.items()}
        cids = sorted(c for c in pooled if abs(pooled[c]) > LEFTOVER_FLOOR)
        cands = []
        for i, ca in enumerate(cids):
            for cb in cids[i + 1:]:
                la, lb = pooled[ca], pooled[cb]
                ma, mb = info[rest[ca][0]].month, info[rest[cb][0]].month
                if ma == mb or _sign(la) == _sign(lb):
                    continue
                dev = abs(abs(la) - abs(lb)) / max(abs(la), abs(lb))
                if dev <= TOLERANCE + 1e-12 and not is_roll(rest[ca] + rest[cb]):
                    cands.append((round(dev, 12), abs(_month_index(ma) - _month_index(mb)), ca, cb,
                                  tuple(rest[ca]), tuple(rest[cb]), ca, cb))
        for c in pairs(cands):
            out.append((root, {c[6]: list(c[4]), c[7]: list(c[5])}))
    return out


def _boxes(book, info: Dict[str, _Fill], cals: List[Structure]) -> Tuple[List[Structure], List[Structure]]:
    """Rule 3: (boxes, the calendars left) from the ticket calendars."""
    def legs_of(s: Structure):
        if len(s.fills) != 2:
            return None
        (ca, ia), (cb, _ib) = sorted(s.fills.items(), key=lambda kv: info[kv[1][0]].month)
        root = book.roots.get(info[ia[0]].root_id)
        return (ca, cb, root) if root is not None else None

    cands = []
    for i, a in enumerate(cals):
        la = legs_of(a)
        for j in range(i + 1, len(cals)):
            b = cals[j]
            lb = legs_of(b)
            if la is None or lb is None:
                continue
            (na, fa, ra), (nb, fb, rb) = la, lb
            if (ra.root_id == rb.root_id or ra.subsector != rb.subsector or ra.exchange == rb.exchange
                    or info[a.fills[na][0]].month != info[b.fills[nb][0]].month
                    or info[a.fills[fa][0]].month != info[b.fills[fb][0]].month):
                continue
            qa, qb = _open_lots(book, info, a.fills[na]), _open_lots(book, info, b.fills[nb])
            if abs(qa) <= LEFTOVER_FLOOR or abs(qb) <= LEFTOVER_FLOOR or _sign(qa) == _sign(qb):
                continue
            unit = _shared_unit(ra, rb)
            pa, pb = (_physical_per_lot(ra, unit), _physical_per_lot(rb, unit)) if unit else (None, None)
            if not pa or not pb:
                continue
            wa, wb = abs(qa) * pa, abs(qb) * pb
            gap = abs(wa - wb) / max(wa, wb)
            if gap <= BALANCE_TOLERANCE + 1e-12:
                cands.append((round(gap, 12), i, j, unit, wa, wb))
    used, boxes = set(), []
    for _gap, i, j, unit, wa, wb in sorted(cands):
        if i in used or j in used:
            continue
        used |= {i, j}
        a, b = cals[i], cals[j]
        box = Structure(ORIGIN_BOX, BOX, f"two calendars of one commodity on two exchanges, opposite ways: "
                                          f"{wa:,.0f} against {wb:,.0f} {unit} (within {BALANCE_TOLERANCE:.0%})")
        for s in (a, b):
            for cid, ids in s.fills.items():
                box.fills[cid].extend(ids)
        (na, fa, _ra), (nb, fb, _rb) = legs_of(a), legs_of(b)
        box.components = [{"month": info[a.fills[na][0]].month, "contract_ids": [na, nb]},
                          {"month": info[a.fills[fa][0]].month, "contract_ids": [fa, fb]}]
        boxes.append(box)
    return boxes, [s for n, s in enumerate(cals) if n not in used]


def _china_legs(book, info: Dict[str, _Fill], s: Structure) -> Dict[str, float]:
    """{month: signed open lots} of the structure's legs on a mainland Chinese exchange in CNY."""
    out: Dict[str, float] = defaultdict(float)
    for cid, ids in s.fills.items():
        f = info.get(ids[0]) if ids else None
        root = book.roots.get(f.root_id) if f else None
        if root is None or root.country != st.CHINA or root.currency not in st.CNY_CCYS:
            continue
        out[f.month] += _open_lots(book, info, ids) * s.share.get(cid, 1.0)
    return {m: v for m, v in out.items() if abs(v) > LEFTOVER_FLOOR}


def _hedge_month(book, tid: str) -> str:
    t = book.by_id[tid]
    if t["product"] in st.FX_PRODUCTS:
        return str(t["leg_date"] or "")[:7]
    return str(t["month_key"] or "")[:7]


def _place_hedges(book, info: Dict[str, _Fill], structures: List[Structure], hedge_ids: Sequence[str],
                  unplaced: Dict[str, str]) -> None:
    """Rule 5: each hedge contract's fills to the structure whose China legs it covers."""
    from engine.spreads import trades as tr
    if not hedge_ids:
        return
    real = [s for s in structures if s.kind != UNMATCHED]
    china = {id(s): _china_legs(book, info, s) for s in real}
    by_cid: Dict[str, List[str]] = defaultdict(list)
    for tid in hedge_ids:
        t = book.by_id[tid]
        cid, _month, _prompt = tr._contract_key(t, book.roots.get(str(t["base_ccy"] or "")))
        by_cid[cid].append(tid)
    month_of = {cid: _hedge_month(book, ids[0]) for cid, ids in by_cid.items()}
    # a hedge calendar dealt as one: on one trade date, hedges of two months, opposite, lots equal within
    # 5 %, fill against fill (the closest sizes first), then the day's rest pooled per contract
    day_fills: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
    for cid, ids in by_cid.items():
        for tid in ids:
            q = book.lots(tid)
            if q is not None and abs(q) > _EPS:
                day_fills[str(book.by_id[tid]["trade_date"])[:10]].append((tid, cid, q))
    taken: set = set()

    def link(groups: Sequence[Tuple[str, Sequence[str], float]]) -> bool:
        fits = [s for s in real if all(month_of[c] in china[id(s)] and _sign(china[id(s)][month_of[c]]) == -_sign(q)
                                       for c, _ids, q in groups)]
        if len(fits) != 1:
            return False
        for c, ids, _q in groups:
            fits[0].hedge_fills[c].extend(ids)
            taken.update(ids)
        return True

    def equal(qa: float, qb: float) -> bool:
        return abs(abs(qa) - abs(qb)) / max(abs(qa), abs(qb)) <= TOLERANCE + 1e-12

    for day in sorted(day_fills):
        items = sorted(day_fills[day], key=lambda x: fill_key(book, x[0]))
        cands = []
        for i, (ta, ca, qa) in enumerate(items):
            for tb, cb, qb in items[i + 1:]:
                if month_of[ca] != month_of[cb] and _sign(qa) != _sign(qb) and equal(qa, qb):
                    cands.append((round(abs(abs(qa) - abs(qb)) / max(abs(qa), abs(qb)), 12), fill_key(book, ta),
                                  fill_key(book, tb), ta, ca, qa, tb, cb, qb))
        for _d, _ka, _kb, ta, ca, qa, tb, cb, qb in sorted(cands):
            if ta not in taken and tb not in taken:
                link([(ca, [ta], qa), (cb, [tb], qb)])
        rest: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for tid, cid, q in items:
            if tid not in taken:
                rest[cid].append((tid, q))
        pooled = {c: float(sum(q for _t, q in v)) for c, v in rest.items()}
        cids = sorted(c for c, q in pooled.items() if abs(q) > _EPS)
        for i, ca in enumerate(cids):
            for cb in cids[i + 1:]:
                qa, qb = pooled[ca], pooled[cb]
                if (month_of[ca] != month_of[cb] and _sign(qa) != _sign(qb) and equal(qa, qb)
                        and not {t for t, _q in rest[ca] + rest[cb]} & taken):
                    link([(ca, [t for t, _q in rest[ca]], qa), (cb, [t for t, _q in rest[cb]], qb)])
    hedged: Dict[int, set] = defaultdict(set)
    for s in real:
        for c in s.hedge_fills:
            hedged[id(s)].add(month_of[c])
    with_china = [s for s in real if china[id(s)]]
    for cid in sorted(by_cid):
        ids = [t for t in by_cid[cid] if t not in taken]
        if not ids:
            continue
        m = month_of[cid]
        cands = [s for s in real if m in china[id(s)]]
        target = None
        if len(cands) == 1:
            target = cands[0]
        elif len(cands) > 1:
            free = [s for s in cands if m not in hedged[id(s)]]
            target = free[0] if len(free) == 1 else None
        elif len(with_china) == 1:
            target = with_china[0]
        if target is None and not cands and len(real) == 1:
            target = real[0]
        if target is None:
            why = (f"a hedge no single spread of the trade takes ({len(cands)} spreads hold a China leg in its month)"
                   if cands else "a hedge no single spread of the trade takes: it hedges the trade as a whole"
                   if real else "a currency trade of a trade with no futures spread: a leg of the trade, in no spread")
            for t in ids:
                unplaced[t] = why
            continue
        target.hedge_fills[cid].extend(ids)
        hedged[id(target)].add(m)


def find_structures(book, name: str, tids: Sequence[str], roll_ids: set, one_roots: set,
                    names: Dict[str, str]) -> Tuple[List[Structure], Dict[str, str], List[str]]:
    """(structures, {trade id: why outside every structure}, notes) of one trade: the rule of the
    module docstring. Every futures and LME fill of the trade is in exactly one structure (or two,
    by share, when two spreads of the pooled rule hold one contract); a hedge in one structure or
    listed outside with why; an option, an FX position of its own or a contract the app does not
    recognise is outside every structure (a leg of the trade, as before)."""
    from engine.spreads import trades as tr
    info: Dict[str, _Fill] = {}
    hedge_ids: List[str] = []
    unplaced: Dict[str, str] = {}
    notes: List[str] = []
    for tid in tids:
        t = book.by_id[tid]
        root = book.roots.get(str(t["base_ccy"] or ""))
        if t["product"] == tr.UNRECOGNISED:
            unplaced[tid] = "a contract the app does not recognise: a leg of the trade, in no spread"
            continue
        if is_hedge(root, t["product"], t["base_ccy"], t["quote_ccy"], t["instrument_id"]):
            hedge_ids.append(tid)
            continue
        q = book.lots(tid)
        if t["product"] in st.PAIRABLE_PRODUCTS and root is not None and q is not None:
            cid, month, _prompt = tr._contract_key(t, root)
            per = float(root.contract_size) if t["product"] == "LME_FWD" else 1.0
            info[tid] = _Fill(tid, cid, root.root_id, month, q / per, str(t["trade_date"])[:10])
        else:
            unplaced[tid] = "an option or a position of its own: a leg of the trade, in no spread"
    structures: List[Structure] = []
    overrides = getattr(book, "overrides", {}) or {}
    pinned: Dict[str, List[str]] = defaultdict(list)
    split = set()
    for tid in list(info) + hedge_ids:
        ov = overrides.get(tid)
        if ov and ov.get("action") == PIN and ov.get("group_name"):
            pinned[str(ov["group_name"])].append(tid)
        elif ov and ov.get("action") == SPLIT and tid in info:
            split.add(tid)
    for group, ids in sorted(pinned.items()):
        s = Structure(ORIGIN_PINNED, "", f"pinned by hand to {group} (spread_overrides)")
        s.name = group
        for tid in ids:
            if tid in info:
                s.fills[info[tid].cid].append(tid)
            else:
                t = book.by_id[tid]
                cid, _m, _p = tr._contract_key(t, book.roots.get(str(t["base_ccy"] or "")))
                s.hedge_fills[cid].append(tid)
                hedge_ids.remove(tid)
        s.kind = s.of_kind = _kind_from_legs(book, info, s)
        structures.append(s)
    in_pins = {t for s in structures for v in s.fills.values() for t in v}
    pool = [t for t in info if t not in split and t not in in_pins]
    pool_roots = {info[t].root_id for t in pool}
    # rule 1: a value-sized spread over two commodities (CATTLE) is one structure; one commodity on
    # two exchanges (strategies' one_spread too, sized by tonnage) still has its calendars and boxes
    value_sized = len({getattr(book.roots.get(r), "subsector", r) for r in one_roots}) >= 2
    tickets: List[Structure] = []
    if pool and not (value_sized and pool_roots <= one_roots):
        merged: Dict[Tuple[str, frozenset], Structure] = {}
        for root, fills in _tickets(book, info, pool, roll_ids):
            key = (root, frozenset(fills))
            s = merged.get(key)
            if s is None:
                s = merged[key] = Structure(ORIGIN_TICKET, "CALENDAR",
                                            "put on as a calendar (one trade date, opposite months, lots within 5 %)")
            for cid, ids in fills.items():
                s.fills[cid].extend(ids)
        boxes, cals = _boxes(book, info, list(merged.values()))
        tickets = boxes + cals
        structures += tickets
    in_tickets = {t for s in tickets for v in s.fills.values() for t in v}
    rest = [t for t in pool if t not in in_tickets]
    loose: Dict[str, List[str]] = defaultdict(list)
    if rest:
        plegs, _h, _o, _closed = st._build_legs(book, rest)
        pbc = {p.contract_id: p for p in plegs}
        parts, _left, note = tr._decompose(book, rest, plegs, roll_ids)
        if note:
            notes.append(note)
        parts = tr._split_parts(book, parts, pbc, one_roots, names)
        by_cid: Dict[str, List[str]] = defaultdict(list)
        for t in rest:
            by_cid[info[t].cid].append(t)
        holders: Dict[str, List[int]] = defaultdict(list)
        for n, p in enumerate(parts):
            for cid in p.legs:
                holders[cid].append(n)
        for n, p in enumerate(parts):
            s = Structure(ORIGIN_POOLED, p.kind, p.note)
            s.closed_roots, s.of_kind, s.split = list(p.closed_roots), p.of_kind, p.split
            for cid, v in p.legs.items():
                s.fills[cid] = list(by_cid.get(cid, []))
                if len(holders[cid]) > 1:
                    tot = sum(abs(parts[j].legs[cid]) for j in holders[cid])
                    s.share[cid] = abs(v) / tot if tot > _EPS else 1.0 / len(holders[cid])
            structures.append(s)
        for cid, ids in by_cid.items():
            if cid not in holders:
                loose[cid] = ids
    # a contract no spread holds: the one structure already holding it, a flat one the spread whose
    # other side it was, else one of the trade's unmatched legs
    left: Dict[str, List[str]] = {}
    for cid, ids in sorted(loose.items()):
        root_id = info[ids[0]].root_id
        flat = abs(_open_lots(book, info, ids)) <= LEFTOVER_FLOOR
        target = None
        # a pinned structure holds exactly the fills pinned to it, never one more
        auto = [s for s in structures if s.origin != ORIGIN_PINNED]
        own = [s for s in auto if cid in s.fills and not s.share.get(cid)]
        sides = [s for s in auto if root_id in s.closed_roots] if flat else []
        same = [s for s in auto if any(info[v[0]].root_id == root_id for v in s.fills.values() if v)] if flat else []
        if len(sides) == 1:
            target = sides[0]
        elif len(own) == 1:
            target = own[0]
        elif len(same) == 1:
            target = same[0]
        if target is not None:
            target.fills[cid].extend(ids)
        else:
            left[cid] = ids
    for tid in sorted(split):
        left.setdefault(info[tid].cid, []).append(tid)
    if left:
        open_left = {c: _open_lots(book, info, ids) for c, ids in left.items()}
        if (not structures and len({info[v[0]].root_id for v in left.values()}) == 1
                and len([c for c, x in open_left.items() if abs(x) > LEFTOVER_FLOOR]) <= 1 and not split):
            # the trade's one commodity: held one way, or, all flat, typed from the months it traded
            s = Structure(ORIGIN_LEFTOVER, OUTRIGHT, "one commodity held one way")
            if not any(abs(x) > LEFTOVER_FLOOR for x in open_left.values()):
                for cid, ids in left.items():
                    s.fills[cid].extend(ids)
                s.kind = s.of_kind = _kind_from_legs(book, info, s)
                s.note = "nothing open: typed from the months it traded"
                s.fills.clear()
        else:
            listed = ", ".join(f"{names.get(c, c)} {'long' if x > 0 else 'short' if x < 0 else 'flat'}"
                               + (f" {abs(x):,.6g} lots" if abs(x) > LEFTOVER_FLOOR else "")
                               for c, x in sorted(open_left.items()))
            why = ("split out by hand (spread_overrides)" if split and set(left) <= {info[t].cid for t in split}
                   else "no spread of this trade takes them")
            s = Structure(ORIGIN_LEFTOVER, UNMATCHED, f"Unmatched legs ({listed}): {why}")
            s.of_kind = ""
        for cid, ids in left.items():
            s.fills[cid].extend(ids)
        structures.append(s)
    for s in structures:
        for cid in list(s.fills):
            s.fills[cid] = sorted(dict.fromkeys(s.fills[cid]), key=lambda t: fill_key(book, t))
    _place_hedges(book, info, structures, hedge_ids, unplaced)
    for s in structures:
        for cid in list(s.hedge_fills):
            s.hedge_fills[cid] = sorted(dict.fromkeys(s.hedge_fills[cid]), key=lambda t: fill_key(book, t))
    return structures, unplaced, notes


def _kind_from_legs(book, info: Dict[str, _Fill], s: Structure) -> str:
    """A structure's kind from its legs alone (a pinned one): one root over two months a calendar,
    one contract an outright, one commodity on two exchanges cross exchange (a box when both hold
    the same two months), else cross product."""
    roots: Dict[str, set] = defaultdict(set)
    for ids in s.fills.values():
        f = info.get(ids[0]) if ids else None
        if f is not None:
            roots[f.root_id].add(f.month)
    if not roots:
        return UNMATCHED
    if len(roots) == 1:
        return "CALENDAR" if len(next(iter(roots.values()))) > 1 else OUTRIGHT
    rs = [book.roots[r] for r in roots if r in book.roots]
    if len(rs) == 2 and len({r.subsector for r in rs}) == 1 and len({r.exchange for r in rs}) == 2:
        months = list(roots.values())
        return BOX if len(months[0]) == 2 and months[0] == months[1] else CROSS_EXCHANGE
    if len({r.subsector for r in rs}) == 1 and len({r.exchange for r in rs}) > 1:
        return CROSS_EXCHANGE
    return CROSS_PRODUCT


# ------------------------------------------------------------------ a structure's legs
def leg_portion(book, row: dict, fids: Sequence[str], share: float, split_ok: bool, by_trade: dict,
                daily_why: str) -> dict:
    """One structure's leg: the trade's leg row (``trades._leg_rows``) on the structure's own fills
    of that contract (times ``share`` when two structures share them). Its lots, open quantity,
    average cost (``avg_cost``), LTD (its fills' ``value_book`` rows summed), Daily (its fills'
    parts of the trade's Daily split), and its LTD split into open and locked in on its own
    average: open = open quantity x multiplier x (mark - average cost) x the spot the rows were
    converted at, locked in = LTD - open, both also in the contract's currency
    (``pnl_open_local``, ``pnl_locked_local``)."""
    from engine.spreads import trades as tr
    fids = [t for t in fids if t in book.by_id]
    open_f = [t for t in fids if book.is_open(t)]
    t0 = book.by_id[fids[0]]
    root = book.roots.get(str(t0["base_ccy"] or ""))
    per_lot = float(root.contract_size) if (t0["product"] == "LME_FWD" and root is not None) else 1.0
    q_full = float(sum(book.lots(t) or 0.0 for t in open_f))
    status = "open" if abs(q_full * share) > _EPS else ("flat" if open_f else "closed")
    qty = q_full * share if status == "open" else 0.0
    lots = qty / per_lot
    if status == "open":
        avg, _pos, locked_q, avg_why = avg_cost(book, open_f)
    else:
        avg, avg_why, locked_q = None, "no open lots", None
    rows = [book.today.get(t) for t in fids]
    unpriced = [f"{t} ({(r or {}).get('reason') or 'not valued by value_book'})" for t, r in zip(fids, rows)
                if r is None or str(r.get("reason") or "") or _num(r.get("pnl_usd")) is None]
    ltd_full = None if unpriced else float(sum(float(r["pnl_usd"]) for r in rows))
    if split_ok and all(t in by_trade for t in fids):
        daily, daily_reason = float(sum(sum(by_trade[t].values()) for t in fids)) * share, ""
    else:
        daily = None
        daily_reason = daily_why if not split_ok else "a trade of this leg is not in the trade's Daily split"
    mult = 1.0 if t0["product"] in tr._NO_MULTIPLIER else _num(t0["multiplier"])
    p_open, p_locked, why = _split(book, row, fids, open_f, q_full, avg, ltd_full, status, mult)
    open_local = locked_local = None
    if status != "open":
        # nothing open: all of it is locked in, in the contract's currency its rows' own local P&L
        local = [_num((r or {}).get("pnl_local")) for r in rows]
        open_local = 0.0
        locked_local = None if unpriced or any(v is None for v in local) else float(sum(local)) * share
    elif mult is not None:
        locked_local = locked_q * mult * share
        if avg is not None and _num(row.get("mark")) is not None:
            open_local = qty * mult * (float(row["mark"]) - avg)
    out = dict(row)
    value_local = value_usd = None
    value_local_reason = value_reason = ""
    raw_mult = _num(t0["multiplier"])
    if status != "open":
        value_local = value_usd = 0.0
    elif t0["product"] in st.OPTION_PRODUCTS:
        value_local_reason = value_reason = "an option's lots x price is its value, not a notional"
    else:
        m = _num(row.get("mark"))
        if m is None or raw_mult is None:
            value_local_reason = row.get("mark_reason") or "its multiplier is not a number"
        else:
            value_local = qty * raw_mult * m
        rq = _num(row.get("quantity"))
        if row.get("hedge") and rq and _num(row.get("value_usd")) is not None:
            value_usd = float(row["value_usd"]) * qty / rq
        elif row.get("hedge"):
            value_reason = row.get("value_reason") or "the hedge's USD notional is not known"
        else:
            leg = Leg(str(t0["instrument_id"]), str(t0["base_ccy"] or ""), str(t0["account"]), str(t0["trade_date"]),
                      lots, tuple(open_f), str(row.get("month") or ""))
            value_usd, value_reason = book.notional(leg, qty)
    row_lots = _num(row.get("lots"))
    rd = _num(row.get("roll_down_usd_per_month"))
    out.update(
        trade_ids=list(fids), open_trade_ids=list(open_f), quantity=qty, lots=lots,
        side="long" if lots > _EPS else "short" if lots < -_EPS else "flat", status=status,
        avg_fill=avg, avg_fill_reason="" if avg is not None else avg_why,
        value_local=value_local, value_local_reason=value_local_reason, value_usd=value_usd, value_reason=value_reason,
        pnl_usd={"daily": daily, "ltd": None if ltd_full is None else ltd_full * share},
        pnl_reasons={"daily": daily_reason, "ltd": ("unpriced: " + "; ".join(unpriced)) if unpriced else ""},
        pnl_open=None if p_open is None else p_open * share,
        pnl_locked=None if p_locked is None else p_locked * share, pnl_split_reason=why,
        pnl_open_local=open_local, pnl_locked_local=locked_local,
        unwound=_unwound(book, fids, q_full, status),
        roll_down_usd_per_month=(rd * lots / row_lots if rd is not None and row_lots else
                                 (None if status == "open" else row.get("roll_down_usd_per_month"))),
        leg_lots=row_lots, fill_share=share,
    )
    return out


def _unwound(book, fids: Sequence[str], q_full: float, status: str) -> bool:
    if status != "open":
        return bool(fids)
    gross = sum(abs(book.lots(t) or 0.0) for t in fids)
    return gross > abs(q_full) + 1e-6 * max(1.0, gross)


def _split(book, row: dict, fids: Sequence[str], open_f: Sequence[str], q_full: float, avg: Optional[float],
           ltd: Optional[float], status: str, mult: Optional[float]) -> Tuple[Optional[float], Optional[float], str]:
    """(open, locked in, why when None) of the structure's own fills of one contract: the rule of
    ``trades._split_leg`` on these fills and their ``avg_cost``."""
    name = row.get("name") or row.get("contract_id")
    if ltd is None:
        return None, None, (row.get("pnl_reasons") or {}).get("ltd") or f"{name}: no P&L since entry"
    if status != "open":
        return 0.0, float(ltd), ""
    if avg is None:
        return None, None, f"{name}: no average cost of the open lots"
    if mult is None:
        return None, None, f"{name}: its multiplier is not a number"
    spots, marks = set(), set()
    for tid in open_f:
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
    if not marks:
        return None, None, f"{name}: no open fill"
    if (max(marks) - min(marks) > 1e-9 * max(1.0, abs(max(marks)))
            or max(spots) - min(spots) > 1e-12 * max(1.0, max(spots))):
        return None, None, f"{name}: its open fills are marked at different closes, so the open lots are not split"
    s, m = spots.pop(), marks.pop()
    if not _unwound(book, fids, q_full, status):
        return float(ltd), 0.0, ""
    opened = q_full * mult * (m - float(avg)) * s
    return opened, float(ltd) - opened, ""


# ------------------------------------------------------------------ the quote (7.3 - 7.5)
def _blank_quote(why: str) -> dict:
    return {"unit": "", "qty_unit": "", "entry": None, "prev": None, "now": None, "change": None,
            "entry_reason": why, "prev_reason": why, "now_reason": why, "change_reason": why, "reason": why,
            "legs": [], "size": None, "size_lots": None, "size_text": "", "size_reason": why,
            "usd_per_unit": None, "balance": None, "balance_leg": "", "balance_reason": why,
            "hedge_cover": [], "fx_unhedged": False, "fx_note": "", "leftover_legs": False, "leftover_reason": "",
            "estimate_note": "", "components": []}


def _spot_of(rows: Dict[str, dict], ids: Sequence[str]) -> Tuple[Optional[float], str]:
    for t in ids:
        r = rows.get(t) or {}
        s = _num(r.get("spot"))
        if s:
            return s, str(r.get("spot_source") or "")
    return None, ""


def _hedge_rate(book, hedges: Sequence[dict], key: str) -> Tuple[Optional[float], str]:
    """The linked hedges' CNH per USD: their open lots' average cost (``key`` 'entry'), or their
    mark / previous close's mark ('now' / 'prev'), weighted by open lots."""
    live = [h for h in hedges if h.get("status") == "open" and abs(_num(h.get("quantity")) or 0.0) > _EPS]
    if not live:
        return None, "the hedge has no open lots"
    if key == "entry":
        ids = [t for h in live for t in h.get("open_trade_ids") or []]
        avg, _q, _l, why = avg_cost(book, ids)
        return avg, why
    field = "mark" if key == "now" else "prev_mark"
    num = den = 0.0
    for h in live:
        v = _num(h.get(field))
        if v is None:
            return None, f"{h.get('name')}: no {'mark' if key == 'now' else 'previous close'}"
        w = abs(float(h["quantity"]))
        num += v * w
        den += w
    return num / den, ""


def _hedge_usd(book, h: dict) -> Optional[float]:
    """A hedge leg's USD notional, signed (long USD positive): a USD/CNH future its lots x its USD
    lot, an FX trade its USD leg."""
    ids = h.get("trade_ids") or []
    t = book.by_id.get(ids[0]) if ids else None
    if t is None:
        return None
    root = book.roots.get(str(t["base_ccy"] or ""))
    q = _num(h.get("quantity")) or 0.0
    if root is not None and root.sector == FX_SECTOR and root.size_unit.strip().upper() == "USD":
        return q * float(root.contract_size)
    if t["product"] in st.FX_PRODUCTS:
        return st._fx_usd_leg(t, q)
    return _num(h.get("value_usd"))


def _is_cny_hedge(book, h: dict) -> bool:
    """A hedge priced in CNH / CNY per USD (a USD/CNH future, a USDCNH forward)."""
    ids = h.get("trade_ids") or []
    t = book.by_id.get(ids[0]) if ids else None
    if t is None:
        return False
    if str(t["quote_ccy"] or "") in st.CNY_CCYS and str(t["product"] or "") in st.FX_PRODUCTS:
        return str(t["base_ccy"] or "") == "USD"
    return str(h.get("currency") or "") in st.CNY_CCYS


def _linked(book, leg: dict, china: Sequence[dict], hedges: Sequence[dict]) -> List[dict]:
    """The CNY / CNH hedges of a China leg: those of its month, else every one when the structure
    has one China leg."""
    cny = [h for h in hedges if _is_cny_hedge(book, h)]
    own = [h for h in cny if str(h.get("month") or h.get("prompt") or "")[:7] == str(leg.get("month") or "")[:7]]
    if own:
        return own
    return cny if len(china) == 1 else []


def _conv(book, leg: dict, side: dict, hedges: Sequence[dict], china: Sequence[dict], prev_rows: Dict[str, dict]
          ) -> dict:
    """One leg's price at entry, on the previous close and now in USD per the structure's unit."""
    ccy = str(side["currency"])
    factor = float(side["factor"])
    out = {"factor": factor, "currency": ccy, "fx_basis": "usd" if ccy == "USD" else "", "fx_entry": None,
           "fx_prev": None, "fx_now": None, "hedge_ids": [], "estimate": [], "why": {}}
    linked = _linked(book, leg, china, hedges) if ccy in st.CNY_CCYS else []
    out["hedge_ids"] = [h["contract_id"] for h in linked]
    for key in ("entry", "prev", "now"):
        if ccy == "USD":
            out[f"fx_{key}"] = 1.0
            continue
        rate, _why = (_hedge_rate(book, linked, key) if linked else (None, "no hedge"))
        if rate:
            out[f"fx_{key}"] = 1.0 / rate
            out["fx_basis"] = "hedge"
            continue
        out["fx_basis"] = "spot"
        if key == "entry":
            ids = leg.get("open_trade_ids") or []
            days = [str(book.by_id[t]["trade_date"])[:10] for t in ids if t in book.by_id]
            day = min(days) if days else ""
            if not day:
                out["why"][key] = "no open fill to date the entry's spot"
                continue
            s, note, why_s = spot_near(book.conn, ccy, day)
            out[f"fx_{key}"] = s
            if s is None:
                out["why"][key] = why_s
            elif note:
                out["estimate"].append(note)
        else:
            rows = book.today if key == "now" else prev_rows
            s, src = _spot_of(rows, leg.get("open_trade_ids") or leg.get("trade_ids") or [])
            out[f"fx_{key}"] = s
            if s is None:
                out["why"][key] = f"no USD spot for {ccy} on the {'as-of' if key == 'now' else 'previous'} valuation"
            elif is_estimate(src):
                out["estimate"].append(f"{ccy} spot estimated ({src})")
    for key, price_key in (("entry", "avg_fill"), ("prev", "prev_mark"), ("now", "mark")):
        p, fx = _num(leg.get(price_key)), out[f"fx_{key}"]
        if p is None:
            reason = leg.get(f"{price_key}_reason") or (leg.get("mark_reason") if key == "now" else "") or ""
            out["why"].setdefault(key, f"{leg.get('name')}: no {'average cost' if key == 'entry' else 'price'}"
                                       + (f" ({reason})" if reason else ""))
        out[f"native_{key}"] = p
        out[f"usd_{key}"] = None if p is None or fx is None else p * factor * fx
    return out


def _qty_in(book, leg: dict, unit: str) -> Optional[float]:
    root = book.roots.get(str(leg.get("root_id") or ""))
    if root is None:
        return None
    per = _physical_per_lot(root, unit)
    return None if per is None else float(leg.get("lots") or 0.0) * per


def _level_from(weights: Sequence[Tuple[dict, float, dict]], key: str) -> Tuple[Optional[float], str]:
    total, whys = 0.0, []
    for leg, w, c in weights:
        v = c.get(f"usd_{key}")
        if v is None:
            whys.append(c["why"].get(key) or f"{leg.get('name')}: no USD price")
            continue
        total += w * v
    return (None, _join(whys)) if whys else (total, "")


def _pair_quote(book, legs: Sequence[dict], hedges: Sequence[dict], china: Sequence[dict], prev_rows: Dict[str, dict]
                ) -> Tuple[Optional[dict], str, List[Tuple[dict, float, dict]]]:
    """(the ratio spec, why None, [(leg, weight, converted prices)]) of a two-leg spread: the
    numerator +1, the other -1 (a calendar near - far), the numerator first."""
    pairs = [(r, book.roots.get(str(r.get("root_id") or ""))) for r in legs]
    spec, why = ratio_spec(pairs, None)
    if spec is None:
        return None, why, []
    n_id = spec["numerator"]["contract_id"]
    out = []
    for r in legs:
        side = spec["numerator"] if r["contract_id"] == n_id else spec["denominator"]
        out.append((r, 1.0 if r["contract_id"] == n_id else -1.0, _conv(book, r, side, hedges, china, prev_rows)))
    out.sort(key=lambda x: -x[1])
    return spec, "", out


def structure_quote(book, kind: str, legs: Sequence[dict], hedges: Sequence[dict], components: Sequence[dict],
                    prev_rows: Dict[str, dict]) -> dict:
    """The structure's quote (the spec's 7.3 - 7.5; display only, never a P&L figure):

    {unit ('USD/t'), qty_unit ('t'), entry, prev, now, change (+ ``<key>_reason``), legs [{contract_id,
    name, weight, currency, factor (quoted price -> currency per unit), native_entry / native_prev /
    native_now (the average cost and the marks as quoted), fx_entry / fx_prev / fx_now (USD per unit
    of its currency), fx_basis ('usd' | 'hedge': the linked USD/CNH hedge's average fill and mark |
    'spot': the spot of the leg's first fill and the valuation's spot), usd_entry / usd_prev /
    usd_now, qty (signed, in the unit), lots, balance (|qty| / (|weight| x |size|)), hedge_ids}],
    size (the first leg's position in the unit, signed: long the spread when > 0), size_lots,
    size_text ('Long 1,880 t (376 lots)'), size_reason, usd_per_unit (USD for a 1.0 rise of the
    level, = size), balance (the leg furthest from 100 %), balance_leg, balance_reason, hedge_cover
    [{leg, contract_id, hedges, hedge_usd, leg_usd (the leg's value at its average cost, in USD at
    the hedge's average fill), cover, wrong_way, reason}], fx_unhedged (a China leg converted at
    spot, no hedge), fx_note, leftover_legs (a leg flat while another is not: no level),
    leftover_reason, components (a box: its two arbs, far month first, each {month, contract_ids,
    unit, legs, entry, prev, now, change, <key>_reason}), estimate_note, reason}."""
    real = [r for r in legs if not r.get("unrecognised")]
    if kind == UNMATCHED:
        return _blank_quote("Unmatched legs: no spread, so no quote")
    if not real:
        return _blank_quote("No leg")
    china = [r for r in real if str(r.get("currency") or "") in st.CNY_CCYS
             and getattr(book.roots.get(str(r.get("root_id") or "")), "country", "") == st.CHINA]
    if any(str(r.get("product") or "") not in RATIO_PRODUCTS for r in real):
        return _blank_quote("A quote is of futures and LME legs only")
    open_legs = [r for r in real if r.get("status") == "open"]
    flat = [r for r in real if r.get("status") != "open"]
    weights: List[Tuple[dict, float, dict]] = []
    comps: List[dict] = []
    unit = ""
    if kind == BOX and len(components) == 2 and len(real) == 4:
        by_cid = {r["contract_id"]: r for r in real}
        near, far = sorted(components, key=lambda c: c["month"])
        for sign, comp in ((1.0, far), (-1.0, near)):
            pair = [by_cid[c] for c in comp["contract_ids"] if c in by_cid]
            if len(pair) != 2:
                return _blank_quote("The box's legs are not all on file")
            spec, why, ws = _pair_quote(book, pair, hedges, china, prev_rows)
            if spec is None:
                return _blank_quote(why)
            unit = unit or (spec["unit"] if "÷" not in spec["unit"] else "")
            weights += [(r, sign * w, c) for r, w, c in ws]
            comp_out = {"month": comp["month"], "contract_ids": list(comp["contract_ids"]), "unit": spec["unit"],
                        "legs": [r["contract_id"] for r, _w, _c in ws]}
            for key in ("entry", "prev", "now"):
                v, why_k = _level_from(ws, key)
                comp_out[key], comp_out[f"{key}_reason"] = v, why_k
            comp_out["change"] = (None if comp_out["now"] is None or comp_out["prev"] is None
                                  else comp_out["now"] - comp_out["prev"])
            comps.append(comp_out)
    elif len(real) == 2:
        ra, rb = (book.roots.get(str(r.get("root_id") or "")) for r in real)
        if ra is not None and rb is not None and ra.root_id != rb.root_id and not st.physical_rule(ra, rb):
            return _blank_quote("Two commodities sized by value: no level in one unit (see the spread's level and "
                                "ratio)")
        spec, why, weights = _pair_quote(book, real, hedges, china, prev_rows)
        if spec is None:
            return _blank_quote(why)
        if "÷" in spec["unit"]:
            return _blank_quote(f"{spec['numerator']['name']} and {spec['denominator']['name']} are in two units: a "
                                f"difference across units means nothing (the ratio is on the spread)")
        unit = spec["unit"]
    elif len(real) == 1:
        r = real[0]
        root = book.roots.get(str(r.get("root_id") or ""))
        if root is None:
            return _blank_quote(f"{r.get('name')}: not in the contract list")
        q = str(root.quote_unit).partition("/")[2].strip()
        side = {"currency": r.get("currency") or root.currency, "factor": float(root.price_scale), "unit": q}
        weights = [(r, 1.0, _conv(book, r, side, hedges, china, prev_rows))]
        unit = f"USD/{q}" if q else ""
    else:
        return _blank_quote(f"A spread of {len(real)} legs has no single quote unit (value-sized, or a template's "
                            f"own level: see the level)")
    qty_unit = unit.partition("/")[2].strip()
    out = _blank_quote("")
    out.update(unit=unit, qty_unit=qty_unit, components=comps)
    lead = weights[0][0]
    size = _qty_in(book, lead, qty_unit) if lead.get("status") == "open" else None
    rows = []
    for r, w, c in weights:
        qty = _qty_in(book, r, qty_unit)
        bal = (abs(qty) / (abs(w) * abs(size)) if qty is not None and size and abs(size) > _EPS else None)
        rows.append({"contract_id": r["contract_id"], "name": r.get("name", ""), "weight": w,
                     "currency": c["currency"], "factor": c["factor"], "fx_basis": c["fx_basis"],
                     "hedge_ids": c["hedge_ids"], "qty": qty, "lots": r.get("lots"), "balance": bal,
                     **{f"{p}_{k}": c.get(f"{p}_{k}") for p in ("native", "fx", "usd") for k in ("entry", "prev", "now")}})
    out["legs"] = rows
    if flat and open_legs:
        why = (f"Leftover legs: {', '.join(r.get('name', '') for r in flat)} flat while "
               f"{', '.join(r.get('name', '') for r in open_legs)} still open, so the spread has no level")
        out.update(leftover_legs=True, leftover_reason=why, entry_reason=why, prev_reason=why, now_reason=why,
                   change_reason=why, reason=why)
    elif not open_legs:
        why = "Nothing open: no level"
        out.update(entry_reason=why, prev_reason=why, now_reason=why, change_reason=why, reason=why)
    else:
        for key in ("entry", "prev", "now"):
            v, why = _level_from(weights, key)
            out[key], out[f"{key}_reason"] = v, why
        out["change"] = None if out["now"] is None or out["prev"] is None else out["now"] - out["prev"]
        out["change_reason"] = _join([out["now_reason"], out["prev_reason"]])
    if size is not None:
        lots = _num(lead.get("lots")) or 0.0
        out.update(size=size, size_lots=lots, usd_per_unit=size, size_reason="",
                   size_text=f"{'Long' if size > 0 else 'Short'} {abs(size):,.{2 if abs(size) < 1000 else 0}f} "
                             f"{qty_unit} ({abs(lots):,.{0 if abs(lots - round(lots)) < 1e-6 else 2}f} lots)")
        bals = [(abs(b["balance"] - 1.0), b) for b in rows if b["balance"] is not None]
        if bals and not out["leftover_legs"]:
            worst = max(bals, key=lambda x: (x[0], x[1]["contract_id"]))[1]
            out.update(balance=worst["balance"], balance_leg=worst["name"], balance_reason="")
    else:
        out["size_reason"] = f"{lead.get('name')} is not open: no size"
    # hedge cover per China leg, at its average cost in USD at the hedge's own average fill
    covers = []
    for r in china:
        linked = _linked(book, r, china, hedges)
        if not linked or r.get("status") != "open":
            continue
        rate, why = _hedge_rate(book, linked, "entry")
        hedge_usd = [_hedge_usd(book, h) for h in linked if h.get("status") == "open"]
        ids = r.get("open_trade_ids") or r.get("trade_ids")
        mult, avg = _num(book.by_id[ids[0]]["multiplier"]), _num(r.get("avg_fill"))
        if rate is None or not hedge_usd or any(x is None for x in hedge_usd) or mult is None or avg is None:
            covers.append({"leg": r["name"], "contract_id": r["contract_id"], "hedges": [h["contract_id"] for h in linked],
                           "hedge_usd": None, "leg_usd": None, "cover": None, "wrong_way": None,
                           "reason": why or "the hedge or the leg has no figure to compare"})
            continue
        h_usd = float(sum(hedge_usd))
        leg_usd = float(r["quantity"]) * mult * avg / rate
        covers.append({"leg": r["name"], "contract_id": r["contract_id"], "hedges": [h["contract_id"] for h in linked],
                       "hedge_usd": h_usd, "leg_usd": leg_usd,
                       "cover": abs(h_usd) / abs(leg_usd) if abs(leg_usd) > _EPS else None,
                       "wrong_way": bool(h_usd * float(r["quantity"]) > 0), "reason": ""})
    out["hedge_cover"] = covers
    spot_legs = [r["name"] for r, _w, c in weights if c["fx_basis"] == "spot"
                 and str(r.get("currency") or "") in st.CNY_CCYS]
    if spot_legs:
        out.update(fx_unhedged=True, fx_note=(f"FX unhedged: {', '.join(spot_legs)} converted at the spot of its first "
                                              f"fill (entry) and the valuation's spot (now): no USD/CNH hedge in this "
                                              f"spread"))
    out["estimate_note"] = _join(e for _r, _w, c in weights for e in c["estimate"])
    return out


def structure_flags(quote: dict) -> List[dict]:
    """The structure's own flags: leftover legs, a leg off balance, a hedge the wrong way or off its
    leg's size, FX unhedged."""
    flags = []
    if quote.get("leftover_legs"):
        flags.append({"code": "leftover_legs", "label": "Leftover legs", "severity": "amber",
                      "sentence": quote.get("leftover_reason", "")})
    b = _num(quote.get("balance"))
    if b is not None and abs(b - 1.0) > BALANCE_TOLERANCE + 1e-12:
        flags.append({"code": "leg_off_balance", "label": "Legs off balance", "severity": "amber",
                      "sentence": f"{quote.get('balance_leg')} is {b:.1%} of the spread's size (balanced is within "
                                  f"{BALANCE_TOLERANCE:.0%})"})
    for c in quote.get("hedge_cover") or []:
        if c.get("wrong_way"):
            flags.append({"code": "hedge_wrong_way", "label": "Hedge wrong way", "severity": "amber",
                          "sentence": f"The USD/CNH hedge of {c['leg']} runs with it, not against it (a long China leg "
                                      f"needs a short USD/CNH hedge)"})
        cover = _num(c.get("cover"))
        if cover is not None and abs(cover - 1.0) > BALANCE_TOLERANCE + 1e-12:
            flags.append({"code": "hedge_cover_off", "label": "Hedge cover off", "severity": "amber",
                          "sentence": f"The hedge of {c['leg']} covers {cover:.0%} of it at its average cost "
                                      f"(within {BALANCE_TOLERANCE:.0%} of 100 % is covered)"})
    if quote.get("fx_unhedged"):
        flags.append({"code": "fx_unhedged", "label": "FX unhedged", "severity": "amber",
                      "sentence": quote.get("fx_note", "")})
    return flags


def component_ratio(book, comp_legs: Sequence[dict]) -> dict:
    """A box component's ratio and USD spread keys (``ratio.spread_ratio`` on its two legs)."""
    by_cid = {r["contract_id"]: r for r in comp_legs}
    return spread_ratio(book, [{"contract_id": r["contract_id"], "lots": r.get("lots")} for r in comp_legs],
                        by_cid, CROSS_EXCHANGE, None)


__all__ = ["BOX", "CROSS_EXCHANGE", "CROSS_PRODUCT", "OUTRIGHT", "STRUCTURE_WORDS", "Structure", "TERM_STRUCTURE",
           "UNMATCHED", "avg_cost", "component_ratio", "fill_key", "find_structures", "leg_portion",
           "structure_flags", "structure_quote", "structure_type"]
