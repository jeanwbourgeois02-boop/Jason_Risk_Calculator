---
name: cache-only-new-data-2026-09-30
description: 2026-09-30 "bloomberg needs to store on cache all the data - and only pull any new data" - key-level asks per worked day, hourly retry kept, scale cache, risk-history empty-stretch sidecar, status["backfill"]["cache"]; tests it flips
metadata:
  type: project
---

User, 2026-09-30: "bloomberg needs to store on cache all the data - and only pull any new data".
Audit found backfill() asked a worked day's WHOLE needs (every pair, tenor, future) although
only one key was missing (`_drop_already_official` then threw the stored half away), a recent
incomplete day was re-asked hourly, and the risk history re-asked on every press the pre-listing
head of every contract listed inside the 900-day window and an expired contract's empty tail.

Built (behaviour of marks / sources / stamps unchanged):
- `_wanted_keys(missing_of, work)`: per day from close_completeness `missing`: spot pairs, fwd
  (pair, settle), fwd_pairs (settle > day: tenors needed), future ids, lme roots. Fetchers take
  `wanted=`; pass 1 reads stored close SPOTs (`_close_spots_on_file`) for pairs not asked and
  NO_CLOSES only when nothing asked came back AND nothing on file; pass 1/2 skip items on file.
  overwrite=True asks everything.
- Recent-day retry stays HOURLY (session decision 2026-09-30: Bloomberg can lag a close past the 05:00 HK roll; a retry now asks only the missing keys, so it is cheap). A once-per-book-day rule was built and reverted the same day. `tried_on` (book day) kept in `_day_state` + sidecar, bookkeeping only.
- `_scale_cache` per db per book day, real session only (scale_fetch None).
- Risk history: `<db>.risk_history_state.json` empty stretches {ticker, lo, hi, on, reason,
  rejected}; usable same day, or final when (on - hi) >= 7 days, or rejected ticker.
  `_trim_known_empty`; a late-OI-only ask is dropped. Save failure of rows still re-asks the
  data days (trim leaves the middle).
- `_Asks.note_answer/empties/by_step`, CACHE_STEPS; report["by_step"]; `_listing_stats` in
  `_cache_stats`, updated in `_record_outcome`; `_cache_block` -> status["backfill"]["cache"].

**Owed test re-pins (not run, user: no tests):** any test expecting a complete-SPOT day to
re-ask SPOT when only a forward/future/input is missing.

**How to apply:** scratch harness `dry_run.py` shape: pin live.book_today by assignment, fake
per-day spot + series fetchers recording asks, press repeatedly, delete one mark, move TODAY.
