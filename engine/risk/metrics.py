"""The Risk tab's numbers: the nm-dashboard's risk metrics (its Portfolio tab, `fx_alpha/
dashboard.py` "Portfolio risk, held-constant book") on this book's own positions.

`book_risk(conn, as_of)` takes the book's delta from `engine.ladder.positions.book_positions`
(each currency's USD delta with the FX options' delta in it, and the metals: the app's
official marks, nothing recomputed here) and applies the nm-dashboard market history
(`engine.risk.history`) to it, held constant, the way the dashboard does, with its
definitions. The macro trader's rates (DV01) and equity-index (ES + SPX) underlyers left
in Phase 2 (user approval 2026-09-24): `book_positions`' `rates` and `equity_index`
blocks are not read. Commodity underlyers join in Phase 4 as kinds of their own
(`DELTA_KINDS`).

  Daily $ P&L series of a row, today's position held constant across history
    FX / METAL: usd_delta x (dln(series) + carry[t]), the log-diff of the USD-per-unit
      close plus carry[t] = (yield_ccy[t-1] - yield_USD[t-1]) / 100 / 365
      (`core/bbg_loader.py::daily_moves_and_carry`: yields in percent, lagged one day,
      forward-filled onto the spot dates); with no yields on file the spot term alone,
      said in the row's note.
  lag2_date = the history's last date on or before as_of, less 2 business days
    (`pd.offsets.BusinessDay(2)`); s_hist = the series up to it, what the dashboard's
    sizer saw.
  vol_blended_ann_usd, on s_hist: NaN with fewer than trail_window_bd (500) observations;
    trailing = std(last 500) x sqrt(252); crisis = std(series over stress_start..stress_end,
    2008-2010) x sqrt(252) when the last date is on or after the cutover (2011-01-01) and
    that window has more than one observation, else trailing; blended = w_trail x
    trailing + w_stress x crisis (2/3, 1/3). `vol_trailing_ann_usd` and
    `vol_crisis_ann_usd` are reported beside it.
  var95_1d_usd (the dashboard's `var95_1y_$M`): minus the 5th percentile (linear
    interpolation) of the LAST var_window_bd (252) observations of the full series, not
    lag-2 limited; NaN with fewer; positive = a loss.
  worst_1d_raw_usd / _date: the minimum of the full series, nothing excluded.
  worst_1d_ex_shocks_usd / _date: the minimum of s_hist from worst_day_start
    (2008-01-01) with the shock dates (config `shock_dates`: SNB 2015-01-15, Brexit
    2016-06-24) set to 0.
  Book: the rows' series summed date by date (correlation embedded; a row with no series
    is left out and named in `missing`), then the same four figures on the sum.
    net_usd = the sum of the rows' USD delta (+ = long the underlyer, the dashboard's
    FX-legs net; NOT the header's FX net USD, which is the USD position with the metals
    out: that one is passed through as `fx_net_usd`), gross_usd = the sum of their
    absolute values. The cap:
    stress_cap_usd = vol_target_usd x stress_pct / 100; vol_vs_target_pct,
    worst_day_ex_vs_cap_pct and over_cap (the worst ex-shocks day's loss over the cap)
    against them, over_vol_target beside it.
  Scenarios: `engine.pnl.stress` on {ccy: usd_delta} of the currency and metal rows,
    passed through unchanged; no futures line, so a scenario's total is its FX total.

Every figure is a plain number or NaN with its reason (`reason` for the row, `reasons`
per metric), never zero for something missing. Nothing here reads `marks`, writes
anything, or asks Bloomberg (hard rules 2, 3, 8): the history is a risk input only.

Output of `book_risk` (a plain, JSON-friendly dict: floats and NaN, strings, bools,
lists; the UI renders it and recomputes nothing):

  {
    "as_of": "2026-09-22",
    "history": {"available", "path", "last_date", "used_to" (last history date on or before
                as_of), "lag2_date", "reason", "note" (the copies seen with their last dates,
                then the coverage for as_of, then whether carry is in), "carry" (bool: yields
                on file), "candidates": [{path, exists, last_date}],
                "files": {spot | yields: {file, path, loaded, rows, columns, first_date,
                last_date, reason}}},
    "config": {vol_target_usd, stress_pct, stress_cap_usd, blended {...}, var_window_bd,
               var_confidence, worst_day_start, shock_dates [{date, name}], file, loaded, note},
    "book": {"net_usd", "gross_usd", "fx_net_usd", "fx_gross_usd",
             "vol_blended_ann_usd", "vol_trailing_ann_usd", "vol_crisis_ann_usd",
             "var95_1d_usd", "worst_1d_raw_usd", "worst_1d_raw_date",
             "worst_1d_ex_shocks_usd", "worst_1d_ex_shocks_date",
             "vol_vs_target_pct", "worst_day_ex_vs_cap_pct", "over_cap", "over_vol_target",
             "days", "first_date", "last_date", "rows_in_series": [underlyer, ...],
             "reason", "reasons": {metric: why NaN},
             "var_window": {"dates": [...], "pnl_usd": [...]}   (the last var_window_bd days
                            of the book series, for a chart)},
    "underlyers": [{"underlyer" ('CHF', 'XAU'), "kind" (FX | METAL), "series" (the
                    history column), "net_usd", "gross_usd", "carry" (bool), the same
                    vol / VaR / worst-day keys as the book, "worst_day_ex_vs_target_pct",
                    "days", "first_date", "last_date", "reason", "reasons", "note"}, ...]
                  ordered by gross USD, largest first (a row with no delta sorts as
                  zero, insertion order kept),
    "scenarios": {name: {"total" (= fx_total), "fx_pnl": {ccy: pnl}, "fx_total"}},
                  in config/stress.yaml order,
    "missing": [plain-language reasons: rows left out of the book series or the
                scenarios, positions the Blotter could not price, ...]
  }
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine.ladder.positions import book_positions
from engine.pnl import stress as stress_scenarios
from engine.risk.config import load_config
from engine.risk.history import YIELDS_FILE, History, load_history

ANNUAL_SCALER = math.sqrt(252.0)
NAN = float("nan")

KIND_FX = "FX"
KIND_METAL = "METAL"
# the kinds whose USD delta makes the book's net / gross; the commodity kinds join in Phase 4
DELTA_KINDS = (KIND_FX, KIND_METAL)
# the kinds config/stress.yaml's scenarios move (a currency or metal -> pct move against USD)
SCENARIO_KINDS = (KIND_FX, KIND_METAL)

METRIC_KEYS = ("vol_blended_ann_usd", "vol_trailing_ann_usd", "vol_crisis_ann_usd", "var95_1d_usd",
               "worst_1d_raw_usd", "worst_1d_raw_date", "worst_1d_ex_shocks_usd", "worst_1d_ex_shocks_date")


def _finite(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NAN
    return v if math.isfinite(v) else NAN


def _isnan(value: float) -> bool:
    return value != value


def _iso(ts: Any) -> Optional[str]:
    if ts is None or ts is pd.NaT:
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- series
def unit_moves(history: History, kind: str, series: str, as_of: str) -> Tuple[Optional[pd.Series], str, str]:
    """(the daily move per unit of USD exposure on or before `as_of`, a note, a reason):
    dln(close) + carry (module docstring), the same for FX and METAL; `kind` stays in the
    signature for the Phase 4 commodity kinds. A missing series gives (None, '', reason)."""
    if not history.available:
        return None, "", f"no market history: {history.reason}"
    frame = history.spot
    if series not in frame.columns:
        return None, "", f"no history for {series}"
    close = frame[series].loc[:as_of].dropna()
    close = close[close > 0]
    if len(close) < 2:
        return None, "", f"no history for {series} on or before {as_of} ({len(close)} observation(s))"
    moves = np.log(close).diff()
    note = ""
    yields = history.yields
    if yields is None or yields.empty or "USD" not in yields.columns:
        why = history.files.get("yields", {}).get("reason") or f"{YIELDS_FILE} not loaded"
        note = f"carry not included: {why}"
    elif series not in yields.columns:
        note = f"carry not included: no {series} column in {YIELDS_FILE}"
    else:
        lagged = yields.loc[:as_of, [series, "USD"]].shift(1)       # the rate fixed at close(t-1) governs day t
        carry = ((lagged[series] - lagged["USD"]) / 100.0) / 365.0
        moves = moves + carry.reindex(moves.index).ffill()
    return moves.dropna(), note, ""


def series_metrics(pnl: pd.Series, config: Dict[str, Any], lag2_date: pd.Timestamp) -> Dict[str, Any]:
    """The four figures of the module docstring on one daily $ P&L series, plus `days`,
    `first_date`, `last_date` and `reasons` (metric -> why it is NaN)."""
    out: Dict[str, Any] = {k: NAN for k in METRIC_KEYS}
    out["worst_1d_raw_date"] = out["worst_1d_ex_shocks_date"] = None
    reasons: Dict[str, str] = {}
    s = pnl.dropna()
    out["days"] = int(len(s))
    out["first_date"], out["last_date"] = (_iso(s.index[0]), _iso(s.index[-1])) if len(s) else (None, None)
    if not len(s):
        reasons["all"] = "no daily P&L on file"
        out["reasons"] = reasons
        return out
    lag2 = _iso(lag2_date)
    s_hist = s.loc[:lag2_date]

    b = config["blended"]
    window = int(b["trail_window_bd"])
    if len(s_hist) < window:
        why = f"needs {window} daily observations to {lag2}: {len(s_hist)} on file"
        reasons["vol_blended_ann_usd"] = reasons["vol_trailing_ann_usd"] = reasons["vol_crisis_ann_usd"] = why
    else:
        trailing = float(s_hist.iloc[-window:].std()) * ANNUAL_SCALER
        if _isnan(trailing):
            reasons["vol_blended_ann_usd"] = reasons["vol_trailing_ann_usd"] = "trailing vol is not a number"
        else:
            last = s_hist.index[-1]
            crisis = trailing
            if last >= pd.Timestamp(b["cutover"]):
                sw = s_hist.loc[b["stress_start"]:b["stress_end"]]
                if len(sw) > 1:
                    crisis = float(sw.std()) * ANNUAL_SCALER
            out["vol_trailing_ann_usd"] = trailing
            out["vol_crisis_ann_usd"] = crisis
            out["vol_blended_ann_usd"] = float(b["w_trail"]) * trailing + float(b["w_stress"]) * crisis

    n = int(config["var_window_bd"])
    if len(s) < n:
        reasons["var95_1d_usd"] = f"needs {n} daily observations: {len(s)} on file"
    else:
        q = float(s.iloc[-n:].quantile(1.0 - float(config["var_confidence"]), interpolation="linear"))
        out["var95_1d_usd"] = -q

    out["worst_1d_raw_usd"] = float(s.min())
    out["worst_1d_raw_date"] = _iso(s.idxmin())

    s_ex = s_hist.loc[config["worst_day_start"]:].copy()
    if not len(s_ex):
        reasons["worst_1d_ex_shocks_usd"] = f"no daily P&L between {config['worst_day_start']} and {lag2}"
    else:
        shocks = pd.DatetimeIndex([pd.Timestamp(d["date"]) for d in config.get("shock_dates", []) if d.get("date")])
        bad = shocks.intersection(s_ex.index)
        if len(bad):
            s_ex.loc[bad] = 0.0
        out["worst_1d_ex_shocks_usd"] = float(s_ex.min())
        out["worst_1d_ex_shocks_date"] = _iso(s_ex.idxmin())
    out["reasons"] = reasons
    return out


def _nan_metrics(reason: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {k: NAN for k in METRIC_KEYS}
    out["worst_1d_raw_date"] = out["worst_1d_ex_shocks_date"] = None
    out.update({"days": 0, "first_date": None, "last_date": None, "reasons": {"all": reason}})
    return out


# --------------------------------------------------------------------------- rows
def _row(underlyer: str, kind: str, series: str, exposure: float, reason: str, note: str = "") -> Dict[str, Any]:
    exposure = _finite(exposure)
    return {"underlyer": underlyer, "kind": kind, "series": series, "net_usd": exposure,
            "gross_usd": abs(exposure), "carry": False, "reason": reason, "note": note}


def rows_from_positions(positions: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """One row per underlyer from `book_positions`' output (module docstring), plus the
    plain-language reasons for anything the positions could not price. Only the `fx` and
    `fx_options` blocks are read; `equity_index` and `rates` are retired (Phase 2)."""
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    fx = positions.get("fx", {})
    for r in fx.get("by_ccy", []):
        ccy = r.get("ccy")
        if not ccy or ccy == "USD":
            continue
        usd = _finite(r.get("usd_delta"))
        reason = "" if not _isnan(usd) else (r.get("reason") or f"no rate for {ccy}")
        rows.append(_row(ccy, KIND_METAL if r.get("metal") else KIND_FX, ccy, usd, reason,
                         note="metal, not in the FX net" if r.get("metal") else ""))
    if fx.get("reason") and not fx.get("available", False):
        missing.append(f"FX positions: {fx['reason']}")
    opts = positions.get("fx_options", {})
    missing.extend(f"FX option: {m}" for m in opts.get("missing", []))
    if opts.get("reason") and opts["reason"] != "no open FX options":
        missing.append(f"FX options: {opts['reason']}")
    return rows, missing


def _sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gross USD, largest first; a row with no delta sorts as zero (a stable sort)."""
    def key(r: Dict[str, Any]):
        gross = r["gross_usd"]
        return -(gross if not _isnan(gross) else 0.0)
    return sorted(rows, key=key)


