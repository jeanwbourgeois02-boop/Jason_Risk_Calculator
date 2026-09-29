"""Risk by trade (CLAUDE.md "Screens redesign plan", Phase G, 2026-09-29): the Risk tab's one row
per trade, and the VaR of any slice of the trades.

**The trade** is the Book's position, never regrouped here: the spreads engine's positions
(`engine.spreads.period_explain.positions_of`), which on a book with trade names is one position
per name (Jason's PBRoot suffix, 'POSITION-STRATEGY-<name>'), every leg and hedge under it. The
positions, their daily USD P&L and the component VaR are `positions.py::position_risk`'s, called
once and read here (its `detail` hook hands over the series behind its figures), so a trade's
share of the book is exactly the Risk tab's by-position contribution, and a position with no
history follows its rules: left out whole, or kept "without hedge" when only a currency hedge
(the SGX USD/CNH future) has no history (user yes 2026-09-29, `partial_reason`).

Per trade (parameters in `config/risk.yaml`; W = `trade_window_bd`, 252; a figure needs at least
`trade_min_days`, 60, and is None with its reason otherwise; None is "cannot be computed", never 0):

  daily_risk_usd   1 standard deviation of the trade's daily USD P&L over its last W days.
  share_of_book    its historical component VaR over the positions' book VaR (`position_risk`'s
                   `contribution_share`, a fraction; the shares add up to 1), with
                   `contribution_var` and `standalone_var` (the book VaR definition on the trade
                   alone, for the hover) beside it.
  hedge_pct        1 - sd(the trade's P&L) / sd(its bigger leg's P&L alone), in PERCENT, over the
                   last W days. The trade's legs are its sides: one per commodity root when its
                   legs (currency hedges apart) cover two roots or more, else one per contract
                   month (a calendar); "bigger" = the larger standard deviation. When two of its
                   legs close hours apart (an exchange of `asian_close_countries` against one
                   elsewhere: SHFE zinc against LME zinc) the moves are 2-day sums (overlapping),
                   so the close-time gap does not read as unhedged risk (`hedge_moves`). An
                   outright with nothing against it is 0 %; one whose only hedge has no history
                   is None. It can be negative: the other side adds risk.
  best_fit_ratio   two-leg trades only (two sides as above; three or more: None, "more than two
                   legs"): the lots of leg B per lot of leg A, held opposite, that minimise the
                   variance of the pair (the slope of a regression of A's P&L per lot on B's,
                   cov(a, b) / var(b)), beside `lot_ratio` = -lots B / lots A as held (the same
                   convention: positive for a spread held one way against the other), and
                   `leg_correlation` (Pearson, a and b). Days both legs settled, the last W,
                   2-day moves on the same rule as hedge %. Leg A is the first leg of the trade's
                   level (spreads-engine's pair), else the bigger one.
  z / percentile   the trade's level against the last `level_window_bd` (252) settlements of that
                   same level, from each leg's own settlement history (risk-history's
                   `commodity_history`, read-only): z = (now - mean) / sd, percentile = share of
                   the window at or below now (percent). The level is spreads-engine's formula
                   (`engine.spreads.levels.converted`, weights and constant of the pair's
                   `level_spec`): a calendar near - far in the root's quote unit, a template or a
                   same-unit pair its difference in its unit; a China-against-West pair the
                   CONVERTED ratio China / foreign, both legs in USD per the template's quantity
                   unit, the China leg through the research app's own USD/CNH (no FX history:
                   None with the reason, never an unconverted ratio). The trade's level is the
                   one pair of spreads-engine's `strategies` (a one-spread trade's front pair)
                   whose legs are all open; several: None, named. "Now" is the last settlement on
                   or before as_of (`level_date`), the research history's, not our marks.
  z_entry / percentile_entry  the same on the trade's first trade date (`entry_date`), against the
                   window ending there.

`subset_var(conn, as_of, trade_names)`: the positions' book VaR recomputed on the chosen trades'
summed daily P&L (VaR is not additive: never the rows summed), the chosen trades' 1-sd daily risk
the same way, and the plain sum of their daily risks beside it for comparison. It reads the series
this module keeps in process per (database file and its mtime and size, as_of, the history object,
the parameters), so a filter change costs a sum and a quantile, no re-pricing and no re-read.

Nothing here reads `marks`, writes anything, or asks Bloomberg (hard rules 2, 3, 8); no P&L
figure is touched (hard rule 7). The research history is a risk input and context only, and
whose it is (Bloomberg's, or mock data on a PC without the research app's pulls) is said at the
top of both results and on every row (`source_kind` / `source_note`, risk-history's
`research_source`).
"""
from __future__ import annotations

import math
import os
import sqlite3
import threading
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from data.contracts import load_roots
from engine.risk.commodity import _PerLot, _sum_series, lme_history_contract
from engine.risk.commodity_history import FX_TOLERANCE_DAYS, load_commodity_history
from engine.risk.config import load_config
from engine.risk.positions import _contract_name, position_risk, var_of
from engine.spreads.levels import LevelSpec, converted, spec_from_dict

NAN = float("nan")
CHINA = "CN"
_TRADE = "__trade"
_MEMO_SLOTS = 4
_MEMO: "OrderedDict[tuple, dict]" = OrderedDict()
_BASE: "OrderedDict[tuple, dict]" = OrderedDict()
_LOCK = threading.Lock()

