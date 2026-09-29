"""The P&L explain of a period (user yes under hard rule 7, 2026-09-29: the P&L tab's headline).

``period_explain(conn, as_of, key)`` splits the period's total P&L, the header's own figure
(``engine.pnl.series.period_pnl(...).total``), into what drove it. It extends the one-day
``daily_split`` of 2026-09-28 over a period; no trade's P&L formula changes, nothing is priced,
nothing is written: every figure is a trade's period P&L as ``period_pnl`` measured it, put in
exactly one bucket.

The classification of each INCLUDED trade of ``period_pnl``'s ``by_trade``, in this order, first
match wins:

1. ``new_trades``: kind 'new trade' (dealt after the reference close: its whole LTD at the end
   of the period counts, as the header's "trading P&L"), whatever it is and whether or not it
   settled since.
2. ``realised``: the trade SETTLED within the period (``by_trade.realised`` as pnl-series gives
   it: settled at the end, not on the reference close; since 2026-09-29 settled only, so a
   closed-out option group is not realised until it settles): its whole change, marks and
   currency alike.
3. ``hedge``: a currency hedge (``hedges.is_hedge``: a future on an fx-sector root, the SGX
   USD/CNH future, or an FX spot / forward / swap / option on a currency pair; never a
   precious-metal pair such as XAUUSD, which is a position of its own and goes to step 4; user,
   2026-09-29; the Book's own test, strategy or not).
4. otherwise split by ``daily_split``'s identity between the reference close's row and the end
   row, ``L_t S_t - L_p S_p = (L_t - L_p) S_t + L_p (S_t - S_p)``: ``spread`` the first term
   (the contracts' own move at the end's conversion), ``fx`` the second (the currency's move on
   the P&L already made; 0 on a USD trade, whose S is 1). The identity's float rounding goes on
   the spread, as ``daily_split`` does.
5. ``other``: a trade of step 4 whose row carries no local P&L or USD conversion on one of the
   two dates (a closed-out option carries no local figure), so it cannot be split: its whole
   change, named in ``other_trades`` with the reason. Never silently into the spread.

So ``spread + fx + hedge + new_trades + realised + other == total`` exactly (to float rounding,
far below a cent), and the positions' totals add up to it too.

LTD has no reference close (``start_ref`` None): every trade's LTD would be "new", so LTD reports
``realised`` (trades settled by the as-of date) and ``open`` only; ``spread``,
``fx``, ``hedge``, ``new_trades`` and ``other`` are None with ``split_reason`` saying why.

Positions (``by_position``): the Book's own grouping from ``book_spreads`` (its ``positions``,
open or closed, their ``position_id`` and ``name``), then each contract of the futures the rule
left outright (``OUTRIGHT-<instrument_id>``, the Book's row id), then every other trade on its
own (``TRADE-<trade_id>``, the Book's row id): an option, an LME ticket, an FX trade. A trade is in
one position only, so the positions add up to the period total.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from data.contracts import load_roots
from engine.pnl.series import DailySeries, PeriodPnl, daily_series, period_pnl, period_start
from engine.pnl.valuation import value_book
from engine.spreads.daily_split import daily_split
from engine.spreads.hedges import is_hedge

EXPLAIN_KEYS = ("daily", "d5", "mtd", "ytd", "ltd")
COMPONENTS = ("spread", "fx", "hedge", "new_trades", "realised", "other")
LTD_SPLIT_REASON = "LTD has no reference close to split against: realised and open only"
_CENT = 0.005


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


def _currencies(conn: sqlite3.Connection, instrument_ids: Iterable[str]) -> Dict[str, tuple]:
    """{instrument_id: (base_ccy, quote_ccy)}."""
    ids = sorted({str(i) for i in instrument_ids})
    out: Dict[str, tuple] = {}
    for n in range(0, len(ids), 500):
        chunk = ids[n:n + 500]
        sql = (f"SELECT instrument_id, base_ccy, quote_ccy FROM instruments "
               f"WHERE instrument_id IN ({','.join('?' * len(chunk))})")
        out.update({str(i): (str(b or ""), str(q or "")) for i, b, q in conn.execute(sql, chunk)})
    return out


def _series_reader(series: DailySeries):
    """``book_spreads``' ``value_fn`` off the series' filled frames (what the screens' shared
    filled reader gives), so the grouping costs no second pricing of a day the series holds."""
    def read(conn: sqlite3.Connection, day: str) -> pd.DataFrame:
        try:
            return series.frame(day)
        except KeyError:
            return value_book(conn, day)
    return read


def positions_of(spreads: dict, trade_rows: Dict[str, dict]) -> List[dict]:
    """[{position_id, name, kind, trade_ids}] covering every trade of ``trade_rows`` once: the
    Book's positions, then ``OUTRIGHT-<instrument_id>`` per outright contract, then
    ``TRADE-<trade_id>``. The one list of the Book's row ids, read by ``scorecard`` too."""
    out: List[dict] = []
    taken: set = set()
    for p in spreads.get("positions") or []:
        tids = [str(t) for t in p.get("trade_ids") or [] if str(t) not in taken]
        if not tids:
            continue
        taken.update(tids)
        out.append({"position_id": str(p.get("position_id") or p.get("name")), "name": str(p.get("name") or ""),
                    "kind": str(p.get("kind") or ""), "trade_ids": sorted(tids)})
    by_inst: Dict[str, List[str]] = {}
    for o in spreads.get("outrights") or []:
        tid = str(o.get("trade_id"))
        if tid in taken:
            continue
        by_inst.setdefault(str(o.get("instrument_id") or ""), []).append(tid)
    for inst, tids in sorted(by_inst.items()):
        taken.update(tids)
        out.append({"position_id": f"OUTRIGHT-{inst}", "name": inst, "kind": "outright", "trade_ids": sorted(tids)})
    for tid in sorted(trade_rows):
        if tid in taken:
            continue
        taken.add(tid)
        r = trade_rows[tid]
        out.append({"position_id": f"TRADE-{tid}", "name": str(r.get("instrument_id") or tid),
                    "kind": str(r.get("product") or "trade").lower(), "trade_ids": [tid]})
    return out


