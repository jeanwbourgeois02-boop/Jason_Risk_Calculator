---
name: day-boundary-book-today-2026-09-22
description: The app's one day boundary (17:00 New York = 05:00 HKT) lives in data.bloomberg.live.book_today; cash_ladder.today_ny only delegates. Manual entry's calendar date is a still-open question.
metadata:
  type: project
---

`cash_ladder.today_ny` no longer computes the 17:00 New York roll itself: it returns
`data.bloomberg.live.book_today(now).isoformat()` and re-exports `live.ROLLOVER_HOUR_NY`
(module-level import; live.py imports cleanly without blpapi, its only `ui` import is lazy
inside a function, so no cycle).

**Why:** user decision 2026-09-22: "I want to clarify the time today, so that all daily pnl
is calculated from the NY 3pm the day before. I am based in HK, so basically all date
rollover at hkt 5am" (HKT 05:00 = 17:00 NY), after "no only roll to new day after new york
5pm". Before this, the screens rolled at 17:00 while the marks, the backfill's "past" and
the ledger kept the NY calendar date, so between 17:00 and midnight NY (the user's HK
morning) the top bar valued D+1 off D's carried marks and Daily read 0 for the whole book.
bbg-data made `book_today` the single rule; every pull, the backfill and the ledger use it.

**How to apply:** never reintroduce a second clock in `ui/tabs/`; anything on the Ladder
that needs "today" calls `today_ny()` (or `book_today` directly). `tests/test_ui.py`
(ui-shell's) pins `cash_ladder.ROLLOVER_HOUR_NY == 17` and `today_ny(datetime(..., tzinfo=ny))`
at 16:59 / 17:00 / 23:30 / 00:05, so the re-export and the tz-aware `now` parameter must
survive any refactor. `calendar_today_ny` (the Manual entry default trade date, NY calendar
date with no roll) was deliberately left alone: whether a manual trade dealt after 17:00 NY
should be dated the rolled day is an open question for the user, not decided.
