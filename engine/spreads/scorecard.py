"""The trader's scorecard: how Jason's trade IDEAS have done (user, 2026-09-29).

Jason is a paper trader and his book is judged idea by idea. An **idea** is a position of the
Book, one row id of ``period_explain.positions_of`` (the one definition every screen uses): a
strategy, a spread position (calendar or template), each outright contract
(``OUTRIGHT-<instrument_id>``), and every other trade on its own (``TRADE-<trade_id>``: an option,
an LME ticket, an FX trade).

Nothing is priced here and no P&L formula is new: every figure is a sum of the members'
LTD USD as ``engine.pnl.series.daily_series`` holds it (the header's own filled valuation of each
business day), read on the right day.

- **Closed or open.** On each business day the idea's members dealt by then are netted per
  contract (instrument and value / prompt / expiry date) over the rows that are still live
  (status not SETTLED or CLOSED): an idea is flat on a day when every such net is zero (the
  trades bought and sold back, the options closed out, the legs settled). It is **closed** when it
  is flat on the as-of date, and its **close date** is the first day of the run of flat days that
  reaches the as-of date (a flat idea that was put on again is open until it goes flat again).
- **P&L.** Every idea's P&L is its LTD on the as-of date (user yes 2026-09-29, the reviewer's
  second pass), so closed + open = the book LTD by construction, and a flat but unsettled
  non-USD future keeps re-converting at each day's spot like every other figure. A closed idea
  also carries, as context, its LTD on its close date (``pnl_at_close``, ``pnl_at_close_date``)
  and the difference (``fx_since_close``: the move since it went flat, the conversion spot's on a
  non-USD future; 0.0 when none).
- **Path.** ``best_ltd`` / ``worst_ltd``: the highest and lowest LTD the idea showed from its
  first trade date to its close date (open: the as-of date), on the days all its dealt members
  were priced; ``days_unpriced`` counts the days left out.
- **Holding days.** Business days of the series from the first trade date to the close date
  (open: the as-of date); 0 for a round trip inside one day. A weekend or holiday as-of (the
  series appends it as its last day) is not counted as a business day.
- **Unpriced.** An idea whose P&L day has a member with no price is left out of every statistic
  with its reason (``included`` False, ``reason``), never counted as zero.

The summary over CLOSED ideas: count, wins, losses, scratches (within half a cent of zero),
win rate (wins / count), average win, average loss, payoff ratio (average win / |average
loss|), expectancy (the mean P&L per idea), total, best and worst idea, average holding days of
the winners and of the losers. Over OPEN ideas: count, the unrealised total, how many are in
profit. The same summary by spread type (the position's ``trade_type``: CROSS_EXCHANGE,
CROSS_PRODUCT, TERM_STRUCTURE, '' none) and by trade name (the PBRoot name,
``trades.strategy``, '' none).
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from engine.pnl.calendar import _is_business_day
from engine.pnl.series import DailySeries, daily_series
from engine.spreads.period_explain import _series_reader, positions_of

DONE = ("SETTLED", "CLOSED")           # a row no longer live: settled, or an option group closed out
SCRATCH_USD = 0.005                    # within half a cent of zero: neither a win nor a loss
_FLAT = 1e-9


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _mean(values: Sequence[float]) -> Optional[float]:
    return float(sum(values)) / len(values) if values else None


def _labels(conn: sqlite3.Connection, as_of: str) -> Dict[str, dict]:
    """{trade_id: {trade_date, strategy, trade_type}} (the columns an old database lacks read as '')."""
    from engine.spreads.book import _read_trades
    return {t["trade_id"]: {"trade_date": str(t["trade_date"])[:10], "strategy": t["strategy"],
                            "trade_type": t["trade_type"]} for t in _read_trades(conn, as_of)}


def _one(values) -> str:
    vals = sorted({str(v or "") for v in values})
    return vals[0] if len(vals) == 1 else ""


def _idea_labels(spreads: dict, ideas: List[dict], labels: Dict[str, dict]) -> Dict[str, Tuple[str, str]]:
    """{position_id: (spread type, trade name)}: a Book position's own ``trade_type`` and
    ``strategy``; an outright contract or a lone trade its trades' own labels when they agree."""
    by_pid = {str(p.get("position_id")): p for p in spreads.get("positions") or []}
    out = {}
    for idea in ideas:
        p = by_pid.get(idea["position_id"])
        if p is not None:
            name = str(p.get("strategy") or "") or (str(p.get("name") or "") if p.get("kind") == "strategy" else "")
            out[idea["position_id"]] = (str(p.get("trade_type") or ""), name)
        else:
            rows = [labels.get(t, {}) for t in idea["trade_ids"]]
            out[idea["position_id"]] = (_one(r.get("trade_type") for r in rows), _one(r.get("strategy") for r in rows))
    return out


