"""``limit_checks(conn, as_of)``: the book against the desk's and the exchanges' limits.

Every limit is the user's, in ``config/limits.yaml``; none is pre-filled, so a fresh file shows
every check NOT_SET (with the position still measured). A limit is compared with the absolute
value of the position: BREACH above it, WARN from ``warn_fraction`` of it (default 0.8), else OK;
a set limit whose position has no figure is N/A with the reason, never OK.

Positions come from ``engine.curve.curve_positions`` only. Limits in lots count futures-equivalent
lots: a future or an LME forward its lots, an option its delta lots (the exchanges aggregate
positions that way). USD limits use the USD delta.

- ``gross_lots`` (book): sum of |lots| over every open contract position (one per product and
  contract, as the Curve tab lists them).
- ``gross_usd`` (book): sum of |USD delta| over the same positions.
- ``net_usd_commodity`` (per root) / ``net_usd_sector`` (per sector): the curve's net USD delta.
- ``lots_per_contract_month`` (per root and month): |net lots| of the month, every product.
- ``exchange_spot_month`` (per root): |net lots| in the spot month, which is, among the root's
  contract months the book holds, the one whose last trade date (Bloomberg's when stored, else
  contract-master's estimate, said so) is the nearest after ``as_of``. A nearer month the book
  does not hold has no position and cannot breach, so taking the nearest held month can only warn
  early, never miss a breach.
- ``exchange_single_month`` (per root): the largest |net lots| in any one month (named).
- ``exchange_all_months`` (per root): |net lots| over all months.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from engine.limits.cells import build_cells, position_lots
from engine.limits.config import LimitsConfig, LimitsConfigError, load_limits

OK, WARN, BREACH, NOT_SET, NA = "OK", "WARN", "BREACH", "NOT_SET", "N/A"
LEVELS = (OK, WARN, BREACH, NOT_SET, NA)
DESK, EXCHANGE = "desk", "exchange"
_EPS = 1e-9

_BASIS = {
    "gross_lots": "sum of |lots| over every open contract position (options at their delta lots)",
    "gross_usd": "sum of |USD delta| over every open contract position",
    "net_usd_commodity": "|net USD delta| of the root, all months",
    "net_usd_sector": "|net USD delta| of the sector",
    "lots_per_contract_month": "|net lots| of the root in that month, every product (options at delta lots)",
    "exchange_spot_month": ("|net lots| in the spot month: the held contract month whose last trade date is "
                            "the nearest after the as-of date"),
    "exchange_single_month": "the largest |net lots| in any one contract month",
    "exchange_all_months": "|net lots| over all contract months",
}
_CONFIG_KEY = {
    "gross_lots": "desk_limits.gross_lots",
    "gross_usd": "desk_limits.gross_usd",
    "net_usd_commodity": "desk_limits.net_usd_per_commodity",
    "net_usd_sector": "desk_limits.net_usd_per_sector",
    "lots_per_contract_month": "desk_limits.lots_per_contract_month",
    "exchange_spot_month": "exchange_limits.<root>.spot_month",
    "exchange_single_month": "exchange_limits.<root>.single_month",
    "exchange_all_months": "exchange_limits.<root>.all_months",
}


def _check(limit: str, scope: str, source: str, unit: str, value: Optional[float], limit_value: Optional[float],
           warn: float, why_none: str = "") -> dict:
    out = {"limit": limit, "scope": scope, "source": source, "unit": unit, "value": value,
           "limit_value": limit_value, "used_pct": None, "level": NOT_SET, "reason": "",
           "basis": _BASIS[limit]}
    if limit_value is None:
        out["reason"] = f"no limit set in config/limits.yaml ({_CONFIG_KEY[limit]})"
        return out
    if value is None:
        out["level"], out["reason"] = NA, why_none or "the position has no figure"
        return out
    size = abs(value)
    if limit_value <= _EPS:
        out["level"] = BREACH if size > _EPS else OK
        out["reason"] = "the limit is 0: no position allowed" if size > _EPS else ""
        return out
    used = size / limit_value
    out["used_pct"] = used * 100.0
    if used > 1.0 + _EPS:
        out["level"], out["reason"] = BREACH, f"{used:.0%} of the limit"
    elif used >= warn - _EPS:
        out["level"], out["reason"] = WARN, f"{used:.0%} of the limit (warn from {warn:.0%})"
    else:
        out["level"] = OK
    return out


def _error(reason: str, scope: str) -> List[dict]:
    return [{"limit": "config", "scope": scope, "source": "", "unit": "", "value": None, "limit_value": None,
             "used_pct": None, "level": NA, "reason": reason, "basis": ""}]


def _sum_abs(values) -> Optional[float]:
    vals = list(values)
    return None if any(v is None for v in vals) else float(sum(abs(v) for v in vals))


def _why_rows(rows: List[dict], get, what: str) -> str:
    return "; ".join(f"{r['contract_id']}: {r.get('reason') or 'no ' + what}" for r in rows if get(r) is None)


def limit_checks(conn: sqlite3.Connection, as_of: str, *, config_path: Optional[Path] = None,
                 curve: Optional[dict] = None) -> List[dict]:
    """The book's limit checks on ``as_of`` (see the module docstring).

    Returns a list of ``{limit, scope, source ('desk' | 'exchange'), unit ('lots' | 'USD'), value
    (signed where it is a net), limit_value (None = not set), used_pct (|value| / limit x 100, None
    when not computed), level ('OK' | 'WARN' | 'BREACH' | 'NOT_SET' | 'N/A'), reason, basis}``: the
    book's two, then per sector, per commodity, the exchange limits per commodity, then per
    contract month. When the file is refused or the positions cannot be read, one row
    ``limit = 'config'`` at level N/A carries the reason.
    """
    try:
        cfg = load_limits(config_path)
    except LimitsConfigError as exc:
        return _error(f"config/limits.yaml refused: {exc}", str(config_path or "config/limits.yaml"))
    if curve is None:
        from engine.curve import curve_positions
        curve = curve_positions(conn, as_of)
    if not curve.get("available", False):
        return _error("; ".join(curve.get("reasons") or []) or "no curve positions", "curve positions")
    return _checks(curve, cfg)


def _checks(curve: dict, cfg: LimitsConfig) -> List[dict]:
    warn = cfg.warn_fraction
    rows = curve.get("rows") or []
    out: List[dict] = []
    out.append(_check("gross_lots", "book", DESK, "lots", _sum_abs(position_lots(r) for r in rows),
                      cfg.gross_lots, warn, _why_rows(rows, position_lots, "delta lots")))
    out.append(_check("gross_usd", "book", DESK, "USD", _sum_abs(r.get("delta_usd") for r in rows),
                      cfg.gross_usd, warn, _why_rows(rows, lambda r: r.get("delta_usd"), "USD delta")))
    for sector, s in (curve.get("by_sector") or {}).items():
        out.append(_check("net_usd_sector", sector, DESK, "USD", s.get("net_delta_usd"),
                          cfg.net_usd_limit_sector(sector), warn, s.get("delta_reason", "")))
    for root_id, c in (curve.get("by_commodity") or {}).items():
        out.append(_check("net_usd_commodity", root_id, DESK, "USD", c.get("net_delta_usd"),
                          cfg.net_usd_limit_commodity(root_id), warn, c.get("delta_reason", "")))

    cells = build_cells(rows)
    by_root: Dict[str, List[dict]] = {}
    for cell in cells.values():
        by_root.setdefault(cell["root_id"], []).append(cell)
    for root_id, mine in by_root.items():
        out += _exchange(root_id, mine, cfg, warn)
    for cell in cells.values():
        out.append(_check("lots_per_contract_month", f"{cell['root_id']} {cell['month']}", DESK, "lots",
                          cell["position_lots"], cfg.lots_per_month_limit(cell["root_id"]), warn,
                          cell["reason"] or "an option in it has no delta"))
    return out


def _exchange(root_id: str, cells: List[dict], cfg: LimitsConfig, warn: float) -> List[dict]:
    why = "; ".join(c["reason"] for c in cells if c["position_lots"] is None and c["reason"]) \
        or "an option has no delta"
    spot = min(cells, key=lambda c: (c["expiry"], c["month"]))
    est = ", estimated" if spot["dates_estimated"] else ""
    spot_scope = f"{root_id} {spot['month']} (last trade {spot['expiry']}{est})"
    known = [c for c in cells if c["position_lots"] is not None]
    if len(known) == len(cells):
        biggest = max(cells, key=lambda c: abs(c["position_lots"]))
        single_value, single_scope = biggest["position_lots"], f"{root_id} {biggest['month']}"
        all_value = float(sum(c["position_lots"] for c in cells))
    else:
        single_value, single_scope, all_value = None, root_id, None
    return [
        _check("exchange_spot_month", spot_scope, EXCHANGE, "lots", spot["position_lots"],
               cfg.exchange_limit(root_id, "spot_month"), warn, spot["reason"] or why),
        _check("exchange_single_month", single_scope, EXCHANGE, "lots", single_value,
               cfg.exchange_limit(root_id, "single_month"), warn, why),
        _check("exchange_all_months", root_id, EXCHANGE, "lots", all_value,
               cfg.exchange_limit(root_id, "all_months"), warn, why),
    ]
