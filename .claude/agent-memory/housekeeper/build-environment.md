---
name: build-environment
description: How to run Python and pytest on this Windows machine (no `python` on PATH; use `py -3`), what is installed, and known pytest cache warning
metadata:
  type: project
---

Run Python as `py -3` (3.14.7). `python` is NOT on PATH in Git Bash. pandas 3.0.5 preinstalled; pytest 9.1.1 was pip-installed on 2026-09-13. No requirements file exists yet. Tests: `py -3 -m pytest c:\Users\jeanw\risk-monitor\tests\test_ingest.py -v`. Package imports (`from data.ingest import ...`) resolve under pytest via the `__init__.py` tree; standalone scripts need `PYTHONPATH=c:\Users\jeanw\risk-monitor`.

**Why:** every specialist prompt must include the interpreter name or the agent wastes a turn discovering it.

**How to apply:** put "use `py -3`, pytest installed" in every specialist prompt. Expect a harmless `PytestCacheWarning` (cannot create `.pytest_cache` in repo root, WinError 5). `.gitignore` only has `data/raw/`; `__pycache__/` and `.pytest_cache/` are not ignored (user-owned file, flagged to user 2026-09-13).
