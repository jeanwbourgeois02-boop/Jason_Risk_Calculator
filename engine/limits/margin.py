"""``margin_estimate(conn, as_of)``: an ESTIMATE of the initial margin the commodity book ties up.

Not the exchange's SPAN and not the clearer's figure: every result carries ``basis`` =
``BASIS``. The rates are the user's, in ``config/limits.yaml`` (``engine.limits.config``); a
commodity with none is n/a with that reason, never charged at a made-up rate.

The rule, per (contract root, contract month) cell of ``engine.limits.cells`` (futures, LME
forwards and options at their delta, netted within the month; margin follows exposure, so an
option is charged on its delta, which understates what a short option really costs):

1. **Outright charge** = |net delta USD| x the root's outright rate (a fraction; the root's own
   rate, else its sector's), or |net delta lots| x the root's USD per lot when that is set. Months
   are never netted against each other here: that offset is what the spread credit is for.
2. **Spread credit.** Each open spread of ``engine.spreads.book_spreads`` takes a credit on its
   matched lots: ``credit % x the charge on those lots``, the % set per kind (``calendar``) or per
   template family (``benchmark``, ``processing``, ``substitution``; a bundle or pin takes the
   family of the calendar or template sizing it, else none). Only futures legs are credited. The
   matched lots of a leg are its open lots less the spread's leftover on that root (assigned to
   the legs of the leftover's sign, largest first); they are credited only up to the net the
   book holds in that month with the same sign, so lots already offset by another trade in the
   same month (netted in step 1) are never credited twice.
3. **Leftover**: the lots a spread leaves outright take no credit, so they stay charged at the
   outright rate; ``leftover_charge_usd`` shows that part (inside the commodity's charge, not
   added again).
4. **Margin** = charge - credit, per cell, then summed per root, per sector and for the book.
   A cell with no figure (no rate, no USD delta, no delta) is n/a with its reason and left out of
   every sum, which says "excludes N of M".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from engine.limits.cells import OPTION, CellKey, build_cells
from engine.limits.config import CREDIT_KEYS, LimitsConfig, LimitsConfigError, load_limits

BASIS = "estimate (config/limits.yaml), not exchange SPAN"
NOTE = ("Estimated initial margin: per contract month, |net delta USD| x the outright rate of "
        "config/limits.yaml (or |net delta lots| x USD per lot), less a credit on the lots matched in a "
        "spread. Margin follows exposure, so an option counts at its delta (this understates a short "
        "option's margin). An estimate, not the exchange's SPAN or the clearer's figure.")
_EPS = 1e-9


def _sign(x: float) -> int:
    return 0 if abs(x) <= _EPS else (1 if x > 0 else -1)


def _empty(as_of: str, cfg_file: str, reasons: List[str], note: str = NOTE) -> dict:
    zero = {"gross_charge_usd": 0.0, "spread_credit_usd": 0.0, "margin_usd": 0.0, "positions": 0,
            "excluded": [], "excluded_count": 0, "caption": "", "reason": "", "basis": BASIS}
    return {"as_of": as_of, "available": False, "basis": BASIS, "note": note, "config_file": cfg_file,
            "config_note": "", "rows": [], "by_root": {}, "by_sector": {}, "book": zero, "spreads": [],
            "reasons": reasons}


# ------------------------------------------------------------------ step 1
def _charge(cell: dict, cfg: LimitsConfig) -> dict:
    kind, rate, source = cfg.margin_rate(cell["root_id"], cell["sector"])
    out = {"rate_kind": kind or "", "rate": rate, "rate_source": source, "charge_usd": None,
           "charge_per_delta_lot": None, "reason": ""}
    if kind is None:
        out["reason"] = source
    elif kind == "per_lot":
        if cell["delta_lots"] is None:
            out["reason"] = cell["reason"] or "no delta in lots"
        else:
            out["charge_usd"] = abs(cell["delta_lots"]) * rate
    elif cell["delta_usd"] is None:
        out["reason"] = cell["reason"] or "no USD delta"
    else:
        out["charge_usd"] = abs(cell["delta_usd"]) * rate
    if out["charge_usd"] is not None and cell["delta_lots"] is not None and abs(cell["delta_lots"]) > _EPS:
        out["charge_per_delta_lot"] = out["charge_usd"] / abs(cell["delta_lots"])
    return out


# ------------------------------------------------------------------ step 2
def credit_key(spread: dict) -> Optional[str]:
    """'calendar' | 'benchmark' | 'processing' | 'substitution' | None (no credit)."""
    if spread.get("kind") == "calendar" or spread.get("family") == "calendar":
        return "calendar"
    family = str(spread.get("family") or "")
    return family if family in CREDIT_KEYS else None


def _leftover_takes(legs: List[dict], leftover: List[dict]) -> List[float]:
    """Per leg, the open lots (magnitude) that are the spread's leftover, not matched."""
    takes = [0.0] * len(legs)
    for entry in leftover or []:
        lots = float(entry.get("lots") or 0.0)
        s = _sign(lots)
        if not s:
            continue
        remaining = abs(lots)
        order = sorted((i for i, leg in enumerate(legs) if leg["root_id"] == entry.get("root_id")
                        and _sign(float(leg["open_lots"])) == s),
                       key=lambda i: -abs(float(legs[i]["open_lots"])))
        for i in order:
            take = min(abs(float(legs[i]["open_lots"])), remaining)
            takes[i] += take
            remaining -= take
            if remaining <= _EPS:
                break
    return takes


