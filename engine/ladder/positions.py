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

The equity index line (ES futures + SPX options) and the rates DV01 left on 2026-09-24
with the macro trader's products (user approval, commodity conversion Phase 2), as did the
1M NDF rates: every currency is at its official spot, like the Ladder. Phase 3 adds the
commodity lines, read from the curve-positions lane: a new block is one more entry in
`_BLOCKS` and `_EMPTY`, and the existing blocks keep their shape.

Every figure is a plain number or NaN with a reason; nothing here is a substitute for a
missing mark (hard rule 2), and `marks_official` is the only marks source.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, Iterable


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


_BLOCKS = (("fx", fx_positions), ("fx_options", fx_option_positions))

_EMPTY = {
    "fx": {"available": False, "net_usd": float("nan"), "gross_usd": float("nan"), "by_ccy": [], "metals": []},
    "fx_options": {"by_pair": {}, "usd_delta": float("nan"), "options": 0, "missing": []},
}


def book_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """{'fx': ..., 'fx_options': ...}, the blocks above. One that cannot be computed at all
    (a stored value that is not a number in a trade the Ladder's own path reads, say) comes
    back in its empty shape with the reason, so the other still shows and nothing is blank
    without a reason."""
    out = {}
    for key, fn in _BLOCKS:
        try:
            out[key] = fn(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- another lane's data error, named on the line
            out[key] = {**_EMPTY[key], "reason": f"could not be computed ({type(exc).__name__}: {exc})"}
    return out
