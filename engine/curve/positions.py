"""Positions by commodity x contract month: the commodity trader's ladder.

One row per open position with a non-zero net: a commodity future, an option on one
(``CMDTY_OPTION``) or an LME forward (``LME_FWD``). Each gives its net lots, the same in physical
units (lots x contract size, in the root's size unit: bbl, t, oz, bu, MMBtu, lb ...), its
notional (``lots x multiplier x price`` in the contract's quote currency and that times ``S`` in
USD), and its delta in futures-equivalent lots and in USD (``engine.curve.rows``). Rolled up per
commodity (the contract root, 'NYMEX:CL') and per sector, so a spread book shows offsetting
months and a small net outright.

Rules (CLAUDE.md hard rules 2 and 3, "P&L conventions -> Futures"):

- A commodity future is a ``FUTURE`` trade whose instrument's ``base_ccy`` is a contract root of
  ``config/contracts.csv`` (the ingest-parser's layout); anything else (the equity index
  futures, ES and the like) is not this module's. Open = ``trade_date <= as_of`` and the leg's
  expiry (``trade_legs.settle_date`` of leg 1) ``> as_of``: the test
  ``engine/ladder/futures_delta.py::futures_usd_delta`` uses.
- An option on a commodity future is a ``CMDTY_OPTION`` trade laid out the same way (its leg is
  dated the option's expiry); it sits under its underlying future's contract month.
- An LME forward is an ``LME_FWD`` trade on a metal's root id ('LME:CA',
  ``engine.lme.is_lme_instrument``), open while its prompt date (its legs' date) is after
  ``as_of``; it sits under the prompt's month, one row per prompt.
- Contract size, unit, multiplier, name, sector, the contract month and the averaging period come
  from contract-master (``data.contracts``) and tonnes per LME lot from ``engine.lme``; nothing
  here is per commodity.
- Prices and ``S`` are the exact official marks of ``as_of``, never estimated (hard rule 2).
- The currency exposure of non-USD futures and options is the P&L they have built up in that
  currency (``value_book``'s ``pnl_local``), not their notional: a margined position holds no
  notional cash.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

from data.contracts import load_roots
from engine.curve.leftover import Leftover, leftover_by_root, row_month
from engine.curve.rows import (FUTURE, LME, OPTION, future_row, lme_row, month_key, number, option_row, per_pct,
                               root_key)
from engine.pnl.valuation import value_book

_OPEN_LISTED_SQL = """
SELECT t.trade_id, t.product, t.instrument_id, t.quantity, i.base_ccy, i.quote_ccy, i.multiplier, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
WHERE t.product IN ('FUTURE', 'CMDTY_OPTION') AND t.trade_date <= :as_of AND l.settle_date > :as_of
ORDER BY t.instrument_id, l.settle_date, t.trade_id
"""

# An LME forward's legs are both dated its prompt; the latest of them is taken, so a ticket is
# never dropped over how its legs are labelled.
_OPEN_LME_SQL = """
SELECT t.trade_id, t.product, t.instrument_id, t.quantity, i.base_ccy, i.quote_ccy, i.multiplier,
       MAX(l.settle_date) AS prompt
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN trade_legs l ON l.trade_id = t.trade_id
WHERE t.product = 'LME_FWD' AND t.trade_date <= :as_of
GROUP BY t.trade_id
HAVING prompt > :as_of
ORDER BY t.instrument_id, prompt, t.trade_id
"""

_FLAT = 1e-9   # a position whose net is within this of zero is flat
_PRODUCT_ORDER = (FUTURE, LME, OPTION)
_OUTRIGHT = (FUTURE, LME)   # lots of the future itself: the net / gross lots and USD notional
_LABEL = {"notional_usd": "USD notional", "delta_usd": "USD delta"}

# Kilograms per one of each mass unit contract-master sizes a contract in (exact definitions:
# the international pound, the US short ton and hundredweight, the UK long ton, the troy ounce).
# Physical units of one commodity add across exchanges only through these; anything else
# (bushels, gallons, MWh, the USD of an FX future) never converts.
_KG_PER = {"t": 1000.0, "kg": 1.0, "g": 0.001, "lb": 0.45359237, "st": 907.18474, "lt": 1016.0469088,
           "cwt": 45.359237, "oz": 0.0311034768}
_PREFERRED_MASS = ("t", "kg")   # a subsector's unit when any of its roots is sized in it, in this order
_FX_SECTOR = "fx"               # a subsector of this sector is an FX hedge (SGX:XUC), never commodity leftover

# Plain words for a subsector key of config/contracts.csv where "capitalise and drop the
# underscores" is not the name a trader uses.
_SUBSECTOR_NAMES = {
    "usdcnh": "USD/CNH", "hrc": "HRC", "lldpe": "LLDPE", "pvc": "PVC", "pta": "PTA", "meg": "MEG", "psf": "PSF",
    "dap": "DAP", "uan": "UAN", "lpg": "LPG", "pet_resin": "PET resin", "coffee_arabica": "Coffee (arabica)",
    "coffee_robusta": "Coffee (robusta)", "": "Unknown commodity",
}


def subsector_name(subsector: str) -> str:
    """The plain name of a subsector key: 'copper' -> 'Copper', 'iron_ore' -> 'Iron ore',
    'live_cattle' -> 'Live cattle', 'usdcnh' -> 'USD/CNH'."""
    key = str(subsector or "")
    if key in _SUBSECTOR_NAMES:
        return _SUBSECTOR_NAMES[key]
    words = key.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _is_root_id(key: str) -> bool:
    return ":" in key


def _group_open(rows) -> Dict[Tuple[str, str, str], dict]:
    """Open trades summed per (product, instrument, expiry), in SQL order."""
    groups: Dict[Tuple[str, str, str], dict] = {}
    for trade_id, product, instrument_id, qty, base_ccy, quote_ccy, multiplier, expiry in rows:
        g = groups.setdefault((product, instrument_id, expiry), {
            "product": product, "instrument_id": instrument_id, "expiry": expiry, "root_key": root_key(base_ccy),
            "currency": str(quote_ccy or ""), "multiplier_on_file": multiplier,
            "lots": 0.0, "trade_ids": [], "bad_quantity": []})
        g["trade_ids"].append(trade_id)
        q = number(qty)
        if q is None:
            g["bad_quantity"].append(trade_id)
        else:
            g["lots"] += q
    return groups


def _usd_totals(rows: List[dict], field: str) -> Tuple[Optional[float], Optional[float], List[str], str]:
    """(net, gross, missing contract ids, reason) of ``field`` over ``rows``: None when any row
    has no figure."""
    missing = [r["contract_id"] for r in rows if r[field] is None]
    if missing:
        why = "; ".join(f"{r['contract_id']}: {r['reason'] or 'no ' + _LABEL.get(field, field)}"
                        for r in rows if r[field] is None)
        return None, None, missing, why
    return float(sum(r[field] for r in rows)), float(sum(abs(r[field]) for r in rows)), [], ""


def _pct_totals(rows: List[dict]) -> dict:
    """USD per 1 % move of ``rows``' delta (``usd_per_pct``): ``long_usd_per_pct`` (the positions
    long, summed), ``short_usd_per_pct`` (the positions short, negative), ``net_usd_per_pct``,
    ``gross_usd_per_pct`` (|long| + |short|) and ``usd_per_pct_reason``; all None, with the
    reason, when any row has no USD delta (never a partial sum)."""
    _net, _gross, missing, why = _usd_totals(rows, "delta_usd")
    if missing:
        return {"long_usd_per_pct": None, "short_usd_per_pct": None, "net_usd_per_pct": None,
                "gross_usd_per_pct": None, "usd_per_pct_reason": why}
    long_ = float(sum(r["usd_per_pct"] for r in rows if r["usd_per_pct"] > 0))
    short = float(sum(r["usd_per_pct"] for r in rows if r["usd_per_pct"] < 0))
    return {"long_usd_per_pct": long_, "short_usd_per_pct": short, "net_usd_per_pct": long_ + short,
            "gross_usd_per_pct": long_ - short, "usd_per_pct_reason": ""}


def _delta_totals(rows: List[dict]) -> dict:
    net_lots = None if any(r["delta_lots"] is None for r in rows) else float(sum(r["delta_lots"] for r in rows))
    net_usd, gross_usd, missing, why = _usd_totals(rows, "delta_usd")
    return {"net_delta_lots": net_lots, "net_delta_usd": net_usd, "gross_delta_usd": gross_usd,
            "delta_missing": missing, "delta_reason": why, **_pct_totals(rows)}


def _months(rows: List[dict], field: str) -> Dict[str, Optional[float]]:
    """{'YYYY-MM': sum of ``field``}; a month with a row that has no figure is None."""
    out: Dict[str, Optional[float]] = {}
    for r in rows:
        if r["year"] is None:
            continue
        key = month_key(r["year"], r["month"])
        if r[field] is None or (key in out and out[key] is None):
            out[key] = None
        else:
            out[key] = out.get(key, 0.0) + r[field]
    return dict(sorted(out.items()))


def _by_commodity(rows: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for root_id in dict.fromkeys(r["root_id"] for r in rows):
        mine = [r for r in rows if r["root_id"] == root_id]
        outright = [r for r in mine if r["product"] in _OUTRIGHT]
        first = mine[0]
        net_usd, gross_usd, missing, why = _usd_totals(outright, "notional_usd")
        units_known = all(r["units"] is not None for r in outright)
        out[root_id] = {
            "name": first["name"], "sector": first["sector"], "subsector": first["subsector"],
            "exchange": first["exchange"], "currency": first["currency"],
            "net_lots": float(sum(r["lots"] for r in outright)),
            "gross_lots": float(sum(abs(r["lots"]) for r in outright)),
            "net_units": float(sum(r["units"] for r in outright)) if units_known else None,
            "unit": first["unit"], "net_usd": net_usd, "gross_usd": gross_usd,
            "months": _months(outright, "lots"),
            "missing": missing, "reason": why,
            "delta_months": _months(mine, "delta_lots"), "months_usd_per_pct": _months(mine, "usd_per_pct"),
            "products": [p for p in _PRODUCT_ORDER if any(r["product"] == p for r in mine)],
            **_delta_totals(mine),
        }
    return out


def _by_sector(rows: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for sector in sorted({r["sector"] for r in rows}):
        mine = [r for r in rows if r["sector"] == sector]
        net_usd, gross_usd, missing, why = _usd_totals([r for r in mine if r["product"] in _OUTRIGHT], "notional_usd")
        out[sector] = {"net_usd": net_usd, "gross_usd": gross_usd, "missing": missing, "reason": why,
                       "commodities": list(dict.fromkeys(r["root_id"] for r in mine)), **_delta_totals(mine)}
    return out


def _gross_key(gross: Optional[float]) -> Tuple[bool, float]:
    """Sort key: largest gross USD first, an unknown gross after every known one."""
    return (gross is None, -(gross or 0.0))


def _unit_rule(split: List[dict]) -> Tuple[str, Optional[Dict[str, float]], str]:
    """(unit, {root unit: factor to it}, note) of a subsector's roots, ``split`` ordered by gross
    USD: one unit shared by every root is kept as it is; mass units convert through ``_KG_PER``
    to tonnes when any root is sized in t, else kg when any is, else the largest root's unit (a
    fixed choice, so a line's unit never flips with the day's marks); anything else does not
    add: unit '', factor None, and the note says so."""
    units = list(dict.fromkeys(c["unit"] for c in split))
    if len(units) == 1:
        return units[0], {units[0]: 1.0}, ""
    if all(u in _KG_PER for u in units):
        unit = next((u for u in _PREFERRED_MASS if u in units), split[0]["unit"])
        note = ", ".join(f"{exch} lots in {u}" + ("" if u == unit else f" converted to {unit}")
                         for exch, u in dict.fromkeys((c["exchange"], c["unit"]) for c in split))
        if unit not in _PREFERRED_MASS:
            note += " (unit of the largest position)"
        return unit, {u: _KG_PER[u] / _KG_PER[unit] for u in units}, note
    named = ", ".join(f"{c['root_id']} in {c['unit'] or '?'}" for c in split)
    return "", None, f"units do not add across exchanges: {named}"


def _subsector_units(split: List[dict]) -> Tuple[Optional[float], str, str]:
    """(net_units, unit, units_note) of a subsector's roots by ``_unit_rule``."""
    unit, factor, note = _unit_rule(split)
    if factor is None:
        return None, unit, note
    unknown = [c["root_id"] for c in split if c["net_units"] is None]
    if unknown:
        return None, unit, (note + "; " if note else "") + f"no units for {', '.join(unknown)}"
    return float(sum(c["net_units"] * factor[c["unit"]] for c in split)), unit, note


