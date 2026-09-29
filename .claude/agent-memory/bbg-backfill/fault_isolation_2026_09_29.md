---
name: fault-isolation-2026-09-29
description: Phase G (2026-09-29) backfill.py made fault-isolated - _Asks (chunked history requests, split retry, timeout give-up, lock release, not-a-number refusals), guarded stages and day steps, transient-failure retry, status keys errors/requests/not_numbers, _BackfillProgress in status["progress"]; the old tests it flips
metadata:
  type: project
---

User, 2026-09-29 (Phase G "Smooth and contained"): "if something pulls badly it doesnt crash
everything - but of course most important is to make sure that everything pulls correctly".
Mirrors bbg-live's pull_once work ([[fault-isolation-progress-2026-09-29]] in bbg-live's memory).

**Shape now:** every history request goes through one `_Asks` per backfill() call:
`asks.series(kind, fetch, ..., key_of=)` packs a key's tickers (a pair's smile or tenors, a
currency's OIS curve, a metal's pillars, a future) into chunks of HISTORY_CHUNK = 50 (chosen so
one pair's 45-ticker smile and the USD+JPY OIS set stay one request, as pinned tests expect);
a non-timeout raise re-asks ticker by ticker; live.TIMEOUTS_BEFORE_GIVING_UP (2) timeouts in a
row -> "not asked: ..." for everything after (the per-day FX closes are asked in pass 1, AFTER
the fwd/futures/LME/vol/OIS history, so they are "not asked" too once it gives up); commit
before every request. Failure reasons are stored per (kind, key, stretch) and read back with
`asks.reason(kind, key, day)`; kinds spot/fwd/future/lme/vol/ois/scale, plus "fwd_refused"
(every tenor of a pair-day was not a number). Values: `_number` = fwd_curve.finite_number
(bbg-curves' helper), wording `fwd_curve.NOT_A_NUMBER`; tenor rows are filtered BEFORE
historical_curve (a NaN would crash `_infer_points_scale`'s log10). `_write_closes(...,
rejected=)` is the last guard.

**Day status:** a day with any step raised (closes, forwards, inputs, options, ledger) is ERROR,
`error` = first reason, `step_errors` = all; the day's other steps still run (before, a raise in
the forwards skipped pricing, and a raising per-day realise_settled left DONE with a flag).

**Retry:** `is_transient(reason)` (request failed / not asked / stopped / run raised / no
session) -> `_day_state[...]["transient"]` (sidecar too) -> due on the next press whatever the
day's age. Without it an older day hit by one timeout would wait until its signature changed.

**Status:** backfill block gains errors, error_count, requests, not_numbers, not_number_count;
"reason" = `failure_sentence(report)` (contains "failed") when anything failed, else ''.
`_run_reports[db]` reset at the start of auto_backfill. `_BackfillProgress` rewrites
status["progress"] (pull's block kept, running True, phase "backfill", sentence "Backfilling
closes: n of m days · <stage>", sub-block ["backfill"]); final sentence = pull's final_sentence
+ " · past closes: ..."; skips when a newer pull's block (another started_at) is there.

**Old tests this flips (owed re-pins, not run - user: no tests until the site is final):**
test_backfill_options.py::test_skipped_no_closes_and_error_days_carry_the_option_keys (ERROR
case now prices options: events not [], step_errors set); test_auto_backfill.py::
test_start_auto_backfill_run_that_raises_says_failed_in_its_reason (reason assertion holds;
the day is now worked, not "not reached yet").

**How to apply:** a new history kind goes through `asks.series` with a key_of and an
ASK_LABELS entry; a new day step gets its own try + `_failed(step, exc, words)`. Scratch
verifier approach: fake fetchers raising ValueError (poison) / BloombergRequestError(...,
"TIMEOUT", ...) and a second sqlite connection doing BEGIN IMMEDIATE with timeout=0 inside the
fake fetch to prove no write lock is held.
