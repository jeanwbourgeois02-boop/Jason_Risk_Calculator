---
name: build-environment
description: How to run Python and pytest on this Windows machine (no `python` on PATH; use `py -3`), what is installed (pandas, pytest, dash 4.4.1; no blpapi), known pre-existing test failures, and why `git diff` misses new files
metadata:
  type: project
---

Run Python as `py -3` (3.14.7). `python` is NOT on PATH in Git Bash. pandas 3.0.5 preinstalled; pytest 9.1.1 pip-installed 2026-09-13; dash 4.4.1 pip-installed 2026-09-14 (registers a `dash` pytest plugin). No requirements file exists yet. blpapi is NOT installed and cannot be installed here: Bloomberg lives on a separate machine with no Claude Code and no repo access, so `data/bloomberg/pull_marks.py` must stay a single repo-import-free file (copied over, run there, marks come back as the canonical marks CSV loaded by `marks_csv.load_marks_csv`). Tests fake blpapi via `sys.modules`. Full suite: `py -3 -m pytest C:\Users\jeanw\risk-monitor\tests\ -v`. Package imports (`from data.ingest import ...`) resolve under pytest via the `__init__.py` tree; standalone scripts need `PYTHONPATH=c:\Users\jeanw\risk-monitor`. UI run: `py -3 -m ui.app` (DB path from env `RISK_DB`, default `data/raw/risk.db`).

Known state as of 2026-09-14: 102 pass, 13 fail in `tests/test_bloomberg.py`, all from the pre-existing `NameError: NY` at `data/bloomberg/pull_marks.py:277` (commit 750d082; open-questions item 38, queued for bbg-data). Do not report these as regressions.

**Why:** every specialist prompt must include the interpreter name or the agent wastes a turn discovering it; the pre-existing failures otherwise get misattributed to whatever task is in flight.

**How to apply:** put "use `py -3`, pytest installed" in every specialist prompt. Expect a harmless `PytestCacheWarning` (cannot create `.pytest_cache` in repo root, WinError 5). `.gitignore` only has `data/raw/`; `__pycache__/` and `.pytest_cache/` are not ignored (user-owned file, flagged to user 2026-09-13). When a specialist creates NEW files, `git diff` shows nothing (untracked): give the reviewer the absolute paths explicitly and tell it to use `git status --short` to confirm scope. See [[feedback-spawn-process]].
