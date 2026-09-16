---
name: review-findings-upload-blotter-2026-09-16
description: 2026-09-16 two-pass review of the upload control — pass 1 dual-format (BNP+blotter), pass 2 after user pivot to blotter-only. No criticals in either pass.
metadata:
  type: project
---

**Pass 1 (dual-format, superseded)**: reviewed `detect_format`/`import_blotter` dispatch in
`data/ingest/upload.py` and `ui/uploads.py`'s FORMAT_STORE_ID wiring. No criticals;
transactional stage-then-publish pattern verified safe (same as `import_report`).

**Pass 2 (blotter-only pivot, final for this task)**: user confirmed mid-task the upload
control must be blotter-only, no BNP support in the UI. Re-reviewed the diff:

- `data/ingest/upload.py`: `detect_format` and dual-dispatch fully removed.
  `import_blotter(payload, filename, db_path, sheet=None)` is the sole UI entry point;
  checks `BLOTTER_REQUIRED = {'Status','Fund','Fin Type','Trade Id','Symbol'}` and raises a
  clean `ValueError` naming missing columns for a non-blotter file — verified live against
  the real `data/raw/HA_PNL_20260818.csv` (BNP file fed to `import_blotter` raises
  `ValueError: This file is not a trade blotter. Missing columns: Fin Type, Status, Trade
  Id`, does not silently succeed with 0 rows). `import_report`/`read_report`/
  `BNP_REQUIRED`/`suggested_date` untouched; `REQUIRED = BNP_REQUIRED` kept as a back-compat
  alias. `data/load.py` CLI and `2_launcher.py`'s `sample` command both still call
  `import_report` directly and are unaffected.
- `ui/uploads.py`: full rewrite, no date picker, no `detect_format`/`FORMAT_STORE_ID`/
  `suggested_date` calls anywhere (grepped repo-wide, zero hits). `_confirm` calls
  `import_blotter(decode(contents), filename, db_path)` directly, no `as_of` gating.
  `describe_source()` correctly stays DB-content-based (reads `ui.app.load_summary()`), not
  upload-path-based, so it remains accurate even if `positions` is non-empty from an
  out-of-band CLI-driven BNP load. No dead code, no broken Output/State wiring found.
- `tests/test_upload.py`, `tests/test_ui.py`: `detect_format` references fully removed
  (zero grep hits), BNP-path tests (`import_report`-based) untouched and still pass.
- `docs/open-questions.md` item 59 (housekeeper's claim that cash-ladder's cash-balance
  column has no in-app data source since UI never calls BNP loading): **verified accurate**
  against `engine/ladder/ladder.py`'s `cash_ladder()` — it reads CASH rows from `positions`
  where `source='BNP'`, written only by `data.ingest.bnp.load`/`import_report`, which the UI
  no longer calls. Not overstated or understated.

Pytest: 494 passed, 1 skipped, confirmed independently by direct run on 2026-09-16
(matches both specialists' reported counts).

**Recurring pattern worth remembering**: this repo's "must not silently succeed with wrong
data" bar applies to format-rejection too, not just P&L math — always spot-check a
format-detection ValueError path against a real malformed file from `data/raw/`, not just
synthetic test fixtures, since column-signature checks are easy to get right in unit tests
but wrong against real file quirks (e.g. actual column name casing/whitespace).
