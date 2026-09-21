---
name: historical-backfill-no-settle-dt-2026-09-21
description: Why 5d/MTD/YTD stayed n/a on the Bloomberg PC (no past FWD_OUTRIGHT was ever written), how the backfill builds a past forward curve now, the freeze-ordering trap, and what is still unverified on a terminal.
metadata:
  type: project
---

User's Bloomberg PC, 2026-09-21: "5d n/a -- needs the 2026-09-14 close: 576 of 577 trades ... no
official FUTURE_PX/FWD_OUTRIGHT/SPOT (37 of 54 needed marks)". Follows
[[historical-backfill-fwd-future-2026-09-18]].

## Root cause
- The historical curve asked HistoricalDataRequest for `SETTLE_DT` on `<PAIR><TENOR> Curncy`. It is a
  static reference field; history does not serve it. `fwd_curve.historical_points_by_day` needs it on
  every row, so every tenor of every past day was dropped: no past forward, no complete past day, ever.
- The same tickers' PX_LAST is forward POINTS by the live tenor path's own account
  (`pull_marks.fetch_tenor_points`, docs/open-questions.md item 28); the backfill read it as an outright
  and its `value <= 0` filter threw away negative points. Had SETTLE_DT come back, points would have been
  written as outrights.
- `auto_backfill` walked oldest day first, one `backfill()` call / session / three requests per day, any
  exception ended the run and the next run restarted from the oldest day; every incomplete day was asked
  again after every feed cycle; reasons were printed, never kept.

## What the code does now (data/bloomberg/backfill.py, fwd_curve.py)
- `fwd_curve.historical_curve`: one day's curve from that day's tenor PX_LAST + that day's SPOT.
  Bloomberg's SETTLE_DT when sent, else dates by convention (`spot_date_for`: T+2, T+1 for
  USDCAD/USDTRY/USDPHP/USDRUB; `tenor_settle_date`: weeks following, months modified following with the
  end-of-month rule; calendar = config/holidays.txt only). Unit by `tenor_unit` (every value within
  [0.5, 2] x spot -> outright, else points). Points -> spot + points / FWD_POINTS_SCALE.
- BBG_BFXFORWARD only for Bloomberg's own outright at Bloomberg's own date; anything from points or at a
  computed date is BBG_INTERP.
- `backfill()` is two passes: all days' SPOT + FUTURE_PX in ONE transaction, then forwards day by day.
  **Why:** the live pull calls `realise_settled(today)` every cycle and a freeze takes the last official
  SPOT/FUTURE_PX on or before settlement and is never recomputed, so writing closes day by day in any
  order but newest-first lets it freeze a trade at an older close. **How to apply:** never write past
  SPOT/FUTURE_PX closes piecemeal out of date order.
- `auto_backfill`: one call per run, `reference_dates` first then newest first, days with needed == 0
  never asked, a day that stayed incomplete retried after `RETRY_SECONDS` (3600) unless its missing set
  changed. Outcome under status "backfill": days / last_run.
- `live.pull_once` rewrites the whole status file without the "backfill" key every cycle, so
  `start_auto_backfill` republishes its whole remembered block on every call.

## Still unverified on a terminal
(Later the same day the terminal did return points and NOTHING for `FWD_POINTS_SCALE`; both fields are now
asked for, and the closes moved to 15:00 NY intraday bars: see [[close-1500-scale-ndf-2026-09-21]].)
Whether the tenor tickers are points or outrights (handled either way), the `FWD_POINTS_SCALE` field name
(`FWD_SCALE`, an exponent, is the likely alternative if the status says no scale came back), whether
history serves `PX_SETTLE` for an expired future (ESU6 expired 2026-09-18).

## Test trap
Tests using "yesterday" break on a Monday (yesterday is a Sunday, no business day): roll back to a weekday.
