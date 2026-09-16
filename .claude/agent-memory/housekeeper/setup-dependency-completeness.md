---
name: setup-dependency-completeness
description: 2_launcher.py's (formerly risk.py) own dependency verification was incomplete, which is what caused the user's "missing dependency" errors, not the setup mechanism itself
metadata:
  type: project
---

On 2026-09-16 the user reported scattered setup / recurring missing-dependency errors (blpapi,
"json-related"). Investigation found the single-entry-point design (`risk.py` + `requirements.txt`,
`SETUP.cmd` as a polyglot pre-Python bootstrap that just calls `py risk.py setup`, `pnl` PowerShell
function installed into the real profile) was already sound and `pnl` worked correctly end-to-end
when tested live. blpapi was already handled correctly everywhere (lazy-imported, guarded by
try/except, never a hard import at module load). (Later the same day `risk.py` and `SETUP.cmd`
were renamed to `2_launcher.py` and `1_setup.cmd` for file-explorer sort order — see
[[root-simplification-2026-09-16]]; everything below still applies under the new names.)

The real bug: `risk.py`'s own verification steps (`cmd_setup`'s "verify imports" line and
`doctor_checks`' `missing` package loop) only checked `dash, pandas, openpyxl, xlrd, werkzeug,
zoneinfo` — omitting `numpy`, `yaml` (PyYAML), and `QuantLib`, all three of which are imported
directly and unconditionally by `engine/pnl/*.py`, `engine/pnl/stress.py`, and `engine/rates/*.py`.
Confirmed live: this machine's `.venv` had requirements.txt installed but QuantLib was NOT actually
present (`import QuantLib` raised `ModuleNotFoundError`), and `doctor` reported "packages: OK"
anyway — it would only have surfaced as a raw ImportError deep in the Rates tab. Fixed by adding
those three modules to both check lists in `risk.py`, and added `numpy>=2,<3` to `requirements.txt`
explicitly (previously only pulled in transitively via pandas).

**Why:** any package imported directly by application code but absent from risk.py's own
verification list is a silent gap — pip can succeed on `requirements.txt` today and still leave a
genuinely broken environment if a later `pip install` step (e.g. blpapi, or a flaky QuantLib wheel
resolution) partially fails, or if requirements.txt drifts from actual imports over time.

**How to apply:** as of 2026-09-16 `requirements.txt` no longer exists as a source file — the repo
root was radically simplified to three files (now named `1_setup.cmd`, `2_launcher.py`, and
`3_diagnostic.py` — the numeric sort-order rename happened later the same day, see
[[root-simplification-2026-09-16]]) plus `docs/README.md`. `2_launcher.py` now holds two
module-level lists, `PACKAGES` (what `cmd_setup` pip-installs) and `IMPORT_CHECKS` (what
`cmd_setup`'s verify step and `doctor_checks`' `missing` loop both import-check) — both read from
the same place, so there is exactly one list to update, not three. `py 2_launcher.py freeze`
regenerates a `requirements.txt` on disk from `PACKAGES` for `pip install -r` / CI that doesn't go
through `2_launcher.py`; it is git-ignored, never hand-edited, and not required for normal use.
Whenever a new top-level dependency is added anywhere in `engine/`, `data/`, or `ui/`, add it to
both `PACKAGES` and `IMPORT_CHECKS` in `2_launcher.py`. Cross-check with a grep across the codebase
for `^import |^from ` rather than trusting the lists alone — that is how the numpy/yaml/QuantLib gap
was found, and how `plotly` (imported directly in `ui/tabs/market_data.py`, previously only pulled
in transitively via `dash`) and `werkzeug` (imported directly in `ui/launch.py`) were caught in the
same sweep.
