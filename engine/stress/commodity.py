"""What if? for the commodity book: each scenario of ``config/commodity_stress.yaml`` applied
to the book's commodity positions (futures, options on futures, LME forwards).

The positions are curve-positions' (``engine.curve.curve_positions``), taken as they are. The
stress is first order, on each position's own delta (``basis = 'delta'``): a position's
scenario P&L is ``delta_usd x move``, with ``delta_usd = delta_lots x multiplier x price x S``
(``delta_lots = lots x delta_factor``: 1 for a future or an LME forward, the averaging share of
a monthly-average contract inside its pricing month, the official DELTA of an option, priced at
its underlying future). Nothing here reads a mark, takes a delta of its own or re-prices
anything (CLAUDE.md hard rules 2 and 3). A position the scenario touches that has no USD delta
is named under ``missing`` with its reason and left out of the total, never counted as zero; a
move of exactly 0 is zero whatever the price, so it needs none.

Kinds (the YAML header documents each): ``outright`` (most specific move wins),
``curve`` (front / back, linear in months to expiry), ``spread`` (legs moved against each
other; a leg may name spreads of the book by id, template or family), ``fx`` (a currency
against the USD, applied to the P&L the non-USD positions hold in it) and ``replay`` (a past
window, moves from the research history through risk-history's ``window_move``).
"""

from __future__ import annotations

import datetime as dt
import math
import sqlite3
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from engine.stress.scenarios import (
    DEFAULT_CONFIG, SPREAD_LEVELS, ScenarioError, load_scenarios, norm, validate_scenarios,
)

BASIS = "delta"
DAYS_PER_MONTH = 365.25 / 12

# (root_id, months_to_expiry, start, end) -> (fractional move or None, reason), or a dict with
# at least {move, reason} (risk-history's window_move_detail shape, whose other keys are kept).
HistoryFn = Callable[[str, float, str, str], object]


def _number(value) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ---- which positions a selector names -------------------------------------------------------

def _spread_tags(spreads: Optional[dict]) -> Dict[str, Dict[str, set]]:
    """{instrument_id: {spread: ids, template: ids, family: names}} over the legs with open lots
    of the book's open spreads."""
    tags: Dict[str, Dict[str, set]] = {}
    for sp in (spreads or {}).get("spreads") or []:
        if sp.get("status") != "open":
            continue
        for leg in sp.get("legs") or []:
            if not _number(leg.get("open_lots")):
                continue
            t = tags.setdefault(str(leg.get("instrument_id") or ""),
                                {"spread": set(), "template": set(), "family": set()})
            t["spread"].add(norm("spread", sp.get("spread_id")))
            if sp.get("template"):
                t["template"].add(norm("template", sp.get("template")))
            if sp.get("family"):
                t["family"].add(norm("family", sp.get("family")))
    return tags


def _matches(select: Dict[str, List[str]], row: dict) -> bool:
    for level, names in select.items():
        if level in SPREAD_LEVELS:
            if not (row.get("_tags", {}).get(level, set()) & set(names)):
                return False
        elif norm(level, row.get("root_id" if level == "root" else level)) not in names:
            return False
    return True


def _selects_spreads(s: dict) -> bool:
    selects = [r["select"] for r in s.get("rules", [])] + [s.get("select") or {}]
    return any(level in SPREAD_LEVELS for sel in selects for level in sel)


def _months_to_expiry(row: dict, as_of: str) -> Tuple[Optional[float], str]:
    try:
        days = (dt.date.fromisoformat(str(row.get("expiry"))[:10]) - dt.date.fromisoformat(as_of)).days
    except ValueError:
        return None, f"expiry {row.get('expiry')!r} is not a date"
    return days / DAYS_PER_MONTH, ""


def _missing(row: dict, reason: str) -> dict:
    return {"contract_id": row.get("contract_id", ""), "instrument_id": row.get("instrument_id", ""),
            "product": row.get("product", ""), "root_id": row.get("root_id", ""),
            "currency": row.get("currency", ""), "reason": reason}


def _position_pnl(row: dict, move: float) -> Tuple[Optional[float], str]:
    """(pnl_usd, reason): the position's own USD delta x move; a zero move is zero."""
    if move == 0:
        return 0.0, ""
    delta = _number(row.get("delta_usd"))
    if delta is None:
        return None, row.get("reason") or "no USD delta on the day"
    return delta * move, ""


