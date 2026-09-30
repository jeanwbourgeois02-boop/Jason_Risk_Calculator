"""Carry (roll-down) of the book's calendar spreads, from the chain's own Bloomberg closes (user,
2026-09-29; from the book database's ``price_history`` since 2026-09-30, when the app stopped
reading the research app's database). CONTEXT: never a mark, never in P&L, delta or a total
(hard rule 2), and it asks Bloomberg nothing (hard rule 8).

For a term-structure pair, near month Mi against far month Mj of one root, the roll-down is
what the pair's level would do over the next contract step if the curve's shape stayed as it is:
the held contracts move up the curve into the places Mi-1 and Mj-1 hold today, so

    roll_down = spread(Mi-1, Mj-1) - spread(Mi, Mj)

in the pair's own unit, the Book's level convention (near - far, the quoted price x the root's
``price_scale``, ``levels.py``). Mi-1 is the root's previous month of its contract cycle
(contract-master's ``active_months``: one calendar month on a monthly root such as WTI, so the
horizon is a month; the step is said in ``horizon_months`` when the cycle is wider). In USD for
the position: ``roll_down x usd_per_unit``, the position's own USD of a 1.0 rise of its level
on its open lots, which carries the position's direction, so + means the curve's shape pays the
position.

The curve: ``engine.risk.commodity_history`` (read-only, Bloomberg's daily closes of every
contract of each held root's chain, in the book database's ``price_history``; our canonical
contract ids are the ids on file). The four contracts are read on their latest common close on
or before the as-of date, so the two spreads come from one curve. The price is Bloomberg's
quoted close (the unit of our FUTURE_PX marks) x our ``price_scale``, so the roll-down is in the
Book's unit.

None with the reason when the near month is already the front contract (Mi-1 has expired, or
has no close on file), a price is missing, or no price history is on file yet; "carry shown for
calendar spreads only" for every other kind of position or pair.
"""

from __future__ import annotations

import math
import sqlite3
from typing import List, Optional, Sequence, Tuple

import pandas as pd

from data.contracts import load_roots
from data.contracts.tickers import MONTH_CODES, make_contract_id

LABEL = "Bloomberg history"
CALENDAR_ONLY = "carry shown for calendar spreads only"


def no_history(history) -> str:
    """The reason no roll-down can be read: the history's own (it says to press Pull Bloomberg now)."""
    return str(getattr(history, "reason", "") or "no price history on file")


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _previous(root, month_key: str) -> Tuple[Optional[str], int, str]:
    """(the root's previous contract month of its cycle as 'YYYY-MM', months back, why when None)."""
    try:
        year, month = int(month_key[:4]), int(month_key[5:7])
    except (TypeError, ValueError):
        return None, 0, f"the leg's contract month {month_key!r} is not known"
    cycle = set(root.active_months or range(1, 13))
    for back in range(1, 13):
        m = month - back
        y = year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        if m in cycle:
            return f"{y:04d}-{m:02d}", back, ""
    return None, 0, f"{root.root_id} has no contract cycle to step back in"


def _contract_id(root, month_key: str) -> str:
    code = MONTH_CODES[int(month_key[5:7]) - 1]     # 'FGHJKMNQUVXZ', index 0 = January
    return make_contract_id(root.bbg_root, code, int(month_key[:4]), root.bbg_yellow_key)


def _blank(ident: str, source: str, name: str, kind: str, reason: str) -> dict:
    return {"id": ident, "source": source, "name": name, "kind": kind, "calendar": False, "label": LABEL,
            "root_id": "", "unit": "", "near": "", "far": "", "nearer_near": "", "nearer_far": "",
            "history_contracts": {}, "history_date": None, "level_curve": None, "level_rolled": None,
            "roll_down": None, "horizon_months": None, "usd_per_unit": None, "roll_down_usd": None,
            "reason": reason, "note": ""}


