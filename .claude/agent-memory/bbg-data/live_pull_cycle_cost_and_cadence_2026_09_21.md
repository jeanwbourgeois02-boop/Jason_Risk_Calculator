---
name: live-pull-cycle-cost-and-cadence-2026-09-21
description: Where a live pull cycle's time goes (measured off-terminal on the 857-trade sample book), the one-shared-session change, the 15-minute cadence and what now waits longer, plus the measuring technique and its traps.
metadata:
  type: project
---

User asks (2026-09-21): "the pull bloomberg now is slow" and "make bloomberg load less often
(maybe every 15min)". INTERVAL_SECONDS 120 -> 900; STALE_AFTER_SECONDS = 2 x interval + 300.

## Cost profile of one cycle (sample book, as of 2026-08-31, fake blpapi, no Terminal)

- Steady cycle ~1.4-1.7 s of local CPU + SQLite. More than 90 % of it is
  `engine.rates.store.price_all_and_store`: ~0.15 s per swap, because `price_swap` takes 19
  NPVs per swap (base + parallel bump + 17 bucket bumps), each a full curve re-bootstrap
  (~8 ms), while store.py only writes the parallel DV01. engine/rates is NOT bbg-data's
  lane: reported, not changed. Everything in bbg-data's own code is under 0.1 s per cycle
  (build_requests 5 ms, write_marks 10 ms for ~330 rows, vol write 13 ms, options 60 ms for
  5 options, realise_settled 8 ms). First cycle of a process adds ~1 s of QuantLib /
  options_calc imports.
- Network can only be COUNTED here: per cycle 1 session open (was 3: FX, rates, vol) and
  these round trips: SPOT (all pairs, one request), FWD_CURVE (all pairs, one request),
  futures PX_LAST (+ a PX_SETTLE historical fallback when empty), one PX_LAST request per
  in-scope OIS currency, one fixings HistoricalDataRequest per IRS currency whose earliest
  swap has started (whole history from that start date, every cycle), one vol request
  (45 tickers per pair; `vol_pairs_needed` has no expiry filter, so expired options' pairs
  are still requested). SPOT, FWD_CURVE and vol were already batched.
- **Why:** the honest conclusion was that nothing measurable locally explains "slow"; the
  real-terminal answer comes from `status["timings"]` (nine fixed keys, seconds, 0.1) plus
  `status["timings_other"]` (build_requests, write_marks = where a SQLite lock wait shows)
  and `status["rates"]["seconds"]` (bloomberg vs pricing).
- **How to apply:** when the user pastes the next diagnostic, read those three before
  touching code. Large `rates.seconds.pricing` -> rates-pricer's bucket-bump cost; large
  `write_marks` -> lock contention (backfill / upload); large `session` or `forwards` ->
  Bloomberg itself.

## Shared session: the one correctness requirement

The three modules each had `itertools.count(1)` for CorrelationIds. On ONE session an id
still pending from a timed-out request must never be re-sent (blpapi raises on a duplicate,
and a late reply would be read as the wrong step's answer), so rates_marketdata now counts
from 1,000,000,001 and vol_marketdata from 2,000,000,001 (pull_marks stays at 1;
fwd_curve uses id(request), far above both). Keep ranges disjoint if a fourth module ever
borrows the session (rates_vol_marketdata.py still opens its own).

## What waits longer at 15 minutes

An option that expired while the app was off gets its payoff PREMIUM (engine/options'
catch-up inside `_options_step`) and its realised row only on the live cycle AFTER the
backfill has landed the expiry date's SPOT: that was <= 2 min after the backfill, now
<= 15 min unless someone clicks "Pull Bloomberg now". Same for the retry of a backfill run
that died. The backfill itself does every incomplete day in one run, so its speed does not
depend on the cadence. Suggested, not built: one extra cycle when the kicked backfill
thread ends having ADDED marks rows (count of past-dated marks before/after, so a backfill
that writes nothing cannot loop).

## Technique and traps

- A dynamic "answering" fake blpapi (tests/test_live.py::_install_answering_blpapi) runs
  the real open_session / fetch_reference / request_fwd_curves / Rates- and
  VolBloombergSource code end to end and logs sessions and requests. Pricing a swap and an
  option with it works (USD 17 quotes, 2 fixings, USDJPY put with strike via
  set_option_terms).
- Fair "before" without touching the shared git index or worktree:
  `git archive HEAD data engine config | tar -x -C <scratch>` and point sys.path there.
- Timings on this dev PC swing 3x when other agents run test suites in parallel (IRS
  pricing read 4.5 s in one run, 1.5 s in the next, same code). Alternate before / after
  runs and compare medians; never trust a single run.
- The files in this lane are LF (tests/test_bloomberg.py is CRLF). `grep -c $'\r'` in Git
  Bash reports every line as CRLF: check with a Python regex instead.
- Bash-tool heredocs containing Python with mixed quotes fail to parse ("unexpected EOF");
  write the script with the Write tool and run the file.
