---
name: pull-speed-2026-10-01
description: 2026-10-01 second speed pass - status["seconds"] per step (SECONDS_KEYS), apply_contract_dates kept on every press (why), spot+futures one PX_LAST request (_CombinedPxLast rules), timeout findings
metadata:
  type: project
---

User 2026-10-01: "Pull Bloomberg now" still slow. Done in live.py / pull_marks.py, behaviour-neutral
(harness: same marks, items, steps, warnings as HEAD on Jason's real book, in ok / timeout / boom modes).

- **status["seconds"]** (SECONDS_KEYS, 0.01 s): session, contract_dates (less its apply),
  apply_contract_dates, requests, spot, forwards, futures, write_marks, lme, curves, vol, options,
  ledger, recalc, total. Filled by `_timed(..., step=name)` inside `_run`; contract_dates_step takes
  an optional `seconds` dict for the apply. status["timings"] untouched (ui/feed_controls and the
  Data tab read it).
- **apply_contract_dates kept on every press**: measured ~15 ms for 20 futures with nothing to move;
  a "stored this press" gate would miss a move an upload / snapshot import left undone, and a raw SQL
  join on contract_static misses ids only `static_dates` normalises. Do not gate it.
- **_CombinedPxLast**: one PX_LAST ReferenceDataRequest for spots + futures when both have asks, no
  future is a listed option (PX_MID would add a field to the spots), and the union fits
  min(REQUEST_CHUNK spot, futures). Tagged purpose LIVE_SPOT (spot's `_bloomberg_said` reads that).
  A raise goes once to the step that sent it (spot), then None, so futures ask on their own exactly
  as before (a timeout still costs the futures' own 30 s: safe choice, a transient gets its retry).
  `build_future_rows(prefetched=)` and `_live_spot_rows(prefetched=)` read it.
- **Timeouts**: a dead ticker never times out (securityError comes back in the same response; a
  future with no PX_LAST costs one PX_SETTLE history round trip). 30 s waits are a hung Terminal:
  the marks steps give up after 2 in a row, but contract dates (30 s per field set) and LME (15 s,
  fwd_curve) do not consult `_NetLog`. Proposed, not made: feed both into the NetLog.

**How to apply:** test fakes keyed on purpose "FUTURE_PX_LIVE" with a spot in the book now see one
"LIVE_SPOT" request carrying the futures; re-pin such tests when the suite is run.
