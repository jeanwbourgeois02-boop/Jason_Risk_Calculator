"""The automatic grouping rule, as pure functions over legs (no database, no valuation).

The rule itself is written once, in the package docstring (``engine/spreads/__init__.py``).
Here a *leg* is one contract's net lots in one (account, trade date) group; a *shape* is what a
set of legs is matched against: a template of ``config/spreads/`` or a calendar (two months of
one root, weights +1 on the nearer month and -1 on the farther, sized in lots).

Matching: every leg's size in the shape's quantity unit, divided by the shape's weight for it,
gives the spread size that leg implies, ``k_i = lots_i x units_per_lot_i / weight_i``. The legs
fit when every ``k_i`` has the same sign; they match when the implied sizes also agree within
``TOLERANCE``: ``(max |k| - min |k|) / max |k| <= 5 %``. The spread's size is the smallest,
``sign x min |k|``, so no leg is counted for more than it holds; what each leg holds beyond that
is its leftover outright, ``lots_i - k x weight_i / units_per_lot_i``.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from engine.spreads.templates import Template

TOLERANCE = 0.05            # implied spread sizes agree within 5 % (the brief's rule, 2026-09-24)
CALENDAR = "calendar"
_EPS = 1e-9
LEFTOVER_FLOOR = 1e-4       # lots: below a ten-thousandth of a contract a leftover is rounding


@dataclass(frozen=True)
class Leg:
    """One contract's net lots in one group (a trade belongs to exactly one leg)."""

    instrument_id: str
    root_id: str
    account: str
    trade_date: str
    lots: float
    trade_ids: Tuple[str, ...]
    month_key: str            # '2026-12' (contract month), else the expiry date: orders a calendar

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.account, self.trade_date, self.instrument_id)


@dataclass(frozen=True)
class Shape:
    kind: str                           # 'calendar' or the template id
    name: str
    family: str
    unit: str                           # the spread's price unit ('USD/bbl'; a calendar: the root's)
    quantity_unit: str                  # what `size` is counted in ('bbl'; a calendar: 'lots')
    weights: Tuple[float, ...]
    units_per_lot: Tuple[float, ...]
    order: int                          # label tie-break: a calendar first, then file order


@dataclass(frozen=True)
class Match:
    shape: Shape
    legs: Tuple[Leg, ...]               # aligned with shape.weights
    implied: Tuple[float, ...]          # k_i per leg
    size: float                         # sign x min |k_i|
    deviation: float                    # (max |k| - min |k|) / max |k|
    also: Tuple[Shape, ...] = field(default=())   # other shapes that group the same legs

    @property
    def leg_keys(self) -> frozenset:
        return frozenset(leg.key for leg in self.legs)

    @property
    def trade_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(t for leg in self.legs for t in leg.trade_ids))

    @property
    def matched(self) -> bool:
        return self.deviation <= TOLERANCE + _EPS

    @property
    def accounts(self) -> Tuple[str, ...]:
        return tuple(sorted({leg.account for leg in self.legs}))


def fit(lots: Sequence[float], weights: Sequence[float], units_per_lot: Sequence[float]
        ) -> Optional[Tuple[float, float, Tuple[float, ...]]]:
    """(size, deviation, implied sizes), or None when a leg is flat or the signs disagree with
    the weights."""
    implied = tuple(q * u / w for q, w, u in zip(lots, weights, units_per_lot))
    if not implied or any(abs(k) < _EPS for k in implied):
        return None
    if not (all(k > 0 for k in implied) or all(k < 0 for k in implied)):
        return None
    mags = [abs(k) for k in implied]
    sign = 1.0 if implied[0] > 0 else -1.0
    return sign * min(mags), (max(mags) - min(mags)) / max(mags), implied


def leftover_lots(lots: Sequence[float], shape: Shape) -> Tuple[float, ...]:
    """What each leg holds beyond the largest whole spread its lots make (all of it when a leg is
    flat or the signs no longer fit, e.g. once one leg has expired)."""
    fitted = fit(lots, shape.weights, shape.units_per_lot)
    size = 0.0 if fitted is None else fitted[0]
    return tuple(_clean(q - size * w / u) for q, w, u in zip(lots, shape.weights, shape.units_per_lot))


def _clean(x: float) -> float:
    """A leftover under ``LEFTOVER_FLOOR`` lots is the templates' rounded weights (0.666667 for
    two thirds leaves 1.5e-6 of a lot on a 3-2-1 crack), shown as 0."""
    return 0.0 if abs(x) < LEFTOVER_FLOOR else round(x, 9)


def template_shape(t: Template) -> Shape:
    return Shape(kind=t.template_id, name=t.name, family=t.family, unit=t.unit, quantity_unit=t.quantity_unit,
                 weights=tuple(leg.weight for leg in t.legs),
                 units_per_lot=tuple(float(leg.units_per_lot) for leg in t.legs), order=t.order + 1)


def calendar_shape(near: Leg, far: Leg, quote_unit: str, code: str) -> Shape:
    return Shape(kind=CALENDAR, name=f"{code} {_label(near)}/{_label(far)} calendar", family=CALENDAR,
                 unit=quote_unit, quantity_unit="lots", weights=(1.0, -1.0), units_per_lot=(1.0, 1.0), order=0)