def _entries(spreads: dict) -> List[Tuple[str, str, str, str, Optional[dict], Optional[float], str, str]]:
    """(id, source, name, kind, level_spec, usd_per_unit, its reason, unit) of every Book position and
    every strategy pair."""
    out = []
    for p in spreads.get("positions") or []:
        out.append((str(p.get("position_id") or ""), "position", str(p.get("name") or ""), str(p.get("kind") or ""),
                    p.get("level_spec"), _num(p.get("usd_per_unit")), str(p.get("usd_per_unit_reason") or ""),
                    str(p.get("level_unit") or "")))
    for s in spreads.get("strategies") or []:
        for pair in s.get("pairs") or []:
            out.append((str(pair.get("pair_id") or ""), "pair", str(pair.get("pair_id") or ""),
                        str(pair.get("type") or ""), pair.get("level_spec"), _num(pair.get("usd_per_unit")),
                        str(pair.get("usd_per_unit_reason") or ""), str(pair.get("unit") or "")))
    return out


def _settles(history, contract_id: str, root_id: str, as_of: pd.Timestamp) -> Tuple[Optional[pd.Series], str, str]:
    """(the contract's RAW closes on or before ``as_of``, the id on file, why when None)."""
    rid, why = history.resolve_contract(contract_id, root_id)
    if rid is None:
        return None, "", why
    s = history.settle_series(contract_id, root_id)
    if s.empty:
        return None, rid, s.attrs.get("reason") or f"{contract_id} has no closes on file"
    scale = _num(s.attrs.get("price_scale")) or 1.0
    s = (s / scale)[s.index <= as_of]
    if s.empty:
        return None, rid, f"{contract_id} has no close on file on or before {as_of:%Y-%m-%d}"
    return s, rid, ""


def _one(history, roots, ident, source, name, kind, spec, upu, upu_why, unit, as_of: pd.Timestamp) -> dict:
    out = _blank(ident, source, name, kind, "")
    if not spec or str(spec.get("kind")) != "calendar" or len(spec.get("legs") or []) != 2:
        out["reason"] = CALENDAR_ONLY
        return out
    near, far = spec["legs"]
    root = roots.get(str(near.get("root_id") or ""))
    out.update(calendar=True, root_id=str(near.get("root_id") or ""), unit=unit or str(spec.get("unit") or ""),
               near=str(near["instrument_id"]), far=str(far["instrument_id"]), usd_per_unit=upu)
    if root is None:
        out["reason"] = f"{out['root_id']} is not a root of config/contracts.csv"
        return out
    if not history.available:
        out["reason"] = no_history(history)
        return out
    nearer = []
    for leg in (near, far):
        prev, back, why = _previous(root, str(leg.get("month_key") or ""))
        if prev is None:
            out["reason"] = why
            return out
        nearer.append((_contract_id(root, prev), back))
    out.update(nearer_near=nearer[0][0], nearer_far=nearer[1][0], horizon_months=nearer[0][1])
    if nearer[0][1] != nearer[1][1]:
        out["note"] = (f"the near leg steps back {nearer[0][1]} month(s) and the far leg {nearer[1][1]} on "
                       f"{root.root_id}'s contract cycle")
    # the nearer month of the near leg must still be listed on the as-of date
    rid, why = history.resolve_contract(nearer[0][0], root.root_id)
    if rid is None:
        out["reason"] = f"{out['near']} is the front contract on file: {why}"
        return out
    ltd = str(history.contracts.at[rid, "last_trade_date"] or "")
    if ltd and ltd < as_of.strftime("%Y-%m-%d"):
        out["reason"] = (f"{out['near']} is already the front contract: {nearer[0][0]} expired on {ltd}, so there is "
                         f"no nearer month on the curve to roll into")
        return out
    ids = (out["near"], out["far"], nearer[0][0], nearer[1][0])
    series, whys = [], []
    for cid in ids:
        s, rid, why = _settles(history, cid, root.root_id, as_of)
        out["history_contracts"][cid] = rid
        if s is None:
            whys.append(why)
        series.append(s)
    if whys:
        out["reason"] = "; ".join(whys)
        return out
    common = series[0].index
    for s in series[1:]:
        common = common.intersection(s.index)
    if common.empty:
        out["reason"] = "the four contracts share no close date on or before the as-of date"
        return out
    day = common.max()
    scale = float(near.get("price_scale") or 1.0)
    px = [float(s.loc[day]) * scale for s in series]
    out["history_date"] = day.strftime("%Y-%m-%d")
    latest = max(s.index.max() for s in series)
    if day < latest:
        out["note"] = "; ".join(x for x in (out["note"], f"read on {out['history_date']}, the latest date all four "
                                            f"contracts closed (one has {latest:%Y-%m-%d})") if x)
    out["level_curve"] = px[0] - px[1]
    out["level_rolled"] = px[2] - px[3]
    out["roll_down"] = out["level_rolled"] - out["level_curve"]
    if upu is None:
        out["reason"] = f"no USD per unit for the position ({upu_why or 'not known'}): the roll-down in its unit only"
    else:
        out["roll_down_usd"] = out["roll_down"] * upu
    return out


