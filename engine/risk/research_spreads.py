"""The research app's spread statistics, read-only, as context beside the book's spreads.

The research app next door (`../Commodity Dashboard`, its `rvapp/db/schema.sql`) computes, for
every spread of its universe, a daily history and one row of statistics per run:

  * `spread_def` (`spread_id`, `family`, `name`, `sector`, `unit` '<currency>/<unit>', `verified`,
    `in_universe`; calendars also `cal_instrument_id`, `near_month`, `far_month`,
    `far_year_offset`);
  * `spread_daily` (`spread_id`, `instance`, `date`, `value` in the spread's unit);
  * `spread_stats` (one row per `spread_id`, `instance`, `asof` run: z-scores, percentile,
    half-life, the daily vol `dvol_20d` in spread units, not annualised, the day's move `chg_1d`
    and `chg_1d_sd`, staleness). `spread_stats_latest` is a copy of each spread's newest row;
    `spread_stats` keeps every run, so a past as-of reads its own run from it.

Keys are (spread_id, instance), as spreads-engine gives them:
  * a template spread: the `config/spreads/` template id ('bench.crude.brent_vs_wti',
    'proc.us.crack_321'), instance '' (None is taken as '');
  * a calendar: 'cal.<exchange>_<code>.<near>_<far>' all lower case, the research app's own
    rule ('cal.nymex_cl.z_f'), instance = the NEAR leg's contract year as four digits ('2026'
    for CLZ26 / CLF27: `far_year_offset` 1 puts the far leg a year later). The research app
    keeps statistics only for the instances it tracks (about six months out), so a deferred
    calendar has a history in `spread_daily` but no statistics.

What is served:
  * `research_spread_stats(keys, as_of, db_path=None)`: per key the statistics of its latest
    run with `asof` on or before `as_of`, or a plain reason.
  * `sigma_move(move, stats, unit=None)`: a level move in the spread's unit over the research
    app's daily vol (`dvol_20d`), labelled research.
  * `research_spread_history(key, start=None, end=None, db_path=None)`: `spread_daily` as a
    Series.

This is CONTEXT only. Nothing read here is ever a mark, written to `marks` or anywhere else, or
used for P&L or delta (hard rule 2), and nothing asks Bloomberg for anything (hard rule 8).
The database is found as `commodity_history` finds it (`COMMODITY_HISTORY_DB`, else
`../Commodity Dashboard/var/rv.sqlite`) and opened read-only (`mode=ro`), one short connection
per call, never held: the research app runs it in WAL mode and keeps writing. Nothing raises
to the caller: a missing, foreign or unreadable file is a reason naming the paths tried.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

import pandas as pd

from engine.risk.commodity_history import _connect, candidates

__all__ = ["LABEL", "STAT_FIELDS", "research_spread_history", "research_spread_stats", "sigma_move"]

LABEL = "research"
TABLES = ("spread_def", "spread_stats", "spread_daily")
# The spread_stats columns served, as named there (NULL there = None here).
STAT_FIELDS = ("level", "z_primary", "z_primary_kind", "z_1y", "pctile_5y", "half_life_days", "dvol_20d",
               "chg_1d", "chg_1d_sd", "stale_days", "stale_leg", "last_obs_date", "contract_note", "n_obs",
               "computed_at", "heavy_asof")


# --------------------------------------------------------------------------- helpers
def _find(db_path: Union[str, Path, None]) -> Tuple[Optional[Path], List[dict], str]:
    """(database path or None, [{path, exists}] tried, reason when none)."""
    if db_path is not None:
        p = Path(db_path).expanduser()
        seen = [{"path": str(p), "exists": p.is_file()}]
    else:
        seen = candidates()
    found = [c for c in seen if c["exists"]]
    if not found:
        return None, seen, "no research database: tried " + ", ".join(c["path"] for c in seen)
    return Path(found[0]["path"]), seen, ""


def _check_tables(conn) -> str:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = [t for t in TABLES if t not in names]
    return f"not the research app's database (no {', '.join(missing)} table)" if missing else ""


def _norm_key(key) -> Tuple[str, str]:
    """(spread_id lower case, instance as text; None -> '', 2026 -> '2026')."""
    if isinstance(key, str) or not isinstance(key, (tuple, list)) or len(key) != 2:
        raise ValueError(f"key {key!r} is not a (spread_id, instance) pair")
    sid, inst = key
    sid = str(sid or "").strip().lower()
    if inst is None or (isinstance(inst, float) and math.isnan(inst)):
        inst = ""
    elif isinstance(inst, float) and inst.is_integer():
        inst = str(int(inst))
    return sid, str(inst).strip()


def _iso(value) -> Tuple[Optional[str], str]:
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        return None, f"{value!r} is not a date ({exc})"
    if pd.isna(ts):
        return None, f"{value!r} is not a date"
    return ts.strftime("%Y-%m-%d"), ""


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _blank_entry(sid: str, inst: str, reason: str) -> dict:
    out = {"spread_id": sid, "instance": inst, "found": False, "reason": reason, "note": "", "label": LABEL,
           "name": None, "family": None, "sector": None, "unit": None, "verified": None, "asof": None}
    out.update({f: None for f in STAT_FIELDS})
    return out


def _calendar_hint(conn, sid: str) -> str:
    """For an unknown calendar id, the month pairs the research app has for that root."""
    parts = sid.split(".")
    if len(parts) != 3 or parts[0] != "cal":
        return ""
    pairs = [r[0].split(".")[-1] for r in conn.execute(
        "SELECT spread_id FROM spread_def WHERE spread_id LIKE ? ORDER BY spread_id", (f"cal.{parts[1]}.%",))]
    if not pairs:
        return f"; the research app has no calendar spreads of {parts[1]}"
    shown = ", ".join(pairs[:14]) + (", ..." if len(pairs) > 14 else "")
    return f"; the research app's {parts[1]} calendars are {shown}"


# ----------------------------------------------------------------------------- stats
def research_spread_stats(keys: Iterable[Tuple[str, object]], as_of, db_path: Union[str, Path, None] = None) -> dict:
    """The research app's statistics per (spread_id, instance) key, each from its latest run with
    `asof` on or before `as_of`. Never raises; see the module docstring for the key convention.

    Returns {available, path, reason, candidates, as_of, run_asof, label, source, stats}:
      available  True when the research database was found and read;
      path       the database read ('' when none); reason: '' or why nothing could be read;
      candidates [{path, exists}] tried; as_of: ISO; label: 'research';
      run_asof   the latest research run on or before `as_of` over the whole database (None when none);
      source     one line naming the database and run, for a caption or a hover;
      stats      {key as the caller gave it: entry}.
    Each entry: spread_id, instance (normalised), found (bool), reason ('' when found), note ('' or
    e.g. that the row is of an older run than run_asof), label 'research', name, family, sector,
    unit (spread_def.unit), verified (bool), asof (the run this row is from), then STAT_FIELDS:
    level, z_primary, z_primary_kind, z_1y, pctile_5y, half_life_days, dvol_20d, chg_1d,
    chg_1d_sd, stale_days, stale_leg, last_obs_date, contract_note, n_obs, computed_at,
    heavy_asof (None where the research app has NULL: not enough history)."""
    keys = list(keys)
    iso, bad = _iso(as_of)
    out = {"available": False, "path": "", "reason": "", "candidates": [], "as_of": iso, "run_asof": None,
           "label": LABEL, "source": "", "stats": {}}

    def fail_all(reason: str) -> dict:
        out["reason"] = reason
        for k in keys:
            try:
                sid, inst = _norm_key(k)
            except ValueError as exc:
                sid, inst = str(k), ""
                out["stats"][k] = _blank_entry(sid, inst, str(exc))
                continue
            out["stats"][k] = _blank_entry(sid, inst, reason)
        return out

    db, seen, why = _find(db_path)
    out["candidates"] = seen
    if db is None:
        return fail_all(why)
    out["path"] = str(db)
    if iso is None:
        return fail_all(f"as-of {bad}")
    try:
        conn = _connect(db)
        try:
            missing = _check_tables(conn)
            if missing:
                return fail_all(f"{db} is {missing}")
            out["available"] = True
            out["run_asof"] = conn.execute("SELECT MAX(asof) FROM spread_stats WHERE asof <= ?", (iso,)).fetchone()[0]
            cols = ", ".join(f"s.{f}" for f in STAT_FIELDS)
            for k in keys:
                try:
                    sid, inst = _norm_key(k)
                except ValueError as exc:
                    out["stats"][k] = _blank_entry(str(k), "", str(exc))
                    continue
                out["stats"][k] = _one(conn, db, sid, inst, iso, out["run_asof"], cols)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- a file that will not read is a reason, never a crash
        out["available"] = False
        return fail_all(f"{db} could not be read ({type(exc).__name__}: {exc})")
    out["source"] = (f"research app database {db}, run of {out['run_asof']} (latest on or before {iso})"
                     if out["run_asof"] else f"research app database {db}: no statistics run on or before {iso}")
    return out


def _one(conn, db: Path, sid: str, inst: str, iso: str, run_asof: Optional[str], cols: str) -> dict:
    d = conn.execute("SELECT name, family, sector, unit, verified, in_universe FROM spread_def WHERE spread_id = ?",
                     (sid,)).fetchone()
    if d is None:
        return _blank_entry(sid, inst, f"spread {sid} is not in the research universe ({db}){_calendar_hint(conn, sid)}")
    entry = _blank_entry(sid, inst, "")
    entry.update(name=d[0], family=d[1], sector=d[2], unit=d[3], verified=bool(d[4]))
    notes = [] if d[5] else ["no longer in the research universe (its last statistics are shown)"]
    row = conn.execute(
        f"SELECT s.asof, {cols} FROM spread_stats s WHERE s.spread_id = ? AND s.instance = ? AND s.asof = "
        "(SELECT MAX(asof) FROM spread_stats WHERE spread_id = ? AND instance = ? AND asof <= ?)",
        (sid, inst, sid, inst, iso)).fetchone()
    if row is None:
        runs = conn.execute("SELECT instance, MIN(asof) FROM spread_stats WHERE spread_id = ? GROUP BY instance "
                            "ORDER BY instance", (sid,)).fetchall()
        mine = [r[1] for r in runs if r[0] == inst]
        label = f"{sid} {inst}".strip()
        if mine:
            entry["reason"] = f"no research statistics for {label} on or before {iso} (its first run is {mine[0]})"
        elif runs:
            have = ", ".join(repr(r[0]) if r[0] == "" else r[0] for r in runs)
            entry["reason"] = (f"no research statistics for {label}: the research app tracks "
                               f"{'instance' if len(runs) == 1 else 'instances'} {have} of it")
        else:
            entry["reason"] = f"the research app has computed no statistics for {sid}"
        entry["note"] = "; ".join(notes)
        return entry
    entry["asof"] = row[0]
    entry.update({f: _clean(v) for f, v in zip(STAT_FIELDS, row[1:])})
    entry["found"] = True
    if run_asof and row[0] < run_asof:
        notes.append(f"statistics of the {row[0]} run: not in the {run_asof} run")
    entry["note"] = "; ".join(notes)
    return entry


# ------------------------------------------------------------------------------ sigma
def _unit_norm(unit) -> str:
    return "".join(str(unit or "").split()).replace("$", "USD").casefold()


def sigma_move(move, stats: dict, unit: Optional[str] = None) -> Tuple[Optional[float], str]:
    """(move / the research app's dvol_20d, '') or (None, reason). `move` is a level change in the
    spread's own unit (spreads-engine's `level_change`); `stats` one entry of
    `research_spread_stats(...)["stats"]`; `unit` the move's unit, checked against the research
    unit when given (case and spaces ignored, '$' read as 'USD'). The result is our move against
    the research app's daily vol: label it research."""
    if not isinstance(stats, dict):
        return None, "no research statistics"
    if not stats.get("found"):
        return None, stats.get("reason") or "no research statistics"
    try:
        m = float(move)
    except (TypeError, ValueError):
        return None, f"no level move ({move!r})"
    if math.isnan(m) or math.isinf(m):
        return None, "no level move"
    label = f"{stats.get('spread_id')} {stats.get('instance') or ''}".strip()
    vol = stats.get("dvol_20d")
    if vol is None or not isinstance(vol, (int, float)) or math.isnan(vol) or vol <= 0:
        return None, f"research has no 20-day daily vol for {label} (run {stats.get('asof')})"
    if unit is not None and _unit_norm(unit) != _unit_norm(stats.get("unit")):
        return None, f"units differ: the move is in {unit}, the research statistics in {stats.get('unit')}"
    return m / float(vol), ""


