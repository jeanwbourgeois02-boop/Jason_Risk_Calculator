---
name: warmup-waits-for-pull-2026-09-30
description: The screens warm-up never runs while a Bloomberg pull or backfill runs; the poll notes it owed and runs it once the status says finished
metadata:
  type: project
---

Since 2026-09-30 (user: the pull is slow and wasteful; bbg-live found the warm-up re-valuing all
15 steps on every revision the pull's commits published) the poll's warm-up waits for the pull.

- `ui/warmup.py::pull_busy(db)`: status file `progress.running` or `backfill.running`, fresh within
  `STALE_BUSY_SECONDS` (15 min, same as `ui/diagnostics_runner.STALE_PULL_SECONDS`). The top-level
  `backfill` block has no timestamp: aged by `progress.backfill.updated_at`, then `progress.updated_at`,
  then the status file's mtime.
- `after_write` (the poll's publish, via `revision.warm_screens(get_db_path, after_write=True)`) only
  sets `_STATE["owed"]` while busy; `warm_if_owed` runs on every poll tick that publishes nothing
  (no file read when nothing is owed). `_request` clears owed. The worker stops rerunning (and sets
  owed) when the database changed under it because a pull is writing.
- Uploads and book switches (`revision.warm_screens(get_db_path)`, `after_change`) and `start` still
  warm at once.

**Why:** the warm-up fought the backfill for the CPU and the SQLite lock during a press.
**How to apply:** any new warm trigger that fires on data writes goes through `after_write`, never
`after_change`. The owed run needs an open browser page (the poll is per page).
