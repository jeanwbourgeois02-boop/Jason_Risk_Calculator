"""The book's positions in one table (user, 2026-09-22: "I want to see my total positions
delta ... in the total tab in blotter"; the Blotter's key table and the Risk tab's inputs).

`book_positions(conn, as_of)` gathers the delta figures the app already computes, each
from the module that owns it, and adds nothing of its own except the sums:

  * FX: the Net / Gross USD delta of the Ladder and the header (`ui.tabs.cash_ladder.
    net_gross_usd`'s inputs, rebuilt here from the same engine calls: exposure records at
    every currency's official SPOT, `rates_from_marks`), Net shown as the USD position
    (+ = long USD, the header's sign), plus each metal on its own line (ounces and USD,
    `portfolio_totals`'s `commodities`).
  * FX options: delta in USD by pair (the Ladder's own option delta records: base-ccy
    delta at spot).

  * Commodities (commodity conversion Phase 3): the commodity futures by sector, then by
    commodity, from curve-positions' `engine.curve.curve_positions` (`by_sector`,
    `by_commodity`, `currency_exposure`), never recomputed. Commodity futures are not in
    the FX net or gross (CLAUDE.md "Net USD": they are positions on the Curve tab).

The equity index line (ES futures + SPX options) and the rates DV01 left on 2026-09-24
with the macro trader's products (user approval, commodity conversion Phase 2), as did the
1M NDF rates: every currency is at its official spot, like the Ladder.

Every figure is a plain number, or NaN (FX blocks) / None (commodities block, curve-positions'
own convention) with a reason; nothing here is a substitute for a missing mark (hard rule 2),
and `marks_official` is the only marks source.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Optional


def _missing_spot_reason(ccys: Iterable[str], as_of: str) -> str:
    """'no official SPOT for <as_of>: JPY, KRW', the header's and the Ladder's wording
    (`ui.tabs.exposure.missing_spot_reason`); '' for none."""
    ccys = sorted(set(ccys))
    return f"no official SPOT for {as_of}: " + ", ".join(ccys) if ccys else ""


def fx_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """{'available', 'net_usd' (+ = long USD), 'gross_usd', 'reason', 'by_ccy': [...], 'metals':
    [{ccy, units, usd_delta, reason}]} from the Ladder's own exposure path. `by_ccy` (user,
    2026-09-22: "I want to see the delta by currency ... this is the key table of the
    blotter") is the Ladder's per-currency risk table: one row per currency with delta,
    {ccy, quoted (the official SPOT as quoted), label (its plain pair, 'USDJPY'; '' when the
    currency has no rate), local_delta, usd_delta (NaN with `reason` when the currency has
    no rate), metal}, largest |USD delta| first, USD itself last."""
    from data.bloomberg.live import rates_from_marks
    from engine.ladder.exposure import COMMODITY_CCYS, build_exposure, portfolio_totals
    from engine.ladder.exposure_adapter import exposure_records_from_db
    records, _unresolved = exposure_records_from_db(conn, as_of)
    rates = rates_from_marks(conn)
    result = build_exposure(records, rates)
    totals = portfolio_totals(result)
    messages = {r["currency"]: r["message"] for r in result.status.to_dict("records")} if not result.status.empty else {}
    by_ccy = []
    for s in result.summary.to_dict("records"):
        ccy = s["currency"]
        entry = rates.get(ccy) or {}
        usd = s["usd_delta"]
        ok = s["status"] in ("OK", "STALE") and usd == usd
        by_ccy.append({"ccy": ccy, "quoted": 1.0 if ccy == "USD" else entry.get("rate"),
                       "label": "USD" if ccy == "USD" else (entry.get("pair") or ""),
                       "local_delta": float(s["local_delta"]), "usd_delta": float(usd) if ok else float("nan"),
                       "reason": "" if ok else (messages.get(ccy) or str(s["status"])), "metal": ccy in COMMODITY_CCYS})
    by_ccy.sort(key=lambda r: (r["ccy"] == "USD", -(abs(r["usd_delta"]) if r["usd_delta"] == r["usd_delta"] else -1), r["ccy"]))
    metals = [{"ccy": c.get("currency"), "units": c.get("local_delta"), "usd_delta": c.get("usd_delta"),
               "reason": c.get("reason") or ("" if c.get("status") == "OK" else str(c.get("status") or ""))}
              for c in totals.get("commodities", [])]
    if totals.get("missing"):
        return {"available": False, "net_usd": float("nan"), "gross_usd": float("nan"),
                "reason": _missing_spot_reason(totals["missing"], as_of), "by_ccy": by_ccy, "metals": metals}
    # portfolio_totals' net is the net non-USD delta (+ = long foreign); the USD position is its opposite.
    return {"available": True, "net_usd": -float(totals["net_usd"]), "gross_usd": float(totals["gross_usd"]),
            "reason": "", "by_ccy": by_ccy, "metals": metals}


def fx_option_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """Delta (USD) of the open FX options by pair: the Ladder's own option delta records,
    base-currency delta at that currency's official SPOT."""
    from data.bloomberg.live import rates_from_marks
    from engine.ladder.exposure import usd_per_local
    from engine.ladder.exposure_adapter import option_records_from_db
    records, unresolved = option_records_from_db(conn, as_of, {})
    rates = rates_from_marks(conn)
    by_pair: Dict[str, float] = {}
    missing = [f"{u.trade_id}: {u.reason}" for u in unresolved]
    for r in records:
        pair, ccy = r["currency_pair"], r["currency"]
        if ccy != pair[:3]:
            continue    # the base-ccy record carries the delta; its quote-ccy mirror is the same position
        rate = 1.0 if ccy == "USD" else (usd_per_local(rates[ccy]) if ccy in rates else None)
        if rate is None:
            missing.append(f"{r['trade_id']}: no official SPOT to convert {ccy} delta to USD")
            continue
        by_pair[pair] = by_pair.get(pair, 0.0) + float(r["local_amount"]) * rate
    return {"by_pair": by_pair, "usd_delta": sum(by_pair.values()) if by_pair else float("nan"),
            "options": len({r["trade_id"] for r in records}), "missing": missing,
            "reason": "" if records or unresolved else "no open FX options"}