_positions_of = positions_of      # the old private name, kept so nothing breaks


def _blank(key: str, start_ref: Optional[str], reason: str) -> dict:
    return {"key": key, "as_of": "", "start_ref": start_ref, "ref_used": None, "ref_note": "", "total": None,
            **{k: None for k in COMPONENTS}, "open": None, "available": False, "reason": reason,
            "split_reason": reason, "excluded": [], "other_trades": [], "by_trade": [], "by_position": []}


def classify_trades(pp: PeriodPnl, roots, ccys: Dict[str, tuple], ltd: bool = False
                    ) -> Tuple[Dict[str, dict], Dict[str, str], List[dict]]:
    """The one bucket order (module docstring), over ``period_pnl``'s included trades as they come
    (its ``kind`` and ``realised`` flags are never re-derived here): ``(parts, bucket,
    other_trades)`` with ``parts[trade_id]`` = ``{spread, fx, hedge, new_trades, realised, other,
    _amount}`` (``_amount`` the trade's period P&L), ``bucket[trade_id]`` the step that took it
    ('new_trades' | 'realised' | 'hedge' | 'spread' | 'other'; for LTD 'realised' | 'open') and
    ``other_trades`` ``[{trade_id, amount, why}]`` of a cent or more. ``roots``: contract-master's
    ``load_roots()``; ``ccys``: ``{instrument_id: (base_ccy, quote_ccy)}``. Read by the Book's
    strategy Daily split too (``strategies.py``), so both screens bucket a trade alike."""
    end_rows = _rows(pp.frame_end)
    included = pp.by_trade[pp.by_trade["included"]] if len(pp.by_trade) else pp.by_trade
    parts: Dict[str, dict] = {}
    bucket: Dict[str, str] = {}
    to_split: List[str] = []
    for rec in included.to_dict("records"):
        tid, amount = str(rec["trade_id"]), float(rec["pnl_usd"])
        part = {c: 0.0 for c in COMPONENTS}
        if ltd:
            name = "realised" if bool(rec["realised"]) else "open"
            if name == "realised":
                part["realised"] = amount
        elif rec["kind"] == "new trade":
            name, part["new_trades"] = "new_trades", amount
        elif bool(rec["realised"]):
            name, part["realised"] = "realised", amount
        else:
            row = end_rows.get(tid) or {}
            inst = str(row.get("instrument_id") or "")
            base, quote = ccys.get(inst, ("", ""))
            if is_hedge(roots.get(base), str(row.get("product") or ""), base, quote, inst):
                name, part["hedge"] = "hedge", amount
            else:
                name = "split"
                to_split.append(tid)
        parts[tid], bucket[tid] = part, name
        parts[tid]["_amount"] = amount

    other_trades: List[dict] = []
    if to_split:
        split = daily_split(pp.frame_end, pp.frame_ref, to_split)
        for tid in to_split:
            amount = parts[tid]["_amount"]
            got = split["by_trade"].get(tid)
            why = split["reasons"].get(tid, "")
            if got is None:
                why = next((w for t, w in split["excluded"] if t == tid), "") or "not split"
            if got is None or why:
                # no local P&L or conversion on one of the two dates: its whole change, named, never spread
                parts[tid]["other"] = amount
                bucket[tid] = "other"
                if abs(amount) >= _CENT:
                    other_trades.append({"trade_id": tid, "amount": amount, "why": why})
                continue
            parts[tid]["fx"] = got["fx"]
            parts[tid]["spread"] = amount - got["fx"]       # the identity's rounding on the spread, as daily_split
            bucket[tid] = "spread"
    return parts, bucket, other_trades


