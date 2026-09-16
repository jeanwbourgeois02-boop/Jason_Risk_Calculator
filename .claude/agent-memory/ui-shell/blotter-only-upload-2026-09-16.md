---
name: blotter-only-upload-2026-09-16
description: ui/uploads.py stripped of all BNP/date-picker logic, blotter-only now; positions table permanently empty on a fresh DB, cash-ladder cash-balance panel affected
metadata:
  type: project
---

2026-09-16, same-day reversal of [[dual-format-upload-2026-09-16]]: the user decided the
app's ONE upload control (`ui/uploads.py`) must accept ONLY the trade blotter format --
no BNP path in the UI at all. `data.ingest.upload.detect_format` was deleted entirely by
data-ingest; `import_blotter(payload, filename, db_path, sheet=None)` is now the sole
entry point the UI calls. Rewrote `ui/uploads.py`: removed `FORMAT_STORE_ID`,
`MANUAL_DATE_ID`, `DATE_PICKER_ID`, `SNAPSHOT_TEXT_ID`, `suggested_date` usage, and all
BNP branching in `_selected`/`_confirm`. Flow is now: pick file -> decode() validates
size -> "Confirm insert" -> `import_blotter` -> result line. No date anywhere. Button
label unchanged ("Upload trade file"). `describe_source()` stays format-agnostic
(3-way branch on positions/trades count from `ui.app.load_summary`) since a dev DB can
still carry `positions` rows from outside this control (`data/load.py` CLI still calls
`import_report`/BNP loading, untouched, just not reachable from the UI).

**Real product gap surfaced**: `engine/ladder/ladder.py`'s `cash_ladder()` reads the
CASH-balance side of the ladder from `positions` where `source = 'BNP'`
(`instrument_id LIKE 'CASH-%'`), written only by `data.ingest.bnp.load` (called from
`import_report`). `data/ingest/blotter.py`'s CURRENCY-row handling deliberately writes
no `positions` row (documented in its own module docstring as a data-contract gap).
Since the UI upload control now never calls the BNP loader, a fresh DB populated only
via the UI will have zero `positions` rows forever -- the cash ladder's cash-balance
column will show empty/blank on such a DB. This is a real gap, not a bug I introduced;
flagged to the user rather than fixed (out of scope for `ui/`, the fix belongs in
`data/ingest/blotter.py` or a new positions-writing step, both outside my directory).

Also cleaned `tests/test_upload.py` (owned by data-ingest but its test file, landed on
me per task instructions): removed the `detect_format` import and its 4 now-broken
tests, since `data-ingest`'s agent is scoped away from touching that file.
