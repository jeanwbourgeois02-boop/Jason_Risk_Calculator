"""Jason's strategies as pairs (user-approved design, 2026-09-28).

A strategy (``trades.strategy``, the broker's PBRoot suffix) is the position (``book.py``); this
module reads *inside* it: which legs pair off, what is left outright, what is a hedge. The
pairing is deterministic from the strategy's type label, never the 5 % rule:

- **CROSS_EXCHANGE** pairs legs of the same contract month on two roots of the same commodity
  (contract-master's ``subsector``) on different exchanges, opposite signs.
- **TERM_STRUCTURE** pairs near against far on one root, opposite signs.
- **CROSS_PRODUCT** pairs two roots of one sector (contract-master's ``sector``: a crack, a
  crush, steel against iron ore, feeder against live cattle) in the same month, opposite signs;
  never across sectors (soybean oil is not paired with aluminium because both are December).

The strategy's type is the position's (``trade_type.py``: the label when the labelled trades
agree, else inferred from the legs). Its rule runs first; what it leaves is offered to the other
two rules in the fixed order term structure, cross exchange, cross product, and a pair made that
way says so in its ``note`` (``type_source`` 'fallback'). Pairs are made greedily by nearest
month; a leg that could pair more than one way is named in the note, never paired silently.

Sizes: the smaller side is paired in full, in physical units (tonnes through the contract size
and contract-master's unit table, or a ``config/spreads/`` template's own quantity when one
matches the two roots; never a number per commodity here), and the larger side pairs the whole
lots nearest to it (an LME leg, counted in tonnes, pairs the exact tonnes). What a pair holds
beyond an exact match is its ``residual_units``; whole lots left on a contract are the
strategy's ``residuals``. A USD/CNH future (a root of contract-master's ``fx`` sector) in a
strategy is a ``hedge``, never paired, whatever its status (a settled one still has a Daily on
its settlement day, which is hedge in the split); so is an FX spot / forward / swap booked under
the strategy's PBRoot (user, 2026-09-28: a rule, Jason's export has no such row yet), its USD
notional the USD leg's signed amount from the fill (long USD positive; None with a reason when
the pair has no USD leg), and an FX option on a currency pair (user, 2026-09-29), its USD
notional the USD leg of its delta (``hedges.py`` is the one test). A precious-metal pair (XAU,
XAG, XPT, XPD on either side) is never a hedge (user, 2026-09-29): an FX product on one is a
residual of its own, said so. An option on a future is a residual, not paired, said so. An LME prompt pairs by
its prompt month. The unlabelled trades ('' entry) have no type
of their own: there a cross pair is made only where a ``config/spreads/`` template names the two
roots (so gold is never paired with aluminium because both are December), and calendars as
usual.

Per pair, beside the sizes: its level at entry, on the previous close and now, in the
template's unit where a template matches the two roots (a China-against-West template the
desk screens as a ratio, ``ratio_screen``, is quoted as the plain ratio China over foreign of
the two raw prices in the template's quantity unit, as the research app's desk convention: no FX
in it), a calendar's near - far in the root's quote unit, else USD per common physical unit,
long leg first; the other form on ``level_alt`` / ``unit_alt``. The formula and the marks each
level reads are ``levels.py``'s and ``book.py``'s (``level_on``, ``entry_level``): the as-of
``value_book`` rows, the Daily reference close's rows, the fills. ``usd_per_unit`` is the USD
P&L of a 1.0 rise of the level on the paired lots (a ratio: with the foreign leg unchanged).
Notionals come from ``book.notional`` (lots x multiplier x mark x spot off the leg's own
``value_book`` row). ``next_event`` is the earlier leg's row of ``engine.expiry.expiry_schedule``,
with the business days between the two legs' events (``legs_apart_bd``, flagged over 5).

Per strategy: the pairs, residuals and hedges, the type, gross / net notional, the hedge
coverage and the Daily split (below). **Coverage sign** (user, 2026-09-28, from
Jason's own fills: buy SHFE zinc, sell USD/CNH at matched notional): a long China leg is long
USDCNH at its notional (its CNY price rises with USDCNH through import parity) and is hedged by
a SHORT USD/CNH position, so the right hedge has the opposite sign to ``cny_net_usd``:
``hedge_coverage_net = -hedge_usd / cny_net_usd`` (1.0 = fully hedged), ``unhedged_cny =
cny_net_usd + hedge_usd``, and a hedge with the same sign as the exposure is warned in
``hedge_reason``. The Daily split follows the P&L explain's one bucket order
(``period_explain.classify_trades``, user yes 2026-09-29): new trades, realised, hedge, then
spread / FX by ``daily_split``'s identity, and ``other`` for a trade that cannot be split, so the
Book and the P&L tab put a trade in the same bucket. Every open lot of the book is in exactly one pair, residual or hedge; a
trade's id is listed once, on the pair or residual its lots were allocated to (by trade date),
and ``contract_trade_ids`` on a leg lists every trade of the contract.

Nothing here re-marks or changes a P&L figure: the P&L keys are the strategy spread's own
(``value_book`` rows summed), and the split is an identity over them.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from data.contracts import ContractRoot, quantity_factor
from engine.pnl.valuation import usd_per_quote
from engine.spreads.grouping import LEFTOVER_FLOOR, Leg, calendar_shape, template_shape
from engine.spreads.hedges import FX_HEDGE_PRODUCTS, fx_non_hedge_why, is_hedge
from engine.spreads.levels import LevelLeg, LevelSpec, converted, spec_for, spec_to_dict, usd_per_level_unit
from engine.spreads.templates import Template, lot_in_quote_units
from engine.spreads.trade_type import CROSS_EXCHANGE, CROSS_PRODUCT, TERM_STRUCTURE, type_fields

PAIRABLE_PRODUCTS = ("FUTURE", "LME_FWD")
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP")     # the FX products whose hedge row is per pair and value date
OPTION_PRODUCTS = ("FX_OPTION", "CMDTY_OPTION", "EQ_OPTION")
CHINA = "CN"
RULES = (TERM_STRUCTURE, CROSS_EXCHANGE, CROSS_PRODUCT)   # the fallback order after the labelled rule
COMMON_UNITS = ("t", "bbl", "mmbtu")                       # one per dimension of contract-master's unit table
LEGS_APART_BD = 5
SOURCE_FALLBACK = "fallback"
RATIO_UNIT = "ratio"
KIND_PAIR = "pair"                                         # a level spec with no template: USD per common unit
_EPS = 1e-9


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _why(row) -> str:
    return str((row or {}).get("reason", "") or "") or "no USD P&L on its row"


def _sign(x: float) -> float:
    return 1.0 if x > 0 else -1.0


# ------------------------------------------------------------------ legs
@dataclass
class PLeg:
    """One contract's open lots in a strategy (an LME metal: one per prompt)."""

    contract_id: str        # 'HGZ26 Comdty'; an LME prompt 'LME:CA 2026-12-16' (expiry_schedule's id)
    instrument_id: str
    root_id: str
    root: ContractRoot
    product: str
    month: str              # '2026-12'
    prompt: str             # the LME prompt ISO, '' for a future
    lots: float             # signed open lots (an LME ticket: tonnes / the lot's tonnes, so fractional)
    whole_lots: bool        # the trades count whole lots (False for an LME ticket, booked in tonnes)
    trade_ids: Tuple[str, ...]
    account: str
    trade_date: str         # the earliest
    currency: str
    trade_lots: Dict[str, float]   # each trade's signed lots (for the allocation of ids to pairs)

    def physical(self, lots: float, unit: str) -> float:
        """``lots`` in ``unit`` through the contract size and contract-master's unit table."""
        return lots * self.root.contract_size * quantity_factor(self.root.size_unit.strip().lower(), unit)

    def as_leg(self, lots: float, trade_ids: Sequence[str]) -> Leg:
        return Leg(self.instrument_id, self.root_id, self.account, self.trade_date, lots, tuple(trade_ids), self.month)

    @property
    def size_unit(self) -> str:
        return self.root.size_unit.strip().lower()


