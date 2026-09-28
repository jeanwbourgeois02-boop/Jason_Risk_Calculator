---
name: ui-redesign-wave1-2026-09-28
description: UI redesign wave 1 (2026-09-28): six-tab target (Book, Exposure, P&L, Timing & cash, Risk, Trades); wave 1 built Book table + Trades tab + slim header across ui-shell/ui-header/ui-book/ui-blotter in one agent; "no tests per wave" rule; what was deferred
metadata:
  type: project
---

The user (2026-09-28) is redesigning the screens into six tabs, one question each: Book (the book as
spreads), Exposure, P&L, Timing & cash, Risk, Trades (Data inside). Wave 1 (one agent owning
ui-shell + ui-header + ui-book + ui-blotter files, the lane rule set aside for the job) built:
tab order Book, Trades (key still `blotter`), Spreads, Curve, Risk, Expiries, FX & cash, Data;
a slim header (As of, Daily, MTD, YTD, LTD, Data chip; Previous day / 5d / Trading on the Daily's
hover, trade count on the date's hover; commodity strip, next-expiry and Risk chip removed, the
risk-chip callback deleted, `risk_summary*` kept for Book); Book = one table by sector (spread
positions + outright contracts, Entry/Now/Move in the unit, "$ per <unit>", Daily/MTD/LTD, Next
date), row detail reusing `ui.tabs.spreads.detail_payloads/members_table/detail_legs_table`
(ids re-pointed to `book-detail-*` to avoid duplicate DOM ids with the hidden Spreads body),
"Needs you", "Last load" (reads `upload_issues`); Trades tab's first sub-tab "All trades" with
Greeks-per-lot columns from `marks_official`, filters commodity/product/status + search + CSV,
Positions and P&L-by-asset-class folded into a collapsed "Summaries" Details.

**Why:** the user found the screens too dense and full of engine vocabulary ("leftover
outright", "groups for review", "USD per 1.0 move"); plain words rule on Book ("unmatched legs",
"could not group", "$ per <unit>", "context" for research stats).

**How to apply:** later waves do Exposure, P&L, Timing & cash, Risk. Mid-wave the user changed
the brief to "no tests per wave: tests once at the end of the batch" — do not write/update/run
tests in a wave unless told; my rewritten Book tests were parked in the session scratchpad
(`test_ui_book_wave1.py`) and the committed test file restored. Greeks on All trades are the
raw per-lot marks (never scaled in the UI); the Options sub-tab scales through the engine.
Deferred: the position's own-marks history chart in the Book row detail (a link to Spreads
instead); status filter is the existing multi-select (OPEN/SETTLED/CLOSED), not a radio.
