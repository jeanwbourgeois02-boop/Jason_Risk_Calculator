"""The book's positions in one table (user, 2026-09-22: "I want to see my total positions
delta, dv01 options in the total tab in blotter. I see now my fx is done correctly, but my
futures is not added up. SPX options - the detla should be added to the esz6 futures").

`book_positions(conn, as_of)` gathers the delta figures the app already computes, each
from the module that owns it, and adds nothing of its own except the sums:

  * FX: the Net / Gross USD delta of the Ladder and the header (`ui.tabs.cash_ladder.
    net_gross_usd`'s inputs, rebuilt here from the same engine calls: exposure records at
    the currencies' rates, an NDF currency at its 1M NDF price), Net shown as the USD
    position (+ = long USD, the header's sign), plus each metal on its own line (ounces
    and USD, `portfolio_totals`'s `commodities`).
  * Equity index, one line for the ES futures and the SPX options together: a listed
    option's delta is contracts x DELTA x multiplier index units (the DELTA mark is per
    unit of the underlying per contract unit, engine/options/portfolio.py), a future's is
    contracts x multiplier (ES: 50 index units per contract). The line shows the sum in
    index units ($ per index point), in ES-contract equivalents (index units / 50) and in
    USD (futures at their own price converted at the day's spot, `futures_usd_delta`'s own
    figure, options at the index level, all official marks of the day), with the futures
    and the options as sub-lines. A future with no price or no conversion spot, or an
    option with no DELTA or no index level, is named in `missing` and left out of the
    sum, never taken as zero. A commodity future (its `base_ccy` a contract root of
    `config/contracts.csv`, 'SHFE:CU') is not on this line at all: its positions are the
    curve-positions lane's (reviewer N-3, 2026-09-24).
  * Rates: DV01 (USD) of the open swaps, the official DV01_USD marks of the day summed,
    per currency; a swap with no mark is named.
  * FX options: delta in USD by pair (the Ladder's own option delta records: base-ccy
    delta at spot).

Every figure is a plain number or NaN with a reason; nothing here is a substitute for a
missing mark (hard rule 2), and `marks_official` is the only marks source.
"""
from __future__ import annotations

import math
import re
import sqlite3
from typing import Dict, List, Optional

ES_MULTIPLIER_NOTE = "ES-contract equivalents = index-unit delta / the ES multiplier (50)"

_OPEN_LISTED_OPTIONS_SQL = """
SELECT t.instrument_id, SUM(t.quantity) AS contracts, i.multiplier, i.bbg_ticker AS underlying, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
WHERE t.product = 'EQ_OPTION' AND t.trade_date <= :as_of AND l.settle_date > :as_of
GROUP BY t.instrument_id, i.multiplier, i.bbg_ticker, l.settle_date
"""

_OPEN_IRS_SQL = """
SELECT t.instrument_id, i.base_ccy, MAX(l.settle_date) AS maturity
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product = 'IRS' AND t.trade_date <= :as_of
GROUP BY t.instrument_id, i.base_ccy
HAVING maturity > :as_of
"""


