---
name: data-tab-three-cards-2026-09-30
description: Data tab rebuilt as Blotter | Bloomberg | Diagnosis cards (user "needs to be clean"); render() 17 outputs, fold ids, the diagnosis report in diagnostics_runner, clipboard trick, proof recipe
metadata:
  type: project
---

User 2026-09-30: "the data tab - Needs to be clean - check of what is and isnt pulled from the blotter and why - no duplicates - bloomberg store on cache, only pull new data - a diagnosis tab ... paste into claude code".

- Layout: title + status line, then `trades_card` (id `market-data-trades`, title "Blotter": upload line, "Loaded N: ..." from `last_upload_outcome`, "Rows not loaded or to fix (N)" = `data_checks.blotter_rows` → `trade_problems_table` Row|Trade Id|Symbol|What happened|Why, `DUPES_ID` duplicates check, parse block), `bloomberg_card` (pull line, `cache_line` from status backfill.cache, `contract_dates_status_line`, history line, Pull problems, then flat folds: `BBG_ASKED_ID`, Problems = `MISSING_PANEL_ID` Details, Marks = `MARKS_SECTION_ID` Details, reference closes Details inside `PAST_CLOSES_PANEL_ID`, contract dates, `BBG_CHECK_FOLD_ID`, Details), `diagnosis_card` (`DIAG_*`).
- `render()` returns 17: 15 = duplicates block, 16 = asked fold. tools/ui_check.render_data fills only 0-10 (infra request to fill 11-16).
- Kept slot ids ui_check fills: PAST_CLOSES_PANEL_ID / CONTRACT_DATES_PANEL_ID stay Div slots (a Details with that id would lose its Summary to _fill).
- Diagnosis: `diagnostics_runner._run` builds `diagnosis_text` after check_book (state `report`, `report_at`); reuses tools/bbg_report `environment_lines`, `last_pull_lines`, `last_pull_problems` (not edited). ~2.5 s with no Bloomberg. Both buttons (check fold, Build) start the same run; `BBG_DONE_ID` change drives `_diag_text`.
- Copy: `dcc.Clipboard(className="btn data-diag-copy")`, its `content` set with the text; label from CSS `::after` (Clipboard takes no children). Textarea readOnly (a control: ui_check does not text-check it).
- Old "Download report" of the check and market_data.report_text/report_filename removed.
- Proof: scratchpad `proof.py` (fill all 17 slots, `uc.check_runs("data", uc.walk(tree))`, two `import_blotter` uploads: re-booked Trade Id, a Cancelled row, a Pending row) and `browser.py` with `.venv/Scripts/python.exe` (playwright only there; grant clipboard perms, read navigator.clipboard).
- Full-page shots after clicks show sticky-thead displacement: an artifact, look at the unclicked shot.
