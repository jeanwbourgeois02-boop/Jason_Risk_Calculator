"""The commodity stress scenarios: read ``config/commodity_stress.yaml`` and check each one.

A scenario is plain data (the file's header documents every kind); nothing here is per
commodity. ``load_scenarios`` returns the scenarios in file order, each normalised to one
shape, and raises ``ScenarioError`` naming the scenario and the fault for anything it cannot
use: an unknown kind, a move that is not a number, or a sector / subsector / root / exchange
that is not in ``config/contracts.csv`` (a misspelt name would otherwise move nothing).

Normalised shapes (every scenario also carries ``name``, ``kind``, ``description``):

- ``outright``: ``rules``: ``[{select: {level: [names]}, move, rank}]``, one per named item.
- ``spread``:   ``rules``: one per leg, same shape.
- ``curve``:    ``front``, ``back``, ``front_months``, ``back_months``, ``select`` ({} = all).
- ``fx``:       ``moves``: ``{CCY: move}``.
- ``replay``:   ``start``, ``end`` (ISO dates, start before end).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "commodity_stress.yaml"

KINDS = ("outright", "curve", "spread", "fx", "replay")

# Selector levels; the rank decides which move a position takes when several name it:
# spread > root > template > subsector > family > exchange > sector > all.
# The outright maps take the four universe levels; a spread leg or a curve's select also takes
# the three spread levels, read from spreads-engine's book_spreads (open spreads only).
LEVELS = ("root", "subsector", "exchange", "sector")
SPREAD_LEVELS = ("spread", "template", "family")
SELECT_LEVELS = SPREAD_LEVELS + LEVELS
RANK = {"spread": 8, "root": 7, "template": 6, "subsector": 5, "family": 4, "exchange": 3, "sector": 2,
        "all": 1}

# A configured move is a fraction; beyond this it is almost surely a percentage typed as a
# whole number (-5 for -5 %). Negative WTI was -3.06, so the bound leaves room for that.
MAX_ABS_MOVE = 5.0

DEFAULT_FRONT_MONTHS = 1.0
DEFAULT_BACK_MONTHS = 12.0


class ScenarioError(ValueError):
    """A scenario the engine cannot use, with its name and the fault."""


def _universe_names() -> Dict[str, set]:
    """{level: names} from contract-master's universe, for checking names (read only)."""
    from data.contracts import load_roots

    roots = load_roots()
    known = {
        "root": set(roots),
        "subsector": {r.subsector for r in roots.values()},
        "exchange": {r.exchange for r in roots.values()},
        "sector": {r.sector for r in roots.values()},
    }
    try:   # spreads-engine's templates, for the template and family names (read only)
        from engine.spreads import CALENDAR, load_templates

        templates, _problems = load_templates()
        known["template"] = {t.template_id for t in templates}
        known["family"] = {CALENDAR} | {t.family for t in templates if t.family}
    except Exception:   # templates unreadable: those two levels are not checked, never refused
        pass
    return known


def _move(value, where: str) -> float:
    if isinstance(value, bool):
        raise ScenarioError(f"{where}: move {value!r} is not a number")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ScenarioError(f"{where}: move {value!r} is not a number") from None
    if not math.isfinite(x):
        raise ScenarioError(f"{where}: move {value!r} is not a finite number")
    if abs(x) > MAX_ABS_MOVE:
        raise ScenarioError(f"{where}: move {x:g} is beyond +/-{MAX_ABS_MOVE:g}; moves are fractions "
                            f"(-0.05 = -5 %)")
    return x


def norm(level: str, name) -> str:
    """A selector name as it is compared: roots and exchanges upper case without spaces, spread
    ids as written (trimmed), everything else lower case."""
    text = str(name or "").strip()
    if level in ("root", "exchange"):
        return re.sub(r"\s+", "", text).upper()
    return text if level == "spread" else text.lower()


_key = norm


def _check_names(level: str, names: Iterable[str], known: Optional[Dict[str, set]], where: str) -> None:
    if known is None or level not in known:   # spread ids belong to the book, not checked here
        return
    pool = {(_key(level, n)) for n in known[level]}
    bad = [n for n in names if _key(level, n) not in pool]
    if bad:
        source = "config/spreads/" if level in SPREAD_LEVELS else "config/contracts.csv"
        raise ScenarioError(f"{where}: {level} {', '.join(map(repr, bad))} not in {source}")