def _day_state(frame_rows: Dict[str, dict], tids: Sequence[str]) -> Tuple[bool, Optional[float], str, int]:
    """(flat, LTD or None, why None, n members dealt) of an idea on one day's rows."""
    net: Dict[Tuple[str, str], float] = defaultdict(float)
    scale: Dict[Tuple[str, str], float] = defaultdict(float)
    ltd, whys, dealt = 0.0, [], 0
    for tid in tids:
        r = frame_rows.get(tid)
        if r is None:
            continue                     # not dealt yet on this day
        dealt += 1
        value = _num(r.get("ltd_usd"))
        if value is None:
            whys.append(f"{tid}: {str(r.get('reason') or '') or 'no USD P&L on its row'}")
        else:
            ltd += value
        if str(r.get("status") or "") not in DONE:
            key = (str(r.get("instrument_id") or ""), str(r.get("settle_date") or ""))
            q = _num(r.get("quantity")) or 0.0
            net[key] += q
            scale[key] = max(scale[key], abs(q))
    flat = dealt > 0 and all(abs(v) <= _FLAT * max(1.0, scale[k]) for k, v in net.items())
    return flat, (None if whys else ltd), "; ".join(whys), dealt


def _idea(idea: dict, days: Sequence[str], rows_by_day: Dict[str, Dict[str, dict]], labels: Dict[str, dict],
          type_name: Tuple[str, str], as_of: str, holidays: frozenset = frozenset()) -> dict:
    tids = idea["trade_ids"]
    first = min((labels.get(t, {}).get("trade_date") or as_of for t in tids), default=as_of)
    start = bisect.bisect_left(days, first)
    states = [(d, *_day_state(rows_by_day[d], tids)) for d in days[start:]]
    # the run of flat days that reaches the as-of date
    close_date = ""
    if states and states[-1][1]:
        n = len(states) - 1
        while n > 0 and states[n - 1][1]:
            n -= 1
        close_date = states[n][0]
    end = close_date or as_of
    path = [(d, ltd) for d, _flat, ltd, _why, dealt in states if d <= end and dealt and ltd is not None]
    unpriced = sum(1 for d, _flat, ltd, _why, dealt in states if d <= end and dealt and ltd is None)
    now_state = states[-1] if states and states[-1][0] == as_of else None
    pnl = now_state[2] if now_state is not None else None
    why = now_state[3] if now_state is not None else f"not valued on {as_of}"
    close_state = next((s for s in states if s[0] == close_date), None) if close_date else None
    at_close = close_state[2] if close_state is not None else None
    since = (pnl - at_close) if pnl is not None and at_close is not None else None
    if since is not None and abs(since) < 0.005:
        since = 0.0
    end_idx = bisect.bisect_right(days, end) - 1
    holding = sum(1 for d in days[start + 1:end_idx + 1] if _is_business_day(dt.date.fromisoformat(d), holidays))
    best = max(path, key=lambda x: x[1]) if path else (None, None)
    worst = min(path, key=lambda x: x[1]) if path else (None, None)
    return {
        "position_id": idea["position_id"], "name": idea["name"], "kind": idea["kind"], "trade_ids": list(tids),
        "spread_type": type_name[0], "trade_name": type_name[1],
        "status": "closed" if close_date else "open", "first_trade_date": first, "close_date": close_date,
        "holding_days": holding,
        "pnl_usd": pnl, "pnl_date": as_of, "included": pnl is not None,
        "pnl_at_close": at_close, "pnl_at_close_date": close_date, "fx_since_close": since,
        "reason": "" if pnl is not None else (why or "not valued"),
        "best_ltd": best[1], "best_date": best[0] or "", "worst_ltd": worst[1], "worst_date": worst[0] or "",
        "days_unpriced": unpriced,
    }


def _summary(ideas: Sequence[dict]) -> dict:
    """The closed and open statistics of ``ideas`` (included ones only; the others counted)."""
    closed = [i for i in ideas if i["status"] == "closed" and i["included"]]
    opened = [i for i in ideas if i["status"] == "open" and i["included"]]
    wins = [i for i in closed if i["pnl_usd"] > SCRATCH_USD]
    losses = [i for i in closed if i["pnl_usd"] < -SCRATCH_USD]
    avg_win = _mean([i["pnl_usd"] for i in wins])
    avg_loss = _mean([i["pnl_usd"] for i in losses])
    pick = (lambda i: None if i is None else {"position_id": i["position_id"], "name": i["name"],
                                              "pnl_usd": i["pnl_usd"]})
    return {
        "closed": {
            "count": len(closed), "wins": len(wins), "losses": len(losses),
            "scratches": len(closed) - len(wins) - len(losses),
            "win_rate": len(wins) / len(closed) if closed else None,
            "avg_win": avg_win, "avg_loss": avg_loss,
            "payoff_ratio": avg_win / abs(avg_loss) if avg_win is not None and avg_loss else None,
            "expectancy": _mean([i["pnl_usd"] for i in closed]),
            "total": float(sum(i["pnl_usd"] for i in closed)),
            "best": pick(max(closed, key=lambda i: i["pnl_usd"]) if closed else None),
            "worst": pick(min(closed, key=lambda i: i["pnl_usd"]) if closed else None),
            "avg_hold_days_win": _mean([i["holding_days"] for i in wins]),
            "avg_hold_days_loss": _mean([i["holding_days"] for i in losses]),
            "excluded": [(i["position_id"], i["reason"]) for i in ideas
                         if i["status"] == "closed" and not i["included"]],
        },
        "open": {
            "count": len(opened), "unrealised_usd": float(sum(i["pnl_usd"] for i in opened)),
            "in_profit": sum(1 for i in opened if i["pnl_usd"] > SCRATCH_USD),
            "excluded": [(i["position_id"], i["reason"]) for i in ideas
                         if i["status"] == "open" and not i["included"]],
        },
    }


