---
name: live-pull-boundary-cases-2026-09-17
description: Three live-Bloomberg-PC bugs found after the first live run -- FWD_OUTRIGHT settling today, FUTURE_PX before the US close, and FX options with no curve/vol pull at all -- root causes and fixes.
metadata:
  type: project
---

Follow-up round after the first real Bloomberg-PC run (2026-09-17 18:27 China = 06:27 New
York): 10/10 SPOT, 15/16 FWD_OUTRIGHT, SOFR curve populated, IRS priced. Three remaining
gaps, all found live (none reproducible on a non-Bloomberg dev box without faking the
scenario) -- useful technique notes below for next time.

## 1. FWD_OUTRIGHT missing when settle_date == as_of

`data/bloomberg/fwd_curve.py::outright_for_date` only interpolates from spot when
`spot_date < target` (strictly before) -- FWD_CURVE's own tenor points start *after*
spot, so a settle date equal to today has no bracket at all and returns `(None, "")`.
Deliberately did NOT touch `outright_for_date` itself (still correct for every other
caller); the fix is in `data/bloomberg/live.py::_fwd_outright_rows`: split requests into
`today_reqs` (`settle_date <= as_of`) marked directly at that pair's live SPOT (source
stays `BBG_BFXFORWARD`, official, detail `"settles today: marked at spot"`) and
`curve_reqs` (`settle_date > as_of`) going through FWD_CURVE as before. `curve_reqs`
being empty skips the whole FWD_CURVE request for that cycle -- verified with an
`AssertionError`-raising fake `request_fwd_curves` in the test that every leg settles
today.

`data/bloomberg/backfill.py` was checked and does NOT touch FWD_OUTRIGHT/fwd_curve at
all (SPOT-only backfill) -- "same in backfill if it shares the path" turned out not to
apply; nothing to change there.

The OK item's `detail` field previously only existed for the FAILED branch;
`pull_once`'s item-status loop now does `hit.get("detail", "")` instead of a hard-coded
`""`, so any row dict can carry an explanatory detail through to `status["items"]` (used
here, and by item 2 below).

## 2. FUTURE_PX missing before the day's US close

