---
name: layout-wave1-kit-book-2026-09-29
description: Layout wave 1 (2026-09-29) - issues drawer is a table (kind/where/reason), row_info "i", head_info / tf.research_head, cap_parts, light headline, one fold arrow, Book Move/Flags columns, panel kv table + tk-hedge-line; shot recipe
metadata:
  type: project
---

Layout wave 1 (user: "things just wandering about not in tables", "its not acceptable"). Done = `ui-check --strict --tab <tab>` exit 0 + the screenshot read.

**Kit (ui/tabs/formatting.py):**
- `issues_drawer(items)` renders `table.issues-table` (Kind | Where | Reason, only the columns used). Items: str, (where, sentence), (kind, where, sentence), component. A where opening with DAILY/5D/MTD/YTD/LTD splits into the Kind column. Same (where, reason) under two kinds = one row "Daily, LTD". N = rows. Reasons through `cap_parts` (capital after ": " and " · ", the checker's LOWERCASE_PART split).
- `row_info(reasons)` = empty span `.row-info`, the "i" drawn by CSS ::before (a text "i" reads as a lowercase word to the checker). `head_info` for navy heads; `trade_filter.research_head(source)` = the z / roll-down heading's research caveat (replaces `research_mark` per cell).
- CSS: `.book-fold > summary` and `.issues-drawer > summary` list-style none + webkit marker hidden, the ::before chevron is the one arrow. `.tk-headline` is one light inline line (no card). `.tf-check` 32px tall box. `.tk-kv`, `.tk-hedge-line`.

**Book:** headline = Net USD + Flags (+ MTD, YTD when filtered): header and the total row hold the rest (one place per number; the header shows the trade count). Trade rows: no Excl. badge, the reason in the figure hover + the row "i" after the name (`excl_line`); totals/groups keep Excl. "Today" -> "Move" with unit suffix, em dash when zero at display precision. Flags column titled and sortable; the type-mismatch dot gone (it is a flag). Panel: `panel_facts` kv table beside the legs (`.tk-legs-main` + table in `.tk-panel-legs`), `hedge_line` Div `.tk-hedge-line` under the legs.

**Traps:** the z column is async (`shared_trade_risk`): a screenshot taken before the risk thread lands shows all dashes; wait for a digit in `td:nth-child(7)` (coordinator mistook it for hidden values). Recipe: scratchpad `wave1/shot_book.py` (build_sample + create_app + werkzeug + playwright via `.venv/Scripts/python.exe`; click `td` nth(2) of the row, wait for `tr.tk-panel`). Print cp1252: set PYTHONIOENCODING=utf-8.
