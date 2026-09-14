---
name: environment
description: How to run Python/pytest for ui/ in this repo's Git Bash shell, and dash install status
metadata:
  type: project
---

- No `python` on PATH in Git Bash; use `py -3` (Python 3.14.7 launcher). pytest 9.1.1, pandas preinstalled.
- `dash` was NOT preinstalled as of 2026-09-14; installed via `py -3 -m pip install dash` -> dash 4.4.1
  (pulls in Flask, plotly, pydantic, etc). Tests must still `pytest.importorskip("dash", reason=...)` so
  the suite degrades gracefully in environments where install isn't possible/allowed.
- Run ui tests: `py -3 -m pytest C:\Users\jeanw\risk-monitor\tests\test_ui.py -v`.
- Run full suite: `py -3 -m pytest C:\Users\jeanw\risk-monitor\tests\ -v`.
- As of 2026-09-14, `tests/test_bloomberg.py` has 13 pre-existing failures unrelated to ui/
  (`NameError: name 'NY' is not defined` in `data/bloomberg/pull_marks.py`, owned by bbg-data).
  This is NOT caused by ui/ changes -- verified via `git status` showing no edits outside ui/ and
  tests/test_ui.py before running. Don't be alarmed by these; report to housekeeper, don't fix
  (outside ui-shell's owned directories).
