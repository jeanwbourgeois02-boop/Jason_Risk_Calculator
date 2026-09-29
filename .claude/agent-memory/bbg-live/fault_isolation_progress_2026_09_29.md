---
name: fault-isolation-progress-2026-09-29
description: Phase G (2026-09-29) pull_once rebuilt as guarded steps - chunked marks requests, split retry, timeout give-up, per-step status["steps"], live status["progress"], connected rule, lock release, LiveFeed backfill hand-off; old tests it flips
metadata:
  type: project
---

User, 2026-09-29 (Phase G "Smooth and contained"): "if something pulls badly it doesnt crash
everything - but of course most important is to make sure that everything pulls correctly";
loading smooth, no waiting.

**Shape now:** pull_once runs PULL_STEPS (contract_dates, requests, spot, forwards, futures,
write_marks, lme, curves, vol, options, ledger) through `_run`: a raise is recorded
(status["steps"], status["step_errors"][step] = {reason, traceback}, a warning "<label>
stopped: ..."), rolled back, and the next step runs; success commits (`_release_lock`). The
ledger runs on every connected path now, the no-requests one and the no-session one included.
Marks steps ask in chunks (`REQUEST_CHUNK` spot 10 / forwards 5 / futures 10 tickers,
`_ask_in_chunks`); a non-timeout raise is retried ticker by ticker; after
TIMEOUTS_BEFORE_GIVING_UP (2) timeouts in a row the rest fail "not asked". contract_dates_step
and _lme_step split the same way (per ticker / per metal).

**Connected rule:** session open failed -> connected False, "pull failed: <Type>: <msg>", recalc +
ledger (`_SharedSession` opens once per cycle, then raises SessionUnavailable). Session opened,
something raised and nothing landed (marks, curve points, LME, contract dates, OIS quotes, vol)
-> connected False, "pull failed: <first reason with a traceback>". Otherwise connected True.

**Progress:** `set_progress` rewrites only status["progress"] under _STATUS_LOCK (so
ui/feed_controls.status_fingerprint does not move mid-pull), skipped when no status file.
Keys: running, started_at, updated_at, step, step_label, step_index, step_count, done, total
(marks of the request list), sentence; final adds finished_at, outcome, final_sentence.

**Why it matters later:** old tests pinned the abort (fetch_reference raising "boom" ->
connected False). Flipped where something else landed: test_live.py
`test_pull_once_status_timings...` (forwards answer {} -> still False, ok) and the "later"
case in the recalc test (~line 1729: forwards land -> now connected True). Owed re-pins,
not run (user: no tests until the site is final).

**How to apply:** a new step goes through `_run` with a `judge` or a `_step_outcome` branch and
a STEP_LABELS entry; never call Bloomberg while a write is uncommitted.