def _unit_months(rows: List[dict], factor: Optional[Dict[str, float]], unit_note: str) -> dict:
    """The delta in one physical unit: ``months_units`` {'YYYY-MM': sum of the rows' delta_units
    x factor}, ``net_delta_units``, ``gross_units`` (the sum of |delta_units| per position),
    ``months_units_missing`` (the contract ids with no figure) and ``months_units_reason``. A
    month with a row that has no delta units is None; a row with no contract month leaves the
    net and gross None (it has no column, and a sum never leaves it out silently)."""
    months: Dict[str, Optional[float]] = {}
    net: Optional[float] = 0.0
    gross: Optional[float] = 0.0
    missing: List[str] = []
    why: List[str] = []
    for r in rows:
        key = row_month(r)
        value = r["delta_units"]
        if factor is None:
            value, reason = None, unit_note
        elif value is not None and r["unit"] not in factor:
            value, reason = None, f"{r['unit'] or '?'} does not convert"
        else:
            value = None if value is None else value * factor[r["unit"]]
            reason = r["reason"] or "no delta"
        if key is None:
            net = gross = None
            missing.append(r["contract_id"])
            why.append(f"{r['contract_id']}: no contract month" + (f" ({r['reason']})" if r["reason"] else ""))
            continue
        if value is None:
            months[key] = None
            net = gross = None
            missing.append(r["contract_id"])
            why.append(f"{r['contract_id']}: {reason}")
            continue
        if not (key in months and months[key] is None):
            months[key] = months.get(key, 0.0) + value
        if net is not None:
            net += value
            gross += abs(value)
    if factor is None:
        why = [unit_note]
    return {"months_units": dict(sorted(months.items())), "net_delta_units": net, "gross_units": gross,
            "months_units_missing": missing, "months_units_reason": "; ".join(dict.fromkeys(why))}


