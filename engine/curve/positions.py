"""Positions by commodity x contract month: the commodity trader's ladder.

One row per open commodity futures contract with a non-zero net position: its net lots, the
same in physical units (lots x contract size, in the root's size unit: bbl, t, oz, bu, MMBtu,
lb ...), and its notional, ``lots x multiplier x price`` in the contract's quote currency and
that times ``S`` in USD. Rolled up per commodity (the contract root, 'NYMEX:CL') and per sector,
so a spread book shows offsetting months and a small net outright.

Rules (CLAUDE.md hard rules 2 and 3, "P&L conventions -> Futures"):

- A commodity future is a ``FUTURE`` trade whose instrument's ``base_ccy`` is a contract root of
  ``config/contracts.csv`` (the ingest-parser's layout); anything else (the equity index
  futures, ES and the like) is not this module's.
- Open = ``trade_date <= as_of`` and the leg's expiry (``trade_legs.settle_date`` of leg 1)
  ``> as_of``: the test ``engine/ladder/futures_delta.py::futures_usd_delta`` uses.
- Contract size, unit, multiplier, name, sector and the contract month come from contract-master
  (``data.contracts``) only; nothing here is per commodity. An ``instruments.multiplier`` that
  disagrees with contract-master's leaves the notional blank with both figures named.
- The price is the official ``FUTURE_PX`` dated ``as_of`` exactly, at the contract's expiry
  (``engine.pnl.valuation._mark_at``, which reads ``marks_official`` only). ``S`` is the exact
  official SPOT of ``as_of`` (``engine.ladder.futures_delta.usd_per_unit``). Neither is ever
  estimated from near marks: a contract without one keeps its lots and units, and its notional
  is None with the reason, never zero and never filled.
- The currency exposure of non-USD futures is the P&L they have built up in that currency
  (``value_book``'s ``pnl_local``), not their notional: a margined future holds no notional cash.
"""

from __future__ import annotations

import math
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from data.contracts import UnknownContract, contract_for, load_roots
from engine.ladder.futures_delta import usd_per_unit
from engine.pnl.valuation import _mark_at, value_book

_OPEN_FUTURES_SQL = """
SELECT t.trade_id, t.instrument_id, t.quantity, i.base_ccy, i.quote_ccy, i.multiplier, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
WHERE t.product = 'FUTURE' AND t.trade_date <= :as_of AND l.settle_date > :as_of
ORDER BY t.instrument_id, l.settle_date, t.trade_id
"""

_FLAT = 1e-9   # a contract whose net is within this of zero is flat


def month_key(year: int, month: int) -> str:
    """The grid's column key: (2026, 12) -> '2026-12'."""
    return f"{int(year):04d}-{int(month):02d}"


def _root_key(base_ccy) -> str:
    return re.sub(r"\s+", "", str(base_ccy or "")).upper()


def _is_root_id(key: str) -> bool:
    return ":" in key


def _number(value) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _price(conn, instrument_id: str, expiry: str, as_of: str) -> Tuple[Optional[float], str, str]:
    """(price, source, reason): the official FUTURE_PX dated `as_of` exactly, or None and why."""
    hit = _mark_at(conn, instrument_id, expiry, "FUTURE_PX", as_of)
    if hit is None:
        return None, "", f"no FUTURE_PX for {instrument_id} (expiry {expiry}) on {as_of}"
    price = _number(hit[0])
    if price is None:
        return None, str(hit[1]), f"FUTURE_PX for {instrument_id} on {as_of} is not a number ({hit[0]!r})"
    return price, str(hit[1]), ""


def _group_open(rows) -> Dict[Tuple[str, str], dict]:
    """Open trades summed per (instrument, expiry), in SQL order."""
    groups: Dict[Tuple[str, str], dict] = {}
    for trade_id, instrument_id, qty, base_ccy, quote_ccy, multiplier, expiry in rows:
        g = groups.setdefault((instrument_id, expiry), {
            "instrument_id": instrument_id, "expiry": expiry, "root_key": _root_key(base_ccy),
            "currency": str(quote_ccy or ""), "multiplier_on_file": multiplier,
            "lots": 0.0, "trade_ids": [], "bad_quantity": []})
        g["trade_ids"].append(trade_id)
        q = _number(qty)
        if q is None:
            g["bad_quantity"].append(trade_id)
        else:
            g["lots"] += q
    return groups