class _Credits:
    def __init__(self, cells: Dict[CellKey, dict], charges: Dict[CellKey, dict], cfg: LimitsConfig):
        self.cells, self.charges, self.cfg = cells, charges, cfg
        self.by_contract: Dict[str, CellKey] = {}
        self.factor: Dict[str, float] = {}
        for key, cell in cells.items():
            for r in cell["rows"]:
                if r["product"] != OPTION:
                    self.by_contract.setdefault(str(r["contract_id"]), key)
                    if r.get("delta_factor") is not None:
                        self.factor[str(r["contract_id"])] = float(r["delta_factor"])
        self.budget = {k: c["delta_lots"] for k, c in cells.items()}
        self.credit_by_cell: Dict[CellKey, float] = {k: 0.0 for k in cells}

    def spread(self, s: dict) -> dict:
        key = credit_key(s)
        pct = self.cfg.spread_credit.get(key) if key else None
        notes: List[str] = []
        if key is None:
            notes.append("no calendar or template shape: no spread credit, every lot at the outright rate")
        elif pct is None:
            notes.append(f"no {key} credit set in config/limits.yaml: every lot at the outright rate")
        legs = [leg for leg in s.get("legs") or []
                if leg.get("product") == "FUTURE" and abs(float(leg.get("open_lots") or 0.0)) > _EPS]
        skipped = [leg["instrument_id"] for leg in s.get("legs") or []
                   if leg.get("product") != "FUTURE" and abs(float(leg.get("open_lots") or 0.0)) > _EPS]
        if skipped:
            notes.append(f"{', '.join(skipped)}: not a future, no credit on it")
        takes = _leftover_takes(legs, s.get("leftover") or [])
        out_legs, excluded = [], []
        credit_total = matched_total = left_total = 0.0
        left_by_root: Dict[str, dict] = {}
        for leg, take in zip(legs, takes):
            out_legs.append(self._leg(leg, take, pct or 0.0, notes, excluded))
            rec = out_legs[-1]
            left = left_by_root.setdefault(leg["root_id"], {"root_id": leg["root_id"], "lots": 0.0,
                                                            "charge_usd": 0.0, "reason": ""})
            left["lots"] += rec["leftover_lots"]
            if rec["credit_usd"] is None:
                left["charge_usd"] = None
                left["reason"] = rec["reason"]
                continue
            credit_total += rec["credit_usd"]
            matched_total += rec["charge_on_matched_usd"]
            if left["charge_usd"] is not None:
                left["charge_usd"] += rec["leftover_charge_usd"]
                left_total += rec["leftover_charge_usd"]
        return {
            "spread_id": s.get("spread_id", ""), "name": s.get("name", ""), "kind": s.get("kind", ""),
            "family": s.get("family", ""), "credit_key": key or "", "credit_pct": pct,
            "legs": out_legs, "charge_on_matched_usd": matched_total, "credit_usd": credit_total,
            "leftover": sorted(left_by_root.values(), key=lambda e: e["root_id"]),
            "leftover_charge_usd": left_total, "excluded": excluded, "excluded_count": len(excluded),
            "note": "; ".join(notes), "basis": BASIS,
        }

    def _leg(self, leg: dict, take: float, pct: float, notes: List[str], excluded: List[str]) -> dict:
        inst = str(leg["instrument_id"])
        open_lots = float(leg["open_lots"])
        s = _sign(open_lots)
        matched = max(abs(open_lots) - take, 0.0)
        factor = self.factor.get(inst, 1.0)
        rec = {"instrument_id": inst, "root_id": leg["root_id"], "contract_month": leg.get("contract_month", ""),
               "open_lots": open_lots, "matched_lots": s * matched, "leftover_lots": s * take,
               "credited_delta_lots": 0.0, "charge_on_matched_usd": 0.0, "credit_usd": 0.0,
               "leftover_charge_usd": 0.0, "reason": ""}
        key = self.by_contract.get(inst)
        if key is None:
            rec["reason"] = f"{inst} is not an open position on the curve: no credit"
            return rec
        per_dl = self.charges[key]["charge_per_delta_lot"]
        if self.charges[key]["charge_usd"] is None:
            rec.update(credit_usd=None, charge_on_matched_usd=None, leftover_charge_usd=None,
                       reason=f"{inst}: {self.charges[key]['reason']}")
            excluded.append(inst)
            return rec
        budget = self.budget.get(key)
        want = matched * factor
        avail = abs(budget) if budget is not None and _sign(budget) == s else 0.0
        credited = min(want, avail)
        if budget is not None:
            self.budget[key] = budget - s * credited
        if want - credited > _EPS:
            notes.append(f"{want - credited:g} lots of {inst} are offset elsewhere in the book in that month "
                         "(already netted in its charge): no credit on them")
        per_dl = per_dl or 0.0
        rec["credited_delta_lots"] = s * credited
        rec["charge_on_matched_usd"] = per_dl * credited
        rec["credit_usd"] = pct * per_dl * credited
        rec["leftover_charge_usd"] = per_dl * take * factor
        self.credit_by_cell[key] += rec["credit_usd"]
        return rec


