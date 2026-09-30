---
name: pull-speed-2026-09-30
description: 2026-09-30 "the bloomberg pull is quite slow and looks wasteful" - where a press's time goes (session opens, round trips, backfill, snapshot, warm-up), what bbg-live changed (chunks 50, one library read, quiet steps), requests left with other lanes, the before/after harness
metadata:
  type: project
---

User at the Bloomberg PC, Jason's real book (20 futures on 11 roots, 2 LME metals / 3 prompts,
USDCNH + USDCNY conversion spots, no FX forward or option, contract dates all stored).

**Where a press's time goes (from the code; the cycle's own local work is under 1 s):**
the pull opens one blpapi session (start + //blp/refdata, ~1-3 s), then spot 1 request,
futures 1 request (2 before, chunks of 10), LME 1 request (its time sits under
timings["forwards"]). The backfill, in its own thread, opens a SECOND session, re-checks
every day since the first trade (close_completeness), asks 3 history requests, runs
realise_settled twice more, re-checks each worked day, then the snapshot rewrites the whole
marks table to CSV. Each commit moves risk.db's mtime, so the screens' warm-up (same process,
GIL) re-values everything, up to 3 reruns while the backfill writes.

**Done in live.py (behaviour-neutral, proved identical marks/items/steps/blocks on the dev
book and the sample):** REQUEST_CHUNK spot/futures 10 -> 50; `needed_live` = one
`library.needed_on(include_unrequestable=True)` per cycle, handed to build_requests,
not_requestable_futures, _lme_step, _curves_step(ccys=), _vol_step(pairs=) (all optional,
None = read as before); `_run(quiet=True)` / `_Progress.step(publish=False)` for a step known
empty; add_done / set_total skip no-op rewrites (19 -> 12 status writes on Jason's book).

**Left as Requests (2026-09-30):** backfill borrows the press's session; skip the closing
ledger when no day was worked; stop re-checking complete days; close_completeness one library
read per call (bbg-library); snapshot only when marks changed / incrementally (bbg-snapshot);
no warm-up while status progress is running (ui). Once the backfill takes a session, LiveFeed
keeps the pull's _SharedSession open until the backfill thread ends, then stops it.

**Session lent to the backfill (landed later 2026-09-30):** `pull_once(lend_session=[])`
appends its `_SharedSession` instead of stopping it only when it opened cleanly, the body ran
to its end and the pull is connected (an all-raised pull still stops its own). `LiveFeed._backfill`
passes `session=(s.session, s.service)` to `start_auto_backfill`; None or a raise -> stop at
once in the feed thread; a thread -> watcher thread "bloomberg-session-return" joins it and
stops (also when the backfill thread raises). backfill.py reads `status["ledger"]`,
`status["time"]`, `_STATUS_LOCK`, `_replace_status_file`, `status_path`, `PROGRESS_KEY`:
never rename them. Harness: scratchpad lend_harness.py pattern (patch pm.open_session,
live.availability, backfill.start_auto_backfill; RISK_SNAPSHOT=0; log thread names).

**How to apply:** verify speed work with a harness (fake fetch_reference / fetch_historical /
request_lme_pillars / request_fwd_curves, fixed `_now_iso`, rates_source / vol_source fakes,
count `_replace_status_file`) on a copy of data/raw/risk.db (Jason's real 89 trades, no marks)
and on `ui.sample_book.build_sample_db(<scratch path>)`. Get the "before" code with
`git show HEAD:data/bloomberg/live.py`, NEVER `git stash`: other lanes edit the tree in parallel.
