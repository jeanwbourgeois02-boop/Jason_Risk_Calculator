"""The book's commodity positions per (contract root, contract month): the unit margin and the
position limits are measured on.

Built from ``engine.curve.curve_positions`` rows only (nothing re-marked here). A cell nets every
product of one root in one contract month: futures and LME forwards at their lots, options on
futures at their delta lots (futures-equivalent), because margin follows exposure and the
exchanges aggregate futures-equivalent positions.

Per cell:
- ``position_lots``: futures and LME forwards' lots plus options' delta lots, netted (None when
  an option in it has no delta);
- ``delta_lots`` / ``delta_usd``: the curve rows' delta summed (None when any row has none);
- ``contracts``, ``products``, ``expiry`` (the earliest), ``dates_estimated`` (a date in it is
  contract-master's estimate, not Bloomberg's), ``reason``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

OPTION = "CMDTY_OPTION"
ESTIMATED = "ESTIMATED"

CellKey = Tuple[str, str]


def _sum(values) -> Optional[float]:
    vals = list(values)
    return None if any(v is None for v in vals) else float(sum(vals))


def month_of(row: dict) -> str:
    """'2026-12' for a row with a contract month; else its contract id (its own cell)."""
    if row.get("year") is not None and row.get("month") is not None:
        return f"{int(row['year']):04d}-{int(row['month']):02d}"
    return str(row.get("contract_id") or "")


def position_lots(row: dict) -> Optional[float]:
    return row.get("delta_lots") if row.get("product") == OPTION else row.get("lots")


def build_cells(rows: List[dict]) -> Dict[CellKey, dict]:
    """{(root_id, month): cell}, in the curve rows' order (sector, root, expiry)."""
    grouped: Dict[CellKey, List[dict]] = {}
    for r in rows:
        grouped.setdefault((str(r["root_id"]), month_of(r)), []).append(r)
    cells: Dict[CellKey, dict] = {}
    for (root_id, month), mine in grouped.items():
        first = mine[0]
        reasons = [f"{r['contract_id']}: {r['reason']}" for r in mine if r.get("reason")]
        cells[(root_id, month)] = {
            "root_id": root_id, "month": month, "name": first.get("name", root_id),
            "sector": first.get("sector", ""), "exchange": first.get("exchange", ""),
            "contracts": list(dict.fromkeys(str(r["contract_id"]) for r in mine)),
            "products": list(dict.fromkeys(str(r["product"]) for r in mine)),
            "expiry": min(str(r.get("expiry") or "") for r in mine),
            "dates_estimated": any(r.get("dates_source") == ESTIMATED for r in mine),
            "position_lots": _sum(position_lots(r) for r in mine),
            "delta_lots": _sum(r.get("delta_lots") for r in mine),
            "delta_usd": _sum(r.get("delta_usd") for r in mine),
            "rows": mine,
            "reason": "; ".join(reasons),
        }
    return cells