_MONTH_CODES = "FGHJKMNQUVXZ"


def _label(leg: Leg) -> str:
    """'Z26' for a leg in December 2026; the instrument id when its month is not known."""
    try:
        year, month = leg.month_key.split("-")[:2]
        return f"{_MONTH_CODES[int(month) - 1]}{year[-2:]}"
    except (ValueError, IndexError):
        return leg.instrument_id


def _match(shape: Shape, legs: Sequence[Leg]) -> Optional[Match]:
    fitted = fit([leg.lots for leg in legs], shape.weights, shape.units_per_lot)
    if fitted is None:
        return None
    size, dev, implied = fitted
    return Match(shape, tuple(legs), implied, size, dev)


def candidates(legs: Iterable[Leg], templates_by_root: Dict[str, List[Template]],
               roots_meta: Dict[str, Tuple[str, str]]) -> Tuple[List[Match], List[str]]:
    """Every calendar and template whose legs are all present with signs that fit, matched or
    not (``Match.matched``), and one sentence per template that the roots present call for but
    that cannot be sized (a unit it cannot convert). ``roots_meta``: root_id -> (quote unit,
    exchange code), for the calendars' unit and name."""
    by_root: Dict[str, List[Leg]] = defaultdict(list)
    for leg in legs:
        by_root[leg.root_id].append(leg)
    out: List[Match] = []
    problems: List[str] = []
    for root_id, root_legs in sorted(by_root.items()):
        ordered = sorted(root_legs, key=lambda leg: (leg.month_key, leg.instrument_id))
        for near, far in itertools.combinations(ordered, 2):
            if near.instrument_id == far.instrument_id or near.month_key == far.month_key:
                continue
            quote_unit, code = roots_meta.get(root_id, ("", root_id))
            m = _match(calendar_shape(near, far, quote_unit, code), (near, far))
            if m is not None:
                out.append(m)
    seen = set()
    present = set(by_root)
    for root_id in sorted(by_root):
        for t in templates_by_root.get(root_id, ()):
            if t.template_id in seen:
                continue
            seen.add(t.template_id)
            if not set(t.roots) <= present:
                continue
            if not t.usable:
                problems.append(f"template {t.template_id} ({t.name}) not used: {t.problem}")
                continue
            shape = template_shape(t)
            for combo in itertools.product(*(by_root[r] for r in t.roots)):
                if len({leg.key for leg in combo}) < len(combo):
                    continue
                m = _match(shape, combo)
                if m is not None:
                    out.append(m)
    return out, problems


def dedupe(matches: Iterable[Match], closest_first: bool = False) -> List[Match]:
    """One match per set of legs: the first shape (a calendar, then file order) labels it and the
    others are kept under ``also``. The same trades grouped under two names is one spread.
    ``closest_first`` (the review lists): the shape whose ratio the lots are nearest labels it."""
    groups: Dict[frozenset, List[Match]] = {}
    order = (lambda m: (round(m.deviation, 9), m.shape.order)) if closest_first else (
        lambda m: (m.shape.order, m.deviation))
    for m in sorted(matches, key=order):
        groups.setdefault(m.leg_keys, []).append(m)
    out = []
    for ms in groups.values():
        first = ms[0]
        out.append(Match(first.shape, first.legs, first.implied, first.size, first.deviation,
                         tuple(m.shape for m in ms[1:])))
    return sorted(out, key=lambda m: (m.trade_ids, m.shape.order))


def widest(matches: Sequence[Match]) -> List[Match]:
    """The review hints without those whose legs all belong to a wider hint (an RBOB crack inside
    a 3-2-1 crack's legs): the one using the most legs is the one to show."""
    return [m for m in matches if not any(m.leg_keys < other.leg_keys for other in matches)]


def split_conflicts(matches: Sequence[Match]) -> Tuple[List[Match], List[List[Match]]]:
    """(clean, conflicts): matches sharing no leg with another, and the sets of matches that
    overlap (connected through shared legs): two ways to group the same trades, for review."""
    parent = list(range(len(matches)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in itertools.combinations(range(len(matches)), 2):
        if matches[i].leg_keys & matches[j].leg_keys:
            parent[find(i)] = find(j)
    comps: Dict[int, List[Match]] = defaultdict(list)
    for i, m in enumerate(matches):
        comps[find(i)].append(m)
    clean = [c[0] for c in comps.values() if len(c) == 1]
    conflicts = [c for c in comps.values() if len(c) > 1]
    return clean, conflicts


def best_cover(legs: Sequence[Leg], templates_by_root: Dict[str, List[Template]],
               roots_meta: Dict[str, Tuple[str, str]]) -> Optional[Match]:
    """For a hand-made group (bundle or pin): the calendar or template that uses every one of its
    legs with signs that fit, closest ratio first, then the label order; None when none does. Only
    sizes the group's leftover: it never decides which trades are in the group."""
    matches, _problems = candidates(legs, templates_by_root, roots_meta)
    covering = [m for m in matches if len(m.legs) == len(legs)]
    if not covering:
        return None
    return min(covering, key=lambda m: (round(m.deviation, 9), m.shape.order))
