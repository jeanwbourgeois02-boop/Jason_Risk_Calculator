---
name: backfill-retry-rule-persisted-2026-09-22
description: 2026-09-22 "yes do part 2" - auto_backfill's retry rule (hourly only for the last 3 business days and never for a day whose only failures are tickers Bloomberg rejects), per-day state persisted in <db>.backfill_state.json with a version stamp (state_version), the "waiting_on_tickers" status sentence; traps (monotonic vs wall clock, NO_CLOSES reason classification, shared-tree commit sweep).
metadata:
  type: project
---

User decision 2026-09-22 ("yes do part 2"): on the Bloomberg PC 43 past days could never
complete (USDBRLSP / USDIDRSP / USDTWDSP "Unknown/Invalid security", "returned no forward
tenor prices" for the same pairs, "returned no PX_SETTLE" for SPX/E261016C7615 / P7615,
"returned no fixing (PX_LAST)" for USDKRW / USDTWD) and were re-asked on every press after a
restart or an hour: ~20 min of requests for nothing. Follows
[[history-inputs-and-futures-settle-2026-09-22]].

## The rule now (data/bloomberg/backfill.py, auto-backfill section)
- Due when: no state; `state["version"] != state_version()`; signature changed; OR the day is
  in `recent_business_days(today)` (last RECENT_BUSINESS_DAYS=3 bd on config/holidays.txt)
  AND not `rejected_only` AND RETRY_SECONDS passed. An older day is NEVER retried by time.
- `is_rejection(reason)`: REJECTION_TEXTS substrings, case-insensitive ("unknown/invalid
  security", "unknown security", "invalid security", "invalid field", "field not valid").
  Bloomberg's text reaches the reason verbatim via pull_marks.fetch_intraday_close_series
  ("Bloomberg answered the intraday request for <t> with: <said>") -> backfill's
  `spot_reason = getattr(fetch, "reason", None)`. A test injects it with a callable object
  carrying a `.reason(ticker, day)` staticmethod (`_RejectingFetch` in test_auto_backfill.py).
- `_failure_reasons(result, still_missing)` is the UNTRUNCATED classification basis (unlike
  `_plain_reasons`, the status file's 5). For NO_CLOSES it uses the pairs' own reasons, not
  the holiday sentence, so an all-tickers-rejected day is `rejected_only`, a holiday is not.
- State entry keys: at (caller's clock), tried_at (wall ISO), version, signature, status,
  missing_count, missing, rejected (labels via `_reason_label`), rejected_only.
- Persistence: `_state_path(db)` = `<db>.backfill_state.json` (sidecar next to the status
  file; no schema.py table - that is data-ingest's). `_load_state` runs at the start of
  auto_backfill only when `_day_state` has no entry for that db; `_save_state` on every
  `_record_outcome` (finally, so also after a raise and after a "nothing due" run).
- `state_version()` = LIBRARY_VERSION + "+" + sha1[:12] of NDF_1M_TICKERS, NDF_FIX_TICKERS,
  _tenor_tickers("USDBRL", STANDARD_TENORS), vm.vol_ticker over VOL_TENORS x VOL_QUOTE_TYPES,
  rm.ois_curve(ccy) (ticker, field) per OIS_INDEX ccy. Any change there re-asks every day.
- Status block key "waiting_on_tickers" (also the log line): "N day(s) wait on tickers
  Bloomberg rejects (t1, ..., at most 5); asked again when the ticker list changes; M older
  day(s) got nothing more from Bloomberg's history; ...; K day(s) within the last 3 business
  days are tried again within the hour". Rebuilt in `_record_outcome` over every incomplete
  day (post-run), so the block is right after the first press. "days" shape untouched.
  ui-market-data does not show it yet (their lane).

## Follow-up the same day (user, explicit): NDF tenor families + PX_LAST for FUTURE_PX history
- `pull_marks.NDF_TENOR_FAMILIES = {"BRL": "BCN", "IDR": "IHN", "TWD": "NTN"}` and
  `pull_marks.tenor_ticker(pair, tenor)`: USD pair of a family -> `<family><tenor> Curncy`, SP ->
  '' (spot is the first pillar; `fwd_curve.historical_curve` already puts spot at the spot
  date for POINTS). KRW / INR / deliverables keep `<pair><tenor> Curncy`. `backfill._tenor_tickers`
  drops '' tenors. Only the 1M of each family is terminal-verified. The live tenor fallback
  `fetch_tenor_points` still uses the pair spelling (NOT changed: it needs the SP SETTLE_DT).
- Scale: `_fetch_points_scales` still asks `USDBRL Curncy` (FWD_SCALE 4 on the PC; 0 for
  USDIDR / USDTWD, i.e. points in full units). `tenor_unit` classifies BRL/IDR/TWD points fine
  (values far outside [0.5, 2] x spot).
- FUTURE_PX history = daily PX_LAST for futures AND listed options (user: "all futures for past
  date pnl calculation, use px last"; no PX_SETTLE history for 'SPX US 10/16/26 P7615 Index').
  Stamp unchanged: `settle_stamp` 17:00 NY. Reason text "Bloomberg returned no PX_LAST for <id>
  on <day>". The LIVE pull's PX_LAST-then-latest-PX_SETTLE fallback (tests in test_live /
  test_bloomberg) is untouched. CLAUDE.md "Futures keep PX_SETTLE" is now stale (housekeeper).
- `state_version()` digests NDF_TENOR_FAMILIES, tenor tickers of USDJPY + each family pair and
  "future_px_field": "PX_LAST", so the PC's 43 stuck days are re-asked once with the new tickers.
- LIBRARY_VERSION "2026-09-22.2" (NDF_FIX_TICKERS: KRW KOBRUSD Index, TWD TRY11 Index).

## Traps
- `clock` is monotonic (or a test's fake) and means nothing across restarts: `tried_at` is
  wall-clock, and `_load_state` sets `at = clock() - elapsed_since_tried_at`. A test that
  clears `_day_state` and sets a fresh clock gets `at ~= clock()` (elapsed ~0).
- A day within the 3-bd window with a mix of rejected + "returned no" failures IS retried
  hourly (day-level rule; the backfill cannot skip one ticker inside a day).
- Shared working tree: another session's `git commit` ("big changes", 64a7e9e) swept my
  uncommitted backfill.py edits into its commit mid-task. Check `git diff HEAD` before
  reporting "uncommitted"; explicit-path staging by others does not protect my files.