# --------------------------------------------------------------------------- scenarios
def _scenarios(rows: List[Dict[str, Any]], scenarios_path=None) -> Tuple[Dict[str, dict], List[str]]:
    """`engine.pnl.stress` on the currency and metal rows' USD delta; a row with no delta
    is left out and named. No futures line: a scenario's total is its FX total."""
    missing: List[str] = []
    delta: Dict[str, float] = {}
    for r in rows:
        if r["kind"] in SCENARIO_KINDS:
            if _isnan(r["net_usd"]):
                missing.append(f"{r['underlyer']}: not in the scenarios ({r['reason']})")
            else:
                delta[r["underlyer"]] = r["net_usd"]
    scenarios = stress_scenarios.load_scenarios(scenarios_path)
    run = stress_scenarios.run_scenarios(delta, scenarios)
    out: Dict[str, dict] = {name: {"total": float(result["fx_total"]), "fx_pnl": dict(result["fx_pnl"]),
                                   "fx_total": float(result["fx_total"])} for name, result in run.items()}
    return out, missing


# --------------------------------------------------------------------------- book
def book_risk(conn: sqlite3.Connection, as_of: str, *, history: Optional[History] = None,
              config: Optional[Dict[str, Any]] = None, scenarios_path=None) -> Dict[str, Any]:
    """The Risk tab's numbers for `as_of` (module docstring for the shape). `history`
    and `config` default to `load_history()` and `load_config()`."""
    history = history if history is not None else load_history()
    config = config if config is not None else load_config()
    config = dict(config)
    config["stress_cap_usd"] = float(config["vol_target_usd"]) * float(config["stress_pct"]) / 100.0

    positions = book_positions(conn, as_of)
    rows, missing = rows_from_positions(positions)

    hist_status = history.status()
    hist_status.update({"used_to": None, "lag2_date": None,
                        "carry": bool(history.available and not history.yields.empty and "USD" in history.yields.columns)})
    lag2_date: Optional[pd.Timestamp] = None
    coverage = ""          # how far the history reaches for as_of: a row's reason when it reaches nowhere
    if history.available:
        upto = history.spot.index[history.spot.index <= pd.Timestamp(as_of)]
        if len(upto):
            used_to = upto[-1]
            lag2_date = used_to - pd.offsets.BusinessDay(2)
            hist_status["used_to"] = _iso(used_to)
            hist_status["lag2_date"] = _iso(lag2_date)
            if _iso(used_to) < as_of:
                coverage = (f"history ends {_iso(used_to)}, before {as_of}: metrics on the history "
                            f"to {_iso(used_to)} (the book's positions are {as_of}'s)")
        else:
            coverage = f"no history on or before {as_of} (the file starts {history.files['spot']['first_date']})"
    notes = [n for n in (history.note, coverage) if n]      # the copies seen (history.py), then the coverage
    if not hist_status["carry"] and history.available:
        notes.append("carry not included: " + (history.files.get("yields", {}).get("reason") or f"{YIELDS_FILE} not loaded"))
    hist_status["note"] = "; ".join(notes)

    # per-row series and metrics
    series: Dict[str, pd.Series] = {}
    for r in rows:
        exposure = r["net_usd"]
        moves, note, why = (None, "", "") if lag2_date is None else unit_moves(history, r["kind"], r["series"], as_of)
        if lag2_date is None:
            why = coverage or f"no market history: {history.reason}"
        if note:
            r["note"] = (r["note"] + "; " if r["note"] else "") + note
        r["carry"] = moves is not None and not note
        if moves is None:
            r["reason"] = r["reason"] or why
            r.update(_nan_metrics(r["reason"]))
            continue
        if _isnan(exposure):
            r.update(_nan_metrics(r["reason"]))
            continue
        pnl = moves * exposure
        series[r["underlyer"]] = pnl
        r.update(series_metrics(pnl, config, lag2_date))
        r["worst_day_ex_vs_target_pct"] = _vs(r["worst_1d_ex_shocks_usd"], config["vol_target_usd"])
    for r in rows:
        r.setdefault("worst_day_ex_vs_target_pct", NAN)
    for r in rows:
        if r["underlyer"] not in series:
            missing.append(f"{r['underlyer']}: not in the book series ({r['reason']})")

    # the book
    rows = _sort_rows(rows)
    fx = positions.get("fx", {})
    delta_rows = [r for r in rows if r["kind"] in DELTA_KINDS]
    book: Dict[str, Any] = {
        "net_usd": _sum(r["net_usd"] for r in delta_rows), "gross_usd": _sum(r["gross_usd"] for r in delta_rows),
        "fx_net_usd": _finite(fx.get("net_usd")), "fx_gross_usd": _finite(fx.get("gross_usd")),
        "rows_in_series": [r["underlyer"] for r in rows if r["underlyer"] in series], "reason": "",
    }
    if series and lag2_date is not None:
        book_pnl = pd.concat(series.values(), axis=1).sum(axis=1, min_count=1).dropna()
        book.update(series_metrics(book_pnl, config, lag2_date))
        tail = book_pnl.iloc[-int(config["var_window_bd"]):]
        book["var_window"] = {"dates": [_iso(d) for d in tail.index], "pnl_usd": [float(v) for v in tail.values]}
    else:
        book["reason"] = (coverage or f"no market history: {history.reason}") if lag2_date is None \
            else "no row has both a position and a history series"
        book.update(_nan_metrics(book["reason"]))
        book["var_window"] = {"dates": [], "pnl_usd": []}
    book["vol_vs_target_pct"] = _vs(book["vol_blended_ann_usd"], config["vol_target_usd"], signed=True)
    book["worst_day_ex_vs_cap_pct"] = _vs(book["worst_1d_ex_shocks_usd"], config["stress_cap_usd"])
    book["over_cap"] = bool(not _isnan(book["worst_1d_ex_shocks_usd"])
                            and -book["worst_1d_ex_shocks_usd"] > config["stress_cap_usd"])
    book["over_vol_target"] = bool(not _isnan(book["vol_blended_ann_usd"])
                                   and book["vol_blended_ann_usd"] > config["vol_target_usd"])

    scenarios, scenario_missing = _scenarios(rows, scenarios_path)
    missing.extend(scenario_missing)
    return {"as_of": as_of, "history": hist_status, "config": config, "book": book,
            "underlyers": rows, "scenarios": scenarios, "missing": missing}


def _sum(values) -> float:
    vals = [v for v in values if not _isnan(_finite(v))]
    return float(sum(vals)) if vals else NAN


def _vs(value: float, target: float, *, signed: bool = False) -> float:
    """`value` as a percentage of `target`; a worst day is a loss, so its magnitude
    (`max(-value, 0)`) is taken unless `signed`."""
    value = _finite(value)
    if _isnan(value) or not target:
        return NAN
    return (value if signed else max(-value, 0.0)) / float(target) * 100.0