def _select(raw, known, where: str) -> Dict[str, List[str]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: select must be a mapping of level to a list of names")
    out: Dict[str, List[str]] = {}
    for level, names in raw.items():
        if level not in SELECT_LEVELS:
            raise ScenarioError(f"{where}: select level {level!r} is not one of {', '.join(SELECT_LEVELS)}")
        names = [names] if isinstance(names, str) else list(names or [])
        if not names:
            raise ScenarioError(f"{where}: select {level} names nothing")
        _check_names(level, names, known, where)
        out[level] = [_key(level, n) for n in names]
    return out


def _rank(select: Dict[str, List[str]]) -> int:
    return max((RANK[level] for level in select), default=RANK["all"])


def _iso(value, where: str) -> str:
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value.isoformat()
    try:
        return dt.date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ScenarioError(f"{where}: {value!r} is not a date (YYYY-MM-DD)") from None


def validate_scenario(raw: dict, curve_defaults: Optional[dict] = None,
                      known: Optional[Dict[str, set]] = None) -> dict:
    """One scenario from the file, normalised (see the module docstring), or ScenarioError.
    ``known`` is the universe's names per level (``_universe_names()``); None skips that check."""
    if not isinstance(raw, dict):
        raise ScenarioError(f"a scenario must be a mapping, got {raw!r}")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ScenarioError(f"a scenario has no name: {raw!r}")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ScenarioError(f"{name}: kind {raw.get('kind')!r} is not one of {', '.join(KINDS)}")
    out = {"name": name, "kind": kind, "description": str(raw.get("description") or "").strip()}

    if kind == "outright":
        rules = []
        if "all" in raw:
            rules.append({"select": {}, "move": _move(raw["all"], f"{name}: all"), "rank": RANK["all"]})
        for level in LEVELS:
            items = raw.get(level)
            if items is None:
                continue
            if not isinstance(items, dict) or not items:
                raise ScenarioError(f"{name}: {level} must map names to moves")
            _check_names(level, items, known, name)
            for item, value in items.items():
                rules.append({"select": {level: [_key(level, item)]},
                              "move": _move(value, f"{name}: {level} {item}"), "rank": RANK[level]})
        if not rules:
            raise ScenarioError(f"{name}: an outright scenario needs all, sector, subsector, exchange or root")
        out["rules"] = rules

    elif kind == "spread":
        legs = raw.get("legs")
        if not isinstance(legs, list) or len(legs) < 2:
            raise ScenarioError(f"{name}: a spread scenario needs at least two legs")
        rules = []
        for i, leg in enumerate(legs, 1):
            where = f"{name}: leg {i}"
            if not isinstance(leg, dict) or "move" not in leg:
                raise ScenarioError(f"{where}: needs a select and a move")
            select = _select(leg.get("select"), known, where)
            if not select:
                raise ScenarioError(f"{where}: select names nothing")
            rules.append({"select": select, "move": _move(leg["move"], where), "rank": _rank(select)})
        out["rules"] = rules

    elif kind == "curve":
        defaults = curve_defaults or {}
        for side in ("front", "back"):
            if side not in raw:
                raise ScenarioError(f"{name}: a curve scenario needs {side}")
        front_m = raw.get("front_months", defaults.get("front_months", DEFAULT_FRONT_MONTHS))
        back_m = raw.get("back_months", defaults.get("back_months", DEFAULT_BACK_MONTHS))
        try:
            front_m, back_m = float(front_m), float(back_m)
        except (TypeError, ValueError):
            raise ScenarioError(f"{name}: front_months and back_months must be numbers") from None
        if not (math.isfinite(front_m) and math.isfinite(back_m)) or front_m < 0 or back_m <= front_m:
            raise ScenarioError(f"{name}: need 0 <= front_months < back_months "
                                f"(got {front_m:g} and {back_m:g})")
        out.update(front=_move(raw["front"], f"{name}: front"), back=_move(raw["back"], f"{name}: back"),
                   front_months=front_m, back_months=back_m,
                   select=_select(raw.get("select"), known, name))

    elif kind == "fx":
        moves = raw.get("moves")
        if not isinstance(moves, dict) or not moves:
            raise ScenarioError(f"{name}: an fx scenario needs moves, a map of currency to move")
        clean = {}
        for ccy, value in moves.items():
            code = str(ccy or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", code) or code == "USD":
                raise ScenarioError(f"{name}: {ccy!r} is not a non-USD currency code")
            clean[code] = _move(value, f"{name}: {code}")
        out["moves"] = clean

    else:  # replay
        start = _iso(raw.get("start"), f"{name}: start")
        end = _iso(raw.get("end"), f"{name}: end")
        if start >= end:
            raise ScenarioError(f"{name}: start {start} must be before end {end}")
        out.update(start=start, end=end)
    return out


def validate_scenarios(raw_list, curve_defaults: Optional[dict] = None, check_names: bool = True) -> List[dict]:
    """A list of raw scenarios, normalised, names unique."""
    if not isinstance(raw_list, list):
        raise ScenarioError("scenarios must be a list")
    known = _universe_names() if check_names else None
    out, seen = [], set()
    for raw in raw_list:
        s = validate_scenario(raw, curve_defaults, known)
        if s["name"] in seen:
            raise ScenarioError(f"{s['name']}: the name is used twice")
        seen.add(s["name"])
        out.append(s)
    return out


def load_scenarios(path: Optional[Path] = None) -> List[dict]:
    """Every scenario of ``config/commodity_stress.yaml`` (or ``path``), in file order."""
    p = Path(path) if path is not None else DEFAULT_CONFIG
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ScenarioError(f"{p}: expected a mapping with a 'scenarios' list")
    defaults = data.get("curve_defaults") or {}
    if not isinstance(defaults, dict):
        raise ScenarioError(f"{p}: curve_defaults must be a mapping")
    return validate_scenarios(data.get("scenarios") or [], defaults)