`pull_marks.py::build_future_rows` (shared by the live feed and the historical/backfill
CLI) always used `HistoricalDataRequest PX_SETTLE` for `as_of` -- fine for backfill
(always a past, closed date), but a live pull at 06:27 New York asks for a settle price
that doesn't exist until the close, and always failed. Fix: `build_future_rows` gained a
`live: bool = False` parameter (default unchanged, historical/backfill callers
untouched); `live=True` (only `data/bloomberg/live.py`'s call site passes it) tries a
live `ReferenceDataRequest PX_LAST` first, falling back to the latest `PX_SETTLE` on or
before `as_of` only if that's empty. Source stays `BBG_BDH` either way -- CLAUDE.md's
official-source table for FUTURE_PX was never touched, only which underlying Bloomberg
field/request supplies the value.

Implementation note: `fetch_historical` gained an optional `start: Optional[date] =
None` parameter (defaults to `as_of`, i.e. every existing single-day caller is
byte-for-byte unchanged) so the fallback can search a multi-day window
(`lookback_days=7` default) instead of just yesterday. This required changing which
point in the returned `fieldData` array is read: was always index `0` (fine when there's
only one point), now index `fd.numValues() - 1` (Bloomberg returns historical points in
ascending date order, so the last one is the most recent -- and for a single-day range
that's the same element as index 0, so this is a no-op for every unchanged caller).
Verified this exact "must pick the LAST point, not the first" behaviour with a two-point
fake historical response in the test.

## 3. FX option PREMIUM/DELTA: nothing ever pulled a curve or a vol quote for an option-only currency/pair

Three compounding gaps, found by reading `engine/options/inputs.py::resolve_market_inputs`
(engine/options is NOT bbg-data's directory -- read-only, to find out exactly what it
needs rather than guessing):

- **`_rates_step`** only ever collected currencies from open **IRS** trades. An FX option
  needs a domestic AND a foreign OIS discount curve
  (`engine/options/rates.py::resolve_fx_rates`); with no IRS in the book at all (the
  live scenario: EUR/SEK/JPY options, zero IRS trades) nothing was ever pulled for any of
  them, so every option hit `resolve_fx_rates`'s "no curve/rate <CCY>" fallback path with
  nothing to fall back to either (no manual rate entered). Fix: `_rates_step` now unions
  IRS currencies with both currencies of every open FX_OPTION's pair. A currency outside
  the Phase 1 OIS set (`rates_marketdata.OIS_INDEX` -- USD/EUR/GBP/JPY/CHF/CAD/AUD; SEK
  is a concrete example that is NOT in it) is never sent to Bloomberg at all -- there is
  no curve to ask for -- and is recorded directly with an explicit "no OIS index in
  Phase 1 scope" reason instead.
  - Gotcha found via a real test failure: don't pull **fixings** for an option-only
    currency. Fixings value a seasoned IRS's current float period; options don't use
    them at all, and `RatesFileSource`/`RatesBloombergSource` routinely have none staged
    for a currency that was only ever added for its curve -- that raised and overwrote
    the (successful) curve-quotes entry's `error` field with an unrelated fixings
    failure. Fixed by gating the fixings sub-call on `ccy in irs_ccys`.
- **No vol step existed at all.** `data/bloomberg/vol_marketdata.py` (ATM/RR/BF smile,
  `VolBloombergSource`/`VolFileSource`, `write_vol_quotes`) was written in an earlier
  phase but `data/bloomberg/live.py` never called into it anywhere -- confirmed by
  grepping the whole file for `vol_marketdata` before this fix: zero matches. So every
  option fell through SMILE and ATM_INTERP (both need `vol_quotes` rows that were never
  written) straight to the MANUAL `option_vols` fallback, which nobody had entered
  either, and was skipped "no vol". Added `_vol_step` (mirrors `_rates_step`'s shape:
  `{skipped}` when no open option, injectable `vol_source`, never raises), called
  alongside `_rates_step` in both of `pull_once`'s call sites (the early-return branch
  too -- an option-only book with no direct FX trade takes that branch, exactly the live
  scenario). Uses `vol_pairs_needed(conn)` for which pairs, live mode
  (`get_vol_quotes(pairs)`, `as_of=None` -> `ReferenceDataRequest`, source `BBG_BDP`) to
  match the rest of the live pull's intraday nature.
- **A "no vol" skip reason gave no way to know which ticker to check.**
  `VolBloombergSource.get_vol_quotes`'s `VolFetchResult.diagnostics` already carries the
  exact failing ticker per (pair, tenor, quote_type) -- just never threaded anywhere.
  `_options_step` gained a `vol_diagnostics` parameter (fed `status["vol"]["diagnostics"]`
  from `pull_once`) and a new `_enrich_no_vol_reason` helper: for a trade whose skip
  reason is exactly `"no vol"`, looks up that trade's pair (`base_ccy||quote_ccy` via a
  join back to `trades`+`instruments`) and appends the matching diagnostics' tickers plus
  a pointer at `py -3 -m data.bloomberg.vol_marketdata --probe`. Only fires for the exact
  string `"no vol"` and only when a same-pair ticker diagnostic exists -- every other skip
  reason (e.g. "no SPOT mark", "no curve/rate SEK") passes through unchanged.
  `VolFileSource`'s own diagnostics (used in tests/offline mode) are pair-level only, no
  `ticker` key -- the enrichment silently finds nothing to add for those and leaves the
  reason as-is, which is correct (that per-ticker detail genuinely doesn't exist there).

`tools/bbg_diagnostics.py::check_option_coverage` and `::check_last_pull` (that module is
normally owned by a different agent; the coordinator explicitly put it in bbg-data's lane
for this one fix) now read the last pull's status file and print the verbatim per-trade
skip reason / per-item failure detail instead of a bare "N of M failed" count -- see
`tests/test_bbg_diagnostics.py` (new file; no test file existed for this module's
individual check functions before).

## Technique note for next time

None of these three bugs were reproducible on this (non-Bloomberg) dev machine under
normal conditions -- two different tricks made them testable anyway:
1. For a "before market close" / "date boundary" bug: inject the exact `today` /
   `settle_date` relationship that trips the condition (`_same_day_db` /
   `_option_db` fixtures), and monkeypatch/fake only the network-facing functions.
2. For "is X actually called at all": use an `AssertionError`-raising fake for the call
   that must NOT happen (e.g. `request_fwd_curves` when every leg settles today,
   `fetch_historical` when live `PX_LAST` succeeds) -- catches a regression that would
   silently start making an extra network call, not just a wrong value.
