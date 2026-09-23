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
- Run ui tests: `py -3 -m pytest tests/test_ui.py -v`.
- Run full suite: `py -3 -m pytest tests/ -v`.
- As of 2026-09-14 (first ui/ session), `tests/test_bloomberg.py` had 13 pre-existing failures
  unrelated to ui/ (`NameError: name 'NY' is not defined` in `data/bloomberg/pull_marks.py`, owned
  by bbg-data). By the second session (same day, cash-ladder-tab + pnl-tab work), bbg-data had
  fixed it -- full suite (155 tests: bloomberg/ingest/ladder/pnl/ui) passed clean. Don't assume the
  bloomberg failures are still there; re-run the full suite to check current state rather than
  trusting this note.
- Dash 4.4.1 callback_map entries are decorated; to call the raw callback function directly in a
  test (bypassing dash's `_initialize_context`/`outputs_list` machinery, which only works via the
  dispatcher) use `app.callback_map["<id>.<prop>"]["callback"].__wrapped__(*args)`. Calling the
  entry directly raises `KeyError: 'outputs_list'`.
- 2026-09-23: a second working copy exists at `C:\Users\jeanw\Jason Risk Monitor` (spaces in the path: quote it).
  There Python 3.14 is the only interpreter and `ruff` is NOT installed (`py -3 -m ruff` -> No module named
  ruff; no venv, no `uvx`/`pipx`), so the ruff step of a brief cannot run: say so in the report, compile-check
  with `py -3 -m py_compile` and eyeball F-rules (unused imports/names) by hand instead. Use `-p no:cacheprovider`.
- `data/raw/new_sample_trades.csv` IS on disk here, so the skipif'd end-to-end ladder test
  (`test_spec_worked_example_aud_as_of_2026_09_17_through_the_tab`) runs on this PC but was skipped on the PC
  that committed a9a2227; a test skipped where a commit was made can drift silently -- check both copies.
