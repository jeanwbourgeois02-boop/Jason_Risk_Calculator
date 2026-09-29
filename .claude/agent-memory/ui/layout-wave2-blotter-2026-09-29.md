---
name: layout-wave2-blotter-2026-09-29
description: Layout wave 2 (2026-09-29) on the Blotter - last upload line + rejects fold as a closed card, Size column with units, option names; Options sub-tab cut to By pair / Expiry ladder / Spot vs strike cards + one "Every option" card of 14 columns, no P&L strip; shot recipe
metadata:
  type: project
---

Blotter layout pass (ui-check --strict --tab blotter exit 0, 3 DESIGNED_LINE kept on purpose).

**All fills:** `blotter-upload-line` in both branches, headline type scale (CSS in `/* wave 2: blotter */`). Rejects = `book-fold tk-fold-block blotter-rejects-fold` styled as a closed card, title `blotter-fold-title` + `kit.chip(count, level)`. Fills column "Lots" -> "Size" ("3 lots", "250k EUR"; an UNRECOGNISED row stays a bare number, reason on hover). Options on futures named by us ("NYMEX Crude oil Dec26 62 put"), not the engine's leg name (which carries "CLZ26P 62": found, not done, spreads-engine).

**Options sub-tab:** no P&L strip (`_STRIPLESS_SCOPES` has "options"; strip refresh not registered). `headline_strip` returns a tf.headline only under a filter (None grouped); old cards kept as `headline_cards`. Grid DISPLAY_COLUMNS 14: note -> `status` (status_words; note hidden but still in data, so the 'o strike' highlight works), `size` text, `delta_text` text coloured by `{delta}`. Rest on the Option cell's tooltip (`_rest_lines`). "By structure" table cut. Drawer `ISSUES_ID` = "options-terms-issues", refreshed by `_refresh_breakdowns` (now 2 outputs). Mark column MARK_FORMAT 5 significant figures.

**Traps:** strike can be NaN in flat legs: go through `_num` before `strike_text`. `tools/ui_check._free_port` was removed by infra mid-wave: own socket port in shot scripts. The Bash heredoc choked on a long python script: write it with Write and run the file. Shot recipe: scratchpad `wave2b/shot.py` (clicks "#blotter-subtabs >> text=Options"). ui_check only shoots the All fills sub-tab.

Tests now stale (owed, not re-pinned): test_ui_blotter options-strip ids (`blotter-strip-options`), test_ui_options on DISPLAY_COLUMNS / headline cards / "Portfolio Totals".