def _row(conn, g: dict, root, as_of: str) -> dict:
    """One contract's row (see module docstring)."""
    reasons: List[str] = []
    lots = g["lots"]
    row = {
        "root_id": g["root_key"], "name": g["root_key"], "sector": "", "subsector": "", "exchange": "",
        "currency": g["currency"], "contract_id": g["instrument_id"], "month": None, "year": None,
        "month_code": "", "expiry": g["expiry"], "first_notice": None, "dates_source": "",
        "lots": lots, "gross_lots": abs(lots), "units": None, "unit": "", "multiplier": None,
        "price": None, "price_source": "", "usd_per_unit": None, "usd_source": "",
        "notional_local": None, "notional_usd": None, "trade_ids": list(g["trade_ids"]), "reason": "",
    }
    if g["bad_quantity"]:
        reasons.append(f"quantity is not a number on {', '.join(g['bad_quantity'])}")
    if root is None:
        reasons.append(f"contract root {g['root_key']} is not in config/contracts.csv")
        row["reason"] = "; ".join(reasons)
        return row
    row.update(name=root.name, sector=root.sector, subsector=root.subsector, exchange=root.exchange,
               unit=root.size_unit, multiplier=root.multiplier)
    if not g["bad_quantity"]:
        row["units"] = lots * root.contract_size
    try:
        cm = contract_for(root.root_id, g["instrument_id"], conn=conn)
    except UnknownContract as exc:
        reasons.append(str(exc))
    else:
        row.update(month=cm.month, year=cm.year, month_code=cm.month_code, dates_source=cm.dates_source,
                   first_notice=cm.first_notice_date.isoformat() if cm.first_notice_date else None)
    if row["currency"] != root.currency:
        reasons.append(f"currency on file {row['currency']!r} is not contract-master's {root.currency!r}")
    on_file = _number(g["multiplier_on_file"])
    multiplier_ok = on_file is not None and abs(on_file - root.multiplier) <= 1e-9 * max(1.0, root.multiplier)
    if not multiplier_ok:
        reasons.append(f"multiplier on file {g['multiplier_on_file']!r} is not contract-master's "
                       f"{root.multiplier:g}")
    price, price_source, why = _price(conn, g["instrument_id"], g["expiry"], as_of)
    row["price"], row["price_source"] = price, price_source
    if why:
        reasons.append(why)
    s, s_note = usd_per_unit(conn, root.currency, as_of)
    if s is None:
        reasons.append(s_note)
    else:
        row["usd_per_unit"], row["usd_source"] = s, s_note
    if price is not None and multiplier_ok and not g["bad_quantity"] and row["currency"] == root.currency:
        row["notional_local"] = lots * root.multiplier * price
        if s is not None:
            row["notional_usd"] = row["notional_local"] * s
    row["reason"] = "; ".join(reasons)
    return row


def _usd_totals(rows: List[dict]) -> Tuple[Optional[float], Optional[float], List[str], str]:
    """(net_usd, gross_usd, missing contract ids, reason): None when any row has no USD figure."""
    missing = [r["contract_id"] for r in rows if r["notional_usd"] is None]
    if missing:
        why = "; ".join(f"{r['contract_id']}: {r['reason'] or 'no USD notional'}"
                        for r in rows if r["notional_usd"] is None)
        return None, None, missing, why
    return (float(sum(r["notional_usd"] for r in rows)),
            float(sum(abs(r["notional_usd"]) for r in rows)), [], "")