def _official(conn: sqlite3.Connection, instrument_id: str, settle_date: str, mark_type: str, as_of: str) -> Optional[float]:
    row = conn.execute(
        "SELECT value FROM marks_official WHERE instrument_id = :i AND settle_date = :s AND mark_type = :m "
        "AND as_of_date = :d ORDER BY snapped_at DESC LIMIT 1",
        {"i": instrument_id, "s": settle_date, "m": mark_type, "d": as_of}).fetchone()
    if row is None:
        return None
    try:
        value = float(row[0])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def fx_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """{'available', 'net_usd' (+ = long USD), 'gross_usd', 'reason', 'by_ccy': [...], 'metals':
    [{ccy, units, usd_delta, reason}]} from the Ladder's own exposure path. `by_ccy` (user,
    2026-09-22: "I want to see the delta by currency ... this is the key table of the
    blotter") is the Ladder's per-currency risk table: one row per currency with delta,
    {ccy, quoted (the rate as quoted), label ('USDKRW' or the 1M NDF ticker), local_delta,
    usd_delta (NaN with `reason` when the currency has no rate), metal}, largest |USD
    delta| first, USD itself last."""
    from data.bloomberg.live import rates_from_marks
    from engine.ladder.exposure import COMMODITY_CCYS, build_exposure, portfolio_totals
    from engine.ladder.exposure_adapter import exposure_records_from_db
    from engine.ladder.ndf import apply_ndf_1m_rates, missing_rate_reason
    records, _unresolved = exposure_records_from_db(conn, as_of)
    rates = apply_ndf_1m_rates(conn, rates_from_marks(conn))
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
                       "label": "USD" if ccy == "USD" else (entry.get("label") or entry.get("pair") or ""),
                       "local_delta": float(s["local_delta"]), "usd_delta": float(usd) if ok else float("nan"),
                       "reason": "" if ok else (messages.get(ccy) or str(s["status"])), "metal": ccy in COMMODITY_CCYS})
    by_ccy.sort(key=lambda r: (r["ccy"] == "USD", -(abs(r["usd_delta"]) if r["usd_delta"] == r["usd_delta"] else -1), r["ccy"]))
    metals = [{"ccy": c.get("currency"), "units": c.get("local_delta"), "usd_delta": c.get("usd_delta"),
               "reason": c.get("reason") or ("" if c.get("status") == "OK" else str(c.get("status") or ""))}
              for c in totals.get("commodities", [])]
    if totals.get("missing"):
        return {"available": False, "net_usd": float("nan"), "gross_usd": float("nan"),
                "reason": missing_rate_reason(totals["missing"], as_of), "by_ccy": by_ccy, "metals": metals}
    # portfolio_totals' net is the net non-USD delta (+ = long foreign); the USD position is its opposite.
    return {"available": True, "net_usd": -float(totals["net_usd"]), "gross_usd": float(totals["gross_usd"]),
            "reason": "", "by_ccy": by_ccy, "metals": metals}


def _commodity_futures(conn: sqlite3.Connection) -> set:
    """Instrument ids of the futures whose `base_ccy` is a contract root of the commodity
    universe (`data.contracts.load_roots`, 'SHFE:CU'): these are not on the equity index line."""
    from data.contracts import load_roots
    roots = load_roots()
    return {instrument_id for instrument_id, base in conn.execute(
                "SELECT instrument_id, base_ccy FROM instruments WHERE asset_class = 'FUTURE'")
            if re.sub(r"\s+", "", str(base or "")).upper() in roots}


def equity_index_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """The ES futures and the SPX options as one delta (module docstring)."""
    from engine.ladder.futures_delta import futures_usd_delta
    lines: List[dict] = []
    missing: List[str] = []
    fut = futures_usd_delta(conn, as_of)
    commodity = _commodity_futures(conn)
    es_multiplier = None
    for instrument_id, d in sorted(fut["details"].items()):
        if instrument_id in commodity:
            continue    # a commodity contract is not an index future (its positions are curve-positions')
        units = d["contracts"] * d["multiplier"]
        es_multiplier = es_multiplier or d["multiplier"]
        if instrument_id not in fut["by_instrument"]:
            # no price, or a price with no spot to convert it (futures_delta names which)
            missing.append(f"{instrument_id}: {d['reason']}")
            lines.append({"label": instrument_id, "kind": "future", "contracts": d["contracts"], "index_units": units,
                          "usd_delta": float("nan"), "level": d["price"], "reason": d["reason"]})
            continue
        lines.append({"label": instrument_id, "kind": "future", "contracts": d["contracts"], "index_units": units,
                      "usd_delta": fut["by_instrument"][instrument_id], "level": d["price"], "reason": ""})
    for instrument_id, contracts, multiplier, underlying, expiry in conn.execute(_OPEN_LISTED_OPTIONS_SQL, {"as_of": as_of}):
        delta = _official(conn, instrument_id, expiry, "DELTA", as_of)
        level = _official(conn, underlying, as_of, "SPOT", as_of) if underlying else None
        line = {"label": instrument_id, "kind": "option", "contracts": float(contracts), "index_units": float("nan"),
                "usd_delta": float("nan"), "level": level, "reason": ""}
        if delta is None:
            line["reason"] = f"no DELTA mark on {as_of} (the Greeks are written by Pull Bloomberg now)"
        elif level is None:
            line["index_units"] = float(contracts) * delta * float(multiplier)
            line["reason"] = f"no SPOT for {underlying or 'its index'} on {as_of}: delta in index units only"
        else:
            line["index_units"] = float(contracts) * delta * float(multiplier)
            line["usd_delta"] = line["index_units"] * level
        if line["reason"]:
            missing.append(f"{instrument_id}: {line['reason']}")
        lines.append(line)
    priced = [l for l in lines if not l["reason"]]
    units = sum(l["index_units"] for l in priced) if priced else float("nan")
    usd = sum(l["usd_delta"] for l in priced) if priced else float("nan")
    return {"lines": lines, "index_units": units, "usd_delta": usd,
            "es_contracts": (units / es_multiplier) if priced and es_multiplier else float("nan"),
            "es_multiplier": es_multiplier, "missing": missing,
            "reason": "" if lines else "no open ES futures or SPX options"}


