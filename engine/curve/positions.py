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
from engine.curve.rows import FUTURE, LME, OPTION, future_row, lme_row, month_key, number, option_row, root_key
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


def _delta_totals(rows: List[dict]) -> dict:
    net_lots = None if any(r["delta_lots"] is None for r in rows) else float(sum(r["delta_lots"] for r in rows))
    net_usd, gross_usd, missing, why = _usd_totals(rows, "delta_usd")
    return {"net_delta_lots": net_lots, "net_delta_usd": net_usd, "gross_delta_usd": gross_usd,
            "delta_missing": missing, "delta_reason": why}


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
            "delta_months": _months(mine, "delta_lots"),
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


def _currency_exposure(conn, groups: List[dict], as_of: str) -> Tuple[Dict[str, dict], List[str]]:
    """{ccy: {pnl_local, pnl_usd, contracts, missing, reason}} over every open non-USD commodity
    future and option (flat ones included: their P&L is still held in that currency), from value_book."""
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
                pl = number(getattr(r, "pnl_local", None)) if r is not None else None
                pu = number(getattr(r, "pnl_usd", None)) if r is not None else None
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
            "by_commodity": {}, "by_sector": {}, "currency_exposure": {}, "months": [],
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


def curve_positions(conn: sqlite3.Connection, as_of: str) -> dict:
    """The book's commodity futures, options on them and LME forwards by contract month on `as_of`.

    Returns ``{as_of, available, note, rows, flat_contracts, by_commodity, by_sector,
    currency_exposure, months, products_present, reasons}``:

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
    - ``by_sector``: ``{sector: {net_usd, gross_usd, missing, reason, commodities,
      net_delta_lots, net_delta_usd, gross_delta_usd, delta_missing, delta_reason}}``, same rules.
    - ``currency_exposure``: ``{ccy: {pnl_local, pnl_usd, contracts, missing, reason}}`` for the
      open non-USD commodity futures and options, summed from ``value_book``; None where a
      trade has none.
    - ``months``: sorted 'YYYY-MM' keys that appear in ``rows``.
    - ``products_present``: the products among ``rows``, in the order FUTURE, LME_FWD, CMDTY_OPTION.
    - ``reasons``: what could not be computed, in plain words.
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
    return {"as_of": as_of, "available": True, "note": note, "rows": rows, "flat_contracts": flat,
            "by_commodity": _by_commodity(rows), "by_sector": _by_sector(rows),
            "currency_exposure": exposure, "months": months,
            "products_present": [p for p in _PRODUCT_ORDER if any(r["product"] == p for r in rows)],
            "reasons": reasons}
