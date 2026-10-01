---
name: ledger-once-per-press-2026-10-01
description: 2026-10-01 user yes "the ledger runs up to three times per press" - auto run makes ONE realise_settled(today) at its end, unconditionally; LEDGER_AT_END read by bbg-live; standalone backfill() keeps its calls
metadata:
  type: project
---

User approved 2026-10-01: one ledger call per "Pull Bloomberg now" (was live call + backfill's
after_last_day + closing). Orchestration only, no ledger change.

- `backfill(..., ledger=True)`: auto_backfill passes `ledger=False` (no per-day, no after_last_day);
  standalone / CLI / tests keep the default and freeze as before.
- `auto_backfill`'s finally ALWAYS calls `_realise_after_backfill(db, today)` (no trades, nothing due,
  nothing written, listing or backfill raised). `_closing_step_needed`, `_pull_ledger_repeat`,
  `_run_writes` deleted. `_ledger_ran[key]` is set by the closing step; `start_auto_backfill._run`
  makes the call itself if auto_backfill died before it.
- `LEDGER_AT_END = True` is bbg-live's contract: it drops its own call when start_auto_backfill
  returns a thread; None (lock busy / no Terminal) means no ledger call was made.
- `_record_ledger` now starts the status block afresh on every call (steps = [that call]).
- Why one call at today is enough: ledger looks at every row with settle < as_of and freezes from
  marks on/before each trade's own settlement, never the as-of; re-freeze converges. Proved on a
  scratch harness (HEAD module vs new, FX book with a live-row freeze re-frozen at the close, two
  presses): realised_pnl identical, frozen_at kept/set identically, marks identical.

**How to apply:** never reintroduce a ledger call inside the auto run before its end, or a skip of
the closing step - bbg-live no longer freezes on a press whose auto-backfill runs.
