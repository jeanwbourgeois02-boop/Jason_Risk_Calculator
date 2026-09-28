"""The Daily P&L of a strategy split into spread, FX and hedge (user yes, 2026-09-28).

``daily_split(rows_t, rows_prev, trade_ids, hedge_ids)`` is a pure function over two
``value_book`` frames: the as-of valuation and the reference close the screens' ``period_rows``
already pairs with it (the step back and the fill decided there, never here). It splits each
included trade's Daily, ``pnl_usd(t) - pnl_usd(prev)``, by the identity

    L_t x S_t - L_p x S_p  =  (L_t - L_p) x S_t  +  L_p x (S_t - S_p)

where ``L`` is the row's local P&L (``pnl_local``) and ``S`` its USD conversion (``spot``, 1 on
a USD contract, so a USD leg's whole Daily is *spread*): the first term is the spread's own
move at today's conversion, the second the move of the currency on the P&L already made.
A hedge trade's whole Daily is *hedge*. spread + fx + hedge equals the sum of the included
trades' Daily to the cent, by construction.

Which trades are included follows ``engine.pnl.reference.diff_split`` exactly: priced on both
dates, or priced today with no row on the reference close (new since it: its whole LTD counts,
``L_p = 0``, as ``period_rows`` says "trading P&L"); a trade priced on one side only is
excluded with its reason, never faked. A priced row whose local P&L or spot is not a number
(a closed-out option carries no local figure) is counted whole as spread and named in
``reasons``: the identity holds, the split is not silent.

No valuation, no database, no new mark: only the columns ``value_book`` already gives.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

SPLIT_KEYS = ("spread", "fx", "hedge")


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _rows(df: Optional[pd.DataFrame]) -> Dict[str, dict]:
    if df is None or len(df) == 0:
        return {}
    return {str(r["trade_id"]): r for r in df.to_dict("records")}


def _priced(row: Optional[dict]) -> bool:
    return row is not None and not str(row.get("reason", "") or "") and _num(row.get("pnl_usd")) is not None


def _why(row: Optional[dict], day_word: str) -> str:
    if row is None:
        return f"not valued on the {day_word}"
    return str(row.get("reason", "") or "") or f"no USD P&L on its row on the {day_word}"


def daily_split(rows_t: pd.DataFrame, rows_prev: pd.DataFrame, trade_ids: Iterable[str],
                hedge_ids: Iterable[str] = ()) -> dict:
    """``{spread, fx, hedge, total, excluded, reasons, included, by_trade}``.

    - ``spread`` / ``fx`` / ``hedge`` / ``total``: USD, summed over the included trades
      (``total = spread + fx + hedge``); 0.0 when nothing is included (``included`` says so).
    - ``excluded``: ``[(trade_id, why)]`` for the trades of ``trade_ids`` left out (unpriced
      today, or priced today but not on the reference close).
    - ``reasons``: ``{trade_id: sentence}`` for included trades whose split needed a note (a
      row with no local P&L or spot: counted whole as spread; a trade new since the close).
    - ``included``: the trade ids counted, sorted.
    - ``by_trade``: ``{trade_id: {spread, fx, hedge}}`` for the included trades.
    """
    today, prev = _rows(rows_t), _rows(rows_prev)
    hedges = frozenset(str(h) for h in hedge_ids)
    sums = {k: 0.0 for k in SPLIT_KEYS}
    excluded: List[Tuple[str, str]] = []
    reasons: Dict[str, str] = {}
    by_trade: Dict[str, dict] = {}
    for tid in sorted(str(t) for t in trade_ids):
        r_t, r_p = today.get(tid), prev.get(tid)
        if not _priced(r_t):
            excluded.append((tid, f"unpriced today: {_why(r_t, 'as-of date')}"))
            continue
        pnl_t = float(r_t["pnl_usd"])
        if r_p is None:
            pnl_p, new = 0.0, True
        elif not _priced(r_p):
            excluded.append((tid, f"priced today but not on the reference close ({_why(r_p, 'reference close')}): "
                                  f"left out rather than faked"))
            continue
        else:
            pnl_p, new = float(r_p["pnl_usd"]), False
        part = {k: 0.0 for k in SPLIT_KEYS}
        if tid in hedges:
            part["hedge"] = pnl_t - pnl_p
        elif new:
            part["spread"] = pnl_t          # L_p = 0: its whole LTD is the spread's own move
            reasons[tid] = "new since the reference close: its whole LTD counts (trading P&L), all of it spread"
        else:
            l_t, s_t, l_p, s_p = (_num(r_t.get("pnl_local")), _num(r_t.get("spot")),
                                  _num(r_p.get("pnl_local")), _num(r_p.get("spot")))
            if None in (l_t, s_t, l_p, s_p):
                part["spread"] = pnl_t - pnl_p
                reasons[tid] = ("its row carries no local P&L or USD conversion on one of the two dates, so its "
                                "Daily is not split: counted whole as spread")
            else:
                spread = (l_t - l_p) * s_t
                fx = l_p * (s_t - s_p)
                # the identity's rounding (1e-10 of a dollar) is put on the spread, so the sum is exact
                part["spread"] = spread + ((pnl_t - pnl_p) - (spread + fx))
                part["fx"] = fx
        for k in SPLIT_KEYS:
            sums[k] += part[k]
        by_trade[tid] = part
    total = sums["spread"] + sums["fx"] + sums["hedge"]
    return {**sums, "total": total, "excluded": excluded, "reasons": reasons,
            "included": sorted(by_trade), "by_trade": by_trade}
