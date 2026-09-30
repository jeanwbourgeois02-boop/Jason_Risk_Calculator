"""The liquidity check (user, 2026-09-29): each open position's size against its contracts' open
interest and average daily volume, because getting out is the risk on deferred months and on the
Chinese exchanges.

A RISK INPUT, labelled 'Bloomberg history': the open interest and volume are Bloomberg's
(OPEN_INT and PX_VOLUME, which the backfill stores in the book database's ``price_history``; read
through ``engine.risk.commodity_history.contract_liquidity``, since 2026-09-30). They are never a
mark, never in P&L or delta (hard rule 2), and nothing here asks Bloomberg (hard rule 8), reads
``marks`` or writes anything. The thresholds are ``config/limits.yaml``'s ``liquidity:`` section,
placeholders until Jason gives his (``placeholder`` says so).

What is checked: the contracts of the open commodity positions of curve-positions
(``engine.curve.curve_positions``): a future at its lots, an LME ticket at its lots (tonnes over
the lot's tonnes) on the metal's 3M pillar's figures (risk-history's rule), an option on a future at
its delta lots (option lots x its DELTA mark, curve-positions' ``delta_factor``) on its underlying
future; an option with no delta is counted nowhere and said so (the contract is tested on the
lots that are known, ``incomplete`` True and the option named in ``excluded`` and ``reason``;
with nothing known the level is NO_DATA). A contract a position bought and sold back is not a leg. The SGX USD/CNH future is a
future like any other. FX spot, forwards, swaps and FX options are not exchange contracts and are
listed in ``skipped``.

Per contract, with the size in the lots Bloomberg's figures count (ours x our lot / its lot
when both are sized in the same unit, else as they are, said in ``note``; the keys
``research_lots`` and ``research_contract_id`` keep their names):
  pct_of_oi     |lots| / open interest (the latest on or before as-of)
  pct_of_adv    |lots| / average daily volume (over the last ``window`` days with a volume)
  days_to_exit  |lots| / (participation x average daily volume)
  level         the worse of the open-interest test (AMBER from ``amber_pct_oi``, RED from
                ``red_pct_oi``) and the days-to-exit test (``amber_days_to_exit`` /
                ``red_days_to_exit``); a test whose figure is missing is not run (``tests`` says
                which ran, ``reason`` why not); with no test run the level is NO_DATA, never GREEN.
                Open interest of 0, or no volume over the window, with a position is RED.
No factor is applied to a Chinese exchange's figures (whether Bloomberg's open interest there is
single- or double-sided is not verified on a terminal): risk-history's note on it is passed
through in ``note``. A month with no Bloomberg volume or open interest on file (none stored, or
-1 = not given) has no figures: NO_DATA, "no liquidity data for this month", with its place in
the strip; never zero.

Two views of each contract:
  * ``contracts``: the book's net in it (every position and product netted: what one would have
    to exit) and its gross within the book (the positions' own |lots| added); the tests run on
    the net;
  * ``positions``: the Book's own positions (``engine.spreads.period_explain.positions_of`` over
    ``book_spreads``: 'POSITION-...', 'OUTRIGHT-<instrument_id>', 'TRADE-<trade_id>', the ids the
    Book, P&L and Risk use), each leg the position's own lots in one contract, the tests on
    them; the position's weakest leg is the one with the worst level, then the most days to exit,
    and the position's level and figures are its weakest leg's.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from data.contracts import load_roots
from engine.curve.rows import LME, OPTION
from engine.limits.config import LimitsConfig, LimitsConfigError, load_limits

GREEN, AMBER, RED, NO_DATA = "GREEN", "AMBER", "RED", "NO_DATA"
LIQUIDITY_LEVELS = (RED, AMBER, GREEN, NO_DATA)
_RANK = {NO_DATA: 0, GREEN: 1, AMBER: 2, RED: 3}
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
FX_SKIP = "FX: not an exchange contract"
LABEL = "Bloomberg history"
BASIS = ("risk input: Bloomberg volume and open interest (daily history stored in the book database), "
         "never a mark, never in P&L or delta")
PLACEHOLDER_NOTE = "thresholds are placeholders (config/limits.yaml, liquidity) until Jason gives his"
NO_MONTH_DATA = "no liquidity data for this month"
_EPS = 1e-9


def _num(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _unit(text: Any) -> str:
    return "".join(str(text or "").split()).lower()


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _params(config: Any) -> Tuple[Dict[str, Any], str]:
    """(the liquidity parameters, '' or why there are none)."""
    if isinstance(config, dict):
        return dict(config), ""
    if config is None:
        try:
            config = load_limits()
        except LimitsConfigError as exc:
            return {}, f"config/limits.yaml does not validate: {exc}"
    if isinstance(config, LimitsConfig):
        if not config.liquidity:
            return {}, (config.note or "config/limits.yaml has no liquidity section: no threshold is set")
        return dict(config.liquidity), ""
    return {}, f"config of type {type(config).__name__} is not a LimitsConfig or a mapping"


# ------------------------------------------------------------------ the tests
def _level_of(value: Optional[float], amber: Optional[float], red: Optional[float]) -> Optional[str]:
    if value is None or (amber is None and red is None):
        return None
    if red is not None and value >= red - _EPS:
        return RED
    if amber is not None and value >= amber - _EPS:
        return AMBER
    return GREEN


def _measure(lots: Optional[float], liq: Optional[dict], params: Dict[str, Any], factor: float) -> dict:
    """The figures and level of |lots| (ours) against one contract's Bloomberg volume and open interest."""
    out = {"research_lots": None, "open_interest": None, "oi_date": None, "adv": None, "adv_days": 0,
           "pct_of_oi": None, "pct_of_adv": None, "days_to_exit": None, "level": NO_DATA,
           "tests": {"open_interest": None, "days_to_exit": None}, "reason": ""}
    if liq is not None:
        out.update(open_interest=_num(liq.get("open_interest")), oi_date=liq.get("oi_date"),
                   adv=_num(liq.get("adv")), adv_days=int(liq.get("adv_days") or 0))
    if lots is None:
        out["reason"] = "no lots figure to test"
        return out
    size = abs(lots) * factor
    out["research_lots"] = size
    if liq is None:
        out["reason"] = "no Bloomberg volume and open interest for this contract"
        return out
    reasons: List[str] = []
    oi, adv = out["open_interest"], out["adv"]
    part = _num(params.get("participation"))
    oi_level = days_level = None
    if oi is None:
        reasons.append("open-interest test not run: no open interest")
    elif oi <= _EPS:
        if size > _EPS:
            oi_level = RED
            reasons.append(f"open interest is 0 on {liq.get('oi_date')}")
        else:
            out["pct_of_oi"] = 0.0
            oi_level = _level_of(0.0, _num(params.get("amber_pct_oi")), _num(params.get("red_pct_oi")))
    else:
        out["pct_of_oi"] = size / oi
        oi_level = _level_of(out["pct_of_oi"], _num(params.get("amber_pct_oi")), _num(params.get("red_pct_oi")))
    if adv is None:
        reasons.append("days-to-exit test not run: no volume")
    elif adv <= _EPS:
        if size > _EPS:
            days_level = RED
            reasons.append(f"no volume over the last {out['adv_days']} day(s) with a volume figure")
        else:
            out["pct_of_adv"] = out["days_to_exit"] = 0.0
            days_level = GREEN
    else:
        out["pct_of_adv"] = size / adv
        if part is None:
            reasons.append("days-to-exit test not run: no participation set")
        else:
            out["days_to_exit"] = size / (part * adv)
            days_level = _level_of(out["days_to_exit"], _num(params.get("amber_days_to_exit")),
                                   _num(params.get("red_days_to_exit")))
    out["tests"] = {"open_interest": oi_level, "days_to_exit": days_level}
    ran = [lv for lv in (oi_level, days_level) if lv is not None]
    out["level"] = max(ran, key=_RANK.get) if ran else NO_DATA
    out["reason"] = _join(str(liq.get("reason") or ""), *reasons)
    return out