def _build_legs(book, tids: Sequence[str]) -> Tuple[List[PLeg], List[str], List[dict], List[str]]:
    """(pairable legs, hedge trade ids, option residual rows, closed trade ids) of a strategy."""
    acc: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    hedges: List[str] = []
    options: List[dict] = []
    closed: List[str] = []
    for tid in tids:
        t = book.by_id[tid]
        root = book.roots.get(t["base_ccy"])
        if is_hedge(root, t["product"], t["base_ccy"], t["quote_ccy"], t["instrument_id"]):
            hedges.append(tid)      # open or settled: its Daily is hedge in the split either way
            continue
        if not book.is_open(tid):
            closed.append(tid)
            continue
        if t["product"] in FX_HEDGE_PRODUCTS and t["product"] not in OPTION_PRODUCTS:
            # a precious-metal spot / forward / swap: a position of its own (user, 2026-09-29)
            options.append({"trade_id": tid, "instrument_id": t["instrument_id"], "root_id": str(t["base_ccy"] or ""),
                            "product": t["product"], "lots": book.lots(tid),
                            "why": fx_non_hedge_why(t["base_ccy"], t["quote_ccy"], t["instrument_id"])})
        elif t["product"] in OPTION_PRODUCTS:
            options.append({"trade_id": tid, "instrument_id": t["instrument_id"], "root_id": str(t["base_ccy"] or ""),
                            "product": t["product"], "lots": book.lots(tid),
                            "why": "an option is not paired: it stays outright inside its strategy"})
        elif t["product"] in PAIRABLE_PRODUCTS and root is not None and book.lots(tid) is not None:
            prompt = str(t["leg_date"] or "") if t["product"] == "LME_FWD" else ""
            acc[(t["instrument_id"], prompt)].append(tid)
        else:
            options.append({"trade_id": tid, "instrument_id": t["instrument_id"], "root_id": str(t["base_ccy"] or ""),
                            "product": t["product"], "lots": book.lots(tid),
                            "why": (f"{t['product']} on {t['base_ccy']!r} is not on a root of config/contracts.csv "
                                    f"or its quantity is not a number: not paired")})
    legs: List[PLeg] = []
    for (inst, prompt), ids in sorted(acc.items()):
        t = book.by_id[ids[0]]
        root = book.roots[t["base_ccy"]]
        qty = sum(book.lots(i) for i in ids)
        if t["product"] == "LME_FWD":
            lots, whole, month = qty / root.contract_size, False, prompt[:7]
            contract_id = f"{root.root_id} {prompt}"
        else:
            lots, whole, month, contract_id = qty, True, str(t["month_key"] or ""), inst
        if abs(lots) < _EPS:
            closed.extend(ids)      # bought and sold back: flat, nothing to pair
            continue
        ordered = tuple(sorted(ids, key=lambda i: (book.by_id[i]["trade_date"], i)))
        scale = 1.0 / root.contract_size if t["product"] == "LME_FWD" else 1.0
        legs.append(PLeg(contract_id, inst, root.root_id, root, t["product"], month, prompt, lots, whole,
                         ordered, str(t["account"]), min(str(book.by_id[i]["trade_date"]) for i in ids),
                         root.currency, {i: (book.lots(i) or 0.0) * scale for i in ordered}))
    return legs, sorted(hedges), options, sorted(closed)


# ------------------------------------------------------------------ the pairing rules
def _allowed(rule: str, a: PLeg, b: PLeg, book=None, need_template: bool = False) -> bool:
    if a.contract_id == b.contract_id or _sign(a.lots) == _sign(b.lots):
        return False
    if rule == TERM_STRUCTURE:
        return a.root_id == b.root_id and a.month != b.month and bool(a.month and b.month)
    if not a.month or not b.month or a.month != b.month or a.root_id == b.root_id:
        return False
    if need_template and not _templates_for(book, a, b):
        return False
    if rule == CROSS_EXCHANGE:
        return a.root.subsector == b.root.subsector and a.root.exchange != b.root.exchange
    return a.root.sector == b.root.sector     # CROSS_PRODUCT: two roots of one sector, one month


def _months_apart(a: PLeg, b: PLeg) -> int:
    try:
        ya, ma = (int(x) for x in a.month.split("-")[:2])
        yb, mb = (int(x) for x in b.month.split("-")[:2])
    except ValueError:
        return 0
    return abs(ya * 12 + ma - yb * 12 - mb)


def _templates_for(book, a: PLeg, b: PLeg) -> List[Template]:
    """The two-leg templates over exactly these two roots, in label order."""
    want = {a.root_id, b.root_id}
    out = [t for t in book.by_root.get(a.root_id, ()) if len(t.legs) == 2 and set(t.roots) == want and t.usable]
    return sorted(out, key=lambda t: t.order)