# ---- the move each kind gives a position --------------------------------------------------

def _rule_move(rules: List[dict], row: dict) -> Tuple[bool, Optional[float], str]:
    """(touched, move, reason) under outright / spread rules: the most specific match wins;
    two equally specific matches with different moves are ambiguous (move None)."""
    hits = [r for r in rules if _matches(r["select"], row)]
    if not hits:
        return False, None, ""
    best = max(r["rank"] for r in hits)
    moves = {r["move"] for r in hits if r["rank"] == best}
    if len(moves) > 1:
        return True, None, (f"matched by two equally specific parts of the scenario with different moves "
                            f"({', '.join(f'{m:+.2%}' for m in sorted(moves))})")
    return True, moves.pop(), ""


def _curve_move(s: dict, row: dict, as_of: str) -> Tuple[bool, Optional[float], str]:
    if s["select"] and not _matches(s["select"], row):
        return False, None, ""
    months, why = _months_to_expiry(row, as_of)
    if months is None:
        return True, None, why
    lo, hi = s["front_months"], s["back_months"]
    if months <= lo:
        return True, s["front"], ""
    if months >= hi:
        return True, s["back"], ""
    w = (months - lo) / (hi - lo)
    return True, s["front"] + w * (s["back"] - s["front"]), ""


def default_history() -> Tuple[Optional[HistoryFn], str]:
    """risk-history's window move on the research history it finds, or (None, why) when the
    module or the history is not there."""
    try:
        from engine.risk.commodity_history import load_commodity_history
    except ImportError as exc:
        return None, (f"the commodity settlement history (risk-history, engine/risk/commodity_history.py) "
                      f"is not available: {exc}")
    try:
        history = load_commodity_history()
    except Exception as exc:  # the history is a risk input: its fault is named, never raised
        return None, f"the commodity settlement history could not be read: {exc}"
    if not history.available:
        return None, history.reason or "the commodity settlement history is not available"
    return history.window_move_detail, ""


def _replay_move(history: HistoryFn, s: dict, row: dict,
                 as_of: str) -> Tuple[bool, Optional[float], str, Optional[dict]]:
    months, why = _months_to_expiry(row, as_of)
    if months is None:
        return True, None, why, None
    try:
        got = history(row.get("root_id", ""), months, s["start"], s["end"])
    except Exception as exc:  # a history fault is this position's n/a, never the whole tab's
        return True, None, f"history lookup failed: {exc}", None
    if isinstance(got, dict):
        detail = {k: v for k, v in got.items() if k not in ("move", "reason")}
        move, reason = got.get("move"), got.get("reason") or ""
    else:
        detail = None
        move, reason = got if isinstance(got, tuple) else (got, "")
    move = _number(move)
    if move is None:
        return True, None, reason or f"no history for {row.get('root_id')} over {s['start']} to {s['end']}", detail
    return True, move, "", detail


# ---- one scenario ------------------------------------------------------------------------------

def _reason(missing: List[dict], touched: bool, nothing: str) -> str:
    if missing:
        return (f"excludes {len(missing)} position(s) with no figure: "
                + "; ".join(f"{m['contract_id'] or m['currency']}: {m['reason']}" for m in missing))
    return "" if touched else nothing


def _summarise(s: dict, contributions: List[dict], missing: List[dict], extra: Optional[dict] = None) -> dict:
    by_sector: Dict[str, float] = {}
    by_root: Dict[str, float] = {}
    for c in contributions:
        by_sector[c["sector"]] = by_sector.get(c["sector"], 0.0) + c["pnl_usd"]
        by_root[c["root_id"]] = by_root.get(c["root_id"], 0.0) + c["pnl_usd"]
    if contributions:
        total: Optional[float] = float(sum(c["pnl_usd"] for c in contributions))
    else:
        total = None if missing else 0.0
    out = {"name": s["name"], "kind": s["kind"], "description": s.get("description", ""), "basis": BASIS,
           "total_usd": total, "by_sector": by_sector,
           "by_root": [{"root_id": k, "pnl_usd": v} for k, v in by_root.items()],
           "by_contract": contributions, "missing": missing,
           "reason": _reason(missing, bool(contributions), "no position in the scenario's scope")}
    out.update(extra or {})
    return out


