---
name: options-note-precedence-and-pull-status
description: Order of the Options sub-tab's per-leg note (own skip reason > pull's options-step error > book reason > generic fallback), the pull_status "options" block shape, and how tests fake it (write live.status_path(db) next to a file DB)
metadata:
  type: project
---

The per-leg `note` on the Options sub-tab has a fixed precedence (`_leg_note`, `_leg_row`):
pending terms / missing strike or barrier first; then, with no PREMIUM on `as_of`:
SETTLED book row -> the trade's OWN skip reason (`_LAST_SKIP` from a strike edit, or the
`skipped` list of the feed's status `options` block) -> the pull's STEP-LEVEL `error`
("not priced: the last Pull Bloomberg now (<d>) failed in its options step: <error>")
-> the book's own reason -> "no PREMIUM mark on <d>: priced the next time you press Pull
Bloomberg now". A listed option (EQ_OPTION) gets the same chain on its "Greeks not
calculated: ..." line, its P&L untouched (Bloomberg's own price).

**Why:** 2026-09-22 on the Bloomberg PC the options step failed as a whole (QuantLib OIS
bootstrap convergence error), so `skipped` was empty and every FX option leg read the
generic "priced the next time you press Pull Bloomberg now" although the pull had run;
the user: "im confused that options cannot be priced live today". The book's reason was
empty because value_book carried the previous close's PREMIUM under the near-marks rule,
so the book reason cannot be relied on to explain a missing as_of mark.

**How to apply:** the status block (`data.bloomberg.live._options_step`) is
`{priced, skipped: [{trade_id, reason}], as_of_date, error}`; the tab only reads it when
`as_of_date == as_of` (`_feed_options_step`). In tests, fake it by writing JSON to
`data.bloomberg.live.status_path(db_path)` (= `<db>.bloomberg_status.json`) beside a
FILE database (`_file_db`), never `:memory:` (`_db_key` is None there). To reproduce
"no mark on as_of but the book is priced", insert the PREMIUM (and the pair SPOT) on an
earlier close only; `_official_marks` reads the exact as_of so the tab sees None.