def _leftover_units(root_ids: List[str], months: List[str], left: Optional[Leftover], roots: dict,
                    factor: Optional[Dict[str, float]], unit_note: str, fx: bool) -> dict:
    """``leftover_months_units`` {'YYYY-MM': units, every month of the line, 0.0 where nothing is
    left}, ``leftover_units`` (their sum) and ``leftover_reason`` over ``root_ids``, in the
    subsector's unit."""
    if fx:
        return {"leftover_months_units": {}, "leftover_units": None,
                "leftover_reason": "an FX hedge: not commodity leftover"}
    if left is None or left.failure:
        return {"leftover_months_units": {m: None for m in months}, "leftover_units": None,
                "leftover_reason": left.failure if left is not None else "the spreads were not read"}
    if factor is None:
        return {"leftover_months_units": {m: None for m in months}, "leftover_units": None,
                "leftover_reason": unit_note}
    cells: Dict[str, Optional[float]] = {m: 0.0 for m in months}
    why: List[str] = []
    total_known = True
    for rid in root_ids:
        root = roots.get(rid)
        per_unit = None if root is None or root.size_unit not in factor else root.contract_size * factor[root.size_unit]
        for month, lots in left.months.get(rid, {}).items():
            if lots is None or per_unit is None:
                cells[month] = None
                why += left.reasons[rid].get(month, []) or [f"{rid}: {root.size_unit if root else 'no unit'} "
                                                           "does not convert"]
            elif cells.get(month, 0.0) is not None:
                cells[month] = cells.get(month, 0.0) + lots * per_unit
        if left.unplaced.get(rid):
            total_known = False
            why += left.unplaced[rid]
    cells = {m: (None if v is None else (0.0 if abs(v) < 1e-9 else v)) for m, v in sorted(cells.items())}
    known = total_known and all(v is not None for v in cells.values())
    return {"leftover_months_units": cells, "leftover_units": float(sum(cells.values())) if known else None,
            "leftover_reason": "; ".join(dict.fromkeys(why))}


