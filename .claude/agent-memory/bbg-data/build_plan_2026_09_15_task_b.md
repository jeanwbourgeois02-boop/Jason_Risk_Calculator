---
name: build-plan-2026-09-15-task-b
description: BUILD_PLAN.md Task B (bbg-data) decisions - live.py/backfill.py rewrite, new inventory.py/manual.py, and the trades.theme schema-churn pitfall
metadata:
  type: project
---

On 2026-09-15 the user authorised docs/BUILD_PLAN.md, superseding the 2026-09-14
"literal workbook arithmetic" correction for the headline calc (see [[data_quirks_bnp_marks]]
for BNP-file quirks, still valid). Task B (bbg-data) was executed:

- `data/bloomberg/live.py::build_requests` now requests one SPOT + one FWD_OUTRIGHT per
  open FX leg's *own* settle_date (no more shared `WORKDAY(as_of,5)` maturity request),
  plus one FUTURE_PX per open FUTURE instrument at its own expiry. `pull_once` now also
  fetches FUTURE_PX via `pull_marks.build_future_rows` (PX_SETTLE, source BBG_BDH).
- `data/bloomberg/backfill.py` no longer writes `pnl_snapshots`; it only writes official
  SPOT marks per business day and then calls `engine.pnl.ledger.realise_settled` if
  importable, wrapped in try/except so an in-progress rewrite of `engine/pnl/ledger.py`
  (owned by the pnl-engine task) never stops marks from being written. `backfill()`
  results now carry `realised`/`unrealisable` instead of `complete`/`missing`.
- New `data/bloomberg/inventory.py`: `mark_inventory(conn, as_of)` (OFFICIAL/INTERP/
  MANUAL/MISSING per needed mark) and `close_completeness(conn, start, end)` (needed vs
  present official SPOT count per business day, for traded USD FX pairs).
- New `data/bloomberg/manual.py::write_manual_mark` — MANUAL rows are visible on Market
  data but only official for DELTA/PREMIUM; never touches `marks_official`.
- `data/bloomberg/marks_csv.py::export_request` was deliberately left untouched: it still
  emits the single `WORKDAY(as_of,5)` FWD_OUTRIGHT request because it feeds the
  Reconciliation tab's workbook comparison, which BUILD_PLAN.md explicitly keeps as a
  separate, unchanged view. Don't "fix" it to match live.py's per-settle-date behaviour.

Pitfall hit mid-task: `data/ingest/` (owned by a concurrent agent) added a `theme TEXT`
column to `trades` (now 14 columns) and extended `realised_pnl` to 14 columns while
`engine/pnl/ledger.py` (owned by yet another concurrent agent) still did an old 12-column
insert into `realised_pnl` — a real cross-team bug, not mine to fix. Any test that seeds
`trades` with a positional `INSERT INTO trades VALUES (...)` needs a trailing `''` (or a
named `theme` value) or it errors "table trades has 14 columns but N values were
supplied". When calling into another task's not-yet-finished module from a guarded path
(e.g. `realise_settled`), wrap the *call* in try/except too, not just the import — an
ImportError guard alone does not protect against a live schema-mismatch bug in the
imported function.
