---
name: one-close-stamp-17-00-2026-09-28
description: Since 2026-09-28 (3c63b30) every past close is stamped 17:00 New York; screen test fixtures must stamp marks T17:00, never T15:00, or the header's needed_marks and the Data tab's close completeness read them as "not a close"
metadata:
  type: project
---

One close stamp for the whole app since 2026-09-28: `data.bloomberg.backfill.is_close_row` is True
iff a past row's `snapped_at` is 17:00 America/New_York of its own date, for every mark type and
instrument (`backfill.close_stamp(day)`; `settle_stamp` is an alias; `pull_marks.CLOSE_HOUR_NY = 17`).
The 15:00 FX rule is gone.

**Why:** user decision 2026-09-28 (commit 3c63b30): Bloomberg's daily close for everything, one stamp.

**How to apply:** a screen fixture that inserts a mark standing for a past close writes
`f"{as_of}T17:00:00-04:00"` (or `backfill.close_stamp(date)`); a 15:00 stamp makes
`header.needed_marks`, `header._missing_marks_reason` and `market_data.past_close_rows` count the
mark as missing. Fixed on 2026-09-28 in `tests/test_header.py::_insert_official_mark`,
`tests/test_ui_market_data.py::_mark` and the literal stamps in `test_ui.py`, `test_ui_blotter.py`,
`test_ui_options.py`. Chip / docstring examples read "17:00 NY". The header's `mark_time_words`
prints whatever stamp is on file, so a live press still shows its own time or "live".
