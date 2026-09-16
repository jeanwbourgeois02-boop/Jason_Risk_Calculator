---
name: diagnostics-module-naming
description: Bloomberg diagnostic file-soup was consolidated 2026-09-16; know the current layout and the two housekeeper follow-ups still open.
metadata:
  type: project
---

Consolidated 2026-09-16 (was four confusingly similar files, see git history for the old
names). Current layout:

1. `tools/bbg_diagnostics.py` — THE canonical in-app diagnostics implementation:
   `run_bloomberg_diagnostics(db_path=None, as_of=None, host=None, port=None) -> list[dict]`.
   Checks session connectivity, official-source mapping vs CLAUDE.md, FX/futures/IRS mark
   coverage, snapped_at offsets, last live pull. Imports freely from the repo.
2. `data/bloomberg/bbg_diagnostics.py` — one-line shim re-exporting (1). Kept (not merged
   away) only because `ui/tabs/header.py` does a lazy import at
   `data.bloomberg.bbg_diagnostics.run_bloomberg_diagnostics`; this is ui-shell's contract,
   not ours to change without coordinating there. Single shim, not shim-of-a-shim.
3. `tools/bloomberg_terminal_probe.py` (renamed from `tools/bloomberg_diagnostic.py`) —
   the fully standalone/portable script with zero repo imports, meant to be copied alone
   onto the Bloomberg terminal machine. Kept separate from (1) deliberately: portability
   is load-bearing, not redundant with the in-app tool.
4. `data/bloomberg/pull_report.py` (renamed from `data/bloomberg/diagnose.py`) — unrelated
   feature: renders `pull_marks.py`'s `.diag.json` output (probe results, open-questions
   27-31 evidence). Not a connectivity checker. `pull_marks.py`'s own docstring/comments
   were updated to point at the new name.

**Still open / reported to housekeeper 2026-09-16, not fixed here (outside data/bloomberg/
and tests/test_bloomberg.py):**
- `risk.py` line ~413 (`doctor --bloomberg`) still shells out to the old path
  `tools/bloomberg_diagnostic.py`; needs updating to `tools/bloomberg_terminal_probe.py`.
- `tests/test_bloomberg_diagnostic.py` (not owned by bbg-data) still does
  `importlib.util.spec_from_file_location(..., "tools/bloomberg_diagnostic.py")` and now
  fails with FileNotFoundError; needs updating to the new filename. Confirmed this is the
  *only* place the rename broke tests outside tests/test_bloomberg.py.

`tests/test_bloomberg.py`'s own diagnose-module tests were updated in place to
`from data.bloomberg import pull_report as diagnose` (alias kept to minimize churn in the
~40 call sites); all 76 tests in tests/test_bloomberg.py pass post-rename.

**How to apply:** if asked to touch "the Bloomberg diagnostic" again, the four files above
are it — no fifth file should ever be added. If `tools/bloomberg_terminal_probe.py`'s
zero-repo-import property is ever violated by an edit, that's a bug: it must keep running
standalone with only that one file + blpapi + the SQLite DB.
