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

**Both 2026-09-16 housekeeper follow-ups below were confirmed FIXED by 2026-09-17** (checked
while investigating the stale-empty-pull bug, see [[bloomberg_feed_diagnostics_trap_2026_09_17]]):
`2_launcher.py` (renamed from `risk.py`) `doctor --bloomberg` now calls
`tools/bloomberg_terminal_probe.py`, and `tests/test_bloomberg_diagnostic.py` loads that same
current filename. No action needed; keeping the entries below for history.
- ~~`risk.py` line ~413 (`doctor --bloomberg`) still shells out to the old path
  `tools/bloomberg_diagnostic.py`; needs updating to `tools/bloomberg_terminal_probe.py`.~~ FIXED.
- ~~`tests/test_bloomberg_diagnostic.py` (not owned by bbg-data) still does
  `importlib.util.spec_from_file_location(..., "tools/bloomberg_diagnostic.py")` and now
  fails with FileNotFoundError; needs updating to the new filename.~~ FIXED.

**New still-open item, found 2026-09-17, reported to housekeeper, not fixed here (outside
data/bloomberg/ and tests/test_bloomberg.py):** `tools/bbg_diagnostics.py::check_last_pull`
(lines ~448-469) reports "Last marks pull" PASS whenever `status.get("connected") and not
status.get("failed")`, with no check on whether `requested == 0` is actually still accurate
right now. This is the check the user actually sees in the app (the in-app diagnostics
button imports the real module via the shim, never the ui/tabs/market_data.py placeholder —
confirmed by tests/test_ui.py::test_bbg_diagnostics_entry_point_prefers_real_module...).
bbg-data added exactly the fix this check needs at
`data.bloomberg.inventory.stale_empty_pull_reason(conn, status, as_of)` (returns None when
trustworthy, else a plain-English reason) and already used it in the
ui/tabs/market_data.py placeholder as a stopgap, but the module that actually runs in
production (tools/bbg_diagnostics.py) is outside bbg-data's edit scope. See
[[bloomberg_feed_diagnostics_trap_2026_09_17]] for the exact patch to apply there.

`tests/test_bloomberg.py`'s own diagnose-module tests were updated in place to
`from data.bloomberg import pull_report as diagnose` (alias kept to minimize churn in the
~40 call sites); all 76 tests in tests/test_bloomberg.py pass post-rename.

**How to apply:** if asked to touch "the Bloomberg diagnostic" again, the four files above
are it — no fifth file should ever be added. If `tools/bloomberg_terminal_probe.py`'s
zero-repo-import property is ever violated by an edit, that's a bug: it must keep running
standalone with only that one file + blpapi + the SQLite DB.