# ---------------------------------------------------------------------------- history
def _empty(reason: str, name: str, **attrs) -> pd.Series:
    s = pd.Series([], index=pd.DatetimeIndex([], name="date"), dtype=float, name=name)
    s.attrs.update(reason=reason, label=LABEL, **attrs)
    return s


def research_spread_history(key: Tuple[str, object], start=None, end=None,
                            db_path: Union[str, Path, None] = None) -> pd.Series:
    """The spread's daily value from `spread_daily`, in its unit, `start` to `end` inclusive (None =
    open): a float Series on a DatetimeIndex named 'date'. attrs: reason ('' when it has data),
    label 'research', path, spread_id, instance, unit, spread_name. Empty with the reason otherwise."""
    try:
        sid, inst = _norm_key(key)
    except ValueError as exc:
        return _empty(str(exc), str(key), path="")
    name = f"{sid} {inst}".strip()
    bounds = []
    for v in (start, end):
        if v is None:
            bounds.append(None)
            continue
        iso, bad = _iso(v)
        if iso is None:
            return _empty(f"history window: {bad}", name, path="", spread_id=sid, instance=inst)
        bounds.append(iso)
    db, _, why = _find(db_path)
    if db is None:
        return _empty(why, name, path="", spread_id=sid, instance=inst)
    base = dict(path=str(db), spread_id=sid, instance=inst)
    try:
        conn = _connect(db)
        try:
            missing = _check_tables(conn)
            if missing:
                return _empty(f"{db} is {missing}", name, **base)
            d = conn.execute("SELECT unit, name FROM spread_def WHERE spread_id = ?", (sid,)).fetchone()
            if d is None:
                return _empty(f"spread {sid} is not in the research universe ({db}){_calendar_hint(conn, sid)}",
                              name, **base)
            sql = "SELECT date, value FROM spread_daily WHERE spread_id = ? AND instance = ?"
            params: list = [sid, inst]
            if bounds[0]:
                sql += " AND date >= ?"
                params.append(bounds[0])
            if bounds[1]:
                sql += " AND date <= ?"
                params.append(bounds[1])
            rows = conn.execute(sql + " ORDER BY date", params).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- a failed read is a reason, never a crash
        return _empty(f"{db} could not be read ({type(exc).__name__}: {exc})", name, **base)
    base.update(unit=d[0], spread_name=d[1])
    if not rows:
        window = f" from {bounds[0] or 'the start'} to {bounds[1] or 'the end'}" if any(bounds) else ""
        return _empty(f"the research app has no history for {name}{window}", name, **base)
    s = pd.Series([float(r[1]) for r in rows], index=pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows]), name="date"),
                  dtype=float, name=name)
    s.attrs.update(reason="", label=LABEL, **base)
    return s
