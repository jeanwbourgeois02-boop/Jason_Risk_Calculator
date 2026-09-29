"""Each scenario's P&L per Book position (the Risk tab's Stress fold, Phase G).

The positions are the Book's own, keyed by spreads-engine's position ids
(``engine.spreads.period_explain.positions_of``, called, never copied: on Jason's book one per
PBRoot trade name, 'POSITION-STRATEGY-<name>'; then 'OUTRIGHT-<instrument_id>', then
'TRADE-<trade_id>'), over ``book_spreads``' output. A trade is in one position only.

A contract's scenario P&L (a ``by_contract`` entry) is split over the trades that make up its
curve-positions row by each trade's own booked quantity (``trades.quantity``: contracts, option
lots, an LME ticket's tonnes): share = quantity / the row's summed quantity. The scenario P&L is
linear in the lots (delta x move), so a contract two trades share is attributed to each by its
own lots, and the shares of a row add up to the row exactly. Nothing is re-priced and no delta is
taken here: the figures are the scenario's own.

An fx scenario's P&L (the P&L held in the moved currency x move) is split by each trade's own
USD P&L: curve-positions' ``currency_exposure[ccy]['by_trade']`` when it gives one, else
``value_book`` on the trades of the rows and flat contracts quoted in that currency
(pnl-valuation's figure, the one ``currency_exposure`` sums),
checked to add up to the currency's figure to the cent.

What cannot be attributed (a row with a quantity that is not a number or that sums to zero, a
currency whose per-trade P&L does not add up) is listed in ``unattributed`` with its figure and
reason, so ``sum(by_position) + sum(unattributed) == total_usd``. A contract the scenario could
not price (the scenario's ``missing``) is named per position in ``missing``; the position's
figure is the sum of its known parts, like the book total.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Dict, List, Optional, Tuple

_CENT = 0.005


def _number(value) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class PositionMap:
    """trade_id -> (position_id, name), and every trade's booked quantity."""

    def __init__(self, conn: Optional[sqlite3.Connection], spreads: Optional[dict], positions: dict):
        self.reason = ""
        self.of_trade: Dict[str, str] = {}
        self.names: Dict[str, str] = {}
        self.quantity: Dict[str, Optional[float]] = {}
        self.currency: Dict[str, str] = {}
        rows = list(positions.get("rows") or [])
        flat = list(positions.get("flat_contracts") or [])
        if spreads is None:
            self.reason = "the book's positions are not known (spreads-engine's book_spreads was not available)"
            return
        if conn is None:
            self.reason = "the trades' quantities are not known (no database given)"
            return
        trade_rows: Dict[str, dict] = {}
        for r in rows + flat:
            for t in r.get("trade_ids") or []:
                trade_rows[str(t)] = {"instrument_id": r.get("instrument_id") or r.get("contract_id"),
                                      "product": r.get("product")}
        try:
            from engine.spreads.period_explain import positions_of
            listed = positions_of(spreads, trade_rows)
        except Exception as exc:  # another lane's fault is a reason, never a crash
            self.reason = f"the book's positions could not be read: {exc}"
            return
        for p in listed:
            pid = str(p["position_id"])
            self.names[pid] = str(p.get("name") or pid)
            for t in p.get("trade_ids") or []:
                if str(t) in trade_rows:
                    self.of_trade[str(t)] = pid
        ids = sorted(trade_rows)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            sql = (f"SELECT t.trade_id, t.quantity, i.quote_ccy FROM trades_official t "
                   f"LEFT JOIN instruments i ON i.instrument_id = t.instrument_id WHERE t.trade_id IN ({marks})")
            for tid, q, ccy in conn.execute(sql, chunk):
                self.quantity[str(tid)] = _number(q)
                self.currency[str(tid)] = str(ccy or "")

    @property
    def available(self) -> bool:
        return not self.reason


def _row_shares(pm: PositionMap, row: dict) -> Tuple[Optional[Dict[str, float]], str]:
    """({position_id: share of the row}, why None)."""
    tids = [str(t) for t in row.get("trade_ids") or []]
    if not tids:
        return None, "the row names no trades"
    qs = {t: pm.quantity.get(t) for t in tids}
    bad = [t for t, q in qs.items() if q is None]
    if bad:
        return None, f"trades.quantity is not a number for {', '.join(bad)}"
    total = sum(qs.values())
    if abs(total) < 1e-12:
        return None, "its trades' quantities add up to zero"
    shares: Dict[str, float] = {}
    for t, q in qs.items():
        pid = pm.of_trade.get(t, f"TRADE-{t}")
        shares[pid] = shares.get(pid, 0.0) + q / total
    return shares, ""


def _finish(pm: PositionMap, by_pos: Dict[str, float], unattributed: List[dict], missing: List[dict],
            total: Optional[float]) -> dict:
    worst = None
    if by_pos:
        pid = min(by_pos, key=lambda k: by_pos[k])
        if by_pos[pid] < 0:
            worst = {"position_id": pid, "name": pm.names.get(pid, pid), "pnl_usd": by_pos[pid]}
    check = None
    if total is not None:
        check = float(total - sum(by_pos.values()) - sum(u["pnl_usd"] for u in unattributed))
    return {"by_position": by_pos, "worst_position": worst,
            "by_position_detail": {"names": {p: pm.names.get(p, p) for p in by_pos},
                                   "missing": missing, "unattributed": unattributed,
                                   "residual_usd": check, "reason": ""}}