# ------------------------------------------------------------------ the book
def _trade_facts(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{trade_id: {quantity, product, instrument_id, value_date}} from the fills."""
    sql = ("SELECT t.trade_id, t.quantity, t.product, t.instrument_id, MAX(l.settle_date) "
           "FROM trades_official t LEFT JOIN trade_legs l ON l.trade_id = t.trade_id GROUP BY t.trade_id")
    return {str(r[0]): {"quantity": r[1], "product": str(r[2] or ""), "instrument_id": str(r[3] or ""),
                        "value_date": str(r[4] or "")} for r in conn.execute(sql)}


def _our_lot(roots: dict, root_id: Optional[str]) -> Optional[Tuple[float, str]]:
    r = roots.get(str(root_id or ""))
    return (float(r.contract_size), str(r.size_unit)) if r is not None else None


def _exposure(tid: str, f: dict, row: Optional[dict], flat: Optional[dict], roots: dict
              ) -> Tuple[Optional[str], Optional[str], Optional[float], str]:
    """(the contract whose liquidity it takes, its root, lots in our contract's lots or None, why
    not) for one open commodity trade (or one curve row, passed as ``f``)."""
    product = f.get("product")
    q = _num(f.get("quantity"))
    if q is None:
        return None, None, None, f"{tid}: the quantity is not a number"
    if product == OPTION:
        if row is None:
            return None, (flat or {}).get("root_id"), None, (
                f"{tid} ({f.get('instrument_id')}): the option nets flat across the book, so it has no delta row")
        under = row.get("underlying_id") or None
        delta = _num(row.get("delta_factor"))
        if not under:
            return None, row.get("root_id"), None, f"{tid} ({row.get('contract_id')}): no underlying future"
        if delta is None:
            return under, row.get("root_id"), None, (
                f"option {row.get('contract_id')} has no delta ({row.get('reason') or 'no DELTA mark'}): "
                f"not counted in {under}")
        return under, row.get("root_id"), q * delta, ""
    src = row or flat or {}
    if product == LME:
        cid = src.get("contract_id") or f"{f.get('instrument_id')} {f.get('value_date')}"
        root = src.get("root_id") or f.get("instrument_id")
        lot = _our_lot(roots, root)
        if lot is None or lot[0] <= 0:
            return cid, root, None, f"{tid} ({cid}): no tonnes per lot for {root}"
        return cid, root, q / lot[0], ""
    return src.get("contract_id") or f.get("instrument_id"), src.get("root_id"), q, ""


def _factor(roots: dict, root_id: Optional[str], liq: Optional[dict]) -> Tuple[float, str]:
    """(our lots -> the lots Bloomberg's figures count, '' or a note)."""
    if not liq:
        return 1.0, ""
    theirs, unit = _num(liq.get("lot_size")), str(liq.get("lot_unit") or "")
    ours = _our_lot(roots, root_id)
    if ours is None or theirs is None or theirs <= 0:
        return 1.0, ""
    same_unit = _unit(ours[1]) == _unit(unit)
    if same_unit and abs(ours[0] - theirs) <= _EPS:
        return 1.0, ""
    if same_unit:
        return ours[0] / theirs, f"our lot of {ours[0]:g} {ours[1]} converted to lots of {theirs:g} {unit}"
    return 1.0, (f"lots compared as they are: a lot in Bloomberg's figures is {theirs:g} {unit}, ours {ours[0]:g} {ours[1]} "
                 "(units differ, no conversion)")


def _badness(rec: dict) -> tuple:
    d, o = rec.get("days_to_exit"), rec.get("pct_of_oi")
    return (_RANK.get(rec.get("level"), 0), d if d is not None else -1.0, o if o is not None else -1.0)


def liquidity(conn: sqlite3.Connection, as_of: str, spreads: Optional[dict] = None, curve: Optional[dict] = None,
              config: Any = None, db_path: Any = None) -> dict:
    """The liquidity check on ``as_of`` (module docstring). ``spreads`` / ``curve``:
    ``engine.spreads.book_spreads(conn, as_of)`` and ``engine.curve.curve_positions(conn, as_of)``,
    built when None. ``config``: a ``LimitsConfig`` (``load_limits()`` when None) or the liquidity
    mapping itself. ``db_path``: the book database whose ``price_history`` holds Bloomberg's
    volume and open interest (a path or an open connection); None = ``conn``'s own database.
    Reads the trades and legs only; never raises on missing data: every gap is a reason.

    Returns ``{as_of, available, reason, label, basis, placeholder, placeholder_note, params,
    contracts, positions, skipped, summary}``:

    - ``params``: the thresholds used (placeholder, participation, amber_pct_oi, red_pct_oi,
      amber_days_to_exit, red_days_to_exit, window).
    - ``contracts``: one per contract the open positions hold, worst first: ``{contract_id`` (a
      future's canonical id, an LME ticket's 'LME:CA <prompt>'), ``root_id, name, exchange,
      products, position_ids, net_lots`` (ours, the book's net of the known pieces; None when no
      piece is known), ``gross_lots`` (the positions' own |lots| added), ``incomplete, excluded``
      (the pieces with no lots, an option with no delta, in words), ``lot_factor, research_lots,
      open_interest, oi_date, adv, adv_days, window, volume_last, volume_date,
      research_contract_id, lot_size, lot_unit, pct_of_oi, pct_of_adv, days_to_exit, level,
      tests {open_interest, days_to_exit}, reason, note}``.
    - ``positions``: one per Book position holding a checked contract, worst first:
      ``{position_id, name, kind, trade_ids, legs, weakest, level, days_to_exit, pct_of_oi,
      pct_of_adv, reason}``; ``legs`` one per contract ``{contract_id, root_id, lots`` (ours, the
      position's net in it), ``incomplete, excluded, trade_ids, research_lots, open_interest, oi_date, adv, adv_days,
      pct_of_oi, pct_of_adv, days_to_exit, level, tests, lot_factor, reason, note}``, worst
      first; ``weakest`` the weakest leg's contract id (None when no leg has data).
    - ``skipped``: ``[{trade_id, instrument_id, product, position_id, reason}]``: FX trades
      ("FX: not an exchange contract") and any open trade the check cannot place.
    - ``summary``: ``{counts {RED, AMBER, GREEN, NO_DATA}`` (positions), ``contract_counts``
      (the same over contracts), ``worst {position_id, name, level, contract_id, days_to_exit,
      pct_of_oi}`` or None, ``no_data [{contract_id, reason}]``, ``n_skipped``, ``sentence}``.
    """
    params, why_none = _params(config)
    placeholder = bool(params.get("placeholder", True))
    out: Dict[str, Any] = {"as_of": as_of, "available": False, "reason": "", "label": LABEL, "basis": BASIS,
                           "placeholder": placeholder, "placeholder_note": PLACEHOLDER_NOTE if placeholder else "",
                           "params": params, "contracts": [], "positions": [], "skipped": [], "summary": {}}
    if spreads is None:                                   # once, and handed to the curve (speed, 2026-09-29)
        from engine.spreads import book_spreads
        spreads = book_spreads(conn, as_of)
    if curve is None:
        from engine.curve import curve_positions
        curve = curve_positions(conn, as_of, spreads=spreads)
    from engine.spreads.period_explain import positions_of

    rows = list(curve.get("rows") or [])
    row_of = {str(t): r for r in rows for t in r.get("trade_ids") or []}
    flat_of = {str(t): r for r in curve.get("flat_contracts") or [] for t in r.get("trade_ids") or []}
    facts = _trade_facts(conn)
    open_ids = set(row_of) | set(flat_of) | {t for t, f in facts.items()
                                              if f["product"] in FX_PRODUCTS and f["value_date"] > as_of}
    trade_rows = {t: {"instrument_id": facts.get(t, {}).get("instrument_id"),
                      "product": facts.get(t, {}).get("product")} for t in open_ids}
    roots = load_roots()

    # -- the positions' legs, in our lots
    pos_legs: List[Tuple[dict, Dict[str, dict]]] = []
    want: Dict[str, Optional[str]] = {}
    for p in positions_of(spreads, trade_rows):
        legs: Dict[str, dict] = {}
        for tid in (str(t) for t in p.get("trade_ids") or [] if str(t) in open_ids):
            f = facts.get(tid) or {}
            skip = {"trade_id": tid, "instrument_id": f.get("instrument_id"), "product": f.get("product"),
                    "position_id": p["position_id"]}
            if f.get("product") in FX_PRODUCTS or (tid not in row_of and tid not in flat_of):
                out["skipped"].append({**skip, "reason": FX_SKIP if f.get("product") in FX_PRODUCTS
                                       else f"{f.get('product')}: not in the liquidity check"})
                continue
            cid, root, lots, why = _exposure(tid, f, row_of.get(tid), flat_of.get(tid), roots)
            if cid is None:
                out["skipped"].append({**skip, "reason": why})
                continue
            leg = legs.setdefault(cid, {"root_id": root, "lots": 0.0, "known": 0, "trade_ids": [], "missing": []})
            leg["trade_ids"].append(tid)
            if lots is None:
                leg["missing"].append(why)
            else:
                leg["lots"] += lots
                leg["known"] += 1
            if not want.get(cid):
                want[cid] = root
        if legs:
            pos_legs.append((p, legs))

    # -- Bloomberg's volume and open interest, once
    window = params.get("window")
    liq: Dict[str, dict] = {}
    if want:
        from engine.risk.commodity_history import LIQUIDITY_WINDOW, contract_liquidity
        window = int(window or LIQUIDITY_WINDOW)
        liq = contract_liquidity(want, as_of, window=window, db_path=conn if db_path is None else db_path)
    out["params"] = {**params, "window": window}

    def figures(cid: str, root: Optional[str], lots: Optional[float]) -> dict:
        rec = liq.get(cid)
        resolved = bool(rec and rec.get("source_contract_id"))
        factor, fnote = _factor(roots, root, rec if resolved else None)
        m = _measure(lots, rec if resolved else None, params, factor)
        if rec is not None and not resolved:
            m["reason"] = f"{NO_MONTH_DATA}: {rec.get('reason') or 'no Bloomberg history on file'}"
        elif resolved and m["level"] == NO_DATA and lots is not None:
            m["reason"] = f"{NO_MONTH_DATA}: {m['reason']}"
        m["lot_factor"] = factor
        m["note"] = _join(str((rec or {}).get("note") or ""), fnote)
        if why_none:
            m["reason"] = _join(why_none, m["reason"])
        return m

    # -- per position
    gross: Dict[str, float] = {}
    members: Dict[str, List[str]] = {}
    for p, legs in pos_legs:
        leg_out: List[dict] = []
        for cid, leg in legs.items():
            lots = leg["lots"] if leg["known"] else None
            if lots is not None and abs(lots) <= _EPS and not leg["missing"]:
                continue                                  # bought and sold back inside the position
            m = figures(cid, leg["root_id"], lots)
            if leg["missing"]:
                m["reason"] = _join(*(f"excluded: {w}" if lots is not None else w for w in leg["missing"]),
                                    m["reason"])
            if lots is not None:
                gross[cid] = gross.get(cid, 0.0) + abs(lots)
            members.setdefault(cid, []).append(p["position_id"])
            leg_out.append({"contract_id": cid, "root_id": leg["root_id"], "lots": lots,
                            "incomplete": bool(leg["missing"]), "excluded": list(leg["missing"]),
                            "trade_ids": sorted(leg["trade_ids"]), **m})
        if not leg_out:
            continue
        leg_out.sort(key=_badness, reverse=True)
        weak = leg_out[0] if leg_out and leg_out[0]["level"] != NO_DATA else None
        no_data = [f"{lg['contract_id']}: {lg['reason'] or 'not tested'}" for lg in leg_out if lg["level"] == NO_DATA]
        out["positions"].append({
            "position_id": p["position_id"], "name": p.get("name") or p["position_id"], "kind": p.get("kind", ""),
            "trade_ids": sorted({t for lg in leg_out for t in lg["trade_ids"]}), "legs": leg_out,
            "weakest": weak["contract_id"] if weak else None, "level": weak["level"] if weak else NO_DATA,
            "days_to_exit": weak["days_to_exit"] if weak else None,
            "pct_of_oi": weak["pct_of_oi"] if weak else None, "pct_of_adv": weak["pct_of_adv"] if weak else None,
            "reason": "not tested: " + "; ".join(no_data) if no_data else "",
        })
    out["positions"].sort(key=_badness, reverse=True)

    # -- per contract: the book's net over curve-positions' rows
    net: Dict[str, float] = {}
    net_missing: Dict[str, List[str]] = {}
    products: Dict[str, List[str]] = {}
    info: Dict[str, dict] = {}
    for r in rows:
        if r.get("product") == LME:
            cid, lots, why = str(r.get("contract_id")), _num(r.get("lots")), "the LME row has no lots"
        else:
            f = {"product": r.get("product"), "quantity": r.get("lots"), "instrument_id": r.get("instrument_id")}
            cid, _root, lots, why = _exposure(str(r.get("contract_id")), f, r, None, roots)
            if cid is None:
                continue
        info.setdefault(cid, r)
        if str(r.get("product")) not in products.setdefault(cid, []):
            products[cid].append(str(r.get("product")))
        if lots is None:
            net_missing.setdefault(cid, []).append(why)
        else:
            net[cid] = net.get(cid, 0.0) + lots
    for cid, root in want.items():
        if cid not in members:
            continue                                      # every position holding it is flat in it
        # the known net (0: held by positions, flat across the book); a piece with no lots is named
        lots = net.get(cid, 0.0) if (cid in net or not net_missing.get(cid)) else None
        m = figures(cid, root, lots)
        if net_missing.get(cid):
            m["reason"] = _join(*(f"excluded: {w}" if lots is not None else w for w in net_missing[cid]),
                                m["reason"])
        rec = liq.get(cid) or {}
        r = info.get(cid) or {}
        out["contracts"].append({
            "contract_id": cid, "root_id": root, "name": r.get("name") or root,
            "exchange": r.get("exchange") or rec.get("exchange", ""), "products": products.get(cid, []),
            "position_ids": members.get(cid, []), "net_lots": lots, "gross_lots": gross.get(cid),
            "incomplete": bool(net_missing.get(cid)), "excluded": list(net_missing.get(cid) or []),
            "window": rec.get("window", window), "volume_last": rec.get("volume_last"),
            "volume_date": rec.get("volume_date"), "research_contract_id": rec.get("source_contract_id"),
            "lot_size": rec.get("lot_size"), "lot_unit": rec.get("lot_unit", ""), **m})
    out["contracts"].sort(key=_badness, reverse=True)

    out["available"] = any(c["level"] != NO_DATA for c in out["contracts"])
    if not out["available"]:
        out["reason"] = why_none or ("no open commodity position" if not want else
                                     "no Bloomberg volume or open interest for any contract the book holds")
    out["summary"] = _summary(out)
    return out


def _summary(out: dict) -> dict:
    counts = {lv: 0 for lv in LIQUIDITY_LEVELS}
    for p in out["positions"]:
        counts[p["level"]] += 1
    ccounts = {lv: 0 for lv in LIQUIDITY_LEVELS}
    for c in out["contracts"]:
        ccounts[c["level"]] += 1
    worst = next((p for p in out["positions"] if p["level"] != NO_DATA), None)
    worst_out = None
    if worst is not None:
        worst_out = {k: worst[k] for k in ("position_id", "name", "level", "days_to_exit", "pct_of_oi")}
        worst_out["contract_id"] = worst["weakest"]
    no_data = [{"contract_id": c["contract_id"], "reason": c["reason"]} for c in out["contracts"]
               if c["level"] == NO_DATA]
    n = len(out["positions"])
    if not n:
        sentence = f"Liquidity: {out['reason'] or 'no open commodity position'}."
    else:
        sentence = (f"Liquidity of {n} position(s): {counts[RED]} red, {counts[AMBER]} amber, "
                    f"{counts[GREEN]} green, {counts[NO_DATA]} not tested")
        if worst_out:
            d, o = worst_out["days_to_exit"], worst_out["pct_of_oi"]
            sentence += (f"; the weakest is {worst_out['name']} ({worst_out['contract_id']}: "
                         f"{'n/a' if d is None else f'{d:.1f}'} days to exit, "
                         f"{'n/a' if o is None else f'{o:.1%}'} of open interest)")
        sentence += "."
        if no_data:
            sentence += f" {len(no_data)} contract(s) not tested (no data or no lots figure)."
        if out["placeholder"]:
            sentence += " Thresholds are placeholders."
    return {"counts": counts, "contract_counts": ccounts, "worst": worst_out, "no_data": no_data,
            "n_skipped": len(out["skipped"]), "sentence": sentence}
