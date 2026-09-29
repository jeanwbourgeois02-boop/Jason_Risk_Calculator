"""The leftover of the curve: the part of each commodity's net that belongs to no spread.

Read, never re-derived, from spreads-engine's strategies (``engine.spreads.book_spreads(conn,
as_of)["strategies"]``: one entry per strategy name and one named '' for the trades that carry
none, so every open lot of the book is in exactly one pair, residual or hedge):

- a **residual** of a strategy (a future or LME prompt with no leg to pair with, or the whole
  lots left after the pairs) is leftover in its own contract month, in full;
- a **pair** (two legs or a one-spread of many) whose sides do not balance carries its leftover
  as the spreads engine placed it: ``leftover_legs`` (root, month, lots, on the heavier side's
  front leg), summed per (root, month); its sizing unit (USD for value, physical, lots) is the
  engine's business, never re-read here. ``leftover_lots`` None (with ``leftover_reason``) spoils
  every root of the pair at its front month with that reason;
- an **option** residual (an option is never paired) is leftover at its delta: the net option
  lots per instrument x the curve row's ``delta_factor`` (the official DELTA mark), in its
  underlying's month; no DELTA mark, no figure;
- a **hedge** (a USD/CNH future, an FX spot / forward / swap) is never commodity leftover.

The leftover lots are delta lots like the grid's: a monthly-average contract's lots shrink by
its averaging share (the curve row's ``delta_factor``, else ``engine.curve.averaging``), an LME
prompt counts its tonnes over the lot size. Each (root, month) is reconciled first: the lots the
strategies account for (pair legs plus residuals) must equal the curve's own net lots there,
else that cell is None with both figures named, never a partial sum.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from engine.curve.averaging import averaging_factor
from engine.curve.rows import FUTURE, LME, OPTION, month_key, number

_TOL = 1e-6        # lots: the strategies and the curve agree within this
_TINY = 1e-9       # a residual below this is an exact match
_FX_SECTOR = "fx"
_LOTS, _OPT = "lots", "option"


class Leftover:
    """Per root: ``months`` {'YYYY-MM': leftover delta lots or None}, ``reasons`` {month or '':
    [why]} and ``unplaced`` (reasons for a leftover that has no month: the root's total is None)."""

    def __init__(self) -> None:
        self.months: Dict[str, Dict[str, Optional[float]]] = defaultdict(dict)
        self.reasons: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        self.unplaced: Dict[str, List[str]] = defaultdict(list)
        self.failure = ""        # the spreads could not be read at all: every leftover is None

    def add(self, root_id: str, month: Optional[str], lots: Optional[float], why: str = "") -> None:
        if month is None:
            self.unplaced[root_id].append(why or "a leftover with no contract month")
            return
        cells = self.months[root_id]
        if lots is None or (month in cells and cells[month] is None):
            cells[month] = None
            if why:
                self.reasons[root_id][month].append(why)
        else:
            cells[month] = cells.get(month, 0.0) + lots

    def spoil(self, root_id: str, month: Optional[str], why: str) -> None:
        self.add(root_id, month, None, why)


def row_month(row: dict) -> Optional[str]:
    """A curve row's 'YYYY-MM', None when it has no contract month."""
    return month_key(row["year"], row["month"]) if row["year"] is not None else None


def _curve_lots(rows: List[dict]) -> Dict[Tuple[str, Optional[str], str], float]:
    """The curve's own net lots per (root, month, futures-or-option)."""
    out: Dict[Tuple[str, Optional[str], str], float] = defaultdict(float)
    for r in rows:
        out[(r["root_id"], row_month(r), _OPT if r["product"] == OPTION else _LOTS)] += r["lots"]
    return out


def _factor(root, product: str, contract_id: str, month: str, by_contract: Dict[str, dict],
            as_of: str) -> Tuple[Optional[float], str]:
    """(delta factor, why when None) of one lot of a future or LME prompt."""
    if product == LME or root is None or not root.averaging:
        return 1.0, ""
    row = by_contract.get(contract_id)
    if row is not None and row["delta_factor"] is not None:
        return row["delta_factor"], ""
    try:
        year, mon = int(month[:4]), int(month[5:7])
    except (TypeError, ValueError):
        return None, f"{contract_id}: an averaging contract with no contract month"
    factor, _note, why = averaging_factor(root, mon, year, as_of)
    return factor, why


def _spoil_pair(pair: dict, legs: List[dict], out: Leftover, why: str) -> None:
    """A pair whose leftover is not known: each root it holds is None at its front month there
    (the leftover could sit on either side, so neither root's total is known)."""
    front: Dict[str, Optional[str]] = {}
    for leg in legs:
        root_id, month = str(leg.get("root_id") or ""), leg.get("contract_month") or None
        if root_id not in front or (month is not None and (front[root_id] is None or month < front[root_id])):
            front[root_id] = month
    text = f"pair {pair.get('pair_id', '')}: {why}"
    for root_id, month in (front.items() if front else [("", None)]):
        out.spoil(root_id, month, text)


def _pair_leftover(pair: dict, legs: List[dict], out: Leftover,
                   raw: List[Tuple[str, str, str, str, float]]) -> None:
    """The spreads engine's own leftover of one pair (``leftover_lots`` on ``leftover_legs``),
    added to ``raw`` in each leg's month; not known, the pair's cells are spoilt with its reason."""
    total = number(pair.get("leftover_lots"))
    if total is None:
        _spoil_pair(pair, legs, out, pair.get("leftover_reason") or "its leftover is not known")
        return
    if abs(total) <= _TINY:
        return
    placed = pair.get("leftover_legs") or []
    if not placed:
        _spoil_pair(pair, legs, out, f"a leftover of {total:g} lots with no leg named")
        return
    for leg in placed:
        lots = number(leg.get("lots"))
        month = leg.get("contract_month") or None
        if lots is None or month is None:
            _spoil_pair(pair, legs, out, f"{leg.get('contract_id', '')}: its leftover "
                                         + ("has no contract month" if lots is not None else "is not a number"))
            return
    for leg in placed:
        raw.append((str(leg["root_id"]), str(leg["contract_month"]), str(leg.get("product") or FUTURE),
                    str(leg.get("contract_id") or ""), number(leg.get("lots"))))


def leftover_by_root(rows: List[dict], strategies: List[dict], roots: dict, as_of: str) -> Leftover:
    """The leftover delta lots per root and month from spreads-engine's ``strategies``, reconciled
    against the curve's ``rows`` (see the module docstring)."""
    out = Leftover()
    by_contract = {r["contract_id"]: r for r in rows if r["product"] in (FUTURE, LME)}
    option_rows: Dict[str, dict] = {}
    for r in rows:
        if r["product"] == OPTION:
            option_rows.setdefault(r["instrument_id"], r)
    accounted: Dict[Tuple[str, Optional[str], str], float] = defaultdict(float)
    raw: List[Tuple[str, str, str, str, float]] = []     # (root, month, product, contract, leftover lots)
    option_net: Dict[str, Tuple[str, float]] = {}        # instrument -> (root, net residual option lots)

    for entry in strategies:
        for pair in entry.get("pairs") or []:
            legs = pair.get("legs") or []
            for leg in legs:
                lots = number(leg.get("lots"))
                if lots is not None:
                    accounted[(str(leg["root_id"]), leg.get("contract_month") or None, _LOTS)] += lots
            _pair_leftover(pair, legs, out, raw)
        for res in entry.get("residuals") or []:
            root_id, product = str(res.get("root_id") or ""), str(res.get("product") or "")
            if root_id not in roots or roots[root_id].sector == _FX_SECTOR:
                continue       # an FX option, a leftover product: not a commodity lot
            lots = number(res.get("lots"))
            if product == OPTION:
                inst = str(res.get("instrument_id") or "")
                have = option_net.get(inst, (root_id, 0.0))
                if lots is None:
                    out.spoil(root_id, row_month(option_rows[inst]) if inst in option_rows else None,
                              f"option {res.get('trade_id', '')}: its quantity is not a number")
                    continue
                option_net[inst] = (root_id, have[1] + lots)
                continue
            month = res.get("contract_month") or None
            if lots is None:
                out.spoil(root_id, month, f"{res.get('contract_id') or res.get('instrument_id')}: "
                                          f"{res.get('why') or 'its lots are not a number'}")
                continue
            accounted[(root_id, month, _LOTS)] += lots
            if month is None:
                out.spoil(root_id, None, f"{res.get('contract_id')}: a residual with no contract month")
                continue
            raw.append((root_id, month, product, str(res.get("contract_id") or ""), lots))

    # Reconcile the futures and LME lots: the strategies must account for every lot of the curve.
    curve = _curve_lots(rows)
    commodity = [k for k in set(k for k in curve if k[2] == _LOTS) | set(accounted)
                 if not (k[0] in roots and roots[k[0]].sector == _FX_SECTOR)]     # a hedge is never leftover
    for key in sorted(commodity, key=lambda k: (k[0], k[1] or "")):
        have, want = accounted.get(key, 0.0), curve.get(key, 0.0)
        if abs(have - want) > _TOL:
            root_id, month, _kind = key
            out.spoil(root_id, month, f"{root_id} {month or '(no month)'}: the spreads account for {have:g} lots, "
                                      f"the curve holds {want:g}")
    for root_id, month, product, contract_id, lots in raw:
        factor, why = _factor(roots.get(root_id), product, contract_id, month, by_contract, as_of)
        if factor is None:
            out.spoil(root_id, month, why)
        else:
            out.add(root_id, month, lots * factor)

    # Options: the net residual per instrument at the curve row's delta.
    for inst, (root_id, lots) in sorted(option_net.items()):
        row = option_rows.get(inst)
        want = sum(r["lots"] for r in rows if r["product"] == OPTION and r["instrument_id"] == inst)
        if abs(lots - want) > _TOL:
            out.spoil(root_id, row_month(row) if row else None,
                      f"{inst}: the spreads hold {lots:g} option lots outright, the curve {want:g}")
            continue
        if abs(lots) <= _TOL:
            continue
        month = row_month(row)
        if month is None:
            out.spoil(root_id, None, f"{inst}: {row['reason'] or 'no contract month'}")
        elif row["delta_factor"] is None:
            out.spoil(root_id, month, f"{inst}: {row['reason'] or 'no delta'}")
        else:
            out.add(root_id, month, lots * row["delta_factor"])
    # An option row of the curve with no option residual behind it: not accounted for.
    for inst, row in option_rows.items():
        if inst not in option_net and row["root_id"] in roots:
            out.spoil(row["root_id"], row_month(row),
                      f"{inst}: the spreads hold none of its {row['lots']:g} option lots, the curve does")
    return out