def _usd_per_lot(rows: List[dict], root_id: str, month: str) -> Tuple[Optional[float], str]:
    """(USD per delta lot, why when None) of ``root_id`` in ``month``: the curve rows' own
    ``delta_usd / delta_lots`` (multiplier x price x S), which must agree across the month's rows
    (two LME prompts of one month at different outrights do not)."""
    mine = [r for r in rows if r["root_id"] == root_id and row_month(r) == month]
    per: List[float] = []
    for r in mine:
        if r["delta_lots"] is None or abs(r["delta_lots"]) <= _FLAT:
            continue
        if r["delta_usd"] is None:
            return None, f"{r['contract_id']}: {r['reason'] or 'no USD delta'}"
        per.append(r["delta_usd"] / r["delta_lots"])
    if not per:
        return None, f"{root_id} {month}: no priced position in that month to value the leftover at"
    if max(per) - min(per) > 1e-9 * max(1.0, max(abs(x) for x in per)):
        return None, (f"{root_id} {month}: its positions are at different prices (LME prompts of one month), "
                      "so the month's leftover has no one USD figure")
    return per[0], ""


def _leftover_pct(root_ids: List[str], months: List[str], left: Optional[Leftover], rows: List[dict],
                  fx: bool) -> dict:
    """``leftover_months_usd_per_pct`` {'YYYY-MM': USD per 1 % move of the leftover, every month
    of the line, 0.0 where nothing is left}, ``leftover_usd_per_pct`` (their sum) and
    ``leftover_usd_per_pct_reason``: the leftover delta lots (``Leftover.months``) x the month's
    USD per delta lot x 1 %. Needs no common physical unit, so it has a figure where the units do
    not add. None with the reason where a part has none (never a partial sum)."""
    if fx:
        return {"leftover_months_usd_per_pct": {}, "leftover_usd_per_pct": None,
                "leftover_usd_per_pct_reason": "an FX hedge: not commodity leftover"}
    if left is None or left.failure:
        return {"leftover_months_usd_per_pct": {m: None for m in months}, "leftover_usd_per_pct": None,
                "leftover_usd_per_pct_reason": left.failure if left is not None else "the spreads were not read"}
    cells: Dict[str, Optional[float]] = {m: 0.0 for m in months}
    why: List[str] = []
    total_known = True
    for rid in root_ids:
        for month, lots in left.months.get(rid, {}).items():
            if lots is None:
                cells[month] = None
                why += left.reasons[rid].get(month, []) or [f"{rid} {month}: its leftover is not known"]
                continue
            if abs(lots) <= _FLAT:
                cells.setdefault(month, 0.0)
                continue
            per_lot, reason = _usd_per_lot(rows, rid, month)
            if per_lot is None:
                cells[month] = None
                why.append(reason)
            elif cells.get(month, 0.0) is not None:
                cells[month] = cells.get(month, 0.0) + per_pct(lots * per_lot)
        if left.unplaced.get(rid):
            total_known = False
            why += left.unplaced[rid]
    cells = {m: (None if v is None else (0.0 if abs(v) < 1e-9 else v)) for m, v in sorted(cells.items())}
    known = total_known and all(v is not None for v in cells.values())
    return {"leftover_months_usd_per_pct": cells,
            "leftover_usd_per_pct": float(sum(cells.values())) if known else None,
            "leftover_usd_per_pct_reason": "; ".join(dict.fromkeys(why))}