def curve_roll_downs(history, root, legs: Sequence[Tuple[str, str, str]], as_of: str,
                     memo: Optional[dict] = None) -> dict:
    """Each leg's own roll-down on ONE root's curve of Bloomberg closes (2026-09-29, the trade panel's
    roll-down per leg; the rule of ``carry`` applied leg by leg): a contract in month Mi moves up
    the curve into the place Mi-1 holds today, so ``roll_down = price(Mi-1) - price(Mi)``, in the
    root's quote unit (Bloomberg's quoted close x our ``price_scale``), over ``horizon_months``
    (the step back on the root's contract cycle). Every leg is read on the latest close date
    on or before ``as_of`` that ALL the legs' contracts and their nearer months share, so the legs
    of one curve add up (a calendar's legs sum to ``carry``'s own roll-down).

    ``legs``: ``(key, contract_id, month_key)`` of the legs, all on ``root`` (a ``ContractRoot``).
    Returns ``{history_date, reason, note, legs: {key: {contract_id, nearer, horizon_months,
    roll_down, history_contracts, reason}}}``: a leg that cannot be read (no month, already the
    front contract, no close on file, an LME prompt whose cash and 3M are not on file) is
    None with its reason and is left out of the common date. ``memo``: a dict the caller keeps
    over several calls on the same ``history`` and ``as_of`` (the trade book's), so a contract's
    lookup and closes are read once (2026-09-29, speed; the same figures).
    CONTEXT: never a mark, never in P&L, delta or a total."""
    memo = {} if memo is None else memo

    def resolve(cid: str):
        key = ("resolve", cid, root.root_id)
        if key not in memo:
            memo[key] = history.resolve_contract(cid, root.root_id)
        return memo[key]

    def settles(cid: str):
        key = ("settles", cid, root.root_id)
        if key not in memo:
            memo[key] = _settles(history, cid, root.root_id, ts)
        return memo[key]
    out = {"history_date": None, "reason": "", "note": "", "legs": {}}
    for key, cid, month in legs:
        out["legs"][key] = {"contract_id": cid, "nearer": "", "horizon_months": None, "roll_down": None,
                            "history_contracts": {}, "reason": ""}
    if not getattr(history, "available", False):
        out["reason"] = no_history(history)
        for leg in out["legs"].values():
            leg["reason"] = out["reason"]
        return out
    ts = pd.Timestamp(as_of).normalize()
    series = {}
    for key, cid, month in legs:
        leg = out["legs"][key]
        prev, back, why = _previous(root, month)
        if prev is None:
            leg["reason"] = why
            continue
        nearer = _contract_id(root, prev)
        leg.update(nearer=nearer, horizon_months=back)
        rid, why = resolve(nearer)
        if rid is None:
            leg["reason"] = f"{cid} is the front contract on file: {why}"
            continue
        ltd = str(history.contracts.at[rid, "last_trade_date"] or "")
        if ltd and ltd < as_of:
            leg["reason"] = (f"{cid} is already the front contract: {nearer} expired on {ltd}, so there is no "
                             f"nearer month on the curve to roll into")
            continue
        pair, whys = [], []
        for c in (cid, nearer):
            s, rid, why = settles(c)
            leg["history_contracts"][c] = rid
            if s is None:
                whys.append(why)
            pair.append(s)
        if whys:
            leg["reason"] = "; ".join(whys)
            continue
        series[key] = pair
    if not series:
        return out
    common = None
    for pair in series.values():
        for s in pair:
            common = s.index if common is None else common.intersection(s.index)
    if common is None or common.empty:
        for key in series:
            out["legs"][key]["reason"] = "the legs' contracts share no close date on or before the as-of date"
        return out
    day = common.max()
    out["history_date"] = day.strftime("%Y-%m-%d")
    latest = max(s.index.max() for pair in series.values() for s in pair)
    if day < latest:
        out["note"] = (f"read on {out['history_date']}, the latest date all the legs' contracts closed "
                       f"(one has {latest:%Y-%m-%d})")
    scale = float(root.price_scale or 1.0)
    for key, (s_leg, s_nearer) in series.items():
        out["legs"][key]["roll_down"] = (float(s_nearer.loc[day]) - float(s_leg.loc[day])) * scale
    return out