def _common(book, a: PLeg, b: PLeg) -> Tuple[Optional[str], float, float, Optional[Template], List[Template], str]:
    """(unit, units per lot of a, of b, the template used, other templates, why when None): the
    quantity both legs are sized in. A term structure counts lots; a cross pair a template's
    quantity unit, else the first common physical unit of contract-master's unit table."""
    if a.root_id == b.root_id:
        if a.whole_lots:
            return "lots", 1.0, 1.0, None, [], ""
        return a.size_unit, a.root.contract_size, b.root.contract_size, None, [], ""
    templates = _templates_for(book, a, b)
    if templates:
        t = templates[0]
        upl = {leg.root_id: float(leg.units_per_lot) for leg in t.legs}
        return t.quantity_unit, upl[a.root_id], upl[b.root_id], t, templates[1:], ""
    for unit in COMMON_UNITS:
        try:
            return unit, a.physical(1.0, unit), b.physical(1.0, unit), None, [], ""
        except ValueError:
            continue
    return None, 0.0, 0.0, None, [], (f"{a.root_id} ({a.size_unit}) and {b.root_id} ({b.size_unit}) share no unit in "
                                      f"contract-master's table and no config/spreads/ template sizes them")


def _whole(x: float, leg: PLeg, cap: float) -> float:
    """The larger side pairs the whole lots nearest to the smaller side's size (at least one),
    never more than it holds; an LME leg counted in tonnes pairs the exact tonnes."""
    if not leg.whole_lots:
        return min(x, cap)
    return min(max(1.0, float(round(x))), cap)


def _pair_lots(rem_a: float, rem_b: float, a: PLeg, b: PLeg, upl_a: float, upl_b: float) -> Tuple[float, float]:
    """Signed lots each leg puts into the pair: the smaller side (in the common unit) in full."""
    pa, pb = abs(rem_a) * upl_a, abs(rem_b) * upl_b
    if pa <= pb:
        return rem_a, _sign(rem_b) * _whole(pa / upl_b, b, abs(rem_b))
    return _sign(rem_a) * _whole(pb / upl_a, a, abs(rem_a)), rem_b


def _order(rule: str, a: PLeg, b: PLeg, template: Optional[Template]) -> Tuple[PLeg, PLeg]:
    """Leg order: a calendar near first; a template its own order; else China first, else the long leg."""
    if rule == TERM_STRUCTURE:
        return (a, b) if a.month <= b.month else (b, a)
    if template is not None:
        return (a, b) if template.legs[0].root_id == a.root_id else (b, a)
    if a.root.country == CHINA and b.root.country != CHINA:
        return a, b
    if b.root.country == CHINA and a.root.country != CHINA:
        return b, a
    return (a, b) if a.lots > 0 else (b, a)


def _pair_all(book, legs: List[PLeg], labelled: str, need_template: bool = False
              ) -> Tuple[List[dict], Dict[str, float], List[str]]:
    """The greedy pairing: ``(raw pairs, remaining lots per contract, notes)``. A raw pair is
    ``{rule, source, a, b, lots_a, lots_b, unit, upl_a, upl_b, template, also, note}``."""
    rem = {leg.contract_id: leg.lots for leg in legs}
    rules = ([labelled] if labelled in RULES else []) + [r for r in RULES if r != labelled]
    pairs: List[dict] = []
    notes: List[str] = []
    dead: set = set()
    for rule in rules:
        cands = [(a, b) for a, b in combinations(legs, 2) if _allowed(rule, a, b, book, need_template)]
        cands.sort(key=lambda ab: (_months_apart(*ab), min(ab[0].month, ab[1].month), ab[0].contract_id,
                                   ab[1].contract_id))
        while True:
            live = [(a, b) for a, b in cands
                    if (a.contract_id, b.contract_id) not in dead
                    and abs(rem[a.contract_id]) > _EPS and abs(rem[b.contract_id]) > _EPS
                    and _sign(rem[a.contract_id]) != _sign(rem[b.contract_id])]
            if not live:
                break
            a, b = live[0]
            unit, upl_a, upl_b, template, also, why = _common(book, a, b)
            if unit is None:
                notes.append(f"{a.contract_id} and {b.contract_id} cannot be paired: {why}")
                dead.add((a.contract_id, b.contract_id))
                continue
            note_parts = []
            if rule != labelled:
                note_parts.append(f"paired as {rule.lower().replace('_', ' ')}, which is not the strategy's "
                                  f"{'type ' + labelled.lower().replace('_', ' ') if labelled else 'type (none read)'}"
                                  f": the {'labelled' if labelled else 'inferred'} rule left these legs unpaired")
            for x, other in ((a, b), (b, a)):
                partners = sorted({(q if p is x else p).contract_id for p, q in live if x in (p, q)} - {other.contract_id})
                if partners:
                    note_parts.append(f"{x.contract_id} could also pair with {', '.join(partners)}: paired with "
                                      f"{other.contract_id} (nearest month, then contract order)")
            la, lb = _pair_lots(rem[a.contract_id], rem[b.contract_id], a, b, upl_a, upl_b)
            rem[a.contract_id] -= la
            rem[b.contract_id] -= lb
            first, second = _order(rule, a, b, template)
            if first is b:
                la, lb, upl_a, upl_b = lb, la, upl_b, upl_a
            pairs.append({"rule": rule, "source": "label" if rule == labelled else SOURCE_FALLBACK,
                          "a": first, "b": second, "lots_a": la, "lots_b": lb, "unit": unit,
                          "upl_a": upl_a, "upl_b": upl_b, "template": template, "also": also,
                          "note": "; ".join(note_parts)})
    for k, v in rem.items():
        if abs(v) < LEFTOVER_FLOOR:
            rem[k] = 0.0
    return pairs, rem, notes


def _allocate(legs: List[PLeg], pairs: List[dict], rem: Dict[str, float]) -> Dict[str, Dict[str, List[str]]]:
    """Trade ids per consumer of each contract: a contract's lots may be split over pairs and a
    residual, but its trades are listed once, on the first pair that uses the contract, else on
    its residual (a fill is not split; ``contract_trade_ids`` on every leg lists them all).
    ``{contract_id: {'pair<n>' | 'residual': [ids]}}``."""
    out: Dict[str, Dict[str, List[str]]] = {}
    for leg in legs:
        first = next((f"pair{n}" for n, p in enumerate(pairs)
                      if leg.contract_id in (p["a"].contract_id, p["b"].contract_id)), "residual")
        out[leg.contract_id] = {first: list(leg.trade_ids)}
    return out


