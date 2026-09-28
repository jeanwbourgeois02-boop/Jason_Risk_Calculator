---
name: one-close-1700-every-instrument-2026-09-28
description: 2026-09-28 (hard-rule-7 yes) every past close is Bloomberg's daily PX_LAST stamped 17:00 NY, FX included; the 15:00 intraday FX rule of 2026-09-21..28 is gone; what backfill.py's stamp API looks like now and the traps of the reversal.
metadata:
  type: project
---

Every past close, whatever the mark type or instrument, is Bloomberg's daily PX_LAST
(`pull_marks.fetch_historical_series`) stamped 17:00 New York of its own date:
`backfill.close_stamp(day)` (no `today` argument any more; `settle_stamp` is a one-line
alias kept because `tests/test_ui_market_data.py`, the ui lane's, calls it). `CLOSE_HOUR_NY
= 17` is defined in backfill.py itself, never read from pull_marks (their constant moved to
17 in parallel, but the backfill's stamp must not follow a live-pull change).
`is_close_row(mark_type, as_of_date, snapped_at, today=None, instrument_id="")` keeps its
signature (inventory calls it with both keywords) but `today` and `instrument_id` no longer
change the answer: a past row is a close iff stamped 17:00 NY of its date, compared as an
instant. It now applies to EVERY mark type (PREMIUM / DELTA included; before, "any other
mark type" was always True).

**Why:** user, 2026-09-28: "everything closes on its day at the time at which its specific
exchange closes". The 15:00 rule (2026-09-21 "the EOD is 3pm New York time", 2026-09-22
"for fx use new 3pm") and its ~140-business-day intraday floor (`first_1500_day`,
`intraday_floor`, `_close_series_fetch`, `DAILY_CLOSE_HOUR_NY`, `SETTLE_HOUR_NY`,
`FX_CLOSE_MARK_TYPES`) were deleted; `SPOT_FWD_MARK_TYPES` is the only survivor, and it
only names the two types whose stale non-close rows `_write_closes` must delete (two
official-capable sources for one key). bbg-live deleted `fetch_intraday_close_series` /
`INTRADAY_SIDES` the same day; `CLOSE_REASON` stays in pull_marks and is still imported.

**How to apply:** a 15:00 row on an old database is "not a close" and is re-asked and
replaced on the next press (the `state_version` digest gained a "close" entry so every
day's remembered state reset once). Missing-mark reason for FX is now "Bloomberg returned
no PX_LAST for <pair> on <day>" (the same wording as futures; `_RETURNED_NO_RE` labels it
"PX_LAST of <pair>"). The auto-backfill `note` names "a row stamped at a live pull's own
time, or at the 15:00 New York close of the rule retired on 2026-09-28". Do not bring the
intraday bar back for any product without a fresh explicit user yes; the abandoned
futures-at-15:00 design is in [[futures-1500-close-reversed-2026-09-28]]. Tests: the
`_book_today_pinned` fixtures still pin today = 2026-09-21 only so the worked days are
past days (not for any intraday reach). vol_quotes / curve_quotes rows take
`engine.rates.store.snapped_at` (rates-pricer's, also 17:00 now), not this module's stamp.
