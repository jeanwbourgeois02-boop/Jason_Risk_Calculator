---
name: close-1500-every-past-day-2026-09-22
description: 2026-09-22 user decision - the 15:00 NY FX close applies to EVERY past day within Bloomberg's intraday reach (the 2026-09-21 cut-over CLOSE_1500_FROM is gone); is_close_row(today) semantics, what the Mac snapshot DB showed, the first-real-pull cost, and the out-of-lane fallout (CLAUDE.md, header "missing" sentence, market-data strip).
metadata:
  type: project
---

Supersedes section C of [[close-1500-scale-ndf-2026-09-21]]. User, 2026-09-22: "yes I want to see
the ltd line chart, which requires all the previous closes, and fixes. For futures, can use
market close, for fx use new 3pm."

## The rule now (data/bloomberg/backfill.py, inventory.py)
- No calendar cut-over. `first_1500_day(today)` == `intraday_floor(today)` (140 weekdays back;
  2026-03-10 from 2026-09-22). `CLOSE_1500_FROM` is deleted; the only mention left is a comment.
- `is_close_row(mark_type, as_of_date, snapped_at, today=None)`: FX row (SPOT/FWD_OUTRIGHT) of a
  past day is a close only at 15:00 NY of its own date; 17:00 counts ONLY for a day before the
  floor. Compared as instants, so a +08:00 stamp of the same moment (the Bloomberg PC is in
  Asia) is a close. FUTURE_PX / NDF_FIX always count. `today` may be a date or an ISO string
  (`_as_date`); `close_completeness` passes its `today` through. The 17:00 branch is checked
  last so `book_today()` is only called for a 17:00 row with no `today` given.
- `_drop_already_official` / `_write_closes` pass `today` too, so a 17:00 row on a day within
  reach is deleted and replaced by the 15:00 row in the same transaction (both sources).
- `auto_backfill` note wording: "N past day(s) hold FX marks that are not that day's 15:00 New
  York close ... every past day from <floor> on ... A day before <floor> ... 17:00 daily close."
- NDF_FIX needed no change: it is in `library.MARK_KINDS`, so `_needed_marks(historical=True)`
  lists it on the fixing date, `close_completeness` marks the day incomplete without it, and
  `backfill()` pass 1 writes it from `_fetch_ndf_fix_history` (uses `fut_fetch`).

## What the Mac snapshot DB showed (data/raw/risk.db, read-only, today=2026-09-22)
- Before: `not_closed > 0` on 1 of 43 business days (09-21, 66 rows). After: 43 of 43, 1,667
  needed marks on file but not at the close; 2,573 official FX rows in all not stamped 15:00 on
  those days (tenor rows the day did not need are not in `not_closed`). Present per day drops
  to 1-3 (the futures' PX_SETTLE / NDF_FIX).
- A whole-`marks` scan finds 46 days: 09-07 (Labor Day, config/holidays.txt) and Sat/Sun
  09-19/09-20 hold live-press rows but are never business days, so `close_completeness` never
  lists them and nothing re-requests or deletes them. Harmless for P&L (no business-day close).
- Every business day was already `complete == False` (present < needed) BEFORE the change, so
  "incomplete" alone never told the story; `not_closed` is the column that shows the stamp problem.

## Cost / behaviour of the first real pull after this
- ~43 days re-requested as stretches: 2 IntradayBarRequests (BID, ASK) per spot ticker and per
  tenor ticker per stretch, so tens of requests, not thousands; still UNVERIFIED on a terminal.
- Until the Bloomberg PC re-pulls and re-exports, the imported snapshot on the Mac stays
  "not closed" everywhere; the marks-import path does not restamp anything.

## Out of lane, reported to the housekeeper (not done here)
- CLAUDE.md "Mark time" still names `backfill.CLOSE_1500_FROM` and "A day before 2026-09-21
  keeps the marks it has": contract text is the housekeeper's.
- `ui/tabs/header.py::needed_marks` -> `close_completeness(as_of, as_of)` for a past date: with
  17:00-stamped fixtures (tests/test_header.py `_insert_official_mark`) a past day's marks now
  read as missing in the header's caption; P&L itself never looks at stamps.
- `ui/tabs/market_data.py` completeness strip: every past day shows incomplete until re-pulled.

## Test traps
- test_backfill's `test_a_day_older_than_bloombergs_intraday_history_takes_the_daily_close`
  pins the log substring "take Bloomberg's daily close (17:00 New York)": keep it in the log line.
- Both test files' autouse fixture pins `live.book_today` to 2026-09-21 so the 2026-09-07..18
  fixture days stay within intraday reach; a test that needs a day beyond the floor passes
  `today=` explicitly (2027-06-01 in the daily-close test).
