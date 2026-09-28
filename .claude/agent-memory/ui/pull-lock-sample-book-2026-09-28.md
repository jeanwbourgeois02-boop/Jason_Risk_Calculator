---
name: pull-lock-sample-book-2026-09-28
description: The "Pull Bloomberg now" lock while the sample book is active (ui/feed_controls.py pull_locked / pull_button / click_outcome refusal; uploads._switch_book outputs the button's disabled and title); the pattern to copy for any control that writes the real book
metadata:
  type: project
---

Since 2026-09-28 every control that writes the real book is locked while the sample book is
active, the same pattern twice: the upload (`sample_book.upload_button`, `uploads._confirm`
refuses) and the pull (`feed_controls.pull_locked()` = `sample_book.is_sample_active()`,
`pull_button(locked)` built at render since the layout is a callable rebuilt per page load,
`click_outcome` refusing first of all so the Data tab's "Pull now" refuses too, and
`uploads._switch_book` outputting the button's `disabled` (allow_duplicate) and `title` so the
state flips in place with the chip). `_pull_clicked` / `_pull_poll` re-read `pull_locked()`
when they set the button's `disabled`, so a refused press or a landing pull never re-enables
a locked button.

**Why:** the user's yes on 2026-09-28: a pull's marks, backfill and ledger writes go to the
real database (hard rule 1), never to the throw-away sample.

**How to apply:** a new control that writes the real book takes the lock the same way: the
disabled state read at render, the refusal inside the server-side outcome function (a stale
page can still post), and the button's `disabled` + `title` added as outputs of
`_switch_book`. Proof recipe: `RISK_DB=<scratch>`, `RISK_LIVE=0`, `create_app()`, `app.layout()`,
`pull_button(pull_locked())` before / after `sample_book.switch("sample")`, `click_outcome`
with a fake feed counting `trigger_now` calls, then `switch("real")`. See
[[sample-book-switch-2026-09-28]].
