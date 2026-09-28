---
name: sample-book-switch-2026-09-28
description: The active-database holder in ui/app.py (active_db_path / set_active_db) and the in-app sample book switch (ui/sample_book.py, the top bar's link and chip in ui/uploads.py); how to prove a switch on a scratch database without tests
metadata:
  type: project
---

Since 2026-09-28 every tab's `register_callbacks` receives `ui.app.active_db_path` (a callable
read at call time), never a captured `Path`: `ACTIVE_DB` in `ui/app.py` is the one process-wide
holder, `create_app` sets it to the database it was built for, and `ui/sample_book.py::switch`
moves it to `data/raw/sample.db` ("View the sample book": `tests.golden_book.build_book(conn,
through=today_ny())` + `realise_settled`, rebuilt fresh every time) or back ("Back to my
book": the real path again, the sample file deleted best effort). The switch callback lives in
`ui/uploads.py::register` (`_switch_book`: it owns the top bar ids) and publishes both revision
stores itself; the Book tab's empty state renders a pattern-id link `{"type": LINK_TYPE, "idx":
"book"}` that the same callback hears. `uploads._confirm` refuses an upload while the sample
is active (the button is disabled too), so the sample is never written into the real book.

**Why:** the user wanted one command (`chelsea`) and the sample reachable from inside the app,
with hard rule 1 kept (the real database holds only uploads).

**How to apply:** a new screen or control that needs the database path takes the
`get_db_path` callable and calls it inside the callback, never at registration. Proof recipe:
`RISK_DB=<scratch>`, `RISK_LIVE=0`, `create_app()`, `app.layout()`, `sample_book.switch("sample")`,
`book.render(today, active_db_path())`, `switch("real")`, compare the scratch file's
`st_mtime_ns` before and after. `golden_book.mark_dates(through)` adds business days after
2026-09-18; `through=None` is the pinned fixture unchanged (`py -3 -m tests.golden_book`
says "matches the golden").
