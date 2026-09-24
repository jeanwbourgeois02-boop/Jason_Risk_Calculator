"""The spread templates: ``config/spreads/<sector>.yaml`` (contract-master's copy of the research
app's spread definitions), read here and turned into what the grouping rule needs.

A template leg's size in the spread's own quantity unit is worked out from the contract root and
the template, never from a number written in this code:

    units per lot = contract_size x quantity_factor(size_unit -> quote unit's quantity)
                    x (the leg's ``qty_factor`` when the template gives one, else
                       quantity_factor(quote unit's quantity -> spread unit's quantity))

``qty_factor`` is the research app's own convention ("spread-quantity units per one
leg-quantity unit": 7.45 bbl per t of gasoil, 33.3333 bushels per short ton of meal);
``quantity_factor`` is contract-master's generic unit table (mass, volume, energy), so a
same-dimension conversion (42000 gal of RBOB into 1000 bbl, 25000 lb of COMEX copper into
11.34 t) needs nothing per commodity. A leg that can be converted neither way leaves its
template out, with the reason, only when that template's roots are in the book.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from data.contracts import ContractRoot, load_roots
from data.contracts.universe import quantity_factor

SPREADS_DIR = Path(__file__).resolve().parents[2] / "config" / "spreads"


@dataclass(frozen=True)
class TemplateLeg:
    root_id: str
    weight: float                  # the template's weight: + = long in a long spread
    qty_factor: Optional[float]    # the template's own conversion, None when it gives none
    units_per_lot: Optional[float] # one lot in the spread's quantity unit; None = not convertible
    problem: str = ""              # why units_per_lot is None


@dataclass(frozen=True)
class Template:
    template_id: str               # 'bench.crude.brent_vs_wti'
    name: str
    family: str                    # benchmark | processing | substitution
    sector: str
    unit: str                      # the spread's price unit, 'USD/bbl'
    quantity_unit: str             # its quantity, 'bbl'
    legs: Tuple[TemplateLeg, ...]
    source: str                    # 'energy.yaml'
    order: int                     # position over every file, files in name order: the label tie-break

    @property
    def roots(self) -> Tuple[str, ...]:
        return tuple(leg.root_id for leg in self.legs)

    @property
    def usable(self) -> bool:
        return all(leg.units_per_lot is not None for leg in self.legs)

    @property
    def problem(self) -> str:
        return "; ".join(leg.problem for leg in self.legs if leg.problem)


def quote_quantity_unit(root: ContractRoot) -> str:
    """'bbl' for USD/bbl, 'gal' for USD/gal: the quantity the root's price is quoted per."""
    return root.quote_unit.partition("/")[2].strip().lower()


def lot_in_quote_units(root: ContractRoot) -> float:
    """One lot in the quantity its price is quoted per (40,000 lb of hogs = 400 cwt)."""
    return root.contract_size * quantity_factor(root.size_unit.strip().lower(), quote_quantity_unit(root))


def _leg(raw: dict, spread_qty: str, roots: Dict[str, ContractRoot], where: str) -> TemplateLeg:
    root_id = str(raw.get("instrument", "")).strip()
    weight = float(raw["weight"])
    if weight == 0:
        raise ValueError(f"{where}: leg {root_id} has weight 0")
    qf = raw.get("qty_factor")
    qf = None if qf is None else float(qf)
    root = roots.get(root_id)
    if root is None:
        raise ValueError(f"{where}: leg {root_id!r} is not a contract root in config/contracts.csv")
    try:
        per_lot = lot_in_quote_units(root)
        per_lot *= qf if qf is not None else quantity_factor(quote_quantity_unit(root), spread_qty)
    except ValueError as exc:
        return TemplateLeg(root_id, weight, qf, None,
                           f"leg {root_id} is quoted per {quote_quantity_unit(root)!r} and the spread per "
                           f"{spread_qty!r}, with no qty_factor in the template ({exc})")
    return TemplateLeg(root_id, weight, qf, per_lot)


@functools.lru_cache(maxsize=4)
def _load(folder: str, stamp: Tuple[Tuple[str, float], ...]) -> Tuple[Tuple[Template, ...], Tuple[str, ...]]:
    roots = load_roots()
    templates: List[Template] = []
    problems: List[str] = []
    order = 0
    for name, _mtime in stamp:
        path = Path(folder) / name
        try:
            entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"config/spreads/{name} could not be read ({exc}); its templates are not used")
            continue
        for n, raw in enumerate(entries if isinstance(entries, list) else []):
            where = f"config/spreads/{name} entry {n + 1} ({raw.get('id', '?') if isinstance(raw, dict) else '?'})"
            try:
                unit = str(raw["unit"]).strip()
                spread_qty = unit.partition("/")[2].strip().lower()
                legs = tuple(_leg(leg, spread_qty, roots, where) for leg in raw["legs"])
                if len(legs) < 2:
                    raise ValueError(f"{where}: a spread needs two legs or more")
                templates.append(Template(
                    template_id=str(raw["id"]), name=str(raw.get("name", raw["id"])),
                    family=str(raw.get("family", "")), sector=str(raw.get("sector", "")),
                    unit=unit, quantity_unit=spread_qty, legs=legs, source=name, order=order))
                order += 1
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"{exc if isinstance(exc, ValueError) else f'{where}: missing or bad field {exc}'}; "
                                f"template not used")
    return tuple(templates), tuple(problems)


def load_templates(folder: Optional[Path] = None) -> Tuple[Tuple[Template, ...], Tuple[str, ...]]:
    """(templates, problems): every template of ``config/spreads/*.yaml`` (files in name order,
    entries in file order), and one sentence per entry or file that could not be used. Cached
    until a file changes."""
    folder = Path(folder or SPREADS_DIR)
    if not folder.is_dir():
        return (), (f"{folder} not found: no spread templates, so only calendars and bundles are grouped",)
    stamp = tuple(sorted((p.name, p.stat().st_mtime) for p in folder.glob("*.yaml")))
    return _load(str(folder), stamp)