def scorecard(conn: sqlite3.Connection, as_of: str, series: Optional[DailySeries] = None,
              spreads: Optional[dict] = None) -> dict:
    """The trader's scorecard on ``as_of`` (module docstring for the rules).

    ``series``: ``daily_series(conn, as_of)`` when None. ``spreads``: ``book_spreads(conn, as_of)``
    for the positions, built off the series' frames when None (as ``period_explain`` does).

    Returns ``{as_of, ideas, summary, by_spread_type, by_trade_name, excluded, reason}``:

    - ``ideas``: one per Book row id, ``{position_id, name, kind, trade_ids, spread_type
      ('CROSS_EXCHANGE' | 'CROSS_PRODUCT' | 'TERM_STRUCTURE' | ''), trade_name (PBRoot name or
      ''), status ('open' | 'closed'), first_trade_date, close_date ('' when open),
      holding_days, pnl_usd (LTD on as-of, closed or open; None when a member is unpriced
      that day), pnl_date (= as_of), included, reason, pnl_at_close / pnl_at_close_date (a
      closed idea's LTD on its close date, context; None / '' when open), fx_since_close
      (pnl_usd - pnl_at_close, 0.0 when under half a cent; None when open or either is
      unpriced), best_ltd, best_date, worst_ltd, worst_date, days_unpriced}``, closed first,
      each by P&L largest first.
    - ``summary``: ``{closed: {count, wins, losses, scratches, win_rate, avg_win, avg_loss,
      payoff_ratio, expectancy, total, best, worst ({position_id, name, pnl_usd} or None),
      avg_hold_days_win, avg_hold_days_loss, excluded [(position_id, why)]}, open: {count,
      unrealised_usd, in_profit, excluded}}``; a statistic with nothing to average is None.
    - ``by_spread_type`` / ``by_trade_name``: ``{key: summary}``, the same shape per group
      ('' = no type / no name).
    - ``excluded``: every idea left out, ``[(position_id, why)]``.
    - ``reason``: '' or why there is no scorecard (no trades, as-of not a business day).
    """
    if series is None:
        series = daily_series(conn, as_of)
    out = {"as_of": as_of, "ideas": [], "summary": _summary([]), "by_spread_type": {}, "by_trade_name": {},
           "excluded": [], "reason": ""}
    days = [d for d in series.days if d <= as_of]
    if not days:
        out["reason"] = "no trades on file on or before the as-of date"
        return out
    if days[-1] != as_of:
        out["reason"] = f"{as_of} is not a business day of the daily series (last {days[-1]})"
        return out
    rows_by_day = {d: {str(r["trade_id"]): r for r in series.frame(d).to_dict("records")} for d in days}
    if spreads is None:
        from engine.spreads.book import book_spreads
        spreads = book_spreads(conn, as_of, value_fn=_series_reader(series))
    ideas = positions_of(spreads, rows_by_day[as_of])
    labels = _labels(conn, as_of)
    type_names = _idea_labels(spreads, ideas, labels)
    rows = [_idea(i, days, rows_by_day, labels, type_names[i["position_id"]], as_of, series.holidays)
            for i in ideas]
    rows.sort(key=lambda r: (r["status"] != "closed", r["pnl_usd"] is None, -(r["pnl_usd"] or 0.0), r["position_id"]))
    out["ideas"] = rows
    out["summary"] = _summary(rows)
    for key, field in (("by_spread_type", "spread_type"), ("by_trade_name", "trade_name")):
        groups: Dict[str, List[dict]] = defaultdict(list)
        for r in rows:
            groups[r[field]].append(r)
        out[key] = {k: _summary(v) for k, v in sorted(groups.items())}
    out["excluded"] = [(r["position_id"], r["reason"]) for r in rows if not r["included"]]
    return out