def _by_subsector(rows: List[dict], by_commodity: Dict[str, dict], roots: dict,
                  left: Optional[Leftover]) -> Dict[str, dict]:
    """One line per commodity across its exchanges (the universe's ``subsector``: 'copper' for
    COMEX:HG and LME:CA), netted where the figures add (USD, delta USD, and physical units in
    one mass unit) and split per root where they do not (lots). A subsector of sector 'fx'
    (SGX:XUC under 'usdcnh') only ever holds its own roots, so it is its own line and is never
    netted into a commodity."""
    out: Dict[str, dict] = {}
    first_of = {}
    for r in rows:
        first_of.setdefault(r["subsector"], r)
    for sub in sorted(first_of, key=lambda s: (first_of[s]["sector"], s)):
        mine = [r for r in rows if r["subsector"] == sub]
        root_ids = sorted(dict.fromkeys(r["root_id"] for r in mine), key=lambda rid: _gross_key(by_commodity[rid]["gross_usd"]))
        split = [{"root_id": rid, **by_commodity[rid]} for rid in root_ids]
        by_exchange: Dict[str, Optional[float]] = {}
        for c in split:
            have = by_exchange.get(c["exchange"], 0.0)
            by_exchange[c["exchange"]] = None if have is None or c["gross_usd"] is None else have + c["gross_usd"]
        outright = [r for r in mine if r["product"] in _OUTRIGHT]
        net_usd, gross_usd, missing, why = _usd_totals(outright, "notional_usd")
        net_units, unit, units_note = _subsector_units(split)
        _unit, factor, _note = _unit_rule(split)
        fx = first_of[sub]["sector"] == _FX_SECTOR
        for c in split:
            theirs = [r for r in mine if r["root_id"] == c["root_id"]]
            c["subsector_unit"] = unit
            c.update(_unit_months(theirs, factor, units_note))
            c.update(_leftover_units([c["root_id"]], list(c["months_units"]), left, roots, factor, units_note, fx))
            c.update(_leftover_pct([c["root_id"]], list(c["months_usd_per_pct"]), left, theirs, fx))
        in_units = _unit_months(mine, factor, units_note)
        out[sub] = {
            "name": subsector_name(sub), "sector": first_of[sub]["sector"],
            "exchanges": sorted(by_exchange, key=lambda e: _gross_key(by_exchange[e])),
            "commodities": root_ids, "split": split,
            "net_units": net_units, "unit": unit, "units_note": units_note,
            "net_usd": net_usd, "gross_usd": gross_usd, "missing": missing, "reason": why,
            "months": _months(outright, "notional_usd"), "delta_months": _months(mine, "delta_usd"),
            "months_usd_per_pct": _months(mine, "usd_per_pct"),
            "products": [p for p in _PRODUCT_ORDER if any(r["product"] == p for r in mine)],
            **{k: v for k, v in _delta_totals(mine).items() if k != "net_delta_lots"},
            **in_units,
            **_leftover_units(root_ids, list(in_units["months_units"]), left, roots, factor, units_note, fx),
            **_leftover_pct(root_ids, list(_months(mine, "usd_per_pct")), left, mine, fx),
        }
    return out


def _currency_exposure(conn, groups: List[dict], as_of: str) -> Tuple[Dict[str, dict], List[str]]:
    """{ccy: {pnl_local, pnl_usd, contracts, missing, reason, by_trade}} over every open non-USD
    commodity future and option (flat ones included: their P&L is still held in that currency),
    from value_book; ``by_trade`` {trade_id: pnl_usd or None} is the per-trade USD P&L summed."""
    by_ccy: Dict[str, List[dict]] = {}
    for g in groups:
        if g["currency"] and g["currency"] != "USD":
            by_ccy.setdefault(g["currency"], []).append(g)
    if not by_ccy:
        return {}, []
    trade_ids = [t for gs in by_ccy.values() for g in gs for t in g["trade_ids"]]
    book = value_book(conn, as_of, trade_ids=trade_ids)
    rows = {str(r.trade_id): r for r in book.itertuples(index=False)}
    out: Dict[str, dict] = {}
    reasons: List[str] = []
    for ccy in sorted(by_ccy):
        local, usd, missing_local, missing_usd, why = 0.0, 0.0, [], [], []
        by_trade: Dict[str, Optional[float]] = {}
        for g in by_ccy[ccy]:
            for tid in g["trade_ids"]:
                r = rows.get(str(tid))
                pl = number(getattr(r, "pnl_local", None)) if r is not None else None
                pu = number(getattr(r, "pnl_usd", None)) if r is not None else None
                by_trade[str(tid)] = pu
                if pl is None:
                    missing_local.append(tid)
                    why.append(f"{tid} ({g['instrument_id']}): "
                               f"{(getattr(r, 'reason', '') if r is not None else '') or 'no local P&L'}")
                else:
                    local += pl
                if pu is None:
                    missing_usd.append(tid)
                    if pl is not None:
                        why.append(f"{tid} ({g['instrument_id']}): "
                                   f"{(getattr(r, 'reason', '') if r is not None else '') or 'no USD P&L'}")
                else:
                    usd += pu
        reason = "; ".join(why)
        out[ccy] = {"pnl_local": None if missing_local else local, "pnl_usd": None if missing_usd else usd,
                    "contracts": sorted({g["instrument_id"] for g in by_ccy[ccy]}),
                    "missing": sorted(set(missing_local) | set(missing_usd)), "reason": reason,
                    "by_trade": by_trade}
        if reason:
            reasons.append(f"{ccy} exposure: {reason}")
    return out, reasons