def _by_commodity(rows: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for root_id in dict.fromkeys(r["root_id"] for r in rows):
        mine = [r for r in rows if r["root_id"] == root_id]
        first = mine[0]
        net_usd, gross_usd, missing, why = _usd_totals(mine)
        units_known = all(r["units"] is not None for r in mine)
        months: Dict[str, float] = {}
        for r in mine:
            if r["year"] is not None:
                key = month_key(r["year"], r["month"])
                months[key] = months.get(key, 0.0) + r["lots"]
        out[root_id] = {
            "name": first["name"], "sector": first["sector"], "subsector": first["subsector"],
            "exchange": first["exchange"], "currency": first["currency"],
            "net_lots": float(sum(r["lots"] for r in mine)),
            "gross_lots": float(sum(abs(r["lots"]) for r in mine)),
            "net_units": float(sum(r["units"] for r in mine)) if units_known else None,
            "unit": first["unit"], "net_usd": net_usd, "gross_usd": gross_usd,
            "months": dict(sorted(months.items())), "missing": missing, "reason": why,
        }
    return out


def _by_sector(rows: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for sector in sorted({r["sector"] for r in rows}):
        mine = [r for r in rows if r["sector"] == sector]
        net_usd, gross_usd, missing, why = _usd_totals(mine)
        out[sector] = {"net_usd": net_usd, "gross_usd": gross_usd, "missing": missing, "reason": why,
                       "commodities": list(dict.fromkeys(r["root_id"] for r in mine))}
    return out


def _currency_exposure(conn, groups: List[dict], as_of: str) -> Tuple[Dict[str, dict], List[str]]:
    """{ccy: {pnl_local, pnl_usd, contracts, missing, reason}} over every open non-USD commodity
    future (flat contracts included: their P&L is still held in that currency), from value_book."""
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
        for g in by_ccy[ccy]:
            for tid in g["trade_ids"]:
                r = rows.get(str(tid))
                pl = _number(getattr(r, "pnl_local", None)) if r is not None else None
                pu = _number(getattr(r, "pnl_usd", None)) if r is not None else None
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
                    "missing": sorted(set(missing_local) | set(missing_usd)), "reason": reason}
        if reason:
            reasons.append(f"{ccy} exposure: {reason}")
    return out, reasons


def _empty(as_of: str, available: bool, note: str, reasons: List[str]) -> dict:
    return {"as_of": as_of, "available": available, "note": note, "rows": [], "flat_contracts": [],
            "by_commodity": {}, "by_sector": {}, "currency_exposure": {}, "months": [], "reasons": reasons}


def curve_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """The book's commodity futures by contract month on `as_of`.

    Returns ``{as_of, available, note, rows, flat_contracts, by_commodity, by_sector,
    currency_exposure, months, reasons}``:

    - ``rows``: one dict per open contract with a non-zero net, ordered by sector, commodity,
      expiry: ``root_id, name, sector, subsector, exchange, currency, contract_id, month, year,
      month_code, expiry, first_notice (ISO or None), dates_source ('BLOOMBERG' | 'ESTIMATED'),
      lots, gross_lots (= |lots|), units, unit, multiplier, price, price_source, usd_per_unit,
      usd_source, notional_local, notional_usd, trade_ids, reason`` ('' when every figure is there).
    - ``flat_contracts``: ``[{root_id, contract_id, expiry, trade_ids}]``, open contracts netting to 0.
    - ``by_commodity``: ``{root_id: {name, sector, subsector, exchange, currency, net_lots,
      gross_lots, net_units, unit, net_usd, gross_usd, months {'YYYY-MM': lots}, missing, reason}}``;
      net_usd / gross_usd are None when any of its rows has no USD notional (``missing`` names
      them, ``reason`` says why).
    - ``by_sector``: ``{sector: {net_usd, gross_usd, missing, reason, commodities}}``, same rule.
    - ``currency_exposure``: ``{ccy: {pnl_local, pnl_usd, contracts, missing, reason}}`` for the
      open non-USD commodity futures, summed from ``value_book``; None where a trade has none.
    - ``months``: sorted 'YYYY-MM' keys that appear in ``rows``.
    - ``reasons``: what could not be computed, in plain words.
    """
    try:
        roots = load_roots()
    except (OSError, ValueError) as exc:
        return _empty(as_of, False, "", [f"the contract universe could not be read: {exc}"])
    groups = _group_open(conn.execute(_OPEN_FUTURES_SQL, {"as_of": as_of}).fetchall())
    mine = [g for g in groups.values() if g["root_key"] in roots or _is_root_id(g["root_key"])]
    if not mine:
        return _empty(as_of, True, f"no open commodity futures on {as_of}", [])

    reasons: List[str] = []
    seen_expiry: Dict[str, List[str]] = {}
    for g in mine:
        seen_expiry.setdefault(g["instrument_id"], []).append(g["expiry"])
    for instrument_id, expiries in seen_expiry.items():
        if len(expiries) > 1:
            reasons.append(f"{instrument_id} has trades on more than one expiry ({', '.join(expiries)}); "
                           "one row per expiry")

    rows, flat = [], []
    for g in mine:
        if not g["bad_quantity"] and abs(g["lots"]) <= _FLAT:
            flat.append({"root_id": g["root_key"], "contract_id": g["instrument_id"], "expiry": g["expiry"],
                         "trade_ids": list(g["trade_ids"])})
            continue
        rows.append(_row(conn, g, roots.get(g["root_key"]), as_of))
    rows.sort(key=lambda r: (r["sector"], r["root_id"], r["expiry"], r["contract_id"]))
    reasons += [f"{r['contract_id']}: {r['reason']}" for r in rows if r["reason"]]

    exposure, exposure_reasons = _currency_exposure(conn, mine, as_of)
    reasons += exposure_reasons
    months = sorted({month_key(r["year"], r["month"]) for r in rows if r["year"] is not None})
    note = "" if rows else f"every open commodity future is flat on {as_of}"
    return {"as_of": as_of, "available": True, "note": note, "rows": rows, "flat_contracts": flat,
            "by_commodity": _by_commodity(rows), "by_sector": _by_sector(rows),
            "currency_exposure": exposure, "months": months, "reasons": reasons}