# ------------------------------------------------------------------ levels of a pair
def _spec(book, p: dict) -> Tuple[Optional[LevelSpec], str]:
    a, b = p["a"], p["b"]
    leg_a, leg_b = a.as_leg(p["lots_a"], a.trade_ids), b.as_leg(p["lots_b"], b.trade_ids)
    if p["rule"] == TERM_STRUCTURE:
        shape = calendar_shape(leg_a, leg_b, a.root.quote_unit, a.root.exchange_code)
        return spec_for(shape, [leg_a, leg_b], book.roots, book.templates)
    if p["template"] is not None:
        return spec_for(template_shape(p["template"]), [leg_a, leg_b], book.roots, book.templates)
    legs = []
    for pleg, w, upl in ((a, 1.0, p["upl_a"]), (b, -1.0, p["upl_b"])):
        lot_units = lot_in_quote_units(pleg.root)
        legs.append(LevelLeg(pleg.instrument_id, pleg.root_id, w, pleg.currency, float(pleg.root.price_scale),
                             lot_units / upl, None, lot_units, pleg.trade_ids, pleg.month))
    return LevelSpec(KIND_PAIR, f"USD/{p['unit']}", "USD", 0.0, tuple(legs), (1.0, -1.0),
                     (p["upl_a"], p["upl_b"])), ""


def _leg_prices(book, spec: LevelSpec, day: str, rows: Dict[str, dict], fallback: bool
                ) -> Tuple[Optional[List[float]], str, str]:
    """Each leg's quoted price on ``day`` from the ``value_book`` rows (the first row with a mark),
    else, with ``fallback``, the exact official FUTURE_PX of the day: the reads of
    ``book.level_on`` without the FX, for a ratio."""
    from engine.spreads.book import official_price
    prices, sources = [], []
    for leg in spec.legs:
        leg_rows = [rows[t] for t in leg.trade_ids if t in rows]
        if leg_rows:
            r = next((r for r in leg_rows if _num(r.get("mark")) is not None), None)
            if r is None:
                return None, f"{leg.instrument_id} has no price on {day} ({_why(leg_rows[0])})", ""
            prices.append(float(r["mark"]))
            sources.append(f"{leg.instrument_id} {r.get('mark_source') or 'value_book'}")
        elif fallback:
            hit = official_price(book.conn, leg, day, str(book.by_id[leg.trade_ids[0]]["expiry_date"]))
            if hit is None:
                return None, (f"{leg.instrument_id} was not yet held on the {day} close and has no official "
                              f"FUTURE_PX that day"), ""
            prices.append(hit[0])
            sources.append(f"{leg.instrument_id} {hit[1]} (official FUTURE_PX of the {day} close: not yet held then)")
        else:
            return None, f"{leg.instrument_id} is not valued by value_book on {day}", ""
    return prices, "", "; ".join(sources)


def _entry_prices(book, spec: LevelSpec) -> Tuple[Optional[List[float]], str]:
    """Each leg's lots-weighted average fill."""
    out = []
    for leg in spec.legs:
        num = den = 0.0
        for tid in leg.trade_ids:
            q, f = book.lots(tid), _num(book.by_id[tid]["price"])
            if q is None or f is None:
                return None, f"{tid}: its quantity or fill is not a number, so the entry has no level"
            num += q * f
            den += q
        if abs(den) < 1e-12:
            return None, f"{leg.instrument_id}: its trades net to zero lots, so it has no average fill"
        out.append(num / den)
    return out, ""


def _ratio(prices: Sequence[float], spec: LevelSpec, cn: int) -> Optional[float]:
    """China over foreign of the two raw prices in the template's quantity unit (no FX)."""
    conv = [converted(px, leg, LevelSpec(spec.kind, spec.unit, leg.currency, 0.0, spec.legs, spec.weights,
                                         spec.units_per_lot)) for px, leg in zip(prices, spec.legs)]
    if abs(conv[1 - cn]) < 1e-12:
        return None
    return conv[cn] / conv[1 - cn]


def _difference(book, spec: LevelSpec, prev_day: str, prev_rows: Dict[str, dict]) -> dict:
    """The template's / calendar's / USD-per-unit level at entry, on the previous close and now,
    through ``book.level_on`` and ``book.entry_level`` (the one level rule)."""
    entry, entry_why, entry_src, entry_px = book.entry_level(spec)
    now, now_why, now_src, now_px = book.level_on(spec, book.as_of, book.today, fallback=False)
    prev, prev_why, prev_src, prev_px = book.level_on(spec, prev_day, prev_rows, fallback=True)
    return {"entry": entry, "entry_reason": entry_why, "prev": prev, "prev_reason": prev_why, "now": now,
            "now_reason": now_why, "change": (now - prev) if now is not None and prev is not None else None,
            "sources": {"entry": entry_src, "prev": prev_src, "now": now_src},
            "prices": {"entry": entry_px, "prev": prev_px, "now": now_px}}


def _ratio_levels(book, spec: LevelSpec, cn: int, prev_day: str, prev_rows: Dict[str, dict]) -> dict:
    entry_px, entry_why = _entry_prices(book, spec)
    now_px, now_why, now_src = _leg_prices(book, spec, book.as_of, book.today, fallback=False)
    prev_px, prev_why, prev_src = _leg_prices(book, spec, prev_day, prev_rows, fallback=True)
    out = {"sources": {"entry": "the fills, each leg's lots-weighted average (no FX in a ratio)", "prev": prev_src,
                       "now": now_src}, "prices": {"entry": entry_px, "prev": prev_px, "now": now_px}}
    for key, px, why in (("entry", entry_px, entry_why), ("prev", prev_px, prev_why), ("now", now_px, now_why)):
        value = _ratio(px, spec, cn) if px is not None else None
        out[key] = value
        out[f"{key}_reason"] = why if px is None else ("" if value is not None else "the foreign leg's price is 0")
    out["change"] = (out["now"] - out["prev"]) if out["now"] is not None and out["prev"] is not None else None
    return out


