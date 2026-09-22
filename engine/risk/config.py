"""The Risk tab's parameters: `config/risk.yaml`, with the nm-dashboard's own constants
(`fx_alpha/core/risk.py`) as the defaults when the file is missing, the way
`engine/pnl/stress.py::load_scenarios` treats `config/stress.yaml`.

Keys (see the yaml for the meaning of each):
  vol_target_usd, stress_pct, blended {trail_window_bd, w_trail, w_stress, stress_start,
  stress_end, cutover}, var_window_bd, var_confidence, worst_day_start,
  shock_dates [{date, name}, ...].
`load_config` returns a plain dict of those, dates as ISO strings, plus `file` (the path
read) and `loaded` (False when the defaults are in use).
"""
from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "risk.yaml"

DEFAULTS: Dict[str, Any] = {
    "vol_target_usd": 4_500_000.0,          # docs/open-questions.md item 15: vol allocation 4.5m on 35m capital
    "stress_pct": 50.0,                     # core/risk.py STRESS_FRACTION = 0.50
    "blended": {
        "trail_window_bd": 500,             # BLENDED_TRAIL_WIN
        "w_trail": 2.0 / 3.0,               # BLENDED_W_TRAIL
        "w_stress": 1.0 / 3.0,              # BLENDED_W_STRESS
        "stress_start": "2008-01-01",       # BLENDED_STRESS_START
        "stress_end": "2010-12-31",         # BLENDED_STRESS_END
        "cutover": "2011-01-01",            # BLENDED_CUTOVER
    },
    "var_window_bd": 252,
    "var_confidence": 0.95,
    "worst_day_start": "2008-01-01",        # dashboard.py `_worst_start`
    "shock_dates": [                        # core/risk.py STRESS_IGNORE_DATES
        {"date": "2015-01-15", "name": "SNB floor removal"},
        {"date": "2016-06-24", "name": "Brexit referendum result"},
    ],
}


def _iso(value: Any) -> str:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _merged(file_values: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    for key, value in (file_values or {}).items():
        if key == "blended" and isinstance(value, dict):
            for k, v in value.items():
                cfg["blended"][k] = _iso(v) if k in ("stress_start", "stress_end", "cutover") else v
        elif key == "shock_dates":
            cfg["shock_dates"] = [{"date": _iso(d.get("date", "")), "name": str(d.get("name", ""))}
                                  for d in (value or []) if isinstance(d, dict)]
        elif key == "worst_day_start":
            cfg[key] = _iso(value)
        else:
            cfg[key] = value
    # numbers as numbers, whatever the yaml spelled
    cfg["vol_target_usd"] = float(cfg["vol_target_usd"])
    cfg["stress_pct"] = float(cfg["stress_pct"])
    cfg["var_window_bd"] = int(cfg["var_window_bd"])
    cfg["var_confidence"] = float(cfg["var_confidence"])
    b = cfg["blended"]
    b["trail_window_bd"] = int(b["trail_window_bd"])
    b["w_trail"] = float(b["w_trail"])
    b["w_stress"] = float(b["w_stress"])
    return cfg


def load_config(path: Union[str, Path, None] = None) -> Dict[str, Any]:
    """The parameters from `path` (default `config/risk.yaml`) over `DEFAULTS`. A missing
    or unreadable file gives the defaults, with `loaded = False` and the reason in
    `note`; never an error."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    note = ""
    values: Optional[Dict[str, Any]] = None
    if p.is_file():
        try:
            values = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(values, dict):
                note = f"{p} is not a mapping: defaults in use"
                values = None
        except Exception as exc:  # noqa: BLE001 -- a bad file is a note, never a crash
            note = f"{p} could not be read ({type(exc).__name__}: {exc}): defaults in use"
    else:
        note = f"{p} not found: defaults in use"
    cfg = _merged(values or {})
    cfg["file"] = str(p)
    cfg["loaded"] = values is not None
    cfg["note"] = note
    return cfg
