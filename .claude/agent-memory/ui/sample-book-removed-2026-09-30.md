---
name: sample-book-removed-2026-09-30
description: The in-app sample book (ui/sample_book.py, top-bar link, SAMPLE BOOK chip, upload/pull locks) was removed 2026-09-30; active_db_path/set_active_db stay for tools and tests
metadata:
  type: project
---

User, 2026-09-30: "remove the sample book and the sample book button in the headline". Done in ui/ only:
`ui/sample_book.py` deleted; the top bar's link, chip and "Back to my book", the `_switch_book` callback,
the upload lock and the "Pull Bloomberg now" lock (`pull_locked`, `pull_title`, `locked_message`,
`PULL_LOCKED_TITLE`) are gone; `feed_controls.pull_button()` and `uploads.upload_button()` take no argument.
The Book's empty state offers only Upload.

**Why:** the user did not want it on screen; the synthetic book is only a test / ui-check fixture now
(`tests/golden_book.py::build_book`, `data/sample/blotter_sample.csv`).

**How to apply:** do not reintroduce a sample switch. `ui.app.ACTIVE_DB` / `set_active_db` stay: `create_app`
sets the database it was built for, and `tools/ui_check.py` calls `set_active_db(None)`. The notes
[[sample-book-switch-2026-09-28]] and [[pull-lock-sample-book-2026-09-28]] are history only.
