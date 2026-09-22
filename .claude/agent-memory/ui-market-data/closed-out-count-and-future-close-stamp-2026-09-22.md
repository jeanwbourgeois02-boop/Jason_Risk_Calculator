---
name: closed-out-count-and-future-close-stamp-2026-09-22
description: Status-file "closed_out" key (list per options/day block, count at recalc top level) shown as a count beside priced/skipped; a FUTURE_PX close row must be stamped backfill.settle_stamp (17:00 NY), so a test fixture stamping futures at 15:00 makes the past-close count wrong
metadata:
  type: project
---

Two facts from 2026-09-22, both about what the tab reads rather than what it computes.

1. The pull status file's options blocks carry a `closed_out` key (user: "we dont need to price
   all options, as some of them might be closed out already"): a list of trade ids in
   `status["options"]`, in each `recalc["days"][i]` and in each backfill day's options block; a
   plain count at `recalc["closed_out"]`. The pricer keeps those out of `skipped`, and bbg-data
   appends "N closed-out options not priced" to the options summary / `recalc_summary` sentence.
   The tab shows the count via `market_data.closed_out_count` / `closed_out_words` beside the
   priced / skipped counts (`_options_step_lines`, `recalc_day_rows` -> `recalc_block`); the top
   bar's line gets it for free through `recalc_words` (the sentence is shown untouched). Absent /
   empty key = nothing said, so an older status file renders exactly as before.

2. Since 2026-09-22 `backfill.is_close_row` counts a past day's FUTURE_PX row as a close only when
   stamped `backfill.settle_stamp(day)` (17:00 New York); a live press's PX_LAST is not a close.
   **Why:** the header's past-close count (`past_close_rows`, "present N of M") follows that rule,
   so the test fixture `_write_needed_marks` stamps FUTURE_PX rows at the settlement and FX rows
   at 15:00. **How to apply:** `_marked_book` (suspect panel, live date) deliberately keeps 15:00
   on its future rows, because `test_suspect_rows_flag_an_old_snap_only_on_the_live_date` measures
   staleness from a 15:00 snap; do not "fix" it to settle_stamp.

Related: [[missing-close-reason-and-feed-cadence-2026-09-21]], [[recalc-status-on-no-bloomberg-press-2026-09-22]].
