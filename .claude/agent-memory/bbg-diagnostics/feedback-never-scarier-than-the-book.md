---
name: feedback-never-scarier-than-the-book
description: user steer 2026-09-28 — a diagnostics row's severity must agree with what the screens show for the trades that need the mark (dash = fail, estimated/filled = warning, official = pass); a missing mark alone is never a FAIL
metadata:
  type: feedback
---

A mark-coverage diagnostic takes its severity from the screens, never from the mark table
alone: value the book once through the shared reader (`ui/tabs/blotter_pricing.py::
priced_value_book`, i.e. `value_book` + `engine.pnl.reference.fill_book`), classify each open
trade (official mark of the as-of = pass; `mark_source` starting `INTERP:` or a note opening
"no price on <date>: value of the <earlier> close" = warning, naming the trades and quoting the
app's own sentence; a non-empty `reason` = fail with that reason). Greeks, an option's
underlying price, unverified roots, unstored contract dates and a not-exercised last pull are
WARNING at worst; a future with no Bloomberg ticker is FAIL only when it also shows a dash.

**Why:** some contracts trade thinly and have no print on a given day; the app already handles
that (hard rule 2 near-marks estimate, then the fill from the last earlier close, at most 5
business days back), so a panel that said FAIL while the Book showed a number would be "scarier
than the Book" and teach the user to distrust it.

**How to apply:** any new coverage row in `tools/bbg_diagnostics.py` goes through
`_Outcomes` / `_screen_severity`; still NAME the missing mark (it is what the pull did not
get) but let the trades' outcomes set the status. Reuse the reader, never re-derive the fill.
Verify on `data/raw/sample.db`: the only FAIL rows there should be connectivity and last pull.
