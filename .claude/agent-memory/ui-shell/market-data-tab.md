---
name: market-data-tab
description: ui/tabs/market_data.py rewrite (Task C3) — inventory table, pull button, completeness strip, manual entry
metadata:
  type: project
---

C3 rewrote `ui/tabs/market_data.py` to own the "Can I trust the numbers?" tab
(BUILD_PLAN.md section 5), moving the pull button / feed status off
`ui/tabs/cash_ladder.py`'s toolbar (C1 strips it there) and keeping
`diagnostics_panel` / `feed_headline` at module scope unchanged so existing callers
(cash_ladder.py) keep importing them from here without edits.

Public surface C5/others call: `build_layout(default_date)`, `register_callbacks(app,
get_db_path)`, plus `diagnostics_panel`, `feed_headline`, `message_box`.

Key defaults picked (undocumented in BUILD_PLAN.md, no one to ask):
- Close-completeness strip shows the trailing 20 business days ending on the selected
  as-of date (`COMPLETENESS_DAYS = 20`); padded start date is `20*1.6+5` calendar days
  back before calling `close_completeness`, then `.tail(20)`.
- Manual entry form is free-text instrument id + settle date (no dropdown/validation
  against `instruments`), mark-type dropdown lists all 8 contract mark_types.

Important: avoid importing `ui.app` from this module. At the time of writing,
`ui/app.py` imports `ui.tabs.pnl` which does not exist yet (mid-refactor across C1-C5),
so any `from ui.app import connect_readonly` blows up this module's tests with an
unrelated ImportError. Wrote a local `_connect_readonly` helper instead (same
`file:...?mode=ro` URI trick). Manual-entry writes use `data.ingest.schema.connect`
(needs a writable connection, unlike the read-only body render) — `write_manual_mark`
calls `conn.commit()` itself.

Schema gotcha: `trades` table has 14 columns as of the theme-column migration (Task B) —
last column `theme TEXT NOT NULL DEFAULT ''`. Any hand-built INSERT INTO trades in tests
needs all 14 values or it raises `OperationalError: table trades has 14 columns but N
values were supplied`.