def rates_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """DV01 (USD) of the open swaps: the day's official DV01_USD marks summed, by currency."""
    by_ccy: Dict[str, float] = {}
    missing: List[str] = []
    swaps = 0
    for instrument_id, ccy, maturity in conn.execute(_OPEN_IRS_SQL, {"as_of": as_of}):
        swaps += 1
        dv01 = _official(conn, instrument_id, maturity, "DV01_USD", as_of)
        if dv01 is None:
            missing.append(f"{instrument_id}: no DV01_USD on {as_of}")
            continue
        by_ccy[ccy] = by_ccy.get(ccy, 0.0) + dv01
    return {"swaps": swaps, "by_ccy": by_ccy, "dv01_usd": sum(by_ccy.values()) if by_ccy else float("nan"),
            "missing": missing, "reason": "" if swaps else "no open swaps"}


def fx_option_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """Delta (USD) of the open FX options by pair: the Ladder's own option delta records,
    base-currency delta at that currency's rate."""
    from data.bloomberg.live import rates_from_marks
    from engine.ladder.exposure import usd_per_local
    from engine.ladder.exposure_adapter import option_records_from_db
    from engine.ladder.ndf import apply_ndf_1m_rates
    records, unresolved = option_records_from_db(conn, as_of, {})
    rates = apply_ndf_1m_rates(conn, rates_from_marks(conn))
    by_pair: Dict[str, float] = {}
    missing = [f"{u.trade_id}: {u.reason}" for u in unresolved]
    for r in records:
        pair, ccy = r["currency_pair"], r["currency"]
        if ccy != pair[:3]:
            continue    # the base-ccy record carries the delta; its quote-ccy mirror is the same position
        rate = 1.0 if ccy == "USD" else (usd_per_local(rates[ccy]) if ccy in rates else None)
        if rate is None:
            missing.append(f"{r['trade_id']}: no rate to convert {ccy} delta to USD")
            continue
        by_pair[pair] = by_pair.get(pair, 0.0) + float(r["local_amount"]) * rate
    return {"by_pair": by_pair, "usd_delta": sum(by_pair.values()) if by_pair else float("nan"),
            "options": len({r["trade_id"] for r in records}), "missing": missing,
            "reason": "" if records or unresolved else "no open FX options"}


_EMPTY = {
    "fx": {"available": False, "net_usd": float("nan"), "gross_usd": float("nan"), "by_ccy": [], "metals": []},
    "equity_index": {"lines": [], "index_units": float("nan"), "usd_delta": float("nan"), "es_contracts": float("nan"),
                     "es_multiplier": None, "missing": []},
    "rates": {"swaps": 0, "by_ccy": {}, "dv01_usd": float("nan"), "missing": []},
    "fx_options": {"by_pair": {}, "usd_delta": float("nan"), "options": 0, "missing": []},
}


def book_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """The four blocks above. One that cannot be computed at all (a stored value that is
    not a number in a trade the Ladder's own path reads, say) comes back in its empty shape
    with the reason, so the other three still show and nothing is blank without a reason."""
    out = {}
    for key, fn in (("fx", fx_positions), ("equity_index", equity_index_positions),
                    ("rates", rates_positions), ("fx_options", fx_option_positions)):
        try:
            out[key] = fn(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- another lane's data error, named on the line
            out[key] = {**_EMPTY[key], "reason": f"could not be computed ({type(exc).__name__}: {exc})"}
    return out
