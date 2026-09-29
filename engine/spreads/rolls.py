"""Rolls in the book, read off Jason's own fills (user, 2026-09-29: "positive and negative carry
from rolling contracts ... a big deal here"). A label on P&L the book already holds: a roll's
P&L is in every figure through its trades' ``value_book`` rows; nothing here is a mark, a P&L
formula or a delta, and no mark but the conversion SPOT is read.

**The rule** (the written grouping rule's style, ``engine/spreads/grouping.py``):

* Futures only (product FUTURE). The export books one contract per row, so a roll is always two
  trades (or two sets of fills); there is no single calendar-spread fill to read.
* A *position* is held per (account, trade name, root, contract): ``trades.strategy`` is Jason's
  position, so a trade of another trade name never rolls this one, and the roll is attributed to
  that one name. The position held in a contract on date D is the sum of that position's trades
  dated before D (a contract already expired before D holds nothing).
* On one trade date D, a *reducing* trade in month Mi (its side opposite to the position held in
  Mi before D) and a trade in the position's own direction in another month Mj of the same root,
  same account and trade name, whose lots agree within ``TOLERANCE`` (5 %, the spreads rule:
  ``(max - min) / max``), form a roll. Lots rolled = the smaller of the two, capped at what was
  held in Mi before D (less what other rolls of that day already moved out of it). Mj later than
  Mi is a roll *out* (the usual roll); Mj earlier is a roll *in*, kept and labelled.
* Matching is trade against trade first, and only where the pair is unique both ways; the
  trades left then are pooled per contract and side for that day (a roll done in several fills
  per leg) and matched the same way. A trade is used in one roll at most. A reducing trade with
  two or more possible partners is *ambiguous*: listed in ``excluded``, never guessed; one whose
  nearest partner is outside 5 % is listed as ``lots_off``.

**Per roll:** ``roll_spread`` = price of the month sold - price of the month bought, in the
fills' quoted unit (``roll_spread_unit`` in the root's quote unit, x ``price_scale``); a long
sells Mi and buys Mj, a short buys Mi and sells Mj. + = the roll earned carry (a long rolled out
in backwardation, a short rolled out in contango), - = it cost carry. ``roll_yield_usd`` =
``roll_spread x lots x multiplier x S``, S = USD per unit of the contract's currency at the last
official SPOT on or before the trade date (``valuation.usd_per_quote_on_or_before``: exact
official rows, USD<ccy> inverted first, the pair and its date named; never a forward, never an
estimate). No spot: the roll stays listed with ``roll_yield_usd`` None and its reason, and is
out of every USD total (counted in ``excluded``). The curve's shape at the roll is read off the
two fills: far - near > 0 contango, < 0 backwardation.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from data.contracts import UnknownContract, contract_for, load_roots
from engine.spreads.grouping import TOLERANCE

_EPS = 1e-9
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _month_label(key: str) -> str:
    """'2026-11' -> 'Nov26'."""
    try:
        y, m = key.split("-")
        return f"{_MONTHS[int(m) - 1]}{y[2:]}"
    except (ValueError, IndexError):
        return key


def _read_futures(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    strategy = "COALESCE(t.strategy, '')" if _has_column(conn, "trades", "strategy") else "''"
    sql = f"""
        SELECT t.trade_id, t.instrument_id, t.trade_date, t.quantity, t.price, t.account,
               {strategy} AS strategy, i.base_ccy, i.quote_ccy, i.multiplier, i.expiry_date
        FROM trades_official t JOIN instruments i USING (instrument_id)
        WHERE t.product = 'FUTURE' AND t.trade_date <= :as_of
        ORDER BY t.trade_date, t.trade_id"""
    cols = ("trade_id", "instrument_id", "trade_date", "quantity", "price", "account", "strategy",
            "base_ccy", "quote_ccy", "multiplier", "expiry_date")
    out = [dict(zip(cols, r)) for r in conn.execute(sql, {"as_of": as_of})]
    for t in out:
        t["trade_id"] = str(t["trade_id"])
        t["strategy"] = str(t["strategy"] or "").strip()
        t["account"] = str(t["account"] or "")
    return out


def _lots_agree(a: float, b: float) -> bool:
    hi = max(abs(a), abs(b))
    return hi > _EPS and (hi - min(abs(a), abs(b))) / hi <= TOLERANCE + _EPS


def _deviation(a: float, b: float) -> float:
    hi = max(abs(a), abs(b))
    return (hi - min(abs(a), abs(b))) / hi if hi > _EPS else 1.0


class _Side:
    """One side of a roll: a single trade or the pooled unmatched fills of one contract and side."""

    def __init__(self, trades: Sequence[dict]):
        self.trades = list(trades)
        self.ids = [t["trade_id"] for t in self.trades]
        self.inst = self.trades[0]["instrument_id"]
        self.month = self.trades[0]["month_key"]
        self.qty = sum(float(t["quantity"]) for t in self.trades)
        self.price = (sum(abs(float(t["quantity"])) * float(t["price"]) for t in self.trades)
                      / sum(abs(float(t["quantity"])) for t in self.trades))


def _pairs(reducers: Sequence[_Side], partners: Sequence[_Side]) -> Tuple[List[Tuple[_Side, _Side]], List[dict]]:
    """(unique-both-ways matches, the reducers with two or more possible partners or a near miss).
    An edge: the partner is in another month, and the lots agree within the tolerance (the side's
    direction is the caller's filter)."""
    edges: Dict[int, List[int]] = defaultdict(list)
    back: Dict[int, List[int]] = defaultdict(list)
    for i, r in enumerate(reducers):
        for j, p in enumerate(partners):
            if p.month != r.month and _lots_agree(r.qty, p.qty):
                edges[i].append(j)
                back[j].append(i)
    matches, open_ = [], []
    for i, r in enumerate(reducers):
        js = edges.get(i, [])
        if len(js) == 1 and len(back[js[0]]) == 1:
            matches.append((r, partners[js[0]]))
        elif js:
            open_.append({"kind": "ambiguous", "reducer": r, "partners": [partners[j] for j in js]})
        else:
            near = [p for p in partners if p.month != r.month]
            if near:
                best = min(near, key=lambda p: _deviation(r.qty, p.qty))
                open_.append({"kind": "lots_off", "reducer": r, "partners": [best]})
    return matches, open_


def _spot(conn: sqlite3.Connection, ccy: str, day: str) -> Tuple[Optional[float], str, str]:
    """(USD per unit of ``ccy`` at the last official SPOT on or before ``day``, how it was read,
    why when None)."""
    from engine.pnl.valuation import usd_per_quote_on_or_before
    if ccy == "USD":
        return 1.0, "USD contract", ""
    try:
        s, pair, source, spot_day = usd_per_quote_on_or_before(conn, ccy, day)
    except ValueError as exc:  # valuation._BadValue: a stored spot that is not a number
        return None, "", f"the {ccy} SPOT on or before {day} is not a number ({exc})"
    s = _num(s)
    if not s:
        return None, "", f"no official SPOT for USD conversion of {ccy} on or before {day}"
    note = f"{pair} official SPOT of {spot_day}" + ("" if spot_day == day else f" (last on or before {day})")
    if pair and pair.startswith("USD"):
        note += ", inverted"
    return s, note, ""


def _positions_by_trade(spreads: Optional[dict]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in (spreads or {}).get("positions") or []:
        pid = str(p.get("position_id") or p.get("name") or "")
        for tid in p.get("trade_ids") or []:
            out.setdefault(str(tid), pid)
    return out


def _summary(rolls_: Sequence[dict], key: str, extra) -> List[dict]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in rolls_:
        groups[r[key]].append(r)
    out = []
    for k, rs in sorted(groups.items()):
        inc = [r for r in rs if r["roll_yield_usd"] is not None]
        row = {key: k, **extra(rs[0]), "rolls": len(rs), "lots": sum(r["lots"] for r in rs),
               "roll_yield_usd": sum(r["roll_yield_usd"] for r in inc) if inc else None,
               "roll_yield_out_usd": sum(r["roll_yield_usd"] for r in inc if r["roll_kind"] == "out") + 0.0,
               "roll_yield_in_usd": sum(r["roll_yield_usd"] for r in inc if r["roll_kind"] == "in") + 0.0,
               "excluded": len(rs) - len(inc),
               "excluded_reasons": [f"{r['date']} {r['label']}: {r['reason']}" for r in rs if r not in inc]}
        if not inc and rs:
            row["reason"] = "no roll of this group could be converted to USD"
        out.append(row)
    return out


def rolls(conn: sqlite3.Connection, as_of: str, spreads: Optional[dict] = None) -> dict:
    """The rolls in the book's futures fills on or before ``as_of`` (the rule in the module
    docstring).

    ``spreads``: ``book_spreads(conn, as_of)`` when the caller has it, used only to name the Book
    position each roll's trades sit in (``position_ids``); never computed here, since the rolls
    need no mark.

    Returns ``{as_of, rolls, by_trade_name, by_root, total_usd, total_out_usd, total_in_usd,
    excluded, reason}``:

    * ``rolls``: one per roll, date order: ``{roll_id, date, account, trade_name, root_id,
      root_name, label ('Oct26 -> Dec26'), from_contract, to_contract (instrument ids),
      from_month, to_month ('YYYY-MM'), roll_kind ('out' to a later month | 'in' to an earlier
      one), direction ('long' | 'short', the position rolled), lots, lots_traded (the smaller
      trade's lots, before the cap at what was held), held_before (lots held in the from month
      before the date, signed), from_price, to_price (lots-weighted fills, quoted), sold_price,
      bought_price, roll_spread (sold - bought, quoted; + = carry earned), roll_spread_unit (x
      price_scale), unit, far_minus_near (quoted), curve ('contango' | 'backwardation' |
      'flat'), currency, multiplier, roll_yield_local, spot (USD per currency unit), spot_note,
      roll_yield_usd (None with ``reason`` when no spot), from_trade_ids, to_trade_ids,
      position_ids, matched ('trade' | 'pooled'), reason}``.
    * ``by_trade_name``: ``{trade_name, rolls, lots, roll_yield_usd (None when no roll of the
      group converts), roll_yield_out_usd, roll_yield_in_usd, excluded, excluded_reasons}``.
    * ``by_root``: the same keyed ``root_id``, with ``root_name`` and ``unit``.
    * ``total_usd``: the sum of every roll converted to USD (0.0 with no roll);
      ``total_out_usd`` / ``total_in_usd`` its rolls out and rolls in.
    * ``excluded``: ``{kind ('no_spot' | 'ambiguous' | 'lots_off' | 'not_checked'), date,
      account, trade_name, root_id, trade_ids, reason}``, every roll or possible roll left out
      of a figure, never silently.
    * ``reason``: '' or why there is nothing to read (no futures on file).
    """
    out = {"as_of": as_of, "rolls": [], "by_trade_name": [], "by_root": [], "total_usd": 0.0,
           "total_out_usd": 0.0, "total_in_usd": 0.0, "excluded": [], "reason": ""}
    trades = _read_futures(conn, as_of)
    if not trades:
        out["reason"] = f"no futures trades on or before {as_of}"
        return out
    roots = load_roots()
    pos_of = _positions_by_trade(spreads)
    groups: Dict[Tuple[str, str, str], List[dict]] = defaultdict(list)
    for t in trades:
        root = roots.get(str(t["base_ccy"]))
        month = ""
        if root is not None:
            try:
                cm = contract_for(root.root_id, str(t["instrument_id"]))
                month = f"{cm.year:04d}-{cm.month:02d}"
            except (UnknownContract, ValueError):
                month = ""
        if not month or _num(t["quantity"]) is None or _num(t["price"]) is None:
            out["excluded"].append({
                "kind": "not_checked", "date": t["trade_date"], "account": t["account"],
                "trade_name": t["strategy"], "root_id": str(t["base_ccy"]), "trade_ids": [t["trade_id"]],
                "reason": (f"{t['instrument_id']}: its contract month is not known to the contract universe"
                           if not month else f"{t['instrument_id']}: quantity or price is not a number"),
                })
            continue
        t["month_key"] = month
        groups[(t["account"], t["strategy"], root.root_id)].append(t)

    found: List[dict] = []
    for (account, name, root_id), ts in sorted(groups.items()):
        root = roots[root_id]
        by_day: Dict[str, List[dict]] = defaultdict(list)
        for t in ts:
            by_day[str(t["trade_date"])].append(t)
        for day in sorted(by_day):
            held: Dict[str, float] = defaultdict(float)
            for t in ts:
                if str(t["trade_date"]) < day and str(t["expiry_date"] or "9999-12-31") >= day:
                    held[t["instrument_id"]] += float(t["quantity"])
            todays = by_day[day]
            room = {inst: abs(q) for inst, q in held.items() if abs(q) > _EPS}
            if not room:
                continue

            def reducing(t, _held=held) -> bool:
                h = _held.get(t["instrument_id"], 0.0)
                return abs(h) > _EPS and float(t["quantity"]) * h < 0

            used: set = set()
            day_open: List[dict] = []
            for pooled in (False, True):
                left = [t for t in todays if t["trade_id"] not in used]
                red_trades = [t for t in left if reducing(t)]
                if pooled:
                    red = _pool(red_trades)
                else:
                    red = [_Side([t]) for t in red_trades]
                matches: List[Tuple[_Side, _Side]] = []
                opens: List[dict] = []
                # partners per direction of the position the reducer belongs to
                for sign in (1.0, -1.0):
                    r_s = [r for r in red if held[r.inst] * sign > 0]
                    p_trades = [t for t in left if float(t["quantity"]) * sign > 0 and not reducing(t)]
                    p_s = _pool(p_trades) if pooled else [_Side([t]) for t in p_trades]
                    m, o = _pairs(r_s, p_s)
                    matches += m
                    opens += o
                for r, p in matches:
                    lots_traded = min(abs(r.qty), abs(p.qty))
                    lots = min(lots_traded, room.get(r.inst, 0.0))
                    if lots <= _EPS:
                        continue
                    room[r.inst] -= lots
                    used.update(r.ids + p.ids)
                    found.append(_roll(conn, day, account, name, root, held[r.inst], r, p, lots, lots_traded,
                                       "pooled" if pooled else "trade", pos_of))
                day_open = opens  # the pooled pass's view is the final one
            for o in day_open:
                r = o["reducer"]
                if set(r.ids) & used:
                    continue
                parts = [p for p in o["partners"] if not set(p.ids) & used]
                if not parts:
                    continue
                ids = r.ids + [i for p in parts for i in p.ids]
                what = ", ".join(f"{p.inst} {p.qty:+g}" for p in parts)
                if o["kind"] == "ambiguous":
                    reason = (f"{r.inst} {r.qty:+g} reduces the {held[r.inst]:+g} lots held, and {len(parts)} trades in "
                              f"other months could be its roll ({what}): not guessed")
                else:
                    reason = (f"{r.inst} {r.qty:+g} reduces the {held[r.inst]:+g} lots held; the nearest trade the "
                              f"position's way in another month ({what}) is "
                              f"{_deviation(r.qty, parts[0].qty):.0%} off in lots (a roll needs "
                              f"{TOLERANCE:.0%}): not a roll")
                out["excluded"].append({"kind": o["kind"], "date": day, "account": account, "trade_name": name,
                                        "root_id": root_id, "trade_ids": ids, "reason": reason})

    found.sort(key=lambda r: (r["date"], r["root_id"], r["trade_name"], r["roll_id"]))
    for r in found:
        if r["roll_yield_usd"] is None:
            out["excluded"].append({"kind": "no_spot", "date": r["date"], "account": r["account"],
                                    "trade_name": r["trade_name"], "root_id": r["root_id"],
                                    "trade_ids": r["from_trade_ids"] + r["to_trade_ids"],
                                    "reason": f"{r['root_name']} {r['label']}: {r['reason']}"})
    out["rolls"] = found
    out["by_trade_name"] = _summary(found, "trade_name", lambda r: {})
    out["by_root"] = _summary(found, "root_id", lambda r: {"root_name": r["root_name"], "unit": r["unit"]})
    priced = [r for r in found if r["roll_yield_usd"] is not None]
    out["total_usd"] = sum(r["roll_yield_usd"] for r in priced) + 0.0
    out["total_out_usd"] = sum(r["roll_yield_usd"] for r in priced if r["roll_kind"] == "out") + 0.0
    out["total_in_usd"] = sum(r["roll_yield_usd"] for r in priced if r["roll_kind"] == "in") + 0.0
    out["excluded"].sort(key=lambda e: (e["date"], e["root_id"], e["kind"]))
    return out


def _pool(trades: Sequence[dict]) -> List[_Side]:
    """The day's unmatched fills pooled per contract and side."""
    by: Dict[Tuple[str, bool], List[dict]] = defaultdict(list)
    for t in trades:
        by[(t["instrument_id"], float(t["quantity"]) > 0)].append(t)
    return [_Side(v) for _, v in sorted(by.items())]


def _roll(conn, day: str, account: str, name: str, root, held: float, r: _Side, p: _Side, lots: float,
          lots_traded: float, matched: str, pos_of: Dict[str, str]) -> dict:
    long_ = held > 0
    sold, bought = (r.price, p.price) if long_ else (p.price, r.price)
    spread = sold - bought
    near, far = (r, p) if r.month < p.month else (p, r)
    fmn = far.price - near.price
    curve = "contango" if fmn > _EPS else "backwardation" if fmn < -_EPS else "flat"
    mult = _num(r.trades[0]["multiplier"]) or float(root.multiplier)
    ccy = str(r.trades[0]["quote_ccy"] or root.currency)
    local = spread * lots * mult
    s, note, why = _spot(conn, ccy, day)
    ids = sorted(r.ids + p.ids)
    return {
        "roll_id": f"ROLL-{day}-{min(ids)}", "date": day, "account": account, "trade_name": name,
        "root_id": root.root_id, "root_name": root.name,
        "label": f"{_month_label(r.month)} -> {_month_label(p.month)}",
        "from_contract": r.inst, "to_contract": p.inst, "from_month": r.month, "to_month": p.month,
        "roll_kind": "out" if p.month > r.month else "in", "direction": "long" if long_ else "short",
        "lots": lots, "lots_traded": lots_traded, "held_before": held,
        "from_price": r.price, "to_price": p.price, "sold_price": sold, "bought_price": bought,
        "roll_spread": spread, "roll_spread_unit": spread * float(root.price_scale), "unit": root.quote_unit,
        "far_minus_near": fmn, "curve": curve, "currency": ccy, "multiplier": mult,
        "roll_yield_local": local, "spot": s, "spot_note": note,
        "roll_yield_usd": local * s if s is not None else None,
        "from_trade_ids": sorted(r.ids), "to_trade_ids": sorted(p.ids),
        "position_ids": sorted({pos_of[t] for t in ids if t in pos_of}),
        "matched": matched, "reason": why,
    }