def _levels(book, p: dict, prev_day: str, prev_rows: Dict[str, dict]) -> dict:
    """``unit``, ``level_*``, ``usd_per_unit``, ``level_alt`` / ``unit_alt``, ``level_sources``,
    ``level_prices``, ``level_spec`` of one pair."""
    spec, why = _spec(book, p)
    out = {"unit": "", "level_entry": None, "level_prev": None, "level_now": None, "level_change": None,
           "level_entry_reason": why, "level_prev_reason": why, "level_now_reason": why, "level_change_reason": why,
           "level_prev_date": prev_day, "usd_per_unit": None, "usd_per_unit_reason": why,
           "unit_alt": "", "level_alt": None, "level_alt_reason": why,
           "level_sources": {}, "level_prices": {}, "level_spec": spec_to_dict(spec)}
    if spec is None:
        return out
    a, b = p["a"], p["b"]
    size = min(abs(p["lots_a"]) * spec.units_per_lot[0], abs(p["lots_b"]) * spec.units_per_lot[1])
    direction = _sign(p["lots_a"])
    template = p["template"]
    diff = _difference(book, spec, prev_day, prev_rows)
    s_unit, s_why = book.unit_spot(spec, book.as_of, exact=False)
    if template is not None and template.ratio_screen:
        cn = 0 if a.root.country == CHINA else 1 if b.root.country == CHINA else 0
        ratio = _ratio_levels(book, spec, cn, prev_day, prev_rows)
        out.update(unit=RATIO_UNIT, level_entry=ratio["entry"], level_prev=ratio["prev"], level_now=ratio["now"],
                   level_change=ratio["change"], level_entry_reason=ratio["entry_reason"],
                   level_prev_reason=ratio["prev_reason"], level_now_reason=ratio["now_reason"],
                   level_change_reason="; ".join(w for w in (ratio["now_reason"], ratio["prev_reason"]) if w),
                   unit_alt=spec.unit, level_alt={k: diff[k] for k in ("entry", "prev", "now", "change")},
                   level_alt_reason="; ".join(f"{k}: {diff[k + '_reason']}" for k in ("entry", "prev", "now")
                                              if diff[k + "_reason"]),
                   level_sources={**ratio["sources"], "alt": diff["sources"]},
                   level_prices={"ratio": ratio["prices"], "alt": diff["prices"]})
        # a 1.0 rise of the ratio with the foreign leg unchanged: the China leg moves by the foreign
        # price (in CNY per unit), on the China leg's paired quantity, converted at the day's spot
        cn_leg = spec.legs[cn]
        now_px = ratio["prices"]["now"]
        s_cn = _num(usd_per_quote(book.conn, cn_leg.currency, book.as_of)[0]) if cn_leg.currency != "USD" else 1.0
        if now_px is None:
            out["usd_per_unit_reason"] = ratio["now_reason"] or "no price now"
        elif not s_cn:
            out["usd_per_unit_reason"] = f"no SPOT for USD conversion of {cn_leg.currency} on {book.as_of}"
        else:
            foreign = spec.legs[1 - cn]
            p_for = converted(now_px[1 - cn], foreign, LevelSpec(spec.kind, spec.unit, foreign.currency, 0.0,
                                                                  spec.legs, spec.weights, spec.units_per_lot))
            cn_lots = p["lots_a"] if cn == 0 else p["lots_b"]
            out["usd_per_unit"] = _sign(cn_lots) * size * p_for * s_cn
            out["level_sources"]["usd_per_unit"] = (f"paired {size:g} {p['unit']} x the foreign price {p_for:.6g} "
                                                    f"x {cn_leg.currency} spot {s_cn:.6g}")
        return out
    out.update(unit=spec.unit, level_entry=diff["entry"], level_prev=diff["prev"], level_now=diff["now"],
               level_change=diff["change"], level_entry_reason=diff["entry_reason"],
               level_prev_reason=diff["prev_reason"], level_now_reason=diff["now_reason"],
               level_change_reason="; ".join(w for w in (diff["now_reason"], diff["prev_reason"]) if w),
               level_sources=diff["sources"], level_prices=diff["prices"], level_alt_reason="")
    if s_unit is None:
        out["usd_per_unit_reason"] = s_why
    else:
        out["usd_per_unit"] = usd_per_level_unit(direction * size, spec, s_unit)
        out["level_sources"]["usd_per_unit"] = (f"paired size {size:g} {p['unit']}"
                                                + ("" if spec.currency == "USD" else
                                                   f", {spec.currency} at the {book.as_of} USD spot {s_unit:.6g}"))
    if spec.is_calendar:
        out["level_alt_reason"] = "a calendar has one form: near minus far in the root's quote unit"
    elif template is not None:
        cn = 0 if a.root.country == CHINA else 1 if b.root.country == CHINA else -1
        if cn >= 0:
            ratio = _ratio_levels(book, spec, cn, prev_day, prev_rows)
            out.update(unit_alt=RATIO_UNIT, level_alt={k: ratio[k] for k in ("entry", "prev", "now", "change")},
                       level_alt_reason="; ".join(f"{k}: {ratio[k + '_reason']}" for k in ("entry", "prev", "now")
                                                  if ratio[k + "_reason"]))
        else:
            out["level_alt_reason"] = "neither leg is on a Chinese exchange, so there is no China-over-foreign ratio"
    else:
        out["level_alt_reason"] = "no config/spreads/ template matches these two roots: only USD per unit"
    return out


# ------------------------------------------------------------------ events
def _event_of(book, leg: PLeg) -> Optional[dict]:
    row = book.schedule().get(leg.contract_id)
    if row is None:
        expiry = str(book.by_id[leg.trade_ids[0]]["expiry_date"] or "")
        date = leg.prompt or (expiry if expiry and not expiry.startswith("9999") else "")
        if not date:
            return None
        return {"contract_id": leg.contract_id, "event": "LME prompt" if leg.prompt else "last trade", "date": date,
                "alert_date": date, "business_days": None, "level": "", "estimated": True,
                "reason": (f"{leg.contract_id} nets to zero lots across the book, so the expiry schedule has no row "
                           f"for it: its stored {'prompt' if leg.prompt else 'expiry date'} stands in")}
    return {"contract_id": leg.contract_id, "event": row.get("next_event"), "date": row.get("next_event_date"),
            "alert_date": row.get("alert_date"), "business_days": row.get("business_days"),
            "level": row.get("level"), "estimated": bool(row.get("estimated")), "reason": row.get("reason", "")}


def _next_event(book, a: PLeg, b: PLeg) -> dict:
    ea, eb = _event_of(book, a), _event_of(book, b)
    out = {"next_event": None, "next_event_reason": "", "legs_apart_bd": None, "legs_apart_flag": False,
           "leg_events": [ea, eb]}
    missing = [leg.contract_id for leg, e in ((a, ea), (b, eb)) if e is None or not e.get("date")]
    if missing:
        out["next_event_reason"] = f"{', '.join(missing)}: no row in the expiry schedule on {book.as_of}"
        out["next_event"] = next((e for e in (ea, eb) if e and e.get("date")), None)
        return out
    first, second = (ea, eb) if str(ea["date"]) <= str(eb["date"]) else (eb, ea)
    out["next_event"] = first
    try:
        from engine.calendars import business_days_between
        apart = business_days_between(a.root.calendar, first["date"], second["date"])
    except Exception as exc:  # noqa: BLE001 -- a calendar without coverage: said, not raised
        out["next_event_reason"] = f"business days between the legs' events not counted ({exc})"
        return out
    out["legs_apart_bd"] = int(apart)
    out["legs_apart_flag"] = abs(int(apart)) > LEGS_APART_BD
    return out


