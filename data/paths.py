"""Where the app's database lives.

The one rule for the database path, below every layer: RISK_DB when set (relative to the
repo root when not absolute), else data/raw/risk.db. `ui.app` re-exports these names, so
`ui.app.get_db_path` is still the name every screen and test uses; the Bloomberg CLI mains
(`data/bloomberg/backfill.py`, `data/bloomberg/live.py`) and `tools/bbg_diagnostics.py`
read it from here so that `data/` never imports `ui/`.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "raw" / "risk.db"


def get_db_path() -> Path:
    """Resolve the database path from RISK_DB, defaulting to data/raw/risk.db."""
    raw = os.environ.get("RISK_DB")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (REPO_ROOT / p)
    return DEFAULT_DB_PATH
