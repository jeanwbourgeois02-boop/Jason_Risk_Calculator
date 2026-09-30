---
name: pull-speed-2026-09-30
description: 2026-09-30 "the bloomberg pull is quite slow and looks wasteful" - backfill.py's speed work: lent session, the listing memo and its fingerprint, the closing-ledger skip and why after_last_day stays, one status read per progress publish, connect() writes the DB
metadata:
  type: project
---

User at the Bloomberg PC, 2026-09-30: "the bloomberg pull is quite slow and looks wasteful".
bbg-live traced the press ([[pull-speed-2026-09-30]] in bbg-live's memory); its requests to
this lane, done behaviour-neutral (same marks, sources, stamps, days, requests, ledger table,
status keys; proved by a before/after harness):

- **Lent session:** `start_auto_backfill(..., session=(session, service))` -> `auto_backfill`
  -> `backfill(session=)`. Takes precedence over `session_factory`, never stopped here, skips
  the availability probe, still a real pull (snapshot saved). Returns the thread (lender stops
  the session after join) or None (lock busy / no Terminal: session unused).
- **Listing memo** (`_listed_days`, `_listing_memo`): each past day's close_completeness row
  kept while its fingerprint holds: per day COUNT/MAX(rowid)/TOTAL(value) of marks (+ TOTAL
  julianday(settle_date), for apply_contract_dates' in-place UPDATE), vol_quotes, curve_quotes;
  per book state_version, bbg_library_state, bbg_library and instruments shape, config
  contracts.csv / holidays.txt / calendars stats. Changed days recomputed per `_runs` stretch.
  **Blind spot:** a rule changed in memory without touching those (a test monkeypatching
  library / contracts functions between two runs on one db) - clear `_listing_memo` there.
- `_record_outcome`: one close_completeness per stretch of worked days, not per day (NOT one
  call over min..max: the YTD reference date would make that span a year).
- **Closing ledger skip** (`_closing_step_needed`): only with a lent session, 0 rows written by
  backfill() (`_run_writes`, conn.total_changes), the pull's status ledger dated today with no
  error/skipped, library not dirty, no upload_report.uploaded_at >= the pull's status "time".
  The skipped step is still recorded as "closing" with the no-op call's block
  (`_pull_ledger_repeat`), so an older run's re-freeze never shows as this one's.
- **after_last_day NOT skipped:** it runs at span_end (a past day), the closing at today; a
  future expiring on span_end is re-frozen at its close only by the today call. Not the
  "same date and marks" bbg-live assumed.
- `_BackfillProgress._publish`: one read under `live._STATUS_LOCK`, writes with
  `live._replace_status_file` (bbg-live privates; a rename there silently stops progress).
- **`schema.connect` writes the DB every call** (views dropped/recreated: 4 transactions, mtime
  moves -> every screen cache re-values). The listing uses `_listing_connection` (plain connect
  when the views exist), so a press with nothing due now leaves risk.db untouched. The general
  fix is ingest-schema's (requested 2026-09-30).

**How to apply:** harness = scratch copy of `git show HEAD:data/bloomberg/backfill.py` loaded as
another module, fake fetch / fwd / fut / quote / scale (tests/test_auto_backfill.py shape), a
simulated pull (realise_settled + status "ledger"/"time"), 4 presses on copies of the sample db
and data/raw/risk.db, comparing results, fetch calls, marks/realised/quotes tables, status block.
