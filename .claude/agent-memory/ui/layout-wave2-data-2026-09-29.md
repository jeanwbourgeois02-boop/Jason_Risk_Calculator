---
name: layout-wave2-data-2026-09-29
description: Layout wave 2, Data tab (2026-09-29) - Diagnostics as a Warm-up/Last pull/Backfill/Bloomberg kv table, folds in the kit's book-fold style, contract-date counts only in the status line, prev-close date in the heading; shot recipe with folds open
metadata:
  type: project
---

Data tab after layout wave 2 (`ui-check --strict --layout --tab data` exit 0, LOOSE_BLOCK 0, only DESIGNED_LINE for the status line and fold titles).

- **Diagnostics** (`market_data.diagnostics_body`): first a `tk-kv` table Warm-up | Last pull | Backfill | Bloomberg (short words, detail on hover; `data_checks.pull_facts(status, no_pull=)` / `backfill_facts`). A status file that says "no pull has run" still has a `time` (the status's own): pass `no_pull` so Last pull reads "None yet", never that time. Then, only when present: the steps table (`steps_table` returns None without steps), `backfill_tables` (h5 titles + tables), `left_out_block` (a table), the library fold, and "Everything the last pull recorded" = `status_block(status)[1:]` one row per block (the first line is the Bloomberg fact). `status_block` itself untouched (tests pin its shape).
- **Folds** Contract dates, Diagnostics, library: `book-fold tk-fold-block data-fold` with a `book-section-title` span (DIAG_SUMMARY_ID stays on the Summary, its n_clicks builds the body). CSS block `/* wave 2: data */ ... /* end wave 2: data */` in style.css.
- **One place:** contract-date counts only in the status line ("Contract dates 23 estimated"); fold title "Contract dates (23)". Marks meta returns "" (the total row holds the counts, zero counts omitted). Prev close: one date for all rows goes in the heading ("Prev close 18 Sep"). Library table dates short, added_at via `short_time`.
- Problems: `cap_parts` on Problem / What it blocks; an unrecognised symbol's "Contract not recognised: " prefix dropped (the chip says it); only columns 3-4 wrap.
- Bloomberg connection check results are a table (Result | Check | What it found), class `data-bbg-results` (never `bbg-check-list`, which is display:flex in the shared CSS).

**Traps:** another agent's pnl.py ruff error made `ruff check ui/` fail: check your own files by path. Appending CSS with `cat >>` lands after other agents' blocks: splice inside your own block instead. Shot recipe: scratchpad `w2data/shot_data.py` (build_sample + create_app + werkzeug on a socket-picked port; `tools.ui_check` has no `_free_port`), click "Data", open the folds, clip from `#market-data-past-closes-panel`.