def _excludes(absent: List[dict], total: int) -> str:
    """'excludes 1 of 3 commodities with no USD figure: COMEX copper (COMEX:HG): <why>; ...'."""
    if not absent:
        return ""
    names = "; ".join(f"{c['name']} ({c['root_id']}): {c['reason'] or 'no USD figure'}" for c in absent)
    return f"excludes {len(absent)} of {total} commodities with no USD figure: {names}"


def _known_sum(values: List[Optional[float]]) -> Optional[float]:
    """Sum of the known figures; None when none is known (never a zero standing for n/a)."""
    known = [float(v) for v in values if v is not None]
    return float(sum(known)) if known else None


def _abs_order(value: Optional[float]):
    """Sort key: largest first, n/a last."""
    return (value is None, -abs(value) if value is not None else 0.0)


def commodity_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """The commodity futures by sector, then by commodity: curve-positions' own figures,
    nothing recomputed but the sums across them.

    {'available', 'note', 'reason', 'sectors': [{sector, net_usd, gross_usd, missing, reason,
    'commodities': [{root_id, name, exchange, currency, net_lots, gross_lots, net_units, unit,
    net_usd, gross_usd, missing, reason}]}], 'net_usd', 'gross_usd', 'missing',
    'currency_exposure': {ccy: {pnl_local, pnl_usd, reason}}}.

    Sectors by gross USD, largest first; commodities inside a sector by |net USD|, largest
    first; n/a last. A commodity with no USD figure (curve-positions' None: a contract with no
    price or conversion spot) stays in its list with curve-positions' reason (its `missing` =
    curve-positions' contract ids) and is left out of the sums; the sector's and the book's
    `missing` name it by root id and their `reason` says "excludes N of M commodities ...".
    A sector with every commodity known takes curve-positions' own sector figures; the book's
    figures are the sectors' summed. A figure with nothing known behind it is None, never 0.
    """
    from engine.curve import curve_positions
    cp = curve_positions(conn, as_of)
    by_commodity = cp.get("by_commodity") or {}
    note = cp.get("note") or ""
    ccy_exposure = {ccy: {"pnl_local": e.get("pnl_local"), "pnl_usd": e.get("pnl_usd"), "reason": e.get("reason", "")}
                    for ccy, e in (cp.get("currency_exposure") or {}).items()}
    if not cp.get("available"):
        return {"available": False, "note": note, "sectors": [], "net_usd": None, "gross_usd": None, "missing": [],
                "reason": "; ".join(cp.get("reasons") or []) or note or "commodity positions unavailable",
                "currency_exposure": ccy_exposure}
    sectors, absent_all, count = [], [], 0
    for sector, s in (cp.get("by_sector") or {}).items():
        comms = []
        for root_id in s.get("commodities") or []:
            c = by_commodity.get(root_id) or {}
            comms.append({"root_id": root_id, "name": c.get("name", root_id), "exchange": c.get("exchange", ""),
                          "currency": c.get("currency", ""), "net_lots": c.get("net_lots"),
                          "gross_lots": c.get("gross_lots"), "net_units": c.get("net_units"), "unit": c.get("unit", ""),
                          "net_usd": c.get("net_usd"), "gross_usd": c.get("gross_usd"),
                          "missing": list(c.get("missing") or []), "reason": c.get("reason", "")})
        comms.sort(key=lambda c: (*_abs_order(c["net_usd"]), c["root_id"]))
        absent = [c for c in comms if c["net_usd"] is None or c["gross_usd"] is None]
        present = [c for c in comms if c["net_usd"] is not None and c["gross_usd"] is not None]
        if absent:
            net = _known_sum([c["net_usd"] for c in present])
            gross = _known_sum([c["gross_usd"] for c in present])
        else:
            net, gross = s.get("net_usd"), s.get("gross_usd")
        sectors.append({"sector": sector, "net_usd": net, "gross_usd": gross,
                        "missing": [c["root_id"] for c in absent], "reason": _excludes(absent, len(comms)),
                        "commodities": comms})
        absent_all += absent
        count += len(comms)
    sectors.sort(key=lambda s: (*_abs_order(s["gross_usd"]), s["sector"]))
    if not sectors:
        return {"available": True, "note": note, "sectors": [], "net_usd": None, "gross_usd": None, "missing": [],
                "reason": note or f"no open commodity futures on {as_of}", "currency_exposure": ccy_exposure}
    return {"available": True, "note": note, "sectors": sectors,
            "net_usd": _known_sum([s["net_usd"] for s in sectors]),
            "gross_usd": _known_sum([s["gross_usd"] for s in sectors]),
            "missing": [c["root_id"] for c in absent_all], "reason": _excludes(absent_all, count),
            "currency_exposure": ccy_exposure}


_BLOCKS = (("fx", fx_positions), ("fx_options", fx_option_positions), ("commodities", commodity_positions))

_EMPTY = {
    "fx": {"available": False, "net_usd": float("nan"), "gross_usd": float("nan"), "by_ccy": [], "metals": []},
    "fx_options": {"by_pair": {}, "usd_delta": float("nan"), "options": 0, "missing": []},
    "commodities": {"available": False, "note": "", "sectors": [], "net_usd": None, "gross_usd": None,
                    "missing": [], "currency_exposure": {}},
}


def book_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """{'fx': ..., 'fx_options': ..., 'commodities': ...}, the blocks above. One that cannot be
    computed at all (a stored value that is not a number in a trade the Ladder's own path
    reads, say) comes back in its empty shape with the reason, so the others still show and
    nothing is blank without a reason."""
    out = {}
    for key, fn in _BLOCKS:
        try:
            out[key] = fn(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- another lane's data error, named on the line
            out[key] = {**_EMPTY[key], "reason": f"could not be computed ({type(exc).__name__}: {exc})"}
    return out
