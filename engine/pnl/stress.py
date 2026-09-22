"""Stress scenarios on the ladder's per-currency USD delta. docs/BUILD_PLAN.md section 4.

Pure arithmetic on a `ccy -> USD delta` dict (e.g. `engine.ladder.exposure.build_exposure
(...).summary` reduced to `{currency: usd_delta}`) plus an optional separate futures USD
delta line (e.g. ES). No correlations, no vol -- just delta x move.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Optional

import yaml

_DEFAULT_STRESS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "stress.yaml"


def move_1pct(delta_by_ccy: Mapping[str, float]) -> Dict[str, float]:
    """Per-currency P&L if that currency strengthens 1% against USD: delta x 0.01."""
    return {ccy: float(delta) * 0.01 for ccy, delta in delta_by_ccy.items()}


def load_scenarios(path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """Named scenarios from config/stress.yaml: {scenario_name: {ccy: pct_move}}.
    Missing file -> empty dict (no scenarios), never an error."""
    p = Path(path) if path is not None else _DEFAULT_STRESS_CONFIG
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    return {name: {ccy: float(pct) for ccy, pct in moves.items()} for name, moves in data.items()}


EQUITY_KEY = "EQUITY"


def futures_pct_by_scenario(scenarios: Mapping[str, Mapping[str, float]]) -> Dict[str, float]:
    """The equity-index move per scenario, read from the scenario's EQUITY key (a pct
    applied to every open equity future's USD delta). Scenarios without the key are
    omitted, so their futures cells stay blank rather than zero."""
    return {name: float(moves[EQUITY_KEY]) for name, moves in scenarios.items() if EQUITY_KEY in moves}


def fx_moves(moves: Mapping[str, float]) -> Dict[str, float]:
    """Scenario moves without the EQUITY key, for currency arithmetic."""
    return {ccy: pct for ccy, pct in moves.items() if ccy != EQUITY_KEY}


def apply_scenario(delta_by_ccy: Mapping[str, float], moves: Mapping[str, float],
                    futures_usd_delta: float = 0.0, futures_pct: float = 0.0) -> dict:
    """Scenario P&L = sum(delta_ccy * pct) over currencies named in `moves`, plus an ES
    (or other futures) line valued at `futures_usd_delta * futures_pct`. Currencies in
    `moves` that are absent from `delta_by_ccy` contribute 0 (no position, no P&L)."""
    fx_pnl = {ccy: float(delta_by_ccy.get(ccy, 0.0)) * float(pct) for ccy, pct in fx_moves(moves).items()}
    futures_pnl = float(futures_usd_delta) * float(futures_pct)
    return {"fx_pnl": fx_pnl, "fx_total": sum(fx_pnl.values()), "futures_pnl": futures_pnl,
            "total": sum(fx_pnl.values()) + futures_pnl}


def run_scenarios(delta_by_ccy: Mapping[str, float], scenarios: Mapping[str, Mapping[str, float]],
                   futures_usd_delta: float = 0.0, futures_pct_by_scenario: Optional[Mapping[str, float]] = None) -> Dict[str, dict]:
    """apply_scenario for every named scenario in `scenarios` (e.g. from load_scenarios)."""
    futures_pct_by_scenario = futures_pct_by_scenario or {}
    return {name: apply_scenario(delta_by_ccy, moves, futures_usd_delta, futures_pct_by_scenario.get(name, 0.0))
            for name, moves in scenarios.items()}