def period_explain(conn: sqlite3.Connection, as_of: str, key: str, series: Optional[DailySeries] = None,
                   spreads: Optional[dict] = None) -> dict:
    """The period's P&L split by driver. ``key``: daily | d5 | mtd | ytd | ltd.

    ``series``: ``engine.pnl.series.daily_series(conn, as_of)`` when None (a caller rendering
    several periods passes the one it holds). ``spreads``: ``book_spreads(conn, as_of)``' result
    for the positions, built off the series' filled frames when None.

    Returns ``{key, as_of, start_ref, ref_used, ref_note, total, spread, fx, hedge, new_trades,
    realised, other, open, available, reason, split_reason, excluded, other_trades, by_trade,
    by_position}``:

    - ``total``: ``period_pnl(...).total``, the header's figure (None when the period has none,
      ``reason`` says why; every component is then None too).
    - ``spread`` / ``fx`` / ``hedge`` / ``new_trades`` / ``realised`` / ``other``: USD, summing to
      ``total``. For LTD only ``realised`` is a figure (the others None, ``split_reason`` says why).
    - ``open``: ``total - realised`` (the P&L of trades not settled within the period; for
      LTD, of the trades not settled on ``as_of``, a closed-out option group included).
    - ``excluded``: ``[(trade_id, why)]``, ``period_pnl``'s trades left out of the total.
    - ``other_trades``: ``[{trade_id, amount, why}]``: the trades in ``other`` whose amount is a
      cent or more (a zero change needs no explaining).
    - ``by_trade``: ``[{trade_id, position_id, bucket, total, spread, fx, hedge, new_trades,
      realised, other}]`` for the included trades (``bucket`` the step that took it).
    - ``by_position``: ``[{position_id, name, kind, trade_ids, total, spread, fx, hedge,
      new_trades, realised, other, open, n_excluded}]``, largest total first; a position with no
      included trade has ``total`` None and its count in ``n_excluded``. The known totals add up
      to ``total``.
    """
    if key not in EXPLAIN_KEYS:
        raise ValueError(f"unknown period {key!r}: one of {', '.join(EXPLAIN_KEYS)}")
    if series is None:
        series = daily_series(conn, as_of)
    start_ref = period_start(as_of, key, series.holidays)
    try:
        pp = period_pnl(series, start_ref, as_of)
    except KeyError as exc:
        return {**_blank(key, start_ref, f"{as_of} is not a business day of the daily series ({exc})"),
                "as_of": as_of}
    ltd = key == "ltd"
    out = {"key": key, "as_of": as_of, "start_ref": pp.start_ref, "ref_used": pp.ref_used, "ref_note": pp.ref_note,
           "total": pp.total, "available": pp.available, "reason": pp.reason,
           "split_reason": LTD_SPLIT_REASON if ltd else "", "excluded": list(pp.excluded)}
    end_rows = _rows(pp.frame_end)
    if spreads is None:
        from engine.spreads.book import book_spreads
        spreads = book_spreads(conn, as_of, value_fn=_series_reader(series))
    positions = positions_of(spreads, end_rows)
    position_of = {t: p["position_id"] for p in positions for t in p["trade_ids"]}

    if not pp.available or pp.total is None:
        out.update({k: None for k in COMPONENTS}, open=None, split_reason=pp.reason, other_trades=[], by_trade=[],
                   by_position=[{**{k: p[k] for k in ("position_id", "name", "kind", "trade_ids")},
                                 "total": None, "open": None, **{c: None for c in COMPONENTS},
                                 "n_excluded": len(p["trade_ids"])} for p in positions])
        return out

    parts, bucket, other_trades = classify_trades(pp, load_roots(), _currencies(conn, (
        r.get("instrument_id") for r in end_rows.values())), ltd)

    by_trade = []
    for tid in sorted(parts):
        amount = parts[tid].pop("_amount")
        by_trade.append({"trade_id": tid, "position_id": position_of.get(tid, f"TRADE-{tid}"), "bucket": bucket[tid],
                         "total": amount, **parts[tid]})
    sums = {c: float(sum(p[c] for p in parts.values())) for c in COMPONENTS}
    if ltd:
        out.update({c: None for c in COMPONENTS if c != "realised"}, realised=sums["realised"],
                   open=pp.total - sums["realised"])
    else:
        out.update(sums, open=pp.total - sums["realised"])
    out["other_trades"] = other_trades
    out["by_trade"] = by_trade
    out["by_position"] = _by_position(positions, parts, by_trade, ltd)
    return out


def _by_position(positions: Sequence[dict], parts: Dict[str, dict], by_trade: Sequence[dict], ltd: bool) -> List[dict]:
    totals = {r["trade_id"]: r["total"] for r in by_trade}
    rows = []
    for p in positions:
        ids = [t for t in p["trade_ids"] if t in parts]
        row = {k: p[k] for k in ("position_id", "name", "kind", "trade_ids")}
        row["n_excluded"] = len(p["trade_ids"]) - len(ids)
        if not ids:
            row.update(total=None, open=None, **{c: None for c in COMPONENTS})
        else:
            row["total"] = float(sum(totals[t] for t in ids))
            for c in COMPONENTS:
                row[c] = None if ltd and c != "realised" else float(sum(parts[t][c] for t in ids))
            row["open"] = row["total"] - row["realised"]
        rows.append(row)
    rows.sort(key=lambda r: (r["total"] is None, -(r["total"] or 0.0), r["position_id"]))
    return rows
