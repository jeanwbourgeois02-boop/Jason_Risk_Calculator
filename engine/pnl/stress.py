"""Stress scenarios on the ladder's per-currency USD delta. docs/BUILD_PLAN.md section 4.

Pure arithmetic on a `ccy -> USD delta` dict (e.g. `engine.ladder.exposure.build_exposure
(...).summary` reduced to `{currency: usd_delta}`). No correlations, no vol -- just delta x
move. The Ladder's stress block and the Risk tab's scenarios both read it.

2026-09-24 (commodity conversion, Phase 2, user yes): the equity index left the app, and
with it the scenarios' EQUITY move and the separate futures line (`futures_usd_delta`,
`futures_pct`, `futures_pct_by_scenario`). A scenario is currency moves only; an EQUITY key
still in `config/stress.yaml` is ignored, and a scenario that moved nothing else is not
loaded. Commodity scenarios arrive in Phase 4 (commodity-stress).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Optional

import yaml

_DEFAULT_STRESS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "stress.yaml"

# Keys a scenario may still carry from the macro book that are not currencies. Ignored.
_RETIRED_KEYS = frozenset({"EQUITY"})


def move_1pct(delta_by_ccy: Mapping[str, float]) -> Dict[str, float]:
    """Per-currency P&L if that currency strengthens 1% against USD: delta x 0.01."""
    return {ccy: float(delta) * 0.01 for ccy, delta in delta_by_ccy.items()}


def _currency_moves(moves: Optional[Mapping[str, float]]) -> Dict[str, float]:
    """`moves` without the retired non-currency keys, as floats."""
    return {ccy: float(pct) for ccy, pct in (moves or {}).items() if ccy not in _RETIRED_KEYS}


def load_scenarios(path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """Named scenarios from config/stress.yaml: {scenario_name: {ccy: pct_move}}, in the
    file's order. Missing file -> empty dict (no scenarios), never an error. An EQUITY key
    is ignored; a scenario left with no currency move (it moved the equity index only) is
    left out rather than shown as a column of zeros."""
    p = Path(path) if path is not None else _DEFAULT_STRESS_CONFIG
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: Dict[str, Dict[str, float]] = {}
    for name, moves in data.items():
        ccy_moves = _currency_moves(moves)
        if ccy_moves:
            out[name] = ccy_moves
    return out


def apply_scenario(delta_by_ccy: Mapping[str, float], moves: Mapping[str, float]) -> dict:
    """Scenario P&L = sum(delta_ccy * pct) over the currencies named in `moves`:
    {"fx_pnl": {ccy: pnl}, "fx_total": sum, "total": sum}. Currencies in `moves` absent
    from `delta_by_ccy` contribute 0 (no position, no P&L); an EQUITY key is ignored."""
    fx_pnl = {ccy: float(delta_by_ccy.get(ccy, 0.0)) * pct for ccy, pct in _currency_moves(moves).items()}
    fx_total = sum(fx_pnl.values())
    return {"fx_pnl": fx_pnl, "fx_total": fx_total, "total": fx_total}


def run_scenarios(delta_by_ccy: Mapping[str, float],
                  scenarios: Mapping[str, Mapping[str, float]]) -> Dict[str, dict]:
    """apply_scenario for every named scenario in `scenarios` (e.g. from load_scenarios)."""
    return {name: apply_scenario(delta_by_ccy, moves) for name, moves in scenarios.items()}