def carry(conn: sqlite3.Connection, as_of: str, spreads: Optional[dict] = None, history=None) -> dict:
    """The roll-down of every calendar position and term-structure pair of the Book on ``as_of``.

    ``spreads``: ``book_spreads(conn, as_of)`` when None. ``history``: a
    ``commodity_history.CommodityHistory`` (``load_commodity_history(conn)``, the book database's
    own ``price_history``, when None; a test may pass its own).

    Returns ``{as_of, label: 'Bloomberg history', rows, by_id, reason}``; ``rows`` one per Book position
    and per strategy pair: ``{id (position_id or pair_id), source ('position' | 'pair'), name,
    kind, calendar (bool), label, root_id, unit, near, far (our contract ids), nearer_near,
    nearer_far (the contracts one step nearer), history_contracts ({our id: the id on file}),
    history_date, level_curve (spread(Mi, Mj) on that date's closes), level_rolled
    (spread(Mi-1, Mj-1)), roll_down (their difference, the pair's unit), horizon_months,
    usd_per_unit, roll_down_usd (+ = the curve's shape pays the position), reason, note}``;
    a figure is None with ``reason`` when it cannot be read. ``by_id`` indexes ``rows``.
    Context only: nothing here is a mark, a P&L or a delta.
    """
    from engine.risk.commodity_history import load_commodity_history
    if spreads is None:
        from engine.spreads.book import book_spreads
        spreads = book_spreads(conn, as_of)
    out = {"as_of": as_of, "label": LABEL, "rows": [], "by_id": {}, "reason": ""}
    try:
        history = history if history is not None else load_commodity_history(conn)
    except Exception as exc:  # noqa: BLE001 -- context: a reason, never a failure
        out["reason"] = f"the price history could not be read ({type(exc).__name__}: {exc})"
        return out
    roots = load_roots()
    ts = pd.Timestamp(as_of).normalize()
    for entry in _entries(spreads):
        try:
            row = _one(history, roots, *entry, ts)
        except Exception as exc:  # noqa: BLE001 -- one row's reason, never the whole table
            row = _blank(entry[0], entry[1], entry[2], entry[3], f"could not be read ({type(exc).__name__}: {exc})")
        out["rows"].append(row)
    out["by_id"] = {r["id"]: r for r in out["rows"]}
    if not getattr(history, "available", False):
        out["reason"] = no_history(history)
    return out