def _empty(as_of: str, available: bool, note: str, reasons: List[str]) -> dict:
    return {"as_of": as_of, "available": available, "note": note, "rows": [], "flat_contracts": [],
            "by_commodity": {}, "by_subsector": {}, "by_sector": {}, "currency_exposure": {}, "months": [],
            "products_present": [], "reasons": reasons}


def _lme_groups(conn, as_of: str, reasons: List[str]) -> List[Tuple[dict, object, float]]:
    """(group, root, tonnes per lot) per open LME forward prompt; an LME_FWD on an instrument
    that is not an LME metal is named in ``reasons``, never shown as lots it is not."""
    raw = conn.execute(_OPEN_LME_SQL, {"as_of": as_of}).fetchall()
    if not raw:
        return []
    from engine.lme import is_lme_instrument, lot_tonnes, metal_root
    out = []
    for g in _group_open(raw).values():
        if not is_lme_instrument(g["instrument_id"]):
            reasons.append(f"LME forward {', '.join(g['trade_ids'])} on {g['instrument_id']!r}: not an LME metal "
                           "(engine.lme), left out of the curve")
            continue
        out.append((g, metal_root(g["instrument_id"]), lot_tonnes(g["instrument_id"])))
    return out


def _leftover(conn, as_of: str, rows: List[dict], roots: dict, spreads: Optional[dict]) -> Tuple[Leftover, List[str]]:
    """The leftover per root and month from spreads-engine's strategies (``engine.curve.leftover``);
    ``spreads`` is a ``book_spreads(conn, as_of)`` result the caller already holds, else it is read."""
    reasons: List[str] = []
    try:
        if spreads is None:
            from engine.spreads import book_spreads     # layer 4, read here; spreads never reads the curve
            spreads = book_spreads(conn, as_of)
        strategies = spreads.get("strategies")
        if not strategies and rows:
            failed = [r for r in spreads.get("reasons") or [] if "strategies could not be built" in r]
            raise ValueError(failed[0] if failed else "the spreads engine found no strategies")
        left = leftover_by_root(rows, strategies or [], roots, as_of)
    except Exception as exc:  # noqa: BLE001 -- the leftover is beside the positions, never in their way
        left = Leftover()
        left.failure = f"the leftover could not be read from the spreads ({type(exc).__name__}: {exc})"
        reasons.append(left.failure)
    reasons += [why for rid in sorted(left.reasons) for m in sorted(left.reasons[rid])
                for why in left.reasons[rid][m] if "the spreads account for" in why or "the spreads hold" in why]
    return left, reasons