# ------------------------------------------------------------------ output rows
def _leg_row(book, leg: PLeg, lots: float, unit: str, upl: float, trade_ids: Sequence[str], weight: float) -> dict:
    return {
        "contract_id": leg.contract_id, "instrument_id": leg.instrument_id, "root_id": leg.root_id,
        "product": leg.product, "contract_month": leg.month, "prompt": leg.prompt, "lots": lots,
        "whole_lots": leg.whole_lots, "units": lots * upl, "unit": unit, "weight": weight,
        "currency": leg.currency, "trade_ids": list(trade_ids), "contract_trade_ids": list(leg.trade_ids),
    }


def _pair_row(book, name: str, n: int, p: dict, alloc: Dict[str, Dict[str, List[str]]], prev_day: str,
              prev_rows: Dict[str, dict]) -> dict:
    a, b = p["a"], p["b"]
    ids_a, ids_b = alloc[a.contract_id].get(f"pair{n}", []), alloc[b.contract_id].get(f"pair{n}", [])
    out = {
        "pair_id": f"{name}|{a.contract_id}|{b.contract_id}", "strategy": name, "n": n,
        "type": p["rule"], "type_source": p["source"], "note": p["note"],
        "template": p["template"].template_id if p["template"] else "",
        "template_name": p["template"].name if p["template"] else "",
        "also_matches": [t.template_id for t in p["also"]],
        "legs": [_leg_row(book, a, p["lots_a"], p["unit"], p["upl_a"], ids_a, 1.0),
                 _leg_row(book, b, p["lots_b"], p["unit"], p["upl_b"], ids_b, -1.0)],
        "trade_ids": sorted(ids_a + ids_b),
        "size": min(abs(p["lots_a"]) * p["upl_a"], abs(p["lots_b"]) * p["upl_b"]), "size_unit": p["unit"],
        "direction": "long" if p["lots_a"] > 0 else "short",
        "residual_units": p["lots_a"] * p["upl_a"] + p["lots_b"] * p["upl_b"], "residual_unit": p["unit"],
    }
    gross = net = 0.0
    whys = []
    for leg, lots in ((a, p["lots_a"]), (b, p["lots_b"])):
        figure, why = book.notional(leg.as_leg(lots, leg.trade_ids), lots if leg.whole_lots
                                    else lots * leg.root.contract_size)
        if figure is None:
            whys.append(why)
        else:
            gross += abs(figure)
            net += figure
    if whys:
        out.update(gross_usd=None, net_usd=None, residual_usd=None, notional_reason="; ".join(whys))
    else:
        out.update(gross_usd=gross, net_usd=net, residual_usd=net, notional_reason="")
    out.update(_levels(book, p, prev_day, prev_rows))
    out.update(_next_event(book, a, b))
    return out


def _residual_row(book, leg: PLeg, lots: float, trade_ids: Sequence[str]) -> dict:
    qty = lots if leg.whole_lots else lots * leg.root.contract_size
    figure, why = book.notional(leg.as_leg(lots, leg.trade_ids), qty)
    return {
        "contract_id": leg.contract_id, "instrument_id": leg.instrument_id, "root_id": leg.root_id,
        "product": leg.product, "contract_month": leg.month, "prompt": leg.prompt, "lots": lots,
        "whole_lots": leg.whole_lots, "units": leg.physical(lots, leg.size_unit), "unit": leg.size_unit,
        "currency": leg.currency, "trade_ids": list(trade_ids), "contract_trade_ids": list(leg.trade_ids),
        "gross_usd": None if figure is None else abs(figure), "net_usd": figure, "notional_reason": why,
        "why": "left after the pairs" if abs(lots - leg.lots) > _EPS else "no leg to pair with",
        "next_event": _event_of(book, leg),
    }


def _hedge_rows(book, tids: Sequence[str]) -> List[dict]:
    """One row per hedge contract (an FX product: per pair and value date), net over its OPEN
    trades (a settled one is listed, adds no lots)."""
    acc: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for tid in tids:
        t = book.by_id[tid]
        acc[(t["instrument_id"], str(t["leg_date"] or "") if t["product"] in FX_PRODUCTS else "")].append(tid)
    return [_hedge_row(book, ids) for _key, ids in sorted(acc.items())]


def _fx_usd_leg(t: dict, quantity: float) -> Optional[float]:
    """The USD leg of an FX fill, signed (long USD positive): the base amount on a USD-based
    pair, minus base x outright on a USD-quoted pair; None when the pair has no USD leg."""
    if str(t["base_ccy"]) == "USD":
        return quantity
    if str(t["quote_ccy"]) == "USD":
        price = _num(t["price"])
        return None if price is None else -quantity * price
    return None


def _official(book, instrument_id: str, mark_type: str) -> Optional[float]:
    row = book.conn.execute(
        "SELECT value FROM marks_official WHERE instrument_id = ? AND mark_type = ? AND as_of_date = ? "
        "ORDER BY snapped_at DESC LIMIT 1", (instrument_id, mark_type, book.as_of)).fetchone()
    return _num(row[0]) if row else None


def _fx_option_usd(book, open_ids: Sequence[str]) -> Tuple[Optional[float], str]:
    """(the USD leg of the open FX options' delta, signed long USD positive, why when None): per
    option, base delta = notional x its official DELTA mark of ``as_of`` (exact, never estimated,
    as every exposure delta); on a USD-based pair that is the USD leg, on a USD-quoted pair the
    USD leg is minus base delta x the pair's official SPOT of ``as_of``; a pair with no USD leg,
    or a DELTA or SPOT not on file, gives None with the sentence (never a partial sum)."""
    total, whys = 0.0, []
    for tid in open_ids:
        t = book.by_id[tid]
        qty, delta = book.lots(tid), _official(book, t["instrument_id"], "DELTA")
        base, quote = str(t["base_ccy"] or ""), str(t["quote_ccy"] or "")
        if delta is None:
            whys.append(f"{t['instrument_id']}: no official DELTA on {book.as_of}, so the option's hedge size is "
                        f"not known")
        elif base == "USD":
            total += qty * delta
        elif quote == "USD":
            spot = _official(book, base + quote, "SPOT")
            if spot is None:
                whys.append(f"{t['instrument_id']}: no official {base}{quote} SPOT on {book.as_of} for the USD leg "
                            f"of its delta")
            else:
                total += -qty * delta * spot
        else:
            whys.append(f"{t['instrument_id']}: the pair has no USD leg, so the option's USD hedge size is not read")
    return (None, "; ".join(whys)) if whys else (total, "")


