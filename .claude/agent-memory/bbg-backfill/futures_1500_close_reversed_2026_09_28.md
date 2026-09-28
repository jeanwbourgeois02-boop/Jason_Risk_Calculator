---
name: futures-1500-close-reversed-2026-09-28
description: 2026-09-28 the user first moved futures / listed options past closes to the 15:00 NY TRADE bar, then reversed it the same day -- FUTURE_PX stays daily PX_LAST stamped 17:00 (settle_stamp); what the abandoned build looked like, in case it comes back.
metadata:
  type: project
---

A future's and a listed option's (EQ_OPTION / CMDTY_OPTION) past close STAYS Bloomberg's daily
PX_LAST stamped 17:00 New York (`settle_stamp`, `is_close_row` FUTURE_PX == 17:00), exactly the
2026-09-22 rule. Only FX (SPOT / FWD_OUTRIGHT) takes the 15:00 intraday bar. LME pillars: 17:00.

**Why:** on 2026-09-28 the user said "keep it at 3pm New York as it was" (housekeeper read it as:
everything at 15:00, futures included, a hard-rule-7 yes), the change was built in backfill.py,
then the user reversed it within the hour: no 15:00 bar for futures. The housekeeper reverted
backfill.py to HEAD with git; the test re-pins had not been applied yet, so nothing of this lane's
survived. bbg-live had added `pull_marks.INTRADAY_TRADE_SIDES = ("TRADE",)` in parallel (a `sides=`
kwarg on `fetch_intraday_close_series` was planned); whether that stayed is bbg-live's business.

**How to apply:** do not move FUTURE_PX to 15:00 again without a fresh, explicit user yes; if it
does come back, the abandoned design was: `fut_fetch` default = `_close_series_fetch(first_1500,
sides=INTRADAY_TRADE_SIDES)` (daily PX_LAST only before the intraday floor), rows stamped
`close_stamp(d, today)`, `is_close_row` FUTURE_PX on the FX rule, a separate `lme_fetch` (daily
history; the injected `fut_fetch` in tests) so the LME pillars keep 17:00, a `reasons` out-param on
`_fetch_future_px_history` carrying the fetch's CLOSE_REASON, a "futures" entry in
`state_version()`'s rules (resets every day's state once), and the auto_backfill note reworded.
Out-of-lane fallout it would have had: tests/test_ui_market_data.py stamps a past FUTURE_PX row
with `backfill.settle_stamp` (17:00). Follows [[history-inputs-and-futures-settle-2026-09-22]].