def empty(reason: str) -> dict:
    return {"by_position": None, "worst_position": None,
            "by_position_detail": {"names": {}, "missing": [], "unattributed": [], "residual_usd": None,
                                   "reason": reason}}


def positional(pm: PositionMap, result: dict, positions: dict) -> dict:
    """by_position of an outright / curve / spread / replay scenario."""
    if not pm.available:
        return empty(pm.reason)
    rows = {str(r.get("contract_id")): r for r in positions.get("rows") or []}
    by_pos: Dict[str, float] = {}
    unattributed: List[dict] = []
    for c in result.get("by_contract") or []:
        row = rows.get(str(c.get("contract_id"))) or {}
        shares, why = _row_shares(pm, row)
        if shares is None:
            unattributed.append({"contract_id": c.get("contract_id", ""), "pnl_usd": c["pnl_usd"],
                                 "reason": f"{c.get('contract_id')}: {why}"})
            continue
        for pid, share in shares.items():
            by_pos[pid] = by_pos.get(pid, 0.0) + c["pnl_usd"] * share
    missing: List[dict] = []
    for m in result.get("missing") or []:
        row = rows.get(str(m.get("contract_id"))) or {}
        pids = sorted({pm.of_trade.get(str(t), f"TRADE-{t}") for t in row.get("trade_ids") or []})
        for pid in pids or [""]:
            missing.append({"position_id": pid, "contract_id": m.get("contract_id", ""), "reason": m.get("reason", "")})
    return _finish(pm, by_pos, unattributed, missing, result.get("total_usd"))


def _trade_pnl_by_ccy(pm: PositionMap, conn, as_of: str, positions: dict, ccys: List[str]
                      ) -> Tuple[Dict[str, Dict[str, Optional[float]]], str]:
    """{ccy: {trade_id: pnl_usd}}: curve-positions' own by_trade where it gives one, else
    value_book on the trades of the curve rows and flat contracts in that currency."""
    exposure = positions.get("currency_exposure") or {}
    out: Dict[str, Dict[str, Optional[float]]] = {}
    need = []
    for ccy in ccys:
        given = (exposure.get(ccy) or {}).get("by_trade")
        if isinstance(given, dict):
            out[ccy] = {str(k): _number(v) for k, v in given.items()}
        else:
            need.append(ccy)
    if not need:
        return out, ""
    if conn is None:
        return out, "the trades' P&L is not known (no database given)"
    tids = {t for t, c in pm.currency.items() if c in need}
    try:
        from engine.pnl.valuation import value_book
        book = value_book(conn, as_of, trade_ids=sorted(tids))
    except Exception as exc:  # pnl-valuation's fault is a reason, never a crash
        return out, f"the trades' P&L could not be read: {exc}"
    for ccy in need:
        out[ccy] = {}
    for r in book.to_dict("records"):
        tid = str(r["trade_id"])
        ccy = pm.currency.get(tid, "")
        if ccy in need:
            out[ccy][tid] = _number(r.get("pnl_usd"))
    for tid in tids:            # a trade value_book gave no row: no figure, never zero
        out[pm.currency[tid]].setdefault(tid, None)
    return out, ""


def fx(pm: PositionMap, conn, as_of: str, result: dict, positions: dict) -> dict:
    """by_position of an fx scenario: each currency's pnl_change_usd split by the trades' own USD
    P&L held in it."""
    if not pm.available:
        return empty(pm.reason)
    cur = [c for c in result.get("by_currency") or [] if c.get("pnl_change_usd") is not None]
    per_trade, why = _trade_pnl_by_ccy(pm, conn, as_of, positions, [c["currency"] for c in cur])
    by_pos: Dict[str, float] = {}
    unattributed: List[dict] = []
    for c in cur:
        ccy, move = c["currency"], c["move"]
        trades = per_trade.get(ccy)
        if trades is None:
            unattributed.append({"contract_id": ccy, "pnl_usd": c["pnl_change_usd"], "reason": f"{ccy}: {why}"})
            continue
        known = {t: v for t, v in trades.items() if v is not None}
        if len(known) != len(trades) or abs(sum(known.values()) - (_number(c.get("pnl_usd")) or 0.0)) > _CENT:
            unattributed.append({"contract_id": ccy, "pnl_usd": c["pnl_change_usd"],
                                 "reason": (f"{ccy}: the trades' own P&L ({sum(known.values()):,.2f}) does not add up "
                                            f"to the P&L held in {ccy} ({c.get('pnl_usd')})")})
            continue
        part: Dict[str, float] = {}
        for t, v in known.items():
            pid = pm.of_trade.get(t, f"TRADE-{t}")
            part[pid] = part.get(pid, 0.0) + v * move
        for pid, v in part.items():
            by_pos[pid] = by_pos.get(pid, 0.0) + v
    missing = [{"position_id": "", "contract_id": m.get("currency") or m.get("contract_id", ""),
                "reason": m.get("reason", "")} for m in result.get("missing") or [] if not m.get("contract_id")]
    return _finish(pm, by_pos, unattributed, missing, result.get("total_usd"))