def _hedge_row(book, tids: Sequence[str]) -> dict:
    t = book.by_id[tids[0]]
    root = book.roots.get(t["base_ccy"])
    open_ids = [i for i in tids if book.is_open(i)]
    bad = [i for i in open_ids if book.lots(i) is None]
    lots = sum(book.lots(i) or 0.0 for i in open_ids if i not in bad)
    out = {"trade_ids": sorted(tids), "open_trade_ids": sorted(open_ids), "instrument_id": t["instrument_id"],
           "root_id": str(t["base_ccy"] or ""), "product": t["product"], "contract_month": str(t["month_key"] or ""),
           "lots": lots, "currency": str(t["quote_ccy"] or ""), "usd_notional": None, "notional_reason": ""}
    if bad:
        out["notional_reason"] = (f"{', '.join(bad)}: trades.quantity is not a number, so the hedge's lots and "
                                  f"USD notional are not known")
    elif t["product"] == "FX_OPTION":
        out["usd_notional"], out["notional_reason"] = _fx_option_usd(book, [i for i in open_ids if i not in bad])
    elif t["product"] in FX_PRODUCTS:
        usd = [_fx_usd_leg(book.by_id[i], book.lots(i)) for i in open_ids if i not in bad]
        if any(u is None for u in usd):
            out["notional_reason"] = (f"{t['instrument_id']}: the pair has no USD leg, so the hedge's USD notional "
                                      f"is not read from its fill")
        else:
            out["usd_notional"] = float(sum(usd))           # the USD leg's signed amount, long USD positive
    elif root is not None and root.size_unit.strip().upper() == "USD":
        out["usd_notional"] = lots * root.contract_size          # a USD/CNH future: lots x its USD size
    else:
        leg = Leg(t["instrument_id"], str(t["base_ccy"] or ""), str(t["account"]), str(t["trade_date"]), lots,
                  tuple(tids), str(t["month_key"] or ""))
        out["usd_notional"], out["notional_reason"] = book.notional(leg, lots)
    return out


CNY_CCYS = ("CNY", "CNH")


def _cny_hedge(h: dict) -> bool:
    """A hedge of the CNY legs: a USD/CNH future or an FX product on a CNH / CNY pair."""
    return str(h.get("currency") or "") in CNY_CCYS or str(h.get("root_id") or "") in CNY_CCYS


def _coverage(book, legs: List[PLeg], hedges: List[dict]) -> dict:
    """The CNY hedges' USD notional against the CNY legs' notional (open lots, ``book.notional``).
    ``hedge_usd`` is every hedge of the strategy (None when one has no USD figure); the coverage,
    ``unhedged_cny`` and the sign warning read ``hedge_cny_usd``, the hedges on a CNH / CNY pair
    only (2026-09-29: a EURUSD option or forward in the strategy covers no China leg)."""
    out = {"hedge_usd": None, "hedge_cny_usd": None, "cny_gross_usd": None, "cny_net_usd": None,
           "hedge_coverage": None, "hedge_coverage_net": None, "unhedged_cny": None, "hedge_reason": ""}
    cny = [leg for leg in legs if leg.currency in CNY_CCYS]
    if not hedges and not cny:
        out["hedge_reason"] = "no CNY legs and no hedge in this strategy"
        return out
    if not any(h["usd_notional"] is None for h in hedges):
        out["hedge_usd"] = float(sum(h["usd_notional"] for h in hedges))
    cny_hedges = [h for h in hedges if _cny_hedge(h)]
    whys = [h["notional_reason"] for h in cny_hedges if h["usd_notional"] is None]
    hedge_usd = float(sum(h["usd_notional"] for h in cny_hedges if h["usd_notional"] is not None))
    if not whys:
        out["hedge_cny_usd"] = hedge_usd
    if not cny:
        out["hedge_reason"] = "; ".join(whys) or "no CNY legs in this strategy: nothing for the hedge to cover"
        return out
    gross = net = 0.0
    for leg in cny:
        qty = leg.lots if leg.whole_lots else leg.lots * leg.root.contract_size
        figure, why = book.notional(leg.as_leg(leg.lots, leg.trade_ids), qty)
        if figure is None:
            whys.append(why)
        else:
            gross += abs(figure)
            net += figure
    if whys:
        out["hedge_reason"] = "; ".join(whys)
        return out
    # a long China leg is long USDCNH at its notional: its hedge is a SHORT USD/CNH position, so the
    # right hedge has the opposite sign to the CNY net (user, 2026-09-28, from Jason's own fills)
    out.update(cny_gross_usd=gross, cny_net_usd=net, unhedged_cny=net + hedge_usd)
    out["hedge_coverage"] = abs(hedge_usd) / gross if gross > _EPS else None
    out["hedge_coverage_net"] = (-hedge_usd / net) + 0.0 if abs(net) > _EPS else None   # + 0.0: never -0.0
    if abs(net) <= _EPS:
        out["hedge_reason"] = "the CNY legs net to zero notional: nothing left for the hedge to cover"
    elif hedge_usd * net > 0:
        out["hedge_reason"] = ("the hedge runs with the CNY exposure, not against it (a long China leg needs a "
                               "short USD/CNH hedge: its CNY price rises with USDCNH)")
    return out


def _prev_rows(book, sid: str) -> Tuple[str, Dict[str, dict]]:
    """(the Daily's reference close, its rows by trade id) for ``level_prev``: the rows the
    strategy's Daily was measured from; with none kept (the strategy unpriced today) the raw
    records of the period's own close, or nothing when that close cannot be read (a leg then
    reads the official marks of the day, or says why not)."""
    daily = book.daily.get(sid) or {}
    day = daily.get("date") or book.refs["daily"]
    rows = daily.get("rows")
    if rows is None:
        try:
            rows = {r["trade_id"]: r for r in book.frames.records(day)}
        except Exception:  # noqa: BLE001 -- a close that cannot be valued: the level says so per leg
            rows = {}
    return day, rows


SPLIT_COMPONENTS = ("spread", "fx", "hedge", "new_trades", "realised", "other")