def _na(s: dict, reason: str, extra: Optional[dict] = None) -> dict:
    out = {"name": s["name"], "kind": s["kind"], "description": s.get("description", ""), "basis": BASIS,
           "total_usd": None, "by_sector": {}, "by_root": [], "by_contract": [], "missing": [],
           "reason": reason}
    out.update(extra or {})
    return out


def _positional(s: dict, rows: List[dict], as_of: str, history: Optional[HistoryFn]) -> dict:
    contributions, missing = [], []
    for row in rows:
        detail = None
        if s["kind"] == "curve":
            touched, move, why = _curve_move(s, row, as_of)
        elif s["kind"] == "replay":
            touched, move, why, detail = _replay_move(history, s, row, as_of)
        else:
            touched, move, why = _rule_move(s["rules"], row)
        if not touched:
            continue
        if move is None:
            missing.append(_missing(row, why))
            continue
        pnl, why = _position_pnl(row, move)
        if pnl is None:
            missing.append(_missing(row, why))
            continue
        c = {"contract_id": row.get("contract_id", ""), "instrument_id": row.get("instrument_id", ""),
             "product": row.get("product", ""), "root_id": row.get("root_id", ""),
             "sector": row.get("sector", ""), "expiry": row.get("expiry", ""), "lots": row.get("lots"),
             "delta_lots": row.get("delta_lots"), "delta_usd": row.get("delta_usd"), "move": move,
             "pnl_usd": pnl}
        if detail is not None:
            c["history"] = detail
        contributions.append(c)
    extra = {"start": s["start"], "end": s["end"]} if s["kind"] == "replay" else None
    return _summarise(s, contributions, missing, extra)


def _fx(s: dict, positions: dict) -> dict:
    """The P&L the non-USD positions hold in a moved currency (curve-positions'
    currency_exposure), revalued: pnl_usd x move (S' = S x (1 + move)). The change in their USD
    delta is reported beside it: it moves the exposure, not the P&L."""
    moves = s["moves"]
    by_currency, missing = [], []
    exposure = positions.get("currency_exposure") or {}
    rows = positions.get("rows") or []
    for ccy in sorted(set(exposure) | {r.get("currency", "") for r in rows}):
        move = moves.get(ccy)
        if move is None and ccy == "CNY":
            move = moves.get("CNH")
        if move is None or ccy in ("", "USD"):
            continue
        exp = exposure.get(ccy) or {}
        pnl_usd = _number(exp.get("pnl_usd")) if exp else 0.0
        if exp and pnl_usd is None:
            missing.append({"contract_id": "", "instrument_id": "", "product": "", "root_id": "",
                            "currency": ccy, "reason": exp.get("reason") or f"no USD P&L held in {ccy}"})
        delta, change = 0.0, 0.0
        for r in rows:
            if r.get("currency") != ccy:
                continue
            d = _number(r.get("delta_usd"))
            if d is None:
                missing.append(_missing(r, "USD delta change: " + (r.get("reason") or "no USD delta")))
                continue
            delta += d
            change += d * move
        by_currency.append({"currency": ccy, "move": move,
                            "pnl_local": _number(exp.get("pnl_local")) if exp else 0.0,
                            "pnl_usd": pnl_usd, "pnl_change_usd": None if pnl_usd is None else pnl_usd * move,
                            "delta_usd": delta, "delta_usd_change": change})
    known = [c["pnl_change_usd"] for c in by_currency if c["pnl_change_usd"] is not None]
    no_pnl = [c for c in by_currency if c["pnl_change_usd"] is None]
    total = float(sum(known)) if known or not no_pnl else None
    return {"name": s["name"], "kind": "fx", "description": s.get("description", ""), "basis": BASIS,
            "total_usd": total, "by_sector": {}, "by_root": [], "by_contract": [], "missing": missing,
            "reason": _reason(missing, bool(by_currency), "no commodity position in a moved currency"),
            "by_currency": by_currency,
            "delta_usd_change": float(sum(c["delta_usd_change"] for c in by_currency))}


# ---- the book's spreads under a scenario ---------------------------------------------------------

