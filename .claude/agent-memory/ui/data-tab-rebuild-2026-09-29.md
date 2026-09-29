---
name: data-tab-rebuild-2026-09-29
description: Data tab rebuilt 2026-09-29 ("Can I trust today's numbers?"): status line of links, one Missing table, one Marks check with filter/search/CSV over a store, Reference closes, Contract dates line, Diagnostics fold; what left and where the readers stayed
metadata:
  type: project
---

User approved the Data tab layout on 2026-09-29 after putting every piece through four tests (answers a real question / changes a decision / not derivable from another figure on screen / trustworthy). `ui/tabs/market_data.py` order: status line (BODY_ID holds it, kept as the body callback's FIRST output because tests/test_ui_smoke.py finds the callback by `..market-data-body.children` and calls it with 7 positional args: `_update_body(as_of, *_triggers)`) -> Missing, and what it blocks (MISSING_PANEL_ID) -> Marks check (MARKS_SECTION_ID; static filter bar reusing `blotter-filter-*` classes; rows in MARKS_STORE_ID, a light callback filters them, CSV reads the store) -> Reference closes (PAST_CLOSES_PANEL_ID) -> Contract dates (a `<details>` line) -> Diagnostics (static closed Details; library + status_block + the bbg check button) -> ISSUES_ID drawer. `render(as_of, db_path)` is module-level: call it directly for proofs.

Removed and why: tab's Pull now (top bar is the one action), pair section, commodity strip/picker/chart/table, completeness squares, manual mark entry (user: dropped; MANUAL never official). "Rows that did not become trades" moves to Trades (another agent): `upload_issue_rows` / `upload_issues_panel` left in market_data.py for it. `unpriced_trade_rows` / `period_only_rows` kept but unrendered: the Missing table and the Reference closes' "Trades left out" absorb them.

Traps seen: `_panel_table` tints rows where `{flag} != ''` and a row with NO flag key counts as tinted, so always give rows a flag key. `_quiet` inside a section with its own title repeats the title: use a plain status-line P there. The golden book / sample.db marks are stamped 15:00 NY, so on a past as-of the header and Data count them "0 of 44" while the Marks check lists 40 official marks (the 17:00 close rule, [[one-close-stamp-17-00-2026-09-28]]): a fixture artefact, not a screen bug.

**How to apply:** new Data pieces go through the same four tests; proof recipe = scratch golden DB + `create_app` dup-output check + `render()` at 2026-09-18 + headless Edge shot (shots.py, own DevTools port, kill only own PIDs).
