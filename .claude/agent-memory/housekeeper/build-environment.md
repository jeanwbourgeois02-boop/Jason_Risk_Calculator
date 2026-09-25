---
name: build-environment
description: How to run Python and pytest on this Windows machine (no `python` on PATH; use `py -3`), what is installed (pandas, pytest, dash 4.4.1; no blpapi), current suite baseline (2026-09-25: 2398 passed, 21 environmental failures), and why `git diff` misses new files
metadata:
  type: project
---

Run Python as `py -3` (3.14.7). `python` is NOT on PATH in Git Bash. pandas 3.0.5 preinstalled; pytest 9.1.1 pip-installed 2026-09-13; dash 4.4.1 pip-installed 2026-09-14 (registers a `dash` pytest plugin). No requirements file exists yet. blpapi is NOT installed and cannot be installed here: Bloomberg lives on a separate machine with no Claude Code and no repo access, so `data/bloomberg/pull_marks.py` must stay a single repo-import-free file (copied over, run there, marks come back as the canonical marks CSV loaded by `marks_csv.load_marks_csv`). Tests fake blpapi via `sys.modules`. Full suite: `py -3 -m pytest tests/ -v`. Package imports (`from data.ingest import ...`) resolve under pytest via the `__init__.py` tree; standalone scripts need `PYTHONPATH=C:\Users\jeanw\Jason Risk Monitor`. UI run: `py -3 -m ui.app` (DB path from env `RISK_DB`, default `data/raw/risk.db`).

**Suite baseline 2026-09-25** (after the screens redesign, commit e420741): 2398 passed, 7 failed, 14 errors, 1 skipped, about 8-13 min. The 21 red are environmental on this PC and are the baseline: no pyarrow (all of `tests/test_risk.py`, 4 failed + 14 errors; `tests/test_ui_risk.py::test_the_rendered_tab_shows_the_engines_numbers_end_to_end`; `tests/test_risk_cli.py::test_doctor_passes_on_schema_database`) and blpapi installed (`tests/test_live.py::test_availability_no_blpapi_never_touches_the_socket`). Anything else red is real. The golden book passes on its 2026-09-25 re-pin (ef93a90).

Older history. Known state as of 2026-09-14 (after aggregate.py / views.py / Cash ladder tab, uncommitted at time of writing): 155 passed (2026-09-14 pm, after data/load.py CLI and ui/tabs/pnl.py landed), 0 failed (one harmless pandas/dash deprecation warning). Earlier baselines: 115 after the `NY` fix, 116 after tzdata. Any failure in `tests/test_bloomberg.py` is now a real regression, not the old pre-existing `NameError: NY` (open-questions item 38, resolved).

**Why:** every specialist prompt must include the interpreter name or the agent wastes a turn discovering it; the suite baseline must be known so failures are attributed to the task in flight, not to history.

**How to apply:** put "use `py -3`, pytest installed" in every specialist prompt. Expect a harmless `PytestCacheWarning` (cannot create `.pytest_cache` in repo root, WinError 5). `.gitignore` only has `data/raw/`; `__pycache__/` and `.pytest_cache/` are not ignored (user-owned file, flagged to user 2026-09-13). When a specialist creates NEW files, `git diff` shows nothing (untracked): give the reviewer the absolute paths explicitly and tell it to use `git status --short` to confirm scope. See [[feedback-spawn-process]].