def curve_positions(conn: sqlite3.Connection, as_of: str, spreads: Optional[dict] = None) -> dict:
    """The book's commodity futures, options on them and LME forwards by contract month on `as_of`.

    Returns ``{as_of, available, note, rows, flat_contracts, by_commodity, by_subsector,
    by_sector, currency_exposure, months, products_present, reasons}``:

    - ``rows``: one dict per open position with a non-zero net, ordered by sector, commodity,
      expiry: ``product`` ('FUTURE' | 'CMDTY_OPTION' | 'LME_FWD'), ``root_id, name, sector,
      subsector, exchange, currency, contract_id`` (a future's or option's canonical id, an LME
      forward's 'LME:CA <prompt>'), ``instrument_id``, ``underlying_id`` (the future itself, an
      option's underlying future, None for an LME forward), ``month, year, month_code`` (an
      option's are its underlying's; an LME forward's its prompt's), ``expiry`` (a future's
      expiry, an option's expiry, an LME forward's prompt), ``first_notice`` (ISO or None),
      ``dates_source`` ('BLOOMBERG' | 'ESTIMATED' | 'PROMPT'), ``lots`` (an option's: option lots;
      an LME forward's: tonnes / tonnes per lot), ``gross_lots`` (= |lots|), ``units``, ``unit``,
      ``multiplier`` (per lot of the future), ``price`` (an option's: its underlying future's),
      ``price_source, usd_per_unit, usd_source, notional_local, notional_usd`` (None for an
      option: its exposure is its delta), ``delta_factor`` (1 for a future, the averaging share
      for a monthly-average contract, the DELTA mark for an option, 1 for an LME forward),
      ``delta_lots`` (lots x delta_factor, futures-equivalent), ``delta_units``, ``delta_local``,
      ``delta_usd`` (delta_lots x multiplier x price x S), ``trade_ids``, ``note`` (what the
      delta is, in words; '' for a plain future), ``reason`` ('' when every figure is there).
    - ``flat_contracts``: ``[{product, root_id, contract_id, expiry, trade_ids}]``, open
      positions netting to 0.
    - ``by_commodity``: ``{root_id: {name, sector, subsector, exchange, currency, net_lots,
      gross_lots, net_units, unit, net_usd, gross_usd, months {'YYYY-MM': lots}, missing, reason,
      delta_months {'YYYY-MM': delta lots}, products, net_delta_lots, net_delta_usd,
      gross_delta_usd, delta_missing, delta_reason}}``. The lots, units, USD notional and
      ``months`` are over futures and LME forwards only (an option is not a lot of the future);
      net_usd / gross_usd are None when any of those rows has no USD notional (``missing`` names
      them, ``reason`` says why). The delta figures are over every product, None (and a
      ``delta_months`` cell None) when any row has no delta.
    - ``by_subsector``: one line per commodity across its exchanges, keyed by the universe's
      ``subsector`` ('copper' holds COMEX:HG and LME:CA; 'zinc' SHFE:ZN and LME:ZS), in order
      of sector then key: ``{subsector: {name (plain words: 'Copper', 'Iron ore', 'USD/CNH'),
      sector, exchanges (those with an open position, largest gross USD first), commodities
      (the root ids, in the same order), split ([{root_id, **by_commodity[root_id]}] in that
      order: the per-root lots, units, USD and months, since lots do not add across exchanges),
      net_units, unit, units_note, net_usd, gross_usd, missing, reason, months {'YYYY-MM': net
      USD notional}, delta_months {'YYYY-MM': delta USD}, products, net_delta_usd,
      gross_delta_usd, delta_missing, delta_reason}}``. The USD figures are the sums
      ``by_sector`` makes (None with ``missing`` / ``reason`` when a row has none; no
      ``net_delta_lots``, which does not add across exchanges). ``net_units`` is in one physical
      ``unit``: the roots' own when they share one, else, when every root is sized in a mass
      unit (t, kg, g, lb, st, lt, cwt, oz), each root's units converted at the exact definitions
      (``_KG_PER``) to tonnes when any root is sized in t, else kg when any is, else the unit of
      the root with the largest gross USD (a fixed choice: the line's unit never flips with the
      day's marks), ``units_note`` saying so ('LME lots in t, COMEX lots in lb converted to t';
      the largest-gross fallback adds 'unit of the largest position'); otherwise (bushels against tonnes, a
      MWh contract) None with the reason in ``units_note``. A subsector of sector 'fx'
      (SGX:XUC, 'usdcnh': one lot is 100,000 USD, so its units are USD) only ever holds its own
      roots and is its own line, never netted into a commodity.
      Added 2026-09-29 (the Exposure grid in physical units), on the subsector line and on each
      ``split`` entry (one per root: the exchange rows, which add up to the line), all in the
      subsector's ``unit`` by the same conversion as ``net_units`` (a ``split`` entry carries it
      as ``subsector_unit``, its own ``unit`` staying the root's): ``months_units`` {'YYYY-MM':
      net delta in physical units}, the rows' ``delta_units`` converted (the same exposure as
      ``delta_months`` in lots: an averaging contract's shrinking delta, an LME ticket's tonnes
      under its prompt month, an option at its DELTA mark under its underlying's month);
      ``net_delta_units`` (their sum) and ``gross_units`` (the sum of |delta units| per
      position); ``months_units_missing`` (the contract ids with no figure) and
      ``months_units_reason``. A month holding a position with no delta, or every month when the
      roots' units do not add (``units_note``), is None; a position with no contract month
      leaves ``net_delta_units`` and ``gross_units`` None; never a partial sum. And the
      **leftover**, the part of the net that belongs to no spread, read from
      ``engine.spreads.book_spreads``' ``strategies`` (``engine.curve.leftover``: a residual in
      full; a pair's ``residual_units`` on the leg whose units carry its sign, in that leg's
      month; an option residual at its delta; a hedge never), in delta units like
      ``months_units``: ``leftover_months_units`` {'YYYY-MM': units; every month of
      ``months_units``, 0.0 where nothing is left, None with the reason where a part has no
      figure or the strategies and the curve disagree on the lots}, ``leftover_units`` (their
      sum; None when any month is None or a leftover has no month) and ``leftover_reason`` (''
      or why). A subsector of sector 'fx' is a hedge: ``leftover_months_units`` {},
      ``leftover_units`` None, ``leftover_reason`` 'an FX hedge: not commodity leftover'.
    - ``by_sector``: ``{sector: {net_usd, gross_usd, missing, reason, commodities,
      net_delta_lots, net_delta_usd, gross_delta_usd, delta_missing, delta_reason}}``, same rules.
    - ``currency_exposure``: ``{ccy: {pnl_local, pnl_usd, contracts, missing, reason, by_trade}}``
      for the open non-USD commodity futures and options, summed from ``value_book``; None where a
      trade has none. ``by_trade`` {trade_id: pnl_usd, None where the trade has none} is what
      ``pnl_usd`` sums (commodity-stress's FX split reads it; 2026-09-29).
    - ``months``: sorted 'YYYY-MM' keys that appear in ``rows``.
    - ``products_present``: the products among ``rows``, in the order FUTURE, LME_FWD, CMDTY_OPTION.
    - ``reasons``: what could not be computed, in plain words (a leftover that could not be read
      from the spreads, or lots the strategies and the curve disagree on, included).

    **USD per 1 % move** (added 2026-09-29 for Risk's Net by commodity, Phase G; the screens
    never scale a figure): what a 1 % move of the price is worth, ``delta USD x 0.01``, the same
    sign, beside every delta-USD figure:

    - each row: ``usd_per_pct`` (None where ``delta_usd`` is None) and ``usd_per_pct_reason``
      ('' or why);
    - ``by_commodity`` (the exchange rows), each ``by_subsector`` line and each of its ``split``
      entries, and ``by_sector``: ``long_usd_per_pct`` (the long positions summed),
      ``short_usd_per_pct`` (the short ones, negative), ``net_usd_per_pct``,
      ``gross_usd_per_pct`` (|long| + |short|) and ``usd_per_pct_reason``; all None, with the
      reason, when any position has no USD delta (never a partial sum);
    - ``by_commodity``, ``by_subsector`` and ``split``: ``months_usd_per_pct`` {'YYYY-MM': the
      month's delta USD x 0.01}, None where a position of the month has none;
    - ``by_subsector`` and ``split``: ``leftover_months_usd_per_pct`` (every month of
      ``months_usd_per_pct``, 0.0 where nothing is left; {} for an FX hedge),
      ``leftover_usd_per_pct`` (their sum) and ``leftover_usd_per_pct_reason``: the leftover's
      delta lots at the month's own USD per delta lot (the rows' ``delta_usd / delta_lots``), so
      it has a figure where the physical units do not add; None with the reason where a part has
      none, or where one month holds LME prompts at different prices.

    ``spreads``: a ``book_spreads(conn, as_of)`` result the caller already holds, to save
    reading it again for the leftover; read here when None.
    """
    try:
        roots = load_roots()
    except (OSError, ValueError) as exc:
        return _empty(as_of, False, "", [f"the contract universe could not be read: {exc}"])
    reasons: List[str] = []
    groups = _group_open(conn.execute(_OPEN_LISTED_SQL, {"as_of": as_of}).fetchall())
    mine = [g for g in groups.values() if g["root_key"] in roots or _is_root_id(g["root_key"])]
    lme = _lme_groups(conn, as_of, reasons)
    if not mine and not lme:
        return _empty(as_of, True, f"no open commodity futures on {as_of}", reasons)

    seen_expiry: Dict[Tuple[str, str], List[str]] = {}
    for g in mine:
        seen_expiry.setdefault((g["product"], g["instrument_id"]), []).append(g["expiry"])
    for (_product, instrument_id), expiries in seen_expiry.items():
        if len(expiries) > 1:
            reasons.append(f"{instrument_id} has trades on more than one expiry ({', '.join(expiries)}); "
                           "one row per expiry")

    rows, flat = [], []
    for g in mine:
        if not g["bad_quantity"] and abs(g["lots"]) <= _FLAT:
            flat.append({"product": g["product"], "root_id": g["root_key"], "contract_id": g["instrument_id"],
                         "expiry": g["expiry"], "trade_ids": list(g["trade_ids"])})
            continue
        build = option_row if g["product"] == OPTION else future_row
        rows.append(build(conn, g, roots.get(g["root_key"]), as_of))
    for g, root, per_lot in lme:
        if not g["bad_quantity"] and abs(g["lots"]) <= _FLAT:
            flat.append({"product": LME, "root_id": g["root_key"], "contract_id": f"{g['instrument_id']} {g['expiry']}",
                         "expiry": g["expiry"], "trade_ids": list(g["trade_ids"])})
            continue
        rows.append(lme_row(conn, g, root, per_lot, as_of))
    rows.sort(key=lambda r: (r["sector"], r["root_id"], r["expiry"], r["contract_id"]))
    reasons += [f"{r['contract_id']}: {r['reason']}" for r in rows if r["reason"]]

    exposure, exposure_reasons = _currency_exposure(conn, mine, as_of)
    reasons += exposure_reasons
    months = sorted({month_key(r["year"], r["month"]) for r in rows if r["year"] is not None})
    note = "" if rows else f"every open commodity future is flat on {as_of}"
    by_commodity = _by_commodity(rows)
    left, left_reasons = _leftover(conn, as_of, rows, roots, spreads)
    reasons += left_reasons
    return {"as_of": as_of, "available": True, "note": note, "rows": rows, "flat_contracts": flat,
            "by_commodity": by_commodity, "by_subsector": _by_subsector(rows, by_commodity, roots, left),
            "by_sector": _by_sector(rows),
            "currency_exposure": exposure, "months": months,
            "products_present": [p for p in _PRODUCT_ORDER if any(r["product"] == p for r in rows)],
            "reasons": reasons}
