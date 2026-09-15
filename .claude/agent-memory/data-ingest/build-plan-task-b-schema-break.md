---
name: build-plan-task-b-schema-break
description: trades.theme column and pnl_snapshots removal (BUILD_PLAN task B) break hardcoded 13-column trades INSERTs and pnl_snapshots reads in tests/modules outside data/ingest
metadata:
  type: project
---

2026-09-15: per docs/BUILD_PLAN.md section 6 Task B, data-ingest added `trades.theme`
(14th column) and `instrument_theme` table, and stopped creating `pnl_snapshots` in
`data/ingest/schema.py::create_schema` (retired per section 3, "pnl_snapshots is
retired"). This is an intentionally breaking, authorised schema change.

**Why:** `value_book`/ledger rework (task A) and the swap/theme/futures-fill work
(task B) both need it; BUILD_PLAN explicitly told data-ingest to remove the DDL and
told pnl-engine to stop writing it.

**How to apply:** any file with a hardcoded `INSERT INTO trades VALUES (13 values)` or
a read against `pnl_snapshots` breaks after this change. As of 2026-09-15 the still-broken,
not-yet-migrated tests (outside data-ingest's ownership, so left unfixed by design) are:
`tests/test_bloomberg_diagnostic.py`, `tests/test_cash_ladder_parity.py`,
`tests/test_excel_parity.py`, `tests/test_exposure_adapter.py`, `tests/test_ladder.py`,
`tests/test_risk_cli.py`, `tests/test_workbook_rates.py`, plus a couple of
`tests/test_ledger.py` cases. `tests/test_ui.py`, `tests/test_pnl.py` (engine's tests)
were already updated to 14-column inserts by the pnl-engine agent by the time this was
checked. Do not "fix" these by editing outside `data/ingest/` or `tests/test_ingest.py` —
report the break to the housekeeper/owning agent instead. See also
[[idempotent-loader]] and [[bnp-file-quirks]] for other ingest quirks.