def _by_spread(result: dict, spreads: dict) -> List[dict]:
    """Each open spread's share of a positional scenario's P&L, from its legs' open lots:
    leg P&L = open_lots x the scenario's P&L per lot held of that contract (the contract's
    P&L / its lots, so an averaging or option leg keeps its delta factor). The leftover
    outright (the lots beyond the whole spread, per root) is its own USD notional, as
    spreads-engine gives it, x the move the scenario gives the spread's legs of that root; when
    those legs move differently (a calendar under a curve scenario) the leftover's month is not
    known and it is None with the reason. The leftover is part of the legs' open lots, so
    ``pnl_usd`` includes it and ``spread_pnl_usd`` is the spread without it."""
    per_lot: Dict[str, Tuple[float, float]] = {}      # instrument_id -> (pnl, lots)
    moved: Dict[str, float] = {}                      # instrument_id -> the scenario's move
    for c in result["by_contract"]:
        pnl, lots = per_lot.get(c["instrument_id"], (0.0, 0.0))
        per_lot[c["instrument_id"]] = (pnl + c["pnl_usd"], lots + (_number(c.get("lots")) or 0.0))
        moved[c["instrument_id"]] = c["move"]
    missing = {m["instrument_id"]: m["reason"] for m in result["missing"] if m.get("instrument_id")}

    out = []
    for sp in (spreads or {}).get("spreads") or []:
        if sp.get("status") != "open":
            continue
        legs, why, root_moves = [], [], {}
        for leg in sp.get("legs") or []:
            iid, open_lots = str(leg.get("instrument_id") or ""), _number(leg.get("open_lots"))
            rate: Optional[float]
            if open_lots is None:
                rate, reason = None, f"{iid}: open lots not known"
            elif iid in missing:
                rate, reason = None, f"{iid}: {missing[iid]}"
            elif iid in per_lot:
                pnl, lots = per_lot[iid]
                rate, reason = (pnl / lots, "") if lots else (None, f"{iid}: no lots on the curve")
            else:
                rate, reason = 0.0, ""          # the scenario does not move this contract
            leg_pnl = None if rate is None or open_lots is None else open_lots * rate
            if reason:
                why.append(reason)
            legs.append({"instrument_id": iid, "root_id": leg.get("root_id", ""), "open_lots": open_lots,
                         "pnl_usd": leg_pnl, "reason": reason})
            if open_lots:
                root_moves.setdefault(leg.get("root_id", ""), set()).add(
                    None if iid in missing else moved.get(iid, 0.0))
        leftover = []
        for left in sp.get("leftover") or []:
            root_id, lots = left.get("root_id", ""), _number(left.get("lots"))
            notional = _number(left.get("usd_notional"))
            moves = root_moves.get(root_id, {0.0})
            if lots == 0:
                leftover.append({"root_id": root_id, "lots": 0.0, "pnl_usd": 0.0, "reason": ""})
            elif lots is None or None in moves or len(moves) != 1 or notional is None:
                reason = ("leftover lots not known" if lots is None
                          else "a leg of this root has no figure" if None in moves
                          else "legs of this root move differently, so the leftover's month is not known"
                          if len(moves) != 1 else left.get("reason") or "leftover has no USD notional")
                leftover.append({"root_id": root_id, "lots": lots, "pnl_usd": None, "reason": reason})
            else:
                leftover.append({"root_id": root_id, "lots": lots, "pnl_usd": notional * next(iter(moves)),
                                 "reason": ""})
        total = None if why else float(sum(leg["pnl_usd"] for leg in legs))
        left_known = [x["pnl_usd"] for x in leftover]
        leftover_pnl = None if any(x is None for x in left_known) else float(sum(left_known))
        out.append({"spread_id": sp.get("spread_id", ""), "name": sp.get("name", ""),
                    "family": sp.get("family", ""), "template": sp.get("template", ""),
                    "pnl_usd": total, "legs": legs, "leftover": leftover, "leftover_pnl_usd": leftover_pnl,
                    "spread_pnl_usd": None if total is None or leftover_pnl is None else total - leftover_pnl,
                    "reason": "; ".join(why)})
    return out


# ---- entry point ---------------------------------------------------------------------------------

