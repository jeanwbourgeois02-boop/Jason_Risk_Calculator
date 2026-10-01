---
name: pull-speed-2026-10-01
description: 2026-10-01 second speed pass in backfill.py - risk-history requests grouped by alike windows, whole-window empty stands a week, backfill() reuses the listing memo, snapshot skipped when nothing changed, status["backfill"]["seconds"]
metadata:
  type: project
---

User 2026-10-01: "Pull Bloomberg now" slow and may pull too much. Done in backfill.py,
behaviour-neutral for values / sources / stamps (harness: auto_backfill on copies, reuse on vs
off, same results, marks and asks).

- **`_risk_chunks`**: securities share a request only if the union window is at most
  `_window_slack(shortest member)` = max(7 days, 10 % of it) longer than every member's own
  stretch. Greedy over (lo, hi). Before, a 900-day ask made 49 tail asks fetch 900 days.
- **Whole-window empty** (`"whole": true` on the sidecar entry, set when the ask was `fresh`):
  `_usable_empty` keeps it RISK_EMPTY_WHOLE_DAYS (7) after `on` and returns hi = today, so the
  window's new days are covered too (else the slid window re-asks one new day daily). Ticker
  change still drops it (entries keyed per ticker).
- **`backfill(reuse_listing=True)`** (auto_backfill only, and only when today == book_today()):
  reads `_listed_days` instead of close_completeness over the span. `_listed_days` now KEEPS memo
  days outside the asked range while the book fingerprint holds (else a sub-span call would
  evict the rest). A library sync / new instrument inside backfill() moves the global fp -> full
  recompute, so a stale listing is never used.
- **Snapshot skip**: `_market_fingerprint` (marks: COUNT, MAX(rowid), TOTAL(julianday(settle_date));
  price_history: COUNT, MAX(rowid); small tables: every column) vs `_exported_fp[key]` recorded
  at the last successful export, in process only. Equal -> no export, status "snapshot" =
  SNAPSHOT_UNCHANGED. Relies on INSERT OR REPLACE always taking a new rowid (OP_NewRowid before
  the conflict check). In practice rare on the Bloomberg PC: every live press writes today's marks.
- **`status["backfill"]["seconds"]`**: listing, past_closes (absent if no day due), closing,
  bookkeeping (from `_step_seconds`), risk_history, snapshot, total. bbg-live has its own
  top-level status["seconds"] since the same day.

**How to apply:** scratch harness = import tests/test_auto_backfill.py helpers (_db, fakes),
pin live.book_today by assignment, wrap backfill.backfill to force reuse_listing=False for the
"before" run, delete a mark between presses.
