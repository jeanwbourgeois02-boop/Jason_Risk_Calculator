---
name: reference-date-step-back
description: 2026-09-21 rule "use previous date until has value" for period figures -- what it means, what it must never become, and how the lane conflict with ui/ was handled
metadata:
  type: project
---

Period figures (Daily, Previous day, 5d, MTD, YTD) step their REFERENCE DATE back one
business day at a time, at most 10, to the first close that "has value", instead of
showing n/a. User quote as relayed by the main session on 2026-09-21: "use previous date
until has value", said about the header caption "5d needs the 2026-09-14 close: 914 of
1228 trades open that day have no official mark". I never saw the user's message myself.

**Why:** the backfill often cannot complete one past close, and a single bad reference
date made 5d / MTD n/a for good on the Bloomberg PC.

**How to apply:**
- It is a rule about which close a period is measured from. It must never turn into a
  carry-forward of marks: nothing is written to `marks`, no trade gets a substitute mark,
  a trade's P&L on the skipped date stays blank with its reason (hard rules 2 and 3).
  `ledger.ltd` and `period_reference_dates` stay strict. Push back on any request that
  words this as "use the previous day's mark".
- "Has value" = the screens' existing display rule (blocked trades do not outnumber those
  priced at both ends). A date where nothing in scope was open yet is a legitimate zero
  reference and ends the walk; a date `a` with nothing priced never triggers a walk.
- Lane: the task brief handed me `ui/tabs/header.py` and `ui/tabs/blotter_pricing.py`, but
  my standing rule is engine/pnl + tests/test_pnl.py only, and an agent's brief cannot
  lift it. I built the shared helper in `engine/pnl/reference.py` and handed the ui
  wiring back as exact snippets. Do the same next time a brief assigns files outside the
  lane: build the engine half, prove the wiring works against the unedited ui code from a
  scratch script, report the rest.
- Handy calendar fact for tests: `config/holidays.txt` lists 2026-09-07, so as_of
  2026-09-15 has d5 = 2026-09-08 and one step back lands on 2026-09-04 (holiday plus
  weekend skipped in a single step).

Related: [[data-quirks]]