# ------------------------------------------------------------------ step 4
def _label(cell: dict) -> str:
    return f"{cell['root_id']} {cell['month']}"


def _roll(rows: List[dict]) -> dict:
    ok = [r for r in rows if r["charge_usd"] is not None]
    bad = [r for r in rows if r["charge_usd"] is None]
    gross = float(sum(r["charge_usd"] for r in ok))
    credit = float(sum(r["spread_credit_usd"] for r in ok))
    return {
        "gross_charge_usd": gross, "spread_credit_usd": credit, "margin_usd": gross - credit,
        "positions": len(rows), "excluded": [_label(r) for r in bad], "excluded_count": len(bad),
        "caption": f"excludes {len(bad)} of {len(rows)} positions with no margin figure" if bad else "",
        "reason": "; ".join(f"{_label(r)}: {r['reason']}" for r in bad), "basis": BASIS,
    }


def margin_estimate(conn: sqlite3.Connection, as_of: str, *, config_path: Optional[Path] = None,
                    curve: Optional[dict] = None, spreads: Optional[dict] = None) -> dict:
    """The book's estimated initial margin on ``as_of`` (see the module docstring).

    ``curve`` / ``spreads``: ``curve_positions`` / ``book_spreads`` output already computed for
    ``as_of`` (a screen that has them passes them; otherwise they are computed here).

    Returns ``{as_of, available, basis, note, config_file, config_note, rows, by_root, by_sector,
    book, spreads, reasons}``:

    - ``rows``: one per (root, contract month): root_id, month ('2026-12'), name, sector, exchange,
      contracts, products, position_lots, delta_lots, delta_usd, rate_kind ('rate' | 'per_lot' |
      '' when none is set), rate, rate_source (a sentence), charge_usd (None = n/a),
      spread_credit_usd, margin_usd (None = n/a), reason ('' or why n/a), basis.
    - ``by_root``: {root_id: {name, sector, rate_kind, rate, rate_source, gross_charge_usd,
      spread_credit_usd, margin_usd, positions, excluded, excluded_count, caption, reason, basis}}.
    - ``by_sector``: {sector: same without the rate keys}; ``book``: the same for the whole book.
      The sums are over the positions with a figure; ``caption`` "excludes N of M positions ..."
      when any is n/a, ``excluded`` names them, ``reason`` says why.
    - ``spreads``: one per open spread: spread_id, name, kind, family, credit_key, credit_pct
      (None = not set), legs [{instrument_id, root_id, contract_month, open_lots, matched_lots,
      leftover_lots, credited_delta_lots, charge_on_matched_usd, credit_usd, leftover_charge_usd,
      reason}], charge_on_matched_usd, credit_usd, leftover [{root_id, lots, charge_usd, reason}],
      leftover_charge_usd, excluded, excluded_count, note, basis.
    - ``reasons``: what could not be read or computed, in plain words.
    """
    cfg_file = str(config_path or "config/limits.yaml")
    try:
        cfg = load_limits(config_path)
    except LimitsConfigError as exc:
        return _empty(as_of, cfg_file, [f"config/limits.yaml refused: {exc}"])
    cfg_file = cfg.file
    if curve is None:
        from engine.curve import curve_positions
        curve = curve_positions(conn, as_of)
    if not curve.get("available", False):
        return _empty(as_of, cfg_file, list(curve.get("reasons") or []) or ["no curve positions"])
    reasons: List[str] = [cfg.note] if cfg.note else []

    cells = build_cells(curve.get("rows") or [])
    charges = {k: _charge(c, cfg) for k, c in cells.items()}
    credits = _Credits(cells, charges, cfg)
    spread_rows: List[dict] = []
    if cells:
        if spreads is None:
            try:
                from engine.spreads import book_spreads
                spreads = book_spreads(conn, as_of)
            except Exception as exc:  # noqa: BLE001 -- no spreads = no credit, never no margin
                reasons.append(f"the spreads could not be read ({type(exc).__name__}: {exc}): no spread credit taken")
                spreads = {"spreads": []}
        for s in spreads.get("spreads") or []:
            if s.get("status") == "open":
                spread_rows.append(credits.spread(s))

    rows: List[dict] = []
    for key, cell in cells.items():
        ch = charges[key]
        credit = credits.credit_by_cell[key] if ch["charge_usd"] is not None else None
        rows.append({
            "root_id": cell["root_id"], "month": cell["month"], "name": cell["name"], "sector": cell["sector"],
            "exchange": cell["exchange"], "contracts": cell["contracts"], "products": cell["products"],
            "position_lots": cell["position_lots"], "delta_lots": cell["delta_lots"], "delta_usd": cell["delta_usd"],
            "rate_kind": ch["rate_kind"], "rate": ch["rate"], "rate_source": ch["rate_source"],
            "charge_usd": ch["charge_usd"], "spread_credit_usd": credit,
            "margin_usd": None if ch["charge_usd"] is None else ch["charge_usd"] - credit,
            "reason": ch["reason"], "basis": BASIS,
        })
    reasons += [f"{_label(r)}: {r['reason']}" for r in rows if r["charge_usd"] is None]

    by_root: Dict[str, dict] = {}
    for root_id in dict.fromkeys(r["root_id"] for r in rows):
        mine = [r for r in rows if r["root_id"] == root_id]
        first = mine[0]
        by_root[root_id] = {"name": first["name"], "sector": first["sector"], "rate_kind": first["rate_kind"],
                            "rate": first["rate"], "rate_source": first["rate_source"], **_roll(mine)}
    by_sector = {sec: _roll([r for r in rows if r["sector"] == sec]) for sec in sorted({r["sector"] for r in rows})}
    return {"as_of": as_of, "available": True, "basis": BASIS, "note": NOTE, "config_file": cfg_file,
            "config_note": cfg.note, "rows": rows, "by_root": by_root, "by_sector": by_sector,
            "book": _roll(rows), "spreads": spread_rows, "reasons": reasons}