def _split(book, sid: str, tids: Sequence[str], spread: dict) -> dict:
    """The strategy's Daily split by the P&L explain's one bucket order (``period_explain.
    classify_trades``, user yes 2026-09-29: a trade dealt today is ``new_trades``, one settled
    today ``realised``, then ``hedge``, then ``spread`` / ``fx``, and a trade that cannot be split
    ``other``, on both screens alike), over ``period_pnl``'s per-trade figures from the very rows
    the strategy's Daily was measured from (its own reference close, as the Daily chose it), only
    when that Daily exists (then every trade is included and ``total`` equals it to the cent);
    otherwise the empty split with ``reason`` (never a partial figure standing in for the Daily)."""
    from engine.pnl.series import DailySeries, period_pnl
    from engine.pnl.valuation import COLUMNS
    from engine.spreads.period_explain import classify_trades
    day, _rows = _prev_rows(book, sid)
    daily_value = (spread.get("pnl_usd") or {}).get("daily")
    empty = {**{k: 0.0 for k in SPLIT_COMPONENTS}, "total": 0.0, "excluded": [], "reasons": {}, "included": [],
             "by_trade": {}, "date": day, "reason": ""}
    if daily_value is None:
        why = (spread.get("pnl_reasons") or {}).get("daily") or "no Daily P&L"
        empty["reason"] = f"the strategy's Daily is not available ({why}), so it is not split"
        empty["excluded"] = [(t, why) for t in sorted(tids)]
        return empty
    rows = (book.daily.get(sid) or {}).get("rows")
    if rows is None:
        empty["reason"] = "the rows the Daily was measured from were not kept, so it is not split"
        return empty
    try:
        ids = frozenset(tids)
        ref = pd.DataFrame(list(rows.values())) if rows else pd.DataFrame(columns=list(COLUMNS))
        end = book.frames.scoped(book.as_of, ids)
        frames = {day: ref[ref["trade_id"].isin(ids)] if len(ref) else ref, book.as_of: end}
        series = DailySeries(book.as_of, tuple(sorted(frames)), frames, frozenset(book.holidays), None, 0, 0.0)
        pp = period_pnl(series, day, book.as_of)
        if pp.ref_used != day or not pp.available or pp.total is None:
            empty["reason"] = (f"the Daily's reference close {day} did not give the split its figures "
                               f"({pp.reason or f'measured from {pp.ref_used}'}), so it is not split")
            return empty
        insts = {str(book.by_id[t]["instrument_id"]): (str(book.by_id[t]["base_ccy"] or ""),
                                                       str(book.by_id[t]["quote_ccy"] or "")) for t in ids}
        parts, bucket, other = classify_trades(pp, book.roots, insts, ltd=False)
    except Exception as exc:  # noqa: BLE001 -- said on the entry, never a failure of book_spreads
        empty["reason"] = f"the Daily could not be split ({type(exc).__name__}: {exc})"
        return empty
    by_trade = {t: {k: p[k] for k in SPLIT_COMPONENTS} for t, p in sorted(parts.items())}
    split = {**{k: float(sum(p[k] for p in by_trade.values())) for k in SPLIT_COMPONENTS},
             "total": float(sum(p["_amount"] for p in parts.values())), "excluded": list(pp.excluded),
             "reasons": {o["trade_id"]: o["why"] for o in other}, "included": sorted(parts),
             "by_trade": by_trade, "bucket": dict(sorted(bucket.items())), "date": day, "reason": ""}
    if split["excluded"] or abs(split["total"] - float(daily_value)) > 0.005:
        split["reason"] = (f"the split's total {split['total']:.2f} is not the strategy's Daily "
                           f"{float(daily_value):.2f}: {len(split['excluded'])} trade(s) excluded")
    return split


def strategy_entry(book, name: str, tids: Sequence[str], spread: Optional[dict]) -> dict:
    """One strategy: ``name`` ('' for the unlabelled trades), its pairs, residuals and hedges."""
    tids = sorted(tids)
    sid = f"STRATEGY-{name}"
    if spread is None:
        spread = book.base(sid, name, "strategy", tids)
        spread["legs"] = book.leg_rows(tids)
        from engine.spreads.book import type_legs
        spread.update(type_fields(spread["type_labels"], type_legs(spread["legs"]), book.roots, None))
        spread.update(book.gross_net(tids))
    legs, hedge_ids, options, closed = _build_legs(book, tids)
    labelled = str(spread.get("trade_type") or "")
    pairs_raw, rem, notes = _pair_all(book, legs, labelled, need_template=(name == ""))
    alloc = _allocate(legs, pairs_raw, rem)
    prev_day, prev_rows = _prev_rows(book, sid)
    pairs = [_pair_row(book, name, n, p, alloc, prev_day, prev_rows) for n, p in enumerate(pairs_raw)]
    residuals = [_residual_row(book, leg, rem[leg.contract_id], alloc[leg.contract_id].get("residual", []))
                 for leg in legs if abs(rem[leg.contract_id]) > _EPS]
    residuals += options
    hedges = _hedge_rows(book, hedge_ids)
    split = _split(book, sid, tids, spread)
    return {
        "name": name, "spread_id": sid, "trade_ids": tids, "closed_trade_ids": closed,
        "type": labelled, "type_source": spread.get("type_source", ""), "type_note": spread.get("type_note", ""),
        "pb_roots": spread.get("pb_roots", []), "type_labels": spread.get("type_labels", []),
        "pairs": pairs, "residuals": residuals, "hedges": hedges, "notes": notes,
        "gross_usd": spread.get("gross_usd"), "net_usd": spread.get("net_usd"),
        "notional_reason": spread.get("notional_reason", ""),
        **_coverage(book, legs, hedges),
        "pnl_usd": spread.get("pnl_usd"), "pnl_reasons": spread.get("pnl_reasons"), "pnl_notes": spread.get("pnl_notes"),
        "ref_dates": spread.get("ref_dates"), "daily": split,
    }


def strategies_from(book, spreads: Sequence[dict]) -> List[dict]:
    """``book_spreads``' ``strategies``: one entry per strategy name (the kind-``strategy``
    spreads, by name) and one named '' for every trade that carries no strategy, last."""
    by_name = {s["name"]: s for s in spreads if s.get("kind") == "strategy"}
    out = [strategy_entry(book, name, s["trade_ids"], s) for name, s in sorted(by_name.items())]
    unlabelled = [t["trade_id"] for t in book.trades if not t["strategy"]]
    if unlabelled:
        out.append(strategy_entry(book, "", unlabelled, None))
    return out
