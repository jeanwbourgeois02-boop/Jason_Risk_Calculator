"""Risk by position (the Risk tab's blocks 2 and 3, user-approved 2026-09-29): how much of the
book's one-day VaR each position carries, and how the positions move together.

The positions are the Book's, never regrouped here: the spreads engine's own position keys,
assigned by `engine.spreads.period_explain.positions_of` (reused, not copied, so every screen and
this module key a position the same way), over `book_spreads(conn, as_of)`:
  * each position `book_spreads` finds (`positions`: a strategy, a bundle or pin, a calendar or
    template spread): its `position_id`, 'POSITION-...';
  * each contract of the futures the spread rule left outright: 'OUTRIGHT-<instrument_id>';
  * every other trade on its own (an option on a future, an LME ticket, an FX trade):
    'TRADE-<trade_id>'.
A trade is in one position only. Only open trades carry risk: a position keeps its open trades
(`trade_ids`), and a position with none is not listed. `book_row_id` = `position_id`. Open = in
a curve-positions row (an open future, option on one or LME prompt), or an FX trade whose last
leg settles after as_of. Inside a position the trades are netted per contract; a contract the
position holds flat adds nothing.

A position's daily USD P&L is the same series the book VaR already uses, held constant across
history (`commodity.py`: the research app's settlement changes x our multiplier x lots x USD
per quote unit, `engine.risk.commodity_history`): each of its contracts at its lots in the
position times curve-positions' delta per lot (`delta_factor`: 1 for a future or an LME
prompt, the shrinking share of an averaging contract, an option's official DELTA mark), summed
day by day. No delta is recomputed, no mark is read here: the delta per lot is curve-positions'
row of the contract. A position with any leg that has no history or no delta is left out whole
(never a partial series) and listed in `excluded` with its reason, with one exception (user yes,
2026-09-29): a **currency-hedge leg** (spreads-engine's `engine.spreads.hedges.is_hedge`, called,
never copied: the SGX USD/CNH future, an FX spot / forward / swap / option on a currency pair)
with no series is left out alone, and the position is kept on its other legs, with `partial`
True, `partial_reason` ("<hedge> hedge not in this figure: no price history; the figure is
without the hedge") and `hedges_left_out`; the book-level `partial_note` names every such
position. A position made only of such hedges is left out. An FX trade has no series here:
`book_positions` gives the currency delta per currency, not per trade, and the nm-dashboard FX
history is absent on a PC without it; a precious-metal pair (not a hedge) therefore leaves its
position out.

Definitions (parameters in `config/risk.yaml`):

  standalone_var   the book VaR definition on the position alone: minus the 5th percentile
                   (linear interpolation) of its last `var_window_bd` (252) daily P&Ls; NaN with
                   fewer, with the reason.
  book_var         the same definition on the included positions' series summed day by day (a
                   day one position has no settlement counts it as 0, the book series' rule).
                   It is NOT the Risk tab's headline VaR (`book.var95_1d_usd`), which is over
                   every underlyer, currencies and metals included: both are returned
                   (`book_var` and `headline_var`), with a sentence saying which is which.
  contribution_var historical component VaR. Over the book's last 252 days, the days whose rank
                   is nearest the 5th-percentile quantile are the book's tail scenarios: with 252
                   days and 5 tail days (`var_contribution_days`) they are the 12th to 16th worst
                   days. Each position's P&L is averaged over those days and its sign flipped (a
                   loss is positive), then every position is scaled by the same factor so that the
                   contributions add up to `book_var` exactly. A position whose P&L helps on the
                   bad days has a negative contribution (it hedges the book).
  contribution_share contribution_var / book_var (a fraction; the shares add up to 1).
  diversification  sum_standalone (the included positions' standalone VaRs added, the ones with
                   a figure), book_var, saved = sum_standalone - book_var.
  correlation      Pearson correlation of the included positions' daily P&L over their last 252
                   common days (every position with at least 252 days of its own; fewer common
                   days: all of them, down to `correlation_min_days`); None with a reason when
                   fewer than 3 positions qualify.

Nothing here reads `marks`, writes anything, or asks Bloomberg (hard rules 2, 3, 8): the history
is a risk input only.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from data.contracts import load_roots
from engine.risk.commodity import _PerLot, _sum_series, contract_series
from engine.spreads.hedges import is_hedge
from engine.spreads.period_explain import positions_of

NAN = float("nan")
_LME = "LME_FWD"
_OPTION = "CMDTY_OPTION"
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

METHOD = ("Historical component VaR: the book's tail days are the {k} of its last {n} days whose rank is "
          "nearest the {pct:g}th-percentile quantile ({ranks} worst days); each position's P&L averaged over "
          "them, sign flipped, then all scaled by one factor so the contributions add up to the book's VaR "
          "exactly. A negative contribution hedges the book.")


def _num(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NAN
    return v if math.isfinite(v) else NAN


def _isnan(v: float) -> bool:
    return v != v


def _iso(ts: Any) -> Optional[str]:
    return None if ts is None else pd.Timestamp(ts).strftime("%Y-%m-%d")


def var_of(s: pd.Series, config: Dict[str, Any]) -> Tuple[float, str]:
    """(the book VaR definition on one series, why NaN): minus the (1 - var_confidence) quantile,
    linear, of its last var_window_bd observations (metrics.py's `var95_1d_usd`, the same line)."""
    n = int(config["var_window_bd"])
    s = s.dropna()
    if len(s) < n:
        return NAN, f"needs {n} daily observations: {len(s)} on file"
    return -float(s.iloc[-n:].quantile(1.0 - float(config["var_confidence"]), interpolation="linear")), ""


# --------------------------------------------------------------------------- the positions
def _contract_name(row: dict) -> str:
    """'WTI Dec26', 'LME copper 2026-12-16', an option's id: plain enough for a label; the
    screen has its own naming kit and matches on `position_id`."""
    name = str(row.get("name") or row.get("root_id") or row.get("contract_id") or "")
    product = row.get("product")
    if product == _LME:
        return f"{name} {row.get('expiry') or ''}".strip()
    if product == _OPTION:
        return str(row.get("instrument_id") or row.get("contract_id") or name).replace(" Comdty", "")
    month, year = row.get("month"), row.get("year")
    if month and year:
        return f"{name} {_MONTHS[int(month) - 1]}{int(year) % 100:02d}"
    return str(row.get("contract_id") or name)


def _rows_by_contract(curve: dict) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """({contract_id: curve-positions row}, {trade_id: its row})."""
    by_contract: Dict[str, dict] = {}
    by_trade: Dict[str, dict] = {}
    for r in (curve or {}).get("rows") or []:
        by_contract[str(r.get("contract_id"))] = r
        for t in r.get("trade_ids") or []:
            by_trade[str(t)] = r
    return by_contract, by_trade


def _trade_facts(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{trade_id: {quantity, product, instrument_id, base_ccy, quote_ccy, strategy, value_date}}
    from the trades on file (the fills, never a mark)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    strat = "t.strategy" if "strategy" in cols else "''"
    sql = (f"SELECT t.trade_id, t.quantity, t.product, t.instrument_id, {strat}, MAX(l.settle_date), "
           f"i.base_ccy, i.quote_ccy "
           f"FROM trades_official t LEFT JOIN trade_legs l ON l.trade_id = t.trade_id "
           f"LEFT JOIN instruments i ON i.instrument_id = t.instrument_id GROUP BY t.trade_id")
    return {str(r[0]): {"quantity": r[1], "product": r[2], "instrument_id": r[3], "strategy": str(r[4] or ""),
                        "value_date": str(r[5] or ""), "base_ccy": str(r[6] or ""), "quote_ccy": str(r[7] or "")}
            for r in conn.execute(sql)}


def _is_hedge(f: dict, roots: Dict[str, Any]) -> bool:
    """spreads-engine's currency-hedge test (`engine.spreads.hedges.is_hedge`), called, never
    copied: an fx-sector future (the SGX USD/CNH future), an FX spot / forward / swap / option on a
    currency pair; a precious-metal pair is not a hedge."""
    return bool(is_hedge(roots.get(f.get("base_ccy") or ""), str(f.get("product") or ""), f.get("base_ccy") or "",
                         f.get("quote_ccy") or "", f.get("instrument_id") or ""))


class _Position:
    def __init__(self, position_id: str, kind: str, name: str, trade_ids: Sequence[str]):
        self.id = position_id
        self.kind = kind
        self.name = name
        self.trade_ids = sorted({str(t) for t in trade_ids})
        # (contract_id, product, quantity as booked, a currency hedge?)
        self.exposures: List[Tuple[str, str, float, bool]] = []
        self.reasons: List[str] = []           # a leg with no series: the whole position is left out
        self.hedges_out: List[Tuple[str, str]] = []   # (hedge name, why): a hedge leg with no series, left out alone


def _contract_of(tid: str, f: dict, by_trade: Dict[str, dict]) -> str:
    """The contract a trade is a position in: its curve-positions row's id, else (a contract that
    nets to zero across the book has no row) the id built the same way (an LME ticket
    '<instrument> <prompt>', anything else its instrument)."""
    row = by_trade.get(tid)
    if row is not None:
        return str(row.get("contract_id"))
    inst = str(f.get("instrument_id") or tid)
    return f"{inst} {f.get('value_date')}" if f.get("product") == _LME else inst


def open_positions(conn: sqlite3.Connection, as_of: str, spreads: dict, curve: dict, fx_why: str
                   ) -> List[_Position]:
    """The Book's positions (module docstring) with their open trades, each trade's contract and
    booked quantity, or the reason it has no series."""
    _by_contract, by_trade = _rows_by_contract(curve)
    facts = _trade_facts(conn)
    open_ids = set(by_trade) | {t for t, f in facts.items()
                                if f.get("product") in FX_PRODUCTS and f.get("value_date", "") > as_of}
    trade_rows = {t: {"instrument_id": (facts.get(t) or {}).get("instrument_id"),
                      "product": (facts.get(t) or {}).get("product")} for t in open_ids}
    roots = load_roots()
    out: List[_Position] = []
    for p in positions_of(spreads, trade_rows):
        tids = [str(t) for t in p.get("trade_ids") or [] if str(t) in open_ids]
        if not tids:
            continue
        pid = str(p["position_id"])
        name = str(p.get("name") or pid)
        if pid.startswith("OUTRIGHT-") or pid.startswith("TRADE-"):
            row = by_trade.get(tids[0])
            f = facts.get(tids[0]) or {}
            name = (_contract_name(row) if row else
                    f"{f.get('instrument_id') or tids[0]} {f.get('value_date') or ''}".strip())
        pos = _Position(pid, str(p.get("kind") or ""), name, tids)
        for tid in tids:
            f = facts.get(tid) or {}
            hedge = _is_hedge(f, roots)
            if f.get("product") in FX_PRODUCTS:
                why = f"{tid} ({f.get('instrument_id')}): {fx_why}"
                if hedge:
                    pos.hedges_out.append((_fx_name(f), why))
                else:
                    pos.reasons.append(why)
                continue
            q = _num(f.get("quantity"))
            if _isnan(q):
                pos.reasons.append(f"{tid}: trades.quantity is not a number")
                continue
            pos.exposures.append((_contract_of(tid, f, by_trade), str(f.get("product") or ""), q, hedge))
        out.append(pos)
    return out


def _fx_name(f: dict) -> str:
    """'USDCNH 2026-11-18 forward' for an FX hedge trade."""
    inst = str(f.get("instrument_id") or "")
    pair = inst[:6] if len(inst) >= 6 else inst
    words = {"FX_SPOT": "spot", "FX_FWD": "forward", "FX_SWAP": "swap", "FX_OPTION": "option"}
    return f"{pair} {f.get('value_date') or ''} {words.get(str(f.get('product')), '')}".replace("  ", " ").strip()


def _position_series(pos: _Position, by_contract: Dict[str, dict], per_lot: _PerLot, as_of: str,
                     parts_out: Optional[Dict[str, pd.Series]] = None) -> Tuple[List[dict], Optional[pd.Series]]:
    """(its legs' detail, its daily USD P&L) or None with `pos.reasons` filled: the trades netted
    per contract, each contract at its lots x curve-positions' delta per lot, on its contract's
    held-constant history. A contract the position holds flat is skipped. A currency-hedge leg
    with no series goes to `pos.hedges_out` (left out alone, user yes 2026-09-29); any other leg
    with no series leaves the whole position out. `parts_out`, when given, receives each leg's
    own series ({contract_id: daily USD P&L at its delta lots}), the parts the sum is made of
    (the trade risk's hedge % and best-fit ratio read them, `trades.py`)."""
    booked: Dict[str, Tuple[str, float, bool]] = {}
    for cid, product, q, hedge in pos.exposures:
        booked[cid] = (product, booked.get(cid, (product, 0.0, hedge))[1] + q, hedge)
    legs: List[dict] = []
    parts: List[pd.Series] = []
    for cid, (product, q, hedge) in booked.items():
        if abs(q) < 1e-9:
            continue                                          # bought and sold back inside the position
        row = by_contract.get(cid)
        leg = {"contract_id": cid, "lots": NAN, "delta_lots": NAN, "history_contract": None, "days": 0,
               "in_series": False, "hedge": hedge, "reason": ""}
        legs.append(leg)
        if row is None:
            leg["reason"] = (f"{cid}: no open row in curve-positions (the contract nets to zero across the book), so "
                             f"no delta per lot")
        elif _isnan(_num(row.get("delta_factor"))):
            leg["reason"] = f"{cid}: no delta per lot ({row.get('reason') or 'not given'})"
        elif product == _LME:                                 # tonnes as lots, through the row's own lots per tonne
            rl, ru = _num(row.get("lots")), _num(row.get("units"))
            if _isnan(rl) or _isnan(ru) or ru == 0.0:
                leg["reason"] = f"{cid}: its LME row has no tonnes per lot"
            else:
                leg["lots"] = q * rl / ru
        else:
            leg["lots"] = q
        if not leg["reason"]:
            detail, s = contract_series({**row, "delta_lots": leg["lots"] * _num(row["delta_factor"])}, per_lot, as_of)
            leg.update(delta_lots=detail["delta_lots"], history_contract=detail["history_contract"],
                       days=detail["days"], in_series=s is not None, note=detail.get("note", ""),
                       reason="" if s is not None else f"{cid}: {detail['reason'] or 'no history'}")
            if s is not None:
                parts.append(s)
                if parts_out is not None:
                    parts_out[cid] = s
                continue
        if hedge:
            pos.hedges_out.append((_contract_name(row) if row else cid, leg["reason"]))
        else:
            pos.reasons.append(leg["reason"])
    if pos.reasons:
        return legs, None
    if not parts:
        pos.reasons.append("; ".join(why for _n, why in pos.hedges_out) or "no open lots")
        return legs, None
    return legs, _sum_series(parts)


def partial_reason(hedges_out: Sequence[Tuple[str, str]]) -> str:
    """The sentence of a position shown without its currency hedge (user yes 2026-09-29)."""
    if not hedges_out:
        return ""
    names = ", ".join(dict.fromkeys(n for n, _why in hedges_out))
    return f"{names} hedge not in this figure: no price history; the figure is without the hedge"


# --------------------------------------------------------------------------- the figures
def _tail_ranks(n: int, confidence: float, k: int) -> List[int]:
    """The k ranks (0 = the worst day) nearest the quantile's position (1 - confidence) x (n - 1),
    ties to the worse day."""
    h = (1.0 - confidence) * (n - 1)
    return sorted(sorted(range(n), key=lambda r: (abs(r - h), r))[:k])


def _correlation(included: List[dict], series: Dict[str, pd.Series], config: Dict[str, Any]
                 ) -> Tuple[Optional[dict], str]:
    n = int(config["var_window_bd"])
    floor = int(config["correlation_min_days"])
    full = [p for p in included if len(series[p["position_id"]]) >= n]
    left = [{"position_id": p["position_id"], "name": p["name"],
             "reason": f"{len(series[p['position_id']])} daily observations, fewer than {n}"}
            for p in included if len(series[p["position_id"]]) < n]
    if len(full) < 3:
        return None, (f"needs at least 3 positions with {n} daily observations each: {len(full)} "
                      f"({len(included)} with history)")
    frame = pd.concat([series[p["position_id"]] for p in full], axis=1, join="inner")
    frame.columns = [p["position_id"] for p in full]
    if len(frame) < floor:
        return None, f"the {len(full)} positions share {len(frame)} common days, fewer than {floor}"
    frame = frame.iloc[-n:]
    corr = frame.corr(method="pearson")
    flat = [p["name"] for p in full if float(frame[p["position_id"]].std()) == 0.0]
    matrix = [[_num(corr.iloc[i, j]) for j in range(len(full))] for i in range(len(full))]
    note = (f"no movement over the window, so no correlation (NaN): {', '.join(flat)}" if flat else "")
    return {"ids": [p["position_id"] for p in full], "names": [p["name"] for p in full], "matrix": matrix,
            "days": int(len(frame)), "first_date": _iso(frame.index[0]), "last_date": _iso(frame.index[-1]),
            "left_out": left, "note": note}, ""


def position_risk(conn: sqlite3.Connection, as_of: str, *, spreads: Optional[dict], curve: Optional[dict],
                  per_lot: _PerLot, config: Dict[str, Any], headline_var: float = NAN,
                  fx_history_reason: str = "", detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Risk by position for `as_of` (module docstring). `spreads` / `curve`: `book_spreads` and
    `curve_positions` of the same as-of (book_risk's, not computed twice); `per_lot`: the
    commodity rows' per-lot series cache; `headline_var`: `book.var95_1d_usd`, returned beside
    the position book's VaR; `fx_history_reason`: why the FX history is absent, '' when on file.
    `detail`, when given, receives the series behind the figures (never in the returned dict,
    which stays JSON-friendly): `series` {position_id: daily USD P&L}, `leg_series`
    {position_id: {contract_id: its leg's daily USD P&L}}, `by_contract` {contract_id:
    curve-positions row} (the trade risk reads them, `trades.py`).

    Returns {
      "available": bool, "reason": '' or why nothing could be computed,
      "method": the tail-window rule in one sentence,
      "positions": [{position_id ('POSITION-...' | 'OUTRIGHT-<instrument_id>' | 'TRADE-<trade_id>'),
                     book_row_id (= position_id), kind (the position's: 'strategy', 'bundle',
                     'pinned', 'calendar', a template id, 'outright', or a trade's product in lower
                     case), name, trade_ids (its open trades),
                     legs [{contract_id, lots, delta_lots, history_contract, days, in_series, hedge,
                     reason}], included (bool), reason ('' when included), partial (bool: counted
                     without a currency hedge that has no history), partial_reason ('' unless
                     partial), hedges_left_out [{name, reason}], days, first_date, last_date,
                     standalone_var, standalone_reason, contribution_var, contribution_share,
                     contribution_reason ('' or why NaN), tail_pnl (its P&L on each tail day, in
                     window.tail_dates order)}],
                    the included ones by contribution_var, largest first, then the excluded,
      "book_var": the VaR of the included positions' summed series (NaN with book_var_reason),
      "book_var_reason", "headline_var" (book.var95_1d_usd, over every underlyer),
      "book_var_note": which is which, and by how much they differ,
      "window": {days, first_date, last_date, quantile_pnl (= -book_var), tail_dates, tail_book_pnl,
                 tail_ranks (1 = the worst day)},
      "diversification": {sum_standalone, book_var, saved (= sum_standalone - book_var),
                          standalone_missing (included positions with no standalone figure), reason},
      "excluded": [{position_id, name, reason}], "excluded_count", "included_count",
      "partial_count", "partial_note" ('' or the sentence naming the positions counted without
                     their currency hedge),
      "correlation": {ids, names, matrix [[...]], days, first_date, last_date, left_out
                      [{position_id, name, reason}], note} or None,
      "correlation_reason": '' or why it is None }
    Every figure a number or NaN with its reason, never zero for something missing."""
    n = int(config["var_window_bd"])
    conf = float(config["var_confidence"])
    k = min(int(config.get("var_contribution_days", 5)), n)
    ranks = _tail_ranks(n, conf, k)
    method = METHOD.format(k=k, n=n, pct=round((1.0 - conf) * 100, 6),
                           ranks=f"the {ranks[0] + 1}{_th(ranks[0] + 1)} to {ranks[-1] + 1}{_th(ranks[-1] + 1)}"
                           if len(ranks) > 1 else f"the {ranks[0] + 1}{_th(ranks[0] + 1)}")
    out: Dict[str, Any] = {
        "available": False, "reason": "", "method": method, "positions": [], "book_var": NAN, "book_var_reason": "",
        "headline_var": _num(headline_var), "book_var_note": "",
        "window": {"days": 0, "first_date": None, "last_date": None, "quantile_pnl": NAN, "tail_dates": [],
                   "tail_book_pnl": [], "tail_ranks": [r + 1 for r in ranks]},
        "diversification": {"sum_standalone": NAN, "book_var": NAN, "saved": NAN, "standalone_missing": 0,
                            "reason": ""},
        "excluded": [], "excluded_count": 0, "included_count": 0, "partial_count": 0, "partial_note": "",
        "correlation": None, "correlation_reason": "",
    }
    if spreads is None or curve is None:
        out["reason"] = out["correlation_reason"] = "the book's positions could not be read (see the missing list)"
        return out
    history = per_lot.history
    fx_why = ("an FX trade: " + (f"no FX market history ({fx_history_reason})" if fx_history_reason else
                                 "book_positions gives the currency delta per currency, not per trade, so its "
                                 "risk is in the currency rows of the headline VaR"))
    by_contract, _by_trade = _rows_by_contract(curve)
    positions = open_positions(conn, as_of, spreads, curve, fx_why)
    series: Dict[str, pd.Series] = {}
    leg_series: Dict[str, Dict[str, pd.Series]] = {}
    if detail is not None:
        detail.update(series=series, leg_series=leg_series, by_contract=by_contract)
    rows: List[dict] = []
    for pos in positions:
        parts: Dict[str, pd.Series] = {}
        legs, s = _position_series(pos, by_contract, per_lot, as_of, parts)
        if s is not None and pos.id not in leg_series:
            leg_series[pos.id] = parts
        partial = s is not None and bool(pos.hedges_out)
        row = {"position_id": pos.id, "book_row_id": pos.id, "kind": pos.kind, "name": pos.name,
               "trade_ids": pos.trade_ids, "legs": legs, "included": s is not None,
               "reason": "; ".join(dict.fromkeys(pos.reasons)), "partial": partial,
               "partial_reason": partial_reason(pos.hedges_out) if partial else "",
               "hedges_left_out": [{"name": n, "reason": why} for n, why in pos.hedges_out] if partial else [],
               "days": 0, "first_date": None, "last_date": None,
               "standalone_var": NAN, "standalone_reason": "", "contribution_var": NAN, "contribution_share": NAN,
               "tail_pnl": []}
        if s is not None:
            if pos.id in series:              # never two rows under one id: the second is named, not summed
                row.update(included=False, reason=f"a second row with the id {pos.id}")
            else:
                series[pos.id] = s
                row.update(days=int(len(s)), first_date=_iso(s.index[0]), last_date=_iso(s.index[-1]))
                row["standalone_var"], row["standalone_reason"] = var_of(s, config)
        if not row["included"] and not row["reason"]:
            row["reason"] = getattr(history, "reason", "") or "no history"
        rows.append(row)
    included = [r for r in rows if r["included"]]
    excluded = [r for r in rows if not r["included"]]
    out.update(excluded=[{"position_id": r["position_id"], "name": r["name"], "reason": r["reason"]} for r in excluded],
               excluded_count=len(excluded), included_count=len(included))
    partials = [r for r in included if r["partial"]]
    out["partial_count"] = len(partials)
    out["partial_note"] = (f"{len(partials)} position(s) shown without their currency hedge (no price history for "
                           f"the hedge; each figure is without it): {', '.join(r['name'] for r in partials)}"
                           if partials else "")
    if not rows:
        out["reason"] = out["correlation_reason"] = f"no open positions on {as_of}"
        return out
    if not included:
        out["reason"] = "no position has a full history series: " + (
            getattr(history, "reason", "") or "; ".join(dict.fromkeys(r["reason"] for r in excluded)))
        out["book_var_reason"] = out["correlation_reason"] = out["reason"]
        out["positions"] = rows
        return out
    out["available"] = True

    # the book of the included positions, and its tail
    frame = pd.concat([series[r["position_id"]] for r in included], axis=1)
    frame.columns = [r["position_id"] for r in included]
    book = frame.sum(axis=1, min_count=1).dropna()
    if len(book) < n:
        out["book_var_reason"] = f"the included positions' book has {len(book)} daily observations, fewer than {n}"
        for r in included:
            r["contribution_reason"] = out["book_var_reason"]
    else:
        window = frame.reindex(book.index[-n:]).fillna(0.0)
        pnl = window.sum(axis=1)
        q = float(pnl.quantile(1.0 - conf, interpolation="linear"))
        book_var = -q
        order = pnl.reset_index(drop=True).sort_values(kind="mergesort").index.to_list()
        tail_idx = [order[r] for r in ranks]
        tail = window.iloc[tail_idx]
        raw = -tail.mean(axis=0)
        total = float(raw.sum())
        out["book_var"] = book_var
        out["window"].update(days=n, first_date=_iso(pnl.index[0]), last_date=_iso(pnl.index[-1]), quantile_pnl=q,
                             tail_dates=[_iso(d) for d in tail.index], tail_book_pnl=[float(v) for v in pnl.iloc[tail_idx]])
        scalable = book_var > 0 and abs(total) > 1e-9 * max(1.0, abs(book_var)) and (total > 0)
        why = "" if scalable else (
            f"the book's VaR is {book_var:,.2f} (no loss at the quantile)" if book_var <= 0 else
            "the positions' average P&L on the tail days adds up to no loss, so it cannot be scaled to the VaR")
        for r in included:
            pid = r["position_id"]
            r["tail_pnl"] = [float(v) for v in tail[pid].values]
            if scalable:
                r["contribution_var"] = float(raw[pid]) * book_var / total
                r["contribution_share"] = r["contribution_var"] / book_var
            else:
                r["contribution_reason"] = why
        if not scalable:
            out["book_var_reason"] = why
    for r in rows:
        r.setdefault("contribution_reason", "" if r["included"] else r["reason"])

    # diversification
    have = [r["standalone_var"] for r in included if not _isnan(r["standalone_var"])]
    missing = len(included) - len(have)
    div = out["diversification"]
    div.update(standalone_missing=missing, book_var=out["book_var"])
    if have:
        div["sum_standalone"] = float(sum(have))
        div["saved"] = div["sum_standalone"] - out["book_var"] if not _isnan(out["book_var"]) else NAN
    div["reason"] = "; ".join(x for x in (
        f"excludes {missing} of {len(included)} positions with no standalone VaR" if missing else "",
        out["book_var_reason"]) if x)

    # which VaR is which
    head = out["headline_var"]
    if _isnan(out["book_var"]):
        note = "no VaR on the positions' book"
    elif _isnan(head):
        note = "the headline VaR over every underlyer is not available; this is the VaR of the positions with history"
    else:
        diff = out["book_var"] - head
        note = ("the positions' book VaR (the included positions only) equals the headline VaR over every underlyer"
                if abs(diff) < 0.005 else
                f"the positions' book VaR is over the {len(included)} positions with history only; the headline VaR "
                f"is over every underlyer (currencies and metals too): they differ by {diff:,.2f}")
        if excluded:
            note += f"; {len(excluded)} position(s) left out (see excluded)"
    if partials:
        note += f"; {len(partials)} position(s) counted without their currency hedge (see partial_note)"
    out["book_var_note"] = note

    out["correlation"], out["correlation_reason"] = _correlation(included, series, config)
    included.sort(key=lambda r: -(r["contribution_var"] if not _isnan(r["contribution_var"]) else -math.inf))
    out["positions"] = included + excluded
    return out


def _th(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
