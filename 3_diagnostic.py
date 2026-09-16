#!/usr/bin/env python
"""3_diagnostic.py -- the third root-level file (with 1_setup.cmd and 2_launcher.py):
run the Bloomberg diagnostics checks from the command line.

Thin wrapper only. The real implementation lives in tools/bbg_diagnostics.py, the same
module ui/tabs/header.py's "Check Bloomberg connection" button calls (via the
data/bloomberg/bbg_diagnostics.py shim) -- so there is exactly one diagnostics
implementation, never a second copy sitting at root. This file exists purely so the
check is a visible, directly runnable entry point instead of being buried in tools/.

    py 3_diagnostic.py
    py 3_diagnostic.py --db data\\raw\\risk.db --as-of 2026-09-16 --json

Checks: Bloomberg session connectivity, official-source mapping vs CLAUDE.md, FX/futures
mark coverage, IRS/OIS curve coverage, snapped_at offsets, last live feed pull. See
tools/bbg_diagnostics.py's docstring for the full list.

For the fully standalone, zero-repo-import script meant to be copied alone onto the
Bloomberg terminal machine (no app/database wiring needed), use
tools/bloomberg_terminal_probe.py instead -- also what `py 2_launcher.py doctor --bloomberg`
runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bbg_diagnostics import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