def commodity_stress(conn: Optional[sqlite3.Connection], as_of: str,
                     scenarios: Union[None, str, Path, List[dict]] = None, *,
                     positions: Optional[dict] = None, spreads: Optional[dict] = None,
                     history: Optional[HistoryFn] = None) -> dict:
    """Every commodity scenario applied to the book on ``as_of``.

    ``scenarios``: None = ``config/commodity_stress.yaml``; a path = that file; a list = raw
    scenario dicts (validated the same way). ``positions``: curve-positions' output for
    ``as_of`` if the caller has it (else ``curve_positions(conn, as_of)`` is called).
    ``spreads``: spreads-engine's ``book_spreads`` output for ``as_of`` if the caller has it;
    else it is read with ``book_spreads(conn, as_of)`` only when a scenario selects by spread,
    template or family. ``history``: the replay moves, ``(root_id, months_to_expiry, start,
    end) -> (move, reason)`` or a ``{move, reason, ...}`` dict; None = risk-history's
    ``window_move_detail`` on the research history it finds.

    Returns ``{as_of, available, basis, config, scenarios, reasons}``; each scenario is
    ``{name, kind, description, basis ('delta'), total_usd, by_sector {sector: usd}, by_root
    [{root_id, pnl_usd}], by_contract [{contract_id, instrument_id, product, root_id, sector,
    expiry, lots, delta_lots, delta_usd, move, pnl_usd, history (a replay: the history's
    contract, dates and settles)}], missing [{contract_id, instrument_id, product, root_id,
    currency, reason}], reason}``, plus ``start`` / ``end`` for a replay, ``by_currency
    [{currency, move, pnl_local, pnl_usd, pnl_change_usd, delta_usd, delta_usd_change}]`` /
    ``delta_usd_change`` for an fx scenario (whose by_root and by_sector are empty: its P&L is
    per currency), and, when the book's spreads are known, ``by_spread [{spread_id, name,
    family, template, pnl_usd, spread_pnl_usd, leftover_pnl_usd, legs [{instrument_id,
    root_id, open_lots, pnl_usd, reason}], leftover [{root_id, lots, pnl_usd, reason}],
    reason}]`` for every positional scenario (open spreads only). ``total_usd`` is None (n/a)
    only when nothing the scenario touches has a figure, with ``reason`` saying why.
    """
    reasons: List[str] = []
    config = ""
    try:
        if scenarios is None or isinstance(scenarios, (str, Path)):
            path = Path(scenarios) if scenarios is not None else DEFAULT_CONFIG
            config = str(path)
            parsed = load_scenarios(path)
        else:
            parsed = validate_scenarios(list(scenarios))
    except (OSError, ScenarioError) as exc:
        return {"as_of": as_of, "available": False, "basis": BASIS, "config": config, "scenarios": [],
                "reasons": [f"the stress scenarios could not be read: {exc}"]}

    if positions is None:
        from engine.curve import curve_positions
        positions = curve_positions(conn, as_of)
    if not positions.get("available", True):
        why = "; ".join(positions.get("reasons") or []) or "the book's positions are not available"
        return {"as_of": as_of, "available": False, "basis": BASIS, "config": config,
                "scenarios": [_na(s, why) for s in parsed], "reasons": [why]}

    spreads_why = ""
    if spreads is None and any(_selects_spreads(s) for s in parsed):
        if conn is None:
            spreads_why = "the book's spreads are not known (no database given)"
        else:
            try:
                from engine.spreads import book_spreads
                spreads = book_spreads(conn, as_of)
            except Exception as exc:  # the spreads are an input: their fault is named, never raised
                spreads_why = f"the book's spreads could not be read: {exc}"
        if spreads_why:
            reasons.append(spreads_why)
    tags = _spread_tags(spreads)
    rows = [dict(r, _tags=tags.get(str(r.get("instrument_id") or ""), {})) for r in positions.get("rows") or []]

    hist_fn, hist_why = history, ""
    if history is None and any(s["kind"] == "replay" for s in parsed):
        hist_fn, hist_why = default_history()

    results = []
    for s in parsed:
        if s["kind"] == "fx":
            res = _fx(s, positions)
        elif s["kind"] == "replay" and hist_fn is None:
            res = _na(s, hist_why, {"start": s["start"], "end": s["end"]})
        elif spreads_why and _selects_spreads(s):
            res = _na(s, spreads_why)
        else:
            res = _positional(s, rows, as_of, hist_fn)
        if spreads is not None and s["kind"] != "fx":
            res["by_spread"] = [] if (res["total_usd"] is None and not res["by_contract"]) else \
                _by_spread(res, spreads)
        if res["reason"] and res["total_usd"] is None:
            reasons.append(f"{s['name']}: n/a, {res['reason']}")
        results.append(res)
    return {"as_of": as_of, "available": True, "basis": BASIS, "config": config, "scenarios": results,
            "reasons": reasons}