LEVEL_RATIO = "ratio"
LEVEL_DIFFERENCE = "difference"


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    """A finite float, else None (this module's rows use None for "cannot be computed")."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _iso(ts: Any) -> Optional[str]:
    return None if ts is None else pd.Timestamp(ts).strftime("%Y-%m-%d")


def _std(s: pd.Series) -> Optional[float]:
    return _num(s.std()) if len(s) > 1 else None


def _moves(frame: pd.DataFrame, two_day: bool) -> pd.DataFrame:
    """Daily moves, or overlapping 2-day sums when the legs close hours apart."""
    return frame.rolling(2).sum().iloc[1:] if two_day else frame


def _asof(conv: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """`conv` on `index`: the last value on or before each date within FX_TOLERANCE_DAYS
    (risk-history's own conversion rule), NaN beyond."""
    conv = conv.dropna().astype(float).sort_index()
    conv.index = pd.to_datetime(conv.index)
    left = pd.DataFrame({"date": pd.to_datetime(index)})
    right = pd.DataFrame({"date": conv.index, "v": conv.to_numpy()})
    merged = pd.merge_asof(left, right, on="date", direction="backward",
                           tolerance=pd.Timedelta(days=FX_TOLERANCE_DAYS))
    return pd.Series(merged["v"].to_numpy(), index=index)


# --------------------------------------------------------------------------- the block (memoised)
def _memo_key(conn: sqlite3.Connection, as_of: str, history, config: Dict[str, Any]) -> Optional[tuple]:
    """(database file, mtime, size, as_of, history identity, parameters), or None for a database
    with no file (in memory): computed afresh on every call then."""
    try:
        path = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
        if not path:
            return None
        st = os.stat(path)
    except (sqlite3.Error, OSError):
        return None
    params = tuple((k, repr(config.get(k))) for k in (
        "var_window_bd", "var_confidence", "var_contribution_days", "correlation_min_days", "trade_window_bd",
        "trade_min_days", "level_window_bd", "level_min_days", "asian_close_countries"))
    return (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size, str(as_of), id(history),
            getattr(history, "path", ""), getattr(history, "last_date", None), params)


def _levels_key(book_levels: Optional[Dict[str, dict]]) -> tuple:
    """The part of the memo key the Book's levels decide (their spec and the day's move)."""
    if book_levels is None:
        return ()
    return ("trade_book",) + tuple(sorted((pid, repr((lv or {}).get("spec")), repr((lv or {}).get("change")),
                         str((lv or {}).get("mode") or "")) for pid, lv in book_levels.items()))


def _base_block(conn: sqlite3.Connection, as_of: str, history, config: Dict[str, Any]) -> Optional[dict]:
    """The latest block built for this database, as_of, history and parameters, whatever Book
    levels it was given: its series and component VaR do not depend on them (subset_var)."""
    key = _memo_key(conn, as_of, history, config)
    if key is None:
        return None
    with _LOCK:
        return _BASE.get(key)


def _block(conn: sqlite3.Connection, as_of: str, spreads: Optional[dict], curve: Optional[dict], history,
           config: Dict[str, Any], book_levels: Optional[Dict[str, dict]] = None) -> dict:
    """{rows (one per trade, the full book), series {position_id: daily USD P&L}, position_risk,
    missing}: kept in process per `_memo_key` and the Book's levels (`_levels_key`)."""
    base = _memo_key(conn, as_of, history, config)
    key = None if base is None else base + (_levels_key(book_levels),)
    if key is not None:
        with _LOCK:
            hit = _MEMO.get(key)
            if hit is not None:
                _MEMO.move_to_end(key)
                return hit
    missing: List[str] = []
    if spreads is None or curve is None:
        from engine.risk.metrics import _commodity_positions
        c2, s2, missing = _commodity_positions(conn, as_of)
        curve = curve if curve is not None else c2
        spreads = spreads if spreads is not None else s2
    per_lot = _PerLot(history, as_of)
    detail: Dict[str, Any] = {}
    pr = position_risk(conn, as_of, spreads=spreads, curve=curve, per_lot=per_lot, config=config, detail=detail)
    series: Dict[str, pd.Series] = detail.get("series") or {}
    leg_series: Dict[str, Dict[str, pd.Series]] = detail.get("leg_series") or {}
    by_contract: Dict[str, dict] = detail.get("by_contract") or {}
    roots = load_roots()
    strategies = {str(s.get("name")): s for s in (spreads or {}).get("strategies") or [] if s.get("name")}
    positions = {str(p.get("position_id")): p for p in (spreads or {}).get("positions") or []}
    first_dates = _first_dates(conn)
    rows = []
    for p in pr.get("positions") or []:
        ctx = _Ctx(p, series.get(p["position_id"]), leg_series.get(p["position_id"], {}), by_contract, roots,
                   strategies.get(p["name"]) if p.get("kind") == "strategy" else None,
                   positions.get(p["position_id"]), config)
        ctx.book_level = (book_levels or {}).get(p["position_id"])
        ctx.book_given = book_levels is not None
        rows.append(_trade_row(ctx, history, as_of, first_dates))
    block = {"rows": rows, "series": series, "position_risk": pr, "missing": missing}
    # kept under the key read after the build as well: a first `book_spreads` on a database
    # creates its own tables, which moves the file's mtime, so the key read before it never
    # matches again
    after = _memo_key(conn, as_of, history, config)
    bases = [k for k in dict.fromkeys((base, after)) if k is not None]
    if bases:
        with _LOCK:
            for b in bases:
                _MEMO[b + (_levels_key(book_levels),)] = block
                _BASE[b] = block
            while len(_MEMO) > _MEMO_SLOTS:
                _MEMO.popitem(last=False)
            while len(_BASE) > _MEMO_SLOTS:
                _BASE.popitem(last=False)
    return block


def _first_dates(conn: sqlite3.Connection) -> Dict[str, str]:
    """{trade_id: trade_date} from the trades on file (the fills)."""
    try:
        return {str(t): str(d or "") for t, d in conn.execute("SELECT trade_id, trade_date FROM trades_official")}
    except sqlite3.Error:
        return {}


class _Ctx:
    """One trade's inputs: its position_risk row, series, legs and spreads-engine entries."""

    def __init__(self, row: dict, series: Optional[pd.Series], parts: Dict[str, pd.Series],
                 by_contract: Dict[str, dict], roots: Dict[str, Any], strategy: Optional[dict],
                 position: Optional[dict], config: Dict[str, Any]):
        self.row, self.series, self.parts, self.by_contract = row, series, parts, by_contract
        self.roots, self.strategy, self.position, self.config = roots, strategy, position, config
        self.book_level: Optional[dict] = None     # the trade's `level` of spreads-engine's trade_book, when given
        self.book_given = False                    # trade_book was passed (a row may still have no trade in it)

    def root_of(self, cid: str) -> str:
        return str((self.by_contract.get(cid) or {}).get("root_id") or "")

    def country_of(self, cid: str) -> str:
        return str(getattr(self.roots.get(self.root_of(cid)), "country", "") or "")


# --------------------------------------------------------------------------- one trade
def _trade_row(ctx: _Ctx, history, as_of: str, first_dates: Dict[str, str]) -> dict:
    p = ctx.row
    strategy = ctx.strategy or {}
    kind = str(p.get("kind") or "")
    all_ids = [str(t) for t in (list(strategy.get("trade_ids") or []) + list(strategy.get("closed_trade_ids") or [])
                                or p.get("trade_ids") or [])]
    entry_dates = sorted(d for d in (first_dates.get(t, "") for t in all_ids) if d)
    out: Dict[str, Any] = {
        "trade": str(p.get("name") or p["position_id"]), "position_id": p["position_id"], "kind": kind,
        "name": p.get("name"), "type": str(strategy.get("type") or (ctx.position or {}).get("trade_type") or ""),
        "trade_ids": list(p.get("trade_ids") or []), "included": bool(p.get("included")),
        "reason": p.get("reason") or "", "partial": bool(p.get("partial")), "partial_reason": p.get("partial_reason") or "",
        "days": p.get("days") or 0, "first_date": p.get("first_date"), "last_date": p.get("last_date"),
        "share_of_book": _num(p.get("contribution_share")), "contribution_var": _num(p.get("contribution_var")),
        "standalone_var": _num(p.get("standalone_var")),
        "share_reason": p.get("contribution_reason") or ("" if _num(p.get("contribution_share")) is not None
                                                         else p.get("reason") or "no contribution"),
        "standalone_reason": p.get("standalone_reason") or "",
        "entry_date": entry_dates[0] if entry_dates else None,
    }
    out.update(_daily_risk(ctx))
    sides, two_day, side_why = _sides(ctx)
    out.update(_hedge(ctx, sides, two_day, side_why))
    spec, spec_why = _level_spec(ctx)
    out.update(_best_fit(ctx, sides, two_day, side_why, spec))
    out.update(_z_block(history, spec, spec_why, as_of, out["entry_date"], ctx))
    kind, note = _source(history)
    # every figure of the row is drawn from the research settlement history: each says whose it is
    out.update(source_kind=kind, source_note=note if kind != "real" else "",
               sources={k: kind for k in HISTORY_FIGURES})
    return out


HISTORY_FIGURES = ("daily_risk_usd", "share_of_book", "contribution_var", "standalone_var", "hedge_pct",
                   "best_fit_ratio", "leg_correlation", "z", "percentile", "level_now", "z_entry",
                   "percentile_entry", "level_at_entry")


def _price_check(conn: sqlite3.Connection, as_of: str, history) -> dict:
    """risk-history's `research_price_check`, passed through (it never raises; a failure is a reason)."""
    try:
        from engine.risk.commodity_history import research_price_check
        return research_price_check(conn, as_of, db_path=getattr(history, "path", None) or None)
    except Exception as exc:  # noqa: BLE001 -- another lane's check never takes the rows down
        return {"available": False, "reason": f"the research price check could not run ({type(exc).__name__}: {exc})",
                "rows": [], "flagged": []}


def _source(history) -> Tuple[str, str]:
    """(source_kind 'real' | 'mock' | 'unknown', source_note): risk-history's `research_source`
    as the history object carries it."""
    kind = str(getattr(history, "source_kind", "") or "unknown")
    note = str(getattr(history, "source_note", "") or "")
    if not note and kind != "real":
        note = "research history's provider is unknown"
    return kind, note


def _window(ctx: _Ctx) -> Tuple[Optional[pd.Series], str]:
    """The trade's last trade_window_bd daily P&Ls, or (None, why)."""
    if ctx.series is None:
        return None, ctx.row.get("reason") or "no history series"
    s = ctx.series.dropna()
    n, floor = int(ctx.config["trade_window_bd"]), int(ctx.config["trade_min_days"])
    if len(s) < floor:
        return None, f"needs {floor} daily observations: {len(s)} on file"
    return s.iloc[-n:], ""


def _daily_risk(ctx: _Ctx) -> dict:
    w, why = _window(ctx)
    out = {"daily_risk_usd": None, "daily_risk_reason": why, "daily_risk_days": 0}
    if w is None:
        return out
    sd = _std(w)
    out.update(daily_risk_usd=sd, daily_risk_days=int(len(w)),
               daily_risk_reason="" if sd is not None else "the daily P&L has no standard deviation")
    if sd is not None and len(w) < int(ctx.config["trade_window_bd"]):
        out["daily_risk_reason"] = f"on {len(w)} days, fewer than {ctx.config['trade_window_bd']}"
    return out


def _sides(ctx: _Ctx) -> Tuple[List[dict], bool, str]:
    """(the trade's sides, 2-day moves?, why there are none). Its legs with a series, currency
    hedges apart, one side per root when they cover two roots or more, else per contract; each
    {key, name, contracts, lots (delta lots summed), series}."""
    legs = [lg for lg in ctx.row.get("legs") or []
            if lg.get("in_series") and not lg.get("hedge") and lg["contract_id"] in ctx.parts]
    if not legs:
        return [], False, (ctx.row.get("reason") or "no leg besides currency hedges has a history series")
    roots = {ctx.root_of(lg["contract_id"]) for lg in legs}
    by_root = len(roots) >= 2
    sides: "OrderedDict[str, dict]" = OrderedDict()
    for lg in legs:
        cid = lg["contract_id"]
        key = ctx.root_of(cid) if by_root else cid
        if by_root:
            name = str(getattr(ctx.roots.get(key), "name", "") or key)
        else:
            name = _contract_name(ctx.by_contract.get(cid) or {"contract_id": cid})
        side = sides.setdefault(key, {"key": key, "name": name, "contracts": [], "lots": 0.0, "parts": []})
        side["contracts"].append(cid)
        side["lots"] += float(lg.get("delta_lots") or 0.0)
        side["parts"].append(ctx.parts[cid])
    for side in sides.values():
        side["series"] = _sum_series(side.pop("parts"))
    asian = set(ctx.config.get("asian_close_countries") or [])
    in_series = [lg["contract_id"] for lg in ctx.row.get("legs") or [] if lg.get("in_series")]
    regions = {ctx.country_of(cid) in asian for cid in in_series}
    return [s for s in sides.values() if s["series"] is not None], len(regions) > 1, ""


def _hedge(ctx: _Ctx, sides: List[dict], two_day: bool, side_why: str) -> dict:
    out = {"hedge_pct": None, "hedge_reason": "", "hedge_leg": None, "hedge_moves": "2-day" if two_day else "1-day",
           "hedge_days": 0}
    w, why = _window(ctx)
    if w is None or not sides:
        out["hedge_reason"] = why or side_why
        return out
    if len(sides) == 1 and ctx.row.get("partial"):
        out["hedge_reason"] = (f"one leg, and its currency hedge has no price history "
                               f"({ctx.row.get('partial_reason')}): nothing to measure")
        return out
    frame = pd.DataFrame({_TRADE: w, **{s["key"]: s["series"].reindex(w.index).fillna(0.0) for s in sides}})
    m = _moves(frame, two_day)
    sd_trade = _std(m[_TRADE])
    sds = {s["key"]: _std(m[s["key"]]) for s in sides}
    big = max(sides, key=lambda s: sds[s["key"]] if sds[s["key"]] is not None else -1.0)
    sd_big = sds[big["key"]]
    out.update(hedge_leg=big["name"], hedge_days=int(len(m)))
    if sd_trade is None or not sd_big:
        out["hedge_reason"] = f"{big['name']} did not move over the window, so there is no ratio"
        return out
    out["hedge_pct"] = (1.0 - sd_trade / sd_big) * 100.0
    notes = []
    if len(sides) == 1:
        hedged = any(lg.get("hedge") and lg.get("in_series") for lg in ctx.row.get("legs") or [])
        notes.append("one leg against its currency hedge" if hedged else "one leg: nothing hedges it")
    if ctx.row.get("partial"):
        notes.append(ctx.row.get("partial_reason"))
    if len(m) < int(ctx.config["trade_window_bd"]) - (1 if two_day else 0):
        notes.append(f"on {len(m)} {'2-day' if two_day else 'daily'} moves")
    out["hedge_reason"] = "; ".join(n for n in notes if n)
    return out


def _best_fit(ctx: _Ctx, sides: List[dict], two_day: bool, side_why: str, spec: Optional[LevelSpec]) -> dict:
    out = {"best_fit_ratio": None, "lot_ratio": None, "leg_correlation": None, "best_fit_reason": "",
           "ratio_legs": [], "lots_a": None, "lots_b": None, "best_fit_moves": "2-day" if two_day else "1-day",
           "best_fit_days": 0}
    if not sides:
        out["best_fit_reason"] = side_why
        return out
    if len(sides) > 2:
        out["best_fit_reason"] = f"more than two legs ({len(sides)}: {', '.join(s['name'] for s in sides)})"
        return out
    if len(sides) < 2:
        out["best_fit_reason"] = "one leg: no ratio to fit"
        return out
    a, b = _order_sides(ctx, sides, spec)
    out.update(ratio_legs=[a["name"], b["name"]], lots_a=a["lots"], lots_b=b["lots"])
    zero = [s["name"] for s in (a, b) if abs(s["lots"]) < 1e-9]
    if zero:
        out["best_fit_reason"] = f"{', '.join(zero)} nets to zero lots, so it has no P&L per lot"
        return out
    out["lot_ratio"] = -b["lots"] / a["lots"]
    frame = pd.concat([a["series"] / a["lots"], b["series"] / b["lots"]], axis=1, join="inner").dropna()
    frame.columns = ["a", "b"]
    frame = frame.iloc[-int(ctx.config["trade_window_bd"]):]
    floor = int(ctx.config["trade_min_days"])
    if len(frame) < floor:
        out["best_fit_reason"] = f"the two legs share {len(frame)} settlement days, fewer than {floor}"
        return out
    m = _moves(frame, two_day)
    var_b = _num(m["b"].var())
    out["best_fit_days"] = int(len(m))
    out["leg_correlation"] = _num(m["a"].corr(m["b"]))
    if not var_b:
        out["best_fit_reason"] = f"{b['name']} did not move over the window, so there is no fit"
        return out
    out["best_fit_ratio"] = _num(m["a"].cov(m["b"]) / var_b)
    return out


def _order_sides(ctx: _Ctx, sides: List[dict], spec: Optional[LevelSpec]) -> Tuple[dict, dict]:
    """(leg A, leg B): A is the side holding the first leg of the trade's level, else the one
    with the larger standard deviation."""
    if spec is not None and spec.legs:
        first = spec.legs[0]
        for i, s in enumerate(sides):
            if s["key"] == first.root_id or any(_same_contract(c, first) for c in s["contracts"]):
                return s, sides[1 - i]
    sd = [(_std(s["series"]) or 0.0) for s in sides]
    return (sides[0], sides[1]) if sd[0] >= sd[1] else (sides[1], sides[0])


def _same_contract(contract_id: str, leg) -> bool:
    """A position leg's contract id against a level leg: the same future, or an LME prompt
    ('LME:ZS 2026-11-18') in the level leg's month."""
    if contract_id == leg.instrument_id:
        return True
    inst, _, prompt = contract_id.partition(" ")
    return inst == leg.instrument_id and bool(prompt) and prompt[:7] == leg.month_key


# --------------------------------------------------------------------------- the level
def _level_spec(ctx: _Ctx) -> Tuple[Optional[LevelSpec], str]:
    """The trade's level: the one spreads-engine pair whose legs are all open in the trade (a
    strategy), or the position's own level spec (a spread with no trade name)."""
    if ctx.book_level is not None:                 # spreads-engine's one level of the trade wins
        if ctx.book_level.get("spec"):
            return spec_from_dict(ctx.book_level["spec"]), ""
        return None, str(ctx.book_level.get("reason") or "the Book's level of this trade has no formula")
    open_ids = [lg["contract_id"] for lg in ctx.row.get("legs") or []
                if not lg.get("hedge") and _num(lg.get("lots")) not in (None, 0.0)]
    if ctx.strategy is not None and len(open_ids) < 2:
        return None, "one open leg: no spread level"
    if ctx.strategy is not None:
        pairs = [p for p in ctx.strategy.get("pairs") or [] if p.get("level_spec")]
        if not pairs:
            return None, "no spread in the trade has a level (spreads-engine: no pair with a level)"
        live = [p for p in pairs if all(any(_same_contract(c, lg) for c in open_ids)
                                        for lg in (spec_from_dict(p["level_spec"]).legs))]
        if len(live) == 1:
            return spec_from_dict(live[0]["level_spec"]), ""
        if not live:
            return None, "no spread of the trade has all its legs open"
        return None, (f"{len(live)} spreads in the trade ({', '.join(str(p.get('pair_id')) for p in live)}): "
                      f"no single level")
    if ctx.position is not None and ctx.position.get("level_spec"):
        return spec_from_dict(ctx.position["level_spec"]), ""
    if str(ctx.row.get("position_id", "")).startswith(("OUTRIGHT-", "TRADE-")):
        return None, "an outright or a single trade has no spread level"
    return None, "the position has no level (no calendar or template fits its legs)"


def _leg_prices(history, leg, as_of: str
                ) -> Tuple[Optional[pd.Series], str, Optional[str], Optional[pd.Series]]:
    """(a level leg's settlements as quoted (the research app's raw settle = Bloomberg's quote) on
    or before as_of, why None, the first date of its own settlements, the research contract read
    on each date (a roll or the splice changes it)). An LME metal reads its
    prompt month's research contract. Before the contract's first settlement, the contract that
    held its place on the strip that day (its months ahead on as_of): the rule the P&L history
    already follows (`commodity_history.daily_pnl_series_for_position`), so the level's history
    reaches as far back as the trade's P&L history does."""
    if leg.instrument_id == leg.root_id:
        try:
            year, month = (int(x) for x in leg.month_key.split("-")[:2])
        except ValueError:
            return None, f"{leg.instrument_id}: its prompt month {leg.month_key!r} is not known", None, None
        cid, _note, why = lme_history_contract(history, leg.root_id, year, month, f"{year:04d}-{month:02d}-15", as_of)
        if cid is None:
            return None, f"{leg.instrument_id} {leg.month_key}: {why}", None, None
        s = history.settle_series(cid)
        name = cid
    else:
        s = history.settle_series(leg.instrument_id, leg.root_id)
        name = leg.instrument_id
    if s.empty:
        return None, s.attrs.get("reason") or f"{leg.instrument_id}: no settlements", None, None
    scale = _num(s.attrs.get("price_scale")) or 1.0
    raw = (s / scale).astype(float)
    raw.index = pd.to_datetime(raw.index)
    own_from = _iso(raw.index[0])
    ids = pd.Series(str(s.attrs.get("research_contract_id") or name), index=raw.index, dtype=object)
    rank, _why = history.months_ahead_of(name, leg.root_id, as_of)
    if rank is not None:
        cm = history.constant_maturity_series(leg.root_id, int(rank))
        if not cm.empty:
            held = cm.attrs.get("contracts") or {}
            cm = (cm / scale).astype(float)
            cm.index = pd.to_datetime(cm.index)
            early = cm[cm.index < raw.index[0]]
            cm_ids = pd.Series([str(held.get(d, held.get(pd.Timestamp(d), ""))) for d in early.index],
                               index=early.index, dtype=object)
            raw = pd.concat([early, raw]).sort_index()
            ids = pd.concat([cm_ids, ids]).sort_index()
    keep = raw.index <= pd.Timestamp(as_of)
    raw, ids = raw[keep], ids[keep]
    if raw.empty:
        return None, f"{leg.instrument_id}: no settlement on or before {as_of}", None, None
    return raw, "", own_from, ids


def _usd_per(history, ccy: str, index: pd.DatetimeIndex) -> Tuple[Optional[pd.Series], str]:
    if ccy == "USD":
        return pd.Series(1.0, index=index), ""
    conv = history.usd_per_unit(ccy)
    if conv.empty:
        return None, (f"no {ccy} FX history in the research database ({conv.attrs.get('reason') or 'none'}), "
                      f"so the level cannot be converted")
    return _asof(conv, index), ""


def level_history(history, spec: LevelSpec, roots: Dict[str, Any], as_of: str
                  ) -> Tuple[Optional[pd.Series], str, str, str]:
    """(the level's daily history on or before as_of, its kind ('ratio' | 'difference'), its unit,
    why None). Each leg at `levels.converted` of its settlement (price x price_scale x the
    quantity conversion); a China-against-West pair the ratio China / foreign with both legs in
    USD; any other level sum weight x leg (in the spec's currency) + constant. The series' attrs
    carry `own_from`: the first date on which every leg reads its own contract (before it, the
    contracts at the same place on the strip, `_leg_prices`)."""
    countries = [str(getattr(roots.get(lg.root_id), "country", "") or "") for lg in spec.legs]
    ratio = len(spec.legs) == 2 and countries.count(CHINA) == 1
    kind = LEVEL_RATIO if ratio else LEVEL_DIFFERENCE
    target = "USD" if ratio else spec.currency
    unit = "ratio China / foreign, both in USD" if ratio else spec.unit
    cols = []
    held: List[pd.Series] = []
    own_from: List[str] = []
    for lg in spec.legs:
        raw, why, first, ids = _leg_prices(history, lg, as_of)
        if raw is None:
            return None, kind, unit, why
        if first:
            own_from.append(first)
        held.append(ids)
        own = LevelSpec(spec.kind, spec.unit, lg.currency, 0.0, spec.legs, spec.weights, spec.units_per_lot)
        cols.append(converted(raw, lg, own))               # no FX yet: the leg's own currency
    frame = pd.concat(cols, axis=1, join="inner").dropna()
    if frame.empty:
        return None, kind, unit, "the legs have no settlement day in common"
    frame.columns = list(range(len(spec.legs)))
    for i, lg in enumerate(spec.legs):
        if lg.currency != target:
            s_leg, why = _usd_per(history, lg.currency, frame.index)
            s_unit, why_u = _usd_per(history, target, frame.index)
            if s_leg is None or s_unit is None:
                return None, kind, unit, why or why_u
            frame[i] = frame[i] * s_leg / s_unit
    frame = frame.dropna()
    if frame.empty:
        return None, kind, unit, f"no settlement day has a {target} conversion within {FX_TOLERANCE_DAYS} days"
    if ratio:
        cn = countries.index(CHINA)
        den = frame[1 - cn].where(frame[1 - cn].abs() > 1e-12)
        level = (frame[cn] / den).dropna()
    else:
        level = sum(w * frame[i] for i, w in enumerate(spec.weights)) + spec.constant
    level = level.dropna().sort_index()
    if not len(level):
        return None, kind, unit, "no level could be formed"
    level.attrs["own_from"] = max(own_from) if own_from else None
    # a day whose change spans a roll or the splice (any leg reads another contract than the day
    # before): its change is not a move of the level (level_sd leaves it out)
    key = pd.concat([h.reindex(level.index) for h in held], axis=1).astype(str).agg("|".join, axis=1)
    level.attrs["switch"] = (key != key.shift()).to_numpy()
    return level, kind, unit, ""


def _z_at(level: pd.Series, at: str, n: int, floor: int) -> dict:
    s = level[level.index <= pd.Timestamp(at)]
    out = {"z": None, "percentile": None, "level": None, "date": None, "days": 0, "reason": ""}
    if s.empty:
        out["reason"] = f"no level on or before {at} (the history starts {_iso(level.index[0])})"
        return out
    w = s.iloc[-n:]
    out.update(level=_num(w.iloc[-1]), date=_iso(w.index[-1]), days=int(len(w)))
    if len(w) < floor:
        out["reason"] = f"needs {floor} settlements of the level to {out['date']}: {len(w)}"
        return out
    sd = _std(w)
    if not sd:
        out["reason"] = "the level did not move over the window"
        return out
    v = float(w.iloc[-1])
    out["z"] = (v - float(w.mean())) / sd
    out["percentile"] = float((w <= v).mean()) * 100.0
    return out


def _z_block(history, spec: Optional[LevelSpec], spec_why: str, as_of: str, entry_date: Optional[str],
             ctx: _Ctx) -> dict:
    out = {"z": None, "percentile": None, "level_now": None, "level_date": None, "level_days": 0,
           "level_kind": None, "level_unit": "", "level_legs": [], "z_reason": spec_why,
           "z_entry": None, "percentile_entry": None, "level_at_entry": None, "z_entry_reason": spec_why,
           "level_own_from": None, "level_note": "",
           "level_sd": None, "level_sd_reason": spec_why, "level_sd_days": 0,
           "level_move": None, "move_sigma": None, "move_sigma_reason": spec_why}
    if spec is None:
        return out
    out["level_legs"] = [lg.instrument_id + (f" {lg.month_key}" if lg.instrument_id == lg.root_id else "")
                         for lg in spec.legs]
    if not getattr(history, "available", False):
        out["z_reason"] = out["z_entry_reason"] = out["level_sd_reason"] = out["move_sigma_reason"] = (
            f"no settlement history: {getattr(history, 'reason', '')}")
        return out
    level, kind, unit, why = level_history(history, spec, ctx.roots, as_of)
    out.update(level_kind=kind, level_unit=unit)
    if level is None:
        out["z_reason"] = out["z_entry_reason"] = out["level_sd_reason"] = out["move_sigma_reason"] = why
        return out
    n, floor = int(ctx.config["level_window_bd"]), int(ctx.config["level_min_days"])
    now = _z_at(level, as_of, n, floor)
    own = level.attrs.get("own_from")
    first_in_window = _iso(level[level.index <= pd.Timestamp(as_of)].index[-now["days"]]) if now["days"] else None
    out.update(z=now["z"], percentile=now["percentile"], level_now=now["level"], level_date=now["date"],
               level_days=now["days"], z_reason=now["reason"], level_own_from=own,
               level_note=(f"before {own} the level reads the contracts at the same place on the curve (the P&L "
                           f"history's rule)" if own and first_in_window and own > first_in_window else ""))
    if now["date"] and (pd.Timestamp(as_of) - pd.Timestamp(now["date"])).days > 7 and not now["reason"]:
        out["z_reason"] = f"the settlements end {now['date']}, before {as_of}"
    out.update(_level_sd(level, as_of, n, floor))
    out.update(_move_sigma(ctx.book_level, kind, out["level_sd"], out["level_sd_reason"], ctx.book_given))
    if not entry_date:
        out["z_entry_reason"] = "the trade's first trade date is not on file"
        return out
    ent = _z_at(level, entry_date, n, floor)
    out.update(z_entry=ent["z"], percentile_entry=ent["percentile"], level_at_entry=ent["level"],
               z_entry_reason=ent["reason"])
    return out


def _level_sd(level: pd.Series, as_of: str, n: int, floor: int) -> dict:
    """The standard deviation of the level's daily changes over the z window (its last n
    settlements to as_of: the n - 1 changes into them), leaving out a change that spans a roll or
    the splice (a leg read another contract the day before), in the level's unit."""
    out = {"level_sd": None, "level_sd_reason": "", "level_sd_days": 0}
    cut = level.index <= pd.Timestamp(as_of)
    s = level[cut]
    if len(s) < 2:
        out["level_sd_reason"] = f"{len(s)} settlement(s) of the level to {as_of}: no daily change"
        return out
    switch = pd.Series(level.attrs.get("switch", [False] * len(level)), index=level.index)[cut].to_numpy(dtype=bool)
    start = s.index[-n:][0]
    diff = s.diff()
    changes = diff[(diff.index > start) & ~switch].dropna()
    out["level_sd_days"] = int(len(changes))
    if len(changes) < floor - 1:
        out["level_sd_reason"] = f"needs {floor - 1} daily changes of the level: {len(changes)}"
        return out
    sd = _std(changes)
    if not sd:
        out["level_sd_reason"] = "the level did not move over the window"
        return out
    out["level_sd"] = sd
    return out


def _move_sigma(book_level: Optional[dict], kind: str, sd: Optional[float], sd_why: str,
                book_given: bool = False) -> dict:
    """Today's move of the Book's level (spreads-engine's trade_book `level.change`, our marks)
    over level_sd (the research history's), when the caller passed trade_book."""
    out = {"level_move": None, "move_sigma": None, "move_sigma_reason": ""}
    if book_level is None:
        out["move_sigma_reason"] = ("not a trade of the Book's trade_book (no trade name): no level move"
                                    if book_given else
                                    "the Book's level move was not passed (trade_book=): the move / level_sd")
        return out
    out["level_move"] = _num(book_level.get("change"))
    mode = str(book_level.get("mode") or "")
    if out["level_move"] is None:
        out["move_sigma_reason"] = str(book_level.get("change_reason") or "the Book's level has no move today")
    elif sd is None:
        out["move_sigma_reason"] = sd_why or "no standard deviation of the level"
    elif mode and mode != kind:
        out["move_sigma_reason"] = f"the Book's level is a {mode}, the history's a {kind}: not the same unit"
    else:
        out["move_sigma"] = out["level_move"] / sd
    return out


# --------------------------------------------------------------------------- public
def _defaults(history, config):
    return (history if history is not None else load_commodity_history(),
            config if config is not None else load_config())


def _select(rows: List[dict], trade_names: Optional[Iterable[str]]) -> Tuple[List[dict], List[str]]:
    """(the rows named, the names that match none). A name matches a trade name or a position id;
    None = every row."""
    if trade_names is None:
        return list(rows), []
    names = [str(n) for n in trade_names]
    wanted = set(names)
    chosen = [r for r in rows if r["trade"] in wanted or r["position_id"] in wanted]
    seen = {r["trade"] for r in chosen} | {r["position_id"] for r in chosen}
    return chosen, [n for n in dict.fromkeys(names) if n not in seen]


def _subset(block: dict, trade_names: Optional[Iterable[str]], config: Dict[str, Any]) -> dict:
    rows, not_found = _select(block["rows"], trade_names)
    series = block["series"]
    used = [r for r in rows if r["included"] and r["position_id"] in series]
    left = [{"trade": r["trade"], "reason": r["reason"] or "no history series"} for r in rows if r not in used]
    out: Dict[str, Any] = {
        "trades": [r["trade"] for r in rows], "trades_in": [r["trade"] for r in used], "left_out": left,
        "not_found": not_found, "partial": [r["trade"] for r in used if r["partial"]],
        "var_usd": None, "var_reason": "", "daily_risk_usd": None, "daily_risk_reason": "",
        "sum_daily_risk_usd": None, "sum_daily_risk_reason": "", "diversification_usd": None,
        "days": 0, "first_date": None, "last_date": None,
        "book_var_usd": _num((block["position_risk"] or {}).get("book_var")),
        "count": len(rows), "count_book": len(block["rows"]),
    }
    if not used:
        out["var_reason"] = out["daily_risk_reason"] = out["sum_daily_risk_reason"] = (
            "no trade chosen" if not rows else "no chosen trade has a history series: "
            + "; ".join(f"{x['trade']}: {x['reason']}" for x in left))
        return out
    s = _sum_series([series[r["position_id"]] for r in used])
    if s is None:
        out["var_reason"] = out["daily_risk_reason"] = "the chosen trades' series are empty"
        return out
    out.update(days=int(len(s)), first_date=_iso(s.index[0]), last_date=_iso(s.index[-1]))
    var, why = var_of(s, config)
    out["var_usd"], out["var_reason"] = _num(var), why
    floor, n = int(config["trade_min_days"]), int(config["trade_window_bd"])
    if len(s) < floor:
        out["daily_risk_reason"] = f"needs {floor} daily observations: {len(s)} on file"
    else:
        out["daily_risk_usd"] = _std(s.iloc[-n:])
    risks = [r["daily_risk_usd"] for r in used if r["daily_risk_usd"] is not None]
    if risks:
        out["sum_daily_risk_usd"] = float(sum(risks))
        miss = len(used) - len(risks)
        out["sum_daily_risk_reason"] = f"excludes {miss} of {len(used)} trades with no daily risk" if miss else ""
        if out["daily_risk_usd"] is not None and not miss:
            out["diversification_usd"] = out["sum_daily_risk_usd"] - out["daily_risk_usd"]
    else:
        out["sum_daily_risk_reason"] = "no chosen trade has a daily risk"
    if left:
        out["var_reason"] = "; ".join(x for x in (out["var_reason"], f"excludes {len(left)} trade(s) with no "
                                                  f"history: {', '.join(x['trade'] for x in left)}") if x)
    return out


def subset_var(conn: sqlite3.Connection, as_of: str, trade_names: Optional[Iterable[str]] = None, *,
               spreads: Optional[dict] = None, curve: Optional[dict] = None, history=None,
               config: Optional[Dict[str, Any]] = None) -> dict:
    """The VaR and daily risk of the chosen trades together (module docstring). `trade_names`:
    trade names or position ids; None = the whole book. Returns {trades (the names chosen),
    trades_in (with a series), left_out [{trade, reason}], not_found, partial (counted without
    their currency hedge), var_usd (+ = a loss), var_reason, daily_risk_usd (1 sd of the summed
    daily P&L), daily_risk_reason, sum_daily_risk_usd (their daily risks added, for comparison),
    sum_daily_risk_reason, diversification_usd (sum - together), days, first_date, last_date,
    book_var_usd (every trade's, `position_risk`'s book VaR), count, count_book, source_kind,
    source_note (whose settlement history the figures are drawn from)}. None with its reason
    where a figure cannot be computed."""
    history, config = _defaults(history, config)
    block = _base_block(conn, as_of, history, config) or _block(conn, as_of, spreads, curve, history, config)
    out = _subset(block, trade_names, config)
    out["source_kind"], out["source_note"] = _source(history)
    return out


def trade_risk(conn: sqlite3.Connection, as_of: str, *, spreads: Optional[dict] = None,
               curve: Optional[dict] = None, trade_names: Optional[Sequence[str]] = None, history=None,
               config: Optional[Dict[str, Any]] = None, trade_book: Optional[dict] = None) -> dict:
    """Risk by trade for `as_of` (module docstring). `spreads` / `curve`: `book_spreads` and
    `curve_positions` of the same as-of when the caller has them (else computed); `history`: a
    `CommodityHistory` (default `load_commodity_history()`); `trade_names`: only those rows
    (trade names or position ids), the shares still of the whole book, plus `subset` =
    `subset_var` of them; `trade_book`: spreads-engine's `trade_book(conn, as_of)` when the
    caller holds it: each trade's `level.spec` is then THE level (read before this module's own
    pick) and its `level.change` gives `move_sigma`.

    Returns {as_of, available, reason, book_var (the positions' book VaR, every trade),
      method {daily_risk, share, hedge, best_fit, z}: one sentence each,
      trades: [{trade (the trade name, else the position's name), position_id, kind, name, type,
                trade_ids (open), included, reason, partial, partial_reason, days, first_date,
                last_date, entry_date,
                daily_risk_usd, daily_risk_reason, daily_risk_days,
                share_of_book (fraction), contribution_var, standalone_var, share_reason,
                standalone_reason,
                hedge_pct (percent), hedge_reason, hedge_leg, hedge_moves ('1-day' | '2-day'), hedge_days,
                best_fit_ratio, lot_ratio, leg_correlation, best_fit_reason, ratio_legs [A, B],
                lots_a, lots_b, best_fit_moves, best_fit_days,
                z, percentile (percent), level_now, level_date, level_days, level_kind ('ratio' |
                'difference'), level_unit, level_legs, z_reason,
                z_entry, percentile_entry, level_at_entry, z_entry_reason, level_own_from,
                level_note, level_sd (sd of the level's daily changes over the z window, rolls and
                the splice left out, in level_unit), level_sd_reason, level_sd_days, level_move
                (the Book's level.change, our marks; None without trade_book), move_sigma
                (level_move / level_sd), move_sigma_reason, source_kind, source_note, sources}]
              (position_risk's order: the included by contribution, largest first, then the
              left out),
      excluded_count, partial_note, missing, subset (only with trade_names), not_found,
      source_kind ('real' | 'mock' | 'unknown') and source_note (risk-history's `research_source`:
      whose settlement history every figure here is drawn from; each trade row repeats
      source_kind, its source_note ('' when real) and `sources` {figure: source_kind}),
      price_check (risk-history's `research_price_check(conn, as_of)`, passed through: whether
      the research prices are the same market as ours, root by root)}.
    Every figure a number or None with its reason."""
    history, config = _defaults(history, config)
    book_levels = ({str(t.get("position_id")): (t.get("level") or {}) for t in (trade_book or {}).get("trades") or []
                    if t.get("position_id")} if trade_book is not None else None)
    block = _block(conn, as_of, spreads, curve, history, config, book_levels)
    pr = block["position_risk"] or {}
    rows, not_found = _select(block["rows"], trade_names)
    out = {
        "as_of": as_of, "available": bool(pr.get("available")), "reason": pr.get("reason") or "",
        "book_var": _num(pr.get("book_var")), "book_var_reason": pr.get("book_var_reason") or "",
        "method": METHOD, "trades": rows, "excluded_count": sum(1 for r in rows if not r["included"]),
        "partial_note": pr.get("partial_note") or "", "missing": list(block["missing"]), "not_found": not_found,
    }
    out["source_kind"], out["source_note"] = _source(history)
    if "price_check" not in block:           # kept with the block: same database, as_of and history
        block["price_check"] = _price_check(conn, as_of, history)
    out["price_check"] = block["price_check"]
    if trade_names is not None:
        out["subset"] = _subset(block, trade_names, config)
        out["subset"]["source_kind"], out["subset"]["source_note"] = out["source_kind"], out["source_note"]
    return out


METHOD = {
    "daily_risk": "1 standard deviation of the trade's daily USD P&L over its last 252 days, today's "
                  "position held constant across the research settlement history.",
    "share": "The trade's historical component VaR over the book's: its P&L averaged over the book's tail "
             "days nearest the 95 % quantile, scaled so the shares add up to 100 %.",
    "hedge": "1 - the trade's P&L volatility / its bigger leg's alone, over a year; legs closing hours "
             "apart (China against the West) on 2-day moves.",
    "best_fit": "The lots of one leg per lot of the other that minimise the pair's variance (a regression "
                "of their P&L per lot), beside the lots held; two-leg trades only.",
    "z": "The trade's level (a calendar's near - far, a China-West pair's converted ratio China / "
         "foreign) against its own last 252 settlements; research settlement history, context only.",
}
